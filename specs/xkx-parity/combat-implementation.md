# 战斗与属性落地说明（P2 引擎侧）

对应 `plan.md` §5。本文记录本次实际落地的字段、原语、回合模型、确定性证据与兼容边界。

## 1. 属性与技能迁移

`Entity`（`src/wsim/world/state.py`）新增，全部带默认值：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `hp` / `max_hp` | int | 0 | `max_hp=0` 表示“无战斗属性”，旧实体保持此状态 |
| `attack` | int | 0 | 基础伤害（兵器/拳脚），结算时 0 按 1 |
| `defense` | int | 0 | 护甲减伤 |
| `attrs` | dict[str,int] | `{}` | `str/int/con/dex/sta/...`，缺省按 10 |
| `combat_exp` | int | 0 | 战斗经验（整数） |
| `skill_levels` | dict[str,int] | `{}` | 技能等级 0..800 |
| `skill_xp` | dict[str,int] | `{}` | 技能经验 0..99，满 100 升 1 级 |
| `death_tick` / `death_location` | int/str | 0 / `""` | 死亡事实，复活时清零 |

**旧存档兼容（关键红线）**

1. `to_dict()` 只在这些字段非空/非零时输出：旧内容包 `create_entity` 不提供 `max_hp`
   等键，实体保持默认值，世界哈希**不新增任何键**（`tests/test_xkx_determinism_legacy.py`
   锁定 735 events / `009076f0…` / `02d5f812…`）。
2. 旧 `skills: dict[str,float]`（0..1）**保持原语义、只读不改**；战斗读取时按
   `int(value * 100)` 截断成等级（不四舍五入），因此旧 NPC 也能参战。
3. 新内容优先写 `skill_levels` / `skill_xp`；两者与旧 `skills` 并存，互不覆盖。
   迁移期不需要一次性重写存档：没有新字段的实体自动走旧浮点回退。

## 2. Effect 原语（纯投影）

`EFFECT_OPS`（`src/wsim/world/effects.py`）新增：

| 原语 | data | 语义 |
|---|---|---|
| `hp_add` | `{entity, delta}` | 伤害/治疗，clamp `[0, max_hp]` |
| `hp_set` | `{entity, value}` | 绝对设置（创世/特殊恢复用），同样 clamp |
| `entity_status` | `{entity, status, tick?, location?}` | 死亡/复活标记；`dead` 要求 hp 已为 0 |
| `revive` | `{entity, hp}` | 仅死亡实体可用；置活、回血、清空 death 字段 |
| `loot_create` | `{entity, item, qty}` | 掉落入背包，计入 `items_created` |
| `combat_exp_add` | `{entity, delta}` | 战斗经验增长 |
| `skill_xp_add` | `{entity, skill, delta}` | 技能经验增长，自动升级（旧口径） |
| `skill_progress` | `{entity, skill, level, learned}` | 公式库 `improve_skill` 的落值载体（等级 0..800 + 熟练度） |
| `combat_log` | 任意战报 | 只读载体，不修改状态（与 `shop_report` 同类） |

所有分支只读事件参数，不做随机、不读墙钟。`entity_status(dead)` 在 `hp>0` 时直接
`EffectError`，从原语层堵死“绕过伤害直接置死”。`fight` 动作只返回 Effect，
`Writer` 再 clone + `apply_effects`，不存在直接改 state 的路径。

**伤害增量与状态必须逐点自洽**：`hp_add` 的钳制（下限 0、上限 `max_hp`）只在
Effect 里做一次，并把钳制后的**实际**增量回写进 `Effect.data["delta"]`，因此
事件里记的增量恒等于状态变化（超额部分不写进增量，只在 `combat_log.overkill`
如实标注）。`normalize()` 在入口给了一份副本，回写同时落到 `d` 与 `e.data`。
回归测试：`test_hp_add_overkill_is_clamped_into_the_event_delta`（单次超杀）、
`test_hp_add_multi_hit_overflow_accumulates_to_state_delta`（多段累计溢出）、
`test_hp_add_on_zero_hp_records_zero_delta`（0 血再受击）、以及 `fight` 事件级
“`sum(hp_add.delta) == 状态变化`”断言。

**`overkill` 的出处（原版没有“超额伤害”文案）**：原版 `feature/damage.lpc:30-33`
把 `qi` 钳到 **-1**（`val>=0 ? set(type,val) : set(type,-1)`）；
`adm/daemons/combatd.lpc:552` 的 `receive_damage` 原样返回请求伤害，战报只用
`damage_msg(damage, type)`（`combatd.lpc:569`），不显示溢出。我们的
`combat_log.overkill = -min(0, hp_raw)` 是“模拟层未钳制 hp”与“state 层钳 0”之差，
**只作对拍审计**：不参与任何状态计算、不消耗随机流、不进玩家文案（`xkx_shell`
里那行“超额伤害：N 点”已删除）。它与“有效气血 -1 下限”不是同一机制；那条线落地后
以它为准，本字段随之作废，禁止拿它当死亡阈值或二次扣血。

## 3. `fight`：原子回合模型 + 公式库接入

- 实现：动作 `src/wsim/actions/combat.py::FightAction`；数值层
  `src/wsim/sim/combat_rules.py`。
- **公式只有一份来源**：`combat_rules` 顶部把 `tools/` 加入 `sys.path` 后
  `import xkx_formula as xf`，命中、闪避、招架、伤害、护甲、膂力、经验压制、
  扣血、伤势、死亡、经验与技能成长**全部**调用库函数；本模块不再保留任何
  公式实现，只做 `Entity -> 库参数` 的适配与调用排序。
  回归锁：`tests/test_combat_fight.py::test_formulas_are_delegated_to_xkx_formula`
  把 `xf.resolve_dodge` 换成必闪后，战果立刻变成 20 回合全闪避、`resolve_damage`
  零调用——说明引擎侧没有第二份可绕过的实现。
- **一次动作 = 1 条事件**：最多 20 回合全部在同一次 `apply()` 内结算，事件
  `effects` 末尾是一条 `combat_log`，其 `data.rounds[]` 即完整战报（结构未变）。
  不存在“每回合一条事件”，`max_rounds` 只能调小（`>20` 被硬截断）。
- 调用顺序严格按 `INTERFACE.md` §2，并在最前面加一步原版选招：
  1. **选招**：`reset_action()` → `query_action()`，一笔 `random(seq)`；
     有映射技能用技能招式，否则回落种族 `combat_action`；
  2. `skill_power(attack)` / `skill_power(dodge, DEFENSE)`；
  3. `resolve_dodge` →（闪避）`dodge_rewards`；
  4. `select_parry_power` → `resolve_parry` →（招架）`parry_rewards`；
  5. `resolve_damage`（内部：武器伤害 → 护甲 → 膂力 → 经验压制 → 空明 → 威力系数）；
  6. `apply_qi_damage` → `roll_wound` →（受伤）`apply_qi_wound`；
  7. `damage_rewards` → `death_state`。
- 引擎侧自己算、不进库的量（库第 4 节要求）：
  - 攻击技能名：`intent.args.skill` 优先，其次 `role` 命中技能表，否则 `unarmed`；
    具体武功走 `Entity.map_skills[基础技能]`（原版 `query_skill_mapped`）；
  - 无映射技能时用种族 `combat_action`（人类 5 条、走兽 3 条；走兽 `damage`
    50/30/30 来自原版 `race/beast.lpc`，不新增伤害来源）；
  - 先手：`dex + dodge 等级` 大者先手，平手由发起方先手（xkx 由心跳队列决定，
    引擎无行动队列，这是引擎规则、不是 xkx 公式）；
  - `power_point` 千分位：读 `attrs["power_point"]`，缺省 `1000`（=1.0，中性值）；
    不按等级自行推导（见 §7 第 6 项）；
  - `jing`：用 `hp` 近似（气血/精神未拆分，见 §7 第 5 项）。
- 死亡/掉落：目标倒下后同事件内依次写 `entity_status(dead)`、`loot_create`
  （掉落表来自 `intent.args.loot`，内容侧负责提供）；玩家死亡额外写
  `player_presence(custody)`（需已有玩家档案）。
- 经验与技能成长：`dodge_rewards` / `parry_rewards` / `damage_rewards` 给出增量，
  `improve_skill` 给出新等级/熟练度，动作层分别落成 `combat_exp_add` 与
  `skill_progress`。`potential` 未建模，奖励里的潜能增量忽略（见 §7 第 8 项）。
- 随机：`Writer` 注入 `PRNG(seed, seq, salt="action:fight")`；`TraceRandom` 把
  `rng.randint(0, n-1)` 适配成库的 `RandomSource.below(n)`（`n<=0` 记一次调用、
  返回 0、不消费随机流）。取数顺序为 **选招在最前**：
  `move(query_action) → dodge → parry → weapon_damage → armor → strength → exp_check →
  wound(护甲) → wound(killing 组) → damage_rewards`。无映射技能且无 `race` 的实体
  不选招，原 9 次取数顺序保持，锁在
  `tests/test_combat_fight.py::test_prng_draw_order_is_locked`；有招式路径锁在
  `tests/test_combat_moves.py`（首笔 `(2,1)`）。
