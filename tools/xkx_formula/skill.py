"""技能成长公式。

来源：`feature/skill.lpc:172-204`（xkx100 @ c1b87db）。
纯函数：不查对象、不调用 `SKILL_D(...)->skill_improved`，只返回新等级/熟练度。
"""
from __future__ import annotations

from dataclasses import dataclass

from .lpc_math import lpc_div


@dataclass(frozen=True)
class SkillProgress:
    """一次 `improve_skill` 的结果。"""

    skill_level: int
    learned: int
    amount: int
    leveled_up: bool
    accepted: bool


def improve_skill(
    *,
    skill_level: int,
    learned: int,
    amount: int,
    learned_skill_count: int,
    spi: int,
    is_player: bool,
    curiousness: bool = False,
    weak_mode: bool = False,
) -> SkillProgress:
    """`feature/skill.lpc:172-204`。

    参数：
    - `skill_level`：当前技能等级（`skills[skill]`，缺省按 0）。
    - `learned`：当前熟练度（`learned[skill]`，缺省按 0）。
    - `amount`：本次增量；可为 0（源码会归一为 1）。
    - `learned_skill_count`：`sizeof(learned)`，已学技能条目数。
    - `spi`：`query_spi()`。
    - `is_player`：`userp(this_object())`。
    - `curiousness`：NPC 的 `query("curiousness")`。
    - `weak_mode`：`weak_mode` 参数。

    返回 `accepted=False` 表示源码直接 return（非玩家且不好学）。
    升级条件是严格大于 `(level + 1)^2`；等于不升级。
    """
    if not is_player and not curiousness:
        return SkillProgress(
            skill_level=int(skill_level),
            learned=int(learned),
            amount=0,
            leveled_up=False,
            accepted=False,
        )

    skill_level = int(skill_level)
    learned = int(learned)
    amount = int(amount)

    if learned_skill_count > spi:
        amount = lpc_div(amount, learned_skill_count - spi)
    if amount == 0:
        amount = 1

    learned += amount
    leveled_up = False
    if (not weak_mode or is_player) and learned > (skill_level + 1) * (skill_level + 1):
        skill_level += 1
        learned = 0
        leveled_up = True

    return SkillProgress(
        skill_level=skill_level,
        learned=learned,
        amount=amount,
        leveled_up=leveled_up,
        accepted=True,
    )
