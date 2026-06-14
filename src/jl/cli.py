"""jl CLI — dispatch over the SQLite source of truth.

  jl              full sweep + weighted coloring
  jl <名>         single-person deep dive
  jl 救补          missing wxid/phone queue
  jl --migrate    persons.json -> SQLite (idempotent)
  jl --dump-yaml  human-readable view of the SQLite truth
  jl --tokens     token-usage + reach feedback

Human-in-the-loop: jl never sends anything. It surfaces a red list and (later)
drafts; the human decides. Every sweep/migration is written to the events table.
"""
from __future__ import annotations

import os
import sys
import time

from . import db, migrate, weighting
from .channels import phone as phone_ch
from .channels import wechat as wechat_ch

# channel kind -> adapter callable returning (ts, summary) for a channel row
_ADAPTERS = {
    "wechat": lambda ch, ctx: wechat_ch.last(ch, url=ctx.get("wx_url")),
    "phone": lambda ch, ctx: phone_ch.last(ch),
}


def _actor():
    """Who is acting — for the audit trail. JL_ACTOR (e.g. cron/<name>) wins,
    else the OS user, else a generic fallback."""
    return os.environ.get("JL_ACTOR") or os.environ.get("USER") or "cli"


def route(args):
    """Pure: map argv (without program name) to (command, params)."""
    if not args:
        return ("sweep", {})
    a = args[0]
    if a in ("--migrate",):
        return ("migrate", {})
    if a in ("--dump-yaml",):
        return ("dump_yaml", {})
    if a in ("--tokens",):
        return ("tokens", {})
    if a in ("救补", "--missing"):
        return ("quebu", {})
    return ("detail", {"name": a})


# ----- per-person signal gathering ------------------------------------------

def _gather(conn, person, ctx):
    """Reach every channel, persist latest interactions, return weighting signals."""
    signals = []
    reaches = 0
    for ch in db.get_channels(conn, person["id"]):
        adapter = _ADAPTERS.get(ch["kind"])
        if not adapter:
            continue
        ts, summary = adapter(ch, ctx)
        reaches += 1
        if ts:
            db.record_interaction(conn, channel_id=ch["id"], ts=ts, summary=summary)
        signals.append({"kind": ch["kind"], "ts": ts, "summary": summary,
                        "channel_id": ch["id"]})
    return signals, reaches


def days_str(days):
    return f"{days:.1f}" if days is not None else "?"


# ----- commands -------------------------------------------------------------

