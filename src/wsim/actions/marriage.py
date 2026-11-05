"""玩家婚姻闭环：求婚、裁决、成婚、日常陪伴、和离。

约束：
- 纯函数，不读墙钟、不使用随机；裁决只依赖状态与内容包。
- 状态变更全部经 Effect 落库，不直接修改 state。
- NPC-NPC 旧婚姻仍走 `MarryAction`，本模块只在玩家参与时生效。
"""
from __future__ import annotations

from ..core.clock import TICKS_PER_DAY
from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import alive, arg_int, arg_str, memory, require_entity, wallet
from .registry import Action

DECISION_ACCEPT = "accept"
DECISION_REJECT = "reject"
DECISION_COURTSHIP = "courtship"

AFFECTION_ACCEPT = 60.0
TRUST_ACCEPT = 30.0
AFFECTION_FLOOR = 40.0
TRUST_FLOOR = 20.0
COURTSHIP_GAP_MIN = 5
COURTSHIP_GAP_MAX = 20
COURTSHIP_TICKS = TICKS_PER_DAY * 3

THRESHOLDS = {
    "affection_accept": AFFECTION_ACCEPT,
    "trust_accept": TRUST_ACCEPT,
    "affection_floor": AFFECTION_FLOOR,
    "trust_floor": TRUST_FLOOR,
    "courtship_gap_min": COURTSHIP_GAP_MIN,
    "courtship_gap_max": COURTSHIP_GAP_MAX,
}


def pair_key(a: str, b: str) -> str:
    return f"{a}|{b}" if a <= b else f"{b}|{a}"


def trust_between(state: WorldState, a: str, b: str) -> float:
    vals: list[float] = []
    for kind in ("friend", "family", "master", "disciple", "marriage", "apprentice", "ally", "acquaintance"):
        rab = state.relation(a, b, kind)
        rba = state.relation(b, a, kind)
        if rab is not None:
            vals.append(rab.trust)
        if rba is not None:
            vals.append(rba.trust)
    return max(vals) if vals else 0.0


def active_marriage(state: WorldState, eid: str) -> bool:
    return any(
        r.kind == "marriage" and r.status == "active" and eid in (r.a, r.b)
        for r in state.relations.values()
    )


def married(state: WorldState, eid: str) -> bool:
    ent = state.entities.get(eid)
    return bool(ent and (ent.spouse is not None or active_marriage(state, eid)))


def structural_reason(
    state: WorldState,
    content,
    actor: str,
    target: str,
    gift_money: int = 0,
    gift_item: str | None = None,
    gift_qty: int = 1,
) -> str | None:
    """求婚的结构性前置校验；返回原因码或 None。

    只做和 NPC 决策无关的硬条件，保证被拒时不会写任何婚姻/资产状态。
    """
    if actor == target:
        return "self_propose"
    ea = state.entities.get(actor)
    if ea is None:
        return "unknown_actor"
    if ea.status != "alive":
        return "actor_not_alive"
    eb = state.entities.get(target)
    if eb is None:
        return "unknown_target"
    if str(target).startswith("player:"):
        return "target_not_npc"
    if eb.status != "alive":
        return "target_not_alive"
    if ea.location != eb.location:
        return "not_together"
    if married(state, actor):
        return "already_married"
    if married(state, target):
        return "target_in_couple"
    if gift_money < 0:
        return "bad_arg:gift_money"
    if gift_money > state.account_balance(wallet(actor)):
        return "not_enough_money"
    if gift_item:
        if gift_item not in content.items:
            return "gift_missing"
        if int(ea.inventory.get(gift_item, 0)) < gift_qty:
            return "gift_missing"
    return None


