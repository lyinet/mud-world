# S1 报告：出手门 + 守卫 + 反击 + 昏迷补刀（三件套）

- 日期：2026-11-08
- 规格：[`specs/xkx-parity/combat-tempo.spec.md`](../../specs/xkx-parity/combat-tempo.spec.md) §3 S1
- 开关：`combat.initiative_gate`，默认 `false`
- 口径：验收一律用 `kill`（复刻只有 `fight` 一个动词、`KILLING=True`）；`fight`/`kill` 的语义差异见 §5（S4，未实现）

## 0. 结论

1. 三件套（`:763` 出手门、`:783-800` 守卫、`:691-718` 反击、`:742` 昏迷快攻）一起实现，开关关闭时状态哈希与事件逐字节不变。
2. 狼狗 10 场：**10/10 击杀、10/10 掉狗皮**；玩家主动 `regular` 出手 **0**，出手 = 反击 + 1 次补刀。
3. 店小二 20 场：20/20 胜；玩家守卫下界 30.57%（原版 31%），店小二 15.26%（原版 24%，差 -8.74pp，解释见 §3.3）。
4. 老虎 10 场：0/10 胜、玩家主动 0 出手、`attack_rounds` 3–10（均值 6.00，原版 5.50）。
5. 基线 `2265c85c…` **不变**，无需迁移；合成战斗世界的差异只出现在 `fight` 事件内。
6. 活世界：8011/8041 未 `--reset` 重启（`tick` 4073 / 4076，`world: existing`），真实 HTTP 打狼狗 18 回合击杀、守卫文案正常；**掉落未通过**，根因是活世界创世早于掉落补丁 + 重生基线不含 `drops`，非 S1 回归（§3.6）。

## 1. 实现与改动清单

### 1.1 核心：`src/wsim/sim/combat_rules.py`

- `PLAYER_MID_CPS = 23`、`PLAYER_MID_COR = 24`：仅战斗层兜底，**不写入创世属性**，保证开关关闭时旧世界状态哈希不变。
- `TraceRandom.below(n)`：`n <= 0` 时返回 0、`trace.append((0, 0))`、`count += 1`，**不调用** `rng.randint`。
- `_raw_attr(ent, name)`：读原始 `attrs`，缺省 0（不使用 10 兜底）。
- `_Actor`：`cps = raw cps + apply/composure`，`cor = raw cor + apply/courage`；新增 `guarding` / `unconscious` / `dead`。
- `simulate_fight(..., initiative_gate: bool = False)`：开关打开才走三分支；关闭保持旧逐轮交换。
- 三分支（每轮每个 actor 一次决策）：
  1. 目标昏迷/死亡 → 攻击方 `guarding=False`，打一次 `quick`（补刀），随后目标判死；
  2. 否则掷门 `prng.below(target.cps * 3) > actor.cor` → 通过则 `guarding=False`，`regular` 出手，允许被反击；
  3. 门失败 → 首次 `guarding=True` 并两次 `prng.below(5)`（文案 + 转播），已守卫则静默、**0 取骰**。
- 反击 `_riposte(...)`：仅 `TYPE_REGULAR && damage < 1 && 防守方 guarding` 触发；`dst.guarding = False`（`:694`）、`roll = prng.below(src.cps)`（`:695`，`cps=0` 不取引擎骰）、`roll < 5` 走 `quick` 否则 `riposte`，**无条件**反击且不递归。
- 可观测字段仅在开关打开时写入：`attack_type`（`regular` / `quick` / `riposte` / `guard`）、`riposte`（bool，仅反击通道为 `true`）。

### 1.2 `combat_rules.py` 之外的改动（逐条）

