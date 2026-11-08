"""取物动作：`loot` / `get` 从当前房间地面拿东西。

契约：
- 输入只有 Intent，输出只有 Effect（`loot_take`），不直接改 state；
- 地面数据由 `loot_create` 写入 `WorldState.ground`，按房间聚合；
- 未指定物品时按 item id 排序全部取走，同 seed 的事件顺序确定；
- 地面没有东西、或指定物品不在本房间，preconditions 给 `loot_not_found`。
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
        bucket = state.ground.get(loc) or {}
        if not bucket:
            return ["loot_not_found"]
        item = str(intent.args.get("item") or "").strip()
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
        bucket = dict(state.ground.get(loc) or {})
        item = str(intent.args.get("item") or "").strip()
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
            effects.append(
                Effect(
                    "loot_take",
                    {
                        "entity": pid,
                        "location": loc,
                        "item": name,
                        "qty": int(qty),
                        "weight": weight * int(qty),
                    },
                )
            )
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
