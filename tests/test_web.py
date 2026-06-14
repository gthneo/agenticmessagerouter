"""Web data handlers — pure, over an in-memory store (no socket)."""
from jl import db, ingest, web


def _seed():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    m = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="m1",
                               name="张三", type="private")
    db.insert_messages(c, m, [ingest.MsgRecord(msg_key="x:1", ts=100, content="带合同来", sender="张三")])
    g = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="g1",
                               name="群", type="group")
    db.set_muted(c, g, True)
    return c


def test_api_conversations_excludes_muted_by_default():
    c = _seed()
    rows = web.api_conversations(c, {})
    names = [r["name"] for r in rows]
    assert "张三" in names and "群" not in names


def test_api_conversations_include_muted():
    c = _seed()
    names = [r["name"] for r in web.api_conversations(c, {"muted": "1"})]
    assert "群" in names


def test_api_messages_returns_conversation_messages():
    c = _seed()
    conv = web.api_conversations(c, {})[0]
    msgs = web.api_messages(c, conv["id"])
    assert msgs[0]["content"] == "带合同来"


def test_api_search_hits_content():
    c = _seed()
    hits = web.api_search(c, "合同")
    assert len(hits) == 1 and hits[0]["content"] == "带合同来"


def test_api_search_empty_query_returns_empty():
    c = _seed()
    assert web.api_search(c, "") == []


def test_api_messages_bad_id_path_is_404(monkeypatch):
    import threading, time, urllib.request, urllib.error, tempfile
    from jl import web, db
    p = tempfile.mktemp(suffix=".db"); c = db.connect(p); db.init_db(c); c.close()
    t = threading.Thread(target=web.serve,
                         kwargs={"conn_path": p, "host": "127.0.0.1", "port": 8094},
                         daemon=True); t.start()
    time.sleep(0.5)
    try:
        urllib.request.urlopen("http://127.0.0.1:8094/api/conversations/abc/messages")
        assert False, "expected HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 404
