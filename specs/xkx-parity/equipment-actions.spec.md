# 装备/物品动作层 + 种族属性掷骰 + 区域透出

状态：待批准（Spec Mode；本文件之外暂不改代码）
基线：`xkx-parity` @ `70208d5`
硬约束：旧九地点世界哈希逐字节不变（735 / `009076f0…` / `02d5f812…`）；同 seed 可复现；不改战斗公式与玩家默认值；不碰其它线未提交文件。

## 0. 已确认的前提

1. `equip_slot` 在 `content/xkx/items.json` 上，不在 `skills_stats.json`。按 `items.json` 设计。
2. `armor_prop/weapon_prop → apply` 的抽取由内容线完成。引擎只消费：
   - `items.json` 有 `apply` → 按该值加成；
   - 没有 `apply` → 不加成、不报错、不编造数值。
   本任务不改 `tools/xkx_extract/*`，不改 `items.json` 的加成数值。
3. 内容线后续会落 `attrs_roll`（10 个种族 NPC）与 `rooms.json` 的 `region`/`region_name`。
   本任务按“字段存在就消费、缺失就走旧行为/返回 null”实现，不等数据。

## 1. 源码依据

- `feature/equip.lpc:6-39`（wear）：`armor_type()` 决定槽位，同一 `armor_type`
  只能穿一件；把 `armor_prop` 的每个键 `add_temp("apply/" + 键, 值)`。
- `feature/equip.lpc:41-114`（wield）：手持槽唯一；把 `weapon_prop` 的每个键
  `add_temp("apply/" + 键, 值)`；武器技能由 `skill_type()` 决定。
- `feature/equip.lpc:116-160`（unequip）：`add_temp("apply/" + 键, -值)`，脱下恢复。
- `adm/daemons/combatd.lpc:205-242`：`apply/attack` / `apply/defense` 加进
  `skill_power` 的 level，再 800 封顶（现有实现已接）。
- `adm/daemons/combatd.lpc:268-274`：持兵器用武器技能，否则 `unarmed`。
- `adm/daemons/combatd.lpc:436-440`：持兵器基础伤害取 `apply/damage`。
- `adm/daemons/combatd.lpc:451-456 / 560-565`：`apply/armor` 是护甲减伤值。
- `adm/daemons/chard.lpc:125-127` + `feature/attribute.lpc:10-29`：
  `max_encumbrance = base_str*5000 + (query_str-base_str)*1000`。
- `cmds/std/drop.lpc`：物品从携带者背包移到当前环境。
- `cmds/std/sleep.lpc`：睡醒后 `qi=eff_qi`、`jing=eff_jing`、`tili=max_tili`。
- `race/beast.lpc:62-67`：种族六维掷骰区间的出处（内容线已抽成 `attrs_roll`）。

## 2. 内容消费

### 2.1 装备 `apply`（只读消费，不抽取）

`content/xkx/items.json` 每条物品可选：

```json
"apply": {"apply/armor": 50},
"apply_source": {"apply/armor": {"path": "...", "line": 12}}
```

引擎只看 `apply`，值为整数；`apply_source` 不参与运行。没有 `apply` 的物品不可穿/持
（动作返回原因码），不做默认加成。

### 2.2 `attrs_roll`（创世消费）

接受每 NPC 顶层字段：

```json
"attrs_roll": {"str": [5, 44], "con": [5, 44], "dex": [5, 44],
                "int": [5, 44], "spi": [5, 44], "sta": [5, 44]}
```

规则：

- 只处理已知道具属性键 `str/con/dex/int/spi/sta/kar/cps/cor`；值必须是
  `[lo, hi]` 或 `(lo, hi)`，`lo <= hi`。
- 未知键或非二元组忽略；已知键格式非法写诊断
  `bad_attrs_roll:<npc_id>:<key>`，不猜值。
- **世界种子掷点，不用墙钟/全局随机**。逐 NPC、逐属性独立：

```python
span = hi - lo + 1
digest = sha256(f"xkx-attrs:{seed}:{npc_id}:{attr}".encode()).digest()
value = lo + int.from_bytes(digest[:8], "big") % span
```

  SHA-256 路径保证：同 seed、同 NPC、同属性，跨进程/跨机器结果一致，不依赖
  NPC 遍历顺序或 `random` 全局状态。
- `attrs_roll` 生成值覆盖同键静态 `attrs`；没有掷骰的键保留静态 `attrs`。
- 只有 `attrs_roll` 而没有 `vitals/attrs/skills` 的 NPC，也视为战斗 NPC：
  `resolve_npc_vitals` 的门控增加 `attrs_roll`，`max_hp` 仍走既有 500/300/200 规则。
