"""LLM abstraction — registry/route/LLM-optional/token-accounting (fake provider)."""
import json

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


def test_complete_provider_exception_degrades_not_raises(monkeypatch):
    def boom(messages, **opts):
        raise RuntimeError("provider blew up")
    monkeypatch.setitem(llm.PROVIDERS, "boom", boom)
    monkeypatch.setattr(llm, "route", lambda task: "boom")
    r = llm.complete([{"role": "user", "content": "x"}], task="reply")
    assert r.ok is False and "blew up" in r.error   # degraded, did not raise


# --- claude_code provider (Claude Code Max plan, shells the `claude` CLI) ---

class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


_CC_OK_JSON = json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": "在的，您说。", "duration_ms": 3200,
    "usage": {"input_tokens": 100, "output_tokens": 12,
              "cache_read_input_tokens": 50, "cache_creation_input_tokens": 30},
})


def test_claude_code_parses_result_and_tokens(monkeypatch):
    monkeypatch.setattr(llm, "_claude_code_bin", lambda: "/usr/bin/claude")
    captured = {}

    def fake_run(args, **kw):
        captured["args"] = args
        captured["input"] = kw.get("input")
        return _FakeProc(stdout=_CC_OK_JSON)
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    r = llm._claude_code([{"role": "system", "content": "你是助手"},
                          {"role": "user", "content": "在吗"}])
    assert r.ok is True and r.text == "在的，您说。" and r.provider == "claude_code"
    # cache tokens fold into tokens_in for honest accounting
    assert r.tokens_in == 180 and r.tokens_out == 12
    # system goes to --system-prompt (full replace), user prompt via stdin
    assert "--system-prompt" in captured["args"] and "你是助手" in captured["args"]
    assert captured["input"] == "在吗"


def test_claude_code_unavailable_without_binary(monkeypatch):
    monkeypatch.setattr(llm, "_claude_code_bin", lambda: None)
    r = llm._claude_code([{"role": "user", "content": "x"}])
    assert r.ok is False and r.error == "llm_unavailable"


def test_claude_code_error_json_degrades(monkeypatch):
    monkeypatch.setattr(llm, "_claude_code_bin", lambda: "/usr/bin/claude")
    bad = json.dumps({"is_error": True, "result": "rate limited"})
    monkeypatch.setattr(llm.subprocess, "run", lambda args, **kw: _FakeProc(stdout=bad))
    r = llm._claude_code([{"role": "user", "content": "x"}])
    assert r.ok is False and "rate limited" in r.error


def test_route_prefers_explicit_provider_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AMR_LLM_PROVIDER", "claude_code")
    assert llm.route("reply") == "claude_code"


def test_route_falls_back_to_claude_key_when_no_pref(monkeypatch):
    monkeypatch.delenv("AMR_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.route("reply") == "claude"
