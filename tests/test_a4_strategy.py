"""A4 战略层：计划校验、G1/G3 护栏、计划内轮转与消费台账。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.sim.intent import MockReasoner, goal_kind
from wsim.sim.strategy import StrategyBook, parse_strategy_plan, validate_plan
from wsim.world.state import Goal


NPC = "qin_wangchuan"
G1 = f"{NPC}:goal_{NPC}_1"
G2 = f"{NPC}:goal_{NPC}_2"
G3 = f"{NPC}:goal_{NPC}_3"


def _payload(primary=G1, secondary=None, quota=None, **extra):
    data = {
        "plan_id": f"plan:{NPC}:0",
        "primary_goal_id": primary,
        "secondary_goal_ids": secondary or [],
        "relation_targets": [],
        "preserve_goal_ids": [],
        "type_quota": quota or [],
        "confidence": 0.6,
        "rationale": "先把眼前的事做稳，再顾旁的。",
    }
    data.update(extra)
    return data


def _plan(payload, horizon=3):
    return parse_strategy_plan(payload, npc=NPC, tick=0, horizon_days=horizon, content=_content())


_CONTENT = None


def _content():
    global _CONTENT
    if _CONTENT is None:
        from wsim.core.content import load_content

        _CONTENT = load_content(ROOT, allow_fixture=False, use_content=True)
    return _CONTENT


def _state(active_goal: str = "", progress: float = 0.0):
    from wsim.core.content import load_content
    from wsim.world.state import WorldState
    from wsim.sim.genesis import build_genesis
    from wsim.world.effects import apply_effects

    content = load_content(ROOT, allow_fixture=False, use_content=True)
    state = WorldState(locations=content.locations)
    genesis = build_genesis(content, content.npcs, root=ROOT)
    apply_effects(state, genesis.effects)
    if active_goal:
        state.goals[active_goal] = Goal(
            id=active_goal,
            npc=NPC,
            kind=goal_kind("earn_money"),
            target=600.0,
            progress=progress,
            status="active",
            content_goal_id="goal_qin_wangchuan_1",
            content_type="earn_money",
        )
        state.entities[NPC].goal_id = active_goal
    return state


def test_plan_validation_rejects_duplicate_and_unknown_goal():
    content = _content()
    state = _state()
    plan = _plan(_payload(primary=f"{NPC}:not_a_goal", secondary=[f"{NPC}:not_a_goal"]))
    ok, reasons = validate_plan(plan, content, state)
    assert not ok
    assert any(r.startswith("duplicate_goal") for r in reasons)
    assert any(r.startswith("unknown_goal") for r in reasons)


def test_plan_validation_rejects_actions_and_g1_unprotected():
    content = _content()
    state = _state(active_goal=G1, progress=0.25)
    plan = _plan(_payload(primary=G2, secondary=[], actions=[{"verb": "work"}]))
    ok, reasons = validate_plan(plan, content, state)
    assert not ok
    assert any(r.startswith("forbidden_key") for r in reasons)
    assert any(r.startswith("g1_progress_unprotected") for r in reasons)


def test_plan_validation_g3_core_type_missing():
    content = _content()
    state = _state()
    plan = _plan(_payload(primary=G3, secondary=[], quota=["build_relation"]))
    ok, reasons = validate_plan(plan, content, state)
    assert not ok
    assert "g3_core_type_missing" in reasons


def test_valid_plan_rotates_within_plan_not_every_tick():
    content = _content()
    state = _state(active_goal=G1, progress=0.2)
    book = StrategyBook()
    plan = _plan(_payload(primary=G1, secondary=[G2]))
    ok, reasons = validate_plan(plan, content, state)
    assert ok, reasons
    book.register(plan, ok, reasons)

    reasoner = MockReasoner(seed=42, content=content)
    reasoner.enable_strategy(book)
    choice = reasoner.selector.choose(state, NPC, 5)
    assert choice is not None and choice.goal_id == G2
    assert book.counts["consumed_entries"] == 0
    assert any(r.get("reason") == "in_plan_rotate" for r in book.records)

    # 新目标未推进到 10% 时，连续 tick 保持在同一条计划目标上。
    state.goals[G2] = Goal(
        id=G2, npc=NPC, kind=goal_kind("build_shop"), target=1.0,
        progress=0.0, status="active", content_goal_id="goal_qin_wangchuan_2",
        content_type="build_shop",
    )
    state.entities[NPC].goal_id = G2
    choices = [reasoner.selector.choose(state, NPC, tick) for tick in (6, 7, 8)]
    assert all(c is not None and c.goal_id == G2 for c in choices)


def test_urgent_does_not_abandon_planned_goal():
    content = _content()
    state = _state(active_goal=G1, progress=0.2)
    # 把现金打到 0 触发 urgent_cash 分支，同时仓库/位置保持可用。
    state.accounts["wallet:" + NPC] = 0
    book = StrategyBook()
    plan = _plan(_payload(primary=G1, secondary=[G2]))
    ok, reasons = validate_plan(plan, content, state)
    assert ok, reasons
    book.register(plan, ok, reasons)
    reasoner = MockReasoner(seed=42, content=content)
    reasoner.enable_strategy(book)
    choice = reasoner.selector.choose(state, NPC, 5)
    if choice is not None and choice.source == "urgent":
        assert choice.abandon == ""
        assert book.counts["paused"] == 1


def test_strategy_prompt_hides_urgent_active_goal():
    """urgent 目标不得作为 active_goal 下发，避免模型把它放进 preserve 导致计划作废。"""
    import json

    from wsim.agent.prompt import build_strategy_prompt

    content = _content()
    state = _state()
    urgent_id = f"urgent:{NPC}:urgent_money:0"
    state.goals[urgent_id] = Goal(
        id=urgent_id, npc=NPC, kind="wealth", target=40.0, progress=0.3,
        status="active", content_goal_id="", content_type="urgent_money",
    )
    state.entities[NPC].goal_id = urgent_id

    prompt = build_strategy_prompt(content, state, NPC, 0, 3)
    summary = json.loads(prompt.user.split("当前状态：\n", 1)[1])
    assert summary["active_goal"] == {}
    assert urgent_id not in prompt.user
    assert all(urgent_id != str(c.get("goal_id")) for c in summary["goal_candidates"])


def test_fake_plan_is_deterministic_and_consumable():
    from wsim.agent.strategy_client import make_fake_strategy_transport

    transport = make_fake_strategy_transport()
    content = _content()
    state = _state(active_goal=G1, progress=0.15)
    from wsim.agent.prompt import build_strategy_prompt

    prompt = build_strategy_prompt(content, state, NPC, 0, 3)
    first = transport(None, prompt.system, prompt.user)
    second = transport(None, prompt.system, prompt.user)
    assert first.text == second.text
    plan = parse_strategy_plan(
        __import__("json").loads(first.text), npc=NPC, tick=0, horizon_days=3, content=content
    )
    ok, reasons = validate_plan(plan, content, state)
    assert ok, reasons
