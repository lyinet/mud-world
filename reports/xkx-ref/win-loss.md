# 胜负对拍：原版 MID 档 vs 复刻同口径

- 采样构建：`c514f4c`×62、`efdbd5a`×29、`e164708`×18、`22ae271`×10、`fce2519`×1（采样期间另一条线在 `src/wsim` 上连续提交，仓库 HEAD 前进了几次；**原版侧只依赖 `run/mudlib` + 驱动，二者未变**，故不影响原版数字。每场的具体 sha 记录在各 `meta.json`）
- MID 快照：`run/snapshots/xkxmid.o` md5 `69fd5711e7b288bfc5fdaa11f8e8f513`（exp 6400、unarmed/dodge/parry 40/40/40、无兵器；**用存档数据模拟 40 级，不是正常成长路径；LPC 一行未改**）
- 复刻对照：`xkx-parity` 上 `b928322` 之后，`WorldSession` 真实动作（`enter` → `fight`），seed 1–40（老虎 1–10），见 `specs/xkx-parity/player-mid-tier.spec.md` §4。

## corrections

> 口径更正记录。旧表述保留，不删；新事实以本节为准。

### C1（2026-11-08，`7b426cf` 报告线）

- **旧表述**：“默认档玩家打老虎，81 回合后玩家 0 血、虎剩 28——老虎打不死”。
- **新事实**：
  1. 试玩世界老板档（`/tmp/xkx-play` 的 `runs/xkx-world`）里没有任何打虎事件（`args like '%laohu%'` 为 0 条）；老板档唯一的老虎记录是 `seq910 tick650` 他自己被老虎第 21 回合打死。
  2. “81 回合、虎剩 28”来自临时验收实例 `p5-tiger`（`player_start: combat_exp 2,000,000 / skills 500`），及 `xkx-p5-http/tiger-root`；**不是默认档，也不是 MID 档**。
  3. 本表“复刻”列的 7.40 才是 MID 同口径；原版 5.50 是“平均每场老虎出手数”（`rounds = max(attacks)`，玩家 0 出手），两者不是同一单位。
- **影响范围**：仅影响“节奏/回合”这类表述与 p5-tiger 样本；老虎 0 胜的胜负结论、以及所有其它对手的原版采样不受影响。
  节奏机制的真实差异另见 `specs/xkx-parity/combat-tempo.spec.md`：原版玩家是 **0 出手（guard 分支）**，复刻是 **每轮挥击但 0 命中**，胜负一致不等于行为一致。

### 样本来源与临时配置

| 样本 | 实例 / run | `player_start` | 用途 | 能否当默认档结论 |
|---|---|---|---|---|
| MID 原版对拍 | 4010 采样实例，`run/snapshots/xkxmid.o` | exp 6400、unarmed/dodge/parry 40/40/40 | 本表原版数据 | 是（MID 口径） |
| MID 复刻对拍 | 复刻 `xkx-parity`，seed 1–40 | 同 MID | 本表复刻数据 | 是（MID 口径） |
| p5-tiger | `xkx-p5-http/tiger-root`（18072） | **临时覆盖 2,000,000 exp / skills 500** | P5 验收、81 回合样本 | **否** |
| p5peek | `/tmp/xkx-play` 试玩世界 | 默认 | 只读 look / 触发 reset | 否（观察账号） |

## 1. 口径（两侧必须对齐才可比）

- 都是 **1v1**：原版 `kill <id>` 只打一只；复刻 `fight {"target": npc_id}` 只取一只实例。道路房里的同族 NPC 不参战（原版 NPC 未写 `init()` 主动攻击）——这一点有实测校验：绿营清兵房内有 4 只 clone，但本组每场对方出手数与我方出手数同量级（如 5 对 5），不是 4 倍；若 4 只同时参战，出手会比会接近 4:1。
- 原版**没有回合上限也没有时限**：`combatd` 的战斗只会因一方死亡/气血耗尽或逃跑而终止。本文不设平局；若 90 秒内未分胜负，记 `timeout` 单列（本次未出现）。
- 回合数：原版无可直接读取的计数器，取 `max(我方出手次数, 对方出手次数)`；复刻为 `combat["rounds_used"]`（双方各获得一次行动机会的交换轮数）。**两者不是同一单位**：玩家 0 出手时，原版 `rounds` 就等于对方出手数（老虎样本的 5.50 = 平均每场老虎出手 5.5 次，不是 5.5 个双方交换回合）。跨版本只比量级，不直接相减。
- 剩余气血：胜场战后 `hp` 实读，记**当前值/上限**，表中给当前值；负场记 0（原版死亡后角色已在鬼门关重生，原角色临终气血**取不到**，故按约定记 0，不是实测 0）。战后上限可能因受伤临时低于满值（如“66/83”），表中只列当前值。
- 对方命中率 = 对方 `result==hit` 的出手 / 对方出手（`hit+dodge+parry`），与 `reports/xkx-ref/hit-rate.md` 同一解析器。

