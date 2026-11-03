"""事业/组织建立动词：open_shop / found_sect。"""
from __future__ import annotations

from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import (
    arg_str,
    first_business_at,
    founding_members,
    goal_progress,
    memory,
    organization_at,
    require_entity,
    treasury_balance,
    treasury_guard_active,
    treasury_open_shop_buffer,
    wallet,
)
from .registry import Action


class OpenShopAction(Action):
    verb = "open_shop"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        at = arg_str(intent, "at")
        shop_type = arg_str(intent, "shop_type")
        arg_str(intent, "name")
        if treasury_guard_active(state, self.content):
            return ["treasury_guard"]
        if at != ent.location:
            return ["wrong_location"]
        if shop_type not in ("stall", "storefront", "branch"):
            return ["bad_shop_type"]
        if ent.business is not None:
            return ["already_owns_shop"]
        cost = int((self.content.economy.get("shop_opening") or {}).get(shop_type, 10**9))
        if state.account_balance(wallet(ent.id)) < cost:
            return ["insufficient_money"]
        # 金库必须留足「保底线 + 开店成本 + 7 日工资」，否则这条开店动作应延后。
        if treasury_balance(state) < treasury_open_shop_buffer(state, self.content, cost):
            return ["treasury_buffer"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        at = arg_str(intent, "at")
        shop_type = arg_str(intent, "shop_type")
        name = arg_str(intent, "name")
        cost = int((self.content.economy.get("shop_opening") or {}).get(shop_type, 0))
        landlord = first_business_at(state, at)
        org = organization_at(state, at)
        payee_account = landlord.cash_account if landlord is not None else (
            org.treasury_account if org is not None else None
        )
        if payee_account is None:
            raise Reject("no_landlord")
        shop_id = f"shop:{ent.id}"
        cash = f"cash:{shop_id}"
        effects: list[Effect] = []
        if cost > 0:
            effects.append(
                Effect("transfer", {"src": wallet(ent.id), "dst": payee_account, "amount": cost, "reason": "shop_rent"})
            )
        effects.extend(
            [
                Effect("create_account", {"account": cash}),
                Effect(
                    "create_business",
                    {
                        "id": shop_id,
                        "name": name,
                        "location": at,
                        "kind": shop_type,
                        "owner": ent.id,
                        "cash_account": cash,
                    },
                ),
                Effect("set_field", {"entity": ent.id, "field": "business", "value": shop_id}),
                Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
                *goal_progress(state, ent.id, "shop", 1.0, intent.tick),
                *memory(state, ent.id, intent.tick, f"在{at}开设{name}（{shop_type}），花费{cost}文", 0.6),
            ]
        )
        return effects


class FoundSectAction(Action):
    verb = "found_sect"

    def __init__(self, content):
        self.content = content

    def _reputation(self, state: WorldState, npc: str) -> float:
        out = 0.0
        for rel in state.relations.values():
            if rel.a == npc and rel.kind in ("friend", "family", "master", "disciple", "ally", "marriage"):
                out += max(0.0, rel.affection)
        return out

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        at = arg_str(intent, "at")
        arg_str(intent, "name")
        sect_id = arg_str(intent, "sect_id", required=False) or ""
        if at != ent.location:
            return ["wrong_location"]
        if sect_id and f"sect:{sect_id}" in state.factions:
            return ["sect_exists"]
        # 已加入地点行会（org:）仍可自立门派；只有已在别的 sect: 才拒绝。
        # found_sect 的目标本身就是“从学徒/行会成员走向开宗立派”。
        if ent.faction is not None and str(ent.faction).startswith("sect:"):
            return ["already_in_faction"]
        sect = self.content.economy.get("sect") or {}
        fee = int(sect.get("registration_fee", 500))
        founding_cost = int(sect.get("founding_cost", int(sect.get("min_money", 2000)) + fee))
        if state.account_balance(wallet(ent.id)) < founding_cost:
            return ["insufficient_money"]
        if self._reputation(state, ent.id) < float(sect.get("min_reputation", 40)):
            return ["low_reputation"]
        min_members = int(sect.get("min_members", 5))
        members = founding_members(state, ent, float(sect.get("min_relation_value", 30)) )
        if min_members > 0 and len(members) < min_members:
            return [f"insufficient_members:{len(members)}/{min_members}"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        at = arg_str(intent, "at")
        name = arg_str(intent, "name")
        sect = self.content.economy.get("sect") or {}
        fee = int(sect.get("registration_fee", 500))
        founding_cost = int(sect.get("founding_cost", 2500))
        members = founding_members(state, ent, float(sect.get("min_relation_value", 30)) )
        sid = arg_str(intent, "sect_id", required=False) or ent.id
        fid = f"sect:{sid}"
        treasury = f"treasury:{fid}"
        effects: list[Effect] = [Effect("create_account", {"account": treasury})]
        # founding_cost 含注册费：注册费上缴销毁，其余作为门派公账启动资金。
        startup = max(0, founding_cost - fee)
        if fee > 0:
            effects.append(Effect("burn", {"account": wallet(ent.id), "amount": fee, "reason": "sect_registration"}))
        if startup > 0:
            effects.append(
                Effect(
                    "transfer",
                    {"src": wallet(ent.id), "dst": treasury, "amount": startup, "reason": "sect_endowment"},
                )
            )
        effects.extend(
            [
                Effect(
                    "create_faction",
                    {
                        "id": fid,
                        "name": name,
                        "location": at,
                        "founder": ent.id,
                        "treasury_account": treasury,
                    },
                ),
                Effect("faction_join", {"entity": ent.id, "faction": fid}),
            ]
        )
        for member in members:
            if member != ent.id:
                effects.append(Effect("faction_join", {"entity": member, "faction": fid}))
        effects.extend(
            [
                Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
                *goal_progress(state, ent.id, "faction", 1.0, intent.tick),
                *memory(state, ent.id, intent.tick, f"在{at}创立{name}，缴纳{fee}文", 0.9),
            ]
        )
        return effects
