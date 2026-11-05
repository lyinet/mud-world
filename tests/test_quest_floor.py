"""委托板保底供给：每发布点每日 ≥1 可接单、金库垫付、确定性轮转。"""
from __future__ import annotations

from wsim.actions.common import TREASURY_ACCOUNT, wallet
from wsim.actions.quests import SystemQuestPostAction
from wsim.core.content import Content
from wsim.core.models import Effect, Intent
from wsim.world.effects import apply_effects
from wsim.world.state import Quest, WorldState


def _mini(templates: list[dict], *, giver_money: int = 0, treasury: int = 100) -> tuple[Content, WorldState]:
    c = Content()
    c.locations = {"m": {"id": "m", "name": "集市", "exits": []}}
    c.economy = {"quests": {"expired_cap_per_template": 3, "expired_cap_global": 20}}
    c.quests = {str(t["id"]): t for t in templates}
    state = WorldState(locations=c.locations)
    effects = [
        Effect("create_account", {"account": TREASURY_ACCOUNT}),
        Effect("create_account", {"account": wallet("n1")}),
    ]
    if treasury > 0:
        effects.append(Effect("mint", {"account": TREASURY_ACCOUNT, "amount": treasury, "reason": "test_float"}))
    if giver_money > 0:
        effects.append(Effect("mint", {"account": wallet("n1"), "amount": giver_money, "reason": "test"}))
    effects.append(
        Effect("create_entity", {
            "id": "n1", "name": "甲", "role": "worker", "location": "m", "home": "m",
            "stamina": 100.0, "skills": {},
        })
    )
    apply_effects(state, effects)
    return c, state


def _tpl(tid: str, reward: int = 8, category: str = "errand", *, repeatable: bool = True) -> dict:
    return {
        "id": tid,
        "category": category,
        "repeatable": repeatable,
        "giver": "n1",
        "target_location": "m",
        "verb": "trade",
        "intent_args": {},
        "deadline_days": 2,
        "reward": {"money": reward, "relation": 1},
    }


def _post(content: Content, state: WorldState, *, tick: int = 0, day: int = 0) -> list[Effect]:
    return SystemQuestPostAction(content).apply(
        state,
        Intent(actor="system", verb="system_quest_post", args={"day": day}, tick=tick, source="system"),
        None,
    )


def test_floor_advances_reward_from_treasury_without_mint():
    content, state = _mini([_tpl("t1", reward=8)], giver_money=3, treasury=100)
    effects = _post(content, state)
    ops = [e.op for e in effects]
    assert "quest_create" in ops
    transfer = next(e for e in effects if e.op == "transfer")
    assert transfer.data["src"] == TREASURY_ACCOUNT
    assert transfer.data["dst"] == wallet("n1")
    assert transfer.data["amount"] == 5
    assert transfer.data["reason"] == "quest_reward_advance"
    assert "mint" not in ops
    before_minted = state.minted_total
    apply_effects(state, effects)
    assert state.account_balance(TREASURY_ACCOUNT) == 95
    assert state.account_balance(wallet("n1")) == 8
    assert state.minted_total == before_minted
    # 账目守恒：所有账户之和 = minted - burned
    assert sum(state.accounts.values()) == state.minted_total - state.burned_total


def test_floor_skips_when_treasury_cannot_cover():
    content, state = _mini([_tpl("t1", reward=8)], giver_money=0, treasury=4)
    effects = _post(content, state)
    assert not [e for e in effects if e.op in ("quest_create", "mint")]
    assert not [e for e in effects if e.op == "transfer"]
    apply_effects(state, effects)
    assert state.account_balance(TREASURY_ACCOUNT) == 4
    assert state.account_balance(wallet("n1")) == 0


def test_floor_rotation_is_deterministic_and_spreads_templates():
    content, state = _mini(
        [_tpl("a_errand", reward=8, category="errand"),
         _tpl("b_procure", reward=9, category="procurement"),
         _tpl("c_work", reward=10, category="commission")],
        giver_money=100,
        treasury=100,
    )
    # 让常规发布全部停发（完成率过低），只剩保底路径。
    for tid in content.quests:
        state.quest_stats[tid] = {"created": 10, "completed": 0, "failed": 0, "expired": 10}
    picks = []
    for day in range(3):
        effects = _post(content, state, tick=day * 144, day=day)
        created = [e.data["template_id"] for e in effects if e.op == "quest_create"]
        assert len(created) == 1, created
        picks.append(created[0])
        apply_effects(state, effects)
        # 次日之前把上一张单下架，模拟无人接单过期
        for qid, q in list(state.quests.items()):
            if q.status == "open":
                q.status = "expired"
                q.completed_tick = day * 144
    # 优先组（errand/procurement）内部按 day % n 轮转；两日一轮，确定性可复现。
    assert picks == ["a_errand", "b_procure", "a_errand"]


def test_floor_falls_back_to_non_preferred_category():
    content, state = _mini([_tpl("c_work", reward=10, category="commission")], giver_money=0, treasury=100)
    effects = _post(content, state)
    created = [e.data["template_id"] for e in effects if e.op == "quest_create"]
    assert created == ["c_work"]


def test_floor_reopens_same_template_when_all_candidates_are_in_flight():
    content, state = _mini([_tpl("t1")], giver_money=100, treasury=100)
    state.quests["quest:t1:0"] = Quest(
        id="quest:t1:0", template_id="t1", giver="n1", acceptor="npc",
        target_npc="", target_location="m", verb="trade", args={},
        reward_money=8, reward_relation=1.0, deadline_tick=288, status="accepted", created_tick=0,
    )
    effects = _post(content, state, tick=144, day=1)
    created = [e for e in effects if e.op == "quest_create"]
    assert len(created) == 1
    assert created[0].data["floor"] is True
    assert created[0].data["id"] == "quest:t1:144"


def test_floor_does_not_stack_when_acceptable_quest_already_open():
    content, state = _mini([_tpl("t1"), _tpl("t2")], giver_money=100, treasury=100)
    state.quests["quest:t1:0"] = Quest(
        id="quest:t1:0", template_id="t1", giver="n1", acceptor=None,
        target_npc="", target_location="m", verb="trade", args={},
        reward_money=8, reward_relation=1.0, deadline_tick=288, status="open", created_tick=0,
    )
    effects = _post(content, state)
    created = [e.data["template_id"] for e in effects if e.op == "quest_create"]
    # 常规路径会补 t2（模板级），但保底路径不应再加第三张。
    assert created == ["t2"]
