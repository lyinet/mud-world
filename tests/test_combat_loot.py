"""loot / get：击杀掉落与地面物品的边界、原因码、确定性与账本一致。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.loot import LootAction  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": [], "services": []}}


class _Content:
    items = {"item.hide": {"weight": 2}, "item.tusk": {"weight": 3}}


def _writer(ground: dict[str, int] | None = None, kills: list | None = None, seed: int = 42):
    """ground 是创世地面（无来源）；kills 是 (tick, victim, {item: qty}) 的击杀掉落。"""
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    reg = ActionRegistry()
    reg.register(LootAction(_Content()))
    writer = Writer(state, store, reg, seed=seed)
    effects = [
        Effect(
            "create_entity",
            {"id": "player:hero", "name": "英雄", "role": "visitor", "location": "inn", "max_hp": 100, "hp": 100},
        )
    ]
    for item, qty in sorted((ground or {}).items()):
        effects.append(Effect("loot_create", {"location": "inn", "item": item, "qty": qty}))
    for tick, victim, drops in kills or []:
        for item, qty in sorted(drops.items()):
            effects.append(
                Effect(
                    "loot_create",
                    {
                        "location": "inn",
                        "item": item,
                        "qty": qty,
                        "reason": "death",
                        "source": "kill",
                        "source_entity": victim,
                        "source_tick": tick,
                    },
                )
            )
    writer.bootstrap(effects, tick=0, verb="genesis")
    return writer, store


def _loot(writer, **args):
    return writer.submit(
        Intent("player:hero", "loot", {"player_id": "player:hero", **args}, 0, "player")
    )


def _get(writer, **args):
    return writer.submit(
        Intent("player:hero", "get", {"player_id": "player:hero", **args}, 0, "player")
    )


def test_loot_only_takes_latest_kill_drop():
    writer, _ = _writer(
        {"item.hide": 7, "item.tusk": 1},
        kills=[
            (5, "npc.dog", {"item.hide": 1}),
            (9, "npc.wolf", {"item.tusk": 2}),
        ],
    )
    event = _loot(writer)
    assert event.outcome == "ok"
    assert writer.state.entities["player:hero"].inventory == {"item.tusk": 2}
    # 世界既有地面物品与上一具尸体的掉落都不动。
    assert writer.state.ground["inn"] == {"item.hide": 8, "item.tusk": 1}
    assert writer.state.ground_sources["inn"][0]["key"] == "kill:5:npc.dog"


def test_get_all_clears_ground():
    writer, _ = _writer({"item.hide": 7}, kills=[(5, "npc.dog", {"item.tusk": 2})])
    event = _get(writer, scope="all")
    assert event.outcome == "ok"
    assert writer.state.ground == {}
    assert writer.state.ground_sources == {}
    assert writer.state.entities["player:hero"].inventory == {"item.hide": 7, "item.tusk": 2}


def test_get_specific_item_and_qty_only_moves_that_much():
    writer, _ = _writer({"item.hide": 7})
    event = _get(writer, scope="all", item="item.hide", qty=3)
    assert event.outcome == "ok"
    assert writer.state.entities["player:hero"].inventory == {"item.hide": 3}
    assert writer.state.ground["inn"]["item.hide"] == 4


def test_get_alias_defaults_to_ground_scope():
    writer, _ = _writer({"item.hide": 2})
    event = _get(writer)
    assert event.outcome == "ok"
    assert writer.state.entities["player:hero"].inventory == {"item.hide": 2}
    assert writer.state.ground == {}


def test_loot_without_kill_drop_rejects_loot_no_corpse():
    writer, _ = _writer({"item.hide": 7, "item.tusk": 1})
    event = _loot(writer)
    assert event.outcome == "rejected"
    assert event.reason == "rejected:loot_no_corpse"
    # 静默成功与清地面都不允许。
    assert writer.state.ground["inn"] == {"item.hide": 7, "item.tusk": 1}
    assert writer.state.entities["player:hero"].inventory == {}


def test_legacy_ground_without_source_is_not_loot_target():
    writer, _ = _writer({"item.hide": 7})  # 旧式 loot_create：无 source 字段
    event = _loot(writer)
    assert event.outcome == "rejected"
    assert event.reason == "rejected:loot_no_corpse"
    assert writer.state.ground_sources == {}


def test_loot_specific_item_only_from_latest_kill():
    writer, _ = _writer(
        {},
        kills=[
            (5, "npc.dog", {"item.hide": 1}),
            (9, "npc.wolf", {"item.tusk": 2}),
        ],
    )
    event = _loot(writer, item="item.hide")
    assert event.outcome == "rejected"
    assert event.reason == "rejected:loot_not_found"
    assert writer.state.entities["player:hero"].inventory == {}


def test_loot_specific_item_and_qty_from_kill():
    writer, _ = _writer({}, kills=[(5, "npc.dog", {"item.hide": 3})])
    event = _loot(writer, item="item.hide", qty=2)
    assert event.outcome == "ok"
    assert writer.state.entities["player:hero"].inventory == {"item.hide": 2}
    assert writer.state.ground["inn"]["item.hide"] == 1
    assert writer.state.ground_sources["inn"][0]["items"] == {"item.hide": 1}


def test_loot_qty_over_kill_drop_rejects():
    writer, _ = _writer({}, kills=[(5, "npc.dog", {"item.hide": 1})])
    event = _loot(writer, item="item.hide", qty=2)
    assert event.outcome == "rejected"
    assert event.reason == "rejected:loot_not_found"
    assert writer.state.ground_sources["inn"][0]["items"] == {"item.hide": 1}


def test_loot_weights_match_content():
    writer, _ = _writer({}, kills=[(5, "npc.dog", {"item.hide": 2, "item.tusk": 1})])
    event = _loot(writer)
    weights = {e["data"]["item"]: e["data"]["weight"] for e in event.effects if e["op"] == "loot_take"}
    assert weights == {"item.hide": 4, "item.tusk": 3}


def test_loot_deltas_match_state_inventory():
    writer, _ = _writer({"item.hide": 2}, kills=[(5, "npc.dog", {"item.tusk": 1})])
    before = dict(writer.state.entities["player:hero"].inventory)
    event = _loot(writer)
    after = writer.state.entities["player:hero"].inventory
    for item, qty in after.items():
        moved = sum(
            e["data"]["qty"] for e in event.effects
            if e["op"] == "loot_take" and e["data"]["item"] == item
        )
        assert moved == qty - int(before.get(item, 0))


def test_same_seed_loot_order_is_identical():
    args = dict(ground={"item.tusk": 1}, kills=[(5, "npc.dog", {"item.hide": 2})])
    a, _ = _writer(**args)
    b, _ = _writer(**args)
    ea = _loot(a)
    eb = _loot(b)
    assert ea.effects == eb.effects
    assert state_hash(a.state) == state_hash(b.state)
