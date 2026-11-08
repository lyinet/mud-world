# B/C/D 验收记录（respawn-parity T5/T6）

日期：2026-11-08　分支：`fix/xkx-room-parity`　工作树：`/private/tmp/xkx-parity-fix`
活世界：`/private/tmp/xkx-play`（API 8041 / UI 8011，run `xkx-world`，**未重建、未 `--reset`**）

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| B 字段对齐 | 已按 `genesis` 创世载荷逐字段镜像；字段集完全相等测试锁死；活世界证明重生载荷含 `drops`、击杀掉皮、背包 +1 |
| C `cor` 出处 | 不是字面值/默认值/补位，而是 `attrs_roll.cor=[10,30]`（`content/xkx/npcs.json:8739-8746`，源 `adm/daemons/race/human.lpc:68`） |
| C 复测 | 店小二守卫 38/249=**15.26%**，玩家 118/386=**30.57%**；`cor` min/mean/max=**12/21.6/30**；xiaoer 仍差下限 0.74pp（**未进 24%±8pp 带，C 的前提被实测推翻，见 §2.5**） |
| C 旧哈希 | `events_total=735 / state 009076f0… / event 02d5f812…` 测试通过，逐字节不变 |
| D 血量 | 原版 `max_qi=50`（beast age=4 公式），复刻 200 来自 `DEFAULT_MAX_HP_TIERS (0,200)`，不是内容字面值 |
| D 每刀 | 原版 54 刀中值 14.5 / 均值 19.22（区间中值口径）；复刻 124 刀中值 19 / 均值 18.15 → **每刀基本对齐** |
| D 结论 | 血量 4 倍、每刀 1.0 倍 → **不等价**，有效 TTK ≈ 4 倍；建议内容补 `max_qi=50`，**待批，未改** |
| 审计 | 老板库无临时 NPC spawn；`create_entity` 仅创世 + 8 个测试账号 `player_enter`；临时实体不存在故无需清理；boss 序列仅多一条只读被拒的 `look`（seq 3965） |

---

## 1. B：`_baseline()` 字段对齐

### 1.1 字段表（genesis spawn ↔ room_reset respawn）

`genesis.py` 写实体用的字段（以活世界 `genesis` 事件 `create_entity` 实测键集为准）：

- 狼狗（无装备）：`attrs, combat_exp, home, hp, id, location, max_hp, name, personality, race, role, skill_levels, skills, stamina, temp_apply`
- 店小二（有装备）：上面 + `equipment, equipped, equipped_apply`

`room_reset._baseline()` 现在输出：

```
entity,name,role,location,home,stamina,skills,personality,death_tick,death_location
+ vitals 条件键: max_hp,hp,attrs,combat_exp,skill_levels,neili,max_neili,jiali,race,map_skills,prepare_skills
+ status（仅 initial_state != alive）
+ inventory
+ equipment / equipped / equipped_apply / weapon_type（条件）
+ drops（条件，与 genesis.py:284-302 同过滤）
+ temp_apply（条件）
+ template / instance（条件）
```

逐字段结论：`name/role/home/personality/skills/drops` **原来缺失，已补**；`status/template/instance/inventory/equipment/equipped/equipped_apply/temp_apply` **原来缺失，已补**；`max_stamina/skill_xp` **原来多发，已删**（创世不发，避免重生多出字段）。

### 1.2 Entity 其余字段逐项审计

| Entity 字段 | genesis 是否写 | respawn 是否写 | 结论 |
|---|---|---|---|
| `template` / `instance` | 条件写（clone） | 条件写（同条件） | 已对齐；活世界载荷含 `template:"/d/hangzhou/npc/wolfdog", instance:2` |
| `attitude` | **Entity 无此字段**（内容层属性） | — | 不适用 |
| `goal_id` | 否（NPC 默认 `None`） | 否 | 一致；原版 destruct+new() 也丢动态目标 |
| `last_action_tick` | 否（默认 0） | 否（默认 0） | 一致 |
| `dialogue` / `npc_meta` | **Entity 无此字段**（内容层） | — | 不适用 |
| `sect` | **Entity 无此字段**（映射为 `faction`，创世不写） | 否 | 一致 |
| `master/disciples/spouse/employed_at/business/faction/escort_id` | 否 | 否 | 一致（新实例默认空） |
| `combat_target/combat_tick/combat_round` | 否 | 否 | 一致，重生清仇恨，符合新实例语义 |
| `potential/score/learned_points/map_skills/prepare_skills/neili/max_neili/jiali` | 仅 vitals 有的条件写 | 同条件写 | 已对齐 |
| `skill_xp/max_stamina` | 否 | **原来写，已删** | 已对齐 |
| `status` | 仅非 alive 写 | 同条件写 | 已对齐 |
| `death_count/last_death_*/last_seen_tick/presence/wimpy` | 否 | 否 | 一致（审计线索由 death_tick/death_location 承担） |