- **没有暴击**：xkx `combatd` 全仓检索 `暴击`/`critical` 零命中，本实现不引入暴击；
  战报断言里显式禁止出现 `critical`/`暴击` 字样（`test_fight_writes_one_event_*`）。

## 4. 刷怪

- 内容：`content/spawns.yaml`（`spawns:` 列表）；旧内容包没有该文件 → `[]`。
- 逻辑：`src/wsim/sim/spawn.py`；动作 `SystemSpawnAction`（`system_spawn`）。
- 首次 `create_entity`；死亡后 `tick >= death_tick + respawn_ticks` 时 `revive`。
- **空转不写事件**：`spawn_due()` 为假时调度层根本不提交意图；
  `tests/test_xkx_determinism_spawn.py` 覆盖空 spawns、全部存活、未到期三种阻断场景，
  并验证有声明时第一天恰好 1 条 `system_spawn`。

## 5. 确定性证据

| 证据 | 值 / 测试 |
|---|---|
| 旧 golden | `tests/test_p3e_golden.py` 两条 V6 测试保持绿（791 events，`6be665b…` / `0c15b0d…`） |
| 旧无玩家世界 | 735 events，state `009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9`，event `02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`，business_cash 11442 |
| 新战斗基线 | 4 events（genesis + spawn + fight ×2），state `91097d90…`（未变），event `273b5455003ce199f46d2b7385a560f83ea3ca1281b9cdf8803845ce11cfc929`（招式接入后因 `rounds[]` 新字段重采，`tests/test_xkx_determinism_combat.py`） |
| 同 seed 双跑 | 新基线测试内双跑 state/event 哈希逐字节一致 |
| 回放等价 | 从 `events.sqlite` 逐条 `apply_effects` 重建的投影与实跑 state 哈希一致 |
| 战斗账本自洽 | 干净 worktree（`b587b60`）审计第 7 项 PASS：`effect_sum == state_delta`（120→112 `-8/-8`，70→0 `-70/-70`），`problems=[]` |
| PRNG 锁序 | `test_prng_draw_order_is_locked` 固定 9 次 `(n, value)` 对，顺序或流变化立即失败 |

event 哈希两次采样说明：切库时先采 `bb1698b…`；随后修 `hp_add` 超额伤害的账本
不自洽（delta 回写实际值，如 -41 → -40）后重采为 `046a133…`，state 哈希**未变**
（`3eae4e85…`），证明这次修复只动账本、不动投影。

招式接入说明：2026-11-07 接入招式名与技能钩子后，`state_final` 仍是 `91097d90…`
（合成场景实体没有 `map_skills`/`race`，不选招、不掷点、不新增伤害）；`event_final`
因 `rounds[].actions[]` 增加 `action_skill/selection/move/move_text/move_index/force_hit/
martial_hit/force_reflected` 而重采为 `273b5455…`，属于战报字段变化，不是数值变化。

基线迁移说明：`dc0aa76e…` / `7968aebf…` 是**引擎自维护公式**时代的采样值；切换到
`tools/xkx_formula` 后按“以库为准”重新采样，不是把数值调回去凑旧哈希。旧 golden
与旧无玩家世界的哈希**未变化**（战斗不进入那两条路径）。

## 6. 切换前后数值差异（同 seed=42 / seq=2 / salt=action:fight）

场次1：`player:hero`（100hp、attack 20、def 5、str 30、dex 20、unarmed 60）
vs `npc.boar`（40hp、attack 10、def 2、str 20、dex 15、unarmed 40），带掉落。

| 项 | 旧实现（`3218800`） | 新实现（库） |
|---|---|---|
| 回合数 | 2 | 1（R1 即倒下，反击不再发生） |
| R1 hero→boar | hit dmg **37**，hp 40→3 | hit dmg **41**，hp 40→0 |
| R1 boar→hero | dodge | 无（目标已倒下） |
| R2 hero→boar | hit dmg **26**，hp 3→0 | 无 |
| 受击后 eff_hp | 未记录 | 1（伤势生效） |
| 经验/技能 | 无任何增量 | boar `combat_exp +1` |
| 取数次数 | 15 | 9 |

场次2：低血英雄（10hp、attack 1、def 0、str/dex 10、无技能）vs 精英野猪
（200hp、attack 80、def 10、str 60、dex 30、unarmed 120、dodge 100）。

| 项 | 旧实现 | 新实现（库） |
|---|---|---|
| R1 boar→hero | hit dmg **113**，hp 10→0 | hit dmg **117**，hp 10→0 |
| winner / dead | defender / hero | defender / hero |
| 取数次数 | 6 | 9 |

差异归因（均已按“以库为准”保留，不回改）：

1. **伤势真正生效**：旧实现 `wounded` 只写战报；库按 `feature/damage.lpc:56-64`
   走 `apply_qi_wound`，压低 `eff_qi`（场次1 为 1），后续受伤路径因此不同。
2. **基础伤害不再抬到 1**：旧实现 `max(1, attack)`；库按源码原值，`attack=0` 时
   拳脚基础伤害可为 0（`random(0)` 也是 0），只剩膂力加成。
3. **命中/招架前先夹取**：库在 `random(ap+dp)` 前把 ap/dp `clamp_power` 到
   `[1, 2e8]`；旧实现只夹取 pp。
4. **死亡判定改用气血原值**：库 `apply_qi_damage` 扣穿时落 `-1`，再由
   `death_state` 分档；旧实现 hp 下限 0 且 `hp<=0` 即判死，所以“恰好 0 血”
   在旧实现立刻死亡、在库里还能再挨一击。
5. **经验与技能成长从无到有**：旧 `simulate_fight` 完全没有奖励分支；库按
   `combatd.lpc:361-373 / 417-423 / 575-591` 发经验、按 `feature/skill.lpc:172-204`
   算技能成长。这是本次切换最大的行为增量，也是新基线哈希变化的主因。
6. **取数流被重新分配**：两版共用同一 PRNG，但取数位置与次数不同（9 vs 15），
   所以单次数值不能逐位对齐；上表只列可比项。

## 7. 8 项“不可机械翻译”内容的引擎侧处理

| # | 库列出的项 | 引擎侧处理 | 状态 |
|---|---|---|---|
| 1 | 技能虚函数钩子（force/martial/weapon 的 `hit_ob`/`be_hit_ob`） | `force_hit`（6 个有 code 的内功共用 `inherit/skill/force.lpc hit_ob`）与 `martial_hit`（mantian-xing / yunlong-zhua，只上毒、整数增量 0）已接；`victim_force_hit`（`force.lpc be_hit_ob` 源码整段被注释）、`victim_parry_hit`（无实现）、`weapon_hit`（由武器对象 `hit_ob` 提供，技能表不可得）保持 0，不查表、不掷点 | 部分：三项 0 已写明原因 |
| 2 | 招式序列（`reset_action()` 的 dodge/parry/force/damage） | 已按原版 `query_action()` 选招：`seq=max{i|level>action[i-1].lvl}`、`seq=random(seq)`、取 `action[seq]`；系数走既有 `dodge_action_bonus/parry_action_bonus/action_damage/action_force`，无映射技能回落种族 `combat_action`；`special_actions`（perform）仍不自动触发 | 已接入普通招式；perform 另立任务 |
| 3 | 战斗状态位（`is_busy`、`bixie/cizu`、`bixie/ciwan`、`killing`、`weapon`、`apply/armor`） | `killing=True`（`fight` 即击杀模式）；`weapon` 读 `equipped["weapon"]`；`armor` 读 `Entity.defense`；`is_busy`/`cizu`/`ciwan` 无行动队列与临时标记，固定 `False` | 部分：`is_busy`/`cizu`/`ciwan` 为缺口 |
| 4 | 身份与存活判定（`userp`/`living`/`noliving`） | `player:` 前缀或 `state.players` 视为玩家；`living` 固定 `True`（死亡实体已被 `preconditions` 挡在战斗外）；`noliving` 未建模 | 部分：`noliving` 为缺口 |
| 5 | 轻功与 `power_point` 千分位系数 | 防御侧 `dodge_power_point_permille=None`（引擎无“映射轻功”概念）；攻击侧读 `attrs["power_point"]`，缺省 1000 | 部分：轻功映射为缺口；`jing` 用 `hp` 近似 |
| 6 | `receive_damage` 副作用（怒气、`heart_beat`、`last_damage_from`） | 只取 `apply_qi_damage` 的气血落值，副作用全部不建模 | 缺口 |
| 7 | 表现层随机（部位、胜负台词） | 不翻译；`rounds[]` 只记数值，不产文案随机 | 已排除（不影响数值） |
| 8 | 死亡后果 + 技能升级回调 + 奖励写回 + 追杀任务奖励 | 死亡只落 `entity_status(dead)` + 玩家 `custody`；无尸体/经验惩罚/`no_die`；`leveled_up` 无全局回调可调；奖励写回由 `combat_exp_add`/`skill_progress` 承担；`potential` 不建模；追杀任务奖励不做 | 部分：经验/技能已接，其余为缺口 |

另有一项库未列出但必须声明：**暴击**。xkx `combatd` 没有暴击公式
（`formula-map.md` §4.1），本实现不引入，战报与事件里不出现该字段。

