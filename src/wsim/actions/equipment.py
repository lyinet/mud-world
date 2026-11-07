"""装备与物品使用（玩家动作）。

装备槽位与已装备物品写进 `Entity.equipped`（Effect `equip/unequip`），
因此属于世界状态、可回放；APC/NPC 不使用这些 verb，无玩家世界哈希不受影响。

槽位口径（`content/xkx/items.json`）：
- 护具用 `equip_slot`（cloth/head/neck/armor/surcoat/waist/wrists/shield/finger/hands/boots）；
- 兵器统一进 `weapon` 槽（原版单手），`equip_slot` 作为兵器技能类型（sword/blade/...）。
- 物品没有 `apply` 时仍可穿/持，但不产生战斗加成；不补默认值。
"""
from __future__ import annotations

from typing import Any

from ..core.models import Effect, Intent
from ..world.state import WorldState
from .common import memory, require_entity
from .registry import Action

# 内容包没有显式槽位字段时的确定性归类：工具进 tool 槽，三件首饰进 accessory 槽。
ACCESSORY_ITEMS = frozenset({"silver_hairpin", "jade_pendant", "pearl_earring"})

ARMOR_SLOTS = frozenset(
    {
        "cloth",
        "head",
        "neck",
        "armor",
        "surcoat",
        "waist",
        "wrists",
        "shield",
        "finger",
        "hands",
        "boots",
    }
)

SLOT_LABELS = {
    "cloth": "衣衫",
    "head": "头部",
    "neck": "颈部",
    "armor": "护甲",
    "surcoat": "外袍",
    "waist": "腰带",
    "wrists": "护腕",
    "shield": "盾牌",
    "finger": "手指",
    "hands": "手部",
    "boots": "鞋履",
    "weapon": "兵器",
    "tool": "工具",
    "accessory": "佩饰",
}


def _item_data(content, item: str) -> dict:
    return (getattr(content, "items", {}) or {}).get(item) or {}


def item_apply(content, item: str) -> dict[str, int]:
    """物品的 `apply` 加成；缺失/非法键返回空，不补默认值。"""
    raw = _item_data(content, item).get("apply")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.startswith("apply/"):
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        result[key] = int(value)
    return result


def item_weight(content, item: str) -> int:
    value = _item_data(content, item).get("weight")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return int(value)


def slot_for_item(item: str, category: str, equip_slot: str = "") -> str:
    if category == "weapon":
        return "weapon"
    if category == "armor":
        return equip_slot if equip_slot in ARMOR_SLOTS else ""
    if category == "tool":
        return "tool"
    if category == "accessory" or item in ACCESSORY_ITEMS:
        return "accessory"
    return ""


def item_is_equippable(item: str, category: str, equip_slot: str = "") -> bool:
    return bool(slot_for_item(item, category, equip_slot))


def equip_cultivate_bonus(content, ent) -> float:
    """已装备工具有 25% 修炼熟练度加成；无装备为 0。"""
    for item in ent.equipped.values():
        if str((content.items.get(item) or {}).get("category", "")) == "tool":
            return 0.25
    return 0.0


def _resolve_item(content, intent: Intent) -> tuple[str, dict, str, str]:
    item = str(intent.args.get("item") or "")
    data = _item_data(content, item) if item else {}
    category = str(data.get("category") or "")
    equip_slot = str(data.get("equip_slot") or "")
    return item, data, category, equip_slot


def _item_name(content, item: str) -> str:
    return str(_item_data(content, item).get("name") or item)


class _EquipBase(Action):
    def __init__(self, content):
        self.content = content

    def _wear_preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        item, _data, category, equip_slot = _resolve_item(self.content, intent)
        if not item or item not in getattr(self.content, "items", {}):
            return ["item_not_available"]
        slot = slot_for_item(item, category, equip_slot)
        if category != "armor" or not slot:
            return ["item_not_wearable"]
        if int(ent.inventory.get(item, 0)) < 1:
            return ["item_missing"]
        if slot in ent.equipped:
            return [f"slot_occupied:{slot}"]
        return []

    def _wield_preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        item, _data, category, _equip_slot = _resolve_item(self.content, intent)
        if not item or item not in getattr(self.content, "items", {}):
            return ["item_not_available"]
        if category != "weapon" or not slot_for_item(item, category):
            return ["item_not_weapon"]
        if int(ent.inventory.get(item, 0)) < 1:
            return ["item_missing"]
        if "weapon" in ent.equipped:
            return ["weapon_already_wielded"]
        return []

    def _armor_effects(self, state: WorldState, intent: Intent) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        item, _data, category, equip_slot = _resolve_item(self.content, intent)
        slot = slot_for_item(item, category, equip_slot)
        old = ent.equipped.get(slot)
        old_apply = item_apply(self.content, old) if old else {}
        effects = [
            Effect(
                "equip",
                {
                    "entity": ent.id,
                    "slot": slot,
                    "item": item,
                    "apply": item_apply(self.content, item),
                    "old_apply": old_apply,
                    "weapon_type": "",
                },
            )
        ]
        effects.extend(memory(state, ent.id, intent.tick, f"穿上了{_item_name(self.content, item)}", 0.15))
        return effects

    def _weapon_effects(self, state: WorldState, intent: Intent) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        item, _data, category, equip_slot = _resolve_item(self.content, intent)
        effects = [
            Effect(
                "equip",
                {
                    "entity": ent.id,
                    "slot": "weapon",
                    "item": item,
                    "apply": item_apply(self.content, item),
                    "old_apply": {},
                    "weapon_type": equip_slot or category,
                },
            )
        ]
        effects.extend(memory(state, ent.id, intent.tick, f"持起了{_item_name(self.content, item)}", 0.15))
        return effects

    def _find_worn_slot(self, ent, intent: Intent) -> str:
        slot = str(intent.args.get("slot") or "")
        if slot:
            return slot
        item = str(intent.args.get("item") or "")
        if not item:
            return ""
        for worn_slot, worn_item in sorted(ent.equipped.items()):
            if worn_item == item:
                return worn_slot
        return ""

    def _unequip_effects(self, state: WorldState, intent: Intent, slot: str) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        item = ent.equipped.get(slot, "")
        effects = [
            Effect(
                "unequip",
                {
                    "entity": ent.id,
                    "slot": slot,
                    "apply": item_apply(self.content, item),
                },
            )
        ]
        verb = "放下了" if slot == "weapon" else "脱下了"
        effects.extend(memory(state, ent.id, intent.tick, f"{verb}{_item_name(self.content, item)}", 0.1))
        return effects

    def _remove_preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        slot = self._find_worn_slot(ent, intent)
        if not slot:
            return ["item_not_equipped"]
        if slot == "weapon":
            return ["not_worn_use_unwield"]
        if slot not in ent.equipped:
            return ["item_not_equipped"]
        return []

    def _unwield_preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        if "weapon" not in ent.equipped:
            return ["not_wielded"]
        return []


