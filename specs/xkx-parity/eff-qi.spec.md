# spec：`eff_qi` 气血上限（有效气血）——死亡判定、恢复与昏迷

状态：**待批准（第二版，已并入审批意见 1–9）**
适用：`worlds/xkx`（开关 `combat.eff_qi`，默认 **False**）
红线：`tools/xkx_formula/**` 零改动；`content/xkx/**` 不动；显式 `git add`；不 force。

---

## 0. 结论先行

原版确有、我们缺的不是“一个比较符”，而是 **`eff_qi` 的持久化 + 心跳恢复 + 昏迷状态**：

| 原版量 | 语义 | 我们当前 |
|---|---|---|
| `qi` | 当前气血 | `Entity.hp`（有） |
| `max_qi` | 气血上限 | `Entity.max_hp`（有） |
| `eff_qi` | 有效气血（伤势天花板，`<= max_qi`） | `_Actor.eff_hp`，**每场战斗重置为 `max_hp`，不回写实体** |
| `max_eff_qi` | **该版本不存在** | 不需要（全库 0 命中） |

三处缺口：
1. `combat_rules.py:681` 对昏迷目标 `target.dead = True` **无条件判死**，绕过了 `eff_qi` 的补刀过程；
2. `eff_hp` 只活在 `_Actor`（`combat_rules.py:182`），战斗结束只回写 `hp`（`actions/combat.py:151-156`），**跨场伤势丢失**；
3. 恢复（`rest`/`sleep`/物品）只封顶 `max_hp`，不认 `eff_qi`；昏迷状态不落库（`effects.py:488` 只收 alive/dead/reincarnated）。

---

## 1. 原文审计（带行号）

### 1.1 四个量的分工

`inherit/char/char.lpc:12` 继承 `F_DAMAGE`，玩家与 NPC 共用同一套逻辑（NPC 初始化 `chard.lpc:72-80`、`npcd.lpc:303-304`、`updated.lpc:263-268` 都会在 set `max_qi` 后同步 `qi/eff_qi = max_qi`）。

- `qi`：当前气血；`max_qi`：气血上限。
- `eff_qi`：伤势天花板；`receive_heal` 不能把 `qi` 抬过它；自身恢复天花板是 `max_qi`。
- 该版本**没有** `max_eff_qi`。

`eff_qi` 的削减途径不止战斗：`inherit/skill/force.lpc:16`（内功反噬）、`cmds/std/go.lpc:292`（落石）、`clone/medicine/nostrum/wuchangdan.lpc:43-44`、`clone/weapon/treasure/zhenwu-jian.lpc:52`。本期只做战斗 + 持久化 + 恢复，其余留 `eff_hp_set/eff_hp_add` 事件接口，不落内容。

### 1.2 两条伤害通道（`feature/damage.lpc`）

`receive_damage`（`:14-38`）：只扣 `qi`，下限 **-1**（`if (val >= 0) set(type, val); else set(type, -1);`），不碰 `eff_qi`。

`receive_wound`（`:40-69`）：

```lpc
56	  val = (int)query("eff_" + type) - damage;
58	  if (val >= 0) set("eff_" + type, val);
59	  else { set("eff_" + type, -1); val = -1; }
64	  if ((int)query(type) > val) set(type, val);
```

扣 `eff_qi`，下限 -1，**随后把 `qi` 钳到不高于新的 `eff_qi`**。

**“伤害先扣 `qi`、超出部分再扣 `eff_qi`”不成立**：两条通道独立，`qi` 必掉，`eff_qi` 按概率掉。

### 1.3 骰序锁死（`combatd.lpc`，实现必须逐次对齐）

护甲吸收（`:436-456`），顺序固定：

1. `damage = apply/damage | apply/unarmed_damage`；
2. `if (damage > 300) damage = 300 + random(damage - 300);` ← 骰 #1，仅当 `damage>300`
3. `damage = (damage + random(damage)) / 2;` ← 骰 #2，**总是**一次 `random(damage)`（`damage==0` 时 LPC `random(0)==0`）
4. `if (action["damage"]) damage += action["damage"] * damage / 200;`
5. `defense_factor = apply/armor;`
6. `if (defense_factor > 300) defense_factor = 300 + random(defense_factor - 300);` ← 骰 #3，仅当 `>300`
7. `defense_factor = (defense_factor + random(defense_factor)) / 2;` ← 骰 #4，总是
8. `damage -= defense_factor; if (damage < 0) damage = 0;`
9. `victim->receive_damage("qi", damage, me);`（无骰）

破防追加 `receive_wound`（`:560-567`），**只认原始 `apply/armor`**：

```lpc
560	  if (random(damage) > (int)victim->query_temp("apply/armor")
561	    && ((me->is_killing(victim->query("id"))) &&
562	      ((!weapon) && !random(4) || weapon && !random(2))
563	      || ((!weapon) && !random(7) || weapon && !random(4)))) {
565	    victim->receive_wound("qi", damage - (int)victim->query_temp("apply/armor"), me);
```

按 Lazy `&&`/`||` 求值，真实骰次：

| 场景 | 骰次序列 | 破防概率 |
|---|---|---|
| 空手击杀 | `random(damage)`；通过则 `random(4)`；失败再 `random(7)` | `1/4 + 3/4·1/7 = 10/28 = 35.71%` |
| 空手非击杀 | `random(damage)`；通过则 `random(7)` | `1/7 = 14.29%` |
| 兵器击杀 | `random(damage)`；通过则 `random(2)`；失败再 `random(4)` | `1/2 + 1/2·1/4 = 62.5%` |
| 兵器非击杀 | `random(damage)`；通过则 `random(4)` | `1/4 = 25%` |

