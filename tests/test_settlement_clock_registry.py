"""NPC 侧周期结算的时钟归属登记表门禁。

目的：任何按“天”触发的结算，要么显式登记为 NPC 日，要么写清为什么必须挂
世界轴。新增结算忘记登记时本测试失败，防止“第二套时钟”悄悄长回来。
"""
from __future__ import annotations

import ast
from pathlib import Path

from wsim.core.settlement_clock import NPC, SETTLEMENTS, WORLD
from wsim.sim.scheduler import DAY_END_VERBS, DAY_START_VERBS

ROOT = Path(__file__).resolve().parents[1]


def test_every_day_driven_settlement_has_an_explicit_clock():
    for verb in (*DAY_START_VERBS, *DAY_END_VERBS):
        assert verb in SETTLEMENTS, f"日界结算 {verb} 未登记时钟归属"
        assert SETTLEMENTS[verb][0] in (NPC, WORLD)


def test_npc_side_settlements_do_not_read_world_day():
    # 这些是 NPC 生计/玩家店铺的“钱按天走”的结算，必须挂 NPC 日。
    npc_keys = {
        "system_living",
        "system_shop_settle",
        "system_savings_tax",
        "system_shop_dividend",
        "system_shop_tax",
        "quest.deadline",
        "player_shop.attendance",
        "player_shop.wage",
        "sect.pay_disciples",
    }
    for key in npc_keys:
        assert SETTLEMENTS[key][0] == NPC, f"{key} 必须按 NPC 日结算"


def test_world_axis_entries_must_carry_reason():
    for key, (clock, reason) in SETTLEMENTS.items():
        assert clock in (NPC, WORLD)
        assert reason.strip(), f"{key} 缺少时钟选择理由"
        if clock == WORLD:
            assert len(reason) >= 12, f"{key} 的世界轴理由过于含糊：{reason}"


def test_day_verbs_in_scheduler_are_all_registered():
    """调度器日界源码里出现的每个 system_* 动词都必须登记。

    新增日界结算若只在 `_day_start/_day_end` 里写动词而忘记登记，这里会失败。
    """
    tree = ast.parse((ROOT / "src/wsim/sim/scheduler.py").read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("_day_start", "_day_end"):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)
                    and sub.value.startswith("system_")
                ):
                    found.add(sub.value)
    missing = found - set(SETTLEMENTS)
    assert not missing, f"新增日界结算未登记时钟归属：{sorted(missing)}"
    assert found == set(DAY_START_VERBS) | set(DAY_END_VERBS)
