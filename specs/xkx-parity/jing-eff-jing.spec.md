# jing / eff_jing（精神）子系统规格

状态：**只做取证与设计，不写实现**（2026-11-09）。
基线：`fix/xkx-aggro-delay` 的 `3d06896`（= `ebf5d50` + 偷袭窗口 / `eff_hp` 修复）。
原版取证根：`world-sim/tools/xkx_ref/src/xkx100/`。
我方取证根：本 worktree `src/wsim/`。
凡标「未取证」的，都在该条内写明原因。

---

## 1. `jing` / `max_jing` / `eff_jing` 的初始化与上限

### 1.1 玩家（人类）

- 建档：`adm/daemons/logind.lpc:918-922` 直接写死
  `jing=100`、`max_jing=100`、`eff_jing=100`。
- 之后每次 `update_condition()` 重算上限：`adm/daemons/updated.lpc:134-138`
  （人类公式在 `adm/daemons/race/human.lpc:71-74` 是同一份）：
  ```
  age <= 14          -> max_jing = 100
  14 < age <= 30     -> max_jing = 100 + (age - 14) * int
  age > 30           -> max_jing = int * 16 + 100
  ```
  另受门派/心法修正（`human.lpc:77-105`、`updated.lpc:141-160`，如 `linji/jing`、
  `hunyuan-yiqi` 等级），此处不复述公式细节。
- 重算条件：`userp(ob) || undefinedp(max_jing) || max_jing < 1`
  （`human.lpc:71`、`updated.lpc:134`），因此玩家每次心跳都会重算。
- 上限侧：`max_jing` 没有统一硬顶（对比 `npcd.lpc:301-302` 给 `max_qi` 顶到 6000）；
  玩家每 tick 归一化：`updated.lpc:263-267`
  （`eff_jing` 缺失→max，`eff_jing>max_jing`→max，`jing>eff_jing`→eff_jing）；
  NPC 在 `chard.lpc:76-77` 做同样的 `eff_jing` 夹取。
- 初次 `jing`/`eff_jing` 补齐：`chard.lpc:73,76-77`
  `undefinedp(jing) → jing=max_jing`、`undefinedp(eff_jing) || eff_jing>max_jing → eff_jing=max_jing`。

### 1.2 走兽 / 其他 NPC

- **走兽有 jing**，不是只有人类有：`adm/daemons/race/beast.lpc:69-74`
  ```
  age <= 3   -> max_jing = 50
  age <= 10  -> 50 + (age - 3) * 20
  age <= 30  -> 190 + (age - 10) * 5
  else       -> 290 + (age - 30) * 2
  ```
- 所有 NPC（含走兽）在 `adm/daemons/npcd.lpc:305-309` 统一覆写：
  ```
  max_jing = hp_status["max_jing"] * scale / 100
  if (max_jing < max_qi / 2) max_jing = max_qi / 2
  eff_jing = max_jing
  jing     = max_jing
  ```
  LPC 中 `hp_status["max_jing"]` 缺失取 0，所以**没有 hp_status 的 NPC 一律是
  `max_jing = max_qi / 2`**（向下取整）。
- 通用补齐：`adm/daemons/chard.lpc:72-80`（`jing=eff_jing=max_jing`）；
  `npcd.lpc` 的覆写发生在其后，故 NPC 最终以上式为准。
- 玩家死后再投胎：`feature/damage.lpc:315-321` `reincarnate()` 只把 `eff_jing/eff_qi`
  抬到 `max_*`，不回 `jing`。

### 1.3 与我们内容的对应

- `content/xkx/npcs.json`：105 个 NPC 中 **49 个**带 `vitals.max_jing`，**52 个**带
  `vitals.max_qi`；`laohu`（age 20，`max_qi=425`）与 `wolfdog`（age 4，`max_qi=50`）
  **都没有 `max_jing`**，按 §1.2 规则应为 `212` / `25`。
- 未取证：`hp_status` 的原始来源（`mob.lpc`/`npc` 模板表）不在本仓库，无法逐个 NPC
  还原 `hp_status["max_jing"]`；因此我方只能采用 `max(vitals.max_jing, max_qi/2)`
  的上式，并在 spec 明示这是「按 npcd 下限」而非逐条 hp_status 复刻。