**红线：`apply/defense` 不参与破防判定，只认 `apply/armor`。** `combatd.lpc:560` 的右值是 `query_temp("apply/armor")`；我们模型里内容的基础 `defense`（NPC 护甲值）仍参与 `:436-456` 的减伤（与关闭态一致），但 **temp `apply/defense` 既不减伤也不破防**。`combat.eff_qi` 开启时破防门实现为 `roll > apply_armor`，`receive_wound` 的伤害也用 `damage - apply_armor`，避免后来人把 `defense` 并进来。关闭态沿用旧口径（`base defense + apply/armor`），仅为满足"关闭态与开启前逐字节一致"，不得作为开启态的依据。

### 1.4 昏迷与死亡（`char.lpc:64-78` 逐字）

```lpc
64	  // If we are dying because of mortal wounds?
65	  if (my["eff_qi"] < 0 || my["eff_jing"] < 0) {
66	    remove_all_enemy();
67	    die();
68	    return;
69	  }
70
71	  // If we're dying or falling unconcious?
72	  if (my["qi"] < 0 || my["jing"] < 0) {
73	    remove_all_enemy();
74	    if (!living(this_object()) ||
75	      this_object()->query_temp("noliving")) die();
76	    else unconcious();
77	    return;
78	  }
```

- `< 0` 才触发；`qi == 0` 仍 alive。
- `unconcious()`（`damage.lpc:105-144`）：`set("jing",0); set("qi",0);`，`call_out("revive", random(100-con)+30)`。
- 昏迷被补刀：`combatd.lpc:741-747` 对 `!living(victim)` 走 `TYPE_QUICK`，**绕过出手门**（不取门骰），命中判定仍正常走闪避/招架。
- 补刀转死的精确条件（原文核对，纠正“必须多次”的说法）：
  1. `unconcious()` 里 `set("qi",0)`（`damage.lpc:137-138`），所以刚昏迷时 `qi==0`，下一次心跳不会 `die()`；
  2. 玩家昏迷时 `set_temp("noliving/unconcious",1)`、NPC 走 `disable_player`（`damage.lpc:131-135`）；
  3. 补刀只要**命中且伤害 ≥1**，`qi` 重新 `<0`；该实体下一次心跳命中 `char.lpc:74` 的 `!living || query_temp("noliving")` → `die()`。
  4. 因此“补刀次数”分布 = 1 次有效命中 + N 次未命中/被招架；**不是** N 次有效命中。`eff_qi < 0` 则是 `char.lpc:65-68` 的直接 `die()`，与昏迷无关。
- 结论：`eff_qi` 的作用不是“让昏迷多挨几刀”，而是**让未昏迷阶段能扛更多回合**（`qi` 掉光前 `eff_qi` 已被 wound 通道逐步削减）。这一点写进验收口径，避免拿“补刀次数”误判。

### 1.5 恢复（`feature/damage.lpc:323-374`）

由 `char.lpc:106-109` 的 tick 门控：

```lpc
106	  if (tick--) return;
107	  else tick = 5 + random(10);
```

- 触发间隔 = `tick + 1` 次心跳 → **6..15 次，均值 10.5**。
- 每次 `heal_up`：`qi += con/3 + max_neili/10`；若 `qi >= eff_qi` 则 `qi = eff_qi`，且 `eff_qi < max_qi` 时 **`eff_qi++`（每次只回 1 点）**。
- `receive_heal`（`:71-84`）封顶 `eff_qi`；`receive_curing`（`:86-103`）抬 `eff_qi`、封顶 `max_qi`。
- `sleep`（`cmds/std/sleep.lpc:208-209`）：醒来 `qi = eff_qi`，**不回满 `max_qi`**。
- 死亡（`:295-308`）：玩家 `qi=1, eff_qi=1` 进鬼门关；NPC `destruct`。
- 还阳 `reincarnate`（`:315-321`）：`eff_qi = max_qi`；`qi` 仍为 1。

### 1.6 半血停战读哪个量

`combatd.lpc:665-667`：`victim->query("qi") * 2 <= victim->query("max_qi")` → 读 **`qi`**，不读 `eff_qi`。映射为 `dst.hp`，`combat_rules.py:604-609` **不改**。

### 1.7 合并后的骰序总表（多人在位，`RecordingPRNG` 锁死）

基线：`origin/xkx-parity@0e84be6`（多人同屏已合入）。**选敌骰在独立派生流** `PRNG(seed, seq, "action:npc_aggression:opponent")`，主战斗流 `PRNG(seed, seq, "action:npc_aggression")`（玩家侧 `action:fight`/`action:kill`）从第 1 笔起不受选敌影响。

