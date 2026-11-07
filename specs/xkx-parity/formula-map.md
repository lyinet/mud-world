# xkx 战斗公式映射表

本文件是 `tools/xkx_formula/` 的来源台账：每个 Python 纯函数对应哪段 LPC、做了哪些
等价改写、随机取数顺序如何、哪些部分不能机械翻译。

## 0. 基线与范围

- LPC 源码：`fluffos/xkx100`，commit `c1b87db0014c85514e053b2a149a2efccb9914e3`
  （2026-10-14，`Merge pull request #9 from fluffos/fix/float-truncation`）。
- 源码只在仓库外 clone（`/tmp/xkx-research/fluffos-xkx100`），不入库。
- 实现范围：`tools/xkx_formula/**`；测试：`tests/test_xkx_formula_*.py`。
- 未触碰 `src/**`、`ui/**`、`content/**`、`config/**`。
- 测试期望值默认是“推导值”：按 LPC 源码逐行手算，不是真实 MUD 驱动上的实测值。
  测试用例里的 `basis` 字段与模块 docstring 都显式标注了这一点。
- 能从本机参照版（`127.0.0.1:4004`）对拍的项，另标“实测校准”，场景/样本量/偏差见
  第 5 节；推导依据全部保留，两者并存。

## 1. 公式总表

| # | LPC 公式 | Python 函数 | 源码位置 | 测试 |
|---|----------|-------------|----------|------|
| 1 | 攻击/防御战力 `skill_power()` | `combat.skill_power()` | `adm/daemons/combatd.lpc:197-242` | `test_skill_power_table` |
| 2 | 攻防上下限夹取 | `combat.clamp_power()` | `combatd.lpc:329-331`、`340-342`、`402-404` | 随闪避/招架用例 |
| 3 | 闪避判定 | `combat.resolve_dodge()` | `combatd.lpc:328-354` | `test_dodge_*` |
| 4 | 招架战力选择 | `combat.select_parry_power()` | `combatd.lpc:387-393` | `test_select_parry_power` |
| 5 | 招架判定 | `combat.resolve_parry()` | `combatd.lpc:395-406` | `test_parry_*` |
| 6 | 武器/空手基础伤害 | `combat.roll_weapon_damage()` | `combatd.lpc:436-445` | `test_roll_weapon_damage_table` |
| 7 | 护甲减伤 | `combat.roll_armor()` + `damage_after_armor()` | `combatd.lpc:451-456` | `test_roll_armor_table` |
| 8 | 膂力伤害加成 | `combat.roll_strength_bonus()` | `combatd.lpc:464-513` | `test_roll_strength_bonus_table` |
| 9 | 经验压制修正 | `combat.modify_damage_by_exp()` | `combatd.lpc:524-528` | `test_modify_damage_by_exp_table` |
| 10 | 空明拳化解 | `combat.kongming_mitigate()` | `combatd.lpc:534-538` | `test_kongming_mitigate` |
| 11 | 招式威力系数 | `combat.apply_power_point()` | `combatd.lpc:546-549` | `test_apply_power_point` |
| 12 | 伤害分步总装 | `combat.resolve_damage()` | `combatd.lpc:436-549` | `test_resolve_damage_full_order_*` |
| 13 | 扣气血 `receive_damage` | `combat.apply_qi_damage()` | `feature/damage.lpc:14-38` | `test_apply_qi_damage` |
| 14 | 扣有效气血 `receive_wound` | `combat.apply_qi_wound()` | `feature/damage.lpc:40-69` | `test_apply_qi_wound` |
| 15 | 死亡/昏迷判定 | `combat.death_state()` | `inherit/char/char.lpc:64-78` | `test_death_state` |
| 16 | 伤势判定 | `combat.roll_wound()` | `combatd.lpc:560-567` | `test_roll_wound_draw_order` |
| 17 | 闪避经验奖励 | `combat.dodge_rewards()` | `combatd.lpc:361-373` | `test_dodge_rewards_*` |
| 18 | 招架经验奖励 | `combat.parry_rewards()` | `combatd.lpc:417-423` | `test_parry_rewards_*` |
| 19 | 命中经验奖励 | `combat.damage_rewards()` | `combatd.lpc:575-591` | `test_damage_rewards_*` |
| 20 | 属性派生 | `attributes.query_str/int/con/dex/sta/spi/per/cps/cor/kar()` | `feature/attribute.lpc:9-99` | `test_attribute_table` |
| 21 | 技能成长 | `skill.improve_skill()` | `feature/skill.lpc:172-204` | `test_improve_skill_table` |

