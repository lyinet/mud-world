"""事务原子性：一个 Intent 的 effects 必须全部成功或全部不落盘。"""
from __future__ import annotations

from wsim.actions.registry import Action, ActionRegistry
from wsim.core.models import Effect, Intent
from wsim.world.store import EventStore
from wsim.world.writer import Writer
from wsim.world.state import WorldState


class _BoomAction(Action):
    verb = "boom"

    def apply(self, state, intent, rng):
        # 第一笔合法，第二笔余额不足；整笔 Intent 必须回滚
        return [
            Effect("transfer", {"src": "wallet:n1", "dst": "treasury:region", "amount": 5, "reason": "ok"}),
            Effect("transfer", {"src": "wallet:n1", "dst": "treasury:region", "amount": 999999, "reason": "bad"}),
        ]


def test_failed_effect_rolls_back_entire_intent(tmp_path, content, state):
    registry = ActionRegistry()
    registry.register(_BoomAction())
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, registry, seed=42)
    before = writer.state.hash()
    wallet_before = writer.state.accounts["wallet:n1"]

    event = writer.submit(Intent(actor="n1", verb="boom", args={}, tick=1, source="test"))

    assert event.outcome == "rejected"
    assert "effect_invalid" in event.reason
    assert event.effects == []
    assert writer.state.hash() == before
    assert writer.state.accounts["wallet:n1"] == wallet_before
    assert store.count() == 1
    assert store.read_all()[0].outcome == "rejected"
    store.close()


def test_unknown_verb_rejects_without_state_change(tmp_path, content, state):
    store = EventStore(tmp_path / "events.sqlite")
    writer = Writer(state, store, ActionRegistry(), seed=42)
    before = state.hash()
    event = writer.submit(Intent(actor="n1", verb="not_a_verb", args={}, tick=1, source="test"))
    assert event.outcome == "rejected"
    assert writer.state.hash() == before
    assert store.count() == 1
    store.close()
