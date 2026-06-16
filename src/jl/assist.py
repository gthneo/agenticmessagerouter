"""Reply-draft assistant: build context from a conversation + person, ask the LLM for
N stance-varied 话术 versions, store as suggestions. LLM-optional (no llm → no-op).
Sending stays behind the outbox HITL gate — this only proposes."""
from __future__ import annotations

import re

from . import db, llm as _llm

# 1a style guide (phase-A). Method content (M1–M8/Cialdini) injected in 1b.
STYLE_GUIDE = (
    "你是用户的中文沟通助手,为下面这段对话起草回复。围绕把事做成、不树敌、给确定"
    "(具体时间/地点/动作,不用'等会/晚点'),宁可糙而真,不要美而假。"
    "输出恰好 {n} 个不同风格的版本,每行一个,格式: 序号) 风格: 正文。"
    "风格依次为: 稳妥 / 直接 / 有温度。只输出这 {n} 行,不要额外说明。"
)


def build_context(conn, conversation_id, recent=12):
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
