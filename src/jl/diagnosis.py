"""T4 诊断引擎 (沟通教练：外圆内方 + 以牙还牙以蜜还蜜).

Hybrid engine: a local rubric (method content) + LLM scoring into a STRUCTURED diagnosis
object + human review. LLM-optional (no model → empty diagnosis, human fills manually),
不黑箱 (structured + inspectable). The rubric / 错位词典 content lives OUTSIDE the public
repo (`~/.config/jl/diagnosis_rubric.md`); this module is only the mechanism."""
from __future__ import annotations

import json
import os
import re

from . import db, llm as _llm

# Structured diagnosis fields. 对方姿态/对等 = the tit-for-tat game layer; 圆/方/力 +
# 错位 = 外圆内方; 真心 = the manipulation guardrail.
DIAGNOSIS_KEYS = ["对方姿态", "对等", "圆", "方", "力", "错位", "真心", "一句话诊断", "口径"]

DIAGNOSIS_GUIDE = (
    "你是沟通诊断引擎,帮用户把关系和事往前推。核心博弈原则:**以牙还牙、以蜜还蜜**——"
    "先判对方姿态(蜜/牙/中性),再校验拟回应是否对等(蜜还蜜;牙则克制对等回应,不升级不记仇,"
    "对方回头即回蜜)。再走外圆内方(圆=接情绪 / 方=对事立边界 / 力=给确定)+错位6型。"
    "守底线:辅助真心不制造假暖、诚实>漂亮、对等≠报复升级。"
    "只输出一个 JSON 对象,键:对方姿态(蜜|牙|中性)、对等(ok|过软|过硬)、圆(ok|缺|过)、"
    "方(ok|污|先)、力(ok|先|无)、错位(字符串,无则空)、真心(ok|假暖)、一句话诊断、口径。"
    "不要输出 JSON 以外的任何文字。"
)


def _rubric_path():
    return os.environ.get("AMR_DIAGNOSIS_RUBRIC") or os.path.expanduser(
        "~/.config/jl/diagnosis_rubric.md")


def load_rubric():
    """Read the local 外圆内方/错位词典 rubric. Content lives OUTSIDE the public repo;
    absent/unreadable → '' (degrade to the built-in guide). Never raises."""
    try:
        with open(_rubric_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def build_diagnosis_context(conn, conversation_id, recent=12, rubric=None):
    rows = conn.execute(
        "SELECT sender, direction, content FROM messages WHERE conversation_id=? "
        "ORDER BY ts DESC LIMIT ?", (conversation_id, recent)).fetchall()
    lines = []
    for r in reversed(rows):
        who = "我" if r["direction"] == "out" else (r["sender"] or "对方")
        lines.append(f"{who}: {r['content']}")
    rb = load_rubric() if rubric is None else rubric
    from . import assist
    sys = (DIAGNOSIS_GUIDE + (("\n\n打法库/rubric:\n" + rb) if rb else "")
           + assist._self_profile_block())   # 我是谁: diagnose in light of who the user is
    user = "最近对话:\n" + "\n".join(lines) + "\n\n请输出诊断 JSON。"
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


_JSON = re.compile(r"\{.*\}", re.S)


def parse_diagnosis(text):
    """Extract the structured diagnosis dict from an LLM response (tolerates ```json
    fences / surrounding prose). Returns only known keys, or {} if unparseable."""
    if not text:
        return {}
    m = _JSON.search(text)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: d[k] for k in DIAGNOSIS_KEYS if k in d}


def diagnose(conn, conversation_id, *, matter_id=None, rubric=None, llm_complete=_llm.complete):
    """Diagnose a conversation into a structured object. LLM-optional: no model → {} (human
    fills manually). Stores onto a matter when matter_id given. Token-accounted via llm layer."""
    msgs = build_diagnosis_context(conn, conversation_id, rubric=rubric)
    res = llm_complete(msgs, task="diagnose", conn=conn)
    if not res.ok or not res.text:
        return {}
    d = parse_diagnosis(res.text)
    if matter_id is not None and d:
        db.set_matter_diagnosis(conn, matter_id, d)
    return d
