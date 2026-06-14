"""ignite/poll orchestration with a fake adapter (no network)."""
from jl import db, ingest, ingest_run


class FakeAdapter(ingest.IngestAdapter):
    platform = "wechat"
    def __init__(self, convs):
        self._convs = convs  # list of (ConvRecord, [MsgRecord])
    def list_conversations(self, account, **kw):
        return [c for c, _ in self._convs]
    def backfill(self, account, conv, cursor):
        return [], ""
    def pull_new(self, account, **kw):
        return self._convs


def _db():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="self")
    return c


def test_ignite_ingests_all_conversations():
    conn = _db()
    convs = [
        (ingest.ConvRecord(chat_id="m1", name="张三", type="private"),
         [ingest.MsgRecord(msg_key="fullwx:1", ts=100, content="hi", sender="张三")]),
        (ingest.ConvRecord(chat_id="g1", name="群", type="group", muted=True),
         [ingest.MsgRecord(msg_key="fullwx:2", ts=200, content="yo", sender="李四")]),
    ]
    n = ingest_run.ignite(conn, FakeAdapter(convs), account_id=1)
    assert n == 2
    assert len(db.get_conversations(conn)) == 2
    assert len(db.get_conversations(conn, muted=False)) == 1   # group muted out of active feed
    assert "ignite" in [e["kind"] for e in db.get_events(conn)]


def test_ignite_is_idempotent():
    conn = _db()
    convs = [(ingest.ConvRecord(chat_id="m1", name="张三", type="private"),
              [ingest.MsgRecord(msg_key="fullwx:1", ts=100, content="hi")])]
    ingest_run.ignite(conn, FakeAdapter(convs), account_id=1)
    n2 = ingest_run.ignite(conn, FakeAdapter(convs), account_id=1)
    assert n2 == 0
