"""P4 里程碑链 / 维护型目标 / 玩家成长线最小回归。

只测纯函数与系统动作的输入输出，不跑长流程；长流程基线见 test_p3e_golden.py。
"""
from __future__ import annotations

from wsim.actions.common import wallet
from wsim.actions.milestones import SystemMaintenanceAction, SystemMilestoneAction
from wsim.api.growth import asset_value, growth_view, reputation_points, today_view
from wsim.core.content import Content
from wsim.core.models import Effect, Intent
from wsim.world.effects import apply_effects
from wsim.world.state import Goal, WorldState

PID = "player:u_p4"


def _mini_content() -> Content:
    c = Content()
    c.locations = {
        "a": {"id": "a", "name": "甲地", "exits": ["b"], "services": ["food"]},
        "b": {"id": "b", "name": "乙地", "exits": ["a"], "services": []},
    }
    c.npcs = [{"id": "n1", "name": "甲", "goals": [{"id": "g1", "type": "earn_money", "priority": 1}]}]
    c.economy = {
        "milestones": {
            "active_cap": 3,
            "chains": {
                "earn_money": [
                    {"stage": 2, "content_type": "earn_money", "kind": "wealth", "target": 250, "label": "扩店本钱"},
                ]
            },
            "secondary": {
                "earn_money": {
                    "id": "shop_ops", "content_type": "earn_money", "kind": "wealth",
                    "target": 30, "label": "雇伙计与交月租",
                    "require_money": 250, "repeat": True,
                }
            },
        },
        "shop_opening": {"stall": 300, "storefront": 1200, "branch": 3000,
                         "monthly_upkeep": {"stall": 30}},
        "maintenance": {
            "rent_period_days": 10, "sect_dues_period_days": 10, "sect_dues": 6,
            "skill_decay_period_days": 5, "skill_decay": 0.002,
            "relation_decay_period_days": 5, "relation_decay": 1.0, "decay_top_n": 5,
        },
        "stamina_cultivate": 4,
        "stamina_work": 12,
        "wages_daily": {"worker": 20},
        "income_tax_rate": 0.1,
        "transaction_tax_rate": 0.03,
        "living_costs": {"by_role": {"worker": {"housing": 2, "upkeep": 1, "dues": 0}}},
    }
    return c


def _seed_goal_state(content: Content, money: int = 0) -> WorldState:
    state = WorldState(locations=content.locations)
    apply_effects(state, [
        Effect("create_account", {"account": wallet("n1")}),
        *([Effect("mint", {"account": wallet("n1"), "amount": money, "reason": "test"})] if money else []),
        Effect("create_entity", {
            "id": "n1", "name": "甲", "role": "worker", "location": "a", "home": "a",
            "stamina": 100.0, "skills": {"worker": 0.5},
        }),
        Effect("create_business", {
            "id": "biz:a:stall", "name": "甲地摊", "location": "a", "kind": "stall",
            "owner": "n1", "cash_account": "treasury:region",
        }),
        Effect("create_business", {
            "id": "biz:a:food", "name": "甲地食肆", "location": "a", "kind": "food",
            "cash_account": "treasury:region",
        }),
    ])
    state.goals["n1:g1"] = Goal(
        id="n1:g1", npc="n1", kind="wealth", target=142.0, progress=142.0,
        status="completed", created_tick=10, source="npc", content_goal_id="g1",
        content_type="earn_money", progress_log=[(100, 142.0)],
    )
    state.entities["n1"].goal_id = "n1:g1"
    return state


def test_milestone_action_continues_chain_after_base_goal():
    content = _mini_content()
    state = _seed_goal_state(content, money=0)
    effects = SystemMilestoneAction(content).apply(
        state, Intent(actor="system", verb="system_milestone", args={"day": 1}, tick=143), None
    )
    created = [e for e in effects if e.op == "goal_create"]
    assert created, "一级完成后应续接二级里程碑"
    assert created[0].data["id"] == "n1:g1:ms2"
    assert created[0].data["target"] == 250.0
    assert created[0].data["source"] == "milestone"


def test_milestone_action_prep_goal_when_gate_not_met():
    content = _mini_content()
    state = _seed_goal_state(content, money=0)
    # 手工把一级里程碑标为完成，让动作走到二级（shop_ops 需 250 文）。
    state.goals["n1:g1:ms2"] = Goal(
        id="n1:g1:ms2", npc="n1", kind="wealth", target=250.0, progress=250.0,
        status="completed", created_tick=200, source="milestone",
        content_goal_id="g1:ms2", content_type="earn_money", progress_log=[(300, 250.0)],
    )
    effects = SystemMilestoneAction(content).apply(
        state, Intent(actor="system", verb="system_milestone", args={"day": 4}, tick=575), None
    )
    created = [e for e in effects if e.op == "goal_create"]
    assert created, "资金门槛不足时应先建备资目标"
    assert created[0].data["content_goal_id"] == "g1:prep:1"
    assert created[0].data["source"] == "milestone"