**判定标准**：`genesis` 的 spawn 载荷是“新实体字段集”的唯一权威；凡它不写的字段，重生也一律不写（默认值），字段集测试保证将来加字段必须同步。

### 1.3 测试（交付物）

`tests/test_xkx_respawn_fields.py`：

```
_normalize_genesis(payload) = set(payload) | {"inventory"}
_normalize_respawn(payload) = set(payload 去掉 death_tick/death_location, entity→id)
assert genesis_set == respawn_set   # 完全相等，不是包含
```

覆盖：clone 带 `drops`、canonical 带装备、负向 `__probe__` 护栏、应用后 `drops=={goupi:1}`、`max_stamina==100`、`skill_xp=={}`、不同 `death_tick` 重掷不同 attrs。

### 1.4 活世界原始证据（老板库，未重建）

`seq 7199 / tick 4880 / system_room_reset`：

```json
{"op":"entity_respawn","data":{"entity":"/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2",
 "template":"/d/hangzhou/npc/wolfdog","instance":2,"name":"大狼狗","role":"wanderer",
 "home":"/d/hangzhou/guozhuang","personality":{"speech_style":"","traits":[]},
 "skills":{"wanderer":0.41},"drops":{"/d/hangzhou/npc/obj/goupi":1},
 "max_hp":200,"hp":200,"stamina":100.0,"death_tick":4874,
 "death_location":"/d/hangzhou/guozhuang", ...}}
```

`seq 7214 / tick 4889` 再次重生，同样含 `"drops":{"/d/hangzhou/npc/obj/goupi":1}`。

真 HTTP `fight 大狼狗`（p5peek）→ `seq 7212 / tick 4883`：

```json
{"op":"entity_status","data":{"entity":"...wolfdog@...#2","status":"dead","tick":4883}},
{"op":"loot_create","data":{"item":"/d/hangzhou/npc/obj/goupi","qty":1,"location":"/d/hangzhou/guozhuang","reason":"fight"}}
```

`look` 显示“地上散落：狗皮”；`loot 狗皮` → `seq 7216 loot` 含 `loot_take`；`i` 显示 `狗皮 ×1`。**背包 +1 成立。**

---

## 2. C：`cor`/`cps` 来源与重掷

### 2.1 出处（店小二 `cor=10` 的真相）

`content/xkx/npcs.json`（xiaoer2 条目）：

- `"attrs": {}`（第 8717 行）
- `"attrs_derive": {"int": ...}`（第 8718-8733 行，只补 int）
- `"attrs_roll": {"con":[10,30], "cor":[10,30], "cps":[10,30], "dex":[10,30], ...}`（第 8734-8767 行）
- `"attrs_roll_source": {"cor":{"kind":"lpc","line":68,"path":"adm/daemons/race/human.lpc"}, "cps":{...line:67...}}`（第 8768-8782 行）

即：`cor` **本来就是逐实例掷点**，`10+random(21)`，不是字面值、不是默认值、不是 `attrs_derive` 补位。此前“复刻 `cor=10` 写死”的判断不成立。

### 2.2 逐实例/逐次重生确定性掷点

- `vitals.roll_attrs(roll_key, ...)`：`sha256(f"xkx-attrs:{seed}:{roll_key}:{attr}")` → `lo + digest % (hi-lo+1)`。
- clone 创世：`roll_key = 实例 id`（`wolfdog@/d/hangzhou/guozhuang#2`），不再是模板 id。
- 重生：`roll_key = f"{ent.id}#{death_tick}"`（首次死亡 `death_tick>0`），每次重生确定性重掷。

