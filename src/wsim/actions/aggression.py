"""NPC 主动攻击动作。

`trigger` 对应原版 `init()` -> `kill_ob`：只登记交战，不结算伤害。
`continue` 对应 NPC 心跳：每个 tick 结算一个回合。
`reset` 对应 `clean_up_enemy()`：目标离开/死亡/昏迷/离线时清掉交战关系。
"""
from __future__ import annotations

from typing import Any

from ..core.models import Effect, Intent
from ..core.rng import PRNG
from ..sim.aggression import (
    MAX_OPPONENT,
    chase_followers,
    decide,
    effective_enemies,
    is_killing,
    killer_chase_enabled,
    killer_ids,
    multi_combat_enabled,
    rule_for,
    select_index,
)
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
    # multi 开时只把“这个逃跑的玩家”从每个 NPC 的表里摘掉，别人的战斗不受影响。
    for other in sorted(state.entities.values(), key=lambda e: e.id):
        if str(target.id) not in effective_enemies(other):
            continue
        effects.extend(_remove_enemy(other, str(target.id)))
    # 原版玩家逃跑也走 `go.lpc main()`：pursuer + killer 的 NPC 会跟到出口。
    for fid in chase_followers(state, target.id, target.location):
        effects.append(Effect(op="set_location", data={"entity": fid, "to": dest}))
    base.update(
        result="success",
        from_location=str(target.location),
        to_location=dest,
    )
    effects.append(Effect(op="wimpy_log", data=base))
    return effects, True


def _reset(npc: Any) -> list[Effect]:
    effects = [
        Effect(op="set_field", data={"entity": npc.id, "field": "combat_target", "value": ""}),
        Effect(op="set_field", data={"entity": npc.id, "field": "combat_tick", "value": 0}),
        Effect(op="set_field", data={"entity": npc.id, "field": "combat_round", "value": 0}),
    ]
    # 延迟登记窗口内也要一并取消（原版目标离开时 `start_aggressive` 会复检）。
    if getattr(npc, "aggro_tick", 0) or getattr(npc, "aggro_target", ""):
        effects.extend(
            [
                Effect(
                    op="set_field",
                    data={"entity": npc.id, "field": "aggro_tick", "value": 0},
                ),
                Effect(
                    op="set_field",
                    data={"entity": npc.id, "field": "aggro_target", "value": ""},
                ),
            ]
        )
    # 关态且旧实体没有新字段时，不新增任何事件（旧世界逐字节不变）。
    if (
        multi_combat_enabled()
        or getattr(npc, "combat_enemies", None)
        or getattr(npc, "combat_contrib", None)
    ):
        effects.extend(
            [
                Effect(
                    op="set_field",
                    data={"entity": npc.id, "field": "combat_enemies", "value": []},
                ),
                Effect(
                    op="set_field",
                    data={"entity": npc.id, "field": "combat_contrib", "value": {}},
                ),
            ]
        )
    return effects


def _add_killer(npc: Any, target_id: str) -> list[Effect]:
    """原版 `attack.lpc:83-84`：`kill_ob` 把目标 id 追加进 killer（去重）。

    关闭态严格返回 []，不新增字段、不产生事件。
    """
    target_id = str(target_id or "")
    if not killer_chase_enabled() or not target_id or is_killing(npc, target_id):
        return []
    return [
        Effect(
            op="set_field",
            data={"entity": npc.id, "field": "killer", "value": [*killer_ids(npc), target_id]},
        )
    ]


