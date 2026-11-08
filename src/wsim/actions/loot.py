"""取物动作：`loot` 搜刮最近击杀掉落，`get` 从房间地面拿东西。

契约：
- 输入只有 Intent，输出只有 Effect（`loot_take`），不直接改 state；
- 地面聚合由 `loot_create` 写入 `WorldState.ground`；
- 击杀来源由 `loot_create` 的 `source="kill"` 写入 `WorldState.ground_sources`，
  `loot` 只取当前房间**最近一次击杀**的条目，不碰其他地面物品；
- `get all` 清空当前房间地面；`get <item> [qty]` 精确取；
- 没有击杀来源时 `loot` 给 `loot_no_corpse`，不静默成功、不清地面。
"""
from __future__ import annotations

from typing import Any

from ..core.models import Effect, Intent
from ..world.state import WorldState
from .common import memory, overweight_reason
from .equipment import item_weight
from .registry import Action


def _player_id(intent: Intent) -> str:
    pid = str(intent.args.get("player_id") or "").strip()
    if pid.startswith("player:"):
        return pid
    actor = str(intent.actor or "").strip()
    return actor if actor.startswith("player:") else ""


def _ground_location(intent: Intent, state: WorldState, pid: str) -> str:
    at = str(intent.args.get("at") or "").strip()
    if at:
        return at
    ent = state.entities.get(pid)
    return ent.location if ent is not None else ""


def _scope(intent: Intent) -> str:
    """显式 scope 优先；裸 `loot` 只搜刮击杀掉落，`get` 走地面全量/精确取。"""
    scope = str(intent.args.get("scope") or "").strip().lower()
    if scope in ("kill", "all"):
        return scope
    return "all" if str(intent.verb or "").strip().lower() == "get" else "kill"


def _latest_kill(state: WorldState, loc: str) -> dict[str, Any] | None:
    """当前房间最近一次击杀的来源条目；同 tick 取列表中靠后的一条。

    已搜刮空的条目会作为墓碑保留，因此「最近一次击杀」不会退回到上一具尸体。
    """
    picks = [
        (idx, entry)
        for idx, entry in enumerate(state.ground_sources.get(loc) or [])
        if str(entry.get("source") or "") == "kill"
    ]
    if not picks:
        return None
    return max(picks, key=lambda pair: (int(pair[1].get("tick") or 0), pair[0]))[1]


class LootAction(Action):
    verb = "loot"
    aliases = ("get",)

    def __init__(self, content: Any = None) -> None:
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["player_required"]
        ent = state.entities.get(pid)
        if ent is None:
            return ["player_not_entered"]
        if ent.status == "dead":
            return ["player_dead"]
        target = str(intent.args.get("target") or "")
        if target.startswith("player:"):
            prof = state.players.get(target)
            other = state.entities.get(target)
            if prof is None or not prof.online or (other is not None and other.presence != "online"):
                return ["target_offline"]
            return ["target_is_player"]
        loc = _ground_location(intent, state, pid)
        if loc != ent.location or loc not in state.locations:
            return ["not_here"]
        raw_qty = intent.args.get("qty")
        if raw_qty is not None and (isinstance(raw_qty, bool) or not isinstance(raw_qty, int) or raw_qty <= 0):
            return ["bad_qty"]
        item = str(intent.args.get("item") or "").strip()
        if _scope(intent) == "kill":
            entry = _latest_kill(state, loc)
            if entry is None:
                return ["loot_no_corpse"]
            source_items = {
                str(name): int(qty)
                for name, qty in (entry.get("items") or {}).items()
                if int(qty) > 0
            }
            if not source_items:
                return ["loot_already_looted"]
            if item:
                available = int(source_items.get(item, 0))
                if available <= 0:
                    return ["loot_not_found"]
                if isinstance(raw_qty, int) and raw_qty > available:
                    return ["loot_not_found"]
                targets = [(item, int(raw_qty or available))]
            else:
                targets = [(name, int(source_items[name])) for name in sorted(source_items)]
        else:
            bucket = state.ground.get(loc) or {}
            if not bucket:
                return ["loot_not_found"]
            if item:
                available = int(bucket.get(item, 0))
                if available <= 0:
                    return ["loot_not_found"]
                if isinstance(raw_qty, int) and raw_qty > available:
                    return ["loot_not_found"]
                targets = [(item, int(raw_qty or available))]
            else:
                targets = [(name, int(bucket[name])) for name in sorted(bucket)]
        added = sum(item_weight(self.content, name) * qty for name, qty in targets)
        reason = overweight_reason(self.content, ent, added)
        if reason:
            return [reason]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        loc = _ground_location(intent, state, pid)
        item = str(intent.args.get("item") or "").strip()
        scope = _scope(intent)
        source_key = ""
        if scope == "kill":
            entry = _latest_kill(state, loc)
            if entry is None:
                return []
            source_key = str(entry.get("key") or "")
            source_items = {
                str(name): int(qty)
                for name, qty in (entry.get("items") or {}).items()
                if int(qty) > 0
            }
            if item:
                qty = int(intent.args.get("qty") or source_items.get(item, 0))
                targets = [(item, qty)]
            else:
                targets = [(name, int(source_items[name])) for name in sorted(source_items)]
        else:
            bucket = dict(state.ground.get(loc) or {})
            if item:
                qty = int(intent.args.get("qty") or bucket.get(item, 0))
                targets = [(item, qty)]
            else:
                targets = [(name, int(bucket[name])) for name in sorted(bucket)]

        effects: list[Effect] = []
        for name, qty in targets:
            if qty <= 0:
                continue
            weight = item_weight(self.content, name)
            data: dict[str, Any] = {
                "entity": pid,
                "location": loc,
                "item": name,
                "qty": int(qty),
                "weight": weight * int(qty),
            }
            if source_key:
                data["source"] = source_key
            effects.append(Effect("loot_take", data))
        return effects


class DropAction(Action):
    """把背包物品放到当前房间地面；与 loot_create 共用 WorldState.ground。"""

    verb = "drop"

    def __init__(self, content: Any = None) -> None:
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["player_required"]
        ent = state.entities.get(pid)
        if ent is None:
            return ["player_not_entered"]
        if ent.status == "dead":
            return ["player_dead"]
        item = str(intent.args.get("item") or "").strip()
        if not item:
            return ["item_missing"]
        qty = intent.args.get("qty", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1 or qty > 20:
            return ["bad_qty"]
        if int(ent.inventory.get(item, 0)) < qty:
            return ["item_missing"]
        if item in (ent.equipped or {}).values():
            return ["item_equipped"]
        if not ent.location or ent.location not in state.locations:
            return ["bad_location"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        ent = state.entities.get(pid)
        item = str(intent.args.get("item") or "").strip()
        qty = int(intent.args.get("qty") or 1)
        name = str(((getattr(self.content, "items", {}) or {}).get(item) or {}).get("name") or item)
        return [
            Effect("item_consume", {"owner": pid, "item": item, "qty": qty}),
            Effect(
                "loot_create",
                {"location": ent.location, "item": item, "qty": qty, "reason": "drop"},
            ),
            Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": intent.tick}),
            *memory(state, pid, intent.tick, f"丢下了{name}x{qty}", 0.05),
        ]
