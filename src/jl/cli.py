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


def _actor():
    """Who is acting — for the audit trail. JL_ACTOR (e.g. cron/<name>) wins,
    else the OS user, else a generic fallback."""
    return os.environ.get("JL_ACTOR") or os.environ.get("USER") or "cli"


def _opt_value(args, flag):
    """Return the value following `flag` in args, or None. Skips it if the next
    token is itself a flag (e.g. `--channel --confirm` -> None, not '--confirm')."""
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            return args[i + 1]
    return None


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
    if a == "reset":
        return ("reset", {
            "confirm": "--confirm" in args,
            "platform": _opt_value(args, "--channel"),
            "include_accounts": "--all" in args,
        })
    if a == "ignite":
        ch = args[1] if len(args) > 1 and not args[1].startswith("--") else "wechat"
        return ("ignite", {"channel": ch})
    if a == "poll":
        return ("poll", {"interval": int(_opt_value(args, "--interval") or 300)})
    if a == "web":
        return ("web", {"port": int(_opt_value(args, "--port") or 8088),
                        "host": _opt_value(args, "--host") or "0.0.0.0"})
    if a == "push":
        return ("push", {
            "channel": args[1] if len(args) > 1 and not args[1].startswith("--") else "phone",
            "remote": _opt_value(args, "--remote") or "http://192.168.31.178:8088",
            "token": _opt_value(args, "--token") or "",
        })
    if a == "link":
        return ("link", {})
    return ("detail", {"name": a})


def days_str(days):
    return f"{days:.1f}" if days is not None else "?"


# ----- commands -------------------------------------------------------------

def cmd_sweep(conn, ctx):
    persons = db.get_persons(conn)
    print(f"\n🟢🟡🔴 关系账户健康度 — {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'姓名':<18} {'类别':<14} {'综合(天)':<10} {'渠道':<8} {'状态'}")
    print("─" * 70)
    red = []
    for p in persons:
        last = db.derive_last_interactions(conn, p["id"])
        signals = [{"kind": plat, "ts": d["ts"]} for plat, d in last.items()]
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
    db.log_event(conn, kind="sweep", actor=_actor(),
                 detail={"persons": len(persons), "red": len(red)})


def cmd_detail(conn, ctx, name):
    p = _find_person(conn, name)
    if not p:
        names = ", ".join(x["name"] for x in db.get_persons(conn))
        print(f"❌ 找不到 {name}. 可选: {names}")
        return
    print(f"\n=== {p['name']} ({p['category']}) ===")
    print(f"别名: {', '.join(p['aliases']) or '-'}")
    print(f"阈值: {p['threshold_days']} 天")
    last = db.derive_last_interactions(conn, p["id"])
    if not last:
        print("(无消息记录)")
    signals = []
    for plat, d in last.items():
        signals.append({"kind": plat, "ts": d["ts"]})
        days = weighting.days_since(d["ts"])
        print(f"  {plat:<8} last: {d['summary']} ({days:.1f} 天前)")
    chosen = weighting.combine(signals)
    if chosen:
        col = weighting.color(chosen["days"], p["threshold_days"])
        print(f"\n综合: {col} {chosen['days']:.1f} 天 (via {chosen['kind']})")
    db.log_event(conn, kind="detail", person_id=p["id"], actor=_actor(), detail={"platforms": list(last)})


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


def cmd_reset(conn, params):
    counts = db.reset_store(conn, dry_run=True,
                            platform=params["platform"],
                            include_accounts=params["include_accounts"])
    base = params["platform"] or "ALL channels"
    scope = base + (" + accounts" if params["include_accounts"] else "")
    print(f"\n⚠️ 复位 reset — 影响范围: {scope}")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    if not params["confirm"]:
        print("\n这是 dry-run。确认无误后加 --confirm 真正清除 (persons 不受影响)。")
        return
    # audit BEFORE the wipe so the trace survives it
    db.log_event(conn, kind="reset", actor=_actor(),
                 detail={"scope": scope, "counts": counts})
    db.reset_store(conn, dry_run=False, platform=params["platform"],
                   include_accounts=params["include_accounts"])
    print("\n✅ 已清除。可重新点火 (jl ignite — B 阶段) 灌入。")


