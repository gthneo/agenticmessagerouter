# AMR Person Unification (③ slice 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Collapse a person's conversations across channels (e.g. 李四's wechat + phone) into one person in the inbox — **auto-link where IDs match, human-confirm where they don't** (HITL, never silent merge), with a merged cross-channel timeline.

**Architecture:** Builds on A's `persons` / `channels` / `conversations.person_id`. Adds: deterministic `link_conversations` (phone via `tail_match`, others exact), `suggest_merges` (name-similarity candidates for HITL), `set_conversation_person` (manual confirm that *learns* the channel so it sticks), web grouping + merged timeline + a `POST /api/link` confirm endpoint, and `jl link`. **Zero LLM** (consistent with the LLM-optional-core rule; LLM-assisted matching is a later optional enhancement).

**Tech stack:** Python 3.10+ stdlib, pytest. Pure/db logic unit-tested; live link verified on .178.

**Decisions (王总 2026-06-14):** pull ③ forward (before lark/wecom); HITL merge with learning.

## Data facts
- `.178`: 194 conversations, 0 persons. persons.json (Mac, real names — deploy like a secret, not in repo) seeds curated people + channels.
- phone conv `chat_id` = raw CallHistory number (e.g. `13000000001`); person phone channel may be `+8613000000001` → **tail_match**, not exact.
- wechat conv `chat_id` = wxid; person wechat channel identifier = wxid → exact. (李四's wechat has no wxid in persons.json → needs HITL candidate + learn.)

## File structure
- `src/jl/db.py` — add `link_conversations`, `set_conversation_person`, `suggest_merges`, `persons_overview`.
- `src/jl/web.py` — add `api_persons`, `api_person_timeline`, `api_merge_candidates`, `api_link` + `POST /api/link` route; update INDEX_HTML to group by person + merged timeline + candidate-confirm.
- `src/jl/cli.py` — `link` route + `cmd_link`.
- tests: `test_store.py`, `test_web.py`, `test_cli.py`.

---

## Task 1: db person-linking logic

**Files:** modify `src/jl/db.py`; test `tests/test_store.py`.

- [ ] **Step 1: failing test** — append to `tests/test_store.py`:

```python
def test_link_conversations_exact_and_tailmatch(conn):
    # person with a wechat wxid channel + a phone channel (+86 form)
    db.upsert_person(conn, id="lisi", name="李四", category="family",
                     threshold_days=14, aliases=["小四", "阿四"])
    db.upsert_channel(conn, person_id="lisi", kind="wechat", identifier="wxid_test_lisi")
    db.upsert_channel(conn, person_id="lisi", kind="phone", identifier="+8613000000001")
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s1")
    db.upsert_account(conn, account_id=2, platform="phone", self_id="s2")
    wc = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                chat_id="wxid_test_lisi", name="李四")
    pc = db.upsert_conversation(conn, account_id=2, platform="phone",
                                chat_id="13000000001", name="李四(电话名)")  # raw, no +86
    other = db.upsert_conversation(conn, account_id=2, platform="phone",
                                   chat_id="95720", name="95720")
    n = db.link_conversations(conn)
    assert n == 2
    assert db.get_conversation(conn, wc)["person_id"] == "lisi"   # exact wxid
    assert db.get_conversation(conn, pc)["person_id"] == "lisi"   # tail_match phone
    assert db.get_conversation(conn, other)["person_id"] is None    # unmatched


def test_link_conversations_skips_ambiguous(conn):
    db.upsert_person(conn, id="a", name="A", category="x", threshold_days=7, aliases=[])
    db.upsert_person(conn, id="b", name="B", category="x", threshold_days=7, aliases=[])
    db.upsert_channel(conn, person_id="a", kind="phone", identifier="+8613000000001")
    db.upsert_channel(conn, person_id="b", kind="phone", identifier="+8613000000001")
    db.upsert_account(conn, account_id=2, platform="phone", self_id="s")
    c = db.upsert_conversation(conn, account_id=2, platform="phone", chat_id="13000000001", name="?")
    db.link_conversations(conn)
    assert db.get_conversation(conn, c)["person_id"] is None   # 2 candidates -> leave for human


def test_set_conversation_person_learns_channel(conn):
    db.upsert_person(conn, id="lisi", name="李四", category="x", threshold_days=14, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    wc = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                chat_id="wxid_new", name="李四")
    db.set_conversation_person(conn, wc, "lisi")
    assert db.get_conversation(conn, wc)["person_id"] == "lisi"
    # learned: wxid_new now a channel on lisi, so re-link is idempotent/sticky
    kinds = {(c["kind"], c["identifier"]) for c in db.get_channels(conn, "lisi")}
    assert ("wechat", "wxid_new") in kinds


def test_suggest_merges_by_name(conn):
    db.upsert_person(conn, id="lisi", name="李四", category="x",
                     threshold_days=14, aliases=["小四"])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    wc = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                chat_id="wxid_x", name="李四")
    sugg = db.suggest_merges(conn)
    # unlinked "李四" conv proposed to merge into person lisi (alias match)
    assert any(s["conversation_id"] == wc and "lisi" in [p["id"] for p in s["candidates"]]
               for s in sugg)
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_store.py -k "link_conversations or set_conversation_person or suggest_merges" -q` → FAIL.

- [ ] **Step 3: implement** in `src/jl/db.py` (after the conversations section; uses `from .channels.phone import tail_match` lazily to avoid import cycles — import inside the function):

```python
def _match_person(conn, platform, chat_id):
    """Return [person_id] whose channels match this conversation's peer id.
    phone -> tail_match (country-code tolerant); others -> exact identifier."""
    rows = conn.execute("SELECT person_id, kind, identifier FROM channels").fetchall()
    hits = set()
    if platform == "phone":
        from .channels.phone import tail_match
        for r in rows:
            if r["kind"] == "phone" and tail_match(r["identifier"], chat_id):
                hits.add(r["person_id"])
    else:
        for r in rows:
            if r["kind"] == platform and r["identifier"] == chat_id:
                hits.add(r["person_id"])
    return sorted(hits)


def link_conversations(conn):
    """Auto-link unlinked conversations to persons by exact/tail channel match.
    Skips ambiguous (>1 candidate) — those go to the human. Returns count linked."""
    n = 0
    for c in conn.execute(
            "SELECT id, platform, chat_id FROM conversations WHERE person_id IS NULL").fetchall():
        cands = _match_person(conn, c["platform"], c["chat_id"])
        if len(cands) == 1:
            conn.execute("UPDATE conversations SET person_id=?, updated_at=? WHERE id=?",
                         (cands[0], _now(), c["id"]))
            n += 1
    conn.commit()
    return n


def set_conversation_person(conn, conversation_id, person_id):
    """Human-confirmed link. Also LEARNS the conversation's peer id as a channel on
    the person so future auto-links stick (the system gets smarter per confirmation)."""
    row = conn.execute("SELECT platform, chat_id FROM conversations WHERE id=?",
                        (conversation_id,)).fetchone()
    if row is None:
        raise ValueError(f"no conversation {conversation_id}")
    upsert_channel(conn, person_id=person_id, kind=row["platform"], identifier=row["chat_id"])
    conn.execute("UPDATE conversations SET person_id=?, updated_at=? WHERE id=?",
                 (person_id, _now(), conversation_id))
    conn.commit()
    log_event(conn, kind="link", person_id=person_id, actor="user",
              detail={"conversation_id": conversation_id, "learned": row["chat_id"]})


def suggest_merges(conn, limit=50):
    """HITL candidates: unlinked conversations whose name matches a person's name/alias.
    Returns [{conversation_id, name, platform, candidates:[person dict]}]. No silent merge."""
    persons = get_persons(conn)
    def name_keys(p):
        return [k for k in ([p["name"]] + list(p.get("aliases", []))) if k]
    out = []
    rows = conn.execute(
        "SELECT id, platform, name, chat_id FROM conversations WHERE person_id IS NULL "
        "AND name != '' ORDER BY last_activity_at DESC LIMIT ?", (limit,)).fetchall()
    for c in rows:
        cands = []
        for p in persons:
            if any(k and (k in c["name"] or c["name"] in k) for k in name_keys(p)):
                cands.append(p)
        if cands:
            out.append({"conversation_id": c["id"], "name": c["name"],
                        "platform": c["platform"], "candidates": cands})
    return out


def persons_overview(conn):
    """Each person with their linked conversation count + latest activity, for the inbox."""
    out = []
    for p in get_persons(conn):
        convs = get_conversations(conn, person_id=p["id"])
        if not convs:
            continue
        last = max((c["last_activity_at"] or 0 for c in convs), default=0)
        out.append({"id": p["id"], "name": p["name"], "category": p["category"],
                    "channels": sorted({c["platform"] for c in convs}),
                    "conversations": len(convs), "last_activity_at": last})
    out.sort(key=lambda x: x["last_activity_at"], reverse=True)
    return out
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_store.py -q` → all pass.
- [ ] **Step 5: commit** — `git add src/jl/db.py tests/test_store.py && git commit -m "feat(store): person linking (auto + HITL-confirm with channel learning) + merge candidates"`

---

## Task 2: web — person grouping, merged timeline, link confirm

**Files:** modify `src/jl/web.py`; test `tests/test_web.py`.

- [ ] **Step 1: failing test** — append to `tests/test_web.py`:

```python
def _seed_person(c):
    db.upsert_person(c, id="lisi", name="李四", category="family",
                     threshold_days=14, aliases=["小四"])
    db.upsert_channel(c, person_id="lisi", kind="phone", identifier="+8613000000001")
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s1")
    db.upsert_account(c, account_id=2, platform="phone", self_id="s2")
    wc = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_x", name="李四")
    pc = db.upsert_conversation(c, account_id=2, platform="phone", chat_id="13000000001", name="李四(电话名)")
    db.insert_messages(c, wc, [ingest.MsgRecord(msg_key="w:1", ts=10, content="微信你好", sender="李四")])
    db.insert_messages(c, pc, [ingest.MsgRecord(msg_key="p:1", ts=20, content="[通话] 95s", sender="李四")])
    return c, wc, pc


def test_api_persons_lists_linked_people():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    db.link_conversations(c)            # links phone via tail_match
    rows = web.api_persons(c)
    assert any(r["id"] == "lisi" for r in rows)


def test_api_person_timeline_merges_channels():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    db.set_conversation_person(c, db.get_conversations(c, account_id=1)[0]["id"], "lisi")
    db.link_conversations(c)
    tl = web.api_person_timeline(c, "lisi")
    contents = [m["content"] for m in tl]
    assert "微信你好" in contents and "[通话] 95s" in contents
    assert tl == sorted(tl, key=lambda m: m["ts"])   # merged, time-ordered


def test_api_link_confirms_and_learns():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    wcid = db.get_conversations(c, account_id=1)[0]["id"]
    res = web.api_link(c, {"conversation_id": wcid, "person_id": "lisi"})
    assert res["ok"] is True
    assert db.get_conversation(c, wcid)["person_id"] == "lisi"


def test_api_merge_candidates_present():
    c = db.connect(":memory:"); db.init_db(c); _seed_person(c)
    cands = web.api_merge_candidates(c)
    assert any(s["name"] == "李四" for s in cands)
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_web.py -k "persons or timeline or link or candidates" -q` → FAIL.

- [ ] **Step 3: implement** in `src/jl/web.py` — add handlers:

```python
def api_persons(conn):
    return db.persons_overview(conn)


def api_person_timeline(conn, person_id, limit=500):
    rows = conn.execute(
        """SELECT m.* FROM messages m
           JOIN conversations c ON c.id = m.conversation_id
           WHERE c.person_id = ? ORDER BY m.ts ASC LIMIT ?""",
        (person_id, limit)).fetchall()
    return [dict(r) for r in rows]


def api_merge_candidates(conn):
    return db.suggest_merges(conn)


def api_link(conn, payload):
    db.set_conversation_person(conn, int(payload["conversation_id"]), payload["person_id"])
    return {"ok": True}
```

Add a `/api/persons`, `/api/persons/{id}/timeline`, `/api/merge-candidates` to `do_GET`, and `/api/link` to `do_POST`:

In `do_GET` (before the 404):
```python
                if u.path == "/api/persons":
                    return self._send(200, api_persons(conn))
                if u.path.startswith("/api/persons/") and u.path.endswith("/timeline"):
                    pid = u.path.split("/")[3]
                    return self._send(200, api_person_timeline(conn, pid))
                if u.path == "/api/merge-candidates":
                    return self._send(200, api_merge_candidates(conn))
```
In `do_POST` (after the ingest branch, before 404 — note: `/api/link` also needs auth, already checked at top of do_POST):
```python
                if u.path == "/api/link":
                    return self._send(200, api_link(conn, payload))
```
(adjust do_POST so it routes both /api/ingest and /api/link after the shared auth + body-parse; keep the existing 404 for other paths.)

Update INDEX_HTML: add a "👤 人" section at the top of the left pane listing `api_persons` (click → merged timeline via `/api/persons/{id}/timeline`), keep the raw conversation list below, and a "🔗 待确认归并" panel from `/api/merge-candidates` with a confirm button that POSTs `/api/link` then reloads. Keep all rendering HTML-escaped (reuse `esc`). Token forwarding (TOK) already present — include it on the POST too.

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_web.py -q` → all pass.
- [ ] **Step 5: commit** — `git add src/jl/web.py tests/test_web.py && git commit -m "feat(web): person grouping + merged timeline + HITL merge-confirm UI"`

---

## Task 3: CLI `jl link`

**Files:** modify `src/jl/cli.py`; test `tests/test_cli.py`.

- [ ] **Step 1: failing test** — append to `tests/test_cli.py`:

```python
def test_route_link():
    assert cli.route(["link"]) == ("link", {})
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_cli.py -k route_link -q` → FAIL.

- [ ] **Step 3: implement** in `src/jl/cli.py`:
route (before final detail return): `if a == "link": return ("link", {})`
command:
```python
def cmd_link(conn, ctx):
    n = db.link_conversations(conn)
    sugg = db.suggest_merges(conn)
    print(f"🔗 自动归并 {n} 个会话到已知联系人。")
    if sugg:
        print(f"\n⚠️ {len(sugg)} 个待人工确认 (名字相似, 不自动并 — 去 Web 收件箱确认):")
        for s in sugg[:10]:
            cand = "/".join(p["name"] for p in s["candidates"])
            print(f"  • [{s['platform']}] {s['name']}  ?= {cand}")
```
dispatch in main: `elif command == "link": cmd_link(conn, ctx)`

- [ ] **Step 4: green** — `.venv/bin/python -m pytest -q` → all pass.
- [ ] **Step 5: commit** — `git add src/jl/cli.py tests/test_cli.py && git commit -m "feat(cli): jl link (auto-link + HITL merge candidates)"`

---

## Task 4: e2e on .178 + deploy + docs

- [ ] **Step 1:** full suite + secrets scan clean.
- [ ] **Step 2:** seed persons on .178: `rsync` Mac `~/.config/jl/persons.json` → `.178:~/.config/jl/persons.json` (real names, on-box not in repo, chmod 600), then on .178 `PYTHONPATH=src python3 -m jl.cli --migrate`.
- [ ] **Step 3:** redeploy code to .178 (`rsync` + `systemctl --user restart amr-web`); run `jl link` on .178 (via ssh, PYTHONPATH=src) → prints auto-linked count + 李四-type candidates.
- [ ] **Step 4:** verify on .178: `curl /api/persons?token=...` lists people; pick 李四, `curl /api/persons/lisi/timeline?token=...` shows merged wechat+phone messages. Confirm a candidate via `POST /api/link` and re-check grouping.
- [ ] **Step 5:** README: add "人归一" section (auto-link by channel, HITL confirm with learning, merged timeline). Note LLM-free (per the codified principle).
- [ ] **Step 6:** commit + push; merge to master.

## Self-review notes
- Scope: deterministic linking (exact + phone tail_match) + HITL name-candidate confirm with channel-learning + person-grouped inbox + merged timeline. Out: LLM-assisted matching (later, optional per LLM-optional-core), fuzzy cross-name clustering beyond substring, person create/edit UI (use persons.json seed for now).
- HITL: auto-link only on unambiguous single match; ambiguous + name-similar go to a human-confirm panel; every confirm logs a `link` event and learns the channel. No silent cross-person merge.
- LLM-free: entirely deterministic — satisfies the LLM-optional-core rule; an LLM matcher can later feed `suggest_merges` as an *additional* candidate source without changing the confirm gate.
- Types: `link_conversations`→int; `set_conversation_person(conn, conv_id, person_id)`; `suggest_merges`→[{conversation_id,name,platform,candidates:[person]}]; `persons_overview`→[{id,name,category,channels,conversations,last_activity_at}]; web `api_*` mirror these.