## 2. 逐函数等价改写

通用改写（以下不再重复）：

- **除法**：LPC 整数 `/` 向零截断，Python `//` 向下取整。所有整数除法走
  `lpc_div()`（`tools/xkx_formula/lpc_math.py`）。
- **随机**：LPC `random(n)` -> `prng.below(n)`，n<=0 返回 0。取数顺序与短路行为逐处复刻。
- **对象读取**：LPC 的 `me->query(...)`、`victim->query_temp(...)`、`query_skill(...)`
  全部改为显式参数；库内不持有对象。
- **浮点**：LPC 只有 `power_point` 一处浮点乘法（`combatd.lpc:549`），改为千分位整数
  乘除；其余全程整数。
- **映射缺键**：LPC 映射缺键按 0 处理，Python 侧 `skills.get(name, 0)`。

### 2.1 `skill_power`（combatd.lpc:197-242）

- 入参替换：`ob->query_skill(skill)` + `apply/attack|defense` -> `skill_level` +
  `apply_bonus`；`combat_exp` 独立传入；`query_str()/query_dex()` -> `attr_effective`；
  `ob->query("str")/query("dex")` -> `attr_base`。
- `!living(ob) || noliving(ob)` -> `living=False` 直接返回 0。
- 防御侧映射轻功的 `power_point(ob)`：浮点 `1.0..1.5` 改为千分位 `1000..1500`，
  `to_int(level * dpower)` -> `lpc_div(level * ppm, 1000)`（向零截断）。
- 源码 `if (!level)` 只对“恰好 0”成立；`level=-5` 会继续走到下限 1，这里保持。
- `str` 夹取 10..200，`str + ob->query("str")` 中的原始基值不夹取；保持。
- `(level / 6) * level * level` 是先整除再乘，不能化简成 `level^3/6`；保持。
- 返回值未夹取到 [1, 200000000]，与源码一致；命中/招架前再 `clamp_power`。

### 2.2 闪避（combatd.lpc:328-354）

- `ap/dp` 先夹取，再 `action["dodge"]` 修正，再夹取；忙碌 `dp /= 3` 在夹取之后
  （可能小于 1），保持源码顺序。
- `action["dodge"]` 的两种乘法（dp>1000000 先除后乘、否则先乘后除）保持，避免溢出。
- `random(ap + dp) < dp && !query_temp("bixie/cizu")` -> `cizu` 参数；随机仍先取，
  `cizu=True` 时依然消费一次随机数，回放序列不变。

### 2.3 招架（combatd.lpc:387-406）

- pp 选择：受害者有武器 -> `parry` 战力，攻击者空手时 `pp *= 2`；受害者空手且攻击者
  有武器 -> 0；双方空手 -> `unarmed` 战力，**不翻倍**（源码 `if (!weapon) pp *= 2`
  只在“受害者有武器”分支内，见 `combatd.lpc:389`）。
- 忙碌 `pp /= 3`、`action["parry"]` 修正、再夹取，顺序保持。
- `random(ap + pp) < pp && !query_temp("bixie/ciwan")` -> `ciwan` 参数，随机先取。

### 2.4 伤害（combatd.lpc:436-549）

- 基础伤害：仅原始值 >300 时做 `300 + random(x-300)`；随后
  `(damage + random(damage))/2`。`random(0)` 按 LPC 为 0。
- `action["damage"]`：`damage += action["damage"] * damage / 200`，整数截断。
- 护甲：仅 `apply/armor` >300 时截断，随后随机平均；扣减后低于 0 取 0。
- 膂力：`query_str()` -> `str_effective`；`hit_ob/be_hit_ob` 的返回值不能在本库求值，
  改为按源码顺序传入整数增量。顺序：force hit -> victim force be_hit ->
  `action["force"]` -> martial hit -> victim parry be_hit -> weapon hit -> 随机平均。
