"""房间 reset / NPC 重生（P5）。

原版语义（`inherit/room/room.lpc::reset()` + 驱动每 5 分钟扫描）：

- 房间 `set("objects")` 的槽位按 `count` 补齐；已被 destruct 的 NPC 在下次
  reset 时于原房间重新 `new()` 一个满状态 clone；
- 被玩家拿走的物品只要对象还存在（在背包里）就不补，只有被消耗/销毁才补；
- living 且未在战斗中的游走 NPC 被 `return_home()` 拉回，昏迷/战斗中的不拉；
  复刻只拉“没有进行中目标”的空闲 NPC（见 effects 内注释）；
- 店主、任务 NPC 没有例外，照常重置。

复刻落地：

- 默认关闭；只有 `config/worlds/xkx.yaml` 的 `room_reset.enabled: true` 打开；
- 周期用 tick 计算：`(tick - phase) % 3 != 2`，即 10/20 分钟交替（均值 15 分钟，
  对应原版 `time to reset : 900` 秒）；phase 由 `PRNG(seed, 0, salt)` 推导，
  不读墙钟、不落状态字段；
- **周期偏差（已知且刻意）**：原版是真实秒级随机 450..899 秒，再叠加驱动 5 分钟
  扫描，实际间隔 10..20 分钟且逐对象独立；复刻的 tick 固定 10 分钟，只能取
  “10/20 分钟交替”这个同均值的粗粒度近似。玩家可感知的后果：一只能在 8 分钟
  内刷新的怪，最快也要等到第 10 分钟的 tick 才可能回来；同一 tick 内到期的
  房间/对象会一起结算，而不是按秒错开。不照抄墙钟秒数的原因是整套引擎必须
  可在回放/双跑中逐字节复现，墙钟会让同 seed 产生不同事件流。
- 重生复用原实体 id（槽位身份），`template`/`instance` 不变，不新建实体；
- 死亡时间/地点移入 `last_death_tick` / `last_death_location` 留审计线索；
  `entity_respawn` 事件本身也带上一轮的 `death_tick`/`death_location`。
- 空转不写事件：只有确实需要恢复时才提交 `system_room_reset`。

商店补货：原版 `F_DEALER::do_buy()` 对货架商品是 `ob = new(ob_file)`（每件现做），
**没有库存字段、也没有补货周期**，所以原版不存在“把铁匠铺买空”。只有玩家寄售
（`from == "pawn"`）才受实际对象数量限制。复刻的 NPC 货架沿用引擎的
`business.stock`（xkx 内容把 `business_stock` 定为 30），因此必须在 reset 时把
NPC 商业体补回 30，否则会把“原版无限”误实现成“30 件卖完永久缺货”。玩家自营
店铺（`owner` 非空）仍走付费 `restock`，不自动补货。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..actions.equipment import item_apply, slot_for_item
from ..actions.registry import Action
from ..core.models import Effect, Intent
from ..core.rng import PRNG
from ..world.state import Entity, WorldState
from .genesis import content_role_skill, npc_relation_maps, npc_spawn_skill
from .vitals import resolve_npc_vitals

# 原版 `randomized resets` 默认 1：450..899 秒；tick=10 分钟，用 10/20 交替
# 近似 1.5 tick（15 分钟）。
RESET_PERIOD_TICKS = 3

# return_home 节流：同一 NPC 连续这么多 tick 没有动作（走路/工作/交易等）才允许
# 拉回。原版 `npc.lpc::return_home()` 不看空闲时长；复刻的 NPC 是 tick 级多步寻路，
# 无条件拉回会把人从半路拽回（实测当日 move 335→6、交易/工作归零）。用已有的
# `last_action_tick` 做无状态节流：正在动的 NPC 每 tick 都会刷新它，不会被拉；
# 目标结束、真正闲下来后，最多再等 2 tick + 房间周期（≤3 tick）就能归位。
RETURN_HOME_IDLE_TICKS = 2


@dataclass(frozen=True)
class RoomResetPolicy:
    content: Any = None
    seed: int = 0
    enabled: bool = False
    # `combat.jing`：重生基线是否带精神字段（默认关闭，关闭态载荷逐字节不变）。
    jing: bool = False

    def _phase(self, room: str) -> int:
        return PRNG(int(self.seed), 0, f"room_reset_phase:{room}").randint(0, RESET_PERIOD_TICKS - 1)

    def due(self, room: str, tick: int) -> bool:
        return (int(tick) - self._phase(room)) % RESET_PERIOD_TICKS != RESET_PERIOD_TICKS - 1

    # ---- 基线数据 ---------------------------------------------------------

    def _npc(self, ref: str) -> dict[str, Any]:
        for row in getattr(self.content, "npcs", []) or []:
            if str(row.get("id")) == ref:
                return row
        return {}

    def _baseline(self, ent: Entity, ref: str) -> dict[str, Any]:
        """按内容重算创世基线；字段集与 `genesis.build_genesis` 的 create_entity 一致。

        重生等价于原版 `destruct()` + 重新 `new()`：新 clone 的字段集必须和创世
        时同一实体完全相同。缺失字段（尤其 `drops`）会让重生后的 NPC 掉皮消失、
        性格/技能漂移，因此这里逐字段镜像 genesis.py:213-329。
        `max_stamina` / `skill_xp` 不在 env payload 里：它们由 `entity_respawn`
        按 create_entity 的默认值复位（100.0 / {}）。
        """
        npc = self._npc(ref)
        errors: list[str] = []
        # 原版每次 new() 现掷：重生用死亡 tick 作为确定性 spawn 序号，既重掷又
        # 可回放（death_tick 已在事件日志里）。
        roll_key = f"{ent.id}#{int(ent.death_tick)}" if int(ent.death_tick) > 0 else ent.id
        vitals = resolve_npc_vitals(npc, seed=int(self.seed), errors=errors, roll_key=roll_key,
                                    jing=bool(self.jing)) or {}
        master_of, disciples = npc_relation_maps(getattr(self.content, "npcs", []) or [])
        role = str(npc.get("role") or ent.role)
        payload: dict[str, Any] = {
            "entity": ent.id,
            "name": str(npc.get("name") or ent.name),
            "role": role,
            "location": ent.home or ent.location,
            "home": ent.home,
            "stamina": 100.0,
            "skills": {content_role_skill(self.content, role): npc_spawn_skill(npc, master_of, disciples)},
            "personality": dict(npc.get("personality") or {}),
            "death_tick": int(ent.death_tick),
            "death_location": ent.death_location,
        }
        for key in ("max_hp", "hp", "attrs", "combat_exp", "skill_levels", "neili",
                    "max_neili", "jiali", "race", "map_skills", "prepare_skills"):
            if key in vitals:
                payload[key] = vitals[key]
        # P4a：内容显式标注的初始状态（xkx 当前没有该键，保持逐字节不变）。
        initial_state = str(npc.get("initial_state") or "")
        if initial_state and initial_state != "alive":
            payload["status"] = initial_state
        inventory: dict[str, int] = {}
        for row in (npc.get("assets") or {}).get("items") or []:
            ref_item = str((row or {}).get("id") or "")
            qty = int((row or {}).get("qty") or 0)
            if ref_item and qty > 0:
                inventory[ref_item] = inventory.get(ref_item, 0) + qty
        payload["inventory"] = inventory
        equipment: list[str] = []
        equipped: dict[str, str] = {}
        weapon_type = ""
        for row in npc.get("equipment") or []:
            ref_item = str((row or {}).get("ref") or "")
            if not ref_item:
                continue
            equipment.append(ref_item)
            if ref_item not in self.content.items:
                continue
            data = self.content.items.get(ref_item) or {}
            category = str(data.get("category") or "")
            if str((row or {}).get("slot") or "") == "wield" or category == "weapon":
                equipped["weapon"] = ref_item
                weapon_type = str(data.get("equip_slot") or "")
            else:
                slot = slot_for_item(ref_item, category, str(data.get("equip_slot") or ""))
                if slot:
                    equipped[slot] = ref_item
        if equipment:
            payload["equipment"] = equipment
        if equipped:
            payload["equipped"] = dict(sorted(equipped.items()))
            apply: dict[str, int] = {}
            for item in equipped.values():
                for key, value in item_apply(self.content, item).items():
                    apply[key] = apply.get(key, 0) + int(value)
            if apply:
                payload["equipped_apply"] = dict(sorted(apply.items()))
            if weapon_type:
                payload["weapon_type"] = weapon_type
        # 原版 die() 固定掉落：与 genesis.py:284-302 同一过滤条件。
        drops: dict[str, int] = {}
        for row in npc.get("drops") or []:
            if not isinstance(row, dict):
                continue
            if row.get("chance") != 1.0 or row.get("item_resolved") is not True:
                continue
            if str(row.get("landing") or "") != "ground":
                continue
            ref_item = str(row.get("item") or "")
            if not ref_item:
                continue
            qty = row.get("qty")
            if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
                qty = 1
            drops[ref_item] = drops.get(ref_item, 0) + int(qty)
        if drops:
            payload["drops"] = dict(sorted(drops.items()))
        temp_apply: dict[str, int] = {}
        for row in npc.get("temp_apply") or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "")
            if not key or row.get("known") is not True:
                continue
            if row.get("source_kind") == "lpc_set":
                continue
            value = row.get("value")
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            temp_apply[key] = int(value)
        if temp_apply:
            payload["temp_apply"] = dict(sorted(temp_apply.items()))
        if ent.template:
            payload["template"] = ent.template
            payload["instance"] = int(ent.instance)
        return payload

    # ---- 扫描 -------------------------------------------------------------

    def _slots(self, state: WorldState, room: str, ref: str) -> list[Entity]:
        rows = [
            ent for ent in state.entities.values()
            if ent.home == room
            and not ent.id.startswith("player:")
            and (ent.template == ref or (not ent.template and ent.id == ref))
        ]
        rows.sort(key=lambda e: (int(e.instance), e.id))
        return rows

    def _vendor_deficit(self, state: WorldState, room: str, service: str) -> int:
        stock = int((getattr(self.content, "economy", {}) or {}).get("business_stock", 0) or 0)
        if stock <= 0 or service not in getattr(self.content, "items", {}):
            return 0
        bid = f"biz:{room}:{service}"
        biz = state.businesses.get(bid)
        if biz is None or biz.owner:
            # 玩家店铺只走付费进货；NPC 货架才补。
            return 0
        return max(0, stock - int(biz.stock.get(service, 0)))

    def effects(self, state: WorldState, tick: int) -> list[Effect]:
        if not self.enabled or tick <= 0:
            return []
        out: list[Effect] = []
        item_presence: dict[str, int] | None = None

        def present(item: str, room: str) -> int:
            nonlocal item_presence
            if item_presence is None:
                item_presence = {}
                for ent in state.entities.values():
                    for key, qty in ent.inventory.items():
                        item_presence[key] = item_presence.get(key, 0) + int(qty)
                    for key in ent.equipped.values():
                        item_presence[key] = item_presence.get(key, 0) + 1
                for other, bucket in state.ground.items():
                    for key, qty in bucket.items():
                        item_presence[key] = item_presence.get(key, 0) + int(qty)
            total = int(item_presence.get(item, 0))
            total -= int((state.ground.get(room) or {}).get(item, 0))
            return total

        for room in sorted(getattr(self.content, "locations", {}) or {}):
            if not self.due(room, tick):
                continue
            rows = sorted(
                (self.content.locations.get(room) or {}).get("objects") or [],
                key=lambda o: (str(o.get("kind") or ""), str(o.get("ref") or "")),
            )
            for obj in rows:
                kind = str(obj.get("kind") or "")
                ref = str(obj.get("ref") or "")
                count = obj.get("count")
                if kind == "npc":
                    if not isinstance(count, int) or count <= 0 or not ref:
                        continue
                    for ent in self._slots(state, room, ref)[:count]:
                        if ent.status == "dead":
                            out.append(Effect("entity_respawn", self._baseline(ent, ref)))
                        elif (
                            ent.status == "alive"
                            and ent.location != room
                            # 原版 return_home() 只拉“游走”的 NPC。复刻里 NPC 的
                            # 跨房移动是 tick 级多步寻路（goal_id 非空），而 reset
                            # 每 1–2 tick 一次；无条件拉回会让所有 NPC 永远走不到
                            # 目的地（实测当日 move 335→6、交易/工作归零）。因此
                            # 只对空闲 NPC 做 return_home，并且要求连续
                            # RETURN_HOME_IDLE_TICKS tick 没有动作，避免把正在赶路/
                            # 干活的 NPC 从半路拽回；目标结束后仍会在有限 tick 内归位。
                            and not ent.goal_id
                            and tick - int(ent.last_action_tick) >= RETURN_HOME_IDLE_TICKS
                        ):
                            out.append(Effect("set_location", {"entity": ent.id, "to": room}))
                    continue
                if kind == "item":
                    if not isinstance(count, int) or count <= 0 or not ref:
                        continue
                    if int((state.ground.get(room) or {}).get(ref, 0)) > 0:
                        continue
                    if present(ref, room) > 0:
                        # 原版：对象还在任意背包/地面就不补；只有被消耗掉才补。
                        continue
                    out.append(
                        Effect(
                            "loot_create",
                            {"location": room, "item": ref, "qty": count, "reason": "room_reset"},
                        )
                    )
            services = sorted(set((self.content.locations.get(room) or {}).get("services") or []))
            for service in services:
                deficit = self._vendor_deficit(state, room, service)
                if deficit > 0:
                    out.append(
                        Effect(
                            "item_create",
                            {
                                "owner": f"biz:{room}:{service}",
                                "item": service,
                                "qty": deficit,
                                "reason": "vendor_restock",
                            },
                        )
                    )
        return out

    def has_work(self, state: WorldState, tick: int) -> bool:
        return bool(self.effects(state, tick))


class SystemRoomResetAction(Action):
    verb = "system_room_reset"

    def __init__(self, policy: RoomResetPolicy):
        self.policy = policy

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return self.policy.effects(state, int(intent.tick))
