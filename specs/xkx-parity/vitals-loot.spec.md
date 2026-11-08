# 规格：打通「能打一场」的三件事（玩家气血 / NPC 气血 / 地面掉落与 get）

状态：已批准并实现（2026-11-07，分支 `xkx-parity`）
范围：`src/wsim/world/**`、`src/wsim/sim/**`、`src/wsim/actions/**`、`tests/test_combat*`、`specs/xkx-parity/**`
明确不动：`tools/xkx_formula/**`、`src/wsim/api/**`、`ui/**`、`config/**`、`content/xkx/**`
不动钩子：`ACTION_SEQUENCE` / `SKILL_HOOKS` 保持 0，不新增任何招式系数。

---

## 0. 事实核对（与任务描述对齐）

| 任务描述 | 实际内容 | 处理 |
| --- | --- | --- |
| 4 个 NPC 缺 `max_hp` | `content/xkx/npcs.json` 里**没有** `max_hp` 字段；缺的是 `vitals.max_qi`：`laohu`、`li`、`qingming`、`tufei2`。另有 3 个连 `vitals` 都没有：`guest`、`shu-sheng`、`tiao-fu` | 统一映射：`max_hp = vitals.max_qi`，缺失走类别默认；7 个 NPC 全部有血 |
| 内容包的 `attrs/vitals` 原样带 | `src/wsim/api/xkx_content.py` 已把 `attrs/vitals/skills/sect` 塞进 NPC dict；`_normalize_npc` 用 `dict(raw)` 保留 | `build_genesis` 直接读，不复制内容包 |
| 玩家创世没血量 | `player_genesis_effects()` 与 `PlayerEnterAction.apply()` 两条 `create_entity` 都没 `max_hp/attrs` | 两条路径都补，同一张表 |

---

## 1. 决策 D1：玩家创世气血与属性

### 1.1 数值表（必须区分来源）

| 字段 | 值 | 来源 |
| --- | --- | --- |
| `max_hp` | 100 | **原版**：`logind.lpc:917-925` 新玩家 `max_qi=eff_qi=100`；`race/human.lpc:116` `age<=14 -> max_qi=max_jing=100`；`clone/user/user.lpc:59` 初始 `age=14` |
| `hp` | `max_hp` | **原版**：新玩家 `eff_qi=max_qi` |
| `attrs.str` | 游侠 20 / 商人 15 / 捕快 18 | **我们的默认**（xkx 登录掷点非确定，不可复刻） |
| `attrs.con` | 20 / 16 / 18 | 同上 |
| `attrs.dex` | 18 / 15 / 17 | 同上 |
| `attrs.int` | 15 / 20 / 17 | 同上 |
| `attrs.spi` | 15 / 18 / 16 | 同上 |
| `attrs.sta` | 20 / 18 / 19 | 同上 |

- 表放在 `src/wsim/actions/player.py`：`PLAYER_BASE_VITALS = {"max_hp": 100, "attrs": {...}}` + `ARCHETYPE_ATTRS: dict[str, dict[str,int]]`。
- 身份键与既有 `ARCHETYPES` 完全一致（游侠/商人/捕快）；`player_genesis_effects()` 无 archetype 入参，用**游侠**一档（记为 `DEFAULT_ARCHETYPE = "游侠"`）。
- 旧 run 的 `visitor` 实体重进不补写（避免无端改动历史事件）；只保证**新创世/新进入**有血量。
- 不加 `attack/defense`（用户未要求，且拳脚攻击由公式库从 attrs+技能推导）。

### 1.2 改动点

1. `src/wsim/actions/player.py`
   - 新增 `ARCHETYPE_ATTRS`、`PLAYER_MAX_HP = 100`、`_vitals_effect_data(archetype) -> dict`（返回 `max_hp/hp/attrs`）。
   - `player_genesis_effects()` 的 `create_entity` data 合并 `_vitals_effect_data(DEFAULT_ARCHETYPE)`。
   - `PlayerEnterAction.apply()` 两处 `create_entity`（新档案、档案存在但实体缺失）同样合并；身份取 `archetype or "游侠"`。
   - `PLAYER_VERBS` 增加 `"loot"`、`"get"`。
