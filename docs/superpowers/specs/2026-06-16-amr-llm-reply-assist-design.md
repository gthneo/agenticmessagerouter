# AMR LLM Layer + Reply-Draft Assistant (B2 slice 1) — Design

**Date:** 2026-06-16
**Status:** approved design (brainstorm), ready for implementation plan
**Scope:** B2-slice-1a — a provider-agnostic LLM abstraction + an auto reply-draft assistant on WeChat. 1b/1c phased below.

## Goal

Elevate AMR from router to **intelligent hub**: when a message arrives, AMR drafts candidate replies (话术) the human can pick/edit/send — the first AI-assist feature, built on a reusable multi-LLM layer. Strictly human-in-the-loop (drafts are suggestions; sending stays behind the existing outbox confirm). LLM-optional (no model → manual compose still works). Token usage is unconstrained-by-design but fully accounted (王总 principle: 不怕花、必须统计 — see global memory `token-spend-agentic-era-principle`).

## Decisions (王总 2026-06-16, brainstorm)
- **Trigger = auto-draft on inbound** (scoped) + on-demand; sending stays HITL (auto-*draft*, not auto-*send*).
- **Context phased**: 1a = conversation timeline + person category/染色 + a 话术 style guide; 1b = inject the M1–M8 methods engine (+《影响力》/Cialdini principles). 1c = graduated autonomy.
- **First provider = Claude API**, but the abstraction is **multi-provider-ready** from day 1 (Ollama/router drop in later with zero core change).
- **First test channel = WeChat** (most relationships).

## Architecture — three focused units

### 1. `src/jl/llm.py` — provider-agnostic LLM abstraction
- `LLMResult` dataclass: `text, provider, model, tokens_in, tokens_out, latency_ms, ok, error`.
- `complete(messages, *, task="reply", provider=None, conn=None) -> LLMResult`:
  - `PROVIDERS` registry `{name: callable(messages, **opts) -> LLMResult}`. **Only `claude` wired in 1a**; the registry + a `route(task)` selector exist so adding `ollama` / multi-provider routing later is a registration, not a refactor (satisfies "为 Q3-2 做准备").
  - `route(task)`: maps a task to a preferred provider with availability fallback (1a: trivially → claude if configured, else the LLM-optional path).
  - **LLM-optional**: if no provider is configured/reachable, `complete` returns `LLMResult(ok=False, error="llm_unavailable", text="")`. Callers MUST treat `ok=False` as "no assist" and degrade to manual — never block.
  - **Token accounting**: on every call, if `conn` given, write `db.record_tokens(channel_kind="llm", op=task, tokens_in, tokens_out)`. Always visible via `jl --tokens`.
- `claude` provider: HTTP to the Anthropic API using `ANTHROPIC_API_KEY` (env, on .178). Pure-stdlib `urllib`. Model: the latest Claude (configurable). Transport errors → `ok=False` (caught), never raise to the caller.

### 2. `suggestions` table + `src/jl/assist.py` — the draft assistant
- New table `suggestions`: `id, conversation_id, version_idx, stance, body, llm_provider, llm_model, status (suggested|used|dismissed), created_at`. Separate from `outbox` (outbox stays = only human-committed drafts; suggestions = AI candidates).
- `db` ops: `add_suggestions(conn, conversation_id, items)`, `get_suggestions(conn, conversation_id, status="suggested")`, `set_suggestion_status(conn, id, status)`, `clear_suggestions(conn, conversation_id)`.
- `assist.build_context(conn, conversation_id)` (pure): assemble the prompt context — the person's cross-channel merged timeline (recent N), person category + 阈值/染色, and a **话术 style guide** (a short, configurable system instruction; the *specific* method content lives outside the public repo and is injected as text). Returns `messages` for the LLM.
- `assist.generate_drafts(conn, conversation_id, *, n=3, llm=llm.complete)` : build context → call llm (task="reply") → parse N distinct-stance versions → `add_suggestions`. Returns the suggestions (or [] + logs if llm unavailable). `llm` injected for testability. Token-accounted via the llm layer.
- **Stances** (1a): n=3 with distinct registers, e.g. 稳妥 / 直接 / 有温度 (the exact stance set is config/style-guide-driven, not hardcoded business logic).

