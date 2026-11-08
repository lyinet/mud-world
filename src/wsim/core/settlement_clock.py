"""NPC 侧周期性结算的时钟归属登记表。

目的：让“第二套时钟”无法悄悄长回来。任何按天触发的结算都必须在这里显式
登记挂哪个时钟；新增结算忘记登记时 `tests/test_settlement_clock_registry.py`
会失败。

- `NPC`：读 NPC 日历（`npc_tick_of(intent)` / `WorldSession.clock_tick()`）。
- `WORLD`：读世界事件轴（`WorldState.tick`），必须写清为什么不会因玩家
  活跃而重复结算。
"""
from __future__ import annotations

NPC = "npc"
WORLD = "world"

# key -> (clock, reason)
SETTLEMENTS: dict[str, tuple[str, str]] = {
    "system_recover": (NPC, "日界体力恢复；按 NPC 日触发"),
    "system_quest_post": (NPC, "委托刷新/保供；按 NPC 日触发"),
    "system_living": (NPC, "NPC 生活费；按 NPC 日触发"),
    "system_debts": (NPC, "债务日结；按 NPC 日触发"),
    "system_quest_fail": (NPC, "过期委托下架/清理；截止与保留期均按 NPC 日"),
    "system_milestone": (NPC, "里程碑日结；按 NPC 日触发"),
    "system_maintenance": (NPC, "日常维护；按 NPC 日触发"),
    "system_shop_settle": (NPC, "玩家店铺工资/出勤/7 日账；出勤标记与账本日均为 NPC 日"),
    "system_savings_tax": (NPC, "月税；按 NPC 日计数到 30/60/90"),
    "system_shop_dividend": (NPC, "店铺分红；按 NPC 日计数"),
    "system_shop_tax": (NPC, "店铺月税；按 NPC 日计数"),
    "quest.deadline": (NPC, "委托截止判定与显示同源（deadline_tick 为 NPC 时钟单位）"),
    "quest.accept_cooldown": (NPC, "NPC 接单冷却/临期判定；按 NPC 日"),
    "player_shop.attendance": (NPC, "雇工出勤；只认 NPC 日的到店标记"),
    "player_shop.wage": (NPC, "雇工工资；随 NPC 日结算"),
    "sect.pay_disciples": (NPC, "门派会费/俸禄/欠饷；按 NPC 日"),
    "shop_ledger_day": (NPC, "7 日账日标签；取 NPC 日结事件的 args['day']"),
    # 世界轴：只推进事件/唤醒，不转移或销毁余额。
    "system_plan": (WORLD, "事件队列 slot 轴；每个 NPC 日只在日界生成一次，不结算金钱"),
    "lod_wake_schedule": (WORLD, "唤醒节奏；不转移/销毁任何账户余额，且受 wake/intent 预算限制"),
    "escort_arrive": (WORLD, "押镖到达时间轴；一次性事件，不按天重复"),
    "event_created_tick": (WORLD, "事件溯源字段；不参与任何日结判定"),
    "player_rest_day_boundary": (WORLD, "玩家体力/在线日界；只影响玩家自身，委托下架已改用 NPC 截止"),
}

# 这些结算不是“动词”，但同样按天触发，必须在表里登记。
NON_VERB_KEYS: tuple[str, ...] = (
    "quest.deadline",
    "quest.accept_cooldown",
    "player_shop.attendance",
    "player_shop.wage",
    "sect.pay_disciples",
    "shop_ledger_day",
)


def clock_for(key: str) -> str:
    return SETTLEMENTS[key][0]
