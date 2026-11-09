"""只读观察视图：看房间 / 看角色，但不改变世界。

背景：文字的 `look` 走 `POST /api/xkx/command`，命令入口会 `_ensure_player`
自动重新进入世界，于是离线账号“看一眼”就被拉上线并触发主动怪。管理台与验收
需要的是真正的旁路观察：**不写事件、不改 presence/last_seen_tick、不推进 tick、
不触发 npc_aggression、不进战斗**。

本模块只做纯投影：所有字段都从 `WorldState` 快照读取（`session._read_snapshot()`），
不调用 `perform` / `xkx_command` / `_touch_tick`，也不持有写锁。
"""
from __future__ import annotations

import re
from typing import Any

from ..actions.player import ACCOUNT_RE

# 与文字壳 `look` 共用同一套方位中文名，避免两处对不上。
from .xkx_shell import DIRECTION_LABELS

OBSERVE_PROFILE_VERSION = 1

# 房间可见状态：死亡/重生中的实体也如实列出，状态字段自己说话。
_PRESENT_STATUSES = frozenset({"alive", "unconcious", "dead"})
_MAX_PRESENT = 200


def _error(code: str, tick: int, text: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "profile_version": OBSERVE_PROFILE_VERSION,
        "readonly": True,
        "code": code,
        "reject_reason": code,
        "reason_text": text,
        "tick": int(tick),
    }
    payload.update(extra)
    return payload


def _item_name(content: Any, item: str) -> str:
    row = (getattr(content, "items", None) or {}).get(item) or {}
    return str(row.get("name") or item)


def _clean_text(raw: Any) -> str:
    """xkx 的 desc 是 LPC 源码折行；中文直接拼接，避免句中残留断行。"""
    return re.sub(r"\s*\n\s*", "", str(raw or "")).strip()


def _room_view(content: Any, state: Any, loc_id: str) -> dict[str, Any]:
    row = (state.locations or {}).get(loc_id) or {}
    exits = []
    for raw in row.get("exits_detail") or []:
        if not isinstance(raw, dict):
            continue
        direction = str(raw.get("dir") or "")
        exits.append(
            {
                "direction": direction,
                "direction_label": DIRECTION_LABELS.get(direction, "") if direction else "",
                "id": str(raw.get("to") or ""),
                "name": str(raw.get("name") or raw.get("to") or ""),
                "available": bool(raw.get("available", raw.get("traversable", True))),
                "reason": str(raw.get("reason") or ""),
            }
        )
    if not exits:
        # 旧内容没有 exits_detail 时退回 exits 列表，至少不丢出口。
        for nxt in sorted(str(x) for x in (row.get("exits") or [])):
            exits.append(
                {
                    "direction": "",
                    "direction_label": "",
                    "id": nxt,
                    "name": str(
                        ((state.locations or {}).get(nxt) or {}).get("name") or nxt
                    ),
                    "available": nxt in (state.locations or {}),
                    "reason": "",
                }
            )
    present = []
    players = getattr(state, "players", {}) or {}
    for ent in sorted((state.entities or {}).values(), key=lambda e: str(e.id)):
        if str(ent.location) != loc_id:
            continue
        if str(ent.status) not in _PRESENT_STATUSES:
            continue
        is_player = str(ent.id).startswith("player:")
        prof = players.get(str(ent.id))
        present.append(
            {
                "entity_id": str(ent.id),
                "name": str(ent.name),
                "role": str(ent.role),
                "status": str(ent.status),
                "is_player": is_player,
                "online": bool(getattr(prof, "online", False)) if prof is not None else None,
            }
        )
        if len(present) >= _MAX_PRESENT:
            break
    ground = [
        {"item": str(item), "name": _item_name(content, str(item)), "qty": int(qty)}
        for item, qty in sorted((getattr(state, "ground", {}) or {}).get(loc_id, {}).items())
        if int(qty) > 0
    ]
    return {
        "id": loc_id,
        "name": str(row.get("name") or loc_id),
        "description": _clean_text(row.get("description")),
        "region": row.get("region"),
        "region_name": row.get("region_name"),
        "exits": exits,
        "present": present,
        "ground": ground,
    }


def _resolved_eff(raw: int, maximum: int) -> tuple[int, str]:
    """`eff_hp` / `eff_jing` 共用的解析口径：-2=旧实体未初始化，-1=濒死。"""
    if raw >= 0:
        return raw, "value"
    if raw == -1:
        # 原版 eff_qi/eff_jing 下限 -1 = 伤势到濒死，仍按真实值参与判定。
        return 0, "dying"
    return maximum, "unset"


