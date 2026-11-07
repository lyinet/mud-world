"""loot / get：地面取物的原因码、确定性与账本一致。"""
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


def _writer(ground: dict[str, int] | None = None, seed: int = 42):
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
    writer.bootstrap(effects, tick=0, verb="genesis")
    return writer, store


def test_loot_takes_all_ground_items_in_sorted_order():
    writer, _ = _writer({"item.tusk": 1, "item.hide": 2})
    event = writer.submit(Intent("player:hero", "loot", {"player_id": "player:hero"}, 0, "player"))
    assert event.outcome == "ok"
    assert [e["data"]["item"] for e in event.effects if e["op"] == "loot_take"] == ["item.hide", "item.tusk"]
    inv = writer.state.entities["player:hero"].inventory
    assert inv == {"item.hide": 2, "item.tusk": 1}
    assert writer.state.ground == {}
    # 重量 = 内容 weight × 数量，事件可直接核对。
    weights = {e["data"]["item"]: e["data"]["weight"] for e in event.effects if e["op"] == "loot_take"}
    assert weights == {"item.hide": 4, "item.tusk": 3}


def test_get_alias_uses_the_same_action():
    writer, _ = _writer({"item.hide": 1})
    event = writer.submit(Intent("player:hero", "get", {"player_id": "player:hero"}, 0, "player"))
    assert event.outcome == "ok"
    assert writer.state.entities["player:hero"].inventory["item.hide"] == 1


def test_empty_ground_rejects_with_loot_not_found():
    writer, _ = _writer()
    event = writer.submit(Intent("player:hero", "loot", {"player_id": "player:hero"}, 0, "player"))
    assert event.outcome == "rejected"
    assert event.reason == "rejected:loot_not_found"


def test_missing_item_rejects_with_loot_not_found():
    writer, _ = _writer({"item.hide": 1})
    event = writer.submit(
        Intent("player:hero", "loot", {"player_id": "player:hero", "item": "item.tusk"}, 0, "player")
    )
    assert event.outcome == "rejected"
    assert event.reason == "rejected:loot_not_found"


def test_loot_specific_item_and_qty_only_moves_that_much():
    writer, _ = _writer({"item.hide": 3})
    event = writer.submit(
        Intent("player:hero", "loot", {"player_id": "player:hero", "item": "item.hide", "qty": 2}, 0, "player")
    )
    assert event.outcome == "ok"
    assert writer.state.entities["player:hero"].inventory["item.hide"] == 2
    assert writer.state.ground["inn"]["item.hide"] == 1


def test_loot_deltas_match_state_inventory():
    writer, _ = _writer({"item.hide": 2})
    before = dict(writer.state.entities["player:hero"].inventory)
    event = writer.submit(Intent("player:hero", "loot", {"player_id": "player:hero"}, 0, "player"))
    after = writer.state.entities["player:hero"].inventory
    for item, qty in after.items():
        moved = sum(
            e["data"]["qty"] for e in event.effects
            if e["op"] == "loot_take" and e["data"]["item"] == item
        )
        assert moved == qty - int(before.get(item, 0))


def test_same_seed_loot_order_is_identical():
    a, _ = _writer({"item.tusk": 1, "item.hide": 2})
    b, _ = _writer({"item.tusk": 1, "item.hide": 2})
    ea = a.submit(Intent("player:hero", "loot", {"player_id": "player:hero"}, 0, "player"))
    eb = b.submit(Intent("player:hero", "loot", {"player_id": "player:hero"}, 0, "player"))
    assert ea.effects == eb.effects
    assert state_hash(a.state) == state_hash(b.state)