- 我方玩家当前**没有 age 字段**（`src/wsim/actions/player.py` 建档无 age），
  故 `max_jing` 只能固定 100（= age≤14 / logind 建档值）；年龄增长后的公式
  （`updated.lpc:134-138`）属于未来项。

### 1.4 我方现状

`src/wsim/world/state.py:60-64` 只有 `hp/max_hp/eff_hp` 与 `stamina`，**没有 jing 字段**；
`src/wsim/sim/vitals.py:183-247` 只解析 `max_qi→max_hp`，不解析 `max_jing`。

---

## 2. 两条伤害通道与钳制

原版两条通道对 `jing` 与 `qi` **完全同构**，只是字段名由 `type` 拼接：

- `receive_damage(type, n)`：`feature/damage.lpc:14-38`
  - `n < 0` → `error`（`:18`）；`type` 只允许 `jing|qi`（`:19-20`）。
  - `val = query(type) - n`；`val >= 0` 写 `val`，否则**写 -1**（`:30-33`）。
  - 副作用：招式怒气 `nuqi += n/5`（`:24-29`）、`set_heart_beat(1)`（`:35`）。
- `receive_wound(type, n)`：`feature/damage.lpc:40-63`
  - `val = query("eff_" + type) - n`；`>=0` 写 `val`，否则**写 -1**（`:56-60`）。
  - 怒气 `nuqi += n/3`（`:50-55`）。
- 恢复侧：
  - `receive_heal(type, n)`：`jing` 最多补到 `eff_jing`（`:78-81`）。
  - `receive_curing(type, n)`：`eff_jing` 最多补到 `max_jing`（`:86-101`）。
- 结论：**下限是 -1，不是 0**；`0` 是「刚好见底」，`-1` 才触发 §3 的昏迷/死亡。
  `jing` 与 `qi` 共用同一函数族，仅字段不同（也没有任何钳到 `max_jing` 的伤害上限）。

我方现状：`src/wsim/world/effects.py:553-588` 只有 `hp_add/hp_set/eff_hp_set/eff_hp_add`，
下限逻辑与上面一致（`floor=-1` 语义），但**没有 jing 通道**；
`tools/xkx_formula/combat.py:368-386` 的 `death_state` 已经接受 `jing/eff_jing`，
只是调用点传的是 hp（见 §3）。

---

## 3. 死亡 / 昏迷判定中的 jing

`inherit/char/char.lpc:57-78`（心跳开头）：

```
eff_qi < 0 || eff_jing < 0  -> remove_all_enemy(); die(); return;
qi < 0     || jing     < 0  -> 非 living 则 die()，否则 unconcious(); return;
```

- `unconcious()`：`feature/damage.lpc:105-144`，其中
  `:121 remove_all_enemy()`、`:137-138 set("jing",0); set("qi",0)`、
  `:143 call_out("revive", random(100 - con) + 30)`。
- `die()`：`feature/damage.lpc:162+`；玩家进鬼门时
  `:299-300 set("jing",1); set("eff_jing",1); set("qi",1); set("eff_qi",1)`；
  仇杀冷却分支会把四项写成 10（`:236-239`）。
- `revive()`（苏醒）本体在 `feature/damage.lpc:146-160`，**不改 qi/jing**；
  所以醒来后 `qi=0, jing=0`，靠 `heal_up` 与 `sleep` 回填。
  （`:204-208` 是论剑退出分支，会四项回满；`:315-321` 是投胎分支；
  `:299-300` 是玩家进鬼门时四项置 1。）
- 自动逃跑的 wimpy 同时看两条线：`char.lpc:87-92`
  `qi*100/max_qi <= wimpy || jing*100/max_jing <= wimpy`。

**与我只建模 qi 的差异**：

| 判定 | 原版 | 我方当前 |
|---|---|---|
| 必死 | `eff_qi<0` **或 `eff_jing<0`** | 仅 `eff_hp<0`（`combat_rules.py:517,551,618` 传 `jing=hp, max_jing=max_hp`，`:633` 传 `death_state`） |
| 昏迷 | `qi<0` **或 `jing<0`** | 仅 `hp<0` |
| 昏迷清空 | `qi=jing=0` | 只 `hp=0` |
| wimpy | qi% 或 jing% | 仅 hp%（`actions/aggression.py:37-105`） |

