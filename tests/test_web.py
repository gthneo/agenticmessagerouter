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


def test_api_ingest_creates_account_conv_and_messages():
    c = db.connect(":memory:"); db.init_db(c)
    payload = {
        "account": {"account_id": 2, "platform": "phone", "label": "iPhone"},
        "conversations": [
            {"conv": {"chat_id": "+8613000000001", "name": "张三", "type": "private",
                      "unread": 0, "last_activity_at": 100, "muted": False},
             "msgs": [{"msg_key": "phone:1", "ts": 100, "content": "[通话] 30s",
                       "sender": "张三", "direction": "in", "type": "call"}]},
        ],
    }
    res = web.api_ingest(c, payload)
    assert res["accounts"] == 1 and res["conversations"] == 1 and res["messages"] == 1
    convs = db.get_conversations(c)
    assert convs[0]["chat_id"] == "+8613000000001"
    assert convs[0]["platform"] == "phone"


def test_api_ingest_is_idempotent():
    c = db.connect(":memory:"); db.init_db(c)
    payload = {
        "account": {"account_id": 2, "platform": "phone"},
        "conversations": [
            {"conv": {"chat_id": "p1", "name": "X", "type": "private"},
             "msgs": [{"msg_key": "phone:1", "ts": 1, "content": "hi"}]},
        ],
    }
    web.api_ingest(c, payload)
    r2 = web.api_ingest(c, payload)
    assert r2["messages"] == 0


def test_api_ingest_round_trips_all_message_fields():
    c = db.connect(":memory:"); db.init_db(c)
    payload = {
        "account": {"account_id": 3, "platform": "feishu"},
        "conversations": [
            {"conv": {"chat_id": "oc1", "name": "群X", "type": "group",
                      "muted": True, "unread": 5, "last_activity_at": 9},
             "msgs": [{"msg_key": "lark:1", "ts": 9, "content": "见附件",
                       "sender": "张三", "sender_id": "ou_a", "direction": "out",
                       "type": "post", "media_ref": "file_abc", "is_mentioned": True}]},
        ],
    }
    web.api_ingest(c, payload)
    row = c.execute("SELECT * FROM messages WHERE msg_key='lark:1'").fetchone()
    assert row["media_ref"] == "file_abc"
    assert row["is_mentioned"] == 1
    assert row["direction"] == "out" and row["type"] == "post"
    assert row["sender_id"] == "ou_a"
