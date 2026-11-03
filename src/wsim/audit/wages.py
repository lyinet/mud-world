"""工资链路审计（欠薪 IOU 对账）。

逐条核对每个 work 事件：

- 应发 gross：按内容包 wages_daily × hours/8 复算；
- 实付 paid：reason == "wage" 的 transfer 金额；
- 欠薪 unpaid：note == "unpaid_wage" 的 debt_create 金额；
- 恒等式：gross == paid + unpaid。

再与两个外部口径对账：

1. cashflow 的 inflow_by_reason["wage"] 必须等于所有 work 事件的实付合计；
2. 期末 active 的 unpaid_wage 债务合计必须等于 debt_ledger 的对应条目，
   且等于“累计新增欠薪 − 累计已偿还”。

历史上把实付转账的 reason 写成 unpaid_wage，导致 cashflow 把 6790 文实付工资
误报成“未付工资”，而 IOU 账本只有 580 文——差一个数量级。本模块负责把这类
统计口径错误在每次运行后立刻暴露出来。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..core.content import Content
from ..core.models import Event
from ..world.state import WorldState


def _wage_transfer(e: Event) -> list[dict[str, Any]]:
    return [
        x.get("data") or {}
        for x in e.effects
        if x.get("op") == "transfer" and (x.get("data") or {}).get("reason") == "wage"
    ]


def _wage_debt(e: Event) -> list[dict[str, Any]]:
    """未付工资：首次欠薪 debt_create，以及后续合并的 debt_increase。"""
    rows: list[dict[str, Any]] = []
    for x in e.effects:
        op = x.get("op")
        d = x.get("data") or {}
        if op == "debt_create" and d.get("note") == "unpaid_wage":
            rows.append(d)
        elif op == "debt_increase" and str(d.get("id", "")).startswith("debt:wage:"):
            rows.append(d)
    return rows


def wage_trace(events: list[Event], state: WorldState, content: Content, npc_ids: set[str]) -> dict:
    rows: list[dict[str, Any]] = []
    by_payer: dict[str, dict[str, int]] = defaultdict(lambda: {"paid": 0, "unpaid": 0, "payments": 0})
    by_location: dict[str, dict[str, int]] = defaultdict(lambda: {"paid": 0, "unpaid": 0, "payments": 0})
    by_npc: dict[str, dict[str, int]] = defaultdict(lambda: {"paid": 0, "unpaid": 0, "work": 0})
    gross_total = paid_total = unpaid_created = 0

    for e in events:
        if e.verb != "work" or e.outcome != "ok":
            continue
        ent = state.entities.get(e.actor)
        hours = int((e.args or {}).get("hours", 8))
        if ent is None:
            continue
        gross = max(1, int(round(content.wage_for(ent.role) * hours / 8.0)))
        transfers = _wage_transfer(e)
        debts = _wage_debt(e)
        paid = sum(int(x.get("amount", 0)) for x in transfers)
        unpaid = sum(int(x.get("amount", 0)) for x in debts)
        gross_total += gross
        paid_total += paid
        unpaid_created += unpaid
        employer = debts[0].get("debtor") if debts else (ent.employed_at or (e.args or {}).get("at", ""))
        payer_account = transfers[0].get("src") if transfers else ""
        if not payer_account:
            biz = state.businesses.get(str(employer))
            payer_account = biz.cash_account if biz is not None else ""
        payee_account = transfers[0].get("dst") if transfers else f"wallet:{e.actor}"
        by_payer[str(employer)]["paid"] += paid
        by_payer[str(employer)]["unpaid"] += unpaid
        by_payer[str(employer)]["payments"] += 1
        location = str(employer).split(":")[1] if str(employer).startswith("biz:") else str(employer)
        by_location[location]["paid"] += paid
        by_location[location]["unpaid"] += unpaid
        by_location[location]["payments"] += 1
        by_npc[e.actor]["paid"] += paid
        by_npc[e.actor]["unpaid"] += unpaid
        by_npc[e.actor]["work"] += 1
        rows.append(
            {
                "seq": e.seq,
                "day": e.day,
                "tick": e.tick,
                "npc": e.actor,
                "role": ent.role,
                "hours": hours,
                "gross": gross,
                "paid": paid,
                "unpaid": unpaid,
                "payer_party": employer,
                "payer_account": payer_account,
                "payee_account": payee_account,
                "balanced": gross == paid + unpaid,
            }
        )

    settled_total = 0
    active_total = 0
    active_count = 0
    for e in events:
        if e.outcome != "ok":
            continue
        for x in e.effects:
            if x.get("op") != "debt_settle":
                continue
            d = x.get("data") or {}
            debt = state.debts.get(str(d.get("id", "")))
            if debt is not None and debt.note == "unpaid_wage":
                settled_total += int(d.get("amount", 0))
    for did in sorted(state.debts):
        debt = state.debts[did]
        if debt.note == "unpaid_wage" and debt.status == "active":
            active_total += debt.amount
            active_count += 1

    # 与 cashflow / debt_ledger 对账
    cashflow_wage = 0
    for e in events:
        if e.outcome != "ok":
            continue
        for x in e.effects:
            if x.get("op") != "transfer":
                continue
            d = x.get("data") or {}
            if d.get("reason") != "wage":
                continue
            if isinstance(d.get("dst"), str) and d["dst"].startswith("wallet:") and d["dst"][7:] in npc_ids:
                cashflow_wage += int(d.get("amount", 0))

    ledger_active = sum(
        d.amount
        for d in state.debts.values()
        if d.note == "unpaid_wage" and d.status == "active"
    )

    reconciliation = {
        "gross_equals_paid_plus_unpaid": gross_total == paid_total + unpaid_created,
        "cashflow_wage_equals_paid": cashflow_wage == paid_total,
        "debt_ledger_active_equals_active": ledger_active == active_total,
        "active_equals_created_minus_settled": active_total == unpaid_created - settled_total,
        "all_rows_balanced": all(r["balanced"] for r in rows),
    }
    return {
        "definition": {
            "gross": "content.wages_daily[role] * hours / 8 (round)",
            "paid": "transfer.op with reason='wage'",
            "unpaid": "debt_create.op with note='unpaid_wage'",
            "invariant": "gross == paid + unpaid",
        },
        "totals": {
            "work_events": len(rows),
            "gross": gross_total,
            "paid": paid_total,
            "unpaid_created": unpaid_created,
            "unpaid_settled": settled_total,
            "unpaid_active": active_total,
            "unpaid_active_count": active_count,
            "cashflow_wage_paid": cashflow_wage,
            "debt_ledger_active": ledger_active,
        },
        "reconciliation": reconciliation,
        "by_payer": {k: dict(sorted(v.items())) for k, v in sorted(by_payer.items())},
        "by_location": {k: dict(sorted(v.items())) for k, v in sorted(by_location.items())},
        "by_npc": {k: dict(sorted(v.items())) for k, v in sorted(by_npc.items())},
        "samples": rows[:20] + [r for r in rows if r["unpaid"] > 0][:20],
        "all_rows": rows,
    }
