"""任务板健康度：可行性预检、完成率门槛、过期上限与清理。"""
from __future__ import annotations

from wsim.actions.common import wallet
from wsim.actions.quests import SystemQuestFailAction, SystemQuestPostAction
from wsim.core.clock import TICKS_PER_DAY
from wsim.core.models import Effect, Intent
from wsim.world.effects import apply_effects
from wsim.world.state import Quest


def _post(state, content, tick: int = 0) -> list[Effect]:
    action = SystemQuestPostAction(content)
    return action.apply(
        state,
        Intent(actor="system", verb="system_quest_post", args={}, tick=tick, source="system"),
        None,
    )


def _expired_quest(tid: str, qid: str, tick: int = 0) -> Quest:
    return Quest(
        id=qid,
        template_id=tid,
        giver="liu_sanniang",
        acceptor=None,
        target_npc="su_hexiang",
        target_location="pharmacy",
        verb="move",
        args={"to": "pharmacy"},
        reward_money=6,
        reward_relation=2.0,
        deadline_tick=tick,
        status="expired",
        completed_tick=tick,
    )


def test_post_skips_when_giver_cannot_pay(real_content, real_state):
    giver = real_content.quests["errand_silk_sample"]["giver"]
    balance = real_state.account_balance(wallet(giver))
    apply_effects(
        real_state,
        [Effect("burn", {"account": wallet(giver), "amount": balance, "reason": "test_drain"})],
    )
    effects = _post(real_state, real_content)
    created = [e.data["template_id"] for e in effects if e.op == "quest_create"]
    assert "errand_silk_sample" not in created


def test_post_skips_low_completion_rate(real_content, real_state):
    real_state.quest_stats["errand_silk_sample"] = {
        "created": 10,
        "completed": 1,
        "failed": 0,
        "expired": 9,
    }
    effects = _post(real_state, real_content)
    created = [e.data["template_id"] for e in effects if e.op == "quest_create"]
    assert "errand_silk_sample" not in created


def test_post_skips_when_template_expired_cap_reached(real_content, real_state):
    for i in range(3):
        real_state.quests[f"expired:errand:{i}"] = _expired_quest(
            "errand_silk_sample", f"expired:errand:{i}"
        )
    effects = _post(real_state, real_content)
    created = [e.data["template_id"] for e in effects if e.op == "quest_create"]
    assert "errand_silk_sample" not in created


def test_post_floor_supply_survives_global_expired_cap(real_content, real_state):
    """全局过期上限只压常规发布，不能饿死保底供给：

    20 条过期记录时常规路径 0 单，但每个有 giver 的发布点仍应有 1 张可接单。
    """
    template_ids = sorted(real_content.quests)
    for i in range(20):
        tid = template_ids[i % len(template_ids)]
        real_state.quests[f"expired:global:{i}"] = _expired_quest(tid, f"expired:global:{i}")
    effects = _post(real_state, real_content)
    created = [e.data["template_id"] for e in effects if e.op == "quest_create"]
    assert created, "全局过期上限不应让所有发布点都断供"
    points = {
        real_state.entities[str(tpl.get("giver", ""))].location
        for tpl in real_content.quests.values()
        if str(tpl.get("giver", "")) in real_state.entities
        and float(tpl.get("require_reputation", 0) or 0) <= 0
    }
    created_locs = {
        real_state.entities[str(real_content.quests[tid]["giver"])].location
        for tid in created
    }
    assert created_locs == points


def test_fail_action_prunes_stale_expired_and_keeps_stats(real_content, real_state):
    qid = "quest:prune:0"
    real_state.quests[qid] = _expired_quest("errand_silk_sample", qid, tick=0)
    real_state.quest_stats["errand_silk_sample"] = {
        "created": 1,
        "completed": 0,
        "failed": 0,
        "expired": 1,
    }
    action = SystemQuestFailAction(real_content)
    tick = (int((real_content.economy.get("quests") or {}).get("expired_retention_days", 7)) + 1) * TICKS_PER_DAY
    effects = action.apply(
        real_state,
        Intent(actor="system", verb="system_quest_fail", args={}, tick=tick, source="system"),
        None,
    )
    assert any(e.op == "quest_prune" and qid in e.data["ids"] for e in effects)
    apply_effects(real_state, effects)
    assert qid not in real_state.quests
    assert real_state.quest_stats["errand_silk_sample"]["expired"] == 1


