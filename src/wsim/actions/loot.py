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
            weight = int((getattr(self.content, "items", {}) or {}).get(name, {}).get("weight", 0) or 0)
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
