"""属性派生与技能成长的表驱动测试。

所有期望值均为**推导值**：按 `feature/attribute.lpc:9-99`、`feature/skill.lpc:172-204`
手算得出，不是真实 MUD 实测值。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from xkx_formula import (  # noqa: E402
    improve_skill,
    query_con,
    query_cor,
    query_cps,
    query_dex,
    query_int,
    query_kar,
    query_per,
    query_spi,
    query_sta,
    query_str,
)

DERIVED = "推导值（LPC 源码手算，非实测）"

ATTRIBUTE_CASES = [
    {
        "func": query_str,
        "args": dict(base_str=20, apply_strength=0, skills={}),
        "expect": 20,
    },
    {
        "func": query_str,
        "args": dict(base_str=20, apply_strength=5, skills={"unarmed": 150}),
        "expect": 40,
    },
    {
        "func": query_str,
        "args": dict(base_str=20, apply_strength=0, skills={"unarmed": 19, "leg": 200, "hand": 150}),
        "expect": 40,
    },
    {
        "func": query_str,
        "args": dict(base_str=0, apply_strength=-5, skills={}),
        "expect": -5,
    },
    {
        "func": query_str,
        "args": dict(base_str=20, apply_strength=0, skills={"unarmed": 5, "leg": 9}),
        "expect": 20,
    },
    {
        "func": query_int,
        "args": dict(base_int=10, apply_intelligence=3, skills={"literate": 95}),
        "expect": 22,
    },
    {
        "func": query_con,
        "args": dict(base_con=10, apply_constitution=0, skills={"force": 87}),
        "expect": 18,
    },
    {
        "func": query_dex,
        "args": dict(base_dex=10, apply_dexerity=2, skills={"dodge": 123}),
        "expect": 24,
    },
    {
        "func": query_sta,
        "args": dict(base_sta=10, apply_stamina=0, skills={"parry": 77}),
        "expect": 17,
    },
    {
        "func": query_spi,
        "args": dict(
            base_spi=10, apply_spirit=2,
            skills={"buddhism": 250, "taoism": 300, "poison": 0},
        ),
        "expect": 42,
    },
    {
        "func": query_spi,
        "args": dict(base_spi=10, apply_spirit=2, skills={}),
        "expect": 12,
    },
    {
        "func": query_per,
        "args": dict(base_per=30, apply_personality=4),
        "expect": 34,
    },
    {
        "func": query_cps,
        "args": dict(base_cps=10, apply_composure=0),
        "expect": 10,
    },
    {
        "func": query_cor,
        "args": dict(base_cor=15, apply_courage=2),
        "expect": 17,
    },
    {
        "func": query_kar,
        "args": dict(base_kar=20, apply_karma=3),
        "expect": 23,
    },
]
for _case in ATTRIBUTE_CASES:
    _case["basis"] = DERIVED


@pytest.mark.parametrize(
    "case", ATTRIBUTE_CASES, ids=[f"{c['func'].__name__}-{c['expect']}" for c in ATTRIBUTE_CASES]
)
def test_attribute_table(case: dict) -> None:
    assert case["basis"] == DERIVED
    assert case["func"](**case["args"]) == case["expect"]


IMPROVE_CASES = [
    {
        "args": dict(
            skill_level=10, learned=0, amount=5, learned_skill_count=3, spi=20, is_player=True
        ),
        "expect": (10, 5, 5, False, True),
    },
    {
        "args": dict(
            skill_level=10, learned=120, amount=5, learned_skill_count=3, spi=20, is_player=True
        ),
        "expect": (11, 0, 5, True, True),
    },
    {
        # 121 == (10+1)^2，严格大于才升级
        "args": dict(
            skill_level=10, learned=120, amount=1, learned_skill_count=3, spi=20, is_player=True
        ),
        "expect": (10, 121, 1, False, True),
    },
    {
        "args": dict(
            skill_level=10, learned=0, amount=5, learned_skill_count=10, spi=5, is_player=True
        ),
        "expect": (10, 1, 1, False, True),
    },
    {
        "args": dict(
            skill_level=10, learned=0, amount=1, learned_skill_count=10, spi=5, is_player=True
        ),
        "expect": (10, 1, 1, False, True),
    },
    {
        "args": dict(
            skill_level=10, learned=0, amount=5, learned_skill_count=3, spi=3, is_player=True
        ),
        "expect": (10, 5, 5, False, True),
    },
    {
        "args": dict(
            skill_level=10, learned=0, amount=5, learned_skill_count=3, spi=1, is_player=True
        ),
        "expect": (10, 2, 2, False, True),
    },
    {
        "args": dict(
            skill_level=10, learned=20, amount=5, learned_skill_count=3, spi=20,
            is_player=False, curiousness=False,
        ),
        "expect": (10, 20, 0, False, False),
    },
    {
        "args": dict(
            skill_level=10, learned=120, amount=5, learned_skill_count=3, spi=20,
            is_player=False, curiousness=True, weak_mode=False,
        ),
        "expect": (11, 0, 5, True, True),
    },
    {
        # 弱模式 NPC：加熟练度但不升级
        "args": dict(
            skill_level=10, learned=120, amount=5, learned_skill_count=3, spi=20,
            is_player=False, curiousness=True, weak_mode=True,
        ),
        "expect": (10, 125, 5, False, True),
    },
    {
        # 弱模式玩家：userp 为真，仍可升级
        "args": dict(
            skill_level=10, learned=120, amount=5, learned_skill_count=3, spi=20,
            is_player=True, weak_mode=True,
        ),
        "expect": (11, 0, 5, True, True),
    },
]
for _case in IMPROVE_CASES:
    _case["basis"] = DERIVED


@pytest.mark.parametrize("case", IMPROVE_CASES, ids=lambda c: str(c["expect"]))
def test_improve_skill_table(case: dict) -> None:
    assert case["basis"] == DERIVED
    result = improve_skill(**case["args"])
    assert (
        result.skill_level,
        result.learned,
        result.amount,
        result.leveled_up,
        result.accepted,
    ) == case["expect"]
