"""社交/组织动词：apprentice（仅拜师）、join_sect（入会/入门派）、marry、talk。"""
from __future__ import annotations

from ..agent.dialogue import PLAYER_ID, PLAYER_NAME
from ..core.clock import TICKS_PER_DAY
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


def _clamp_delta(requested: float, tick_used: float, day_used: float) -> float:
    """同时受「单 tick ±2」与「单日 ±8」余量约束。"""
    lower = max(-2.0 - tick_used, -8.0 - day_used)
    upper = min(2.0 - tick_used, 8.0 - day_used)
    if lower > upper:
        return 0.0
    return max(lower, min(upper, requested))


class TalkAction(Action):
    """玩家发言落地：写 NPC 记忆 + 小幅关系变化；不产生任何钱变动。"""

    verb = "talk"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        alive(state, arg_str(intent, "target"))
        message = intent.args.get("message")
        reply = intent.args.get("reply")
        if not isinstance(message, str) or not message.strip():
            return ["bad_message"]
        if len(message) > 200:
            return ["bad_message"]
        if not isinstance(reply, str) or not reply.strip():
            return ["missing_reply"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        target = alive(state, arg_str(intent, "target"))
        message = str(intent.args["message"]).strip()
        reply = str(intent.args["reply"]).strip()
        try:
            req_aff = float(intent.args.get("affection_delta", 0.0))
            req_trust = float(intent.args.get("trust_delta", 0.0))
        except (TypeError, ValueError) as exc:
            raise Reject("bad_arg:deltas") from exc

        tick = int(intent.tick)
        day = tick // TICKS_PER_DAY
        ledger = dict(state.talk_ledger.get(target.id) or {})
        day_aff = float(ledger.get("day_affection", 0.0))
        day_trust = float(ledger.get("day_trust", 0.0))
        tick_aff = float(ledger.get("tick_affection", 0.0))
        tick_trust = float(ledger.get("tick_trust", 0.0))
        if int(ledger.get("day", day)) != day:
            day_aff = day_trust = 0.0
            tick_aff = tick_trust = 0.0
        if int(ledger.get("last_tick", tick)) != tick:
            tick_aff = tick_trust = 0.0

        d_aff = _clamp_delta(req_aff, tick_aff, day_aff)
        d_trust = _clamp_delta(req_trust, tick_trust, day_trust)

        effects: list[Effect] = []
        if PLAYER_ID not in state.entities:
            effects.append(
                Effect(
                    "create_entity",
                    {
                        "id": PLAYER_ID,
                        "name": PLAYER_NAME,
                        "role": "visitor",
                        "location": target.location,
                        "stamina": 100.0,
                        "max_stamina": 100.0,
                    },
                )
            )

        effects.append(
            Effect(
                "memory_add",
                {
                    "npc": target.id,
                    "tick": tick,
                    "text": f"{PLAYER_NAME}说：{message}",
                    "importance": 0.4,
                    "source": "talk:player",
                },
            )
        )
        effects.append(
            Effect(
                "memory_add",
                {
                    "npc": target.id,
                    "tick": tick,
                    "text": f"我答：{reply}",
                    "importance": 0.3,
                    "source": "talk:npc",
                },
            )
        )

        existing = state.relation(PLAYER_ID, target.id, "acquaintance")
        relation = {
            "a": PLAYER_ID,
            "b": target.id,
            "kind": "acquaintance",
        }
        if existing is None:
            relation["affection"] = max(-100.0, min(100.0, d_aff))
            relation["trust"] = max(-100.0, min(100.0, 40.0 + d_trust))
        else:
            relation["affection_delta"] = d_aff
            relation["trust_delta"] = d_trust
        effects.append(Effect("relation_upsert", relation))
        effects.append(
            Effect(
                "talk_ledger",
                {
                    "npc": target.id,
                    "day": day,
                    "day_affection": day_aff + d_aff,
                    "day_trust": day_trust + d_trust,
                    "last_tick": tick,
                    "tick_affection": tick_aff + d_aff,
                    "tick_trust": tick_trust + d_trust,
                },
            )
        )
        effects.append(Effect("set_field", {"entity": target.id, "field": "last_action_tick", "value": tick}))
        effects.append(Effect("set_field", {"entity": PLAYER_ID, "field": "last_action_tick", "value": tick}))
        return effects


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
