"""金库收入侧杠杆：交易税分流、守恒、open_shop 缓冲延后。"""
from __future__ import annotations

from wsim.actions.basic import ConsumeAction
from wsim.actions.business import OpenShopAction
from wsim.actions.common import (
    tax_split_effects,
    treasury_balance,
    treasury_open_shop_buffer,
)
from wsim.audit.conservation import conservation
from wsim.actions.common import wallet
from wsim.core.models import Effect, Intent
from wsim.sim.intent import GoalChoice, Planner
from wsim.world.effects import apply_effects


def test_tax_split_is_half_transfer_half_burn(real_content, state):
    effects = tax_split_effects(real_content, wallet("n1"), 100)
    transfer = [e for e in effects if e.op == "transfer"]
    burn = [e for e in effects if e.op == "burn"]
    assert sum(e.data["amount"] for e in transfer) == 50
    assert sum(e.data["amount"] for e in burn) == 50
    assert sum(e.data["amount"] for e in effects) == 100

    before_minted = state.minted_total
    apply_effects(state, effects)
    cons = conservation(state)
    assert cons["conservation_error"] == 0
    assert state.minted_total == before_minted
    assert state.account_balance("treasury:region") == 1050


def test_tax_split_handles_odd_amount_without_loss(real_content, state):
    effects = tax_split_effects(real_content, wallet("n1"), 51)
    assert sum(e.data["amount"] for e in effects) == 51
    assert [e.data["amount"] for e in effects] == [26, 25]
    apply_effects(state, effects)
    assert conservation(state)["conservation_error"] == 0


def test_consume_tax_is_split_to_treasury(content, state):
    content.economy["taxes"] = {"transaction": 0.04}
    action = ConsumeAction(content)
    intent = Intent(
        actor="n1",
        verb="consume",
        args={"item": "food", "qty": 10},
        tick=1,
        source="test",
    )
    effects = action.apply(state, intent, None)
    to_treasury = [
        e for e in effects if e.op == "transfer" and e.data.get("reason") == "transaction_tax_treasury"
    ]
    burned = [e for e in effects if e.op == "burn" and e.data.get("reason") == "transaction_tax"]
    assert sum(e.data["amount"] for e in to_treasury) == 1
    assert sum(e.data["amount"] for e in burned) == 1

    before = state.account_balance("treasury:region")
    before_minted = state.minted_total
    apply_effects(state, effects)
    assert state.account_balance("treasury:region") == before + 50 + 1
    assert state.minted_total == before_minted
    assert conservation(state)["conservation_error"] == 0


def test_open_shop_precondition_requires_treasury_buffer(content, state):
    content.economy["shop_opening"] = {"stall": 300}
    content.economy["work"] = {"employer_reserve": 100, "treasury_guard_multiplier": 1.0}
    action = OpenShopAction(content)
    intent = Intent(
        actor="n1",
        verb="open_shop",
        args={"at": "a", "shop_type": "stall", "name": "小摊"},
        tick=1,
        source="test",
    )
    assert treasury_balance(state) == 1000
    assert treasury_open_shop_buffer(state, content, 300) == 540
    assert "insufficient_money" in action.preconditions(state, intent)

    apply_effects(state, [Effect("mint", {"account": wallet("n1"), "amount": 300, "reason": "test"})])
    apply_effects(state, [Effect("burn", {"account": "treasury:region", "amount": 500, "reason": "test"})])
    assert treasury_balance(state) == 500
    assert "treasury_buffer" in action.preconditions(state, intent)


def test_planner_defers_open_shop_when_buffer_short(content, state):
    content.economy["shop_opening"] = {"stall": 300}
    content.economy["work"] = {"employer_reserve": 2000, "treasury_guard_multiplier": 1.2}
    planner = Planner(content, 42)
    choice = GoalChoice(
        npc="n1",
        goal_id="build_shop",
        content_goal_id="build_shop",
        content_type="build_shop",
        kind="build_shop",
        target=0.0,
        source="content",
        reason="盘个小摊",
    )
    ent = state.entities["n1"]
    intents: list[Intent] = []
    planner._goal_step(state, intents, ent, choice, "a", 0, 100, 0, 100, {})
    assert planner.postponed_open_shop == 1
    assert not [i for i in intents if i.verb == "open_shop"]