def decide(
    state: WorldState,
    actor: str,
    target: str,
) -> dict[str, object]:
    """确定性 NPC 裁决，返回可审计的判定依据。"""
    affection = float(state.affection_between(actor, target))
    trust = float(trust_between(state, actor, target))
    base = {
        "affection": round(affection, 2),
        "trust": round(trust, 2),
        "thresholds": dict(THRESHOLDS),
    }
    if trust < TRUST_FLOOR:
        return {**base, "decision": DECISION_REJECT, "reason_code": "trust_too_low"}
    if affection < AFFECTION_FLOOR:
        return {**base, "decision": DECISION_REJECT, "reason_code": "bond_too_low"}
    if affection >= AFFECTION_ACCEPT and trust >= TRUST_ACCEPT:
        return {**base, "decision": DECISION_ACCEPT, "reason_code": "accepted"}
    if TRUST_FLOOR <= trust < TRUST_ACCEPT and affection >= AFFECTION_FLOOR:
        return {**base, "decision": DECISION_COURTSHIP, "reason_code": "courtship"}
    gap = AFFECTION_ACCEPT - affection
    if COURTSHIP_GAP_MIN <= gap <= COURTSHIP_GAP_MAX:
        return {**base, "decision": DECISION_COURTSHIP, "reason_code": "courtship"}
    # 好感缺口 1–4 点：仍按门槛不足拒绝；缺口 >20 已在上面拒绝。
    return {**base, "decision": DECISION_REJECT, "reason_code": "bond_too_low"}


def _audit(actor: str, tick: int, verdict: dict[str, object], note: str) -> Effect:
    return Effect(
        "audit_log",
        {
            "kind": "marriage_decision",
            "actor": actor,
            "tick": tick,
            "fields": [
                f"affection={verdict['affection']}",
                f"trust={verdict['trust']}",
                f"decision={verdict['decision']}",
                f"reason={verdict['reason_code']}",
            ],
            "note": note,
        },
    )


class MarriageProposeAction(Action):
    verb = "marriage_propose"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        target = arg_str(intent, "target")
        gift_money = arg_int(intent, "gift_money", default=0, minimum=0, maximum=100000)
        gift_item = arg_str(intent, "gift_item", required=False)
        gift_qty = arg_int(intent, "gift_qty", default=1, minimum=1, maximum=20)
        code = structural_reason(
            state, self.content, intent.actor, target, gift_money, gift_item, gift_qty
        )
        return [code] if code else []

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        target = arg_str(intent, "target")
        gift_money = arg_int(intent, "gift_money", default=0, minimum=0, maximum=100000)
        gift_item = arg_str(intent, "gift_item", required=False)
        gift_qty = arg_int(intent, "gift_qty", default=1, minimum=1, maximum=20)
        ea = require_entity(state, intent.actor)
        eb = alive(state, target)
        verdict = decide(state, ea.id, eb.id)
        tick = intent.tick
        decision = str(verdict["decision"])
        reason_code = str(verdict["reason_code"])
        thresholds = dict(verdict["thresholds"])

        if decision == DECISION_ACCEPT:
            ntext = f"{ea.name}向我求婚，我答应了。"
            ptext = f"我向{eb.name}求婚，对方答应了。"
            status = "proposed"
            expires = None
        elif decision == DECISION_COURTSHIP:
            ntext = f"{ea.name}向我求婚，我说再处处看。"
            ptext = f"我向{eb.name}求婚，对方说再处处看。"
            status = "courtship"
            expires = tick + COURTSHIP_TICKS
        else:
            ntext = f"{ea.name}向我求婚，我婉拒了。"
            ptext = f"我向{eb.name}求婚，对方婉拒了。"
            status = "rejected"
            expires = None

        meta: dict[str, object] = {
            "a": ea.id,
            "b": eb.id,
            "status": status,
            "proposal_tick": tick,
            "decision": decision,
            "reason_code": reason_code,
            "thresholds": thresholds,
            "gift_money": gift_money,
            "gift_item": gift_item or "",
            "gift_qty": gift_qty if gift_item else 0,
        }
        if expires is not None:
            meta["since_tick"] = tick
            meta["expires_tick"] = expires

        effects: list[Effect] = [
            Effect("marriage_meta", meta),
            *_memory_pair(
                intent.actor,
                target,
                tick,
                ntext,
                ptext,
                importance=0.5,
            ),
            _audit(ea.id, tick, verdict, f"propose:{decision}:{reason_code}"),
        ]
        return effects


