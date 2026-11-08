"""事业/组织建立动词：open_shop / found_sect，以及店铺雇佣与日结。"""
from __future__ import annotations

from ..core.clock import TICKS_PER_DAY, day_of
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
    shop_price,
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

# 店主定价带：只允许基准价 ±20%，超出返回 price_out_of_range。
SHOP_PRICE_BAND = 0.20
RESTOCK_QTY_MAX = 50

# 专程进店客流：档位基础概率 + 每名当班雇工加成，上限 0.80。
# 机制常数写在这里而不是 content：不改内容数值，且可单独回归。
SHOP_ATTRACTION_BASE = {"stall": 0.32, "storefront": 0.45, "branch": 0.55}
SHOP_ATTRACTION_PER_EMPLOYEE = 0.12
SHOP_ATTRACTION_CAP = 0.80


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


def shop_attraction(biz, present: int) -> float:
    """专程进店概率：档位基础值 + 当班雇工加成，封顶 0.80。

    在没有玩家店的世界不会被调用；同 seed 下只看 (kind, present)，无随机、无墙钟。
    """
    base = SHOP_ATTRACTION_BASE.get(str(getattr(biz, "kind", "")), 0.32)
    return min(SHOP_ATTRACTION_CAP, base + SHOP_ATTRACTION_PER_EMPLOYEE * max(0, int(present)))


def _employee_output_units(state: WorldState, eid: str) -> int:
    """雇工日产出件数随技能：新手工 1 件，技能 ≥0.5 的熟练工 2 件。"""
    ent = state.entities.get(eid)
    if ent is None:
        return 0
    return max(1, int(round(1 + float(max_skill(ent))))) if max_skill(ent) > 0 else 1


def player_shop_for(state: WorldState, ent) -> tuple[object | None, str]:
    """动作共用：当前玩家自己开的店；返回 (biz, 拒绝码)。"""
    shop_id = str(getattr(ent, "business", "") or "")
    if not shop_id or shop_id not in state.businesses:
        return None, "no_shop"
    biz = state.businesses[shop_id]
    if biz.owner != ent.id:
        return None, "not_shop_owner"
    return biz, ""


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


# 已签日结契约的雇工在 WorkAction 里写的可读记忆；日结据此判断“当日是否真的到店”。
# 出勤是事实：只有当日确实在店内上工（带 employer 的 work）才计薪、计产出，
# 人没来就 0 工资 0 产出，不能靠契约自动发钱。
ATTENDANCE_MARKER = "工资本日由日结发放"


def settled_day_of(tick: int) -> int:
    """日界事件 tick 对应的结算日。

    无头/无跨 tick 丢弃时在当日最后一 tick（143/287/...）结算；
    共享世界玩家动作会跨 tick，日界可能落在次日边界后几 tick，
    此时结算日仍是刚结束的那一天。两种口径都归一到“刚结束的一天”。
    """
    day = day_of(tick)
    if tick % TICKS_PER_DAY == TICKS_PER_DAY - 1:
        return day
    return day - 1


def _worked_on_day(
    state: WorldState, eid: str, day: int, clock_marker: bool = False
) -> bool:
    if clock_marker:
        # NPC 日口径：优先认店铺上的持久出勤凭证（记忆会被 80 条上限淘汰），
        # 再退回当日 NPC 日标记（兼容旧事件 / 无店铺场景）。
        for biz in state.businesses.values():
            if eid in biz.employees or eid in biz.employee_wages:
                if int(getattr(biz, "attendance_day", -1)) == day:
                    return True
        needle = f"{ATTENDANCE_MARKER}:D{day}"
        return any(needle in str(mem.get("text", "")) for mem in state.memories.get(eid, ()))
    start = day * TICKS_PER_DAY
    end = start + TICKS_PER_DAY
    for mem in state.memories.get(eid, ()):
        tick = int(mem.get("tick", -1))
        if start <= tick < end and ATTENDANCE_MARKER in str(mem.get("text", "")):
            return True
    return False


