# 战斗节奏与出手权 —— 原版取证、差异与 S1 设计

- **状态**：S0 已批准并落地（报告分列，禁止相减）；**S1 未批准、未实现**；S2 搁置。
- **取证环境**：原版 4004（launchd，`tools/xkx_ref/run/mudlib`）；采样实例 4010（同一驱动与 mudlib 副本，MID 快照）。
- **红线**：`src/wsim/sim/combat_rules.py` 与 `tools/xkx_formula` 零改动；不 force；显式 `git add`。

## 0. 结论（先看这里）

1. **出手权**：`fight()` 每个 `heart_beat` 对每个 living 调用一次，双方各自结算；没有 dex / `combat_speed` / `jiali` / `force` 的先手排序。谁先手只取决于心跳调度。
2. **三个分支**（`combatd.lpc:742 / 763 / 783`）：victim busy 或 unconscious → 快攻；否则 `random(victim.cps*3) > me.cor` → 普通攻击；否则守卫。守卫分支只是**本回合** `return`，没有 `continue`；下一心跳重新掷。因为走兽 `cps=0` 且 `random(0)=0`，重掷结果恒定，才表现为“整场不出手”。
3. **运行期实测**：杭州老虎 `cps=0/cor=0`，郭庄大狼狗 `cps=0/cor=30`，`random(0)` 连续 50 次全为 0。玩家对走兽的**主动**普通攻击被确定性挡住。
4. **但“走兽不可击杀”是错的**：`combatd.lpc:692-718` 的**反击（riposte）是第二条出手通道**——攻击方普通攻击造成 0 伤害、且防守方处于 `guarding` 时，防守方**无条件**获得一次出手；`random(me.cps)<5` 只决定提示语和 `TYPE_QUICK`/`TYPE_RIPOSTE`，不决定是否反击。狼狗 `cps=0` → `random(0)=0<5` 恒真，反击照常发生。另有 `:742` 的 unconscious 快攻通道。
5. **实测反证**：MID vs 郭庄大狼狗 10 场普通 `fight`，玩家出手 7–19 次，**逐场与 riposte 计数完全相等**（全部来自反击）；狼狗出手 7–24 次、命中 0–2 次。改用 `kill` 后 **10/10 击杀、10/10 掉狗皮**；玩家出手 = 反击数 + 1，多出的 1 次是目标昏迷后的最后一击（`:743` 的 `!living(victim)` 快攻）。
6. **老虎 0 出手不是“走兽免疫”**：老虎 `apply/attack=20000` 被 `skill_power` 封顶后 ap≈2.1e7，55/55 全中，从不给反击窗口；`chat_chance_combat=0`，也不产生 busy 窗口。所以 5.50 是**场均老虎出手数**（10 场合计 55），不是“每回合 10 次”。
7. **被打中不取消出手**：`is_busy` 只由主动动作 `start_busy` 设置，挨打不置 busy；受击只在目标 busy 时调用 `interrupt_me`（`:664`），可能**提前解除** busy，而不是取消其攻击。复刻“双方独立结算”本身没错，缺的是三分支与反击。
8. **伤害与 ap/dp 无偏差**：原版 2–19 刀打空 MID；复刻 MID 7.40 轮，同量级。81 回合来自临时 `2,000,000 exp / skills 500` 验收账号，不是默认档（见 `reports/xkx-ref/win-loss.md` 的 `corrections` C1）。

## 1. 原版源码事实

### 1.1 `fight()` 逐字（`adm/daemons/combatd.lpc:728-801`）

