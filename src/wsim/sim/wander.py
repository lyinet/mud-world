"""NPC 游走策略（P4b）。

只做一件事：在 LOD 唤醒点上，为到期且合格的 NPC 生成一个 `move` intent，
走向当前房间的一个可用出口。没有新的 verb/effect/timer：

- 决策随机：`PRNG(seed, tick, f"npc_wander:{npc_id}")`，只依赖 tick 与 NPC id；
- 落地随机：仍由 Writer 用 `PRNG(seed, seq, f"action:{verb}")`（本模块不碰 seq）；
- 位置变更：仍由现有 `move` action → `set_location` effect 完成。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.models import Intent
from ..core.rng import PRNG

# 内容包里已经有这些标记，游走直接复用，不另维护禁用名单。
INELIGIBLE_MARKERS = ("is_master",)


@dataclass(frozen=True)
class WanderPolicy:
    """默认关闭；只有世界配置显式打开时才生成游走 intent。"""

    enabled: bool = False
    step_limit: int = 1

    def _meta(self, content: Any, npc: str) -> dict[str, Any]:
        for row in getattr(content, "npcs", []) or []:
            if str(row.get("id")) == npc:
                return row
        return {}

    def _is_shopkeeper(self, content: Any, npc: str) -> bool:
        return npc in (getattr(content, "shop_by_npc", None) or {})

    def _is_quest_npc(self, content: Any, npc: str) -> bool:
        return npc in (getattr(content, "quest_npc_refs", None) or set())

    def eligible(self, npc: str, state: Any, content: Any) -> bool:
        """资格过滤：存活、有出口、不是师父/店主/任务 NPC。"""
        ent = state.entities.get(npc) if state is not None else None
        # 参考快照里的 `unconcious` 不行动；死亡/转世同样不行动。
        if ent is None or ent.status != "alive":
            return False
        loc = state.locations.get(ent.location)
        if loc is None:
            return False
        if not [x for x in (loc.get("exits") or []) if str(x) in state.locations]:
            return False
        meta = self._meta(content, npc)
        if any(bool(meta.get(key)) for key in INELIGIBLE_MARKERS):
            return False
        if self._is_shopkeeper(content, npc) or self._is_quest_npc(content, npc):
            return False
        return True

    def intent_for(
        self, *, seed: int, tick: int, npc: str, state: Any, content: Any
    ) -> Intent | None:
        """一个唤醒最多一步：返回唯一 `move` intent，不合格返回 None。"""
        if not self.enabled or int(self.step_limit) <= 0:
            return None
        if not self.eligible(npc, state, content):
            return None
        ent = state.entities[npc]
        exits = sorted(
            str(x) for x in (state.locations[ent.location].get("exits") or [])
            if str(x) in state.locations
        )
        rng = PRNG(seed, tick, f"npc_wander:{npc}")
        to = rng.choice(exits)
        return Intent(
            actor=npc,
            verb="move",
            args={"to": to},
            tick=tick,
            source="npc",
            reason=f"游走：{ent.location} -> {to}",
        )