- `build_genesis` 增加关键字参数 `seed: int = 0`；三个真实入口显式传入：
  - `src/wsim/api/session.py` → `seed=seed`
  - `src/wsim/sim/headless.py` → `seed=cfg.seed`
  - `src/wsim/sim/scheduler.py` → `seed=cfg.seed`
  其余测试/工具不传时默认 0；旧内容没有 `attrs_roll`，效果逐字节不变。
- 诊断写入 `GenesisResult.mismatches`，沿用现有格式。

### 2.3 `region`

`content/xkx/rooms.json` 每房间可选 `region` / `region_name`。改动
`src/wsim/api/xkx_content.py` 的 `content.locations[rid]` 组装，原样复制：

```python
"region": raw.get("region"),
"region_name": raw.get("region_name"),
```

不补默认值；缺失就是 `None`（JSON null）。

## 3. 状态模型

### 3.1 `src/wsim/world/state.py::Entity`

新增两个字段，只在非空时进入 `to_dict()`（旧世界没有装备事件，哈希不变）：

```python
equipped_apply: dict[str, int] = field(default_factory=dict)
weapon_type: str = ""
```

- `equipped` 仍是 `槽位 -> 物品 id`；
- 武器槽固定 `"weapon"`（原版单手槽），`weapon_type` 存 `equip_slot`（sword/blade/...）；
- 护具槽直接用 `equip_slot`。

### 3.2 `src/wsim/world/effects.py`

扩展现有 `equip` / `unequip`，不新增事件类型：

- `equip` data 增加 `apply`（新物品）、`old_apply`（被替换物品）、`weapon_type`；
- `unequip` data 增加 `apply`（被脱物品）；
- 落地时累加/扣减 `Entity.equipped_apply`；`slot == "weapon"` 时写/清
  `Entity.weapon_type`；
- 物品在 `inventory` ↔ `equipped` 之间搬移（沿用现有实现）；重量统计两处都算；
- `apply` 只接受 `apply/` 前缀的整数键，非法项忽略并保留诊断（不猜）。

## 4. 装备动作

### 4.1 `src/wsim/actions/equipment.py`

常量与函数：

```python
ARMOR_SLOTS = frozenset({"cloth","head","neck","armor","surcoat","waist",
                         "wrists","shield","finger","hands","boots"})
SLOT_LABELS = {..., "weapon": "兵器", "tool": "工具", "accessory": "佩饰"}

def slot_for_item(item, category, equip_slot=""):
    # weapon -> "weapon"；armor 且 equip_slot 合法 -> equip_slot；
    # tool/accessory -> 兼容槽；其余 -> ""
def item_apply(content, item) -> dict[str, int]
def item_weight(content, item) -> int  # weight 为空按 0
```

动作：

| verb | 行为 | 拒绝码 |
|---|---|---|
| `wear` | 仅护具，槽位=`equip_slot` | `item_not_wearable`、`slot_occupied:<slot>`、`item_missing` |
| `wield` | 仅 `category=weapon` 且 `apply` 非空，槽 `"weapon"` | `item_not_weapon`、`weapon_already_wielded`、`item_missing` |
| `remove` | 仅护具；参数 `item` 或 `slot` | `item_not_equipped`、`not_worn_use_unwield` |
| `unwield` | 仅武器槽 | `not_wielded` |

- `EquipAction.verb="equip"` 保留为兼容分派：武器按 wield、护具按 wear、
  `tool/accessory` 走旧兼容槽；`UnequipAction.verb="unequip"` 同理。
- 每个动作发 `equip`/`unequip` Effect，Effect 自带 `apply`，回放不依赖内容文件。
- 没有 `apply` 的武器/护具：可正常穿/持但不产生战斗加成；用户明确要求
  “没有就不加成”，不把它当错误。

### 4.2 负重

`src/wsim/actions/common.py`：

```python
def max_encumbrance(ent) -> int:
    base = int(ent.attrs.get("str", 0))
    if base <= 0:
        return 0  # 旧内容无属性：不启用，保持旧行为
    improve = max(level // 10 for level in ent.skill_levels.values()) if ... else 0
    return base * 5000 + improve * 1000

def carried_weight(content, ent) -> int
def overweight_reason(content, ent, added_weight) -> str  # "" 或 "overloaded:<need>:<max>"
```

- 重量 = `inventory` 数量×weight + `equipped` 每件 weight（装备已移出背包）。
- 玩家默认 `str=20, unarmed=40` → 上限 `104000`。
- 旧九地点实体没有 `attrs`，上限 0 且不拦动作。
- 接入：`LootAction`、`BuyAction`、`TradeAction` 前置检查；拒绝码
  `overloaded:<need>:<max>`，一次检查全部，不静默部分拿取。
- `drop/sell/remove/unwield` 只减重，不检查。