```c
void fight(object me, object victim) {
  string result;

  // 比武现场转播 start
  player = users();
  // 比武现场转播 end

  if (!living(me) || me->query_temp("noliving")) return;

  //	if( !me->visible(victim)
  //	&& (random(100 + (int)me->query_skill("perception")) < 100) )
  //		return;

  // If victim is busy or unconcious, always take the chance to make an attack.
  if (victim->is_busy() ||
    !living(victim) || victim->query_temp("noliving")) {
    me->set_temp("guarding", 0);
    if (!victim->is_fighting(me)) victim->fight_ob(me);
    do_attack(me, victim, me->query_temp("weapon"), TYPE_QUICK);

    if (me->is_fighting(victim) && victim->is_fighting(me))
      if ((!objectp(me->query_temp("weapon")) && sizeof(me->query_skill_prepare()) > 1) ||     //空手有prepare则双击
      //			(objectp(me->query_temp("weapon")) && (me->query_temp("weapon"))->query("skill_type")=="sword" && me->query_skill("bixie-jian", 1) >= 60 && me->query_skill_mapped("sword") == "bixie-jian") || //辟邪60级以上双击
      // 把bixie-jian双击做到 /kungfu/skill/bixie-jian.c 中间去
      (me->query("double_attack") && !me->query_temp("weapon")))       //互搏空手无prepare双击
      {
        if (me->query("double_attack") &&
          sizeof(me->query_skill_prepare()) < 2)
          message_combatd(CYN "\n$N双手分使，灵活异常，好象变成了两个人似的！" NOR, me);
        me->set_temp("action_flag", 1);
        do_attack(me, victim, me->query_temp("weapon"), TYPE_QUICK);
        me->set_temp("action_flag", 0);
      }

    // Else, see if we are brave enough to make an aggressive action.
  } else if (random((int)victim->query_cps() * 3) > (int)me->query_cor()) {
    me->set_temp("guarding", 0);
    if (!victim->is_fighting(me)) victim->fight_ob(me);
    do_attack(me, victim, me->query_temp("weapon"), TYPE_REGULAR);

    if (me->is_fighting(victim) && victim->is_fighting(me))
      if ((!objectp(me->query_temp("weapon")) && sizeof(me->query_skill_prepare()) > 1) ||     //空手有prepare则双击
      //			(objectp(me->query_temp("weapon")) && (me->query_temp("weapon"))->query("skill_type")=="sword" && me->query_skill("bixie-jian", 1) >= 60 && me->query_skill_mapped("sword") == "bixie-jian") || //辟邪60级以上双击
      // bixie双击做到bixie-jian.c
      (me->query("double_attack") && !me->query_temp("weapon")))       //互搏空手无prepare双击
      {
        if (me->query("double_attack") &&
          sizeof(me->query_skill_prepare()) < 2)
          message_combatd(CYN "\n$N双手分使，灵活异常，好象变成了两个人似的！" NOR, me);
        me->set_temp("action_flag", 1);
        do_attack(me, victim, me->query_temp("weapon"), TYPE_REGULAR);
        me->set_temp("action_flag", 0);
      }

    // Else, we just start guarding.
  } else if (!me->query_temp("guarding")) {
    me->set_temp("guarding", 1);
    message_vision(guard_msg[random(sizeof(guard_msg))], me, victim);
    //		message_combatd(guard_msg[random(sizeof(guard_msg))], me,victim);
    // 比武现场转播 start
    result = guard_msg[random(sizeof(guard_msg))];
    result = replace_string(result, "$P", me->name());
    result = replace_string(result, "$N", me->name());
    result = replace_string(result, "$n", victim->name());
    result = replace_string(result, "$p", victim->name());
    if ((string)environment(me)->query("short") == "擂台")
      for (userno = 0; userno < sizeof(player); userno++)
        if ((player[userno])->query_temp("view_leitai") &&
          (string)environment(player[userno])->query("short") == "武道场")
          tell_object(player[userno], result);
    // 比武现场转播 end
    return;
  } else return;
}
```

### 1.2 三个分支的语义

| 分支 | 位置 | 条件 | 动作 | 备注 |
|---|---|---|---|---|
| 快攻 | `:742-760` | `victim->is_busy()` 或 `!living(victim)` 或 `noliving` | `do_attack(..., TYPE_QUICK)` | 目标昏迷/忙碌时**无门**；之后按条件双击 |
| 普通 | `:763-780` | `random(victim.cps*3) > me.cor` | `do_attack(..., TYPE_REGULAR)` | 唯一的 cps/cor 出手门；之后按条件双击 |
| 守卫 | `:783-800` | 以上都不成立 | 首次置 `guarding=1` 并打一条消息；已守卫则静默 | **只 `return` 本心跳**，下一心跳重掷 |

- `me` 是攻击方，`victim` 是被攻击方；门读的是 **victim 的 cps** 与 **me 的 cor**。
- 守卫分支首次进入时会消耗**两次** `random(sizeof(guard_msg))`（`:785` 和 `:788`），第二次只用于擂台转播；复刻若要对齐随机序列，必须保留这两次抽取。
- 攻击分支会 `if (!victim->is_fighting(me)) victim->fight_ob(me)`，所以被攻击方即使原本没参战也会被拉进战斗。
- 双击条件与 `double_attack`/`prepare` 有关，与本议题无关，但会影响出手计数。

### 1.3 反击（riposte）逐字（`combatd.lpc:691-718`）