待补清单（按依赖排序，均不在本次可交付范围）：
1. `special_actions`（perform 分支）与三项未实现钩子（`victim_force_hit` /
   `victim_parry_hit` / `weapon_hit`）——前者依赖任务/冷却/内力触发，后者源码被注释
   或由武器对象提供；
2. `is_busy` / `bixie cizu` / `bixie ciwan` 临时状态位（依赖行动队列）；
3. 轻功映射与 `power_point` 按技能求值；
4. `receive_damage` 副作用（怒气、`heart_beat`）与 `noliving`；
5. `potential` 字段与死亡后果（尸体、经验惩罚、昏迷计时）。

## 8. 范围说明与已知边界

- 为完成任务必须修改 `src/wsim/world/state.py` 与 `src/wsim/world/effects.py`：它们
  不在允许目录列表内，但也不在禁改清单（api/ui/config/tools/content）内。
- `tools/xkx_formula/**` **未修改**；接入中发现的问题只记录不改，随回报提交。
- `fight` 的 `target` 必须是 NPC；玩家对玩家 PvP 未开放（前置 `target_is_player`）。
- 掉落表由调用方/内容提供；内核不猜测尸体背包，避免物品重复计数。
- 跨场伤势不持久化：`eff_hp` 每场从 `max_hp` 起算，`qi/jing` 尚未拆分
  （`jing` 用 `hp` 近似）。
- `system_spawn` 是无内容时的“零事件”保障；若外部强行提交空 spawn 意图，
  `Writer` 仍会记一条 `effects=[]` 的事件（事件溯源框架的既有语义），因此事件侧
  约束由调度层负责，测试锁在 headless。

## 9. vitals / loot 里程碑（2026-11-07）

目标：让真实 xkx 世界（`content/xkx`，195 房 / 92 NPC）从“能走能买卖”推进到“能打一场并捡起掉落”。

### 9.1 玩家创世气血与属性

`src/wsim/actions/player.py` 的 `player_genesis_effects()` 与 `PlayerEnterAction` 两条创世路径
都会写 `max_hp/hp/attrs`。数值来源必须分开看：

| 字段 | 值 | 来源 |
| --- | --- | --- |
| `max_hp` | 100 | **原版**：`logind.lpc:917-925` 新玩家 `max_qi=eff_qi=100`；`race/human.lpc:116` `age<=14 → max_qi=max_jing=100`；`clone/user/user.lpc:59` 初始 `age=14` |
| `hp` | 100（= max_hp） | **原版**：新玩家 `eff_qi=max_qi` |
| `attrs.str`（膂力） | 游侠 20 / 商人 15 / 捕快 18 | **我们的默认**：xkx 登录掷点非确定，不可复刻 |
| `attrs.con`（根骨） | 20 / 16 / 18 | 同上 |
| `attrs.dex`（身法） | 18 / 15 / 17 | 同上 |
| `attrs.int`（悟性） | 15 / 20 / 17 | 同上 |
| `attrs.spi`（精神） | 15 / 18 / 16 | 同上 |
| `attrs.sta`（耐力） | 20 / 18 / 19 | 同上 |
| `combat_exp` | 6400 | **产品档位（不是原版新手档）**：原版技能 40 级的最低经验 `40^3/10 = 6400`（`cmds/skill/learn.lpc:102`；`practice/study` 同门槛） |
| `skill_levels.unarmed` | 40 | **产品档位**：与 6400 经验自洽的“40 级武艺”状态；原版由拜师学得，登录掷点非确定 |
| `skill_levels.dodge` | 40 | **产品档位**：与 unarmed/parry 同级，保证攻防两侧同一档位；原版 40 级闪避是合法可达值 |
| `skill_levels.parry` | 40 | **产品档位**：同上，避免“拳脚 40 级却只有 250 经验”的不自洽组合 |

- 身份键沿用既有 `ARCHETYPES`（游侠 / 商人 / 捕快）；预置账号无身份，用 `DEFAULT_ARCHETYPE="游侠"`。
- **2026-11-07 MID 档调整**：`combat_exp 250 → 6400`，`dodge/parry 10 → 40`。
  依据是原版技能-经验门槛 `skill^3/10 ≤ combat_exp`（`cmds/skill/learn.lpc:102`），
  `40^3/10 = 6400`；原版 MID 实测快照（`tools/xkx_ref/measure_hit_rate.py:6`，
  `reports/xkx-ref/hit-rate.md`）同样是 `exp 6400 / 40·40·40 / 无兵器`。

> **开局档位是产品选择，不是原版新手档。** 原版新手档（技能近 0、`combat_exp≈0`）的实测数据
> 保留在 `reports/xkx-ref/hit-rate.md` 的 NEW 档，将来可做“新手体验”对照。

- 旧默认行（已废止，2026-11-07 前）：`combat_exp=250`、`unarmed=40`、`dodge=10`、`parry=10`。
  废止原因是它在原版公式下不对应一个存在的角色：拳脚 40 级却只有 250 经验，导致
  `ap` 打不动人而 `pp` 又很难被打中。旧值仅作为 §9.1.1 的“改前”对照组保留。
- 经验压制公式（`modify_damage_by_exp`）保持原版逻辑不变，不因“好打/难打”而改公式。
- 旧玩家实体（`max_hp<=0`）在 `player_enter` 时补一次 `vitals_set`，8011 的既有存档也不会“没有血量”。
- **门控**：仅当内容包 NPC 带 `vitals/attrs/skills` 键（xkx 切片）时生效。旧 `content/`（九地点）
  没有这些键，玩家创世事件与旧 golden / 无玩家哈希逐字节不变。
- 世界配置 `player_start` 是唯一可调入口（`config/worlds/xkx.yaml`）；`WorldSession` 读取后
  注入 `enter` payload 与 genesis，动作层只接收归一后的值，不散落配置读取。

#### 9.1.1 MID 档复测（2026-11-07，seed 1–40）

口径：真实 `WorldSession.perform(enter/fight)`，每 `(对手, seed)` 独立 `run_dir`；
`npc_limit=0`、`endow=False`、`world_config=config/worlds/xkx.yaml`。
“对方命中率” = NPC 的 `hit / (hit+dodge+parry)`；玩家剩余气血取中位数（阵亡记 0）。

| 对手 | seed 数 | MID 胜/负/平 | MID 平均回合 | MID 玩家剩余气血中位数 | MID 对方命中率 |
| --- | --- | --- | --- | --- | --- |
| 店小二 | 40 | 40 / 0 / 0 | 12.18 | 100 | 0.00%（0/447） |
| 流氓 | 40 | 40 / 0 / 0 | 23.45 | 100 | 2.78%（25/898） |
| 小沙弥 | 40 | 40 / 0 / 0 | 23.88 | 63.5 | 14.10%（129/915） |
| 刀客 | 40 | 0 / 40 / 0 | 8.93 | 0 | 53.22%（190/357） |
| 绿营清兵 | 40 | 0 / 40 / 0 | 6.90 | 0 | 58.33%（161/276） |
| 老虎 | 10 | 0 / 10 / 0 | 7.50 | 0 | 100.00%（75/75） |

改前对照（`combat_exp=250`、`dodge/parry=10`，同一测量脚本、去掉回合封顶）：

| 对手 | 胜/负/平 | 平均回合 | 玩家剩余气血中位数 | 对方命中率 |
| --- | --- | --- | --- | --- |
| 店小二 | 40 / 0 / 0 | 12.10 | 100 | 0.45%（2/444） |
| 流氓 | 32 / 8 / 0 | 40.92 | 19.5 | 22.85%（374/1637） |
| 小沙弥 | 0 / 40 / 0 | 13.95 | 0 | 47.49%（265/558） |
| 刀客 | 0 / 40 / 0 | 4.62 | 0 | 98.92%（183/185） |
| 绿营清兵 | 0 / 40 / 0 | 4.03 | 0 | 98.76%（159/161） |
| 老虎 | 0 / 10 / 0 | 7.20 | 0 | 100.00%（72/72） |

结论与边界：

- 改前“全败”只适用于 **小沙弥 / 刀客 / 绿营清兵**（0/40）；店小二与流氓在 250 经验下本来就能赢。
- MID 档没有出现“打谁都稳赢”：刀客、绿营清兵、老虎仍然稳定击败玩家；小沙弥从 0/40 胜
  变为 40/40 胜，是本次档位调整的主要变化。
- 店小二对方命中率 0.00% 与原版 MID 实测一致（`reports/xkx-ref/hit-rate.md`：0/164），
  属于原版档位本身的行为，不是本次新增的难度问题；流氓 2.78% 也未降到 0。
- 同 seed 双跑一致（SQLite 文件 sha256 + 战报 `hash_obj(combat)` 完全相同）：
  - 店小二 seed=1：`file_sha256=76c4a597…6aedf`、`combat_hash=36db2e02…b7203`
  - 流氓 seed=2：`file_sha256=ad34c607…09a07`、`combat_hash=5b5b1120…f9e192`

### 9.1.2 出生点附近 NPC 主动攻击审计（2026-11-08）