`tools/xkx_formula/combat.py:379-386` 已实现原版两条规则，**只差真实 jing 取值**；
所以这一项是「接线 + 字段」，不是重写公式。

---

## 4. 恢复

### 4.1 `heal_up`（心跳被动）

- 调用时机：`heart_beat` 每 `tick = 5 + random(10)`（6..15 心跳）一次，
  `char.lpc:108-109`，由 `char.lpc:118` 调 `heal_up()`。
- jing 公式：`feature/damage.lpc:360-367`
  ```
  jing += con / 3 + max_jingli / 10
  if (jing >= eff_jing) {
      jing = eff_jing;
      if (eff_jing < max_jing) eff_jing++;   // 每 tick 只 +1
  }
  ```
  qi 同构：`:369-375`（`qi += con/3 + max_neili/10`，同样封 `eff_qi` 再 `eff_qi++`）。
- 前置：没水/没食物时人不恢复（`:352-358`，只对 user/宠物）。
- 注意：`eff_jing` 每 tick 只 +1，而 `jing` 直接跳到 `eff_jing`；
  所以「伤势天花板」恢复远慢于气血值本身。

### 4.2 `sleep`

`cmds/std/sleep.lpc:194-217`：

- 若 `mud_age - last_slp < 120` 且 `max_jing > 160`（睡太多）：
  `qi += eff_qi/2 - qi/2`、`jing += eff_jing/2 - jing/2`（`:199-200`），
  **只回一半，且不回满**。
- 否则：`qi = eff_qi`、`jing = eff_jing`（`:208-209`），并记录 `last_slp`（`:213`）。
- 两条分支都**只回到 eff_*，绝不回 max**；苏醒路径 `:241-242` 也是
  `qi=eff_qi; jing=eff_jing`。

### 4.3 我方现状

- `SystemHealAction`（`src/wsim/actions/system.py:514-578`）只恢复 hp/eff_hp，
  jing 无对应。
- `PlayerRestAction`（`src/wsim/actions/player.py:623-699`）`sleep` 分支只
  `hp = healing_ceiling(ent)`（`:658-663`），jing 无对应；
  `healing_ceiling` 在 `player.py:585`，只看 `eff_hp`。
- 我方没有 `max_jingli`，故 `max_jingli/10` 项恒为 0（需在 spec 注明，不虚构）。

---

## 5. 依赖 jing 的功能

### 5.1 `ansuan`（已实现，缺 jing 门槛与消耗）

- 门槛：`cmds/std/ansuan.lpc:133-134`
  `if (query("jing", 1) < 100) return notify_fail("你无法集中精力，暗算不了别人。")`。
- 消耗：`:226-227` `start_busy(3)` 后 `add("jing", -50)`（先判定后扣，故不会扣成负数）。
- 我方 `AnsuanAction`（`src/wsim/actions/combat.py:624+`）preconditions 里
  **没有 `jing < 100`**，apply 里也**没有 `-50`**。

### 5.2 `sleep` / `rest`

见 §4.2；我方 `sleep` 缺 jing。

### 5.3 wimpy 自动逃跑

`char.lpc:87-92` 同时比较 qi% 与 jing%；我方
`src/wsim/actions/aggression.py:37-105` 只比较 hp%。

### 5.4 全部读取 jing 的命令与技能（grep 依据）

命令（语义读，43 个，`grep -rn 'query("jing"\|add("jing"\|set("jing"\|eff_jing\|max_jing' cmds/`）：

