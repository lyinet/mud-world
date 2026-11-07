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
- 调用顺序严格按 `INTERFACE.md` §2：
  1. `skill_power(attack)` / `skill_power(dodge, DEFENSE)`；
  2. `resolve_dodge` →（闪避）`dodge_rewards`；
  3. `select_parry_power` → `resolve_parry` →（招架）`parry_rewards`；
  4. `resolve_damage`（内部：武器伤害 → 护甲 → 膂力 → 经验压制 → 空明 → 威力系数）；
  5. `apply_qi_damage` → `roll_wound` →（受伤）`apply_qi_wound`；
  6. `damage_rewards` → `death_state`。
- 引擎侧自己算、不进库的量（库第 4 节要求）：
  - 攻击技能名：`intent.args.skill` 优先，其次 `role` 命中技能表，否则 `unarmed`；
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
  返回 0、不消费随机流）。取数顺序即库的源码顺序，锁在
  `tests/test_combat_fight.py::test_prng_draw_order_is_locked`：
  `dodge → parry → weapon_damage → armor → strength → exp_check → wound(护甲) →
  wound(killing 组) → damage_rewards`。切换库后该战例为 **9 次**取数（旧实现 15 次）。
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
| 新战斗基线 | 4 events（genesis + spawn + fight ×2），state `3eae4e85e27e4f268c3f9d2579aac42d0e6465d759c2ebca7b3a93ff77627f86`，event `bb1698b33f0d3c2c96af753afa53868ccf68c6419a66b48099db60952a15af2b`（`tests/test_xkx_determinism_combat.py`） |
| 同 seed 双跑 | 新基线测试内双跑 state/event 哈希逐字节一致 |
| 回放等价 | 从 `events.sqlite` 逐条 `apply_effects` 重建的投影与实跑 state 哈希一致 |
| PRNG 锁序 | `test_prng_draw_order_is_locked` 固定 9 次 `(n, value)` 对，顺序或流变化立即失败 |

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
| 1 | 技能虚函数钩子（force/martial/weapon 的 `hit_ob`/`be_hit_ob`） | 无技能配置/虚函数机制，`SKILL_HOOKS` 五项固定传 0，参数位置保留在 `resolve_damage` 的源码顺序上 | 缺口：需内容侧先定义技能钩子 |
| 2 | 招式序列（`reset_action()` 的 dodge/parry/force/damage） | `ACTION_SEQUENCE` 四项固定 0（中性值），不做招式选择与连招 | 缺口：需招式表与选择规则 |
| 3 | 战斗状态位（`is_busy`、`bixie/cizu`、`bixie/ciwan`、`killing`、`weapon`、`apply/armor`） | `killing=True`（`fight` 即击杀模式）；`weapon` 读 `equipped["weapon"]`；`armor` 读 `Entity.defense`；`is_busy`/`cizu`/`ciwan` 无行动队列与临时标记，固定 `False` | 部分：`is_busy`/`cizu`/`ciwan` 为缺口 |
| 4 | 身份与存活判定（`userp`/`living`/`noliving`） | `player:` 前缀或 `state.players` 视为玩家；`living` 固定 `True`（死亡实体已被 `preconditions` 挡在战斗外）；`noliving` 未建模 | 部分：`noliving` 为缺口 |
| 5 | 轻功与 `power_point` 千分位系数 | 防御侧 `dodge_power_point_permille=None`（引擎无“映射轻功”概念）；攻击侧读 `attrs["power_point"]`，缺省 1000 | 部分：轻功映射为缺口；`jing` 用 `hp` 近似 |
| 6 | `receive_damage` 副作用（怒气、`heart_beat`、`last_damage_from`） | 只取 `apply_qi_damage` 的气血落值，副作用全部不建模 | 缺口 |
| 7 | 表现层随机（部位、胜负台词） | 不翻译；`rounds[]` 只记数值，不产文案随机 | 已排除（不影响数值） |
| 8 | 死亡后果 + 技能升级回调 + 奖励写回 + 追杀任务奖励 | 死亡只落 `entity_status(dead)` + 玩家 `custody`；无尸体/经验惩罚/`no_die`；`leveled_up` 无全局回调可调；奖励写回由 `combat_exp_add`/`skill_progress` 承担；`potential` 不建模；追杀任务奖励不做 | 部分：经验/技能已接，其余为缺口 |

另有一项库未列出但必须声明：**暴击**。xkx `combatd` 没有暴击公式
（`formula-map.md` §4.1），本实现不引入，战报与事件里不出现该字段。

待补清单（按依赖排序，均不在本次可交付范围）：
1. 技能虚函数钩子 + 招式序列（依赖内容侧技能/招式定义）；
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
