# 命中率实测规格（xkx100 原版 · 引擎校准基准）v3

状态：已执行（v3 按实际采集口径校正：MID exp 6400、startroom 直登、老虎登录即 kill）
产出物：`reports/xkx-ref/hit-rate.md` + 原始日志 `reports/xkx-ref/hit-rate/<tier>_<target>/run{1,2,3}.txt`
文件范围：`tools/xkx_ref/**`、`reports/xkx-ref/**`、`specs/xkx-parity/hit-rate.*`
禁止：改任何 LPC 逻辑；改 `src/**`、`ui/**`、`content/**`、`config/**`；动其他线路未提交改动。

## 1. 已确认的判据（源码事实，非推断）

`adm/daemons/combatd.c`：

- 闪避：`if (random(ap + dp) < dp)`（L354）→ 命中率 ≈ `ap/(ap+dp)`；随后招架：`if (random(ap + pp) < pp)`（L406）。
- `skill_power(ob, skill, usage)`（L197-242）：
  - `level = query_skill(skill) + apply/attack|defense`；**level==0 时直接返回 `combat_exp/2`**；
  - level 上限 **800**；`power = (level/6)*level*level`；
  - 攻击 = `(power+exp)/80 * (str + query("str"))`；防御 = 同式用 `dex + query("dex")`。
- 空手防御：`pp = skill_power(victim, "unarmed", DEFENSE)`（L392）——即“空手招架力取招架方的拳脚战力”，与复刻一致。
- 无兵器、无 prepare 映射时 `attack_skill = "unarmed"`（L272）。
- 伤害：基础伤害只来自 `apply/damage|unarmed_damage`（无兵器时为 0，L436-439），再加 `query_str()`（L464）与技能 `hit_ob`；普通玩家只能看到 `damage_msg` 分档文案。
- 战斗涨经验：玩家每次攻击/防御成功约 +0.38 exp（L572-591），**所以 50 次攻击会显著抬高 exp 与命中率**，必须在单条命内重新载入快照。

## 2. 目标角色：两档

| 档 | 角色 | exp | 技能（等级） | str/dex | 兵器 | 来源 |
|----|------|-----|------------|---------|------|------|
| NEW（新号） | xkxref | 9 | 无 | 20/20 | 无 | 现有快照 `run/snapshots/xkxref.o` |
| MID（40 级档） | xkxmid | 6400 | unarmed 40 / dodge 40 / parry 40 | 20/20 | 无 | **构造快照**：在 xkxref 存档文本上改 `combat_exp` 与 `skills` 两个字段（数据层，不改 LPC） |

MID 的“差在哪”（必须写进报告）：

1. 原版要拜师、练功、实战才能到 40 级，本机无法在合理时间内自然练成；该快照只改了存档数据，未改任何 LPC。
2. exp 取 6400（`cmds/skill/practice.c` 允许 40 级的最低经验，40^3/10）；取 0 会让伤害公式
   `while (random(defense_factor) > my_exp) damage /= 3` 把伤害反复除到 0，属不合法状态。
3. 天赋 20/20/20/20，而复刻对照是 dex 18；差异会体现在 `(dex+query("dex"))` 项。
4. 无兵器、无 `map_skill` 映射 → dodge 的 `power_point` 按 1.0，无特殊轻功加成。
5. 无护甲、无内功/内力（max_neili 0），jiali 不生效。

**构造合法性的验证**：先只对 4010 实例做一次 dry-run，登录后 `score` 应显示经验 6400、`skills` 应显示 unarmed/dodge/parry 40；若驱动拒绝该存档（回退到新号或报错），立即停止并改用真实新号 + 明确“无法构造”的差距说明。

## 3. 场景矩阵（6 组 × 3 次取样，单次对方出手 ≥50；老虎场景玩家 0 出手，见第 6 节）