```
cmds/imm/full.lpc, cmds/skill/exercise.lpc, cmds/skill/jingzuo.lpc,
cmds/skill/learn.lpc, cmds/skill/perform.lpc, cmds/skill/persuade.lpc,
cmds/skill/research.lpc, cmds/skill/respirate.lpc, cmds/skill/study.lpc,
cmds/skill/teach.lpc, cmds/skill/yanlian.lpc, cmds/std/ansuan.lpc,
cmds/std/attack.lpc, cmds/std/beg.lpc, cmds/std/check.lpc, cmds/std/come.lpc,
cmds/std/drink.lpc, cmds/std/eat.lpc, cmds/std/feed.lpc, cmds/std/guard.lpc,
cmds/std/hbless.lpc, cmds/std/imbibe.lpc, cmds/std/jiasi.lpc,
cmds/std/locate.lpc, cmds/std/location.lpc, cmds/std/mbless.lpc,
cmds/std/piao.lpc, cmds/std/sleep.lpc, cmds/std/steal.lpc, cmds/std/train.lpc,
cmds/usr/finger.lpc, cmds/usr/hp.lpc, cmds/usr/score.lpc, cmds/usr/who.lpc,
cmds/wiz/body.lpc, cmds/wiz/sc.lpc, cmds/wiz/sphp.lpc, cmds/wiz/spsc.lpc,
cmds/wiz/w_du.lpc, cmds/wiz/w_jiao.lpc, cmds/wiz/w_xue.lpc,
cmds/wiz/w_yanjiu.lpc, cmds/wiz/who1.lpc
```

按语义分类（代表行）：

| 类别 | 文件 / 行 | 行为 |
|---|---|---|
| 修炼扣 jing | `cmds/skill/learn.lpc:94-95,122`；`study.lpc:78-87`；`research.lpc:64-80` | 每次扣 `jing_cost`，不足则拒绝 |
| 打坐/吐纳 | `cmds/skill/respirate.lpc:38,62`；`jingzuo.lpc` | `jing` 换成 `jingli` |
| 赶路/杂项 | `cmds/std/come.lpc:45-52`；`guard.lpc:39`（jing<200 拒绝）| 扣 jing 或设门槛 |
| 偷/乞/喂 | `steal.lpc`、`beg.lpc`、`feed.lpc` | 成功率/门槛读 jing |
| 暗算 | `ansuan.lpc:133-134,227` | ≥100 门槛，-50 |
| 睡觉 | `sleep.lpc:196-217,241-242` | 回到 eff_jing |
| 只读展示 | `cmds/usr/hp.lpc:51-54`、`score.lpc`、`who.lpc`、`finger.lpc` | 显示 jing/eff_jing/max_jing |

技能（`grep -rn 'query("jing"\|add("jing"\|set("jing"\|eff_jing\|receive_damage("jing"\|receive_wound("jing"' kungfu/skill`，68 个）：

```
kungfu/skill/art.u, banruo-zhang/yipai.lpc, bibo-shengong/roar.lpc,
bixie-jian/cixin.lpc, changquan.lpc, chousui-zhang/biyan.lpc,
chousui-zhang/huoqiang.lpc, chousui-zhang/huoqiu.lpc,
chousui-zhang/throw.lpc, chousui-zhang/yinhuo.lpc, dagou-bang/ban.lpc,
duanyun-bian/wuguang.lpc, force/bipin.000, force/bipin.lpc,
force/regenerate.lpc, goplaying.lpc, hamagong/hama.lpc,
huagong-dafa/jingsuck.lpc, huagu-mianzhang.lpc, huifeng-jian/jue.lpc,
huifeng-jian/mie.lpc, hunyuan-yiqi/roar.lpc, jingang-quan/jingang.lpc,
jiuyang-shengong/roar.lpc, king-of-dragon/heal.lpc,
king-of-dragon/jingheal.lpc, king-of-dragon/roar.lpc,
kuaihuo-strike/zhouyu.lpc, kuihua-xinfa/roar.lpc,
lingshe-zhangfa/shoot.lpc, linji-zhuang/{daxiao,fengyun,longhe,tiandi,youming,zhixin}.lpc,
liuyang-zhang/po.lpc, liuyang-zhang/zhong.lpc, lonely-sword/pozhong.lpc,
lonely-sword/zongjue.lpc, longxiang/fanchang.lpc, love-sword.lpc,
luteplaying.lpc, meiren-sanzhao.lpc, qianzhu-wandushou/suck.lpc,
qishang-quan/jingshi.lpc, quanzhen-jian/he.lpc, sanshou.lpc,
shenlong-staff/chang3.lpc, shenzhao-jing/jingheal.lpc,
songyang-strike/yinyang.lpc, sun-finger/meixin.lpc, taiji-quan/ji.lpc,
taiji-quan/luanhuan.lpc, taiji-shengong/taiji.lpc,
tanzhi-shentong/jinglei.lpc, tiezhang-zhangfa/pushan.lpc,
wudu-shengong/bx_arrow.lpc, wudu-whip/judu.lpc, wuyun-jian.lpc,
xuantian-zhi.lpc, xueshan-sword/wanmei.lpc, yijinjing/roar.lpc,
yunlong-zhua/ningxue.lpc, yunv-jian/he.lpc, yunv-xinfa/roar.lpc,
zhanzhuang-gong/roar.lpc, zhemei-shou/hua.lpc
```

