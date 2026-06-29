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


def test_api_matters_create_filter_status():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_person(c, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w1", name="张三")
    db.link_person(c, cid, "u1")
    r = web.api_create_matter(c, {"title": "周四饭局", "conversation_ids": [cid], "person_ids": ["u1"]})
    assert r["ok"] and r["id"]
    rows = web.api_matters(c, {"conversation": str(cid)})
    assert len(rows) == 1 and rows[0]["title"] == "周四饭局"
    web.api_matter_status(c, {"id": r["id"], "status": "handled"})
    assert web.api_matters(c, {"conversation": str(cid), "status": "open"}) == []


def test_api_proactive_lists_watched_and_red_with_openers():
    import time
    c = db.connect(":memory:"); db.init_db(c)
    now = int(time.time())
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    # watched 🟢 with an opener queued
    db.upsert_person(c, id="w", name="张三", category="biz", threshold_days=14, aliases=[])
    db.set_watch(c, "w", True)
    wc = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w", name="张三", type="private")
    db.link_person(c, wc, "w")
    db.insert_messages(c, wc, [ingest.MsgRecord(msg_key="w1", ts=now - 3600, content="刚聊", direction="in")])
    db.add_suggestions(c, wc, [{"stance": "稳妥", "body": "开场"}], kind="opener")
    # fresh unwatched → excluded
    db.upsert_person(c, id="skip", name="李四", category="biz", threshold_days=14, aliases=[])
    sc = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="s2", name="李四", type="private")
    db.link_person(c, sc, "skip")
    db.insert_messages(c, sc, [ingest.MsgRecord(msg_key="s1", ts=now - 3600, content="也刚", direction="in")])
    # watched, no channel → missing
    db.upsert_person(c, id="nc", name="王五", category="biz", threshold_days=3, aliases=[])
    db.set_watch(c, "nc", True)

    rows = {r["person_id"]: r for r in web.api_proactive(c)}
    assert set(rows) == {"w", "nc"}
    assert rows["w"]["openers"] == 1 and rows["w"]["missing_channel"] is False
    assert rows["nc"]["missing_channel"] is True


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


def _seed_person(c):
    db.upsert_person(c, id="lisi", name="李四", category="family",
                     threshold_days=14, aliases=["小四"])
    db.upsert_channel(c, person_id="lisi", kind="phone", identifier="+8613000000001")
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s1")
    db.upsert_account(c, account_id=2, platform="phone", self_id="s2")
    wc = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_x", name="李四")
    pc = db.upsert_conversation(c, account_id=2, platform="phone", chat_id="13000000001", name="李四电话")
    db.insert_messages(c, wc, [ingest.MsgRecord(msg_key="w:1", ts=10, content="微信你好", sender="李四")])
    db.insert_messages(c, pc, [ingest.MsgRecord(msg_key="p:1", ts=20, content="[通话] 95s", sender="李四")])
    return c, wc, pc


def test_api_persons_lists_linked_people():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    db.link_conversations(c)
    rows = web.api_persons(c)
    assert any(r["id"] == "lisi" for r in rows)


def test_api_person_timeline_merges_channels():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    # link wechat manually (no wxid in person yet), phone auto-links via tail_match
    db.set_conversation_person(c, db.get_conversations(c, account_id=1)[0]["id"], "lisi")
    db.link_conversations(c)
    tl = web.api_person_timeline(c, "lisi")
    contents = [m["content"] for m in tl]
    assert "微信你好" in contents and "[通话] 95s" in contents
    assert [m["ts"] for m in tl] == sorted(m["ts"] for m in tl)   # merged, time-ordered


def test_api_link_confirms_and_learns():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    wcid = db.get_conversations(c, account_id=1)[0]["id"]
    res = web.api_link(c, {"conversation_id": wcid, "person_id": "lisi"})
    assert res["ok"] is True
    assert db.get_conversation(c, wcid)["person_id"] == "lisi"


