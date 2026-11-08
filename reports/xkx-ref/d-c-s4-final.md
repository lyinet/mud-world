# D / C / S4 收尾验收报告

日期：2026-11-08
仓库：`/private/tmp/xkx-parity-fix`，分支 `fix/xkx-room-parity`
世界：`/private/tmp/xkx-play`（API 8041，UI 8011，不 `--reset`）

## 0. 结论速览

| 项 | 结论 |
| --- | --- |
| D 走兽气血 | `race == "走兽"` 一律走 `beast.lpc:75-79` 年龄公式；狼狗 `age=4 → 50`、老虎 `age=20 → 425`；默认三档只留给源码未覆盖的 race/缺 `age` 的兜底 |
| D 重跑 | 狼狗 `kill` 10/10 击杀、10/10 掉皮；老虎 10 场全部按原版被反杀；店小二 20 场切磋全部停战 |
| D TTK | 狼狗每刀 17.46 vs 原版 19.22（−9%），同一单位下 200→50 血使 attack_rounds 15.6→5.4（≈3.0×）；显著短于原版，已归因到**死亡判定/补刀映射**，未回调任何数值 |
| C 逐回合门 | 20 场 × 149 回合，玩家门 149 行、店小二门 137 行（差的 12 行是停战最后一回合被截断）；两侧门每回合都真实执行 |
| C 对账 | 玩家 69/149 = **46.31%**，公式期望 **46.31%**；店小二 40/137 = **29.20%**，公式期望 **28.02%**（n=137，差 1.18pp 在抽样噪声内） |
| C 旧 15.26% | 旧口径只数“首次守卫”、且把不进门骰的反击/补刀算进分母；重算旧口径 = 玩家 31.15%、店小二 20.49%，正是原版 31%/24% 的同类下界。**不是 cps/cor 错，也没改公式** |
| S4 活体 | `fight 店小二` 停战、存活、`buy 龙井虾仁` 成功；`kill 大狼狗` 18 回合致死、`loot 狗皮×2`、行囊 +2；`help fight` / `help kill` 区别已上网 |
| 红线 | `tools/xkx_formula` 零改动；`content/xkx/**` 零改动；旧九地点 `735 / 009076f0… / 02d5f812…` 由测试锁死；全量 pytest 0 failed |

---

## 1. D：走兽血量按原版公式

### 1.1 实现

`src/wsim/sim/vitals.py`：

```python
def beast_max_qi(age: int) -> int:
    """adm/daemons/race/beast.lpc:75-79"""
    if age <= 5:
        return 50
    if age <= 20:
        return 50 + (age - 5) * 25
    return 425 + (age - 20) * 5
```

`resolve_npc_vitals` 的顺序改为：

1. 内容有 `vitals.max_qi > 0` → 直接用它（原版口径）。
2. 否则 `race == "走兽"` 且内容有 `age` → `beast_max_qi(age)`。
3. 否则落 `DEFAULT_MAX_HP_TIERS`（500/300/200）兜底。

覆盖范围（内容包实测）：

| race | 实例 | 走哪条 |
| --- | --- | --- |
| `走兽` | 大狼狗 `age=4` → **50**；杭州老虎 `age=20` → **425** | 原版公式 |
| `走畜` | 骆驼 `age=5` | 源码未覆盖，兜底 **200** |
| `飞禽` | 麻雀 / 乌鸦 | 源码未覆盖，兜底 **200** |
| `爬蛇` / `昆虫` / `游鱼` | 毒蛇 / 蝎子 / 鱼 | 源码未覆盖，兜底 **200** |
| `走兽` 但缺 `age` | 旧 `content/` legacy 实体 | 不猜 `random(40)+5`，兜底 |

`specs/xkx-parity/vitals-loot.spec.md` §2 已同步公式、档位与覆盖范围；
`tests/test_combat_vitals.py` 新增 `beast_max_qi` 边界（0/4/5/6/20/21/40 → 50/50/50/75/425/430/525）、
狼狗 50、老虎 425、显式 `max_qi` 优先，共 3 项断言。

### 1.2 重跑结果（新世界，seed 1-10 / 101-120 / 201-210）

