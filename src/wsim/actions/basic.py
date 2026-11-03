"""基础动词：move / cultivate / work / consume。

动作只读 state，返回 Effect[]；随机数由 Writer 注入的 PRNG 提供。
"""
from __future__ import annotations

from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import (
    arg_int,
    arg_str,
    business_selling,
    cheapest_food,
    employer_of,
    food_item,
    goal_progress,
    income_tax,
    max_skill,
    memory,
    payment_with_shortfall,
    require_entity,
    skill_for,
    tax_split_effects,
    transaction_tax,
    wallet,
)
from .registry import Action


class MoveAction(Action):
    verb = "move"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        to = arg_str(intent, "to")
        loc = state.locations.get(ent.location)
        if loc is None:
            return ["unknown_location"]
        if to not in state.locations:
            return ["unknown_location"]
        if to not in loc.get("exits", []):
            return ["not_adjacent"]
        cost = float(self.content.economy.get("stamina_move", 5))
        if ent.stamina < cost:
            return ["insufficient_stamina"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        to = arg_str(intent, "to")
        cost = float(self.content.economy.get("stamina_move", 5))
        return [
            Effect("set_location", {"entity": ent.id, "to": to}),
            Effect("stamina", {"entity": ent.id, "delta": -cost}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *memory(state, ent.id, intent.tick, f"移动到 {to}", 0.1),
        ]


class CultivateAction(Action):
    verb = "cultivate"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        hours = arg_int(intent, "hours", 2, minimum=1, maximum=8)
        cost = float(self.content.economy.get("stamina_cultivate", 4)) * hours
        if ent.stamina < cost:
            return ["insufficient_stamina"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        hours = arg_int(intent, "hours", 2, minimum=1, maximum=8)
        skill = arg_str(intent, "skill", required=False) or skill_for(self.content, ent.role)
        cost = float(self.content.economy.get("stamina_cultivate", 4)) * hours
        delta = 0.0025 * hours
        before = float(ent.skills.get(skill, 0.0))
        gained = min(delta, max(0.0, 1.0 - before))
        return [
            Effect("stamina", {"entity": ent.id, "delta": -cost}),
            Effect("skill", {"entity": ent.id, "skill": skill, "delta": gained}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *goal_progress(state, ent.id, "mastery", gained, intent.tick),
            *memory(state, ent.id, intent.tick, f"修炼{skill} {hours}小时", 0.25),
        ]


class WorkAction(Action):
    verb = "work"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        at = arg_str(intent, "at", required=False) or ent.location
        hours = arg_int(intent, "hours", 8, minimum=1, maximum=12)
        if at != ent.location:
            return ["not_at_workplace"]
        bid = None
        if intent.args.get("quest") and arg_str(intent, "at", required=False):
            cands = [b for b in state.business_at(at) if b.kind != "service_room"]
            bid = cands[0].id if cands else None
        if bid is None:
            bid = employer_of(state, ent)
        if not bid:
            # 任务代工：允许在指定地点的店铺做短工（不必是长期雇主）。
            cands = [b for b in state.business_at(at) if b.kind != "service_room"]
            bid = cands[0].id if cands else None
        if not bid or bid not in state.businesses:
            return ["no_employer"]
        biz = state.businesses[bid]
        if biz.location != ent.location:
            return ["not_at_workplace"]
        cost = float(self.content.economy.get("stamina_work", 12)) * hours / 8.0
        if ent.stamina < cost:
            return ["insufficient_stamina"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        hours = arg_int(intent, "hours", 8, minimum=1, maximum=12)
        bid = None
        if intent.args.get("quest") and intent.args.get("at"):
            at = str(intent.args["at"])
            cands = [b for b in state.business_at(at) if b.kind != "service_room"]
            bid = cands[0].id if cands else None
        if bid is None:
            bid = employer_of(state, ent)
        if not bid:
            at = arg_str(intent, "at", required=False) or ent.location
            cands = [b for b in state.business_at(at) if b.kind != "service_room"]
            bid = cands[0].id if cands else None
        if not bid or bid not in state.businesses:
            raise Reject("no_employer")
        biz = state.businesses[bid]
        if biz.location != ent.location:
            raise Reject("not_at_workplace")
        gross = max(1, int(round(self.content.wage_for(ent.role) * hours / 8.0)))
        employer_cash = state.account_balance(biz.cash_account)
        received = min(gross, employer_cash)
        tax = income_tax(self.content, received)
        wallet_bal = state.account_balance(wallet(ent.id))
        tax = min(tax, wallet_bal + received)
        net = received - tax
        cost = float(self.content.economy.get("stamina_work", 12)) * hours / 8.0
        skill = skill_for(self.content, ent.role)

        effects: list[Effect] = []
        effects.extend(
            payment_with_shortfall(
                state,
                biz.id,
                ent.id,
                gross,
                debt_id=f"debt:wage:{bid}:{ent.id}",
                created_tick=intent.tick,
                note="unpaid_wage",
                transfer_reason="wage",
            )
        )
        if tax > 0:
            effects.append(Effect("burn", {"account": wallet(ent.id), "amount": tax, "reason": "income_tax"}))
        effects.extend(
            [
                Effect("stamina", {"entity": ent.id, "delta": -cost}),
                Effect("skill", {"entity": ent.id, "skill": skill, "delta": hours * 0.0006}),
                Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
                *goal_progress(state, ent.id, "wealth", float(max(0, net)), intent.tick),
                *memory(
                    state,
                    ent.id,
                    intent.tick,
                    f"在{biz.name}做工{hours}小时，应得{gross}文，实收{received}文，税{tax}文",
                    0.2,
                ),
            ]
        )
        return effects


class ConsumeAction(Action):
    verb = "consume"

    def __init__(self, content):
        self.content = content

    def _seller(self, state: WorldState, ent, item: str):
        return business_selling(state, ent.location, item)

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        item = arg_str(intent, "item", required=False)
        if item is None:
            item = cheapest_food(self.content, ent.location, state)
            if item is None:
                return ["item_not_available"]
        qty = arg_int(intent, "qty", 1, minimum=1, maximum=10)
        if item not in self.content.items:
            return ["unknown_item"]
        seller = self._seller(state, ent, item)
        if seller is None:
            return ["item_not_available"]
        price = self.content.item_price(item) * qty
        tax = transaction_tax(self.content, price)
        if state.account_balance(wallet(ent.id)) < price + tax:
            return ["insufficient_money"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        item = arg_str(intent, "item", required=False) or cheapest_food(self.content, ent.location, state)
        if item is None:
            raise Reject("item_not_available")
        qty = arg_int(intent, "qty", 1, minimum=1, maximum=10)
        seller = self._seller(state, ent, item)
        if seller is None:
            raise Reject("item_not_available")
        price = self.content.item_price(item) * qty
        tax = transaction_tax(self.content, price)
        category = self.content.items[item].get("category", "misc")
        boost = float(self.content.economy.get("food_stamina", 25)) if category in ("food", "drink") else 5.0
        return [
            Effect("transfer", {"src": wallet(ent.id), "dst": seller.cash_account, "amount": price, "reason": "consume"}),
            *tax_split_effects(self.content, wallet(ent.id), tax),
            Effect("item_move", {"src": seller.id, "dst": ent.id, "item": item, "qty": qty}),
            Effect("item_consume", {"owner": ent.id, "item": item, "qty": qty}),
            Effect("stamina", {"entity": ent.id, "delta": boost}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *memory(state, ent.id, intent.tick, f"在{seller.name}消费{item}x{qty}，花费{price}文", 0.15),
        ]
