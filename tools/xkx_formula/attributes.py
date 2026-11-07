"""人物属性派生公式。

来源：`feature/attribute.lpc:9-99`（xkx100 @ c1b87db）。
全部为纯函数：属性基数、`apply/*` 临时加成、原始技能等级由调用方传入。
LPC 的 `query_skill(skill, 1)` 是“原始等级”，不是 `query_skill(skill)` 的有效值，
因此统一用 `skills` 映射里的整数等级。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .lpc_math import lpc_div

_MELEE_IMPROVERS = ("leg", "strike", "finger", "cuff", "hand", "claw")
_SPI_IMPROVERS = (
    "buddhism",
    "mahayana",
    "lamaism",
    "taoism",
    "ziyunyin",
    "zhengqijue",
    "poison",
)


def _level(skills: Mapping[str, Any], name: str) -> int:
    """LPC 映射缺键等价于 0；显式用 int() 锁住整数语义。"""
    return int(skills.get(name, 0))


def _max_improve(
    skills: Mapping[str, Any], names: tuple[str, ...], start: str
) -> int:
    """复刻 LPC 的“逐项比较、阈值随 improve 变化”的写法。

    `if (query_skill(n) >= improve * 10) improve = query_skill(n) / 10;`
    由于每次赋值只会让 improve 不减，最终结果等于这些技能等级最大值 / 10（向零截断）。
    这里仍按源码顺序逐项计算，保留边界语义。
    """
    improve = lpc_div(_level(skills, start), 10)
    if not any(_level(skills, name) for name in names):
        return improve
    for name in names:
        level = _level(skills, name)
        if level >= improve * 10:
            improve = lpc_div(level, 10)
    return improve


def query_str(base_str: int, apply_strength: int, skills: Mapping[str, Any]) -> int:
    """膂力：`base + apply/strength + max(unarmed, leg, strike, finger, cuff, hand, claw)/10`。"""
    improve = _max_improve(skills, _MELEE_IMPROVERS, "unarmed")
    return int(base_str) + int(apply_strength) + improve


def query_int(base_int: int, apply_intelligence: int, skills: Mapping[str, Any]) -> int:
    """悟性：`base + apply/intelligence + literate/10`。"""
    return int(base_int) + int(apply_intelligence) + lpc_div(_level(skills, "literate"), 10)


def query_con(base_con: int, apply_constitution: int, skills: Mapping[str, Any]) -> int:
    """根骨：`base + apply/constitution + force/10`。"""
    return int(base_con) + int(apply_constitution) + lpc_div(_level(skills, "force"), 10)


def query_dex(base_dex: int, apply_dexerity: int, skills: Mapping[str, Any]) -> int:
    """身法：`base + apply/dexerity + dodge/10`。"""
    return int(base_dex) + int(apply_dexerity) + lpc_div(_level(skills, "dodge"), 10)


def query_sta(base_sta: int, apply_stamina: int, skills: Mapping[str, Any]) -> int:
    """耐力：`base + apply/stamina + parry/10`。"""
    return int(base_sta) + int(apply_stamina) + lpc_div(_level(skills, "parry"), 10)


def query_spi(base_spi: int, apply_spirit: int, skills: Mapping[str, Any]) -> int:
    """灵性：`base + apply/spirit + max(buddhism, mahayana, ..., poison)/10`。

    源码 `feature/attribute.lpc:62` 的括号写法 `query_skill("poison"), 1)` 是笔误；
    按语义应为“任一知识技能非零则进入比较”。对可达状态两者输出相同（全零时逐项比较
    只会把 improve 设为 0），因此这里按 OR-of-nonzero 等价实现。
    """
    improve = _max_improve(skills, _SPI_IMPROVERS, "buddhism")
    return int(base_spi) + int(apply_spirit) + improve


def query_kar(base_kar: int, apply_karma: int) -> int:
    """福缘：`base + apply/karma`。"""
    return int(base_kar) + int(apply_karma)


def query_per(base_per: int, apply_personality: int) -> int:
    """容貌：`base + apply/personality`。"""
    return int(base_per) + int(apply_personality)


def query_cps(base_cps: int, apply_composure: int) -> int:
    """定力：`base + apply/composure`。"""
    return int(base_cps) + int(apply_composure)


def query_cor(base_cor: int, apply_courage: int) -> int:
    """胆识：`base + apply/courage`。"""
    return int(base_cor) + int(apply_courage)
