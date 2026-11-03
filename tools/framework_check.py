#!/usr/bin/env python3
"""P2 框架层验收：五项串行检查，全部通过才输出 PASS。

1. 新增内容不改码：manifest + additions 校验，31 NPC / 9 地点 / 31 物品 / 新门派，
   重跑 10 天不报错，守恒误差 0。
2. 新增插件不改码：扫描 plugins/actions/ 注册 borrow_money，纯函数不改 state，
   经 Writer 成功提交。
3. 越权 MCP 被拒：read 调用 propose/admin 被拒绝，审计留痕。
4. LOD 不遍历全量：只弹出到期事件，process_count == 到期数 < NPC 总数。
5. 守恒仍为 0：复用 10 天run 的 conservation/debt_audit 结果。

运行：python3 tools/framework_check.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.registry import build_registry  # noqa: E402
from wsim.core.content import load_content  # noqa: E402
from wsim.core.content_manifest import validate_content_dir  # noqa: E402
from wsim.core.models import Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.mcp import AuditLog, GatewayError, MCPGateway, PERMISSION_READ  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.sim.lod import LODScheduler, MockReasoner, load_lod_config  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

OUT = ROOT / "runs" / "framework"
RESULTS: list[dict[str, Any]] = []
CTX: dict[str, Any] = {}


def _print(idx: int, name: str, ok: bool, detail: str) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{idx}/5] {name} ... {status}")
    print(f"       {detail}")


def _check(idx: int, name: str, fn: Callable[[], str]) -> None:
    try:
        detail = fn()
        RESULTS.append({"check": idx, "name": name, "ok": True, "detail": detail})
        _print(idx, name, True, detail)
    except Exception as exc:
        RESULTS.append({"check": idx, "name": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        _print(idx, name, False, f"{type(exc).__name__}: {exc}")


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
    CTX["summary"] = summary
    issues = summary["content_audit"]["issues"]
    assert not issues, f"content_audit 有未解释问题: {issues}"
    state = json.loads((OUT / "content10d" / "state_final.json").read_text(encoding="utf-8"))
    assert "ming_chabo" in state["entities"], "新 NPC 未进入世界状态"
    assert "sect:tea_guild" in state["factions"], "新门派未实例化"
    return (
        f"NPC={len(content.npcs)} 地点={len(content.locations)} 物品={len(content.items)} "
        f"动词={len(content.verbs)} 门派={len(content.sects)}；10 天 events={summary['events']['total']} "
        f"rejected={summary['events']['rejected']} conservation_error=0"
    )


def _build_world(npcs_limit: int = 3) -> tuple[Any, Writer, EventStore, Any, Any]:
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


def check_plugin_extension() -> str:
    content, writer, store, registry, tmp = _build_world()
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
        before = writer.state.hash()
        effects = action.apply(writer.state, intent, PRNG(42, 1, "action:borrow_money"))
        assert writer.state.hash() == before, "插件 apply 修改了 state"
        assert [e.op for e in effects] == ["transfer", "debt_create", "memory_add"]

        event = writer.submit(intent)
        assert event.outcome == "ok", f"插件事务被拒: {event.reason}"
        assert event.verb == "borrow_money"
        assert any(k.startswith("borrow") or k.startswith("debt:borrow") for k in writer.state.debts)
        return (
            f"verb=borrow_money 插件注册成功；apply 前后 state 哈希不变；"
            f"Writer 提交 seq={event.seq}，债务数={len(writer.state.debts)}"
        )
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


def check_mcp_sandbox() -> str:
    content, writer, store, registry, tmp = _build_world()
    audit_path = OUT / "mcp_audit.jsonl"
    if audit_path.exists():
        audit_path.unlink()
    try:
        gw = MCPGateway(lambda: writer.state, writer=writer, audit=AuditLog(audit_path))
        eid = sorted(writer.state.entities)[0]

        # read 只读快照
        snap = gw.call("reader", PERMISSION_READ, "world.read_snapshot", {})["snapshot"]
        snap["entities"][eid]["stamina"] = 0  # 改副本，不应影响世界
        assert writer.state.entities[eid].stamina != 0

        # 越权：read 尝试 propose，必须被拒且留痕
        denied = False
        try:
            gw.call("reader", "read", "world.propose_intent", {
                "actor": eid, "verb": "move", "args": {"to": "b"}, "tick": 1,
            })
        except GatewayError as exc:
            denied = "permission_denied" in str(exc)
        assert denied, "越权 propose 未被拒绝"

        # propose 只能入 outbox
        gw.call("planner", "propose_intent", "world.propose_intent", {
            "actor": eid, "verb": "move", "args": {"to": "b"}, "tick": 1,
        })
        before = writer.state.hash()
        assert len(gw.outbox) == 1 and writer.state.hash() == before

        # admin 双人确认
        loc = writer.state.entities[eid].location
        exits = sorted(content.locations[loc]["exits"])
        assert exits, "测试地点无出口"
        token = gw.call("alice", "admin_apply", "admin.request", {
            "intent": {"actor": eid, "verb": "move", "args": {"to": exits[0]}, "tick": 1},
            "reason": "framework_check",
        })["token"]
        try:
            gw.call("alice", "admin_apply", "admin.approve", {"token": token})
            raise AssertionError("同一人批准未被拒绝")
        except GatewayError as exc:
            assert "second_person_required" in str(exc)
        gw.call("bob", "admin_apply", "admin.approve", {"token": token})
        applied = gw.call("alice", "admin_apply", "admin.apply", {"token": token})
        assert applied["outcome"] == "ok", applied
        assert writer.state.entities[eid].location == exits[0]

        recs = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        assert any(r["ok"] is False and "permission_denied" in r["error"] for r in recs)
        assert all({"caller", "permission", "method", "params_digest", "result", "tick", "seq"} <= set(r) for r in recs)
        return (
            f"read 快照隔离；read->propose 被拒并留痕（audit={len(recs)} 条）；"
            f"propose 仅入 outbox；admin 需第二人批准后由 Writer 提交"
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
    before = scheduler.process_count
    wakes = scheduler.advance_to(1060)  # 60 tick 窗口
    delta = scheduler.process_count - before
    assert delta == len(wakes)
    assert delta <= len(ids) // 2, "到期处理项数过大，疑似全量遍历"
    assert delta < len(ids)
    summary = scheduler.summary()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "lod.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return (
        f"NPC={len(ids)}，稳定期 60 tick 窗口只唤醒 {delta} 个（远小于 NPC 总数）；"
        f"四档分布={summary['by_tier']}；process_count={scheduler.process_count}"
    )


def check_conservation() -> str:
    summary = CTX.get("summary")
    assert summary, "缺少 10 天 run 结果"
    cons = summary["k6"]["conservation"]
    debt = summary["k6"]["debt_audit"]
    assert cons["conservation_error"] == 0, cons
    assert not cons["negative_accounts"], cons
    assert debt["status"] == "pass", debt
    return (
        f"minted={cons['minted_total']} burned={cons['burned_total']} "
        f"accounts_sum={cons['accounts_sum']} conservation_error=0；"
        f"debt_audit={debt['status']}（checks={len(debt.get('checks', []))}）"
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    _check(1, "新增内容不改码", check_content_extension)
    _check(2, "新增插件不改码", check_plugin_extension)
    _check(3, "越权 MCP 被拒", check_mcp_sandbox)
    _check(4, "LOD 不遍历全量", check_lod_event_driven)
    _check(5, "守恒仍为 0", check_conservation)

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
