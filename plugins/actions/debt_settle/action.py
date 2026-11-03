"""debt_settle 插件入口。

契约：纯函数，只读 state，返回 Effect[]。
还款金额不超过“债务余额 + 债务人钱包余额”，由 Effect 层再次校验。
"""
from __future__ import annotations

from wsim.core.models import Effect, Reject


def apply(state, args):
    actor = str(args["actor"])
    want = str(args.get("debt") or "")
    debts = [
        d for d in state.debts.values()
        if d.status == "active" and d.debtor == actor
    ]
    if want:
        # 指定债务可能已在同一窗口被其他计划清偿：退回到该债务人的最小欠债，
        # 而不是把整笔还款判为必拒（计划期与执行期之间的正常竞争）。
        named = [d for d in debts if d.id == want]
        debts = named or debts
    if not debts:
        raise Reject("no_debt")
    debt = sorted(debts, key=lambda d: (d.amount, d.id))[0]
    bal = state.account_balance(f"wallet:{actor}")
    if bal <= 0:
        raise Reject("insufficient_balance")
    amount = int(args.get("amount") or debt.amount)
    pay = min(amount, debt.amount, bal)
    if pay <= 0:
        raise Reject("insufficient_balance")
    return [
        Effect("debt_settle", {"id": debt.id, "amount": pay}),
        Effect(
            "memory_add",
            {
                "npc": actor,
                "tick": getattr(state, "tick", 0),
                "text": f"还债{pay}文（{debt.id}）",
                "importance": 0.35,
            },
        ),
    ]
