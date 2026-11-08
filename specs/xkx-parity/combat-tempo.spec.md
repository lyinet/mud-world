# P6：战斗节奏 / 出手权——原版取证与修复设计（**未实现，待评审**）

> 状态：只做了源码取证与差异分析。**本文不包含任何代码改动**；
> `tools/xkx_formula/` 与 `src/wsim/sim/combat_rules.py` 一行未动。
> 触发样本：原版 4004，MID 档玩家 vs 杭州山路老虎，10 场；
> 复刻同口径 10 场（`reports/xkx-ref/win-loss.md`）。
>
> 先纠正一条口径：报告里的 **5.50 回合**是「平均每场老虎出手 5.5 次」，
> 不是「5.5 个双方各出手一次的交换回合」；**81 回合**来自临时
> `player_start` 验收账号（`2,000,000 exp / skills 500`），不是 MID，也不是默认档。
> 详见 §2.3 与 `reports/xkx-ref/win-loss.md` 的 `## corrections`。

## 0. 结论先行

1. 玩家不是“没轮到”。每个 `heart_beat` 都轮到了，但每次都被
   `combatd.lpc::fight()` 的 **guard 分支**判定为不出手，而且是**确定性**的。
2. 原因：`combatd.lpc:763` 的出手判定是
   `random(victim->query_cps() * 3) > me->query_cor()`。
   老虎 race 是 `走兽` → `beast.lpc` 只掷 `str/int/per/con/dex/kar`，
   **不设 `cps`/`cor`**（`beast.lpc:60-67`），所以 `cps = 0`；
   `random(0) = 0`，`0 > 玩家 cor(≥10)` 恒假 → 玩家永远进 guard 分支。
3. 老虎打玩家走的是另一支：玩家 `cps = 10 + random(21)`（`human.lpc:67`），
   老虎 `cor = 0`，`random(player_cps*3) > 0` 几乎恒真 → 老虎几乎每个
   `heart_beat` 都出手。这就是“0 次 vs 55 次”的非对称来源。
4. **没有“被打中取消出手”**。`is_busy()` 只由主动动作设置
   （`feature/action.lpc:8/28`），战斗中挨打不会置 busy；`combatd` 里也没有
   stun/continue 分支取消攻击方的出手。唯一与“挨打”相关的是
   “防守方 busy 时被快攻、dp/pp 除以 3”（`combatd.lpc:343/395/742`）。
5. 5.50 与 55 的计数口径：报告取 `rounds = max(我方出手, 对方出手)`
   （`tools/xkx_ref/measure_win_loss.py:95`）。玩家 0 出手时，
   `rounds` 就等于老虎出手数。**55 是 10 场合计，5.50 是场均值**，
   不是“5.5 回合里每回合 10 次”。
6. ap/dp 没有映射错误：老虎 `apply/attack = 20000` 经 `skill_power()`
   封顶到 `level = 800`（`combatd.lpc:226`）后，ap ≈ 2.1×10^7；
   MID dp 只有 10^3–10^4 量级，闪避率约 0.02%，**55/55 全中是公式的必然结果**。
   复刻同一公式下老虎也是 100% 命中，不存在“老虎被闪避/招架”。

## 1. 原版源码事实

源码根：`tools/xkx_ref/src/xkx100/`（采样实例 `run/mudlib` 为同一份 `.c` 副本，
行号一致）。

### 1.1 出手权从哪里来

- `inherit/char/char.lpc:57-96`：每个 living 的 `heart_beat()` 末尾调用 `attack()`；
  `set_heart_beat(1)` 在 `feature/attack.lpc:50`（`fight_ob` 内）打开。
- `feature/attack.lpc:228-236`：`attack()` 在 `is_fighting()` 时调用
  `COMBAT_D->fight(this_object(), opponent)`。
  **一次 `heart_beat` = 一次 `fight()` = 通常一次 `do_attack()`**
  （互搏空手且 `query("double_attack")` 时追加一次，`combatd.lpc:752/772`）。
- `adm/daemons/combatd.lpc:728-801`：`fight()` 的三分支：

  ```lpc
  // L742 防守方 busy：直接快攻
  if (victim->is_busy())
    do_attack(me, victim, me->query_temp("weapon"), TYPE_QUICK);
  // L763 防守方不忙：先掷 cps/cor
  else if (random((int)victim->query_cps() * 3) > (int)me->query_cor())
    do_attack(me, victim, me->query_temp("weapon"), TYPE_REGULAR);
  // L783 掷输：进入 guard，只提示一次，然后 return
  else if (!me->query_temp("guarding")) {
    me->set_temp("guarding", 1);
    tell_object(me, guard_msg);
    return;
  }
  // L800 已在 guarding：本 heartbeat 什么都不做
  ```

  没有 `dex` / `combat_speed` / `jiali` 的全局“先手排序”；
  双方各自按自己的 `heart_beat` 独立掷点。`double_attack`
  （`combatd.lpc:752/772`）只在 `query("double_attack")` 且空手时追加一次，
  MID 玩家和老虎都没有。