**原版机制**：NPC 的主动攻击不在各 NPC 的 `create()` 里，而在继承的
`feature/attack.lpc:245 init()`：

- `:262` `userp(ob) && is_killing(ob->query("id"))` → `auto_fight`
- `:265` `vendetta_mark` 且玩家带对应 `vendetta` → `auto_fight`
- `:269` `userp(ob) && query("attitude") == "aggressive"` → `auto_fight`

`heroism` / `peaceful` / `friendly` 都不在这个分支里；有条件的主动攻击由 NPC 自己的
`init()` 调 `kill_ob`。绿营清兵 `/d/hangzhou/npc/qingbing.lpc`：`:13 combat_exp 20000`、
`:15 attitude "heroism"`、`:17-18 apply/attack|defense 30`、
`:20-23 club 60 / unarmed 50 / parry 50 / dodge 50`；**全文没有 `init()`、没有 `kill_ob`**。
所以原版它不是见人就打，只有玩家先动手（或已有 kill/vendetta 关系）才还手。

我们的对应实现：内容包保留 `vitals.combat_exp=20000`、四门技能、`changqiang` + `junfu`；
`aggressive:false`（`tools/xkx_extract/extract.py:1010` 按 `attitude=="aggressive"` 写入）。
但 `aggressive` **没有任何运行时消费者**（`grep -R aggressive src/wsim` 只命中抽取器）；
`FightAction` 只在玩家输入 `fight` 时触发（`src/wsim/actions/combat.py:43,70`）。
因此我们不是“见人就打”，而是**完全没有 NPC 主动攻击**。§9.1.1 里清兵 7 回合杀玩家
是测量脚本主动 `fight` 的结果，不是 NPC 先动手。

| NPC | 原版是否主动 | 触发条件（源码） | 我们是否一致 |
| --- | --- | --- | --- |
| 绿营清兵 `qingbing` | 否 | `qingbing.lpc:15` heroism；无 `init()`/`kill_ob` | 一致（都不主动） |
| 刀客 `dao-ke` | 否 | `dao-ke.lpc:30 init()` 只问候/传送，无 `kill_ob` | 一致（都不主动） |
| 老虎 `laohu` | **是（无条件）** | `laohu.lpc:20` attitude aggressive；`attack.lpc:269` | **不一致：我们更弱**（内容 `aggressive:true`，但运行时不消费，不主动） |
| 毒蛇 `dushe` | **是（条件）** | `dushe.lpc:27-33`：玩家身上没有 `xionghuang` → `call_out("kill_ob",1)` | **不一致：我们更弱**（无 `kill_ob` 实现） |
| 大狼狗 `wolfdog` | **是（概率条件）** | `wolfdog.lpc:29-35`：`random(kar+per) < 30` → `call_out("kill_ob",1)` | **不一致：我们更弱**（无 `kill_ob` 实现） |
| 流氓 `liumang` | 否 | `liumang.lpc:14` peaceful；无 `init()`/`kill_ob` | 一致（都不主动） |
| 店小二 `xiaoer2` | 否 | `xiaoer2.lpc:29 init()` 只问候；attitude friendly | 一致（都不主动） |

从 `/d/hangzhou/jiulou` 按 `content/xkx/exits.json` BFS 1–3 跳（`traversable` 出口）：

| 跳数 | 房间 | NPC | 原版主动? | 玩家主动攻击的实测后果（MID 档） |
| --- | --- | --- | --- | --- |
| 0 | 楼外楼 `/d/hangzhou/jiulou` | 店小二 ×1 | 否 | 玩家胜（12 回合，满血） |
| 1 | 相约轩 / 青石大道 | — | — | — |
| 2 | 红娘庄 `/d/hangzhou/marryroom` | 红娘 ×1 | 否（friendly） | 玩家 3/3 败（9–13 回合） |
| 3 | 断桥 `/d/hangzhou/duanqiao` | 穷汉 ×1 | 否（friendly） | 玩家胜（13–14 回合，满血） |
| 3 | 青石大道 `/d/hangzhou/road12` | 李可秀 ×1 | 否（heroism） | 玩家 3/3 败（4 回合） |
| 3 | 青石大道 `/d/hangzhou/road12` | 绿营清兵 ×4 | 否（heroism） | 玩家 0/40 胜（平均 6.90 回合） |
| 3 | 铁匠铺 `/d/hangzhou/tiejiangpu` | 铁匠 ×1 | 否（friendly） | 玩家胜（13 回合，满血） |

**原版会主动攻击且玩家必死的 NPC：1–3 跳内 0 个。** 玩家不输入 `fight` 时，出生点附近
没有任何 NPC 会先动手。若玩家主动攻击，“会死”的是红娘（2 跳）、李可秀与绿营清兵
（3 跳，均在 `road12`）；店小二、穷汉、铁匠可胜。这是产品层是否加“此地危险”提示的依据，
不是代码缺陷。

**流氓命中率 2.78% vs 原版 7.82% 归因（只归因，未修）**：

- `apply` 已接上：内容 `temp_apply` 有 `apply/attack 10`、`apply/defense 10`，实体
  `_Actor.apply_attack=10`；不是“有 apply 没接上”。
- 不是“持械按空手算”：原版 `liumang.lpc` 本身空手（`unarmed 20`，只穿 `cloth`），无武器。
- 主因是 **NPC 六维缺失**：原版 human race 的 str/con/dex 是 `10 + random(21)`
  （`adm/daemons/race/human.lpc:60-62`），原版 MID 快照里的 liumang 实例 str/dex=20；
  我们的内容 `attrs={}`，引擎 fallback 10。`skill_power` 的乘数是 `(str_eff + base_str)`，
  我们是 10+10=20，原版是 20+20=40，ap 直接差一倍。按当前公式反推：只把 liumang
  str/dex 补到 20，预测对方命中率 2.1% → 6.4%（实测当前 2.78%），与原版 7.82% 同一量级。
- 次因是 `query_skill` 语义：原版非 raw 返回 `skills[skill]/2 + skills[skill_map]`
  （`reports/xkx-ref/hit-rate.md:30`），MID 显示 40、战斗有效 20；我们直接用 40/20。
  因为 liumang 还有 `apply/attack 10`，双方同倍缩放后 apply 占比变小，玩家防御相对更高，
  命中率被进一步压低；两者都修正后反推约 5.3%。
- 结论：**不是 apply 缺失、不是持械/空手错配；是 NPC str/dex 取了下限 10（原版实例 20）
  + `query_skill` 未半档**。两条已按原版语义修复，见 §9.1.3 与 §14。

### 9.1.3 NPC 六维：按原版种族 setup 掷点（2026-11-08）

原版没有 `set("race")` 的对象走人类初始化（`adm/daemons/chard.lpc:25-30`），
再由 `adm/daemons/race/human.lpc:60-68` 掷点：

| 属性 | 原版写法 | 说明 |
| --- | --- | --- |
| str/con/dex/per/kar | `10 + random(21)` | 闭区间 10..30 |
| sta/cps/cor | `10 + random(21)`，条件含 `|| my[x] == 0` | 同上 |
| int | `80 - str - con - dex` | **派生值，不掷骰** |

修复内容：

- 抽取器 `tools/xkx_extract/extract.py`：
  - `RACE_ATTR_FILES` 增加 `人类 -> adm/daemons/race/human.lpc`；
  - 无显式 `set("race")` 时按 `chard.lpc:25` 落 `race="人类"`，`race_source` 指向该行；
  - `ATTR_KEYS` 补齐 `sta/cps/cor/spi`（`spi` 只收显式值；人类 setup 不掷它）；
  - `_RACE_ATTR_ROLL_RE` 同时接受 `random(N) + M`（兽）与 `M + random(N)`（人），
    并接受 `undefinedp(...) || my[...] == 0` 条件；
  - 人类 `int` 未被显式 set 时输出 `attrs_derive.int`（`difference`, total 80,
    minus `[str, con, dex]`, source `human.lpc:63`）。
- 引擎 `src/wsim/sim/vitals.py`：
  - `roll_attrs` 原有盐 `xkx-attrs:<seed>:<npc_id>:<attr>` 不变，同 seed 双跑一致；
  - 新增 `_apply_attrs_derive`：在显式值 + 掷点之后按 `attrs_derive` 计算；
    显式/掷点值优先不覆盖；缺任一被减项时不派生并记
    `incomplete_attrs_derive:<npc>:<attr>` 诊断。
- 例外：显式 `set("str", …)` 等（含无法静态取值的表达式）不掷、不覆盖；
  显式 `set("int", …)` 时不输出 `attrs_derive`。

旧世界隔离：只有 `content/xkx` 重新生成；`resolve_npc_vitals` 仍只对带
`vitals/attrs/skills/attrs_roll/attrs_derive` 任一键的 NPC 生效，旧 `content/`
30 个 legacy NPC 一个键都没有，事件与哈希逐字节不变。xkx 世界 `content_hash`
按预期变化（`06bdaf46…` → `99a23a90…`），旧 run 启动即 `content_hash_mismatch`，
需 `--reset`，已登记 `docs/GOLDEN-BASELINE-LOG.md`。

### 9.2 NPC 创世 `max_hp`

