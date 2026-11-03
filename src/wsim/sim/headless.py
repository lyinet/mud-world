"""无头世界运行器：内容 -> 创世事件 -> tick 循环 -> 事件日志 + 审计摘要。

不读墙钟、不做隐式全局随机；所有顺序排序后遍历。
"""
from __future__ import annotations

import heapq
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..actions.registry import build_registry
from ..audit.conservation import conservation, debt_ledger
from ..audit.debt_audit import debt_audit
from ..audit.diversity import diversity_stats
from ..audit.goals import goal_stats
from ..audit.illegal import illegal_stats
from ..audit.relations import ALL_KINDS, relation_stats
from ..audit.report import cashflow_stats, content_audit, economy_stats
from ..audit.wages import wage_trace
from ..core.canonical import dumps, hash_obj
from ..core.clock import TICKS_PER_DAY, day_of, stamp
from ..core.content import Content, load_content
from ..core.models import Intent
from ..world.hash import event_hash, state_hash
from ..world.state import WorldState
from ..world.store import EventStore
from ..world.writer import Writer
from .genesis import build_genesis
from .intent import MockReasoner
from .lod import LODScheduler, load_lod_config


@dataclass
class RunConfig:
    days: int = 10
    seed: int = 42
    npc_limit: int = 0
    out_root: Path = Path("runs")
    run_id: str | None = None
    with_players: int = 0
    dump_state: bool = False


def _npc_money(state: WorldState) -> int:
    return sum(state.account_balance(f"wallet:{eid}") for eid in sorted(state.entities))


def _daily_snapshot(
    state: WorldState,
    day_events: list,
    day: int,
    prev_npc_money: int,
    npc_count: int,
) -> dict[str, Any]:
    illegal = illegal_stats(day_events)
    cons = conservation(state)
    npc_money = _npc_money(state)
    verbs: dict[str, int] = {}
    for e in day_events:
        if e.outcome == "ok" and e.source != "system" and not e.verb.startswith("system_"):
            verbs[e.verb] = verbs.get(e.verb, 0) + 1
    return {
        "day": day + 1,
        "tick": (day + 1) * TICKS_PER_DAY,
        "events": len(day_events),
        "rejected": illegal["rejected"],
        "illegal_rate": illegal["illegal_rate"],
        "npc_money": npc_money,
        "npc_net_today": npc_money - prev_npc_money,
        "npc_net_per_day": (npc_money - prev_npc_money) / max(1, npc_count),
        "accounts_sum": cons["accounts_sum"],
        "minted_total": cons["minted_total"],
        "burned_total": cons["burned_total"],
        "conservation_error": cons["conservation_error"],
        "verbs": dict(sorted(verbs.items())),
        "state_hash": state_hash(state),
    }