- 经验压制：`while (random(defense_factor) > my_exp)` 循环，`damage -= damage/3`、
  `defense_factor /= 2` 全部向零截断。源码在负经验时会死循环；本库显式拒绝负经验。
- 空明拳：源码 `damage <= 50`（含 0）固定为 10，`>50` 为 `damage/5`；保持。
- 威力系数：`power_point` 浮点改千分位；只在 `power_point > 0` 时相乘，不做 1.0..1.5
  夹取（夹取只属于防御侧 `skill_power` 的轻功系数）。
- `resolve_damage()` 只是把上述步骤按源码顺序串起来，并返回分步值，便于动作层核对。

### 2.5 扣血与死亡（damage.lpc / char.lpc）

- `receive_damage`：`val = qi - damage; val >= 0 ? val : -1`。
- `receive_wound`：`eff_qi` 扣到负数统一落 -1，随后 `qi` 不得高于新 `eff_qi`。
- 心跳判定优先级：`eff_qi<0 || eff_jing<0` -> dead；否则 `qi<0 || jing<0` ->
  `living && !noliving` 时 unconcious，否则 dead。`living` 参数合并了
  `living(obj)` 与 `query_temp("noliving")`。
- 原文拼写 `unconcious` 保留为返回值常量，便于日志对齐。

### 2.6 伤势判定（combatd.lpc:560-567）

- 布尔式短路会改变随机取数次数，这里逐步复刻：
  1. `random(damage) > armor` 失败 -> False（1 次取数）。
  2. killing：空手 `random(4)==0`，持武器 `random(2)==0`；命中 -> True。
  3. 否则：空手 `random(7)==0`，持武器 `random(4)==0`。
- 运算符优先级按 C/LPC：`&&` 高于 `||`，`killing` 只作用于第一组。

### 2.7 经验奖励（combatd.lpc:361-373、417-423、575-591）

- `userp(x)` -> `is_player`；`my`/`your` 映射字段 -> 显式参数。
- `random(jing * 100 / max_jing + int)` 的两步整除保持（先 `jing*100/max_jing`，再加 int）。
- 命中奖励使用 `jing * 30 / max_jing`，与闪避/招架的 100 不同；保持。
- 潜能条件 `potential - learned_points < 100000` 是严格小于；等于 100000 不加。
- 技能上限判断 `< 300`，到 300 不再返回技能增量。
- 闪避奖励的攻击者 NPC 分支会先取 `random(int)` 判经验，再取一次
  `random(int)` 作为 `improve_skill` 增量；`improve_skill` 内部再把 0 归一为 1。
- 招架分支源码只有受害者奖励，没有攻击者经验；不要“补全”。
- 命中奖励：双方都是玩家时整段跳过，不取任何随机数；否则先攻击者（若 `ap<dp`）
  再受害者（无条件）。

### 2.8 属性（feature/attribute.lpc:9-99）

- `query_skill(skill, 1)` 是原始等级，不是有效等级；`skills` 映射传原始等级。
- `query_str` 的 improve：先取 `unarmed/10`，再按 leg/strike/finger/cuff/hand/claw
  顺序比较；每次比较的阈值是“当前 improve*10”，所以结果等于这些等级最大值/10。
- `query_spi` 的源码 `query_skill("poison"), 1)` 是笔误；按“任一知识技能非零”实现。
  全零时两种写法输出都为 0，可达状态等价。
- `apply/*` 临时加成、基数、原始 base 属性全部显式传入；无上下限夹取（夹取在
  `skill_power` 内单独做）。

### 2.9 技能成长（feature/skill.lpc:172-204）

- `userp(this_object())` -> `is_player`；`query("curiousness")` -> `curiousness`。
- `sizeof(learned)` -> `learned_skill_count`；`query_spi()` -> `spi`。
- 学习惩罚：严格 `count > spi` 才除以 `count - spi`；等于不惩罚。
- `if (!amount) amount = 1`：0 增量为 1；负增量保持原样（源码也如此）。
- 升级条件严格大于 `(level+1)^2`；等于不升级。
- 升级后的 `SKILL_D(skill)->skill_improved(this_object())` 是全局回调，不翻译，
  只返回 `leveled_up=True` 供动作层自行处理。

