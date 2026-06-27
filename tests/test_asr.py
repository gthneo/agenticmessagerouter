"""ASR (speech→text) layer tests. Provider-agnostic + LLM-optional: no ASR_URL →
available() False and transcribe degrades (ok=False) — the human stays at [语音].

Synthetic only; never depends on a real ASR endpoint (env unset, no config file).
"""
import pytest

from jl import asr, db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _no_real_asr(monkeypatch, tmp_path):
    """Guarantee no live ASR config bleeds in: unset env + point the config-file
    lookups at an empty tmp HOME so ~/.config/jl/asr_url can't exist."""
    monkeypatch.delenv("ASR_URL", raising=False)
    monkeypatch.delenv("ASR_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


def test_available_false_when_no_url():
    assert asr.available() is False


def test_transcribe_empty_audio_is_no_audio():
    res = asr.transcribe(b"")
    assert res.ok is False and res.error == "no_audio"


def test_transcribe_without_url_is_unavailable():
    res = asr.transcribe(b"\x01\x02\x03")
    assert res.ok is False and res.error == "asr_unavailable"


def test_transcribe_uses_provider_and_records_tokens(conn, monkeypatch):
    calls = {}

    def fake(audio, *, mime="audio/silk"):
        calls["audio"], calls["mime"] = audio, mime
        return asr.ASRResult(text="你好", provider="http", ok=True)

    monkeypatch.setitem(asr.PROVIDERS, "http", fake)
    res = asr.transcribe(b"silkbytes", mime="audio/silk", conn=conn)
    assert res.ok and res.text == "你好"
    assert calls["audio"] == b"silkbytes"
    row = conn.execute(
        "SELECT channel_kind, op, reach_count, tokens_out FROM tokens").fetchone()
    assert row["channel_kind"] == "asr" and row["op"] == "transcribe"
    assert row["reach_count"] == 1 and row["tokens_out"] == len("你好")


def test_transcribe_failure_records_no_tokens(conn, monkeypatch):
    monkeypatch.setitem(asr.PROVIDERS, "http",
                        lambda audio, *, mime="audio/silk": asr.ASRResult(ok=False, error="boom"))
    res = asr.transcribe(b"x", conn=conn)
    assert res.ok is False
    assert conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 0
