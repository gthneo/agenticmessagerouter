"""fullwechat adapter — pure field mapping (live HTTP verified manually)."""
from jl.channels import fullwechat as fw
from jl import ingest


def test_map_message_to_msgrecord():
    raw = {"localId": 17, "serverId": 99, "chatId": "m1", "sender": "wxid_a",
           "senderName": "张三", "type": 1, "content": "你好",
           "timestamp": "2026-06-14T08:37:05+00:00", "isMentioned": False}
    m = fw.map_message(raw)
    assert isinstance(m, ingest.MsgRecord)
    assert m.msg_key == "fullwx:17"
    assert m.sender == "张三" and m.sender_id == "wxid_a"
    assert m.content == "你好"
    assert m.ts == fw._ts("2026-06-14T08:37:05+00:00")   # match adapter's own conversion
    assert m.ts > 0
    assert m.is_mentioned is False


def test_clean_content_text_passes_through():
    assert fw.clean_content(1, "你好呀") == "你好呀"


def test_clean_content_media_types_become_placeholders():
    assert fw.clean_content(3, "<msg><img cdnthumburl='x'/></msg>") == "[图片]"
    assert fw.clean_content(34, "...") == "[语音]"
    assert fw.clean_content(43, "...") == "[视频]"
    assert fw.clean_content(47, "...") == "[表情]"
    assert fw.clean_content(48, "...") == "[位置]"
    assert fw.clean_content(42, "...") == "[名片]"


def test_clean_content_app_msg_extracts_title_else_placeholder():
    xml = "<msg><appmsg><title>一篇好文章</title><url>http://x</url></appmsg></msg>"
    assert fw.clean_content(49, xml) == "[链接] 一篇好文章"
    assert fw.clean_content(49, "<msg><appmsg></appmsg></msg>") == "[链接/文件]"


def test_clean_content_strips_leaked_xml_even_if_typed_text():
    # defensive: a media blob mislabeled type=1 must not dump raw XML into the timeline
    blob = '<msg><img cdnthumburl="305f02..." cdnthumbaeskey="750b3c"/></msg>'
    assert fw.clean_content(1, blob) == "[图片]"


def test_map_message_uses_clean_content_for_media():
    raw = {"localId": 5, "type": 3, "content": "<msg><img cdnthumburl='z'/></msg>",
           "senderName": "张三", "sender": "wxid_a", "timestamp": "2026-06-14T08:00:00+00:00"}
    assert fw.map_message(raw).content == "[图片]"


def test_map_message_falls_back_to_serverid_when_no_localid():
    raw = {"localId": 0, "serverId": 12345, "chatId": "m1", "sender": "x",
           "senderName": "Y", "type": 1, "content": "hi",
           "timestamp": "2026-06-14T08:37:05+00:00"}
    assert fw.map_message(raw).msg_key == "fullwx:s12345"


def test_map_chat_personal_not_muted():
    raw = {"id": "m1", "name": "张三", "isGroup": False,
           "lastActivityAt": "2026-06-14T08:37:05+00:00", "unreadCount": 3}
    c = fw.map_chat(raw)
    assert c.chat_id == "m1" and c.type == "private" and c.muted is False
    assert c.unread == 3 and c.last_activity_at == fw._ts("2026-06-14T08:37:05+00:00")


def test_map_chat_group_default_muted():
    raw = {"id": "g1@chatroom", "name": "群", "isGroup": True,
           "lastActivityAt": "2026-06-14T08:37:05+00:00", "unreadCount": 999}
    c = fw.map_chat(raw)
    assert c.type == "group" and c.muted is True


def test_is_ingestable_skips_folders_and_official():
    assert fw.is_ingestable({"id": "m135", "isGroup": False}) is True
    for bad in ("brandsessionholder", "gh_abc", "placeholder_x", "_sys"):
        assert fw.is_ingestable({"id": bad, "isGroup": False}) is False


def test_all_conversations_pages_until_short_page(monkeypatch):
    # simulate an activity-sorted list dominated by official accounts across pages
    pages = {
        0: [{"id": f"gh_{i}", "isGroup": False, "name": "off",
             "lastActivityAt": "", "unreadCount": 0} for i in range(200)],
        200: [{"id": "m_real1", "isGroup": False, "name": "张三",
               "lastActivityAt": "", "unreadCount": 1},
              {"id": "g_real@chatroom", "isGroup": True, "name": "群",
               "lastActivityAt": "", "unreadCount": 9}],
    }
    a = fw.FullWechatAdapter(token="x")
    def fake_get(path):
        # parse offset from the querystring
        off = int(path.split("offset=")[1])
        return pages.get(off, [])
    monkeypatch.setattr(a, "_get", fake_get)
    convs = a.all_conversations(None, page=200)
    ids = [c.chat_id for c in convs]
    assert ids == ["m_real1", "g_real@chatroom"]   # officials skipped, real ones kept across pages


def test_pull_new_pairs_each_conv_with_its_messages(monkeypatch):
    a = fw.FullWechatAdapter(token="x")
    monkeypatch.setattr(a, "all_conversations",
                        lambda account: [ingest.ConvRecord(chat_id="m1", name="张三")])
    monkeypatch.setattr(a, "_messages",
                        lambda cid, lim, off: [ingest.MsgRecord(msg_key="fullwx:1", ts=1, content="hi")])
    out = a.pull_new(None)
    assert len(out) == 1
    conv, msgs = out[0]
    assert conv.chat_id == "m1" and msgs[0].content == "hi"
