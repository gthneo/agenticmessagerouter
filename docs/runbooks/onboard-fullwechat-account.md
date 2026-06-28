# Runbook — onboard a fullwechat WeChat account into AMR (`jl`)

**Audience:** FDE / 现场 Build 工程师 + 数字运维工程师 Agent
**Applies to:** AMR `jl` ≥ v0.5 (per-account onboarding surface)
**Updated:** 2026-06-28

---

## What this does

AMR ingests WeChat from per-account backends through the `accounts` table. Onboarding a
new (or repointing an existing) backend = **one accounts row + one token file**. This was
previously a hand-written `db.upsert_account(...)` in a Python REPL against the prod DB.
It is now productized as `jl account add|set`, with a human-in-the-loop dry-run→commit gate.

Each backend is a [fullwechat](https://) host exposing a REST surface (default port `6174`,
Bearer auth). On the **backend host** the bearer lives at `~/.config/agent-wechat/token`.
On the **AMR host** you place a *copy* of that token under the standardized cred path and
let `jl` wire it up.

### Cred convention

```
~/.config/jl/cred/<tool>_<account_id>.token      # per-account bearer, chmod 600
```

`jl account add/set --token-file <path>` copies your token *copy* into this path, `chmod
600`s it, and sets `accounts.cred_ref` to point at it. `cli._fullwechat_targets(conn)` reads
this file at poll time to authenticate per account. **Never** commit a token; never echo its
bytes — the tooling only moves the file.

---

## Steps

### 0. Decide: new account or repoint an existing one?

```sh
jl account ls
```

- A backend you've never registered → **`jl account add`** (auto-allocates the next id).
- An existing row to re-point (e.g. id=4 was `tool=powerdata`, now must become
  `tool=fullwechat` at a new host) → **`jl account set <id>`** (overwrites only the flags
  you pass; leaves the rest).

### 1. Mirror the backend token to the AMR host

On the **backend host**, the bearer is at `~/.config/agent-wechat/token`. Copy it to a
scratch path on the **AMR host** (scp / paste into a `chmod 600` file). This scratch file is
what you pass to `--token-file`; `jl` copies it into the cred path for you.

```sh
# example: token now sits at /tmp/ren28.token on the AMR host (delete after)
```

### 2. Register / repoint (dry-run first — HITL gate)

`jl account add|set` **defaults to a dry-run** that prints exactly what will change
(allocated `account_id`, tool, host, `cred_ref` path, and the token copy plan). Nothing is
written until you add `--commit` (alias `--yes`). This is the project's write-confirm gate.

**New account:**

```sh
jl account add \
  --platform wechat --tool fullwechat \
  --host http://HOST:6174 \
  --self-id <your-own-wxid> \
  --label "<人读名>" \
  --token-file /tmp/ren28.token
# review the plan → re-run with --commit
jl account add ... --commit
```

**Repoint an existing account (the 仁兄 case):**

```sh
jl account set 4 \
  --tool fullwechat --host http://HOST:6174 \
  --token-file /tmp/ren28.token
# dry-run shows a before→after diff with ←改 on changed fields → confirm with --commit
jl account set 4 ... --commit
```

Then delete the scratch token: `rm /tmp/ren28.token`.

### 3. Verify backend reachability

Confirm the backend is logged in as the expected wxid (Bearer auth, port 6174):

```sh
curl -s -H "Authorization: Bearer $(cat ~/.config/jl/cred/fullwechat_<id>.token)" \
     http://HOST:6174/api/status/auth
```

Expect a logged-in status whose wxid matches the `--self-id` you registered. If it shows
logged-out / a different wxid, re-pair the backend before continuing.

### 4. Ingest

```sh
jl poll           # iterates every tool=fullwechat account, pulls new messages per backend
```

`cli._fullwechat_targets` now yields `(account_id, host, token)` for the new row, so `poll`
picks it up automatically — no code change.

### 5. Reunify the account's own identity into SELF (我)

The `--self-id` you registered is *your own* login on that backend, not a contact. Confirm it
as a SELF identity so AMR colors that side of conversations as 我 (right-side green bubbles):

- **Web inbox:** open the SELF panel — the new account's `self_id` appears under suggestions
  (`/api/self` ← `db.suggest_self_identities`); confirm it (persona 自我).
- **Headless:** `db.seed_self_from_accounts(conn)` promotes every account `self_id` to a SELF
  identity in one shot.

Then re-run `jl poll` (or wait a cycle): `db.apply_self_directions` flips messages you sent
on that account to `direction=out`.

### 6. Confirm

```sh
jl account ls     # the row shows tool=fullwechat + the new host
```

Open a conversation on that account in the Web inbox and confirm your own messages render as
我 (right side). Done.

---

## Notes / gotchas

- **Token hygiene:** cred files are `chmod 600`. Delete the scratch `--token-file` after
  commit. Never `cat` a token into a shared log; never commit `~/.config/jl/cred/`.
- **Repoint vs add:** `set` only overwrites the flags you pass. To change just the label,
  `jl account set <id> --label "新名"` leaves tool/host/token untouched (no token copy).
- **8-bit id space:** `account_id` is 0–255 (schema CHECK). `next_account_id` = max+1.
- **`UNIQUE(platform, self_id)`:** two accounts can't share the same `(platform, self_id)`.
  If a repoint collides, you're likely duplicating a self — reuse the existing id instead.
- **LLM-optional:** none of this needs an LLM — onboarding is pure config + file plumbing.
