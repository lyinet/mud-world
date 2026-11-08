# B/C/D 规格：重生基线字段对齐 + 逐实例属性掷点 + 狼狗血量溯源

- 日期：2026-11-08
- 分支：`fix/xkx-room-parity`（已 push）
- 状态：**待批准**（批准后才动代码）
- 红线：`tools/xkx_formula/` 零改动；显式 `git add`；不 force；全量 pytest 全绿。
- 前置事实：S1 三件套已验；基线与旧九地点哈希未迁移。

---

## B. `room_reset._baseline()` 字段集对齐 genesis（根因修，不是补 `drops`）

### B.1 权威字段集（genesis 实际写入）

| 来源 | 字段 | 代码位置 |
|---|---|---|
| 固定身份/基础 | `id,name,role,location,home,stamina,skills,personality` | `genesis.py:213-222` |
| vitals 投影 | `max_hp,hp,attrs,combat_exp,skill_levels,race`，非零才落 `neili,max_neili,jiali`，非空才落 `map_skills,prepare_skills` | `vitals.py:184-202`，`genesis.py:225-227` |
| 初始状态 | `status`（仅 `initial_state != alive`，xkx 当前无此键） | `genesis.py:230-232` |
| xkx 装备 | `equipment,equipped,equipped_apply,weapon_type` | `genesis.py:248-283` |
| xkx 掉落 | `drops`（只收 `chance==1.0 && item_resolved && landing=="ground"`，qty 非正整数按 1） | `genesis.py:284-302` |
| xkx 临时攻防 | `temp_apply`（`known==true`、整数、排除 `lpc_set`） | `genesis.py:303-329` |
| clone 身份 | `template,instance` | `genesis.py:346-351` |

### B.2 决策

1. `_baseline()` 的 respawn 载荷字段集**等于**上表（`id` 改写为处理器要求的 `entity`），另外保留审计键 `death_tick/death_location`。
2. `max_stamina`、`skill_xp` **从载荷移除**：genesis 从不写这两个键，它们属于 `create_entity` 的创建默认值。改由 `entity_respawn` 处理器在应用载荷前强制复位为创建默认（`max_stamina=100.0`、`skill_xp={}`）。这样"字段集完全相等"不需要豁免清单。
3. `entity_respawn` 新增应用：`name/role/home/personality/drops/status/template/instance`。
   - `status` 仅接受 `alive/unconcious`，与 `create_entity:226-230` 同校验；缺省仍为 `alive`。
   - `template/instance` 是槽位身份，载荷值必须与现有实体一致（不一致抛 `EffectError`），不做静默覆盖。
4. `skills` 的数值由 genesis 与 `_baseline()` 共用同一个纯函数 `npc_spawn_skill(npcs, npc)`（从 `genesis.py:209` 抽出，输出保持不变），避免第二份公式。
5. 商店补货、周期、return_home 逻辑不动。

### B.3 `Entity` 漏字段逐项结论（用户点名的字段）

| 字段 | Entity 是否存在 | 原版 new() 语义 | 决策 |
|---|---|---|---|
| `template/instance` | 是（`state.py:101-102`） | 新 clone 由模板创建，身份不变 | 载荷带上，处理器校验一致 |
| `attitude` | **否**（Entity 无此字段） | 内容 `attitude` 已投射为 `personality`/aggressive 规则 | 不新增字段 |
| `goal_id` | 是（`state.py:50`） | 新 clone 没有 goal | **不重置**：复刻 goal 是调度器持久引用，清空会断任务链；写进报告作为刻意偏差 |
| `last_action_tick` | 是（`state.py:52`） | 新 clone 为 0 | 保持现值：重生后立即参与 return_home 节流是有意行为；报告说明 |
| `dialogue/npc_meta` | **否**（Entity 无此字段） | 对话在内容包，不在实体 | 不新增字段 |
| `sect` | **否**；相近的是 `faction`（`state.py:49`） | genesis 不写 faction | 不新增字段 |
| `drops` | 是（`state.py:41`） | die() 固定掉落 | **补**（本次核心 bug） |
| `name/role/home/personality/status` | 是 | 新 clone 恢复模板值 | **补** |
| `max_stamina/skill_xp` | 是 | 创建默认 100 / `{}` | 处理器复位默认，不进载荷 |

