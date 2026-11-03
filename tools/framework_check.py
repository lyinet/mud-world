#!/usr/bin/env python3
"""P3-A 框架验收：六项串行检查，全部通过才输出 PASS。

阻断项要求（见 reports/verify-p2-2026-11-03.md）：
1. 门禁能抓住“世界停摆”：拒绝率上限、每日最少动作、burned>0、
   unknown_verb 出现即 FAIL，且 check5 从 events.sqlite 独立重算。
2. MCP 权限由服务端身份映射决定，调用方不能自报。
3. 插件隔离覆盖完整 state（tick / 地点图 / 关系表）。
4. LOD 外部计数：decide 调用数 == 唤醒数，且 != NPC 总数；headless 不再提交 system_plan。
5. 守恒为 0，债务审计 9 项全 true，且出现真实还款正样本。
6. 假端点端到端：进程内启动 127.0.0.1 假 LLM → 推理 → Intent 落地 → 关闭；
   校验 source=llm 的事件、三种决策时机的落地计数、守恒与 replay。

运行：python3 tools/framework_check.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from wsim.actions.registry import build_registry  # noqa: E402
from wsim.audit.debt_audit import debt_audit  # noqa: E402
from wsim.core.canonical import hash_obj  # noqa: E402
from wsim.core.content import load_content  # noqa: E402
from wsim.core.content_manifest import validate_content_dir  # noqa: E402
from wsim.core.models import Effect, Event, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.mcp import AuditLog, GatewayError, MCPGateway  # noqa: E402
from wsim.plugins.loader import PluginError, load_plugins  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.sim.lod import LODScheduler, MockReasoner, load_lod_config  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

OUT = ROOT / "runs" / "framework"
RESULTS: list[dict[str, Any]] = []
CTX: dict[str, Any] = {}

MAX_REJECT_RATE = 0.02
MIN_ACTIONS_PER_NPC_DAY = 1


def _print(idx: int, name: str, ok: bool, detail: str) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{idx}/6] {name} ... {status}")
    print(f"       {detail}")


def _check(idx: int, name: str, fn: Callable[[], str]) -> None:
    try:
        detail = fn()
        RESULTS.append({"check": idx, "name": name, "ok": True, "detail": detail})
        _print(idx, name, True, detail)
    except Exception as exc:
        RESULTS.append({"check": idx, "name": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        _print(idx, name, False, f"{type(exc).__name__}: {exc}")


def _read_events(run_dir: Path) -> list[Event]:
    """直接从 events.sqlite 读，作为所有门禁的独立事实源。"""
    store = EventStore(run_dir / "events.sqlite")
    try:
        return store.read_all()
    finally:
        store.close()


def _is_ok(e: Event) -> bool:
    return e.outcome == "ok"


def _burned_from_events(events: list[Event]) -> int:
    total = 0
    for e in events:
        if not _is_ok(e):
            continue
        for eff in e.effects:
            if eff.get("op") == "burn":
                total += int((eff.get("data") or {}).get("amount", 0))
    return total


def check_content_extension() -> str:
    validate_content_dir(ROOT / "content")
    content = load_content(ROOT, allow_fixture=False, use_content=True)
    ids = {n["id"] for n in content.npcs}
    assert "ming_chabo" in ids, "additions NPC 未加载"
    assert "teahouse" in content.locations, "additions 地点未加载"
    assert "tea" in content.items, "additions 物品未加载"
    assert "tea_guild" in content.sects, "additions 门派未加载"
    assert "borrow_money" in content.verbs, "additions 动词元数据未加载"

    summary = run_headless(
        ROOT,
        RunConfig(days=10, seed=42, npc_limit=0, out_root=OUT, run_id="content10d", dump_state=True),
        content=content,
    )
    run_dir = OUT / "content10d"
    events = _read_events(run_dir)
    CTX.update(
        {"summary": summary, "events": events, "run_dir": run_dir, "content": content,
         "npc_ids": {n["id"] for n in content.npcs}}
    )

    issues = summary["content_audit"]["issues"]
    assert not issues, f"content_audit 有未解释问题: {issues}"
    state = json.loads((run_dir / "state_final.json").read_text(encoding="utf-8"))
    assert "ming_chabo" in state["entities"], "新 NPC 未进入世界状态"
    assert "sect:tea_guild" in state["factions"], "新门派未实例化"

    # ---- 停摆门禁：全部从 events.sqlite 独立计算，不读 summary 自报字段 ----
    total = len(events)
    rejected = sum(1 for e in events if e.outcome == "rejected")
    reject_rate = rejected / total if total else 1.0
    assert total > 0, "事件日志为空：世界停摆"
    assert reject_rate <= MAX_REJECT_RATE, (
        f"拒绝率 {reject_rate:.2%} 超过上限 {MAX_REJECT_RATE:.0%}（rejected={rejected}/{total}）"
    )

    unknown = [e for e in events if "unknown_verb" in str(e.reason)]
    assert not unknown, f"出现 unknown_verb 事件 {len(unknown)} 条，例：seq={unknown[0].seq} verb={unknown[0].verb}"
    plan_events = [e for e in events if e.verb == "system_plan"]
    assert not plan_events, f"仍在提交 system_plan（{len(plan_events)} 条），LOD 未真正接管主循环"

    accepted = [e for e in events if _is_ok(e) and e.source != "system" and not e.verb.startswith("system_")]
    daily = Counter(e.day for e in accepted)
    worst_day, worst_count = min(daily.items(), key=lambda kv: kv[1]) if daily else (0, 0)
    npc_count = len(CTX["npc_ids"])
    assert worst_count >= npc_count * MIN_ACTIONS_PER_NPC_DAY, (
        f"第 {worst_day} 天仅 {worst_count} 个动作，低于 {npc_count}（每日至少 1 动作/NPC）：世界停摆"
    )
    active_npcs = {e.actor for e in accepted}
    assert active_npcs >= CTX["npc_ids"], (
        f"有 {len(CTX['npc_ids'] - active_npcs)} 个 NPC 全程无动作："
        f"{sorted(CTX['npc_ids'] - active_npcs)[:5]}"
    )

    burned = _burned_from_events(events)
    assert burned > 0, "burned=0：没有消费/损耗发生，经济未真正运转"

    k1 = summary["k1"]
    assert k1["k1_scope"] == "all_content_goals", k1.get("k1_scope")
    assert k1["goals_total"] == 91, f"K1 分母应为 91（内容包全量），实际 {k1['goals_total']}"
    assert abs(k1["k1_rate"] - k1["goals_progressed"] / 91) < 1e-9, "K1 全量口径算术不一致"
    assert "k1_rate_excluding_one_shot" in k1, "缺少剔除一次性目标后的 K1"

    return (
        f"NPC={len(content.npcs)} 地点={len(content.locations)} 物品={len(content.items)} "
        f"动词={len(content.verbs)} 门派={len(content.sects)}；10 天 events={total} "
        f"rejected={rejected}（{reject_rate:.2%}）最少每日动作={worst_count}（第 {worst_day} 天，"
        f"{npc_count} NPC）burned={burned} system_plan=0；"
        f"K1 全量={k1['goals_progressed']}/{k1['goals_total']}={k1['k1_rate']:.2%} "
        f"剔除一次性={k1['k1_rate_excluding_one_shot']:.2%}"
    )


def _build_world(npcs_limit: int = 3) -> tuple[Any, Writer, EventStore, Any, Path]:
    content = load_content(ROOT, allow_fixture=False, use_content=True)
    npcs = sorted(content.npcs, key=lambda n: n["id"])[:npcs_limit]
    state = WorldState(locations=content.locations)
    tmp = Path(tempfile.mkdtemp(prefix="p2-plugin-"))
    store = EventStore(tmp / "events.sqlite")
    registry = build_registry(content, 42)
    writer = Writer(state, store, registry, 42)
    genesis = build_genesis(content, npcs, root=ROOT)
    writer.bootstrap(genesis.effects, tick=0, verb="genesis")
    return content, writer, store, registry, tmp


def _write_plugin(root: Path, name: str, manifest: str, code: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    (d / "action.py").write_text(textwrap.dedent(code), encoding="utf-8")
    return d


def check_plugin_extension() -> str:
    content, writer, store, registry, tmp = _build_world()
    evil_tmp = Path(tempfile.mkdtemp(prefix="p2-evil-"))
    try:
        action = registry.get("borrow_money")
        assert action is not None, "plugins/actions/borrow_money 未注册"
        assert "plugins" in str(action.manifest.path), "borrow_money 不是来自插件目录"
        balances = sorted(
            ((writer.state.account_balance(f"wallet:{eid}"), eid) for eid in writer.state.entities),
            reverse=True,
        )
        lender = next((eid for bal, eid in balances if bal >= 10), None)
        assert lender, "没有余额足够的出借人"
        borrower = next(eid for _, eid in balances if eid != lender)

        intent = Intent(actor=borrower, verb="borrow_money", args={"target": lender, "amount": 10},
                        tick=1, source="framework_check")
        before = writer.state.full_hash()
        effects = action.apply(writer.state, intent, PRNG(42, 1, "action:borrow_money"))
        assert writer.state.full_hash() == before, "插件 apply 修改了 state"
        assert [e.op for e in effects] == ["transfer", "debt_create", "memory_add"]

        event = writer.submit(intent)
        assert event.outcome == "ok", f"插件事务被拒: {event.reason}"
        assert any(k.startswith("borrow") or k.startswith("debt:borrow") for k in writer.state.debts)

        # 阻断项 3：完整 state 隔离，恶意插件改 tick 必须被拒绝
        _write_plugin(
            evil_tmp,
            "evil_tick",
            """
            id: evil_tick
            verb: evil_tick
            version: "1.0.0"
            entry: action.py
            args: {}
            preconditions: []
            effects: [memory_add]
            """,
            """
            from wsim.core.models import Effect

            def apply(state, args):
                state.tick = 999
                return [Effect("memory_add", {"npc": "n1", "tick": 0, "text": "x", "importance": 0.1})]
            """,
        )
        evil = load_plugins(evil_tmp)[0]
        raised = False
        try:
            evil.apply(writer.state, Intent(actor=borrower, verb="evil_tick", args={}, tick=1),
                       PRNG(42, 1, "action:evil_tick"))
        except PluginError as exc:
            raised = "plugin_mutated_state" in str(exc)
        assert raised, "恶意插件修改 tick 未被隔离层拒绝"

        return (
            f"verb=borrow_money 插件注册成功；apply 前后完整 state 哈希不变；"
            f"Writer 提交 seq={event.seq}，债务数={len(writer.state.debts)}；"
            f"evil_tick（tick=999）被 plugin_mutated_state 拒绝"
        )
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(evil_tmp, ignore_errors=True)


def check_mcp_sandbox() -> str:
    content, writer, store, registry, tmp = _build_world()
    audit_path = OUT / "mcp_audit.jsonl"
    if audit_path.exists():
        audit_path.unlink()
    identities = {
        "reader": "read",
        "planner": "propose_intent",
        "mallory": "read",          # 低权用户，自称 admin_apply 必须被拒
        "alice": "admin_apply",
        "bob": "admin_apply",
        "carol": "admin_apply",
    }
    try:
        gw = MCPGateway(
            lambda: writer.state, writer=writer, audit=AuditLog(audit_path), identities=identities
        )
        eid = sorted(writer.state.entities)[0]

        # read 只读快照
        snap = gw.call("reader", "world.read_snapshot", {})["snapshot"]
        snap["entities"][eid]["stamina"] = 0
        assert writer.state.entities[eid].stamina != 0

        # 低权用户自称 admin：权限来自服务端映射，必须被拒
        denied = False
        try:
            gw.call("mallory", "admin.request", {
                "intent": {"actor": eid, "verb": "move", "args": {"to": "b"}, "tick": 1},
                "permission": "admin_apply",   # 自报字段应被忽略
            })
        except GatewayError as exc:
            denied = "permission_denied" in str(exc)
        assert denied, "低权用户自报 admin_apply 未被拒绝"

        # 未注册身份直接拒绝
        denied = False
        try:
            gw.call("eve", "world.read_snapshot", {})
        except GatewayError as exc:
            denied = "unregistered_identity" in str(exc)
        assert denied, "未注册身份未被拒绝"

        # read 尝试 propose 被拒
        denied = False
        try:
            gw.call("reader", "world.propose_intent", {
                "actor": eid, "verb": "move", "args": {"to": "b"}, "tick": 1,
            })
        except GatewayError as exc:
            denied = "permission_denied" in str(exc)
        assert denied, "越权 propose 未被拒绝"

        # propose 只能入 outbox，不得改状态
        before_propose = writer.state.hash()
        gw.call("planner", "world.propose_intent", {
            "actor": eid, "verb": "move", "args": {"to": "b"}, "tick": 1,
        })
        assert len(gw.outbox) == 1 and writer.state.hash() == before_propose

        loc = writer.state.entities[eid].location
        exits = sorted(content.locations[loc]["exits"])
        assert exits, "测试地点无出口"

        token = gw.call("alice", "admin.request", {
            "intent": {"actor": eid, "verb": "move", "args": {"to": exits[0]}, "tick": 1},
            "reason": "framework_check",
        })["token"]

        # 申请人不能批准
        try:
            gw.call("alice", "admin.approve", {"token": token})
            raise AssertionError("同一人批准未被拒绝")
        except GatewayError as exc:
            assert "second_person_required" in str(exc)

        gw.call("bob", "admin.approve", {"token": token})

        # 申请人不能 apply
        try:
            gw.call("alice", "admin.apply", {"token": token})
            raise AssertionError("申请人 apply 未被拒绝")
        except GatewayError as exc:
            assert "second_person_required" in str(exc)

        # 非批准人不能 apply
        try:
            gw.call("carol", "admin.apply", {"token": token})
            raise AssertionError("非批准人 apply 未被拒绝")
        except GatewayError as exc:
            assert "apply_by_approver_only" in str(exc)

        applied = gw.call("bob", "admin.apply", {"token": token})
        assert applied["outcome"] == "ok", applied
        assert writer.state.entities[eid].location == exits[0]

        recs = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        assert any(r["ok"] is False and "permission_denied" in r["error"] for r in recs)
        assert all(r.get("identity_source") == "server_map" for r in recs), "审计未记录身份来源"
        assert all(r["permission"] == identities.get(r["caller"], "unregistered") for r in recs), (
            "审计权限与服务端身份映射不一致"
        )
        return (
            f"身份映射生效：mallory(read) 自报 admin 被拒、未注册 eve 被拒；"
            f"propose 仅入 outbox；admin 需第二人批准且只能由批准人 apply（申请人/第三人被拒）；"
            f"audit={len(recs)} 条，identity_source=server_map"
        )
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


def check_lod_event_driven() -> str:
    content = load_content(ROOT, allow_fixture=False, use_content=True)
    config = load_lod_config(ROOT / "config" / "lod.yaml")
    ids = [n["id"] for n in content.npcs]
    home = {n["id"]: n.get("home") or "market" for n in content.npcs}
    exits = {lid: list(loc["exits"]) for lid, loc in content.locations.items()}
    scheduler = LODScheduler(
        ids,
        config,
        MockReasoner(seed=42),
        seed=42,
        state_provider=lambda npc: {
            "id": npc,
            "home": home[npc],
            "exits": exits.get(home[npc], []),
            "stamina": 80.0,
        },
        world_view_provider=lambda tick: {"tick": tick},
    )
    scheduler.advance_to(1000)  # 预热到稳定期
    before = {
        "process": scheduler.process_count,
        "decide": scheduler.decide_calls,
        "provider": scheduler.state_provider_calls,
        "tiers": dict(scheduler.decide_by_tier),
    }
    wakes = scheduler.advance_to(1060)  # 60 tick 窗口
    delta = scheduler.process_count - before["process"]

    # 外部计数：唤醒结果由调用方拿到，调度器计数器必须与之一致
    assert delta == len(wakes), f"process_count 增量 {delta} != 外部唤醒数 {len(wakes)}"
    assert scheduler.decide_calls - before["decide"] == len(wakes), "decide 调用数与唤醒数不一致"
    assert scheduler.state_provider_calls - before["provider"] == len(wakes), "state_provider 调用数与唤醒数不一致"
    tier_delta = sum(scheduler.decide_by_tier[k] - before["tiers"].get(k, 0) for k in scheduler.decide_by_tier)
    assert tier_delta == len(wakes), f"分档唤醒合计 {tier_delta} != {len(wakes)}"

    woken = {w.npc_id for w in wakes}
    assert len(wakes) < len(ids), "60 tick 内唤醒数等于 NPC 总数，疑似全量遍历"
    assert len(woken) != len(ids), "60 tick 内每个 NPC 都被唤醒，疑似全量扫描"
    assert any(w.lod != "spotlight" for w in wakes), "所有唤醒都是最高档，LOD 未分档"

    summary = scheduler.summary()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "lod.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 接进 headless：实际 10 天 run 中 system_plan 必须为 0（由 check1 写入 CTX）
    events = CTX.get("events") or []
    plans = [e for e in events if e.verb == "system_plan"]
    assert not plans, f"headless 仍在提交 system_plan（{len(plans)} 条）"
    run_lod = (CTX.get("summary") or {}).get("lod") or {}
    assert run_lod.get("processed_wakes", 0) > 0, "headless 未使用 LOD 调度"
    assert run_lod.get("processed_wakes") == run_lod.get("decide_calls"), "headless 唤醒数与 decide 调用数不一致"

    return (
        f"NPC={len(ids)}，稳定期 60 tick 只唤醒 {delta} 个/去重 {len(woken)} 个；"
        f"decide={scheduler.decide_calls} state_provider={scheduler.state_provider_calls}；"
        f"四档分布={summary['by_tier']}；headless 10 天 wakes={run_lod.get('processed_wakes')} system_plan=0"
    )


def check_conservation() -> str:
    events: list[Event] = CTX.get("events") or []
    run_dir: Path = CTX.get("run_dir")
    content = CTX.get("content")
    assert events and run_dir and content, "缺少 10 天 run 结果"
    state_final = json.loads((run_dir / "state_final.json").read_text(encoding="utf-8"))

    # ---- 从 events.sqlite 独立重放，不读 summary 自报字段 ----
    replay = WorldState(locations=content.locations)
    for e in events:
        if e.outcome != "ok":
            continue
        apply_effects(replay, [Effect.from_dict(x) for x in e.effects])
        replay.tick = e.tick

    accounts_sum = sum(int(v) for v in replay.accounts.values())
    minted = int(replay.minted_total)
    burned = int(replay.burned_total)
    error = accounts_sum - (minted - burned)
    negative = sorted(acc for acc, bal in replay.accounts.items() if int(bal) < 0)
    assert error == 0, f"独立重放守恒误差 {error}（accounts_sum={accounts_sum} minted={minted} burned={burned}）"
    assert not negative, f"独立重放出现负账户: {negative[:5]}"
    assert burned > 0, "独立重放 burned=0：无消费/损耗"
    assert accounts_sum == sum(int(v) for v in state_final["accounts"].values()), (
        "事件日志重放账户合计与 state_final.json 不一致"
    )
    assert replay.hash() == hash_obj(state_final), "事件日志重放状态哈希与 state_final.json 不一致"

    audit = debt_audit(events, replay, content, CTX.get("npc_ids"))
    checks = audit["checks"]
    assert len(checks) == 9, f"债务审计应为 9 项，实际 {len(checks)}"
    failed = [k for k, v in checks.items() if not v["ok"]]
    assert not failed, f"债务审计未通过: {failed}"
    assert audit["status"] == "pass", audit["status"]
    assert audit["settled_events"] > 0, "没有真实还款正样本（settled_events=0）"

    ledger = debt_ledger_counts(state_final)
    return (
        f"独立重放：accounts_sum={accounts_sum} minted={minted} burned={burned} "
        f"conservation_error=0；债务审计 9/9 true，settled_events={audit['settled_events']}，"
        f"active_debts={ledger}；状态哈希与 state_final 一致"
    )


def debt_ledger_counts(state_final: dict[str, Any]) -> int:
    return sum(1 for d in state_final.get("debts", {}).values() if d.get("status") == "active")


def check_fake_llm_endpoint() -> str:
    """假端点端到端：进程内 127.0.0.1 → 推理 → Intent 落地 → replay。"""
    from fake_llm_server import FakeLLMServer

    env_keys = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
    saved = {k: os.environ.get(k) for k in env_keys}
    run_id = "fake_llm_2d"
    run_dir = OUT / run_id
    srv = FakeLLMServer(port=0, seed=7)
    try:
        srv.start()
        os.environ["LLM_BASE_URL"] = srv.base_url
        os.environ["LLM_API_KEY"] = "fake-ci-key"
        os.environ["LLM_MODEL"] = "fake/model"
        summary = run_headless(
            ROOT,
            RunConfig(
                days=2, seed=42, npc_limit=0, out_root=OUT, run_id=run_id,
                with_players=1, dump_state=True, enable_llm=True,
                llm_budget={
                    "daily_tokens": 2_000_000, "monthly_tokens": 20_000_000,
                    "daily_cost": 100.0, "monthly_cost": 1000.0,
                },
            ),
        )
    finally:
        srv.stop()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    events = _read_events(run_dir)
    llm_ok = [e for e in events if e.source == "llm" and e.outcome == "ok"]
    llm_applied = [e for e in llm_ok if e.effects]
    assert llm_applied, "假端点推理没有落到世界（无 source=llm 且带 effects 的 ok 事件）"
    assert summary["llm"]["enabled"] is True
    assert str(summary["llm"]["fingerprint"]["base_url"]).startswith("http://127.0.0.1:"), (
        "假端点必须全程 127.0.0.1"
    )
    assert summary["llm"]["reasoner_calls"] > 0, "reasoner 未真正调用模型"
    assert summary["llm"]["schema_reject_total"] == 0, summary["llm"]["schema_rejects"]
    assert summary["llm"]["fallbacks"] == 0, summary["llm"]["fallbacks"]

    dec = summary["decisions"]
    # P3-B：夜间批处理只缓存意图，落地发生在白天实时唤醒。
    assert dec["night_batch"]["invocations"] > 0 and dec["night_batch"]["model_calls"] > 0, dec["night_batch"]
    assert dec["realtime"]["invocations"] > 0 and dec["realtime"]["applied"] > 0, dec["realtime"]

    total = len(events)
    rejected = sum(1 for e in events if e.outcome == "rejected")
    reject_rate = rejected / total if total else 1.0
    blocked = int(summary.get("precondition_blocked", {}).get("total", 0))
    pruned = int(summary.get("precondition_pruned", {}).get("total", 0))
    attempt_rate = (rejected + blocked) / total if total else 1.0
    proposal_rate = (rejected + blocked + pruned) / total if total else 1.0
    assert reject_rate <= MAX_REJECT_RATE, f"假端点 run 拒绝率 {reject_rate:.2%} 超上限"
    assert attempt_rate <= MAX_REJECT_RATE, (
        f"假端点 run 严格非法率 {attempt_rate:.2%}（reject={rejected} + blocked={blocked} / events={total}）超上限"
    )
    assert proposal_rate <= MAX_REJECT_RATE, (
        f"假端点 run 含剪枝提案率 {proposal_rate:.2%}"
        f"（reject={rejected} + blocked={blocked} + pruned={pruned} / events={total}）超上限"
    )
    assert summary["verdict"]["k6_illegal_pass"] is True, "K6 严格口径 verdict 未通过"
    assert summary["verdict"]["k6_illegal_incl_pruned_pass"] is True, "含剪枝诊断口径未通过"

    content = CTX.get("content") or load_content(ROOT, allow_fixture=False, use_content=True)
    replay = WorldState(locations=content.locations)
    for e in events:
        if e.outcome != "ok":
            continue
        apply_effects(replay, [Effect.from_dict(x) for x in e.effects])
        replay.tick = e.tick
    accounts = sum(int(v) for v in replay.accounts.values())
    minted = int(replay.minted_total)
    burned = int(replay.burned_total)
    assert accounts - (minted - burned) == 0, "假端点 run replay 守恒不为 0"
    stored = (run_dir / "world_hash.txt").read_text(encoding="utf-8").strip()
    assert replay.hash() == stored, "假端点 run replay 哈希不一致"

    return (
        f"127.0.0.1:{srv.port} 启动→2 天→关闭；source=llm 落地 {len(llm_applied)}/{len(llm_ok)} 条，"
        f"night_batch={dec['night_batch']['model_calls']} "
        f"realtime={dec['realtime']['model_calls']} replan={dec['replan']['model_calls']}"
        f"（入口触发 {dec['night_batch']['invocations']}/{dec['realtime']['invocations']}/"
        f"{dec['replan']['invocations']}）；calls={summary['llm']['reasoner_calls']} "
        f"schema_reject=0 fallback=0 拒绝率={reject_rate:.2%} "
        f"严格非法率=({rejected}+{blocked})/{total}={attempt_rate:.2%} "
        f"含剪枝=({rejected}+{blocked}+{pruned})/{total}={proposal_rate:.2%} 守恒=0 replay=MATCH"
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    _check(1, "新增内容不改码 + 停摆门禁", check_content_extension)
    _check(2, "新增插件不改码 + 完整 state 隔离", check_plugin_extension)
    _check(3, "MCP 身份映射与越权拒绝", check_mcp_sandbox)
    _check(4, "LOD 事件驱动与外部计数", check_lod_event_driven)
    _check(5, "守恒 / 债务独立重算", check_conservation)
    _check(6, "假端点端到端（127.0.0.1）", check_fake_llm_endpoint)

    (OUT / "check.json").write_text(
        json.dumps({"results": RESULTS, "pass": all(r["ok"] for r in RESULTS)},
                   ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    ok = all(r["ok"] for r in RESULTS)
    print()
    print("FRAMEWORK CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