### 1.2 被打中是否取消出手

- `feature/action.lpc:28`：`nomask int is_busy() { return busy != 0; }`；
  `start_busy()`（同文件 L8）只被主动动作（打坐、吃饭、某些技能）调用，
  **挨打不会置 busy**。
- `combatd.lpc:343`（`dp /= 3`）、`395`（`pp /= 3`）只在
  `victim->is_busy()` 时生效；`combatd.lpc:742` 同理（快攻）。
- `combatd.lpc:664` 的 `interrupt_me()` 打断的是**非战斗动作**，不是出手。
- 结论：玩家 0 出手不是“被打断/被打晕”，而是 §1.1 的 guard 分支。
  19 次老虎出手仍未还手的那场（`reports/xkx-ref/win-loss/tiger_hz/fight006.txt`）
  可以排除“运气/出手太少”的解释——它是确定性的。

### 1.3 一回合几次攻击 / 计数口径

- 原版每 `heart_beat` 一次 `fight()`，每次至多一刀（无双击时）。
  不存在“一回合 10 次”。
- `tools/xkx_ref/measure_win_loss.py:95`：

  ```python
  "rounds": max(p["attacks"], n["attacks"]),
  ```

  老虎样本里 `player_attacks = 0`，所以 `rounds` ≡ `npc_attacks`。
  `tiger_hz/meta.json` 的 10 场是 5/4/2/4/4/19/4/2/7/4，合计 55，
  均值 5.50。**55 是合计，5.50 是场均；不是每回合 10 次。**
- 复刻的 `rounds_used` 是“双方各获得一次行动机会”的交换轮数
  （`combat_rules.py:402-568`），与原版 `max(attacks)` **不是同一单位**。
  两者只能比量级，不能直接相减。

### 1.4 伤害量级

- 老虎空手：`apply/unarmed_damage` 未设 → 0（`combatd.lpc:436-439`），
  `action["damage"]` 亦为 0，护甲抵消 `defense_factor` 为 0（L451-454），
  真正的伤害来自 `damage_bonus = me->query_str()`（L464）；
  L509-513 再把它折成 `(x + random(x)) / 2` 后加进伤害。
  老虎 `str = random(40) + 5`（`beast.lpc:62`），即 5–44，
  单刀期望约为 `0.75 × str ≈ 4–33`。
- 经验修正（L524-528）是 `while (random(defense_factor) > my["combat_exp"])`，
  `defense_factor` 是挨打方的 `combat_exp`。MID 6400、老虎 30000：
  `random(6400)` 永远 `< 30000`，循环不执行，**不减免**。
- 原版实测：MID 被打死用 **2–19 刀**。`fight006.txt` 的状态带
  （“动作似乎开始有点不太灵光”→“气喘嘘嘘”→“十分疲惫”→“头重脚轻”
  →“力不从心”→“站都站不稳”→死）约每刀掉 5% 气血，
  反推 `max_qi` 在 100–200 量级，单刀 5–50（轻/重伤描述浮动）。
  精确 `max_qi` 未在战报里输出，记为未记录。
- 复刻：老虎每刀 5–9，MID `max_hp = 100`，老虎 100% 命中，
  实测 7.40 轮（`reports/xkx-ref/player-mid-tier.spec.md` §4；
  `specs/xkx-parity/combat-implementation.md` 另有一组 15 轮记录，取决于种子集）。
  与原版 2–19 刀同一量级，**不需要改伤害**。

### 1.5 `random(ap+dp)` 的真实表现

- `combatd.lpc:197-241` `skill_power()`：

  ```lpc
  level = ob->query_skill(skill);
  if (usage == SKILL_USAGE_ATTACK) level += ob->query_temp("apply/attack");
  if (level > 800) level = 800;          // L226
  power = (level / 6) * level * level;   // L236
  return (power + exp) / 80 * (str + ob->query("str"));   // L239
  ```

- 老虎（`apply/attack = 20000`）：`level` 0+20000 → 封顶 800，
  `power = 133*800*800 = 85,120,000`，`(power+30000)/80 = 1,064,375`，
  按 `str = 10` 取 `ap ≈ 21,287,500`。
- MID 玩家（`dodge 40`、`apply/defense 0`）：`power = 6*1600 = 9,600`，
  `(9600+6400)/80 = 200`，`dp = 200 × (dex+dex)`，`dex = 10~20` →
  **dp ≈ 4,000–8,000**。
- 闪避率 = `dp / (ap + dp) ≈ 0.019%–0.038%`；招架同量级。
  **55/55 全中是必然**，不是“原版 dp 算小了”。
- 复刻复用了同一个 `skill_power`（`tools/xkx_formula/combat.py`，
  公式库零改动），老虎同样 100% 命中（`combat-implementation.md` §14）。

## 2. 复刻现状与差异清单

