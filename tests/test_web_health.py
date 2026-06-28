"""PII-free ops/health endpoint — pure handler over an in-memory store (no socket).

The whole point of /api/health is that an EXTERNAL ops Agent can monitor AMR without
the main token and WITHOUT ever seeing contact PII. These tests therefore assert two
orthogonal things: (1) the operational SHAPE (counts/booleans/versions only), and
(2) a hard PII guard — the synthetic contact name/wxid/message content seeded into the
DB must NOT leak into the serialized output anywhere.
"""
import json

from jl import db, ingest, web
from jl.version import __version__


# Synthetic-only PII (public-repo-safe placeholders). The PII guard asserts NONE of
# these strings appear anywhere in the serialized /api/health output.
PII_NAME = "张三"
PII_WXID = "wxid_test_zhangsan"
PII_CONTENT = "带合同来三号厂房"


def _seed(*, killswitch=False, supervised=False, outbox=False):
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="self_wxid_test",
                      label="fullwechat #1", host="http://10.0.0.5:9000",
                      tool="fullwechat")
    m = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id=PII_WXID,
                               name=PII_NAME, type="private")
    db.insert_messages(c, m, [ingest.MsgRecord(
        msg_key=f"{PII_WXID}:1", ts=100, content=PII_CONTENT, sender=PII_NAME,
        sender_id=PII_WXID, direction="in")])
    # a couple more convs so autonomy.off has a nonzero count
    db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_test_b",
                           name="李四", type="private")
    if supervised:
        db.set_autonomy(c, m, "supervised")
    if killswitch:
        db.set_killswitch(c, True)
    if outbox:
        oid = db.queue_outbox(c, conversation_id=m, body="您好", actor="user")
        # one pending (above) + one failed
        oid2 = db.queue_outbox(c, conversation_id=m, body="测试", actor="user")
        db.mark_outbox(c, oid2, "failed", error="backend down")
    return c


def _no_probe(*a, **k):
    """Mock backend probe → never hits network; returns a fixed version."""
    return {"version": "0.12.0", "schema": "1"}


def test_health_shape_basic():
    c = _seed()
    h = web.api_health(c, now=1782600000, probe=_no_probe)
    assert h["amr_version"] == __version__
    assert h["ok"] is True
    assert h["ts"] == 1782600000
    assert h["killswitch"] is False
    assert set(h["autonomy"]) == {"off", "observe", "supervised"}
    assert isinstance(h["autonomy"]["off"], int)
    assert set(h["auto_replies"]) == {"armed", "shadow", "human"}
    assert set(h["outbox"]) == {"pending", "failed_recent"}
    assert isinstance(h["backends"], list)
    assert "events_recent" in h and "errors_24h" in h["events_recent"]


def test_health_killswitch_reflected():
    c = _seed(killswitch=True)
    h = web.api_health(c, now=1782600000, probe=_no_probe)
    assert h["killswitch"] is True


def test_health_supervised_conv_counted():
    c = _seed(supervised=True)
    h = web.api_health(c, now=1782600000, probe=_no_probe)
    assert h["autonomy"]["supervised"] == 1
    # 2 convs total, 1 dialed to supervised → 1 left at off
    assert h["autonomy"]["off"] == 1


def test_health_outbox_counts():
    c = _seed(outbox=True)
    h = web.api_health(c, now=1782600000, probe=_no_probe)
    assert h["outbox"]["pending"] == 1
    assert h["outbox"]["failed_recent"] == 1


def test_health_backends_pii_free_fields():
    c = _seed()
    h = web.api_health(c, now=1782600000, probe=_no_probe)
    for b in h["backends"]:
        # ONLY these operational keys — no self_id, no host (could leak internal IP)
        assert set(b) <= {"slot", "tool", "reachable", "backend_version"}
        assert "self_id" not in b
        assert "host" not in b


def test_health_public_no_token(monkeypatch):
    """/api/health must work with NO token (public, like /api/version)."""
    monkeypatch.setenv("JL_WEB_TOKEN", "the-secret-main-token")
    c = _seed()
    # the handler-level auth gate is bypassed for /api/health (wired before _auth_ok);
    # the pure handler itself takes no token at all.
    h = web.api_health(c, now=1782600000, probe=_no_probe)
    assert h["ok"] is True
    assert "the-secret-main-token" not in json.dumps(h, ensure_ascii=False)


def _walk_strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def test_health_is_pii_free():
    """HARD RULE: no contact name / wxid / message content anywhere in the output —
    neither as a value nor as a key. Walk the whole serialized JSON and assert the
    known synthetic PII does not appear."""
    c = _seed(supervised=True, outbox=True)
    h = web.api_health(c, now=1782600000, probe=_no_probe)
    blob = json.dumps(h, ensure_ascii=False)
    for pii in (PII_NAME, PII_WXID, PII_CONTENT, "self_wxid_test", "10.0.0.5", "李四"):
        assert pii not in blob, f"PII leaked into /api/health: {pii!r}"
    # also walk structurally (catch non-JSON-serializable surprises)
    for s in _walk_strings(h):
        for pii in (PII_NAME, PII_WXID, PII_CONTENT, "self_wxid_test"):
            assert pii not in s, f"PII leaked (structural): {pii!r} in {s!r}"


def test_health_auto_replies_degrades_to_null_on_error():
    """If propose_replies is slow/raises, that field degrades to null — never a 500."""
    c = _seed()

    def boom(*a, **k):
        raise RuntimeError("LLM gate hung")

    h = web.api_health(c, now=1782600000, probe=_no_probe, propose=boom)
    assert h["auto_replies"] is None
    assert h["ok"] is True  # endpoint still healthy/serves


def test_health_backend_probe_degrades_to_null():
    """A slow/raising backend probe must degrade reachable→null, never block/500."""
    c = _seed()

    def boom(*a, **k):
        raise RuntimeError("backend timeout")

    h = web.api_health(c, now=1782600000, probe=boom)
    for b in h["backends"]:
        assert b["reachable"] is None
        assert b["backend_version"] is None
