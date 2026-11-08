"""`touxi` / `ansuan`：原版命令内直接 `do_attack()` 的引擎复刻。

原版坐标：
- `cmds/std/touxi.c:47-58`：同房间；`random(combat_exp) < exp/2` 时一刀，否则三刀；
  之后 `obj->kill_ob(me)`。
- `cmds/std/ansuan.c:143-210`：隔一个出口；活人 >2 不干；先一刀，`random(combat_exp)`
  `> exp/2` 再补一刀，否则对方反击一刀；`random(combat_exp) < exp/5` 被发现，
  被发现就留在目标房间，否则退回原处。
- 两者都在 `kill_ob()` 之前出手，所以破防门的 `me->is_killing(victim)` 为 false。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import wsim.sim.aggression as aggression_mod  # noqa: E402
import wsim.sim.combat_rules as cr  # noqa: E402
from wsim.actions.combat import AnsuanAction, TouxiAction  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim.combat_rules import simulate_fight  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {
    "inn": {"id": "inn", "name": "客栈", "exits": ["forest"]},
    "forest": {"id": "forest", "name": "树林", "exits": ["inn"]},
}
HERO = "player:hero"
MON = "npc.wolf"


class _MaxRNG:
    """所有 `randint` 取上界：让分支条件确定成立 / 不成立。"""

    def randint(self, a: int, b: int) -> int:
        return b


def _hero(**over) -> dict:
    data = {
        "id": HERO,
        "name": "英雄",
        "role": "wanderer",
        "location": "inn",
        "max_hp": 100,
        "hp": 100,
        "attack": 60,
        "defense": 0,
        "attrs": {"str": 30, "dex": 20, "con": 20},
        "skills": {"dodge": 0.0, "parry": 0.0},
        "combat_exp": 0,
        "skill_levels": {"unarmed": 120, "dodge": 0, "parry": 0},
    }
    data.update(over)
    return data


def _monster(**over) -> dict:
    data = {
        "id": MON,
        "name": "狼狗",
        "role": "monster",
        "location": "inn",
        "race": "走兽",
        "max_hp": 200,
        "hp": 200,
        "attack": 5,
        "defense": 0,
        "attrs": {"str": 20, "dex": 15, "con": 20},
        "skills": {"dodge": 0.0, "parry": 0.0},
        "combat_exp": 0,
        "skill_levels": {"unarmed": 0, "dodge": 0, "parry": 0},
    }
    data.update(over)
    return data


def _state(hero: dict | None = None, monster: dict | None = None) -> WorldState:
    state = WorldState(locations=LOCATIONS)
    apply_effects(
        state,
        [
            Effect("create_entity", hero or _hero()),
            Effect("create_entity", monster or _monster()),
        ],
    )
    return state


def _open(state: WorldState, mode: str, rng: Any, **kw):
    return simulate_fight(
        state.entities[HERO],
        state.entities[MON],
        rng,
        eff_qi=True,
        killing=True,
        opening={"mode": mode},
        **kw,
    )


def _actors(actions: list[dict[str, Any]]) -> list[str]:
    return [str(row["actor"]) for row in actions]


# ---------------------------------------------------------------- 偷袭分支
def test_touxi_low_exp_single_strike_bypasses_initiative_gate():
    # hero.exp=0 -> random(0)=0 < 200000/2，走“一刀”分支。
    state = _state(monster=_monster(combat_exp=200000))
    result = _open(state, "touxi", _MaxRNG(), initiative_gate=True)

    actions = result["rounds"][0]["actions"]
    assert result["rounds_used"] == 1
    assert _actors(actions) == [HERO]
    assert actions[0]["attack_type"] == "regular"
    # 偷袭不经过 `fight()` 的出手门：没有 gate_* 审计字段。
    assert not any(key.startswith("gate_") for key in actions[0])
    assert result["opening"] == {
        "mode": "touxi", "branch": "single", "detected": None,
    }
    assert result["hp_raw"][MON] < 200


def test_touxi_high_exp_triple_strike():
    # 目标 exp=0 -> 0/2=0，`random(hero.exp) < 0` 恒假，走“三刀”分支。
    state = _state(monster=_monster(combat_exp=0))
    result = _open(
        state, "touxi", _MaxRNG(),
        initiative_gate=True,
    )
    actions = result["rounds"][0]["actions"]
    assert result["opening"]["branch"] == "triple"
    assert _actors(actions) == [HERO, HERO, HERO]


# ---------------------------------------------------------------- 暗算分支
def test_ansuan_low_exp_counterattack_and_detected():
    # hero.exp=0：第二刀条件 0 > 100000/2 不成立 -> 对方反击；
    # 发现条件 0 < 100000/5 成立 -> 被发现。
    state = _state(monster=_monster(combat_exp=100000))
    result = _open(state, "ansuan", PRNG(42, 7, "action:ansuan"), initiative_gate=True)

    actions = result["rounds"][0]["actions"]
    assert result["opening"]["branch"] == "counter"
    assert result["opening"]["detected"] is True
    assert _actors(actions) == [HERO, MON]


def test_ansuan_no_detection_when_target_exp_zero():
    state = _state(monster=_monster(combat_exp=0))
    result = _open(state, "ansuan", PRNG(42, 7, "action:ansuan"), initiative_gate=True)
    assert result["opening"]["detected"] is False


def test_ansuan_high_exp_gets_second_strike():
    # hero.exp=1000、目标 exp=0：MaxRNG 让 `random(1000)=999 > 0`，连出两刀；
    # 发现判定 999 < 0 为假 -> 未被发现。
    state = _state(hero=_hero(combat_exp=1000), monster=_monster(combat_exp=0))
    result = simulate_fight(
        state.entities[HERO],
        state.entities[MON],
        _MaxRNG(),
        eff_qi=True,
        killing=True,
        opening={"mode": "ansuan"},
        initiative_gate=True,
    )
    actions = result["rounds"][0]["actions"]
    assert result["opening"]["branch"] == "double"
    assert result["opening"]["detected"] is False
    assert _actors(actions) == [HERO, HERO]


# ---------------------------------------------------------------- 破防门
def test_opening_strikes_are_not_killing_for_wound_gate(monkeypatch):
    seen: list[bool] = []

    def _wound(**kw):
        seen.append(bool(kw["killing"]))
        return False

    monkeypatch.setattr(cr.xf, "roll_wound", _wound)
    state = _state(hero=_hero(), monster=_monster(combat_exp=200000))
    simulate_fight(
        state.entities[HERO],
        state.entities[MON],
        _MaxRNG(),
        eff_qi=True,
        killing=True,
        opening={"mode": "touxi"},
    )
    assert seen, "偷袭必须命中并进入破防判定"
    assert all(flag is False for flag in seen)


# ---------------------------------------------------------------- 动作层
def _writer(hero: dict, monster: dict, action, seed: int = 42):
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    registry = ActionRegistry()
    registry.register(action)
    writer = Writer(state, store, registry, seed)
    writer.bootstrap(
        [Effect("create_entity", hero), Effect("create_entity", monster)], tick=0, verb="genesis"
    )
    return writer


def _submit(writer, verb: str, args: dict, tick: int = 0):
    return writer.submit(
        Intent(actor=HERO, verb=verb, args=args, tick=tick, source="player")
    )


def _log(event) -> dict:
    return [e for e in event.effects if e["op"] == "combat_log"][0]["data"]


@pytest.fixture()
def multi_on(monkeypatch):
    monkeypatch.setattr(aggression_mod, "_MULTI_COMBAT_ENABLED", True)


def _forest_monster(**over) -> dict:
    return _monster(location="forest", name="老虎", max_hp=100000, hp=100000, **over)


def test_touxi_action_registers_hostile_npc(multi_on):
    writer = _writer(
        _hero(),
        _monster(combat_exp=0, max_hp=100000, hp=100000),
        TouxiAction(),
    )
    event = _submit(writer, "touxi", {"target": MON})

    assert event.outcome == "ok", event
    assert HERO in writer.state.entities[MON].combat_enemies
    assert writer.state.entities[MON].combat_target == HERO
    assert writer.state.entities[MON].hp < 100000


def test_ansuan_detected_moves_player_into_target_room(multi_on):
    writer = _writer(_hero(), _forest_monster(combat_exp=100000), AnsuanAction())
    event = _submit(writer, "ansuan", {"target": MON, "to": "forest"})

    assert event.outcome == "ok", event
    log = _log(event)
    assert log["opening"]["detected"] is True
    assert writer.state.entities[HERO].location == "forest"
    assert HERO in writer.state.entities[MON].combat_enemies


def test_ansuan_undetected_human_target_stays_put_and_calm(multi_on):
    monster = _forest_monster(race="人类", combat_exp=0)
    writer = _writer(_hero(), monster, AnsuanAction())
    event = _submit(writer, "ansuan", {"target": MON, "to": "forest"})

    assert event.outcome == "ok", event
    log = _log(event)
    assert log["opening"]["detected"] is False
    assert writer.state.entities[HERO].location == "inn"
    assert HERO not in writer.state.entities[MON].combat_enemies


def test_ansuan_undetected_beast_still_hostile_by_accept_ansuan(multi_on):
    # `npc.c accept_ansuan()`：不会说话的目标在出手前就 `kill_ob(who)`。
    writer = _writer(_hero(), _forest_monster(race="走兽", combat_exp=0), AnsuanAction())
    event = _submit(writer, "ansuan", {"target": MON, "to": "forest"})

    assert event.outcome == "ok", event
    assert _log(event)["opening"]["detected"] is False
    assert writer.state.entities[HERO].location == "inn"
    assert HERO in writer.state.entities[MON].combat_enemies


def test_ansuan_rejects_same_room_target():
    writer = _writer(_hero(), _monster(combat_exp=0), AnsuanAction())
    event = _submit(writer, "ansuan", {"target": MON, "to": "inn"})

    assert event.outcome == "rejected"
    assert event.reason == "rejected:not_adjacent"


def test_ansuan_rejects_room_with_too_many_living():
    extra1 = _monster(id="npc.extra1", name="路人甲", location="forest", combat_exp=0)
    extra2 = _monster(id="npc.extra2", name="路人乙", location="forest", combat_exp=0)
    writer = _writer(_hero(), _forest_monster(combat_exp=0), AnsuanAction())
    writer.bootstrap(
        [Effect("create_entity", extra1), Effect("create_entity", extra2)],
        tick=0, verb="genesis",
    )
    event = _submit(writer, "ansuan", {"target": MON, "to": "forest"})

    assert event.outcome == "rejected"
    assert event.reason == "rejected:too_many_people"