| 文件 | 改动 | 原因 |
|---|---|---|
| `src/wsim/actions/combat.py` | `combat_effects(..., initiative_gate=False)`、`FightAction(initiative_gate=False)`、`apply` 透传；`_drop_counts` 读 `defender.inventory` / `explicit_loot` / `content_drops` | 让动作层能开关门，并让内容掉落在 headless 与 API 两条路径一致 |
| `src/wsim/actions/aggression.py` | `NpcAggressionAction(rules=None, initiative_gate=False)` 透传 | NPC 主动攻击同样受开关控制 |
| `src/wsim/actions/registry.py` | `build_registry(..., initiative_gate=False)` 注册 `FightAction` / `NpcAggressionAction` | 单一开关贯穿 |
| `src/wsim/api/session.py` | 读取 `world_config` 的 `combat.initiative_gate`（缺省/异常/非 dict → False），传给 `build_registry` | 运行时开关 |
| `src/wsim/api/xkx_shell.py` | 新增 `GUARD_MESSAGES`（原版 `combatd.lpc:25-31` 五条）；`_combat_report_lines` 渲染 `result == "guard"`，`guard_silent` 静默 | 守卫不能被渲染成“命中 0 伤害” |
| `src/wsim/sim/headless.py` | `RunConfig.initiative_gate: bool = False`，传给 `build_registry` | 对拍/回归可开关两跑 |
| `src/wsim/actions/player.py` | `ARCHETYPE_ATTRS` **不** 加 `cps/cor` | 保持创世属性与旧哈希 |
| `tests/test_xkx_initiative_gate.py` | 新增 7 个测试（骰流/骰序/生命周期/击杀闭环） | 锁死语义 |

- 公式库 `tools/xkx_formula/`：**零改动**（`git diff --stat -- tools/xkx_formula` 为空）。
- `config/worlds/default.yaml`：**不加键**。
- `config/worlds/xkx.yaml`：本次**不提交**开关（验收含活世界掉落未通过，见 §3.6），待老板判定后单独提交，便于回滚。
- 未改 `combat_rules.py` 的任何伤害/护甲/技能系数。

### 1.3 开关语义

一个 `combat.initiative_gate` 同时开启“门 + 守卫 + 反击 + 昏迷快攻”，不存在只开门的中间态。关闭时：

- 状态哈希 `2265c85c…`、事件哈希 `d0c4777d…`、722 事件逐字节不变；
- `combat_log` 动作不写 `attack_type` / `riposte` 字段。

## 2. 源码核对（先核后写，spec 已先落地）

### 2.1 `guarding` 生命周期（逐处行号）

| 动作 | 行号 | 行为 | 复刻 |
|---|---|---|---|
| 置位 | `combatd.lpc:784` | 首次进入守卫分支 `set_temp("guarding", 1)` | `actor.guarding = True`（仅门失败且未守卫） |
| 清除：被反击 | `combatd.lpc:694` | 受击方 `guarding` 且本次 `damage < 1` → `guarding=0` 后反击 | `_riposte` 里 `dst.guarding = False` |
| 清除：快攻 | `combatd.lpc:744` | 攻击方打 busy/昏迷目标前 `guarding=0` | 补刀分支先 `actor.guarding = False` |
| 清除：主动出手 | `combatd.lpc:764` | 门通过后 `guarding=0` 再 `do_attack` | 主动分支 `actor.guarding = False` |
| 清除：死亡/离场 | 对象销毁 | `guarding` 是临时变量，随对象消失 | 实体 `dead` 后不再参与决策 |
| **不清除**：被命中且有伤害 | `combatd.lpc:436+` | `damage >= 1` 不触发反击，`guarding` 保持 | 相同（只有 `damage < 1` 才清） |
| **不清除**：连续守卫 | `combatd.lpc:800` | 已 `guarding` 静默 `return` | 相同，且不取骰 |
| **不清除**：脱离战斗 | 无显式代码 | 没有“离开战斗清 guarding”的入口 | 不额外造清除点 |

> 因此原版确实存在“守卫标记一直挂着，直到下一次主动出手/被反击/快攻/死亡”的窗口；复刻按原样镜像，不添加“被打中就清”的额外逻辑。

### 2.2 骰序（源码核对结论）

| 顺序 | 取数 | 次数 | 源码 |
|---|---|---|---|
| 1 | `random(victim.cps*3)` 出手门 | `cps*3 > 0` 时 1 次；`cps=0` 时 **0 次** | `:763` |
| 2 | 守卫首次 `random(5)` ×2 | **恰好 2 次**（显示文案 + 转播文案） | `:785`、`:788` |
| 3 | 守卫已置位 | **0 次**（静默 `return`） | `:800` |
| 4 | 招式/闪避/招架/伤害 | 沿用既有公式库顺序 | `:280-300`、`:354`、`:406`、`:436+` |
| 5 | 反击 `random(me.cps)` | `cps>0` 时 1 次；`cps=0` 时 **0 次** | `:695` |
| 6 | 反击方整套 `do_attack` | 同 4 | `:705` / `:716` |
| 7 | 双击 | 条件恒假，**0 次** | `:748` / `:768` |