`src/wsim/sim/vitals.py` 的 `resolve_npc_vitals()`：

1. `vitals.max_qi > 0` → `max_hp = max_qi`（**原版口径**，气血上限就是 max_qi），`hp = max_hp`。
2. 缺失时按最高技能等级给默认（**我们的默认**，可解释）：

| 判据 | 默认 `max_hp` | 内容包实例 |
| --- | --- | --- |
| 最高技能 ≥ 80 | 500 | 李拙道人（100）、清明禅师（100） |
| 0 < 最高技能 < 80 | 300 | 土匪（25）、进香客（20）、书生（literate 40） |
| 无技能 | 200 | 老虎、挑夫 |

3. `max(1, …)` 兜底；测试断言 105 条内容 NPC（92 slice + 13 external）全部 `> 0`。
4. `attrs` / `vitals.combat_exp` / `skills` 原样带入：`attrs` 进 `Entity.attrs`，
   `skills`（整数等级）进 `Entity.skill_levels`，`combat_exp` 进 `Entity.combat_exp`。
5. xkx 的 `equipment` 在创世时逐件 `item_create` 进 NPC 背包（`reason="genesis_equipment"`），
   死亡时随尸体掉到地面；不额外编造掉落表。

实测缺失 `max_qi` 的 4 个：老虎 200、李拙道人 500、清明禅师 500、土匪 300。

### 9.3 地面掉落与 `loot` / `get`

- `WorldState.ground: dict[location, dict[item, qty]]`；只在非空时写进 `to_dict()`，
  旧世界哈希不受影响。
- `loot_create {location?, entity?, item, qty, reason?}`：落到**房间地面**（`location` 优先，
  缺省取实体所在地）；`items_created` 记一次造物。
- `loot_take {entity, location, item, qty, weight}`：地面 → `Entity.inventory`；数量不足整事务回滚；
  不重复计 `items_created`。
- `LootAction`（`src/wsim/actions/loot.py`）：`verb="loot"`，`aliases=("get",)`，由
  `ActionRegistry.register()` 同时注册两个 verb。未指定 `item` 时按 item id 排序全取；
  `weight = content.items[item].weight × qty` 写进事件，可直接核对重量。
- 原因码：`player_required` / `player_not_entered` / `player_dead` / `not_here` /
  `bad_qty` / `loot_not_found`。
- `FightAction` 在 API 壳不传 `loot` 时的掉落来源：`defender.inventory`（排序）+
  `defender.equipped.values()`（排序去重），逐件 `loot_create` 到尸体所在地面。
- xkx 房间 `objects` 中 `kind=="item"` 且 `count` 为整数的条目在创世时落到地面
  （`reason="genesis_ground"`）；`count_expr=random(...)` 一律跳过。

### 9.4 真实世界战报（`content/xkx`，seed=7，纯默认创世无脚本注入）

> 本节数字采于**上一版默认** `dodge/parry=40`，保留作为里程碑记录。
> 40/10/10 的复测见 §10.3：穷汉/店小二不再掉血，属于当前默认的已知偏差。

- 世界：195 房间 / 92 NPC / 4 个房间有地面物品（共 5 件）。
- 玩家：只走 `player_enter`，**不注入任何属性/技能/经验**；落地即
  `hp 100/100`、`combat_exp 250`、`skill_levels {unarmed:40, dodge:40, parry:40}`、
  游侠六维 `{str:20, con:20, dex:18, int:15, spi:15, sta:20}`。
- 目标 A：`/d/hangzhou/npc/poorman`（穷汉，`/d/hangzhou/duanqiao`），`vitals` 缺失 →
  默认 `max_hp=200`；`combat_exp=250`；装备 `cloth`（weight 3000）进背包。
  - 14 回合玩家胜（`winner=attacker`，`rounds_used=14`）。
  - `hp_add` 增量 `player:-46` / `poorman:-200`；状态变化 `100→54` / `200→0`，逐项相等；
    `overkill={poorman:1}` 只进战报。
  - 掉落 `loot_create {location:"/d/hangzhou/duanqiao", item:"/d/hangzhou/npc/obj/cloth", qty:1}`；
    `loot` 后 `loot_take {qty:1, weight:3000}`，背包 `cloth ×1`，地面清空。
  - `state_hash=50124449e2e0e877cffae443a11a8b56f4600595521539c80025c20ec46ab82a`。
- 目标 B（更弱）：`/d/hangzhou/npc/boy`（男孩，`/d/hangzhou/maojiabu`），
  默认 `max_hp=200`；`combat_exp=100`。
  - 13 回合玩家胜；`hp_add` 增量 `player:-9` / `boy:-200`，`100→91` / `200→0`；
    确认不会被 1 回合秒杀，仍有完整回合体验。
  - 掉落同上并捡起；`state_hash=461ad93bed911eb40eaccacb3ce25748ac10a5d1567c06379346926416113a63`。
- 经验压制保留：exp 1000 的毒蛇仍会压制 250 经验玩家，公式未改。

### 9.5 测试与基线

- 新增：`tests/test_combat_vitals.py`（玩家/NPC 气血来源、缺失档位、legacy 门控）、
  `tests/test_combat_loot.py`（取物、原因码、重量、同 seed 顺序）。
- `tests/test_xkx_determinism_combat.py` 因掉落改地面而显式重采：
  `state_final=91097d90…`、`event_final=412be545…`（旧值保留为漂移记录）。
- 未变：`tests/test_xkx_determinism_legacy.py`（735 events / `009076f0…` / `02d5f812…`）、
  `tests/test_p3e_golden.py` 全部通过。

## 10. 命中参数映射自查（2026-11-07，不改公式）

### 10.1 逐项结论

| # | 自查项 | 结论 | 证据 |
| --- | --- | --- | --- |
| 1 | `skill_power` 的 ap/dp 取哪些技能 | **错在 `attack_skill`：把 `role` 当技能兜底** | 玩家 `skills={"镖师":0.35}` → `skill_map["镖师"]=35`，旧代码返回 `"镖师"`，`unarmed=40` 被遮蔽；ap 3476 而非 5412。NPC 的 `{"wanderer":0.41}` 同样被当成武功：店小二 ap 2540（正确值 50）、流氓 3036（正确值 594）。源码 `combatd.lpc:271-274`：有武器取武器技能，否则 prepare，再否则 `unarmed`。 |
| 2 | `power_point` 缺省 1000 / `jing≈hp` | **都不是命中率原因** | 缺省 1000=1.0，只乘 1；`dodge_power_point_permille=None` 对应“没有映射轻功”。`jing` 只出现在 `dodge_rewards` / `parry_rewards` 的经验判定（`combatd.lpc:361-373`、`417-423`），`resolve_dodge` 的入参里没有 `jing`。 |
| 3 | 防御 dp 是否漏乘 dex/身法 | **没有漏乘** | `_defense_power` 传 `attr_effective=query_dex(base, 0, skills)`、`attr_base=base`；`query_dex = base + dodge/10`。玩家 `dex 18 → 22`，穷汉 `dex 18 → 18`（无 dodge）。dp=4920=((40/6)*1600+250)/80*(22+18)。 |
| 4 | `clamp_power([1,2e8])` 是否压极端 | **没有触发** | 实测 ap/dp 范围 50..15000（见 10.2），离 1 与 2e8 都很远。 |
| 5 | NPC `attrs` 是否从内容包进实体 | **73/105 带 attrs 且已生效；缺省是 10，不是 20** | 穷汉实体 `attrs={"con":17,"dex":18,"int":13,"str":20}`；老虎/店小二/流氓内容里 `attrs={}`，引擎默认 10。但这两只的相关技能等级为 0，`skill_power` 先走 `combat_exp/2` 分支，dex 根本不参与，所以“老虎 dex 30 没生效”不是本次命中率差异的原因。原版老虎的强度来自 `set_temp("apply/defense",30000)` / `apply/attack 20000` / `apply/armor 1000`，而抽取器只解析 `set`、不解析 `set_temp`（`tools/xkx_extract/extract.py:546`），内容包没有这些字段。 |

### 10.2 映射修复与实测（旧默认 40/40；3 seeds × 60 回合，双方各 180 次出手）

修复：`combat_rules.attack_skill` 删除 `role` 兜底，无显式技能时固定 `unarmed`。
未改 `tools/xkx_formula` 任何公式。

> 下表采于映射修复当时的默认 `unarmed/dodge/parry=40`，保留用于对比。
> `dodge/parry` 下调为 10 后的复测见 §10.3。

产品默认新号（`unarmed/dodge/parry=40`、`exp=250`、游侠六维）：

| 目标 | 修复前 玩家命中 / 对方命中 | 修复后 玩家命中 / 对方命中 |
| --- | --- | --- |
| 老虎 `laohu`（exp 30000、dodge 0） | 2.2% / 49.4% | **10.0% / 53.9%** |
| 店小二 `xiaoer2`（exp 100、unarmed 0） | 98.9% / 11.1% | **98.9% / 0.0%** |
| 流氓 `liumang`（exp 1000、unarmed 20） | 67.8% / 10.6% | **85.0% / 1.1%** |
| 穷汉 `poorman`（exp 250、unarmed 0） | 92.8% / 26.1% | **96.7% / 0.0%** |

