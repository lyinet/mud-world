"""W2 世界自走调度器：不依赖任何 HTTP 请求，按虚拟时钟推进世界。

设计边界：
- 单进程内循环 + 最小堆，不引入线程池/多进程/消息队列。
- 默认纯规则（MockReasoner），LLM 调用数为 0；spotlight 档可插模型，
  但默认关闭，且一次运行内有硬上限，超限自动降级回规则。
- 事件仍是唯一事实源：本模块只负责「何时唤醒谁」，所有写入仍走 Writer。
- 虚拟时钟只用于游戏时间，真实时钟只用于配速、CPU 护栏与观测；
  墙钟不进世界状态、不进事件、不进哈希。
- 玩家进出只追加事件，不重置、不回滚；`ambient_progress.jsonl` 逐 tick
  记录 hash 链，用于证明推进连续。
"""
from __future__ import annotations

import heapq
import json
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..actions.registry import build_registry
from ..core.canonical import hash_obj
from ..core.clock import TICKS_PER_DAY, TICKS_PER_HOUR, day_of, stamp
from ..core.content import Content, load_content
from ..core.models import Event, Intent
from ..world.hash import event_hash, state_hash
from ..world.state import WorldState
from ..world.store import EventStore
from ..world.writer import Writer
from .genesis import build_genesis
from .headless import intent_block_reason, is_prunable
from .intent import MockReasoner
from .lod import LODScheduler, load_lod_config

# 1x 的虚拟时间速率：30 分钟真实时间 ≈ 300 ticks（10 ticks/分钟）。
TICKS_PER_SECOND_1X = 10.0 / 60.0
SPEEDS = (1.0, 2.0, 4.0)


def _nearest_speed(value: float) -> float:
    return min(SPEEDS, key=lambda s: abs(s - float(value)))


@dataclass
class AmbientConfig:
    out_dir: Path
    run_id: str = ""
    seed: int = 42
    npc_limit: int = 0
    speed: float = 1.0
    # 真实时间每 1 秒推进多少 tick（1x 基准）。
    ticks_per_second_1x: float = TICKS_PER_SECOND_1X
    # CPU 护栏：单进程 CPU/墙钟 比例上限，默认 0.85（不吃满一核）。
    max_cpu_ratio: float = 0.85
    # 成本模型：默认规则驱动，零 LLM。
    llm_enabled: bool = False
    llm_max_calls: int = 0
    # 每次摘要间隔：默认 1 游戏小时 = 6 ticks。
    summary_every_ticks: int = TICKS_PER_HOUR
    control_file: Path | None = None
    # 固定上限（None 表示不限制，由外部 stop）。
    max_ticks: int | None = None
    # 起始 tick（重启续跑时使用；默认 0）。
    start_tick: int = 0

    def normalized_speed(self) -> float:
        return _nearest_speed(self.speed)


@dataclass
class AmbientCostModel:
    """一次运行的硬成本台账；rule 模式恒为 0 次调用。"""

    llm_enabled: bool = False
    max_calls: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    degraded_to_rules: int = 0
    spotlight_wakes: int = 0
    last_denied_reason: str = ""

    def allow(self) -> tuple[bool, str]:
        if not self.llm_enabled:
            return False, "llm_disabled"
        if self.max_calls <= 0:
            return False, "hard_cap_zero"
        if self.calls >= self.max_calls:
            return False, "hard_cap_reached"
        return True, ""

    def record(self, cost_usd: float = 0.0) -> None:
        self.calls += 1
        self.cost_usd = round(float(self.cost_usd) + float(cost_usd), 6)

    def deny(self, reason: str) -> None:
        self.degraded_to_rules += 1
        self.last_denied_reason = reason

    def summary(self) -> dict[str, Any]:
        return {
            "llm_enabled": bool(self.llm_enabled),
            "llm_max_calls": int(self.max_calls),
            "llm_calls": int(self.calls),
            "cost_usd": round(float(self.cost_usd), 6),
            "degraded_to_rules": int(self.degraded_to_rules),
            "spotlight_wakes": int(self.spotlight_wakes),
            "last_denied_reason": self.last_denied_reason,
        }