一次攻击心跳：

```
gate (random(victim.cps*3))
  ├─ 守卫：首次 → guardmsg×2 → return
  │        再次 → 0 次 → return
  └─ 出手：选招 → dodge → parry → damage/wound/reward
              └─ regular 且 damage<1 且对方 guarding:
                   random(attacker.cps) → 反击方整套 do_attack
```

### 2.3 `random(0)` 镜像

- LPC 驱动实测：`random(0)` 返回 0，且**不消耗**随机流。
- 复刻 `TraceRandom.below(n <= 0)`：返回 0、不调用 `rng.randint`；`draws` 只统计真实取骰。
- 测试（`tests/test_xkx_initiative_gate.py`）：同 seed 下 `victim.cps=0` 走 4 次真实取骰，`victim.cps=15` 走 10 次，差 **6** = 6 个回合的门判断次数；且 `calls[0] == (0, 0)`，证明门调用被记录但未落引擎。

### 2.4 测试锁死

`tests/test_xkx_initiative_gate.py`（7 个）：

1. `random(0)` 不取骰；
2. 同 seed 骰流长度差 = 门取数次数（4 vs 10，差 6）；
3. 首次守卫恰好两次 `guardmsg`，随后静默 0 取骰；
4. 首个引擎骰是门 `(0, 0)`，其后才是 `(5, 4)` / `(5, 3)`；
5. 固定 seed 完整骰序快照（含 `(69,24)` / `(69,48)` dodge / `(4680,612)` / `(6580,5432)`）；
6. 走兽不主动出手，但可被反击 + 补刀击杀；
7. dodge / parry 之后也触发反击（`rp[0]["riposte"] is True`），补刀 `riposte is False` 且恰好一次。

## 3. 验收（真跑，贴原始行）

工具：`/tmp/s1_accept.py`（临时，不提交），临时开关世界 `/tmp/s1-xkx-gate.yaml`；逐场独立 `WorldSession(seed, spawn, world_config)`。

### 3.1 狼狗（MID vs 郭庄大狼狗，`kill` 口径，10 场）

```
  1 | kill=True | 玩家 15/12 ({'regular': 0, 'quick': 15, 'riposte': 0}) riposte=14 finisher=1 | 狼狗 14/0 | rounds_used=28 attack_rounds=15 | 守卫首/静默 玩家 14/13 狼狗 4/9 | 狗皮=有 | draws=295
  2 | kill=True | 玩家 14/12 ({'regular': 0, 'quick': 14, 'riposte': 0}) riposte=13 finisher=1 | 狼狗 13/0 | rounds_used=20 attack_rounds=14 | 守卫首/静默 玩家 13/6 狼狗 4/2 | 狗皮=有 | draws=264
  3 | kill=True | 玩家 15/14 ({'regular': 0, 'quick': 15, 'riposte': 0}) riposte=14 finisher=1 | 狼狗 16/2 | rounds_used=31 attack_rounds=16 | 守卫首/静默 玩家 14/16 狼狗 8/6 | 狗皮=有 | draws=343
  4 | kill=True | 玩家 21/14 ({'regular': 0, 'quick': 21, 'riposte': 0}) riposte=20 finisher=1 | 狼狗 20/0 | rounds_used=33 attack_rounds=21 | 守卫首/静默 玩家 20/12 狼狗 8/4 | 狗皮=有 | draws=383
  5 | kill=True | 玩家 13/12 ({'regular': 0, 'quick': 13, 'riposte': 0}) riposte=12 finisher=1 | 狼狗 14/2 | rounds_used=26 attack_rounds=14 | 守卫首/静默 玩家 12/13 狼狗 8/3 | 狗皮=有 | draws=304
  6 | kill=True | 玩家 16/13 ({'regular': 0, 'quick': 16, 'riposte': 0}) riposte=15 finisher=1 | 狼狗 16/1 | rounds_used=30 attack_rounds=16 | 守卫首/静默 玩家 15/14 狼狗 9/4 | 狗皮=有 | draws=343
  7 | kill=True | 玩家 17/11 ({'regular': 0, 'quick': 17, 'riposte': 0}) riposte=16 finisher=1 | 狼狗 16/0 | rounds_used=27 attack_rounds=17 | 守卫首/静默 玩家 16/10 狼狗 6/4 | 狗皮=有 | draws=309
  8 | kill=True | 玩家 14/12 ({'regular': 0, 'quick': 14, 'riposte': 0}) riposte=13 finisher=1 | 狼狗 14/1 | rounds_used=21 attack_rounds=14 | 守卫首/静默 玩家 13/7 狼狗 5/1 | 狗皮=有 | draws=285
  9 | kill=True | 玩家 15/11 ({'regular': 0, 'quick': 15, 'riposte': 0}) riposte=14 finisher=1 | 狼狗 15/1 | rounds_used=28 attack_rounds=15 | 守卫首/静默 玩家 14/13 狼狗 8/4 | 狗皮=有 | draws=301
 10 | kill=True | 玩家 13/13 ({'regular': 0, 'quick': 13, 'riposte': 0}) riposte=12 finisher=1 | 狼狗 14/2 | rounds_used=32 attack_rounds=14 | 守卫首/静默 玩家 12/19 狼狗 9/8 | 狗皮=有 | draws=326
汇总：击杀 10/10，掉狗皮 10/10，rounds_used min=20 max=33 mean=27.60；attack_rounds min=14 max=21 mean=15.60
玩家主动 regular 合计： 0
玩家 quick 合计： 153  riposte 通道合计： 143  补刀合计： 10
```

