"""Runtime contract boundary checks — THE TEETH against the 2026-06-28 msg_id bug.

Two orthogonal checks live here:

1. **Key-collision detection at ingest** (db.insert_messages): two DIFFERENT
   messages landing on the SAME (conversation_id, msg_key) — the exact shape of the
   2026-06-28 故障 (msg_id = 会话内 localId → 新消息撞老 key → INSERT OR IGNORE 静默丢弃).
   We do NOT change dedup; we DETECT the genuine collision and raise a LOUD
   `contract_violation` event + count it, so the next time it happens it is obvious.

2. **Structural canonical validation** (contract_validate.validate_canonical): a pure
   function returning violation strings for a canonical envelope (required fields, closed
   kind enum, direction in {in,out}, msg_id a non-empty string if present).

All fixtures are synthetic placeholders (张三 / wxid_test_*).
"""
from jl import db, ingest, contract_validate


# ----- 1a. key-collision detection ------------------------------------------

def _seed_conv():
    c = db.connect(":memory:")
    db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="self_wxid_test")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat",
                                 chat_id="wxid_test_zhangsan", name="张三")
    return c, cid


def _msg(key, *, ts, content):
    return ingest.MsgRecord(msg_key=key, ts=ts, content=content, sender="张三",
                            sender_id="wxid_test_zhangsan", direction="in")


def test_true_duplicate_is_not_a_collision():
    """SAME key + SAME content + SAME ts = a genuine re-poll duplicate → no alarm."""
    c, cid = _seed_conv()
    db.insert_messages(c, cid, [_msg("fullwx:K", ts=100, content="带合同来")])
    res = db.insert_messages(c, cid, [_msg("fullwx:K", ts=100, content="带合同来")])
    # no new row, no collision event
    assert db.collision_count(res) == 0
    viol = [e for e in db.get_events(c) if e["kind"] == "contract_violation"]
    assert viol == []


def test_key_collision_is_detected_and_alarmed():
    """SAME (conv, key) but DIFFERENT content = the 2026-06-28 bug shape → LOUD."""
    c, cid = _seed_conv()
    db.insert_messages(c, cid, [_msg("fullwx:K", ts=100, content="第一条·老消息A")])
    # a genuinely different message reusing the same key (localId reuse)
    res = db.insert_messages(c, cid, [_msg("fullwx:K", ts=200, content="第二条·新消息B")])

    assert db.collision_count(res) == 1, "the collision must be COUNTED"
    viol = [e for e in db.get_events(c) if e["kind"] == "contract_violation"]
    assert len(viol) == 1, "a contract_violation event must be logged"
    d = viol[0]["detail"]
    assert d["type"] == "msg_key_collision"
    assert d["conversation_id"] == cid
    assert d["msg_key"] == "fullwx:K"


def test_collision_detail_is_pii_free():
    """The alarm carries ONLY ids/keys — NO message content / sender PII."""
    c, cid = _seed_conv()
    db.insert_messages(c, cid, [_msg("fullwx:K", ts=100, content="老内容含敏感词张三")])
    db.insert_messages(c, cid, [_msg("fullwx:K", ts=200, content="新内容也敏感wxid_test")])
    viol = [e for e in db.get_events(c) if e["kind"] == "contract_violation"][0]
    blob = repr(viol["detail"])
    assert "老内容" not in blob and "新内容" not in blob
    assert "张三" not in blob