2. `src/wsim/world/effects.py`
   - `create_entity` 已支持 `max_hp/hp/attrs`，无需改；仅确认 `hp` 缺省时等于 `max_hp`。
3. 兼容性
   - `content/`（legacy）世界的玩家创世事件会多出 3 个键 → **如果** 某条 golden 含玩家创世就必须显式迁移；当前 `test_p3e_golden.py` 与 `test_xkx_determinism_legacy.py` 都是 `npc_limit=0` 无玩家世界，预期不受影响（实现后实测确认）。
   - 若实测漂移：保留旧基线值、新增 `*_V6` 基线并写清原因，不改旧值。

### 1.3 验收

- 新建玩家实体 `state.entities[pid].max_hp == 100`、`hp == 100`、`attrs` 六项齐全。
- `fight` 不再因缺 `max_hp` 拒绝。

---

## 2. 决策 D2：NPC 创世落 `max_hp`

### 2.1 映射规则（新模块 `src/wsim/sim/vitals.py`）

```
resolve_npc_vitals(npc) -> {"max_hp": int, "hp": int, "attrs": dict, "combat_exp": int, "skill_levels": dict}
```

1. `max_hp = int(vitals.max_qi)`，若 `max_qi > 0`（**原版口径**：气血上限就是 max_qi）。
2. 缺失 `max_qi` 且 `race == "走兽"`、内容带 `age` 时，按原版
   `adm/daemons/race/beast.lpc:75-79` 的年龄公式（D 决策）：

   ```
   age <= 5        -> max_qi = 50
   age <= 20       -> max_qi = 50 + (age - 5) * 25
   else            -> max_qi = 425 + (age - 20) * 5
   ```

   - 内容实例：郭庄大狼狗 `age=4 → 50`（`wolfdog.lpc:8`）；杭州老虎 `age=20 → 425`（`laohu.lpc`）。
   - `age` 缺失时不猜原版 `random(40)+5`，落第 3 步兜底。
   - 覆盖范围：本内容包里 `race == "走兽"` 的只有大狼狗与老虎；`走畜`（骆驼）、
     `飞禽`（麻雀/乌鸦）、`爬蛇`、`昆虫`、`游鱼` 的原版公式**未纳入本次复刻**，走兜底。
3. 其余 race（或走兽缺 `age`）按类别默认（**我们的默认**，可解释）：

| 类别判据（按顺序） | 默认 `max_hp` | 依据 |
| --- | --- | --- |
| `skills` 中存在等级 ≥ 80 | 500 | 一流好手：李拙道人 / 清明禅师（原版技能 100） |
| `skills` 非空但最高 < 80 | 300 | 有功夫在身：土匪（25）/ 进香客（20）/ 书生（literate 40） |
| `skills` 为空 | 200 | 无技能或源码未覆盖的 race：骆驼（走畜）/ 挑夫 |

`DEFAULT_MAX_HP_TIERS` 只作“源码公式未覆盖”的兜底，不再用于走兽。
4. 兜底：`max_hp = max(1, 结果)`，**结构上不可能出现 0**（另有测试断言 92 个 NPC 全部 > 0）。
5. `hp = max_hp`（创世满血）。
6. `attrs = dict(npc.attrs)` 原样；`combat_exp = int(vitals.combat_exp or 0)`。
7. `skill_levels = {k: int(v) for k, v in npc.skills.items()}`（内容包技能是整数等级）。
8. **门控**：只有 NPC dict 含 `vitals`/`attrs`/`skills` 任一键时才走本规则；`content/` legacy 的 30 个 NPC 三个键都没有 → `create_entity` 不新增任何键，旧哈希不变。

### 2.2 改动点

- 新增 `src/wsim/sim/vitals.py`（纯函数，无 IO、无随机）。
- `src/wsim/sim/genesis.py` 的 `build_genesis()` NPC 循环：`create_entity` data 合并 `resolve_npc_vitals(npc)`；保留现有 `skills`（角色浮点）与 `personality` 不变。
- `src/wsim/actions/combat.py`：`max_hp<=0` 时 `preconditions` 返回明确原因码 `target_no_vitals` / `attacker_no_vitals`（若现有实现是抛异常则改为拒绝码），不再 500。

