"""LOD 调度层（P2）。

核心约束：
- tick 只推进「到期事件」：调度器维护一个最小堆，`advance_to()` 只弹出到期项，
  复杂度 O(到期事件数 · log N)，绝不每 tick 遍历全部 NPC。
- Reasoner 接口 `decide(npc_state, world_view) -> Intent[]`；MockReasoner v1 在
  `sim/intent.py` 实现，真实模型以后替换实现即可。
- 支持动态升降档：玩家靠近 / 关系活跃升温 / 长期无交互降档。档位变化会重排
  该 NPC 的下一次唤醒（旧堆项用 generation 标记作废）。
- 输出每个 NPC 当前 LOD 档位、累计唤醒次数与档位变化日志，供成本模型验证。
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from ..core.canonical import hash_obj
from ..core.models import Intent
from ..core.rng import PRNG
from .intent import MockReasoner, Reasoner  # noqa: F401  统一从 sim.intent 导出

TIER_ORDER = ("silent", "background", "active", "spotlight")


class LODConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LODTier:
    name: str
    wake_interval_ticks: int
    cost_weight: float
    share: float


@dataclass(frozen=True)
class LODConfig:
    version: str
    tiers: dict[str, LODTier]

    def shares(self) -> list[tuple[str, float]]:
        return [(name, self.tiers[name].share) for name in TIER_ORDER]


def load_lod_config(path: Path) -> LODConfig:
    if yaml is None:
        raise LODConfigError("PyYAML 未安装")
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    tiers_raw = raw.get("tiers") or {}
    errors: list[str] = []
    tiers: dict[str, LODTier] = {}
    for name in TIER_ORDER:
        item = tiers_raw.get(name)
        if not isinstance(item, dict):
            errors.append(f"缺少档位 {name}")
            continue
        try:
            interval = int(item["wake_interval_ticks"])
            cost = float(item["cost_weight"])
            share = float(item["share"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{name} 缺少合法 wake_interval_ticks/cost_weight/share")
            continue
        if interval <= 0:
            errors.append(f"{name} wake_interval_ticks 必须 > 0")
        if cost < 0:
            errors.append(f"{name} cost_weight 不能为负")
        if share < 0:
            errors.append(f"{name} share 不能为负")
        tiers[name] = LODTier(name=name, wake_interval_ticks=interval, cost_weight=cost, share=share)
    if sum(t.share for t in tiers.values()) <= 0:
        errors.append("share 总和必须 > 0")
    if errors:
        raise LODConfigError("; ".join(errors))
    return LODConfig(version=str(raw.get("version", "")), tiers=tiers)


def _unit(key: str, seed: int, purpose: str) -> float:
    h = hash_obj({"key": key, "seed": seed, "purpose": purpose})
    return int(h[:8], 16) / float(1 << 32)


def _assign_tier(npc_id: str, seed: int, config: LODConfig) -> str:
    u = _unit(npc_id, seed, "lod_tier")
    total = sum(t.share for t in config.tiers.values())
    acc = 0.0
    for name in TIER_ORDER:
        acc += config.tiers[name].share / total
        if u < acc:
            return name
    return TIER_ORDER[-1]


def _rank(tier: str) -> int:
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else 0


def promote(tier: str) -> str:
    return TIER_ORDER[min(len(TIER_ORDER) - 1, _rank(tier) + 1)]


def demote(tier: str) -> str:
    return TIER_ORDER[max(0, _rank(tier) - 1)]


@dataclass
class WakeResult:
    tick: int
    npc_id: str
    lod: str
    intents: list[Intent] = field(default_factory=list)


class LODScheduler:
    """事件驱动 LOD 调度器。

    `advance_to(target_tick)` 从堆中弹出所有 tick <= target 的唤醒项；未到期 NPC
    不参与遍历。外部可通过 `decide_calls` / `state_provider_calls` / `process_count`
    三个独立计数器核对“没有全量扫描”，而不是靠调度器自证。
    """

    def __init__(
        self,
        npc_ids: list[str],
        config: LODConfig,
        reasoner: Reasoner,
        seed: int = 42,
        start_tick: int = 0,
        state_provider: Callable[[str], dict[str, Any]] | None = None,
        world_view_provider: Callable[[int], dict[str, Any]] | None = None,
    ):
        self.config = config
        self.reasoner = reasoner
        self.seed = seed
        self.now = start_tick
        self.started_at = start_tick
        self._heap: list[tuple[int, str, int]] = []
        self._gen: dict[str, int] = {}
        self.lod: dict[str, str] = {}
        self.wake_counts: dict[str, int] = {npc: 0 for npc in npc_ids}
        self.process_count = 0
        # 因单步配额被跳过、未调用 reasoner 的到期唤醒数（共享世界追赶时记账用）。
        self.skipped_wakes = 0
        # 外部可核对的计数器：decide 调用数 / state_provider 调用数 / 分档唤醒数。
        self.decide_calls = 0
        self.state_provider_calls = 0
        self.decide_by_tier: dict[str, int] = {name: 0 for name in TIER_ORDER}
        self.tier_changes: list[dict[str, Any]] = []
        self._state_provider = state_provider or (lambda npc: {"id": npc})
        self._world_view_provider = world_view_provider or (lambda tick: {"tick": tick})
        for npc in sorted(npc_ids):
            tier = _assign_tier(npc, seed, config)
            self.lod[npc] = tier
            self._gen[npc] = 0
            interval = config.tiers[tier].wake_interval_ticks
            phase = int(_unit(npc, seed, "lod_phase") * interval)
            heapq.heappush(self._heap, (start_tick + max(1, phase), npc, 0))

    def advance_to(self, target_tick: int, max_wakes: int | None = None) -> list[WakeResult]:
        """弹出 tick <= target_tick 的唤醒项。

        `max_wakes` 给定时，单次调用最多真正唤醒/决策这么多个 NPC；其余到期项只
        跳过并记账（不调用 reasoner），把下一次唤醒按正常间隔推回堆。堆的 now 仍
        推进到 target_tick，因此世界时间不会落后；被跳过的只是事件产出。
        """
        if target_tick < self.now:
            raise ValueError("不能回拨 tick")
        wakes: list[WakeResult] = []
        while self._heap and self._heap[0][0] <= target_tick:
            tick, npc, gen = heapq.heappop(self._heap)
            if gen != self._gen.get(npc, 0):
                continue  # 档位变化后旧的唤醒项作废
            if max_wakes is not None and len(wakes) >= max_wakes:
                self.skipped_wakes += 1
                tier = self.lod[npc]
                interval = self.config.tiers[tier].wake_interval_ticks
                jitter = PRNG(
                    self.seed, self.wake_counts.get(npc, 0) + 1, f"lod_jitter:{npc}"
                ).randint(0, max(1, interval // 4))
                heapq.heappush(self._heap, (tick + interval + jitter, npc, gen))
                continue
            result = self._wake(npc, tick)
            wakes.append(result)
            self.process_count += 1
        self.now = target_tick
        return wakes

    def advance_to_batched(self, target_tick: int, batch_size: int = 8) -> list[WakeResult]:
        """批量版 advance_to：先收集到期唤醒，再按批调用 reasoner.decide_batch。

        不改变事件顺序与 jitter 计算；只把 N 个独立模型请求合并成少数几批。
        """
        if target_tick < self.now:
            raise ValueError("不能回拨 tick")
        if not hasattr(self.reasoner, "decide_batch") or batch_size <= 1:
            return self.advance_to(target_tick)
        due: list[dict[str, Any]] = []
        while self._heap and self._heap[0][0] <= target_tick:
            tick, npc, gen = heapq.heappop(self._heap)
            if gen != self._gen.get(npc, 0):
                continue
            self.wake_counts[npc] = self.wake_counts.get(npc, 0) + 1
            tier = self.lod[npc]
            interval = self.config.tiers[tier].wake_interval_ticks
            view = dict(self._world_view_provider(tick))
            view["tier"] = tier
            view["interval"] = interval
            self.state_provider_calls += 1
            self.decide_calls += 1
            self.decide_by_tier[tier] = self.decide_by_tier.get(tier, 0) + 1
            jitter = PRNG(self.seed, self.wake_counts[npc], f"lod_jitter:{npc}").randint(
                0, max(1, interval // 4)
            )
            heapq.heappush(self._heap, (tick + interval + jitter, npc, self._gen.get(npc, 0)))
            due.append({"npc": npc, "tick": tick, "tier": tier, "view": view})
        wakes: list[WakeResult] = []
        for start in range(0, len(due), max(1, batch_size)):
            chunk = due[start:start + max(1, batch_size)]
            items = [
                {"npc_state": self._state_provider(d["npc"]), "world_view": d["view"], "timing": "realtime"}
                for d in chunk
            ]
            results = self.reasoner.decide_batch(items)
            for d, intents in zip(chunk, results):
                wakes.append(WakeResult(tick=d["tick"], npc_id=d["npc"], lod=d["tier"], intents=intents))
        self.process_count += len(wakes)
        self.now = target_tick
        return wakes

    def _wake(self, npc: str, tick: int) -> WakeResult:
        self.wake_counts[npc] = self.wake_counts.get(npc, 0) + 1
        tier = self.lod[npc]
        interval = self.config.tiers[tier].wake_interval_ticks
        view = dict(self._world_view_provider(tick))
        view["tier"] = tier
        view["interval"] = interval
        self.state_provider_calls += 1
        intents = self.reasoner.decide(self._state_provider(npc), view)
        self.decide_calls += 1
        self.decide_by_tier[tier] = self.decide_by_tier.get(tier, 0) + 1
        jitter = PRNG(self.seed, self.wake_counts[npc], f"lod_jitter:{npc}").randint(0, max(1, interval // 4))
        heapq.heappush(self._heap, (tick + interval + jitter, npc, self._gen.get(npc, 0)))
        return WakeResult(tick=tick, npc_id=npc, lod=tier, intents=intents)

    def set_tier(self, npc: str, tier: str, now: int | None = None, reason: str = "") -> bool:
        """动态升降档；变化后立刻按新档位重排下一次唤醒。"""
        if tier not in TIER_ORDER:
            raise ValueError(f"unknown LOD tier: {tier}")
        old = self.lod.get(npc)
        if old == tier:
            return False
        at = self.now if now is None else now
        self.lod[npc] = tier
        self._gen[npc] = self._gen.get(npc, 0) + 1
        interval = self.config.tiers[tier].wake_interval_ticks
        phase = int(_unit(npc, self.seed, "lod_phase") * interval)
        heapq.heappush(self._heap, (at + max(1, phase), npc, self._gen[npc]))
        change = {
            "tick": at,
            "npc": npc,
            "from": old,
            "to": tier,
            "reason": reason or "manual",
        }
        self.tier_changes.append(change)
        return True

    def update_tiers(
        self,
        now: int,
        heat: dict[str, int] | None = None,
        idle_days: dict[str, int] | None = None,
        nearby: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """每日结算动态档位。

        - 玩家靠近 → spotlight（headless 无玩家时 nearby 为空，接口保留）；
        - 关系活跃（trade/apprentice/marry/join_sect 等 >= 3 次/日）→ 升一档；
        - 连续 >= 3 天无交互 → 降一档；>= 7 天 → silent。
        """
        heat = heat or {}
        idle_days = idle_days or {}
        nearby = nearby or set()
        before = len(self.tier_changes)
        for npc in sorted(self.lod):
            cur = self.lod[npc]
            target = cur
            reason = ""
            if npc in nearby:
                target, reason = "spotlight", "player_nearby"
            elif heat.get(npc, 0) >= 3:
                target, reason = promote(cur), "relation_active"
            elif idle_days.get(npc, 0) >= 7:
                target, reason = "silent", "long_idle"
            elif idle_days.get(npc, 0) >= 3:
                target, reason = demote(cur), "idle"
            if target != cur:
                self.set_tier(npc, target, now=now, reason=reason)
        return self.tier_changes[before:]

    def stats(self) -> dict[str, dict[str, Any]]:
        return {
            npc: {
                "lod": self.lod[npc],
                "wakes": self.wake_counts.get(npc, 0),
                "interval": self.config.tiers[self.lod[npc]].wake_interval_ticks,
                "cost_weight": self.config.tiers[self.lod[npc]].cost_weight,
            }
            for npc in sorted(self.lod)
        }

    def summary(self) -> dict[str, Any]:
        by_tier = {name: 0 for name in TIER_ORDER}
        for tier in self.lod.values():
            by_tier[tier] += 1
        return {
            "version": self.config.version,
            "now": self.now,
            "npc_count": len(self.lod),
            "processed_wakes": self.process_count,
            "skipped_wakes": self.skipped_wakes,
            "decide_calls": self.decide_calls,
            "state_provider_calls": self.state_provider_calls,
            "decide_by_tier": dict(self.decide_by_tier),
            "by_tier": by_tier,
            "tier_changes": len(self.tier_changes),
            "stats": self.stats(),
        }