def test_api_merge_candidates_present():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    cands = web.api_merge_candidates(c)
    assert any(s["name"] == "李四" for s in cands)


def _ob_conv(c):
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    return db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_t", name="张三")


def _stub_wechat_sender(monkeypatch, result, record=None):
    from jl import send
    def _send(chat_id, body):
        if record is not None:
            record.append((chat_id, body))
        return result
    monkeypatch.setitem(send.SENDERS, "wechat", _send)


def test_api_queue_outbox_creates_pending_preview():
    c = db.connect(":memory:"); db.init_db(c); cid = _ob_conv(c)
    res = web.api_queue_outbox(c, {"conversation_id": cid, "body": "你好"})
    assert res["status"] == "pending" and res["body"] == "你好" and res["id"]
    assert db.get_outbox(c, status="pending")[0]["chat_id"] == "wxid_t"


def test_api_confirm_outbox_sends_and_marks(monkeypatch):
    c = db.connect(":memory:"); db.init_db(c); cid = _ob_conv(c)
    rec = []
    _stub_wechat_sender(monkeypatch, (True, ""), rec)
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "hi"})["id"]
    res = web.api_confirm_outbox(c, {"id": oid})
    assert res["ok"] is True
    assert rec == [("wxid_t", "hi")]
    assert db.get_outbox_row(c, oid)["status"] == "sent"
    assert "send" in [e["kind"] for e in db.get_events(c)]


def _out_msgs(c, cid):
    return [dict(r) for r in c.execute(
        "SELECT * FROM messages WHERE conversation_id=? AND direction='out' ORDER BY id",
        (cid,)).fetchall()]


def test_api_confirm_outbox_persists_sent_message(monkeypatch):
    """A successful send writes an out-message into `messages` so the human sees it on
    re-open/refresh (0号宪法: 结果回交给人看)."""
    c = db.connect(":memory:"); db.init_db(c); cid = _ob_conv(c)
    _stub_wechat_sender(monkeypatch, (True, ""))
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "去火星需要做一些什么准备？"})["id"]
    res = web.api_confirm_outbox(c, {"id": oid})
    assert res["ok"] is True
    outs = _out_msgs(c, cid)
    assert len(outs) == 1
    assert outs[0]["content"] == "去火星需要做一些什么准备？"
    assert outs[0]["direction"] == "out"
    assert outs[0]["type"] == "text"
    # No false contract-violation collision (content-hash key is unique per content+ts).
    assert "contract_violation" not in [e["kind"] for e in db.get_events(c)]


def test_api_confirm_outbox_no_persist_on_failure(monkeypatch):
    c = db.connect(":memory:"); db.init_db(c); cid = _ob_conv(c)
    _stub_wechat_sender(monkeypatch, (False, "offline"))
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "hi"})["id"]
    web.api_confirm_outbox(c, {"id": oid})
    assert _out_msgs(c, cid) == []   # failed send leaves no message


def test_api_confirm_outbox_content_dedup_guard(monkeypatch):
    """Two confirmed sends of identical content within the dedup window collapse to ONE
    persisted out-message (absorbs a possible poll re-ingest of our own send)."""
    c = db.connect(":memory:"); db.init_db(c); cid = _ob_conv(c)
    _stub_wechat_sender(monkeypatch, (True, ""))
    for _ in range(2):
        oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "同一句话"})["id"]
        web.api_confirm_outbox(c, {"id": oid})
    assert len(_out_msgs(c, cid)) == 1


def test_api_confirm_outbox_send_failure_marks_failed(monkeypatch):
    c = db.connect(":memory:"); db.init_db(c); cid = _ob_conv(c)
    _stub_wechat_sender(monkeypatch, (False, "offline"))
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "hi"})["id"]
    res = web.api_confirm_outbox(c, {"id": oid})
    assert res["ok"] is False
    assert db.get_outbox_row(c, oid)["status"] == "failed"