class AmbientReasoner:
    """按 LOD 档位路由：spotlight 可走模型（受硬上限），其余一律规则。

    模型异常不吞：直接抛给调度器写 `ambient_error.json` 并中止。
    """

    def __init__(
        self,
        rule: MockReasoner,
        cost: AmbientCostModel,
        model_factory: Callable[[], Any] | None = None,
    ):
        self.rule = rule
        self.cost = cost
        self.model_factory = model_factory

    def decide(self, npc_state: dict[str, Any], world_view: dict[str, Any]) -> list[Intent]:
        tier = str(world_view.get("tier", ""))
        if tier == "spotlight":
            self.cost.spotlight_wakes += 1
            if not self.cost.llm_enabled:
                return self.rule.decide(npc_state, world_view)
            allowed, reason = self.cost.allow()
            if allowed:
                if self.model_factory is not None:
                    model = self.model_factory()
                    self.cost.record()
                    return model.decide(npc_state, world_view)
                self.cost.deny("model_not_wired")
            else:
                self.cost.deny(reason)
        return self.rule.decide(npc_state, world_view)

    def replan(self, npc_state: dict[str, Any], world_view: dict[str, Any], timing: str = "replan") -> list[Intent]:
        return self.rule.replan(npc_state, world_view, timing=timing)

    def observe(self, event: Event) -> None:
        self.rule.observe(event)


