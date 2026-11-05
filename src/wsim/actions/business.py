"""事业/组织建立动词：open_shop / found_sect，以及店铺雇佣与日结。"""
from __future__ import annotations

from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import (
    arg_int,
    arg_str,
    explicit_goal_id,
    first_business_at,
    founding_members,
    goal_progress,
    income_tax,
    max_skill,
    memory,
    organization_at,
    require_entity,
    role_allowed,
    sell_price,
    treasury_balance,
    treasury_guard_active,
    treasury_open_shop_buffer,
    wallet,
)
from .registry import Action

# 开店初始库存：档位越高，品类与数量越多。只使用内容包里真实存在的物品，
# 成本按既有经济模型 sell_price（零售价的 60%）计，向区域公账（treasury:region）进货，
# 不凭空造钱；物品计入 items_created，可被日结与交易审计追溯。
SHOP_INITIAL_STOCK = {
    "stall": {"flatbread": 6, "noodle": 4, "rice": 3},
    "storefront": {
        "flatbread": 10, "noodle": 8, "rice": 6, "coarse_cloth": 4,
        "cleaver": 2, "needle": 2,
    },
    "branch": {
        "flatbread": 14, "noodle": 10, "rice": 8, "coarse_cloth": 6,
        "cleaver": 3, "needle": 3, "wound_powder": 3, "mugwort": 3,
    },
}

# 店铺可雇人数与雇工契约门槛。岗位必须是 economy.wages_daily 里的职业名，
# 日薪按该岗位市场价上下浮动：低于 0.8 倍视为压价，高于 1.5 倍视为不合理。
SHOP_HIRE_CAPACITY = {"stall": 1, "storefront": 2, "branch": 3}
SHOP_HIRE_MIN_SKILL = 0.15
SHOP_HIRE_MIN_AFFECTION = 6.0
SHOP_HIRE_WAGE_MIN_RATIO = 0.8
SHOP_HIRE_WAGE_MAX_RATIO = 1.5


def _initial_stock(content, kind: str) -> dict[str, int]:
    table = SHOP_INITIAL_STOCK.get(kind) or {}
    return {item: qty for item, qty in sorted(table.items()) if item in content.items}


def shop_stock_cost(content, kind: str) -> int:
    """初始库存进货成本：按既有 sell_price 计价。"""
    return sum(sell_price(content, item) * qty for item, qty in _initial_stock(content, kind).items())