```c
  // See if the victim can make a riposte.
  if (attack_type == TYPE_REGULAR && damage < 1 &&
    victim->query_temp("guarding")) {
    victim->set_temp("guarding", 0);
    if (random(me->query_cps()) < 5) {
      //			message_vision("$N一击不中，露出了破绽！\n", me);
      message_combatd("$N一击不中，露出了破绽！\n", me);
      // 比武现场转播 start
      //			if ((string)environment(me)->query("short") == "擂台")
      //			for (userno=0; userno<sizeof(player); userno++)
      //				if ((player[userno])->query_temp("view_leitai")&&
      //             (string)environment(player[userno])->query("short") == "武道场")
      //				tell_object(player[userno], me->name()+"一击不中，露出了破绽！\n");
      // 比武现场转播 end
      do_attack(victim, me, victim->query_temp("weapon"), TYPE_QUICK);
    } else {
      //			message_vision("$N见$n攻击失误，趁机发动攻击！\n", victim, me);
      message_combatd("$N见$n攻击失误，趁机发动攻击！\n", victim, me);
      // 比武现场转播 start
      //			if ((string)environment(me)->query("short") == "擂台")
      //			for (userno=0; userno<sizeof(player); userno++)
      //				if ((player[userno])->query_temp("view_leitai")&&
      //             (string)environment(player[userno])->query("short") == "武道场")
      //				tell_object(player[userno], me->name()+"见"+victim->name()+"攻击失误，趁机发动攻击！\n");
      // 比武现场转播 end
      do_attack(victim, me, victim->query_temp("weapon"), TYPE_RIPOSTE);
    }
  }
```

- **两个分支都调用 `do_attack(victim, me, ...)`**。`random(me->query_cps()) < 5` 只切换提示语与 `TYPE_QUICK`/`TYPE_RIPOSTE`，**不决定是否反击**。
- 触发条件三项：`attack_type == TYPE_REGULAR`、`damage < 1`、`victim->query_temp("guarding")`。玩家被走兽攻击时若在守卫，走兽一旦未造成伤害，玩家立刻获得一次出手。
- 走兽 `cps=0` → `random(0)=0 < 5` 恒真，走“露出了破绽”分支，仍是 `do_attack(victim, me, ..., TYPE_QUICK)`。
- 这是“玩家 0 主动出手却能打死狼狗”的全部解释；也是“门很窄、但狩猎没死”的关键。

### 1.4 busy / unconscious

- `feature/action.lpc:28`：`nomask int is_busy() { return busy != 0; }`。
- `busy` 只能由主动动作 `start_busy()` 写入；**挨打不会置 busy**。受击时 `:664` 的 `interrupt_me` 只在目标原本 busy 时调用，且可能因 `busy < interrupt` 直接把 busy 清零（提前解除），不是“打断出手”。
- `do_attack` 在目标 busy 时把 dp/pp ÷3（`combatd.lpc:333-343` 一段），这是“快攻更容易命中”的来源。
- `!living(victim)` / `noliving` 走同一快攻分支；昏迷目标因此可被继续攻击，这也是击杀的最后一下。
- 狼狗 `chat_msg_combat` 里虽然有 `random_move`（`wolfdog.lpc:16-20`），但运行期实测 `chat_chance_combat=0`，该聊天不会触发，故本组测试里 busy 分支没有参与；`go.lpc:75` 的“逃跑失败 → `start_busy(1)`”属于 S2。

### 1.5 计数口径

- 每个心跳每个 living 至多一次 `fight()`；每次 `fight()` 至多一次 `do_attack`（满足双击条件时再一次）。
- 报告中的原版 `attack_rounds = max(我方出手, 对方出手)`；玩家主动 0 出手时，它等于对方出手数。5.50 是 10 场 `attack_rounds` 的平均，即**场均老虎出手 5.5 次**，不是“每回合 10 次”，也不是“双方交换 5.5 轮”。
- 复刻的 `rounds_used` 是“双方各获得一次行动机会的交换轮数”。两者单位不同，S0 已要求分列。

### 1.6 运行期取值（原版驱动，非源码推断）

方法：在 `run/mudlib` 与 `run/mudlib-sample` 各放一个临时只读命令 `/cmds/usr/xkxprobe.c`（两份 `run/` 均在 `.gitignore` 内）；4004 用 launchd 重启以刷新命令缓存，**不带 `--reset`**，存档未动；取样后删除探针。源码树未改。

| 对象（实例） | 端口/账号 | race | `cps` 原始 | `cor` 原始 | `query_cps` | `query_cor` | exp | busy | chat_combat |
|---|---|---|---|---|---|---|---|---|---|
| 杭州老虎 `laohu#605` | 4004 / xkxref | 走兽 | 0 | 0 | 0 | 0 | 30000 | 0 | 0 |
| 郭庄大狼狗 `wolfdog#604` | 4004 / xkxref | 走兽 | 0 | 30 | 0 | 30 | 1000 | 0 | 0 |
| 郭庄大狼狗 `wolfdog#603` | 4004 / xkxref | 走兽 | 0 | 30 | 0 | 30 | 1000 | 0 | 0 |
| 店小二 `xiaoer2#607` | 4004 / xkxref | 人类 | 15 | 12 | 15 | 12 | 100 | 0 | 0 |
| MID 玩家 `/clone/user/user#236` | 4010 / mid | 人类 | 23 | 24 | 23 | 24 | 6400 | 0 | 0 |
| 观测玩家 `/clone/user/user#597` | 4004 / xkxref | 人类 | 23 | 24 | 23 | 24 | 9 | 0 | 0 |