class AmbientScheduler:
    """后台世界自走调度器。

    公共接口：
    - start() / stop()
    - pause() / resume()
    - step(n=1)
    - set_speed(s)
    - enter_player(...) / leave_player(...)
    - run_ticks(n)  （不配速，测试与验收用）
    """

    def __init__(
        self,
        root: Path | None = None,
        cfg: AmbientConfig | None = None,
        content: Content | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        cpu_clock: Callable[[], float] | None = None,
    ):
        if cfg is None:
            raise ValueError("AmbientScheduler 需要 AmbientConfig")
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
        self.cfg = cfg
        self.out_dir = Path(cfg.out_dir)
        self.clock = clock or time.perf_counter
        self.sleeper = sleeper or time.sleep
        self.cpu_clock = cpu_clock or time.process_time
        self.content = content or load_content(
            self.root, min_npcs=0, allow_fixture=False, use_content=True
        )

        self.tick = int(cfg.start_tick)
        self._processed = 0
        self._first_tick_pending = True
        self.paused = False
        self.stopped = False
        self.speed = cfg.normalized_speed()
        self._anchor_tick = self.tick
        self._anchor_time = self.clock()
        self._auto_resume_at: float | None = None
        self._error: dict[str, Any] | None = None
        self._started_wall = self.clock()
        self._start_cpu = self.cpu_clock()
        self._cpu_guard_sleeps = 0
        self._cpu_guard_slept_s = 0.0
        self._cpu_total = 0.0
        self._wall_total = 0.0
        self._guard_wall_acc = 0.0
        self._guard_cpu_acc = 0.0
        self._control_seq = 0

        self._events_total = 0
        self._npc_events_total = 0
        self._active_npcs: set[str] = set()
        self._verb_counts: Counter[str] = Counter()
        self._window_events = 0
        self._window_verbs: Counter[str] = Counter()
        self._window_npcs: set[str] = set()
        self._today_heat: Counter[str] = Counter()
        self._event_chain = ""
        self._progress_chain = ""
        self._last_seq = 0
        self._tick_events = 0
        self._summaries: list[dict[str, Any]] = []
        self._player_actions: list[dict[str, Any]] = []
        self._control_log: list[dict[str, Any]] = []
        self._blocked: Counter[tuple[str, str]] = Counter()
        self._pruned: Counter[tuple[str, str]] = Counter()
        self._replans = 0
        self._queue: list[tuple[int, int, int, str, Any]] = []
        self._queue_seq = 0
        self._replan_today: Counter[str] = Counter()
        self._online_players: set[str] = set()
        self._lod_changes: list[dict[str, Any]] = []
        self._final: dict[str, Any] | None = None

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._audit_fh = (self.out_dir / "ambient.jsonl").open("a", encoding="utf-8")
        self._progress_fh = (self.out_dir / "ambient_progress.jsonl").open("a", encoding="utf-8")
        self._summary_fh = (self.out_dir / "ambient_summaries.jsonl").open("a", encoding="utf-8")
        self._control_fh = (self.out_dir / "ambient_control.jsonl").open("a", encoding="utf-8")

        npcs = self.content.npcs[: cfg.npc_limit] if cfg.npc_limit else self.content.npcs
        if not npcs:
            raise RuntimeError("没有 NPC 可自走")
        self.npcs = npcs
        self.npc_ids = sorted({str(n["id"]) for n in npcs})

        self.state = WorldState(locations=self.content.locations)
        self.store = EventStore(self.out_dir / "events.sqlite")
        self.registry = build_registry(self.content, cfg.seed)
        self.writer = Writer(self.state, self.store, self.registry, cfg.seed)

        self.cost = AmbientCostModel(
            llm_enabled=bool(cfg.llm_enabled), max_calls=max(0, int(cfg.llm_max_calls))
        )
        self.rule_reasoner = MockReasoner(seed=cfg.seed, content=self.content, telemetry=None)
        self.reasoner = AmbientReasoner(self.rule_reasoner, self.cost, None)

        genesis = build_genesis(self.content, npcs, root=self.root)
        self._genesis_event = self.writer.bootstrap(genesis.effects, tick=0, verb="genesis")
        self.state = self.writer.state
        self._record_event(self._genesis_event)
        self.genesis = genesis

        lod_cfg = load_lod_config(self.root / "config" / "lod.yaml")
        self.lod_cfg = lod_cfg
        self.lod = LODScheduler(
            list(self.npc_ids),
            lod_cfg,
            self.reasoner,
            seed=cfg.seed,
            start_tick=self.tick,
            state_provider=lambda npc: {"id": npc},
            world_view_provider=lambda tick: {
                "tick": tick,
                "day": tick // TICKS_PER_DAY,
                "state": self.writer.state,
                "content": self.content,
            },
        )
        self._log_line(
            f"[{stamp(0)}] ambient_boot npcs={len(self.npc_ids)} locations={len(self.content.locations)} "
            f"seed={cfg.seed} content={self.content.content_hash[:12]} speed={self.speed:g}x"
        )

    # ---------------------------------------------------------------- 基础设施
    def _log_line(self, text: str) -> None:
        print(text, flush=True)
        with (self.out_dir / "ambient.log").open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    def _write_json(self, name: str, payload: dict[str, Any]) -> None:
        path = self.out_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _record_event(self, ev: Event) -> None:
        if ev.seq <= self._last_seq:
            return
        self._last_seq = ev.seq
        self._events_total += 1
        self._tick_events += 1
        self._event_chain = hash_obj(
            {"prev": self._event_chain, "event": ev.to_canonical_dict()}
        )
        row = {
            "seq": ev.seq,
            "tick": ev.tick,
            "day": ev.day,
            "virtual_time": stamp(ev.tick),
            "actor": ev.actor,
            "verb": ev.verb,
            "outcome": ev.outcome,
            "source": ev.source,
            "reason": ev.reason,
            "args": ev.args,
            "effect_ops": [str(e.get("op", "")) for e in ev.effects],
            "canonical": ev.to_canonical_dict(),
            "event_hash": hash_obj(ev.to_canonical_dict()),
            "event_chain": self._event_chain,
        }
        self._audit_fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._audit_fh.flush()
        if (
            ev.outcome == "ok"
            and ev.source != "system"
            and not ev.verb.startswith("system_")
            and ev.actor in self.npc_ids
        ):
            self._npc_events_total += 1
            self._active_npcs.add(ev.actor)
            self._verb_counts[ev.verb] += 1
            self._window_events += 1
            self._window_verbs[ev.verb] += 1
            self._window_npcs.add(ev.actor)
            # 自走世界没有玩家注入热度：NPC 自身的行动即“活跃度”，
            # 否则纯后台运行会在数日内整体降档、事件密度单调下滑。
            self._today_heat[ev.actor] += 1

    def _push(self, tick: int, prio: int, kind: str, payload: Any) -> None:
        heapq.heappush(self._queue, (max(0, int(tick)), prio, self._queue_seq, kind, payload))
        self._queue_seq += 1

    def _enqueue(self, intents: list[Intent], at_tick: int) -> None:
        for it in intents:
            prio = 1 if it.verb == "talk" else 2
            self._push(max(it.tick, at_tick), prio, "intent", it)

    def _submit(self, intent: Intent) -> Event | None:
        reason = intent_block_reason(self.registry, self.writer.state, intent)
        if reason:
            if is_prunable(reason):
                self._pruned[(intent.verb, reason)] += 1
            else:
                self._blocked[(intent.verb, reason)] += 1
            return None
        ev = self.writer.submit(intent)
        self._record_event(ev)
        return ev

    def _schedule_quest_deliver(self, actor: str, quest_id: str, tick: int) -> None:
        if not quest_id:
            return
        quest = self.writer.state.quests.get(quest_id)
        self._push(
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

    # ---------------------------------------------------------------- 日界
    def _day_start(self, tick: int) -> None:
        day = tick // TICKS_PER_DAY
        self._emit_system(Intent("system", "system_recover", {}, tick, "system"))
        self._emit_system(Intent("system", "system_quest_post", {"day": day}, tick, "system"))
        for npc in self.npc_ids:
            self._emit_system(Intent(npc, "system_living", {"day": day}, tick, "system"))
        if self._online_players:
            locs = {
                self.writer.state.entities[p].location
                for p in sorted(self._online_players)
                if p in self.writer.state.entities
            }
            nearby = {
                npc
                for npc in self.npc_ids
                if npc in self.writer.state.entities
                and self.writer.state.entities[npc].location in locs
            }
            for npc in sorted(nearby):
                self.lod.set_tier(npc, "spotlight", now=tick, reason="player_nearby")

    def _emit_system(self, intent: Intent) -> Event | None:
        ev = self._submit(intent)
        if ev is not None and ev.outcome == "rejected":
            # 系统动作被拒属于世界异常，不静默：写入可见错误并在汇总中体现。
            self._log_line(f"[{stamp(intent.tick)}] system_rejected verb={intent.verb} reason={ev.reason}")
        return ev

    def _day_end(self, tick: int) -> None:
        day = tick // TICKS_PER_DAY
        for verb, args in (
            ("system_debts", {}),
            ("system_quest_fail", {}),
            ("system_milestone", {"day": day}),
            ("system_maintenance", {"day": day}),
        ):
            self._emit_system(Intent("system", verb, args, tick, "system"))
        if (day + 1) in (30, 60, 90):
            for verb in ("system_savings_tax", "system_shop_dividend", "system_shop_tax"):
                self._emit_system(Intent("system", verb, {"day": day}, tick, "system"))
        self._update_tiers(tick + 1, day)

    def _update_tiers(self, now: int, day: int) -> None:
        heat = Counter(self._today_heat)
        idle_days = {}
        for npc in self.npc_ids:
            ent = self.writer.state.entities.get(npc)
            last = ent.last_action_tick if ent is not None else 0
            idle_days[npc] = max(0, (now - last) // TICKS_PER_DAY)
        nearby = set()
        if self._online_players:
            locs = {
                self.writer.state.entities[p].location
                for p in sorted(self._online_players)
                if p in self.writer.state.entities
            }
            nearby = {
                npc for npc in self.npc_ids
                if npc in self.writer.state.entities
                and self.writer.state.entities[npc].location in locs
            }
        changes = self.lod.update_tiers(now=now, heat=heat, idle_days=idle_days, nearby=nearby)
        for c in changes:
            self._lod_changes.append({**c, "day": day + 1})

    # ---------------------------------------------------------------- tick 推进
    def _process_tick(self) -> None:
        if self._first_tick_pending:
            self._first_tick_pending = False
        else:
            self.tick += 1
        self._processed += 1
        t = self.tick
        self._tick_events = 0
        if t % TICKS_PER_DAY == 0:
            self._today_heat = Counter()
            self._day_start(t)
            self._replan_today = Counter()
        for wake in self.lod.advance_to(t):
            self._enqueue(wake.intents, t)
        while self._queue and self._queue[0][0] <= t:
            _, _prio, _seq, kind, payload = heapq.heappop(self._queue)
            if kind == "intent":
                ev = self._submit(payload)
                if ev is None:
                    self._maybe_replan(payload)
                    continue
                self.reasoner.observe(ev)
                if ev.outcome == "rejected" and payload.source in ("planner", "llm"):
                    self._maybe_replan(payload)
                if ev.outcome == "ok":
                    quest_id = str((payload.args or {}).get("quest") or "")
                    for eff in ev.effects:
                        if eff.get("op") == "escort_start":
                            arrive = int((eff.get("data") or {}).get("arrive_tick", t))
                            self._push(arrive, 4, "escort", None)
                            if quest_id and payload.verb == "escort":
                                self._schedule_quest_deliver(payload.actor, quest_id, arrive)
            elif kind == "replan":
                npc = str(payload)
                tier = self.lod.lod.get(npc, "active")
                view = {
                    "tick": t,
                    "state": self.writer.state,
                    "content": self.content,
                    "tier": tier,
                    "interval": self.lod_cfg.tiers[tier].wake_interval_ticks,
                }
                self._enqueue(self.reasoner.replan({"id": npc}, view, timing="replan"), t)
                self._replans += 1
            elif kind == "escort":
                self._emit_system(
                    Intent("system", "system_escort_arrive", {}, t, "system")
                )
        if t % TICKS_PER_DAY == (TICKS_PER_DAY - 1):
            # 日末结算：t=143 对应第 0 日结束，与 headless 的 base+143 对齐。
            self._day_end(t)
        # 世界时钟与事件日志同源：tick 不参与状态哈希，只用于玩家写操作校验。
        self.writer.state.tick = t
        self._write_progress(t)
        if self.cfg.summary_every_ticks > 0 and t % self.cfg.summary_every_ticks == 0:
            self._write_summary(t)

    def _maybe_replan(self, payload: Intent) -> None:
        if payload.source not in ("planner", "llm"):
            return
        if self._replan_today[payload.actor] >= 6:
            return
        self._replan_today[payload.actor] += 1
        self._push(self.tick + 1, 3, "replan", payload.actor)

    def _write_progress(self, t: int) -> None:
        state_digest = state_hash(self.writer.state)
        self._progress_chain = hash_obj(
            {
                "prev": self._progress_chain,
                "tick": t,
                "seq": self._last_seq,
                "state": state_digest,
                "event_chain": self._event_chain,
            }
        )
        row = {
            "tick": t,
            "day": t // TICKS_PER_DAY,
            "seq": self._last_seq,
            "events_total": self._events_total,
            "state_hash": state_digest,
            "event_chain": self._event_chain,
            "chain": self._progress_chain,
        }
        self._progress_fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._progress_fh.flush()

    def _write_summary(self, t: int) -> None:
        now = self.clock()
        elapsed = max(0.0, now - self._started_wall)
        cpu_ratio = self._avg_cpu_ratio()
        try:
            cpu_used = max(0.0, self.cpu_clock() - self._start_cpu)
            proc_ratio = round(cpu_used / elapsed, 4) if elapsed > 0 else 0.0
        except Exception:
            proc_ratio = 0.0
        row = {
            "tick": t,
            "day": t // TICKS_PER_DAY + 1,
            "virtual_time": stamp(t),
            "events_total": self._events_total,
            "events_window": self._window_events,
            "npc_events_total": self._npc_events_total,
            "npc_count": len(self.npc_ids),
            "active_npcs_total": len(self._active_npcs),
            "active_npcs_window": len(self._window_npcs),
            "verbs_total": len(self._verb_counts),
            "verbs_window": dict(sorted(self._window_verbs.items())),
            "llm_calls": self.cost.calls,
            "cost_usd": round(self.cost.cost_usd, 6),
            "elapsed_s": round(elapsed, 3),
            "cpu_ratio_proc": proc_ratio,
            "cpu_ratio_tick_avg": cpu_ratio,
            "ticks_per_real_min": (
                round(self._processed / (elapsed / 60.0), 3) if elapsed > 0 else 0.0
            ),
            "cpu_guard_sleeps": self._cpu_guard_sleeps,
        }
        self._summaries.append(row)
        self._summary_fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._summary_fh.flush()
        self._log_line(
            f"[{stamp(t)}] ambient_hour tick={t} events={row['events_total']}(+{self._window_events}) "
            f"npcs={row['active_npcs_total']}/{row['npc_count']} verbs={row['verbs_total']} "
            f"llm_calls={row['llm_calls']} cost=${row['cost_usd']:.4f} "
            f"elapsed={row['elapsed_s']:.1f}s tpm={row['ticks_per_real_min']:.1f}"
        )
        self._window_events = 0
        self._window_start_tick = t
        self._window_verbs = Counter()
        self._window_npcs = set()

    # ---------------------------------------------------------------- 控制面
    def pause(self, source: str = "api") -> None:
        if self.paused:
            return
        self.paused = True
        self._record_control("pause", source)
        self._log_line(f"[{stamp(self.tick)}] ambient_pause tick={self.tick}")

    def resume(self, source: str = "api") -> None:
        if not self.paused:
            return
        self.paused = False
        self._auto_resume_at = None
        self._anchor_tick = self.tick
        self._anchor_time = self.clock()
        self._record_control("resume", source)
        self._log_line(f"[{stamp(self.tick)}] ambient_resume tick={self.tick}")

    def set_speed(self, speed: float, source: str = "api") -> None:
        self.speed = _nearest_speed(speed)
        self._anchor_tick = self.tick
        self._anchor_time = self.clock()
        self._record_control("speed", source, {"speed": self.speed})
        self._log_line(f"[{stamp(self.tick)}] ambient_speed {self.speed:g}x")

    def step(self, n: int = 1, source: str = "api") -> int:
        """单步推进 n 个 tick；暂停状态下也可用，之后仍保持原运行状态。"""
        n = max(0, int(n))
        self._record_control("step", source, {"ticks": n})
        for _ in range(n):
            self._process_tick()
            if self._max_reached():
                break
        self._anchor_tick = self.tick
        self._anchor_time = self.clock()
        return self.tick

    def stop(self, source: str = "api") -> None:
        self.stopped = True
        self._record_control("stop", source)

    def _record_control(self, command: str, source: str, extra: dict[str, Any] | None = None) -> None:
        self._control_seq += 1
        row = {
            "seq": self._control_seq,
            "tick": self.tick,
            "virtual_time": stamp(self.tick),
            "command": command,
            "source": source,
            "elapsed_s": round(max(0.0, self.clock() - self._started_wall), 3),
        }
        if extra:
            row.update(extra)
        self._control_log.append(row)
        self._control_fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._control_fh.flush()

    def _poll_control(self) -> None:
        if not self.cfg.control_file:
            return
        path = Path(self.cfg.control_file)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            self._write_error("control", exc)
            raise
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        command = str(raw.get("command") or "")
        if command == "pause":
            self.pause(source="control_file")
        elif command == "resume":
            self.resume(source="control_file")
        elif command == "step":
            self.step(int(raw.get("ticks", 1)), source="control_file")
        elif command == "speed":
            self.set_speed(float(raw.get("speed", 1.0)), source="control_file")
        elif command == "stop":
            self.stop(source="control_file")

    # ---------------------------------------------------------------- 配速
    def _max_reached(self) -> bool:
        return self.cfg.max_ticks is not None and self._processed >= int(self.cfg.max_ticks)

    def _target_tick(self) -> int:
        elapsed = max(0.0, self.clock() - self._anchor_time)
        rate = float(self.cfg.ticks_per_second_1x) * self.speed
        if rate <= 0:
            return self._anchor_tick
        return self._anchor_tick + int(elapsed * rate)

    def _idle_sleep(self) -> None:
        rate = max(1e-9, float(self.cfg.ticks_per_second_1x) * self.speed)
        period = 1.0 / rate
        elapsed = max(0.0, self.clock() - self._anchor_time)
        remain = period - (elapsed % period)
        self.sleeper(min(0.25, max(0.002, remain)))

    def _cpu_guard(self, tick_wall: float, tick_cpu: float) -> None:
        if tick_wall <= 0:
            return
        self._wall_total += tick_wall
        self._cpu_total += max(0.0, tick_cpu)
        # 按时间窗评估，避免配速空闲把「忙时 CPU 比例」误算成整进程占用。
        self._guard_wall_acc += tick_wall
        self._guard_cpu_acc += max(0.0, tick_cpu)
        if self._guard_wall_acc < 0.25:
            return
        ratio = self._guard_cpu_acc / self._guard_wall_acc
        limit = float(self.cfg.max_cpu_ratio)
        if limit > 0 and ratio > limit:
            need = max(0.0, self._guard_cpu_acc / limit - self._guard_wall_acc)
            if need > 0:
                self.sleeper(need)
                self._cpu_guard_sleeps += 1
                self._cpu_guard_slept_s += need
                self._wall_total += need
        self._guard_wall_acc = 0.0
        self._guard_cpu_acc = 0.0

    def _avg_cpu_ratio(self) -> float:
        if self._wall_total <= 0:
            return 0.0
        return round(self._cpu_total / self._wall_total, 4)

    def run_ticks(self, n: int) -> int:
        """不配速连跑 n 个 tick（测试/验收用），仍受 CPU 护栏约束。"""
        for _ in range(max(0, int(n))):
            self._tick_with_guard()
            if self._max_reached():
                break
        return self.tick

    def _tick_with_guard(self) -> None:
        t0_wall = self.clock()
        t0_cpu = self.cpu_clock()
        try:
            self._process_tick()
        except Exception as exc:  # 不静默：落盘后继续抛出
            self._write_error("tick", exc)
            raise
        self._cpu_guard(self.clock() - t0_wall, self.cpu_clock() - t0_cpu)

    def start(
        self,
        minutes: float | None = None,
        pause_at_tick: int | None = None,
        resume_after_s: float | None = None,
    ) -> dict[str, Any]:
        """启动按真实时间配速的自走循环，直到 stop / 时长到 / tick 上限。"""
        deadline = self.clock() + minutes * 60.0 if minutes is not None else None
        pause_done = False
        self._anchor_tick = self.tick
        self._anchor_time = self.clock()
        while not self.stopped:
            self._poll_control()
            if deadline is not None and self.clock() >= deadline:
                break
            if self._max_reached():
                break
            if self.paused:
                if self._auto_resume_at is not None and self.clock() >= self._auto_resume_at:
                    self.resume(source="auto_resume")
                else:
                    self.sleeper(0.05)
                    continue
            if (
                pause_at_tick is not None
                and not pause_done
                and self.tick >= int(pause_at_tick)
            ):
                pause_done = True
                self.pause(source="pause_at_tick")
                if resume_after_s is not None:
                    self._auto_resume_at = self.clock() + float(resume_after_s)
                continue
            target = self._target_tick()
            if self.tick < target:
                self._tick_with_guard()
            else:
                self._idle_sleep()
        return self.final_summary()

    # ---------------------------------------------------------------- 玩家进出
    def _player_location(self) -> str:
        if "market" in self.content.locations:
            return "market"
        return sorted(self.content.locations)[0]

    def enter_player(
        self,
        player_id: str = "player:visitor",
        account_id: str = "",
        location: str | None = None,
        display_name: str = "访客",
        archetype: str = "游侠",
    ) -> dict[str, Any]:
        pid = player_id if player_id.startswith("player:") else f"player:{player_id}"
        account = account_id or pid[len("player:"):]
        at = location or self._player_location()
        intent = Intent(
            actor=pid,
            verb="player_enter",
            args={
                "player_id": pid,
                "account_id": account,
                "display_name": display_name,
                "archetype": archetype,
                "at": at,
            },
            tick=self.tick,
            source="player",
            reason="ambient_player_enter",
        )
        ev = self.writer.submit(intent)
        self._record_event(ev)
        if ev.outcome == "ok":
            self._online_players.add(pid)
            nearby = {
                npc for npc in self.npc_ids
                if npc in self.writer.state.entities
                and self.writer.state.entities[npc].location == at
            }
            for npc in sorted(nearby):
                self.lod.set_tier(npc, "spotlight", now=self.tick, reason="player_nearby")
        row = {
            "action": "enter",
            "player": pid,
            "tick": self.tick,
            "seq": ev.seq,
            "outcome": ev.outcome,
            "reason": ev.reason,
            "location": at,
            "state_hash": state_hash(self.writer.state),
            "chain": self._event_chain,
        }
        self._player_actions.append(row)
        self._log_line(
            f"[{stamp(self.tick)}] player_enter {pid} tick={self.tick} seq={ev.seq} outcome={ev.outcome}"
        )
        return row

    def leave_player(self, player_id: str = "player:visitor") -> dict[str, Any]:
        pid = player_id if player_id.startswith("player:") else f"player:{player_id}"
        ev = self.writer.submit(
            Intent(
                actor=pid,
                verb="player_leave",
                args={"player_id": pid},
                tick=self.tick,
                source="player",
                reason="ambient_player_leave",
            )
        )
        self._record_event(ev)
        if ev.outcome == "ok":
            self._online_players.discard(pid)
        row = {
            "action": "leave",
            "player": pid,
            "tick": self.tick,
            "seq": ev.seq,
            "outcome": ev.outcome,
            "reason": ev.reason,
            "state_hash": state_hash(self.writer.state),
            "chain": self._event_chain,
        }
        self._player_actions.append(row)
        self._log_line(
            f"[{stamp(self.tick)}] player_leave {pid} tick={self.tick} seq={ev.seq} outcome={ev.outcome}"
        )
        return row

    # ---------------------------------------------------------------- 收尾
    def final_summary(self) -> dict[str, Any]:
        if self._final is not None:
            return self._final
        all_events = self.store.read_all()
        elapsed = max(0.0, self.clock() - self._started_wall)
        try:
            cpu_used = max(0.0, self.cpu_clock() - self._start_cpu)
            proc_ratio = round(cpu_used / elapsed, 4) if elapsed > 0 else 0.0
        except Exception:
            proc_ratio = 0.0
        row = {
            "run_id": self.cfg.run_id,
            "seed": self.cfg.seed,
            "speed": self.speed,
            "ticks": self._processed,
            "last_tick": self.tick,
            "days": round(self._processed / TICKS_PER_DAY, 3),
            "npc_count": len(self.npc_ids),
            "events_total": len(all_events),
            "npc_events": self._npc_events_total,
            "active_npcs": len(self._active_npcs),
            "active_npc_ids": sorted(self._active_npcs),
            "distinct_verbs": len(self._verb_counts),
            "verbs": dict(sorted(self._verb_counts.items())),
            "llm_calls": self.cost.calls,
            "cost_usd": round(self.cost.cost_usd, 6),
            "cost": self.cost.summary(),
            "elapsed_s": round(elapsed, 3),
            "ticks_per_real_min": (
                round(self._processed / (elapsed / 60.0), 3) if elapsed > 0 else 0.0
            ),
            "cpu_ratio_proc": proc_ratio,
            "cpu_ratio_tick_avg": self._avg_cpu_ratio(),
            "cpu_guard_sleeps": self._cpu_guard_sleeps,
            "cpu_guard_slept_s": round(self._cpu_guard_slept_s, 3),
            "summaries": len(self._summaries),
            "progress_records": self._processed,
            "state_hash": state_hash(self.writer.state),
            "event_hash": event_hash(all_events),
            "event_chain": self._event_chain,
            "progress_chain": self._progress_chain,
            "last_seq": self._last_seq,
            "blocked": {f"{verb}:{reason}": n for (verb, reason), n in sorted(self._blocked.items())},
            "pruned": {f"{verb}:{reason}": n for (verb, reason), n in sorted(self._pruned.items())},
            "replans": self._replans,
            "lod": self.lod.summary(),
            "lod_changes": self._lod_changes,
            "player_actions": self._player_actions,
            "control": self._control_log,
            "content_hash": self.content.content_hash,
            "files": {
                "events": "events.sqlite",
                "audit": "ambient.jsonl",
                "progress": "ambient_progress.jsonl",
                "summaries": "ambient_summaries.jsonl",
                "control": "ambient_control.jsonl",
                "log": "ambient.log",
            },
        }
        if self._error is not None:
            row["error"] = self._error
        self._final = row
        self._write_json("ambient_summary.json", row)
        self._close()
        return row

    def _write_error(self, phase: str, exc: BaseException) -> None:
        payload = {
            "tick": self.tick,
            "virtual_time": stamp(self.tick),
            "phase": phase,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        self._error = payload
        self._write_json("ambient_error.json", payload)
        self._log_line(
            f"[{stamp(self.tick)}] AMBIENT_ERROR phase={phase} {payload['error']} (see ambient_error.json)"
        )

    def _close(self) -> None:
        for fh in (self._audit_fh, self._progress_fh, self._summary_fh, self._control_fh):
            try:
                fh.close()
            except Exception:
                pass
        try:
            self.store.close()
        except Exception:
            pass