| # | 阶段 | 骰/取数 | 流 | 条件 |
|---|---|---|---|---|
| 0 | 选敌（仅 NPC 心跳） | `randint(0,3)`（原版 `random(4)`）；越界回退 `targets[0]` | `…:opponent` | 每次 NPC 出手，**单敌也取**；prune 失效目标不耗骰 |
| 1 | 逃跑（wimpy） | 按原版 `wimpy` 规则，成功则不进入战斗骰 | 主战斗流 | 仅 `wimpy` 触发 |
| 2 | 出手门 | `randint(0, max(0, target.cps*3-1))`，`roll > actor.cor` 才出手 | 主战斗流 | `eff_qi` 不改变此门；`cps*3==0` 不取骰 |
| 3 | 守卫文案 | 首次：`randint(0,4)` 选文案 + `randint(0,4)` 转播（仅门失败时） | 主战斗流 | 与该 actor 的 `guarded` 标记互斥，静默后不再取骰 |
| 4 | 选招 | `select_action` 按技能表取骰 | 主战斗流 | 已昏迷目标走 **Quick**，**跳过 #2/#3/#4** |
| 5 | 命中 | 闪避骰 + 奖励骰（`resolve_dodge`） | 主战斗流 | 未命中则本轮结束 |
| 6 | 招架 | 招架骰 + 奖励骰（`resolve_parry`） | 主战斗流 | 被招架则本轮结束 |
| 7 | 钩子 | force/martial 钩子骰 | 主战斗流 | 无钩子不取 |
| 8 | 伤害 | 护甲随机折半：`random(damage)`、`random(armor)`（`>300` 时各多一笔） | 主战斗流 | 见 §1.3 骰 #1–#4 |
| 9 | 扣气血 | `apply_qi_damage`（无骰） | — | `qi` 下限 -1 |
| 10 | 破防 | `random(damage)` → 击杀分支再 1–2 笔（§1.3） | 主战斗流 | 只认 `apply/armor` |
| 11 | 反击 | 守方 `random(cps)` 判定 + 反击命中骰 | 主战斗流 | 快攻目标：**不再反击**，攻击方下一轮继续 Quick |

锁死方式：`RecordingPRNG` 断言 (a) 独立流只出现 #0 一笔/次；(b) 主战斗流逐笔与关闭态一致（仅当 `eff_qi` 打开时 #8–#10 产生的伤害值不同，**笔数不变**）；(c) 昏迷目标的 #2/#3/#4 三阶段 0 笔。

### 1.8 昏迷 / 死亡与多人目标（原版 `remove_all_enemy`）

- `char.lpc:66,73`：`eff_qi<0` 与 `qi<0` 两条路径都先 `remove_all_enemy()`。
- 我们的映射：进入 `unconcious` **和** `dead` 时都清空 `combat_enemies` 列表与 `combat_target`；`combat_contrib` 保留（奖励归属需要，原版 `kill` 结算不依赖 `enemy` 表）。
- 昏迷中的 NPC：scheduler 不再为其生成出手/游走/买卖意图（原版 `disable_player`）；昏迷中的玩家：拒绝除 `look`/`hp` 等只读命令外的动作。
- 苏醒（`revive`）后敌人表为空，**不自动恢复仇恨**；需重新 `kill`/被攻击才重新登记（原版如此）。
- 昏迷目标仍可被攻击：#4 走 Quick、跳过门；命中则 `qi<0` → 下一心跳死亡。

---

## 2. 恢复速率：实测推导（替代上一版自定的 6 点/小时）

### 2.1 已确认的换算基准

| 量 | 值 | 出处 |
|---|---|---|
| 心跳间隔默认值 | **1000 ms** | `src/fluffos-v2019/src/base/internal/rc.cc:68`（`"heartbeat interval msec"` 默认 1000）；`run/mudlib/config.ini:149-150` 该行被注释，未覆盖 |
| 游戏时间流速 | **1 现实秒 = 365 游戏秒** | `adm/daemons/natured.lpc:8` `#define TIME_TICK ((time()%(60*24*3600))*365)` |
| heal_up 间隔 | **6..15 心跳，均值 10.5** | `inherit/char/char.lpc:106-109` |
| 每次 heal_up 抬 `eff_qi` | **+1 点** | `feature/damage.lpc:371-374` |
| 我们的 tick | **10 游戏分钟 = 600 游戏秒** | `src/wsim/core/clock.py`、metrics 注释 |

折算：1 心跳 = 365 游戏秒 = 6.083 游戏分钟 = **0.6083 tick**；1 tick = **1.6438 心跳**。

### 2.2 实测结果（2026-11-08，4004 参照实例）

插桩位置同上（`run/mudlib` 运行副本，`.c` 后缀），测完已还原并重启。

| 量 | 实测 | 样本 |
|---|---|---|
| 心跳间隔 | 121 次心跳跨 121 s（t=1794118580..1794118701），约 **1008 ms**；每秒 1 次 | 1 个玩家，121 s |
| heal_up 间隔（心脏跳动数） | **6..16，均值 10.548，中位 10** | 57 个实体、416 个区间样本 |
| heal_up 分布 | 6:37 7:45 8:43 9:46 10:38 11:40 12:42 13:44 14:28 15:43 16:10 | 同上 |
| 单次 `qi` 增量 | +6（`con=20`，`max_neili=0` → `con/3`） | 玩家 `qi` 1→7→13… |

结论：实测均值 10.548 与源码期望 10.5 偏差 0.46%，心跳在 1000±100 ms 内，**采用 §2.3 的 0.1558 点/tick**（按 10.548 心跳；若按 10.5 则 0.1566，差异 <0.5%，不改变实现常量）。

