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

import hashlib
from typing import Any

# 原版 `race/beast.lpc:62-67` 的六维键；内容线只对这些键输出 [lo, hi]。
ROLLABLE_ATTRS = ("str", "con", "dex", "int", "spi", "sta", "kar", "cps", "cor")

# 我们的默认（非原版）：内容包缺 max_qi 时按最高技能等级给可解释的气血上限。
# 500：一流好手（技能 ≥ 80，对应原版技能 100）；
# 300：有功夫在身但未到一流（0 < 技能 < 80）；
# 200：无战斗技能（野兽 / 杂役）。
DEFAULT_MAX_HP_TIERS: tuple[tuple[int, int], ...] = ((80, 500), (1, 300), (0, 200))

# 内容包没有原版 `set("race", ...)` 字段；按 NPC id 末段/性别做最小推断。
# 只给战斗默认招式用，不参与任何数值公式。未列出的非人类按“走兽”处理。
_RACE_BY_ID = {
    "laohu": "走兽",
    "wolfdog": "走兽",
    "camel": "走兽",
    "horse": "走兽",
    "zaohongma": "走兽",
    "huangbiaoma": "走兽",
    "ziliuma": "走兽",
    "dushe": "爬虫",
    "xiezi": "爬虫",
    "maque": "飞禽",
    "wuya": "飞禽",
    "fish": "鱼类",
    "hai": "人类",
}
_HUMAN_GENDERS = {"男性", "女性", "无性"}


def _infer_race(npc: dict[str, Any]) -> str:
    raw = str(npc.get("race") or "").strip()
    if raw:
        return raw
    tail = str(npc.get("id") or "").rsplit("/", 1)[-1]
    if tail in _RACE_BY_ID:
        return _RACE_BY_ID[tail]
    if str(npc.get("gender") or "").strip() in _HUMAN_GENDERS:
        return "人类"
    return "走兽"


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


def roll_attrs(
    roll_key: str,
    attrs_roll: Any,
    seed: int = 0,
    errors: list[str] | None = None,
) -> dict[str, int]:
    """按世界种子掷出 NPC 六维；SHA-256 保证跨进程/跨机器可复现。

    公式：`lo + int(sha256("xkx-attrs:<seed>:<roll_key>:<attr>")[:8], "big") % span`。
    `roll_key` 是**实体身份**而不是模板 id：规范实体用 npc id，clone 用实例 id，
    重生用 `"<entity>#<death_tick>"`。原版每个 clone 在 `new()` 时现掷
    （`race/human.lpc:60-68`），键里带实例身份才能复现“每个实例独立”。
    不读墙钟、不用全局 random、不依赖 NPC 遍历顺序。
    """
    if not isinstance(attrs_roll, dict):
        return {}
    rolled: dict[str, int] = {}
    for key in sorted(attrs_roll):
        if key not in ROLLABLE_ATTRS:
            continue
        span = attrs_roll[key]
        if (
            not isinstance(span, (list, tuple))
            or len(span) != 2
            or isinstance(span[0], bool)
            or isinstance(span[1], bool)
            or not isinstance(span[0], int)
            or not isinstance(span[1], int)
            or span[0] > span[1]
        ):
            if errors is not None:
                errors.append(f"bad_attrs_roll:{roll_key}:{key}")
            continue
        lo, hi = int(span[0]), int(span[1])
        digest = hashlib.sha256(f"xkx-attrs:{int(seed)}:{roll_key}:{key}".encode("utf-8")).digest()
        rolled[key] = lo + int.from_bytes(digest[:8], "big") % (hi - lo + 1)
    return rolled


def _apply_attrs_derive(
    npc_id: str,
    rules: Any,
    attrs: dict[str, int],
    errors: list[str] | None,
) -> None:
    """按内容包 `attrs_derive` 计算派生属性（原版 human.lpc:63 的 int）。

    规则只支持 `kind=difference`：`attr = total - sum(minus)`。
    显式值 / 掷点值优先，不覆盖；任一被减项缺失时不派生，并记诊断。
    """
    if not isinstance(rules, dict):
        return
    for attr, rule in rules.items():
        attr = str(attr)
        if attr in attrs:
            continue
        if attr not in ROLLABLE_ATTRS or not isinstance(rule, dict):
            if errors is not None:
                errors.append(f"bad_attrs_derive:{npc_id}:{attr}")
            continue
        minus = [str(k) for k in (rule.get("minus") or [])]
        if rule.get("kind") != "difference" or not minus:
            if errors is not None:
                errors.append(f"bad_attrs_derive:{npc_id}:{attr}")
            continue
        if any(k not in attrs for k in minus):
            if errors is not None:
                errors.append(f"incomplete_attrs_derive:{npc_id}:{attr}")
            continue
        attrs[attr] = _int_or(rule.get("total")) - sum(attrs[k] for k in minus)


def resolve_npc_vitals(
    npc: dict[str, Any],
    *,
    seed: int = 0,
    errors: list[str] | None = None,
    roll_key: str | None = None,
) -> dict[str, Any] | None:
    """返回可直接并入 `create_entity` 的字段；legacy NPC 返回 None。

    `roll_key` 省略时回退为 npc id（规范实体/旧调用方行为不变）。
    """
    if not any(k in npc for k in ("vitals", "attrs", "skills", "attrs_roll", "attrs_derive")):
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
    key = str(roll_key) if roll_key is not None else str(npc.get("id") or "")
    rolled = roll_attrs(key, npc.get("attrs_roll"), seed, errors)
    attrs.update(rolled)
    _apply_attrs_derive(str(npc.get("id") or ""), npc.get("attrs_derive"), attrs, errors)
    skills = {
        str(k): max(0, min(800, _int_or(v)))
        for k, v in (npc.get("skills") or {}).items()
    }
    map_skills = {
        str(k): str(v)
        for k, v in (npc.get("map_skills") or {}).items()
        if str(v)
    }
    prepare_skills = [str(x) for x in (npc.get("prepare_skills") or []) if str(x)]
    race = _infer_race(npc)
    resolved: dict[str, Any] = {
        "max_hp": max_hp,
        "hp": max_hp,
        "attrs": attrs,
        "combat_exp": max(0, _int_or(vitals.get("combat_exp"))),
        "skill_levels": skills,
    }
    if race:
        resolved["race"] = race
    # 内功发力与映射技能：原版 `vitals{neili,jiali}` / `query_skill_mapped`。
    # 为 0 / 空时不落键，保持旧内容实体与旧世界哈希不变。
    for key in ("neili", "max_neili", "jiali"):
        value = max(0, _int_or(vitals.get(key)))
        if value:
            resolved[key] = value
    if map_skills:
        resolved["map_skills"] = map_skills
    if prepare_skills:
        resolved["prepare_skills"] = prepare_skills
    return resolved