## 2. 结果对照

| 对手 | 原版 胜/负/逃/超时 | 原版 平均回合 | 原版 剩余气血(中位/最低/最高) | 原版 对方命中率 | 复刻 胜/负 | 复刻 平均回合 | 复刻 剩余气血 | 复刻 对方命中率 |
|---|---|---|---|---|---|---|---|---|
| 店小二 | 20/0/0/0 | 17.00 | 100/100/100 | 0/251 = 0.00% [0.00%, 1.51%] | 40/0 | 12.40 | 100 | 0.00% |
| 流氓 | 20/0/0/0 | 22.25 | 100/78/100 | 13/359 = 3.62% [2.13%, 6.10%] | 40/0 | 28.23 | 86.5 | 5.60% |
| 小沙弥 | 19/1/0/0 | 36.45 | 72/21/100 | 84/666 = 12.61% [10.30%, 15.35%] | 39/1 | 25.00 | 59.0 | 15.50% |
| 刀客 | 0/20/0/0 | 6.20 | 0（全负，取不到临终值） | 77/120 = 64.17% [55.27%, 72.18%] | 0/40 | 5.47 | 0 | 68.04% |
| 绿营清兵 | 0/20/0/0 | 4.45 | 0（全负，取不到临终值） | 70/89 = 78.65% [69.05%, 85.89%] | 0/40 | 4.53 | 0 | 70.17% |
| 老虎 | 0/10/0/0 | 9.30 | 0（全负，取不到临终值） | 93/93 = 100.00% [96.03%, 100.00%] | 0/10 | 7.40 | 0 | 100.00% |
| 老虎(杭州山路) | 0/10/0/0 | 5.50 | 0（全负，取不到临终值） | 55/55 = 100.00% [93.47%, 100.00%] | 0/10 | 7.40 | 0 | 100.00% |

## 3. 判据

判据：如果原版 MID 也打不过刀客/清兵，我们就是忠实的；如果原版能赢，说明我们还缺东西。

- **店小二**：两侧都以胜为主（原版 20 胜/0 负，复刻 40 胜/0 负）——**忠实**。
- **流氓**：两侧都以胜为主（原版 20 胜/0 负，复刻 40 胜/0 负）——**忠实**。
- **小沙弥**：两侧都以胜为主（原版 19 胜/1 负，复刻 39 胜/1 负）——**忠实**。
- **刀客**：两侧都打不过（原版 0 胜/20 负，复刻 0 胜/40 负）——**胜负忠实**。
- **绿营清兵**：两侧都打不过（原版 0 胜/20 负，复刻 0 胜/40 负）——**胜负忠实**。
- **老虎**：两侧都打不过（原版 0 胜/10 负，复刻 0 胜/10 负）——**胜负忠实**；**节奏未对齐**，见 §3 专项与 `specs/xkx-parity/combat-tempo.spec.md`。
- **老虎(杭州山路)**：两侧都打不过（原版 0 胜/10 负，复刻 0 胜/10 负）——**胜负忠实**；**节奏未对齐**，见 §3 专项与 `specs/xkx-parity/combat-tempo.spec.md`。

**结论：没有出现“原版能赢、复刻全负”的对手**，因此胜负这一维度上复刻是忠实的，不需要靠补快攻/连招/兵器/内力去救胜率。

