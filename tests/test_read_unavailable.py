"""AMR consume-side of contract §6.4 read_unavailable (#82).

The backend now returns HTTP 409 + {error:{code:"read_unavailable",...}} when a chat
can't be read (shard key missing / table unmapped). AMR must: (a) NOT let one chat's
409 abort the whole account poll, (b) surface it (event + health count) rather than
treat the conversation as 0 互动. These tests pin both.
"""
import json

from jl import ingest, ingest_run, db, web
from jl.channels import fullwechat


# --- parsing (pure) --------------------------------------------------------------

def test_parse_read_unavailable_recognizes_signal():
    body = {"error": {"code": "read_unavailable", "reason": "key_unavailable",
                      "chatId": "wxid_test_roy",
                      "coverage": {"coveredShards": 28, "totalShards": 30}}}
    ru = ingest.parse_read_unavailable(body)
    assert isinstance(ru, ingest.ReadUnavailable)
    assert ru.chat_id == "wxid_test_roy"
    assert ru.reason == "key_unavailable"
    assert ru.coverage == {"coveredShards": 28, "totalShards": 30}


def test_parse_read_unavailable_accepts_json_string():
    ru = ingest.parse_read_unavailable(
        '{"error":{"code":"read_unavailable","reason":"table_unavailable","chatId":"c1"}}')
    assert ru.reason == "table_unavailable" and ru.chat_id == "c1"


def test_parse_read_unavailable_none_for_normal_or_garbage():
    assert ingest.parse_read_unavailable([]) is None            # a real (empty) array
    assert ingest.parse_read_unavailable({"messages": []}) is None
    assert ingest.parse_read_unavailable("not json") is None
    assert ingest.parse_read_unavailable({"error": {"code": "other"}}) is None


def test_parse_read_unavailable_fallback_chat_id():
    ru = ingest.parse_read_unavailable(
        {"error": {"code": "read_unavailable", "reason": "unknown"}},
        fallback_chat_id="cfallback")
    assert ru.chat_id == "cfallback" and ru.reason == "unknown"


# --- adapter: one 409 must not abort the account poll ----------------------------

def test_pull_new_skips_unreadable_conv_and_continues(monkeypatch):
    a = fullwechat.FullWechatAdapter(url="http://x", token="t")
    convs = [ingest.ConvRecord(chat_id="good1", name="A"),
             ingest.ConvRecord(chat_id="bad", name="B"),
             ingest.ConvRecord(chat_id="good2", name="C")]
    monkeypatch.setattr(a, "all_conversations", lambda acct: convs)

    def fake_messages(chat_id, limit, offset):
        if chat_id == "bad":
            raise ingest.ReadUnavailable(chat_id="bad", reason="key_unavailable")
        return [ingest.MsgRecord(msg_key=f"k:{chat_id}", ts=1, content="hi", sender="s")]

    monkeypatch.setattr(a, "_messages", fake_messages)
    out = a.pull_new({"account_id": 1})

    assert [c.chat_id for c, _ in out] == ["good1", "good2"]   # bad skipped, NOT aborted
    un = a.drain_unreadable()
    assert len(un) == 1 and un[0]["chat_id"] == "bad" and un[0]["reason"] == "key_unavailable"
    assert a.drain_unreadable() == []                          # drained (idempotent)


def test_backfill_unreadable_returns_empty_not_raises(monkeypatch):
    a = fullwechat.FullWechatAdapter(url="http://x", token="t")

    def fake_messages(chat_id, limit, offset):
        raise ingest.ReadUnavailable(chat_id=chat_id, reason="table_unavailable")

    monkeypatch.setattr(a, "_messages", fake_messages)
    page, nxt = a.backfill({"account_id": 1}, ingest.ConvRecord(chat_id="bad"), "0")
    assert page == [] and nxt == ""                            # stops cleanly, no raise
    assert a.drain_unreadable()[0]["reason"] == "table_unavailable"


# --- orchestration: surface as an event, not silent ------------------------------

def test_ignite_logs_read_unavailable_event():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")

    class FakeAdapter:
        platform = "wechat"
        validate_conn = None

        def __init__(self):
            self._un = [{"chat_id": "bad", "reason": "key_unavailable",
                         "coverage": {"coveredShards": 1, "totalShards": 2}}]

        def pull_new(self, account, recent_limit=30):
            return []

        def drain_unreadable(self):
            u = self._un
            self._un = []
            return u

    ingest_run.ignite(c, FakeAdapter(), account_id=1)
    rows = c.execute("SELECT detail FROM events WHERE kind='read_unavailable'").fetchall()
    assert len(rows) == 1
    d = json.loads(rows[0][0])
    assert d["chat_id"] == "bad" and d["reason"] == "key_unavailable"


def test_health_exposes_read_unavailable_24h_count():
    c = db.connect(":memory:"); db.init_db(c)
    db.log_event(c, kind="read_unavailable", actor="poll",
                 detail={"chat_id": "bad", "reason": "key_unavailable"})
    h = web.api_health(c)
    assert h["read_unavailable_24h"] == 1
