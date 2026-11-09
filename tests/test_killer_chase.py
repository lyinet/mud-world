"""原版 `feature/attack.lpc` killer 名单复刻测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.aggression import _remove_enemy, _reset  # noqa: E402
from wsim.actions.combat import AnsuanAction, FightAction, KillAction, TouxiAction  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim.aggression import (  # noqa: E402
    configure_killer_chase,
    configure_multi_combat,
    killer_chase_enabled,
    multi_combat_enabled,
)
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.world.effects import apply_atomic, apply_effects  # noqa: E402
from wsim.world.state import Entity, WorldState  # noqa: E402

LEGACY = {
    "events_total": 735,
    "state_final": "009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9",
    "event_final": "02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae",
    "business_cash": 11442,
    "precondition_blocked": 6,
    "precondition_pruned": 3,
}


def _restore(value: bool) -> None:
    configure_killer_chase(value)


def test_entity_serialization_omits_empty_killer_and_pursuer():
    ent = Entity(id="npc:test", name="测试", role="monster", location="room")
    data = ent.to_dict()
    assert "killer" not in data
    assert "pursuer" not in data

    ent.killer = ["player:p1"]
    ent.pursuer = True
    data = ent.to_dict()
    assert data["killer"] == ["player:p1"]
    assert data["pursuer"] is True


def _hero(**over) -> Entity:
    data = dict(
        id="player:hero",
        name="英雄",
        role="wanderer",
        location="inn",
        max_hp=100000,
        hp=100000,
        attack=20,
        defense=5,
        attrs={"str": 30, "dex": 20},
        skills={"dodge": 0.4},
        combat_exp=10,
        skill_levels={"unarmed": 60},
    )
    data.update(over)
    return Entity(**data)


def _monster(**over) -> Entity:
    data = dict(
        id="npc.boar",
        name="野猪",
        role="monster",
        location="inn",
        race="野兽",
        max_hp=1000000,
        hp=1000000,
        attack=10,
        defense=2,
        attrs={"str": 20, "dex": 15},
        skills={"dodge": 0.2},
        combat_exp=5,
        skill_levels={"unarmed": 40},
    )
    data.update(over)
    return Entity(**data)


def _state(hero=None, monster=None, extra_locations=None) -> WorldState:
    locs = {"inn": {"id": "inn", "name": "客栈", "exits": []}}
    if extra_locations:
        locs.update(extra_locations)
    state = WorldState(locations=locs)
    state.entities[(hero or _hero()).id] = hero or _hero()
    mon = monster or _monster()
    state.entities[mon.id] = mon
    return state


def _apply(action, state, verb, args):
    intent = Intent(actor="player:hero", verb=verb, args=args, tick=7, source="player")
    return apply_atomic(state, action.apply(state, intent, PRNG(42, 7)))


def test_kill_ob_writes_killer_on_all_entry_points():
    old_k, old_m = killer_chase_enabled(), multi_combat_enabled()
    try:
        configure_killer_chase(True)
        configure_multi_combat(False)

        beast = _apply(FightAction(), _state(), "fight", {"target": "npc.boar"})
        assert "player:hero" in beast.entities["npc.boar"].killer

        speaking = _monster(race="人类")
        fight_human = _apply(FightAction(), _state(monster=speaking), "fight", {"target": "npc.boar"})
        assert fight_human.entities["npc.boar"].killer == []

        kill = _apply(KillAction(), _state(), "kill", {"target": "npc.boar"})
        assert "player:hero" in kill.entities["npc.boar"].killer
        assert "npc.boar" in kill.entities["player:hero"].killer

        touxi = _apply(TouxiAction(), _state(), "touxi", {"target": "npc.boar"})
        assert "player:hero" in touxi.entities["npc.boar"].killer

        ansuan_state = _state(
            monster=_monster(location="inn2"),
            extra_locations={"inn2": {"id": "inn2", "name": "后堂", "exits": []}},
        )
        ansuan = _apply(
            AnsuanAction(), ansuan_state, "ansuan", {"target": "npc.boar", "to": "inn2"}
        )
        assert "player:hero" in ansuan.entities["npc.boar"].killer
    finally:
        configure_killer_chase(old_k)
        configure_multi_combat(old_m)


def test_remove_all_enemy_keeps_killer():
    old_k = killer_chase_enabled()
    try:
        configure_killer_chase(True)
        state = _state()
        npc = state.entities["npc.boar"]
        npc.killer = ["player:hero"]
        npc.combat_enemies = ["player:hero"]
        npc.combat_target = "player:hero"
        npc.combat_tick = 5
        npc.combat_round = 3
        apply_effects(state, _reset(npc))
        assert npc.combat_enemies == []
        assert npc.combat_target == ""
        assert npc.killer == ["player:hero"]
    finally:
        configure_killer_chase(old_k)


def test_remove_enemy_refuses_while_killing():
    old_k, old_m = killer_chase_enabled(), multi_combat_enabled()
    try:
        state = _state()
        npc = state.entities["npc.boar"]
        npc.killer = ["player:hero"]
        npc.combat_enemies = ["player:hero", "player:other"]
        configure_killer_chase(True)
        configure_multi_combat(True)
        assert _remove_enemy(npc, "player:hero") == []
        configure_killer_chase(False)
        assert len(_remove_enemy(npc, "player:hero")) == 1
    finally:
        configure_killer_chase(old_k)
        configure_multi_combat(old_m)


def test_death_clears_killer_both_ways():
    old_k = killer_chase_enabled()
    try:
        configure_killer_chase(True)
        hero, monster = _hero(), _monster(hp=0)
        state = _state(hero=hero, monster=monster)
        hero.killer = ["npc.boar"]
        monster.killer = ["player:hero"]
        apply_effects(
            state,
            [
                Effect(
                    "entity_status",
                    {
                        "entity": "npc.boar",
                        "status": "dead",
                        "tick": 9,
                        "location": "inn",
                        "remove_all_killer": True,
                    },
                )
            ],
        )
        assert monster.killer == []
        assert hero.killer == []
    finally:
        configure_killer_chase(old_k)


def test_killer_chase_off_keeps_legacy_baseline(tmp_path):
    old = killer_chase_enabled()
    try:
        configure_killer_chase(False)
        summary = run_headless(
            ROOT,
            RunConfig(days=2, seed=42, npc_limit=0, out_root=tmp_path, run_id="legacy-killer-off"),
        )
        assert summary["events"]["total"] == LEGACY["events_total"]
        assert summary["hashes"]["state_final"] == LEGACY["state_final"]
        assert summary["hashes"]["event_final"] == LEGACY["event_final"]
        assert summary["economy"]["business_cash_now"] == LEGACY["business_cash"]
        assert summary["precondition_blocked"]["total"] == LEGACY["precondition_blocked"]
        assert summary["precondition_pruned"]["total"] == LEGACY["precondition_pruned"]
    finally:
        _restore(old)