### 4.3 `drop`

`src/wsim/actions/loot.py` 新增 `DropAction`（与 `loot_create` 共用 `WorldState.ground`）：

- verb `drop`；参数 `item`、可选 `qty`（默认 1，1..20）。
- 拒绝：`item_missing`、`bad_qty`、`item_equipped`、`player_dead`。
- Effect：`item_consume(owner,item,qty)` → `loot_create(location,item,qty,reason="drop")`
  → `set_field(last_action_tick)`；回放只依赖事件参数。

### 4.4 `rest` / `sleep`

`src/wsim/actions/player.py::PlayerRestAction`：

- `verb="rest"`，`aliases=("sleep",)`；注册到 `build_registry`。
- `rest`：保留现有每小时恢复率 + 地点系数；新增 `hp_add`：
  `delta = hours * TICKS_PER_HOUR * rate`，仅 `max_hp > 0`。
- `sleep`（`intent.verb == "sleep"`）：按原版睡醒口径回满 `hp` 与 `stamina`
  （Effect 自行钳制）。
- 耗时：沿用 `WorldSession` 的 `hours * TICKS_PER_HOUR`；`hours` 默认 4、范围 1..8；
  响应保留 `duration_ticks`。
- 差异写文档：原版还回 `jing/neili`，我们没有这两个池，只回 `hp/stamina`。

## 5. 战斗接入（不改公式）

`src/wsim/sim/combat_rules.py`：

1. `_Actor.__init__`：`temp_apply = Entity.temp_apply + Entity.equipped_apply` 逐键相加。
2. `attack_skill(ent, explicit)`：`explicit` 在技能表内 → 用它；否则持兵器且有
   `weapon_type` → 返回 `weapon_type`；否则 `unarmed`。
3. 不改 `tools/xkx_formula/*`、800 封顶、命中/招架/伤害公式。

已知且接受：玩家默认只有 `unarmed=40`，持剑后攻击技能为 `sword`（等级 0），
ap 按源码会下降；装备带来的实际增益是 `apply/damage` 进基础伤害。**不改默认值**。
验证同时给“默认玩家”和“夹具 sword=40”两组数字，说明差异来源。

## 6. API / 玩家可见

`src/wsim/api/session.py`：

- `WRITE_ACTIONS` / `ACTION_ALIASES` / payload 增加 `wear`、`wield`、`remove`、
  `unwield`、`drop`、`sleep`：
  - wear/wield：`{"item": ...}`；remove/unwield：`{"item"|"slot": ...}`；
  - drop：`{"item", "qty"}`；sleep：`{"hours"}`。
- `PLAYER_VERBS` 增加上述 6 个 verb。
- `_item_meta` 传 `equip_slot` 给 `slot_for_item`；`SLOT_LABELS` 用新表。
- `_equipment_view` 增加只读 `weapon_type` 与 `applied`（`equipped_apply`）。
- `REASON_TEXT` 增加静态码；`overloaded:<need>:<max>` 前缀格式化。
- `player` 对象与 `_location_view` 增加只读
  `region` / `region_name`；直接取 `content.locations[loc]`，缺失为 `null`。
  `world_map` 已读这两个字段，内容落地后自动生效。

## 7. 测试与验收

### 7.1 自动测试

- `tests/test_equipment_actions.py`
  - `apply` 有无两种路径：有 `apply` → `equipped_apply` 变化；无 `apply` → 可穿但不加成；
  - wear/wield/remove/unwield 槽位与原因码；
  - `attack_skill` 持剑返回 `sword`；`_Actor.temp_apply` 含 `apply/damage`；
    `apply/armor` 进入 `defense + apply_armor`；
  - drop → `state.ground` → loot 闭环；
  - 负重：`loot/buy` 超限返回 `overloaded:<need>:<max>`，状态不变；
  - rest/sleep：注册表含两 verb；sleep 回满；`duration_ticks` 正确；
  - Writer/Replay 回放 equip/unequip/drop/rest 后字段一致。
- `tests/test_attrs_roll.py`
  - 构造带 `attrs_roll` 的固定 NPC：同 seed 两次 `build_genesis` 的 `attrs` 完全相同；
  - 所有值落在区间内；不同 seed 至少一个属性不同；
  - 非法区间写 `bad_attrs_roll:` 诊断且不生成该键；
  - 无 `attrs_roll` 的 legacy NPC 不受影响。
- `tests/test_region_view.py`
  - `xkx_content` 组装后 `content.locations[id]` 保留 `region/region_name`；
  - `_location_view` 与 state `player` 字段原样透出；缺失为 `None`。
- 现有测试仅因新 verb/新只读字段更新断言，不改默认值断言。