### B.4 字段对齐测试（本次真正交付物）

新增 `tests/test_xkx_respawn_fields.py`：

1. 用真实 `content/xkx` 构建 genesis，取两类实体的 `create_entity` 载荷：
   - 带掉落的 clone：`/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2`
   - 无掉落的规范实体：`/d/hangzhou/npc/xiaoer`
2. 把实体置 `dead`（`death_tick/death_location` 非 0），跑 `RoomResetPolicy.effects()` 取 `entity_respawn` 载荷。
3. 归一化：`respawn["id"] = respawn.pop("entity")`；删 `death_tick/death_location`；断言 `set(genesis_payload) == set(respawn_payload)`（**集合完全相等，不是包含**）。
4. 值断言：`drops`、`equipment`、`equipped_apply`、`personality`、`name/role/home`、`hp==max_hp`、`status=="alive"` 相等。
5. 应用后断言：狼狗 `drops == {"/d/hangzhou/npc/obj/goupi": 1}`；`max_stamina==100`、`skill_xp=={}`。
6. 负向护栏：手工往 genesis 载荷塞一个假字段 `"__probe__": 1`，测试必须失败（证明"谁加字段忘同步就红"）。

### B.5 活世界验证（不重建老板的世界）

部署目录 `/private/tmp/xkx-play`，API 8041，DB `runs/xkx-world`：

1. 把 B 的代码同步到部署副本，重启 API（**不带 `--reset`**），确认日志 `world: existing`。
2. 等下一次 `system_room_reset`，从 sqlite 贴原始行：`entity_respawn` 的 `args` 含非空 `"drops"`（对象是郭庄大狼狗）。
3. 真实 HTTP `look` → `kill 大狼狗` → `look`，贴：`loot_create` 原始事件行、地面狗皮行、`loot` 后背包 `+1` 的 `view`/`i` 输出。
4. 不重建世界、不 `--reset`、不改存档。

---

## C. `cor`/`cps` 来源与逐实例重掷

### C.1 来源结论（实现阶段贴文件+行）

- 内容：`content/xkx/npcs.json` 中店小二条目的 `attrs_roll.cor/cps = [10,30]`（与 `racy/human.lpc:60-68` 的 `10+random(21)` 等价区间）。
- 引擎：`vitals.py:82-115 roll_attrs()`，键为 `"xkx-attrs:<seed>:<模板npc_id>:<attr>"`。
- 偏差：键用**模板 id**，同模板 clone 共享；同一世界种子下值固定。原版每个 clone `new()` 时现掷。

### C.2 决策：确定性"逐实例 + 逐次重生"掷点

把 `roll_attrs(npc_id, ...)` 改为 `roll_attrs(roll_key, ...)`，调用方给身份键：

| 场景 | `roll_key` | 理由 |
|---|---|---|
| 规范实体创世 | `npc_id` | 保持规范实例稳定 |
| clone 创世 | `instance_id`（如 `ref@room#n`） | 同模板各 clone 独立，镜像原版逐个 `new()` |
| 重生 | `f"{entity_id}#{death_tick}"` | 原版 destruct+new() 会重掷；用死亡 tick 作为确定性 spawn 序号，回放可复现 |

- 公式不变：`lo + int(sha256(f"xkx-attrs:{seed}:{key}:{attr}")[:8],"big") % span`，仍不读墙钟、不用全局随机。
- 为什么不破坏同 seed 复现：key 只由 `(seed, 实体身份, 确定性 spawn 序号)` 组成，死亡 tick 已在事件日志中，回放时逐字节可重建。
- `attrs_derive.int` 仍按新 attrs 重算。
- clone 创世不能再 `deepcopy` 模板 attrs 了事：`genesis.py:346-363` 在 deepcopy 后用该实例 `roll_key` 调 `resolve_npc_vitals()` 覆盖 vitals（hp/max_hp/skills/equipment 等保持模板值，只有 `attrs` 变化）。