## 3. 随机取数顺序（回放契约）

一次 `do_attack` 的取数顺序（同一 PRNG 实例按顺序调用）：

1. 闪避：1 次（**即使 `cizu=True` 也先取**，源码先算 `random` 再判标志）。
2. 未闪避 -> 招架：1 次（`ciwan=True` 同样先取）。
3. 未招架 -> 基础伤害：原始值 >300 时 2 次，否则 1 次。
4. 护甲：护甲值 >300 时 2 次，否则 1 次。
5. 膂力加成：仅 `bonus > 0` 时 1 次。
6. 经验压制：0..N 次（`defense_factor` 每次减半，直到随机值不超过攻方经验）。
7. 扣血（无随机）。
8. 伤势：1..3 次（首个护甲判定 + killing 组 + 兜底组）。
9. 奖励：闪避分支 0..2 次，招架 0..1 次，命中 0..2 次。

`SequenceRandom.below(n<=0)` 仍记录一次调用、返回 0、不消费预置值。这样 `random(0)`
在回放序列里也占位，动作层可逐调用核对。

## 4. 不能机械翻译的部分（报告项）

这些不是公式本身，而是依赖对象/全局状态、技能虚函数、时间或副作用的逻辑。
本库**没有**替它们发明公式，只把结果作为参数暴露或明确排除：

1. **技能虚函数钩子**（`combatd.lpc:466-509`）
   `force_skill->hit_ob/be_hit_ob`、`martial_skill->hit_ob/be_hit_ob`、
   `weapon->hit_ob`、`me->hit_ob`。它们按技能/武器类型返回 int 或字符串，
   可能读写对象状态。`roll_strength_bonus` 接受 `force_hit`、`victim_force_hit`、
   `martial_hit`、`victim_parry_hit`、`weapon_hit` 五个整数增量，顺序与源码一致。
2. **招式序列**（`combatd.lpc:420-435`）
   `action = me->reset_action()` 由具体技能生成，含 `dodge/parry/force/damage/
   damage_type/action/post_action`。选择过程依赖技能与随机，本库只接受这些字段的
   已求值结果（`dodge_action_bonus`、`parry_action_bonus`、`action_force`、
   `action_damage`）。
3. **忙碌状态**（`combatd.lpc:343`、`395`）
   `victim->is_busy()` 取决于行动队列调度；以 `victim_busy` 布尔传入。
4. **辟邪状态位**（`combatd.lpc:354`、`406`）
   `query_temp("bixie/cizu")`、`query_temp("bixie/ciwan")` 是技能临时标记；以
   `cizu`/`ciwan` 布尔传入。
5. **对象身份与战斗关系**
   `userp()`、`living()`、`query_temp("noliving")`、`is_killing(id)`、`wizardp()`、
   `query_temp("weapon")`、`query_temp("apply/armor")` 等；全部变成参数。
6. **技能映射与轻功系数**（`combatd.lpc:216-227`）
   `query_skill_mapped("dodge")` 与 `SKILL_D(dskill)->power_point(ob)` 依赖技能配置，
   部分实现还会读对象状态（如 `quanzhen-jian` 查 `xiantian-qigong`、`shenlong-baiwei`
   判 `userp`）。本库只接受千分位 `dodge_power_point_permille`。
7. **`receive_damage` 副作用**（`feature/damage.lpc`）
   怒气 `nuqi` 累积、`add_heart_beat`、`last_damage_from`、死亡惩罚等写状态逻辑；
   `apply_qi_damage` 只算气血落值。
8. **死亡后果**（`inherit/char/char.lpc` 与 `die()` 实现）
   尸体、经验惩罚、`remove_all_enemy`、昏迷计时、`no_die` 等副作用不在纯函数范围；
   `death_state` 只返回 `alive/unconcious/dead`。
9. **技能升级回调**（`feature/skill.lpc:202`）
   `SKILL_D(skill)->skill_improved(this_object())` 是全局虚函数回调；
   `improve_skill` 只返回 `leveled_up`。