| 场景 | 场次 | 结果 | rounds_used | attack_rounds | 玩家每刀 | 玩家伤害/回合 | 多段/连击 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 狼狗 `kill` | 10 | 10/10 击杀、10/10 掉皮 | 4–15，均 **9.20** | 4–8，均 **5.40** | **17.46** | **7.40** | 无（每 actor 每回合最多 1 次非守卫动作） |
| 老虎 `kill` | 10 | 0/10 胜，玩家均被反杀 | 3–10，均 **6.10** | 均 **6.10** | —（玩家 0 次出手） | — | 无 |
| 店小二 `fight` | 20 | 20/20 停战 | 5–10，均 **7.45** | 均 **6.30** | **17.52** | **14.58** | 无 |

狼狗原始对照（`reports/xkx-ref/s1-initiative-gate.md`，改前 `max_hp=200`）：
`rounds_used` 均 27.60、`attack_rounds` 均 15.60、玩家 124 刀 / 10 场、每刀 18.15。

### 1.3 “血量 4 倍，回合只差 15.6 vs ~13”的矛盾解释

先纠正单位：旧报告的 **15.6 是复刻 `attack_rounds`（max(玩家出手, 狼狗出手)）**，
`~13` 是原版 kill 的 `attack_rounds`；两者都按“每回合最多一次出手”计，
但复刻与关联的 `rounds_used` 是**交换轮**，单位不同，不能直接与 `rounds_used` 相减。

用同一口径重排：

| 口径 | 原版 kill | 复刻 改前（hp 200） | 复刻 改后（hp 50） |
| --- | --- | --- | --- |
| `max_qi/max_hp` | 50 | 200 | 50 |
| 玩家每刀均伤 | 19.22 | 18.15 | 17.46 |
| 玩家每场命中 | 5.4 | 12.4 | 3.9 |
| `attack_rounds` | 5–13 | 15.60 | **5.40** |
| `rounds_used` | — | 27.60 | **9.20** |
| 有效命中/回合（命中 ÷ rounds_used） | — | 0.449 | 0.424 |
| 总伤害/回合（伤害 ÷ rounds_used） | — | 8.15 | 7.40 |
| 多段/连击 | 无 | 无 | 无 |

结论：

- 改前→改后，`rounds_used` 27.60→9.20 = **3.0×**，`attack_rounds` 15.60→5.40 = **2.9×**；
  血量 200→50 是 4×，差的那一截来自**每次击杀固定多打一刀的补刀/过量伤害**：
  改后 50 血实际打掉 68（过量 36%），改前 200 血打掉 225（过量 12.5%），
  按“有效血量”算是 225→68 = 3.3×，与观测的 3.0× 一致。
- 每回合有效命中数 0.449→0.424、每回合总伤害 8.15→7.40，两者都基本不变，
  所以不存在“4 倍血被伤害膨胀抵消”的情况；伤害没有随血量改而漂移。
- 每 actor 每回合最多 1 次非守卫动作，**无多段、无连击**，不存在一回合多次结算。
- 旧观测“15.6 vs ~13”之所以看起来只有 1.2×，是因为把复刻 200 血的
  `attack_rounds` 与原版 50 血的 `attack_rounds` 直接比；原版攻击轮里夹着大量
  守卫/未出手回合（玩家实际出手 0.42 次/轮），而复刻的 `attack_rounds` 几乎等于
  玩家出手次数（0.72–0.79 次/轮）。口径不同，不能拿它反推血量倍数。

### 1.4 改后偏短的归因（未回调数值）

- 每刀伤害 **17.46 vs 原版 19.22（−9%）**，不是“伤害/防御数值”问题。
- 真正差异在**死亡判定映射**：复刻在目标 `qi <= 0`（昏迷）后，下一次 quick 补刀
  直接把目标置 `dead`（`combat_rules` 的 `uncon` + finisher 分支）；原版
  `inherit/char/char.lpc:72-77` 只在 `eff_qi < 0` 时 `die()`，`qi < 0` 只是昏迷，
  要继续靠 `receive_wound` 把 `eff_qi` 打到负值才会死。因此原版 50 血也要
  ~5.4 刀命中（大量刀落在昏迷后的 wound 通道），复刻 ~3.9 刀就结束。
- 这是“伤害→气血→死亡阈值”的映射差异，不是 `max_qi` 数值差异；
  按要求**没有回调 max_hp、没有改每刀伤害**。
