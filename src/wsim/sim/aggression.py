"""NPC 主动攻击规则（只由 xkx 内容包启用）。

原版依据：
- 通用 aggressive：`feature/attack.lpc:245` 的 `init()` 在对象进入房间时调用
  `COMBAT_D->auto_fight(this_object(), ob, "aggressive")`（`feature/attack.lpc:269-271`）。
- 毒蛇：`d/hangzhou/npc/dushe.lpc:27-34`，玩家身上没有 `xionghuang` 时
  `call_out("kill_ob", 1, me)`。
- 大狼狗：`d/hangzhou/npc/wolfdog.lpc:29-36`，
  `random(ob->query_kar() + ob->query_per()) < 30` 时 `call_out("kill_ob", 1, ob)`。

`kill_ob` 只登记仇恨并 `set_heart_beat(1)`（`feature/attack.lpc:69` /
`feature/attack.lpc:36-41`），伤害在下一个心跳结算。复刻里的对应关系：
触发事件只写 `combat_target`，ambient 每 tick 结算一回合。
"""
from __future__ import annotations

import os
from typing import Any

# 原版 `feature/attack.lpc:10`：选敌窗口大小（不是敌人表上限）。
MAX_OPPONENT = 4

# 进程启动读一次；运行中改环境变量不生效（评审要求“读一次”）。
_ENV_MULTI_COMBAT = str(os.environ.get("XKX_MULTI_COMBAT", "")).strip().lower() in {
    "1", "true", "on", "yes"
}
_MULTI_COMBAT_ENABLED = _ENV_MULTI_COMBAT


def multi_combat_enabled() -> bool:
    """多敌敌人表开关（默认关闭，关闭态零字段/零事件/零额外骰）。"""
    return _MULTI_COMBAT_ENABLED


def configure_multi_combat(enabled: bool) -> None:
    """API 入口读世界配置 `combat.multi_combat` 点亮开关；环境变量优先。

    只在进程启动时调用一次（与 `_ENV_MULTI_COMBAT` 同语义）；运行中不重读。
    """
    global _MULTI_COMBAT_ENABLED
    _MULTI_COMBAT_ENABLED = bool(enabled) or _ENV_MULTI_COMBAT


def effective_enemies(entity: Any) -> list[str]:
    """当前敌人表；旧存档只有 `combat_target` 时回退成单元素表。"""
    raw = getattr(entity, "combat_enemies", None) or []
    ids: list[str] = []
    for item in raw:
        sid = str(item)
        if sid and sid not in ids:
            ids.append(sid)
    if ids:
        return ids
    target = str(getattr(entity, "combat_target", "") or "")
    return [target] if target else []


def select_index(count: int, rng: Any) -> tuple[int, int]:
    """原版 `attack.lpc:139-140`：`random(4)`，越界回退 0。

    返回 `(选中下标, 原始骰值)`；`count<=0` 时不耗骰。
    调用方须传入独立盐的 PRNG（`action:npc_aggression:opponent`），
    不得与本回合战斗骰同流（见 spec 已知偏差）。
    """
    if count <= 0:
        return 0, -1
    roll = int(rng.randint(0, MAX_OPPONENT - 1))
    return (roll if roll < count else 0), roll


# 只列需要特殊语义的 NPC；其余 `aggressive=true` 的 NPC 一律按 always 处理。
AGGRESSION_SPECS: dict[str, dict[str, Any]] = {
    "/d/hangzhou/npc/laohu": {
        "kind": "always",
        "delay": 0,
        "source": "d/hangzhou/npc/laohu.lpc:20 -> feature/attack.lpc:269",
    },
    "/d/hangzhou/npc/dushe": {
        "kind": "unless_item",
        "item": "/clone/medicine/vegetable/xionghuang",
        "item_key": "xionghuang",
        "delay": 1,
        "source": "d/hangzhou/npc/dushe.lpc:27-34",
    },
    "/d/hangzhou/npc/wolfdog": {
        "kind": "chance_kar_per",
        "threshold": 30,
        "delay": 1,
        "source": "d/hangzhou/npc/wolfdog.lpc:29-36",
    },
}


def aggression_rules(content: Any) -> dict[str, dict[str, Any]]:
    """从内容包派生主动攻击规则；旧九地点内容没有这些 id/字段，返回空表。"""
    rules: dict[str, dict[str, Any]] = {}
    for npc in (getattr(content, "npcs", None) or []):
        if not isinstance(npc, dict):
            continue
        npc_id = str(npc.get("id") or "")
        if not npc_id:
            continue
        spec = AGGRESSION_SPECS.get(npc_id)
        if spec is not None:
            rules[npc_id] = {**spec, "name": str(npc.get("name") or npc_id)}
        elif npc.get("aggressive"):
            # feature/attack.lpc:269：attitude=aggressive 的 NPC 无条件 auto_fight。
            rules[npc_id] = {
                "kind": "always",
                "delay": 0,
                "name": str(npc.get("name") or npc_id),
                "source": "feature/attack.lpc:269",
            }
    return rules


def rule_for(entity: Any, rules: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """克隆实体用 template 回溯到内容 id；原生实体 id 就是内容 id。"""
    template = str(getattr(entity, "template", "") or "")
    if template and template in rules:
        return rules[template]
    return rules.get(str(getattr(entity, "id", "")))


def carries_item(entity: Any, item_ref: str, item_key: str) -> bool:
    """对应原版 `present("xionghuang", this_player())`。

    `present` 会同时搜背包和已装备对象；这里两处都查，并兼容全路径/短 id。
    """
    wanted = {item_ref, item_key}
    for key, qty in dict(getattr(entity, "inventory", {}) or {}).items():
        if int(qty) > 0 and (str(key) in wanted or str(key).rsplit("/", 1)[-1] == item_key):
            return True
    for item in list((getattr(entity, "equipped", {}) or {}).values()) + list(
        getattr(entity, "equipment", []) or []
    ):
        if item and (str(item) in wanted or str(item).rsplit("/", 1)[-1] == item_key):
            return True
    return False


def _attr(entity: Any, name: str) -> int:
    try:
        return int((getattr(entity, "attrs", {}) or {}).get(name, 10))
    except (TypeError, ValueError):
        return 10


def decide(
    rule: dict[str, Any],
    npc: Any,
    player: Any,
    rng: Any,
) -> tuple[bool, dict[str, Any]]:
    """返回 (是否开打, 审计字段)。概率分支的取数顺序在这里锁定。"""
    kind = str(rule.get("kind") or "")
    base = {"kind": kind}

    if kind == "always":
        return True, {**base, "result": "attack"}

    if kind == "unless_item":
        item_ref = str(rule.get("item") or "")
        item_key = str(rule.get("item_key") or item_ref.rsplit("/", 1)[-1])
        if carries_item(player, item_ref, item_key):
            return False, {**base, "result": "declined_item", "item": item_ref}
        return True, {**base, "result": "attack", "item": item_ref}

    if kind == "chance_kar_per":
        total = max(1, _attr(player, "kar") + _attr(player, "per"))
        threshold = int(rule.get("threshold") or 30)
        # 原版 random(kar+per) -> [0, kar+per-1]。
        roll = int(rng.randint(0, total - 1))
        triggered = roll < threshold
        return triggered, {
            **base,
            "result": "attack" if triggered else "declined_chance",
            "roll": roll,
            "total": total,
            "threshold": threshold,
        }

    # 未知规则不开打，避免静默给 NPC 加攻击性。
    return False, {**base, "result": "declined_unknown"}