- `random(0)` 在 4004 与 4010 各采样 50 次：`nonzero=0, min=0, max=0`（两处一致）。这不是“源码里没写所以为 0”的推论。
- 店小二/人类 NPC 的 cps/cor 是创建时 `10 + random(21)` 掷出的；多次实例实测到 `15/12`、`13/10`、`16/10`、`22/16`、`28/13`、`30/30`，符合 `human.lpc:67-68`。
- 走兽的 cps/cor 来源：`adm/daemons/race/beast.lpc:57-67` 只设 `str/int/per/con/dex/kar`，**不设 cps/cor**；狼狗只额外 `set("cor", 30)`（`d/hangzhou/npc/wolfdog.lpc:13`），不设 cps → 0。
- `beast1.lpc:102-103` / `beast2.lpc:84-85` 的默认值是 `cps=random(21)+5`、`cor=random(41)+5`；走兽（`beast.lpc`）不适用。
- `query_cps/query_cor` 定义在 `feature/attribute.lpc:92-99`：`query("cps") + query_temp("apply/composure")`、`query("cor") + query_temp("apply/courage")`；本组对象的两项临时加值均为 0。

### 1.7 实测反证 A：MID vs 郭庄大狼狗 10 场（普通 `fight`）

口径：登录在 `/d/hangzhou/dadao1` 完成（无怪），`go west` 进入郭庄后才开始记录；每场独立重启 4010 并覆盖 MID 快照。`fight` 是**非致命**切磋：`cmds/std/fight.lpc` 只对目标调用 `kill_ob`，玩家侧是 `fight_ob`，所以打不死（见 §1.8 的 `kill` 对照）。

| # | 结果 | 玩家出手/命中 | 狼狗出手/命中 | 狼狗累计掉血(区间中值) | riposte 计数 | 守卫回合(玩家/狼狗) | attack_rounds |
|---|---|---|---|---|---|---|---|
| 1 | 未分胜负 | 15 / 9 | 22 / 2 | 160.5 | 15 | 13 / 10 | 22 |
| 2 | 未分胜负 | 9 / 7 | 10 / 1 | 146.5 | 9 | 8 / 6 | 10 |
| 3 | 未分胜负 | 8 / 6 | 10 / 0 | 147.0 | 8 | 8 / 8 | 10 |
| 4 | 未分胜负 | 7 / 7 | 7 / 0 | 161.5 | 7 | 8 / 5 | 7 |
| 5 | 未分胜负 | 14 / 9 | 17 / 3 | 175.5 | 14 | 12 / 6 | 17 |
| 6 | 未分胜负 | 19 / 14 | 24 / 0 | 248.0 | 19 | 16 / 13 | 24 |
| 7 | 未分胜负 | 9 / 7 | 12 / 0 | 161.5 | 9 | 9 / 5 | 12 |
| 8 | 未分胜负 | 12 / 10 | 13 / 0 | 205.0 | 12 | 11 / 5 | 13 |
| 9 | 未分胜负 | 9 / 9 | 12 / 1 | 175.5 | 9 | 10 / 5 | 12 |
| 10 | 未分胜负 | 12 / 9 | 14 / 1 | 190.5 | 12 | 11 / 8 | 14 |

- **每一场的玩家出手数都等于 riposte 计数**（15=15、9=9、…、12=12）。玩家没有一次通过 `:763` 的主动门出手。
- 玩家守卫回合 3–16 次，狼狗 5–13 次，符合“双方每心跳各自掷门”。
- 狼狗命中 0–2 次、玩家闪避/招架高，正是它给玩家反击窗口的原因。
- 狼狗 `max_qi=50`，累计伤害 146–248 远超其血量：它被打到 `living=0`（昏迷），但 `fight`
  是**非致命**切磋，不会 `die()`，所以 10 场都停在“未分胜负”。致命结果见 §1.8 的 `kill` 对照。

### 1.8 实测反证 B：同一组改用 `kill`（致命，10 场）

| # | 结果 | 玩家出手/命中 | 狼狗出手/命中 | riposte | 伤害估算（区间中值） | attack_rounds | 狗皮 |
|---|---|---|---|---|---|---|---|
| 1 | 击杀 | 8 / 7 | 8 / 0 | 7 | 113 | 8 | 有 |
| 2 | 击杀 | 7 / 7 | 8 / 0 | 6 | 143 | 8 | 有 |
| 3 | 击杀 | 10 / 8 | 11 / 0 | 9 | 142 | 11 | 有 |
| 4 | 击杀 | 5 / 5 | 5 / 0 | 4 | 85 | 5 | 有 |
| 5 | 击杀 | 10 / 8 | 12 / 0 | 9 | 127 | 12 | 有 |
| 6 | 击杀 | 7 / 6 | 10 / 1 | 6 | 99 | 10 | 有 |
| 7 | 击杀 | 5 / 4 | 7 / 0 | 4 | 86 | 7 | 有 |
| 8 | 击杀 | 7 / 7 | 10 / 1 | 6 | 128 | 10 | 有 |
| 9 | 击杀 | 13 / 9 | 17 / 1 | 12 | 156 | 17 | 有 |
| 10 | 击杀 | 12 / 9 | 13 / 0 | 11 | 171 | 13 | 有 |

