"""Provider-agnostic ASR (speech→text). LLM-optional by contract: callers degrade
(stay at [语音]) when ok is False. Audio source = backend's media.ref (契约 §2.3);
transcription is AMR assist downstream. Generic HTTP provider; multi-provider-ready."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass

from . import db


@dataclass
class ASRResult:
    text: str = ""
    provider: str = ""
    latency_ms: int = 0
    ok: bool = False
    error: str = ""


def _asr_url():
    u = os.environ.get("ASR_URL")
    if u:
        return u.rstrip("/")
    try:
        with open(os.path.expanduser("~/.config/jl/asr_url"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _asr_token():
    t = os.environ.get("ASR_TOKEN")
    if t:
        return t
    try:
        with open(os.path.expanduser("~/.config/jl/asr_token"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _http(audio, *, mime="audio/silk"):
    """Generic HTTP ASR: POST raw audio bytes to ASR_URL, expect JSON {text|transcript}
    or plain text. Configure URL via env ASR_URL or ~/.config/jl/asr_url (FQDN ok)."""
    url = _asr_url()
    if not url:
        return ASRResult(ok=False, error="asr_unavailable")
    headers = {"Content-Type": mime}
    tok = _asr_token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=audio, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:
        return ASRResult(ok=False, error=str(e))
    lat = int((time.time() - t0) * 1000)
    try:
        d = json.loads(body)
        text = (d.get("text") or d.get("transcript") or "") if isinstance(d, dict) else ""
    except ValueError:
        text = body.strip()
    if not text:
        return ASRResult(ok=False, error="empty_transcript", latency_ms=lat)
    return ASRResult(text=text.strip(), provider="http", latency_ms=lat, ok=True)


PROVIDERS = {"http": _http}


def available():
    """True if an ASR endpoint is configured (LLM-optional gate)."""
    return bool(_asr_url())


def transcribe(audio, *, mime="audio/silk", conn=None):
    """Transcribe audio bytes → ASRResult. ok=False = degrade (stay at [语音]).
    Records usage to the tokens table (channel_kind='asr') when conn given and ok."""
    if not audio:
        return ASRResult(ok=False, error="no_audio")
    res = PROVIDERS["http"](audio, mime=mime)
    if conn is not None and res.ok:
        db.record_tokens(conn, channel_kind="asr", op="transcribe",
                         reach_count=1, tokens_out=len(res.text))
    return res
