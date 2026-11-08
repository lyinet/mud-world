"""招式序列（`query_action`）与技能钩子（`hit_ob`）的只读内容索引。

数据来源（内容线已抽出，本模块只读不改）：
- `content/xkx/skills_actions.json`：每技能的普通招式 `actions[]`；
- `content/xkx/skills_stats.json`：每技能的 `hooks`（force_hit / martial_hit / ...）。

语义对齐原版：
- 选招是具体技能 daemon 的 `query_action()`（`bixie-jian.lpc:86-111` 等）：
  先按技能等级求 `seq`，再 `seq = random(seq)`，返回 `action[seq]`；
  `seq == 0` 时 `random(0) == 0`，仍取 `action[0]`。
- 系数读取按 LPC mapping 语义：缺键 / `coefficient_source == "default"` 都是 0。

本模块不掷点、不读墙钟；随机由调用方传入的 `RandomSource`（`prng.below(n)`）提供。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTENT_DIR = _REPO_ROOT / "content" / "xkx"

COEFF_KEYS = ("dodge", "parry", "force", "damage")

# 无映射技能时的种族默认招式（`adm/daemons/race/*.lpc` 的 `combat_action`）。
# 这些不是“武功招式名”，是原版 `query_action()` 的兜底文案与系数：
# - human.lpc：`combat_action` 5 条，无 dodge/parry/force/damage 系数；
# - beast.lpc：`combat_action` 3 条，`damage` 分别 50 / 30 / 30。
# 内容包没有 `race` 字段，`resolve_npc_vitals` 用 id / 性别做最小推断（见 §风险）。
_DEFAULT_ACTIONS: dict[str, list[dict[str, Any]]] = {
    "人类": [
        {"index": 0, "name": "", "text": "$N挥拳攻击$n的$l", "level": 0, "coeff": {k: 0 for k in COEFF_KEYS}},
        {"index": 1, "name": "", "text": "$N往$n的$l一抓", "level": 0, "coeff": {k: 0 for k in COEFF_KEYS}},
        {"index": 2, "name": "", "text": "$N往$n的$l狠狠地踢了一脚", "level": 0, "coeff": {k: 0 for k in COEFF_KEYS}},
        {"index": 3, "name": "", "text": "$N提起拳头往$n的$l捶去", "level": 0, "coeff": {k: 0 for k in COEFF_KEYS}},
        {"index": 4, "name": "", "text": "$N对准$n的$l用力挥出一拳", "level": 0, "coeff": {k: 0 for k in COEFF_KEYS}},
    ],
    "走兽": [
        {"index": 0, "name": "", "text": "$N扑上来张嘴往$n的$l狠狠地一咬", "level": 0,
         "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 50}},
        {"index": 1, "name": "", "text": "$N举起爪子往$n的$l抓了过去", "level": 0,
         "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 30}},
        {"index": 2, "name": "", "text": "$N跃起来用前掌往$n的$l猛地一拍", "level": 0,
         "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 30}},
    ],
}


def _as_int(value: Any, default: int = 0) -> int:
    """整数化；None / bool / 非法值都落到 default（LPC mapping 缺键按 0）。"""
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _rows(data: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get(key) or []
    return [row for row in (data or []) if isinstance(row, dict)]


def _normalize_action(raw: dict[str, Any]) -> dict[str, Any]:
    coeffs = raw.get("coefficients") or {}
    return {
        "index": _as_int(raw.get("index")),
        "name": str(raw.get("name") or ""),
        "text": str(raw.get("text") or ""),
        "level": _as_int(raw.get("skill_level_required")),
        "coeff": {key: _as_int(coeffs.get(key)) for key in COEFF_KEYS},
    }


def _read_action_table(path: Path) -> dict[str, list[dict[str, Any]]]:
    rows = _rows(_read_json(path), "skills_actions")
    table: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        skill = str(row.get("id") or "")
        if not skill:
            continue
        actions = [
            _normalize_action(raw)
            for raw in (row.get("actions") or [])
            if isinstance(raw, dict)
        ]
        if actions:
            table[skill] = actions
    return table


def _read_hook_table(path: Path) -> dict[str, dict[str, Any]]:
    rows = _rows(_read_json(path), "skills_stats")
    table: dict[str, dict[str, Any]] = {}
    for row in rows:
        skill = str(row.get("id") or "")
        hooks = row.get("hooks")
        if skill and isinstance(hooks, dict):
            table[skill] = {str(k): dict(v) for k, v in hooks.items() if isinstance(v, dict)}
    return table


@lru_cache(maxsize=8)
def _cached_actions(path_str: str) -> dict[str, list[dict[str, Any]]]:
    return _read_action_table(Path(path_str))


@lru_cache(maxsize=8)
def _cached_hooks(path_str: str) -> dict[str, dict[str, Any]]:
    return _read_hook_table(Path(path_str))


def _path(content_dir: Path | str | None, filename: str) -> Path:
    base = Path(content_dir) if content_dir is not None else DEFAULT_CONTENT_DIR
    return base / filename


def load_action_table(content_dir: Path | str | None = None) -> dict[str, list[dict[str, Any]]]:
    """`skill_id -> actions[]`；文件缺失时返回空表（战报不受影响）。"""
    return _cached_actions(str(_path(content_dir, "skills_actions.json")))


def load_hook_table(content_dir: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """`skill_id -> hooks{}`；文件缺失时返回空表。"""
    return _cached_hooks(str(_path(content_dir, "skills_stats.json")))


def clear_cache() -> None:
    """测试用：清掉内容索引缓存。"""
    _cached_actions.cache_clear()
    _cached_hooks.cache_clear()


def mapped_skill(entity: Any, base_skill: str) -> str:
    """原版 `query_skill_mapped(type)`：基础技能 → 具体武功；没有就返回空串。"""
    mapping = getattr(entity, "map_skills", None) or {}
    value = mapping.get(str(base_skill)) if isinstance(mapping, dict) else None
    return str(value) if value else ""


def select_action(actions: list[dict[str, Any]] | None, level: int, prng: Any) -> dict[str, Any] | None:
    """按 `query_action()` 语义选一条招式；空表返回 None 且不掷点。

    ```
    seq = 0
    for (i = ttl; i > 0; i--) if (lvl > action[i-1]["lvl"]) { seq = i; break; }
    seq = random(seq); return action[seq];
    ```
    `seq == 0` 时原版 `random(0) == 0`，仍取 `action[0]`；本实现用
    `prng.below(0)` 记录一笔 `(0, 0)`，与库对 non-positive 上界的约定一致。
    """
    if not actions:
        return None
    ttl = len(actions)
    seq = 0
    for i in range(ttl, 0, -1):
        if int(level) > int(actions[i - 1].get("level", 0)):
            seq = i
            break
    picked = prng.below(seq) if seq > 0 else prng.below(0)
    if picked < 0 or picked >= ttl:  # 防御性：原版不会越界，内容异常时也不崩
        picked = 0
    return actions[picked]


def coefficient(action: dict[str, Any] | None, key: str) -> int:
    """动作系数；缺键按 LPC mapping 语义取 0。"""
    if not action:
        return 0
    return _as_int((action.get("coeff") or {}).get(key))


def default_actions_for(race: str) -> list[dict[str, Any]]:
    """种族默认招式；未知种族返回空表（不掷点、无文案）。"""
    return _DEFAULT_ACTIONS.get(str(race or ""), [])


def select_default_action(race: str, prng: Any) -> dict[str, Any] | None:
    """原版种族 `query_action()`：`combat_action[random(sizeof(combat_action))]`。"""
    actions = default_actions_for(race)
    if not actions:
        return None
    return actions[prng.below(len(actions))]


# `inherit/weapon/*.lpc` 的 `set("verbs", ...)`：武器类型 → 可选动词。
# 键与内容包 `equip_slot` 一致；`weapon.lpc` 是基类，未知类型回落到它的动词表。
_WEAPON_VERBS: dict[str, tuple[str, ...]] = {
    "axe": ("chop", "slash", "slice", "hack"),
    "blade": ("slash", "slice", "hack"),
    "club": ("bash", "crush", "slam"),
    "dagger": ("slice", "pierce", "thrust"),
    "fork": ("impale", "pierce", "thrust"),
    "hammer": ("bash", "crush", "slam"),
    "hook": ("chop", "hack"),
    "mace": ("chop", "bash", "crush", "slam"),
    "rake": ("chop", "slash", "bash", "hack"),
    "spear": ("bash", "thrust", "pierce", "impale", "crush"),
    "staff": ("bash", "crush", "slam"),
    "stick": ("bash", "crush", "slam"),
    "sword": ("slash", "slice", "thrust"),
    "throwing": ("throw",),
    "whip": ("whip",),
}
_WEAPON_FALLBACK_VERBS: tuple[str, ...] = ("chop", "slash", "slice", "hack")

# `adm/daemons/weapond.lpc:7-100` 的 `weapon_actions` 原文与系数。
# 只保留引擎会读到的 dodge/parry/damage；`post_action`（兵器破坏等）未建模。
_WEAPON_ACTIONS: dict[str, dict[str, Any]] = {
    "slash": {
        "text": "$N挥动$w，斩向$n的$l", "damage_type": "割伤",
        "coeff": {"dodge": 0, "parry": 20, "force": 0, "damage": 0},
    },
    "slice": {
        "text": "$N用$w往$n的$l砍去", "damage_type": "劈伤",
        "coeff": {"dodge": 20, "parry": 0, "force": 0, "damage": 0},
    },
    "chop": {
        "text": "$N的$w朝著$n的$l劈将过去", "damage_type": "劈伤",
        "coeff": {"dodge": 0, "parry": -20, "force": 0, "damage": 0},
    },
    "hack": {
        "text": "$N挥舞$w，对准$n的$l一阵乱砍", "damage_type": "劈伤",
        "coeff": {"dodge": 30, "parry": 0, "force": 0, "damage": 30},
    },
    "thrust": {
        "text": "$N用$w往$n的$l刺去", "damage_type": "刺伤",
        "coeff": {"dodge": 15, "parry": -15, "force": 0, "damage": 0},
    },
    "pierce": {
        "text": "$N的$w往$n的$l狠狠地一捅", "damage_type": "刺伤",
        "coeff": {"dodge": -30, "parry": -30, "force": 0, "damage": 0},
    },
    "whip": {
        "text": "$N将$w一扬，往$n的$l抽去", "damage_type": "鞭伤",
        "coeff": {"dodge": -20, "parry": 30, "force": 0, "damage": 0},
    },
    "impale": {
        "text": "$N用$w往$n的$l直戳过去", "damage_type": "刺伤",
        "coeff": {"dodge": -10, "parry": -10, "force": 0, "damage": 0},
    },
    "strike": {
        "text": "$N一个大舒臂抡起$w，对着$n的$l往下一砸", "damage_type": "筑伤",
        "coeff": {"dodge": -10, "parry": -10, "force": 0, "damage": 0},
    },
    "bash": {
        "text": "$N挥舞$w，往$n的$l用力一砸", "damage_type": "挫伤",
        "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 0},
    },
    "crush": {
        "text": "$N高高举起$w，往$n的$l当头砸下", "damage_type": "挫伤",
        "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 0},
    },
    "slam": {
        "text": "$N手握$w，眼露凶光，猛地对准$n的$l挥了过去", "damage_type": "挫伤",
        "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 0},
    },
    "throw": {
        "text": "$N将$w对准$n的$l射了过去", "damage_type": "刺伤",
        "coeff": {"dodge": 0, "parry": 0, "force": 0, "damage": 0},
    },
}


def select_weapon_action(weapon_type: str, prng: Any) -> dict[str, Any] | None:
    """原版 `weapond.lpc:92-100 query_action()`：按武器 verbs 随机一条。

    仅在 `query_skill_mapped(type)` 为空时由 `feature/attack.lpc:214-224`
    回落到武器对象；`weapon_type` 为空（没有武器类型）时返回 None 且不掷点。
    """
    kind = str(weapon_type or "")
    if not kind:
        return None
    verbs = _WEAPON_VERBS.get(kind, _WEAPON_FALLBACK_VERBS)
    verb = verbs[prng.below(len(verbs))]
    row = _WEAPON_ACTIONS[verb]
    return {
        "index": verbs.index(verb),
        "name": "",
        "text": str(row["text"]),
        "level": 0,
        "coeff": dict(row["coeff"]),
    }


def has_code_hook(hooks: dict[str, Any] | None, name: str) -> bool:
    """钩子是否是“源码存在函数体”的 code 类型（静态无常量值）。"""
    row = (hooks or {}).get(str(name)) if isinstance(hooks, dict) else None
    if not isinstance(row, dict):
        return False
    return bool(row.get("implemented")) and row.get("value_kind") == "code"
