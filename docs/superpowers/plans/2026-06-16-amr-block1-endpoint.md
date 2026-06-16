# AMR Block 1 — Endpoint 路由模型 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a person's reachable addresses first-class "endpoints" so AMR routes to the best one (一人多渠道每渠道多号), replacing the v0.6 single-conversation `primary_conversation` stopgap.

**Architecture:** `channels` table = canonical endpoint registry (canonical identifiers, per-endpoint recency, pin). New `endpoints` + `best_endpoint` selection layer in a small module. Pure logic is TDD'd; live channel paths verified on .178 with synthetic-then-real data.

**Tech Stack:** Python 3.10 stdlib, sqlite3. Tests: pytest. Spec: `docs/superpowers/specs/2026-06-16-amr-endpoint-matters-diagnosis-design.md`.

---

### Task 1: Canonicalize + dedup the `channels` endpoint registry

**Files:**
- Modify: `src/jl/db.py` (add `dedup_channels`, canon on write in `link_conversations`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dedup_channels_folds_phone_format_variants(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_channel(conn, person_id="u1", kind="phone", identifier="13686472775")
    db.upsert_channel(conn, person_id="u1", kind="phone", identifier="+8613686472775")  # same canon
    db.upsert_channel(conn, person_id="u1", kind="phone", identifier="13760177688")      # distinct
    db.upsert_channel(conn, person_id="u1", kind="wechat", identifier="wxid_a")
    folded = db.dedup_channels(conn)
    chans = db.get_channels(conn, "u1")
    ids = sorted((c["kind"], c["identifier"]) for c in chans)
    assert folded == 1
    assert ids == [("phone", "13686472775"), ("phone", "13760177688"), ("wechat", "wxid_a")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_dedup_channels_folds_phone_format_variants -v`
Expected: FAIL (`dedup_channels` missing).

- [ ] **Step 3: Implement `dedup_channels` + canon helper in db.py**

```python
def _canon_identifier(kind, identifier):
    if kind == "phone":
        from .channels.phone import canon_phone
        return canon_phone(identifier)
    return identifier

def dedup_channels(conn):
    """Fold endpoint rows that share a canonical identifier (phone +86 variants).
    Returns count folded. Distinct identifiers stay separate."""
    rows = conn.execute("SELECT id, person_id, kind, identifier FROM channels").fetchall()
    seen, folded = {}, 0
    for r in rows:
        key = (r["person_id"], r["kind"], _canon_identifier(r["kind"], r["identifier"]))
        if key in seen:
            conn.execute("DELETE FROM channels WHERE id=?", (r["id"],))
            folded += 1
        else:
            seen[key] = r["id"]
            canon = key[2]
            if canon and canon != r["identifier"]:
                conn.execute("UPDATE channels SET identifier=? WHERE id=?", (canon, r["id"]))
    conn.commit()
    return folded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_dedup_channels_folds_phone_format_variants -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jl/db.py tests/test_db.py
git commit -m "feat(db): dedup_channels folds canonical-identifier endpoint duplicates"
```

---

### Task 2: Per-endpoint recency (stop the platform collapse)

**Files:**
- Modify: `src/jl/db.py` (add `endpoints_with_recency`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
def test_endpoints_with_recency_per_identifier(conn):
    from jl import ingest
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    db.upsert_account(conn, account_id=2, platform="phone", self_id="s")
    a = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_a", name="张三")
    b = db.upsert_conversation(conn, account_id=2, platform="phone", chat_id="13686472775", name="张三")
    for cid in (a, b):
        db.link_person(conn, cid, "u1")
    db.insert_messages(conn, a, [ingest.MsgRecord(msg_key="a1", ts=100, content="x", direction="in")])
    db.insert_messages(conn, b, [ingest.MsgRecord(msg_key="b1", ts=200, content="y", direction="in")])
    eps = db.endpoints_with_recency(conn, "u1")
    by = {(e["kind"], e["identifier"]): e for e in eps}
    assert by[("wechat", "wxid_a")]["last_ts"] == 100      # each endpoint keeps its own recency
    assert by[("phone", "13686472775")]["last_ts"] == 200  # not collapsed to one-per-platform
    assert by[("wechat", "wxid_a")]["conversation_id"] == a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_endpoints_with_recency_per_identifier -v`
Expected: FAIL (`endpoints_with_recency` missing).

- [ ] **Step 3: Implement `endpoints_with_recency`**

```python
def endpoints_with_recency(conn, person_id):
    """One row per (kind, canonical identifier) reachable for a person, with its own
    last interaction ts + the conversation that defines it. Endpoint-level (NOT
    platform-collapsed) so a person fresh on one number and cold on another is seen."""
    out = {}
    for c in get_conversations(conn, person_id=person_id):
        kind = c["platform"]
        ident = _canon_identifier(kind, c["chat_id"])
        n, last_ts = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(ts),0) FROM messages WHERE conversation_id=?",
            (c["id"],)).fetchone()
        key = (kind, ident)
        prev = out.get(key)
        if prev is None or last_ts > prev["last_ts"]:
            out[key] = {"kind": kind, "identifier": ident, "last_ts": last_ts,
                        "msgs": n, "conversation_id": c["id"], "chat_id": c["chat_id"]}
    return list(out.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_endpoints_with_recency_per_identifier -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jl/db.py tests/test_db.py
git commit -m "feat(db): endpoints_with_recency — per-endpoint recency, no platform collapse"
```

---

### Task 3: Endpoint pin (human-chosen primary)

**Files:**
- Modify: `src/jl/schema.sql` (channels + `pinned`), `src/jl/db.py` (`_ADDED_COLUMNS`, `set_endpoint_pin`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
def test_set_endpoint_pin_marks_one_endpoint(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_channel(conn, person_id="u1", kind="wechat", identifier="wxid_a")
    db.set_endpoint_pin(conn, "u1", "wechat", "wxid_a", True)
    chans = {(c["kind"], c["identifier"]): c for c in db.get_channels(conn, "u1")}
    assert chans[("wechat", "wxid_a")]["pinned"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_set_endpoint_pin_marks_one_endpoint -v`
Expected: FAIL (no `pinned` column / `set_endpoint_pin`).

- [ ] **Step 3: Implement**

In `src/jl/schema.sql`, add to the `channels` table definition: `pinned INTEGER NOT NULL DEFAULT 0,` (after `label`).
In `src/jl/db.py` `_ADDED_COLUMNS`, add `"channels": [("pinned", "INTEGER NOT NULL DEFAULT 0")]`.
Add:

```python
def set_endpoint_pin(conn, person_id, kind, identifier, on=True):
    conn.execute("UPDATE channels SET pinned=? WHERE person_id=? AND kind=? AND identifier=?",
                 (1 if on else 0, person_id, kind, identifier))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_set_endpoint_pin_marks_one_endpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jl/schema.sql src/jl/db.py tests/test_db.py
git commit -m "feat(db): channels.pinned + set_endpoint_pin (human-chosen endpoint)"
```

---

### Task 4: `best_endpoint` routing (weight × recency × sendable, pin override)

**Files:**
- Create: `src/jl/routing.py`
- Test: `tests/test_routing.py`

- [ ] **Step 1: Write the failing test**

```python
"""Endpoint routing — pure selection over endpoints + a sendable predicate."""
from jl import routing


def _ep(kind, ident, last_ts, pinned=0):
    return {"kind": kind, "identifier": ident, "last_ts": last_ts, "pinned": pinned}


def test_best_endpoint_prefers_weight_times_recency():
    eps = [_ep("phone", "13800000000", 100), _ep("wechat", "wxid_a", 100)]
    # equal recency → higher channel weight (wechat 1.0 > phone 0.8) wins
    best = routing.best_endpoint(eps, sendable=lambda e: True)
    assert best["identifier"] == "wxid_a"


def test_best_endpoint_pin_overrides_score():
    eps = [_ep("wechat", "wxid_a", 999), _ep("phone", "13800000000", 1, pinned=1)]
    best = routing.best_endpoint(eps, sendable=lambda e: True)
    assert best["identifier"] == "13800000000"   # human pin wins regardless of score


def test_best_endpoint_falls_back_to_sendable():
    eps = [_ep("wechat", "wxid_stale", 999), _ep("wechat", "wxid_live", 1)]
    best = routing.best_endpoint(eps, sendable=lambda e: e["identifier"] == "wxid_live")
    assert best["identifier"] == "wxid_live"      # top score unsendable → next sendable


def test_best_endpoint_none_when_no_sendable():
    eps = [_ep("wechat", "wxid_x", 5)]
    assert routing.best_endpoint(eps, sendable=lambda e: False) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_routing.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/jl/routing.py`**

```python
"""Endpoint routing: pick the best reachable endpoint for a person. Pure — the
sendability predicate is injected (live channel check lives in the caller)."""
from __future__ import annotations

from . import weighting


def _recency(last_ts, now):
    days = max(0.0, (now - last_ts) / 86400.0) if last_ts else 1e9
    return 1.0 / (days + 1.0)


def score(endpoint, now):
    w = weighting.DEFAULT_WEIGHTS.get(endpoint["kind"], 0.5)
    return w * _recency(endpoint.get("last_ts") or 0, now)


def best_endpoint(endpoints, *, sendable, now=None):
    """Return the best SENDABLE endpoint, or None. A pinned endpoint wins if sendable;
    otherwise highest weight×recency among sendable endpoints."""
    import time
    now = time.time() if now is None else now
    usable = [e for e in endpoints if sendable(e)]
    if not usable:
        return None
    pinned = [e for e in usable if e.get("pinned")]
    pool = pinned or usable
    return max(pool, key=lambda e: score(e, now))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_routing.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/jl/routing.py tests/test_routing.py
git commit -m "feat(routing): best_endpoint — weight×recency, pin override, sendable fallback"
```

---

### Task 5: Wire routing into assist (supersede `primary_conversation`) + sendable predicate

**Files:**
- Modify: `src/jl/assist.py` (`primary_conversation` delegates to routing over endpoints)
- Test: `tests/test_assist.py`

- [ ] **Step 1: Write the failing test**

```python
def test_primary_conversation_uses_best_endpoint(monkeypatch):
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    stale = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_stale", name="张三")
    live = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_live", name="张三")
    for cid in (stale, live):
        db.link_person(conn, cid, "u1")
    db.insert_messages(conn, stale, [ingest.MsgRecord(msg_key="s1", ts=999, content="x", direction="in")])
    db.insert_messages(conn, live, [ingest.MsgRecord(msg_key="l1", ts=1, content="y", direction="in")])
    # only wxid_live is sendable → routing must pick it despite lower recency
    monkeypatch.setattr(assist, "_sendable_chat_ids", lambda kinds=None: {"wxid_live"})
    pc = assist.primary_conversation(conn, "u1")
    assert pc["chat_id"] == "wxid_live"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_assist.py::test_primary_conversation_uses_best_endpoint -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — replace `primary_conversation` body in `src/jl/assist.py`

```python
def _sendable_chat_ids(kinds=None):
    """Live, selectable chat ids per sendable channel. Best-effort: a channel whose
    live list can't be fetched contributes nothing here (caller degrades). Network."""
    ids = set()
    try:
        from .channels.fullwechat import FullWechatAdapter
        live = FullWechatAdapter()._live_chat_ids()
        if live:
            ids |= {i for i in live if i}
    except Exception:
        pass
    return ids


def primary_conversation(conn, person_id):
    """Best send-target conversation for a person, via endpoint routing (weight×recency,
    pin override, sendable fallback). Falls back to most-recent sendable-platform private
    conv when the live list is unavailable (LLM/Network-optional)."""
    from . import routing
    eps = []
    chans = {(c["kind"], c["identifier"]): c for c in db.get_channels(conn, person_id)}
    for e in db.endpoints_with_recency(conn, person_id):
        if e["kind"] not in SENDABLE_PLATFORMS:
            continue
        ch = chans.get((e["kind"], e["identifier"]))
        e = dict(e, pinned=(ch or {}).get("pinned", 0))
        eps.append(e)
    if not eps:
        return None
    live = _sendable_chat_ids()
    if live:
        sendable = lambda e: e["chat_id"] in live or e["identifier"] in live
    else:
        sendable = lambda e: True   # can't check → don't block; recency decides
    best = routing.best_endpoint(eps, sendable=sendable)
    if not best:
        best = routing.best_endpoint(eps, sendable=lambda e: True)  # nothing live → most-recent
    return db.get_conversation(conn, best["conversation_id"]) if best else None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_assist.py -v`
Expected: PASS (existing primary_conversation tests + the new one; the recency test still holds because with `live` empty all are sendable and recency decides).

- [ ] **Step 5: Commit**

```bash
git add src/jl/assist.py tests/test_assist.py
git commit -m "feat(assist): primary_conversation routes via best_endpoint (sendable-aware)"
```

---

### Task 6: Feishu DM endpoint ingest

**Files:**
- Modify: `src/jl/channels/lark.py` (DM/P2P conversations alongside groups)
- Test: `tests/test_lark.py`

- [ ] **Step 1: Write the failing test**

```python
def test_map_p2p_chat_is_private_unmuted():
    chat = {"chat_id": "oc_dm1", "name": "李四", "chat_mode": "p2p"}
    c = fw_lark.map_chat(chat)   # lark module imported as fw_lark in this test file
    assert c.type == "private" and c.muted is False
```

(Adjust the import alias to match `tests/test_lark.py`'s existing import of the lark module.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lark.py::test_map_p2p_chat_is_private_unmuted -v`
Expected: FAIL (`map_chat` hardcodes group/muted).

- [ ] **Step 3: Implement** — in `src/jl/channels/lark.py` `map_chat`

```python
def map_chat(chat):
    is_p2p = chat.get("chat_mode") == "p2p"
    return ingest.ConvRecord(
        chat_id=chat["chat_id"],
        name=chat.get("name", ""),
        type="private" if is_p2p else "group",
        muted=not is_p2p,        # groups arrive muted; DMs are active endpoints
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_lark.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jl/channels/lark.py tests/test_lark.py
git commit -m "feat(lark): p2p DMs ingest as private unmuted endpoints"
```

---

### Task 7: Endpoint integration verification on .178 (real data)

**Files:** none (operational verification)

- [ ] **Step 1:** Deploy (`deploy/deploy.sh dbos-user@192.168.31.178`), run `db.dedup_channels` + `db.dedup_phone_conversations` on the live DB.
- [ ] **Step 2:** For 李夏宁: assert `endpoints_with_recency` returns 微信×2 (or merged) + 电话×2 distinct + (if linked) 飞书DM — i.e. multi-channel multi-id enumerated, no format dup.
- [ ] **Step 3:** Assert `assist.primary_conversation(李夏宁)` returns a SENDABLE conversation (live wxid), not the stale one.
- [ ] **Step 4:** Spot-check 何峰博 / 仁兄(self) / 赵冰 / 刘宏玏 endpoints enumerate correctly.
- [ ] **Step 5:** Record results in memory `host-178-access.md`; no commit (operational).

---

## Self-Review notes
- Spec coverage: canon channels (Task1), per-endpoint recency (Task2), pin (Task3), best_endpoint weight×recency×sendable+pin+fallback (Task4), wire-in superseding primary_conversation (Task5), feishu DM (Task6), live verify with the named test people (Task7). All Block-1 spec items covered.
- Type consistency: endpoint dict shape `{kind, identifier, last_ts, msgs, conversation_id, chat_id, pinned}` consistent across db.endpoints_with_recency → routing.best_endpoint → assist.
- `weighting.DEFAULT_WEIGHTS` reused (no new weight source). `canon_phone` reused from phone module.
- Blocks 2 (matters+UI) and 3 (T4 diagnosis) get their own plans after Block 1 lands.