执行：

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m pytest -q tests/test_xkx_determinism_legacy.py
```

必须贴旧世界哈希：
`735` / `009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9` /
`02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`。

### 7.2 真实 xkx 世界命令回放

用 `WorldSession`（真实 `content/xkx`，seed 固定），测试账号 `player:u_equip`
落在 `/d/hangzhou/jiulou`。用一次 `item_create` 夹具发 `/d/city/obj/changjian`
与 `/d/city/obj/tiejia`（只做验证夹具，不改默认初始资产）。

按顺序执行并原样贴 `session.perform(...)` 的 JSON：

1. `enter` → 初始 `equipment.slots` 空、`applied` 空。
2. `wield {"item":"/d/city/obj/changjian"}` → `slots.weapon` 有值、
   `weapon_type="sword"`；若内容已带 `apply`，`applied={"apply/damage":25}`；
   若尚未带，`applied={}` 并如实标注“内容线未落地”。
3. `fight {"target":"/d/hangzhou/npc/xiaoer2","rounds":20}` → 战报
   `rounds[].skill` 出现 `"sword"`；用固定 seed 的 `simulate_fight` 打印
   “空手 vs 持剑”的基础伤害入参 `attack + apply/damage`。
4. `unwield` → `slots.weapon` 消失、`applied` 回到空、长剑回背包。
5. `wear {"item":"/d/city/obj/tiejia"}` → `slots.armor` 有值；
   有 `apply` 时 `applied["apply/armor"]==50`；`remove` 后归零。
6. `drop {"item":"/d/city/obj/changjian","qty":1}` → 背包减 1、
   `ground["/d/hangzhou/jiulou"]["/d/city/obj/changjian"]==1`；
   `loot {"item":"/d/city/obj/changjian"}` → 地面清零、背包加回。
7. `rest {"hours":4}` → `duration_ticks==24`，`stamina/hp` 增加；
   `sleep {"hours":8}` → `hp==max_hp`、`stamina==max_stamina`、`duration_ticks==48`。
8. 负重拒绝：夹具在地面放 6 件 `tiejia`（120000），
   `loot {"item":"/d/city/obj/tiejia"}` → `reject_reason` 以 `overloaded:` 开头，
   `inventory/ground` 均不变。
9. 夹具 `skill_levels={"sword":40,...}` 复跑 2-4，贴 `attack_skill` 与 ap 对照。

### 7.3 掷骰与区域验证

- `attrs_roll` 内容落地后：同一 seed 跑两次创世，贴 10 个种族 NPC 的六维；
  第二次逐字节相同；换 seed 至少一个数值变化。
- 区域：`state`/`look` 响应贴出 `region`、`region_name`；
  当前内容若尚未落地，贴 `null` 并标注“内容线未落地”，不编造。
- 旧九地点世界哈希不变。

### 7.4 文档与 Git

- 更新 `specs/xkx-parity/combat-implementation.md`：装备 apply 接入、负重公式、
  rest/sleep 差异、`attrs_roll` 掷骰算法、region 透出。
- 只显式 `git add` 本任务文件：
  `src/wsim/world/state.py`、`src/wsim/world/effects.py`、
  `src/wsim/actions/equipment.py`、`common.py`、`loot.py`、`commerce.py`、
  `player.py`、`registry.py`、`src/wsim/sim/combat_rules.py`、`vitals.py`、
  `genesis.py`、`src/wsim/sim/headless.py`、`scheduler.py`、
  `src/wsim/api/session.py`、`src/wsim/api/xkx_content.py`、
  `tests/test_equipment_actions.py`、`tests/test_attrs_roll.py`、
  `tests/test_region_view.py`、
  `specs/xkx-parity/equipment-actions.spec.md`、`equipment-actions.tasks.md`、
  `combat-implementation.md`。
- 不 stage：`reports/xkx-audit/*`、`reports/soak/*`、
  `tools/xkx_formula/calibration/*.json`、`specs/xkx-parity/hit-rate.spec.md/.tasks.md`
  及其它线未提交文件。
- 提交信息：`feat(xkx): 装备动作层、负重、丢弃、休息、种族掷骰与区域透出`
- 推送：`git push origin xkx-parity`（不 force）。

## 8. 明确不做

- 不抽取/改写 `items.json` 的 `apply` 数值（内容线负责）。
- 不改 `tools/xkx_formula/*`、800 封顶、`SKILL_MAX`。
- 不改玩家默认 `max_hp=100 / combat_exp=250 / unarmed=40 / dodge=10 / parry=10`。
- 不改 NPC `temp_apply` 的 `known=false` 诊断规则。
- 不发明 `apply/unarmed_damage`、装备叠加系数、武器技能默认值或区域层级。
- 不处理 NPC 随身 `equipment` 的战斗加成（现有死亡/掉落语义保持）。
