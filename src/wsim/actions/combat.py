"""战斗动作（P2）：`fight` 一次动作原子结算最多 20 回合，只写 1 条事件。

设计要点：
- 随机全部来自 Writer 注入的 `PRNG(seed, seq, salt="action:fight")`；
- 初始状态只读，结算结果全部落成纯 Effect（hp_add / entity_status / loot_create /
  combat_exp_add / skill_xp_add），不直接改 state；
- `combat_log` 是只读 Effect，把 rounds[] 战报写进同一条事件，回放时跳过。
"""
from __future__ import annotations

from typing import Any

from ..core.models import Effect, Intent
from ..sim.combat_rules import simulate_fight
from ..sim.spawn import spawn_effects
from ..world.state import WorldState
from .growth import DEATH_GATE
from .registry import Action


def _combat_target(intent: Intent) -> str:
    return str(intent.args.get("target") or intent.args.get("target_id") or "")


def _int_dict(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items() if int(v) > 0}


def _round_cap(intent: Intent) -> int | None:
    """默认不封顶（原版 `fight()` 无回合上限）。

    `max_rounds` 只在调用方显式给出正整数时生效，用于审计/演练固定回合数；
    引擎内部另有防挂死硬保护，不通过这个参数表达。
    """
    raw = intent.args.get("max_rounds")
    if not isinstance(raw, int) or raw <= 0:
        return None
    return raw


def _drop_counts(
    ent: Any,
    explicit_loot: dict[str, int] | None = None,
    *,
    include_equipment: bool = False,
    increment_equipped: bool = False,
    content_drops: dict[str, int] | None = None,
) -> dict[str, int]:
    """一个非玩家实体死亡时随身落的物品与数量。"""
    drops: dict[str, int] = {}
    if explicit_loot:
        drops.update({str(k): int(v) for k, v in explicit_loot.items() if int(v) > 0})
    else:
        for item in sorted(ent.inventory):
            qty = int(ent.inventory[item])
            if qty > 0:
                drops[item] = qty
        if increment_equipped:
            # 玩家死亡路径（原版 make_corpse）按“背包 + 每件已装备”累加。
            for item in ent.equipped.values():
                if item:
                    drops[str(item)] = drops.get(str(item), 0) + 1
        else:
            # NPC 尸体路径按 id 去重，不重复计背包里已有的同 id。
            for item in sorted(set(ent.equipped.values())):
                if item and item not in drops:
                    drops[item] = 1
    # 原版 `die()` 里的固定掉落（内容包 drops，仅必掉且已解析的条目）。
    for item, qty in sorted((content_drops or {}).items()):
        if str(item) and int(qty) > 0:
            drops[str(item)] = max(drops.get(str(item), 0), int(qty))
    # 同一 id 已在背包/显式掉落里时取较大数量，避免双计。
    if include_equipment:
        equip_counts: dict[str, int] = {}
        for item in ent.equipment:
            if item:
                equip_counts[item] = equip_counts.get(item, 0) + 1
        for item, qty in sorted(equip_counts.items()):
            drops[item] = max(drops.get(item, 0), qty)
    return drops


def _drop_effects(
    drops: dict[str, int],
    location: str,
    reason: str,
    consume_owner: str | None = None,
) -> list[Effect]:
    effects: list[Effect] = []
    for item in sorted(drops):
        effects.append(
            Effect(
                op="loot_create",
                data={"location": location, "item": item, "qty": int(drops[item]), "reason": reason},
            )
        )
        if consume_owner is not None:
            effects.append(
                Effect(op="item_consume", data={"owner": consume_owner, "item": item, "qty": int(drops[item])})
            )
    return effects