def _remove_enemy(npc: Any, target_id: str) -> list[Effect]:
    """从 NPC 敌人表中摘掉一个目标；表空才整只收兵（multi 语义）。"""
    # 原版 `attack.lpc:152-153`：只要目标还在 killer 名单，就拒绝 remove_enemy。
    if killer_chase_enabled() and is_killing(npc, target_id):
        return []
    if not multi_combat_enabled():
        return _reset(npc) if str(getattr(npc, "combat_target", "")) == target_id else []
    remaining = [eid for eid in effective_enemies(npc) if eid != target_id]
    if not remaining:
        return _reset(npc)
    effects = [
        Effect(
            op="set_field",
            data={"entity": npc.id, "field": "combat_enemies", "value": remaining},
        )
    ]
    if str(getattr(npc, "combat_target", "")) == target_id:
        effects.append(
            Effect(
                op="set_field",
                data={"entity": npc.id, "field": "combat_target", "value": remaining[0]},
            )
        )
    return effects


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
        eff_qi: bool = False,
    ):
        self.rules = rules or {}
        self.initiative_gate = bool(initiative_gate)
        self.eff_qi = bool(eff_qi)

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
        mode = str(intent.args.get("mode") or "")
        target_id = str(intent.args.get("target") or "")
        # 规则表只决定“主动开战”。multi 开时，被玩家先打的普通 NPC 也能继续心跳还手
        # （4004 实测：店小二 5/5 还手），所以 continue 不要求它在规则表里。
        # killer 命中的触发/续战同样不要求 aggressive 规则（原版 init 的 hatred）。
        killer_fight = killer_chase_enabled() and bool(target_id) and is_killing(npc, target_id)
        if self._rule(npc) is None and not (mode == "continue" and multi_combat_enabled()):
            if mode not in ("reset", "promote") and not killer_fight:
                return ["not_aggressive"]
        if npc.status != "alive":
            return ["actor_not_alive"]
        if mode == "trigger":
            target_id = str(intent.args.get("target") or "")
            target = state.entities.get(target_id)
            if target is None:
                return ["target_missing"]
            if not target_id.startswith("player:"):
                return ["target_not_player"]
            prof = state.players.get(target_id)
            if prof is not None and (not prof.online or target.presence != "online"):
                return ["target_offline"]
            if target.status != "alive":
                return ["target_not_alive"]
            if target.location != npc.location:
                return ["not_together"]
            if multi_combat_enabled():
                pending = int(getattr(npc, "aggro_tick", 0) or 0)
                if pending and str(getattr(npc, "aggro_target", "")) == target_id:
                    return ["already_fighting"]
                # 只拒绝“这个玩家已经在敌人表里”，不再因为已有主目标就拒绝第二人。
                if target_id in effective_enemies(npc):
                    return ["already_fighting"]
            elif getattr(npc, "aggro_tick", 0) or npc.combat_target:
                return ["already_fighting"]
            if int(npc.max_hp) <= 0 or int(target.max_hp) <= 0:
                return ["no_combat_stats"]
            return []
        if mode == "continue":
            if multi_combat_enabled():
                # 目标有效性由 apply 里 prune；这里不锁单一 combat_target。
                return []
            if str(npc.combat_target) != str(intent.args.get("target") or ""):
                return ["not_fighting_target"]
            return []
        if mode == "reset":
            return []
        if mode == "promote":
            return []
        return ["bad_mode"]

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        npc = state.entities[intent.actor]
        mode = str(intent.args.get("mode") or "")
        target = state.entities.get(str(intent.args.get("target") or ""))

        if mode == "reset":
            return [*_reset(npc), _log(npc, target, "reset", result="ended")]

        if mode == "promote":
            pending = str(getattr(npc, "aggro_target", "") or "")
            target = state.entities.get(pending)
            effects = [
                Effect(op="set_field", data={"entity": npc.id, "field": "aggro_tick", "value": 0}),
                Effect(op="set_field", data={"entity": npc.id, "field": "aggro_target", "value": ""}),
            ]
            registered = False
            if target is not None and target.status == "alive" and target.location == npc.location:
                enemies = effective_enemies(npc)
                if multi_combat_enabled():
                    if pending not in enemies:
                        effects.append(
                            Effect(
                                op="set_field",
                                data={
                                    "entity": npc.id,
                                    "field": "combat_enemies",
                                    "value": [*enemies, pending],
                                },
                            )
                        )
                    if not npc.combat_target:
                        effects.extend(
                            [
                                Effect(
                                    op="set_field",
                                    data={"entity": npc.id, "field": "combat_target", "value": pending},
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
                else:
                    effects.extend(
                        [
                            Effect(
                                op="set_field",
                                data={"entity": npc.id, "field": "combat_target", "value": pending},
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
                registered = True
            if registered:
                # 延迟窗口结束才真正执行 `kill_ob`，此时才写 killer。
                effects.extend(_add_killer(npc, pending))
            effects.append(
                _log(npc, target, "promote", result="engaged" if registered else "target_gone")
            )
            return effects

        if mode == "trigger":
            rule = self._rule(npc)
            if target is None:
                return _reset(npc)
            if rule is None:
                # 原版 `attack.lpc:262-264` init()：killer 命中直接 auto_fight(hatred)，
                # 不掷骰、不看 attitude。
                if not (killer_chase_enabled() and is_killing(npc, str(target.id))):
                    return _reset(npc)
                triggered, info = True, {"kind": "hatred", "result": "attack"}
            else:
                triggered, info = decide(rule, npc, target, rng)
            effects: list[Effect] = []
            enemies = effective_enemies(npc)
            delay = max(0, int(rule.get("delay") or 0)) if rule else 0
            if triggered and delay > 0:
                # 原版自定义 `init()` 的 `call_out("kill_ob", 1, ob)`：先记待生效，
                # 下一个心跳才登记敌人；窗口内 touxi/ansuan 仍可出手。
                effects.extend(
                    [
                        Effect(
                            op="set_field",
                            data={
                                "entity": npc.id,
                                "field": "aggro_tick",
                                "value": int(intent.tick) + delay,
                            },
                        ),
                        Effect(
                            op="set_field",
                            data={"entity": npc.id, "field": "aggro_target", "value": str(target.id)},
                        ),
                    ]
                )
            elif triggered:
                if multi_combat_enabled():
                    # 原版 `enemy += ({ob})`：登记全部合法玩家，不去重覆盖已有目标。
                    if str(target.id) not in enemies:
                        effects.append(
                            Effect(
                                op="set_field",
                                data={
                                    "entity": npc.id,
                                    "field": "combat_enemies",
                                    "value": [*enemies, str(target.id)],
                                },
                            )
                        )
                        enemies = [*enemies, str(target.id)]
                    # `combat_target` 只在空表时初始化；已有主目标不因第二人进场而改。
                    if not npc.combat_target:
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
                else:
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
            # 原版 `kill_ob` 执行即写 killer；延迟窗口只在 promote 时写。
            if triggered and delay <= 0:
                effects.extend(_add_killer(npc, str(target.id)))
            log_kwargs: dict[str, Any] = {
                "triggered": triggered,
                "tick": int(intent.tick),
                **info,
            }
            if delay:
                log_kwargs["delay"] = delay
                log_kwargs["aggro_tick"] = int(intent.tick) + delay
            if multi_combat_enabled():
                log_kwargs["targets"] = enemies
            effects.append(_log(npc, target, "trigger", **log_kwargs))
            return effects

        # continue：一个心跳回合。原版 char.lpc 先判 wimpy 自动逃跑，再攻击。
        multi = multi_combat_enabled()
        if multi:
            candidates = effective_enemies(npc)
            valid: list[str] = []
            for eid in candidates:
                ent = state.entities.get(eid)
                prof = state.players.get(eid)
                if ent is None or ent.status != "alive" or ent.location != npc.location:
                    continue
                if prof is not None and not bool(getattr(prof, "online", True)):
                    continue
                valid.append(eid)
            pruned = [eid for eid in candidates if eid not in valid]
            if not valid:
                return [
                    *_reset(npc),
                    _log(npc, target, "reset", result="target_gone", pruned=pruned),
                ]
            effects: list[Effect] = []
            if pruned:
                effects.append(
                    Effect(
                        op="set_field",
                        data={"entity": npc.id, "field": "combat_enemies", "value": valid},
                    )
                )
            # 原版 `attack.lpc:139-140`：先 `random(4)`，越界回退第 0 个。
            # 刻意偏差：选敌骰走独立盐，不消耗本回合战斗流（见 spec 已知偏差）。
            opp_rng = PRNG(rng.seed, rng.seq, "action:npc_aggression:opponent")
            opp_index, opp_roll = select_index(len(valid), opp_rng)
            target = state.entities[valid[opp_index]]
            if str(npc.combat_target) != str(target.id):
                effects.append(
                    Effect(
                        op="set_field",
                        data={"entity": npc.id, "field": "combat_target", "value": str(target.id)},
                    )
                )
            round_no = int(npc.combat_round) + 1
            wimpy_effects, escaped = _wimpy_escape(state, npc, target, rng, intent)
            if escaped:
                effects.extend(wimpy_effects)
                effects.append(
                    _log(
                        npc,
                        target,
                        "continue",
                        triggered=False,
                        result="escaped",
                        round=round_no,
                        reason="wimpy",
                        targets=valid,
                        opponent_index=opp_index,
                        opponent_roll=opp_roll,
                        pruned=pruned,
                    )
                )
                return effects
            effects.extend(
                [
                    *wimpy_effects,
                    Effect(
                        op="set_field",
                        data={"entity": npc.id, "field": "combat_round", "value": round_no},
                    ),
                ]
            )
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
                        "targets": valid,
                        "opponent_index": opp_index,
                        "opponent_roll": opp_roll,
                        "pruned": pruned,
                    },
                    drop_dead_npc_attacker=True,
                    initiative_gate=self.initiative_gate,
                    eff_qi=self.eff_qi,
                )
            )
            dead = {
                str(eff.data.get("entity"))
                for eff in effects
                if eff.op == "entity_status" and eff.data.get("status") == "dead"
            }
            npc_dead = str(npc.id) in dead
            if npc_dead:
                effects.extend(_reset(npc))
            elif str(target.id) in dead:
                # 只摘这一个；其余玩家继续围攻。
                effects.extend(_remove_enemy(npc, str(target.id)))
            effects.append(
                _log(
                    npc,
                    target,
                    "continue",
                    triggered=True,
                    result="ended" if npc_dead else "round",
                    round=round_no,
                    targets=valid,
                    opponent_index=opp_index,
                    opponent_roll=opp_roll,
                    pruned=pruned,
                )
            )
            return effects

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
