"""Reply-draft assistant: build context from a conversation + person, ask the LLM for
N stance-varied 话术 versions, store as suggestions. LLM-optional (no llm → no-op).
Sending stays behind the outbox HITL gate — this only proposes."""
from __future__ import annotations

import os
import re

from . import db, llm as _llm, weighting

SENDABLE_PLATFORMS = ("wechat", "feishu", "wecom")

# 1a style guide (phase-A). 1b folds in the method playbook (below) as background.
STYLE_GUIDE = (
    "你是用户的中文沟通助手,为下面这段对话起草回复。围绕把事做成、不树敌、给确定"
    "(具体时间/地点/动作,不用'等会/晚点'),宁可糙而真,不要美而假。"
    "输出恰好 {n} 个不同风格的版本,每行一个,格式: 序号) 风格: 正文。"
    "风格依次为: 稳妥 / 直接 / 有温度。只输出这 {n} 行,不要额外说明。"
)

# 1b: the method playbook (M1–M8 + 《影响力》/Cialdini) is injected as *background
# guidance*, not as new stances. The guardrail keeps it from sliding into manipulation.
# The playbook *content* lives in a local file OUTSIDE this public repo (see load_playbook).
PLAYBOOK_GUIDANCE = (
    "\n\n以下是你的「打法库」(沟通方法 + 影响力原则),当作底料融入上面三档回复:"
    "可借其中原则让话更有说服力,但务必守底线——不操纵、不欺骗、不树敌,"
    "一切围绕把事做成、给对方确定。打法库:\n"
)


def _playbook_path():
    return os.environ.get("AMR_PLAYBOOK") or os.path.expanduser("~/.config/jl/playbook.md")


def load_playbook():
    """Read the local method playbook (M1–M8 + Cialdini). Its content lives OUTSIDE the
    public repo; an absent/unreadable file → '' so we degrade to the 1a style guide.
    Never raises — LLM-optional, and the playbook is optional on top of that."""
    try:
        with open(_playbook_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def build_context(conn, conversation_id, recent=12, playbook=None):
    conv = db.get_conversation(conn, conversation_id)
    person = db.get_person(conn, conv["person_id"]) if conv and conv.get("person_id") else None
    rows = conn.execute(
        "SELECT sender, direction, content FROM messages WHERE conversation_id=? "
        "ORDER BY ts DESC LIMIT ?", (conversation_id, recent)).fetchall()
    lines = []
    for r in reversed(rows):
        who = "我" if r["direction"] == "out" else (r["sender"] or "对方")
        lines.append(f"{who}: {r['content']}")
    pname = (person or {}).get("name") or (conv or {}).get("name") or "对方"
    pcat = (person or {}).get("category") or ""
    sys = STYLE_GUIDE.format(n=3)
    pb = load_playbook() if playbook is None else playbook
    if pb:
        sys = sys + PLAYBOOK_GUIDANCE + pb
    user = (f"对话对象: {pname}" + (f"(类别 {pcat})" if pcat else "") + "\n\n"
            "最近对话:\n" + "\n".join(lines) + "\n\n请起草回复。")
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


_LINE = re.compile(r"^\s*\d+\s*[)\).、]\s*([^:：]+?)\s*[:：]\s*(.+?)\s*$")


def parse_versions(text):
    """Parse 'N) 风格: 正文' lines into [{version_idx, stance, body}]."""
    out = []
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        out.append({"version_idx": len(out), "stance": m.group(1).strip(),
                    "body": m.group(2).strip()})
    return out


def generate_drafts(conn, conversation_id, *, n=3, llm_complete=_llm.complete):
    """Generate + store N reply suggestions. Returns count stored (0 if llm unavailable)."""
    messages = build_context(conn, conversation_id)
    res = llm_complete(messages, task="reply", conn=conn)
    if not res.ok or not res.text:
        return 0
    versions = parse_versions(res.text)[:n]
    if not versions:
        return 0
    db.clear_suggestions(conn, conversation_id)
    for v in versions:
        v["llm_provider"] = res.provider
        v["llm_model"] = res.model
    db.add_suggestions(conn, conversation_id, versions)
    return len(versions)


# ---- C / B3: proactive openers ------------------------------------------------

# A proactive opener is NOT a reply — there's no inbound to answer. It re-warms a
# relationship that has gone quiet. Same 3 stances; the playbook (1b) still applies.
OPENER_GUIDE = (
    "你是用户的中文沟通助手,为下面这位联系人起草一条**主动联络的开场白**(不是回复对方,"
    "是我主动找对方)。借自然由头(上次聊的事/共同进展,少用天气节气尬聊),给确定(具体下一步/"
    "时间),留个让对方好接话的钩子,不油腻、不交浅言深,围绕把关系和事往前推。"
    "输出恰好 {n} 个不同风格的版本,每行一个,格式: 序号) 风格: 正文。"
    "风格依次为: 稳妥 / 直接 / 有温度。只输出这 {n} 行,不要额外说明。"
)


def _person_days(conn, person_id):
    """Combined-freshness days since last interaction for a person (None = no data)."""
    last = db.derive_last_interactions(conn, person_id)
    chosen = weighting.combine([{"kind": k, "ts": d["ts"]} for k, d in last.items()])
    return chosen["days"] if chosen else None


def primary_conversation(conn, person_id):
    """Pick the best send target for a proactive opener: a private conversation on a
    sendable platform. Prefers the most-recently-active one (so a freshly-linked/used
    chat wins over a stale duplicate), tie-broken by richer history. Returns dict or None."""
    best, best_key = None, None
    for c in db.get_conversations(conn, person_id=person_id):
        if c.get("type") != "private" or c.get("platform") not in SENDABLE_PLATFORMS:
            continue
        n, last_ts = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM messages WHERE conversation_id=?",
            (c["id"],)).fetchone()
        key = (last_ts, n)   # newest activity first, then more messages
        if best_key is None or key > best_key:
            best, best_key = c, key
    return best