def test_maintenance_rent_creates_obligation_and_transfers():
    content = _mini_content()
    state = _seed_goal_state(content, money=50)
    effects = SystemMaintenanceAction(content).apply(
        state, Intent(actor="system", verb="system_maintenance", args={"day": 9}, tick=1439), None
    )
    goals = [e for e in effects if e.op == "goal_create"]
    assert any(g.data["source"] == "maintenance" for g in goals), "月租应产生维护型目标"
    rent = [g for g in goals if g.data["source"] == "maintenance"][0]
    assert rent.data["target"] == 30.0
    transfer = [e for e in effects if e.op == "transfer"]
    assert transfer and transfer[0].data["amount"] == 30


def test_maintenance_skill_decay():
    content = _mini_content()
    state = _seed_goal_state(content, money=0)
    before = float(state.entities["n1"].skills["worker"])
    effects = SystemMaintenanceAction(content).apply(
        state, Intent(actor="system", verb="system_maintenance", args={"day": 4}, tick=719), None
    )
    decay = [e for e in effects if e.op == "skill"]
    assert decay and decay[0].data["delta"] == -0.002
    assert before > 0.0


def test_growth_view_three_lines_have_next_level():
    content = _mini_content()
    state = _seed_goal_state(content, money=300)
    apply_effects(state, [
        Effect("create_account", {"account": wallet(PID)}),
        Effect("mint", {"account": wallet(PID), "amount": 300, "reason": "test"}),
        Effect("create_entity", {
            "id": PID, "name": "阿青", "role": "worker", "location": "a", "home": "a",
            "stamina": 100.0, "skills": {"worker": 0.35},
        }),
    ])
    view = growth_view(state, content, PID)
    assert view["reputation"]["level"] == 1 and view["reputation"]["next_at"] == 3.0
    assert view["skill"]["level"] == 1 and view["skill"]["next_at"] == 0.42
    assert view["assets"]["level"] == 1 and view["assets"]["next_at"] == 600.0
    assert view["assets"]["next_unlock"].startswith("可开摊")

    state.entities[PID].skills["worker"] = 0.42
    state.accounts[wallet(PID)] = 600
    view = growth_view(state, content, PID)
    assert view["skill"]["level"] == 2 and view["skill"]["name"] == "手熟"
    assert view["assets"]["level"] == 2 and view["assets"]["name"] == "小有积蓄"


def test_reputation_counts_quests_and_relations():
    content = _mini_content()
    state = _seed_goal_state(content, money=0)
    apply_effects(state, [
        Effect("create_account", {"account": wallet(PID)}),
        Effect("create_entity", {
            "id": PID, "name": "阿青", "role": "worker", "location": "a", "home": "a",
            "stamina": 100.0, "skills": {"worker": 0.35},
        }),
        Effect("relation_upsert", {"a": PID, "b": "n1", "kind": "friend", "affection": 10}),
    ])
    assert reputation_points(state, PID) == 5.0
    assert asset_value(state, PID, content) == 0.0


def test_today_view_lists_four_action_groups():
    content = _mini_content()
    state = _seed_goal_state(content, money=0)
    apply_effects(state, [
        Effect("create_account", {"account": wallet(PID)}),
        Effect("mint", {"account": wallet(PID), "amount": 300, "reason": "test"}),
        Effect("create_entity", {
            "id": PID, "name": "阿青", "role": "worker", "location": "a", "home": "a",
            "stamina": 100.0, "skills": {"worker": 0.35},
        }),
    ])
    board = {"rows": [{
        "id": "q1", "name": "整理药材", "giver_name": "甲", "reward_money": 12, "acceptable": True,
    }]}
    view = today_view(
        state, content, PID,
        location="a", quest_board=board,
        nearby=[{"id": "n1", "name": "甲", "role": "worker"}],
    )
    assert view["quests"][0]["id"] == "q1"
    assert view["skills"][0]["hours_to_next"] == 28
    assert view["npcs"][0]["id"] == "n1"
    assert [b["shop_type"] for b in view["business"]] == ["stall", "storefront", "branch"]
    assert view["business"][0]["affordable"] is True
    assert view["business"][1]["affordable"] is False
    assert view["next_action"].startswith("先接委托")
