"""Provider-agnostic LLM layer. LLM-optional by contract: callers degrade to manual
when ok is False. Token usage is unconstrained-by-design but always accounted
(global memory: token-spend-agentic-era-principle). Claude wired; multi-provider-ready."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass

from . import db

CLAUDE_MODEL = os.environ.get("AMR_CLAUDE_MODEL", "claude-opus-4-8")
# claude_code provider: shell out to the `claude` CLI (Max plan, no API key).
CLAUDE_CODE_BIN = os.environ.get("AMR_CLAUDE_BIN", "claude")
CLAUDE_CODE_MODEL = os.environ.get("AMR_CLAUDE_CODE_MODEL", CLAUDE_MODEL)


@dataclass
class LLMResult:
    text: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    ok: bool = False
    error: str = ""


def _claude(messages, *, max_tokens=1024, **opts):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return LLMResult(ok=False, error="llm_unavailable")
    system = ""
    msgs = []
    for m in messages:
        if m.get("role") == "system":
            system += (m["content"] + "\n")
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    body = {"model": CLAUDE_MODEL, "max_tokens": max_tokens, "messages": msgs}
    if system:
        body["system"] = system.strip()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return LLMResult(ok=False, error=str(e), latency_ms=int((time.time() - t0) * 1000))
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    usage = d.get("usage") or {}
    return LLMResult(text=text, provider="claude", model=d.get("model", CLAUDE_MODEL),
                     tokens_in=usage.get("input_tokens", 0),
                     tokens_out=usage.get("output_tokens", 0),
                     latency_ms=int((time.time() - t0) * 1000), ok=True)


def _claude_code_bin():
    """Resolve the `claude` CLI path, or None if unavailable (LLM-optional)."""
    if os.path.isabs(CLAUDE_CODE_BIN):
        return CLAUDE_CODE_BIN if os.path.exists(CLAUDE_CODE_BIN) else None
    return shutil.which(CLAUDE_CODE_BIN)


def _claude_code(messages, *, timeout=120, **opts):
    """Provider backed by the Claude Code CLI (Max plan — no ANTHROPIC_API_KEY needed).
    Shells `claude -p --output-format json`, full-replacing the system prompt so drafts
    are free of the CLI's coding-agent persona. Token usage parsed from the JSON result."""
    binpath = _claude_code_bin()
    if not binpath:
        return LLMResult(ok=False, error="llm_unavailable")
    system_parts, user_parts = [], []
    for m in messages:
        (system_parts if m.get("role") == "system" else user_parts).append(m["content"])
    prompt = "\n\n".join(user_parts)
    args = [binpath, "-p", "--output-format", "json", "--model", CLAUDE_CODE_MODEL]
    system = "\n".join(system_parts).strip()
    if system:
        args += ["--system-prompt", system]
    t0 = time.time()
    try:
        proc = subprocess.run(args, input=prompt, capture_output=True,
                              text=True, timeout=timeout)
    except Exception as e:  # subprocess/timeout failure → degrade, never raise
        return LLMResult(ok=False, error=str(e), latency_ms=int((time.time() - t0) * 1000))
    lat = int((time.time() - t0) * 1000)
    if proc.returncode != 0:
        return LLMResult(ok=False, latency_ms=lat,
                         error=(proc.stderr or f"claude exited {proc.returncode}")[:300])
    try:
        d = json.loads(proc.stdout or "{}")
    except ValueError as e:
        return LLMResult(ok=False, error=f"bad claude json: {e}", latency_ms=lat)
    if d.get("is_error"):
        return LLMResult(ok=False, latency_ms=lat,
                         error=str(d.get("result") or "claude reported error"))
    usage = d.get("usage") or {}
    tokens_in = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                 + usage.get("cache_creation_input_tokens", 0))
    return LLMResult(text=d.get("result", ""), provider="claude_code",
                     model=CLAUDE_CODE_MODEL, tokens_in=tokens_in,
                     tokens_out=usage.get("output_tokens", 0), latency_ms=lat, ok=True)


PROVIDERS = {"claude": _claude, "claude_code": _claude_code}


def available():
    """True if any provider is usable right now (LLM-optional gate)."""
    return route("reply") is not None


def route(task):
    """Pick a provider name for a task, or None if none available (LLM-optional).
    `AMR_LLM_PROVIDER` explicitly pins a provider (e.g. claude_code to dogfood the
    Max plan on .178); otherwise fall back to the Claude API when a key is present."""
    pref = os.environ.get("AMR_LLM_PROVIDER")
    if pref in PROVIDERS:
        return pref
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return None


def complete(messages, *, task="reply", provider=None, conn=None, **opts):
    """Run an LLM completion. Returns LLMResult; ok=False means 'no assist' (degrade).
    Records token usage to the tokens table when conn is given."""
    name = provider or route(task)
    fn = PROVIDERS.get(name) if name else None
    if fn is None:
        return LLMResult(ok=False, error="llm_unavailable")
    try:
        res = fn(messages, **opts)
    except Exception as e:  # a provider must never break the LLM-optional contract
        return LLMResult(ok=False, error=str(e))
    if conn is not None and (res.tokens_in or res.tokens_out):
        db.record_tokens(conn, channel_kind="llm", op=task,
                         tokens_in=res.tokens_in, tokens_out=res.tokens_out)
    return res