def cmd_sweep(conn, ctx):
    persons = db.get_persons(conn)
    print(f"\n🟢🟡🔴 关系账户健康度 — {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"   微信 MCP: {'✅' if ctx.get('wx_url') else '❌ offline'}\n")
    print(f"{'姓名':<18} {'类别':<14} {'综合(天)':<10} {'渠道':<8} {'状态'}")
    print("─" * 70)
    red = []
    total_reach = 0
    for p in persons:
        signals, reaches = _gather(conn, p, ctx)
        total_reach += reaches
        chosen = weighting.combine(signals)
        comb_d = chosen["days"] if chosen else None
        col = weighting.color(comb_d, p["threshold_days"])
        via = chosen["kind"] if chosen else "-"
        print(f"{p['name']:<18} {p['category']:<14} {days_str(comb_d):<10} {via:<8} {col}")
        if col == "🔴":
            red.append((p, comb_d))
    if red:
        print("\n🔴 红色清单 (建议主动联络, 发不发你决定):")
        for p, d in red:
            d_s = f"{d:.1f} 天" if d is not None else "全渠道空"
            print(f"  • {p['name']:<14} 距上次互动 {d_s} (阈值 {p['threshold_days']} 天)")
    _print_missing(conn, persons)
    db.record_tokens(conn, channel_kind="*", op="sweep", reach_count=total_reach)
    db.log_event(conn, kind="sweep", actor=_actor(),
                 detail={"persons": len(persons), "red": len(red),
                         "reaches": total_reach})


def cmd_detail(conn, ctx, name):
    p = _find_person(conn, name)
    if not p:
        names = ", ".join(x["name"] for x in db.get_persons(conn))
        print(f"❌ 找不到 {name}. 可选: {names}")
        return
    print(f"\n=== {p['name']} ({p['category']}) ===")
    print(f"别名: {', '.join(p['aliases']) or '-'}")
    print(f"阈值: {p['threshold_days']} 天")
    signals, reaches = _gather(conn, p, ctx)
    if not signals:
        print("(无已配置渠道)")
    for s in signals:
        d = weighting.days_since(s["ts"])
        if s["ts"]:
            print(f"  {s['kind']:<8} last: {s['summary']} ({d:.1f} 天前)")
        else:
            print(f"  {s['kind']:<8} last: (无)")
    chosen = weighting.combine(signals)
    if chosen:
        col = weighting.color(chosen["days"], p["threshold_days"])
        print(f"\n综合: {col} {chosen['days']:.1f} 天 (via {chosen['kind']})")
    # deep-dive also reaches live channels — account for it and leave a trace.
    db.record_tokens(conn, channel_kind="*", op="detail", reach_count=reaches)
    db.log_event(conn, kind="detail", person_id=p["id"], actor=_actor(),
                 detail={"reaches": reaches})


def cmd_quebu(conn, ctx):
    persons = db.get_persons(conn)
    print("\n⚠️ 待补队列:")
    any_missing = _print_missing(conn, persons, header=False)
    if not any_missing:
        print("  (无缺号)")


def cmd_migrate(conn, ctx):
    n = migrate.migrate_persons_json(conn)
    print(f"✅ migration 完成: {n} 人 → SQLite ({db.DEFAULT_DB})")


def cmd_dump_yaml(conn, ctx):
    persons = db.get_persons(conn)
    print("# jl SQLite 真相源 — 人读视图 (dump-yaml)")
    print(f"# 生成 {time.strftime('%Y-%m-%d %H:%M')}  共 {len(persons)} 人")
    print("persons:")
    for p in persons:
        print(f"  - id: {p['id']}")
        print(f"    name: {p['name']}")
        print(f"    category: {p['category']}")
        print(f"    threshold_days: {p['threshold_days']}")
        if p["aliases"]:
            print(f"    aliases: [{', '.join(p['aliases'])}]")
        chans = db.get_channels(conn, p["id"])
        if chans:
            print("    channels:")
            for c in chans:
                lbl = f"  # {c['label']}" if c["label"] else ""
                print(f"      - {c['kind']}: {c['identifier']}{lbl}")


def cmd_tokens(conn, ctx):
    t = db.token_summary(conn)
    print("\n📊 Token / reach 用量 (累计):")
    print(f"  reach 次数: {t['reach_count']}")
    print(f"  tokens_in:  {t['tokens_in']}")
    print(f"  tokens_out: {t['tokens_out']}")


# ----- helpers --------------------------------------------------------------

def _find_person(conn, name):
    for p in db.get_persons(conn):
        if name == p["id"] or name == p["name"] or name in p["aliases"]:
            return p
    return None


def _print_missing(conn, persons, header=True):
    missing = []
    for p in persons:
        kinds = {c["kind"] for c in db.get_channels(conn, p["id"])}
        miss = []
        if "wechat" not in kinds:
            miss.append("微信 wxid")
        if "phone" not in kinds:
            miss.append("电话")
        if miss:
            missing.append((p["name"], miss))
    if missing and header:
        print("\n⚠️ 待补 (jl 救补 看队列):")
    for name, miss in missing:
        print(f"  • {name:<14} 缺: {', '.join(miss)}")
    return bool(missing)


_DISPATCH = {
    "sweep": cmd_sweep,
    "quebu": cmd_quebu,
    "migrate": cmd_migrate,
    "dump_yaml": cmd_dump_yaml,
    "tokens": cmd_tokens,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command, params = route(argv)
    conn = db.connect()
    db.init_db(conn)
    ctx = {}
    if command in ("sweep", "detail"):
        ctx["wx_url"] = wechat_ch.pick_endpoint()
    if command == "detail":
        cmd_detail(conn, ctx, params["name"])
    else:
        _DISPATCH[command](conn, ctx)
    conn.close()


if __name__ == "__main__":
    main()
