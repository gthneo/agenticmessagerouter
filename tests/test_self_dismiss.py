"""SELF-suggestion dismiss (✕ 不是我) — a rejected self-candidate must stop being
suggested, persistently and reversibly (归一 UI 的 Delete 角; #85)."""
from jl import db, web


def _seed():
    c = db.connect(":memory:")
    db.init_db(c)
    # an account whose self_id is NOT yet declared as self → it gets suggested
    db.upsert_account(c, account_id=5, platform="wechat", self_id="wxid_test_bandi2277")
    return c


def test_candidate_suggested_by_default():
    c = _seed()
    sugg = db.suggest_self_identities(c)
    assert any(s["identifier"] == "wxid_test_bandi2277" for s in sugg)


def test_dismiss_removes_from_suggestions():
    c = _seed()
    db.dismiss_self_candidate(c, "wechat", "wxid_test_bandi2277")
    sugg = db.suggest_self_identities(c)
    assert not any(s["identifier"] == "wxid_test_bandi2277" for s in sugg)
    assert db.get_self_dismissed(c)          # persisted (canonicalized key)


def test_dismiss_matches_device_suffix_variants():
    # dismiss the base; a device-suffixed self_id of the same account stays dismissed
    # (canonicalized: wxid_bandi_2325 → base wxid_bandi)
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=5, platform="wechat", self_id="wxid_bandi_2325")
    db.dismiss_self_candidate(c, "wechat", "wxid_bandi")   # dismiss base
    assert not any(s["identifier"] == "wxid_bandi_2325" for s in db.suggest_self_identities(c))


def test_undismiss_restores_suggestion():
    c = _seed()
    db.dismiss_self_candidate(c, "wechat", "wxid_test_bandi2277")
    db.undismiss_self_candidate(c, "wechat", "wxid_test_bandi2277")
    assert any(s["identifier"] == "wxid_test_bandi2277" for s in db.suggest_self_identities(c))


def test_dismiss_does_not_add_to_self():
    c = _seed()
    db.dismiss_self_candidate(c, "wechat", "wxid_test_bandi2277")
    assert db.is_self(c, "wechat", "wxid_test_bandi2277") is False   # dismissed ≠ self


def test_api_self_dismiss_endpoint():
    c = _seed()
    r = web.api_self_dismiss(c, {"kind": "wechat", "identifier": "wxid_test_bandi2277"})
    assert r["ok"] is True
    assert not any(s["identifier"] == "wxid_test_bandi2277"
                   for s in db.suggest_self_identities(c))


def test_unify_ui_has_dismiss_and_plain_language():
    """归一界面自明性: 建议候选要有「✕ 不是我」dismiss, 归一按钮要有人话说明。"""
    html = web._index_html()
    assert "dismissSelf" in html            # ✕ 不是我 的前端函数
    assert "/self/dismiss" in html          # dismiss endpoint
    assert "不是我" in html                  # 明确的拒绝动作文案


def test_api_self_includes_source():
    """每条已认作我的身份带「渠道源」，解释多变体从何而来。"""
    c = _seed()  # account 5 self_id wxid_test_bandi2277
    db.add_self_identity(c, "wechat", "wxid_test_bandi2277", persona="工作", label="测试")
    res = web.api_self(c)
    reg = res["registered"]
    assert reg and all("source" in s for s in reg)
    assert any("账号#5" in s["source"] for s in reg)   # 至少一条匹配到接入账号


def test_unify_ui_collapsible_and_human_result():
    html = web._index_html()
    assert "<details" in html               # 「已认作我」可折叠
    assert "渠道源" in html or "· 源:" in html  # 渠道源标注
    assert "认人跑完" in html and "都已认好" in html  # 0/0 说人话