- **杭州山路老虎专项**：`d/hangzhou/npc/laohu.lpc` 与武当虎头岩老虎同源同数值（exp 30000、`apply/attack` 20000、`apply/defense` 30000、`apply/armor` 1000）；原版 MID 新采 10 场 0 胜 10 负、平均 5.50 回合、老虎 55/55 次出手全部命中，玩家 0 次出手。**胜负上原版同样打不动 → 不改伤害/护甲数值**。但节奏机制并不忠实：原版玩家 0 出手来自 `combatd.lpc:763` 的 cps/cor guard 分支（走兽 `beast.lpc` 不设 cps/cor → 0），复刻则是每轮挥击但 0 命中；详见 `specs/xkx-parity/combat-tempo.spec.md`。**修复前不把节奏判为忠实。**

## 4. 共同败因（两侧同向，不等于已对齐）

下面区分**源码可观察**与**推断**：

- 源码可观察：`d/hangzhou/npc/dao-ke.c` 经验 15000、`blade` 45 / `parry` 40 / `dodge` 40，持钢刀；`d/hangzhou/npc/qingbing.c` 经验 20000、`club` 60 / `parry` 50 / `dodge` 50，持长枪，另有 `set("apply/attack", 30)` / `set("apply/defense", 30)`。
- 源码可观察：MID 玩家经验 6400、空手 40 / 闪避 40 / 招架 40、无兵器；对比之下对手的经验与兵器技能都高出 2–3 倍。
- 源码可观察：`d/hangzhou/npc/laohu.lpc` 与虎头岩老虎同源：经验 30000、`apply/attack` 20000、`apply/defense` 30000、`apply/armor` 1000、`jiali` 100；MID 档的 40 级空手/闪避/招架在数值上无法破防。
- 推断（未逐项验证）：玩家打不动对手、又被高伤反击，是“经验+兵器技能差”造成的；原版 4.45–6.20 回合即结束，说明这不是我们的伤害换算偏慢，而是角色档位本身压制。
- 节奏差异（同阶、非胜负向）：小沙弥原版 36.45 回合 vs 复刻 25.00；流氓原版 22.25 vs 复刻 28.23。若下一步要逼近，优先调节奏，而不是胜率。

## 5. 局限

- 原版样本量：店小二/流氓/小沙弥/刀客/绿营清兵各 20 场，老虎（虎头岩/杭州山路）各 10 场（复刻为 40/40/40/40/40/10；杭州山路沿用同数值老虎的复刻 0/10 口径）。0 胜的 20 场样本 95% 置信上界约 14%（Clopper-Pearson），不能排除低概率胜。
- 回合数口径不完全等价（见 §1）；两侧平均回合只作量级对照。
- 负场的临终气血**取不到**（角色已在鬼门关重生），表中记 0 是按约定，不是实测。
- 原版采样期间仓库 HEAD 在 `src/wsim` 上前进，但原版侧只依赖 `run/mudlib` + 驱动，未受影响。

## 6. 逐场明细

### 店小二（20 场，20 胜 0 负 0 逃 0 超时）