其中**直接对 jing 造成伤害**（`receive_damage("jing")` / `receive_wound("jing")`）的
至少包括：`qishang-quan/jingshi.lpc`、`liuyang-zhang/zhong.lpc`、`liuyang-zhang/po.lpc`、
`wudu-shengong/bx_arrow.lpc`、`tanzhi-shentong/jinglei.lpc`、
`huagong-dafa/jingsuck.lpc`、`qianzhu-wandushou/suck.lpc`、`sanshou.lpc`、
`meiren-sanzhao.lpc`、`taiji-quan/luanhuan.lpc`、`tiezhang-zhangfa/pushan.lpc`。
其余多为消耗/回复/展示 jing。

**当前内容包不包含这些技能**：`content/xkx/skills.json` 的 48 个技能里，只有
`hunyuan-yiqi`、`jingang-quan` 命中字符串 `jing`，且都是 `yijinjing`/`jingang-quan`
名字包含，不是精神值。因此**第一阶段不需要实现 jing 伤害技能**，只需保证
`receive_damage("jing")` 的通道语义在 spec 中固定下来。

---

## 6. 落到我们的架构

### 6.1 `Entity` 字段

`src/wsim/world/state.py:60-64` 之后新增（与 hp 组并列）：

```python
jing: int = 0
max_jing: int = 0
eff_jing: int = -2      # 语义同 eff_hp：-2 未初始化，-1 见底，>=0 有效
```

不新增 `max_jingli`/`jingli`（当前内容与判定均不依赖；`heal_up` 里该项按 0 处理）。

### 6.2 `to_dict()` 序列化判据（与 `eff_qi` 一致）

`state.py:168-179` 现有判据是「`max_hp` 非 0 才写 hp 组；`eff_hp >= -1 且 != max_hp`
才写 `eff_hp`」。jing 完全照抄：

```python
if self.max_jing:
    data["jing"] = int(self.jing)
    data["max_jing"] = int(self.max_jing)
    if self.eff_jing >= -1 and int(self.eff_jing) != int(self.max_jing):
        data["eff_jing"] = int(self.eff_jing)
```

`heal_credit/heal_cd`（`:176-179`）现在只在 `eff_hp` 受伤时输出；改为
**`eff_hp` 或 `eff_jing` 任一受伤**时输出（两者共用同一心跳信用，见 §4.1）。
`revive_tick` 共用，不新增字段。

### 6.3 数值解析与迁移

1. `src/wsim/sim/vitals.py`：
   - 新增 `jing_for_npc(max_hp, content_max_jing)`：内容 `max_jing>0` 直用；
     否则按 `npcd.lpc:305-309` 的下限取 `max_hp // 2`。
   - **不走 `beast.lpc` 年龄公式**：`npcd.lpc` 的 `hp_status` 覆写发生在 race 公式
     之后，`hp_status` 缺失时 `max_jing` 归 0 再抬到 `max_qi/2`。所以
     `laohu=425//2=212`、`wolfdog=50//2=25`（与 §7.2 J2 一致）；若叠加
     `beast_max_jing` 会得到 240/70，反而偏离原版 npcd 结果。
   - `resolve_npc_vitals`（`:183-247`）增加 `jing: bool = False` 参数：只有开关打开
     才落 `max_jing/jing` 两个键，关闭态创世事件逐字节不变。
   - 只在 `combat.jing` 打开时输出 `max_jing/jing`，关闭时与现状逐字节一致。
