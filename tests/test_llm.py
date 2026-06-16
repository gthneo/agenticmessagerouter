"""LLM abstraction — registry/route/LLM-optional/token-accounting (fake provider)."""
from jl import llm, db


def _fake_provider(result):
    def p(messages, **opts):
        return llm.LLMResult(text=result, provider="fake", model="fake-1",
                             tokens_in=11, tokens_out=7, latency_ms=1, ok=True, error="")
    return p


def test_complete_dispatches_to_registered_provider(monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "fake", _fake_provider("hi"))
    monkeypatch.setattr(llm, "route", lambda task: "fake")
    r = llm.complete([{"role": "user", "content": "x"}], task="reply")
    assert r.ok is True and r.text == "hi" and r.provider == "fake"


def test_complete_llm_optional_when_no_provider(monkeypatch):
    monkeypatch.setattr(llm, "route", lambda task: None)
    r = llm.complete([{"role": "user", "content": "x"}], task="reply")
    assert r.ok is False and r.error == "llm_unavailable" and r.text == ""


def test_complete_records_tokens_when_conn_given(monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "fake", _fake_provider("hi"))
    monkeypatch.setattr(llm, "route", lambda task: "fake")
    c = db.connect(":memory:"); db.init_db(c)
    llm.complete([{"role": "user", "content": "x"}], task="reply", conn=c)
    t = db.token_summary(c)
    assert t["tokens_in"] == 11 and t["tokens_out"] == 7


def test_available_false_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.available() is False