- 回合口径：`attack_rounds = max(玩家出手, 狼狗出手)`，落在原版 7–24 区间；`rounds_used` 是交换轮，单位不同，按 S0 不得与 `attack_rounds` 相减。
- 逐场满足“玩家出手 = riposte + 1”（+1 是昏迷补刀）。
- 已知非 S1 差异：复刻狼狗内容没有 `vitals.max_qi`，`resolve_npc_vitals` 落到 `default_max_hp = 200`；原版 `beast.lpc:76` 的 age=4 狼狗 `max_qi=50`。这是回合偏长的原因，按红线不调数值。

首场原始战报（节选）：

```
你向大狼狗出手。
第1回合
  你注视着大狼狗的行动，企图寻找机会出手。
  大狼狗目不转睛地盯着你的动作，寻找进攻的最佳时机。
...
第27回合
  你缓缓地移动脚步，想要找出大狼狗的破绽。
  大狼狗举起爪子往你的要害抓了过去，但你闪开了。
  你提起拳头往大狼狗的要害捶去，造成15点伤害（气血9→0）。
第28回合
  你提起拳头往大狼狗的要害捶去，造成17点伤害（气血-1→0）。
共28回合，你赢了，大狼狗倒下了。
气血：你100 / 大狼狗 0。
超额伤害：大狼狗 1 点。
```

### 3.2 走兽不可主动（同一批样本）

- 10 场 `regular` 合计 **0**；玩家所有出手都来自 `quick`（其中 143 次 `riposte=true`，10 次补刀 `riposte=false`）。
- 字段来源（活世界真实 HTTP 战报的 `engine_details.combat.rounds`，与 headless 同构）：

```
guard   {"actor": "player:s1hunter", "attack_type": "guard", "riposte": null, "result": "guard", "damage": 0}
regular {"actor": "/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2", "attack_type": "regular", "riposte": false, "result": "dodge", "damage": 0}
quick   {"actor": "player:s1hunter", "attack_type": "quick", "riposte": true, "result": "hit", "damage": 18}
计数：guard 25、regular 9、quick+riposte 9、quick+补刀 1
```

即：狼狗的 9 次出手全是 `regular`，玩家的 10 次出手 = 9 次 `quick+riposte` + 1 次 `quick+补刀`，玩家 `regular` = 0。

### 3.3 店小二（MID vs 店小二，20 场）