10. **奖励写回**
    `my["combat_exp"] += 1`、`potential += 1` 等是对象状态写入；奖励函数只返回
    `Reward` 增量，由动作层落库。
11. **表现层随机**（`combatd.lpc:306`、`670-673` 等）
    部位 `random(sizeof(limbs))`、胜负台词 `random(sizeof(winner_msg))` 只影响文案，
    不参与战斗数值，未翻译。
12. **追杀/任务奖励**（`combatd.lpc` 后半段）
    依赖 `time()`、killer、quest 映射与冷却，不属于本轮战斗公式范围。

### 4.1 缺失公式：暴击

在 `c1b87db` 全仓库检索 `暴击`、`critical` 均无命中：**xkx 的 combatd 没有暴击判定**。
最接近的机制是：

- `action["damage"]`（每招固定/比例加伤，`combatd.lpc:442-445`）；
- `SKILL_D(...)->power_point()`（招式威力系数，`546-549`）；
- `double_attack` / `TYPE_QUICK` / `TYPE_RIPOSTE`（多段攻击，`combatd.lpc:705-778`）；
- `kongming`（目标侧减伤，`534-538`）。

本库没有凭空发明暴击公式。如果动作层需要暴击，必须先给出设计（触发条件、倍率、
随机取数位置），再按新公式补来源与测试。

## 5. 实测校准（本机参照版 127.0.0.1:4004）

### 5.1 方法与可复现性

- 环境：fluffos v2019 + xkx100 mudlib，commit `c1b87db`，本机 `127.0.0.1:4004`。
- 采集脚本：`tools/xkx_formula/probe_calibration.py`（telnet 直连：登录/建号/走路/
  `fight`/`quit`；不读写 `tools/xkx_ref/**`）。解析脚本：
  `tools/xkx_formula/calibrate_report.py`。原始记录：
  `tools/xkx_formula/calibration/*.json`（含 `*.summary.json`）。
- 固定场景：角色 `xkxfa`（杭州出生，创建天赋 膂力20/悟性20/根骨20，无技能、无兵器）；
  对手 `书生`（`d/hangzhou/npc/shu-sheng.lpc`：`combat_exp=0`、无战斗技能、无护甲、
  `attitude=peaceful`）。
- 不可控项与处理：
  - 昼夜/天色只影响 `look` 的出口显示，不影响战斗数值。
  - 未显式 `set()` 的人族 NPC，str/con/dex 由 `adm/daemons/race/human.lpc:60-62`
    取 `10+random(21)`；书生 str 只能给区间，不能给单值。
  - 谣言、窗外、NPC greeting 等插播文本会混入输出；解析按“动作行 + 下一行结果”
    配对，不按位置计数，单次输出不作基准。
  - **经验漂移**：被打中时受害者按 `combatd.lpc:586-590`
    （`random(max_qi+qi) < damage`）有概率 `+1 combat_exp`，会抬高其 `skill_power`。
    书生场景里我方经验 0→6，若仍按静态 `ap=1` 预测会得到假偏差；下文按每轮实测经验
    重建 ap。

### 5.2 命中 / 闪避 / 招架（N=71，书生场景，10 轮）

预测（`skill_power` → `resolve_dodge` → `resolve_parry`）：
`ap = clamp(max(exp/2, 1))`；`dp = pp = clamp(max(书生 exp/2, 1)) = 1`；
`P(闪避)=dp/(ap+dp)`，`P(招架)=(1-P(闪避))*pp/(ap+pp)`，`P(命中)=余下`。

| 项 | 样本量 | 实测 | 预测（按每轮实测经验重建 ap） | 偏差 |
|----|--------|------|------------------------------|------|
| 闪避率 | 71 | 42.25%（30） | 44.13% | -1.88pp |
| 招架率 | 71 | 23.94%（17） | 23.85% | +0.10pp |
| 命中率 | 71 | 33.80%（24） | 32.02% | +1.78pp |

对照：若不考虑经验漂移、固定 `ap=1`（闪避 50%/招架 25%/命中 25%），偏差为
闪避 -7.75pp、命中 +8.80pp。可见偏差主要来自场景漂移，而非公式翻译。