- heal_up 平均间隔 = 10.548 心跳 × 1.008 s ≈ 10.63 现实秒 = **6.47 tick**。
- `eff_qi` 恢复 = 1 点 / 6.47 tick = **0.1546 点/tick**（按 1000 ms 默认值为 **0.1558**）= **0.928~0.935 点/游戏小时**。
- 玩家 `max_qi` 回满（`eff_qi: 1 → max_qi`）：
  - `max_qi=100`：100 × 6.47 = **647 tick = 107.8 游戏小时 = 4.49 游戏天**（原版现实约 1055 s = 17.6 分钟，按 365× 即 106.9 游戏小时，一致）。
  - `max_qi=425`（MID 老虎档）：425 × 6.47 = **2750 tick = 458 游戏小时 ≈ 19.1 游戏天**。

### 2.3 校准公式（后续若心跳配置变化，按此重算）

```text
hb_per_tick   = 600 / (365 × 实测心跳秒)
points_per_tick = hb_per_tick / 实测heal_up均值(心跳)
full_refill_ticks = max_qi / points_per_tick
```

当前实测落在 1000 ms / 10.5 心跳附近，实现采用 `HEAL_CREDIT_PER_TICK = 16438`（1/10000 心跳单位）与 `HEAL_CD ∈ [6,15]`；**禁止再用 6 点/小时**。

### 2.4 实现方式（避免整数取整偏差）

- 每个实体维护 `heal_credit`（单位 1/10000 tick）与 `heal_cd`（心跳数，0 表示待抽）。
- 每 tick：`heal_credit += round(10000 × hb_per_tick)`；`while heal_credit >= heal_cd×10000`：触发一次 heal_up，扣减并按 `6 + rng.randrange(10)` 重抽 `heal_cd`。
- heal_up 内容：`eff_hp += 1`（`eff_hp < max_hp` 时）；`hp = min(hp + con//3 + neili//10, eff_hp)`。
- `con` 取 `Entity.attrs["con"]`（缺省 20）；`max_neili` 本引擎暂为 0。

---

## 3. 设计

### 3.1 字段与开关

- `Entity.eff_hp: int = 0`（0 = 未初始化/旧内容）。
- `Entity.heal_credit: int = 0`、`Entity.heal_cd: int = 0`。
- `Entity.revive_tick: int = 0`（0 = 不在昏迷）。
- 开关 `combat.eff_qi`：引擎默认 **False**；`config/worlds/xkx.yaml:14-16` 先写 `eff_qi: false`，验收通过后**单独提交**改为 `true`。
- 关闭时：不取骰、不发新事件、不序列化新字段 → 与当前逐字节一致。

### 3.2 结算（开关打开时）

1. 战斗开始：`_Actor.eff_hp = ent.eff_hp if ent.eff_hp > 0 else ent.max_hp`（旧实体迁移）。
2. 伤害按 §1.3 的骰序执行；`apply_qi_damage` → 破防门 → `apply_qi_wound`（含 `qi` 钳位）→ `death_state`。
3. **删除** `combat_rules.py:671-681` 的无条件 `target.dead = True`；昏迷目标 Quick 补刀与普通命中同一路径，由 `eff_qi` 概率通道决定转死时机。
4. 回合末回写：`hp_add`（`hp` 可为 **-1**）+ `eff_hp_set`（钳 `[-1, max_hp]`，值不变不发事件）。
5. 状态落库：
   - `eff_hp < 0` → `entity_status=dead`（现有死亡/掉落/尸体链路不变）；
   - `hp < 0 && eff_hp >= 0` → `entity_status=unconcious`，`revive_tick = tick + revive_ticks`（§4.2），`hp = 0`（对齐 `unconcious` 的 `set("qi",0)`）。
6. 到 `revive_tick`：`status=alive`，**`hp` 保持 0**（`revive()` 原文不写 `qi`），`eff_hp` 不变，随后靠 heal_up 回填。
7. 玩家还阳 `reincarnate`：`eff_hp = max_hp`，`hp = 1`（对齐 `damage.lpc:315-321` + 死亡时 `qi=1`）。

### 3.3 被动心跳恢复（本期必做）

- 新增系统动作 `system_heal`（`src/wsim/actions/system.py`，紧邻 `SystemRecoverAction`）：每 tick 由 `scheduler._process_tick` 在**确有需要时**发一次（存在 `status=="unconcious"`、或 `eff_hp < max_hp`、或 `hp < eff_hp` 的实体），否则不发，保证开关关闭/无人受伤时事件序列不变。
- 内容：§2.4 的 `heal_credit` 累积 + heal_up；以及昏迷到期苏醒扫描。
- 副作用：`water`/`food` 每次 heal_up 各 -1、耗尽则不回血（`damage.lpc:345-353`）——本引擎若没有 food/water 字段，则写明“本期不建模饥渴断疗”，作为已知偏差。

### 3.4 疗伤手段

本引擎**没有** `receive_curing` 对应的命令/物品（`hp_add` 只抬 `qi`，且封顶 `eff_hp`）。本期明确：**没有疗伤手段，伤势只靠时间（heal_up）恢复**；`eff_hp_add` 仅留事件接口，不落内容。

### 3.5 序列化与迁移

- `to_dict()`：仅当 `max_hp > 0 && eff_hp != max_hp` 时输出 `eff_hp`、`heal_credit`、`heal_cd`。
- `revive_tick` 仅在 `status == "unconcious"` 时输出。
- 旧实体（`max_hp > 0 && eff_hp == 0`）加载时视为 `eff_hp = max_hp`（“旧实体视为未受伤”，`eff_qi = qi = max_qi`），不追溯旧伤。
- 旧九地点（`max_hp == 0`）不新增任何键 → 哈希 `735 / 009076f0… / 02d5f812…` 不变。
- xkx 活世界**不** `--reset`；迁移证据：同一实体迁移前后 `to_dict()` 差异仅 `eff_hp`（受伤后才出现）、旧九地点三项哈希复算不变。