### 2.3 验收

- xkx 92 NPC 全部 `max_hp > 0`；7 个 active 攻击 NPC 可被 `fight` 结算；缺 `max_qi` 的实例：老虎 425（走兽 age=20）、李拙/清明 500、土匪 300、骆驼 200（兜底）。
- legacy 30 NPC 实体序列化**不含** `max_hp/hp/attrs`。

---

## 3. 决策 D3：地面掉落 + `loot` / `get`

### 3.1 状态模型

- `WorldState.ground: dict[str, dict[str, int]]`：`location -> {item_id: qty}`。
- `to_dict()` 只在 `ground` 非空时输出（排序键），旧世界/无战斗世界哈希逐字节不变。
- `Entity` 不新增字段（负重不落状态，容量不设限；重量只用于事件审计与验收）。

### 3.2 Effect 语义

| op | data | 行为 |
| --- | --- | --- |
| `loot_create`（改语义） | `{location | entity, item, qty, weight?, reason?}` | 掉落到**房间地面**；`location` 优先，缺省取 `entity.location`；`items_created += qty`（保持“造物”记账） |
| `loot_take`（新增） | `{entity, location, item, qty, weight?}` | 从地面扣减并进 `Entity.inventory`；地面不足报 `EffectError`（整事务回滚）；**不重复计 items_created** |

- `EFFECT_OPS` 增加 `"loot_take"`。
- `src/wsim/actions/combat.py` 的掉落改为 `Effect("loot_create", {"location": defender.location, "item": item, "qty": qty, "weight": w, "reason": "fight"})`。

### 3.3 动作 `LootAction`（`src/wsim/actions/combat.py` 或新 `src/wsim/actions/loot.py`，优先新文件）

- `verb = "loot"`，`aliases = ("get",)`。
- `ActionRegistry.register()` 支持 `action.aliases`：同一个实例注册到多个 verb 键；插件冲突检查沿用 `has()`。
- args：`player_id`（或 actor）、`item`（可选）、`qty`（可选，仅指定 item 时生效）、`at`（可选，默认实体所在地）。
- preconditions 原因码：
  - `missing_player_id`
  - `not_here`：实体不存在 / 位置不符
  - `loot_not_found`：地面为空，或指定 item 不在该房间地面
  - `bad_qty`：qty 非正
- apply（确定性）：
  - 无 `item`：按 `sorted(ground[loc])` 顺序**全部取走**；每件一条 `loot_take`。
  - 有 `item`：取 `min(qty or 全部, 地面数量)`；qty 超过地面数量时 preconditions 已给 `loot_not_found` 语义（取全部则不报错）。
  - 每条 effect 带 `weight = content.items[item].weight * qty`（缺失 weight 记 0），事件可直接核对重量。
- 注册：`build_registry()` 增加 `reg.register(LootAction(content))`；`PLAYER_VERBS` 加 `"loot"`/`"get"`。
- 不开 `kill`，不改 `FORBIDDEN_VERBS`。

### 3.4 验收

- 打死 NPC → 地面有掉落 → `loot` 后 `Entity.inventory` 增加、地面清零。
- 空地面 / 指定不存在物品 → 明确原因码 `loot_not_found`。
- 同 seed 两跑的事件流逐字节一致（取物按 item id 排序）。
- `sum(loot_create.qty) == sum(ground) + sum(loot_take.qty)`；每次 `loot_take.qty` 与 `inventory` 增量一致。

---

## 4. 兼容性与重采基线

| 基线 | 预期 |
| --- | --- |
| `tests/test_xkx_determinism_legacy.py`（735 events，无玩家） | **逐字节不变** |
| `tests/test_p3e_golden.py`（无玩家 2 天） | **逐字节不变** |
| `tests/test_combat_fight.py`（单场战斗） | `loot_create` 语义变化 → 断言改为“地面掉落”，哈希/事件重采并在注释写原因 |
| `tests/test_xkx_determinism_combat.py` | 同上，新基线显式重采 |

