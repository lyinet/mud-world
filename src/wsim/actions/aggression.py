"""NPC 主动攻击动作。

`trigger` 对应原版 `init()` -> `kill_ob`：只登记交战，不结算伤害。
`continue` 对应 NPC 心跳：每个 tick 结算一个回合。
`reset` 对应 `clean_up_enemy()`：目标离开/死亡/昏迷/离线时清掉交战关系。
"""
from __future__ import annotations

from typing import Any

from ..core.models import Effect, Intent
from ..sim.aggression import decide, rule_for
from ..world.state import WorldState
from .combat import combat_effects
from .registry import Action


def _reset(npc: Any) -> list[Effect]:
    return [
        Effect(op="set_field", data={"entity": npc.id, "field": "combat_target", "value": ""}),
        Effect(op="set_field", data={"entity": npc.id, "field": "combat_tick", "value": 0}),
        Effect(op="set_field", data={"entity": npc.id, "field": "combat_round", "value": 0}),
    ]


def _log(npc: Any, target: Any, mode: str, **extra: Any) -> Effect:
    data = {
        "mode": mode,
        "npc": str(getattr(npc, "id", "")),
        "npc_name": str(getattr(npc, "name", "")),
        "target": str(getattr(target, "id", "")),
        "target_name": str(getattr(target, "name", "")),
    }
    data.update(extra)
    return Effect(op="aggression_log", data=data)


class NpcAggressionAction(Action):
    verb = "npc_aggression"

    def __init__(self, rules: dict[str, dict[str, Any]] | None = None):
        self.rules = rules or {}

    def _rule(self, entity: Any) -> dict[str, Any] | None:
        return rule_for(entity, self.rules)

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        npc = state.entities.get(intent.actor)
        if npc is None:
            return ["actor_missing"]
        # 内容包里的野兽/NPC 的 role 有 npc / wanderer / 镖师等，不能拿 role
        # 当“是不是 NPC”；真正的门槛是它是否在主动攻击规则表里。
        if str(intent.actor).startswith("player:"):
            return ["actor_is_player"]
        if self._rule(npc) is None:
            return ["not_aggressive"]
        if npc.status != "alive":
            return ["actor_not_alive"]
        mode = str(intent.args.get("mode") or "")
        if mode == "trigger":
            target_id = str(intent.args.get("target") or "")
            target = state.entities.get(target_id)
            if target is None:
                return ["target_missing"]
            if not target_id.startswith("player:"):
                return ["target_not_player"]
            if target.status != "alive":
                return ["target_not_alive"]
            if target.location != npc.location:
                return ["not_together"]
            if npc.combat_target:
                return ["already_fighting"]
            if int(npc.max_hp) <= 0 or int(target.max_hp) <= 0:
                return ["no_combat_stats"]
            return []
        if mode == "continue":
            if str(npc.combat_target) != str(intent.args.get("target") or ""):
                return ["not_fighting_target"]
            return []
        if mode == "reset":
            return []
        return ["bad_mode"]

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        npc = state.entities[intent.actor]
        mode = str(intent.args.get("mode") or "")
        target = state.entities.get(str(intent.args.get("target") or ""))

        if mode == "reset":
            return [*_reset(npc), _log(npc, target, "reset", result="ended")]

        if mode == "trigger":
            rule = self._rule(npc)
            if rule is None or target is None:
                return _reset(npc)
            triggered, info = decide(rule, npc, target, rng)
            effects: list[Effect] = []
            if triggered:
                effects.extend(
                    [
                        Effect(
                            op="set_field",
                            data={"entity": npc.id, "field": "combat_target", "value": str(target.id)},
                        ),
                        Effect(
                            op="set_field",
                            data={"entity": npc.id, "field": "combat_tick", "value": int(intent.tick)},
                        ),
                        Effect(
                            op="set_field",
                            data={"entity": npc.id, "field": "combat_round", "value": 0},
                        ),
                    ]
                )
            effects.append(
                _log(
                    npc,
                    target,
                    "trigger",
                    triggered=triggered,
                    tick=int(intent.tick),
                    **info,
                )
            )
            return effects

        # continue：一个心跳回合。
        if target is None or target.status != "alive" or target.location != npc.location:
            return [*_reset(npc), _log(npc, target, "reset", result="target_gone")]

        round_no = int(npc.combat_round) + 1
        effects = [
            Effect(
                op="set_field",
                data={"entity": npc.id, "field": "combat_round", "value": round_no},
            )
        ]
        effects.extend(
            combat_effects(
                state,
                npc,
                target,
                rng,
                intent=intent,
                salt="action:npc_aggression",
                max_rounds=1,
                reason="npc_aggression",
                extra_log={
                    "aggression_round": round_no,
                    "aggressor": str(npc.id),
                    "defender": str(target.id),
                },
                drop_dead_npc_attacker=True,
            )
        )

        dead = {
            str(eff.data.get("entity"))
            for eff in effects
            if eff.op == "entity_status" and eff.data.get("status") == "dead"
        }
        ended = str(target.id) in dead or str(npc.id) in dead
        if ended:
            effects.extend(_reset(npc))
        effects.append(
            _log(
                npc,
                target,
                "continue",
                triggered=True,
                result="ended" if ended else "round",
                round=round_no,
            )
        )
        return effects
