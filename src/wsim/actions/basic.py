"""基础动词：move / cultivate / work / consume。

动作只读 state，返回 Effect[]；随机数由 Writer 注入的 PRNG 提供。
"""
from __future__ import annotations

from ..core.clock import TICKS_PER_DAY, day_of, npc_tick_of
from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import (
    arg_int,
    arg_str,
    business_selling,
    cheapest_food,
    employer_of,
    explicit_goal_id,
    food_item,
    goal_progress,
    income_tax,
    max_skill,
    memory,
    path_between,
    payment_with_shortfall,
    quest_progress_effects,
    require_entity,
    skill_for,
    tax_split_effects,
    transaction_tax,
    wallet,
)
from .equipment import equip_cultivate_bonus
from .registry import Action


class MoveAction(Action):
    verb = "move"

    def __init__(self, content):
        self.content = content

    def _step(self, state: WorldState, ent, intent: Intent) -> tuple[str | None, int]:
        """返回（落点，步数）。

        正常情况按计划的相邻一跳移动；跨 tick 丢意图会让实际位置偏离计划，
        此时按 `dest`（最终目的地）从**实际所在地**重算最短路并一次补足，
        保证动作（上工/吃饭等）开始前确实到达目的地，同时不再产生
        `not_adjacent` 空转。
        """
        to = arg_str(intent, "to")
        dest = arg_str(intent, "dest", required=False)
        loc = state.locations.get(ent.location)
        if loc is None or to not in state.locations:
            return None, 0
        if to in loc.get("exits", []):
            return to, 1
        if to == ent.location:
            # 跨 tick 丢意图后，前面的跳可能已按 dest 自愈、提前到达终点，
            # 剩余的“最后一跳”就变成原地一跳。它不是非法移动，按 0 步成功消除空转。
            if dest and dest != ent.location and dest in state.locations:
                route = path_between(state, ent.location, dest)
                if route:
                    return dest, len(route)
            return to, 0
        if dest and dest == ent.location:
            # 已经站在最终目的地：剩下的陈旧一跳作废，按 0 步成功。
            return to, 0
        if dest and dest in state.locations:
            route = path_between(state, ent.location, dest)
            if route:
                return dest, len(route)
        return None, 0

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        to = arg_str(intent, "to")
        dest = arg_str(intent, "dest", required=False)
        if to not in state.locations or (dest and dest not in state.locations):
            return ["unknown_location"]
        if state.locations.get(ent.location) is None:
            return ["unknown_location"]
        step, steps = self._step(state, ent, intent)
        if step is None:
            return ["not_adjacent"]
        cost = float(self.content.economy.get("stamina_move", 5))
        if ent.stamina < cost * steps:
            return ["insufficient_stamina"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        step, steps = self._step(state, ent, intent)
        if step is None:
            raise Reject("not_adjacent")
        cost = float(self.content.economy.get("stamina_move", 5))
        if steps == 0:
            # 原地一跳：已在此地，不扣体力、不写记忆，避免“看起来在动”的空转事件。
            return []
        return [
            Effect("set_location", {"entity": ent.id, "to": step}),
            Effect("stamina", {"entity": ent.id, "delta": -cost * steps}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *memory(state, ent.id, intent.tick, f"移动到 {step}", 0.1),
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
        # 已装备工具提供 25% 熟练度加成（装备状态在世界状态里，可回放）。
        delta = 0.0025 * hours * (1.0 + equip_cultivate_bonus(self.content, ent))
        before = float(ent.skills.get(skill, 0.0))
        gained = min(delta, max(0.0, 1.0 - before))
        return [
            Effect("stamina", {"entity": ent.id, "delta": -cost}),
            Effect("skill", {"entity": ent.id, "skill": skill, "delta": gained}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *goal_progress(state, ent.id, "mastery", gained, intent.tick, goal_id=explicit_goal_id(intent)),
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
        employed_by_self = ent.business is not None and ent.business == bid
        settled_daily = ent.id in biz.employee_wages
        employer_cash = state.account_balance(biz.cash_account)
        received = 0 if (employed_by_self or settled_daily) else min(gross, employer_cash)
        tax = income_tax(self.content, received)
        wallet_bal = state.account_balance(wallet(ent.id))
        tax = min(tax, wallet_bal + received)
        net = received - tax
        cost = float(self.content.economy.get("stamina_work", 12)) * hours / 8.0
        skill = skill_for(self.content, ent.role)

        effects: list[Effect] = []
        if employed_by_self:
            # 店主自雇：工资记 0，不生成“自己欠自己”的工资债。
            # 店主收益走月度分红（system_shop_dividend），不走工资。
            note = "店主自雇，工资记0文（店主收益走分红）"
        elif settled_daily:
            # 已签日结契约的雇工：工资由日界 system_shop_settle 统一结算，
            # 此处不再重复支付，也不生成工资债。
            note = f"已签日结契约，工资本日由日结发放{gross}文"
            if getattr(intent, "clock_tick", None) is not None:
                # 有 NPC 时钟：把出勤钉在 NPC 日上，日结按同一日判定出勤。
                # 标记格式必须与 business.ATTENDANCE_MARKER 的判定串一致
                # （`工资本日由日结发放:D{day}`），金额放在标记之后。
                note = (
                    f"已签日结契约，工资本日由日结发放:D{npc_tick_of(intent) // TICKS_PER_DAY}"
                    f"，应得{gross}文"
                )
        else:
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
            note = f"在{biz.name}做工{hours}小时，应得{gross}文，实收{received}文，税{tax}文"
        if tax > 0:
            effects.append(Effect("burn", {"account": wallet(ent.id), "amount": tax, "reason": "income_tax"}))
        effects.extend(
            [
                Effect("stamina", {"entity": ent.id, "delta": -cost}),
                Effect("skill", {"entity": ent.id, "skill": skill, "delta": hours * 0.0006}),
                Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
                *goal_progress(state, ent.id, "wealth", float(max(0, net)), intent.tick, goal_id=explicit_goal_id(intent)),
                *memory(state, ent.id, intent.tick, note, 0.2),
                *quest_progress_effects(
                    state, ent.id, "work",
                    tick=intent.tick, location=ent.location, hours=hours,
                ),
            ]
        )
        return effects


class ConsumeAction(Action):
    verb = "consume"

    def __init__(self, content):
        self.content = content

    def _seller(self, state: WorldState, ent, item: str, tick: int):
        # 卖家解析按 (地点, 买主, 世界日) 轮转：玩家店与 NPC 店同价同货时同权。
        return business_selling(state, ent.location, item, salt=ent.id, day=day_of(tick))

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
        seller = self._seller(state, ent, item, intent.tick)
        if seller is None:
            return ["item_not_available"]
        price = self.content.item_price(item, state) * qty
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
        seller = self._seller(state, ent, item, intent.tick)
        if seller is None:
            raise Reject("item_not_available")
        price = self.content.item_price(item, state) * qty
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