| # | 结果 | 回合 | 我方出手/命中 | 对方出手/命中 | 战后气血 | 原始日志 |
|---|---|---|---|---|---|---|
| 1 | win | 16 | 16/16 | 13/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight001.txt` |
| 2 | win | 18 | 18/17 | 13/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight002.txt` |
| 3 | win | 17 | 17/16 | 10/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight003.txt` |
| 4 | win | 14 | 14/14 | 10/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight004.txt` |
| 5 | win | 19 | 19/19 | 13/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight005.txt` |
| 6 | win | 17 | 17/16 | 12/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight006.txt` |
| 7 | win | 13 | 13/13 | 10/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight007.txt` |
| 8 | win | 15 | 15/15 | 12/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight008.txt` |
| 9 | win | 20 | 20/19 | 18/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight009.txt` |
| 10 | win | 19 | 19/19 | 17/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight010.txt` |
| 11 | win | 16 | 16/14 | 12/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight011.txt` |
| 12 | win | 14 | 14/14 | 8/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight012.txt` |
| 13 | win | 19 | 19/19 | 14/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight013.txt` |
| 14 | win | 23 | 23/22 | 16/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight014.txt` |
| 15 | win | 13 | 13/12 | 10/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight015.txt` |
| 16 | win | 19 | 19/19 | 14/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight016.txt` |
| 17 | win | 11 | 11/11 | 7/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight017.txt` |
| 18 | win | 22 | 22/21 | 17/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight018.txt` |
| 19 | win | 17 | 17/16 | 12/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight019.txt` |
| 20 | win | 18 | 18/17 | 13/0 | 100/100 | `reports/xkx-ref/win-loss/xiaoer/fight020.txt` |

### 流氓（20 场，20 胜 0 负 0 逃 0 超时）

| # | 结果 | 回合 | 我方出手/命中 | 对方出手/命中 | 战后气血 | 原始日志 |
|---|---|---|---|---|---|---|
| 1 | win | 23 | 23/15 | 20/1 | 94/95 | `reports/xkx-ref/win-loss/liumang/fight001.txt` |
| 2 | win | 22 | 22/14 | 18/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight002.txt` |
| 3 | win | 18 | 18/11 | 16/1 | 95/100 | `reports/xkx-ref/win-loss/liumang/fight003.txt` |
| 4 | win | 25 | 25/16 | 23/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight004.txt` |
| 5 | win | 22 | 22/14 | 19/2 | 78/100 | `reports/xkx-ref/win-loss/liumang/fight005.txt` |
| 6 | win | 16 | 16/14 | 15/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight006.txt` |
| 7 | win | 19 | 19/13 | 16/1 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight007.txt` |
| 8 | win | 23 | 23/14 | 21/1 | 92/100 | `reports/xkx-ref/win-loss/liumang/fight008.txt` |
| 9 | win | 22 | 22/12 | 16/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight009.txt` |
| 10 | win | 29 | 29/18 | 24/1 | 90/91 | `reports/xkx-ref/win-loss/liumang/fight010.txt` |
| 11 | win | 17 | 17/12 | 10/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight011.txt` |
| 12 | win | 18 | 18/11 | 12/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight012.txt` |
| 13 | win | 27 | 27/17 | 24/1 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight013.txt` |
| 14 | win | 21 | 21/13 | 18/2 | 98/100 | `reports/xkx-ref/win-loss/liumang/fight014.txt` |
| 15 | win | 26 | 26/16 | 22/1 | 96/100 | `reports/xkx-ref/win-loss/liumang/fight015.txt` |
| 16 | win | 21 | 21/13 | 14/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight016.txt` |
| 17 | win | 28 | 28/18 | 20/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight017.txt` |
| 18 | win | 19 | 19/13 | 15/0 | 100/100 | `reports/xkx-ref/win-loss/liumang/fight018.txt` |
| 19 | win | 24 | 24/16 | 18/1 | 96/100 | `reports/xkx-ref/win-loss/liumang/fight019.txt` |
| 20 | win | 25 | 25/17 | 18/1 | 96/100 | `reports/xkx-ref/win-loss/liumang/fight020.txt` |

### 小沙弥（20 场，19 胜 1 负 0 逃 0 超时）

| # | 结果 | 回合 | 我方出手/命中 | 对方出手/命中 | 战后气血 | 原始日志 |
|---|---|---|---|---|---|---|
| 1 | win | 16 | 16/10 | 13/1 | 85/100 | `reports/xkx-ref/win-loss/shami/fight001.txt` |
| 2 | win | 24 | 24/12 | 19/2 | 80/100 | `reports/xkx-ref/win-loss/shami/fight002.txt` |
| 3 | win | 17 | 17/11 | 12/1 | 85/100 | `reports/xkx-ref/win-loss/shami/fight003.txt` |
| 4 | win | 32 | 32/13 | 32/5 | 66/83 | `reports/xkx-ref/win-loss/shami/fight004.txt` |
| 5 | win | 20 | 20/12 | 14/1 | 100/100 | `reports/xkx-ref/win-loss/shami/fight005.txt` |
| 6 | win | 53 | 52/17 | 53/7 | 51/100 | `reports/xkx-ref/win-loss/shami/fight006.txt` |
| 7 | win | 36 | 36/15 | 30/3 | 84/100 | `reports/xkx-ref/win-loss/shami/fight007.txt` |
| 8 | loss | 56 | 56/14 | 49/11 | 0/0 | `reports/xkx-ref/win-loss/shami/fight008.txt` |
| 9 | win | 35 | 35/18 | 29/6 | 50/87 | `reports/xkx-ref/win-loss/shami/fight009.txt` |
| 10 | win | 39 | 39/13 | 35/4 | 72/92 | `reports/xkx-ref/win-loss/shami/fight010.txt` |
| 11 | win | 61 | 58/18 | 61/11 | 21/57 | `reports/xkx-ref/win-loss/shami/fight011.txt` |
| 12 | win | 54 | 54/16 | 49/6 | 52/90 | `reports/xkx-ref/win-loss/shami/fight012.txt` |
| 13 | win | 36 | 36/15 | 36/3 | 81/85 | `reports/xkx-ref/win-loss/shami/fight013.txt` |
| 14 | win | 20 | 20/13 | 14/1 | 98/100 | `reports/xkx-ref/win-loss/shami/fight014.txt` |
| 15 | win | 44 | 40/15 | 44/5 | 54/69 | `reports/xkx-ref/win-loss/shami/fight015.txt` |
| 16 | win | 25 | 25/14 | 23/3 | 84/90 | `reports/xkx-ref/win-loss/shami/fight016.txt` |
| 17 | win | 49 | 49/17 | 45/4 | 60/73 | `reports/xkx-ref/win-loss/shami/fight017.txt` |
| 18 | win | 21 | 21/13 | 18/0 | 100/100 | `reports/xkx-ref/win-loss/shami/fight018.txt` |
| 19 | win | 47 | 47/14 | 46/4 | 67/89 | `reports/xkx-ref/win-loss/shami/fight019.txt` |
| 20 | win | 44 | 40/14 | 44/6 | 63/76 | `reports/xkx-ref/win-loss/shami/fight020.txt` |

### 刀客（20 场，0 胜 20 负 0 逃 0 超时）

| # | 结果 | 回合 | 我方出手/命中 | 对方出手/命中 | 战后气血 | 原始日志 |
|---|---|---|---|---|---|---|
| 1 | loss | 9 | 9/1 | 9/5 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight001.txt` |
| 2 | loss | 6 | 5/0 | 6/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight002.txt` |
| 3 | loss | 3 | 2/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight003.txt` |
| 4 | loss | 7 | 7/1 | 4/3 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight004.txt` |
| 5 | loss | 6 | 4/0 | 6/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight005.txt` |
| 6 | loss | 4 | 1/0 | 4/3 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight006.txt` |
| 7 | loss | 8 | 7/1 | 8/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight007.txt` |
| 8 | loss | 8 | 6/1 | 8/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight008.txt` |
| 9 | loss | 7 | 5/2 | 7/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight009.txt` |
| 10 | loss | 5 | 5/0 | 5/3 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight010.txt` |
| 11 | loss | 7 | 4/0 | 7/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight011.txt` |
| 12 | loss | 5 | 5/1 | 4/3 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight012.txt` |
| 13 | loss | 4 | 3/1 | 4/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight013.txt` |
| 14 | loss | 8 | 7/1 | 8/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight014.txt` |
| 15 | loss | 8 | 8/0 | 8/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight015.txt` |
| 16 | loss | 6 | 5/1 | 6/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight016.txt` |
| 17 | loss | 6 | 6/0 | 6/4 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight017.txt` |
| 18 | loss | 7 | 4/0 | 7/5 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight018.txt` |
| 19 | loss | 3 | 3/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight019.txt` |
| 20 | loss | 7 | 7/0 | 7/5 | 0/0 | `reports/xkx-ref/win-loss/daoke/fight020.txt` |