def build_opener_context(conn, person_id, recent=12, playbook=None):
    person = db.get_person(conn, person_id)
    conv = primary_conversation(conn, person_id)
    lines = []
    if conv:
        rows = conn.execute(
            "SELECT sender, direction, content FROM messages WHERE conversation_id=? "
            "ORDER BY ts DESC LIMIT ?", (conv["id"], recent)).fetchall()
        for r in reversed(rows):
            who = "我" if r["direction"] == "out" else (r["sender"] or "对方")
            lines.append(f"{who}: {r['content']}")
    pname = (person or {}).get("name") or "对方"
    pcat = (person or {}).get("category") or ""
    days = _person_days(conn, person_id)
    sys = OPENER_GUIDE.format(n=3)
    pb = load_playbook() if playbook is None else playbook
    if pb:
        sys = sys + PLAYBOOK_GUIDANCE + pb
    gap = (f"距上次互动约 {days:.0f} 天。" if days is not None else "")
    if lines:
        history = "最近对话:\n" + "\n".join(lines)
        ask = "请起草主动联络的开场白。"
    else:
        history = "尚无历史往来——这是初次主动联系。"
        # cold start: the model must NOT fabricate a shared past (would be exposed).
        ask = ("请起草初次主动联络的开场白:不要假装聊过、不要编造过往交流或具体事项;"
               "先简短表明身份/来意,给对方一个清晰、真实的由头或好处,留个好接的话头。")
    user = (f"联系人: {pname}" + (f"(类别 {pcat})" if pcat else "") + f" {gap}\n\n"
            + history + "\n\n" + ask)
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def generate_opener(conn, person_id, *, n=3, llm_complete=_llm.complete):
    """Generate + store N proactive opener suggestions on the person's primary
    conversation. Returns count stored; 0 if no send channel or llm unavailable."""
    conv = primary_conversation(conn, person_id)
    if not conv:
        return 0   # caller routes to 救补 (missing channel)
    messages = build_opener_context(conn, person_id)
    res = llm_complete(messages, task="opener", conn=conn)
    if not res.ok or not res.text:
        return 0
    versions = parse_versions(res.text)[:n]
    if not versions:
        return 0
    db.clear_suggestions(conn, conv["id"])
    for v in versions:
        v["llm_provider"] = res.provider
        v["llm_model"] = res.model
    db.add_suggestions(conn, conv["id"], versions, kind="opener")
    return len(versions)


def proactive_sweep(conn, *, llm_complete=_llm.complete):
    """Scoped proactive re-engagement: for each person who is 关注(watched) OR 🔴
    overdue, draft an opener (skipping those with a fresh opener). No send channel →
    missing_channel (救补). Returns {"drafted": [...], "missing_channel": [...]}."""
    drafted, missing = [], []
    for p in db.get_persons(conn):
        days = _person_days(conn, p["id"])
        is_red = weighting.color(days, p["threshold_days"]) == "🔴"
        if not (p.get("watch") or is_red):
            continue
        conv = primary_conversation(conn, p["id"])
        if not conv:
            missing.append(p["id"])
            continue
        if db.get_suggestions(conn, conv["id"], kind="opener"):
            continue   # fresh opener already queued — no spam re-drafting
        if generate_opener(conn, p["id"], llm_complete=llm_complete) > 0:
            drafted.append({"person_id": p["id"], "conversation_id": conv["id"]})
    return {"drafted": drafted, "missing_channel": missing}


def _latest_direction(conn, conversation_id):
    r = conn.execute("SELECT direction FROM messages WHERE conversation_id=? "
                     "ORDER BY ts DESC LIMIT 1", (conversation_id,)).fetchone()
    return r["direction"] if r else None


def auto_draft_sweep(conn, *, llm_complete=_llm.complete):
    """Scoped auto-draft: for private + person-linked + unmuted conversations whose
    latest message is inbound (awaiting my reply) and which have no fresh suggestions,
    generate drafts. Returns the conversation ids drafted for."""
    touched = []
    for c in db.get_conversations(conn, muted=False):
        if c["type"] != "private" or not c.get("person_id"):
            continue
        if _latest_direction(conn, c["id"]) != "in":
            continue
        if db.get_suggestions(conn, c["id"]):
            continue
        if generate_drafts(conn, c["id"], llm_complete=llm_complete) > 0:
            touched.append(c["id"])
    return touched
