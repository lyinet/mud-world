# 侠客行（xkx）玩法承接评估：现有世界引擎可行性

> 状态：评估稿（只读分析，未改 `src/**`、`ui/**`、`tests/**`）。
> 对象仓库：`world-sim/`。
> 参照规格：`world-sim/specs/mud-paradigm/spec.md`（文字 MUD 操作范式）。
> 实测来源：`docs/CAPACITY.md`、`docs/CONTENT-SCALE.md`、`runs/*/summary.json`。
> 本文所有“现状”判断都指到具体文件与函数；所有“必须实测”的项都不给拍脑袋数字。

---

## 0. 一句话判断

**有条件可行。**

- **可行的是“xkx 核心循环的可玩版本”**：文字房间/出口、物品买卖、NPC/门派/师徒、任务、经济、刷怪、回合战斗。这些在现有事件溯源、单写者、确定性 PRNG 之上都能表达，且不需要推翻架构。
- **不可行的是“严格逐项复刻”**：逐秒心跳 + 每回合实时结算 + 千房间 + 数百怪同屏。现有实现是“单进程 + 单写者 + 每写整世界 `clone()` + 单核 GIL”，实测安全线 100 席、150 席开始劣化、200 席写动作 P95 近 5 秒（`docs/CAPACITY.md` §5–§7）。这不是架构对错问题，是容量与延迟问题。
- **前置条件 3 条**：
  1. 战斗必须**事件化**：HP、冷却、怪物刷新、尸体、掉落全部进 `WorldState`，只经 `Effect` 变更，禁止进程内隐藏状态。
  2. 新字段必须**默认空且不写入无玩家哈希**，沿用 `presence/equipped/marriages` 的条件序列化模式（`src/wsim/world/state.py` 的 `to_dict`）。
  3. 房间扩到 1000 之前，先完成状态 `clone()`、BFS、事件回放、读视图的实测与分区方案；否则会先撞容量墙。

---

## 1. 现状基线（只列与本评估有关的代码事实）

### 1.1 事件溯源与单写者

