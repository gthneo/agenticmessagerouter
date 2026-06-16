# AMR 主动调度 · 定期翻起 (Batch C / B3 slice 1, F5) — Design

**Date:** 2026-06-16
**Status:** approved design (brainstorm), ready for implementation plan
**Scope:** C-slice-1 (F5) — the system *proactively* surfaces who to re-engage and
auto-drafts an **opener** (not a reply) for human approval. Builds on the existing
染色 red-list + the 1b method playbook. F6 (资料定期传递) / F7 (话术推广 campaign) and
per-person custom cadence are out of scope.

## Goal
Turn AMR from reactive (drafts replies to inbound) into **proactive** — "the soul of
the Hub" (王总). On a schedule, for relationships that have gone quiet (🔴 overdue) or
are explicitly **关注 (watched)**, AMR drafts a re-engagement **opener** 话术 and queues
it for the human to pick / edit / send. Strictly human-in-the-loop (openers are
suggestions; the outbox confirm stays the only send path). LLM-optional (no model →
no openers, but the red list still tells the human who to contact). Missing send
channel → routed to 救补, never guessed.

## Decisions (王总 2026-06-16, brainstorm)
- **Trigger** = persistent **关注 (watch)** flag on a person **+** auto-scope to 🔴
  overdue; the queue refreshes periodically. A watched 🟢 person (e.g. a key client)
  still enters the queue. Plus on-demand `jl 主动 <名>` for any person.
- **Schedule** = add one proactive sweep round to the existing 5-min poll (auto-draft
  openers into a review queue; never auto-send).
- **No record / no channel** (e.g. a fresh cold contact) → create the person record,
  draft a *cold* opener if possible, and if there is no sendable channel, route to the
  救补 (missing-channel) list — a missing-channel HITL demo, not a silent drop.
- **Data** = `suggestions` gains a `kind` column (`reply` | `opener`) so the proactive
  queue can filter openers; everything else (outbox / confirm / HITL) is reused.

## Architecture

### 1. Schema (schema.sql is the single DDL source)
- `persons` + `watch INTEGER NOT NULL DEFAULT 0` — 关注 flag.
- `suggestions` + `kind TEXT NOT NULL DEFAULT 'reply'` — `reply` | `opener`.
- **Idempotent migration for the live .178 DB**: `init_db` runs the schema
  (`CREATE TABLE IF NOT EXISTS` — adds columns only on a fresh DB), then a new
  `_ensure_columns(conn)` step uses `PRAGMA table_info` to `ALTER TABLE ... ADD COLUMN`
  any missing column. Safe to re-run; no data loss.

### 2. db ops
- `set_watch(conn, person_id, on=True)` — toggle the flag (commits).
- `get_persons(conn)` already returns all columns → `watch` flows through `_person_row`.
- `add_suggestions(conn, conversation_id, items, *, kind="reply")` — write the kind.
- `get_suggestions(conn, conversation_id, status="suggested", kind=None)` — `kind=None`
  returns all (back-compat); a value filters. (`clear_suggestions` stays whole-conv.)

### 3. assist — opener generation
- `OPENER_GUIDE`: a style guide for a **proactive opener** — 主动联络的开场,不是回复;
  借自然由头(上次的事/共同进展,少用天气节气尬聊),给确定(具体下一步),留钩子,不油腻,
  守底线。输出恰好 3 档 (稳妥/直接/有温度),格式同 1a (`序号) 风格: 正文`).
- `build_opener_context(conn, person_id, recent=12, playbook=None)` (pure): assemble the
  person's primary-conversation timeline (may be empty → 冷启动: state "尚无历史"), the
  person's category + days-since-last-interaction (染色), `OPENER_GUIDE`, and the 1b
  playbook (`load_playbook()` by default, injectable). Returns `messages`.
- `primary_conversation(conn, person_id)` (pure): pick the person's best send target —
  a `type='private'` conversation on a sendable platform (wechat/feishu), most messages
  wins; returns the conversation dict or `None`.