def _character_view(content: Any, state: Any, ent: Any) -> dict[str, Any]:
    raw_eff = int(getattr(ent, "eff_hp", -2))
    eff, eff_state = _resolved_eff(raw_eff, int(getattr(ent, "max_hp", 0) or 0))
    # 精神组与气血组同构（`feat/xkx-jing`）；`max_jing==0` 表示未启用/旧档。
    raw_eff_jing = int(getattr(ent, "eff_jing", -2))
    eff_jing, eff_jing_state = _resolved_eff(
        raw_eff_jing, int(getattr(ent, "max_jing", 0) or 0)
    )
    inventory = [
        {"item": str(item), "name": _item_name(content, str(item)), "qty": int(qty)}
        for item, qty in sorted((getattr(ent, "inventory", None) or {}).items())
        if int(qty) > 0
    ]
    equipped = [
        {"slot": str(slot), "item": str(item), "name": _item_name(content, str(item))}
        for slot, item in sorted((getattr(ent, "equipped", None) or {}).items())
        if str(item)
    ]
    is_player = str(ent.id).startswith("player:")
    prof = (getattr(state, "players", {}) or {}).get(str(ent.id))
    loc_id = str(ent.location)
    return {
        "entity_id": str(ent.id),
        "account_id": str(ent.id)[len("player:"):] if is_player else "",
        "name": str(ent.name),
        "role": str(ent.role),
        "is_player": is_player,
        "location": loc_id,
        "location_name": str(
            ((state.locations or {}).get(loc_id) or {}).get("name") or loc_id
        ),
        "status": str(ent.status),
        # 玩家档案字段只对玩家有意义；NPC 返回 null。
        "presence": str(getattr(prof, "presence", "")) if prof is not None else None,
        "online": bool(getattr(prof, "online", False)) if prof is not None else None,
        "hp": int(getattr(ent, "hp", 0) or 0),
        "max_hp": int(getattr(ent, "max_hp", 0) or 0),
        # eff_hp / eff_jing 原样给出，另给解析值：-2=旧实体未初始化（按 max 计），
        # -1=濒死（effective 记 0）；两者共用 `_resolved_eff` 口径。
        "eff_hp": raw_eff,
        "eff_hp_effective": eff,
        "eff_hp_state": eff_state,
        "jing": int(getattr(ent, "jing", 0) or 0),
        "max_jing": int(getattr(ent, "max_jing", 0) or 0),
        "eff_jing": raw_eff_jing,
        "eff_jing_effective": eff_jing,
        "eff_jing_state": eff_jing_state,
        "inventory": inventory,
        "inventory_total": sum(row["qty"] for row in inventory),
        "equipped": equipped,
    }


def build_observe_view(
    session: Any,
    state: Any,
    *,
    account_id: str = "",
    entity_id: str = "",
    room_id: str = "",
) -> dict[str, Any]:
    """纯投影：只读 `state` / `content`，不触碰 session 的写路径。

    解析顺序：`account_id` -> `player:<account>`；`entity_id` 直接查实体；
    `room_id` 可单独观察房间，也可作为角色的“必须在该房间”校验。
    """
    tick = int(getattr(state, "tick", 0))
    account = str(account_id or "").strip().lower()
    ent_id = str(entity_id or "").strip()
    loc_id = str(room_id or "").strip()
    if not account and not ent_id and not loc_id:
        return _error(
            "observe_missing_target", tick, "需要 account_id / entity_id / room_id 之一。"
        )
    if account and not ACCOUNT_RE.match(account):
        return _error("bad_account", tick, "账号格式不合法（3-32 位小写字母、数字、下划线或连字符）。")
    if account and not ent_id:
        ent_id = f"player:{account}"
    if ent_id and not account and ent_id.startswith("player:"):
        account = ent_id[len("player:"):]

    ent = (getattr(state, "entities", None) or {}).get(ent_id) if ent_id else None
    if ent_id and ent is None:
        return _error("unknown_entity", tick, "角色不存在，或尚未进入过世界。", entity_id=ent_id)
    if ent is not None and loc_id and str(ent.location) != loc_id:
        return _error(
            "entity_not_in_room",
            tick,
            "该角色不在指定房间。",
            entity_id=ent_id,
            room_id=loc_id,
            actual_room=str(ent.location),
        )
    if ent is not None:
        loc_id = str(ent.location)
    if loc_id and loc_id not in (getattr(state, "locations", None) or {}):
        return _error("unknown_room", tick, "房间不存在。", room_id=loc_id)

    view: dict[str, Any] = {
        "ok": True,
        "profile_version": OBSERVE_PROFILE_VERSION,
        # 固定标记：客户端/验收可据此断言“这次读取没有副作用”。
        "readonly": True,
        "tick": tick,
        "seq": int(getattr(session.store, "max_seq", lambda: 0)()),
        "room": _room_view(session.content, state, loc_id) if loc_id else None,
        "character": _character_view(session.content, state, ent) if ent is not None else None,
    }
    return view
