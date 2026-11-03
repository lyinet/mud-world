"""社交/组织动词：apprentice（仅拜师）、join_sect（入会/入门派）、marry。"""
from __future__ import annotations

from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import (
    alive,
    arg_int,
    arg_str,
    goal_progress,
    max_skill,
    memory,
    organization_at,
    require_entity,
    wallet,
)
from .registry import Action


class ApprenticeAction(Action):
    verb = "apprentice"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        arg_int(intent, "duration_days", 7, minimum=7, maximum=365)
        if target.id == ent.id:
            return ["self_apprentice"]
        if target.location != ent.location:
            return ["not_together"]
        if ent.master is not None:
            return ["already_has_master"]
        if target.master == ent.id:
            return ["cycle"]
        cur = target
        seen = {ent.id}
        while cur.master:
            if cur.master in seen:
                return ["cycle"]
            seen.add(cur.master)
            nxt = state.entities.get(cur.master)
            if nxt is None:
                break
            cur = nxt
        if max_skill(target) < 0.5:
            return ["master_not_qualified"]
        if max_skill(target) < max_skill(ent) + 0.1:
            return ["master_not_qualified"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        return [
            Effect(
                "relation_upsert",
                {
                    "a": target.id,
                    "b": ent.id,
                    "kind": "master",
                    "affection": 5.0,
                    "affection_delta": 5.0,
                    "trust_delta": 5.0,
                },
            ),
            Effect(
                "relation_upsert",
                {
                    "a": ent.id,
                    "b": target.id,
                    "kind": "disciple",
                    "affection": 5.0,
                    "affection_delta": 5.0,
                    "trust_delta": 5.0,
                },
            ),
            Effect("add_disciple", {"master": target.id, "disciple": ent.id}),
            Effect("set_field", {"entity": ent.id, "field": "master", "value": target.id}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *goal_progress(state, ent.id, "bond", 5.0, intent.tick),
            *memory(state, ent.id, intent.tick, f"拜{target.name}为师", 0.6),
            *memory(state, target.id, intent.tick, f"收{ent.name}为徒", 0.6),
        ]


class JoinSectAction(Action):
    verb = "join_sect"

    def __init__(self, content):
        self.content = content

    def _faction(self, state: WorldState, target: str):
        if target in state.factions:
            return state.factions[target]
        if target in state.locations:
            return organization_at(state, target)
        return None

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        target = arg_str(intent, "target")
        dues = arg_int(intent, "dues", 0, minimum=0, maximum=100000)
        fac = self._faction(state, target)
        if fac is None:
            return ["unknown_sect"]
        if fac.location and fac.location != ent.location:
            return ["not_at_sect"]
        if ent.faction is not None and ent.faction != fac.id:
            return ["already_in_faction"]
        if ent.id in fac.members:
            return ["already_member"]
        if state.account_balance(wallet(ent.id)) < dues:
            return ["insufficient_money"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        target = arg_str(intent, "target")
        dues = arg_int(intent, "dues", 0, minimum=0, maximum=100000)
        fac = self._faction(state, target)
        if fac is None:
            raise Reject("unknown_sect")
        effects: list[Effect] = [Effect("faction_join", {"entity": ent.id, "faction": fac.id})]
        if dues > 0:
            effects.append(Effect("transfer", {"src": wallet(ent.id), "dst": fac.treasury_account, "amount": dues, "reason": "sect_dues"}))
        if fac.leader and fac.leader != ent.id:
            for x, y in ((ent.id, fac.leader), (fac.leader, ent.id)):
                effects.append(
                    Effect(
                        "relation_upsert",
                        {"a": x, "b": y, "kind": "ally", "affection": 3.0, "affection_delta": 3.0},
                    )
                )
        effects.extend(
            [
                Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
                *goal_progress(state, ent.id, "faction", 1.0, intent.tick),
                *memory(state, ent.id, intent.tick, f"加入{fac.name}", 0.5),
            ]
        )
        return effects


class RecruitAction(Action):
    """真实招揽：同地点、面对面把对方拉进门派（sworn 关系）。

    开宗立派前没有 faction，recruit 先建立 sworn 关系；found_sect 只认这些
    由真实动作建立的成员，不再靠开局关系一次性凑数。
    """

    verb = "recruit"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        if target.id == ent.id:
            return ["self_recruit"]
        if target.location != ent.location:
            return ["not_together"]
        if target.faction and str(target.faction).startswith("sect:"):
            return ["target_in_sect"]
        if any(
            r.kind == "sworn" and r.status == "active" and r.a == ent.id and r.b == target.id
            for r in state.relations.values()
        ):
            return ["already_sworn"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        current = state.affection_between(ent.id, target.id)
        delta = max(1.0, 30.0 - current)
        effects: list[Effect] = [
            Effect(
                "relation_upsert",
                {"a": ent.id, "b": target.id, "kind": "sworn", "affection": 30.0,
                 "affection_delta": delta, "trust": 45.0},
            ),
            Effect(
                "relation_upsert",
                {"a": target.id, "b": ent.id, "kind": "sworn", "affection": 30.0,
                 "affection_delta": delta, "trust": 45.0},
            ),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            # 招人只是进度，不能把 found_sect 目标提前判成完成：
            # 5 次招揽才累计 0.5，真正的 1.0 由 found_sect 动作给出。
            *goal_progress(state, ent.id, "faction", 0.1, intent.tick),
            *memory(state, ent.id, intent.tick, f"招揽{target.name}入伙", 0.5),
            *memory(state, target.id, intent.tick, f"答应与{ent.name}共举门派", 0.4),
        ]
        if ent.faction:
            effects.insert(2, Effect("faction_join", {"entity": target.id, "faction": ent.faction}))
        return effects


class MarryAction(Action):
    verb = "marry"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        if target.id == ent.id:
            return ["self_marriage"]
        if target.location != ent.location:
            return ["not_together"]
        if ent.spouse is not None or target.spouse is not None:
            return ["already_married"]
        for e in (ent, target):
            if any(
                r.kind == "marriage" and r.status == "active" and e.id in (r.a, r.b)
                for r in state.relations.values()
            ):
                return ["already_married"]
        if state.affection_between(ent.id, target.id) < 60.0:
            return ["affection_too_low"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        return [
            Effect("marry", {"a": ent.id, "b": target.id}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            Effect("set_field", {"entity": target.id, "field": "last_action_tick", "value": intent.tick}),
            *goal_progress(state, ent.id, "bond", 10.0, intent.tick),
            *goal_progress(state, target.id, "bond", 10.0, intent.tick),
            *memory(state, ent.id, intent.tick, f"与{target.name}成婚", 0.9),
            *memory(state, target.id, intent.tick, f"与{ent.name}成婚", 0.9),
        ]
