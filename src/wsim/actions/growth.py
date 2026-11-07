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

from dataclasses import dataclass
from typing import Any

from ..core.models import Effect, Intent, Reject
from ..world.state import Entity, WorldState
from .common import arg_int, arg_str
from .registry import Action

# 鬼门关是原版 /d/death/gate；xkx 内容切片没有收录该房间，
# 由 session 在 xkx 世界启动时作为虚拟地点注入 state.locations。
DEATH_GATE = "/d/death/gate"

# 原版 practice_skill 各技能的通用体力门槛（anran-zhang/anying 等均为 30）。
PRACTICE_STAMINA_COST = 30


def _xkx_master(meta: dict[str, Any] | None) -> bool:
    return bool(meta and meta.get("is_master"))


def _skill_index(content) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in getattr(content, "skills", []) or []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or "")
        if sid:
            out[sid] = raw
    return out


def _stats_index(content) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in getattr(content, "skills_stats", []) or []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or "")
        if sid:
            out[sid] = raw
    return out


def _curve_value(stat: dict[str, Any] | None, key: str, default: int) -> int:
    if not stat:
        return default
    curve = stat.get("curve") or {}
    node = curve.get(key) or {}
    value = node.get("value")
    return int(value) if isinstance(value, (int, float)) else default


def _skill_alias(content, name: str) -> str | None:
    """把技能 id/中文名解析为 id；支持全名与唯一前缀。"""
    if not name:
        return None
    want = name.strip()
    skills = _skill_index(content)
    if want in skills:
        return want
    lowered = want.lower()
    if lowered in skills:
        return lowered
    by_name = [sid for sid, meta in skills.items() if str(meta.get("name") or "") == want]
    if len(by_name) == 1:
        return by_name[0]
    prefix = [sid for sid, meta in skills.items() if sid.lower().startswith(lowered)]
    if len(prefix) == 1:
        return prefix[0]
    name_prefix = [sid for sid, meta in skills.items() if str(meta.get("name") or "").startswith(want)]
    if len(name_prefix) == 1:
        return name_prefix[0]
    return None


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


def _int_attribute(ent: Entity, default: int = 15) -> int:
    attrs = getattr(ent, "attrs", {}) or {}
    try:
        return int(attrs.get("int", default))
    except (TypeError, ValueError):
        return default


def _master_skill(meta: dict[str, Any] | None, skill: str) -> int:
    if not meta:
        return 0
    skills = meta.get("skills") or {}
    try:
        return int(skills.get(skill, 0))
    except (TypeError, ValueError):
        return 0


def _skill_label(content, sid: str) -> str:
    meta = _skill_index(content).get(sid) or {}
    return str(meta.get("name") or sid)


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


class LearnAction(Action):
    verb = "learn"
    aliases = ("study",)

    def __init__(self, content):
        self.content = content

    def _resolve(self, state: WorldState, intent: Intent) -> tuple[Entity, Entity, str, int]:
        player = state.entities[intent.actor]
        teacher = _resolve_present_master(state, self.content, player, str(intent.args["teacher"]))
        skill_name = str(intent.args["skill"])
        skill = _skill_alias(self.content, skill_name)
        if skill is None:
            raise Reject("unknown_skill")
        times = int(intent.args["times"])
        return player, teacher, skill, times

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        player = state.entities.get(intent.actor)
        if player is None:
            raise Reject("unknown_actor")
        if player.status != "alive":
            raise Reject("actor_not_alive")
        if player.location == DEATH_GATE:
            raise Reject("in_ghost_gate")
        player, teacher, skill, times = self._resolve(state, intent)
        if player.master != teacher.id:
            raise Reject("not_apprentice_of_teacher")
        master_level = _master_skill(_content_npc(self.content, teacher.id), skill)
        if master_level <= 0:
            raise Reject("teacher_lacks_skill")
        my_level = int(player.skill_levels.get(skill, 0))
        meta = _skill_index(self.content).get(skill) or {}
        if my_level >= master_level:
            raise Reject("skill_not_lower")
        if my_level >= 250 and str(meta.get("type") or "") != "knowledge":
            raise Reject("master_wont_teach")
        if str(meta.get("type") or "") == "martial":
            if (my_level ** 3) // 10 > int(player.combat_exp):
                raise Reject("insufficient_combat_exp")
        jing_cost = 150 // max(1, _int_attribute(player))
        if my_level <= 0:
            jing_cost *= 2
        if int(player.potential) - int(player.learned_points) < times:
            raise Reject("insufficient_potential")
        if int(player.stamina) < jing_cost * times:
            raise Reject("insufficient_jing")
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        player = state.entities[intent.actor]
        player, teacher, skill, times = self._resolve(state, intent)
        meta = _skill_index(self.content).get(skill) or {}
        stat = _stats_index(self.content).get(skill)
        max_level = 800
        stats_level = (stat or {}).get("max_level") or {}
        if isinstance(stats_level, dict) and isinstance(stats_level.get("value"), (int, float)):
            max_level = int(stats_level["value"])
        int_attr = _int_attribute(player)
        learn_bonus = _curve_value(stat, "learn_bonus", 10)
        jing_cost = 150 // max(1, int_attr)
        if int(player.skill_levels.get(skill, 0)) <= 0:
            jing_cost *= 2
        self_skill = int(player.skill_levels.get(skill, 0))
        level_before = self_skill
        xp_before = int(player.skill_xp.get(skill, 0))
        level = level_before
        learned = xp_before
        stamina_before = int(player.stamina)
        potential_before = int(player.potential)
        learned_points_before = int(player.learned_points)
        attempts = 0
        for _ in range(times):
            # 原版取数顺序：每练一次先 random(improve_points) 决定本次进步量，
            # 再做 improve_skill 的阈值进位。顺序被测试锁住，不能提前合并取数。
            improve_points = (int_attr + int_attr) // 2 + learn_bonus
            improve_points = max(15, improve_points)
            gain = rng.randint(0, improve_points - 1)
            level, learned = _improve_values(level, learned, gain, max_level)
            attempts += 1
        effects: list[Effect] = [
            Effect("stamina", {"entity": player.id, "delta": -jing_cost * attempts}),
            Effect("potential_spend", {"entity": player.id, "cost": attempts}),
            Effect("learned_points_add", {"entity": player.id, "delta": attempts}),
            Effect("skill_progress", {"entity": player.id, "skill": skill, "level": level, "learned": learned}),
            Effect(
                "growth_log",
                {
                    "action": "learn",
                    "teacher": teacher.id,
                    "teacher_name": teacher.name,
                    "skill": skill,
                    "skill_name": _skill_label(self.content, skill),
                    "times": attempts,
                    "jing_cost": jing_cost * attempts,
                    "learn_bonus": learn_bonus,
                    "level_before": level_before,
                    "level_after": level,
                    "xp_before": xp_before,
                    "xp_after": learned,
                    "stamina_before": stamina_before,
                    "stamina_after": stamina_before - jing_cost * attempts,
                    "potential_before": potential_before,
                    "potential_after": potential_before - attempts,
                    "learned_points_before": learned_points_before,
                    "learned_points_after": learned_points_before + attempts,
                },
            ),
        ]
        return effects