class WearAction(_EquipBase):
    verb = "wear"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        return self._wear_preconditions(state, intent)

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return self._armor_effects(state, intent)


class WieldAction(_EquipBase):
    verb = "wield"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        return self._wield_preconditions(state, intent)

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return self._weapon_effects(state, intent)


class RemoveAction(_EquipBase):
    verb = "remove"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        return self._remove_preconditions(state, intent)

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        return self._unequip_effects(state, intent, self._find_worn_slot(ent, intent))


class UnwieldAction(_EquipBase):
    verb = "unwield"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        return self._unwield_preconditions(state, intent)

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return self._unequip_effects(state, intent, "weapon")


class EquipAction(_EquipBase):
    """兼容旧 verb：按物品类型分派到 wear / wield / 工具首饰槽。"""

    verb = "equip"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        _item, _data, category, _equip_slot = _resolve_item(self.content, intent)
        if category == "weapon":
            return self._wield_preconditions(state, intent)
        if category == "armor":
            return self._wear_preconditions(state, intent)
        ent = require_entity(state, intent.actor)
        item, _data, category, equip_slot = _resolve_item(self.content, intent)
        if not item or item not in getattr(self.content, "items", {}):
            return ["item_not_available"]
        slot = slot_for_item(item, category, equip_slot)
        if not slot:
            return ["item_not_equippable"]
        if int(ent.inventory.get(item, 0)) < 1:
            return ["item_missing"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        _item, _data, category, equip_slot = _resolve_item(self.content, intent)
        if category == "weapon":
            return self._weapon_effects(state, intent)
        if category == "armor":
            return self._armor_effects(state, intent)
        ent = require_entity(state, intent.actor)
        item, _data, category, equip_slot = _resolve_item(self.content, intent)
        slot = slot_for_item(item, category, equip_slot)
        effects = [
            Effect(
                "equip",
                {
                    "entity": ent.id,
                    "slot": slot,
                    "item": item,
                    "apply": item_apply(self.content, item),
                    "old_apply": item_apply(self.content, ent.equipped.get(slot, "")),
                    "weapon_type": "",
                },
            )
        ]
        effects.extend(memory(state, ent.id, intent.tick, f"装备了{_item_name(self.content, item)}", 0.15))
        return effects


class UnequipAction(_EquipBase):
    """兼容旧 verb：按当前槽位分派到 remove / unwield。"""

    verb = "unequip"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        slot = self._find_worn_slot(ent, intent)
        if not slot or slot not in ent.equipped:
            return ["equipment_slot_empty"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        slot = self._find_worn_slot(ent, intent)
        return self._unequip_effects(state, intent, slot)


class UseItemAction(Action):
    verb = "use_item"

    def __init__(self, content):
        self.content = content

    def _consume_info(self, intent: Intent):
        item = str(intent.args.get("item") or "")
        data = self.content.items.get(item) if item else None
        category = str((data or {}).get("category", ""))
        return item, category

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        item, category = self._consume_info(intent)
        if not item or item not in self.content.items:
            return ["item_not_available"]
        if category not in ("food", "drink", "medicine"):
            return ["item_not_usable"]
        qty = int(intent.args.get("qty") or 1)
        if qty < 1 or qty > 10:
            return ["bad_quantity"]
        if int(ent.inventory.get(item, 0)) < qty:
            return ["item_missing"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        item, category = self._consume_info(intent)
        qty = int(intent.args.get("qty") or 1)
        if category in ("food", "drink"):
            boost = float(self.content.economy.get("food_stamina", 25)) * qty
        else:
            boost = float(self.content.economy.get("medicine_stamina", 25)) * qty
        name = str((self.content.items.get(item) or {}).get("name") or item)
        return [
            Effect("item_consume", {"owner": ent.id, "item": item, "qty": qty}),
            Effect("stamina", {"entity": ent.id, "delta": boost}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": intent.tick}),
            *memory(state, ent.id, intent.tick, f"使用了{name}x{qty}", 0.1),
        ]
