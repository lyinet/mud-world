"""无头世界运行器：内容 -> 创世事件 -> tick 循环 -> 事件日志 + 审计摘要。

不读墙钟、不做隐式全局随机；所有顺序排序后遍历。
"""
from __future__ import annotations

import heapq
import json
import os
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..actions.registry import ActionRegistry, build_registry
from ..actions.common import treasury_balance, treasury_guard_threshold
from ..actions.social import PLAYER_SOURCES
from ..agent.budget import BudgetGuard, BudgetLimits
from ..agent.cost import CostTelemetry, load_cost_rates
from ..agent.dialogue_async import AsyncDialogueQueue
from ..agent.intent_model import load_intent_catalog
from ..agent.provider import (
    LLMReasoner,
    ProviderClient,
    ProviderConfig,
    ProviderConfigError,
    load_model_slots,
    load_provider_settings,
    make_replay_transport,
)
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
from ..core.models import Intent, Reject
from ..world.hash import event_hash, state_hash
from ..world.state import WorldState
from ..world.store import EventStore
from ..world.writer import Writer
from .genesis import build_genesis
from .intent import MockReasoner
from .lod import LODScheduler, load_lod_config


def intent_block_reason(registry: ActionRegistry, state: WorldState, intent: Intent) -> str:
    """提案前的前置校验：自主意图不满足前置条件时返回原因，玩家/系统不拦。

    玩家意图必须走正常 Reject 事件，才能让 API 返回可解释的拒绝；系统意图
    由各自 action 保证可用。这里只拦 LLM/planner/mock 等自主来源。
    """
    if intent.source in PLAYER_SOURCES or intent.verb.startswith("system_"):
        return ""
    action = registry.get(intent.verb)
    if action is None:
        return ""
    try:
        reasons = action.preconditions(state, intent)
    except Reject as exc:
        # 目标/委托人不存在等存在性错误由 helper 直接抛 Reject，
        # 在提案阶段同样是不可行，转为原因字符串，避免把整轮 sim 打崩。
        return str(exc)
    return str(reasons[0]) if reasons else ""


PRUNABLE_REASONS = frozenset({"not_together", "not_at_giver", "not_at_target", "wrong_location"})

# 同 tick 调度优先级：社交/交易先于移动，保证“已经决定要对某人说话”时目标还没走开。
SOCIAL_FIRST_VERBS = frozenset(
    {"talk", "message", "trade", "sell", "gift", "quest_accept", "quest_deliver", "recruit"}
)


def is_prunable(reason: str) -> bool:
    """位置类前置在提交前已判不可行：该提案不应进入 Writer，也不计 blocked。"""
    return reason in PRUNABLE_REASONS