def _improve_values(
    level: int, learned: int, amount: int, max_level: int, *, can_level: bool = True
) -> tuple[int, int]:
    """原版 feature/skill.lpc improve_skill：阈值 (level+1)^2。

    weak_mode（练习时 base <= 本技能）对玩家不做等级进位，只累积熟练度。
    """
    learned += amount
    if not can_level:
        return level, learned
    limit = (level + 1) * (level + 1)
    if learned > limit:
        level += 1
        learned = 0
        if level > max_level:
            return max_level, 0
    return level, learned


class PracticeAction(Action):
    verb = "practice"
    aliases = ("train", "exercise")

    def __init__(self, content):
        self.content = content

    def _resolve(self, state: WorldState, intent: Intent) -> tuple[Entity, str, str, int]:
        player = state.entities[intent.actor]
        raw = str(intent.args["skill"])
        times = int(intent.args["times"])
        skill = _skill_alias(self.content, raw)
        if skill is None:
            raise Reject("unknown_skill")
        meta = _skill_index(self.content).get(skill) or {}
        enable = [str(x) for x in (meta.get("enable") or [])]
        base = ""
        for candidate in enable:
            if _skill_alias(self.content, candidate) and int(player.skill_levels.get(candidate, 0)) >= 1:
                base = candidate
                break
        if not base:
            martialtype = str(meta.get("martialtype") or "")
            if martialtype and int(player.skill_levels.get(martialtype, 0)) >= 1:
                base = martialtype
            else:
                base = skill
        return player, skill, base, times

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        player = state.entities.get(intent.actor)
        if player is None:
            raise Reject("unknown_actor")
        if player.status != "alive":
            raise Reject("actor_not_alive")
        if player.location == DEATH_GATE:
            raise Reject("in_ghost_gate")
        player, skill, base, times = self._resolve(state, intent)
        my_skill = int(player.skill_levels.get(skill, 0))
        if my_skill < 1:
            raise Reject("skill_not_learned")
        base_skill = int(player.skill_levels.get(base, 0))
        if base_skill < 1:
            raise Reject("basic_skill_missing")
        if base_skill // 2 <= my_skill // 3:
            raise Reject("practice_ratio_low")
        if (my_skill ** 3) // 10 > int(player.combat_exp):
            raise Reject("insufficient_combat_exp")
        if int(player.stamina) < PRACTICE_STAMINA_COST:
            raise Reject("insufficient_stamina")
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        player = state.entities[intent.actor]
        player, skill, base, times = self._resolve(state, intent)
        stat = _stats_index(self.content).get(skill)
        max_level = 800
        stats_level = (stat or {}).get("max_level") or {}
        if isinstance(stats_level, dict) and isinstance(stats_level.get("value"), (int, float)):
            max_level = int(stats_level["value"])
        base_level = int(player.skill_levels.get(base, 0))
        skill_level = int(player.skill_levels.get(skill, 0))
        practice_bonus = _curve_value(stat, "practice_bonus", 10)
        weak_mode = 0 if base_level > skill_level else 1
        # 原版 practice.lpc 的 `skill` 在循环前取一次，循环内不再重算，故跨度恒定。
        span = (base_level * base_level) // (skill_level + 1) // 5
        level_before = skill_level
        xp_before = int(player.skill_xp.get(skill, 0))
        level = level_before
        learned = xp_before
        stamina_before = int(player.stamina)
        attempts = min(times, stamina_before // PRACTICE_STAMINA_COST)
        gains: list[int] = []
        for _ in range(attempts):
            # 原版 practice.lpc：random((base^2)/(skill+1)/5) + 1 + practice_bonus。
            # random(0) 在原版会返回 0；这里同样只在跨度为 0 时取 0。
            gain = (rng.randint(0, span - 1) if span > 0 else 0) + 1 + practice_bonus
            level, learned = _improve_values(
                level, learned, gain, max_level, can_level=(weak_mode == 0)
            )
            gains.append(gain)
        effects: list[Effect] = [
            Effect("stamina", {"entity": player.id, "delta": -PRACTICE_STAMINA_COST * attempts}),
            Effect("skill_progress", {"entity": player.id, "skill": skill, "level": level, "learned": learned}),
            Effect(
                "growth_log",
                {
                    "action": "practice",
                    "skill": skill,
                    "skill_name": _skill_label(self.content, skill),
                    "base": base,
                    "times": attempts,
                    "requested": times,
                    "weak_mode": weak_mode,
                    "stamina_cost": PRACTICE_STAMINA_COST * attempts,
                    "practice_bonus": practice_bonus,
                    "gains": gains,
                    "level_before": level_before,
                    "level_after": level,
                    "xp_before": xp_before,
                    "xp_after": learned,
                    "stamina_before": stamina_before,
                    "stamina_after": stamina_before - PRACTICE_STAMINA_COST * attempts,
                    "partial": attempts < times,
                },
            ),
        ]
        return effects


class ClimbAction(Action):
    verb = "climb"
    aliases = ("climb_tree",)

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        player = state.entities.get(intent.actor)
        if player is None:
            raise Reject("unknown_actor")
        if player.status != "alive":
            raise Reject("actor_not_alive")
        if player.location != "/d/shiliang/gate":
            raise Reject("climb_wrong_place")
        needle = str(intent.args.get("arg") or "").strip()
        if not needle or needle.lower() != "tree":
            raise Reject("climb_target_unknown")
        dodge = int(player.skill_levels.get("dodge", 0))
        if dodge < 50:
            raise Reject("dodge_too_low")
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        player = state.entities[intent.actor]
        dodge = int(player.skill_levels.get("dodge", 0))
        return [
            Effect("set_field", {"entity": player.id, "field": "location", "value": "/d/shiliang/yuanzi"}),
            Effect(
                "growth_log",
                {
                    "action": "climb",
                    "from": player.location,
                    "to": "/d/shiliang/yuanzi",
                    "dodge": dodge,
                    "requirement": 50,
                    "graceful": dodge >= 80,
                },
            ),
        ]


class JumpAction(Action):
    verb = "jump"
    aliases = ("leap",)

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        player = state.entities.get(intent.actor)
        if player is None:
            raise Reject("unknown_actor")
        if player.status != "alive":
            raise Reject("actor_not_alive")
        if player.location != "/d/shiliang/tingzi":
            raise Reject("jump_wrong_place")
        needle = str(intent.args.get("arg") or "").strip()
        if needle != "木桩":
            raise Reject("jump_target_unknown")
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        player = state.entities[intent.actor]
        dodge = int(player.skill_levels.get("dodge", 0))
        if dodge >= 100:
            dest = "/d/shiliang/xiaodao"
            landing = "小岛"
        else:
            dest = "/d/shiliang/huli"
            landing = "湖里"
        return [
            Effect("set_field", {"entity": player.id, "field": "location", "value": dest}),
            Effect(
                "growth_log",
                {
                    "action": "jump",
                    "from": player.location,
                    "to": dest,
                    "landing": landing,
                    "dodge": dodge,
                    "requirement": 100,
                    "fell": dodge < 100,
                },
            ),
        ]


class ReviveAction(Action):
    verb = "revive"
    aliases = ("resurrect", "huanyang")

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        player = state.entities.get(intent.actor)
        if player is None:
            raise Reject("unknown_actor")
        if player.status != "dead":
            raise Reject("not_dead")
        if player.location != DEATH_GATE:
            raise Reject("not_in_ghost_gate")
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        player = state.entities[intent.actor]
        home = player.home or player.death_location or ""
        if not home:
            raise Reject("no_revival_location")
        effects: list[Effect] = [
            # 原版 reincarnate()：气/精恢复满值、状态回到 alive。
            Effect("revive", {"entity": player.id, "hp": int(player.max_hp)}),
            Effect("set_field", {"entity": player.id, "field": "location", "value": home}),
            Effect(
                "growth_log",
                {
                    "action": "revive",
                    "from": DEATH_GATE,
                    "to": home,
                    "death_count": int(player.death_count),
                    "death_location": player.death_location,
                },
            ),
        ]
        return effects
