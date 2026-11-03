"""K6：货币守恒与债务复式记账。"""
from __future__ import annotations

from wsim.audit.conservation import conservation, debt_ledger
from wsim.core.models import Effect
from wsim.world.effects import apply_effects


def test_mint_transfer_burn_keep_zero_error(content, state):
    apply_effects(
        state,
        [
            Effect("transfer", {"src": "treasury:region", "dst": "wallet:n1", "amount": 50, "reason": "test"}),
            Effect("burn", {"account": "wallet:n1", "amount": 10, "reason": "test_tax"}),
            Effect("mint", {"account": "treasury:region", "amount": 1, "reason": "test_mint"}),
        ],
    )
    result = conservation(state)
    assert result["conservation_error"] == 0
    assert result["negative_accounts"] == []
    assert result["accounts_sum"] == result["net_issued"]


def test_debt_ledger_is_projection_not_tautological_validation(content, state):
    apply_effects(
        state,
        [
            Effect(
                "debt_create",
                {
                    "id": "debt:test:1",
                    "creditor": "n1",
                    "debtor": "biz:a:food",
                    "amount": 30,
                    "created_tick": 0,
                    "note": "initial_debt",
                    "long_term": True,
                },
            )
        ],
    )
    ledger = debt_ledger(state)
    # receivable/payable 只是投影；K6 的平衡校验必须走 debt_audit（事件级重建）
    assert ledger["active_debts"] == 1
    assert ledger["long_term_debts"] == 1
    assert ledger["receivable_total"] == 30
    assert ledger["payable_total"] == 30
    assert "balanced" not in ledger
    assert all("balanced" not in d for d in ledger["debts"])
    signs = sorted(e["sign"] for e in ledger["entries"])
    assert signs == [-1, 1]
    # 债务本身不改变现金总量
    assert conservation(state)["conservation_error"] == 0


def test_debt_settle_moves_cash_and_preserves_conservation(content, state):
    apply_effects(
        state,
        [
            Effect(
                "debt_create",
                {
                    "id": "debt:test:2",
                    "creditor": "n1",
                    "debtor": "biz:a:food",
                    "amount": 30,
                    "created_tick": 0,
                    "note": "wage",
                },
            ),
            Effect("debt_settle", {"id": "debt:test:2", "amount": 20}),
        ],
    )
    assert state.debts["debt:test:2"].amount == 10
    assert conservation(state)["conservation_error"] == 0