- **10/10 击杀，10/10 掉狗皮**（`look` 原文含 `狗皮(Gou pi)`）。
- 玩家出手 = riposte + 1。多出的 1 次是目标昏迷后的最后一击：狼狗 `max_qi=50`，被打到 `living=0` 后，玩家下一次 `fight()` 走 `:742` 的 `!living(victim)` 快攻。
- 注意 `goupi.lpc:10` 的 id 是 `"gou pi","pi"`，不是 `goupi`；`get gou pi` 才能捡。

### 1.9 人的对照：MID vs 店小二（20 场，沿用已落盘样本）

- 玩家出手 11–23 次（均值 17.0），店小二 7–18 次（均值 12.55）。
- 守卫消息的首回合计数：玩家 154 次、店小二 80 次；以“守卫首回合 / (出手 + 守卫首回合)”作**下界**，玩家约 31%、店小二约 24%（连续守卫时 `:800` 静默，实际不出手比例更高）。
- 双方都持续出手 → 这道门在人对人时是**偶发不出手**，不是恒定不出手。与 `human.lpc:67-68` 的 `10+random(21)` 一致。

### 1.10 原版玩家如何拿到狗皮

- 不需要任务或购买：`wolfdog.lpc:38-41` 的 `die()` 直接 `new(__DIR__ "obj/goupi")`。
- 杀法不是“主动挥击”，而是：先被狼狗攻击并进入 `guarding`；狼狗命中率低（本组 0–3 次命中 / 7–24 次出手），每次未造成伤害都触发 `:692-718` 的反击；玩家靠反击把狼狗打到 0，再用昏迷快攻补最后一下。
- 因此 **“玩家对走兽主动 0 出手”与“玩家能杀走兽”同时成立**。之前 spec 里“走兽无法被普通攻击击杀”的推论已删除。

## 2. 差异清单（复刻 vs 原版）

| # | 机制 | 原版 | 复刻现状 | 影响 |
|---|---|---|---|---|
| 1 | cps/cor 属性 | `attribute.lpc:92-99`；人类 `10+random(21)`，beast1/2 各有默认，走兽 0 | 无 cps/cor 概念 | 无法表达出手门 |
| 2 | 主动出手门 | `fight():763` | 每轮双方各出手一次 | 玩家对老虎也挥击（但 0 命中） |
| 3 | 反击 riposte | `do_attack():692-718`，无条件反击 | 无 | 走兽的“可击杀性”完全缺失 |
| 4 | busy/昏迷快攻 | `fight():742` | 无（`VICTIM_BUSY=False`） | 少一条出手/补刀通道 |
| 5 | 守卫状态与消息 | `guarding` 临时标记；首次守卫打消息、两次随机取文案 | 无 | 节奏不可观察 |
| 6 | 双击 | `fight():748/768` | 无 | 出手计数上限偏低 |
| 7 | 计数单位 | `attack_rounds=max(出手)` | `rounds_used=交换轮` | S0 已分列，禁止相减 |
| 8 | 伤害/ap/dp | `skill_power`/`do_attack` | 复用 `tools/xkx_formula`，同量级 | 无需改数值（原版也打不动老虎） |

**收敛**：差异不是“某个系数偏了”，而是**行动决策模型缺了三条通道**（主动门、反击、busy/昏迷快攻）。只补主动门会把打猎闭环打死；必须三条一起补。

## 3. 修复设计

### S0（已批准，已落地）

- `tools/xkx_ref/measure_win_loss.py` 的结果表分列：原版 `attack_rounds`、复刻 `rounds_used`；逐场表头改为 `attack_rounds`；表下注明“单位不同、禁止相减”。
- 已重生成 `reports/xkx-ref/win-loss.md`；旧文与 `corrections` 保留。

### S1（已批准；实现中）

**目标**：把 `simulate_fight()` 从“双方每轮各出手一次”改为原版的三分支行动决策，并补上反击与昏迷补刀。**三件套必须一起上线**：`:763` 出手门 + `:783` guard 置位与 `:799` 返回 + `:692-718` 反击 + `:742` 昏迷快攻。只开门不补反击，会让狼狗/走兽不可击杀，打猎闭环立刻死。

#### S1a：cps/cor 数据（逐 race 取自源码，不自造默认表）