| # | 项 | 原版 | 复刻 `22ae271` | 影响 |
|---|---|---|---|---|
| 1 | 出手决策 | 每 `heart_beat` 独立掷 `cps/cor`，输者 guard | `duel()` 每轮按 `dex+dodge` 先手，双方各出手一次（`combat_rules.py:555-566`） | 玩家出手次数不同 |
| 2 | 走兽属性默认 | `beast.lpc` 不设 cps/cor → 0；玩家永远 guard | 内容缺省 attrs 按 10/10；没有 cps/cor 概念 | 机制缺失的根因 |
| 3 | 回合口径 | `rounds = max(attacks)`；玩家 0 时等于 NPC 出手 | `rounds_used` = 双方交换轮数 | 5.50 vs 7.40 只能比量级；81 是另一账号 |
| 4 | 先手 | 无全局先手，各按自身 heartbeat | `dex+dodge` 先手，双方每轮都动 | 结构不同，日志/PRNG 序列不同 |
| 5 | busy | 挨打不置 busy；busy 时被快攻且 dp/pp ÷3 | `VICTIM_BUSY = False`（L49） | 本对拍不触发；属未建模 |
| 6 | ap/dp/伤害 | `skill_power` + str 加成 + armor/exp 修正 | 同一公式库 | **无差异**（原版 2–19 刀 vs 复刻 7.40） |
| 7 | 双击/多段 | `double_attack` 仅空手且高效 | 未建模 | 老虎/MID 都没有，不影响本对拍 |

结论：**胜负结果忠实，节奏机制不忠实**。
“打不动”的结论仍然成立；但“为什么打不动”在原版是**不允许出手**，
在复刻是**允许出手但打不中**。这是机制差异，不是数值差异。

## 3. 修复设计（分步、可回滚，尚未实现）

### S0 口径对齐（纯测量，零行为变化）
- 把 `measure_win_loss.py` 的 `rounds` 明确标注为
  `attack_rounds = max(attacks)`（原版只能观测到出手数，没有真正的交换计数器）；
  复刻侧同时输出 `rounds_used`（交换轮数）。报告分两列展示，
  **不把 5.50 与 7.40 直接相减**。
- 回滚：纯工具/文档改动。

### S1 cps/cor 出手门（核心，行为改动）
- 在 `combat_rules.simulate_fight()` 的每轮，对每个 living 独立执行原版三分支：
  1. `victim.busy` → 必出手（dp/pp ÷3 已有公式支持）；
  2. `random(victim.cps*3) > attacker.cor` → 出手；
  3. 否则本轮到该 living 为 guard（不出手，只在该段首次输出一次 guard 文案）。
- 去掉“双方每轮必各出手一次”的假设；同一轮内的行动顺序按实体 id 固定，
  保证 seed 可复现。
- `Entity` 增加 `cps`/`cor`：
  - `human.lpc:67-68`：`cps = cor = 10 + random(21)`（10..30）；
  - `beast1.lpc:102-103`、`beast2.lpc:84-85`：`cps = random(21)+5`（5..25）、
    `cor = random(41)+5`（5..45）；
  - **`beast.lpc`（走兽）不设 → 0**，这是老虎/狼狗样本的关键；
  - 未在内容里显式给出时，**按 race 取默认，不统一取 10**。
- 开关：世界配置 `combat.initiative_gate`（默认 `false`）；
  xkx 世界开 `true`。关闭时走旧逻辑，回滚只需翻开关。
- 预期影响：
  - 走兽（老虎、狼狗、野兔、猴子…）：玩家普通出手 → **0**，与原版一致；
    若没有绕过 `fight()` 直接调用 `do_attack()` 的特殊技能，
    老虎/狼狗将**无法被玩家用普通攻击击杀**（原版就是如此）。
  - 人类 / beast1 / beast2：`cps` 非 0，门大概率通过，胜率变化需重测。
  - 此前“玩家击杀狼狗 → 重生”的闭环演练将无法用普通玩家复现；
    需要保留该回归时，用脚本伤害或测试专用入口，不要为了测试保留偏差。

### S2 busy 建模（可选，独立）
- 目前战斗动作不设置 busy，`VICTIM_BUSY` 恒 False 对本对拍无影响。
  后续若要建模打坐/吃饭等动作，再补 `busy` 状态；不在本修复范围。

### S3 回归验证
- 重跑 6 个既有对手 + 杭州老虎 10 场：
  - 老虎：`player_attacks = 0`，`attack_rounds ≈ 5–9`，0 胜 10 负；
  - 非走兽对手：胜负与 `rounds` 变化需逐项记录，不允许“顺手调数值”；
  - `tools/xkx_formula/` diff 为空；
  - 门关闭时，旧世界的事件/状态哈希必须逐字节一致。

## 4. 不做的事
- 不改 `tools/xkx_formula/` 任何系数、不改 `apply/attack`/`armor`/伤害常量；
- 不因为“81 回合打不死”调低老虎防御；
- 不把 `p5-tiger`（临时 `2,000,000 exp / skills 500`）的任何数字当作默认档结论。