def _completed_quest(tid: str, qid: str, tick: int = 0) -> Quest:
    quest = _expired_quest(tid, qid, tick)
    quest.status = "completed"
    quest.acceptor = "su_hexiang"
    return quest


def _fail_effects(state, content, tick: int) -> list[Effect]:
    return SystemQuestFailAction(content).apply(
        state,
        Intent(actor="system", verb="system_quest_fail", args={}, tick=tick, source="system"),
        None,
    )


def test_completed_quests_are_not_pruned_in_no_player_world(real_content, real_state):
    """无玩家世界不清理 completed：state_hash 必须与旧基线逐字节一致。"""
    qid = "quest:done-old"
    real_state.quests[qid] = _completed_quest("errand_silk_sample", qid, tick=0)
    real_state.quest_stats["errand_silk_sample"] = {
        "created": 1,
        "completed": 1,
        "failed": 0,
        "expired": 0,
    }
    effects = _fail_effects(real_state, real_content, tick=10 * TICKS_PER_DAY)
    assert not [e for e in effects if e.op == "quest_prune"]
    assert qid in real_state.quests


def test_completed_quests_pruned_after_retention_with_players(real_content, real_state):
    from wsim.world.state import PlayerProfile

    real_state.players["player:tester"] = PlayerProfile(
        id="player:tester", name="测试", archetype="wanderer", role="player"
    )
    old = "quest:done-old"
    fresh = "quest:done-fresh"
    real_state.quests[old] = _completed_quest("errand_silk_sample", old, tick=0)
    real_state.quests[fresh] = _completed_quest("errand_silk_sample", fresh, tick=8 * TICKS_PER_DAY)
    real_state.quest_stats["errand_silk_sample"] = {
        "created": 2,
        "completed": 2,
        "failed": 0,
        "expired": 0,
    }
    tick = 9 * TICKS_PER_DAY
    effects = _fail_effects(real_state, real_content, tick)
    pruned = {qid for e in effects if e.op == "quest_prune" for qid in e.data["ids"]}
    assert old in pruned
    assert fresh not in pruned
    apply_effects(real_state, effects)
    assert old not in real_state.quests
    assert fresh in real_state.quests
    # 清理只动投影：历史统计不受影响。
    assert real_state.quest_stats["errand_silk_sample"]["completed"] == 2


def test_completed_quests_capped_by_count(real_content, real_state):
    """数量上限兜底：即使都在保留期内，最旧的超额记录也会被清掉。"""
    from wsim.world.state import PlayerProfile

    real_state.players["player:tester"] = PlayerProfile(
        id="player:tester", name="测试", archetype="wanderer", role="player"
    )
    cap = int((real_content.economy.get("quests") or {}).get("completed_max", 1000))
    for i in range(cap + 3):
        qid = f"quest:done:{i:04d}"
        real_state.quests[qid] = _completed_quest("errand_silk_sample", qid, tick=i)
    effects = _fail_effects(real_state, real_content, tick=cap + 3)
    pruned = [qid for e in effects if e.op == "quest_prune" for qid in e.data["ids"]]
    assert pruned == [f"quest:done:{i:04d}" for i in range(3)]


def test_completed_prune_is_replay_identical(real_content, real_state):
    """清理效果走事件日志：回放同一份 effect 必须得到相同 state_hash。"""
    from wsim.world.state import PlayerProfile

    real_state.players["player:tester"] = PlayerProfile(
        id="player:tester", name="测试", archetype="wanderer", role="player"
    )
    for i in range(3):
        qid = f"quest:done:{i}"
        real_state.quests[qid] = _completed_quest("errand_silk_sample", qid, tick=i)
    effects = _fail_effects(real_state, real_content, tick=20 * TICKS_PER_DAY)
    before_hash = real_state.hash()
    pre = real_state.clone()
    apply_effects(real_state, effects)
    # 从同一前置状态重放同一批 effect：结果必须逐字节一致，且再次重放幂等。
    apply_effects(pre, effects)
    assert pre.hash() == real_state.hash()
    apply_effects(real_state, effects)
    assert real_state.hash() == pre.hash()
    assert real_state.hash() != before_hash