参考口径（0 经验 / 0 武功 / 天赋 20，对应 `reports/xkx-ref/scenarios/stability.md`
的 `xkxref`）：修复后玩家对所有目标命中率 0%（ap=exp/2=0 → clamp 1），
对方命中率 96.7%–100%。这正是“原版新号打不动任何东西”，也是创世默认要上抬的原因。

**待办**：`reports/xkx-ref/hit-rate.md` 尚未产出；数字到了以后用同一口径
（`xkxref` 角色表、同样目标、50+ 回合）并排比较，偏差 >±15pp 才继续查映射。

店小二“12 回合一次没打中我”在修复后是**预期结果**：玩家 dp=4920（dodge 40），
店小二 ap=50（unarmed 0 → exp/2），闪避 99% 后还有 unarmed 招架 99%，
单次命中率约 0.01%。这不是公式或映射错误，而是“40 级闪避/招架 vs 0 级 NPC”
的必然结果；参考版新号 0 闪避时对方命中率是 96.7%。

### 10.3 默认值调整：dodge/parry 40 → 10（3 seeds × 60 回合，双方各 180 次出手）

口径：纯默认创世（游侠、`unarmed=40 / dodge=10 / parry=10`、`exp=250`、六维默认），
不注入任何数值；双方 HP 拉满以打满 60 回合，3 seeds（7 / 42 / 2026），每方 180 次出手。
公式库未改。落地数值：ap(unarmed)=5412、dp(dodge)=148、pp(unarmed)=4551。

| 目标 | 我方命中 | 对方命中 | 我方收到伤害（总 / 单次） | 我方造成伤害（单次） |
| --- | --- | --- | --- | --- |
| 店小二 `xiaoer2` | 174/180 = **96.7%** | 2/180 = **1.1%** | 12 / 6（仅 seed 7、2026 各 6） | 12–23 |
| 穷汉 `poorman` | 175/180 = **97.2%** | 2/180 = **1.1%** | 32 / 15–17（仅 seed 2026） | 12–23 |
| 流氓 `liumang` | 142/180 = **78.9%** | 18/180 = **10.0%** | 162 / 6–11 | 6–23 |
| 老虎 `laohu` | 12/180 = **6.7%** | 139/180 = **77.2%** | 970 / 5–9 | 2–4 |

同一批 60 回合截到前 20 回合（`fight` 的真实上限）：店小二 1/60（1.7%）、
穷汉 1/60（1.7%）、流氓 6/60（10.0%）、老虎 47/60（78.3%）。长局 1.1% 不是
60 回合口径造成的假象，初始单次期望也在同一量级。

真实对局（纯默认新号 100 血，`max_rounds=20`，3 seeds）：

| 目标 | 结果 | 我方剩余血 / 受伤 | 我方造成伤害 |
| --- | --- | --- | --- |
| 店小二 | 3/3 胜（11–12 回合） | 100 / 0 / 0 | 201–220 |
| 穷汉 | 3/3 胜（11–12 回合） | 100 / 0 / 0 | 204–209 |
| 流氓 | 3/3 20 回合未分胜负 | 91 / 83 / 91；受伤 9 / 17 / 9 | 163–230 |
| 老虎 | 3/3 玩家死（16–20 回合） | 0；受伤 101 / 106 / 106 | 2–11 |

结论（如实记录，未为凑区间改公式）：

- **老虎继续打不过**：对方命中 77.2%，20 回合内玩家必死，经验压制（30000 vs 250）保留。
- **弱怪能赢，但“要掉血”未达成**：店小二/穷汉真实对局 0 伤害，对方命中 1.1%，
  低于目标区间 20%–60%；流氓 10.0% 也低于区间，但至少会掉 9–17 血。
- 原因不是映射或公式：命中 = `(1-dodge%) × (1-parry%)`。`dodge=10` 只把 dp 从
  4920 降到 148；但双方空手时 pp 取**招架方的 unarmed 战力**
  （`combatd.lpc:387-393`，`select_parry_power`），`unarmed=40` 使 pp=4551。
  店小二 ap=50：闪避 148/(50+148)=74.7%，剩余 25.3% 里 98.9% 被招架 → 命中约 0.26%；
  穷汉 ap=125 → 约 1.2%，与实测 1.1% 一致。
- 敏感性（仅测量，未改默认）：固定 `dodge/parry=10`、`exp=250`，`unarmed=40` 时
  店小二/穷汉 1.1%/1.1%；`unarmed=20` 时 1.1%/5.0%；`unarmed=10` 时 4.4%/21.7%，
  但玩家对流氓命中降到 1.1%（打不过）。也就是说，“弱怪 20%–60% 命中”与
  “40 级 unarmed 稳赢流氓”在这套对称技能公式下不能同时成立；要同时成立，
  只能从内容侧给弱 NPC 补 `apply/attack` 或武器（原版老虎正是用 `set_temp` 补的），
  或接受玩家打不过流氓。这两个方向都不属于本次“改默认值”的范围。
- **旧九地点世界哈希逐字节不变**：`tests/test_xkx_determinism_legacy.py` 735 events /
  state `009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9` /
  event `02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`；
  `tests/test_p3e_golden.py` 全绿。
- **待办**：`reports/xkx-ref/hit-rate.md` 尚未产出；出来后用同一口径（`xkxref`
  角色表、同样目标、50+ 回合）并排比较，偏差 >±15pp 才继续查映射。

### 10.4 难度梯度（当前默认，定稿）

默认值固定为 `unarmed=40 / dodge=10 / parry=10 / combat_exp=250`，不再为命中率区间调整。
下表是真实对局（100 血、20 回合上限、3 seeds）下的难度梯度：

| 档位 | 目标 | 结果 | 我方受伤 | 60 回合口径 对方命中 |
| --- | --- | --- | --- | --- |
| 弱怪 | 店小二、穷汉 | **稳赢**（11–12 回合） | **0** | 1.1% |
| 中怪 | 流氓 | **20 回合平手** | **9–17** | 10.0% |
| 强怪 | 老虎 | **必死**（16–20 回合） | 101–106 | 77.2% |

- 这是原版公式 + 当前默认值的**自然结果，不是 bug，也不打算修**。
  弱怪打不动人是因为 `unarmed=40` 同时参与攻击与空手招架；老虎必死是
  `combat_exp=250 vs 30000` 的经验压制。两者都保留。
- 若以后要改变某一档，只能从内容侧补 `set_temp("apply/...")` 或武器，不改默认值/公式。
- 经验压制：`modify_damage_by_exp` 原版逻辑保持不变。

### 10.5 `set_temp` 临时攻防（已接入）

内容字段：`npcs.json` 每条 NPC 的 `temp_apply[]`，每项
`{key, value, value_expr, known, source_kind, source{path,line}}`。
创世只把 `known=true` 且 `value` 为整数的项写进 `Entity.temp_apply`。

按 `combatd.lpc` 原语义接入，**不自行发明叠加方式**：

| 字段 | 源码依据 | 引擎落点 |
| --- | --- | --- |
| `apply/attack` | `combatd.lpc:207` | `skill_power` 的 `apply_bonus`（攻击）；`level = 技能 + apply`，再 800 封顶 |
| `apply/defense` | `combatd.lpc:210` | 同上（防御）：同时作用于闪避 dp、招架 pp、空手 pp |
| `apply/armor` | `combatd.lpc:451-456`、`560-565` | 护甲减伤值，同时用于伤势判定；膂力加成在护甲之后加，不被护甲吃掉 |
| `apply/damage` | `combatd.lpc:436-440` | **只有持兵器时**才作为基础伤害；空手取 `apply/unarmed_damage` |

- 当前 xkx 内容没有 `apply/unarmed_damage`，引擎也没有兵器建模，所以
  `apply/damage` 目前不进入任何 NPC 的伤害；这是源码语义，不是漏接。
  真正影响这四只的是 `apply/attack` / `apply/defense` / `apply/armor`。
- 与原版差异：原版 `set_temp` 每次 reset 重设；本引擎只在**创世写一次**，
  战斗内不重设、不衰减。
- `known=false` 的 5 条（`is_riding`、`is_rided_by`、`qingshu`、`armor`、`shoupa`）
  忽略，并写诊断 `unknown_temp_apply:<npc>:<key>:<path>:<line>`；
  同类 NPC 的 known=true 项照常生效，不因同一条记录里的未知项被整条丢弃。
- `Entity.temp_apply` 只在非空时写进 `to_dict()`；旧九地点 NPC 没有该字段，
  世界哈希逐字节不变。

#### 量级自证：为什么 `apply/defense=30000` 得到 21,287,500

不是把 30000 当千分位/倍率，而是源码的两条分支：

```
combatd.lpc:204  level = query_skill("dodge") = 0
combatd.lpc:210  level += apply/defense = 30000            -> 30000
combatd.lpc:226  if (level > 800) level = 800              -> 800      （封的是等级）
combatd.lpc:236  power = (level / 6) * level * level       -> 133 * 640000 = 85,120,000
combatd.lpc:241  return (power + exp) / 80 * (dex + query("dex"))
                 = (85,120,000 + 30,000) / 80 * (10 + 10)
                 = 1,064,375 * 20 = 21,287,500
combatd.lpc:342  clamp [1, 200,000,000]                    -> 21,287,500（未触顶）
```

