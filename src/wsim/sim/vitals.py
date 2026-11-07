"""NPC 战斗数值解析：内容包 `vitals/attrs/skills` → `create_entity` 战斗字段。

来源必须分清：
- `max_hp` 优先取 **原版** `vitals.max_qi`（xkx 里气血上限就是 max_qi）。
- 内容包缺 `max_qi` 时用**我们的默认**三档（见 `DEFAULT_MAX_HP_TIERS`），
  判据只看内容包自带的技能等级，不掷点、不看墙钟。
- `attrs / combat_exp / skill_levels` 原样带内容包值，不做二次换算。

门控：只有 NPC dict 带 `vitals/attrs/skills` 任一键时才解析。旧 `content/`
（30 个 legacy NPC）三个键都没有，`create_entity` 不会新增任何战斗字段，
旧世界哈希逐字节不变。
"""
from __future__ import annotations

from typing import Any

# 我们的默认（非原版）：内容包缺 max_qi 时按最高技能等级给可解释的气血上限。
# 500：一流好手（技能 ≥ 80，对应原版技能 100）；
# 300：有功夫在身但未到一流（0 < 技能 < 80）；
# 200：无战斗技能（野兽 / 杂役）。
DEFAULT_MAX_HP_TIERS: tuple[tuple[int, int], ...] = ((80, 500), (1, 300), (0, 200))


def _int_or(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _skill_top(npc: dict[str, Any]) -> int:
    levels = [_int_or(v) for v in (npc.get("skills") or {}).values()]
    return max(levels) if levels else 0


def default_max_hp(npc: dict[str, Any]) -> int:
    """内容包缺 `max_qi` 时的默认气血上限（我们的默认，不是原版数值）。"""
    top = _skill_top(npc)
    for threshold, value in DEFAULT_MAX_HP_TIERS:
        if top >= threshold:
            return value
    return DEFAULT_MAX_HP_TIERS[-1][1]


def resolve_npc_vitals(npc: dict[str, Any]) -> dict[str, Any] | None:
    """返回可直接并入 `create_entity` 的字段；legacy NPC 返回 None。"""
    if not any(k in npc for k in ("vitals", "attrs", "skills")):
        return None
    vitals = dict(npc.get("vitals") or {})
    max_qi = _int_or(vitals.get("max_qi"))
    max_hp = max_qi if max_qi > 0 else default_max_hp(npc)
    # 结构上不可能出现 0 血：缺内容时最差也落到 200。
    max_hp = max(1, max_hp)
    attrs = {
        str(k): _int_or(v)
        for k, v in (npc.get("attrs") or {}).items()
        if not isinstance(v, bool) and isinstance(v, (int, float))
    }
    skills = {
        str(k): max(0, min(800, _int_or(v)))
        for k, v in (npc.get("skills") or {}).items()
    }
    return {
        "max_hp": max_hp,
        "hp": max_hp,
        "attrs": attrs,
        "combat_exp": max(0, _int_or(vitals.get("combat_exp"))),
        "skill_levels": skills,
    }
