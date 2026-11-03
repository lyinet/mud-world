"""交易动词：buy / sell / trade（复式记账，货币只转移或销毁，不增发）。"""
from __future__ import annotations

from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import (
    arg_int,
    arg_str,
    alive,
    business_selling,
    business_with_cash,
    goal_progress,
    memory,
    require_entity,
    sell_price,
    tax_split_effects,
    transaction_tax,
    wallet,
)
from .registry import Action


def _item_list(intent: Intent, key: str) -> list[tuple[str, int]]:
    raw = intent.args.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Reject(f"bad_arg:{key}")
    out: list[tuple[str, int]] = []
    for row in raw:
        if not isinstance(row, dict) or "item" not in row:
            raise Reject(f"bad_arg:{key}")
        item = str(row["item"])
        qty = row.get("qty", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            raise Reject(f"bad_arg:{key}")
        out.append((item, qty))
    return out


class BuyAction(Action):
    verb = "buy"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        item = arg_str(intent, "item")
        qty = arg_int(intent, "qty", 1, minimum=1, maximum=20)
        at = arg_str(intent, "from", required=False) or ent.location
        if at != ent.location:
            return ["wrong_location"]
        if item not in self.content.items:
            return ["unknown_item"]
        seller = business_selling(state, ent.location, item)
        if seller is None:
            return ["item_not_available"]
        price = self.content.item_price(item) * qty
        tax = transaction_tax(self.content, price)
        if state.account_balance(wallet(ent.id)) < price + tax:
            return ["insufficient_money"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        item = arg_str(intent, "item")
        qty = arg_int(intent, "qty", 1, minimum=1, maximum=20)
        seller = business_selling(state, ent.location, item)
        if seller is None:
            raise Reject("item_not_available")
        price = self.content.item_price(item) * qty
        tax = transaction_tax(self.content, price)
        effects: list[Effect] = [
            Effect("transfer", {"src": wallet(ent.id), "dst": seller.cash_account, "amount": price, "reason": "buy"}),
            Effect("item_move", {"src": seller.id, "dst": ent.id, "item": item, "qty": qty}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *memory(state, ent.id, intent.tick, f"在{seller.name}买入{item}x{qty}，花费{price}文", 0.2),
        ]
        if tax > 0:
            effects[1:1] = tax_split_effects(self.content, wallet(ent.id), tax)
        return effects


class SellAction(Action):
    verb = "sell"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        item = arg_str(intent, "item")
        qty = arg_int(intent, "qty", 1, minimum=1, maximum=20)
        at = arg_str(intent, "to", required=False) or ent.location
        if at != ent.location:
            return ["wrong_location"]
        if item not in self.content.items:
            return ["unknown_item"]
        if ent.inventory.get(item, 0) < qty:
            return ["item_not_held"]
        revenue = sell_price(self.content, item) * qty
        buyer = business_with_cash(state, ent.location, revenue)
        if buyer is None:
            return ["no_buyer_cash"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        item = arg_str(intent, "item")
        qty = arg_int(intent, "qty", 1, minimum=1, maximum=20)
        revenue = sell_price(self.content, item) * qty
        buyer = business_with_cash(state, ent.location, revenue)
        if buyer is None:
            raise Reject("no_buyer_cash")
        tax = transaction_tax(self.content, revenue)
        net = revenue - tax
        effects: list[Effect] = [
            Effect("item_move", {"src": ent.id, "dst": buyer.id, "item": item, "qty": qty}),
            Effect("transfer", {"src": buyer.cash_account, "dst": wallet(ent.id), "amount": revenue, "reason": "sell"}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *goal_progress(state, ent.id, "wealth", float(max(0, net)), intent.tick),
            *memory(state, ent.id, intent.tick, f"向{buyer.name}卖出{item}x{qty}，得{revenue}文", 0.2),
        ]
        if tax > 0:
            effects[2:2] = tax_split_effects(self.content, wallet(ent.id), tax)
        return effects


class TradeAction(Action):
    verb = "trade"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        target_id = arg_str(intent, "target")
        target = alive(state, target_id)
        if target.id == ent.id:
            return ["self_trade"]
        if ent.id.startswith("player:") and ent.presence != "online":
            return ["player_offline"]
        if target.id.startswith("player:") and target.presence != "online":
            return ["target_offline"]
        if target.location != ent.location:
            return ["not_together"]
        offer = _item_list(intent, "offer")
        want = _item_list(intent, "want")
        for item, qty in offer:
            if ent.inventory.get(item, 0) < qty:
                return ["offer_not_held"]
        for item, qty in want:
            if target.inventory.get(item, 0) < qty:
                return ["want_not_held"]
        delta = intent.args.get("money_delta", 0)
        if isinstance(delta, bool) or not isinstance(delta, int):
            return ["bad_arg:money_delta"]
        if delta > 0 and state.account_balance(wallet(target.id)) < delta:
            return ["target_insufficient_money"]
        if delta < 0 and state.account_balance(wallet(ent.id)) < -delta:
            return ["insufficient_money"]
        if state.affection_between(ent.id, target.id) < -20:
            return ["hostile"]
        # 玩家 -> NPC 的公平闸门：防止 1 个烧饼换走名剑。
        if ent.id.startswith("player:") and not target.id.startswith("player:"):
            for item, _ in offer + want:
                if item.startswith("service_"):
                    return ["service_item"]
            offer_value = sum(self.content.item_price(item) * qty for item, qty in offer)
            want_value = sum(self.content.item_price(item) * qty for item, qty in want)
            received = offer_value + max(0, -delta)
            given = want_value + max(0, delta)
            ratio = 0.50 if state.affection_between(ent.id, target.id) >= 60 else 0.70
            if received < ratio * given:
                return ["trade_unfair"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        target = alive(state, arg_str(intent, "target"))
        offer = _item_list(intent, "offer")
        want = _item_list(intent, "want")
        delta = int(intent.args.get("money_delta", 0))
        effects: list[Effect] = []
        for item, qty in offer:
            effects.append(Effect("item_move", {"src": ent.id, "dst": target.id, "item": item, "qty": qty}))
        for item, qty in want:
            effects.append(Effect("item_move", {"src": target.id, "dst": ent.id, "item": item, "qty": qty}))
        if delta > 0:
            effects.append(Effect("transfer", {"src": wallet(target.id), "dst": wallet(ent.id), "amount": delta, "reason": "trade"}))
            tax = transaction_tax(self.content, delta)
            if tax > 0:
                effects.extend(tax_split_effects(self.content, wallet(target.id), tax))
        elif delta < 0:
            amount = -delta
            effects.append(Effect("transfer", {"src": wallet(ent.id), "dst": wallet(target.id), "amount": amount, "reason": "trade"}))
            tax = transaction_tax(self.content, amount)
            if tax > 0:
                effects.extend(tax_split_effects(self.content, wallet(ent.id), tax))
        for a, b in ((ent.id, target.id), (target.id, ent.id)):
            effects.append(
                Effect(
                    "relation_upsert",
                    {
                        "a": a,
                        "b": b,
                        "kind": "friend",
                        "affection": 3.0,
                        "affection_delta": 3.0,
                        "trust_delta": 2.0,
                    },
                )
            )
        effects.extend(
            [
                Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
                Effect("set_field", {"entity": target.id, "field": "last_action_tick", "value": intent.tick}),
                *goal_progress(state, ent.id, "bond", 3.0, intent.tick),
                *goal_progress(state, target.id, "bond", 3.0, intent.tick),
                *([*goal_progress(state, ent.id, "wealth", float(delta), intent.tick)] if delta > 0 else []),
                *memory(state, ent.id, intent.tick, f"与{target.name}交易", 0.2),
                *memory(state, target.id, intent.tick, f"与{ent.name}交易", 0.2),
            ]
        )
        return effects
