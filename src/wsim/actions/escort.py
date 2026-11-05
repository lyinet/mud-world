"""押镖动词与到达结算系统动作（P2-B：逐线路结算）。

结算优先级（唯一事实源）：
1. `content/escort_routes.yaml` 命中线路 -> 该线路的 duration_days / fee 区间 /
   agency_cut / risk_loss_prob / risk_events 全部生效；
2. 未命中线路（或旧 Intent 只给了 route 路径）-> 回退到 `economy.escort` 全局值，
   并写一条 `escort_global_fallback` 记忆，便于审计。
不得同时使用线路值和全局值（旧实现的双重计算已删除）。
"""
from __future__ import annotations

from ..core.clock import TICKS_PER_DAY, TICKS_PER_HOUR
from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import (
    arg_int,
    escort_business,
    explicit_goal_id,
    goal_progress,
    memory,
    payment_with_shortfall,
    require_entity,
    role_allowed,
    tax_split_effects,
    transaction_tax,
    wallet,
)
from .registry import Action


# 押镖托管桶：货物在发单时从背包扣出，以 `escrow:<镖单id>:<物品>` 为键存进
# 镖局 business.stock。这个键不是内容包里的真实物品，所有买卖 / NPC 采购路径
# 都只认 content.items，所以不会被当成商品买走；`business.stock` 本身落盘，
# 所以托管在账上看得见、可回放。
ESCROW_PREFIX = "escrow:"


def _escrow_account(oid: str) -> str:
    return f"{ESCROW_PREFIX}{oid}"


def _escrow_key(oid: str, item: str) -> str:
    return f"{_escrow_account(oid)}:{item}"


def _escrow_prefix(oid: str) -> str:
    return f"{_escrow_account(oid)}:"


def _escrow_tokens(state: WorldState, oid: str) -> list[tuple[str, str, int]]:
    """返回 (持有商业体 id, 真实物品 id, 数量)；按 id 排序保证回放稳定。"""
    prefix = _escrow_prefix(oid)
    out: list[tuple[str, str, int]] = []
    for bid in sorted(state.businesses):
        biz = state.businesses[bid]
        for key, qty in sorted(biz.stock.items()):
            if key.startswith(prefix) and int(qty) > 0:
                out.append((bid, key[len(prefix):], int(qty)))
    return out


def _route_path(intent: Intent) -> list[str]:
    raw = intent.args.get("route")
    if not isinstance(raw, list) or len(raw) < 2:
        raise Reject("bad_arg:route")
    return [str(x) for x in raw]


def resolve_route(content, intent: Intent) -> dict | None:
    """按 route_id 或路径精确匹配内容包线路；匹配不上返回 None。"""
    rid = intent.args.get("route_id")
    if rid and str(rid) in content.escort_routes:
        return content.escort_routes[str(rid)]
    path = _route_path(intent)
    for route in content.escort_routes.values():
        if [str(x) for x in (route.get("route") or [])] == path:
            return route
    return None


