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


def _below(rng: Any, n: int) -> int:
    """`random(n)` 的确定性等价：n<=0 时返回 0 且不消费随机流。"""
    n = int(n)
    return rng.randint(0, n - 1) if n > 0 else 0


def _dex(ent: Any) -> int:
    # 与 combat_rules 的属性缺省一致：内容未写 dex 时按 10。
    return int((getattr(ent, "attrs", None) or {}).get("dex", 10) or 0)


def _wimpy_escape(state: WorldState, npc: Any, target: Any, rng: Any, intent: Intent):
    """原版 `char.lpc:80-97` 心跳里的自动逃跑。

    随机顺序对齐原版 `go.lpc:336-347`：`do_flee` 先 `random` 选方向，再进
    `go.lpc:60-79 main()` 掷 dex；失败时方向随机数也已消费、人不移动。
    返回 `(effects, escaped)`；`escaped=True` 时调用方跳过本回合伤害。
    """
    threshold = int(getattr(target, "wimpy", 0) or 0)
    max_hp = int(getattr(target, "max_hp", 0) or 0)
    hp = int(getattr(target, "hp", 0) or 0)
    if threshold <= 0 or max_hp <= 0 or hp * 100 > threshold * max_hp:
        return [], False

    round_no = int(npc.combat_round) + 1
    exits = sorted(str(x) for x in (state.locations.get(target.location, {}).get("exits") or []))
    base = {
        "mode": "wimpy",
        "npc": str(npc.id),
        "npc_name": str(npc.name),
        "target": str(target.id),
        "target_name": str(target.name),
        "round": round_no,
        "threshold": threshold,
        "hp": hp,
        "max_hp": max_hp,
        "dex_target": _dex(target),
        "dex_npc": _dex(npc),
        "intent_tick": int(intent.tick),
        "reason": "wimpy",
    }
    if not exits:
        base.update(result="no_exit", from_location=str(target.location), exit_count=0, roll_order=[])
        return [Effect(op="wimpy_log", data=base)], False

    # 原版顺序：先 `random(sizeof(directions))` 选出口（失败也消费这一笔）。
    exit_index = _below(rng, len(exits))
    dest = exits[exit_index]
    roll_player = _below(rng, _dex(target))
    roll_enemy = _below(rng, _dex(npc))
    base.update(
        exit_index=exit_index,
        exit_count=len(exits),
        roll_order=["exit_index", "roll_target", "roll_npc"],
        roll_target=roll_player,
        roll_npc=roll_enemy,
    )
    if roll_player <= roll_enemy:
        base.update(
            result="failed",
            from_location=str(target.location),
            attempted_exit=dest,
        )
        return [Effect(op="wimpy_log", data=base)], False

    effects: list[Effect] = [
        Effect(op="set_location", data={"entity": target.id, "to": dest}),
    ]
    # 等价 `remove_all_enemy()`：清空所有正在打玩家的 NPC，而不是只清当前这只。
    for other in sorted(state.entities.values(), key=lambda e: e.id):
        if str(getattr(other, "combat_target", "")) == str(target.id):
            effects.extend(_reset(other))
    base.update(
        result="success",
        from_location=str(target.location),
        to_location=dest,
    )
    effects.append(Effect(op="wimpy_log", data=base))
    return effects, True


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

    def __init__(
        self,
        rules: dict[str, dict[str, Any]] | None = None,
        initiative_gate: bool = False,
    ):
        self.rules = rules or {}
        self.initiative_gate = bool(initiative_gate)

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

        # continue：一个心跳回合。原版 char.lpc 先判 wimpy 自动逃跑，再攻击。
        if target is None or target.status != "alive" or target.location != npc.location:
            return [*_reset(npc), _log(npc, target, "reset", result="target_gone")]

        round_no = int(npc.combat_round) + 1
        wimpy_effects, escaped = _wimpy_escape(state, npc, target, rng, intent)
        if escaped:
            effects = [*wimpy_effects]
            effects.append(
                _log(
                    npc,
                    target,
                    "continue",
                    triggered=False,
                    result="escaped",
                    round=round_no,
                    reason="wimpy",
                )
            )
            return effects

        effects = [
            *wimpy_effects,
            Effect(
                op="set_field",
                data={"entity": npc.id, "field": "combat_round", "value": round_no},
            ),
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
                initiative_gate=self.initiative_gate,
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