### C.3 版本与哈希

- 旧九地点（legacy 内容）在 `resolve_npc_vitals` 第一个门（`vitals.py:158`）就返回 `None`，**不进入 roll_attrs**；实测必须仍为 `735` 事件 / `009076f0…` / `02d5f812…`，贴原始输出。
- xkx 世界因属性键变化，状态哈希会变；报告旧/新值并逐事件归因（只应有 `create_entity.attrs` 变化，事件条数不变）。**不静默改基线文件**，等批准。

### C.4 验收

1. `tests/test_xkx_attrs_roll.py`：
   - 同模板 44 个 clone 中 `cor` 至少 2 个不同值；
   - 同一 `(seed,key)` 重复调用逐字节相等；
   - `death_tick` 不同 → 重生 `cor` 不同（确定性）；
   - 全量 xkx 人类 `cor` 分布 `min>=10`、`max<=30`、`mean∈[19,21]`。
2. 重跑店小二 20 场（复用 `/private/tmp/s1_accept.py` 口径，MID、`kill`）：
   - 店小二守卫下界应回到 `24% ± 8pp`；
   - 玩家仍 `31% ± 8pp`；
   - 报 20 场实际 `cor` 的 min/mean/max，应接近 10–30、均值 20。

---

## D. 大狼狗 `max_hp=200` vs 原版 `max_qi=50`（只查不改）

1. 读原版源码确认：
   - `adm/daemons/race/beast.lpc:76` 的 `max_qi=50` 是基础值还是被 `d/hangzhou/npc/wolfdog.lpc` 覆盖；
   - 内容抽取是否漏了 wolfdog 的 `max_qi`（当前 `content/xkx/npcs.json` 的 wolfdog `vitals` 只有 `combat_exp`）。
2. 我们的 200 出处：`vitals.py:25 DEFAULT_MAX_HP_TIERS` 的 `(0, 200)` 档；因为 wolfdog `skills={}`、无 `max_qi`。
3. 每刀实际伤害对照：
   - 原版：从 10 场狼狗战报逐刀取伤害中值（若现有日志无狼狗，明确写出缺口并给采集方案，不伪造）；
   - 复刻：用 `simulate_fight` 记录每刀 `damage`，给中值/区间；
   - 结论：`血量 ×4` 与 `每刀伤害比` 合起来是否等价；给"保持/下调/补 `max_qi=50`"建议，**不改数值**。

---

## 老板存档写入审计（8011 / `/private/tmp/xkx-play/runs/xkx-world`）

1. 查 sqlite 事件表，列出验收期间的所有非环境写入：
   - 临时 spawn 的实体（`create_entity` 中 id/来源非内容创世）；
   - `p5peek` 账号的 `fight`/`kill` 与 `entity_dead`；
   - 所有 `entity_respawn`。
2. 证明临时实体已消失：`look`/`view`/实体查询输出。
3. 证明老板角色（`player:boss`）事件序列除环境事件外**没有**任何验收操作写入：按 actor/args 过滤并贴计数与原始行。
4. 只读审计，不写库；需要重启只重启 API，不 `--reset`。

---

## 验收清单（全部真跑，贴原始行）

- [ ] 字段集完全相等测试通过，且负向护栏能红。
- [ ] 活世界下一次 reset 的 `entity_respawn` 含非空 `drops`；真 HTTP 击杀 → `loot_create` → 背包 +1。
- [ ] 旧九地点 `735 / 009076f0… / 02d5f812…` 实测不变。
- [ ] 店小二 20 场：守卫 24%±8pp、玩家 31%±8pp、`cor` 分布 min/mean/max 达标。
- [ ] D 只给来源、伤害对照、结论与建议，未改数值。
- [ ] 审计清单完整，临时实体消失，boss 序列无验收写入。
- [ ] 全量 pytest 全绿；`tools/xkx_formula/` `git diff --stat` 为空；显式 `git add`；已 push。
