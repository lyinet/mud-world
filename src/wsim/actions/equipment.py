"""装备与物品使用（玩家动作）。

装备槽位与已装备物品写进 `Entity.equipped`（Effect `equip/unequip`），
因此属于世界状态、可回放；APC/NPC 不使用这些 verb，无玩家世界哈希不受影响。
"""
from __future__ import annotations

from ..core.models import Effect, Intent
from ..world.state import WorldState
from .common import memory, require_entity
from .registry import Action

# 内容包没有显式槽位字段时的确定性归类：工具进 tool 槽，三件首饰进 accessory 槽。
ACCESSORY_ITEMS = frozenset({"silver_hairpin", "jade_pendant", "pearl_earring"})
SLOT_FOR_CATEGORY = {"tool": "tool"}
SLOT_LABELS = {"tool": "工具", "accessory": "佩饰"}


def slot_for_item(item: str, category: str) -> str:
    if category == "tool":
        return "tool"
    if item in ACCESSORY_ITEMS:
        return "accessory"
    return ""


def item_is_equippable(item: str, category: str) -> bool:
    return bool(slot_for_item(item, category))


def equip_cultivate_bonus(content, ent) -> float:
    """已装备工具有 25% 修炼熟练度加成；无装备为 0。"""
    for item in ent.equipped.values():
        if str((content.items.get(item) or {}).get("category", "")) == "tool":
            return 0.25
    return 0.0


class EquipAction(Action):
    verb = "equip"

    def __init__(self, content):
        self.content = content

    def _resolve(self, intent: Intent):
        item = str(intent.args.get("item") or "")
        data = self.content.items.get(item) if item else None
        category = str((data or {}).get("category", ""))
        return item, category, slot_for_item(item, category)

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        item, category, slot = self._resolve(intent)
        if not item or item not in self.content.items:
            return ["item_not_available"]
        if not slot:
            return ["item_not_equippable"]
        if int(ent.inventory.get(item, 0)) < 1:
            return ["item_missing"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        item, _category, slot = self._resolve(intent)
        effects = [Effect("equip", {"entity": ent.id, "slot": slot, "item": item})]
        name = str((self.content.items.get(item) or {}).get("name") or item)
        effects.extend(memory(state, ent.id, intent.tick, f"装备了{name}", 0.15))
        return effects


class UnequipAction(Action):
    verb = "unequip"

    def __init__(self, content):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        slot = str(intent.args.get("slot") or "")
        if not slot or slot not in SLOT_LABELS:
            return ["unknown_equipment_slot"]
        if slot not in ent.equipped:
            return ["equipment_slot_empty"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        slot = str(intent.args.get("slot") or "")
        effects = [Effect("unequip", {"entity": ent.id, "slot": slot})]
        effects.extend(memory(state, ent.id, intent.tick, "卸下了装备", 0.1))
        return effects


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
