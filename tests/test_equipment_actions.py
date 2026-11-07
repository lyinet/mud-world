"""装备/取物动作层：物品 apply、槽位校验、地面掉落与负重口径。"""
from __future__ import annotations

from wsim.actions.common import max_encumbrance, overweight_reason
from wsim.actions.equipment import (
    RemoveAction,
    WearAction,
    WieldAction,
    item_apply,
)
from wsim.actions.loot import DropAction, LootAction
from wsim.core.models import Effect, Intent
from wsim.world.effects import apply_effects
from wsim.world.state import Entity, WorldState

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": [], "services": []}}


class _Content:
    items = {
        "cloth": {
            "name": "布衣",
            "category": "armor",
            "equip_slot": "cloth",
            "weight": 10,
            "apply": {"apply/armor": 5},
        },
        "cloth2": {
            "name": "皮甲",
            "category": "armor",
            "equip_slot": "cloth",
            "weight": 20,
        },
        "odd": {
            "name": "怪甲",
            "category": "armor",
            "equip_slot": "tail",
            "weight": 5,
        },
        "sword": {
            "name": "长剑",
            "category": "weapon",
            "equip_slot": "sword",
            "weight": 500,
            "apply": {"apply/damage": 20},
        },
        "bread": {"name": "炊饼", "category": "food", "weight": 1},
        "heavy": {"name": "重物", "category": "misc", "weight": 3000},
    }


def _state(inventory: dict[str, int] | None = None, equipped: dict[str, str] | None = None):
    state = WorldState(locations=LOCATIONS)
    ent = Entity(
        id="player:hero",
        name="英雄",
        role="visitor",
        location="inn",
        inventory=dict(inventory or {}),
        equipped=dict(equipped or {}),
    )
    state.entities[ent.id] = ent
    return state, ent


def _intent(verb: str, **args) -> Intent:
    return Intent("player:hero", verb, {"player_id": "player:hero", **args}, 0, "player")


def test_item_apply_returns_empty_without_apply():
    assert item_apply(_Content(), "bread") == {}
    assert item_apply(_Content(), "cloth2") == {}
    assert item_apply(_Content(), "not_an_item") == {}


def test_item_apply_reads_valid_apply_entries_only():
    assert item_apply(_Content(), "cloth") == {"apply/armor": 5}

    class _Bad:
        items = {
            "x": {
                "apply": {
                    "armor": 5,
                    "apply/armor": True,
                    "apply/defense": 2,
                    "apply/attack": 3.5,
                }
            }
        }

    assert item_apply(_Bad(), "x") == {"apply/defense": 2}


def test_equip_and_unequip_effects_move_items_and_track_apply():
    state, ent = _state({"cloth": 1, "sword": 1})
    apply_effects(
        state,
        [
            Effect(
                "equip",
                {
                    "entity": ent.id,
                    "slot": "cloth",
                    "item": "cloth",
                    "apply": item_apply(_Content(), "cloth"),
                    "old_apply": {},
                    "weapon_type": "",
                },
            )
        ],
    )
    assert ent.equipped == {"cloth": "cloth"}
    assert ent.equipped_apply == {"apply/armor": 5}
    assert "cloth" not in ent.inventory

    apply_effects(
        state,
        [
            Effect(
                "equip",
                {
                    "entity": ent.id,
                    "slot": "weapon",
                    "item": "sword",
                    "apply": item_apply(_Content(), "sword"),
                    "old_apply": {},
                    "weapon_type": "sword",
                },
            )
        ],
    )
    assert ent.equipped["weapon"] == "sword"
    assert ent.weapon_type == "sword"
    assert ent.equipped_apply == {"apply/armor": 5, "apply/damage": 20}
    assert "sword" not in ent.inventory

    apply_effects(
        state,
        [Effect("unequip", {"entity": ent.id, "slot": "weapon", "apply": {"apply/damage": 20}})],
    )
    assert ent.weapon_type == ""
    assert ent.equipped_apply == {"apply/armor": 5}
    assert ent.inventory["sword"] == 1

    apply_effects(
        state,
        [Effect("unequip", {"entity": ent.id, "slot": "cloth", "apply": {"apply/armor": 5}})],
    )
    assert ent.equipped_apply == {}
    assert ent.equipped == {}
    assert ent.inventory == {"cloth": 1, "sword": 1}


def test_wear_rejects_non_armor_and_bad_slot():
    state, _ent = _state({"sword": 1, "odd": 1})
    action = WearAction(_Content())
    assert action.preconditions(state, _intent("wear", item="sword")) == ["item_not_wearable"]
    assert action.preconditions(state, _intent("wear", item="odd")) == ["item_not_wearable"]