def _ensure_account(conn, account_id, platform, label, host=""):
    if account_id not in {a["account_id"] for a in db.get_accounts(conn)}:
        db.upsert_account(conn, account_id=account_id, platform=platform,
                          label=label, host=host)
    return account_id


def cmd_ignite(conn, ctx):
    from . import ingest_run
    ch = ctx.get("channel", "wechat")
    if ch == "wechat":
        from .channels.fullwechat import FullWechatAdapter, DEFAULT_URL
        adapter = FullWechatAdapter()
        aid = _ensure_account(conn, 1, "wechat", "fullwechat #1", DEFAULT_URL)
    elif ch == "lark":
        from .channels.lark import LarkAdapter
        adapter = LarkAdapter()
        aid = _ensure_account(conn, 3, "feishu", "feishu #1")
    else:
        print(f"❌ 未知渠道: {ch} (支持: wechat, lark)")
        return
    try:
        n = ingest_run.ignite(conn, adapter, account_id=aid, actor=_actor())
    except RuntimeError as e:
        print(f"❌ 点火失败 [{ch}]: {e}")
        if ch == "lark":
            print("  提示: 飞书需 user 身份授权,且 App 须含 im:chat:read / im:message:read "
                  "scope。在该机跑: lark-cli auth login --as user 重新授权。")
        return
    print(f"✅ 点火完成 [{ch}]: 新增 {n} 条消息入库 (account #{aid})")


def cmd_poll(conn, ctx):
    import time as _t
    from .channels.fullwechat import FullWechatAdapter, DEFAULT_URL
    from . import ingest_run
    aid = _ensure_account(conn, 1, "wechat", "fullwechat #1", DEFAULT_URL)
    interval = ctx.get("interval", 300)
    print(f"🔁 poll 每 {interval}s 拉新 (Ctrl-C 停)")
    while True:
        n = ingest_run.ignite(conn, FullWechatAdapter(), account_id=aid, actor="poll")
        print(f"  [{_t.strftime('%H:%M:%S')}] +{n}")
        _t.sleep(interval)


def cmd_web(conn, ctx):
    from . import web
    web.serve(conn_path=db.DEFAULT_DB, host=ctx.get("host", "0.0.0.0"),
              port=ctx.get("port", 8088))


def cmd_push(conn, ctx):
    from . import push as push_mod
    ch = ctx.get("channel", "phone")
    if ch == "phone":
        from .channels.phone import PhoneAdapter
        adapter, account_id, label = PhoneAdapter(), 2, "phone"
    else:
        print(f"❌ 未知渠道: {ch} (当前支持: phone)")
        return
    payload = push_mod.build_payload(adapter, account_id=account_id, label=label)
    nconv = len(payload["conversations"])
    res = push_mod.push(ctx["remote"], ctx.get("token", ""), payload)
    print(f"✅ push {ch}: {nconv} 会话 → {ctx['remote']}  入库 {res.get('messages')} 条新消息")


def cmd_link(conn, ctx):
    n = db.link_conversations(conn)
    sugg = db.suggest_merges(conn)
    print(f"🔗 自动归并 {n} 个会话到已知联系人。")
    if sugg:
        print(f"\n⚠️ {len(sugg)} 个待人工确认 (名字相似, 不自动并 — 去 Web 收件箱确认):")
        for s in sugg[:10]:
            cand = "/".join(p["name"] for p in s["candidates"])
            print(f"  • [{s['platform']}] {s['name']}  ?= {cand}")


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
    if command == "detail":
        cmd_detail(conn, ctx, params["name"])
    elif command == "reset":
        cmd_reset(conn, params)
    elif command == "ignite":
        ctx["channel"] = params["channel"]; cmd_ignite(conn, ctx)
    elif command == "poll":
        ctx["interval"] = params["interval"]; cmd_poll(conn, ctx)
    elif command == "web":
        ctx.update(params); cmd_web(conn, ctx)
    elif command == "push":
        ctx.update(params); cmd_push(conn, ctx)
    elif command == "link":
        cmd_link(conn, ctx)
    else:
        _DISPATCH[command](conn, ctx)
    conn.close()


if __name__ == "__main__":
    main()
