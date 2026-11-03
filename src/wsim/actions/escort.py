"""押镖动词与到达结算系统动作。"""
from __future__ import annotations

from ..core.models import Effect, Intent, Reject
from ..core.clock import TICKS_PER_HOUR
from ..world.state import WorldState
from .common import (
    arg_int,
    arg_str,
    escort_business,
    goal_progress,
    memory,
    payment_with_shortfall,
    require_entity,
    role_allowed,
    transaction_tax,
    wallet,
)
from .registry import Action


class EscortAction(Action):
    verb = "escort"

    def __init__(self, content):
        self.content = content

    def _route(self, intent: Intent) -> list[str]:
        raw = intent.args.get("route")
        if not isinstance(raw, list) or len(raw) < 2:
            raise Reject("bad_arg:route")
        route = [str(x) for x in raw]
        return route

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        if not role_allowed(self.content, "escort", ent.role):
            return ["role_not_allowed"]
        route = self._route(intent)
        if route[0] != ent.location:
            return ["not_at_origin"]
        for a, b in zip(route, route[1:]):
            if a not in state.locations or b not in state.locations:
                return ["unknown_location"]
            if b not in state.locations[a].get("exits", []):
                return ["route_not_adjacent"]
        if ent.escort_id is not None:
            return ["already_on_escort"]
        cargo = arg_int(intent, "cargo_value", 100, minimum=100, maximum=100000)
        fee = arg_int(intent, "fee", 0, minimum=1, maximum=100000)
        agency = escort_business(state, route[0])
        if agency is None:
            return ["no_escort_agency"]
        if state.account_balance(agency.cash_account) < fee:
            return ["agency_insufficient_cash"]
        if cargo < int((self.content.economy.get("escort") or {}).get("cargo_min_value", 100)):
            return ["cargo_too_low"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        route = self._route(intent)
        cargo = arg_int(intent, "cargo_value", 100, minimum=100, maximum=100000)
        fee = arg_int(intent, "fee", 0, minimum=1, maximum=100000)
        agency = escort_business(state, route[0])
        if agency is None:
            raise Reject("no_escort_agency")
        cfg = self.content.economy.get("escort") or {}
        risk = float(cfg.get("risk_loss_prob", 0.15))
        duration = int(cfg.get("duration_hours", 24)) * TICKS_PER_HOUR
        success = not rng.chance(risk)
        oid = f"escort:{ent.id}:{intent.tick}"
        return [
            Effect(
                "escort_order_create",
                {
                    "id": oid,
                    "origin": route[0],
                    "destination": route[-1],
                    "cargo_value": cargo,
                    "fee": fee,
                    "deposit": max(1, fee),
                    "created_tick": intent.tick,
                },
            ),
            Effect(
                "escort_start",
                {
                    "id": oid,
                    "carrier": ent.id,
                    "success": success,
                    "arrive_tick": intent.tick + duration,
                },
            ),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *memory(
                state,
                ent.id,
                intent.tick,
                f"接下{route[0]}->{route[-1]}的镖单，镖银{fee}文，{'预计顺利' if success else '预感有险'}",
                0.5,
            ),
        ]


class SystemEscortArriveAction(Action):
    verb = "system_escort_arrive"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        effects: list[Effect] = []
        cfg = self.content.economy.get("escort") or {}
        cut = float(cfg.get("agency_cut", 0.30))
        for oid in sorted(state.escorts):
            order = state.escorts[oid]
            if order.status != "active" or order.arrive_tick > intent.tick:
                continue
            carrier = state.entities.get(order.carrier or "")
            if carrier is None or carrier.status != "alive":
                effects.append(Effect("escort_settle", {"id": oid}))
                continue
            agency = escort_business(state, order.origin) or escort_business(state)
            if order.success:
                share = max(1, order.fee - int(order.fee * cut))
                if agency is not None:
                    effects.extend(
                        payment_with_shortfall(
                            state,
                            agency.id,
                            carrier.id,
                            share,
                            debt_id=f"debt:escort:{oid}",
                            created_tick=intent.tick,
                            note="escort_fee",
                        )
                    )
                    tax = transaction_tax(self.content, share)
                    bal = state.account_balance(wallet(carrier.id))
                    if agency is not None and state.account_balance(agency.cash_account) >= share:
                        tax = min(tax, bal + share)
                    else:
                        tax = min(tax, bal + min(share, state.account_balance(agency.cash_account)))
                    if tax > 0:
                        effects.append(Effect("burn", {"account": wallet(carrier.id), "amount": tax, "reason": "transaction_tax"}))
                effects.extend(goal_progress(state, carrier.id, "escort", 1.0, intent.tick))
                effects.extend(memory(state, carrier.id, intent.tick, f"镖单{oid}送达，得银{share}文", 0.6))
            else:
                effects.extend(memory(state, carrier.id, intent.tick, f"镖单{oid}途中失手", 0.8))
            effects.extend(
                [
                    Effect("escort_settle", {"id": oid}),
                    Effect("set_location", {"entity": carrier.id, "to": order.destination}),
                    Effect("stamina", {"entity": carrier.id, "delta": -10.0}),
                ]
            )
        return effects