| race | cps | cor | 源码 / 复刻取值 |
|---|---|---|---|
| 人类 | `10 + random(21)` | `10 + random(21)`（仅当未定义或 0） | `adm/daemons/race/human.lpc:67-68`；复刻 NPC 由内容 `attrs_roll` 的 `cps/cor: [10,30]` 落成 `attrs`，等价于 `10+random(21)` |
| beast1 | `random(21) + 5` | `random(41) + 5` | `adm/daemons/race/beast1.lpc:102-103` |
| beast2 | `random(21) + 5` | `random(41) + 5` | `adm/daemons/race/beast2.lpc:84-85` |
| 走兽（beast） | **源码未设 → 0** | **源码未设 → 0** | `adm/daemons/race/beast.lpc:57-67` |
| 走兽特例：郭庄大狼狗 | 源码未设 → 0 | `set("cor", 30)` | `d/hangzhou/npc/wolfdog.lpc:13`；运行期实测 `cps=0 / cor=30` |
| 杭州老虎 | 源码未设 → 0 | 源码未设 → 0 | `d/hangzhou/npc/laohu.lpc:20-30`（只设 exp/apply/armor/jiali）；运行期实测 `0 / 0` |
| 复刻玩家 | 23（战斗层兜底） | 24（战斗层兜底） | 原版 `logind` 掷点不可确定性复刻；**不写入创世属性**（保证开关关闭时旧世界状态哈希不变），战斗层 `combat_rules.PLAYER_MID_CPS/COR` 按原版 MID 快照实测 `23/24` 兜底；仅 `initiative_gate` 打开时读取 |

- 复刻读取口径必须与 `feature/attribute.lpc:92-99` 一致：
  `cps = attrs["cps"] + temp_apply["apply/composure"]`，
  `cor = attrs["cor"] + temp_apply["apply/courage"]`；两个临时键缺省即 0，不省略。
- **禁止**用 `_ATTR_DEFAULTS` 的 10 兜底 cps/cor：走兽内容没有这两个键，必须 raw get 默认 **0**。
- 任何源码确实不掷的值都写 0，并在注释里注明“源码未设，运行期实测值 = X”。

#### S1b：每心跳三分支（`combatd.lpc:735-800`）

1. **快攻分支**（`:742-760`）：目标 `busy` 或 `!living`（昏迷/非活）→ 攻击方 `guarding=0`，`do_attack(TYPE_QUICK)`。
2. **主动分支**（`:763-780`）：否则掷门 `random(victim.cps*3) > me.cor`；为真 → `guarding=0`，`do_attack(TYPE_REGULAR)`。
3. **守卫分支**（`:783-800`）：门为假 → 若尚未 `guarding`，置 1、消耗两次 `random(sizeof(guard_msg))`（5 条文案，`:785`/`:788`）、`return`；若已 `guarding`，静默 `return`，**不取骰**。
- 原版没有 `continue`：每个 heart beat 每个 living 各跑一次 `fight()`，`return` 只结束本次心跳。复刻里“每个交换轮双方各跑一次决策”，因此一方守卫时该轮它就没有动作。
- 双击（`:748-760` / `:768-780`）在本复刻不成立：实体没有 `query("double_attack")`，也没有 `prepare` 多于一式；条件恒假，**不取骰**。不新增双击建模。

#### S1c：反击（`combatd.lpc:691-718`）

- 触发条件：`attack_type == TYPE_REGULAR && damage < 1 && victim.query_temp("guarding")`。
- 关键：`damage < 1` 包含**闪避**（`RESULT_DODGE`）和**招架**（`RESULT_PARRY`），不只是“命中但伤害 0”；因此反击必须在 dodge/parry 的提前返回之前判定。
- 动作：`victim.guarding = 0` → `random(me.cps)`（`me` = 未命中的攻击方）→ **无条件** `do_attack(victim, me, ..., ...)`：
  - `< 5` → `TYPE_QUICK`；`>= 5` → `TYPE_RIPOSTE`；两支都真的反击，只切换提示语/类型。
- 反击自身的 `do_attack` 不再递归触发反击（其 `attack_type != TYPE_REGULAR`）。
- `:719` 的 `double_attack` 是技能映射钩子，本复刻没有映射技能，不调用、不取骰。
- **可观测字段**（只在开关打开时写入，关闭时事件字段与旧基线逐字节一致）：
  - `combat_log.rounds[].actions[].attack_type`：`regular` / `quick` / `riposte` / `guard`。
  - `combat_log.rounds[].actions[].riposte`：布尔值，仅反击通道为 `true`。原版 `TYPE_QUICK` 同时用于反击（`random(me.cps)<5`）与昏迷补刀（`:742`），单看类型无法验收“玩家出手 = 反击数（`kill` 时 +1 补刀）”，所以显式登记来源。
- **战报渲染**：守卫动作不能按“击中伤害 0”显示。首次守卫显示 `guard_msg[guard_message]`（`combatd.lpc:25-31`，5 条），连续守卫静默（`:799-800`）；这两条文案都来自原版，不是新造提示。

#### S1d：随机取数顺序（先回源码核对，再锁测试）

**逐次核对结论**（源码行号为证）：

