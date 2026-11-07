"""NPC 死亡掉落：随身装备（equipment）转成地面物品，再由 loot/get 取走。

对应原版 `make_corpse` 把装备塞进尸体；本引擎不建模尸体，死亡后直接进
`WorldState.ground`。旧九地点 NPC 没有 `equipment` 字段，行为与哈希不变。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.combat import FightAction  # noqa: E402
from wsim.actions.loot import LootAction  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.api.xkx_content import load_xkx_content  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import Entity, WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}


def _world(*, equipment: list[str] | None = None, inventory: dict[str, int] | None = None):
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    registry = ActionRegistry()
    registry.register(FightAction())
    registry.register(LootAction())
    writer = Writer(state, store, registry, seed=42)
    effects = [
        Effect(
            "create_entity",
            {
                "id": "player:hero",
                "name": "英雄",
                "role": "wanderer",
                "location": "inn",
                "max_hp": 100,
                "hp": 100,
                "attrs": {"str": 50},
                "combat_exp": 1000,
                "skill_levels": {"unarmed": 100},
            },
        ),
        Effect(
            "create_entity",
            {
                "id": "npc.weak",
                "name": "弱鸡",
                "role": "monster",
                "location": "inn",
                "max_hp": 1,
                "hp": 1,
                "equipment": list(equipment or []),
            },
        ),
    ]
    for item, qty in sorted((inventory or {}).items()):
        effects.append(Effect("item_create", {"owner": "npc.weak", "item": item, "qty": qty, "source": "test"}))
    writer.bootstrap(effects, tick=0, verb="genesis")
    return writer


def test_death_drops_equipment_after_entity_status_and_loot_picks_it_up():
    writer = _world(equipment=["item.cloth"])
    event = writer.submit(
        Intent(
            "player:hero",
            "fight",
            {"target": "npc.weak", "loot": {"item.hide": 1}},
            0,
            "player",
        )
    )
    assert event.outcome == "ok"
    ops = [e["op"] for e in event.effects]
    assert ops.index("entity_status") < ops.index("loot_create")
    # 显式 loot 不能吞掉随身装备；两件都在地面。
    assert writer.state.ground["inn"]["item.cloth"] == 1
    assert writer.state.ground["inn"]["item.hide"] == 1

    take = writer.submit(Intent("player:hero", "loot", {}, 0, "player"))
    assert take.outcome == "ok"
    player = writer.state.entities["player:hero"]
    assert player.inventory["item.cloth"] == 1
    assert "item.cloth" not in (writer.state.ground.get("inn") or {})


def test_equipment_and_inventory_same_id_do_not_double_count():
    writer = _world(equipment=["item.cloth"], inventory={"item.cloth": 1})
    writer.submit(Intent("player:hero", "fight", {"target": "npc.weak"}, 0, "player"))
    assert writer.state.ground["inn"]["item.cloth"] == 1


def test_equipment_list_is_serialized_only_when_present():
    assert "equipment" not in Entity(id="npc.legacy", name="旧村民", role="farmer", location="inn").to_dict()
    data = Entity(
        id="npc.gear", name="带装备", role="monster", location="inn",
        equipment=["item.cloth", "item.cloth"],
    ).to_dict()
    assert data["equipment"] == ["item.cloth", "item.cloth"]


def test_xkx_genesis_moves_equipment_into_entity_not_inventory():
    content = load_xkx_content(ROOT / "content" / "xkx")
    state = WorldState(locations=content.locations)
    result = build_genesis(content, content.npcs, root=ROOT, state=state)
    apply_effects(state, result.effects)

    poorman = state.entities["/d/hangzhou/npc/poorman"]
    assert "/d/hangzhou/npc/obj/cloth" in poorman.equipment
    assert poorman.inventory == {}
    # 内容线已补齐装备物品：真实内容不应再有缺失诊断。
    assert not any(m.startswith("unknown_equipment_item:") for m in result.mismatches)
    for npc in content.npcs:
        expected = [str((row or {}).get("ref") or "") for row in (npc.get("equipment") or [])]
        expected = [ref for ref in expected if ref]
        if expected:
            assert state.entities[str(npc["id"])].equipment == expected, npc["id"]

    # 缺失路径仍在：临时给穷汉塞一个不存在的 ref，必须只写诊断、不编造数值。
    broken = copy.deepcopy(next(n for n in content.npcs if n["id"] == "/d/hangzhou/npc/poorman"))
    broken["equipment"].append({"ref": "missing/ghost_blade"})
    state2 = WorldState(locations=content.locations)
    result2 = build_genesis(content, [broken], root=ROOT, state=state2)
    apply_effects(state2, result2.effects)
    assert "unknown_equipment_item:/d/hangzhou/npc/poorman:missing/ghost_blade" in result2.mismatches
    assert "missing/ghost_blade" in state2.entities["/d/hangzhou/npc/poorman"].equipment