---

## 4. 昏迷与 revive 换算

### 4.1 原版数值

`call_out("revive", random(100 - con) + 30)` 心跳。玩家默认 `con=20` → **30..109 心跳**（con≤0 才是 30..129，用户所说 30–130 的上沿）。

### 4.2 折算

| con | 心跳 | tick（×0.6083） | 游戏时间 |
|---|---|---|---|
| 20 | 30..109（均值 69.5） | **18..66（均值 42.3）** | 3.04..11.05 游戏小时（均值 7.05） |
| 10 | 30..119 | 18..72 | 3.04..12.1 游戏小时 |

实现：`revive_ticks = max(1, round((30 + rng.randrange(100 - con)) * 0.6083))`，`revive_tick = tick + revive_ticks`，随机数走 `WorldSession` PRNG 并写入事件载荷，保证回放。

### 4.3 苏醒后的值

- `qi/hp = 0`（昏迷时已归 0，`revive()` 不再赋值）→ 我们**不**在苏醒时补血。
- `eff_qi` 不变（伤势仍在）。
- `jing` 不在本期建模（见 §8）。

### 4.4 测试

1. 昏迷实体跨 tick 存续：`status=unconcious` 且未到 `revive_tick` 时，被补刀 0 次也不消失/不复活。
2. 到期苏醒：`tick == revive_tick` 时 `status=alive`、`hp=0`、`eff_hp` 不变。
3. 被补刀多次才转死：固定 PRNG 下统计补刀次数 `>= 2` 才 `eff_hp < 0`。

---

## 5. `hp_add` 与恢复封顶

- 开关打开时 `hp_add` 下限 **-1**（`effects.py:474` 的 `max(0, …)` 改为 `max(-1, …)`）；关闭时仍为 0。
- `receive_wound` 后 `hp <= eff_hp` 钳位（公式库已有，接线验证）。
- `sleep` 醒来 `hp = eff_hp`（`actions/player.py:580-594` 改为按 `eff_hp` 计算，不回满 `max_hp`）。
- `rest` / 物品 `hp_add` 一律封顶 `eff_hp`。

---

## 6. 验收（目标是回合数）

### 6.1 单测（开关 True/False 各跑）

1. `hp=0, eff_hp=50` → alive；`hp=-1, eff_hp=50` → unconcious；`eff_hp=-1` → dead。
2. `hp=-1` 可被治疗救回：`hp_add` 后 `hp<=eff_hp`；到 `revive_tick` 回 alive。
3. `receive_wound` 后 `hp <= eff_hp`。
4. 跨场伤势：第一场后 `eff_hp=30/50`，第二场 `_Actor.eff_hp` 仍 30。
5. 昏迷补刀多次才死（§4.4）。
6. 停战读 `hp`：`eff_hp` 很低但 `hp*2>max_hp` 不停战；反之停战。
7. `sleep` 只回到 `eff_hp`。
8. `eff_hp == max_hp` 时 `to_dict()` 无新键；`!=` 时才出现。
9. 开关关闭：事件序列/状态哈希与当前基线逐字节一致；legacy `735 / 009076f0… / 02d5f812…` 不变。
10. 骰序：`RecordingPRNG` 断言 §1.3 四种场景的 `random()` 调用次数与顺序；`apply/defense` 不参与破防（构造 `defense=100, apply_armor=0`，破防骰仍按 `random(damage) > 0`）。

### 6.2 重跑

- 狼狗 `kill` 10 场：`attack_rounds` 均值/区间回到原版 **7–24（约 13）**；给出每刀伤害、每回合伤害、有效血量（累计伤害/`max_qi`）、昏迷次数、昏迷→死亡补刀次数分布。
- 老虎 `kill` 10 场（`max_qi=425`）：回合数仍应接近原版 ~5.5（“打不动”）。
- 店小二 `fight` 20 场：20/20 半血停战；贴一次战报，证明停手时 `qi` 到阈值而 `eff_qi` 仍高于 `qi`（停战判据读 `qi`）。
- **多目标回归**（8031 或独立实例）：两人同打一只怪，`combat_enemies` 仍为两人、选敌分布仍 75/25；昏迷/死亡时敌人表被清空；`combat_contrib` 保留且不被少人改动碰坏。
- 若回合仍显著短于原版，先报“伤害/防御映射偏差”归因，不回调数值。

### 6.3 活体验收

- 8011/8041：`fight 店小二` 半血停手 → `look` 人在 → `buy` 成功；`kill 大狼狗` → 掉皮、背包 +1。
- **0 血不死、可救**（真实 HTTP）：把玩家打到 `hp<0`（昏迷）→ 不死亡、`look` 可见昏迷 → 自然苏醒（或 `hp_add` 治疗）→ 继续活动；随后再把 `eff_hp` 打到 `<0` → 死亡 → 鬼门关 → 还阳。
- 死亡惩罚复检：活体记录一次“被打死 → 鬼门关 → 还阳”，确认 `exp -= exp/50`、技能降级（`effects.py:774-776`）、鬼门关传送与还阳（`actions/combat.py:182-190`、`actions/growth.py:594`）仍触发。
- xkx 活世界不 `--reset`；给迁移前后同实体字段对比 + 旧九地点三项哈希。
- 开关关闭态与开启前逐字节一致；`config/worlds/xkx.yaml` 验收后单独提交开启。

