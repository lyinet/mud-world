"""fight / kill 分离：切磋半血即止、击杀仍致死、开关只由世界配置控制。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.combat import FightAction, KillAction  # noqa: E402
from wsim.actions.registry import ActionRegistry, build_registry  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}


def _hero(**over) -> dict:
    data = {
        "id": "player:hero",
        "name": "英雄",
        "role": "wanderer",
        "location": "inn",
        "max_hp": 100,
        "hp": 100,
        "attack": 20,
        "defense": 5,
        "attrs": {"str": 30, "dex": 20},
        "skills": {"dodge": 0.4},
        "combat_exp": 10,
        "skill_levels": {"unarmed": 60},
    }
    data.update(over)
    return data


def _monster(**over) -> dict:
    data = {
        "id": "npc.boar",
        "name": "野猪",
        "role": "monster",
        "location": "inn",
        "max_hp": 40,
        "hp": 40,
        "attack": 10,
        "defense": 2,
        "attrs": {"str": 20, "dex": 15},
        "skills": {"dodge": 0.2},
        "combat_exp": 5,
        "skill_levels": {"unarmed": 40},
    }
    data.update(over)
    return data


def _writer(hero: dict, monster: dict, action, seed: int = 42):
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    registry = ActionRegistry()
    registry.register(action)
    writer = Writer(state, store, registry, seed)
    writer.bootstrap(
        [Effect("create_entity", hero), Effect("create_entity", monster)], tick=0, verb="genesis"
    )
    return writer, store


def _submit(writer, verb: str, args: dict, tick: int = 0):
    return writer.submit(
        Intent(actor="player:hero", verb=verb, args=args, tick=tick, source="player")
    )


def _log(event) -> dict:
    return [e for e in event.effects if e["op"] == "combat_log"][0]["data"]


def _ops(event) -> list[str]:
    return [e["op"] for e in event.effects]


def test_spar_stops_at_half_hp_without_death_or_loot():
    hero = _hero()
    training = _monster(
        id="npc.training", max_hp=300, hp=300, attack=1, defense=0,
        attrs={"str": 10, "dex": 10}, skills={"dodge": 0.0},
        skill_levels={"unarmed": 10}, combat_exp=500,
    )
    writer, _ = _writer(hero, training, FightAction(spar=True))
    event = _submit(writer, "fight", {"target": "npc.training"})

    assert event.outcome == "ok"
    assert "entity_status" not in _ops(event)
    assert "loot_create" not in _ops(event)

    log = _log(event)
    assert log["stopped"] is True
    assert log["lethal"] is False
    assert log["winner"] == "attacker"

    target = writer.state.entities["npc.training"]
    assert target.status == "alive"
    assert target.hp * 2 <= target.max_hp
    assert any(
        e["op"] == "combat_exp_add" and e["data"]["delta"] > 0 for e in event.effects
    )


def test_fight_against_killer_npc_stays_lethal():
    hero = _hero(
        max_hp=10, hp=10, attack=1, defense=0,
        attrs={"str": 10, "dex": 10}, skills={"dodge": 0.0},
        combat_exp=0, skill_levels={},
    )
    killer = _monster(
        id="npc.killer", max_hp=500, hp=500, attack=80, defense=10,
        attrs={"str": 60, "dex": 30}, skills={"dodge": 0.0},
        combat_exp=100, skill_levels={"unarmed": 120},
        combat_target="player:hero",
    )
    writer, _ = _writer(hero, killer, FightAction(spar=True))
    # create_entity 不写交战状态；模拟 NPC 已被对手 kill_ob 登记为敌人。
    writer.state.entities["npc.killer"].combat_target = "player:hero"
    event = _submit(writer, "fight", {"target": "npc.killer"})

    assert event.outcome == "ok"
    assert "entity_status" in _ops(event)
    assert writer.state.entities["player:hero"].status == "dead"
    log = _log(event)
    assert log["lethal"] is True
    assert log["stopped"] is False


def test_kill_kills_and_drops_loot_with_kill_reason():
    hero = _hero()
    monster = _monster()
    writer, _ = _writer(hero, monster, KillAction())
    event = _submit(
        writer, "kill", {"target": "npc.boar", "loot": {"item.boar_hide": 1}}
    )

    assert event.outcome == "ok"
    assert writer.state.entities["npc.boar"].status == "dead"
    assert writer.state.ground["inn"]["item.boar_hide"] == 1
    drops = [e["data"] for e in event.effects if e["op"] == "loot_create"]
    assert drops and all(row["reason"] == "kill" for row in drops)
    log = _log(event)
    assert log["lethal"] is True
    assert log["stopped"] is False


def test_flag_off_registers_no_kill_and_fight_stays_lethal(content):
    reg = build_registry(content, 42, lethal_fight=False)
    assert reg.get("fight") is not None
    assert reg.get("fight").spar is False
    assert reg.get("kill") is None

    writer, _ = _writer(_hero(), _monster(), reg.get("fight"))
    event = _submit(writer, "fight", {"target": "npc.boar"})
    assert event.outcome == "ok"
    assert writer.state.entities["npc.boar"].status == "dead"


def test_flag_on_makes_fight_spar_and_registers_kill(content):
    reg = build_registry(content, 42, lethal_fight=True)
    fight = reg.get("fight")
    kill = reg.get("kill")
    assert fight is not None and fight.spar is True
    assert kill is not None and kill.spar is False
    assert kill.verb == "kill"


def test_same_seed_double_run_is_identical():
    def run(action, verb: str):
        monster = _monster(
            id="npc.training", max_hp=300, hp=300, attack=1, defense=0,
            attrs={"str": 10, "dex": 10}, skills={"dodge": 0.0},
            skill_levels={"unarmed": 10}, combat_exp=500,
        )
        writer, _ = _writer(_hero(), monster, action)
        event = _submit(writer, verb, {"target": "npc.training"})
        return event.to_canonical_dict(), state_hash(writer.state)

    assert run(FightAction(spar=True), "fight") == run(FightAction(spar=True), "fight")
    assert run(KillAction(), "kill") == run(KillAction(), "kill")