class EscortAction(Action):
    verb = "escort"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        if not role_allowed(self.content, "escort", ent.role):
            return ["role_not_allowed"]
        route = _route_path(intent)
        if route[0] != ent.location:
            return ["not_at_origin"]
        for a, b in zip(route, route[1:]):
            if a not in state.locations or b not in state.locations:
                return ["unknown_location"]
            if b not in state.locations[a].get("exits", []):
                return ["route_not_adjacent"]
        if ent.escort_id is not None:
            return ["already_on_escort"]
        # 真实货物：必须是内容包物品、数量合法、背包里确实持有。
        cargo_item = str(intent.args.get("cargo_item") or "")
        if cargo_item:
            if cargo_item not in self.content.items:
                return ["item_not_available"]
            qty = arg_int(intent, "cargo_qty", 1, minimum=1, maximum=10000)
            if int(ent.inventory.get(cargo_item, 0)) < qty:
                return ["item_missing"]
        cargo = arg_int(intent, "cargo_value", 100, minimum=100, maximum=100000)
        fee = arg_int(intent, "fee", 0, minimum=1, maximum=100000)
        agency = escort_business(state, route[0])
        if agency is None:
            return ["no_escort_agency"]
        if state.account_balance(agency.cash_account) < fee:
            return ["agency_insufficient_cash"]
        meta = resolve_route(self.content, intent)
        if meta is not None:
            if cargo < int(meta.get("cargo_value_min", 100)):
                return ["cargo_too_low"]
            if cargo > int(meta.get("cargo_value_max", 100000)):
                return ["cargo_too_high"]
            if fee < int(meta.get("fee_min", 1)):
                return ["fee_too_low"]
            if fee > int(meta.get("fee_max", 100000)):
                return ["fee_too_high"]
        elif cargo < int((self.content.economy.get("escort") or {}).get("cargo_min_value", 100)):
            return ["cargo_too_low"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        route = _route_path(intent)
        cargo = arg_int(intent, "cargo_value", 100, minimum=100, maximum=100000)
        fee = arg_int(intent, "fee", 0, minimum=1, maximum=100000)
        agency = escort_business(state, route[0])
        if agency is None:
            raise Reject("no_escort_agency")
        meta = resolve_route(self.content, intent)
        cfg = self.content.economy.get("escort") or {}
        if meta is not None:
            duration = int(meta.get("duration_days", 1)) * TICKS_PER_DAY
            risk = float(meta.get("risk_loss_prob", 0.15))
            cut = float(meta.get("agency_cut", 0.30))
            route_id = str(meta["id"])
            fallback = False
        else:  # 仅当线路未命中时才读全局值（不允许两者叠加）
            duration = int(cfg.get("duration_hours", 24)) * TICKS_PER_HOUR
            risk = float(cfg.get("risk_loss_prob", 0.15))
            cut = float(cfg.get("agency_cut", 0.30))
            route_id = ""
            fallback = True

        success = not rng.chance(risk)
        fee_penalty = 0
        cargo_loss = 0.0
        stamina_penalty = 0.0
        risk_event = ""
        delay = 0
        if success and meta is not None:
            for ev in meta.get("risk_events") or []:
                if rng.chance(float(ev.get("probability", 0.0))):
                    risk_event = str(ev.get("id", ""))
                    fee_penalty = int(ev.get("fee_penalty", 0))
                    cargo_loss = float(ev.get("cargo_loss_ratio", 0.0))
                    stamina_penalty = float(ev.get("stamina_penalty", 0.0))
                    if str(ev.get("effect", "")) == "delay":
                        delay = TICKS_PER_DAY // 2
                    break

        oid = f"escort:{ent.id}:{intent.tick}"
        # 玩家可传真实货物（item + qty）；不传时事件体与旧世界逐字节一致。
        cargo_item = str(intent.args.get("cargo_item") or "")
        cargo_qty = (
            arg_int(intent, "cargo_qty", 1, minimum=1, maximum=10000) if cargo_item else 0
        )
        order_payload = {
            "id": oid,
            "origin": route[0],
            "destination": route[-1],
            "cargo_value": cargo,
            "fee": fee,
            "deposit": max(1, fee),
            "created_tick": intent.tick,
        }
        if cargo_item:
            order_payload["cargo_item"] = cargo_item
            order_payload["cargo_qty"] = cargo_qty
        effects = [
            Effect("escort_order_create", order_payload),
            Effect(
                "escort_start",
                {
                    "id": oid,
                    "carrier": ent.id,
                    "success": success,
                    "arrive_tick": intent.tick + duration + delay,
                    "route_id": route_id,
                    "agency_cut": cut,
                    "fee_penalty": fee_penalty,
                    "cargo_loss_ratio": cargo_loss,
                    "risk_event": risk_event,
                    "risk_stamina": stamina_penalty,
                },
            ),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *memory(
                state,
                ent.id,
                intent.tick,
                f"接下{route[0]}->{route[-1]}的镖单（{route_id or 'global'}），镖银{fee}文"
                + (f"，风险事件{risk_event}" if risk_event else "")
                + ("，回退全局费率" if fallback else ""),
                0.5,
            ),
        ]
        if cargo_item:
            # 发单即托管：背包真实货物 -> escrow:<镖单id>:<物品> 托管桶（镖局库存）。
            # 两条效果与镖单同事务；任一条失败整笔回滚，不会出现“只扣货没开单”。
            account = _escrow_account(oid)
            effects.insert(
                2,
                Effect(
                    "item_consume",
                    {
                        "owner": ent.id,
                        "item": cargo_item,
                        "qty": cargo_qty,
                        "reason": "escort_cargo_escrow",
                        "order": oid,
                        "escrow_account": account,
                        "movement": "player->escrow",
                    },
                ),
            )
            effects.insert(
                3,
                Effect(
                    "item_create",
                    {
                        "owner": agency.id,
                        "item": _escrow_key(oid, cargo_item),
                        "qty": cargo_qty,
                        "reason": "escort_cargo_escrow",
                        "order": oid,
                        "escrow_account": account,
                        "movement": "player->escrow",
                    },
                ),
            )
        return effects


def _escrow_settle_effects(state: WorldState, order, oid: str) -> list[Effect]:
    """结算托管桶：成功送达即离场；失败原量退回承运人背包。

    返回空列表表示这条镖单没有托管货物（NPC 与旧 intent），调用方的事件序列
    逐字节不变。承运人实体不存在时只销毁托管并写明 orphaned，避免“隐形口袋”。
    """
    tokens = _escrow_tokens(state, oid)
    if not tokens:
        return []
    account = _escrow_account(oid)
    out: list[Effect] = []
    if order.success:
        ratio = max(0.0, float(order.cargo_loss_ratio))
        for bid, item, qty in tokens:
            key = _escrow_key(oid, item)
            lost = min(qty, int(round(qty * ratio))) if ratio > 0 else 0
            if lost > 0:
                out.append(
                    Effect(
                        "item_consume",
                        {
                            "owner": bid,
                            "item": key,
                            "qty": lost,
                            "reason": "escort_cargo_loss",
                            "order": oid,
                            "escrow_account": account,
                            "movement": "escrow->loss",
                        },
                    )
                )
            rest = qty - lost
            if rest > 0:
                out.append(
                    Effect(
                        "item_consume",
                        {
                            "owner": bid,
                            "item": key,
                            "qty": rest,
                            "reason": "escort_cargo_deliver",
                            "order": oid,
                            "escrow_account": account,
                            "movement": "escrow->delivery",
                        },
                    )
                )
        return out
    target = str(order.carrier or "")
    holder = target if target in state.entities else ""
    reason = "escort_cargo_return" if holder else "escort_cargo_orphaned"
    for bid, item, qty in tokens:
        out.append(
            Effect(
                "item_consume",
                {
                    "owner": bid,
                    "item": _escrow_key(oid, item),
                    "qty": qty,
                    "reason": reason,
                    "order": oid,
                    "escrow_account": account,
                    "movement": "escrow->player" if holder else "escrow->void",
                },
            )
        )
        if holder:
            out.append(
                Effect(
                    "item_create",
                    {
                        "owner": holder,
                        "item": item,
                        "qty": qty,
                        "reason": reason,
                        "order": oid,
                        "escrow_account": account,
                        "movement": "escrow->player",
                    },
                )
            )
    return out


class SystemEscortArriveAction(Action):
    verb = "system_escort_arrive"

    def __init__(self, content):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        effects: list[Effect] = []
        cfg = self.content.economy.get("escort") or {}
        for oid in sorted(state.escorts):
            order = state.escorts[oid]
            if order.status != "active" or order.arrive_tick > intent.tick:
                continue
            carrier = state.entities.get(order.carrier or "")
            if carrier is None or carrier.status != "alive":
                effects.append(Effect("escort_settle", {"id": oid}))
                effects.extend(_escrow_settle_effects(state, order, oid))
                continue
            agency = escort_business(state, order.origin) or escort_business(state)
            cut = order.agency_cut if order.route_id else float(cfg.get("agency_cut", 0.30))
            if order.success:
                net_fee = max(1, order.fee - order.fee_penalty)
                share = max(1, net_fee - int(net_fee * cut))
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
                    if state.account_balance(agency.cash_account) >= share:
                        tax = min(tax, bal + share)
                    else:
                        tax = min(tax, bal + min(share, state.account_balance(agency.cash_account)))
                    if tax > 0:
                        effects.extend(tax_split_effects(self.content, wallet(carrier.id), tax))
                effects.extend(goal_progress(
                    state, carrier.id, "escort", 1.0, intent.tick,
                    goal_id=explicit_goal_id(intent),
                ))
                note = f"镖单{oid}送达，得银{share}文"
                if order.risk_event:
                    note += f"（风险事件 {order.risk_event}，扣{order.fee_penalty}文"
                    if order.cargo_loss_ratio:
                        note += f"，货损{order.cargo_loss_ratio:.0%}"
                    note += "）"
                effects.extend(memory(state, carrier.id, intent.tick, note, 0.6))
            else:
                effects.extend(memory(state, carrier.id, intent.tick, f"镖单{oid}途中失手", 0.8))
            if order.cargo_loss_ratio > 0:
                effects.extend(
                    memory(
                        state,
                        carrier.id,
                        intent.tick,
                        f"镖货损失约{order.cargo_loss_ratio:.0%}，需向镖局说明",
                        0.7,
                    )
                )
            effects.extend(_escrow_settle_effects(state, order, oid))
            effects.extend(
                [
                    Effect("escort_settle", {"id": oid}),
                    Effect("set_location", {"entity": carrier.id, "to": order.destination}),
                    Effect(
                        "stamina",
                        {
                            "entity": carrier.id,
                            "delta": -10.0 - float(order.cargo_loss_ratio) * 20.0 - float(order.risk_stamina),
                        },
                    ),
                ]
            )
        return effects