- 建议（待批，未动）：另开一轮把补刀后的 `dead` 改为原版 `eff_qi < 0` 才死，
  并补一条“昏迷后仍可被 wound 打死、但不因 `qi<0` 立即死”的测试；这会拉长
  狼狗 TTK，使 kill 回合更贴原版，但不属于本次 D 的数值范围。

---

## 2. C：逐回合门槛审计

### 2.1 可观测字段（只在 `initiative_gate=true` 时写入）

`src/wsim/sim/combat_rules.py` 在门结算处把同一组字段写进每个 regular/guard 动作：

```json
{"gate_cor": 24, "gate_cps": 0, "gate_n": 0, "gate_roll": 0,
 "gate_pass": false, "gate_guard": true}
```

- `gate_roll = prng.below(victim.cps * 3)`；`gate_n = victim.cps * 3`。
- `gate_pass = gate_roll > attacker.cor`；`gate_guard = not gate_pass`。
- 骰序完全未变：仍是“先取门骰，再过门则 strike，不过则守卫；首次守卫再取两次
  `random(5)` 文案骰”。新增字段只是把已发生的值带出来，不新增/减少随机流。
- 关闭 `initiative_gate` 时动作行不写这些字段，旧事件字节不变。

### 2.2 覆盖

- 20 场 `fight 店小二`（seed 101-120），共 **149 回合**。
- 玩家门：**149 行 / 149 回合**；店小二门：**137 行**，少的 12 行全部是
  最后一回合玩家把店小二打到半血、战斗立即停战，店小二没轮到出手。
  逐场断言 `player_evals == rounds_used`、`npc_evals ∈ {rounds_used, rounds_used-1}`，
  0 例外。
- 全量逐行日志：`reports/xkx-ref/c-gate-audit.log`（594 行，含狼狗/老虎/店小二
  共 40 场；每场每回合每个 actor 一行）。按仓库策略 `reports/**/*.log` 不入库，
  原始日志留在工作树；本报告 §2.3 / §2.4 与下方样例已固化全部结论。

### 2.3 实测 vs 公式期望

期望口径：`random(n)` 均匀取 `0..n-1`，不出手 = `roll <= cor`，故
`P(不出手) = min(cor+1, n) / n`；`n = victim.cps * 3`。

| 侧 | 守卫/门评估 | 实测不出手率 | 公式期望（样本加权） | 判定 |
| --- | --- | --- | --- | --- |
| 玩家 | 69 / 149 | **46.31%** | **46.31%** | 完全一致 |
| 店小二 | 40 / 137 | **29.20%** | **28.02%** | 差 1.18pp，n=137 抽样噪声内 |

店小二 `cor/cps` 的逐实例分布（20 场，T3 的逐实例掷点）：

- `cor`：min **10** / mean **17.95** / max **28**
- `cps`：min **10** / mean **20.30** / max **30**
- 对应 `race/human.lpc:60-68` 的 `10+random(21)`（10–30，均值 20）；
  `cor` 均值偏低 2.05 是 20 个样本的抽样波动（均匀分布 SE≈1.3）。
- `_Actor.cps = raw_cps + temp_apply["apply/composure"]`、
  `_Actor.cor = raw_cor + temp_apply["apply/courage"]`；xiaoer2 的
  `temp_apply=[]`，狼狗只有 `apply/attack|damage|armor`，均不含 composure/courage；
  玩家 MID 也无装备加成。故门里读到的就是 `attrs_roll` 的原值。

### 2.4 旧口径 15.26% / 30.57% 是什么

用同一批 20 场，按旧口径 `首次守卫 / (出手 + 首次守卫)` 重算：

| 侧 | 首次守卫 | 静默守卫 | 出手（含反击/补刀） | 旧口径 | 全回合门口径 |
| --- | --- | --- | --- | --- | --- |
| 玩家 | 57 | 12 | 126 | **31.15%** | 46.31% |
| 店小二 | 25 | 15 | 97 | **20.49%** | 29.20% |

- 旧口径把**静默守卫**丢掉，还把**不进门骰**的反击/补刀算进“出手”分母，
  所以天然低于真实不出手率；原版报告里的 31% / 24% 也是同口径的下界
  （原文自己写的是“守卫首回合/(出手+守卫首回合) 作下界”）。