```
101 | 玩家 13/13 守卫首/静默 9/0 | 店小二 11/0 守卫首/静默 3/0 | 回合=16
102 | 玩家 13/13 守卫首/静默 9/2 | 店小二 8/0 守卫首/静默 4/5 | 回合=18
103 | 玩家 13/13 守卫首/静默 3/1 | 店小二 8/0 守卫首/静默 3/1 | 回合=14
104 | 玩家 13/13 守卫首/静默 3/1 | 店小二 10/0 守卫首/静默 2/0 | 回合=14
105 | 玩家 12/12 守卫首/静默 8/2 | 店小二 9/0 守卫首/静默 2/1 | 回合=14
106 | 玩家 15/14 守卫首/静默 3/0 | 店小二 13/0 守卫首/静默 0/0 | 回合=15
107 | 玩家 13/13 守卫首/静默 7/0 | 店小二 11/0 守卫首/静默 1/0 | 回合=13
108 | 玩家 13/13 守卫首/静默 5/0 | 店小二 9/0 守卫首/静默 3/0 | 回合=14
109 | 玩家 12/12 守卫首/静默 9/1 | 店小二 11/0 守卫首/静默 1/0 | 回合=13
110 | 玩家 13/13 守卫首/静默 5/0 | 店小二 9/0 守卫首/静默 3/1 | 回合=15
111 | 玩家 15/14 守卫首/静默 8/0 | 店小二 13/0 守卫首/静默 1/0 | 回合=15
112 | 玩家 14/14 守卫首/静默 11/2 | 店小二 13/0 守卫首/静默 2/0 | 回合=16
113 | 玩家 14/14 守卫首/静默 6/0 | 店小二 13/0 守卫首/静默 2/0 | 回合=16
114 | 玩家 14/13 守卫首/静默 2/0 | 店小二 11/0 守卫首/静默 1/0 | 回合=14
115 | 玩家 15/14 守卫首/静默 2/0 | 店小二 12/0 守卫首/静默 1/0 | 回合=15
116 | 玩家 14/13 守卫首/静默 6/0 | 店小二 11/0 守卫首/静默 3/0 | 回合=15
117 | 玩家 12/12 守卫首/静默 5/1 | 店小二 7/0 守卫首/静默 2/2 | 回合=13
118 | 玩家 13/13 守卫首/静默 6/0 | 店小二 11/0 守卫首/静默 1/0 | 回合=14
119 | 玩家 14/13 守卫首/静默 3/0 | 店小二 10/0 守卫首/静默 2/1 | 回合=14
120 | 玩家 13/13 守卫首/静默 8/0 | 店小二 11/0 守卫首/静默 1/0 | 回合=14
下界比例：玩家 118/386 = 30.57%；店小二 38/249 = 15.26%
原版对照：玩家 31%、店小二 24%；胜场 20/20
```

- 玩家：30.57% vs 31%，差 **-0.43pp**，在 ±8pp 内。
- 店小二：15.26% vs 24%，差 **-8.74pp**，略超 ±8pp，解释：
  - 复刻店小二 `cps=15 / cor=10` 是内容固定值；原版每个实例在 `human.lpc:67-68` 用 `10+random(21)` 重掷，20 个实例的 `cor` 均值≈20。
  - 门是 `random(victim.cps*3) > me.cor`。玩家 `cps=23` → `random(69)`；`cor=10` 时失败下界约 `10/69 = 14.5%`，与观测 15.26% 吻合；`cor≈20` 时约 `20/69 = 29.0%`，原版观测 24% 落在两者之间（连续守卫静默不计入下界）。
  - 这是“固定实例 vs 每实例重掷”的分布差，不是公式改动；按红线**不调数值**。

### 3.4 老虎（MID vs 杭州老虎，10 场）

```
201 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 3/3 | 回合=3 | 玩家死亡=True | 玩家守卫首/静默 1/2
202 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 7/7 | 回合=7 | 玩家死亡=True | 玩家守卫首/静默 1/5
203 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 4/4 | 回合=4 | 玩家死亡=True | 玩家守卫首/静默 1/2
204 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 5/5 | 回合=6 | 玩家死亡=True | 玩家守卫首/静默 1/4
205 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 9/9 | 回合=9 | 玩家死亡=True | 玩家守卫首/静默 1/7
206 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 9/9 | 回合=10 | 玩家死亡=True | 玩家守卫首/静默 1/8
207 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 5/5 | 回合=5 | 玩家死亡=True | 玩家守卫首/静默 1/3
208 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 5/5 | 回合=5 | 玩家死亡=True | 玩家守卫首/静默 1/3
209 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 5/5 | 回合=5 | 玩家死亡=True | 玩家守卫首/静默 1/2
210 | 胜=defender | 玩家 0/0 {'regular': 0, 'quick': 0, 'riposte': 0} | 老虎 6/6 | 回合=6 | 玩家死亡=True | 玩家守卫首/静默 1/4
老虎胜场：0/10；回合 min=3 max=10 mean=6.00
玩家主动 regular 合计：0；玩家出手合计：0
```

