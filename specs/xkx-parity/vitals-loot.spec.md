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
2. 缺失时按类别默认（**我们的默认**，可解释）：

| 类别判据（按顺序） | 默认 `max_hp` | 依据 |
| --- | --- | --- |
| `skills` 中存在等级 ≥ 80 | 500 | 一流好手：李拙道人 / 清明禅师（原版技能 100） |
| `skills` 非空但最高 < 80 | 300 | 有功夫在身：土匪（25）/ 进香客（20）/ 书生（literate 40） |
| `skills` 为空 | 200 | 无技能或野兽：老虎 / 挑夫 |

3. 兜底：`max_hp = max(1, 结果)`，**结构上不可能出现 0**（另有测试断言 92 个 NPC 全部 > 0）。
4. `hp = max_hp`（创世满血）。
5. `attrs = dict(npc.attrs)` 原样；`combat_exp = int(vitals.combat_exp or 0)`。
6. `skill_levels = {k: int(v) for k, v in npc.skills.items()}`（内容包技能是整数等级）。
7. **门控**：只有 NPC dict 含 `vitals`/`attrs`/`skills` 任一键时才走本规则；`content/` legacy 的 30 个 NPC 三个键都没有 → `create_entity` 不新增任何键，旧哈希不变。

### 2.2 改动点

- 新增 `src/wsim/sim/vitals.py`（纯函数，无 IO、无随机）。
- `src/wsim/sim/genesis.py` 的 `build_genesis()` NPC 循环：`create_entity` data 合并 `resolve_npc_vitals(npc)`；保留现有 `skills`（角色浮点）与 `personality` 不变。
- `src/wsim/actions/combat.py`：`max_hp<=0` 时 `preconditions` 返回明确原因码 `target_no_vitals` / `attacker_no_vitals`（若现有实现是抛异常则改为拒绝码），不再 500。

### 2.3 验收

- xkx 92 NPC 全部 `max_hp > 0`；7 个 active 攻击 NPC 可被 `fight` 结算；4 个缺 `max_qi` 的分别为 200/500/500/300。
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