为什么同 seed 可复现：`roll_key` 只依赖创世顺序固定的实例 id 与事件里确定的 `death_tick`，不含墙钟/随机源；同 seed 同事件序列 → 同 `roll_key` → 同 SHA-256 → 同属性。旧世界（无 `attrs_roll`）不进入掷点路径。

### 2.3 旧九地点哈希（实测）

`pytest tests/test_xkx_determinism_legacy.py -q` → `1 passed`：

```
events_total = 735
state_final  = 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9
event_final  = 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae
```

逐字节不变。

### 2.4 店小二 20 场复测（seed 101-120，MID，initiative gate）

| seed | cor | 玩家守卫首/静默 | 店小二守卫首/静默 | 回合 |
|---|---|---|---|---|
| 101 | 26 | 9/0 | 3/0 | 16 |
| 102 | 13 | 6/1 | 2/0 | 13 |
| 103 | 26 | 7/1 | 2/1 | 15 |
| 104 | 28 | 5/0 | 1/0 | 11 |
| 105 | 21 | 5/1 | 3/0 | 14 |
| 106 | 13 | 6/0 | 1/0 | 12 |
| 107 | 12 | 7/1 | 2/0 | 14 |
| 108 | 20 | 6/0 | 2/1 | 13 |
| 109 | 14 | 5/1 | 1/0 | 12 |
| 110 | 28 | 5/0 | 2/0 | 12 |
| 111 | 19 | 6/0 | 2/0 | 13 |
| 112 | 22 | 5/1 | 2/1 | 13 |
| 113 | 30 | 6/0 | 2/0 | 13 |
| 114 | 25 | 5/0 | 2/0 | 12 |
| 115 | 21 | 6/1 | 2/0 | 13 |
| 116 | 20 | 5/0 | 2/0 | 12 |
| 117 | 19 | 6/0 | 2/0 | 13 |
| 118 | 24 | 5/1 | 2/0 | 12 |
| 119 | 27 | 6/0 | 2/0 | 13 |
| 120 | 24 | 6/1 | 2/0 | 13 |

汇总：玩家 118/386=**30.57%**（在 31%±8pp 内）；店小二 38/249=**15.26%**（目标 24%±8pp，**差下限 0.74pp**）；`cor` 分布 **min 12 / mean 21.6 / max 30**（接近 10-30、均值 20）。

### 2.5 偏差（必须单报）

C 的前提“店小二 `cor=10` 被写死”被实测推翻：`cor` 一直逐实例掷点。本轮已把掷点键从模板改为实例/重生身份（正确性提升，旧世界哈希不变），但**守卫率仍为 15.26%，没有回到 24%±8pp**。插桩：店小二 gate 失败 49/260=18.85%，与其 `cor` 均值 21.6 对应的理论值（约 27%）仍有差距，指向 initiative gate 的 `cps` 有效值/样本口径，而不是 `cor` 冻结。**未改公式库**，作为未闭合项上报。

---

## 3. D：大狼狗 `max_hp=200` vs 原版 `max_qi=50`（只查来源）

### 3.1 原版

- `adm/daemons/race/beast.lpc:75-76`：

```lpc
if (undefinedp(my["max_qi"])) {
    if (my["age"] <= 5) my["max_qi"] = 50;
```

- `d/hangzhou/npc/wolfdog.lpc:8`：`set("age", 4);`，全文**不覆盖** `max_qi`。
- 结论：`max_qi=50` 是 `beast.lpc` 基础值（age≤5 档），不是 wolfdog 覆盖值。

### 3.2 复刻 200 的来源

- `src/wsim/sim/vitals.py:25`：`DEFAULT_MAX_HP_TIERS = ((80, 500), (1, 300), (0, 200))`。
- 狼狗内容 `content/xkx/npcs.json` 只有 `attrs.age=4`、`vitals.combat_exp=1000`，无 `vitals.max_qi`；`skills={}` → 档位 top=0 → 命中 `(0, 200)`。
- 结论：**200 是默认档，不是内容字面值，也不是公式推导值。**