原则：只对**显式受本次语义变更影响**的战斗基线重采；旧值保留为注释记录，不静默改数。

---

## 5. 真实世界验证（验收产物）

1. 用 `src/wsim/api/xkx_content.load_xkx_content("content/xkx")`（只调用，不修改）构造与 8011 同源的 Content。
2. 脚本（放 `/tmp`，不入库）：`build_genesis` → `player_genesis_effects` → `player_enter` → 找 7 个 aggressive NPC 之一（优先 `tufei2`，离出生点近）→ `fight` → `loot`。
3. 输出并贴给用户：
   - `fight` 事件完整 `effects` 与 `combat_log.rounds[]`；
   - 双方 `hp_before/hp_after/max_hp`；
   - 掉落清单（item/qty/weight）；
   - `loot` 事件与背包前后数量；
   - `sum(hp_add.delta)` 与 state 血量变化逐项相等。
4. 统计 92 NPC 的 `max_hp` 分布，确认无 0。

---

## 6. 风险与边界

- 玩家 attrs 是**我们的默认**，与 xkx 登录掷点不同；文档必须醒目标注，后续可由内容/配置覆盖。
- 掉落到地面后，NPC 尸体不再自带物品（原版尸体可被搜刮）；本规格只做“地面掉落 + 取物”，不做 corpse 容器。
- `loot_take` 不校验负重上限：xkx 有负重，但 Entity 无 capacity 字段，本次不引入（不做未要求的扩展）。
- `loot` 目前只对玩家动作开放（`PLAYER_VERBS`），NPC 不自动捡拾。

---

## 6.1 批准后补充（实现契约，来自 API 壳的真实调用）

- `src/wsim/api/xkx_shell.py` 的 `fight` 只传 `{target, opponent, npc}`，**不传 `loot`**；
  `loot` 只传 `{target?, npc?}`，**不传 `item/qty`**。API 层不可改，因此：
  - `FightAction` 在没有 `loot` 参数时，按确定性规则自行产出掉落：
    `defender.inventory`（排序） + `defender.equipped.values()`（排序去重），逐件 `loot_create` 到
    `defender.location` 地面。装备物来自内容包 `equipment`，是本里程碑唯一可解释的掉落来源。
  - `LootAction` 接受 `target/npc/item/qty`；`target` 只作上下文，不参与筛选（本里程碑地面按房间聚合），
    未给 `item` 时取该房间地面全部，按 item id 排序。
- `_combat_reason("fight")` 以“是否存在 `max_hp>0` 的玩家实体”为开闸条件，D1 完成后自动放行。
- 已存在的旧玩家实体（创世早于本改动）在 `player_enter` 时补一次 `vitals_set`，保证 8011 现有存档也
  不会“没有血量”。新增 Effect `vitals_set {entity, max_hp, hp, attrs}`，只在 `max_hp<=0` 时发。
- 地面初始物品：xkx 房间 `objects` 中 `kind=="item"` 且 `count` 为整数的条目，在创世时 `loot_create`
  到对应房间地面（`reason="genesis_ground"`）；`count_expr=random(...)` 的一律跳过，不引入随机。

## 7. 批准项

- [ ] D1 玩家数值表（100 血 + 六维默认值）
- [ ] D2 NPC 缺失 `max_qi` 的三档默认（500 / 300 / 200）
- [ ] D3 `loot_create` 改为地面掉落 + 新增 `loot_take` + `loot`/`get` 别名
- [ ] 允许对受影响的战斗基线显式重采

---

## 8. 修正（2026-11-09）：`loot` / `get` 语义分离

**问题**：现行 `LootAction` 未指定物品时按房间地面全量取走，`loot` 与 `get` 共用一个动作，
导致冒烟时一次 `loot` 把郭庄地面既有物品堆（7 张皮、粗布、炊饼等）整体收进测试账号，
而不是只搜刮刚击杀的狼狗掉落。这是语义 bug，不是口径问题。

### 8.1 目标行为（与原版 xkx 习惯对齐）

