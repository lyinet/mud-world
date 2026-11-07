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
from ..sim.combat_rules import MAX_ROUNDS, attack_skill, simulate_fight
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
        )

        effects: list[Effect] = []
        hp_a = int(result["hp"][str(attacker.id)])
        hp_b = int(result["hp"][str(defender.id)])
        if hp_a != attacker.hp:
            effects.append(
                Effect(op="hp_add", data={"entity": attacker.id, "delta": hp_a - attacker.hp, "reason": "fight"})
            )
        if hp_b != defender.hp:
            effects.append(
                Effect(op="hp_add", data={"entity": defender.id, "delta": hp_b - defender.hp, "reason": "fight"})
            )

        for ent, final_hp in ((attacker, hp_a), (defender, hp_b)):
            if final_hp > 0 or ent.status == "dead":
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
        if hp_b <= 0 and not str(defender.id).startswith("player:"):
            for item in sorted(loot):
                effects.append(
                    Effect(
                        op="loot_create",
                        data={"entity": attacker.id, "item": item, "qty": int(loot[item]), "reason": "fight"},
                    )
                )

        # 经验：胜者 +3 战斗经验 / +5 出手技能经验；打平（20 回合未分胜负）双方 +1。
        skill_a = attack_skill(attacker, str(skill) if skill else None)
        skill_b = attack_skill(defender, str(defender_skill) if defender_skill else None)
        winner = result["winner"]
        if winner == "attacker":
            effects.append(Effect(op="combat_exp_add", data={"entity": attacker.id, "delta": 3}))
            effects.append(
                Effect(op="skill_xp_add", data={"entity": attacker.id, "skill": skill_a, "delta": 5})
            )
        elif winner == "defender":
            effects.append(Effect(op="combat_exp_add", data={"entity": defender.id, "delta": 3}))
            effects.append(
                Effect(op="skill_xp_add", data={"entity": defender.id, "skill": skill_b, "delta": 5})
            )
        else:
            effects.append(Effect(op="combat_exp_add", data={"entity": attacker.id, "delta": 1}))
            effects.append(Effect(op="combat_exp_add", data={"entity": defender.id, "delta": 1}))

        effects.append(
            Effect(
                op="combat_log",
                data={
                    "salt": "action:fight",
                    "max_rounds": _round_cap(intent),
                    "skill": skill_a,
                    "target_skill": skill_b,
                    "winner": winner,
                    "rounds_used": int(result["rounds_used"]),
                    "hp": result["hp"],
                    "dead": result["dead"],
                    "rounds": result["rounds"],
                    "draws": len(trace),
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