class MarriageAcceptAction(Action):
    verb = "marriage_accept"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        player = arg_str(intent, "player")
        target = arg_str(intent, "target")
        if target != intent.actor:
            return ["target_mismatch"]
        for eid in (player, target):
            ent = state.entities.get(eid)
            if ent is None or ent.status != "alive":
                return ["unknown_target"]
        if state.entities[player].spouse is not None or state.entities[target].spouse is not None:
            return ["already_married"]
        if state.entities[player].location != state.entities[target].location:
            return ["not_together"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        player = arg_str(intent, "player")
        target = arg_str(intent, "target")
        pe = require_entity(state, player)
        ne = require_entity(state, target)
        return [
            Effect(
                "marriage_meta",
                {
                    "a": pe.id,
                    "b": ne.id,
                    "status": "accepted",
                    "decision": DECISION_ACCEPT,
                    "reason_code": "accepted",
                },
            ),
            *memory(state, ne.id, intent.tick, f"我答应了{pe.name}的求婚。", 0.6),
        ]


class MarriageRejectAction(Action):
    verb = "marriage_reject"

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        # 拒绝不改变 spouse、不转账；事件本身以 rejected 落库，供审计。
        code = str(intent.args.get("reason_code") or "bond_too_low")
        raise Reject(code)


class MarriageAction(Action):
    verb = "marriage"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        a = str(intent.args.get("a") or "")
        b = str(intent.args.get("b") or "")
        if not a or not b or a == b:
            return ["bad_args"]
        ea = state.entities.get(a)
        eb = state.entities.get(b)
        if ea is None or eb is None or ea.status != "alive" or eb.status != "alive":
            return ["unknown_target"]
        if a not in state.entities or b not in state.entities:
            return ["unknown_target"]
        if ea.location != eb.location:
            return ["not_together"]
        if ea.spouse is not None or eb.spouse is not None:
            return ["already_married"]
        if active_marriage(state, a) or active_marriage(state, b):
            return ["already_married"]
        gift_money = int(intent.args.get("gift_money") or 0)
        gift_item = str(intent.args.get("gift_item") or "")
        gift_qty = int(intent.args.get("gift_qty") or 0)
        if gift_money < 0 or gift_money > state.account_balance(wallet(a)):
            return ["not_enough_money"]
        if gift_item and int(ea.inventory.get(gift_item, 0)) < gift_qty:
            return ["gift_missing"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        a = str(intent.args["a"])
        b = str(intent.args["b"])
        gift_money = int(intent.args.get("gift_money") or 0)
        gift_item = str(intent.args.get("gift_item") or "")
        gift_qty = int(intent.args.get("gift_qty") or 0)
        ea = require_entity(state, a)
        eb = require_entity(state, b)
        effects: list[Effect] = []
        if gift_money > 0:
            effects.append(
                Effect(
                    "transfer",
                    {
                        "src": wallet(a),
                        "dst": wallet(b),
                        "amount": gift_money,
                        "reason": "marriage_dowry",
                    },
                )
            )
        if gift_item and gift_qty > 0:
            effects.append(
                Effect(
                    "item_move",
                    {"src": a, "dst": b, "item": gift_item, "qty": gift_qty},
                )
            )
        effects.extend(
            [
                Effect("marry", {"a": a, "b": b}),
                Effect(
                    "marriage_meta",
                    {
                        "a": a,
                        "b": b,
                        "status": "married",
                        "since_tick": intent.tick,
                        "proposal_tick": int(intent.args.get("proposal_tick") or intent.tick),
                        "decision": DECISION_ACCEPT,
                        "reason_code": "married",
                        "thresholds": dict(THRESHOLDS),
                        "gift_money": gift_money,
                        "gift_item": gift_item,
                        "gift_qty": gift_qty if gift_item else 0,
                    },
                ),
                *_memory_pair(
                    a,
                    b,
                    intent.tick,
                    f"我和{ea.name}成亲了。",
                    f"我和{eb.name}成亲了。",
                    importance=0.9,
                ),
            ]
        )
        return effects


class MarriageEndAction(Action):
    verb = "marriage_end"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = state.entities.get(intent.actor)
        if ent is None or ent.status != "alive":
            return ["unknown_actor"]
        if ent.spouse is None:
            return ["not_married"]
        other = state.entities.get(ent.spouse)
        if other is None or other.status != "alive":
            return ["spouse_not_alive"]
        if ent.location != other.location:
            return ["spouse_not_here"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        spouse = state.entities[ent.spouse]  # preconditions 已保证存在
        tick = intent.tick
        effects: list[Effect] = [
            Effect("set_field", {"entity": ent.id, "field": "spouse", "value": None}),
            Effect("set_field", {"entity": spouse.id, "field": "spouse", "value": None}),
            Effect(
                "relation_upsert",
                {
                    "a": ent.id,
                    "b": spouse.id,
                    "kind": "marriage",
                    "status": "ended",
                },
            ),
            Effect(
                "relation_upsert",
                {
                    "a": spouse.id,
                    "b": ent.id,
                    "kind": "marriage",
                    "status": "ended",
                },
            ),
            Effect(
                "marriage_meta",
                {
                    "a": ent.id,
                    "b": spouse.id,
                    "status": "ended",
                    "ended_tick": tick,
                    "decision": "ended",
                    "reason_code": "marriage_end",
                },
            ),
            *_memory_pair(
                ent.id,
                spouse.id,
                tick,
                f"我和{ent.name}和离了。",
                f"我和{spouse.name}和离了。",
                importance=0.8,
            ),
        ]
        return effects


def _memory_pair(a: str, b: str, tick: int, text_for_b: str, text_for_a: str, importance: float) -> list[Effect]:
    return [
        Effect(
            "memory_add",
            {"npc": b, "tick": tick, "text": text_for_b, "importance": importance},
        ),
        Effect(
            "memory_add",
            {"npc": a, "tick": tick, "text": text_for_a, "importance": importance},
        ),
    ]


class SpouseTimeAction(Action):
    """配偶每日陪伴：同日问候、带饭或提醒日常小事。仅玩家-NPC 婚姻触发。"""

    verb = "spouse_time"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        actor = intent.actor
        target = arg_str(intent, "target")
        ea = state.entities.get(actor)
        if ea is None or ea.status != "alive":
            return ["unknown_actor"]
        if not str(target).startswith("player:"):
            return ["target_not_player"]
        eb = state.entities.get(target)
        if eb is None or eb.status != "alive":
            return ["unknown_target"]
        if ea.spouse != target or eb.spouse != actor:
            return ["not_spouse"]
        if ea.location != eb.location:
            return ["not_together"]
        if eb.presence == "offline":
            return ["spouse_offline"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        actor = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        tick = intent.tick
        day = tick // TICKS_PER_DAY
        effects: list[Effect] = []

        food = None
        for iid in sorted(actor.inventory):
            item = self.content.items.get(iid) or {}
            if item.get("category") in ("food", "drink") and int(actor.inventory.get(iid, 0)) > 0:
                food = iid
                break
        if food is not None:
            name = str((self.content.items.get(food) or {}).get("name", food))
            note = f"{actor.name}给你带了{name}，让你趁热吃。"
            effects.append(Effect("item_move", {"src": actor.id, "dst": target.id, "item": food, "qty": 1}))
        else:
            lines = (
                f"{actor.name}提醒你今日该缴租了，账本放在柜上。",
                f"{actor.name}把家里收拾好了，说等你回来就歇着。",
                f"{actor.name}午后去了市集，说晚上等你一起吃饭。",
            )
            note = lines[day % len(lines)]

        for x, y in ((actor.id, target.id), (target.id, actor.id)):
            effects.append(
                Effect(
                    "relation_upsert",
                    {
                        "a": x,
                        "b": y,
                        "kind": "marriage",
                        "affection_delta": 1.0,
                        "trust_delta": 1.0,
                    },
                )
            )
        effects.extend(
            [
                Effect(
                    "memory_add",
                    {"npc": target.id, "tick": tick, "text": note, "importance": 0.4, "source": "spouse:daily"},
                ),
                Effect(
                    "memory_add",
                    {"npc": actor.id, "tick": tick, "text": note, "importance": 0.3, "source": "spouse:daily"},
                ),
                Effect(
                    "marriage_meta",
                    {
                        "a": actor.id,
                        "b": target.id,
                        "status": "married",
                        "last_daily_tick": tick,
                        "daily_note": note,
                    },
                ),
            ]
        )
        return effects
