"""命中参数映射锁：身份(role)不是武功，ap/dp 必须取源码指定的技能等级。

背景：真实 xkx 世界曾把玩家的 `skills={"镖师":0.35}`、NPC 的
`skills={"wanderer":0.41}` 当作攻击技能，导致创世纪默认的 `unarmed=40`
被角色身份等级遮蔽；`combatd.lpc:268-274` 的规则是
“有武器取武器技能，否则取 prepare，再否则 `unarmed`”。
本文件只锁参数映射，不改 `tools/xkx_formula` 的公式。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import xkx_formula as xf  # noqa: E402

from wsim.sim import combat_rules as cr  # noqa: E402
from wsim.world.state import Entity  # noqa: E402


def _player() -> Entity:
    """与 player_enter 默认值一致：三项武功 40、exp 250；旧身份技能同时存在。"""
    return Entity(
        id="player:hero",
        name="测试新号",
        role="镖师",
        location="inn",
        attrs={"str": 20, "con": 20, "dex": 18, "int": 15, "spi": 15, "sta": 20},
        skills={"镖师": 0.35},
        skill_levels={"unarmed": 40, "dodge": 40, "parry": 40},
        combat_exp=250,
        hp=100,
        max_hp=100,
    )


def test_role_is_never_used_as_attack_skill():
    player = _player()
    assert cr.skill_map(player)["镖师"] == 35  # 身份仍留在旧技能表，但不再参与战斗
    assert cr.attack_skill(player) == "unarmed"


def test_attack_power_uses_unarmed_40_not_role_35():
    player = _player()
    levels = cr.skill_map(player)
    str_effective = xf.query_str(20, 0, levels)
    unarmed_ap = xf.skill_power(
        skill_level=40, apply_bonus=0, combat_exp=250,
        attr_effective=str_effective, attr_base=20,
        usage=xf.SKILL_USAGE_ATTACK,
    )
    role_ap = xf.skill_power(
        skill_level=35, apply_bonus=0, combat_exp=250,
        attr_effective=str_effective, attr_base=20,
        usage=xf.SKILL_USAGE_ATTACK,
    )
    got = cr._attack_power(cr._Actor(player, set()), cr.attack_skill(player))
    assert got == unarmed_ap
    assert got > role_ap


def test_defense_power_uses_dodge_parry_unarmed_levels():
    player = _player()
    actor = cr._Actor(player, set())
    levels = cr.skill_map(player)
    dex_effective = xf.query_dex(18, 0, levels)
    for skill in ("dodge", "parry", "unarmed"):
        expected = xf.skill_power(
            skill_level=40, apply_bonus=0, combat_exp=250,
            attr_effective=dex_effective, attr_base=18,
            usage=xf.SKILL_USAGE_DEFENSE,
            dodge_power_point_permille=None,
        )
        assert cr._defense_power(actor, skill) == expected, skill


def test_npc_without_skills_uses_exp_half_and_missing_attrs_default_10():
    tiger = Entity(
        id="npc.tiger", name="老虎", role="wanderer", location="inn",
        skills={"wanderer": 0.41}, combat_exp=30000, hp=200, max_hp=200,
    )
    assert cr.attack_skill(tiger) == "unarmed"
    # level=0 走 `combat_exp / 2`，dex 不参与（源码 combatd.lpc:224）。
    assert cr._attack_power(cr._Actor(tiger, set()), "unarmed") == 15000
    assert cr._defense_power(cr._Actor(tiger, set()), "dodge") == 15000
    # 内容包没有 attrs 时用确定性默认 10，不是 20。
    assert cr._base(tiger, "dex") == 10


def test_attrs_from_content_are_loaded():
    poorman = Entity(
        id="npc.poorman", name="穷汉", role="wanderer", location="inn",
        attrs={"con": 17, "dex": 18, "int": 13, "str": 20},
        skills={"wanderer": 0.41}, combat_exp=250, hp=200, max_hp=200,
    )
    assert cr._base(poorman, "dex") == 18
    assert cr.attr(poorman, "dex") == 18
