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
| `skill_xp_add` | `{entity, skill, delta}` | 技能经验增长，自动升级 |
| `combat_log` | 任意战报 | 只读载体，不修改状态（与 `shop_report` 同类） |

所有分支只读事件参数，不做随机、不读墙钟。`entity_status(dead)` 在 `hp>0` 时直接
`EffectError`，从原语层堵死“绕过伤害直接置死”。`fight` 动作只返回 Effect，
`Writer` 再 clone + `apply_effects`，不存在直接改 state 的路径。

## 3. `fight`：原子回合模型

- 实现：`src/wsim/actions/combat.py::FightAction`，规则纯函数在
  `src/wsim/sim/combat_rules.py`。
- **一次动作 = 1 条事件**：最多 20 回合全部在同一次 `apply()` 内结算，事件
  `effects` 末尾是一条 `combat_log`，其 `data.rounds[]` 即完整战报。
  不存在“每回合一条事件”，`max_rounds` 只能调小（`>20` 被硬截断）。
- 回合内顺序：先手按 `dex + dodge等级`，平手由发起方先手；先手出手后若目标
  未死则反击。
- 公式来源与整数化（`adm/daemons/combatd.lpc`）：
  - `skill_power` 197–242 → `(level//6*level*level + combat_exp)//80 * (2*str|dex)`，
    `level=0` 时回退 `combat_exp//2`；
  - 命中/闪避 328–354 → `random(ap+dp)`，`<dp` 判闪避；
  - 招架 387–406 → `random(ap+pp)`，`<pp` 判招架；空手对兵器、兵器对空手规则保留；
  - 伤害 436–558 → 基础 `attack`，>300 走 `300+random(d-300)`，再 `(d+random(d))//2`；
    护甲同理，`str` 膂力加成 `(str+random(str))//2`，经验修正循环
    `damage -= damage//3`，最后 `damage * power_point//1000`；
  - 破防/受伤 560–591 → `random(damage) > armor` 记 `wounded`（只入战报，不影响当前血量）。
  - 全程 `int` 运算，没有浮点尾差；旧 `skills` 转等级只发生一次截断。
- 死亡/掉落：目标 hp 归零后同事件内依次写 `entity_status(dead)`、`loot_create`
  （掉落表来自 `intent.args.loot`，内容侧负责提供）；玩家死亡额外写
  `player_presence(custody)`（需已有玩家档案）。
- 经验：胜者 `+3 combat_exp / +5 skill_xp`，20 回合平局双方 `+1 combat_exp`。
- 随机：`Writer` 注入 `PRNG(seed, seq, salt="action:fight")`；动作内**只**通过
  `combat_rules.below()` 取数，顺序固定为
  `dodge → parry → damage_cap → damage_roll → armor_cap → armor_roll → str_bonus → exp_check → wound`。
  `tests/test_combat_fight.py::test_prng_draw_order_is_locked` 写死首个战例的 15 次取数
  （含 `n` 值），顺序或次数变化立即失败。

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
| 新战斗基线 | 4 events（genesis + spawn + fight ×2），state `dc0aa76e…`，event `7968aebf…`（`tests/test_xkx_determinism_combat.py`） |
| 同 seed 双跑 | 新基线测试内双跑 state/event 哈希逐字节一致 |
| 回放等价 | 从 `events.sqlite` 逐条 `apply_effects` 重建的投影与实跑 state 哈希一致 |
| PRNG 锁序 | `test_prng_draw_order_is_locked` 固定 15 次取数标签与首个 `n=27450` |

## 6. 范围说明与已知边界

- 为完成任务必须修改 `src/wsim/world/state.py` 与 `src/wsim/world/effects.py`：它们
  不在允许目录列表内，但也不在禁改清单（api/ui/config/tools/content）内。
- `fight` 的 `target` 必须是 NPC；玩家对玩家 PvP 未开放（前置 `target_is_player`）。
- 掉落表由调用方/内容提供；内核不猜测尸体背包，避免物品重复计数。
- 战斗经验/技能成长只做整数保底，尚未接 xkx 的 `potential`、`qi/jing/neili/jiali`
  细项；这些属于后续内容侧与机制侧联动。
- `system_spawn` 是无内容时的“零事件”保障；若外部强行提交空 spawn 意图，
  `Writer` 仍会记一条 `effects=[]` 的事件（事件溯源框架的既有语义），因此事件侧
  约束由调度层负责，测试锁在 headless。
