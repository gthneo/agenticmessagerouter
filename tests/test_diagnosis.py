"""T4 诊断引擎 — rubric loader, structured parse, diagnose (fake llm), LLM-optional."""
from jl import db, diagnosis, llm, ingest


def _seed():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_person(c, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w1", name="张三")
    db.link_person(c, cid, "u1")
    db.insert_messages(c, cid, [ingest.MsgRecord(msg_key="m1", ts=1, content="你说漂亮话", sender="张三", direction="in")])
    return c, cid


_FAKE_JSON = ('好的，这是诊断：\n```json\n'
              '{"对方姿态":"牙","对等":"过软","圆":"ok","方":"先","力":"无",'
              '"错位":"方先","真心":"ok","一句话诊断":"对方在质疑，别急着讲道理",'
              '"口径":"先认对方的对，再换语音","额外":"忽略我"}\n```')


def test_load_rubric_reads_local_file(tmp_path, monkeypatch):
    p = tmp_path / "rubric.md"
    p.write_text("外圆内方：圆先接→方过滤→力释放。以牙还牙以蜜还蜜。", encoding="utf-8")
    monkeypatch.setenv("AMR_DIAGNOSIS_RUBRIC", str(p))
    assert "以牙还牙" in diagnosis.load_rubric()


def test_load_rubric_absent_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AMR_DIAGNOSIS_RUBRIC", str(tmp_path / "nope.md"))
    assert diagnosis.load_rubric() == ""


def test_parse_diagnosis_extracts_json_and_filters_keys():
    d = diagnosis.parse_diagnosis(_FAKE_JSON)
    assert d["对方姿态"] == "牙" and d["对等"] == "过软" and d["错位"] == "方先"
    assert d["一句话诊断"].startswith("对方在质疑")
    assert "额外" not in d                       # unknown key filtered out


def test_parse_diagnosis_garbage_returns_empty():
    assert diagnosis.parse_diagnosis("抱歉我无法诊断") == {}


def test_build_diagnosis_context_has_tit_for_tat_and_timeline():
    c, cid = _seed()
    msgs = diagnosis.build_diagnosis_context(c, cid, rubric="")
    sysmsg = next(m["content"] for m in msgs if m["role"] == "system")
    usermsg = next(m["content"] for m in msgs if m["role"] == "user")
    assert "以牙还牙" in sysmsg and "外圆内方" in sysmsg
    assert "你说漂亮话" in usermsg


def test_diagnose_stores_structured_on_matter():
    c, cid = _seed()
    mid = db.create_matter(c, title="话术被识破", person_ids=["u1"], conversation_ids=[cid])
    fake = lambda messages, **kw: llm.LLMResult(text=_FAKE_JSON, ok=True, provider="f", model="f1")
    d = diagnosis.diagnose(c, cid, matter_id=mid, llm_complete=fake)
    assert d["对方姿态"] == "牙"
    stored = db.get_matters(c)[0]["diagnosis"]
    assert stored["错位"] == "方先" and stored["一句话诊断"].startswith("对方在质疑")


def test_diagnose_llm_unavailable_returns_empty():
    c, cid = _seed()
    fake = lambda messages, **kw: llm.LLMResult(ok=False, error="llm_unavailable")
    assert diagnosis.diagnose(c, cid, llm_complete=fake) == {}


def test_diagnosis_口径_drives_draft_context():
    from jl import assist
    c, cid = _seed()
    mid = db.create_matter(c, title="话术被识破", person_ids=["u1"], conversation_ids=[cid])
    db.set_matter_diagnosis(c, mid, {"口径": "先认对方的对，再换语音", "一句话诊断": "别讲漂亮话"})
    # generate_drafts must inject the 口径 into the system prompt
    captured = {}
    def fake(messages, **kw):
        captured["sys"] = next(m["content"] for m in messages if m["role"] == "system")
        return llm.LLMResult(text="1) 稳妥: A\n2) 直接: B\n3) 有温度: C", ok=True, provider="f", model="f")
    assert assist.generate_drafts(c, cid, llm_complete=fake) == 3
    assert "先认对方的对" in captured["sys"]   # diagnosis 口径 reached the draft prompt
