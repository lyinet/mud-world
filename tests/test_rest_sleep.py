"""rest / sleep：同一 PlayerRestAction 的两种恢复口径。"""
from __future__ import annotations

from pathlib import Path

from wsim.actions.player import rest_rate
from wsim.actions.registry import build_registry
from wsim.core.clock import TICKS_PER_HOUR
from wsim.core.content import load_content
from wsim.core.models import Intent
from wsim.world.state import Entity, WorldState

ROOT = Path(__file__).resolve().parents[1]
LOCATIONS = {"wild": {"id": "wild", "name": "野外", "exits": [], "services": []}}


def _registry():
    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    return build_registry(content, seed=42)


def _state() -> WorldState:
    state = WorldState(locations=LOCATIONS)
    state.entities["player:hero"] = Entity(
        id="player:hero",
        name="英雄",
        role="visitor",
        location="wild",
        hp=40,
        max_hp=100,
        stamina=20.0,
        max_stamina=100.0,
    )
    return state


def _intent(verb: str, **args) -> Intent:
    return Intent("player:hero", verb, {"player_id": "player:hero", **args}, 0, "player")


def test_rest_and_sleep_resolve_to_the_same_action():
    reg = _registry()
    rest = reg.get("rest")
    sleep = reg.get("sleep")
    assert rest is not None
    assert sleep is not None
    assert sleep is rest


def test_sleep_restores_hp_and_stamina_to_max():
    reg = _registry()
    state = _state()
    effects = reg.get("sleep").apply(state, _intent("sleep"), None)

    stamina = next(e for e in effects if e.op == "stamina")
    hp = next(e for e in effects if e.op == "hp_add")
    assert stamina.data["delta"] == 100.0 - 20.0
    assert hp.data["delta"] == 100 - 40


def test_rest_uses_location_rate_and_hours():
    reg = _registry()
    state = _state()
    ent = state.entities["player:hero"]
    effects = reg.get("rest").apply(state, _intent("rest", hours=2), None)

    rate = rest_rate(state, ent.location, ent.home or "")
    expected = float(2 * TICKS_PER_HOUR) * rate
    stamina = next(e for e in effects if e.op == "stamina")
    hp = next(e for e in effects if e.op == "hp_add")
    assert stamina.data["delta"] == expected
    assert hp.data["delta"] == int(expected)
    assert stamina.data["reason"] == "rest"
