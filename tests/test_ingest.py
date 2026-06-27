"""Pure ingestion helpers — msg_key, content_hash, blob_path, dataclasses."""
from jl import ingest


def test_msg_key_uses_stable_id_when_present():
    assert ingest.msg_key(source="fullwx", stable_id="12345") == "fullwx:12345"


def test_msg_key_falls_back_to_content_hash():
    k = ingest.msg_key(source="powerdata", stable_id=None,
                       ts=1000, sender="张三", content="明天见")
    assert k.startswith("h:")
    assert len(k) == 2 + 16  # "h:" + 16 hex chars


def test_msg_key_content_hash_is_stable_for_same_inputs():
    a = ingest.msg_key(source="x", stable_id=None, ts=1000, sender="张三", content="hi")
    b = ingest.msg_key(source="x", stable_id=None, ts=1000, sender="张三", content="hi")
    assert a == b


def test_msg_key_content_hash_differs_on_different_content():
    a = ingest.msg_key(source="x", stable_id=None, ts=1000, sender="张三", content="hi")
    b = ingest.msg_key(source="x", stable_id=None, ts=1000, sender="张三", content="yo")
    assert a != b


def test_content_hash_minute_collision_is_intentional():
    # same minute + sender + content is treated as the same message
    h1 = ingest.content_hash(ts=1000, sender="张三", content="hi")
    h2 = ingest.content_hash(ts=1000, sender="张三", content="hi")
    assert h1 == h2


def test_blob_path_is_sharded_by_hash_prefix():
    sha = "ab" + "0" * 62
    assert ingest.blob_path(sha) == "blobs/ab/" + sha


def test_blob_path_with_root():
    sha = "cd" + "1" * 62
    assert ingest.blob_path(sha, root="/data") == "/data/blobs/cd/" + sha


def test_msgrecord_defaults():
    m = ingest.MsgRecord(msg_key="x:1", ts=10, content="hi")
    assert m.direction == "in"
    assert m.type == "text"
    assert m.sender == ""


def test_convrecord_defaults():
    c = ingest.ConvRecord(chat_id="c1", name="张三")
    assert c.type == "private"
    assert c.muted is False


def test_msg_key_empty_stable_id_falls_back_to_hash():
    k = ingest.msg_key(source="x", stable_id="", ts=1000, sender="张三", content="hi")
    assert k.startswith("h:")


def test_msg_key_zero_string_stable_id_is_kept():
    # "0" is a non-empty string -> a real id, not a fallback
    assert ingest.msg_key(source="x", stable_id="0") == "x:0"


# ---- Message Channel canonical envelope → MsgRecord (薄映射) -----------------
from jl import ingest as _ig


def test_is_canonical_detects_schema_and_kind():
    assert _ig.is_canonical({"schema": "message.canonical/1", "kind": "text", "text": "hi"})
    assert _ig.is_canonical({"kind": "link", "text": "[链接]"})       # kind+text 也算
    assert not _ig.is_canonical({"localId": 5, "content": "hi", "type": 1})  # 原始 fullwechat dict
    assert not _ig.is_canonical("not a dict")


def test_from_canonical_text_minimal():
    m = _ig.from_canonical({"channel": "wechat", "kind": "text", "text": "你好",
                            "ts": 1750000000, "sender": "张三", "direction": "in"})
    assert m.type == "text" and m.content == "你好" and m.sender == "张三"
    assert m.direction == "in" and m.raw == {}


def test_from_canonical_link_puts_subobject_in_raw():
    m = _ig.from_canonical({"channel": "wechat", "kind": "link", "text": "[链接] 标题",
                            "ts": 1, "sender": "张三",
                            "link": {"title": "标题", "url": "https://x", "source": "公众号"}})
    assert m.type == "link"
    assert m.raw == {"title": "标题", "url": "https://x", "source": "公众号"}


def test_from_canonical_quote_and_system():
    q = _ig.from_canonical({"kind": "quote", "text": "好的", "ts": 1, "sender": "我",
                            "direction": "out", "quote": {"author": "张三", "refText": "初稿"}})
    assert q.type == "quote" and q.direction == "out" and q.raw["author"] == "张三"
    s = _ig.from_canonical({"kind": "system", "text": "拍了拍", "ts": 1,
                            "system": {"event": "pat", "text": "张三 拍了拍 你"}})
    assert s.type == "system" and s.raw["event"] == "pat"


def test_from_canonical_media_ref_and_msgid_key():
    m = _ig.from_canonical({"channel": "wechat", "kind": "voice", "text": "[语音]", "ts": 1,
                            "sender": "张三", "msg_id": "abc123",
                            "media": {"placeholder": "[语音]", "ref": "cdn://v"}})
    assert m.type == "voice" and m.media_ref == "cdn://v"
    assert m.msg_key == "wechat:abc123"   # msg_id → stable key


def test_from_canonical_no_msgid_falls_to_content_hash():
    m = _ig.from_canonical({"channel": "wechat", "kind": "text", "text": "hi", "ts": 60, "sender": "a"})
    assert m.msg_key.startswith("h:")     # 无 msg_id → 内容哈希键


def test_fullwechat_map_message_uses_canonical_when_present():
    from jl.channels import fullwechat as fw
    env = {"schema": "message.canonical/1", "channel": "wechat", "kind": "file",
           "text": "[文件]", "ts": 1, "sender": "张三", "direction": "in",
           "file": {"name": "a.pdf", "ext": "pdf"}}
    m = fw.map_message(env)
    assert m.type == "file" and m.raw == {"name": "a.pdf", "ext": "pdf"}
    # 原始(非 canonical) 仍走旧解析
    raw = {"localId": 9, "type": 1, "content": "hi", "senderName": "李四", "timestamp": ""}
    m2 = fw.map_message(raw)
    assert m2.type == "text" and m2.content == "hi" and m2.msg_key.startswith("fullwx:")