逐轮（`hp` 实测经验 → ap；命中率）：

| 轮 | 起始经验 | ap | N | 实测命中率 | 预测命中率 |
|----|----------|----|---|-----------|-----------|
| 1 | 0 | 1 | 3 | 0.667 | 0.250 |
| 2 | 1 | 1 | 7 | 0.286 | 0.250 |
| 3 | 2 | 1 | 11 | 0.182 | 0.250 |
| 4 | 3 | 1 | 4 | 0.500 | 0.250 |
| 5 | 3 | 1 | 3 | 0.000 | 0.250 |
| 6 | 3 | 1 | 21 | 0.381 | 0.250 |
| 8 | 4 | 2 | 16 | 0.375 | 0.444 |
| 9 | 6 | 3 | 5 | 0.400 | 0.562 |
| 10 | 6 | 3 | 1 | 0.000 | 0.562 |

### 5.3 单次伤害：取值范围与文本桶

原版只通过 `damage_msg`（`combatd.lpc:63-130`）输出文本桶，不打印数值，故本轮只
校准到桶级。

| 方向 | 样本量 | 实测桶 | 预测 | 偏差 |
|------|--------|--------|------|------|
| 我方打书生 | 24 命中 | 10-19：24（100%） | 10-19 | 0（桶级完全一致） |
| 书生打我 | 14 命中 | 10-19：6；20-39：8 | `[5,29]` | 桶级一致 |
| 流氓打我 | 24 命中 | 10-19：24 | `[5,29]` | 桶级一致 |

- 我方：空手且无兵器 → 基础伤害 0；书生无护甲 → 护甲减伤 0；
  `query_str()=20` → `(20+random(20))/2 ∈ [10,19]`；我方经验 6-7 远大于书生 0-1，
  `random(0/1) > 6` 恒假，经验压制不触发。预测恰好是 10-19 桶。
- 书生：str ∈ [10,30] → `(str+random(str))/2 ∈ [5,29]`。观测全部落在 10..29，
  且出现 20-39 桶 ⇒ 该实例 str ≥ ~20（str ≤ 19 时伤害上限 18，不可能进 20-39 桶）。
- 流氓：6 轮 + 12 轮合计观测 24 次全在 10-19 ⇒ 该实例 str ∈ [11,19]（同样只是桶级反推）。

### 5.4 经验压制：新号 vs 1000 经验流氓

- 场景：同一角色（`combat_exp` 6→7，`ap=3`）在 `d/hangzhou/road6` 对
  `d/hangzhou/npc/liumang.lpc`（`combat_exp=1000`、unarmed/dodge 20、
  `apply/attack=10`、`apply/defense=10`）。两批记录合计 6 轮 + 12 轮。
- 预测：流氓 `dp = pp = ((30/6)*30*30 + 1000)/80 * (dex+dex_base)`，dex 随机 10..30
  ⇒ dp ∈ [1360,4080]；`ap=3` ⇒ 闪避率 99.78%..99.93%，命中率约 5e-7..1e-6。
- 实测（N=29 次我方攻击）：闪避 28（96.6%）、招架 1（3.4%）、命中 0。
  对手 24 次命中全部落在 10-19 文本桶。
- 偏差：命中率 0 vs ~1e-6 —— 方向上完全一致（“几乎打不中”）。但 0/29 的 95%
  上界约 11.7%，统计上仍无法把 1e-6 与 1% 区分开；这是场景结构决定的——每轮 1-4 次
  攻击后我方气血即降到一半，比武结束。
- 唯一需留意的点：出现 1 次招架（`结果被他挡开了`）。按 dp=pp≥1360 预测
  P(招架)≈0.22%，29 次里出现 1 次的二项尾概率约 6%，属小样本尾部，不足以判定
  公式有误；若后续样本 N≈300 仍稳定在 ~3% 招架，再回查 pp 分支。
