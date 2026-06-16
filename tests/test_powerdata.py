"""PowerData adapter — pure prose parsers (live MCP-over-HTTP verified manually)."""
from jl.channels import powerdata as pd
from jl import ingest


SESSIONS_SAMPLE = (
    "最近 6 个会话:\n"
    "\n"
    "[06-16 20:04] 工聯會福州家長群 [群] (575条未读)\n"
    "  链接/文件: 冰冰: 教学...\n"
    "\n"
    "[06-16 20:03] 历史与影像 (70条未读)\n"
    "  文本: 永别了\n"
)

HISTORY_SAMPLE = (
    "中国企业家杂志 的消息记录（返回 3 条...）:\n"
    "\n"
    "[2026-06-16 07:59] 中国企业家杂志: [链接] 标题\n"
    "[2026-06-16 12:59] me: 我说的话\n"
    "[2026-06-16 13:00] 李向泉: [图片]\n"
)


# ---- parse_sessions --------------------------------------------------------
def test_parse_sessions_count_and_fields():
    out = pd.parse_sessions(SESSIONS_SAMPLE)
    assert len(out) == 2
    assert all(set(s) == {"name", "is_group", "unread", "preview"} for s in out)


def test_parse_sessions_group_detection():
    out = pd.parse_sessions(SESSIONS_SAMPLE)
    grp, priv = out[0], out[1]
    assert grp["name"] == "工聯會福州家長群" and grp["is_group"] is True
    assert priv["name"] == "历史与影像" and priv["is_group"] is False


def test_parse_sessions_unread_is_int():
    out = pd.parse_sessions(SESSIONS_SAMPLE)
    assert out[0]["unread"] == 575 and isinstance(out[0]["unread"], int)
    assert out[1]["unread"] == 70


def test_parse_sessions_preview_prefix_stripped():
    out = pd.parse_sessions(SESSIONS_SAMPLE)
    assert out[0]["preview"] == "冰冰: 教学..."   # "链接/文件: " stripped
    assert out[1]["preview"] == "永别了"           # "文本: " stripped


def test_parse_sessions_skips_header_line():
    # the "最近 6 个会话:" header must not become an entry
    names = [s["name"] for s in pd.parse_sessions(SESSIONS_SAMPLE)]
    assert "最近 6 个会话:" not in names


def test_parse_sessions_empty_input():
    assert pd.parse_sessions("") == []


# ---- parse_history ---------------------------------------------------------
def test_parse_history_count_and_type():
    out = pd.parse_history(HISTORY_SAMPLE)
    assert len(out) == 3
    assert all(isinstance(m, ingest.MsgRecord) for m in out)


def test_parse_history_ts_parsed_positive():
    out = pd.parse_history(HISTORY_SAMPLE)
    assert all(m.ts > 0 for m in out)
    assert out[0].ts == pd._ts("2026-06-16 07:59")


def test_parse_history_sender_content_split():
    out = pd.parse_history(HISTORY_SAMPLE)
    assert out[0].sender == "中国企业家杂志"
    assert out[0].content == "[链接] 标题"


def test_parse_history_media_placeholder_kept():
    out = pd.parse_history(HISTORY_SAMPLE)
    assert out[2].sender == "李向泉" and out[2].content == "[图片]"


def test_parse_history_self_label_is_out():
    out = pd.parse_history(HISTORY_SAMPLE)
    me = out[1]
    assert me.sender == "me" and me.direction == "out"
    assert me.content == "我说的话"


def test_parse_history_others_are_in():
    out = pd.parse_history(HISTORY_SAMPLE)
    assert out[0].direction == "in" and out[2].direction == "in"


def test_parse_history_custom_self_label():
    text = "[2026-06-16 07:59] 班迪: hi\n[2026-06-16 08:00] 张三: yo\n"
    out = pd.parse_history(text, self_label="班迪")
    assert out[0].direction == "out" and out[1].direction == "in"


def test_parse_history_msg_key_deterministic_and_stable():
    a = pd.parse_history(HISTORY_SAMPLE)
    b = pd.parse_history(HISTORY_SAMPLE)
    assert [m.msg_key for m in a] == [m.msg_key for m in b]   # stable across runs
    assert len({m.msg_key for m in a}) == 3                    # distinct per message
    assert all(m.msg_key.startswith("powerdata:") for m in a)


def test_parse_history_msg_key_matches_ingest_helper():
    m = pd.parse_history(HISTORY_SAMPLE)[0]
    import hashlib
    stable = hashlib.sha1(f"{m.ts}|{m.sender}|{m.content}".encode("utf-8")).hexdigest()[:16]
    assert m.msg_key == ingest.msg_key(source="powerdata", stable_id=stable)


def test_parse_history_empty_input():
    assert pd.parse_history("") == []


# ---- adapter capability contract -------------------------------------------
def test_adapter_is_readonly():
    assert pd.PowerDataAdapter.can_send is False
    assert pd.PowerDataAdapter.tool == "powerdata"
    assert pd.PowerDataAdapter.platform == "wechat"


def test_adapter_is_ingest_subclass():
    assert issubclass(pd.PowerDataAdapter, ingest.IngestAdapter)


def test_send_refuses():
    a = pd.PowerDataAdapter(token="x")
    ok, err = a.send("某人", "你好")
    assert ok is False and err


def test_backfill_returns_done():
    a = pd.PowerDataAdapter(token="x")
    msgs, cursor = a.backfill(None, ingest.ConvRecord(chat_id="某人"), "")
    assert msgs == [] and cursor == ""


def test_list_conversations_maps_sessions(monkeypatch):
    a = pd.PowerDataAdapter(token="x")
    monkeypatch.setattr(a, "_call", lambda name, **kw: SESSIONS_SAMPLE)
    convs = a.list_conversations(None)
    assert [c.chat_id for c in convs] == ["工聯會福州家長群", "历史与影像"]
    grp = convs[0]
    assert grp.type == "group" and grp.muted is True and grp.unread == 575
    assert convs[1].type == "private" and convs[1].muted is False


def test_pull_new_pairs_conv_with_messages(monkeypatch):
    a = pd.PowerDataAdapter(token="x")

    def fake_call(name, **kw):
        if name == "get_recent_sessions":
            return SESSIONS_SAMPLE
        return HISTORY_SAMPLE

    monkeypatch.setattr(a, "_call", fake_call)
    out = a.pull_new(None)
    assert len(out) == 2
    conv, msgs = out[0]
    assert conv.chat_id == "工聯會福州家長群"
    assert len(msgs) == 3 and msgs[0].content == "[链接] 标题"