2. 创世 / 重生：`src/wsim/world/effects.py` 的 `create_entity`（`:318-327`）与
   `entity_respawn`（`:699-717`）在写 `max_hp/hp` 的同一位置写
   `jing = eff_jing = max_jing`（`chard.lpc:72-80`、`npcd.lpc:308-309`）；
   `max_jing==0` 时保持默认（旧内容包）。
3. 玩家建档：`src/wsim/actions/player.py` 的 `_player_vitals` 增加
   `jing=eff_jing=max_jing=100`（`logind.lpc:918-922`）。
4. 旧存档迁移：读档时字段缺失 → `max_jing=0, jing=0, eff_jing=-2`；开关打开且
   `max_jing==0 && max_hp>0` 时按 §6.3.1 回填一次，然后
   `jing=eff_jing=max_jing`；已有 `max_jing>0` **不覆盖**。与 `eff_hp` 的
   `scheduler.py:962` 归一化放在同一处（新增 `eff_jing < -1 → max_jing`）。
5. 事件/快照兼容：`max_jing==0` 的实体不产生任何新键、不发新事件；
   `vitals_set`（`effects.py:831`）需接受 `jing/max_jing/eff_jing`。

### 6.4 需要改的动作与判定点

| 位置 | 改动 | 依据 |
|---|---|---|
| `sim/combat_rules.py:517,551` | 奖励参数 `victim_jing/attacker_jing` 改传真实 jing | `dodge_rewards/parry_rewards/damage_rewards` 已按 jing% 计算 |
| `sim/combat_rules.py:633-635` | `death_state` 改传 `jing/eff_jing`，不再复用 hp | `char.lpc:65-78` |
| `sim/combat_rules.py` 伤害结算 | 增加 `channel="jing"` 分支（`apply_jing_wound` 或复用 `apply_qi_wound` 字段），默认 `qi` | `damage.lpc:30-33,56-60` |
| `actions/combat.py:624+` `AnsuanAction` | precondition 加 `jing < 100`；apply 加 `set_field jing -= 50` | `ansuan.lpc:133-134,227` |
| `actions/aggression.py:37-105` | wimpy 同时比较 `jing*100/max_jing` | `char.lpc:87-92` |
| `actions/system.py:514-578` `SystemHealAction` | 同一次心跳里恢复 `jing` 与 `eff_jing`；复用 `heal_credit/heal_cd` | `damage.lpc:360-367` |
| `actions/player.py:585,623-699` | `healing_ceiling` 增加 jing 版；`sleep` 写 `jing=eff_jing`；`rest` 同样封 `eff_jing` | `sleep.lpc:196-217` |
| `world/effects.py:553-588` | 新增 `jing_add/jing_set/eff_jing_set/eff_jing_add`，钳制与 hp 组一致 | `damage.lpc:78-101` |
| `world/effects.py:589` `heal_apply` | data 增加 `jing/eff_jing`；点名时同步 | §4.1 |
| `actions/system.py` 昏迷/苏醒 | 昏迷时 `jing=0`（与 `qi=0` 同事件）；苏醒不清 jing | `damage.lpc:137-138,246-290` |
| `api/session.py` | `_load_eff_qi`（`:1623-1625`）旁边新增 `_load_jing`；`vitals` 视图增加 `jing/max_jing/eff_jing`（仅开关开时） | 展示口径 |

### 6.5 开关

- 新增 **独立开关 `combat.jing`**，默认 **false**。
- 依赖：`combat.jing: true` 要求 `combat.eff_qi: true`；否则启动时报配置错误并
  按 false 处理（jing 的死亡/昏迷语义依附 eff_qi 结算）。
- 关闭时：不解析 `max_jing`、不写字段、不发事件、不改 ansuan/sleep/wimpy、
  不改变任何旧哈希。
- 不建议与 `eff_qi` 合并成一个开关：`eff_qi` 已经在 xkx 基线中开启，合并会让
  现有战报与哈希一次性跳变，无法分段验收。

---

## 7. 验收口径