### 绿营清兵（20 场，0 胜 20 负 0 逃 0 超时）

| # | 结果 | 回合 | 我方出手/命中 | 对方出手/命中 | 战后气血 | 原始日志 |
|---|---|---|---|---|---|---|
| 1 | loss | 5 | 5/0 | 5/5 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight001.txt` |
| 2 | loss | 4 | 1/0 | 4/4 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight002.txt` |
| 3 | loss | 6 | 5/0 | 6/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight003.txt` |
| 4 | loss | 5 | 5/1 | 5/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight004.txt` |
| 5 | loss | 4 | 1/0 | 4/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight005.txt` |
| 6 | loss | 3 | 3/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight006.txt` |
| 7 | loss | 6 | 4/0 | 6/4 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight007.txt` |
| 8 | loss | 5 | 4/0 | 5/4 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight008.txt` |
| 9 | loss | 6 | 4/0 | 6/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight009.txt` |
| 10 | loss | 5 | 2/0 | 5/4 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight010.txt` |
| 11 | loss | 4 | 3/0 | 4/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight011.txt` |
| 12 | loss | 4 | 2/1 | 4/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight012.txt` |
| 13 | loss | 3 | 2/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight013.txt` |
| 14 | loss | 3 | 3/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight014.txt` |
| 15 | loss | 3 | 2/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight015.txt` |
| 16 | loss | 3 | 1/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight016.txt` |
| 17 | loss | 4 | 4/0 | 4/4 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight017.txt` |
| 18 | loss | 6 | 2/0 | 6/3 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight018.txt` |
| 19 | loss | 5 | 2/0 | 5/5 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight019.txt` |
| 20 | loss | 5 | 4/0 | 5/4 | 0/0 | `reports/xkx-ref/win-loss/qingbing/fight020.txt` |

### 老虎（10 场，0 胜 10 负 0 逃 0 超时）

| # | 结果 | 回合 | 我方出手/命中 | 对方出手/命中 | 战后气血 | 原始日志 |
|---|---|---|---|---|---|---|
| 1 | loss | 15 | 0/0 | 15/15 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight001.txt` |
| 2 | loss | 6 | 0/0 | 6/6 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight002.txt` |
| 3 | loss | 3 | 0/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight003.txt` |
| 4 | loss | 3 | 0/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight004.txt` |
| 5 | loss | 24 | 0/0 | 24/24 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight005.txt` |
| 6 | loss | 6 | 0/0 | 6/6 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight006.txt` |
| 7 | loss | 3 | 0/0 | 3/3 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight007.txt` |
| 8 | loss | 11 | 0/0 | 11/11 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight008.txt` |
| 9 | loss | 5 | 0/0 | 5/5 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight009.txt` |
| 10 | loss | 17 | 0/0 | 17/17 | 0/0 | `reports/xkx-ref/win-loss/tiger/fight010.txt` |

