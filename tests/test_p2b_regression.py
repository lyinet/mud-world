"""P2-B 回归：真实内容包、意图驱动、system_plan 退场、K1 全量口径。"""
from __future__ import annotations

from conftest import ROOT
from wsim.audit.goals import goal_stats
from wsim.actions.business import OpenShopAction
from wsim.actions.common import treasury_balance, treasury_guard_active, treasury_guard_threshold
from wsim.actions.quests import SystemQuestFailAction, SystemQuestPostAction
from wsim.actions.system import SystemShopDividendAction
from wsim.core.models import Effect, Intent
from wsim.sim.headless import RunConfig, run_headless
from wsim.sim.intent import MockReasoner
from wsim.world.effects import apply_effects
from wsim.world.store import EventStore


def test_real_content_fixture_covers_full_world(real_content):
    assert len(real_content.npcs) == 31
    assert len(real_content.locations) == 9
    assert len(real_content.items) == 31
    assert len(real_content.verbs) >= 14


def test_headless_is_intent_driven_and_never_submits_system_plan(real_content, tmp_path):
    summary = run_headless(
        ROOT,
        RunConfig(days=1, seed=42, out_root=tmp_path, run_id="p2b-intent"),
        content=real_content,
    )
    store = EventStore(tmp_path / "p2b-intent" / "events.sqlite")
    events = store.read_all()
    store.close()

    assert not [e for e in events if e.verb == "system_plan"], "system_plan 应已退出主循环"
    assert not [e for e in events if "unknown_verb" in e.reason], "出现 unknown_verb"

    planner_events = [e for e in events if e.source == "planner"]
    assert planner_events, "意图层没有产生任何动作"
    missing_reason = [e.seq for e in planner_events if e.outcome == "ok" and not str(e.reason or "").strip()]
    assert not missing_reason, f"意图层事件缺少 reason: {missing_reason[:5]}"

    lod = summary["lod"]
    assert lod["processed_wakes"] > 0 and lod["decide_calls"] == lod["processed_wakes"]
    assert lod["state_provider_calls"] == lod["processed_wakes"]
    assert summary["intent"]["reason_coverage"] >= 0.99


def test_mock_reasoner_is_deterministic_and_explains_every_choice(real_content, real_state):
    view = {
        "tick": 0,
        "state": real_state,
        "content": real_content,
        "tier": "active",
        "interval": 144,
    }
    npc = sorted(real_state.entities)[0]
    a = MockReasoner(seed=42, content=real_content).decide({"id": npc}, view)
    b = MockReasoner(seed=42, content=real_content).decide({"id": npc}, view)
    assert [i.to_dict() for i in a] == [i.to_dict() for i in b]
    assert a, "确定性规则桩应给出计划"
    assert all(i.reason.strip() for i in a), "每个 Intent 必须带一句话动机"


def test_k1_reports_91_full_scope_and_excluding_one_shot(real_content, real_state):
    k1 = goal_stats(real_state, 0, real_content)
    assert k1["k1_scope"] == "all_content_goals"
    assert k1["goals_total"] == 91
    assert abs(k1["k1_rate"] - k1["goals_progressed"] / 91) < 1e-9
    assert "k1_rate_excluding_one_shot" in k1
    assert 0.0 <= k1["k1_rate_excluding_one_shot"] <= 1.0


def test_treasury_guard_pauses_nonessential_capex(real_content, real_state):
    threshold = treasury_guard_threshold(real_content)
    assert threshold == 2400  # employer_reserve 2000 × 1.2
    assert not treasury_guard_active(real_state, real_content)

    # 把金库烧到阈值以下（测试可 mint/burn，业务代码不允许）。
    burn = Effect(
        "burn",
        {"account": "treasury:region",
         "amount": treasury_balance(real_state) - (threshold - 1),
         "reason": "test_guard"},
    )
    apply_effects(real_state, [burn])
    assert treasury_balance(real_state) == threshold - 1
    assert treasury_guard_active(real_state, real_content)

    npc = sorted(real_state.entities)[0]
    ent = real_state.entities[npc]
    shop = Intent(
        actor=npc, verb="open_shop",
        args={"at": ent.location, "shop_type": "stall", "name": "测试铺"},
        tick=1, source="planner",
    )
    assert OpenShopAction(real_content).preconditions(real_state, shop) == ["treasury_guard"]
    dividend = Intent(actor="system", verb="system_shop_dividend", args={}, tick=1, source="system")
    assert SystemShopDividendAction(real_content).apply(real_state, dividend, None) == []


def test_stale_open_quest_expires_so_template_can_repost(real_content, real_state):
    tid = "escort_iron_short"
    qid = f"quest:{tid}:stale"
    apply_effects(
        real_state,
        [
            Effect(
                "quest_create",
                {
                    "id": qid,
                    "template_id": tid,
                    "giver": "pei_wujiu",
                    "target_npc": "",
                    "target_location": "market",
                    "verb": "escort",
                    "args": {"route": ["escort_agency", "smithy", "market"], "cargo_value": 150, "fee": 140},
                    "reward_money": 12,
                    "reward_relation": 2.0,
                    "deadline_tick": 10,
                    "created_tick": 0,
                    "settlement_amount": 0,
                },
            )
        ],
    )
    assert real_state.quests[qid].status == "open"

    fail = SystemQuestFailAction(real_content)
    effects = fail.apply(
        real_state,
        Intent(actor="system", verb="system_quest_fail", args={}, tick=50, source="system"),
        None,
    )
    apply_effects(real_state, effects)
    # 无人接的过期单关闭为 expired，而不是永远占住模板名。
    assert real_state.quests[qid].status == "expired"

    post = SystemQuestPostAction(real_content)
    posted = post.apply(
        real_state,
        Intent(actor="system", verb="system_quest_post", args={"day": 0}, tick=100, source="system"),
        None,
    )
    assert any(
        e.op == "quest_create" and str((e.data or {}).get("template_id")) == tid for e in posted
    ), "过期占位清除后，同一模板应能重新发单"