def test_api_confirm_outbox_rejects_nonpending():
    c = db.connect(":memory:"); db.init_db(c); cid = _ob_conv(c)
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "hi"})["id"]
    db.mark_outbox(c, oid, "sent")
    res = web.api_confirm_outbox(c, {"id": oid})
    assert res["ok"] is False   # already sent -> not re-sendable


def test_api_cancel_outbox():
    c = db.connect(":memory:"); db.init_db(c); cid = _ob_conv(c)
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "hi"})["id"]
    web.api_cancel_outbox(c, {"id": oid})
    assert db.get_outbox_row(c, oid)["status"] == "canceled"
    assert db.get_outbox(c, status="pending") == []


def _sg_conv(c):
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w", name="张三")
    db.add_suggestions(c, cid, [{"version_idx": 0, "stance": "稳妥", "body": "稳妥版",
                                 "llm_provider": "fake", "llm_model": "f1"}])
    return cid


def test_api_suggestions_lists_for_conversation():
    c = db.connect(":memory:"); db.init_db(c); cid = _sg_conv(c)
    rows = web.api_suggestions(c, cid)
    assert len(rows) == 1 and rows[0]["body"] == "稳妥版"


def test_api_dismiss_suggestion():
    c = db.connect(":memory:"); db.init_db(c); cid = _sg_conv(c)
    sid = web.api_suggestions(c, cid)[0]["id"]
    res = web.api_dismiss_suggestion(c, {"id": sid})
    assert res["ok"] is True and web.api_suggestions(c, cid) == []


def test_api_generate_drafts_llm_optional(monkeypatch):
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w", name="张三")
    from jl import llm
    monkeypatch.setattr(llm, "available", lambda: False)
    res = web.api_generate_drafts(c, {"conversation_id": cid})
    assert res["ok"] is False and "llm" in res["error"].lower()


# ----- v0.8 settings: SELF / reunify / watch -------------------------------

def test_api_self_lists_registered_and_suggestions():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="wxid_me")
    db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_helper",
                           name="文件传输助手", type="private")
    db.add_self_identity(c, "phone", "+8613000000000", persona="自我")
    d = web.api_self(c)
    # phone stored canonical (digits, no +86)
    assert any(r["identifier"] == "13000000000" for r in d["registered"])
    # only the account self_id is suggested — name-hint guessing removed (no 文件传输助手 garbage)
    sug_ids = {s["identifier"] for s in d["suggestions"]}
    assert "wxid_me" in sug_ids and "wxid_helper" not in sug_ids


def test_api_add_and_remove_self():
    c = db.connect(":memory:"); db.init_db(c)
    res = web.api_add_self(c, {"kind": "wechat", "identifier": "wxid_me",
                               "persona": "AI分身", "label": "分身号"})
    assert res["ok"] is True
    reg = web.api_self(c)["registered"]
    row = next(r for r in reg if r["identifier"] == "wxid_me")
    assert row["persona"] == "AI分身"
    assert "self_add" in [e["kind"] for e in db.get_events(c)]
    web.api_remove_self(c, {"kind": "wechat", "identifier": "wxid_me"})
    assert web.api_self(c)["registered"] == []


def test_api_reunify_returns_stats():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    res = web.api_reunify(c, {})
    assert res["ok"] is True and "linked" in res and "candidates" in res
    assert "reunify" in [e["kind"] for e in db.get_events(c)]


def test_api_watch():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_person(c, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    res = web.api_watch(c, {"person_id": "u1", "on": True})
    assert res["ok"] is True
    assert next(p for p in db.get_persons(c) if p["id"] == "u1")["watch"]
    assert "watch" in [e["kind"] for e in db.get_events(c)]


def test_api_unlink_no_link_returns_gracefully():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w", name="张三")
    res = web.api_unlink(c, {"conversation_id": cid})
    assert res["ok"] is False and res["freed"] is None