| 组 | 档 | 对手 | 落点（改存档 `startroom` 直登） | 关键参数（源码推断） |
|----|----|------|--------------|----------------------|
| N1 | NEW | 店小二 `xiao er` | `/d/city/zuixianlou` 醉仙楼 | exp100、无技能 → ap=dp=50 |
| N2 | NEW | 流氓 `liu mang` | `/d/foshan/street2` 佛山镇街（单只同名 clone） | exp1000、unarmed/dodge 20、apply 10 → ap=dp≈2720 |
| N3 | NEW | 老虎 `laohu` | `/d/wudang/hutouyan` 虎头岩 | exp30000、apply 20000/30000、armor1000、jiali100、dex 默认20 → dp≈4×10^7 |
| M1 | MID | 店小二 | 同上 | 同上 |
| M2 | MID | 流氓 | 同上 | 同上 |
| M3 | MID | 老虎 | 同上 | 同上 |

- 开战用 `kill`（`fight` 对店小二会被拒绝）。
- 导航不做 44 步走路：`startroom` 是存档数据字段，只决定登录落点，不参与战斗计算；
  由此避开夜间出口不可见、路线被挡等与命中率无关的环境噪声。
- N1/N2/M1/M2 用“死亡或击杀 → 停实例 → 覆盖快照 → 起实例”循环，每条命都是新 clone。
- N3/M3：老虎场景登录即 `kill`，不先做 `look`/`verbose`/`hp`（玩家会被秒杀，时间全留给战斗）；
  玩家 0 次出手是 `combatd` 的 guard 分支所致（见第 6 节）。

## 4. 统计口径（全部可从文本观察）

- `player_attacks`：以“你…”开头、以“！”结尾、排除“喝道/「」”的攻击动作行。
- `player_dodge/parry/hit`：紧随结果行关键词：`闪|躲|避`→被闪避；`架|格|挡|招架`→被招架；`结果…`→命中。
- `npc_*`：以对手名开头、含“你”、以“！”结尾的动作行；结局同上。蓄势行（“注视着…寻找机会出手。”）不计。
- 命中率 = hit/attacks；单次与三次合并均给 Wilson 95% 区间。
- **我方每回合掉血**：`set verbose 1` 会给每次我方出手打印精确 `精：x/y 气：a/b`；逐回合取相邻两次的 `气` 差（净掉血 = 伤害 − 回气），报 min/median/max 与分档文案交叉校验（文案阈值来自 `damage_msg`）。
- 我方输出伤害：只有文案档位（`<10/10-19/20-39/40-79/80-119/120-159/160-239/≥240`），按档取中点，标“估计”。
- 每回合平均伤害（双方）= Σ(命中×档位中点)/该方出手次数；另列“每次命中平均伤害”。

## 5. 必须记录的属性

- 玩家（每次跑开始/结束）：`score`（悟性/根骨/膂力/身法、exp、被杀数）、`skills`（技能与等级）、`i`（有无兵器）、`hp`（精/气）。
- 对手：`look` 原文；LPC 数值（exp/skills/str/dex/apply）**标注“源码推断，游戏内不可见”**。
- 天赋：20/20/20/20（存档即该角色）。

## 6. 交付结构（`reports/xkx-ref/hit-rate.md`）

1. 环境与固定输入（驱动版本、mudlib c1b87db、快照 hash、端口、时间）。
2. 六个场景各：≥1 条完整回合原文、单次统计表、三次表与区间。
3. 汇总表：NEW / MID × 三目标；每格给双方命中率与三次区间。
4. 对背景问题的直接回答：MID 档 vs 店小二/流氓/老虎 的双方命中率，与复刻 1.1% / 10.0% / 77.2% 的对照结论。
5. 公式预测 vs 实测的差异说明（取不到的字段写明“取不到+原因”，不填估计值）。
6. 可观察 vs 推断清单（逐项标注），MID 档差距清单。
7. 复现命令、原始日志路径、快照 md5。

## 7. 验收

- 6 组 × 3 次，每次对方出手 ≥50（老虎场景玩家 0 出手，按对方出手计）。
- 报告中每个数字都能由原始日志用 `--reparse` 复算。
- 无 LPC 改动；`git status` 仅本任务文件；显式 `git add`（`reports/**/*.txt` 用 `-f`）；推 `origin xkx-parity`。
- 4004 只做 ≤3 分钟确认跑；测完断开会话，守护在线；采样实例全部停止。
