"""K6：货币守恒与债务复式记账。"""
from __future__ import annotations

from ..world.state import WorldState


def conservation(state: WorldState) -> dict:
    accounts_sum = sum(int(v) for v in state.accounts.values())
    minted = int(state.minted_total)
    burned = int(state.burned_total)
    error = accounts_sum - (minted - burned)
    negative = sorted(acc for acc, bal in state.accounts.items() if int(bal) < 0)
    return {
        "accounts_sum": accounts_sum,
        "minted_total": minted,
        "burned_total": burned,
        "net_issued": minted - burned,
        "conservation_error": error,
        "negative_accounts": negative,
        "accounts_count": len(state.accounts),
    }


def debt_ledger(state: WorldState) -> dict:
    """把每条 canonical Debt 展开为借贷双方两条分录。

    注意：这里只是投影视图，`receivable_total == payable_total` 是同一 amount
    同时加减的恒真式，**不能**作为 K6 校验。资金是否真实转移由
    `audit/debt_audit.py` 从事件日志独立重建。
    """
    entries: list[dict] = []
    per_debt: list[dict] = []
    receivable = 0
    payable = 0
    long_term = 0
    active = 0
    for did in sorted(state.debts):
        debt = state.debts[did]
        if debt.status != "active":
            continue
        active += 1
        if debt.long_term:
            long_term += 1
        receivable += debt.amount
        payable += debt.amount
        entries.append(
            {
                "debt_id": did,
                "party": debt.creditor,
                "role": "receivable",
                "amount": debt.amount,
                "sign": 1,
                "long_term": debt.long_term,
                "note": debt.note,
            }
        )
        entries.append(
            {
                "debt_id": did,
                "party": debt.debtor,
                "role": "payable",
                "amount": -debt.amount,
                "sign": -1,
                "long_term": debt.long_term,
                "note": debt.note,
            }
        )
        per_debt.append(
            {
                "id": did,
                "creditor": debt.creditor,
                "debtor": debt.debtor,
                "amount": debt.amount,
                "long_term": debt.long_term,
                "note": debt.note,
            }
        )
    return {
        "active_debts": active,
        "long_term_debts": long_term,
        "receivable_total": receivable,
        "payable_total": payable,
        "projection_note": "receivable/payable 只作投影；债务平衡以 debt_audit 为准",
        "entries": entries,
        "debts": per_debt,
    }