def combat_effects(
    state: WorldState,
    attacker: Any,
    defender: Any,
    rng,
    *,
    intent: Intent,
    salt: str,
    max_rounds: int | None,
    reason: str = "fight",
    loot: dict[str, int] | None = None,
    extra_log: dict[str, Any] | None = None,
    drop_dead_npc_attacker: bool = False,
) -> list[Effect]:
    """结算一场（或一回合）战斗并产出 Effect。`FightAction` 与 NPC 主动攻击共用。"""
    skill = intent.args.get("skill")
    defender_skill = intent.args.get("target_skill")

    trace: list = []
    result = simulate_fight(
        attacker,
        defender,
        rng,
        skill=str(skill) if skill else None,
        defender_skill=str(defender_skill) if defender_skill else None,
        max_rounds=max_rounds,
        trace=trace,
        player_ids=set(state.players),
    )

    effects: list[Effect] = []
    hp_a = int(result["hp_raw"][str(attacker.id)])
    hp_b = int(result["hp_raw"][str(defender.id)])
    if hp_a != attacker.hp:
        effects.append(
            Effect(op="hp_add", data={"entity": attacker.id, "delta": hp_a - attacker.hp, "reason": reason})
        )
    if hp_b != defender.hp:
        effects.append(
            Effect(op="hp_add", data={"entity": defender.id, "delta": hp_b - defender.hp, "reason": reason})
        )

    dead_ids = set(result["dead"])
    for ent in (attacker, defender):
        if str(ent.id) not in dead_ids or ent.status == "dead":
            continue
        effects.append(
            Effect(
                op="entity_status",
                data={
                    "entity": ent.id,
                    "status": "dead",
                    "tick": int(intent.tick),
                    "location": str(ent.location),
                },
            )
        )
        if str(ent.id) in state.players:
            effects.append(
                Effect(
                    op="player_presence",
                    data={"id": str(ent.id), "presence": "custody", "tick": int(intent.tick)},
                )
            )
            # 原版 die()：清点死亡后果（掉经验/掉技能/掉潜能/死亡计数），
            # 并把玩家移入鬼门关。鬼门关不存在时（旧九地点世界）只记状态。
            if DEATH_GATE in state.locations:
                effects.append(
                    Effect(
                        op="set_field",
                        data={"entity": ent.id, "field": "location", "value": DEATH_GATE},
                    )
                )
            exp_loss = int(ent.combat_exp) // 50
            pot_after = int(ent.potential)
            if pot_after > int(ent.learned_points):
                pot_after = max(0, pot_after + int((ent.learned_points - pot_after) / 2))
            lost_skills = sorted(
                sid
                for sid in set(ent.skill_levels) | set(ent.skill_xp)
                if int(ent.skill_levels.get(sid, 0)) > 0 or int(ent.skill_xp.get(sid, 0)) > 0
            )
            effects.append(Effect(op="player_death", data={"entity": ent.id}))
            # 原版 make_corpse 直接把随身物品留在死亡地点；引擎不建模尸体，
            # 先摘下已装备物品再按背包总量落地，避免双计。
            for slot in sorted(ent.equipped):
                effects.append(
                    Effect(op="unequip", data={"entity": ent.id, "slot": slot})
                )
            drops = _drop_counts(ent, increment_equipped=True)
            effects.extend(
                _drop_effects(
                    drops,
                    str(ent.death_location or ent.location),
                    "death",
                    consume_owner=str(ent.id),
                )
            )
            effects.append(
                Effect(
                    op="death_log",
                    data={
                        "entity": ent.id,
                        "death_location": str(ent.death_location or ent.location),
                        "exp_loss": exp_loss,
                        "combat_exp_before": int(ent.combat_exp),
                        "combat_exp_after": max(0, int(ent.combat_exp) - exp_loss),
                        "potential_loss": max(0, int(ent.potential) - pot_after),
                        "potential_after": pot_after,
                        "skills_lost": lost_skills,
                        "death_count": int(ent.death_count) + 1,
                        "dropped": sorted(drops),
                    },
                )
            )

    # 掉落：只有被击杀的非玩家目标掉落；玩家尸体不产生掉落。
    # API 壳不传 loot 时，按内容里的“尸体可带走物”确定性产出：
    # 背包物品 + 已装备物品（equipped）。随身装备（equipment）无条件落地，
    # 对应原版 make_corpse；我们不建模尸体，直接进地面。
    # NPC 主动攻击时攻击者是 NPC（老虎/毒蛇/狼狗），它被打死也要掉随身物。
    if str(defender.id) in dead_ids and not str(defender.id).startswith("player:"):
        effects.extend(
            _drop_effects(
                _drop_counts(
                    defender, loot, include_equipment=True,
                    content_drops=getattr(defender, "drops", None),
                ),
                str(defender.location),
                "fight",
            )
        )
    if (
        drop_dead_npc_attacker
        and str(attacker.id) in dead_ids
        and not str(attacker.id).startswith("player:")
    ):
        effects.extend(
            _drop_effects(
                _drop_counts(
                    attacker, include_equipment=True,
                    content_drops=getattr(attacker, "drops", None),
                ),
                str(attacker.location),
                "death",
            )
        )

    # 经验与技能成长：增量/落值全部由 xkx_formula 的 Reward/improve_skill 算出。
    for eid in (str(attacker.id), str(defender.id)):
        delta = int(result["combat_exp"].get(eid, 0))
        if delta:
            effects.append(Effect(op="combat_exp_add", data={"entity": eid, "delta": delta}))
    for update in result["skill_updates"]:
        effects.append(
            Effect(
                op="skill_progress",
                data={
                    "entity": update["entity"],
                    "skill": update["skill"],
                    "level": int(update["level"]),
                    "learned": int(update["learned"]),
                },
            )
        )

    # 超额伤害只进战报：hp_add 的 delta 由 Effect 钳制后回写，事件必须等于实际变化。
    overkill = {
        eid: -int(result["hp_raw"][eid])
        for eid in (str(attacker.id), str(defender.id))
        if int(result["hp_raw"][eid]) < 0
    }

    log = {
        "salt": salt,
        "max_rounds": max_rounds,
        "skill": result["skill"],
        "target_skill": result["target_skill"],
        "winner": result["winner"],
        "rounds_used": int(result["rounds_used"]),
        "hp": result["hp"],
        "dead": result["dead"],
        "overkill": overkill,
        "rounds": result["rounds"],
        "draws": int(result["draws"]),
    }
    if extra_log:
        log.update(extra_log)
    effects.append(Effect(op="combat_log", data=log))
    return effects