---

## 7. 与 `combat.eff_qi` 关闭态的字节一致性

所有新分支（新字段、新事件、新骰、新系统动作）均以开关为唯一闸门；关闭时：
- `_Actor` 不读 `eff_hp`，回写不含 `eff_hp_set`；
- `hp_add` 下限仍 0；
- 不注册/不触发 `system_heal`；
- `to_dict()` 不输出新键。

---

## 8. 已知偏差：精神（`jing`/`eff_jing`）

原版 `char.lpc:65,72`：`eff_jing < 0` 也会 `die()`，`jing < 0` 也会 `unconcious()`。本期**不建模**：

- 玩家可见后果：原版存在“被耗精神致昏/致死”的战斗路径；我们没有这条路径，MP 型（精神）攻击对我们只按气血结算或无效。
- xkx 中实际扣 `jing` 的攻击来源（源码清单）：
  `kungfu/skill/dagou-bang/ban.lpc:59`、`kungfu/skill/zhanzhuang-gong/roar.lpc:42,44`、`kungfu/skill/qishang-quan/jingshi.lpc:74-75`、`kungfu/skill/liuyang-zhang/po.lpc:81,102-103`、`kungfu/skill/liuyang-zhang/zhong.lpc:86`、`kungfu/skill/huifeng-jian/jue.lpc:76-77`、`kungfu/skill/huifeng-jian/mie.lpc:68-69`、`kungfu/skill/banruo-zhang/yipai.lpc:73`；自损类 `hamagong/hama.lpc:63,114,165`、`meiren-sanzhao.lpc:60`、`taiji-quan/ji.lpc:93,97`、`luanhuan.lpc:76`、`luteplaying.lpc:49,57`、`tiezhang-zhangfa/pushan.lpc:67,119,169`。
- 不做理由：这些全部是**特技**（perform），本引擎当前只实现基础 40 级空手/轻功，没有任何 `perform` 入口；建模 `jing` 需要同时引入精神条、特技系统与 `eff_jing` 恢复，超出本期 `eff_qi` 范围。等特技子系统立项时一并做。

---

## 9. 待批准清单

1. §2 的实测任务与校准公式（实测前采用 0.1566 点/tick，实测偏离则按公式重算）。
2. §3 的实现面（字段/事件/开关/`system_heal`）。
3. §3.4“本期无疗伤手段”与 §8“`jing` 不建模”的明示。
4. §6 的验收项与“config 开启单独提交”。

---

## 10. 实现与验收记录（2026-11-08）

### 10.1 提交

- `2e0f4ed` feat(xkx-parity): eff_qi 有效气血/伤势天花板（开关默认关）
- `9773182` fix(xkx-parity): eff_qi 关闭态恢复旧口径 + 昏迷即结束本场 + 双向清敌人表
- `a9cfdc2` fix(xkx-parity): remove_all_enemy 改为事件旗标门控，关闭态零副作用
- 本节记录 + `eff-qi.tasks.md` 勾选：单独提交
- `config/worlds/xkx.yaml` 的 `combat.eff_qi: true`：验收通过后单独提交

### 10.2 与设计的细化（不影响验收语义）

1. `Entity.eff_hp` 默认 `-2`（spec §3.1 写 0）：`-2` = 引擎未初始化/旧世界实体，`-1` = 伤势到濒死，`>=0` = 有效气血。`_Actor` 把 `<= -2` 视作 `max_hp`（等价 §3.5“旧实体视为未受伤”），`-1` 仍按真实值参与 `eff<0` 判定。
2. `to_dict()` 只在 `eff_hp >= -1 and eff_hp != max_hp` 时输出 eff 系列；旧实体零新键。`revive_tick` 只在非 0 时输出（spec 写“status==unconcious 才输出”，语义等价且不残留苏醒后的键）。

### 10.3 关闭态逐字节一致

同种子、同指令两场景（酒楼 `kill 店小二`；郭庄 `kill 大狼狗`）：`events_total`、`event_hash`、`state_hash`、全部回复行均 `cur == base`。旧世界哈希 `735 / 009076f0… / 02d5f812…` 不变。

### 10.4 迁移

活世界快照 12899 events / 161 entities，两树 `replay_into` 后全实体 `to_dict()` **BYTE-IDENTICAL**，`eff_keys_in_dict = 0`；`_Actor` 对 `eff_hp=-2` 取 `max_hp`。

### 10.5 验收数值（`eff_qi: true`，脚本 `/tmp/eq_accept2.py`）

狼狗 `kill` 10 场（seed 1–10，同 seed 与关闭态对照；脚本 `/tmp/tempo_diag.py`）：