### 老虎(杭州山路)（10 场，0 胜 10 负 0 逃 0 超时）

| # | 结果 | 回合 | 我方出手/命中 | 对方出手/命中 | 战后气血 | 原始日志 |
|---|---|---|---|---|---|---|
| 1 | loss | 5 | 0/0 | 5/5 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight001.txt` |
| 2 | loss | 4 | 0/0 | 4/4 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight002.txt` |
| 3 | loss | 2 | 0/0 | 2/2 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight003.txt` |
| 4 | loss | 4 | 0/0 | 4/4 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight004.txt` |
| 5 | loss | 4 | 0/0 | 4/4 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight005.txt` |
| 6 | loss | 19 | 0/0 | 19/19 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight006.txt` |
| 7 | loss | 4 | 0/0 | 4/4 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight007.txt` |
| 8 | loss | 2 | 0/0 | 2/2 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight008.txt` |
| 9 | loss | 7 | 0/0 | 7/7 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight009.txt` |
| 10 | loss | 4 | 0/0 | 4/4 | 0/0 | `reports/xkx-ref/win-loss/tiger_hz/fight010.txt` |

## 5. 复现命令

```bash
# 全量重采（会启停 4010 实例；每场独立重启并覆盖快照）
python3 tools/xkx_ref/measure_win_loss.py --target xiaoer --n 20 --port 4010
python3 tools/xkx_ref/measure_win_loss.py --target liumang --n 20 --port 4010
python3 tools/xkx_ref/measure_win_loss.py --target shami --n 20 --port 4010
python3 tools/xkx_ref/measure_win_loss.py --target daoke --n 20 --port 4010
python3 tools/xkx_ref/measure_win_loss.py --target qingbing --n 20 --port 4010
python3 tools/xkx_ref/measure_win_loss.py --target tiger --n 10 --port 4010
python3 tools/xkx_ref/measure_win_loss.py --target tiger_hz --n 10 --port 4010

# 只从已落盘原文重算本报告
python3 tools/xkx_ref/measure_win_loss.py --report-only
```

- 原始日志：`reports/xkx-ref/win-loss/<target>/fight*.txt`、`meta.json`（含每场 `build_sha`）。
- 采集器每次登录前重启 4010 并覆盖存档；跑完自动停实例，不占用 4004。
