"""P3-E 前置校验：LLM/planner 的位置不满足时不得进入事件日志。"""
from __future__ import annotations

from pathlib import Path

from wsim.actions.registry import build_registry
from wsim.audit.illegal import illegal_stats
from wsim.core.content import load_content
from wsim.core.models import Effect, Event, Intent
from wsim.sim.headless import intent_block_reason, is_prunable
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState
from wsim.world.store import EventStore
from wsim.world.writer import Writer

ROOT = Path(__file__).resolve().parents[1]


def _world(tmp_path):
    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    state = WorldState(locations=content.locations)
    apply_effects(
        state,
        [
            Effect("create_entity", {"id": "npc_a", "name": "甲", "role": "脚夫", "location": "market"}),
            Effect("create_entity", {"id": "npc_b", "name": "乙", "role": "脚夫", "location": "inn"}),
        ],
    )
    registry = build_registry(content, seed=1)
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, registry, seed=1)
    return registry, writer, store


def test_llm_talk_without_move_is_blocked_before_event(tmp_path):
    registry, writer, store = _world(tmp_path)
    intent = Intent(
        actor="npc_a",
        verb="talk",
        args={"target": "npc_b", "message": "近来可好？"},
        tick=6,
        source="llm",
    )

    reason = intent_block_reason(registry, writer.state, intent)
    assert reason == "not_together"
    if not reason:
        writer.submit(intent)
    assert store.read_all() == [], "前置不满足的 talk 不能生成 talk:not_together 事件"


def test_location_precondition_is_pruned_before_blocked(tmp_path):
    """位置类前置不满足时直接判不可行（pruned），不再计入 blocked。"""
    registry, writer, store = _world(tmp_path)
    talk = Intent(
        actor="npc_a",
        verb="talk",
        args={"target": "npc_b", "message": "近来可好？"},
        tick=6,
        source="llm",
    )
    assert intent_block_reason(registry, writer.state, talk) == "not_together"
    assert is_prunable("not_together") is True
    assert is_prunable("not_at_giver") is True
    assert is_prunable("item_not_held") is False
    assert store.read_all() == []

    ghost = Intent(
        actor="npc_a", verb="talk", args={"target": "npc_ghost", "message": "喂"},
        tick=6, source="llm",
    )
    assert intent_block_reason(registry, writer.state, ghost) == "unknown_target"
    assert is_prunable("unknown_target") is False


def test_planner_trade_and_quest_are_blocked_before_event(tmp_path):
    registry, writer, store = _world(tmp_path)
    trade = Intent(
        actor="npc_a",
        verb="trade",
        args={"target": "npc_b", "offer": [], "want": []},
        tick=6,
        source="planner",
    )
    quest = Intent(
        actor="npc_a",
        verb="quest_accept",
        args={"quest": "quest:not-exist:0"},
        tick=6,
        source="planner",
    )
    assert intent_block_reason(registry, writer.state, trade) in {"not_together", "unknown_target"}
    assert intent_block_reason(registry, writer.state, quest) in {"not_at_giver", "quest_not_found"}
    for intent in (trade, quest):
        if not intent_block_reason(registry, writer.state, intent):
            writer.submit(intent)
    assert store.read_all() == []


def test_k6_reports_executed_and_attempt_rates():
    """K6 必须同时给出两种口径；严格口径把提案前拦截计入违规尝试。"""
    def event(seq: int, outcome: str, reason: str = "") -> Event:
        return Event(
            seq=seq, tick=seq, day=1, intent_id=f"intent:{seq}", actor="npc_a",
            verb="talk", args={}, effects=[], outcome=outcome, reason=reason, source="llm",
        )

    stats = illegal_stats(
        [event(1, "ok"), event(2, "ok"), event(3, "rejected", "not_together"), event(4, "ok")],
        precondition_blocked=2,
    )
    assert stats["events_total"] == 4
    assert stats["rejected"] == 1
    assert stats["illegal_rate"] == 1 / 4
    assert stats["precondition_blocked"] == 2
    assert stats["illegal_attempt_rate"] == (1 + 2) / 4


def test_player_intent_is_not_silently_blocked(tmp_path):
    registry, writer, _ = _world(tmp_path)
    intent = Intent(
        actor="player:u_demo",
        verb="talk",
        args={"target": "npc_b", "message": "近来可好？"},
        tick=6,
        source="player",
    )
    # 玩家意图必须走 Reject 事件，由 API 返回可解释的人话原因；不能被前置层吞掉。
    assert intent_block_reason(registry, writer.state, intent) == ""
