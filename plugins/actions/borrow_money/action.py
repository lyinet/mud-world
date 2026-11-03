"""borrow_money 插件入口。

契约：纯函数，只读 state，返回 Effect[]；不得 import 引擎写路径。
借款 = 出借人钱包 -> 借款人钱包 + 一笔债务账本 + 记忆。
"""
from __future__ import annotations

from wsim.core.models import Effect


def apply(state, args):
    actor = str(args["actor"])
    target = str(args["target"])
    amount = int(args["amount"])
    debt_id = f"debt:borrow:{actor}:{target}:{len(state.debts)}"
    return [
        Effect("transfer", {"src": f"wallet:{target}", "dst": f"wallet:{actor}", "amount": amount, "reason": "borrow_money"}),
        Effect(
            "debt_create",
            {
                "id": debt_id,
                "creditor": target,
                "debtor": actor,
                "amount": amount,
                "created_tick": getattr(state, "tick", 0),
                "note": "borrow_money",
            },
        ),
        Effect("memory_add", {"npc": actor, "tick": getattr(state, "tick", 0), "text": f"向{target}借{amount}文", "importance": 0.4}),
    ]
