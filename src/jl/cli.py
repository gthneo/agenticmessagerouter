"""jl CLI — dispatch over the SQLite source of truth.

  jl              full sweep + weighted coloring
  jl <名>         single-person deep dive
  jl 救补          missing wxid/phone queue
  jl account onboard [--registry <path>] [--commit]  配置驱动接入 fullwechat 后端
                  (读注册表 → 逐条身份预检 → dry-run → --commit 接入；防绑错号)
  jl person refresh-name [<名>]  display name → 最新会话名 (dry-run → --commit)
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
    if a == "migrate-kinds":
        return ("migrate_kinds", {"confirm": "--confirm" in args})
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
    if a == "draft-assist":
        cid = args[1] if len(args) > 1 and not args[1].startswith("--") else None
        return ("draft_assist", {"conversation_id": int(cid) if cid else None})
    if a in ("主动", "proactive"):
        name = next((x for x in args[1:] if not x.startswith("--")), None)
        return ("proactive", {"name": name})
    if a in ("关注", "--watch"):
        name = next((x for x in args[1:] if not x.startswith("--")), None)
        return ("watch", {"name": name, "on": "--off" not in args})
    if a in ("连", "连渠道"):
        rest = [x for x in args[1:] if not x.startswith("--")]
        return ("connect", {"name": rest[0] if rest else None,
                            "chat_id": rest[1] if len(rest) > 1 else None})
    if a in ("电话归一", "dedup-phone"):
        return ("dedup_phone", {})
    if a in ("logs", "日志"):
        return ("logs", {"level": _opt_value(args, "--level"),
                         "component": _opt_value(args, "--component")})
    if a in ("解绑", "unlink"):
        cid = args[1] if len(args) > 1 and not args[1].startswith("--") else None
        return ("unlink", {"conversation_id": int(cid) if cid else None})
    if a in ("account", "账号"):
        return _route_account(args[1:])
    if a in ("person", "人"):
        return _route_person(args[1:])
    return ("detail", {"name": a})


# the per-account flags `account add|set` accepts: CLI --kebab → plan key
_ACCOUNT_FLAGS = {
    "--platform": "platform", "--tool": "tool", "--host": "host",
    "--self-id": "self_id", "--label": "label", "--token-file": "token_file",
}


def _route_account(rest):
    """Sub-route `jl account <sub> ...`. ls | add | set <id>, plus --commit/--yes
    (write only on confirm; default is a dry-run that prints the plan)."""
    sub = rest[0] if rest and not rest[0].startswith("--") else "ls"
    params = {"sub": sub, "commit": ("--commit" in rest or "--yes" in rest)}
    tail = rest[1:]
    if sub == "onboard":
        params["registry"] = _opt_value(rest, "--registry")
        return ("account", params)
    if sub == "set":
        aid = tail[0] if tail and not tail[0].startswith("--") else None
        params["account_id"] = int(aid) if aid is not None else None
        tail = tail[1:] if aid is not None else tail
    if sub in ("add", "set"):
        flags = {}
        for cli_flag, key in _ACCOUNT_FLAGS.items():
            v = _opt_value(rest, cli_flag)
            if v is not None:
                flags[key] = v
        params["flags"] = flags
    return ("account", params)


def _route_person(rest):
    """Sub-route `jl person <sub> ...`. Today only refresh-name [<id-or-name>],
    plus --commit/--yes (write only on confirm; default is a dry-run). Room for
    future person subcommands; only refresh-name is built (YAGNI)."""
    sub = rest[0] if rest and not rest[0].startswith("--") else "refresh-name"
    tail = rest[1:] if rest and not rest[0].startswith("--") else rest
    target = next((x for x in tail if not x.startswith("--")), None)
    return ("person", {"sub": sub, "target": target,
                       "commit": ("--commit" in rest or "--yes" in rest)})


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


def cmd_migrate_kinds(conn, params):
    """迁移闸: messages.type 数字串 → canonical kind (保守集)。dry-run → --confirm。"""
    r = db.migrate_types_to_kinds(conn, dry_run=True)
    print(f"\n🧬 type→kind 迁移 — 将改 {r['changed']} 条:")
    for k, v in (r["by_kind"] or {}).items():
        print(f"  {k}: {v}")
    print(f"  (49 appmsg {r['skipped_appmsg49']} 条不动 — 子类需后端 canonical / re-poll)")
    if not params["confirm"]:
        print("\n这是 dry-run。确认后加 --confirm 写入 (幂等，建议先备份 jl.db)。"
              "\n注意: 旧后端 re-poll 会把数字 type 覆盖回来 — 真正持久要等后端吐 canonical。")
        return
    r2 = db.migrate_types_to_kinds(conn, dry_run=False)
    print(f"\n✅ 已迁移 {r2['changed']} 条 type→kind。")


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


def _fullwechat_targets(conn):
    """所有 tool=fullwechat 的账号 → [(account_id, url, token), ...].
    url = account.host 或默认; token = account.cred_ref 文件内容 或默认。
    按账号绑后端(Router 命门)。若无配置账号, 回退到 [(1, default_url, default_token)]。
    """
    import os as _os
    from .channels.fullwechat import _default_url, _token
    out = []
    for a in db.get_accounts(conn):
        if a.get("tool") != "fullwechat":
            continue
        url = (a.get("host") or "").strip() or _default_url()
        token = None
        cred = (a.get("cred_ref") or "").strip()
        if cred:
            p = _os.path.expanduser(cred)
            if _os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    token = f.read().strip()
        out.append((a["account_id"], url, token or _token()))
    if not out:
        out = [(1, _default_url(), _token())]
    return out


def cmd_poll(conn, ctx):
    import time as _t
    from .channels.fullwechat import FullWechatAdapter, DEFAULT_URL
    from . import ingest_run
    _ensure_account(conn, 1, "wechat", "fullwechat #1", DEFAULT_URL)  # 确保默认账号存在
    interval = ctx.get("interval", 300)
    print(f"🔁 poll 每 {interval}s 拉新 (Ctrl-C 停)")
    while True:
        total = 0
        for aid, url, token in _fullwechat_targets(conn):
            try:
                total += ingest_run.ignite(conn, FullWechatAdapter(url=url, token=token),
                                           account_id=aid, actor="poll")
            except Exception as e:
                print(f"  [poll] acct {aid} @ {url} 失败: {e}")
        db.apply_self_directions(conn)  # 回灌的自我发出消息标 direction=out (右绿气泡)
        from . import assist as _assist
        _assist.transcribe_sweep(conn)   # 语音转写(LLM-optional·无ASR则空转)
        print(f"  [{_t.strftime('%H:%M:%S')}] +{total}")
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


def cmd_draft_assist(conn, ctx):
    from . import assist, llm
    cid = ctx.get("conversation_id")
    if not llm.available():
        print("⚠️ 未配置 LLM(ANTHROPIC_API_KEY 缺)——助手不可用,请手敲。(LLM-optional)")
        return
    if cid:
        n = assist.generate_drafts(conn, cid)
        print(f"✨ 会话 {cid}: 生成 {n} 版话术(去收件箱挑/改/发)")
    else:
        touched = assist.auto_draft_sweep(conn)
        print(f"✨ 自动拟稿: {len(touched)} 个待回会话已生成话术")


def cmd_unlink(conn, ctx):
    cid = ctx.get("conversation_id")
    if not cid:
        print("用法: jl 解绑 <会话id>（把误并的会话从某人拆出去）")
        return
    freed = db.unlink_conversation(conn, cid)
    if freed:
        db.log_event(conn, kind="unlink", person_id=freed, actor=_actor(),
                     detail={"conversation_id": cid})
        print(f"🔓 会话 {cid} 已从 {freed} 拆出（端点也已移除）。")
    else:
        print(f"会话 {cid} 本就未归人，无需拆。")


def cmd_logs(conn, ctx):
    rows = db.get_logs(conn, level=ctx.get("level"), component=ctx.get("component"), limit=50)
    print(f"\n📋 运维日志 (level>={ctx.get('level') or 'ALL'} component={ctx.get('component') or '*'}):")
    for r in rows:
        print(f"  [{time.strftime('%m-%d %H:%M', time.localtime(r['ts']))}] {r['level']:<5} {r['component']:<10} {r['msg']}")
    if not rows:
        print("  (无日志)")


def cmd_dedup_phone(conn, ctx):
    n = db.dedup_phone_conversations(conn)
    db.log_event(conn, kind="dedup_phone", actor=_actor(), detail={"folded": n})
    print(f"📞 电话归一: 合并了 {n} 条同号重复会话(+86/格式变体)，号码已规范化。"
          if n else "📞 电话归一: 无重复(号码已规范)。")


def cmd_connect(conn, ctx):
    """Link a person to a live fullwechat chat id, pulling its recent messages so the
    chat becomes a real (sendable) send target. Fixes 'reachable-but-not-linked' people
    (e.g. a cold contact whose WeChat exists) and lets a stale link be re-pointed."""
    from . import ingest
    from .channels.fullwechat import FullWechatAdapter, DEFAULT_URL
    name, chat_id = ctx.get("name"), ctx.get("chat_id")
    p = _find_person(conn, name) if name else None
    if not p or not chat_id:
        print("用法: jl 连 <名> <微信chat_id>（chat_id 是 fullwechat 的 id，如 m… / adambb_joy）")
        return
    aid = _ensure_account(conn, 1, "wechat", "fullwechat #1", DEFAULT_URL)
    try:
        msgs = FullWechatAdapter()._messages(chat_id, 30, 0)
    except Exception as e:
        print(f"⚠️ 拉取 {chat_id} 失败: {e}")
        return
    conv = ingest.ConvRecord(chat_id=chat_id, name=p["name"], type="private")
    cid, n = db.ingest_records(conn, account_id=aid, platform="wechat", conv=conv, msgs=msgs)
    db.link_person(conn, cid, p["id"])
    db.log_event(conn, kind="connect", person_id=p["id"], actor=_actor(),
                 detail={"chat_id": chat_id, "msgs": n})
    print(f"🔗 已把 {p['name']} 连到微信会话 {chat_id}（会话 {cid}，拉到 {n} 条）。"
          f"\n   jl 主动 {p['id']} 可拟开场。")


def cmd_watch(conn, ctx):
    name = ctx.get("name")
    on = ctx.get("on", True)
    p = _find_person(conn, name) if name else None
    if not p:
        print(f"❌ 找不到 {name}. 先 jl --migrate / link 建档。")
        return
    db.set_watch(conn, p["id"], on)
    db.log_event(conn, kind="watch", person_id=p["id"], actor=_actor(),
                 detail={"on": on})
    print(f"{'⭐ 已关注' if on else '☆ 已取消关注'}: {p['name']}"
          + ("(进入主动联络队列)" if on else ""))


def cmd_proactive(conn, ctx):
    from . import assist, llm
    name = ctx.get("name")
    if name:
        p = _find_person(conn, name)
        if not p:
            print(f"❌ 找不到 {name}.")
            return
        if not llm.available():
            print("⚠️ 未配置 LLM——主动话术不可用,但红榜仍告诉你该联系谁。(LLM-optional)")
            return
        n = assist.generate_opener(conn, p["id"])
        if n > 0:
            print(f"📞 {p['name']}: 拟好 {n} 版开场白(去收件箱挑/改/发)")
        else:
            print(f"⚠️ {p['name']}: 缺可发渠道(微信/飞书),已应入救补——补好渠道再拟。")
        return
    # full sweep
    if not llm.available():
        print("⚠️ 未配置 LLM——只列该联系谁,不自动拟话术。(LLM-optional)")
    out = assist.proactive_sweep(conn) if llm.available() else {"drafted": [], "missing_channel": []}
    print(f"\n📞 主动联络队列 — {time.strftime('%Y-%m-%d %H:%M')}")
    if out["drafted"]:
        print("✨ 已拟开场白(去收件箱挑/改/发):")
        for d in out["drafted"]:
            p = db.get_person(conn, d["person_id"])
            print(f"  • {p['name']:<14} → 会话 {d['conversation_id']}")
    if out["missing_channel"]:
        print("\n⚠️ 想主动联系但缺渠道(入救补,补微信/飞书号再拟):")
        for pid in out["missing_channel"]:
            p = db.get_person(conn, pid)
            print(f"  • {p['name']}")
    if not out["drafted"] and not out["missing_channel"]:
        print("  (无 关注/🔴 待联络的人,或都已拟好)")
    db.log_event(conn, kind="proactive", actor=_actor(),
                 detail={"drafted": len(out["drafted"]), "missing": len(out["missing_channel"])})


# ----- account onboarding ---------------------------------------------------

def cmd_account(conn, params):
    """Productized per-account backend onboarding (jl account ls|add|set).

    add/set go through an explicit HITL gate: by default a DRY-RUN that prints
    exactly what will change; pass --commit (or --yes) to actually write. No
    silent fire-and-forget; the token contents are never printed."""
    from . import onboard
    sub = params.get("sub", "ls")
    if sub == "ls":
        return _account_ls(conn)
    if sub == "onboard":
        return cmd_account_onboard(conn, params)
    if sub not in ("add", "set"):
        print(f"❌ 未知子命令: account {sub} (支持: ls / add / set / onboard)")
        return
    if sub == "add" and not params["flags"].get("token_file"):
        print("用法: jl account add --platform wechat --tool fullwechat \\\n"
              "        --host http://HOST:6174 --self-id <wxid> --label \"<名>\" \\\n"
              "        --token-file <后端token副本路径> [--commit]")
        return
    try:
        plan = onboard.build_plan(conn, op=sub,
                                  account_id=params.get("account_id"),
                                  flags=params["flags"])
    except ValueError as e:
        print(f"❌ {e}")
        return
    _print_account_plan(plan)
    if not params.get("commit"):
        print("\n这是 dry-run。确认无误后加 --commit（或 --yes）真正写入。")
        return
    onboard.apply_plan(conn, plan)
    db.log_event(conn, kind="account_" + sub, actor=_actor(),
                 detail={"account_id": plan["account_id"],
                         "tool": plan["after"]["tool"],
                         "host": plan["after"]["host"],
                         "copy_token": plan["copy_token"]})
    print(f"\n✅ account #{plan['account_id']} 已{'登记' if sub == 'add' else '更新'}"
          f"（tool={plan['after']['tool']} host={plan['after']['host']}）。"
          f"\n   下一步: jl poll 拉新 → 在 Web 收件箱把该 self_id 确认进 SELF(我)。")


def _account_ls(conn):
    accts = db.get_accounts(conn)
    print(f"\n📇 账号 accounts — 共 {len(accts)} 个")
    print(f"{'id':<4} {'platform':<9} {'tool':<11} {'self_id':<22} {'host':<28} label")
    print("─" * 90)
    for a in accts:
        print(f"{a['account_id']:<4} {a['platform']:<9} {(a.get('tool') or '-'):<11} "
              f"{(a.get('self_id') or '-'):<22} {(a.get('host') or '-'):<28} {a.get('label') or ''}")
    if not accts:
        print("  (无账号 — jl account add 登记第一个后端)")


def _print_account_plan(plan):
    """Render the before→after summary that gates the write. Never prints token bytes."""
    a = plan["after"]
    verb = "新增" if plan["op"] == "add" else "更新"
    print(f"\n⚙️ account {verb}计划 — account_id #{plan['account_id']}")
    if plan["op"] == "set":
        b = plan["before"]
        print("  字段         before → after")
        for k in ("platform", "tool", "host", "self_id", "label", "cred_ref"):
            bv, av = b.get(k) or "-", a.get(k) or "-"
            mark = "  ←改" if bv != av else ""
            print(f"  {k:<11}  {bv} → {av}{mark}")
    else:
        for k in ("platform", "tool", "host", "self_id", "label", "cred_ref"):
            print(f"  {k:<11}  {a.get(k) or '-'}")
    if plan["copy_token"]:
        print(f"  token-file   {plan['token_file']}  →  {plan['cred_dest']}  (chmod 600)")
    else:
        print("  token-file   (未提供 — cred_ref 保持不变)")


def cmd_account_onboard(conn, params, *, fetch=None):
    """Config-driven onboarding: read the backend registry and onboard each enabled
    entry through the identity-preflight → dry-run → (--commit) apply gate.

    For EACH enabled backend:
      1. identity preflight (GET /api/status/auth) — base wxid MUST equal self_id,
         else SKIP + loud warning (防绑错号; mismatch/unreachable never aborts the run).
      2. capability probe (GET /api/capabilities) — one-line summary, optional.
      3. dry-run plan — same before→after table as `jl account set`.
      4. --commit → apply, read back the slot, log an account_onboard event.

    Default (no --commit) writes NOTHING. ``fetch`` is the HTTP seam (tests inject)."""
    from . import onboard
    reg_path = params.get("registry") or onboard.DEFAULT_REGISTRY
    fetch = fetch or onboard._get_json
    try:
        backends = onboard.load_registry(reg_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ 读注册表失败: {e}\n   模板见 ops/fullwechat-backends.example.json，"
              f"真实副本放 {onboard.DEFAULT_REGISTRY}（不入仓）。")
        return
    commit = bool(params.get("commit"))
    enabled = [b for b in backends if b.get("enabled")]
    print(f"\n🧩 配置驱动接入 — 注册表 {reg_path}")
    print(f"   {len(backends)} 条后端，其中 {len(enabled)} 条 enabled"
          + ("（--commit 真接）" if commit else "（dry-run，不写库）"))
    done = skipped = 0
    for entry in backends:
        label = entry.get("label", "?")
        slot = entry.get("amr_account_slot", "?")
        if not entry.get("enabled"):
            print(f"\n— {label} (slot {slot}) — 已停用 disabled，跳过。")
            continue
        print(f"\n━━ {label} (slot {slot}) @ {entry.get('host', '?')} ━━")
        res = onboard.onboard_entry(conn, entry, fetch=fetch)
        pre = res["preflight"]
        if not pre["ok"]:
            skipped += 1
            if pre["reason"] == "mismatch":
                print(f"⚠️ 身份不符: backend 报 {pre['logged_in']} (base {pre['base']}) "
                      f"≠ 配置 self_id {pre['self_id']} — 跳过, 防绑错号")
            elif pre["reason"] == "no_token":
                print(f"⚠️ 跳过: token_file 缺失或为空 ({entry.get('token_file')}) — 无法预检身份")
            elif pre["reason"] == "unreachable":
                print(f"⚠️ 跳过: 后端不可达 ({pre.get('error', 'unreachable')}) — 不接, 一个坏后端不影响其余")
            elif pre["reason"] == "no_user":
                print("⚠️ 跳过: /api/status/auth 未返回 loggedInUser — 响应异常, 不接")
            else:
                print(f"⚠️ 跳过: 预检失败 ({pre['reason']})")
            continue
        print(f"✅ 身份预检通过: loggedInUser={pre['logged_in']} (base {pre['base']}) == self_id {pre['self_id']}")
        caps = res["capabilities"]
        print(f"   能力: {caps['summary'] if caps['summary'] else '(/api/capabilities 不可用，按降级处理)'}")
        plan = res["plan"]
        _print_account_plan(plan)
        if not commit:
            print("   （dry-run：未写库。加 --commit 真正接入。）")
            done += 1
            continue
        onboard.apply_plan(conn, plan)
        row = {a["account_id"]: a for a in db.get_accounts(conn)}.get(plan["account_id"], {})
        db.log_event(conn, kind="account_onboard", actor=_actor(),
                     detail={"account_slot": plan["account_id"], "host": entry.get("host"),
                             "loggedInUser": pre["logged_in"], "confirmer": _actor()})
        print(f"   ✅ 已接入 → 回读 slot {plan['account_id']}: "
              f"tool={row.get('tool')} host={row.get('host')} self_id={row.get('self_id')}")
        done += 1
    verb = "已接入" if commit else "通过预检(dry-run)"
    print(f"\n汇总: {verb} {done} 条 | 跳过 {skipped} 条 | enabled {len(enabled)} 条")
    if not commit and done:
        print("确认上面计划无误后，加 --commit 真正写入。")


# ----- person actions -------------------------------------------------------

def cmd_person(conn, params):
    """Productized person actions (jl person refresh-name [<id-或-名>]).

    refresh-name syncs a person's stale display name to its primary/linked
    conversation's roster-fresh name (ingest keeps the conversation name fresh;
    persons.name does not auto-update). Goes through the same HITL gate as
    `jl account`: default DRY-RUN prints the before→after table and writes
    nothing; pass --commit (or --yes) to apply the UPDATE(s) + audit each."""
    from . import person as person_mod
    sub = params.get("sub", "refresh-name")
    if sub != "refresh-name":
        print(f"❌ 未知子命令: person {sub} (支持: refresh-name)")
        return
    target = params.get("target")
    pid = None
    if target is not None:
        p = _find_person(conn, target)
        if not p:
            names = ", ".join(x["name"] for x in db.get_persons(conn))
            print(f"❌ 找不到 {target}. 可选: {names}")
            return
        pid = p["id"]
    plan = person_mod.name_refresh_plan(conn, person_id=pid)
    if not plan:
        scope = f"{target}" if target is not None else "全员"
        print(f"\n✅ 名字刷新 — {scope}: 无需更新（display name 与最新会话名已一致）。")
        return
    print(f"\n🔤 名字刷新计划 — 共 {len(plan)} 人 (display name → 最新会话名):")
    print(f"  {'person_id':<16} {'当前名 (old)':<18} {'新名 (after)'}")
    print("  " + "─" * 56)
    for d in plan:
        print(f"  {d['person_id']:<16} {d['old']:<18} → {d['new']}")
    if not params.get("commit"):
        print("\n这是 dry-run，未写库。确认无误后加 --commit（或 --yes）真正更新。")
        return
    person_mod.apply_name_refresh(conn, plan)
    for d in plan:
        db.log_event(conn, kind="name_refresh", person_id=d["person_id"],
                     actor=_actor(), detail={"from": d["old"], "to": d["new"]})
    print(f"\n✅ 已更新 {len(plan)} 人的 display name（每条已写审计 events）。")


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
    elif command == "migrate_kinds":
        cmd_migrate_kinds(conn, params)
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
    elif command == "draft_assist":
        ctx.update(params); cmd_draft_assist(conn, ctx)
    elif command == "proactive":
        ctx.update(params); cmd_proactive(conn, ctx)
    elif command == "watch":
        ctx.update(params); cmd_watch(conn, ctx)
    elif command == "connect":
        ctx.update(params); cmd_connect(conn, ctx)
    elif command == "dedup_phone":
        cmd_dedup_phone(conn, ctx)
    elif command == "logs":
        ctx.update(params); cmd_logs(conn, ctx)
    elif command == "unlink":
        ctx.update(params); cmd_unlink(conn, ctx)
    elif command == "account":
        cmd_account(conn, params)
    elif command == "person":
        cmd_person(conn, params)
    else:
        _DISPATCH[command](conn, ctx)
    conn.close()


if __name__ == "__main__":
    main()