| seed | 回合 | 玩家出手 | 命中 | 伤害 | 每刀 | 狼狗出手 | 狼狗命中 | 昏迷 | 补刀次数 | 关闭态回合 | 关闭态玩家出手 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 12 | 6 | 5 | 75 | 15.0 | 4 | 0 | 2 | 2 | 11 | 7 |
| 2 | 7 | 5 | 4 | 69 | 17.2 | 5 | 1 | 2 | 1 | 8 | 5 |
| 3 | 13 | 6 | 4 | 82 | 20.5 | 3 | 0 | 2 | 3 | 10 | 6 |
| 4 | 4 | 4 | 4 | 72 | 18.0 | 3 | 0 | 2 | 1 | 7 | 4 |
| 5 | 10 | 8 | 6 | 89 | 14.8 | 7 | 0 | 2 | 1 | 4 | 4 |
| 6 | 6 | 3 | 3 | 63 | 21.0 | 3 | 0 | 0 | 0 | 8 | 4 |
| 7 | 8 | 4 | 4 | 81 | 20.2 | 3 | 0 | 2 | 1 | 12 | 8 |
| 8 | 10 | 5 | 4 | 70 | 17.5 | 5 | 1 | 2 | 1 | 9 | 4 |
| 9 | 5 | 4 | 4 | 81 | 20.2 | 3 | 0 | 2 | 1 | 15 | 7 |
| 10 | 7 | 4 | 4 | 70 | 17.5 | 4 | 1 | 2 | 1 | 8 | 5 |

- 开启态均值：`rounds_used` 8.20、玩家出手 4.90、每刀 15.3、狼狗出手 4.00、昏迷 1.80、击杀 10/10。
- 关闭态均值（同 seed）：`rounds_used` 9.20、玩家出手 5.40、每刀 12.6、狼狗出手 4.80。
- `attack_rounds`（旧脚本口径 = `max(玩家出手, 对手出手)`）均值 4.90 **不是回合数**；与 9.20 相比属于换指标，不能并列。
- 开启 vs 关闭：回合 −1.0（−11%）、玩家出手 −0.5、狼狗出手 −0.8、每刀 +21%。原因见 §10.11。

老虎 `kill` 10 场（玩家 vs 老虎，逐列归属）：

| seed | 回合 | 玩家出手 | 玩家伤害 | 老虎出手 | 老虎命中/伤害 | 玩家终态 hp/eff | 老虎终态 hp/eff |
|---|---|---|---|---|---|---|---|
| 201–210 | 4–9 | **0** | **0** | 4–9 | 4–9 全部命中，106–135 | 0 / 8–88，`unconcious` | 425 / 425，`alive` |

- 4–9 是**老虎的出手次数**；“被打昏”的是**玩家**，不是老虎。玩家 `eff_qi` 8–88 是玩家自己的剩余伤势。
- 玩家 0 伤害的原因不是打不动，而是**一次都没出手**：出手门 `random(老虎 cps×3)`，老虎 `cps=0` → `random(0)=0`，`0 > 玩家 cor 24` 恒不成立（`combatd.lpc:763`；与 `reports/xkx-ref/combat-tempo.spec.md` §0 的原版结论同构）。次因是老虎 `apply/armor=1000`，即便出手也会被吃光。
- 老虎 `apply/attack=20000` 且 55/55 全中（原版实测），玩家拿不到 `damage<1` 的反击窗口，所以全程只能挨打。

店小二 `fight` 20 场：回合 6–9（均值 7.55）、玩家出手 6–8、`qi` 80–100、`eff_qi` 163–189（max=200）半血停手 20/20；未受伤者 `eff_hp=-2`（不输出）。

基线对比：原版狼狗样本是“狼狗出手 7–24 次、玩家出手 7–19 次（全部来自反击）”（`reports/xkx-ref/combat-tempo.spec.md` §0）。我们开启态是狼狗 4.00、玩家 4.90、回合 8.20。差值来自 50 血下的击杀刀数（每刀 15.3，3–4 刀即倒），`d-c-s4-final.md:13` 已记录“同一单位 200→50 血使 attack_rounds 15.6→5.4（≈3.0×）”，因此该组数字只能与 50 血的原版样本对齐，不能与 200 血样本比。

### 10.6 多目标回归（8031 独立实例 `xkx-eff-live`，`eff_qi: true`）

- `targets` 6/6 次均为 `[player:effqa, player:effqb]`；`opponent_index` 14 次采样中 11 次为 0（78.6%，≈75/25）
- 敌人表 `["player:effqb","player:effqa"]`（含两人）
- 昏迷落库 `seq136 tick140 /d/hangzhou/npc/wolfdog revive_tick=177`；昏迷期间补刀 1 次命中
- 昏迷事件清除 NPC 侧 `combat_target`（`set_field` 置空）
- 死亡事件 `entity_status` 携带 `remove_all_enemy: true`；双向清空由单测覆盖玩家侧（活体事件流只记录 NPC 侧 `set_field`）

### 10.7 0 血不死 / 死亡与还阳（真实 HTTP）

- B：`effqc` 于 `seq166 tick213` 被打到 `hp=-1`、`eff_qi=57`，`status=unconcious`、`revive_tick=253`；昏迷期间无死亡事件；`tick253` 自动回到 `alive`；苏醒后连续移动 10 步到郭庄（看到二只大狼狗），可继续活动。
- C：`effqd` 累计伤势至 `eff_qi=9`，`seq20300` 老虎打到 `hp=1`；`seq20301` 李拙道人 sword riposte 43 伤害 `wounded=true` → `eff_hp_set -1`、`entity_status status=dead`、`remove_all_enemy=true`、`location=/d/death/gate`、`player_presence=custody`、`player_death`、`unequip`、掉落 `shuzhi/coarse_cloth/flatbread`。
- 死亡惩罚 `death_log`：`combat_exp 6401→6273`（loss 128 = 6401/50）、`potential 99→50`（loss 49）、`skills_lost ["dodge","parry","unarmed"]`。
- `revive` HTTP 成功：“你从鬼门关还阳，回到楼外楼。（累计死亡 1 次）”，位置 `/d/hangzhou/jiulou`，`hp -1→100`（`eff_qi` 不变，按常规恢复）。
- 附注：死亡态下文字壳的 `look` 会被进入检查拒绝为“你已经在线，无需重复进入”，`revive` 不受影响；属 presence/文字壳既有问题，不在本期修改。