@dataclass
class RunConfig:
    days: int = 10
    seed: int = 42
    npc_limit: int = 0
    out_root: Path = Path("runs")
    run_id: str | None = None
    with_players: int = 0
    dump_state: bool = False
    enable_llm: bool = False
    llm_budget: dict[str, Any] | None = None
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.0


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

    started_at = time.perf_counter()
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

    blocked: Counter[tuple[str, str]] = Counter()
    blocked_examples: list[dict[str, Any]] = []
    pruned: Counter[tuple[str, str]] = Counter()
    prune_examples: list[dict[str, Any]] = []

    def emit(intent: Intent):
        # 自主意图（LLM/planner）在提交前做前置校验：位置类不满足直接判不可行并丢弃
        # （计 precondition_pruned，不计 blocked）；其余仍记 blocked 并触发下一 tick 重规划。
        reason = intent_block_reason(registry, writer.state, intent)
        if reason:
            if is_prunable(reason):
                pruned[(intent.verb, reason)] += 1
                if len(prune_examples) < 20:
                    prune_examples.append(
                        {
                            "source": intent.source,
                            "actor": intent.actor,
                            "verb": intent.verb,
                            "reason": reason,
                            "tick": intent.tick,
                            "args": dict(intent.args or {}),
                        }
                    )
                return None
            blocked[(intent.verb, reason)] += 1
            if len(blocked_examples) < 20:
                blocked_examples.append(
                    {
                        "source": intent.source,
                        "actor": intent.actor,
                        "verb": intent.verb,
                        "reason": reason,
                        "tick": intent.tick,
                        "args": dict(intent.args or {}),
                    }
                )
            return None
        ev = writer.submit(intent)
        if ev.seq > events[-1].seq:
            events.append(ev)
            return ev
        return ev

    lod_cfg = load_lod_config(root / "config" / "lod.yaml")
    telemetry = CostTelemetry(rates=load_cost_rates(root))
    # LLM 模式下 cost.jsonl 只记真实模型调用；Mock 底稿是免费降级，不重复计费。
    mock_reasoner = MockReasoner(
        seed=cfg.seed, content=content, telemetry=None if cfg.enable_llm else telemetry
    )
    reasoner: Any = mock_reasoner
    budget: BudgetGuard | None = None
    llm_info: dict[str, Any] = {"enabled": False, "model": "mock", "reason": "mock_only"}
    dialogue_async = None
    run_concurrency = max(1, int(load_provider_settings(root).get("concurrency", 4)))
    if cfg.enable_llm:
        settings = load_provider_settings(root)
        slots = load_model_slots(root)

        def slot_client(slot: str, transport=None) -> ProviderClient:
            s = dict(slots.get(slot) or settings)
            return ProviderClient(
                ProviderConfig.from_env(
                    True,
                    timeout_s=float(s.get("timeout_s", 30)),
                    max_retries=int(s.get("max_retries", 2)),
                    backoff_base_s=float(s.get("backoff_base_s", 1.0)),
                    max_tokens=int(s.get("max_tokens", cfg.llm_max_tokens)),
                    temperature=float(s.get("temperature", cfg.llm_temperature)),
                    base_url=str(s.get("base_url", "")),
                    model=str(s.get("model", "")),
                    key_env=str(s.get("api_key_env", "LLM_API_KEY")),
                    json_mode=bool(s.get("json_mode", True)),
                    slot=slot,
                    model_env=str(s.get("model_env", "")),
                    extra_body=list(s.get("extra_body") or []),
                    backoff_429_s=float(s.get("backoff_429_s", 25.0)),
                ),
                transport=transport,
            )

        try:
            provider = slot_client("decision")
            provider_cfg = provider.config
        except ProviderConfigError as exc:
            llm_info = {"enabled": False, "model": "mock", "reason": str(exc)}
        else:
            rates = load_cost_rates(root)
            raw_budget = cfg.llm_budget or settings.get("budget") or {}
            budget = BudgetGuard(
                BudgetLimits.from_dict(raw_budget),
                input_rate=float(rates.per_1k_input_tokens) / 1000.0,
                output_rate=float(rates.per_1k_output_tokens) / 1000.0,
            )
            intent_client = None
            intent_error = ""
            replay_path = os.environ.get("WSIM_INTENT_REPLAY", "").strip()
            try:
                intent_client = slot_client(
                    "intent", transport=make_replay_transport(replay_path) if replay_path else None
                )
            except ProviderConfigError as exc:
                intent_error = str(exc)  # 无 key 时只降级意图槽，不影响决策/对话
            dialogue_client = None
            try:
                dialogue_client = slot_client("dialogue")
            except ProviderConfigError as exc:
                llm_info["dialogue_error"] = str(exc)
            catalog = load_intent_catalog(root, content.verbs) if intent_client else None
            reasoner = LLMReasoner(
                provider, mock_reasoner, content, telemetry,
                budget=budget, registry=registry,
                intent_client=intent_client, intent_catalog=catalog,
                concurrency=run_concurrency,
                record_path=(os.environ.get("WSIM_INTENT_RECORD", "").strip() or None),
            )
            llm_info = {
                "enabled": True,
                "model": provider_cfg.model,
                "fingerprint": provider_cfg.fingerprint(),
                "slots": {
                    "decision": provider_cfg.fingerprint(),
                    "dialogue": dialogue_client.config.fingerprint() if dialogue_client else "unavailable",
                    "intent": intent_client.config.fingerprint() if intent_client else "unavailable",
                },
                "intent_error": intent_error,
                "dialogue_error": llm_info.get("dialogue_error", ""),
                "extract_adapter": "fence/unwrap+zh-en-key-normalize+utterance-grounding+unknown_key",
            }
            dialogue_async = AsyncDialogueQueue(
                dialogue_client, budget=budget, log_dir=out_dir
            ) if dialogue_client is not None else None
            llm_info["dialogue_async"] = dialogue_async is not None
    scheduler = LODScheduler(
        sorted(npc_ids),
        lod_cfg,
        reasoner,
        seed=cfg.seed,
        start_tick=0,
        state_provider=lambda npc: {"id": npc},
        world_view_provider=lambda tick: {
            "tick": tick,
            "day": tick // TICKS_PER_DAY,
            "state": writer.state,
            "content": content,
        },
    )

    # 事件堆：只做「到期」推进，不做每 tick 全量 NPC 扫描。
    queue: list[tuple[int, int, int, str, Any]] = []
    seq = 0

    def push(tick: int, prio: int, kind: str, payload: Any) -> None:
        nonlocal seq
        heapq.heappush(queue, (max(0, int(tick)), prio, seq, kind, payload))
        seq += 1

    # 三种决策时机的落地统计：日界批处理 / 运行时实时 / 被拒后重决策。
    decision_stats: dict[str, dict[str, int]] = {
        name: {"decisions": 0, "actions": 0, "submitted": 0, "accepted": 0, "applied": 0}
        for name in ("night_batch", "realtime", "replan")
    }
    # 决策调用次数（不管是否产出动作）与落地统计分开计数。
    timing_calls: dict[str, int] = {name: 0 for name in decision_stats}
    intent_timing: dict[int, str] = {}

    def enqueue(intents: list[Intent], timing: str, at_tick: int) -> None:
        if not intents:
            return
        decision_stats[timing]["decisions"] += 1
        decision_stats[timing]["actions"] += len(intents)
        for it in intents:
            intent_timing[id(it)] = timing
            # 同一 tick 内先执行社交/交易，再执行移动：避免目标 NPC 先移动导致
            # 已通过决策期校验的 talk/trade 在提交时变成位置类不可行。
            prio = 1 if it.verb in SOCIAL_FIRST_VERBS else 2
            push(max(it.tick, at_tick), prio, "intent", it)

    def track_landing(payload: Any, ev: Any) -> None:
        timing = intent_timing.pop(id(payload), None)
        if timing is None:
            return
        decision_stats[timing]["submitted"] += 1
        if ev.outcome == "ok":
            decision_stats[timing]["accepted"] += 1
            if ev.effects:
                decision_stats[timing]["applied"] += 1

    player_loc = ""
    if cfg.with_players:
        player_loc = "market" if "market" in content.locations else sorted(content.locations)[0]
        enter = writer.submit(
            Intent(
                actor="player:visitor",
                verb="player_enter",
                args={"player_id": "player:visitor", "name": "访客", "at": player_loc},
                tick=0,
                source="player",
                reason="玩家进入世界",
            )
        )
        if enter.seq > events[-1].seq:
            events.append(enter)
        log.append(
            f"[{stamp(0)}] player_enter seq={enter.seq} outcome={enter.outcome} "
            f"presence={writer.state.entities.get('player:visitor').presence if 'player:visitor' in writer.state.entities else 'missing'}"
        )

    def run_night_batch(base: int, day: int) -> None:
        """日界批处理：为当日非 spotlight 的 NPC 生成一份计划，供白天推进。"""
        if not bool(llm_info.get("enabled")):
            return
        batch: list[dict[str, Any]] = []
        for npc in sorted(npc_ids):
            tier = scheduler.lod.get(npc, "active")
            if tier == "spotlight":
                continue
            ent = writer.state.entities.get(npc)
            if player_loc and ent is not None and ent.location == player_loc:
                continue  # 玩家身边的 NPC 交给实时决策
            view = {
                "tick": base,
                "day": day,
                "state": writer.state,
                "content": content,
                "tier": tier,
                "interval": lod_cfg.tiers[tier].wake_interval_ticks,
            }
            batch.append({"npc_state": {"id": npc}, "world_view": view, "timing": "night_batch"})
        if not batch:
            return
        timing_calls["night_batch"] += len(batch)
        if hasattr(reasoner, "decide_batch"):
            # 只预计算“今日意图”，不提前生成规则动作；动作在白天唤醒时落地。
            reasoner.decide_batch(batch, draft=False)
        # 夜间批处理不推进世界；events 全部来自白天及时机驱动。

    log.append(
        f"[{stamp(0)}] intent_layer=GoalSelector+Planner lod_initial="
        + ",".join(f"{npc}:{tier}" for npc, tier in sorted(scheduler.lod.items()))
    )

    replan_today: Counter = Counter()
    escort_queued: set[int] = set()
    quest_delivery_queued: set[str] = set()
    lod_changes: list[dict[str, Any]] = []
    # 金库保底告警：日界按 treasury:region 实际余额判定，进出各写一条事件。
    guard_threshold = treasury_guard_threshold(content)
    guard_active = False
    guard_days = 0
    guard_events: list[dict[str, Any]] = []

    def schedule_quest_deliver(actor: str, quest_id: str, tick: int) -> None:
        if not quest_id or quest_id in quest_delivery_queued:
            return
        quest_delivery_queued.add(quest_id)
        quest = writer.state.quests.get(quest_id)
        push(
            tick + 1,
            6,
            "intent",
            Intent(
                actor=actor,
                verb="quest_deliver",
                args={"quest": quest_id, "quest_verb": quest.verb if quest is not None else ""},
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
        if player_loc:
            nearby = {
                npc["id"]
                for npc in npcs
                if writer.state.entities.get(npc["id"]) is not None
                and writer.state.entities[npc["id"]].location == player_loc
            }
            for npc in sorted(nearby):
                scheduler.set_tier(npc, "spotlight", now=base, reason="player_nearby")
        run_night_batch(base, day)

        for tick in range(base, base + TICKS_PER_DAY):
            # LOD：只弹出到期唤醒，复杂度 O(到期事件数)
            if hasattr(reasoner, "decide_batch"):
                wakes = scheduler.advance_to_batched(tick, batch_size=run_concurrency)
            else:
                wakes = scheduler.advance_to(tick)
            for wake in wakes:
                timing_calls["realtime"] += 1
                enqueue(wake.intents, "realtime", tick)

            while queue and queue[0][0] <= tick:
                _, _prio, _seq, kind, payload = heapq.heappop(queue)
                if kind == "intent":
                    ev = emit(payload)
                    if ev is None:
                        # 前置条件不满足：没进事件日志，也没消耗世界动作；让 NPC 下一 tick 重新规划。
                        if payload.source in ("planner", "llm") and replan_today[payload.actor] < 6:
                            replan_today[payload.actor] += 1
                            push(tick + 1, 3, "replan", payload.actor)
                        continue
                    day_events.append(ev)
                    track_landing(payload, ev)
                    reasoner.observe(ev)
                    if ev.outcome == "rejected" and payload.source in ("planner", "llm"):
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
                    timing_calls["replan"] += 1
                    intents = reasoner.replan({"id": npc}, view, timing="replan")
                    enqueue(intents, "replan", tick)
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
        # 预算审计：当天真的因超限降级 Mock 才写，一天最多一条。
        if budget is not None and budget.take_audit(day):
            day_events.append(
                emit(
                    Intent(
                        actor="system",
                        verb="system_budget",
                        args={
                            "day": day,
                            "day_no": day + 1,
                            "mocked": int(budget.mocked_by_day.get(day, 0)),
                            "degraded": int(budget.degraded_by_day.get(day, 0)),
                            "dialogue_mocked": int(budget.dialogue_mocked_by_day.get(day, 0)),
                            "intent_mocked": int(budget.intent_mocked_by_day.get(day, 0)),
                            "day_tokens": int(budget.day_tokens.get(day, 0)),
                            "month_tokens": int(budget.month_tokens),
                            "month_cost": round(float(budget.month_cost), 6),
                            "limits": budget.limits.to_dict(),
                        },
                        tick=base + TICKS_PER_DAY - 1,
                        source="system",
                    )
                )
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

        # 日界金库保底告警：余额跌破阈值即进入 guard，回到阈值以上才退出。
        # 事件只记录状态（无 Effect），回放仍按原事件序列重建，守恒不受影响。
        treasury_now = treasury_balance(writer.state)
        guard_now = treasury_now < guard_threshold
        if guard_now != guard_active:
            guard_active = guard_now
            guard_events.append(
                {
                    "day": day + 1,
                    "tick": base + TICKS_PER_DAY - 1,
                    "active": guard_now,
                    "balance": treasury_now,
                    "threshold": guard_threshold,
                }
            )
            day_events.append(
                emit(
                    Intent(
                        actor="system",
                        verb="system_treasury_guard",
                        args={
                            "day": day,
                            "day_no": day + 1,
                            "active": guard_now,
                            "balance": treasury_now,
                            "threshold": guard_threshold,
                        },
                        tick=base + TICKS_PER_DAY - 1,
                        source="system",
                    )
                )
            )
        if guard_now:
            guard_days += 1

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
        snapshot["treasury"] = {
            "balance": treasury_now,
            "threshold": guard_threshold,
            "guard": guard_now,
        }
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
    k6_ill = illegal_stats(
        events, precondition_blocked=sum(blocked.values()), precondition_pruned=sum(pruned.values())
    )
    k6_rel_strong = relation_stats(state)
    k6_rel_all = relation_stats(state, kinds=ALL_KINDS)
    ledger = debt_ledger(state)
    debts_audit = debt_audit(events, state, content, npc_ids)
    econ = economy_stats(state, content, initial_npc_money, cfg.days, len(npcs))
    econ["cashflow"] = cashflow_stats(events, npc_ids, cfg.days, state)
    econ["operating_net_per_day"] = econ["cashflow"]["operating_net_per_npc_day"]
    econ["capital_out_per_day"] = econ["cashflow"]["capital_out_total"] / max(1, cfg.days * len(npcs))
    # 现金去向分项（B6）：NPC 现金净变化 = 经营净流入 − 资本开支；差额必须为 0。
    cf = econ["cashflow"]
    other_accounts = (
        econ["supply_now"]
        - econ["npc_money_now"]
        - econ["business_cash_now"]
    )
    econ["cash_delta_breakdown"] = {
        "npc_net_change": econ["npc_net_change"],
        "operating_net": cf["operating_net"],
        "capital_out": cf["capital_out_total"],
        "operating_minus_capital": cf["operating_net"] - cf["capital_out_total"],
        "reconciles_npc_cash": cf["operating_net"] - cf["capital_out_total"] == econ["npc_net_change"],
        "inflow_by_reason": cf["inflow_by_reason"],
        "outflow_by_reason": cf["outflow_by_reason"],
        "capital_out_by_reason": cf["capital_out_by_reason"],
        "business_cash_now": econ["business_cash_now"],
        "business_cash_change": econ["business_cash_now"] - econ["initial_location_float"],
        "other_accounts_cash": other_accounts,
        "burned_total": econ["burned_total"],
        "minted_total": econ["minted_total"],
    }
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
    goal_changes = list(mock_reasoner.selector.changes) if mock_reasoner.selector is not None else []
    intent_audit = {
        "decisions": len(mock_reasoner.decisions),
        "goal_changes": len(goal_changes),
        "preemptions": sum(1 for c in goal_changes if c.get("type") == "goal_preempt"),
        "replans": mock_reasoner.replan_count,
        "planned_overrides": sum(1 for c in goal_changes if c.get("type") == "goal_return"),
        "reason_coverage": reason_coverage,
        "reasoned_events": len(reason_events),
        "by_source": dict(sorted(source_counts.items())),
        "postponed_open_shop": mock_reasoner.postponed_open_shop,
        "logs": ["decisions.jsonl", "goal_changes.jsonl", "lod_tiers.jsonl", "cost.jsonl"],
    }
    cost_summary = telemetry.summary()
    llm_summary = dict(llm_info)
    llm_summary["schema_rejects"] = dict(sorted(telemetry.schema_rejects.items()))
    llm_summary["schema_reject_total"] = sum(telemetry.schema_rejects.values())
    llm_summary["fallbacks"] = telemetry.fallbacks
    if isinstance(reasoner, LLMReasoner):
        llm_summary["reasoner_calls"] = reasoner.calls
        llm_summary["reasoner_ok_calls"] = reasoner.ok_calls
        llm_summary["success_rate"] = (
            round(reasoner.ok_calls / reasoner.calls, 4) if reasoner.calls else 0.0
        )
        llm_summary["reasoner_fallbacks"] = reasoner.fallback_count
        llm_summary["calls_by_tier"] = dict(sorted(reasoner.calls_by_tier.items()))
        llm_summary["calls_by_timing"] = dict(sorted(reasoner.calls_by_timing.items()))
        llm_summary["mock_by_timing"] = dict(sorted(reasoner.mock_by_timing.items()))
        llm_summary["reasoner"] = reasoner.summary()
        llm_summary["by_slot"] = cost_summary.get("by_slot", {})
        latencies = sorted(
            float(r.get("latency_s", 0.0))
            for r in telemetry.records
            if int(r.get("calls", 0)) > 0 and float(r.get("latency_s", 0.0)) > 0
        )
        if latencies:
            def _pct(values: list[float], p: float) -> float:
                idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * p))))
                return round(values[idx], 3)

            llm_summary["latency"] = {
                "n": len(latencies),
                "min": round(latencies[0], 3),
                "p50": _pct(latencies, 0.5),
                "p95": _pct(latencies, 0.95),
                "max": round(latencies[-1], 3),
                "mean": round(sum(latencies) / len(latencies), 3),
            }
            by_slot_lat: dict[str, list[float]] = {}
            for rec in telemetry.records:
                if int(rec.get("calls", 0)) > 0 and float(rec.get("latency_s", 0.0)) > 0:
                    by_slot_lat.setdefault(str(rec.get("slot") or "decision"), []).append(
                        float(rec["latency_s"])
                    )
            llm_summary["latency_by_slot"] = {
                slot: {
                    "n": len(vals),
                    "p50": _pct(sorted(vals), 0.5),
                    "p95": _pct(sorted(vals), 0.95),
                }
                for slot, vals in sorted(by_slot_lat.items())
            }
        usage_src: dict[str, int] = {}
        for rec in telemetry.records:
            key = str(rec.get("usage_source") or "reported")
            usage_src[key] = usage_src.get(key, 0) + int(rec.get("calls", 0))
        llm_summary["usage_source"] = dict(sorted(usage_src.items()))
        llm_summary["dialogue_async"] = dialogue_async.stats() if dialogue_async is not None else None
    budget_summary = budget.summary() if budget is not None else {"enabled": False}
    decisions_summary: dict[str, dict[str, Any]] = {}
    for name, row in decision_stats.items():
        calls = timing_calls[name]
        decisions_summary[name] = {
            # invocations = 该时机的决策入口被触发次数；model_calls = 真正发出 HTTP 的次数。
            "invocations": calls,
            "model_calls": int(llm_summary.get("calls_by_timing", {}).get(name, 0)),
            "decisions": row["decisions"],
            "actions": row["actions"],
            "avg_actions": round(row["actions"] / row["decisions"], 4) if row["decisions"] else 0.0,
            "submitted": row["submitted"],
            "accepted": row["accepted"],
            "applied": row["applied"],
            "accept_rate": round(row["accepted"] / row["submitted"], 4) if row["submitted"] else 0.0,
            "apply_rate": round(row["applied"] / row["submitted"], 4) if row["submitted"] else 0.0,
        }

    # 任务 / 镖单 / 门派成立统计（复核 B5/B7 需要直接给出数字）
    quest_status: dict[str, int] = {}
    quest_by_template: dict[str, dict[str, int]] = {}
    for q in state.quests.values():
        quest_status[q.status] = quest_status.get(q.status, 0) + 1
        row = quest_by_template.setdefault(
            q.template_id, {"total": 0, "completed": 0, "failed": 0}
        )
        row["total"] += 1
        if q.status == "completed":
            row["completed"] += 1
        if q.status == "failed":
            row["failed"] += 1
    escort_status: dict[str, int] = {}
    for o in state.escorts.values():
        escort_status[o.status] = escort_status.get(o.status, 0) + 1
    founded = [
        {
            "sect": str((e.args or {}).get("sect_id", "")),
            "name": str((e.args or {}).get("name", "")),
            "founder": e.actor,
            "tick": e.tick,
            "day": day_of(e.tick),
        }
        for e in events
        if e.verb == "found_sect" and e.outcome == "ok"
    ]
    health_by_template: dict[str, dict[str, Any]] = {}
    for tid, stats in sorted(state.quest_stats.items()):
        created = int(stats.get("created", 0))
        health_by_template[tid] = {
            "created": created,
            "completed": int(stats.get("completed", 0)),
            "failed": int(stats.get("failed", 0)),
            "expired": int(stats.get("expired", 0)),
            "completion_rate": round(int(stats.get("completed", 0)) / created, 4) if created else 0.0,
        }
    quest_summary = {
        "status": dict(sorted(quest_status.items())),
        "completed": quest_status.get("completed", 0),
        "failed": quest_status.get("failed", 0),
        "expired": quest_status.get("expired", 0),
        "accepted": quest_status.get("accepted", 0),
        "open": quest_status.get("open", 0),
        "by_template": dict(sorted(quest_by_template.items())),
        "post_events": sum(1 for e in events if e.verb == "system_quest_post" and e.outcome == "ok"),
        "health": {
            "active": quest_status.get("open", 0) + quest_status.get("accepted", 0),
            "expired_on_board": sum(1 for q in state.quests.values() if q.status == "expired"),
            "expired_total": state.quests_expired_total,
            "by_template": health_by_template,
        },
    }
    escort_summary = {
        "status": dict(sorted(escort_status.items())),
        "done": escort_status.get("done", 0),
        "created_events": sum(1 for e in events if e.verb == "escort" and e.outcome == "ok"),
        "arrived_events": sum(1 for e in events if e.verb == "system_escort_arrive" and e.outcome == "ok"),
    }
    work_cfg = content.economy.get("work") or {}
    tax_to_treasury = 0
    tax_burned = 0
    for e in events:
        for eff in e.effects:
            if not isinstance(eff, dict):
                continue
            data = eff.get("data") or {}
            if eff.get("op") == "transfer" and data.get("reason") == "transaction_tax_treasury":
                tax_to_treasury += int(data.get("amount", 0))
            elif eff.get("op") == "burn" and data.get("reason") == "transaction_tax":
                tax_burned += int(data.get("amount", 0))
    treasury_summary = {
        "account": "treasury:region",
        "reserve": int(work_cfg.get("employer_reserve", 2000)),
        "multiplier": float(work_cfg.get("treasury_guard_multiplier", 1.2)),
        "threshold": guard_threshold,
        "balance_final": state.account_balance("treasury:region"),
        "tax_to_treasury": tax_to_treasury,
        "tax_burned": tax_burned,
        "open_shop_postponed": mock_reasoner.postponed_open_shop,
        "guard": {
            "enters": sum(1 for g in guard_events if g["active"]),
            "exits": sum(1 for g in guard_events if not g["active"]),
            "days_active": guard_days,
            "days_total": cfg.days,
            "active_at_end": guard_active,
            "entered_day": next((g["day"] for g in guard_events if g["active"]), None),
            "suspended_actions": ["open_shop", "propensity_buy", "system_shop_dividend"],
            "transitions": guard_events,
        },
    }
    sect_summary = {"founded": founded, "founded_count": len(founded)}

    final_hash = state_hash(state)
    ev_hash = event_hash(events)
    verdict = {
        "k6_conservation_pass": k6_cons["conservation_error"] == 0
        and not k6_cons["negative_accounts"]
        and debts_audit["status"] == "pass",
        # 结论取严格口径：提案前拦截也计入违规尝试。
        "k6_illegal_pass": k6_ill["illegal_attempt_rate"] < 0.02,
        "k6_illegal_executed_pass": k6_ill["illegal_rate"] < 0.02,
        # 诊断：把“提交前判不可行而剪枝”的提案也当作违规尝试，仍须 ≤2%。
        "k6_illegal_incl_pruned_pass": k6_ill["illegal_proposal_rate"] < 0.02,
        "k6_relations_pass": k6_rel_strong["conflict_rate"] < 0.01,
        "k1_pass": k1["k1_rate"] >= 0.60,
        "k3_status": "diagnostic_only",
        "k3_note": (
            "K3 双层诊断（verb 层 + 对象层），P2 起意图驱动后仅作对照，不设 pass/fail；"
            "同一 run 同时输出 v1.0.0 兼容口径与 v1.1.0 主口径，见 docs/metrics.md v1.1.0。"
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
            # 已执行动作口径（历史字段，保持兼容）。
            "illegal_rate": k6_ill["illegal_rate"],
            # 违规尝试口径（严格）：(reject + 提案前拦截) / 事件数。
            "illegal_attempt_rate": k6_ill["illegal_attempt_rate"],
            "illegal_proposal_rate": k6_ill["illegal_proposal_rate"],
            "precondition_blocked": k6_ill["precondition_blocked"],
            "precondition_pruned": k6_ill["precondition_pruned"],
            "by_reason": k6_ill["by_reason"],
            "by_verb": k6_ill["by_verb"],
        },
        "precondition_blocked": {
            "total": sum(blocked.values()),
            "by_verb": {
                verb: dict(sorted(Counter({reason: n for (v, reason), n in blocked.items() if v == verb}).items()))
                for verb in sorted({v for v, _ in blocked})
            },
            "examples": blocked_examples,
        },
        "precondition_pruned": {
            "total": sum(pruned.values()),
            "by_verb": {
                verb: dict(sorted(Counter({reason: n for (v, reason), n in pruned.items() if v == verb}).items()))
                for verb in sorted({v for v, _ in pruned})
            },
            "examples": prune_examples,
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
        "quests": quest_summary,
        "escorts": escort_summary,
        "treasury": treasury_summary,
        "sects": sect_summary,
        "cost": cost_summary,
        "llm": llm_summary,
        "decisions": decisions_summary,
        "budget": budget_summary,
        "verdict": verdict,
        "wall_time_s": round(time.perf_counter() - started_at, 3),
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
        "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True) for d in mock_reasoner.decisions) + "\n",
        encoding="utf-8",
    )
    (out_dir / "cost.jsonl").write_text(telemetry.to_jsonl(), encoding="utf-8")
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