- **伤害压制循环**（`combatd.lpc:524-528`）在本轮可复现场景中未触发：书生经验 0→1，
  `random(0/1) > 6` 恒假；流氓虽有 1000 经验但打不中。要在真实 MUD 上校准该循环，
  需要一个“经验 >80 且 dp 小”的对手（`(power+exp)/80` ≥1 才会让 dp 明显大于 1）；
  本仓库杭州 NPC 中没有这种组合（女孩 exp=70 但 `attitude=friendly`，`accept_fight`
  直接拒绝）。该项仍为推导值，列入待校准。

### 5.5 全量校准结论

| 校准项 | 样本量 | 实测 | 预测 | 偏差 | 判定 |
|--------|--------|------|------|------|------|
| 闪避/招架/命中（dp=pp=1） | N=71 | 42.25% / 23.94% / 33.80% | 44.13% / 23.85% / 32.02% | -1.88 / +0.10 / +1.78pp | 通过 |
| 我方空手伤害桶（str=20、无护甲） | 24 | 10-19：100% | 10-19 | 0 | 通过（桶级） |
| 对方伤害桶（str 随机） | 14 + 24 | 10..29 | `[5,29]` | 桶级一致 | 通过（桶级） |
| 经验压制—命中（0 exp vs 1000 exp） | 29 | 0 命中 | ~1e-6 | 方向一致；上界 11.7% | 通过（定性） |
| 经验压制—伤害循环 | 0 | — | — | 未触发 | 待校准 |
| 受害者经验反馈（被打中 +exp） | 6 / 14 命中 | exp 0→6，潜能 99→105 | `+= 1 / += 1` | 形态一致 | 通过（定性） |

- 未发现“公式翻译错误”级别的偏差。唯一看起来偏大的静态偏差（闪避 -7.75pp、命中
  +8.80pp）已由“被打中会涨经验 → ap 漂移”解释，按实测经验重建 ap 后收敛到 ±2pp。
- 仍无法用运行时校准的部分：伤害压制循环、数值级（非文本桶）伤害分布、经验阈值
  两侧的精确台阶。原因分别是：找不到符合经验的对手、原版只打印文本桶、样本量受限。

## 6. 测试与运行

```bash
cd /Users/lyinet/projects/XKX/world-sim
python3 -m pytest tests/test_xkx_formula_combat.py \
                 tests/test_xkx_formula_growth.py \
                 tests/test_xkx_formula_replay.py
```

当前结果：`95 passed`。

覆盖点：

- 边界：0 级 vs 800 级上限、属性 10/200 夹取、`level=0` 返回 `exp/2`、
  `level<0` 走到下限、`dp/pp` 夹取、`random(0)`；
- 必中/必闪取向：用固定随机源分别取 `0` 与 `n-1`，覆盖概率两端；
- 极高防御：护甲 100000 仍按“截断 300 + 随机平均”；
- 经验压制：零经验 vs 1000 经验、部分压制、负经验拒绝；
- 死亡：`eff_qi<0`、`eff_jing<0`、`qi<0`、`jing<0`、非 living；
- 取数顺序：每个用例断言 `prng.calls` 的上界序列；回放测试断言两次同序列输出一致；
- 纯度：AST 扫描公式库 6 个模块，禁止 `random/time/os/sqlite3/wsim` 导入
  （同目录的 telnet 对拍脚本 `probe_calibration.py` / `calibrate_report.py` 需要
  socket/time，不在纯度约束内）。

### 6.1 复现实测校准

```bash
cd /Users/lyinet/projects/XKX/world-sim

# 书生场景（命中/闪避/招架 + 空手伤害桶），10 轮
python3 tools/xkx_formula/probe_calibration.py --player-id xkxfa \
    --password '<口令>' --walk west southwest southwest west west north \
    --fight sheng --rounds 10 --round-seconds 45 --regen-wait 12 \
    --record tools/xkx_formula/calibration/scene_shusheng_10rounds.json

# 解析：实测 vs 预测、Wilson 95% CI、伤害桶、逐轮经验漂移
python3 tools/xkx_formula/calibrate_report.py \
    --input tools/xkx_formula/calibration/scene_shusheng_10rounds.json \
    --target 书生 --ap 1 --dp 1 --pp 1 --per-round \
    --summary-out tools/xkx_formula/calibration/scene_shusheng_10rounds.summary.json
```
