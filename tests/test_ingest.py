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