| # | 取数 | 何时发生 | 次数 | 源码 |
|---|---|---|---|---|
| 1 | `random(victim.cps*3)` | 每次主动分支判定 | `cps*3>0` 时 1 次；`cps=0` 时 **0 次** | `:763` |
| 2 | `random(sizeof(guard_msg))` | 首次进入守卫分支 | **2 次**（`:785` 显示文案 + `:788` 转播文案，即使不在擂台也照取） | `:785`、`:788` |
| 3 | 守卫分支但已 `guarding` | 静默 `return` | **0 次** | `:800` |
| 4 | 招式选择（`reset_action`/`query_action`） | 每次 `do_attack` 开头 | 按现有公式库/招式表 | `combatd.lpc:280-300`；复刻沿用既有 `select_*` |
| 5 | `random(ap+dp)` | 闪避判定 | 1 次 | `:354` |
| 6 | `random(ap+pp)` | 招架判定（未闪避时） | 1 次 | `:406` |
| 7 | 伤害 / 护甲 / 膂力 / 经验压制 / 伤势 / 奖励 | 命中后 | 按公式库既有顺序 | `:436-567`；`tools/xkx_formula/combat.py` |
| 8 | `random(me.cps)` | 触发反击时（`me` = 未命中方） | `cps>0` 时 1 次；`cps=0` 时 **0 次** | `:695` |
| 9 | 反击方的整套 `do_attack` 取数 | 反击结算 | 同 #4–#7 | `:705` / `:716` |
| 10 | 双击 | 条件恒假 | 0 次 | `:748`/`:768` |

**一次攻击心跳的完整顺序**：

```
gate (random(victim.cps*3))
  ├─ 守卫: 首次 → guardmsg×2, 结束
  │        再次 → 0 次, 结束
  └─ 出手: action selection → dodge → parry → damage/wound/rewards
              └─ 若 TYPE_REGULAR 且 damage<1 且对方 guarding:
                   riposte_roll = random(attacker.cps)
                   → 反击方整套 do_attack（快捷或反击类型）
```

- **昏迷补刀**：目标 `qi<0 && eff>=0` 时进入 `unconcious()`；它在**下一次自身心跳**必 `die()`（`char.lpc:72-77`）。两者之间攻击方至多获得**一次**快攻（`fight():742`），因此击杀样本的“玩家出手 = 反击 + 1”，且最多 +1。复刻照此实现：目标昏迷后，攻击方下一轮打一次 `TYPE_QUICK`，随后目标死亡。
- 未建模且不取骰的原版随机：`limbs[random(sizeof(limbs))]`（`:306`）、`reset_action` 内部的具体招式随机（复刻用既有招式表近似）。这两项必须在实现注释里登记为“已知偏差”，不得声称逐骰一致。
- **`random(0)` 语义**：LPC 驱动实测 `random(0)=0` 且不消耗随机流。复刻 `TraceRandom.below(n<=0)` 返回 0 **且不调用 `rng.randint`**；`draws` 计数只统计真实取骰。
- 测试（照 `test_xkx_growth.py` 的 `RecordingPRNG`）：
  1. `random(0)` 不取骰：同 seed 下 `victim.cps=0` 与 `victim.cps=15` 两条路径，底层随机流长度差 = 门判断的真实取数次数（0 vs 1/回合）。
  2. 守卫首次消耗 `guardmsg×2`，连续守卫 `0` 次。
  3. 固定 seed 的完整骰序快照：`gate → action → dodge → parry → damage → wound → reward → riposte_roll → 反击`。
  4. 反击在 dodge/parry 后也触发（不是只对“伤害 0 的命中”触发）。
- 开关关闭时不得改变既有骰序；现有 `test_prng_draw_order_is_locked` 必须保持原快照。

#### S1e：开关与回滚

- 配置键：`combat.initiative_gate`，**默认 false**（缺省/缺文件/解析失败都视为关闭）。
- `config/worlds/default.yaml` **不加键**；`config/worlds/xkx.yaml` 在验收通过后单独提交打开。
- 一个开关开启**整套三分支行动决策**（门 + guard + 反击 + 昏迷快攻），避免被误解为“只开 cps/cor 门”。
- 关闭开关即回到现有行为；`combat_rules.py` 的系数与 `tools/xkx_formula` 零改动。
- 复刻玩家 cps/cor 定值属于 `actions/player.py` 的创世属性，随开关无关；需要在实现说明里单独列出。

#### S1f：验收（真跑，贴原始行）

- 野兽：MID 打郭庄大狼狗 `kill` 10 场，10/10 击杀、10/10 狗皮；逐场给玩家出手/命中、其中 riposte、狼狗出手/命中、回合数；回合落在原版 7–24 区间。
- 走兽不可主动：同批样本玩家主动 `TYPE_REGULAR` 出手 = 0（补刀 `TYPE_QUICK` 除外），事件里能看到 `attack_type`。
- 人对人：MID 打店小二 20 场，守卫首回合/出手下界与原版 31%/24% 对照，差 > ±8pp 必须解释。
- 老虎：MID 打杭州老虎 10 场，仍 0 胜、回合接近原版 5.5。
- 活世界：8011 重启后（不 `--reset`）真实 HTTP 打一场狼狗，贴 `look`/战报/掉落。

