"""招式选招、既有系数与 force/martial 钩子接入战斗结算。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import wsim.sim.combat_rules as cr  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim.combat_rules import simulate_fight  # noqa: E402
from wsim.sim.vitals import _infer_race  # noqa: E402
from wsim.world.state import Entity  # noqa: E402

ACTION_TABLE = {
    "test-art": [
        {"index": 0, "name": "起手式", "text": "$N起手", "level": 0,
         "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 0}},
        {"index": 1, "name": "白虹贯日", "text": "$N一剑刺向$n的$l", "level": 10,
         "coeff": {"dodge": 10, "parry": 20, "force": 40, "damage": 50}},
    ],
    "mantian-xing": [
        {"index": 0, "name": "满天花雨", "text": "$N扬手打出满天暗器", "level": 0,
         "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 0}},
    ],
}


class ScriptedRandom:
    """按给定序列返回；用尽后返回下界（0），保证可复现。"""

    def __init__(self, values=None):
        self.values = list(values or [])

    def randint(self, a: int, b: int) -> int:
        if self.values:
            value = self.values.pop(0)
        else:
            value = a
        return max(int(a), min(int(b), int(value)))


def _hero(**over):
    data = dict(
        id="player:hero", name="英雄", role="wanderer", location="inn",
        max_hp=100, hp=100, attack=20, defense=5,
        attrs={"str": 30, "dex": 20}, combat_exp=10,
        skill_levels={"unarmed": 60},
    )
    data.update(over)
    return Entity(**data)


def _boar(**over):
    data = dict(
        id="npc.boar", name="野猪", role="monster", location="inn",
        max_hp=40, hp=40, attack=10, defense=2,
        attrs={"str": 20, "dex": 15}, combat_exp=5,
        skill_levels={"unarmed": 40},
    )
    data.update(over)
    return Entity(**data)


def _patched(monkeypatch, always_hit: bool = True):
    if always_hit:
        monkeypatch.setattr(cr.xf, "resolve_dodge", lambda **kw: False)
        monkeypatch.setattr(cr.xf, "resolve_parry", lambda **kw: False)


def _spy_damage(monkeypatch):
    """记录每次 resolve_damage 的入参（第一笔是发起方，之后可能是对手反击）。"""
    seen: list[dict] = []
    original = cr.xf.resolve_damage

    def spy(**kwargs):
        seen.append(dict(kwargs))
        return original(**kwargs)

    monkeypatch.setattr(cr.xf, "resolve_damage", spy)
    return seen


def test_no_map_and_no_race_keeps_old_flow():
    trace: list = []
    result = simulate_fight(
        _hero(), _boar(), PRNG(42, 2, "action:fight"),
        trace=trace, action_table=ACTION_TABLE, hook_table={},
    )
    first = result["rounds"][0]["actions"][0]
    assert first["selection"] == ""
    assert first["move"] == "" and first["move_text"] == ""
    assert first["action_skill"] == ""
    # 没有选招时不掷点：第一笔仍是闪避判定。
    assert trace[0][0] != 2


def test_skill_action_selected_first_and_reported(monkeypatch):
    seen = _spy_damage(monkeypatch)
    _patched(monkeypatch)
    hero = _hero(skill_levels={"unarmed": 60, "test-art": 25},
                 map_skills={"unarmed": "test-art"})
    trace: list = []
    result = simulate_fight(
        hero, _boar(), ScriptedRandom([1]), trace=trace,
        action_table=ACTION_TABLE, hook_table={},
    )
    assert trace[0] == (2, 1)  # query_action: seq=2 -> random(2)
    action = result["rounds"][0]["actions"][0]
    assert action["selection"] == "skill"
    assert action["action_skill"] == "test-art"
    assert action["move"] == "白虹贯日"
    assert action["move_text"] == "$N一剑刺向$n的$l"
    assert seen[0]["action_damage"] == 50
    assert seen[0]["action_force"] == 40


def test_missing_coefficient_keys_are_zero_and_report_is_json_safe(monkeypatch):
    seen = _spy_damage(monkeypatch)
    _patched(monkeypatch)
    table = {"test-art": [{"index": 0, "name": "残招", "text": "$N出手", "level": 0,
                           "coeff": {"dodge": 3}}]}
    hero = _hero(skill_levels={"unarmed": 60, "test-art": 20},
                 map_skills={"unarmed": "test-art"})
    result = simulate_fight(
        hero, _boar(), ScriptedRandom(), action_table=table, hook_table={},
    )
    assert seen[0]["action_damage"] == 0
    assert seen[0]["action_force"] == 0
    assert seen[0]["force_hit"] == 0
    assert seen[0]["martial_hit"] == 0
    action = result["rounds"][0]["actions"][0]
    assert action["move"] == "残招"
    assert isinstance(action["damage"], int)
    json.dumps(result, ensure_ascii=False)  # 不许出现 NaN / 不可序列化对象


def test_human_and_beast_default_actions():
    human = simulate_fight(
        _hero(race="人类"), _boar(), PRNG(42, 2, "action:fight"),
        action_table={}, hook_table={},
    )
    action = human["rounds"][0]["actions"][0]
    assert action["selection"] == "default"
    assert action["move"] == "" and action["move_text"].startswith("$N")

    beast = simulate_fight(
        _hero(race="走兽"), _boar(), PRNG(42, 2, "action:fight"),
        action_table={}, hook_table={},
    )
    action = beast["rounds"][0]["actions"][0]
    assert action["selection"] == "default"
    assert action["move_text"].startswith("$N")


def test_force_hit_positive_increment(monkeypatch):
    monkeypatch.setattr(cr.xf, "resolve_dodge", lambda **kw: False)
    monkeypatch.setattr(cr.xf, "resolve_parry", lambda **kw: False)
    seen = _spy_damage(monkeypatch)
    hero = _hero(
        skill_levels={"unarmed": 60, "force": 100},
        map_skills={"force": "hunyuan-yiqi"},
        neili=100, max_neili=100, jiali=20,
    )
    result = simulate_fight(
        hero, _boar(),
        ScriptedRandom(), action_table={},
        hook_table={"hunyuan-yiqi": {"force_hit": {"implemented": True, "value_kind": "code"}}},
    )
    # neili 100 -> 80：80//20 + 20 - 0 = 24；random(force)=0 < 24
    assert seen[0]["force_hit"] == 24
    action = result["rounds"][0]["actions"][0]
    assert action["force_hit"] == 24
    assert action["force_reflected"] == 0


def test_force_hit_reflection(monkeypatch):
    monkeypatch.setattr(cr.xf, "resolve_dodge", lambda **kw: False)
    monkeypatch.setattr(cr.xf, "resolve_parry", lambda **kw: False)
    seen = _spy_damage(monkeypatch)
    hero = _hero(
        skill_levels={"unarmed": 60, "force": 100},
        map_skills={"force": "hunyuan-yiqi"},
        neili=21, max_neili=100, jiali=20,
    )
    victim = _boar(
        skill_levels={"unarmed": 40, "force": 100}, neili=600,
    )
    result = simulate_fight(
        hero, victim, ScriptedRandom([99]), action_table={},
        hook_table={"hunyuan-yiqi": {"force_hit": {"implemented": True, "value_kind": "code"}}},
    )
    # neili 21 -> 1：1//20 + 20 - 600//25 = 0 + 20 - 24 = -4 -> 反震 8
    assert seen[0]["force_hit"] == 0
    action = result["rounds"][0]["actions"][0]
    assert action["force_reflected"] == 8
    assert result["hp"]["player:hero"] <= 92


def test_martial_hit_consumes_randoms_and_adds_nothing(monkeypatch):
    _patched(monkeypatch)
    seen = _spy_damage(monkeypatch)
    hero = _hero(
        skill_levels={"unarmed": 60, "mantian-xing": 60},
        map_skills={"unarmed": "mantian-xing"},
    )
    trace: list = []
    simulate_fight(
        hero, _boar(), ScriptedRandom([1, 99, 2]), trace=trace,
        action_table=ACTION_TABLE,
        hook_table={"mantian-xing": {"martial_hit": {"implemented": True, "value_kind": "code"}}},
    )
    assert seen[0]["martial_hit"] == 0
    assert (60, 59) in trace
    assert (2, 1) in trace


def test_same_seed_double_run_is_identical():
    hero = _hero(skill_levels={"unarmed": 60, "test-art": 25},
                 map_skills={"unarmed": "test-art"})
    first = simulate_fight(hero, _boar(), PRNG(42, 2, "action:fight"),
                           action_table=ACTION_TABLE, hook_table={})
    second = simulate_fight(hero, _boar(), PRNG(42, 2, "action:fight"),
                            action_table=ACTION_TABLE, hook_table={})
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == \
        json.dumps(second, sort_keys=True, ensure_ascii=False)


def test_race_inference_for_xkx_npcs():
    assert _infer_race({"id": "/d/hangzhou/npc/laohu", "gender": None}) == "走兽"
    assert _infer_race({"id": "/d/hangzhou/npc/xiaoer", "gender": "男性"}) == "人类"
    assert _infer_race({"id": "/d/hangzhou/npc/dushe", "gender": None}) == "爬虫"