- `generate_opener(conn, person_id, *, n=3, llm_complete=llm.complete)`: resolve
  `primary_conversation`. **No conversation → return `0` and the caller treats the person
  as 缺渠道 (救补)**. Else build context → llm (task="opener") → parse N versions →
  `clear_suggestions` then `add_suggestions(kind="opener")` on that conversation. Returns
  count stored. Token-accounted via the llm layer. LLM unavailable → 0 (no-op).
- `proactive_sweep(conn, *, llm_complete=llm.complete)`: for each person where
  `watch==1` **or** color==🔴 (compute via `weighting.combine` + `color`): skip if a fresh
  `opener` suggestion already exists (no spam re-drafting); if `primary_conversation` is
  None → add to `missing_channel`; else `generate_opener` and, on success, add the
  person/conv to `drafted`. Returns `{"drafted": [...], "missing_channel": [...]}`.

### 4. CLI
- `jl 主动` (alias `proactive`): run `proactive_sweep`, print the 该联系谁 table
  (watched/🔴 persons, days-since, threshold), which got openers (with conv id → "去收件箱
  挑/改/发"), and the 缺渠道(救补) sub-list. Logs an event.
- `jl 主动 <名>`: `generate_opener` for one person (any color); no channel → 救补 notice.
- `jl 关注 <名>` / `jl 关注 --off <名>`: toggle the watch flag (logs an event with actor).

### 5. Web (lean)
Openers are stored as `suggestions` on the person's conversation, so the existing
conversation view already renders them (用此版 → reply box → outbox → confirm). Add a
homepage **"📞 该联系谁"** section: `GET /api/proactive` returns watched/🔴 persons, each
with their opener count + conversation link + a 缺渠道 flag. Click → open that
conversation → pick/edit/send. No new send path. Keeps the no-manual UI principle.

### 6. poll / schedule integration
After the poll's ingest + `auto_draft_sweep`, also call `proactive_sweep` (guarded by
`llm.available()` + try/except, like the reply sweep). So 定期翻起 runs automatically
every 5 min, scoped to watched/🔴, openers landing in the review queue. Never sends.

## Data flow
schedule (poll) → `proactive_sweep` → for watched/🔴 with a send channel: `generate_opener`
→ `suggestions(kind=opener)` → 该联系谁 list + conversation view show openers → human
picks/edits → reply box → `/api/outbox` (queue) → confirm → channel send. No channel →
救补. LLM unavailable → no openers, red list still surfaces who to contact (manual).

## Error handling / safety
- HITL: openers are drafts; only the existing outbox confirm sends. watch toggles and
  sweeps are logged to `events` (who/when/why).
- 缺渠道 → 救补, never guess a wxid/number.
- Scoped + dedup (skip fresh openers) → no spam re-drafting.
- LLM-optional throughout: every opener path tolerates `ok=False` → degrade.
- Migration idempotent; never drops/rewrites existing rows.

## Testing (TDD, synthetic fixtures only — 张三/李四/王五, no real contacts)
- db: `set_watch` + `get_persons` reflects `watch`; `add_suggestions(kind=...)` /
  `get_suggestions(kind=...)` filter; `_ensure_columns` adds a missing column to a DB
  created without it (simulate old schema).
- assist: `build_opener_context` with timeline and empty (冷启动); `primary_conversation`
  picks the right send target / None; `generate_opener` stores `kind=opener` (fake llm) /
  returns 0 + no store when no conversation / no-op when llm unavailable;
  `proactive_sweep` scopes to watched+🔴, skips fresh openers, routes no-channel to
  `missing_channel`.
- cli: `route` maps `主动` / `主动 <名>` / `关注 <名>` / `关注 --off <名>`.
- web: `/api/proactive` shape (fake data).

## Out of scope (slice 1)
F6 资料定期传递, F7 话术推广 campaign, per-person custom cadence rules (Approach B),
multi-channel opener fan-out, opener A/B acceptance tracking (feeds future 1c autonomy).

## Live run (王总's dogfood targets, on .178 — real data, never in the repo)
Mark Connie / Shirley / 何峰博 as 关注. Shirley is 🔴 (auto + watched); Connie is 🟢 but
watched (proves watch overrides color); 何峰博 has no record → create person + (no
channel yet) → 救补 demo. Run `jl 主动`, verify openers for Connie/Shirley land in the
queue with methods applied, and 何峰博 surfaces in 救补.