### 3. Triggers + UI
- **Auto (scoped)**: after the poll ingests new inbound, for each conversation matching ALL of: `type=private` AND `muted=0` AND `person_id` set AND latest message `direction=in` (awaiting my reply) AND no fresh `suggested` rows → call `generate_drafts`. Groups/official/muted/already-replied are skipped (relevance, not cost). Runs on .178.
- **On-demand**: web button "✨ AI 拟话术" on a conversation + `jl draft-assist <conv>` → same `generate_drafts`.
- **Web UI**: in a conversation view, show the `suggested` versions (stance label + body) each with **"用此版"** (fills the reply textarea — client-side) and **"✕"** (dismiss). The human edits in the reply box → existing `/api/outbox` queue → confirm → send. New read route `GET /api/conversations/{id}/suggestions`; POST `/api/suggestions/dismiss`. No new send path — reuse outbox. UI stays self-explanatory (no manual): suggestions sit under the conversation, click one, edit, send.

## Data flow
inbound → (poll, scoped) `generate_drafts` → `suggestions` → inbox shows N versions → human picks/edits → reply box → `/api/outbox` (queue) → confirm → channel send. LLM unavailable → no suggestions, human composes manually (unchanged).

## Phasing (步骤分批, 不跳批)
- **1a (this spec)**: `llm.py` (Claude, multi-provider-ready, token-accounted, LLM-optional) + `suggestions` + `assist.py` (context phase-A) + auto/on-demand triggers + web suggestions UI. Test on WeChat.
- **1b**: context phase-B — inject the **M1–M8 methods engine** (魂表分离 / 道德绑架 parser / 还击 3 档 / 给确定 …) **+《影响力》(Cialdini) 6 原则**(互惠/承诺一致/社会认同/权威/喜好/稀缺) as method guidance; deepen/optimize the style guide. (Method *content* maintained in the local strategy doc, not the public repo.)
- **1c**: graduated autonomy — track suggestion acceptance/edit rate per person/scenario; the Agent **proposes** "auto-reply this person going forward?"; human approves → that person/scenario joins a whitelist for auto-send (logged, revocable). This realizes 王总's "训练完毕 Agent 主动提议全程自动回复".
- later: Ollama@.156 provider + task/cost/availability routing (Q3-2).

## Error handling / safety
- LLM-optional: every assist path tolerates `ok=False` → degrade to manual, never block read/compose/send.
- HITL: 1a/1b never auto-send; only the existing outbox confirm sends. 1c auto-send requires explicit human-approved graduation per person.
- Token accounting on every call (tokens table; `jl --tokens`). Unconstrained spend, full visibility.
- Scoped auto-draft prevents drafting replies to groups/official/already-handled.

## Testing
- Pure/unit (TDD): `llm.route`/registry/LLM-optional-degradation/token-accounting (fake provider); `assist.build_context` assembly; `generate_drafts` version parsing (fake llm); `suggestions` db ops; web suggestion handlers (fake llm). 
- Integration (manual, .178): real Claude call (ANTHROPIC_API_KEY) generating drafts for a real WeChat conversation; pick → outbox → confirm → send.

## Out of scope (1a)
M1–M8/Cialdini methods (1b), graduated autonomy (1c), Ollama/routing, non-wechat channels for assist (the assist is channel-agnostic via conversations, but first verified on wechat), image/file replies.

## Prereq
`ANTHROPIC_API_KEY` on .178 (王总 to provide / I configure to env). Without it, 1a still ships — assist simply shows "未配置 LLM" and manual compose works (LLM-optional proof).
