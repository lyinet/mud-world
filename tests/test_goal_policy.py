"""目标切换策略：承诺窗口 / 滞回阈值 / 切换留痕（默认关闭，开启后可复算）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.sim.intent import GoalSwitchPolicy, MockReasoner  # noqa: E402
from wsim.world.state import Goal  # noqa: E402


def _npc_with_goals(content) -> tuple[str, list[dict]]:
    for row in content.npcs:
        goals = sorted(row.get("goals") or [], key=lambda g: (float(g.get("priority", 9)), str(g.get("id"))))
        if len(goals) >= 2:
            return str(row["id"]), goals
    raise AssertionError("内容包没有包含 >=2 目标的 NPC")


def _activate(state, content, npc: str, row: dict, created_tick: int = 0) -> Goal:
    from wsim.actions.common import goal_kind, goal_target

    kind = goal_kind(str(row.get("type", "")))
    goal = Goal(
        id=f"{npc}:{row['id']}",
        npc=npc,
        kind=kind,
        target=float(goal_target(content, kind)),
        created_tick=created_tick,
        content_goal_id=str(row["id"]),
        content_type=str(row.get("type", "")),
    )
    state.goals[goal.id] = goal
    state.entities[npc].goal_id = goal.id
    # 排除紧急抢占：保证体力充足、现钱足够、无未结欠条。
    state.entities[npc].stamina = 100.0
    state.accounts[f"wallet:{npc}"] = max(100, state.account_balance(f"wallet:{npc}"))
    for debt in state.debts.values():
        if getattr(debt, "debtor", "") == npc:
            debt.status = "settled"
    return goal


def _view(state, content, tick: int) -> dict:
    return {"tick": tick, "interval": 1, "tier": "active", "state": state, "content": content}


def _system_goals(intents) -> list:
    return [i for i in intents if i.verb == "system_goal"]


def test_commitment_window_blocks_switch(real_content, real_state):
    npc, goals = _npc_with_goals(real_content)
    current_row, alt_row = goals[1], goals[0]  # 当前优先级 2，新目标优先级 1
    _activate(real_state, real_content, npc, current_row, created_tick=0)
    reasoner = MockReasoner(
        seed=42,
        content=real_content,
        goal_policy=GoalSwitchPolicy(enabled=True, commit_ticks=24, hysteresis_delta=0.0),
    )
    out = reasoner.decide({"id": npc}, _view(real_state, real_content, 12), goal_override=str(alt_row["id"]))
    assert _system_goals(out) == []
    assert reasoner.goal_commitment_blocked >= 1
    last = reasoner.goal_switches[-1]
    assert last["action"] == "keep_blocked"
    assert last["from_goal"] == f"{npc}:{current_row['id']}"
    assert last["to_goal"] == f"{npc}:{alt_row['id']}"
    assert last["source"] == "model"
    assert "承诺窗口" in last["reason"]


def test_commitment_window_expires(real_content, real_state):
    npc, goals = _npc_with_goals(real_content)
    current_row, alt_row = goals[1], goals[0]
    _activate(real_state, real_content, npc, current_row, created_tick=0)
    reasoner = MockReasoner(
        seed=42,
        content=real_content,
        goal_policy=GoalSwitchPolicy(enabled=True, commit_ticks=24, hysteresis_delta=0.0),
    )
    out = reasoner.decide({"id": npc}, _view(real_state, real_content, 24), goal_override=str(alt_row["id"]))
    system = _system_goals(out)
    assert system and system[0].args["goal"] == f"{npc}:{alt_row['id']}"
    last = reasoner.goal_switches[-1]
    assert last["action"] == "switch" and last["source"] == "model"
    assert last["from_score"] is not None and last["to_score"] is not None


def test_hysteresis_blocks_close_scores(real_content, real_state):
    npc, goals = _npc_with_goals(real_content)
    current_row, alt_row = goals[0], goals[1]  # 当前优先级 1，新目标优先级 2
    _activate(real_state, real_content, npc, current_row, created_tick=0)
    reasoner = MockReasoner(
        seed=42,
        content=real_content,
        goal_policy=GoalSwitchPolicy(enabled=True, commit_ticks=0, hysteresis_delta=0.5),
    )
    out = reasoner.decide({"id": npc}, _view(real_state, real_content, 5), goal_override=str(alt_row["id"]))
    assert _system_goals(out) == []
    last = reasoner.goal_switches[-1]
    assert last["action"] == "keep_blocked" and "滞回" in last["reason"]
    assert reasoner.goal_switch_stats(horizon_tick=144)["blocked_by_policy"] == {"hysteresis": 1}


def test_override_guard_v2_blocks_type_mismatch(real_content, real_state):
    npc = "lu_jiujin"
    rows = next(r for r in real_content.npcs if str(r.get("id")) == npc)["goals"]
    rule_row = next(g for g in rows if g["type"] == "earn_money")
    alt_row = next(g for g in rows if g["type"] == "build_relation")
    _activate(real_state, real_content, npc, rule_row, created_tick=0)
    reasoner = MockReasoner(
        seed=42,
        content=real_content,
        goal_policy=GoalSwitchPolicy(enabled=False, override_guard_v2=True, override_high_probability=0.55),
    )
    out = reasoner.decide({"id": npc}, _view(real_state, real_content, 5), goal_override=str(alt_row["id"]))
    assert _system_goals(out) == []
    assert reasoner.override_blocked_by_reason.get("type_mismatch") == 1
    ex = reasoner.override_blocked_examples[-1]
    assert ex["reason"] == "type_mismatch" and ex["npc"] == npc


def test_override_guard_v2_blocks_high_probability(real_content, real_state):
    npc = "lu_jiujin"
    rows = next(r for r in real_content.npcs if str(r.get("id")) == npc)["goals"]
    rule_row = next(g for g in rows if g["type"] == "earn_money")
    alt_row = next(g for g in rows if g["type"] == "build_relation")
    goal = _activate(real_state, real_content, npc, rule_row, created_tick=0)
    goal.progress = 0.05  # 绝对进度 < 0.1，规则仍保持当前目标
    # 抬高同类型历史通过率，使 p 超过 0.55。
    for i in range(30):
        gid = f"history:earn_money:{i}"
        real_state.goals[gid] = Goal(
            id=gid, npc="history_npc", kind="wealth", target=1.0, progress=1.0,
            status="completed", created_tick=0, content_goal_id=f"history_{i}", content_type="earn_money",
        )
    reasoner = MockReasoner(
        seed=42,
        content=real_content,
        goal_policy=GoalSwitchPolicy(enabled=False, override_guard_v2=True, override_high_probability=0.55),
    )
    out = reasoner.decide({"id": npc}, _view(real_state, real_content, 5), goal_override=str(alt_row["id"]))
    assert _system_goals(out) == []
    assert reasoner.override_blocked_by_reason.get("high_completion_probability") == 1


def test_type_quota_redirects_model_choice_to_deficit(real_content, real_state):
    npc = "gu_qingya"
    rows = next(r for r in real_content.npcs if str(r.get("id")) == npc)["goals"]
    current_row = next(g for g in rows if g["type"] == "gain_status")
    model_row = next(g for g in rows if g["type"] == "build_relation")
    _activate(real_state, real_content, npc, current_row, created_tick=0)
    reasoner = MockReasoner(
        seed=42,
        content=real_content,
        goal_policy=GoalSwitchPolicy(enabled=False, override_guard_v2=False, type_quota=True, type_quota_floor=1),
    )
    out = reasoner.decide({"id": npc}, _view(real_state, real_content, 5), goal_override=str(model_row["id"]))
    system = _system_goals(out)
    assert system and system[0].args["goal"].endswith("goal_gu_qingya_3")  # earn_money 缺口补位
    assert reasoner.quota_redirects == 1
    assert system[0].args["content_type"] == "earn_money"


def test_policy_disabled_keeps_old_behavior(real_content, real_state):
    npc, goals = _npc_with_goals(real_content)
    current_row, alt_row = goals[0], goals[1]
    _activate(real_state, real_content, npc, current_row, created_tick=0)
    reasoner = MockReasoner(seed=42, content=real_content)
    out = reasoner.decide({"id": npc}, _view(real_state, real_content, 5), goal_override=str(alt_row["id"]))
    system = _system_goals(out)
    assert system and system[0].args["goal"] == f"{npc}:{alt_row['id']}"
