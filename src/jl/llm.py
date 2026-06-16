"""Provider-agnostic LLM layer. LLM-optional by contract: callers degrade to manual
when ok is False. Token usage is unconstrained-by-design but always accounted
(global memory: token-spend-agentic-era-principle). Claude wired; multi-provider-ready."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass

from . import db

CLAUDE_MODEL = os.environ.get("AMR_CLAUDE_MODEL", "claude-opus-4-8")


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


PROVIDERS = {"claude": _claude}


def available():
    """True if any provider is usable right now (1a: Claude key present)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def route(task):
    """Pick a provider name for a task, or None if none available (LLM-optional)."""
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