| 命令 | 语义 | 行为 |
| --- | --- | --- |
| `loot` / `loot <物品> [数量]` | `scope=kill` | 只取当前房间**最近一次击杀**来源桶里的掉落；不碰其他地面物品 |
| `get all`（`全部/所有/都`） | `scope=all` | 清空当前房间地面；取走多件时回报**总件数** |
| `get <物品> [数量]` | `scope=all` + item/qty | 精确取单件/指定数量；数量超过地面报 `loot_not_found` |
| 裸 `get` | — | 返回用法提示（`get all` / `get <物品> [数量]`），不改状态 |

- 当前房间没有击杀来源桶时，`loot` 必须以 `loot_no_corpse` 拒绝，文字壳提示
  「这里没有刚死的东西可搜刮。」；不得静默成功，不得清空地面。
- 最近一次击杀已被搜空时，`loot` 以 `loot_already_looted` 拒绝，文字壳提示
  「刚死的东西已经被搜刮过了。」；**不得回退去搜上一具尸体**。
- 击杀来源桶存在但指定物品不在其中：`loot_not_found`，文字壳提示
  「刚死的东西身上没有「X」，只有：…」。
- 方案**不改模拟语义**：所有变更仍只经 `loot_create` / `loot_take` 两个 Effect；
  关闭/未启用路径（旧内容、旧事件、无击杀）不产生额外副作用。

### 8.2 数据模型

- `WorldState.ground`（`location -> {item: qty}`）**保持原样**，仍是地面聚合视图，
  供 `look/inventory/session` 与 `get all` 使用；旧世界序列化逐字节不变。
- 新增 `WorldState.ground_sources: dict[str, list[dict[str, Any]]]`：
  `location -> [{"key", "source", "entity", "tick", "items": {item: qty}}]`。
  - 仅 `source="kill"` 写入；`key = f"kill:{tick}:{entity}"`，同 key 合并进同一条目
    （一次击杀的多件掉落不会分裂成多条）。
  - 列表顺序即事件顺序，「最近一次击杀」取 `tick` 最大者，同 tick 取列表末位。
  - 最近一次击杀被搜空后保留**墓碑条目**（`items={}`），确保再次 `loot` 提示
    `loot_already_looted` 而不是回溯上一具尸体；更早的空条目会被清理。
  - 为空时不写进 `to_dict()`，旧世界哈希不变。
- 归因不变量：对任意 `loc/item`，`sum(source.items[item]) <= ground[loc][item]`。

### 8.3 Effect 契约变更（向后兼容）

| op | 新增可选字段 | 行为 |
| --- | --- | --- |
| `loot_create` | `source`（仅 `"kill"`）、`source_entity`、`source_tick` | 既有聚合逻辑不变；带 `source` 时同步写入/合并 `ground_sources` |
| `loot_take` | `source`（来源 key） | 既有聚合扣减不变；带 `source` 时只扣该来源桶（不足报 `EffectError`）；不带时先扣未归因部分（聚合 − 已归因总量），再按条目顺序扣归因桶 |

- 旧事件只有 `location/item/qty/reason`：回放后 `ground_sources` 为空，
  `loot` 视为「无击杀掉落」，`get all` 行为不变。
- `src/wsim/actions/combat.py` 的 `_drop_effects` 增加 `source/source_entity/source_tick`
  参数；两处 NPC 死亡掉落传 `source="kill"`、`source_entity=死者 id`、`source_tick=intent.tick`；
  玩家死亡掉落（`consume_owner` 路径）保持不归因。

### 8.4 代码改动清单

1. `src/wsim/world/state.py`
   - `WorldState` 增 `ground_sources` 字段；`to_dict()` 非空才输出。
2. `src/wsim/world/effects.py`
   - `loot_create`：解析可选来源字段，写/合并 `ground_sources`。
   - `loot_take`：按 `source` 定向或“未归因优先”扣减，维护不变量并清理空桶。