- `Intent` 是唯一外部输入格式，`Effect` 是唯一变更载体，`Event` 是唯一事实源：
  [models.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/models.py#L14-L86)。
- 所有写入走 `Writer.submit()`：`preconditions → PRNG → apply → Effect[] → clone → apply_effects → append → self.state = trial`：
  [writer.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/writer.py#L88-L156)。
- 拒绝也是事件（`outcome=rejected`），状态不变：
  [writer.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/writer.py#L80-L86)，
  [models.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/models.py#L89-L99)。
- 启动不重新创世，而是把 `events.sqlite` 的 `ok` 事件按 `seq` 重放：
  [persistence.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/persistence.py#L57-L69)。
- 事件表 append-only，禁止 UPDATE/DELETE：
  [store.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/store.py#L15-L33)。
- 内容哈希不一致直接拒绝启动，防止“脏档继续跑”：
  [persistence.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/persistence.py#L50-L55)，
  [world_guard.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/world_guard.py#L79-L111)。

**对 xkx 的含义**：只要战斗、刷怪、掉落都写成事件，重启、回放、审计、封号追责全部免费获得；代价是每场战斗的事件量与磁盘增长。

### 1.2 确定性铁律

- 只用 `PRNG(seed, seq, salt)`，底层 SplitMix64，种子由 `sha256(f"{seed}|{seq}|{salt}")` 派生，禁止全局 `random`：
  [rng.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/rng.py#L1-L31)。
- 浮点统一 `round(6)`、字典按键排序、集合转有序列表：
  [canonical.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/canonical.py#L13-L34)。
- `WorldState.clone()` 用 pickle 往返做整世界深拷贝，保证试算失败时旧投影不受影响：
  [state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L407-L415)。
- `to_dict()` 对“只有玩家/新机制才会出现的字段”做条件输出，保证无玩家世界哈希逐字节不变：
  [state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L417-L450)。
- golden 测试锁死同 seed 的事件数、state hash、event hash；任何调度/前置改动造成漂移立即 FAIL：
  [test_p3e_golden.py](file:///Users/lyinet/projects/XKX/world-sim/tests/test_p3e_golden.py#L1-L59)。

**对 xkx 的含义**：命中的随机、伤害浮动、掉落随机**不是**冲突，只要全部从 `PRNG(seed, seq, salt)` 取数，并把随机结果落进 Effect/Event，就能“随机但可复现”。真正要小心的是浮点累积顺序与隐藏计时器。

### 1.3 状态模型

`WorldState` 的核心字段：`locations / entities / players / businesses / factions / relations / debts / escorts / quests / goals / accounts / schedule / memories / talk_ledger / quest_stats ...`：
[state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L372-L405)。

`Entity` 现有字段：`role / location / home / stamina / max_stamina / status / inventory / equipped / skills / personality / employed_at / master / disciples / business / faction / goal_id / escort_id / presence`：
[state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L18-L44)。

**关键缺口**：没有 `hp / max_hp / attack / defense / attrs（膂力/悟性/根骨/身法/福缘/容貌/灵性）/ exp / potential`，也没有房间地面物品、容器、怪物模板、刷新计时。

### 1.4 时间与调度

- 时间常量：`TICKS_PER_HOUR = 6`、`TICKS_PER_DAY = 144`（1 tick = 10 游戏分钟）：
  [clock.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/clock.py#L7-L8)。
- 玩家每次写动作把世界推进 `tick_step`（默认 6 tick = 1 游戏小时），并写一条 `system_tick`：
  [session.py `_advance()`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L2720-L2746)，
  以及 `tick_step` 的默认值 6（[session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L386-L401)）。
- 后台自走 1x 速率是 10 tick/分钟（0.167 tick/s），即 1 游戏日 ≈ 2.4 真实小时：
  [scheduler.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/scheduler.py#L42-L44)。
- LOD 四档：`silent 1440 / background 288 / active 144 / spotlight 36` tick 唤醒间隔，初始份额 `.20/.40/.30/.10`：
  [lod.yaml](file:///Users/lyinet/projects/XKX/world-sim/config/lod.yaml#L5-L20)。
- LOD 用最小堆按到期唤醒，`advance_to()` 复杂度是“本 tick 到期数”而不是全体 NPC：
  [lod.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/lod.py#L165-L177)，
  [_wake()](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/lod.py#L221-L234)。
- 每个 tick 都会算一次全量 `state_hash` 并写进度链：
  [scheduler.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/scheduler.py#L697-L707)。
  （注：`state.hash()` 不含 `locations`；`full_hash()` 才含房间图，见 [state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L452-L474)。）

### 1.5 玩家动作与校验模型

- 客户端动词白名单 `PLAYER_VERBS`，明确**不含** `attack / kill / rob`（注释写明 L3 伤害/抢掠/强迫婚配不在其中）：
  [player.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/player.py#L44-L89)。
- 动作入口 `perform()`：别名 → 禁动词 → 角色门禁 → 参数 → 只读前置 `_precheck_player_action` → 组 `Intent` → `Writer.submit()`：
  [session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L2110-L2152)。
- 玩家可读快照 `player_state()` 返回真实 `location / exits / services / wallet / skills / stamina / quests / relations`：
  [session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L593-L652)，
  [_location_view](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L1593-L1656)。
- 能力位路由在 provider 注册表层：`dialogue / strategy / self_goal / night_batch / quest_gen / persona_gen / validation`：
  [providers.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/admin/providers.py#L33-L59)。

### 1.6 内容规模与实测预算

| 口径 | NPC | 地点 | 物品 | 委托模板 | 2 日事件 | 10 日事件 | 唤醒/日 |
|---|---|---|---|---|---|---|---|
| 默认关闭态 | 31 | 9 | 41 | 27 | 735 | 3588 | 40.8 |
| `scaleup_w3` 开启态 | 60 | 20 | 70 | 60 | 1991 | 6014 | 未单独统计（10 日约 600 事件/日） |

- 默认态计数与 2 日事件数：`docs/CONTENT-SCALE.md` §“关闭态验收”（注明当前 `content_hash` 已变化，但 31/9/41/27 与 735 是当前实测）。
- 10 日 31 NPC：`runs/a3-model-10d-31npc/summary.json` → `events_total=3588`、`processed_wakes=408`、`wakes/day=40.8`；按档位唤醒 `spotlight 296 / background 57 / active 50 / silent 5`，即 **72.5% 的唤醒发生在 spotlight**。
- 开启态：`docs/CONTENT-SCALE.md` §“开启态验收”。

**注意**：LOD 的 `.10` spotlight 只是**初始份额**；`update_tiers()` 会按热度升降档，所以 31 NPC 实跑时 spotlight 有 10 个、唤醒占 72.5%，与静态份额不一致。扩 NPC 时不能拿 `.10` 直接估成本。

---

## 2. 概念映射表（xkx → 现有引擎）

图例：**✅ 直接可用**；**🟡 可复用但需改造**；**❌ 必须新增**。

| xkx 概念 | 现有承载（代码/内容） | 判定 | 缺口 / 需要新增 |
|---|---|---|---|
| 房间 room | `content.locations` → `WorldState.locations`；视图 `_location_view` 已读 `description/exits/services` | 🟡 | 房间长描述目前内容里为空；无区域/室内外/flags；无房间内可见物列表（NPC 走 `_present_view`，物品无地面） |
| 出口 exit | `locations[].exits` 存地点 id；`MoveAction` 只允许相邻地点移动 | ✅ | 无方向名（n/s/e/w）、无上锁门/钥匙、无单向出口（加载期会补反向边） |
| 物品 item | `content.items` 的 `id/name/category/base_price`；`Entity.inventory = {item_id: qty}`；`item_create/item_consume/item_move` | 🟡 | 只有“同质计数”，无实例、无描述之外的耐久/重量/单位价值分离、无地面/容器 |
| 容器 container | — | ❌ | `_owner_stock()` 只接受 entity/business（[effects.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/effects.py#L140-L145)）；需新增 `container:<id>` / `ground:<loc>` 持有者与容量 |
| NPC | `Entity` + `content/npcs.yaml`（31）+ `content/personas/`（30 张卡）+ `MockReasoner/Planner` | ✅ | 有身份、日程、关系、目标、记忆；无怪物模板/仇恨/等级 |
| 怪物 / 野兽 | `Entity` 可复用（`status=alive/dead` 已有） | 🟡 | 无 `hp/attack/defense/level/aggression`；无刷新点 |
| 技能 skill | `Entity.skills: dict[str, float]`（0..1）；`Effect("skill")` 夹在 `[0,1]`；`CultivateAction` | 🟡 | 无整数等级、无经验/熟练度、无基本/特殊/知识分类、无 `learn/practice`、无师承限制 |
| 技能等级 / 熟练度 | 仅有 0..1 浮点 | ❌ | 需新增 `skill_levels` / `skill_xp`（或把 float 映射成显示档位，属路线 C） |
| 属性（膂力/悟性/根骨/身法/福缘/容貌/灵性） | — | ❌ | 需新增 `Entity.attrs`；创建时用 `PRNG` 掷点并落事件 |
| 门派 sect | `Faction` + `content/sects.yaml` + `JoinSectAction/FoundSectAction` + `faction_join`/`sect_deposit`/`sect_withdraw` | ✅ | 门派技能传承、门规、贡献值需内容/状态扩展（结构已在） |
| 师徒 master/disciple | `Entity.master/disciples`；`add_disciple`；`ApprenticeAction`；关系 kind `master/disciple` | ✅ | 玩家侧收徒/逐出已有（`player_sect.py`）；师门任务与传承加成需新增 |
| 战斗回合 combat | — | ❌ | `PLAYER_VERBS` 无 `attack`；`EFFECT_OPS` 无 `hp_damage/death`；无战斗状态机 |
| 任务 quest | `Quest` + `quest_create/accept/step/complete/fail`；27 模板、6 类（errand/inquiry/procurement/escort/debt_collection/commission） | ✅ | 师门任务无 `category` 约定；接单前置按 giver NPC/地点校验，需小改 `open_quests/_quest_feasible` |
| 师门任务 | 可用 Quest 结构 | 🟡 | 需新增 `category: sect`、`giver: faction`、贡献奖励与前置（门派身份/师徒） |
| 心跳 heartbeat | `tick` 6/时、144/日；`AmbientScheduler` 实时推进；`PlayerHeartbeatAction` 只维护在线态 | 🟡 | 世界心跳有；**逐秒战斗心跳**没有；玩家动作会把时间瞬跳 +6 tick |
| 刷新 refresh/respawn | `ScheduleItem` + `schedule_add/remove` 可排未来事件；`System*Action` 有日结/维护 | 🟡 | 无 `spawns` 状态与 `system_spawn`；死亡实体不会自动重生 |
| 货币 currency | `accounts` + 钱包/公账/门派金库/债务；`mint/burn/transfer`；仅 genesis 可 `mint` | ✅ | 无玩家银行存取（店铺 `withdraw` 不是钱庄）；大额携带无重量/上限 |
| 物价 price | `items.base_price` + 供需乘数 + `shop_price` + 交易税 + 买卖上限 | ✅ | 无 NPC 间自由定价/黑市；物价模型偏经济模拟，不是 xkx 的固定价目表 |
| 装备 equipment | `Entity.equipped` + `equip/unequip` + `_ENTITY_FIELDS` | 🟡 | 有槽位与穿戴；**无攻防加成**（需等战斗） |
| 死亡 / 复活 | `Entity.status=alive/dead/reincarnated`；玩家 `presence=custody/dormant` | 🟡 | 无尸体、无掉落、无死亡惩罚；玩家死亡流程未接 |
| 经验 / 潜能 | — | ❌ | 玩家无 `exp/potential`；NPC 的 `Goal.progress` 不能替代 |
| 正邪 / 杀气 | — | ❌ | 需新增 `alignment/karma`（可作为 `attrs` 的扩展） |
| 食物 / 饮水 | 只有 `stamina`；`consume/use_item` 恢复 | 🟡 | 无独立饥渴值；可先砍或映射到 stamina |
| 昼夜 / 天气 | `tick/day` 有；无天气状态 | 🟡 | 昼夜用 tick 推导即可；天气需内容/系统事件 |
| 地图 / 帮助 | 不在引擎；玩家 UI 读 `/api/player/state` | ✅（壳层） | 地图/分级 help 是客户端工作，低风险 |
| 聊天频道 / 组队 / 玩家交易 | 无 | ❌ | `talk` 只对 NPC；玩家间交互需要 API/会话层新能力 |
| 玩家 PK / 偷窃 / 抢劫 | 明确禁止（`PLAYER_VERBS` 注释 L3） | ❌（且不建议） | 与“无伤害/无抢掠”的产品红线冲突；若要做需单独评审 |

---

## 3. 硬冲突与表面冲突

### 3.1 真正的冲突

**T1｜tick 粒度是 10 分钟，不是 1 秒。**
`TICKS_PER_HOUR=6` / `TICKS_PER_DAY=144`（[clock.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/clock.py#L7-L8)），且玩家每动作 +6 tick（[session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L2720-L2746)）。xkx 的“每 1~3 秒一回合”在这套时钟里无法直接表达；若把 `TICKS_PER_HOUR` 改成 3600，会同时改变 LOD 间隔、任务截止、日结、全部 golden 基线，代价是重写内容时间标尺与所有基线。

**T2｜战斗状态完全缺失。**
`Entity` 无 `hp/attack/defense`（[state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L18-L44)），`EFFECT_OPS` 无伤害/死亡原语（[effects.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/effects.py#L44-L98)），`PLAYER_VERBS` 无 `attack`（[player.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/player.py#L44-L89)）。这不是“加个 action 文件”能解决的：`Action.apply()` 只能返回 `Effect`，所以必须同时新增状态字段与 Effect 原语。

**T3｜单写者 + 一次动作一条事务，与“多回合实时战斗”冲突。**
`Writer.submit()` 一次 intent 只提交一条事件（[writer.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/writer.py#L88-L156)）。xkx 的“攻击 → 对方反击 → 再攻击”如果是三个玩家意图，就要三次全局写锁；如果是同一次战斗，需要一种“一个动作内多回合”的表达。

**T4｜千房间的派生成本，不是存不下，而是算得慢。**
房间是内容，不是事件（`WorldState(locations=content.locations)`，见 [session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L455-L464)），所以 1000 房间不会增加 genesis 事件量；但会抬高：
- `path_between()` 的 BFS：每次规划/移动都是 O(V+E)（[common.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/common.py#L388-L404)）；
- `full_hash()` / 插件隔离的整图比较（[state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L455-L474)）；
- 读视图与内存；
- 如果以后把地面物品放进 `locations`，还会进 `full_hash` 与 clone。

**T5｜无玩家哈希纪律会限制“随手加字段”。**
`to_dict()` 条件输出是现有红线（[state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L417-L450)），golden 锁死 2 日世界（[test_p3e_golden.py](file:///Users/lyinet/projects/XKX/world-sim/tests/test_p3e_golden.py#L1-L59)）。任何新字段只要默认值被序列化，现有无玩家基线立刻漂移。这是纪律冲突，不是能力冲突，但必须写进开发规范。

### 3.2 只是表面冲突

**S1｜“大量随机”不是冲突。**
命中、伤害浮动、掉落、暴击，全部可以用 `PRNG(seed, seq, salt)` 取数（[rng.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/rng.py#L1-L31)）。当前 `Writer` 已经按 `seq` + `"action:{verb}"` 播种；战斗只要换成 `salt="combat:round:N"` 即可，同一场战斗在同一事件序下结果完全一致。**真正要防的是**：
- 用浮点累加血量/伤害（建议 HP 用整数，公式内部用整数或每步 `round(6)`）；
- 用 `time.time()` 做冷却（必须换成 `tick` 或 `round` 计数）；
- 用进程内字典缓存怪物状态（必须落 `WorldState`）。

**S2｜“实时心跳”不必然冲突。**
现有 ambient 已经把虚拟时钟按真实时间推进（1x = 10 tick/分钟，[scheduler.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/scheduler.py#L42-L44)）。只要“何时触发”由墙钟决定、“触发结果”写成事件，回放只重放事件、不重跑时钟，日志仍然是确定性的。这与录像回放同理：录像不依赖当时的帧率。

**S3｜“回合制”不必然冲突。**
事件日志允许同一 tick 多条事件：婚姻链路就在同一个 `self.state.tick` 连续提交第二条事件（[session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L2153-L2169)）。所以“一回合一条事件、同一 tick 下多条事件”完全可行；玩家写操作的单调约束只要求 `intent.tick <= state.tick`，不要求递增。

### 3.3 “每世界一个种子 + 事件可回放”能保住战斗手感吗？

**结论：能，但只在满足以下 5 个条件时成立。**

1. **战斗全状态进投影**：`hp / max_hp / 战斗冷却 / 连招计数 / 怪物刷新点 / 尸体 / 掉落` 全部是 `WorldState` 字段，只经 `Effect` 变更。
2. **随机只用 `PRNG(seed, seq, salt)`**：每回合独立 salt，禁止复用上一回合的 PRNG 对象（避免“多打一次就改变下一场掉落”的隐式耦合）。
3. **整数化数值**：HP、伤害、经验、掉落数量全用整数；命中率/闪避率用“千分位整数”比较，不用浮点概率累积。
4. **回合顺序由事件序决定**：同一 tick 内按 `seq` 排序，客户端按 `seq` 播放战斗日志；不允许按墙钟排序。
5. **回放只重放 Effect**：`replay_into()` 已经不重跑 `Action`（[persistence.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/persistence.py#L57-L69)），所以只要 Effect 足够表达战斗结果，回放就与实时一致。

**代价（必须提前接受）**：

| 代价 | 说明 |
|---|---|
| 事件量放大 | 一场 10 回合战斗 ≈ 10–30 条事件；按 1.8 KB/事件（`docs/CAPACITY.md` §5），单场约 18–54 KB；高频刷怪会把日增磁盘从 MB 级推到 GB 级 |
| 写锁占用变长 | 每回合一条事件 = 每回合一次 `clone()+append`；若一场战斗 20 回合，单玩家会把写锁占用放大 20 倍 |
| 实时感是“播放”不是“等待” | 服务端可以按 tick/事件立即结算，客户端逐行播放日志；玩家感知像回合制 MUD，但世界时间不会真的逐秒走 |
| 回放启动变慢 | 战斗事件越多，`replay_into()` 启动越慢；需要“快照 + 尾段事件”的派生缓存（仍以事件为唯一事实源，快照只是可重建缓存） |
| 平衡与通胀 | 掉落必须走 `item_create`，而货币只有 genesis 可 `mint`；无限制掉落会破坏封闭经济（`items_created/items_burned` 是现有审计口径） |

### 3.4 战斗落地的三种技术方案

| 方案 | 做法 | 确定性 | 手感 | 事件量 | 适用 |
|---|---|---|---|---|---|
| **A-atomic（推荐先做）** | 一次 `fight` 动作内跑完 N 回合，写 1 条事件 + `combat_log` Effect（逐回合文本） | 完全确定，天然可回放 | 服务端即时结算，客户端逐行动画；不是“等 3 秒一回合” | 1 条/场 | P1 最小闭环、单机/小规模 |
| **A-round-events** | 一回合一条事件，同一 tick 多条 seq；客户端按 seq 流式播放 | 完全确定 | 接近 xkx 的逐回合日志 | 10–30 条/场 | P2 以后，需要真实“逐回合”观感 |
| **B-live（分叉才做）** | 独立战斗循环按墙钟推进，回合触发时间写入事件 | 日志确定，实时调度不确定但被记录 | 真正逐秒 | 10–30 条/场 + 调度事件 | 仅当“逐秒实时”是硬需求 |

**推荐**：先 A-atomic，把“能打、能掉、能死、能回放”跑通；等玩家验证有乐趣，再升级 A-round-events。B-live 不应在第一版出现。

---

## 4. 规模与性能

### 4.1 当前实测预算（不重复造数）

**世界自走（无玩家压力）**

| 口径 | 事件/日 | 唤醒/日 | 事件/唤醒 | 说明 |
|---|---|---|---|---|
| 31 NPC / 9 地点 / 10 日 | 358.8 | 40.8 | 8.8 | `runs/a3-model-10d-31npc/summary.json` |
| 31 NPC / 2 日 | 367.5 | — | — | 735 events / 2 days（`docs/CONTENT-SCALE.md`） |
| 60 NPC / 20 地点 / 2 日 | 995.5 | — | — | 1991 events / 2 days |
| 60 NPC / 20 地点 / 10 日 | 601.4 | — | — | 6014 events / 10 days |

**玩家压力（`docs/CAPACITY.md` §5–§7）**

| 席位 | 读 P95 | 写动作 P95 | 写吞吐 | CPU | 结论 |
|---|---|---|---|---|---|
| 50 | 3.5 ms | 17.6 ms | 23.4/s | 18.9% | 健康 |
| 100 | 11.9 ms | 241 ms | 45.8/s | 55.4% | 安全线 |
| 150 | 13.9 ms | 958 ms | 61.5/s | 91.0% | 劣化区 |
| 200 | 20.9 ms | 3.07 s（5 分钟稳态 4.97 s） | 61.4/s | 100% | 过载 |
| 300* | 21.9 ms | 7.57 s | 55.3/s | 118% | 实验口径，不可用 |

> `*` 300 席为越界实验，只用于观察拐点，不作为容量承诺。

- `WorldState.clone()`：200 席时 3–4 ms（[state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L407-L415)）。
- `EventStore.append`：0.2–0.4 ms；SQLite 不是瓶颈，全局写锁才是（`docs/CAPACITY.md` §3）。
- 磁盘：61 写/s 时 0.108 MB/s ≈ 5.3–9.3 GB/天；约 1.8 KB/事件。
- `seq_gap` 全档位 0；SQLite WAL 稳定。

### 4.2 扩到 1000 房间 / 数百 NPC 会怎样（从代码推出的瓶颈，不是预测数字）

| 维度 | 现在的成本来源 | 扩到 1000 房间 / 300 NPC 的方向 | 需要实测的量 |
|---|---|---|---|
| 自走调度 | LOD 最小堆，每 tick 只处理到期唤醒（[lod.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/lod.py#L165-L177)） | 唤醒数与 NPC 数近似线性；`spotlight` 占比可能随热度上升而抬高（实测 31 NPC 已占 72.5% 唤醒） | 300 NPC 的 `wakes/day`、`events/day`、`spotlight` 占比、`decide` 耗时 |
| 每 tick 成本 | 每 tick 一次 `state_hash()`（[scheduler.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/scheduler.py#L697-L707)） | `state.hash()` 不含房间，含全部实体/商业/任务/账户；实体数 ×10 时哈希近似线性增长 | 单次 `state.hash()` ms vs NPC 数；1x/2x/4x 下可持续 tick/s、CPU 占比 |
| 写路径 | 每写一次整世界 `clone()` + append（[writer.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/writer.py#L127-L155)） | `clone()` 随实体/商业/任务/账户增长；战斗多回合会放大写次数 | `clone()` ms vs (rooms, NPCs)；100 席下写 P95；锁持有时间拆分 |
| 事件量 | 每唤醒约 8.8 条事件（31 NPC 实测） | 300 NPC 约 10 倍唤醒 → 约 10 倍事件；战斗再乘 10–30 条/场 | 100/300 NPC 的 10 日事件数；战斗事件条数与字节数 |
| 读延迟 | 快照读不持锁；但 `player_state` 会算价格/地点/店铺/在场视图（[session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L614-L631)） | 房间/商业体越多，价格与店铺视图越重；现在已按 tick 做缓存 | 1000 房/300 NPC 下 `/api/player/state` P95、价格缓存命中率、单次视图 ms |
| 回放 | 启动全量 `read_all()` + 逐事件 `apply_effects`（[persistence.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/persistence.py#L57-L69)） | 战斗事件会把日志膨胀 10–30 倍；启动时间线性增长 | 1 万/10 万/100 万事件回放耗时与内存；快照+尾段方案的收益 |
| 寻路 | 每次规划/移动 BFS 全图（[common.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/common.py#L388-L404)） | 1000 房时 O(V+E) 每次；NPC 唤醒 × 规划次数会放大 | `path_between` p95 @1000 房；是否需要全源预计算/分层图 |

**一个容易误判的点**：`state_hash` **不含** `locations`（`WorldState.to_dict()` 没有把 `locations` 放进去，见 [state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L417-L450)），所以“1000 个房间把每 tick 哈希拖慢”这个直觉**不成立**；真正被房间拖慢的是 BFS、读视图、`full_hash()` 与内存。反过来，NPC/商业体/任务数会直接拖慢每 tick 哈希与 `clone()`。

### 4.3 必须实测的清单（上线前逐项打勾）

1. **状态规模矩阵**：rooms `{9, 50, 200, 1000}` × NPC `{31, 100, 300}`，测 `clone()` ms、`state.hash()` ms、`full_hash()` ms、RSS。
2. **每 tick 预算**：`_process_tick` 单 tick 的 `state_hash + status 写盘` 耗时；1x/2x/4x 下能维持的 tick/s 与 CPU。
3. **LOD 放大**：300 NPC 跑 10 日，记录 `processed_wakes / events_total / decide_by_tier`；确认 `spotlight` 占比与 LLM 调用上限。
4. **战斗事件放大**：一场 10/20/30 回合战斗的事件条数、`effects` 字节数、WAL 增量；按目标战斗频率推算日增磁盘。
5. **回放启动**：1 万 / 10 万 / 100 万事件的 `replay_into` 耗时与峰值内存；验证“快照 + 尾段事件”能否把启动压到可接受范围。
6. **读延迟**：1000 房 / 300 NPC 下 100 席的 `/api/player/state` P95；价格缓存命中率；单次 `_location_view` / `_shop_view` ms。
7. **写延迟**：100 席下分别用“1 条战斗事件”和“20 条回合事件”压测写 P95、锁持有时间、队列深度。
8. **BFS**：1000 房时 `path_between` p95；NPC 规划频率 × BFS 次数是否成为 CPU 主项；是否要加全源最短路缓存。
9. **不变量**：`seq_gap=0`、钱包合计 delta=0、`integrity_check=ok` 在战斗高频下仍成立。
10. **基线保护**：新增字段前后跑 `tests/test_p3e_golden.py`，确认无玩家 2 日世界逐字节不变。

---

## 5. 三条路线对比

### A：同一套引擎 + xkx 内容包（核心增量，不推翻架构）

**严格按“不动核心、只加 content/actions”不可行**：战斗需要 `Entity.hp` 与 `EFFECT_OPS` 的伤害/死亡原语，这两处都在核心（[state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L18-L44)、[effects.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/effects.py#L44-L98)）。因此这里的 A 指 **A′：同引擎 + 增量核心（新字段/新 Effect/新动作/新内容包）**。

| 项 | 内容 |
|---|---|
| 改动面 | `state.py`（条件序列化的 `hp/attrs/spawns`）、`effects.py`（`hp_damage/death/spawn`）、`actions/combat.py`、`player.py` 白名单、`content/packs/xkx/**`、golden 新增基线 |
| 保留资产 | 事件溯源、单写者、PRNG、replay、门派/任务/经济/NPC 调度、现有测试套件、API/UI |
| 确定性 | 完全保留（战斗事件化 + 整数化 + 种子化） |
| 上限 | 受单写者、`clone()`、tick 粒度限制；千房间/数百怪需要分区与性能改造 |
| 代价 | 核心文件被持续扩展，新字段必须遵守条件序列化；战斗子系统会成为第二大模块 |
| 适合 | **第一版就选它** |

### B：从 core 分叉独立服务（复用事件溯源 + 单写者，战斗/技能/心跳另写）

| 项 | 内容 |
|---|---|
| 改动面 | 复制/抽取 `core`（models/canonical/rng/clock）、`world`（state/writer/store/effects）、`sim`（lod/scheduler/headless），新写 `combat/`、`skills/`、`spawn/`、新 API、新 UI、新测试 |
| 保留资产 | 事件模型、PRNG、Writer、SQLite 日志；**不保留**现有内容、会话 API、UI、现有测试的直接覆盖 |
| 确定性 | 可以保留，但需要重新建立 golden 基线；双服务长期漂移风险高 |
| 上限 | 可以按真实时间 1 秒 tick 重设计，不受现有 10 分钟时钟约束 |
| 代价 | 内容加载器、动作体系、玩家 API、权限/账号、店铺/经济全部要重做或搬运；维护两套事件语义 |
| 适合 | 只有当“逐秒实时 + 千房间 + 数百怪同屏”是**不可协商**的产品硬需求时 |

### C：不做机制复刻，只做“风格复刻”

| 项 | 内容 |
|---|---|
| 改动面 | 只做客户端文字壳 + 只读聚合；复用 9 房间/31 NPC/27 任务/经济 |
| 玩家可见 | 滚动文字、`look/go/i/score/help/ask`、真实地点/NPC/物价/背包；**没有战斗、没有技能等级、没有属性** |
| 确定性 | 零风险 |
| 成本 | 最低（约 2 个 agent × 2 轮可出可玩壳） |
| 代价 | 不是 xkx：没有“练功/打架/死/掉宝”的核心循环 |
| 适合 | 先验证“文字 MUD 的观感是否有人买账”，作为 A 的 P0 阶段 |

### 推荐

**推荐 A（A′），并用 C 的壳做第一阶段。**

理由：

1. **架构不阻碍战斗**：事件溯源 + 种子化 PRNG + 同 tick 多事件，足以表达回合战斗；不需要分叉。
2. **分叉的代价被低估**：B 会丢掉现有内容、API、UI、测试和“单写者已调优”的成果；而它想解决的问题（逐秒、千房间）在 A 的 P5 也能通过分区/性能改造解决，且那时已经有真实玩家数据支撑是否值得。
3. **C 单独不成立**：风格壳能玩 30 分钟，但没有成长/战斗/掉落就没有 xkx 的留存结构；所以 C 只能作为 A 的 P0。

**“玩家能进去玩”的最短路径（P0 + P1，约 5 个 agent-round）**：

1. 客户端文字壳接现有 `/api/player/state` + `/api/player/action`：`look / n / s / e / w / i / score / help / ask`（真实 9 房间、31 NPC、背包、钱包、体力）。
2. 3 个房间内放 2 个怪物模板，新增 `fight` 动作、`hp/max_hp`、`hp_damage/death`、掉落 `item_create + transfer`、`system_spawn` 刷新。
3. 验收：玩家能 `look` 看到真实房间描述与出口；`go` 真写事件；`fight` 把怪 HP 打到 0；`loot` 进背包；`score` 显示 HP；同 seed 两次 run 的 state/event hash 一致；重启后能回放。

---

## 6. 分阶段计划（按“阶段结束时玩家能看见什么”切）

> 工作量口径：以“当前并行 agent 产能”为单位，`x agent × y 轮` 是粗估（±50%），不是承诺。
> 每阶段都必须先过 golden/回放，再谈体验。

### P0｜看得见的文字壳（2 agent × 2 轮）

- **范围**：滚动文本区、命令输入、房间块（标题/长描述/可见 NPC/出口句）、`look/go/i/score/help/ask`。
- **模块**：`ui/player`（或独立 text client）；只读复用 [session.py `_location_view`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L1593-L1656) 与 `_present_view`；`move/talk` 走现有动作。
- **玩家看见**：真实的 9 个地点、31 个 NPC、41 件物品、真实物价与背包；输入 `n` 会真移动并掉体力；`look` 输出房间名/描述/出口/NPC。
- **验收**：9 个房间全部可达；`move` 事件真实入库；`i` 与 `score` 数字来自世界状态而非前端伪造；无战斗。
- **风险**：房间长描述内容为空，需要补 9 段文案；NPC 列表需要只读聚合（不改 API 契约则放客户端过滤）。

### P1｜最小战斗闭环（3 agent × 3 轮）

- **范围**：`fight` 动词；`Entity.hp/max_hp`；`hp_damage / death` Effect；2 个怪物模板、3 个房间；掉落；`system_spawn` 刷新；战斗日志文本。
- **模块**：`state.py`、`effects.py`、`actions/combat.py`、`actions/player.py` 白名单、`content/packs/xkx/`、`tests`（新增 golden）。
- **玩家看见**：`score` 有气血；`fight` 逐行输出“你击中 / 对方反击”；怪死掉落后 `i` 多出物品；玩家 HP 归零后进入复活流程；怪按刷新间隔重新出现。
- **验收**：能 kill 至少 1 种怪；掉落真实进背包；同 seed 两次 run 的 state/event hash 完全一致；`replay_into` 能重建战斗后的世界；无玩家 2 日 golden 不变。
- **备注**：这是“玩家能进去玩”的完成线。

### P2｜成长：属性 / 技能 / 装备（3 agent × 3 轮）

- **范围**：七属性、技能等级/经验、`learn/practice`、装备攻防加成、`score` 详情页。
- **模块**：`state.py`（`attrs/skill_levels`）、`effects.py`（`attr/skill_xp`）、`actions/skill.py`、`actions/equipment.py`、战斗公式、内容技能表。
- **玩家看见**：`score` 显示膂力/悟性/根骨/身法等；技能从“初学乍练 3/100”往上升；练功消耗体力；换武器后打同一只怪伤害变化。
- **验收**：至少 6 项属性、3 条技能线、5 件装备参与结算；属性/技能对命中和伤害有可观测差异；全部数值可回放。

### P3｜门派、师徒与师门任务（2 agent × 3 轮）

- **范围**：入门/拜师/收徒、门派贡献、5 条师门任务链、门派技能传承。
- **模块**：`Faction`、`sects.yaml`、`player_sect.py`、`quests.py`、`quest_accept` 前置、内容包。
- **玩家看见**：能加入门派、拜师；师门任务面板；完成给贡献与门派技能；`score` 显示门派/师父。
- **验收**：3 个门派可加入；拜师/收徒成功入库；5 条师门任务可接可交；贡献值可查。

### P4｜内容扩容到“像一张地图”（2 agent × 4 轮 + 内容生成）

- **范围**：100+ 房间、50+ NPC/怪、30+ 物品、20+ 任务、区域地图、分级 help、`ask` 主题库。
- **模块**：`content/packs/xkx/**`（locations/npcs/items/quests/personas）、地图客户端、help 客户端；引擎只做必要的加载与校验。
- **玩家看见**：3 个区域、100+ 房间可走；`map` 给出文字地图；`help` 三级下钻；NPC 有可问主题；任务有明确发布人/目标。
- **验收**：无孤立房间；所有 NPC 有 persona；20 条任务可完成；读图/走图不超时。

### P5｜千房间 / 数百 NPC 的容量验证（2 agent × 3 轮）

- **范围**：1000 房间、300 NPC/怪、LOD 与刷新；按 §4.3 清单实测；必要时分区。
- **模块**：`content`（房间图）、`state.py`/`writer.py`（如需要快照/分片）、`scheduler.py`、`tools/` 压测脚本。
- **玩家看见**：地图规模真的变大；跨区域移动、怪刷新、任务链都不断；读延迟不退化。
- **验收**：100 席下读 P95、写 P95、CPU、`seq_gap`、回放启动时间均达到事先写死的目标；达不到则明确分区，不许用“优化中”糊过去。

---

## 7. 明确做不到 / 建议砍或替代

| # | xkx 特性 | 现状与原因 | 建议 |
|---|---|---|---|
| 1 | 逐秒实时战斗心跳 | tick=10 分钟（[clock.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/clock.py#L7-L8)）；改时钟会重写全部内容标尺与 golden | 砍“逐秒等待”，保留“逐回合日志 + 客户端播放”；真要逐秒走 B 分叉 |
| 2 | 千房间 + 数百怪同屏 | 单进程单写者，实测 200 席写 P95 近 5 秒（`docs/CAPACITY.md`） | 先 100 房间/50 怪验证；千房间做分区，别单进程硬扛 |
| 3 | 物品实例 / 容器 / 耐久 / 重量 | 物品是 `{item_id: qty}`（[state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L27)），`_owner_stock` 只认 entity/business（[effects.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/effects.py#L140-L145)） | 第一版不做容器/耐久；需要时新增 `ground:<loc>` / `container:<id>` 持有者，单独立项 |
| 4 | 玩家 PK / 偷窃 / 抢劫 | `PLAYER_VERBS` 明确禁止 L3；且全局写锁会把 PvP 串行化 | 砍；如要做，先做“同意决斗房”，单独评审 |
| 5 | 全部门派武功树 / 迷宫 / 千房间剧情 | 不是引擎问题，是内容产能问题；31 NPC 已有 30 张 persona 卡，1000 房间需要同量级文案 | 先 3 门派 × 3 技能线 × 100 房间；剧情用模板 + persona，不逐房手写 |
| 6 | 尸体 / 地面掉落持久化 | 无 ground/container owner，掉落只能进背包 | 第一版“击杀直接入包”；后续再做尸体容器 |
| 7 | 无限制随机掉落 | 货币/物品有封闭经济审计（`mint` 仅 genesis，[effects.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/effects.py#L44-L103)） | 掉落表必须配 `items_created/items_burned` 预算与日上限 |
| 8 | 玩家间聊天 / 组队 / 交易 | `talk` 只对 NPC；无玩家间消息模型 | 砍出第一版；需要时在会话层加频道，不动世界状态 |
| 9 | 银行 / 钱庄存取 | 有账户与 `withdraw`（店铺提现），无玩家钱庄 | 用现有 `accounts` 做“钱庄账户”，P3 再加 |
| 10 | 天气 / 精细饥渴 | 只有 `stamina` 与 `day` | 天气可用系统事件做展示，不参与结算；饥渴先映射到 stamina |
| 11 | 全量 xkx 任务/解谜脚本 | 现有任务模板是声明式（`verb + intent_args + reward`），复杂脚本需要新 DSL | 先做声明式任务；复杂链用多模板串联，不写脚本引擎 |
| 12 | 多人实时同房间观感 | 单写者 + 快照读，房间内玩家列表是派生视图 | 可以做，但高并发下读的是快照；不要承诺“毫秒级同步” |

---

## 8. 证据与复现

### 8.1 关键代码索引

| 主题 | 文件/函数 |
|---|---|
| Intent/Effect/Event | [models.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/models.py#L14-L99) |
| 单写者提交 | [writer.py `submit()`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/writer.py#L88-L156) |
| 事件表 | [store.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/store.py#L15-L80) |
| 回放 | [persistence.py `replay_into()`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/persistence.py#L57-L69) |
| 确定性 PRNG | [rng.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/rng.py#L1-L56) |
| 规范化 | [canonical.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/core/canonical.py#L13-L34) |
| 状态/克隆/哈希 | [state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L372-L474) |
| Effect 原语 | [effects.py `EFFECT_OPS`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/effects.py#L44-L98) |
| LOD | [lod.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/lod.py#L165-L234)、[lod.yaml](file:///Users/lyinet/projects/XKX/world-sim/config/lod.yaml#L5-L20) |
| 每 tick 调度 | [scheduler.py `_process_tick`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/scheduler.py#L607-L687) |
| 每 tick 全量哈希 | [scheduler.py `_write_progress`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/scheduler.py#L697-L707) |
| 玩家动作与白名单 | [session.py `perform`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L2110-L2152)、[player.py `PLAYER_VERBS`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/player.py#L44-L89) |
| 房间/店铺视图 | [session.py `_location_view`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L1593-L1656) |
| 寻路 BFS | [common.py `path_between`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/common.py#L388-L404) |
| 能力位路由 | [providers.py `CAPABILITIES`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/admin/providers.py#L33-L59) |
| golden 红线 | [test_p3e_golden.py](file:///Users/lyinet/projects/XKX/world-sim/tests/test_p3e_golden.py#L1-L59) |

### 8.2 复现命令

```bash
cd world-sim

# 确定性基线
PYTHONPATH=src python -m pytest -q tests/test_p3e_golden.py

# 容量实测
.venv/bin/python tools/capacity_sweep.py --levels 50,100,150,200 \
    --duration 90 --think-time 2 --label xkx-eval --out reports/capacity
PYTHONPATH=src .venv/bin/python tools/capacity_probe.py --players 150 --duration 45

# 世界自走基线（31 NPC / 10 日；14400 tick = 10 × 144）
python tools/ambient_run.py --run-id eval-10d --ticks 14400 --seed 42 --speed 4.0

# 内容校验
python content/validate_content.py --no-pack
```

> 注：`ambient_run.py` 支持 `--ticks`（固定 tick）或 `--minutes`（配速实时），不支持 `--days`；10 游戏日 = 14400 tick。本文未执行任何会改状态的命令。

### 8.3 未做的验证（必须诚实标注）

- 未实测 1000 房间/300 NPC 的 `clone()`、`state.hash()`、BFS、回放耗时。
- 未实测战斗事件放大后的写锁占用与磁盘增长。
- 未实测“同一 tick 多条战斗事件”在 100 席并发下的锁队列深度。
- 未验证 xkx 式掉落表在现有封闭经济下的通胀影响。

这四项是 P1/P5 的准入条件；在实测完成前，不应向玩家承诺“千房间/实时战斗”的规模。