### 10.8 已知偏差

- `jing/eff_jing` 不建模（§8）。
- 无 `receive_curing` 对应命令：本期无疗伤手段（§3.4）。
- 玩家侧 `combat_enemies` 清空为内存态、不落事件（原版 `remove_all_enemy` 同样不是事件）；活体证据为敌人表与 NPC 侧 `set_field`，玩家侧由单测覆盖。

### 10.9 开启后同步的测试（`config/worlds/xkx.yaml` 翻为 `true` 时）

1. `tests/test_xkx_api.py::test_fight_reports_real_rounds_and_death`：首击伤害字面量 15→19。原因是开启后每场战斗开头两方各抽一次 `heal_cd`（`combat_rules.py:433`），骰流整体前移 2；属开启态预期，不是伤害公式变化。
2. `tests/test_xkx_growth.py::test_combat_death_and_revive_real_session`：`hp<0 && eff_qi>=0` 只是昏迷，`die()` 只认 `eff_qi<0`；测试先把玩家置成伤势 5 点，一场即跨过 0，继续验证死亡惩罚、尸体与还阳。断言数值未变（6272 / 39 级）。
3. `tests/test_w1_concurrency.py`：原断言用“两边关系数值不等”证明未串档，但对话增量有每 tick/每日上限，两人同时打满会撞值（全量跑中偶发失败）；改为按事件归因——每条 `relation_upsert` 的 `a` 必须等于发起对话的 actor、`b` 为同一 NPC，且至少 40 条。
4. `tests/test_xkx_growth.py::test_player_query_cps_cor_have_real_values`（新增）：锁死玩家 `cps=23 / cor=24` 与 `apply/composure`、`apply/courage` 的叠加。

### 10.10 cps / cor 链路（“cps=0”口径澄清）

实测（`/tmp/tempo_diag.py` §0，MID 游侠 6400 经验、技能 40）：

| 对象 | `query("cps")` | `apply/composure` | `query_cps()` | `query("cor")` | `query_cor()` |
|---|---|---|---|---|---|
| 玩家（创世后） | **23** | 0 | **23** | **24** | **24** |
| 大狼狗 | 0（未写） | 0 | 0 | 30 | 30 |
| 老虎 | 0（未写） | 0 | 0 | 0 | 0 |
| 店小二 | 17 | 0 | 17 | 22 | 22 |

- 原版 `race/human.lpc:60-68` 的 cps/cor 是登录掷点 `10+random(21)`；复刻不可复现掷点，按 MID 快照写死 23/24（与六维定值同一做法），创世即写入 `attrs`。**旧存档玩家**没有这两个键，战斗层仍按 23/24 兜底（`PLAYER_MID_CPS/COR`）。
- 上一版 §10.8 写的“玩家 cps=0”是错的：0 属于**走兽**（`race/beast.lpc` 不掷 cps/cor）。该句已删除，责任在我把对象写混了，不是取值链路有两个版本。
- `query_cps()` 只是 `query("cps") + apply/composure`；本实现没有 `apply/composure` 来源（无装备/无 buff），所以两者相等。
- 老虎 0 伤害与 cps 无关（见 §10.5 的三条归属）。
- 影响面：只影响**新建角色**的状态字典（`attrs` 多 `cps/cor` 两个键）；旧世界 `735 / 009076f0… / 02d5f812…` 与 p3e golden（无玩家）实测不变。

### 10.11 节奏变化因果链（9.20 → 4.90）

1. **先纠口径**：4.90 是旧脚本 `attack_rounds = max(玩家出手, 对手出手)` 的均值，即玩家出手次数，不是回合数。同指标的回合数开启态是 8.20；9.20 是**关闭态回合数**。同 seed 同指令对照：回合 9.20→8.20（−1.0），玩家出手 5.40→4.90，狼狗出手 4.80→4.00，每刀 12.6→15.3。
2. **差的来源**：开启后每场战斗开头两方各抽一次 `heal_cd`（`combat_rules.py:433`），骰流从第 1 回合起前移 2，命中/伤害序列整体换一套（每刀 +21% 是抽样变化，公式库 diff 为空）；同时 `combat_rules.py:737-740` 昏迷即结束本场，比关闭态少一次同回合对手行动，回合 −1。
3. **不是重复结算**：`qi` 与 `eff_qi` 是原版两条独立通道——`combatd.lpc:560` 先 `receive_damage` 扣 `qi`，命中 wound 再 `receive_wound("qi", damage-armor)` 扣 `eff_qi`，并把 `qi` 钳到 `eff_qi` 之下（`damage.lpc:56-64`）。seed 1 实测：玩家 4 刀合计 56，狼狗 `hp 50→-1`（报告层显示 0）、`eff_qi 50→2`，两条通道各自与事件一一对应；`qi` 没有被扣两次，`eff_qi` 也不参与吸收气血（只作上限）。