- 0/10 胜，维持原版“老虎打不死”。
- `attack_rounds` 3–10、均值 6.00；原版 5.50（区间 2–19），处于同一量级，不要求逐场相等。
- 玩家全程 0 出手：老虎 `cps=0` → 门 `random(0)=0` 永不通过；只有挨打且 `damage<1` 时才可能反击，但老虎 3–10 次出手全部命中（见原版 S0 数据），故无反击机会。

### 3.5 活世界（8011/8041，未 `--reset`）

重启证据（`runs/xkx-world/logs/api.log`）：

```
{"ok": true, ..., "tick": 4073, ..., "world": "existing", ...}
{"ok": true, ..., "tick": 4076, ..., "world": "existing", ...}
```

`look`（tick 4177，真实 HTTP）：

```
【郭庄】庄园的大匾上是“郭庄”两个大字。大门(gate)紧闭，里面静悄悄的一点声音都没有。
出口：东→沿湖大道。
在场人物：二只大狼狗。
你身上有300文。
```

`fight 大狼狗`（tick 4089，节选）：

```
你向大狼狗出手。
第1回合
  你注视着大狼狗的行动，企图寻找机会出手。
  大狼狗举起爪子往你的要害抓了过去，但你闪开了。
  你往大狼狗的要害狠狠地踢了一脚，造成18点伤害（气血166→148，受了伤）。
第2回合
  你正盯着大狼狗的一举一动，随时准备发动攻势。
  大狼狗缓缓地移动脚步，想要找出你的破绽。
...
第17回合
  你目不转睛地盯着大狼狗的动作，寻找进攻的最佳时机。
  大狼狗跃起来用前掌往你的要害猛地一拍，但你闪开了。
  你往大狼狗的要害一抓，造成20点伤害（气血7→0）。
第18回合
  你往大狼狗的要害一抓，被大狼狗架住了。
共18回合，你赢了，大狼狗倒下了。
气血：你100 / 大狼狗 0。
超额伤害：大狼狗 1 点。
世界：第29天 09时
```

结论：活世界能继续打猎，守卫文案（五条原版文案）正常渲染，击杀闭环成立。

### 3.6 活世界掉落：**未通过**（根因非 S1）

- 该场 `fight` 事件 effects：

```
['hp_add', 'entity_status', 'combat_exp_add', 'combat_log', 'player_presence']
```

**没有 `loot_create`**。

- 根因 1：世界创建于 `2026-11-08T05:10:52`（`runs/xkx-world/world_id.json`），而大狼狗掉落补丁 `7fe7846` 提交于 `2026-11-08 07:36:42`；旧实体没有 `drops` 内容。
- 根因 2：`src/wsim/sim/room_reset.py` 的 `_baseline()`（约 80–139 行）重算重生载荷时**不含 `drops`**，所以 tick 4177 的 `entity_respawn` 复活出来的狼狗仍无掉落。
- 证据：活世界事件库中含 `"drops"` 的事件数 = **0**。
- 这与 S1 无关：headless 10/10 掉皮，说明 `FightAction._drop_counts` 路径正常；活世界缺的是实体数据。**未在本次改动中修复**，待老板决定是单独修 `_baseline` 还是允许一次 rebuild。

## 4. 基线归因（另开一次，不做迁移）

脚本：`/tmp/s1_baseline_diff.py`（临时，不提交）。原始输出：