### 7.1 必须保持不变的基线（开关关闭）

- 旧九地点（`RunConfig(days=2, seed=42, npc_limit=0)`）连跑两次，必须仍为：
  ```
  events=735
  state_final=009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9
  event_final=02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae
  business_cash=11442, blocked=6, pruned=3
  ```
- 现有 golden / `to_dict` 快照、`tests/test_combat_eff_qi.py`、
  `tests/test_xkx_npc_aggression.py` 全绿。

### 7.2 需要新增的原始数据（开关打开）

| # | 场景 | seed | 预期原始值 |
|---|---|---|---|
| J1 | 玩家建档 | 42 | `jing=100, max_jing=100, eff_jing=100` |
| J2 | `laohu` / `wolfdog` genesis | 42 | `max_jing=212`（425/2）/ `25`（50/2） |
| J3 | 带 `vitals.max_jing` 的人类 NPC | 42 | 取内容值，不套 212 类下限 |
| J4 | ansuan 门槛 | 42 | `jing=99` → `rejected:cannot_focus`；`jing=100` → ok，事后 `jing=50` |
| J5 | sleep（正常） | 42 | `jing=10, eff_jing=80, max_jing=100` → 醒来 `jing=80`（不回 100） |
| J6 | sleep（睡太多） | 42 | `mud_age-last_slp<120` 且 `max_jing>160` → `jing += eff_jing/2 - jing/2` |
| J7 | heal_up | 42 | `con=20, jing=0, eff_jing=80, max_jing=100`：每次触发 `jing += 6`，封顶 80 后 `eff_jing+1` |
| J8 | `receive_wound("jing")` 致死 | 42 | `eff_jing` 0 → -1 → `dead`（qi 仍满） |
| J9 | `receive_damage("jing")` 昏迷 | 42 | `jing` 0 → -1 → `unconcious`，事件里 `jing=0, qi=0` |
| J10 | wimpy | 42 | `jing=10% max_jing`、qi 满 → 触发 flee（原版只看 qi 时不触发）|
| J11 | 两连跑确定性 | 42 | 同一 run 两次所有 `to_dict/event` 哈希一致 |

### 7.3 需要重新基线（开关打开后）

- xkx 世界（seed `20261014`）开启 `combat.jing` 后的 `state_final/event_final`
  必须**重新记录**，不要求等于旧值；验收只看：
  1. 新增键只出现在有 `max_jing>0` 的实体；
  2. 关闭开关时与旧值逐字节一致；
  3. 同一 seed 两次跑结果一致。
- `events` 数量允许因 `system_heal` 明细增加而变化，但必须记录前后差值并归类到
  `heal_apply` / `combat` / `status`。

### 7.4 未取证 / 不在本期

- 走兽以外 race（飞禽/爬虫/鱼类）的 `max_jing` 公式：`beast.lpc` 只给走兽；
  其余原版落 `hp_status`，而 `hp_status` 源不在仓库 → 未取证，按 `max_qi/2` 处理。
- 玩家 age 增长与门派/心法对 `max_jing` 的加成（`updated.lpc:141-160`）：本期不建模。
- `max_jingli` / `jingli`：本期不建模，`heal_up` 中该项按 0。
- jing 伤害技能（qishang-quan 等）：当前内容包没有这些技能，本期只固定通道语义。
- `sleep` 的 `last_slp`/`mud_age` 差分：我方没有 mud_age，先用「总是正常睡眠」分支，
  睡太多分支记为后续项（J6 仅作为参考，不列入本期验收）。

---

## 附：取证命令

```bash
cd world-sim/tools/xkx_ref/src/xkx100
grep -rn 'query("jing"\|add("jing"\|set("jing"\|eff_jing\|max_jing' cmds/
grep -rn 'query("jing"\|add("jing"\|set("jing"\|eff_jing\|receive_damage("jing"\|receive_wound("jing"' kungfu/skill
grep -rn 'jing' feature/damage.lpc inherit/char/char.lpc adm/daemons/{chard,npcd,updated,logind}.lpc \
  adm/daemons/race/{human,beast}.lpc cmds/std/{ansuan,sleep}.lpc
```