def test_wear_rejects_occupied_slot():
    state, _ent = _state({"cloth2": 1}, equipped={"cloth": "cloth"})
    action = WearAction(_Content())
    assert action.preconditions(state, _intent("wear", item="cloth2")) == ["slot_occupied:cloth"]


def test_wield_rejects_non_weapon_and_existing_weapon():
    state, _ent = _state({"bread": 1})
    action = WieldAction(_Content())
    assert action.preconditions(state, _intent("wield", item="bread")) == ["item_not_weapon"]

    state, _ent = _state({"sword": 1}, equipped={"weapon": "sword"})
    assert action.preconditions(state, _intent("wield", item="sword")) == ["weapon_already_wielded"]


def test_remove_requires_worn_item_and_refuses_weapon():
    state, _ent = _state({"sword": 1})
    action = RemoveAction(_Content())
    assert action.preconditions(state, _intent("remove", item="sword")) == ["item_not_equipped"]

    state, _ent = _state({}, equipped={"weapon": "sword"})
    assert action.preconditions(state, _intent("remove", item="sword")) == ["not_worn_use_unwield"]


def test_drop_then_loot_round_trip_uses_ground():
    state, ent = _state({"bread": 3})
    content = _Content()

    drop = DropAction(content)
    intent = _intent("drop", item="bread", qty=2)
    assert drop.preconditions(state, intent) == []
    apply_effects(state, drop.apply(state, intent, None))
    assert state.ground["inn"]["bread"] == 2
    assert ent.inventory["bread"] == 1

    loot = LootAction(content)
    intent = _intent("loot", item="bread", qty=2)
    assert loot.preconditions(state, intent) == []
    apply_effects(state, loot.apply(state, intent, None))
    assert ent.inventory["bread"] == 3
    assert "inn" not in state.ground


def test_max_encumbrance_uses_str_and_skill_improvement():
    ent = Entity(
        id="player:hero",
        name="英雄",
        role="visitor",
        location="inn",
        attrs={"str": 10},
        skill_levels={"unarmed": 45, "dodge": 12},
    )
    assert max_encumbrance(ent) == 10 * 5000 + 4 * 1000


def test_overweight_reason_reports_need_and_limit():
    content = _Content()
    ent = Entity(
        id="player:hero",
        name="英雄",
        role="visitor",
        location="inn",
        attrs={"str": 1},
        inventory={"heavy": 2},
    )
    assert overweight_reason(content, ent, 0) == "overloaded:6000:5000"
    assert overweight_reason(content, ent, 0) != ""

    light = Entity(id="p2", name="轻", role="visitor", location="inn", attrs={"str": 20})
    assert overweight_reason(content, light, 0) == ""


def test_encumbrance_is_disabled_without_attrs():
    content = _Content()
    ent = Entity(
        id="npc.old",
        name="旧村民",
        role="farmer",
        location="inn",
        inventory={"heavy": 3},
    )
    assert max_encumbrance(ent) == 0
    assert overweight_reason(content, ent, 100000) == ""


def test_equipped_apply_feeds_combat_actor_without_touching_formulas():
    """装备 apply 只经既有入参进入战斗：apply/attack、apply/armor 归零即恢复。"""
    from wsim.sim.combat_rules import _Actor, attack_skill

    state, ent = _state({"sword": 1, "cloth": 1})
    assert _Actor(ent, set()).apply_attack == 0
    assert _Actor(ent, set()).apply_armor == 0
    assert attack_skill(ent) == "unarmed"

    apply_effects(
        state,
        [
            Effect("equip", {
                "entity": ent.id, "slot": "weapon", "item": "sword",
                "apply": {"apply/attack": 40, "apply/damage": 20},
                "old_apply": {}, "weapon_type": "sword",
            }),
            Effect("equip", {
                "entity": ent.id, "slot": "cloth", "item": "cloth",
                "apply": {"apply/armor": 5}, "old_apply": {}, "weapon_type": "",
            }),
        ],
    )
    actor = _Actor(ent, set())
    assert actor.apply_attack == 40
    assert actor.apply_armor == 5
    assert actor.weapon is True
    assert attack_skill(ent) == "sword"

    apply_effects(
        state,
        [
            Effect("unequip", {"entity": ent.id, "slot": "weapon", "apply": {"apply/attack": 40, "apply/damage": 20}}),
            Effect("unequip", {"entity": ent.id, "slot": "cloth", "apply": {"apply/armor": 5}}),
        ],
    )
    restored = _Actor(ent, set())
    assert restored.apply_attack == 0
    assert restored.apply_armor == 0
    assert restored.weapon is False
    assert attack_skill(ent) == "unarmed"
    assert ent.equipped_apply == {}
    assert ent.weapon_type == ""
