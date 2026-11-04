"""玩家成长线与“今天能做什么”面板（P4）。

只读视图，不改世界状态：
- 三条成长线：声望（社会身份）、技能（手艺熟练度）、资产（钱与产业）。
- 每条 5 级，给出当前值、下一级条件与解锁内容。
- `today` 汇总当前地点马上能做的事：可接委托、可学技能、可拜访 NPC、可买产业。
最小实现：纯函数 + 现有 state/content，不引入新框架。
"""
from __future__ import annotations

from typing import Any

from ..actions.common import (
    first_business_at,
    reputation_points,
    skill_for,
    treasury_guard_active,
    wallet,
)
from ..world.state import WorldState

REPUTATION_LEVELS = [
    {"level": 1, "name": "无名之辈", "at": 0.0, "unlock": "初到此地，先接委托混个脸熟"},
    {"level": 2, "name": "街坊熟人", "at": 3.0, "unlock": "可接护送类委托，街坊愿意搭话"},
    {"level": 3, "name": "小有名气", "at": 15.0, "unlock": "行会引荐：可进出门派与商会内堂"},
    {"level": 4, "name": "一方人物", "at": 40.0, "unlock": "门派客卿：可收徒、可代掌门派事务"},
    {"level": 5, "name": "城中翘楚", "at": 90.0, "unlock": "话事人：城中大事可拍板"},
]

SKILL_LEVELS = [
    {"level": 1, "name": "生手", "at": 0.0, "unlock": "跟着师傅打下手"},
    {"level": 2, "name": "手熟", "at": 0.42, "unlock": "可独立开摊（手艺够撑起门面）"},
    {"level": 3, "name": "熟练", "at": 0.45, "unlock": "可带徒；高级委托愿意找你"},
    {"level": 4, "name": "精通", "at": 0.55, "unlock": "可接高级活，工钱上浮"},
    {"level": 5, "name": "宗师", "at": 0.70, "unlock": "开馆授业：可自设招牌与收徒"},
]

ASSET_LEVELS = [
    {"level": 1, "name": "囊中羞涩", "at": 0.0, "unlock": "先做工攒本钱"},
    {"level": 2, "name": "小有积蓄", "at": 600.0, "unlock": "可开摊（本钱 300 文）"},
    {"level": 3, "name": "有产者", "at": 900.0, "unlock": "可买铺面（本钱 1200 文）"},
    {"level": 4, "name": "富户", "at": 1500.0, "unlock": "可雇伙计、扩摊"},
    {"level": 5, "name": "产业主", "at": 3000.0, "unlock": "可开分号、置产"},
]

BUSINESS_VALUE = {"stall": 300, "storefront": 1200, "branch": 3000}


def _level(levels: list[dict], value: float) -> dict[str, Any]:
    idx = 0
    for i, row in enumerate(levels):
        if value >= float(row["at"]):
            idx = i
    cur = levels[idx]
    nxt = levels[idx + 1] if idx + 1 < len(levels) else None
    return {
        "level": int(cur["level"]),
        "name": str(cur["name"]),
        "value": round(float(value), 3),
        "at": float(cur["at"]),
        "next_at": float(nxt["at"]) if nxt else None,
        "next_name": str(nxt["name"]) if nxt else "",
        "next_unlock": str(nxt["unlock"]) if nxt else "",
        "progress": round((value - float(cur["at"])) / (float(nxt["at"]) - float(cur["at"])), 3)
        if nxt and float(nxt["at"]) > float(cur["at"])
        else 1.0,
    }


def asset_value(state: WorldState, pid: str, content) -> float:
    ent = state.entities.get(pid)
    value = float(state.account_balance(wallet(pid)))
    if ent is not None and ent.business:
        biz = state.businesses.get(ent.business)
        if biz is not None:
            value += float(BUSINESS_VALUE.get(biz.kind, 0))
    return value


def growth_view(state: WorldState, content, pid: str) -> dict[str, Any]:
    ent = state.entities.get(pid)
    skills = {k: round(float(v), 4) for k, v in (ent.skills.items() if ent else [])}
    main_skill = skill_for(content, ent.role) if ent else ""
    skill_value = float(skills.get(main_skill, 0.0)) if main_skill else max(skills.values(), default=0.0)
    return {
        "reputation": _level(REPUTATION_LEVELS, reputation_points(state, pid)),
        "skill": {
            **_level(SKILL_LEVELS, skill_value),
            "skill": main_skill,
            "skills": skills,
        },
        "assets": {**_level(ASSET_LEVELS, asset_value(state, pid, content)), "money": int(state.account_balance(wallet(pid)))},
    }


def _hours_to_next(value: float, next_at: float | None) -> int | None:
    if next_at is None:
        return None
    per_hour = 0.0025
    return max(1, int((float(next_at) - value + per_hour - 1e-9) / per_hour))


def today_view(
    state: WorldState,
    content,
    pid: str,
    *,
    location: str,
    quest_board: dict[str, Any],
    nearby: list[dict[str, Any]],
) -> dict[str, Any]:
    ent = state.entities[pid]
    skills = growth_view(state, content, pid)["skill"]
    wallet_bal = int(state.account_balance(wallet(pid)))

    quests = [
        {
            "id": row["id"],
            "name": row["name"],
            "giver_name": row["giver_name"],
            "reward_money": int(row["reward_money"]),
        }
        for row in quest_board.get("rows", [])
        if row.get("acceptable")
    ][:3]

    skill_rows = []
    if skills.get("skill"):
        next_at = skills.get("next_at")
        skill_rows.append(
            {
                "skill": skills["skill"],
                "current": float(skills["value"]),
                "next_at": next_at,
                "next_level": skills.get("level", 0) + 1 if next_at is not None else None,
                "hours_to_next": _hours_to_next(float(skills["value"]), next_at),
            }
        )

    npc_rows = [
        {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "hint": "可打听委托" if row.get("role") else "可闲聊",
        }
        for row in nearby
    ][:3]

    opening = content.economy.get("shop_opening") or {}
    guard = treasury_guard_active(state, content)
    has_landlord = first_business_at(state, location) is not None
    business_rows = []
    if has_landlord and ent.business is None:
        for kind in ("stall", "storefront", "branch"):
            cost = int(opening.get(kind, 0))
            if cost <= 0:
                continue
            business_rows.append(
                {
                    "shop_type": kind,
                    "cost": cost,
                    "affordable": wallet_bal >= cost,
                    "blocked": "treasury_guard" if guard else "",
                }
            )

    if quests:
        next_action = f"先接委托：{quests[0]['name']}"
    elif business_rows and business_rows[0]["affordable"]:
        next_action = f"可开摊：本钱 {business_rows[0]['cost']} 文"
    elif skills.get("hours_to_next"):
        next_action = f"练手艺：{skills['hours_to_next']} 小时到下一级"
    else:
        next_action = "先做工攒钱"

    return {
        "tick": int(state.tick),
        "location": location,
        "location_name": "",
        "next_action": next_action,
        "quests": quests,
        "skills": skill_rows,
        "npcs": npc_rows,
        "business": business_rows,
    }