- 15000 是另一条分支：`level == 0` 时 `combatd.lpc:224` 直接返回 `combat_exp / 2`，
  不走三次方；一旦有 `apply` 等级，就走 236/241。所以从 15000 跳到 2100 万
  是源码分支切换，不是单位错。
- 原版同一参数：`laohu.lpc:22` `combat_exp=30000`，`laohu.lpc:28-30`
  `apply/attack=20000 / defense=30000 / armor=1000`；`setup_beast`
  （`adm/daemons/race/beast.lpc:62,66`）给 str/dex 随机 `5..44`，
  `query_str/query_dex` 下限 10（`feature/attribute.lpc:10-30,46-48`）。
  代入同一公式，原版 dp 范围是 `1,064,375 × (15..88)` =
  **15,965,625 .. 93,665,000**，同样是 10^7 量级，且都低于 2e8 上限。
  我们的内容没有抽随机属性，`attrs={}` 按默认 10/10，取到该范围的下限
  **21,287,500**。单位一致，不需要修。
- `apply/attack=20000` 同理：20000 → 800，ap 也是 21,287,500。
- 轻功 `power_point`（`combatd.lpc:213-219`）只在有映射轻功时乘 1.0–1.5；
  老虎没有映射轻功，不乘。

#### 接入前后对比（3 seeds × 60 回合，双方各 180 次；真实对局 100 血 / 20 回合）

| 目标 | 接入前 我/敌 | 接入后 我/敌 | 60 回合受伤 前→后 | 真实对局 前→后 |
| --- | --- | --- | --- | --- |
| 店小二 | 96.7% / 1.1% | **96.7% / 1.1%** | 12 → 12 | 稳赢 0 伤 → 稳赢 0 伤 |
| 穷汉 | 97.2% / 1.1% | **97.2% / 1.1%** | 32 → 32 | 稳赢 0 伤 → 稳赢 0 伤 |
| 流氓 | 78.9% / 10.0% | **60.0% / 16.1%** | 162 → 244 | 平手 9–17 伤 → 平手 30–51 伤 |
| 老虎 | 6.7% / 77.2% | **0.0% / 100.0%** | 970 → 1269 | 必死 16–20 回合 → 必死 15 回合，造成 0 |

接入后明细（60 回合口径）：店小二受伤 12（6/次）、穷汉 32（15–17）、
流氓 244（6–11）、老虎 1269（5–9）；真实对局剩余血：流氓 49/70/61，
老虎 0（15 回合，造成 0）。

哪条变了、为什么：

- **店小二/穷汉没变**：内容里没有 `temp_apply`，数值与接入前逐项相同。
- **流氓变难**：`apply/attack=10` 使 ap 594 → 1496；`apply/defense=10` 使
  dp/pp 594 → 1496。玩家命中 78.9% → 60.0%，对方命中 10.0% → 16.1%；
  仍 20 回合平手，但掉血从 9–17 升到 30–51。
- **老虎变成原版形状**：`apply/attack=20000` / `apply/defense=30000` 进入
  `skill_power` 后被 800 封顶，ap/dp 从 15000 变为 **21,287,500**；
  `apply/armor=1000` 同时吃减伤与伤势判定。结果玩家命中 0.0%、
  对方命中 100.0%、玩家必死且造成 0 伤害。与原版“老虎 ap/dp ~10^7、
  armor 1000”一致；之前的 6.7% 命中只是因为内容没把 `set_temp` 抽出来。

## 11. NPC 死亡：随身装备落地（不建模尸体）

### 11.1 模型

- 新增 `Entity.equipment: list[str]`，创世时把内容包 `equipment[].ref` 原样挂到实体上，
  **不再进 NPC 背包**（否则会被交易/拾取逻辑当成普通携带物）。空列表不写 `to_dict()`，
  旧九地点 NPC 没有该字段，创世事件与哈希逐字节不变。
- 战斗动作在 `entity_status(dead)` 之后、`combat_log` 之前，把 `equipment` 转成
  `loot_create`（落 `WorldState.ground`）。即使请求显式传了 `loot`，随身装备也会落地；
  与背包/显式掉落同 id 时取较大数量，避免双计。
- 内容包 `drops` 仍只放源码 `die()` 的真掉落（内容线补的 3 条），完全不参与这段逻辑，
  因此不会和装备双计。
- 物品表没有的装备 ref：创世诊断追加
  `unknown_equipment_item:<npc_id>:<ref>`，不补造重量/价格；`loot_take` 仍按物品表算，
  查不到就是 weight 0。当前 xkx 切片有 34 个这类 ref（如 `/clone/misc/cloth`、
  `/d/shaolin/npc/obj/dao-cloth.lpc`）。

### 11.2 真实世界验证（`content/xkx`，seed=7，纯默认新号）

穷汉创世即 `equipment=["/d/hangzhou/npc/obj/cloth"]`、`inventory={}`。
12 回合击杀后原始 effects（顺序）：

```json
[{"op":"hp_add","data":{"delta":-200,"entity":"/d/hangzhou/npc/poorman","reason":"fight"}},
 {"op":"entity_status","data":{"entity":"/d/hangzhou/npc/poorman","status":"dead","tick":0,"location":"/d/hangzhou/duanqiao"}},
 {"op":"loot_create","data":{"item":"/d/hangzhou/npc/obj/cloth","location":"/d/hangzhou/duanqiao","qty":1,"reason":"fight"}},
 {"op":"combat_log","data":{"winner":"attacker","rounds_used":12,"skill":"unarmed","target_skill":"unarmed","hp":{"/d/hangzhou/npc/poorman":0,"player:hero":100},"overkill":{"/d/hangzhou/npc/poorman":1}, "...":"..."}},
 {"op":"player_presence","data":{"id":"player:hero","presence":"online","tick":0}}]
```

`loot` 后：

```json
[{"op":"loot_take","data":{"entity":"player:hero","location":"/d/hangzhou/duanqiao","item":"/d/hangzhou/npc/obj/cloth","qty":1,"weight":3000}},
 {"op":"player_presence","data":{"id":"player:hero","presence":"online","tick":0}}]
```

状态：地面清空，玩家背包 `cloth ×1`，穷汉 `status=dead/hp=0`，
`state_hash=7d5c350536a64df339fa3211e712539c4993a3d4868129eeaabfce496ab0104f`。

### 11.3 旧世界证明

`tests/test_xkx_determinism_legacy.py` 全绿：735 events /
state `009076f0…` / event `02d5f812…` / business_cash 11442 / blocked 6 / pruned 3。
`tests/test_p3e_golden.py` 全绿。旧内容 NPC 没有 `equipment`，死亡路径不新增任何 Effect。

## 12. 招式名与技能钩子接入（2026-11-07）

### 12.1 数据与语义

- `src/wsim/sim/skill_actions.py` 只读加载 `content/xkx/skills_actions.json`：
  22 个武功 / 247 条普通招式 / 4 条 `special_actions`；`default` 系数 126 个，全为 0。
- 选招严格按 `query_action()`：
  `seq = max{i | level > action[i-1].lvl}` → `seq = random(seq)` → `action[seq]`；
  `seq=0` 时仍记一笔 `below(0)` 并取第 0 条。映射技能为空时回落种族 `combat_action`
  （人类 5 条无系数；走兽 3 条 `damage` 50/30/30）。
- 缺键与 `coefficient_source=="default"` 都取 0；`tests/test_skill_actions.py`
  遍历 247 条断言四键齐全且 default 全 0，`tests/test_combat_moves.py` 锁死
  “缺 `force` 键 = 0、战报 JSON 不含 `None/NaN`”。
- 钩子：`force_hit` 按 `force.lpc hit_ob` 接入 6 个有 code 的内功；
  `martial_hit` 接入 mantian-xing / yunlong-zhua（只上毒，整数增量 0，但消费随机）；
  `victim_force_hit`（`be_hit_ob` 整段被注释）、`victim_parry_hit`（无实现）、
  `weapon_hit`（由武器对象 `hit_ob` 提供，技能表不可得）保持 0，不查表、不掷点。

### 12.2 真实 xkx 世界两场（`content/xkx`，seed=42，新号 100 血）

弱怪 `/d/hangzhou/npc/xiaoer2`（店小二，200 血）：

```text
第1回合
  你挥拳攻击店小二的要害，造成20点伤害（气血200→180）。
  店小二往你的要害一抓，但你闪开了。
...
共11回合，你赢了，店小二倒下了。
```

老虎 `/d/hangzhou/npc/laohu`（200 血）：

```text
第1回合
  你挥拳攻击老虎的要害，但老虎闪开了。
  老虎跃起来用前掌往你的要害猛地一拍，造成9点伤害（气血100→91）。
...
第15回合
  你提起拳头往老虎的要害捶去，但老虎闪开了。
  老虎扑上来张嘴往你的要害狠狠地一咬，造成5点伤害（气血1→0，受了伤）。
共15回合，你倒下了。
```