def test_dedup_behavior_unchanged_keeps_first_row():
    """Collision detection must NOT change dedup: the FIRST row is kept (still IGNORE)."""
    c, cid = _seed_conv()
    db.insert_messages(c, cid, [_msg("fullwx:K", ts=100, content="老消息A")])
    db.insert_messages(c, cid, [_msg("fullwx:K", ts=200, content="新消息B")])
    rows = c.execute("SELECT content FROM messages WHERE conversation_id=?", (cid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["content"] == "老消息A"   # original kept, not overwritten


def test_distinct_keys_no_collision():
    c, cid = _seed_conv()
    res = db.insert_messages(c, cid, [
        _msg("fullwx:K1", ts=100, content="A"),
        _msg("fullwx:K2", ts=200, content="B"),
    ])
    assert db.collision_count(res) == 0


# ----- 1b. validate_canonical (pure) ----------------------------------------

def _good_env(**over):
    env = {
        "schema": "message.canonical/1",
        "channel": "wechat",
        "kind": "text",
        "text": "你好",
        "ts": 1750000000,
        "direction": "in",
        "sender": "张三",
    }
    env.update(over)
    return env


def test_valid_envelope_no_violations():
    assert contract_validate.validate_canonical(_good_env()) == []


def test_missing_required_fields_reported():
    env = _good_env()
    del env["text"]
    del env["direction"]
    viol = contract_validate.validate_canonical(env)
    assert any("text" in v for v in viol)
    assert any("direction" in v for v in viol)


def test_kind_must_be_in_closed_enum():
    viol = contract_validate.validate_canonical(_good_env(kind="frobnicate"))
    assert any("kind" in v for v in viol)
    # a real kind passes
    assert contract_validate.validate_canonical(_good_env(kind="quote")) == []


def test_direction_must_be_in_or_out():
    assert any("direction" in v
               for v in contract_validate.validate_canonical(_good_env(direction="sideways")))
    assert contract_validate.validate_canonical(_good_env(direction="out")) == []


def test_msg_id_if_present_must_be_nonempty_string():
    # absent msg_id is fine
    assert contract_validate.validate_canonical(_good_env()) == []
    # present but empty → violation
    assert any("msg_id" in v
               for v in contract_validate.validate_canonical(_good_env(msg_id="")))
    # present non-string → violation
    assert any("msg_id" in v
               for v in contract_validate.validate_canonical(_good_env(msg_id=123)))
    # present valid string → ok
    assert contract_validate.validate_canonical(_good_env(msg_id="srv_9001")) == []


def test_non_dict_envelope_is_a_violation():
    assert contract_validate.validate_canonical(None)
    assert contract_validate.validate_canonical("nope")


# ----- 1b wiring: check_and_log (opt-in, gated, validate-and-warn) -----------

def test_check_and_log_alarms_bad_canonical_envelope():
    c, _ = _seed_conv()
    bad = _good_env(kind="bogus")          # canonical (has schema+kind+text) but bad kind
    n = contract_validate.check_and_log(c, [bad], channel="wechat", enabled=True)
    assert n == 1
    viol = [e for e in db.get_events(c) if e["kind"] == "contract_violation"]
    assert len(viol) == 1
    assert viol[0]["detail"]["type"] == "schema"


def test_check_and_log_skips_noncanonical_and_clean():
    c, _ = _seed_conv()
    legacy = {"localId": 5, "type": 1, "content": "你好"}   # not canonical → skipped
    good = _good_env()
    n = contract_validate.check_and_log(c, [legacy, good], enabled=True)
    assert n == 0
    assert [e for e in db.get_events(c) if e["kind"] == "contract_violation"] == []


def test_check_and_log_disabled_is_a_noop():
    c, _ = _seed_conv()
    n = contract_validate.check_and_log(c, [_good_env(kind="bogus")], enabled=False)
    assert n == 0
    assert [e for e in db.get_events(c) if e["kind"] == "contract_violation"] == []


def test_check_and_log_pii_free_detail():
    c, _ = _seed_conv()
    bad = _good_env(kind="bogus", text="敏感正文带合同张三", sender="张三")
    contract_validate.check_and_log(c, [bad], enabled=True)
    viol = [e for e in db.get_events(c) if e["kind"] == "contract_violation"][0]
    blob = repr(viol["detail"])
    assert "敏感正文" not in blob and "张三" not in blob