3. `src/wsim/actions/loot.py`
   - `_scope(intent)`：显式 `args.scope` 优先；否则 `verb=="get"` → `all`，`verb=="loot"` → `kill`。
   - `_latest_kill(state, loc)`：取最近击杀条目。
   - `preconditions`：`kill` 无来源 → `loot_no_corpse`；指定 item 不在来源桶 → `loot_not_found`。
   - `apply`：`kill` 的 `loot_take` 带 `source=entry["key"]`；`all` 保持现状。
4. `src/wsim/actions/combat.py`
   - `_drop_effects` 与两处 NPC 死亡调用点加来源参数（**只动 `actions/`；禁止改
     `combat_rules.py` 与 `sim/`**，另一条线正在那上面做逐刀账本/偷袭对拍）。
5. `src/wsim/api/xkx_shell.py`
   - `_cmd_loot` 按用户实际输入的首词区分 `loot` 族与 `get` 族；`get all` 清地面；
     `get <物品> [数量]` 透传 `qty`（复用 `_match_item_rows` 已拆出的数量）；
     裸 `get` 给用法；`loot` 无击杀给「这里没有刚死的东西可搜刮。」。
   - `_EXTRA_REASON_TEXT` 增加 `loot_no_corpse`；帮助文本同步。
6. `src/wsim/api/session.py`：无需改动（`loot` 属 `XKX_EXTRA_PLAYER_VERBS`，payload 原样透传）。

### 8.5 测试清单

- `tests/test_combat_loot.py`（重写取物断言）：
  - 地面有创世物品 + 一次击杀掉落 → `loot` 只取击杀掉落，地面其余物品原样保留；
  - 连续两次击杀 → `loot` 只取最近一次；再次 `loot` 报 `loot_already_looted`，
    第一次击杀的掉落仍留在地面（不回退搜刮）；
  - `get all` 清空地面；
  - `get <id> <n>` 精确取 n，剩余数量正确；
  - 无击杀掉落 → `loot_no_corpse`；旧式 `loot_create`（无 source）不被 `loot` 视为目标；
  - 同 seed 顺序与事件逐字节一致。
- `tests/test_combat_effects.py`：`loot_create` 带来源写入 `ground_sources`；
  旧事件回放 `ground_sources` 为空且 `to_dict()` 不含该键；`loot_take` 定向扣减。
- `tests/test_combat_death_equipment.py` / `tests/test_xkx_room_reset.py`：击杀后 `loot` 仍能取到尸体掉落。
- `tests/test_equipment_actions.py`：`drop → get` 往返（原 `loot` 往返改为 `get`）。
- `tests/test_xkx_api.py`：`loot` 空房间提示改为「这里没有刚死的东西可搜刮。」；
  `get 鹅卵石` 保留；`loot 金锁`（无击杀）提示同步。

### 8.6 验证（不碰生产档）

1. 单测：`python -m pytest tests/test_combat_loot.py tests/test_combat_effects.py tests/test_combat_death_equipment.py tests/test_equipment_actions.py tests/test_xkx_api.py tests/test_xkx_room_reset.py -q`。
2. 世界库副本：复制生产 `runs/xkx-world/events.sqlite` 到 `/tmp/xkx-loot-verify/`，
   在副本上起临时实例（端口 8051/8052，`world: existing`），用新测试账号验证
   `loot` / `get all` / `get <物品> <数量>` / 无击杀提示；**不读写 `runs/xkx-world`**。
3. 生产实例 8011/8041 保持在线，不重启、不重置；老板存档不改动。
4. 提交节奏：`fix/xkx-loot-semantics` 分支基于 `git fetch --all --prune` 后的
   **当前** `origin/xkx-parity` HEAD（fetch 时为 `febfd72`，不写死旧快照），
   按「世界层 Effect → 动作语义 → 文字壳」三段小提交并逐段 push。
5. 每段提交前跑 `grep -rn "FAKE-LEAK" src/`，输出必须为空。
6. 第一段（来源字段 + 分桶 + 兼容）完成后立即报告命令原文与测试输出，不等三段全做完。

### 8.7 批准

需用户确认第 8.1–8.6 节后进入实现；实现中若发现与 `combat_rules.py` 逐刀账本线冲突，
立即停下报告，不自行合并。
