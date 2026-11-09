"""`exert recover` / `exert regenerate` 的行为锁（段 2）。

原值取证：`kungfu/skill/force/recover.lpc:7-33`、`regenerate.lpc:7-33`。
`n = 100*q/force`（下限 20）；`n` 超过内力时 `q = q*neili/n`、`n = neili`。
"""
from __future__ import annotations

import random as _random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.skills import ExertAction, force_level  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}


def _state(*, eff_hp=80, hp=50, eff_jing=80, jing=50, force=50,
           neili=100, max_neili=200, max_jing=100, **extra) -> WorldState:
    state = WorldState(locations=LOCATIONS)
    payload = {
        "id": "npc.a", "name": "甲", "role": "npc", "location": "inn",
        "max_hp": 100, "hp": hp, "max_jing": max_jing, "jing": jing,
        "eff_jing": eff_jing, "neili": neili, "max_neili": max_neili,
        "skill_levels": {"force": force},
    }
    payload.update(extra)
    apply_effects(state, [Effect("create_entity", payload)])
    apply_effects(state, [Effect("eff_hp_set", {"entity": "npc.a", "value": eff_hp})])
    return state


def _intent(func: str, tick: int = 7, **args) -> Intent:
    return Intent("npc.a", "exert", {"func": func, **args}, tick, "npc")


def _run(state, func: str, tick: int = 7):
    action = ExertAction(healing=True)
    intent = _intent(func, tick)
    reasons = action.preconditions(state, intent)
    assert reasons == [], reasons
    effects = action.apply(state, intent, _random.Random(42))
    apply_effects(state, effects)
    return effects


# ---- force_level --------------------------------------------------------

def test_force_level_prefers_skill_levels():
    state = _state(force=50)
    assert force_level(state.entities["npc.a"]) == 50


def test_force_level_falls_back_to_legacy_skills():
    state = WorldState(locations=LOCATIONS)
    apply_effects(state, [Effect("create_entity", {
        "id": "npc.a", "name": "甲", "role": "npc", "location": "inn",
        "max_hp": 100, "hp": 50, "skills": {"force": 0.4},
    })])
    assert force_level(state.entities["npc.a"]) == 40


# ---- recover ------------------------------------------------------------

def test_recover_normal():
    """H5：q=30, n=100*30/50=60 → neili=40, qi=80。"""
    state = _state()
    _run(state, "recover")
    ent = state.entities["npc.a"]
    assert (ent.neili, ent.hp, ent.eff_hp) == (40, 80, 80)


def test_recover_scales_q_when_neili_short():
    """H6：q=60,n=120,neili=30 → q=15,n=30 → neili=0, qi=35。"""
    state = _state(eff_hp=80, hp=20, neili=30)
    _run(state, "recover")
    ent = state.entities["npc.a"]
    assert (ent.neili, ent.hp) == (0, 35)


def test_recover_rejects_small_gap():
    """H7：缺口 9 < 10 → not_injured。"""
    state = _state(eff_hp=80, hp=71)
    assert ExertAction(healing=True).preconditions(state, _intent("recover")) == ["not_injured"]


def test_recover_rejects_low_neili():
    """H8：内力 19 → insufficient_neili。"""
    state = _state(neili=19)
    assert ExertAction(healing=True).preconditions(state, _intent("recover")) == ["insufficient_neili"]


def test_recover_uses_floor_of_twenty():
    """H9：force=200, q=20 → n=10 抬到 20；neili 100→80，qi 封到 80。"""
    state = _state(force=200, eff_hp=80, hp=60, neili=100)
    _run(state, "recover")
    ent = state.entities["npc.a"]
    assert (ent.neili, ent.hp) == (80, 80)


def test_recover_sets_busy_only_in_fight():
    state = _state()
    apply_effects(state, [Effect("set_field", {
        "entity": "npc.a", "field": "combat_target", "value": "npc.b",
    })])
    effects = _run(state, "recover", tick=7)
    ent = state.entities["npc.a"]
    assert ent.busy_until == 8
    assert any(e.op == "busy_set" for e in effects)


def test_recover_idle_does_not_set_busy():
    state = _state()
    _run(state, "recover")
    assert state.entities["npc.a"].busy_until == 0


# ---- regenerate ---------------------------------------------------------

def test_regenerate_mirrors_recover():
    """H10：jing=50, eff_jing=80, force=50, neili=100 → jing=80, neili=40。"""
    state = _state(max_jing=100, jing=50, eff_jing=80)
    _run(state, "regenerate")
    ent = state.entities["npc.a"]
    assert (ent.jing, ent.eff_jing, ent.neili) == (80, 80, 40)


def test_regenerate_requires_jing_model():
    state = _state(max_jing=0, jing=0, eff_jing=-2)
    assert ExertAction(healing=True).preconditions(
        state, _intent("regenerate")) == ["no_combat_stats"]


# ---- 开关与未知功能 -----------------------------------------------------

def test_exert_is_closed_when_switch_off():
    state = _state()
    assert ExertAction(healing=False).preconditions(
        state, _intent("recover")) == ["not_available"]


def test_signature_heal_is_not_implemented():
    state = _state()
    assert ExertAction(healing=True).preconditions(
        state, _intent("heal")) == ["no_such_function"]


def test_busy_blocks_exert():
    state = _state()
    apply_effects(state, [Effect("busy_set", {
        "entity": "npc.a", "rounds": 5, "tick": 5,
    })])
    assert ExertAction(healing=True).preconditions(
        state, _intent("recover", tick=9)) == ["busy"]