```
A. 健康 xkx 1 日世界（seed 20261014，room_reset=True）
off: events=722 state_final=2265c85c1be9596d3f5b31dcc69fbfa57c1eca9f4a5ee4d9b783b6379263fe97
     event_final=d0c4777d605fed0046db5aa5d9a11712e82ef4fbb2cbe37553a05252c224e67d
on : events=722 state_final=2265c85c1be9596d3f5b31dcc69fbfa57c1eca9f4a5ee4d9b783b6379263fe97
     event_final=d0c4777d605fed0046db5aa5d9a11712e82ef4fbb2cbe37553a05252c224e67d
事件逐条相同: True
fight 事件: off=0 on=0；npc_aggression: off=0 on=0
旧基线 2265c85c… 与新值相同: True

B. 合成战斗世界（tests/test_xkx_determinism_combat.py 同场景）
gate off: state=91097d90bca6fd9097ff9190f1b4158a4d76f51f3b5e3940cd8b3b34617b995e
          event=859c990195cbfd59a941de3d42dfcd3aa36dcbac75ac463da8c42aa2f0918b90
gate on : state=005dc4b2839d82c5c313d9e8a655691283cd6ea6f08fc04c218d55c441b4bbe8
          event=ca186e6617f758aa3d386de8249fe14f5f2d56b02d45393e865c91d381d6622b
event[0] genesis: 完全一致
event[1] system_spawn: 完全一致
event[2] fight: 有差异
    op off=['hp_add', 'hp_add', 'entity_status', 'loot_create', 'combat_exp_add', 'combat_log']
    op on =['hp_add', 'entity_status', 'loot_create', 'combat_exp_add', 'combat_log']
    combat_log off: rounds_used=3 dead=['npc.boar'] draws=35 动作=<无字段>:5
    combat_log on : rounds_used=1 dead=['npc.boar'] draws=17 动作=guard:1, quick+riposte:1, regular+补刀/普通:1
event[3] fight: 有差异
    op off=['hp_add', 'entity_status', 'loot_create', 'combat_exp_add', 'combat_log']
    op on =['hp_add', 'hp_add', 'entity_status', 'loot_create', 'combat_exp_add', 'combat_log']
    combat_log off: rounds_used=2 dead=['npc.spawn_boar'] draws=23 动作=<无字段>:3
    combat_log on : rounds_used=4 dead=['npc.spawn_boar'] draws=58 动作=guard:3, quick+riposte:2, quick+补刀/普通:1, regular+补刀/普通:3
```

归因：

- **健康 1 日世界**没有 `fight` / `npc_aggression` 事件，开关开/关的输出逐条相同，`state_final 2265c85c…`、`event_final d0c4777d…`、722 事件全部不变 → **不需要迁移基线文件**，旧基线留档即可。
- **合成战斗世界**只有 `fight` 事件变化：`genesis` / `system_spawn` 逐字节一致；差异全部是 `attack_type` / `riposte` 字段与 guard/quick 动作流（`on` 侧多一次 `hp_add` 是反击/补刀顺序造成的效果合并形态差异）。
- `regular+补刀/普通` 是合成场景里野猪攻击玩家（野猪 `cor=0`，门恒通过），玩家从未主动 `regular`，与“走兽不可主动”的结论一致。

## 5. S4：`fight` vs `kill`（只给源码结论，不实现）

详见 spec §3 S4。结论：

| 维度 | `fight`（切磋） | `kill`（致命） |
|---|---|---|
| 入口 | `cmds/std/fight.lpc:52-53` 双方 `fight_ob`；对动物 `:57` 只 `me->fight_ob(obj); obj->kill_ob(me)` | `cmds/std/kill.lpc:82-100` 双方 `kill_ob` |
| 半血停战 | `combatd.lpc:665-685`：双方都未 `is_killing` 且目标 `qi*2<=max_qi` → `remove_enemy` + `return 1` | 不触发 |
| 死亡入口 | 半血停战，不会走到 `char.lpc:65-78` 的 `die()` | `eff_qi<0 → die()`；`qi<0 → unconcious()`，下一心跳 `die()` |
| 掉落 | 不 `die()` → **无掉落** | `wolfdog.lpc:38-41` 的 `die()` 直接 `new obj/goupi` |
| 成长 | `damage_rewards` / `dodge_rewards` / `parry_rewards` 照常给 `combat_exp` 与 `improve_skill` | 同左 |

复刻现状：只有 `fight` 动词、`KILLING=True` 恒定，倒地即死并掉皮 → 玩家切磋会把店主/狼狗打死，是产品可见偏差。**本轮不实现**，等 S1 验收结论后再单独立项。

## 6. 红线核对

- 公式库 `tools/xkx_formula/`：`git diff` 为空。
- `combat_rules.py` 之外改动：见 §1.2 逐条。
- 开关默认 false；`config/worlds/default.yaml` 未加键；`config/worlds/xkx.yaml` 未在本提交打开。
- 全量 pytest：1020 项收集、1002 通过、18 skip、0 failed（exit 0）。
- 未 force push；未动 `master`。