### 3.3 每刀实际伤害

| 口径 | 样本 | 每刀中值 | 每刀均值 | 区间 |
|---|---|---|---|---|
| 原版（战报伤害档中值：10-19→14.5，20-39→29.5） | 10 场 / 54 刀 | **14.5** | **19.22** | 14.5–29.5 |
| 复刻（engine `damage` 精确值） | 10 场 / 124 刀 | **19.0** | **18.15** | 12–23 |

每刀基本对齐（复刻均值 −5.6%）。

### 3.4 合成判断

- 原版：`max_qi=50`，每刀 ~19 → 约 3–5 刀打空/昏迷（原版 10 场 3–9 回合、54 刀/10 场）。
- 复刻：`max_hp=200`，每刀 ~18 → 约 11–14 刀击杀（本轮 `hits` 11–14，`rounds_used` 19–33）。
- **血量 4 倍没有被每刀伤害抵消**（每刀约 1.0 倍），有效 TTK 约 4 倍，**不等价**。

### 3.5 建议（待批，未动数值）

1. 内容给 wolfdog 补 `vitals.max_qi: 50`（或由 `beast.lpc` 的 age 公式统一推导），不要用默认档。
2. 不要同时调每刀伤害（已对齐，调了会双重补偿）。
3. 注意死亡判定：复刻是 `hp<0` 昏迷后下一心跳 `die()`，原版也是 `qi<0` 昏迷后心跳 `die()`（`char.lpc` + `combatd.lpc:742-760`），所以 50 会直接把 TTK 拉回 ~3 刀，方向正确。
4. 影响面：只影响 xkx 内容 NPC；旧九地点无 `vitals` 路径，哈希不变。建议单独一提交 + 10 场复测。

---

## 4. 顺带：老板存档写入审计

库：`/private/tmp/xkx-play/runs/xkx-world/events.sqlite`

### 4.1 我制造的全部写入

| seq | tick | actor | verb | 说明 |
|---|---|---|---|---|
| 639 | 1419 | player:shots | fight | 验收观察账号战斗 |
| 2209 | 1802 | player:verify2 | fight | 验收观察账号战斗 |
| 4083 | 2947 | player:s1hunter | fight | 验收观察账号战斗 |
| 4874 | 4874 | player:p5peek | fight | B 活世界制造死亡槽 |
| 4883 | 4883 | player:p5peek | fight | B 真 HTTP 击杀，掉狗皮 |
| 4889 | 4889 | player:p5peek | loot | 拾取狗皮 |
| 7199 | 4880 | system | system_room_reset | `entity_respawn` 含 `drops=goupi×1` |
| 7214 | 4889 | system | system_room_reset | 下一次 reset 再重生，同样含 `drops` |
| 2921 | 2921 | player:boss | look | **被拒**（`unknown_action`），只读探测，无效果 |

另：`player_enter` 共 8 个测试账号（shots/probe1/probe2/evidence/p5peek/s1hunter/verify2 + 既有 u_demo genesis）。**没有任何临时 NPC `create_entity`**。

### 4.2 临时实体是否消失

- `create_entity` 总数 158 = 105 规范 + 53 clone；非创世来源 9 条 = `genesis_player u_demo` + 8 个测试账号 `player_enter`。**没有临时 NPC spawn**，因此不存在需要清理的临时实体。
- 当前 `p5peek` 房间实体查询（只读 `/api/player/state`）：郭庄仅 `wolfdog@/d/hangzhou/guozhuang#2`，无多余实体。
- 我们的击杀已被 `system_room_reset` 正常重生（7199/7214），狼狗当前存活。

### 4.3 boss 事件序列

`player:boss` 全部事件：

- `tick<=650`：用户自己的演示流程（含 `seq 321/325` 打狼狗、`seq 252` 打店小二），**不是验收写入**。
- `seq 3965 tick 2921 look`：验收探测，`outcome=rejected`，只读，无状态变更。**如实披露**。

结论：除这一条被拒的只读 `look` 外，boss 序列没有任何来自验收的写操作；验收写入全部落在独立观察账号 `p5peek/shots/verify2/s1hunter` 上。
