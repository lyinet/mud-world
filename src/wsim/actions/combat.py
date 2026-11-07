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
from ..sim.combat_rules import MAX_ROUNDS, simulate_fight
from ..sim.spawn import spawn_effects
from ..world.state import WorldState
from .registry import Action


def _combat_target(intent: Intent) -> str:
    return str(intent.args.get("target") or intent.args.get("target_id") or "")


def _int_dict(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items() if int(v) > 0}


def _round_cap(intent: Intent) -> int:
    """硬上限 20 回合；参数只能调小，不能调大。"""
    raw = intent.args.get("max_rounds")
    if not isinstance(raw, int) or raw <= 0:
        return MAX_ROUNDS
    return min(MAX_ROUNDS, raw)


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
        skill = intent.args.get("skill")
        defender_skill = intent.args.get("target_skill")
        loot = _int_dict(intent.args.get("loot"))

        trace: list = []
        result = simulate_fight(
            attacker,
            defender,
            rng,
            skill=str(skill) if skill else None,
            defender_skill=str(defender_skill) if defender_skill else None,
            max_rounds=_round_cap(intent),
            trace=trace,
            player_ids=set(state.players),
        )

        effects: list[Effect] = []
        hp_a = int(result["hp_raw"][str(attacker.id)])
        hp_b = int(result["hp_raw"][str(defender.id)])
        if hp_a != attacker.hp:
            effects.append(
                Effect(op="hp_add", data={"entity": attacker.id, "delta": hp_a - attacker.hp, "reason": "fight"})
            )
        if hp_b != defender.hp:
            effects.append(
                Effect(op="hp_add", data={"entity": defender.id, "delta": hp_b - defender.hp, "reason": "fight"})
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

        # 掉落：只有被击杀的目标掉落；玩家尸体不产生掉落。
        # API 壳不传 loot 时，按内容里的“尸体可带走物”确定性产出：
        # 背包物品 + 已装备物品（equipment），全部落到尸体所在地面。
        if str(defender.id) in dead_ids and not str(defender.id).startswith("player:"):
            if not loot:
                for item in sorted(defender.inventory):
                    qty = int(defender.inventory[item])
                    if qty > 0:
                        loot[item] = qty
                for item in sorted(set(defender.equipped.values())):
                    if item and item not in loot:
                        loot[item] = 1
            for item in sorted(loot):
                effects.append(
                    Effect(
                        op="loot_create",
                        data={
                            "location": str(defender.location),
                            "item": item,
                            "qty": int(loot[item]),
                            "reason": "fight",
                        },
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

        effects.append(
            Effect(
                op="combat_log",
                data={
                    "salt": "action:fight",
                    "max_rounds": _round_cap(intent),
                    "skill": result["skill"],
                    "target_skill": result["target_skill"],
                    "winner": result["winner"],
                    "rounds_used": int(result["rounds_used"]),
                    "hp": result["hp"],
                    "dead": result["dead"],
                    "overkill": overkill,
                    "rounds": result["rounds"],
                    "draws": int(result["draws"]),
                },
            )
        )
        return effects


class SystemSpawnAction(Action):
    """日界刷怪。没有内容声明或没有到期刷新时返回 []，调度层不会提交意图。"""

    verb = "system_spawn"

    def __init__(self, spawns: list[dict[str, Any]] | None = None):
        self.spawns = spawns or []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return spawn_effects(state, self.spawns, int(intent.tick))
