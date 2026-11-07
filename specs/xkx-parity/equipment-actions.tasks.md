# 装备/物品动作层 + 种族掷骰 + 区域透出 — 执行任务

对应规格：[equipment-actions.spec.md](./equipment-actions.spec.md)
基线：`xkx-parity` @ `70208d5`

## T1 装备 apply 消费（不抽取）

- [x] `equipment.py::item_apply` 只读 `items.json` 的 `apply`；没有就返回 `{}`。
- [x] 有 `apply`：`equip` 累加、`unequip` 扣减；无 `apply`：可穿但不加成。
- [x] 单测两种物品各一条，断言不编造默认值。
- [x] 不改 `tools/xkx_extract/*`、不改 `items.json` 的数值。

## T2 状态与 Effect

- [x] `Entity` 增加 `equipped_apply`、`weapon_type`，仅非空进 `to_dict()`。
- [x] `effects.py` 的 `equip/unequip` 支持 `apply`、`old_apply`、`weapon_type`。
- [x] 单测：同槽替换扣旧加新；unequip 归零；旧实体字段为空且不进哈希。

## T3 装备动作

- [x] `slot_for_item(item, category, equip_slot)`：weapon→`weapon`，
      armor→`equip_slot`，tool/accessory 兼容槽。
- [x] `WearAction`、`WieldAction`、`RemoveAction`、`UnwieldAction`；
      `EquipAction`/`UnequipAction` 保留兼容分派。
- [x] 原因码：`item_not_wearable`、`item_not_weapon`、`slot_occupied:<slot>`、
      `weapon_already_wielded`、`item_not_equipped`、`not_worn_use_unwield`、`not_wielded`。
- [x] `registry.py` 注册；`session.py` 加 verb/payload/`PLAYER_VERBS`；
      `_equipment_view` 暴露 `weapon_type`、`applied`。
- [x] xkx 文字壳补 `wear/wield/remove/unwield/drop/rest/sleep` 及中文别名。

## T4 负重

- [x] `common.py` 增加 `max_encumbrance`、`carried_weight`、`overweight_reason`。
- [x] `LootAction`、`BuyAction`、`TradeAction` 前置检查；
      拒绝码 `overloaded:<need>:<max>`；状态不变。
- [x] `session.py` 支持 `overloaded:` 前缀文案；`_weight_view` 透出真实上限。
- [x] 旧内容（无 attrs）不启用负重。

## T5 drop

- [x] `loot.py` 新增 `DropAction`（verb `drop`，qty 1..20）。
- [x] 拒绝：`item_missing`、`bad_qty`、`item_equipped`、`player_dead`。
- [x] Effect：`item_consume` + `loot_create(reason="drop")`，共用 `WorldState.ground`。
- [x] 单测：drop→loot 闭环；真实世界命令闭环已验证。

## T6 rest / sleep

- [x] `PlayerRestAction` 加 `aliases=("sleep",)` 并注册。
- [x] `rest`：原恢复率 + `hp_add`（仅 `max_hp>0`）。
- [x] `sleep`：回满 `hp`/`stamina`；耗时 `hours*TICKS_PER_HOUR`。
- [x] `session.py` 加 `sleep` 分支与耗时；默认 4、范围 1..8。
- [x] 单测：注册表含 rest/sleep；sleep 回满；`duration_ticks` 正确。

## T7 战斗接线（不改公式）

- [x] `_Actor` 合并 `temp_apply + equipped_apply`。
- [x] `attack_skill` 持兵器返回 `weapon_type`。
- [x] 单测：持剑 `_Actor.temp_apply["apply/damage"]` 等于内容值、skill=`sword`；
      `apply/armor` 进入 `defense + apply_armor`；脱剑恢复。
- [x] 不改 `tools/xkx_formula/*`。

## T8 attrs_roll 掷骰

- [x] `vitals.py` 增加 `roll_attrs(npc_id, attrs_roll, seed, errors)`：
      SHA-256 逐属性掷点，格式见规格 2.2。
- [x] `resolve_npc_vitals(npc, *, seed=0, errors=None)`：
      门控加入 `attrs_roll`；掷点覆盖静态 `attrs` 同键。
- [x] `build_genesis(..., seed=0)`；`session/headless/scheduler` 显式传 seed。
- [x] 诊断：`bad_attrs_roll:<npc>:<key>`；非法项不生成数值。
- [x] 单测：同 seed 两次完全一致、值在区间内、不同 seed 有变化、
      legacy NPC 不受影响。

## T9 region 透出

- [x] `xkx_content.py` 的 `content.locations[rid]` 复制 `region`/`region_name`。
- [x] `session.py::_location_view` 返回 `region`/`region_name`；
      state 的 `player` 对象同字段；缺失为 `null`。
- [x] 单测：字段透出与 null 兜底；`world_map` 不改逻辑。

## T10 验证与提交

- [x] `PYTHONPATH=src python -m pytest -q` 全绿：`921 passed, 18 skipped`。
- [x] `PYTHONPATH=src python -m pytest -q tests/test_xkx_determinism_legacy.py`：
      `735` / `009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9` /
      `02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`，1 passed。
- [x] 真实世界命令（内容无长剑/铁甲可买，改用地面大石头与店小二掉落的布衣）：
      enter → loot/wield 大石头（weapon_type=hammer）→ fight 店小二 → unwield →
      loot/wear 布衣 → remove → drop 布衣 → loot 布衣 → rest 2 → sleep 4。
- [x] 负重超限：真实内容单件最大 30000 < 玩家上限 104000，无法用真实命令构造；
      由动作层单测覆盖 `overloaded:<need>:<max>`（`test_equipment_actions.py`）。
- [x] `attrs_roll`：同 seed 两次创世一致；10 个种族 NPC 有掷点；换 seed 至少一项变化。
- [x] region：state/look 透出 `region`/`region_name`；缺失如实 null。
- [x] 更新 `specs/xkx-parity/combat-implementation.md`。
- [x] 显式路径 `git add`、提交、`git push origin xkx-parity`（不 force）。

## 验收门槛

- 旧九地点世界哈希逐字节不变。
- 四个动作 + 负重 + drop + rest/sleep 都有真实命令与原始响应。
- 有 `apply` 就加成、没有就不加成；不编造任何装备数值。
- 同 seed 创世属性逐字节一致；区域缺失如实为 null。
- 装备加成只进入现有战斗入参，公式文件零改动。
