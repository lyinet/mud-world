"""成长线与生死：bai / learn / practice / climb / jump / revive。

语义逐条对齐原版 LPC：
- cmds/skill/apprentice.lpc（bai）：目标必须是在场、活着的 F_MASTER。
- cmds/skill/learn.lpc：`learn <师父> <技能> <次数>`，必须是本人师父，
  潜能 (potential-learned_points) 与精力门槛按源码计算，improve_skill 用
  (level+1)^2 阈值。
- cmds/skill/practice.lpc：`practice <技能> <次数>`，练习公式
  random((base^2)/(skill+1)/5)+1+practice_bonus。
- d/shiliang/gate.lpc：climb tree 需 dodge>=50。
- d/shiliang/tingzi.lpc：jump 木桩 dodge>=100 上小岛，否则落湖里。
- feature/damage.lpc die()/killer_reward() 的死亡惩罚。
"""
from __future__ import annotations

from typing import Any

from ..core.models import Effect, Intent, Reject
from ..world.state import Entity, WorldState
from .common import arg_str
from .registry import Action

# 鬼门关是原版 /d/death/gate；xkx 内容切片没有收录该房间，
# 由 session 在 xkx 世界启动时作为虚拟地点注入 state.locations。
DEATH_GATE = "/d/death/gate"

# 原版 practice_skill 各技能的通用体力门槛（anran-zhang/anying 等均为 30）。
PRACTICE_STAMINA_COST = 30


def _xkx_master(meta: dict[str, Any] | None) -> bool:
    return bool(meta and meta.get("is_master"))


def _content_npc(content, eid: str) -> dict[str, Any] | None:
    for raw in getattr(content, "npcs", []) or []:
        if str(raw.get("id") or "") == eid:
            return raw
    return None


def _npcs_in_room(state: WorldState, loc: str, exclude: str) -> list[Entity]:
    return [
        ent
        for ent in state.entities.values()
        if ent.location == loc and ent.id != exclude and not str(ent.id).startswith("player:")
    ]


def _match_entity(entities: list[Entity], content, needle: str) -> list[Entity]:
    if not needle:
        return []
    want = needle.strip()
    exact = [ent for ent in entities if ent.name == want or ent.id == want or ent.id.rsplit("/", 1)[-1] == want]
    if exact:
        return exact
    prefix = [
        ent
        for ent in entities
        if ent.name.startswith(want) or ent.id.startswith(want) or ent.id.rsplit("/", 1)[-1].startswith(want)
    ]
    if prefix:
        return prefix
    by_cn_name = []
    for ent in entities:
        meta = _content_npc(content, ent.id)
        if meta and str(meta.get("name") or "").startswith(want):
            by_cn_name.append(ent)
    return by_cn_name


def _resolve_present_master(
    state: WorldState, content, player: Entity, needle: str
) -> Entity:
    pool = _npcs_in_room(state, player.location, player.id)
    matches = _match_entity(pool, content, needle)
    if not matches:
        raise Reject("master_not_here")
    if len(matches) > 1:
        raise Reject("master_ambiguous")
    target = matches[0]
    if target.status != "alive":
        raise Reject("master_dead")
    meta = _content_npc(content, target.id)
    if not _xkx_master(meta):
        raise Reject("not_a_master")
    return target


class BaiAction(Action):
    verb = "bai"
    aliases = ("apprentice",)

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        player = state.entities.get(intent.actor)
        if player is None:
            raise Reject("unknown_actor")
        if player.status != "alive":
            raise Reject("actor_not_alive")
        if player.location == DEATH_GATE:
            raise Reject("in_ghost_gate")
        needle = arg_str(intent, "target")
        assert needle is not None
        target = _resolve_present_master(state, self.content, player, needle)
        if target.id == player.id:
            raise Reject("self_target")
        if player.master:
            if player.master == target.id:
                raise Reject("already_apprentice")
            raise Reject("already_has_master")
        if player.faction:
            raise Reject("already_in_sect")
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        player = state.entities[intent.actor]
        target = _resolve_present_master(state, self.content, player, str(intent.args["target"]))
        meta = _content_npc(self.content, target.id) or {}
        sect_id = str(meta.get("sect") or "")
        effects: list[Effect] = [
            Effect("add_disciple", {"master": target.id, "disciple": player.id}),
            Effect("set_field", {"entity": player.id, "field": "master", "value": target.id}),
        ]
        if sect_id:
            faction_id = f"sect:{sect_id}"
            faction = state.factions.get(faction_id)
            if faction is None:
                account = f"account:{faction_id}"
                if account not in state.accounts:
                    effects.append(Effect("create_account", {"account": account, "balance": 0}))
                effects.append(
                    Effect(
                        "create_faction",
                        {
                            "id": faction_id,
                            "name": str((self.content.sects.get(sect_id) or {}).get("name") or sect_id),
                            "location": target.location,
                            "founder": "",
                            "treasury_account": account,
                        },
                    )
                )
            effects.append(Effect("faction_join", {"entity": player.id, "faction": faction_id}))
        name = target.name
        sect_name = str((self.content.sects.get(sect_id) or {}).get("name") or "") if sect_id else ""
        effects.append(
            Effect(
                "growth_log",
                {
                    "action": "bai",
                    "master": target.id,
                    "master_name": name,
                    "sect": sect_id,
                    "sect_name": sect_name,
                },
            )
        )
        return effects
