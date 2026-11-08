"""玩家可见日期/判定层的 NPC 时钟单一入口验收（spec §4、§6.2、§8.2）。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from wsim.actions.business import ATTENDANCE_MARKER, _worked_on_day
from wsim.api.session import WorldSession
from wsim.core.clock import TICKS_PER_DAY, npc_tick_of
from wsim.core.models import Intent

ROOT = Path(__file__).resolve().parents[1]


def test_session_clock_tick_reads_ambient_and_falls_back(tmp_path):
    session = WorldSession(ROOT, run_id="clock-one", seed=42, npc_limit=0, run_dir=tmp_path)
    world = int(session.state.tick)
    # 无 ambient：玩家可见日 = 世界 tick（headless 兼容路径）
    assert session.clock_tick() == world

    session.ambient = SimpleNamespace(tick=288, stopped=False)
    assert session.clock_tick() == 288

    session.ambient.stopped = True
    assert session.clock_tick() == int(session.state.tick)
    session.store.close()


def test_player_view_exposes_single_clock_entry(tmp_path):
    session = WorldSession(ROOT, run_id="clock-view", seed=42, npc_limit=0, run_dir=tmp_path)
    session.perform("u_demo", "enter", {"archetype": "游侠", "display_name": "阿青"})
    session.ambient = SimpleNamespace(tick=288, stopped=False)
    view = session._status_view("player:u_demo")
    assert view["clock_tick"] == 288
    assert view["clock_day"] == 2
    assert view["today"]["clock_tick"] == 288
    assert view["today"]["clock_day"] == 2
    # 原始 tick 仍是世界事件轴，不得被当成日期
    assert view["tick"] == int(session.state.tick)
    session.store.close()


def test_quest_deadline_and_display_share_npc_clock(tmp_path):
    session = WorldSession(ROOT, run_id="clock-quest", seed=42, npc_limit=0, run_dir=tmp_path)
    session.perform("u_demo", "enter", {"archetype": "游侠", "display_name": "阿青"})
    quest = next(q for q in session.state.quests.values() if q.status == "open")
    session.ambient = SimpleNamespace(tick=288, stopped=False)
    # 玩家日历到达 NPC 日 2，而世界 tick 远小于这个值：显示/判定都必须跟 NPC 时钟。
    row = session._quest_row(quest)
    assert row["deadline_tick"] == quest.deadline_tick
    assert row["deadline_day"] == quest.deadline_tick // TICKS_PER_DAY + 1
    session.store.close()


def test_npc_tick_falls_back_to_world_tick_without_ambient():
    assert npc_tick_of(Intent(actor="npc:a", verb="noop", args={}, tick=777)) == 777
    assert npc_tick_of(
        Intent(actor="npc:a", verb="noop", args={}, tick=777, clock_tick=288)
    ) == 288


def test_shop_attendance_judges_npc_day_not_world_window():
    from wsim.world.state import WorldState

    state = WorldState(locations={})
    state.memories["npc:a"] = [{"tick": 9999, "text": f"上工{ATTENDANCE_MARKER}:D2"}]
    # 世界 tick 9999（第 69 世界日）不属于第 2 日；但 NPC 日标记 D2 必须算出勤。
    assert _worked_on_day(state, "npc:a", 2, clock_marker=True) is True
    assert _worked_on_day(state, "npc:a", 2, clock_marker=False) is False


def test_work_writes_attendance_marker_in_npc_day(content):
    """WorkAction 产出的日结契约记忆必须能被 _worked_on_day(clock_marker=True) 认出来。

    回归点：标记串曾写成 `工资本日由日结发放30文：D0`，与判定串
    `工资本日由日结发放:D0` 不匹配，导致共享世界雇工永远“未到店”。
    """
    from wsim.actions.basic import WorkAction
    from wsim.core.models import Effect, Intent
    from wsim.world.effects import apply_effects
    from wsim.world.state import WorldState

    state = WorldState(locations=content.locations)
    shop = "shop:player:own"
    apply_effects(state, [
        Effect("create_entity", {
            "id": "player:own", "name": "掌柜", "role": "worker",
            "location": "a", "stamina": 100.0, "max_stamina": 100.0,
        }),
        Effect("create_account", {"account": "wallet:player:own"}),
        Effect("create_account", {"account": f"cash:{shop}"}),
        Effect("create_business", {
            "id": shop, "name": "测试摊", "location": "a", "kind": "food",
            "owner": "player:own", "cash_account": f"cash:{shop}",
        }),
        Effect("set_field", {"entity": "player:own", "field": "business", "value": shop}),
        Effect("create_entity", {
            "id": "npc:emp", "name": "雇工", "role": "worker",
            "location": "a", "stamina": 100.0, "max_stamina": 100.0,
            "skills": {"work": 0.4},
        }),
        Effect("create_account", {"account": "wallet:npc:emp"}),
        Effect("shop_hire", {
            "business": shop, "entity": "npc:emp", "position": "worker", "wage": 20,
        }),
        Effect("mint", {"account": f"cash:{shop}", "amount": 40, "reason": "test_float"}),
    ])
    action = WorkAction(content)
    intent = Intent(actor="npc:emp", verb="work", args={"hours": 8},
                    tick=9999, clock_tick=288, source="test")
    assert action.preconditions(state, intent) == []
    apply_effects(state, action.apply(state, intent, None))
    # 记忆上限 80 条会淘汰旧条目：出勤必须有持久凭证，清空记忆后仍要认。
    state.memories.pop("npc:emp", None)
    # clock_tick=288 → NPC 日 2；世界 tick 9999 是第 69 日，旧口径不该算出勤。
    assert _worked_on_day(state, "npc:emp", 2, clock_marker=True) is True
    assert _worked_on_day(state, "npc:emp", 2, clock_marker=False) is False


def test_quest_deadline_is_created_in_npc_ticks():
    from wsim.actions.quests import SystemQuestPostAction

    tpl = {"verb": "talk", "deadline_days": 1, "settlement": {}}
    eff = SystemQuestPostAction._quest_effect(
        tpl, "quest:q1", "npc:giver", tick=9999, clock_tick=288
    )
    assert eff.data["deadline_tick"] == 288 + TICKS_PER_DAY
    # 事件轴字段保持世界 tick，避免改动事件哈希
    assert eff.data["created_tick"] == 9999