- 因此“玩家 30.57% 贴住 31%、店小二 15.26% 差一倍”不是“两侧门没都执行”，
  而是**两个方向的门评估次数、静默守卫比例、反击通道占比不同**造成的口径差。
  事件行已证明：玩家 149 回合每回合都有门骰，店小二 137 回合每回合也有门骰。
- 真实全回合不出手率 46.31%（玩家）/ 29.20%（店小二）都不低于原版下界 31%/24%，
  区间关系自洽。**没有为对齐 24% 改任何公式。**

样例行（完整 594 行见日志）：

```
# xiao seed=101 verb=fight target=/d/hangzhou/npc/xiaoer2 rounds=8
101 | r 1 | player:s1xiao101 | cor=24 | cps=15 | cps*3=45 | roll=24 | roll>cor=False | guard=True
101 | r 1 | /d/hangzhou/npc/xiaoer2 | cor=10 | cps=23 | cps*3=69 | roll=18 | roll>cor=True  | guard=False
101 | r 2 | player:s1xiao101 | cor=24 | cps=15 | cps*3=45 | roll=18 | roll>cor=False | guard=True
101 | r 2 | /d/hangzhou/npc/xiaoer2 | cor=10 | cps=23 | cps*3=69 | roll=29 | roll>cor=True  | guard=False
```

---

## 3. S4 活体验收（8011 / 8041，真 HTTP，不 `--reset`）

账户 `s4accept`（非 `boss`），世界 `existing`，API 重启后 `tick≈5568`。

### 3.1 `fight 店小二`

`POST /api/xkx/command {"command":"fight 店小二"}` → `seq 8514 / tick 5566`：

```
【楼外楼】...店小二跑来跑去，忙着招呼客人。
第1回合
  你慢慢地移动着脚步，伺机出手。
  店小二慢慢地移动着脚步，伺机出手。
  你往店小二的要害用力挥出一拳，造成16点伤害（气血100→84）。
共1回合，你占上风。
店小二拱手认输：“阁下武功高强，在下认输。”
点到即止。
气血：你100 / 店小二 84。
```

- `engine_details.combat`：`winner=attacker`、`stopped=true`、`dead=[]`、`lethal=false`。
- 说明：店小二在上一轮 S4 验收（`seq 8127`）已从 200 被打到 **100/200**，即已在
  半血阈值；本轮第一次命中越过 `hp*2 <= max_hp`，立即停战并停在上限下方 84。
  阈值判断按原版 `combatd.lpc:683-691` 的“命中后判定”，允许最后一刀过量。

### 3.2 `look` 存活 + `buy`

```
【楼外楼】...在场人物：店小二。
货架：... 龙井虾仁 50文（剩29）...
### buy 龙井虾仁 → ok=true
你买下了一客龙井虾仁。
```

随后 `i`：`龙井虾仁 ×1`（原为 0），证明店小二既没死也仍能交易。

### 3.3 `kill 大狼狗` + 掉皮

从楼外楼 `go 西/北/西/西南/西/西/南/西` 到郭庄（`/d/hangzhou/guozhuang`），
`在场人物：二只大狼狗`。`kill 大狼狗` → `seq 8573 / tick 5622`：

```
第1回合 ... 你挥拳攻击大狼狗的要害，造成13点伤害（气血124→111）。
...
第17回合 ... 造成22点伤害（气血6→0）。
第18回合 ... 造成17点伤害（气血-1→0，受了伤）。
共18回合，你赢了，大狼狗倒下了。
气血：你100 / 大狼狗 0。
```

`look`：`地上散落：狗皮×2。`；`loot 狗皮` → `捡起：狗皮×2。`
`i`：

```
【行囊】
狗皮 ×2（折卖 180文/件）
龙井虾仁 ×1
粗布 ×1
炊饼 ×3
```

背包从 0 → 狗皮×2（本场击杀 + 另一次环境击杀的遗留），满足 +1。

### 3.4 `help` 两行

`help fight`：

```
【命令 · fight（切磋、较量）】
说明：向目标发起切磋（非致命）：按回合结算招式、伤害，受击方气血降到一半即停手，不分生死、不掉落。
依据：FightAction 已注册；开启 combat.lethal_fight 后是切磋（combat_rules 按 combatd.lpc:683-691 半血停战），仍给 combat_exp/技能进度。要取性命用 kill。
```

`help kill`：