def shop_stock_snapshot(content, biz) -> tuple[int, list[str]]:
    """收盘库存快照：总件数 + 该档位常规品类里已售罄的 SKU。

    只读 `biz.stock`：不改库存、不参与成交，只用于日结/报告归因
    "缺货导致的零成交日"。无玩家店的世界不会被调用，事件序列不变。
    """
    stock_units = sum(max(0, int(qty)) for qty in biz.stock.values())
    canonical = sorted(_initial_stock(content, biz.kind))
    stockout_items = [item for item in canonical if int(biz.stock.get(item, 0)) <= 0]
    return stock_units, stockout_items


def build_shop_settlements(
    content,
    state: WorldState,
    tick: int | None = None,
    ledger_day: int | None = None,
    clock_day: int | None = None,
) -> list[dict]:
    """计算玩家店铺当日日结行（不产生效果，只给日界事件 args 用）。

    流水直接读 `Business.day_revenue/day_restock`（由 transfer 效果维护），
    工资按**当日实际出勤**计：有该雇工当日的日结出工记忆才计薪与产出，
    缺勤者 0 工资 0 产出；工资与税在这里按“先工资、后税”的现金约束算出
    实付/欠付，净利口径：revenue - restock - 工资 - 税。
    """
    day = settled_day_of(int(tick)) if tick is not None else None
    if clock_day is not None:
        # 有 NPC 时钟：出勤/工资判定改读 NPC 日，与账本日同源。
        day = int(clock_day)
    row_day = int(ledger_day) if ledger_day is not None else day
    rows: list[dict] = []
    for bid in sorted(state.businesses):
        biz = state.businesses[bid]
        if not is_player_owned(biz):
            continue
        revenue = int(biz.day_revenue)
        restock = int(biz.day_restock)
        cash = state.account_balance(biz.cash_account)
        employees = []
        absent = []
        for eid in sorted(biz.employees):
            e = state.entities.get(eid)
            if e is None or e.status != "alive":
                continue
            contract_wage = int(biz.employee_wages.get(eid, 0))
            if contract_wage <= 0:
                continue
            present = True if day is None else _worked_on_day(
                state, eid, day, clock_marker=clock_day is not None
            )
            row = {
                "employee": eid,
                "name": e.name,
                "position": str(biz.employee_positions.get(eid, "")),
                "contract_wage": contract_wage,
                "wage": contract_wage if present else 0,
                "paid": 0,
                "unpaid": 0,
                "present": present,
                "absent": not present,
            }
            employees.append(row)
            if not present:
                absent.append({
                    "employee": eid,
                    "name": e.name,
                    "position": str(biz.employee_positions.get(eid, "")),
                    "contract_wage": contract_wage,
                })
        remaining = cash
        for row in employees:
            if not row["present"]:
                continue
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
        present_count = sum(1 for row in employees if row["present"])
        output_units = sum(
            _employee_output_units(state, row["employee"]) for row in employees if row["present"]
        )
        stock_units, stockout_items = shop_stock_snapshot(content, biz)
        rows.append({
            "shop": bid,
            "kind": biz.kind,
            **({"day": row_day} if (ledger_day is not None or clock_day is not None) else {}),
            "revenue": revenue,
            "restock": restock,
            "tx": int(biz.day_customers),
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
            "absent": absent,
            "present_count": present_count,
            "output_item": output_item,
            "output_qty": output_units if output_item else 0,
            "customers": int(biz.day_customers),
            "cash": cash,
            "stock_units": stock_units,
            "stockout_items": stockout_items,
            # 归因口径：当天零成交且该档常规品类有售罄 SKU → 记一次缺货日。
            "stockout": int(biz.day_customers) == 0 and bool(stockout_items),
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
            if emp.get("absent"):
                # 缺勤：0 工资、0 产出、不生成欠薪债（欠薪只在“出勤但现金不足”时发生）。
                absent_name = str(emp.get("name") or ent.name)
                if biz.owner:
                    effects.extend(memory(
                        state, biz.owner, tick,
                        f"{absent_name}当日未到店，工资0文、产出0件",
                        0.4,
                    ))
                effects.extend(memory(
                    state, eid, tick,
                    f"当日未到店（{biz.name}），日结工资0文、产出0件",
                    0.3,
                ))
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
        _, history_row = build_shop_history_row(row, tick)
        effects.append(Effect("shop_history", {"business": bid, "row": history_row}))
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


def build_shop_history_row(row: dict, tick: int) -> tuple[int, dict]:
    """shop_report 的 7 日行：与 system_shop_settle 事件 args 同源，只留决策字段。"""
    day = row.get("day")
    if day is None:
        day = settled_day_of(int(tick))
    day = int(day)
    return day, {
        "day": day,
        "revenue": int(row.get("revenue", 0)),
        "restock": int(row.get("restock", 0)),
        "wage": int(row.get("wage", 0)),
        "tax": int(row.get("tax", 0)),
        "net_profit": int(row.get("net", 0)),
        "tx": int(row.get("tx", 0)),
        "customers": int(row.get("customers", 0)),
        "stock_units": int(row.get("stock_units", 0)),
        "stockout": bool(row.get("stockout", False)),
        "stockout_items": list(row.get("stockout_items") or []),
    }


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


class RestockAction(Action):
    verb = "restock"

    def __init__(self, content):
        self.content = content

    def _quote(self, state: WorldState, biz, item: str, qty: int, from_id: str):
        """返回 (单价, 收款账户, 拒绝码)；缺省从区域公账批发。"""
        if from_id:
            src = state.businesses.get(from_id)
            if src is None or src.id == biz.id or src.location != biz.location:
                return 0, "", "no_supply"
            if int(src.stock.get(item, 0)) < qty:
                return 0, "", "no_supply"
            return shop_price(self.content, state, src, item), src.cash_account, ""
        return sell_price(self.content, item), "treasury:region", ""

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        biz, code = player_shop_for(state, ent)
        if code:
            return [code]
        if biz.location != ent.location:
            return ["wrong_location"]
        item = arg_str(intent, "item")
        if item not in self.content.items:
            return ["unknown_item"]
        qty = arg_int(intent, "qty", minimum=1, maximum=RESTOCK_QTY_MAX)
        from_id = arg_str(intent, "from", required=False) or ""
        unit, payee, code = self._quote(state, biz, item, qty, from_id)
        if code:
            return [code]
        total = unit * qty
        if state.account_balance(biz.cash_account) < total:
            return ["insufficient_shop_cash"]
        if state.account_balance(payee) < 0:
            return ["no_supply"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        biz, _ = player_shop_for(state, ent)
        item = arg_str(intent, "item")
        qty = arg_int(intent, "qty", minimum=1, maximum=RESTOCK_QTY_MAX)
        from_id = arg_str(intent, "from", required=False) or ""
        unit, payee, _ = self._quote(state, biz, item, qty, from_id)
        total = unit * qty
        before = int(biz.stock.get(item, 0))
        effects: list[Effect] = [
            Effect("transfer", {"src": biz.cash_account, "dst": payee,
                                "amount": total, "reason": "restock"}),
        ]
        if from_id:
            effects.append(Effect("item_move", {"src": from_id, "dst": biz.id,
                                                "item": item, "qty": qty}))
        else:
            effects.append(Effect("item_create", {"owner": biz.id, "item": item,
                                                  "qty": qty, "reason": "restock"}))
        effects.append(Effect("audit_log", {
            "kind": "shop_restock", "actor": ent.id, "tick": intent.tick,
            "fields": [f"shop={biz.id}", f"item={item}", f"qty={qty}",
                       f"unit={unit}", f"total={total}",
                       f"stock_before={before}", f"stock_after={before + qty}"],
            "note": "restock",
        }))
        effects.extend(memory(
            state, ent.id, intent.tick,
            f"{biz.name}补货{item}×{qty}，单价{unit}文，共{total}文，库存{before}→{before + qty}",
            0.5,
        ))
        return effects


class SetPriceAction(Action):
    verb = "set_price"

    def __init__(self, content):
        self.content = content

    def _band(self, state: WorldState, item: str) -> tuple[int, int, int]:
        base = int(self.content.item_price(item, state))
        return base, max(1, int(round(base * (1 - SHOP_PRICE_BAND)))), int(round(base * (1 + SHOP_PRICE_BAND)))

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        biz, code = player_shop_for(state, ent)
        if code:
            return [code]
        item = arg_str(intent, "item")
        if item not in self.content.items:
            return ["unknown_item"]
        price = arg_int(intent, "price", minimum=0, maximum=10**7)
        if price > 0:
            base, low, high = self._band(state, item)
            if price < low or price > high:
                return ["price_out_of_range"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        biz, _ = player_shop_for(state, ent)
        item = arg_str(intent, "item")
        price = arg_int(intent, "price", minimum=0, maximum=10**7)
        base, low, high = self._band(state, item)
        text = f"{biz.name}把{item}售价调到{price}文（基准{base}，允许{low}–{high}）" if price > 0 \
            else f"{biz.name}把{item}售价恢复基准{base}文"
        return [
            Effect("shop_price", {"business": biz.id, "item": item, "price": price}),
            *memory(state, ent.id, intent.tick, text, 0.5),
        ]


class DismissAction(Action):
    verb = "dismiss"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        biz, code = player_shop_for(state, ent)
        if code:
            return [code]
        target = arg_str(intent, "target")
        if target not in state.entities:
            return ["employee_not_found"]
        if target not in biz.employees:
            return ["not_employed"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        biz, _ = player_shop_for(state, ent)
        target = arg_str(intent, "target")
        owed = state.debts.get(f"debt:wage:{biz.id}:{target}")
        note = "；欠薪仍为店铺债务" if owed is not None and owed.status == "active" else ""
        return [
            Effect("shop_dismiss", {"business": biz.id, "entity": target}),
            *memory(state, ent.id, intent.tick,
                     f"{biz.name}辞退{state.entities[target].name}{note}", 0.5),
            *memory(state, target, intent.tick,
                     f"被{biz.name}辞退，契约解除{note}", 0.4),
        ]


class WithdrawAction(Action):
    verb = "withdraw"

    def __init__(self, content):
        self.content = content

    def _qty(self, state: WorldState, intent: Intent, biz) -> int:
        return arg_int(intent, "qty", minimum=1, maximum=10**7)

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        biz, code = player_shop_for(state, ent)
        if code:
            return [code]
        qty = self._qty(state, intent, biz)
        if qty <= 0:
            return ["bad_arg:qty"]
        if state.account_balance(biz.cash_account) < qty:
            return ["insufficient_shop_cash"]
        if wallet(ent.id) not in state.accounts:
            return ["not_enough_money"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        biz, _ = player_shop_for(state, ent)
        qty = self._qty(state, intent, biz)
        after = state.account_balance(biz.cash_account) - qty
        return [
            Effect("transfer", {"src": biz.cash_account, "dst": wallet(ent.id),
                                "amount": qty, "reason": "withdraw"}),
            *memory(state, ent.id, intent.tick,
                     f"从{biz.name}提现{qty}文，店现余{after}文", 0.4),
        ]


class ShopReportAction(Action):
    verb = "shop_report"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        _, code = player_shop_for(state, ent)
        return [code] if code else []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        biz, _ = player_shop_for(state, ent)
        days = [dict(row) for row in biz.day_history][-7:]
        return [Effect("shop_report", {
            "shop": biz.id, "name": biz.name, "kind": biz.kind,
            "days": days,
            "cash": state.account_balance(biz.cash_account),
            "price_overrides": dict(sorted(biz.price_overrides.items())),
            "stock": {item: int(qty) for item, qty in sorted(biz.stock.items())},
            "stockouts": sum(1 for d in days if d.get("stockout")),
        })]


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