两只 NPC 内容里都没有 `map_skills`，所以按原版 `reset_action()` 回落种族
`combat_action`，战报展示的是原版动作文案（不是新造的招式名）。有映射技能的
真实 NPC（李沅芷，`map_skills.unarmed=taiji-quan`）战报示例：

```text
李沅芷使出一招「白蛇吐信」，击中你，伤害108，气血100→0。
```

### 12.3 同 seed 伤害序列对比（`PRNG(42, 0, "action:fight")`）

三组：A 接入前（无 `map_skills/race`，空表）、B 只选招并把系数归零、
C 接入后。B→C 为 **0 个差异回合**，说明本次接入没有新增伤害来源；A→B 的差异
全部来自原版每回合都会发生的一次 `query_action` 掷点（`combatd.lpc:280-295`
调用 `reset_action()`/`query_action()`），下游随机流整体平移。

老虎（走兽默认招式，16 回合）：

| 回合 | A 伤害 | B/C 伤害 |
|---|---|---|
| 4 | 6 | 5 |
| 6 | 7 | 5 |
| 7 | 6 | 8 |
| 8 | 9 | 6 |
| 9 | 7 | 9 |
| 10 | 7 | 8 |
| 11 | 8 | 6 |
| 12 | 5 | 6 |
| 13 | 9 | 6 |
| 14 | 5 | 9 |
| 15 | 7 | 5 |
| 16 | 7 | 8 |

其余 4 回合数值相同。店小二（12 回合）A→B 差异 10 回合，同样只来自掷点平移；
B→C 仍为 0。

为什么 B→C 全为 0：这两个实体的 `attack` 为 0，伤害几乎全部来自 `roll_strength_bonus`，
`combatd action["damage"]` 的 `damage += action["damage"]*damage/200` 乘在 0 上；
人类默认招式四系数全 0，走兽默认招式只有 `damage` 且被上式吞掉。因此本战例没有
可观察的系数增量，但参数通路已由 `tests/test_combat_moves.py` 用
`dodge=50/parry=40` 直接验证透传。

### 12.4 确定性证据

- 旧九地点：`tests/test_xkx_determinism_legacy.py` 原值通过，
  state `009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9`，
  event `02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`；
  `tests/test_p3e_golden.py` 原值通过。
- 同 seed 双跑（真实老虎场景，两条独立会话，走同一条路线再 `fight 老虎`）：
  state `345aa83fd32c140445fb959b69cb8c054477f1623460453451078f270d3907ae`（两次相同），
  event `4c9dff8f5e5c6c470a6323c37f271e67f4bfab162e903087a65c1b527cf63e87`（两次相同），
  战报文本逐行相同。
- 合成战斗基线：`state_final` 仍 `91097d90…`（无 `map_skills/race` 的实体不选招、
  不掷点、数值不变）；`event_final` 因 `rounds[]` 新增字段重采为 `273b5455…`。
- 下游战例：真实战斗的 `combat_exp` 245→247（`test_xkx_growth`），同样来自
  `query_action` 掷点平移，不是新增伤害/经验来源。

## 13. 装备与物品动作层

- 装备 `apply` 只读消费：`item_apply` 只读 `items.json` 的 `apply`，不补默认值；
  `equip/unequip` 效果维护 `Entity.equipped_apply` 与 `weapon_type`，
  战斗侧通过 `_Actor.temp_apply` 合并、`attack_skill` 优先取 `weapon_type`。
  `tools/xkx_formula/*` 与内容包 JSON 零改动。
- 负重：`max_encumbrance = str*5000 + floor(max_skill/10)*1000`；无 `attrs` 返回 0
  表示不启用。`overweight_reason` 超限时给 `overloaded:<need>:<max>`。
- `drop` 与 `loot_create` 共用 `WorldState.ground`：掉落到当前房间地面，
  `loot/get` 再从地面移回背包（`loot_take`）。
- `rest` 按地点系数（客栈/住处/镇内/野外）逐小时恢复体力与气血；
  `sleep` 沿用睡醒口径回满 hp/stamina。两者都由 `PlayerRestAction`
  （`verb="rest"`，`aliases=("sleep",)`）注册，写动作按声明小时推进世界钟。
- NPC 掷点：`roll_attrs` 用
  `sha256("xkx-attrs:<seed>:<npc_id>:<attr>")[:8]` 取大端整数对区间取模；
  seed 来自世界配置/创世，legacy NPC 无 `vitals/attrs/skills/attrs_roll/attrs_derive`
  时不落战斗字段。
- `region/region_name` 由 `xkx_content` 从 `rooms.json` 只读透出到
  `content.locations`；视角输出缺失时为 `null`，不编造区域。

## 14. `query_skill` 半档语义（2026-11-08）

原版实现（`feature/skill.lpc:87-100`）：

```
87 varargs int query_skill(string skill, int raw) {
88   if (!raw) {
91     s = query_temp("apply/" + skill);
93     s += skills[skill] / 2;
95     if (mapp(skill_map)) s += skills[skill_map[skill]];
96     return s;
97   }
98   if (mapp(skills) && !undefinedp(skills[skill])) return skills[skill];
99   return 0;
100 }
```

引擎在**参数映射层**实现：`_Actor.level()` = raw，`_Actor.effective()` =
`apply/<skill> + raw/2 + mapped`。公式库 `tools/xkx_formula/**` 零改动。

### 14.1 调用点清单与 raw/半档依据

| 调用点 | 原版依据 | 口径 | 引擎落点 |
| --- | --- | --- | --- |
| 攻击 ap | `combatd.lpc:204` `query_skill(skill)`；`:207` 再加 `apply/attack` | **半档** | `_attack_power` → `effective(skill)` |
| 防御 dp/pp | `combatd.lpc:204/210`（dodge/parry/unarmed） | **半档** | `_defense_power` → `effective(skill)` |
| 内功发力 | `inherit/skill/force.lpc:13,28` `query_skill("force")` | **半档** | `_force_hit` → `effective("force")` |
| 先手近似 | 原版无显式先手（MudOS heartbeat） | 引擎自选，取 **半档** 与 ap/dp 同口径 | `speed_a/b` → `effective("dodge")` |
| 技能成长 | `feature/skill.lpc:178` 直接用 `skills[skill]` | **raw** | `_Actor.learn` → `level(skill)` |
| 闪避/招架/伤害奖励阈值 | `combatd.lpc:365/421/583` 均传 `query_skill(..., 1)` | **raw** | `dodge_rewards` / `parry_rewards` / `damage_rewards` 的 `*_skill_level` → `level()` |
| 招式选择 | `mantian-xing.lpc:186`、`yunlong-zhua.lpc:123-134` 等 `query_skill(..., 1)` | **raw** | `skill_action` / `_martial_hit` → `level()` |

### 14.2 改造前后实测（6 对手，seed 1–40；老虎 1–10）

每格：胜/负 · 平均回合 · 玩家剩余气血中位数 · 对方命中率。

| NPC | 改前（HEAD） | 只修属性 | 属性 + 半档 | 原版实测 |
| --- | --- | --- | --- | --- |
| 店小二 | 40/0 · 12.18 · 100 · 0.00% | 40/0 · 12.18 · 100 · 0.00% | 40/0 · 12.40 · 100 · 0.00% | 0.00% |
| 流氓 | 40/0 · 23.45 · 100 · 2.78% | 39/1 · 29.50 · 86.5 · 7.10% | 40/0 · 28.23 · 86.5 · 5.60% | 7.82% |
| 小沙弥 | 40/0 · 23.88 · 63.5 · 14.10% | 40/0 · 23.88 · 63.5 · 14.10% | 39/1 · 25.00 · 59.0 · 15.50% | — |
| 刀客 | 0/40 · 8.93 · 0 · 53.22% | 0/40 · 5.72 · 0 · 63.76% | 0/40 · 5.47 · 0 · 68.04% | — |
| 绿营清兵 | 0/40 · 6.90 · 0 · 58.33% | 0/40 · 4.80 · 0 · 66.67% | 0/40 · 4.53 · 0 · 70.17% | — |
| 老虎 | 0/10 · 7.50 · 0 · 100% | 0/10 · 7.50 · 0 · 100% | 0/10 · 7.40 · 0 · 100% | 100% |

偏差：店小二 0（一致）、老虎 100%（一致）、流氓 5.60% vs 7.82%（低 2.22pp）。
流氓残余偏差来自原版实例固定 str/dex=20，而我们在 seed 1–40 上掷 10..30（均值 20），
以及原版战报的快攻/连击分支未建模；不再人为调参。

结论：这两项是**战斗输入校正**，不是难度调整。修完后玩家对刀客/清兵反而更差
（对方属性从下限 10 恢复成原版掷点），这是原版行为，按用户要求不改。

### 14.3 确定性与红线

- 旧九地点三项哈希不变：735 /
  `009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9` /
  `02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`。
- `tools/xkx_formula/**` `git diff --stat` 为空。
- 同 seed 双跑一致（seed=1 店小二、seed=2 流氓，两条独立会话）：
  `file_sha256` 与 `combat_hash` 均相同（见本轮交付报告）。
- xkx `content_hash` 仅因属性内容变化：`06bdaf46…` → `99a23a90…`，旧 run 需 `--reset`。