### S2（搁置）

非战斗动作产生的 busy（如 `go.lpc:75` 逃跑失败 `start_busy(1)`）如何影响快攻。狼狗 `chat_chance_combat=0`，本议题不需要它；等 S1 落地后再评估。

### S3：回归与基线重定（批准后执行）

- 回归：6 个既有目标 + 老虎 + 狼狗各 10 场；判据：老虎玩家主动 0 次/0 胜；狼狗可击杀且狗皮可拿；人对人双方都有出手与守卫；公式库 diff 为空。
- 基线：`xkx` 健康世界（seed `20261014` / 1 日）的 `state_final` 旧值 `2265c85c…`。**实测更正（本轮）**：该世界 `fight` 与 `npc_aggression` 均为 0 条，开关开/关逐事件完全一致，`state_final 2265c85c…` / `event_final d0c4777d…` / 722 事件**不变**，因此**不需要迁移基线**。归因见 `reports/xkx-ref/s1-initiative-gate.md`：合成战斗世界里只有 `fight` 事件变化，`genesis` / `system_spawn` 逐字节一致，新增差异全部是 `attack_type` / `riposte` 字段与 guard/quick 动作流。若将来基线世界加入真实战斗，再按“新旧值 + 逐事件 diff”流程处理，仍不得悄悄改值。

### S4：`fight` vs `kill`（只给源码结论，不实现）

**结论：两者不是同一件事，我们现在的 `fight` 直接判死 + 掉皮是产品可见偏差。**

| 维度 | `fight`（切磋） | `kill`（致命） |
|---|---|---|
| 入口 | `cmds/std/fight.lpc:52-53`：双方 `fight_ob`；对动物 `:57` 只 `me->fight_ob(obj); obj->kill_ob(me)` | `cmds/std/kill.lpc:82-100`：主动方 `kill_ob`，NPC 回应 `kill_ob` |
| 是否真受伤 | `do_attack` 仍扣 `qi`，但 `combatd.lpc:665-685`：双方都未 `is_killing` 且目标 `qi*2<=max_qi` 时，双方 `remove_enemy` 并 `return 1`，切磋在“半血”结束 | `is_killing` 为真，不触发半血停战；继续扣 `qi`/`eff_qi` |
| 死亡入口 | 半血停战 → 不会走到 `char.lpc:65-78` 的 `die()`；目标保持 `living` | `char.lpc:65-78`：`eff_qi<0` → `die()`；`qi<0` → `unconcious()`，下一次自身心跳 `die()` |
| 掉落 | `die()` 才掉落；切磋不 `die()`，所以**无掉落** | 狼狗 `wolfdog.lpc:38-41` 的 `die()` 直接 `new obj/goupi` |
| 成长 | `damage_rewards` / `dodge_rewards` / `parry_rewards` 照常给 `combat_exp` 与 `improve_skill`，没有“切磋减半”分支 | 同左 |
| 对我们的要求 | 需新增“非致命/半血停战”模式，且 `fight` 不得掉落 | 现有 `KILLING=True` 行为才对应 `kill` |

- 复刻现状：`FightAction` 只有 `fight` 一个动词，`KILLING=True` 恒定，倒下即判死并由既有掉落逻辑掉皮 → 玩家切磋会把店主/狼狗打死。
- **本轮不实现**：S4 会改动 `fight` 的停战/死亡/掉落三处语义，等 S1 验收后再单独立项。验收 S1 时一律用 `kill` 口径，避免把 S4 的偏差算到 S1 头上。

## 4. 不做的事

- 不改 `combat_rules.py` 的任何系数，不改公式库。
- 不调伤害/护甲/技能数值来“让老虎可打”。
- 不自造种族默认表；不把 `p5-tiger` 的临时 `player_start` 样本当默认档。
- 不在 S1 验收通过前打开 `config/worlds/xkx.yaml` 的开关；不悄悄更新基线。
- 不实现 S4（`fight` 非致命/半血停战/不掉落）。

## 5. 证据留存

- 运行期探针：`run/mudlib/cmds/usr/xkxprobe.c`、`run/mudlib-sample/cmds/usr/xkxprobe.c`（临时，`run/` 已 gitignore；取证后删除）。
- 原始战斗日志（临时，不入库）：`/tmp/cleanwolf/fight*.txt`、`/tmp/killwolf/fight*.txt`；每场记录已汇总到 §1.7/§1.8。
- 复现口径：登录在 `dadao1`，`go west` 进入 `guozhuang` 后才开始记录；`fight` 与 `kill` 分开统计。