```
【命令 · kill（杀、击杀）】
说明：对目标下杀手（致命）：走死亡结算并掉落；与 fight 的切磋不同，fight 到半血会停手，kill 才会打死目标。
依据：KillAction 在 combat.lethal_fight 打开时注册；文字壳 kill 走 _cmd_attack，目标解析与 fight 相同（_match_fight_target，多命中取第一件）。
```

来源：`tools/xkx_help/data/commands.yaml` 更新后由 `tools/xkx_help/build.py`
重新生成 `content/xkx_help/*` 与 `specs/xkx-parity/help-coverage.md`，
已同步到 `/private/tmp/xkx-play/content/xkx_help/`。

---

## 4. 老板库写入审计

### 4.1 验收账号与事件范围

| 账号 | 事件数 | seq 范围 | 主要 verb |
| --- | --- | --- | --- |
| `probe1` | 3 | 913-916 | enter/endowment/buy |
| `probe2` | 4 | 918-937 | enter/endowment/buy/move |
| `evidence` | 2 | 947-948 | enter/endowment |
| `verify2` | 32 | 2820-3041 | enter/move/buy/wield/fight/bai |
| `p5peek` | 21 | 3968-8160 | enter/move/fight×2/kill×2/loot×2 |
| `shots` | 7 | 870-898 | enter/move/fight |
| `s1hunter` | 5 | 5507-5573 | enter/fight |
| `s4spar` | 4 | 8115-8136 | enter/fight/buy（S4 首次） |
| `s4accept` | 15 | 8510-8713 | enter/fight/buy/move×8/kill/loot（本次） |

合计约 **93** 条验收写入，全部落在上表账号；没有一条挂在 `player:boss` 名下。

### 4.2 `boss` 自身的序列

- `seq 5-861`（tick 3-598）：用户自己的演示流程（`talk/buy/fight/rest` 等），
  不是验收写入。
- `seq 3965`（tick 2921）：`player:boss | look | {"account":"boss","reason":"unknown_action"}`
  → `outcome=rejected`，`effects` 为空。这是一次被拒绝的未知动作探测，
  **没有产生任何状态变更**；其后 `boss` 再无可观测写入，停在 `/d/hangzhou/shanlu7`。
- 结论：`boss` 的事件序列里没有来自本轮 D/C/S4 验收的写入。

### 4.3 临时实体

- 事件表里没有 `wolfdog`/临时 NPC 的 `spawn`/`entity_add` 记录；唯一带实例后缀的
  实体是正常房间克隆 `/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2`。
- 当前活体 `look`：楼外楼 `在场人物：店小二`；郭庄 `在场人物：二只大狼狗`。
  没有任何临时实体残留。

### 4.4 `entity_respawn` 载荷（B 修复仍在）

`system_room_reset` 的 `entity_respawn` 原始载荷（`seq 8575 / tick 5623`）：

```json
{"data":{"entity":"/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2",
 "instance":2,"hp":200,"max_hp":200,
 "drops":{"/d/hangzhou/npc/obj/goupi":1},
 "attrs":{"con":21,"cor":30,"dex":34,"int":7,"kar":17,"str":26}, ...}}
```

`drops` 非空。活世界仍显示 `max_hp=200`，因为该世界创世/重生早于 D 代码且未重建；
D 的 50 只在新建世界或下一轮按新代码重生时生效（不重建老板世界的要求不变）。

---

## 5. 改动文件与红线

- `src/wsim/sim/vitals.py`：走兽公式 + 兜底注释。
- `src/wsim/sim/combat_rules.py`：门审计字段（仅 `initiative_gate` 开启时）。
- `config/worlds/xkx.yaml`：恢复 `combat.initiative_gate: true`（S4 配置提交时误删），
  保留 `combat.lethal_fight: true`。
- `tools/xkx_help/data/commands.yaml` + `content/xkx_help/*`：`fight`/`kill` 帮助。
- `tests/test_combat_vitals.py`：走兽公式与兜底断言。
- `specs/xkx-parity/vitals-loot.spec.md`、`specs/xkx-parity/help-coverage.md`。
- `reports/xkx-ref/d-c-s4-final.md`、`reports/xkx-ref/c-gate-audit.log`。
- 未动：`tools/xkx_formula/**`、`content/xkx/**`。
- 旧九地点由 `tests/test_xkx_determinism_legacy.py` 锁死
  `735 / 009076f0... / 02d5f812...`。
