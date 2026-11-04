"""自提案目标：schema、可行性校验器、生命周期硬门与 stub 确定性。"""
from __future__ import annotations

import json
from pathlib import Path

from wsim.sim.self_goal import (
    FeasibilityValidator,
    SelfGoalLedger,
    parse_proposal,
)
from wsim.world.state import Goal


def _raw(npc: str, **over):
    base = {
        "npc": npc,
        "goal_type": "advance_skill",
        "target": 0.01,
        "deadline_days": 3,
        "preconditions": [],
        "expected_actions": 6,
        "expected_money": 0,
        "objects": ["inn"],
        "purpose": "精进手艺",
    }
    base.update(over)
    return base


def test_parse_rejects_missing_and_unknown(real_content):
    npc = str(real_content.npcs[0]["id"])
    _, errs = parse_proposal({"npc": npc}, npc, 0)
    assert any("schema:missing" in e for e in errs)

    raw = _raw(npc, goal_type="steal_everything")
    _, errs = parse_proposal(raw, npc, 0)
    assert "schema:goal_type:unsupported" in errs

    raw = _raw(npc, deadline_days=9)
    _, errs = parse_proposal(raw, npc, 0)
    assert "schema:deadline_days:out_of_range" in errs


def test_validator_accepts_reachable_with_equation(real_content, real_state):
    npc = str(real_content.npcs[0]["id"])
    proposal, errs = parse_proposal(_raw(npc), npc, 0)
    assert errs == []
    verdict = FeasibilityValidator(real_content).validate(real_state, proposal, 0, "active")
    assert verdict.accepted
    assert "capacity=" in verdict.equation
    assert "<=" in verdict.equation
    assert verdict.capacity_units >= proposal.target


def test_validator_rejects_over_budget_actions(real_content, real_state):
    npc = str(real_content.npcs[0]["id"])
    proposal, errs = parse_proposal(_raw(npc, expected_actions=12), npc, 0)
    assert errs == []
    verdict = FeasibilityValidator(real_content).validate(real_state, proposal, 0, "active")
    assert not verdict.accepted
    assert verdict.reason == "budget_actions_exceeded"
    assert "expected_actions=12 > action_capacity=6" in verdict.equation


def test_validator_rejects_over_budget_money(real_content, real_state):
    npc = str(real_content.npcs[0]["id"])
    cash = real_state.account_balance(f"wallet:{npc}")
    proposal, errs = parse_proposal(_raw(npc, expected_money=cash + 500), npc, 0)
    assert errs == []
    verdict = FeasibilityValidator(real_content).validate(real_state, proposal, 0, "active")
    assert not verdict.accepted
    assert verdict.reason == "budget_money_exceeded"
    assert f"expected_money={cash + 500} > cash={cash}" in verdict.equation


def test_validator_rejects_unreachable_target(real_content, real_state):
    npc = str(real_content.npcs[0]["id"])
    proposal, errs = parse_proposal(_raw(npc, target=1.0), npc, 0)
    assert errs == []
    verdict = FeasibilityValidator(real_content).validate(real_state, proposal, 0, "active")
    assert not verdict.accepted
    assert verdict.reason == "target_exceeds_capacity"
    assert f"< target={proposal.target:.4f}" in verdict.equation


def test_lifecycle_initial_goal_protected(real_content, real_state):
    npc = str(real_content.npcs[0]["id"])
    ent = real_state.entities[npc]
    assert ent.goal_id, "创世应已给 NPC 设置初始内容目标"
    ledger = SelfGoalLedger(real_content, mode="competent")
    reason, detail = ledger.can_activate(real_state, npc)
    assert reason == "initial_goal_protected"
    assert ent.goal_id in detail


def test_lifecycle_second_self_goal_rejected(real_content, real_state):
    npc = str(real_content.npcs[0]["id"])
    other = f"{npc}:goal_other"
    real_state.goals[other] = Goal(
        id=other, npc=npc, kind="wealth", target=100.0, status="active",
        content_goal_id="goal_other", content_type="earn_money",
    )
    real_state.entities[npc].goal_id = other
    self_goal = f"self:{npc}:existing"
    real_state.goals[self_goal] = Goal(
        id=self_goal, npc=npc, kind="mastery", target=0.01, status="active",
        source="self", content_goal_id=f"self:{self_goal}", content_type="advance_skill",
    )
    ledger = SelfGoalLedger(real_content, mode="competent")
    reason, detail = ledger.can_activate(real_state, npc)
    assert reason == "self_goal_active"
    assert detail == f"active_self_goal={self_goal}"


def test_stub_is_deterministic(real_content, real_state):
    npc = str(real_content.npcs[0]["id"])
    ticks = 12
    ledgers = [SelfGoalLedger(real_content, mode="competent") for _ in range(2)]
    for ledger in ledgers:
        ledger.maybe_issue(real_state, npc, ticks, "active", 0)
    a = [r for r in ledgers[0].records if r["kind"] == "proposal"]
    b = [r for r in ledgers[1].records if r["kind"] == "proposal"]
    assert len(a) == len(b) == 1
    assert json.dumps(a[0], sort_keys=True, ensure_ascii=False) == json.dumps(
        b[0], sort_keys=True, ensure_ascii=False
    )