def daily_shop_tax(content, kind: str) -> int:
    """日结里的日摊店铺税：月租/30，至少 1 文。"""
    table = (content.economy.get("shop_opening") or {}).get("monthly_upkeep") or {}
    return max(1, int(table.get(kind, 30)) // 30)


def shop_output_item(content, kind: str) -> str | None:
    items = _initial_stock(content, kind)
    return sorted(items)[0] if items else None


def is_player_owned(biz) -> bool:
    return bool(biz.owner) and str(biz.owner).startswith("player:")


def has_player_shop(state: WorldState) -> bool:
    """世界内是否存在玩家店铺：headless / 调度器 / 日结前置共用这一处判断。"""
    return any(is_player_owned(biz) for biz in state.businesses.values())


def is_shop_hire(intent: Intent) -> bool:
    """recruit 带岗位/日薪/店铺参数时走店铺雇佣，否则保持原门派招揽语义。"""
    return any(k in intent.args for k in ("shop", "position", "wage"))


def _shop_hire_args(content, state: WorldState, intent: Intent):
    ent = require_entity(state, intent.actor)
    target = state.entities.get(arg_str(intent, "target"))
    if target is None or target.status != "alive":
        raise Reject("target_not_found")
    shop_id = arg_str(intent, "shop", required=False) or ent.business or ""
    position = arg_str(intent, "position", required=False) or target.role
    wage_arg = intent.args.get("wage")
    if wage_arg is None or wage_arg == "":
        wage = int(content.wage_for(position))
    else:
        wage = arg_int(intent, "wage", minimum=1, maximum=100000)
    return ent, target, shop_id, position, wage


def shop_hire_preconditions(content, state: WorldState, intent: Intent) -> list[str]:
    ent = require_entity(state, intent.actor)
    target = state.entities.get(arg_str(intent, "target"))
    if target is None or target.status != "alive":
        return ["target_not_found"]
    if target.presence == "offline":
        return ["target_offline"]
    if not str(ent.id).startswith("player:"):
        return ["not_player_shop_hire"]
    if target.id == ent.id:
        return ["self_recruit"]
    shop_id = arg_str(intent, "shop", required=False) or ent.business or ""
    if shop_id not in state.businesses:
        return ["no_shop"]
    biz = state.businesses[shop_id]
    if biz.owner != ent.id:
        return ["not_shop_owner"]
    if biz.location != ent.location:
        return ["wrong_location"]
    if target.location != biz.location:
        return ["target_not_at_shop"]
    if target.id in biz.employees:
        return ["already_hired"]
    cap = SHOP_HIRE_CAPACITY.get(biz.kind)
    if cap is None:
        return ["bad_shop_type"]
    if len(biz.employees) >= cap:
        return ["shop_full"]
    if target.business and target.business in state.businesses:
        return ["target_owns_shop"]
    position = arg_str(intent, "position", required=False) or target.role
    if position not in (content.economy.get("wages_daily") or {}):
        return ["unknown_position"]
    if max_skill(target) < SHOP_HIRE_MIN_SKILL:
        return ["skill_too_low"]
    if state.affection_between(ent.id, target.id) < SHOP_HIRE_MIN_AFFECTION:
        return ["relation_too_low"]
    market = int(content.wage_for(position))
    wage = intent.args.get("wage")
    wage = int(content.wage_for(position)) if wage in (None, "") else int(wage)
    if wage < int(round(market * SHOP_HIRE_WAGE_MIN_RATIO)):
        return ["wage_too_low"]
    if wage > int(round(market * SHOP_HIRE_WAGE_MAX_RATIO)):
        return ["wage_too_high"]
    return []


def shop_hire_effects(content, state: WorldState, intent: Intent) -> list[Effect]:
    ent, target, shop_id, position, wage = _shop_hire_args(content, state, intent)
    biz = state.businesses[shop_id]
    return [
        Effect("shop_hire", {"business": shop_id, "entity": target.id, "position": position, "wage": wage}),
        Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
        Effect("set_field", {"entity": target.id, "field": "last_action_tick", "value": intent.tick}),
        *memory(state, ent.id, intent.tick,
                f"雇用{target.name}在{biz.name}做{position}，日薪{wage}文", 0.7),
        *memory(state, target.id, intent.tick,
                f"受雇于{biz.name}做{position}，日薪{wage}文", 0.6),
    ]


def build_shop_settlements(content, state: WorldState) -> list[dict]:
    """计算玩家店铺当日日结行（不产生效果，只给日界事件 args 用）。

    流水直接读 `Business.day_revenue/day_restock`（由 transfer 效果维护），
    工资与税在这里按“先工资、后税”的现金约束算出实付/欠付，净利口径：
    revenue - restock - 工资 - 税。
    """
    rows: list[dict] = []
    for bid in sorted(state.businesses):
        biz = state.businesses[bid]
        if not is_player_owned(biz):
            continue
        revenue = int(biz.day_revenue)
        restock = int(biz.day_restock)
        cash = state.account_balance(biz.cash_account)
        employees = []
        for eid in sorted(biz.employees):
            e = state.entities.get(eid)
            if e is None or e.status != "alive":
                continue
            wage = int(biz.employee_wages.get(eid, 0))
            if wage <= 0:
                continue
            employees.append({
                "employee": eid,
                "position": str(biz.employee_positions.get(eid, "")),
                "wage": wage,
                "paid": 0,
                "unpaid": wage,
            })
        remaining = cash
        for row in employees:
            row["paid"] = min(row["wage"], remaining)
            row["unpaid"] = row["wage"] - row["paid"]
            remaining -= row["paid"]
        wage_total = sum(row["wage"] for row in employees)
        # 日摊税按日计提；现金不足的部分结转次日（tax_arrears），
        # 仍在本事件的后续日结里补缴，不混入 system_debts。
        tax = daily_shop_tax(content, biz.kind)
        arrears_before = int(biz.tax_arrears)
        tax_due = tax + arrears_before
        tax_paid = min(tax_due, remaining)
        net = revenue - restock - wage_total - tax
        output_item = shop_output_item(content, biz.kind)
        rows.append({
            "shop": bid,
            "kind": biz.kind,
            "revenue": revenue,
            "restock": restock,
            "wage": wage_total,
            "wage_paid": sum(row["paid"] for row in employees),
            "wage_unpaid": sum(row["unpaid"] for row in employees),
            "tax": tax,
            "tax_due": tax_due,
            "tax_paid": tax_paid,
            "tax_arrears_before": arrears_before,
            "tax_arrears": tax_due - tax_paid,
            "net": net,
            "loss": net < 0,
            "employees": employees,
            "output_item": output_item,
            "output_qty": len(employees) if output_item else 0,
            "cash": cash,
        })
    return rows


def shop_settlement_effects(content, state: WorldState, rows: list[dict], tick: int) -> list[Effect]:
    """把日结行落成效果：付工资（不足记欠账）、缴日摊税、雇工产出、写记忆。"""
    effects: list[Effect] = []
    for row in rows:
        bid = str(row["shop"])
        biz = state.businesses.get(bid)
        if biz is None:
            continue
        for emp in row.get("employees") or []:
            eid = str(emp["employee"])
            ent = state.entities.get(eid)
            if ent is None or ent.status != "alive":
                continue
            paid = int(emp["paid"])
            unpaid = int(emp["unpaid"])
            # 工资按行内已算好的“现金约束”支付，不能再用 payment_with_shortfall
            # 现查余额：apply 阶段 state 不会随效果变化，多名雇工会重复看到同一笔现金。
            if paid > 0:
                effects.append(
                    Effect("transfer", {"src": biz.cash_account, "dst": wallet(eid),
                                        "amount": paid, "reason": "wage"})
                )
                tax = min(income_tax(content, paid), state.account_balance(wallet(eid)) + paid)
                if tax > 0:
                    effects.append(Effect("burn", {"account": wallet(eid), "amount": tax, "reason": "income_tax"}))
            if unpaid > 0:
                # 欠薪是“店铺欠雇工”：creditor=雇工、debtor=店铺；日界 system_debts
                # 会用店铺现金补付。若旧债已结清，另开一笔带 tick 的债，避免 id 冲突。
                debt_id = f"debt:wage:{bid}:{eid}"
                existing = state.debts.get(debt_id)
                if existing is not None and existing.status == "active":
                    effects.append(Effect("debt_increase", {"id": debt_id, "amount": unpaid}))
                else:
                    if existing is not None:
                        debt_id = f"{debt_id}:{tick}"
                    effects.append(Effect("debt_create", {
                        "id": debt_id, "creditor": eid, "debtor": bid, "amount": unpaid,
                        "long_term": False, "created_tick": tick, "note": "unpaid_wage",
                    }))
            effects.extend(memory(
                state, eid, tick,
                f"在{biz.name}出工（{emp.get('position', '')}），日结工资{paid}文"
                + (f"，欠{int(emp['unpaid'])}文" if int(emp["unpaid"]) > 0 else ""),
                0.4,
            ))
        tax_paid = int(row.get("tax_paid", 0))
        if tax_paid > 0:
            effects.append(Effect("burn", {"account": biz.cash_account, "amount": tax_paid, "reason": "shop_daily_tax"}))
        output_item = row.get("output_item")
        output_qty = int(row.get("output_qty", 0))
        if output_item and output_qty > 0:
            effects.append(Effect("item_create", {
                "owner": bid, "item": str(output_item), "qty": output_qty, "reason": "shop_output",
            }))
        if biz.owner:
            warn = "（亏损，店主需补足）" if row.get("loss") else ""
            arrears = int(row.get("tax_arrears", 0))
            carry = f"，欠税{arrears}文结转次日" if arrears > 0 else ""
            effects.extend(memory(
                state, biz.owner, tick,
                f"{biz.name}日结：营收{int(row['revenue'])}文，进货{int(row['restock'])}文，"
                f"工资{int(row['wage'])}文，税{int(row['tax'])}文，净利{int(row['net'])}文"
                f"{carry}{warn}",
                0.6,
            ))
    return effects


def shop_day_reset_effects(state: WorldState, rows: list[dict]) -> list[Effect]:
    """日结后清零运行期流水并结转欠税；无玩家店铺时返回空，保证旧世界零事件。"""
    arrears = {
        str(row["shop"]): int(row.get("tax_arrears", 0))
        for row in rows
    }
    if not arrears:
        return []
    return [Effect("shop_day_close", {"tax_arrears": arrears})]


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
        stock_cost = shop_stock_cost(self.content, shop_type)
        total = cost + stock_cost
        if state.account_balance(wallet(ent.id)) < total:
            return ["insufficient_money"]
        # 金库必须留足「保底线 + 开店成本 + 该店 3 日预计工资」，否则延后。
        if treasury_balance(state) < treasury_open_shop_buffer(self.content, cost, shop_type):
            return ["treasury_buffer"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        at = arg_str(intent, "at")
        shop_type = arg_str(intent, "shop_type")
        name = arg_str(intent, "name")
        cost = int((self.content.economy.get("shop_opening") or {}).get(shop_type, 0))
        stock = _initial_stock(self.content, shop_type)
        stock_cost = shop_stock_cost(self.content, shop_type)
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
        # 初始库存按 sell_price 向区域公账进货：钱从店主钱包真实转出，货物计入店铺库存，
        # 不产生凭空货币（无赠送、无 mint）；后续补货仍需走同样的进货路径。
        if stock_cost > 0:
            effects.append(
                Effect("transfer", {"src": wallet(ent.id), "dst": "treasury:region",
                                    "amount": stock_cost, "reason": "shop_stock"})
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
            ]
        )
        for item, qty in stock.items():
            effects.append(
                Effect("item_create", {"owner": shop_id, "item": item, "qty": qty, "reason": "initial_stock"})
            )
        effects.extend(
            [
                Effect("set_field", {"entity": ent.id, "field": "business", "value": shop_id}),
                Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
                *goal_progress(state, ent.id, "shop", 1.0, intent.tick, goal_id=explicit_goal_id(intent)),
                *memory(
                    state, ent.id, intent.tick,
                    f"在{at}开设{name}（{shop_type}），铺面费{cost}文，进货{stock_cost}文，"
                    f"初始库存{sum(stock.values())}件",
                    0.6,
                ),
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
        if not role_allowed(self.content, "found_sect", ent.role):
            return ["role_not_allowed"]
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
                *goal_progress(state, ent.id, "faction", 1.0, intent.tick, goal_id=explicit_goal_id(intent)),
                *memory(state, ent.id, intent.tick, f"在{at}创立{name}，缴纳{fee}文", 0.9),
            ]
        )
        return effects
