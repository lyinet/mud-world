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

import contextlib
import heapq
import json
import os
import threading
import time
import traceback
from collections import Counter, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from ..actions.business import has_player_shop
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

# 日界触发的系统结算：与 core/settlement_clock.SETTLEMENTS 同一事实源，
# 新增日界动词必须同时登记时钟归属，否则登记表测试失败。
DAY_START_VERBS: tuple[str, ...] = (
    "system_recover",
    "system_quest_post",
    "system_living",
)
DAY_END_VERBS: tuple[str, ...] = (
    "system_debts",
    "system_quest_fail",
    "system_milestone",
    "system_maintenance",
    "system_shop_settle",
    "system_savings_tax",
    "system_shop_dividend",
    "system_shop_tax",
)

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
    # CPU 护栏：单进程 CPU/墙钟 比例上限，默认 70%（不吃满一核）。
    max_cpu_ratio: float = 0.70
    # 采样窗口（秒）：窗口内原始 CPU 比超过上限先补睡降速。
    cpu_sample_s: float = 0.25
    # 持续超限多久后暂停自走，避免拖死服务端。
    cpu_pause_after_s: float = 2.0
    # 恢复线：暂停期间比例低于该值并持续 cpu_resume_after_s 秒后自动恢复。
    cpu_resume_below_ratio: float = 0.35
    cpu_resume_after_s: float = 2.0
    # 父进程看护：父进程消失时自走随之停止（start.sh 收口用）。
    parent_pid: int | None = None
    # 状态文件；默认 <out_dir>/ambient_status.json。
    status_file: Path | None = None
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
    # 共享世界：ambient 每轮最多追赶多少个世界 tick。
    # 玩家一次写推进 6 tick，世界时钟可比 ambient 配速快数百倍；用这个上限
    # 限制单步的 LOD 唤醒量与写入尖峰，追不上的部分记到 lag_ticks 并告警。
    # 1200 ≈ 50 玩家、think-time 2s 时一个 6s 配速步的世界跨度（约 870），
    # 留余量；追上世界时钟后按 ambient 自己的配速再推进一格。
    max_catchup_ticks: int = 1200
    # 共享世界：每轮最多真正唤醒多少个 NPC。超出配额不调用 reasoner，只跳过并
    # 记到 skipped_wakes；LOD 时间仍推进，因此世界时间不落后，只是事件被稀释。
    max_wakes_per_step: int = 8
    # 共享世界：每轮最多消费多少个队列事件；超出的留在队列，下一轮继续。
    max_intents_per_step: int = 16
    # 心跳间隔（秒）：与 tick 解耦，idle 时也刷新状态文件的 heartbeat_at。
    heartbeat_s: float = 1.0

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
        writer: Writer | None = None,
        store: EventStore | None = None,
        state: WorldState | None = None,
        registry: Any | None = None,
        lock: Any | None = None,
    ):
        if cfg is None:
            raise ValueError("AmbientScheduler 需要 AmbientConfig")
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
        self.cfg = cfg
        self.out_dir = Path(cfg.out_dir)
        self.clock = clock or time.perf_counter
        self.sleeper = sleeper or time.sleep
        # 共享世界下 ambient 跑在 API 进程的后台线程里：护栏必须只看自走线程
        # 自己的 CPU（time.thread_time），否则 50 玩家的请求 CPU 会被算到自走头上，
        # 自走被误判超限并反复暂停 —— 这正是“玩家活跃时 ambient 饿死”的来源之一。
        # 独立进程模式下进程 CPU 与线程 CPU 等价，沿用 process_time。
        if cpu_clock is not None:
            self.cpu_clock = cpu_clock
        elif writer is not None:
            self.cpu_clock = time.thread_time
        else:
            self.cpu_clock = time.process_time
        self.content = content or load_content(
            self.root, min_npcs=0, allow_fixture=False, use_content=True
        )
        # 共享世界模式：注入 API 的单写者（Writer/EventStore/registry）与锁，
        # 自走在同一份 events.sqlite 上排队，绝不另开第二个写者。
        self.writer = writer
        self.store = store
        self.registry = registry
        self.shared = writer is not None
        self._lock = lock
        self._owns_store = store is None

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
        # CPU 护栏升级：持续超限暂停 + 自动恢复。
        self._cpu_paused = False
        self._cpu_over_s = 0.0
        self._cpu_pauses = 0
        self._cpu_resumes = 0
        self._resume_wall_acc = 0.0
        self._resume_cpu_acc = 0.0
        self._resume_ok_s = 0.0
        self._pause_reason = ""
        self._stop_reason = ""
        self._last_tick_at = self._now_iso()
        self._status_written_at = 0.0
        self._next_parent_check = self.clock()
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
        self._day_marker: int | None = None

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.status_path = (
            Path(cfg.status_file) if cfg.status_file else self.out_dir / "ambient_status.json"
        )
        self._audit_fh = (self.out_dir / "ambient.jsonl").open("a", encoding="utf-8")
        self._progress_fh = (self.out_dir / "ambient_progress.jsonl").open("a", encoding="utf-8")
        self._summary_fh = (self.out_dir / "ambient_summaries.jsonl").open("a", encoding="utf-8")
        self._control_fh = (self.out_dir / "ambient_control.jsonl").open("a", encoding="utf-8")
        # 归因探针：每步一行（锁等待 / 锁内 / 进度写 / 状态写），缓冲 30 行再落盘，
        # 避免探针自身的 IO 成为被测停顿。ambient_steps.jsonl 仅供归因，不进任何哈希。
        self._steps_fh = (self.out_dir / "ambient_steps.jsonl").open("a", encoding="utf-8")
        self._steps_pending = 0

        npcs = self.content.npcs[: cfg.npc_limit] if cfg.npc_limit else self.content.npcs
        if not npcs:
            raise RuntimeError("没有 NPC 可自走")
        self.npcs = npcs
        self.npc_ids = sorted({str(n["id"]) for n in npcs})

        if not self.shared:
            self.state = WorldState(locations=self.content.locations)
            self.store = EventStore(self.out_dir / "events.sqlite")
            self.registry = build_registry(self.content, cfg.seed)
            self.writer = Writer(self.state, self.store, self.registry, cfg.seed)
        else:
            # 世界（含 genesis/玩家存档）已由 WorldSession 建立；这里只接手，不重建。
            self.writer = writer
            self.store = store if store is not None else writer.store
            self.registry = registry if registry is not None else writer.registry
            self.state = state if state is not None else writer.state
            self.tick = int(self.state.tick)
            # 共享世界已有 genesis：下一个 tick 直接从世界当前 tick 往后推。
            self._first_tick_pending = False

        self.cost = AmbientCostModel(
            llm_enabled=bool(cfg.llm_enabled), max_calls=max(0, int(cfg.llm_max_calls))
        )
        self.rule_reasoner = MockReasoner(seed=cfg.seed, content=self.content, telemetry=None)
        self.reasoner = AmbientReasoner(self.rule_reasoner, self.cost, None)

        if self.shared:
            self.genesis = None
            self._genesis_event = None
        else:
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
            world_view_provider=self._world_view,
        )
        # 共享世界的两个时钟：world_tick 是玩家推进的世界时钟；
        # processed_world_tick 是 ambient 已结算到的世界时间点；差值即 lag。
        self._world_tick = int(self.writer.state.tick) if self.shared else 0
        self._processed_world_tick = self._world_tick
        self._lag_ticks = 0
        self._lag_ticks_max = 0
        # 调度步实际耗时（滚动窗口，用于区分“锁内自走变慢”与“进程/系统级停顿”）。
        self._step_ms: deque[float] = deque(maxlen=512)
        self._step_ms_max = 0.0
        self._lock_wait_ms: deque[float] = deque(maxlen=512)
        self._in_lock_ms: deque[float] = deque(maxlen=512)
        self._in_lock_cpu_ms: deque[float] = deque(maxlen=512)
        self._lock_wait_ms_max = 0.0
        self._in_lock_ms_max = 0.0
        self._last_parts: dict[str, float] = {}
        self._skipped_days = 0
        self._dropped_stale = 0
        # lag_steps：累计被 max_catchup_ticks 截断的调度步数（与
        # ambient_catchup_truncated 同一计数）；lag_streak：旧 lag_steps 语义，
        # 连续截断步数，改名保留，避免旧字段静默改义。
        self._lag_steps = 0
        self._lag_streak = 0
        # 每 NPC 日推进的世界 tick 数（EMA）；headless 恒为 144。
        self._world_ticks_per_npc_day = float(TICKS_PER_DAY)
        self._day_world_tick = self._world_tick
        self._heartbeat_at = self._now_iso()
        self._max_queue_backlog = 0
        self._log_line(
            f"[{stamp(0)}] ambient_boot npcs={len(self.npc_ids)} locations={len(self.content.locations)} "
            f"seed={cfg.seed} content={self.content.content_hash[:12]} speed={self.speed:g}x"
        )
        self._write_status("running")

    # ---------------------------------------------------------------- 基础设施
    def _log_line(self, text: str) -> None:
        print(text, flush=True)
        with (self.out_dir / "ambient.log").open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    def _write_json(self, name: str, payload: dict[str, Any]) -> None:
        path = self.out_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _now_iso(self) -> str:
        # 状态文件需要人类可读墙钟时间；用 clock_gettime，避免确定性 lint 禁止的调用。
        epoch = time.clock_gettime(time.CLOCK_REALTIME)
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch))

    def _write_status(self, state: str, reason: str = "") -> None:
        """原子写 ambient_status.json：API/UI 只读它，不碰世界状态。"""
        self._status_written_at = self.clock()
        self._heartbeat_at = self._now_iso()
        # lag_ticks 是“世界时钟已推进、ambient 尚未结算”的实时差额；峰值必须在
        # 每次读数时刷新，否则末值会大于 max（旧实现只在截断分支更新 max）。
        live_lag = (
            max(0, int(self.writer.state.tick) - int(self._processed_world_tick))
            if self.shared
            else 0
        )
        if self.shared and live_lag > self._lag_ticks_max:
            self._lag_ticks_max = live_lag
        payload = {
            "ok": state in ("running", "paused"),
            "run_id": self.cfg.run_id,
            "pid": os.getpid(),
            "state": state,
            "reason": reason,
            "tick": int(self.writer.state.tick) if self.shared else int(self.tick),
            "heartbeat_at": self._heartbeat_at,
            "last_tick_at": self._last_tick_at,
            "updated_at": self._now_iso(),
            "ambient_tick": int(self._processed_world_tick) if self.shared else int(self.tick),
            "world_tick": int(self.writer.state.tick) if self.shared else int(self.tick),
            "lag_ticks": live_lag,
            "lag_ticks_max": int(self._lag_ticks_max) if self.shared else 0,
            # lag_steps 为累计截断步数；旧的“连续截断步数”改名 lag_streak/
            # lag_steps_streak 保留，旧字段含义不静默改变。
            "lag_steps": int(self._lag_steps) if self.shared else 0,
            "lag_streak": int(self._lag_streak) if self.shared else 0,
            "lag_steps_streak": int(self._lag_streak) if self.shared else 0,
            "ambient_catchup_truncated": int(self._lag_steps) if self.shared else 0,
            "world_ticks_per_npc_day": round(self._world_ticks_per_npc_day, 2),
            "npc_tick": int(self.tick),
            "step_ms_max": round(self._step_ms_max, 2),
            "step_ms_p95": self._step_ms_p95(),
            "lock_wait_ms_p95": self._p95(self._lock_wait_ms),
            "lock_wait_ms_max": round(self._lock_wait_ms_max, 2),
            "in_lock_ms_p95": self._p95(self._in_lock_ms),
            "in_lock_ms_max": round(self._in_lock_ms_max, 2),
            "in_lock_cpu_ms_p95": self._p95(self._in_lock_cpu_ms),
            "step_parts": dict(self._last_parts),
            "store_ck": self.store.ck_stats() if hasattr(self.store, "ck_stats") else {},
            "skipped_days": int(self._skipped_days) if self.shared else 0,
            "skipped_wakes": int(self.lod.skipped_wakes),
            "dropped_stale": int(self._dropped_stale),
            "queue_backlog": len(self._queue),
            "max_queue_backlog": int(self._max_queue_backlog),
            "speed": self.speed,
            "npc_count": len(self.npc_ids),
            "events_total": self._events_total,
            "npc_events_total": self._npc_events_total,
            "active_npcs": len(self._active_npcs),
            "verbs": len(self._verb_counts),
            "llm_calls": self.cost.calls,
            "parent_pid": self.cfg.parent_pid,
            "cpu": {
                "ratio_avg": self._avg_cpu_ratio(),
                "limit": float(self.cfg.max_cpu_ratio),
                "throttles": int(self._cpu_guard_sleeps),
                "throttled_s": round(self._cpu_guard_slept_s, 3),
                "pauses": int(self._cpu_pauses),
                "resumes": int(self._cpu_resumes),
            },
        }
        tmp = self.status_path.with_name(self.status_path.name + ".tmp")
        try:
            t0 = self.clock()
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            os.replace(tmp, self.status_path)
            self._last_parts["status_file_ms"] = round((self.clock() - t0) * 1000.0, 3)
        except OSError as exc:
            self._log_line(f"[{stamp(self.tick)}] ambient_status_write_failed {type(exc).__name__}: {exc}")

    def _maybe_status_heartbeat(self) -> None:
        if self.clock() - self._status_written_at >= float(self.cfg.heartbeat_s):
            self._write_status("paused" if self.paused else "running", self._pause_reason)

    def _parent_gone(self) -> bool:
        pid = self.cfg.parent_pid
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return True
        except (PermissionError, OverflowError, ValueError):
            return False
        return False

    # -------------------------------------------------- 共享世界（单写者会合点）
    def _guard(self):
        """与 API 的 WorldSession.lock 共用同一把锁；独立模式为空上下文。"""
        return self._lock if self._lock is not None else contextlib.nullcontext()

    def _sync_shared_tick(self) -> None:
        """把世界 tick 与在线玩家同步进来；不覆盖 ambient 自己的配速时钟。

        玩家动作一次推进 6 tick，世界时钟会远快于 ambient 配速。ambient 仍按
        自己的配速决定“何时执行一次调度步”，并在 _process_tick 里用
        max_catchup_ticks 限制单步追赶的世界 tick 跨度；事件 tick 取世界当前
        tick，保证不倒退。补跑时保留队列里的旧意图，由调度步按配额消费。
        """
        if not self.shared:
            return
        with self._guard():
            self._world_tick = int(self.writer.state.tick)
            self._online_players = {
                pid for pid, prof in self.writer.state.players.items() if prof.online
            }

    def start_background(self, name: str = "wsim-ambient") -> threading.Thread:
        """在后台线程跑配速循环（与 API 同进程、共用同一把写锁）。"""

        def _run() -> None:
            try:
                self.start()
            except Exception as exc:  # 不静默：落盘 ambient_error.json，线程正常退出
                self._write_error("background", exc)

        thread = threading.Thread(target=_run, name=name, daemon=True)
        thread.start()
        return thread

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
        if self.shared and intent.clock_tick is None:
            # 共享世界：所有提交意图统一带 NPC 日历 tick（ambient 步数）。
            # 玩家/系统 Intent 由各自来源显式补；这里兜住 LOD/计划产出，
            # 保证判定层与显示层读同一个时钟。headless 不加，旧哈希不变。
            intent = replace(intent, clock_tick=int(self.tick))
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
    def _world_view(self, tick: int) -> dict[str, Any]:
        view: dict[str, Any] = {
            "tick": tick,
            "day": tick // TICKS_PER_DAY,
            "state": self.writer.state,
            "content": self.content,
        }
        if self.shared:
            # NPC 日历只在共享世界暴露；headless 没有 clock_tick，判定回退到
            # 旧 tick/cursor 语义，保证历史哈希逐字节不变。
            view["clock_tick"] = int(self.tick)
        return view

    def _day_start(self, tick: int, day: int | None = None) -> None:
        if day is None:
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

    def _day_end(self, tick: int, day: int | None = None) -> None:
        if day is None:
            day = tick // TICKS_PER_DAY
        for verb, args in (
            ("system_debts", {}),
            ("system_quest_fail", {}),
            ("system_milestone", {"day": day}),
            ("system_maintenance", {"day": day}),
        ):
            self._emit_system(Intent("system", verb, args, tick, "system"))
        # 玩家店铺日结：独立事件，只在存在玩家店铺时发出；无店世界的事件序列逐字节不变。
        if has_player_shop(self.writer.state):
            self._emit_system(
                Intent("system", "system_shop_settle", {"day": day}, tick, "system")
            )
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
        if self.shared:
            # 调度步由 ambient 自己的配速驱动；世界时钟只作为结算时间轴。
            self.tick += 1
            world = int(self.writer.state.tick)
            self._world_tick = world
            # 单步最多追赶 max_catchup_ticks 个世界 tick：限制 LOD 唤醒量与写入尖峰。
            cap = max(1, int(self.cfg.max_catchup_ticks))
            catchup_target = self._processed_world_tick + cap
            if catchup_target <= world:
                # 被追赶上限截断：本步只结算到 processed+cap，剩余差额记 lag 并告警，
                # 保证“世界永久落后于玩家时钟”不会静默发生。
                target = catchup_target
                self._lag_ticks = max(0, world - target)
                self._lag_ticks_max = max(self._lag_ticks_max, self._lag_ticks)
                self._lag_steps += 1
                self._lag_streak += 1
                if self._lag_streak == 1 or self._lag_streak % 60 == 0:
                    self._log_line(
                        f"[{stamp(world)}] ambient_catchup_truncated lag={self._lag_ticks} "
                        f"cap={cap} streak={self._lag_streak} processed={self._processed_world_tick}"
                    )
            else:
                # 已追上玩家世界时钟：按 ambient 自己的配速再推进一格。
                # 无玩家时世界时钟正是靠这一步前进，与旧行为一致。
                target = world + 1
                self._lag_ticks = 0
                self._lag_streak = 0
            if target < self._processed_world_tick:
                target = self._processed_world_tick
            skipped = target // TICKS_PER_DAY - self._processed_world_tick // TICKS_PER_DAY
            if skipped > 1:
                self._skipped_days += skipped - 1
            self._processed_world_tick = target
            self._online_players = {
                pid for pid, prof in self.writer.state.players.items() if prof.online
            }
            # 事件时间轴：追平时用 target（推进世界一格）；追赶时用 world（不倒退）。
            t = target if target > world else world
        elif self._first_tick_pending:
            self._first_tick_pending = False
            t = self.tick
        else:
            self.tick += 1
            t = self.tick
        self._processed += 1
        # sched_t：ambient 本次结算到的世界时间点。共享世界用它驱动 LOD/队列，限额追赶。
        sched_t = self._processed_world_tick if self.shared else t
        self._tick_events = 0
        if self.shared:
            # 日界必须按 ambient 自己的配速推进，不能跟着玩家世界时钟跑：
            # 50 玩家会把一天压到不足 1 秒，若按世界日每步结算一次，等于给 NPC
            # 每天缴 144 倍生活费（会把钱包扣成负数），系统事件率也会被放大百倍。
            # 共享世界因此用 ambient tick 作为“NPC 日”：144 步 = 1 个 NPC 日。
            day = self.tick // TICKS_PER_DAY
        else:
            day = t // TICKS_PER_DAY
        if self._day_marker is None:
            self._day_marker = day
            if self.shared or t % TICKS_PER_DAY == 0:
                self._today_heat = Counter()
                self._day_start(t, day=day)
                self._replan_today = Counter()
        elif day != self._day_marker:
            old = self._day_marker
            if self.shared:
                # 记账：一个 NPC 日推进了多少世界 tick（EMA，下限 144）。
                world_now = int(self.writer.state.tick)
                delta = max(1, world_now - self._day_world_tick)
                self._world_ticks_per_npc_day = max(
                    float(TICKS_PER_DAY),
                    0.5 * self._world_ticks_per_npc_day + 0.5 * float(delta),
                )
                self._day_world_tick = world_now
            # 共享世界里结算点比世界日稀疏得多；用当前世界 tick 作事件时间戳
            # （事件 tick 仍单调），但日编号用 ambient 日，绝不在同一日重复结算。
            if self.shared:
                self._day_end(t, day=old)
            self._day_marker = day
            self._today_heat = Counter()
            self._day_start(t, day=day)
            self._replan_today = Counter()
        wake_budget = int(self.cfg.max_wakes_per_step) if self.shared else None
        intent_budget = int(self.cfg.max_intents_per_step) if self.shared else None
        enqueued = 0
        for wake in self.lod.advance_to(sched_t, max_wakes=wake_budget):
            if intent_budget is not None and enqueued >= intent_budget:
                # 按“实际事件数”再做一次预算：一次唤醒可能产出多个 Intent，
                # 只按 wake 数限流仍会超产。被截断的唤醒记 skipped_wakes。
                self.lod.skipped_wakes += 1
                continue
            self._enqueue(wake.intents, sched_t)
            enqueued += len(wake.intents)
        consumed = 0
        dropped = 0
        stale_before = sched_t - TICKS_PER_DAY if self.shared else None
        while self._queue and self._queue[0][0] <= sched_t:
            if stale_before is not None and self._queue[0][0] < stale_before:
                # 队列里超过一天没被消费的意图已经过时（任务状态/位置都变了），
                # 提交只会被拒并触发 replan 雪崩；直接丢弃并记账。
                if dropped >= 512:
                    break
                heapq.heappop(self._queue)
                self._dropped_stale += 1
                dropped += 1
                continue
            if self.shared and consumed >= int(self.cfg.max_intents_per_step):
                # 配额用完：剩余留在队列，下一调度步继续；防止玩家把世界时钟
                # 推得很快时 NPC 一次性补跑几千步造成写入尖峰。
                break
            _, _prio, _seq, kind, payload = heapq.heappop(self._queue)
            consumed += 1
            if kind == "intent":
                if self.shared and int(payload.tick) < t:
                    # 安全网：任何迟到的旧意图都归并到当前 tick，事件 tick 不倒退。
                    payload = replace(payload, tick=t)
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
        self._max_queue_backlog = max(self._max_queue_backlog, len(self._queue))
        if not self.shared and t % TICKS_PER_DAY == (TICKS_PER_DAY - 1):
            # 日末结算：t=143 对应第 0 日结束，与 headless 的 base+143 对齐。
            # 共享模式改由“日界变更”分支结算，避免玩家跳 tick 时漏结算或重复结算。
            self._day_end(t)
        # 世界时钟与事件日志同源：tick 不参与状态哈希，只用于玩家写操作校验。
        self.writer.state.tick = t
        self._write_progress(t)
        self._last_tick_at = self._now_iso()
        self._write_status("paused" if self.paused else "running", self._pause_reason)
        if self.cfg.summary_every_ticks > 0 and t % self.cfg.summary_every_ticks == 0:
            self._write_summary(t)

    def _maybe_replan(self, payload: Intent) -> None:
        if payload.source not in ("planner", "llm"):
            return
        if self._replan_today[payload.actor] >= 6:
            return
        self._replan_today[payload.actor] += 1
        base = self._processed_world_tick if self.shared else self.tick
        self._push(base + 1, 3, "replan", payload.actor)

    def _write_progress(self, t: int) -> None:
        t0 = self.clock()
        state_digest = state_hash(self.writer.state)
        t_hash = self.clock() - t0
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
        t1 = self.clock()
        self._progress_fh.flush()
        self._last_parts["progress_hash_ms"] = round(t_hash * 1000.0, 3)
        self._last_parts["progress_write_ms"] = round((t1 - t0 - t_hash) * 1000.0, 3)
        self._last_parts["progress_flush_ms"] = round((self.clock() - t1) * 1000.0, 3)

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
    def pause(self, source: str = "api", reason: str = "") -> None:
        if self.paused:
            return
        self.paused = True
        self._pause_reason = reason
        self._record_control("pause", source, {"reason": reason} if reason else None)
        self._log_line(
            f"[{stamp(self.tick)}] ambient_pause tick={self.tick} source={source}"
            + (f" reason={reason}" if reason else "")
        )
        self._write_status("paused", reason)

    def resume(self, source: str = "api") -> None:
        if not self.paused:
            return
        self.paused = False
        self._pause_reason = ""
        self._cpu_paused = False
        self._cpu_over_s = 0.0
        self._resume_ok_s = 0.0
        self._auto_resume_at = None
        self._anchor_tick = self.tick
        self._anchor_time = self.clock()
        self._record_control("resume", source)
        self._log_line(f"[{stamp(self.tick)}] ambient_resume tick={self.tick} source={source}")
        self._write_status("running")

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
            with self._guard():
                self._process_tick()
            if self._max_reached():
                break
        self._anchor_tick = self.tick
        self._anchor_time = self.clock()
        return self.tick

    def stop(self, source: str = "api") -> None:
        self.stopped = True
        self._stop_reason = source
        self._record_control("stop", source)
        self._write_status("stopped", source)

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
        """超过阈值先补睡降速；持续超限则暂停自走，而不是拖死服务端。"""
        if tick_wall <= 0:
            return
        self._wall_total += tick_wall
        self._cpu_total += max(0.0, tick_cpu)
        # 按时间窗评估，避免配速空闲把「忙时 CPU 比例」误算成整进程占用。
        self._guard_wall_acc += tick_wall
        self._guard_cpu_acc += max(0.0, tick_cpu)
        if self._guard_wall_acc < max(0.01, float(self.cfg.cpu_sample_s)):
            return
        ratio = self._guard_cpu_acc / self._guard_wall_acc
        limit = float(self.cfg.max_cpu_ratio)
        elapsed = self._guard_wall_acc
        if limit > 0 and ratio > limit:
            need = max(0.0, self._guard_cpu_acc / limit - self._guard_wall_acc)
            if need > 0:
                self.sleeper(need)
                self._cpu_guard_sleeps += 1
                self._cpu_guard_slept_s += need
                self._wall_total += need
                elapsed += need
            self._cpu_over_s += elapsed
            if (
                not self.paused
                and not self.stopped
                and self.cfg.cpu_pause_after_s > 0
                and self._cpu_over_s >= float(self.cfg.cpu_pause_after_s)
            ):
                self._cpu_paused = True
                self._cpu_pauses += 1
                self._log_line(
                    f"[{stamp(self.tick)}] cpu_guard_pause ratio={ratio:.3f} "
                    f"limit={limit:.2f} over_s={self._cpu_over_s:.2f}"
                )
                self.pause(source="cpu_guard", reason="cpu_ratio_over_limit")
        else:
            self._cpu_over_s = 0.0
        self._guard_wall_acc = 0.0
        self._guard_cpu_acc = 0.0

    def _cpu_pause_watch(self, wall: float, cpu: float) -> None:
        """暂停期间低频采样；低于恢复线并持续足够久后自动恢复。"""
        if not self._cpu_paused or wall <= 0:
            return
        self._resume_wall_acc += wall
        self._resume_cpu_acc += max(0.0, cpu)
        if self._resume_wall_acc < max(0.01, float(self.cfg.cpu_sample_s)):
            return
        ratio = self._resume_cpu_acc / self._resume_wall_acc
        if ratio <= float(self.cfg.cpu_resume_below_ratio):
            self._resume_ok_s += self._resume_wall_acc
            if self._resume_ok_s >= float(self.cfg.cpu_resume_after_s):
                self._cpu_paused = False
                self._cpu_resumes += 1
                self._log_line(
                    f"[{stamp(self.tick)}] cpu_guard_resume ratio={ratio:.3f} "
                    f"limit={float(self.cfg.max_cpu_ratio):.2f} after_s={self._resume_ok_s:.2f}"
                )
                self._resume_ok_s = 0.0
                self.resume(source="cpu_guard")
        else:
            self._resume_ok_s = 0.0
        self._resume_wall_acc = 0.0
        self._resume_cpu_acc = 0.0

    def _avg_cpu_ratio(self) -> float:
        if self._wall_total <= 0:
            return 0.0
        return round(self._cpu_total / self._wall_total, 4)

    def _observe_step_ms(self, ms: float) -> None:
        """记录调度步实际墙钟耗时；滚动窗口 512 步。"""
        self._step_ms.append(float(ms))
        if ms > self._step_ms_max:
            self._step_ms_max = float(ms)

    def _observe_step_split(self, lock_wait_ms: float, in_lock_ms: float,
                            in_lock_cpu_ms: float, epoch: float) -> None:
        """归因：一步拆成锁等待 / 锁内执行，并落 ambient_steps.jsonl。

        30s 窗口里的写 p95 若变慢，可以直接对上这一步到底是“等锁”还是
        “锁内自走 + 落盘”造成的。探针缓冲 30 行后 flush，避免自身成为停顿源。
        """
        self._lock_wait_ms.append(float(lock_wait_ms))
        self._in_lock_ms.append(float(in_lock_ms))
        self._in_lock_cpu_ms.append(float(in_lock_cpu_ms))
        if lock_wait_ms > self._lock_wait_ms_max:
            self._lock_wait_ms_max = float(lock_wait_ms)
        if in_lock_ms > self._in_lock_ms_max:
            self._in_lock_ms_max = float(in_lock_ms)
        row = {
            "epoch": round(epoch, 3),
            "npc_tick": int(self.tick),
            "world_tick": int(self.writer.state.tick),
            "step_ms": round(lock_wait_ms + in_lock_ms, 3),
            "lock_wait_ms": round(float(lock_wait_ms), 3),
            "in_lock_ms": round(float(in_lock_ms), 3),
            # 锁内墙钟与锁内 CPU 之差 = 被 OS 抢占/在 GIL/IO 上让出（含 GC 停顿）。
            # 两者接近说明确实是代码在算；差得远说明不是代码慢，而是被调度掉。
            "in_lock_cpu_ms": round(float(in_lock_cpu_ms), 3),
            "parts": dict(self._last_parts),
            "shared": bool(self.shared),
        }
        self._steps_fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._steps_pending += 1
        if self._steps_pending >= 30:
            try:
                self._steps_fh.flush()
            finally:
                self._steps_pending = 0

    def _p95(self, values: deque[float]) -> float:
        if not values:
            return 0.0
        vals = sorted(values)
        idx = max(0, int(len(vals) * 0.95) - 1)
        return round(vals[idx], 2)

    def _step_ms_p95(self) -> float:
        return self._p95(self._step_ms)

    def run_ticks(self, n: int) -> int:
        """不配速连跑 n 个 tick（测试/验收用），仍受 CPU 护栏约束。"""
        for _ in range(max(0, int(n))):
            self._sync_shared_tick()
            self._tick_with_guard()
            if self._max_reached():
                break
            if self._cpu_paused:
                # 快跑模式没有真实墙钟可恢复；交由 start()/外部恢复。
                break
        return self.tick

    def _tick_with_guard(self) -> None:
        t0_wall = self.clock()
        t0_cpu = self.cpu_clock()
        t0_epoch = time.clock_gettime(time.CLOCK_REALTIME)
        t_enter = None
        t_in_end = None
        c_enter = None
        try:
            # 共享模式下整 tick（含事件追加/提交）都在 API 的锁内，
            # 与玩家写路径串行，保证单写者、单事务、seq 连续。
            with self._guard():
                t_enter = self.clock()
                c_enter = self.cpu_clock()
                self._process_tick()
                t_in_end = self.clock()
                c_in_end = self.cpu_clock()
        except Exception as exc:  # 不静默：落盘后继续抛出
            self._write_error("tick", exc)
            raise
        t_exit = self.clock()
        wall = t_exit - t0_wall
        lock_wait = (t_enter - t0_wall) if t_enter is not None else 0.0
        in_lock = (t_in_end - t_enter) if (t_enter is not None and t_in_end is not None) else 0.0
        in_lock_cpu = (c_in_end - c_enter) if (c_enter is not None) else 0.0
        self._observe_step_ms(wall * 1000.0)
        self._observe_step_split(lock_wait * 1000.0, in_lock * 1000.0, in_lock_cpu * 1000.0, t0_epoch)
        self._cpu_guard(wall, self.cpu_clock() - t0_cpu)

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
            # 玩家可能在同一把锁里刚推进了世界时钟：先对齐，再决定是否自走。
            self._sync_shared_tick()
            # 心跳与 tick 解耦：idle 分支也刷新状态文件，health 才不会把活进程判成 stopped。
            self._maybe_status_heartbeat()
            now = self.clock()
            if now >= self._next_parent_check:
                self._next_parent_check = now + 0.5
                if self._parent_gone():
                    self._log_line(
                        f"[{stamp(self.tick)}] ambient_parent_gone pid={self.cfg.parent_pid}"
                    )
                    self.stop(source="parent_gone")
                    break
            if deadline is not None and now >= deadline:
                break
            if self._max_reached():
                break
            if self.paused:
                if self._cpu_paused:
                    w0 = self.clock()
                    c0 = self.cpu_clock()
                    self.sleeper(0.05)
                    self._cpu_pause_watch(self.clock() - w0, self.cpu_clock() - c0)
                    self._maybe_status_heartbeat()
                elif self._auto_resume_at is not None and now >= self._auto_resume_at:
                    self.resume(source="auto_resume")
                else:
                    self.sleeper(0.05)
                    self._maybe_status_heartbeat()
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
        with self._guard():
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
        with self._guard():
            ev = self.writer.submit(
                Intent(
                    actor=pid,
                    verb="player_leave",
                    args={"player_id": pid},
                    tick=int(self.writer.state.tick) if self.shared else self.tick,
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
            "cpu_ratio_limit": float(self.cfg.max_cpu_ratio),
            "cpu_guard_sleeps": self._cpu_guard_sleeps,
            "cpu_guard_slept_s": round(self._cpu_guard_slept_s, 3),
            "step_ms_max": round(self._step_ms_max, 2),
            "step_ms_p95": self._step_ms_p95(),
            "step_ms_samples": len(self._step_ms),
            "lock_wait_ms_max": round(self._lock_wait_ms_max, 2),
            "lock_wait_ms_p95": self._p95(self._lock_wait_ms),
            "in_lock_ms_max": round(self._in_lock_ms_max, 2),
            "in_lock_ms_p95": self._p95(self._in_lock_ms),
            "store_ck": self.store.ck_stats() if hasattr(self.store, "ck_stats") else {},
            "cpu_pauses": self._cpu_pauses,
            "cpu_resumes": self._cpu_resumes,
            "parent_pid": self.cfg.parent_pid,
            "status_file": str(self.status_path),
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
        self._write_status(
            "stopped", "error" if self._error else (self._stop_reason or "finished")
        )
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
        self._write_status("stopped", f"error:{phase}")
        self._log_line(
            f"[{stamp(self.tick)}] AMBIENT_ERROR phase={phase} {payload['error']} (see ambient_error.json)"
        )

    def _close(self) -> None:
        for fh in (self._audit_fh, self._progress_fh, self._summary_fh, self._control_fh, self._steps_fh):
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
        if self._owns_store:
            try:
                self.store.close()
            except Exception:
                pass