def run_headless(
    root: Path | None = None,
    cfg: RunConfig | None = None,
    content: Content | None = None,
) -> dict[str, Any]:
    cfg = cfg or RunConfig()
    root = root or Path(__file__).resolve().parents[3]
    content = content or load_content(root, min_npcs=0, allow_fixture=False, use_content=True)
    npcs = content.npcs[: cfg.npc_limit] if cfg.npc_limit else content.npcs
    if not npcs:
        raise RuntimeError("没有 NPC 可运行")

    run_id = cfg.run_id or f"seed{cfg.seed}-{cfg.days}d-{len(npcs)}npc"
    out_dir = Path(cfg.out_root) / run_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    state = WorldState(locations=content.locations)
    store = EventStore(out_dir / "events.sqlite")
    registry = build_registry(content, cfg.seed)
    writer = Writer(state, store, registry, cfg.seed)

    genesis = build_genesis(content, npcs, root=root)
    genesis_event = writer.bootstrap(genesis.effects, tick=0, verb="genesis")
    events = [genesis_event]
    state = writer.state
    npc_ids = {n["id"] for n in npcs}
    initial_npc_money = genesis.initial_npc_money
    prev_npc_money = _npc_money(state)
    hash_10d = None
    hash_final = None

    log: list[str] = []
    daily: list[dict[str, Any]] = []
    log.append(
        f"[{stamp(0)}] genesis npcs={len(npcs)} locations={len(content.locations)} "
        f"items={len(content.items)} content_verbs={len(content.verbs)} "
        f"npc_money={genesis.initial_npc_money} float={genesis.initial_location_float} "
        f"debts={genesis.debts} long_term={genesis.long_term_debts} content={content.content_hash[:12]}"
    )
    if genesis.mismatches:
        log.append(f"[{stamp(0)}] genesis_mismatches={genesis.mismatches}")
    log.append(
        f"[{stamp(0)}] registry_npc_verbs={sorted(v for v in registry.verbs() if not v.startswith('system_'))}"
    )

    def emit(intent: Intent):
        ev = writer.submit(intent)
        if ev.seq > events[-1].seq:
            events.append(ev)
            return ev
        return ev

    lod_cfg = load_lod_config(root / "config" / "lod.yaml")
    reasoner = MockReasoner(seed=cfg.seed, content=content)
    scheduler = LODScheduler(
        sorted(npc_ids),
        lod_cfg,
        reasoner,
        seed=cfg.seed,
        start_tick=0,
        state_provider=lambda npc: {"id": npc},
        world_view_provider=lambda tick: {"tick": tick, "state": writer.state, "content": content},
    )

    # 事件堆：只做「到期」推进，不做每 tick 全量 NPC 扫描。
    queue: list[tuple[int, int, int, str, Any]] = []
    seq = 0

    def push(tick: int, prio: int, kind: str, payload: Any) -> None:
        nonlocal seq
        heapq.heappush(queue, (max(0, int(tick)), prio, seq, kind, payload))
        seq += 1

    log.append(
        f"[{stamp(0)}] intent_layer=GoalSelector+Planner lod_initial="
        + ",".join(f"{npc}:{tier}" for npc, tier in sorted(scheduler.lod.items()))
    )

    replan_today: Counter = Counter()
    escort_queued: set[int] = set()
    quest_delivery_queued: set[str] = set()
    lod_changes: list[dict[str, Any]] = []

    def schedule_quest_deliver(actor: str, quest_id: str, tick: int) -> None:
        if not quest_id or quest_id in quest_delivery_queued:
            return
        quest_delivery_queued.add(quest_id)
        push(
            tick + 1,
            6,
            "intent",
            Intent(
                actor=actor,
                verb="quest_deliver",
                args={"quest": quest_id},
                tick=tick + 1,
                source="planner",
                reason="任务办妥，回去交付",
            ),
        )

    for day in range(cfg.days):
        base = day * TICKS_PER_DAY
        day_events: list = []
        replan_today = Counter()
        # 每日维护：日界一次性 O(N) 事件（生活成本），不是每 tick 扫描。
        day_events.append(emit(Intent(actor="system", verb="system_recover", args={}, tick=base, source="system")))
        day_events.append(
            emit(Intent(actor="system", verb="system_quest_post", args={"day": day}, tick=base, source="system"))
        )
        for npc in sorted(npcs, key=lambda n: n["id"]):
            day_events.append(
                emit(Intent(actor=npc["id"], verb="system_living", args={"day": day}, tick=base, source="system"))
            )

        for tick in range(base, base + TICKS_PER_DAY):
            # LOD：只弹出到期唤醒，复杂度 O(到期事件数)
            for wake in scheduler.advance_to(tick):
                for it in wake.intents:
                    push(max(it.tick, tick), 2, "intent", it)

            while queue and queue[0][0] <= tick:
                _, _prio, _seq, kind, payload = heapq.heappop(queue)
                if kind == "intent":
                    ev = emit(payload)
                    day_events.append(ev)
                    if ev.outcome == "rejected" and payload.source == "planner":
                        reasoner.observe(ev)
                        if replan_today[payload.actor] < 6:
                            replan_today[payload.actor] += 1
                            push(tick + 1, 3, "replan", payload.actor)
                    if ev.outcome == "ok":
                        quest_id = str((payload.args or {}).get("quest") or "")
                        if quest_id and payload.verb != "quest_deliver":
                            if payload.verb == "escort":
                                pass  # 到达后再安排交付（见 escort_start 分支）
                        for eff in ev.effects:
                            if eff.get("op") == "escort_start":
                                arrive = int((eff.get("data") or {}).get("arrive_tick", tick))
                                if arrive not in escort_queued:
                                    escort_queued.add(arrive)
                                    push(arrive, 4, "escort", None)
                                if quest_id and payload.verb == "escort":
                                    schedule_quest_deliver(payload.actor, quest_id, arrive)
                elif kind == "replan":
                    npc = str(payload)
                    tier = scheduler.lod.get(npc, "active")
                    view = {
                        "tick": tick,
                        "state": writer.state,
                        "content": content,
                        "tier": tier,
                        "interval": lod_cfg.tiers[tier].wake_interval_ticks,
                    }
                    for it in reasoner.replan({"id": npc}, view):
                        push(max(it.tick, tick), 2, "intent", it)
                elif kind == "escort":
                    day_events.append(
                        emit(Intent(actor="system", verb="system_escort_arrive", args={}, tick=tick, source="system"))
                    )

        day_events.append(
            emit(Intent(actor="system", verb="system_debts", args={}, tick=base + TICKS_PER_DAY - 1, source="system"))
        )
        day_events.append(
            emit(Intent(actor="system", verb="system_quest_fail", args={}, tick=base + TICKS_PER_DAY - 1,
                        source="system"))
        )
        if (day + 1) in (30, 60, 90):
            for verb in ("system_savings_tax", "system_shop_dividend", "system_shop_tax"):
                day_events.append(
                    emit(
                        Intent(
                            actor="system",
                            verb=verb,
                            args={"day": day},
                            tick=base + TICKS_PER_DAY - 1,
                            source="system",
                        )
                    )
                )

        # 日界：动态档位（关系活跃升温 / 长期无交互降档 / 玩家靠近）
        heat = Counter()
        for ev in day_events:
            if (
                ev.outcome == "ok"
                and ev.source != "system"
                and ev.verb in ("trade", "apprentice", "marry", "join_sect", "found_sect", "escort")
            ):
                heat[ev.actor] += 1
        idle_days: dict[str, int] = {}
        for npc in sorted(npc_ids):
            ent = writer.state.entities.get(npc)
            last = ent.last_action_tick if ent is not None else 0
            idle_days[npc] = max(0, ((day + 1) * TICKS_PER_DAY - last) // TICKS_PER_DAY)
        changes = scheduler.update_tiers(
            now=(day + 1) * TICKS_PER_DAY,
            heat=heat,
            idle_days=idle_days,
            nearby=set(),
        )
        for c in changes:
            lod_changes.append({**c, "day": day + 1})

        state = writer.state
        snapshot = _daily_snapshot(state, day_events, day, prev_npc_money, len(npcs))
        snapshot["last_seq"] = events[-1].seq
        daily.append(snapshot)
        prev_npc_money = snapshot["npc_money"]
        log.append(
            f"[{stamp((day + 1) * TICKS_PER_DAY)}] day {day + 1:02d} hash={snapshot['state_hash'][:12]} "
            f"events={snapshot['events']} rejected={snapshot['rejected']} cons_err={snapshot['conservation_error']} "
            f"npc_money={snapshot['npc_money']} net={snapshot['npc_net_today']} "
            f"lod=" + ",".join(f"{k}:{v}" for k, v in scheduler.summary()["by_tier"].items())
        )
        if day == 9:
            hash_10d = snapshot["state_hash"]
        if day == cfg.days - 1:
            hash_final = snapshot["state_hash"]

    state = writer.state
    events = store.read_all()
    horizon = cfg.days * TICKS_PER_DAY

    k1 = goal_stats(state, horizon, content)
    k3 = diversity_stats(events, npc_ids, seed=cfg.seed)
    k6_cons = conservation(state)
    k6_ill = illegal_stats(events)
    k6_rel_strong = relation_stats(state)
    k6_rel_all = relation_stats(state, kinds=ALL_KINDS)
    ledger = debt_ledger(state)
    debts_audit = debt_audit(events, state, content, npc_ids)
    econ = economy_stats(state, content, initial_npc_money, cfg.days, len(npcs))
    econ["cashflow"] = cashflow_stats(events, npc_ids, cfg.days, state)
    econ["operating_net_per_day"] = econ["cashflow"]["operating_net_per_npc_day"]
    econ["capital_out_per_day"] = econ["cashflow"]["capital_out_total"] / max(1, cfg.days * len(npcs))
    wages = wage_trace(events, state, content, npc_ids)
    audit = content_audit(content, registry, genesis.mismatches)

    reason_events = [
        e for e in events if e.outcome == "ok" and e.source in ("planner", "npc", "mock_reasoner")
    ]
    reason_coverage = (
        sum(1 for e in reason_events if str(e.reason or "").strip()) / len(reason_events)
        if reason_events
        else 1.0
    )
    source_counts = Counter(e.source for e in events)
    lod_summary = scheduler.summary()
    lod_summary["changes"] = lod_changes
    goal_changes = list(reasoner.selector.changes) if reasoner.selector is not None else []
    intent_audit = {
        "decisions": len(reasoner.decisions),
        "goal_changes": len(goal_changes),
        "preemptions": sum(1 for c in goal_changes if c.get("type") == "goal_preempt"),
        "replans": reasoner.replan_count,
        "planned_overrides": sum(1 for c in goal_changes if c.get("type") == "goal_return"),
        "reason_coverage": reason_coverage,
        "reasoned_events": len(reason_events),
        "by_source": dict(sorted(source_counts.items())),
        "logs": ["decisions.jsonl", "goal_changes.jsonl", "lod_tiers.jsonl"],
    }

    final_hash = state_hash(state)
    ev_hash = event_hash(events)
    verdict = {
        "k6_conservation_pass": k6_cons["conservation_error"] == 0
        and not k6_cons["negative_accounts"]
        and debts_audit["status"] == "pass",
        "k6_illegal_pass": k6_ill["illegal_rate"] < 0.01,
        "k6_relations_pass": k6_rel_strong["conflict_rate"] < 0.01,
        "k1_pass": k1["k1_rate"] >= 0.60,
        "k3_status": "diagnostic_only",
        "k3_note": (
            "K3 双层诊断（verb 层 + 对象层），P2 起意图驱动后仅作对照，不设 pass/fail；"
            "口径与阈值未变，见 docs/metrics.md v1.0.0。"
        ),
        "intent_reason_pass": reason_coverage >= 0.99,
        "wage_chain_pass": all(wages["reconciliation"].values()),
    }

    summary = {
        "run_id": run_id,
        "seed": cfg.seed,
        "days": cfg.days,
        "npc_count": len(npcs),
        "content_hash": content.content_hash,
        "content_sources": dict(content.sources),
        "hashes": {
            "state_day10": hash_10d,
            "state_final": final_hash,
            "event_final": ev_hash,
        },
        "events": {
            "total": len(events),
            "rejected": k6_ill["rejected"],
            "illegal_rate": k6_ill["illegal_rate"],
            "by_reason": k6_ill["by_reason"],
            "by_verb": k6_ill["by_verb"],
        },
        "k6": {
            "conservation": k6_cons,
            "illegal": k6_ill,
            "relations_strong_ties": k6_rel_strong,
            "relations_all_kinds": k6_rel_all,
            "debt_ledger": ledger,
            "debt_audit": debts_audit,
        },
        "k1": k1,
        "k3": k3,
        "lod": lod_summary,
        "intent": intent_audit,
        "economy": econ,
        "wages": wages,
        "content_audit": audit,
        "verdict": verdict,
    }

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "daily.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True) for d in daily) + "\n",
        encoding="utf-8",
    )
    (out_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e.to_dict(), ensure_ascii=False, sort_keys=True) for e in events) + "\n",
        encoding="utf-8",
    )
    (out_dir / "wage_trace.json").write_text(
        json.dumps(wages, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "k3_details.json").write_text(
        json.dumps(k3, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "decisions.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True) for d in reasoner.decisions) + "\n",
        encoding="utf-8",
    )
    (out_dir / "goal_changes.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in goal_changes) + "\n",
        encoding="utf-8",
    )
    (out_dir / "lod_tiers.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in lod_changes) + "\n",
        encoding="utf-8",
    )
    (out_dir / "lod.json").write_text(
        json.dumps(lod_summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "run.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    (out_dir / "world_hash.txt").write_text(final_hash + "\n", encoding="utf-8")
    if cfg.dump_state:
        (out_dir / "state_final.json").write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
    (out_dir / "event_hash.txt").write_text(ev_hash + "\n", encoding="utf-8")
    store.set_meta("world_hash", final_hash)
    store.set_meta("event_hash", ev_hash)
    store.set_meta("content_hash", content.content_hash)
    store.close()

    return summary
