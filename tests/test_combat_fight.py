"""P2 `fight`：原子结算、整数战报、PRNG 取数顺序锁与回放等价。

关键断言：
- 一次 fight 只追加 1 条事件，事件里带 rounds[]；
- 同 seed + 同 seq + salt="action:fight" 双跑逐字节一致；
- 取数顺序（dodge→parry→damage→armor→str→exp→wound）写死，
  顺序一旦改变此测试立即失败；
- 从事件日志重放 Effect 得到的投影与实跑状态哈希一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.combat import FightAction  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim.combat_rules import simulate_fight  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import Entity, WorldState  # noqa: E402
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


def _writer(hero: dict, monster: dict, seed: int = 42):
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    registry = ActionRegistry()
    registry.register(FightAction())
    writer = Writer(state, store, registry, seed)
    writer.bootstrap(
        [Effect("create_entity", hero), Effect("create_entity", monster)], tick=0, verb="genesis"
    )
    return writer, store


def _fight(writer, args: dict, tick: int = 0):
    return writer.submit(
        Intent(actor="player:hero", verb="fight", args=args, tick=tick, source="player")
    )


def _log(event) -> dict:
    return [e for e in event.effects if e["op"] == "combat_log"][0]["data"]


def test_fight_writes_one_event_with_rounds_and_kills_target():
    writer, store = _writer(_hero(), _monster())
    event = _fight(writer, {"target": "npc.boar", "loot": {"item.boar_hide": 1}})

    assert store.count() == 2  # genesis + fight
    assert event.verb == "fight" and event.outcome == "ok"
    ops = [e["op"] for e in event.effects]
    assert "hp_add" in ops and "entity_status" in ops and "loot_create" in ops
    assert "combat_log" in ops

    log = _log(event)
    assert log["winner"] == "attacker"
    assert log["rounds_used"] <= 20
    assert len(log["rounds"]) == log["rounds_used"]
    # 战报全部整数：不允许出现浮点尾差
    for rnd in log["rounds"]:
        for action in rnd["actions"]:
            assert isinstance(action["damage"], int)
            assert isinstance(action["hp_before"], int) and isinstance(action["hp_after"], int)

    boar = writer.state.entities["npc.boar"]
    assert (boar.status, boar.hp) == ("dead", 0)
    assert writer.state.entities["player:hero"].inventory["item.boar_hide"] == 1

    # 死亡字段同样来自 Effect，不是动作里直接写 state
    dead = [e for e in event.effects if e["op"] == "entity_status"][0]["data"]
    assert dead["entity"] == "npc.boar" and dead["status"] == "dead" and dead["tick"] == 0


def test_fight_loss_kills_the_player_and_still_writes_one_event():
    writer, store = _writer(
        _hero(max_hp=10, hp=10, attack=1, defense=0, attrs={"str": 10, "dex": 10},
              combat_exp=0, skill_levels={}, skills={"dodge": 0.0}),
        _monster(max_hp=200, hp=200, attack=80, defense=10,
                 attrs={"str": 60, "dex": 30}, combat_exp=100,
                 skill_levels={"unarmed": 120, "dodge": 100}),
    )
    event = _fight(writer, {"target": "npc.boar"})
    assert store.count() == 2
    log = _log(event)
    assert log["winner"] == "defender"
    hero = writer.state.entities["player:hero"]
    assert (hero.status, hero.hp) == ("dead", 0)
    assert "entity_status" in [e["op"] for e in event.effects]


def test_round_cap_is_hard_twenty():
    writer, store = _writer(_hero(), _monster())
    event = _fight(writer, {"target": "npc.boar", "max_rounds": 99})
    log = _log(event)
    assert log["max_rounds"] == 20
    assert log["rounds_used"] <= 20


def test_prng_draw_order_is_locked():
    """固定属性 + seed=42 + seq=2 的取数序列必须逐项不变。"""
    hero = Entity(id="player:hero", name="英雄", role="wanderer", location="inn",
                  attrs={"str": 30, "dex": 20}, skills={"dodge": 0.4}, combat_exp=10,
                  skill_levels={"unarmed": 60}, max_hp=100, hp=100, attack=20, defense=5)
    boar = Entity(id="npc.boar", name="野猪", role="monster", location="inn",
                  attrs={"str": 20, "dex": 15}, skills={"dodge": 0.2}, combat_exp=5,
                  skill_levels={"unarmed": 40}, max_hp=40, hp=40, attack=10, defense=2)
    trace: list = []
    simulate_fight(hero, boar, PRNG(42, 2, "action:fight"), trace=trace)
    expected_labels = [
        "dodge", "parry", "damage_roll", "armor_roll", "str_bonus", "exp_check", "wound",
        "dodge",
        "dodge", "parry", "damage_roll", "armor_roll", "str_bonus", "exp_check", "wound",
    ]
    assert [label for label, _ in trace] == expected_labels
    assert trace[0] == ("dodge", 27450)
    assert len(trace) == 15


def test_same_seed_same_action_is_byte_identical():
    def run():
        writer, store = _writer(_hero(), _monster())
        event = _fight(writer, {"target": "npc.boar", "loot": {"item.boar_hide": 1}})
        return event.to_canonical_dict(), state_hash(writer.state), store.event_hash()

    first = run()
    second = run()
    assert first == second


def test_replay_from_event_log_equals_live_state():
    writer, store = _writer(_hero(), _monster())
    _fight(writer, {"target": "npc.boar", "loot": {"item.boar_hide": 1}})

    replay = WorldState(locations=LOCATIONS)
    for event in store.read_all():
        apply_effects(replay, [Effect.from_dict(e) for e in event.effects])
        replay.tick = event.tick
    assert state_hash(replay) == state_hash(writer.state)