class FightAction(Action):
    verb = "fight"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        target = _combat_target(intent)
        if not target:
            return ["target_missing"]
        if target == intent.actor:
            return ["self_target"]
        attacker = state.entities.get(intent.actor)
        if attacker is None:
            return ["actor_missing"]
        defender = state.entities.get(target)
        if defender is None:
            return ["target_missing"]
        if target.startswith("player:"):
            return ["target_is_player"]
        if attacker.status == "dead":
            return ["actor_dead"]
        if defender.status == "dead":
            return ["target_dead"]
        if attacker.location != defender.location:
            return ["not_together"]
        if int(attacker.max_hp) <= 0 or int(defender.max_hp) <= 0:
            return ["no_combat_stats"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        attacker = state.entities[intent.actor]
        defender = state.entities[_combat_target(intent)]
        return combat_effects(
            state,
            attacker,
            defender,
            rng,
            intent=intent,
            salt="action:fight",
            max_rounds=_round_cap(intent),
            reason="fight",
            loot=_int_dict(intent.args.get("loot")),
        )


class SystemSpawnAction(Action):
    """日界刷怪。没有内容声明或没有到期刷新时返回 []，调度层不会提交意图。"""

    verb = "system_spawn"

    def __init__(self, spawns: list[dict[str, Any]] | None = None):
        self.spawns = spawns or []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return spawn_effects(state, self.spawns, int(intent.tick))
