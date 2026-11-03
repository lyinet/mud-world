"""apply 纯洁性：动作不得修改入参 state，且同一 state+PRNG 输出一致。"""
from __future__ import annotations

from wsim.actions.basic import ConsumeAction, CultivateAction, MoveAction, WorkAction
from wsim.core.models import Intent
from wsim.core.rng import PRNG
from wsim.world.effects import effects_to_dicts


def _intent(verb, args, tick=1):
    return Intent(actor="n1", verb=verb, args=args, tick=tick, source="npc")


def test_work_apply_is_pure_and_repeatable(content, state):
    action = WorkAction(content)
    intent = _intent("work", {"at": "a", "hours": 8})
    before = state.hash()
    e1 = action.apply(state, intent, PRNG(42, 1, "action:work"))
    e2 = action.apply(state, intent, PRNG(42, 1, "action:work"))
    assert state.hash() == before
    assert effects_to_dicts(e1) == effects_to_dicts(e2)


def test_move_apply_is_pure(content, state):
    action = MoveAction(content)
    intent = _intent("move", {"to": "b"})
    before = state.hash()
    effects = action.apply(state, intent, PRNG(42, 2, "action:move"))
    assert state.hash() == before
    assert any(e.op == "set_location" for e in effects)


def test_cultivate_apply_is_pure(content, state):
    action = CultivateAction(content)
    intent = _intent("cultivate", {"hours": 2, "skill": "work"})
    before = state.hash()
    effects = action.apply(state, intent, PRNG(42, 3, "action:cultivate"))
    assert state.hash() == before
    assert any(e.op == "skill" for e in effects)


def test_consume_apply_is_pure(content, state):
    action = ConsumeAction(content)
    intent = _intent("consume", {"item": "food"})
    before = state.hash()
    effects = action.apply(state, intent, PRNG(42, 4, "action:consume"))
    assert state.hash() == before
    assert any(e.op in ("item_move", "item_consume") for e in effects)
