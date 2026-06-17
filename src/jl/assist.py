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


# 口吻沉淀: few-shot the user's own recently-sent messages so drafts sound like THEM.
VOICE_GUIDE = (
    "\n\n下面是我(用户本人)最近亲手发出的几条消息,请**模仿我的口吻语气**"
    "(用词/句长/标点/称呼/语气词习惯),写得像我本人说的,别端着、别套路腔:\n")


def _voice_block(conn, conversation_id):
    samples = db.get_voice_samples(conn, conversation_id=conversation_id)
    return (VOICE_GUIDE + "\n".join("· " + s for s in samples)) if samples else ""


# 「我是谁」自然人画像 (性格/灵魂/喜好/工作特征/核心词). Injected into every "我" LLM
# interaction so drafts/diagnosis carry the user's person. Content lives LOCALLY (privacy).
SELF_PROFILE_GUIDE = (
    "\n\n关于用户本人(我是谁——起草/回应时自然体现这个人的性格、喜好、做事风格,"
    "别生硬复述、别假):\n")


def _self_profile_path():
    return os.environ.get("AMR_SELF_PROFILE") or os.path.expanduser("~/.config/jl/self_profile.md")


def load_self_profile():
    """The user's natural-person profile (性格/灵魂/喜好/核心词). Off public repo; absent → ''."""
    try:
        with open(_self_profile_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_self_profile(text):
    """Persist the user's natural-person profile to the local file (user-editable anytime,
    incl. after delivery to a client). Stays OUTSIDE the public repo."""
    path = _self_profile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text or "")


def _self_profile_block():
    p = load_self_profile()
    return (SELF_PROFILE_GUIDE + p) if p else ""


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


def build_context(conn, conversation_id, recent=12, playbook=None, guidance=""):
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
    if guidance:   # T4 诊断口径 drives the draft (沟通教练 → 起草)
        sys = sys + "\n\n本次诊断口径(据此起草,务必落实):" + guidance
    sys = sys + _self_profile_block()                 # 我是谁: the user's natural-person profile
    sys = sys + _voice_block(conn, conversation_id)   # 口吻沉淀: write like me
    user = (f"对话对象: {pname}" + (f"(类别 {pcat})" if pcat else "") + "\n\n"
            "最近对话:\n" + "\n".join(lines) + "\n\n请起草回复。")
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def _diagnosis_guidance(conn, conversation_id):
    """The 口径 from an open matter's T4 diagnosis on this conversation (drives 起草)."""
    for m in db.get_matters(conn, conversation_id=conversation_id, status="open"):
        kou = (m.get("diagnosis") or {}).get("口径")
        if kou:
            return kou
    return ""


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
    """Generate + store N reply suggestions. Returns count stored (0 if llm unavailable).
    If an open matter on this conversation carries a T4 诊断口径, it drives the drafting."""
    messages = build_context(conn, conversation_id,
                             guidance=_diagnosis_guidance(conn, conversation_id))
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


def _sendable_chat_ids():
    """Live, selectable chat ids across sendable channels. Best-effort: a channel whose
    live list can't be fetched contributes nothing (caller degrades). Network."""
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
    """Best send-target conversation, via endpoint routing (weight×recency, human pin
    override, sendable fallback) over a person's多渠道多号 endpoints. When the live
    chat list can't be fetched, every endpoint is treated sendable and recency decides
    (network-optional). Returns the conversation dict or None."""
    from . import routing
    chans = {(c["kind"], c["identifier"]): c for c in db.get_channels(conn, person_id)}
    eps = []
    for e in db.endpoints_with_recency(conn, person_id):
        if e["kind"] not in SENDABLE_PLATFORMS:
            continue
        e = dict(e, pinned=(chans.get((e["kind"], e["identifier"])) or {}).get("pinned", 0))
        eps.append(e)
    if not eps:
        return None
    live = _sendable_chat_ids()
    sendable = (lambda e: e["chat_id"] in live or e["identifier"] in live) if live else (lambda e: True)
    best = routing.best_endpoint(eps, sendable=sendable)
    if not best:   # nothing currently sendable → fall back to most-recent overall
        best = routing.best_endpoint(eps, sendable=lambda e: True)
    return db.get_conversation(conn, best["conversation_id"]) if best else None


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
    sys = sys + _self_profile_block()                # 我是谁
    if conv:
        sys = sys + _voice_block(conn, conv["id"])   # 口吻沉淀: opener in my voice too
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
