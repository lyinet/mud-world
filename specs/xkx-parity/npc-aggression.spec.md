# xkx 复刻：NPC 主动攻击（世界自走）

## 实现进度

- 老虎（无条件）、毒蛇（看玩家是否携带 `xionghuang`）、大狼狗（`kar+per` 概率）
  三种语义都已在上一轮的 `feat(xkx): NPC 主动攻击` 提交（`efdbd5a`）里实现，
  并已用 8091/8096/8097+8098 的真实 run 验收；`e164708` 只做变基后的测试基线对齐。
  本 spec 描述的是已落地的完整实现，不是本轮新增范围。

## 原版语义（源码核实）

### 触发时机是“进入房间”，不是心跳轮询

- `feature/attack.lpc:245` `void init()`：MudOS 在“有对象被 move 到本对象环境”时调用。
  `init()` 只处理当时 `this_player()` 这一个进入者，并先排除：
  `is_fighting()` / 不在战斗状态 / 与进入者不同房间 / 进入者不是活人 / linkdead。
- `feature/attack.lpc:269-271`：`attitude == "aggressive"` 且进入者是 `userp` 时，
  `COMBAT_D->auto_fight(this_object(), ob, "aggressive")`。
- `adm/daemons/combatd.lpc:808` `auto_fight()`：防重调用 `looking_for_trouble`，
  然后 `call_out("start_aggressive", 0, me, obj)`——给受害者一个“下一拍前逃走”的窗口。
- `adm/daemons/combatd.lpc:900-915` `start_aggressive()`：再次校验仍在同房间/活人/非和平房，
  通过后 `me->kill_ob(obj)`。
- `feature/attack.lpc:69` `kill_ob()`：校验同房间/非和平房/未已在杀同一人；
  打印“看起来 X 想杀死你！”；把目标加入 `killer`；最后 `fight_ob(ob)`。
- `feature/attack.lpc:36-41` `fight_ob()`：`set_heart_beat(1)`。
- `inherit/char/char.lpc:57` `heart_beat()` → `feature/attack.lpc:228` `attack()`
  → 每个心跳出手一次。`clean_up_enemy()`（`feature/attack.lpc:112`）会把不同房间/
  非活人的敌人清掉。

**结论**：`init()` 是进入房间时触发一次；`kill_ob` 只是登记仇恨并开启心跳，
**不是立刻结算伤害**。第一刀发生在 NPC 的下一个心跳。玩家在心跳前离开房间，
`clean_up_enemy()` 会结束交战。

### 三个杭州 NPC

- 老虎 `d/hangzhou/npc/laohu.lpc:20` `attitude=aggressive`，没有自定义 `init()`，
  继承 `feature/attack.lpc`：无条件 auto_fight。
- 毒蛇 `d/hangzhou/npc/dushe.lpc:27-34`：`interactive(this_player())` 且
  `present("xionghuang", this_player())` 为空时，`call_out("kill_ob", 1, me)`；
  身上有雄黄则不触发。
- 大狼狗 `d/hangzhou/npc/wolfdog.lpc:29-36`：
  `random(ob->query_kar() + ob->query_per()) < 30` 时 `call_out("kill_ob", 1, ob)`。

> 内容切片中 `aggressive=true` 的 NPC 实际有 6 个（chang1/chang2/fish/li/qingming/
> tufei2）。它们都继承同一 `feature/attack.lpc:269` 分支，原版同样会 auto_fight。
> `dushe` / `wolfdog` 依赖自定义 `init()`，在切片里没有 `aggressive` 标记，
> 因此按 id 显式补规则。清兵/刀客/流氓/店小二等被动 NPC 不参与。

## 复刻设计

### 状态

在 `Entity` 上新增三个**仅 xkx 会写入**的字段：

- `combat_target`：当前交战对象 id（NPC 侧登记，对应原版 `enemy` / `killer`）。
- `combat_tick`：开始交战的 tick（审计用）。
- `combat_round`：已结算的战斗回合数（用于验收“第几回合开始掉血”）。

字段为空时不写进 `to_dict()`，旧九地点世界的状态序列化与哈希逐字节不变。

### 触发（对应 `init()`）

- `Session._perform` 在玩家 `move`/`enter`/`climb`/`jump`/`revive` 成功后比较前后位置：
  - 位置变化：对新房间内所有带主动攻击规则且未在交战的 NPC 提交一次
    `npc_aggression(mode=trigger, target=进入者)`。
  - `enter`（登录/建号）即使位置未变也按原版“对象进入房间”语义检查一次。
- **每个 NPC 在本次进入中只判定一次**；已在交战的 NPC 跳过（对应 `is_fighting()` 保护）。
- 顺序按 NPC entity id 排序，保证确定性。

### 规则

规则只从 xkx 内容包派生（旧内容包没有这些 id / `aggressive`，规则集为空）：

| NPC | kind | 原版依据 |
|---|---|---|
| `aggressive=true`（含 laohu） | `always` | `feature/attack.lpc:269` |
| `dushe` | `unless_item`（`xionghuang`） | `dushe.lpc:31` |
| `wolfdog` | `chance_kar_per`（阈值 30） | `wolfdog.lpc:33` |

概率取数严格走 `PRNG(seed, seq, "action:npc_aggression")` 的 `randint(0, kar+per-1)`，
与 LPC `random(kar+per)` 同区间；不使用墙钟/`random` 模块。判定先于战斗取数，
顺序由测试锁定。

### 交手与结束（对应心跳 / `clean_up_enemy`）

- `trigger` 成功：只写 `combat_target` / `combat_tick` / `combat_round=0`，
  本事件不造成伤害（对应 `kill_ob` 只开心跳）。
- ambient 每 tick 对每个仍在交战的 NPC 提交一次 `npc_aggression(mode=continue)`：
  - 用 `simulate_fight(..., max_rounds=1)` 结算一个心跳回合；
  - `combat_round += 1`，这也是回答“第几回合开始掉血”的计数。
  - 一个 tick 内同一 NPC 只会收到一条 continue 意图。
- 交战中 NPC 的定时 `move` 计划在调度器里跳过：原版交战期间心跳归 combat 所有，
  否则 NPC 会在同一 tick 游走到别的房间，被 `clean_up_enemy()` 提前收兵。
- 目标死亡/昏迷/离线/离开房间，或 NPC 死亡：写 `combat_target=""`（对应
  `clean_up_enemy()`）。玩家死亡走既有 `player_death` + 鬼门关路径。

### 旧世界隔离

- `aggression_rules(content)` 只识别 xkx 的 NPC id / `aggressive` 字段；
  旧 `content/*.yaml` 规则集为空。
- `Entity` 新字段空值不序列化。
- `NpcAggressionAction` 只在规则非空且玩家进入带规则房间时提交；旧世界不产生新事件。
- 验证方式：运行旧世界 golden/确定性测试并比对原有基线哈希，而不是口头断言。

## 验收要点

- 老虎：进入 `/d/hangzhou/shanlu6`（老虎房）不输入 `fight`，第 1 个心跳回合开始掉血。
- 毒蛇：不带雄黄进入 `/d/hangzhou/huanglongdong` 会被攻击；带雄黄进入不触发。
- 大狼狗：同 seed 双跑，`/d/hangzhou/guozhuang` 的判定与伤害完全一致。
- 旧九地点世界：原有 golden/确定性基线原样通过。

## 偷袭窗口与登记时机（2026-11-09 实测校正）

### 原版调用链（行号）

- `feature/attack.lpc:245-273 init()`：对象被 move 进房间时由 MudOS 同步调用；aggressive
  分支在 `:269-271` 调 `COMBAT_D->auto_fight(this_object(), ob, "aggressive")`。
- `adm/daemons/combatd.lpc:808-820 auto_fight()`：`:819 call_out("start_aggressive", 0, me, obj)`。
  即 **`kill_ob` 不在 move 指令内**，而是排到下一次 call_out 处理。
- `adm/daemons/combatd.lpc:900-914 start_aggressive()`：`:913 me->kill_ob(obj)`。
- 自定义 `init()` 的两只：`d/hangzhou/npc/wolfdog.lpc:29-36`、`d/hangzhou/npc/dushe.lpc:27-36`
  都是 `call_out("kill_ob", 1, ob)`，即 **1 个心跳后**才登记。
- `feature/attack.lpc:69` `kill_ob()`：把目标加进 killer；`fight_ob()` 在 `:35-66`，
  `:64-65 enemy += ({ ob })`。
- `cmds/std/touxi.lpc:18-19`：拒绝条件只有 `obj->is_fighting(me)`——**只看目标是否已把
  攻击者登记为敌人**，不看攻击者自己是否在战斗。
- `inherit/char/char.lpc:57-97 heart_beat()`：`:87-96` 只有在有敌人时才 `attack()`；
  即 `kill_ob` 之后第一刀落在 NPC 的下一个心跳。

### 复刻现状（修复前，行号）

- `src/wsim/api/session.py:3077`：move/enter 成功的**同一条指令内**调用
  `_trigger_npc_aggression`。
- `src/wsim/actions/aggression.py:231-237`（trigger 预检）、`:332`（原 immediate 分支）：
  命中时立即写 `combat_target` / `combat_tick` / `combat_round`。
- `src/wsim/actions/combat.py:583-592`：touxi 的拒绝条件 =
  `defender.combat_target == attacker.id` 或 attacker 在 `effective_enemies(defender)` 中。
- 规则与掷骰：`src/wsim/sim/aggression.py:72-110`（laohu=always，dushe=unless_item，
  wolfdog=chance_kar_per 阈值 30）、`:149`（`decide()`）。

### 可判定规则（修复前）

- **R1**：touxi 只针对**单个目标**判定；目标未把攻击者登记为敌人就允许，已登记就
  `already_fighting`。
- **R2**：进房触发是同步的：目标规则一旦 `triggered=true`，同一 tick 即写
  `combat_target`，之后任何 tick 的 touxi 都会被拒；`triggered=false` 的目标
  （概率未中、带雄黄、非主动、或同模板的另一个未触发 clone）仍可被偷袭。
- **R3**：不存在“同 tick move 后 touxi 可以”的窗口——复刻把窗口压成了 0；能否偷袭
  只取决于**该目标这次是否触发**，与 tick 先后无关。

### 2026-11-09 矛盾复现（seed=42，content_hash=56417288…）

狼狗（`/d/hangzhou/guozhuang` 同房两个 clone）：

- `seq 6 tick 0` canonical `wolfdog`：`chance_kar_per roll=0 total=40 threshold=30
  triggered=true` → 写 `combat_target=player:u_demo`。
- `seq 7 tick 0` `wolfdog@/d/hangzhou/guozhuang#2`：`roll=39 total=40 threshold=30
  triggered=false`。
- `seq 9 tick 6 touxi`：target = `…guozhuang#2`（未触发那只），outcome=ok，
  `opening={"branch":"triple","mode":"touxi"}`，三刀 17/20/0，目标 50→13。
- 结论：狼狗偷袭成功不是窗口成立，而是**打的是没触发的那只 clone**。

老虎（同房只有一只，`always`）：

- `seq 7 tick 6 move` → `seq 8 tick 6 npc_aggression`（triggered=true）→
  `seq 10 tick 12 touxi` = `rejected:already_fighting`。
- 与 R2 一致；对照原版 `call_out(...,0)`，下一次玩家指令前基本已登记，**该拒绝忠实**。

### 与原版的差异判定

**真偏差**：原版 `kill_ob` 至少延迟一个 call_out（aggressive 0 拍；wolfdog/dushe 1 心跳），
复刻在 move 指令内立即登记。后果：

- laohu 这类 `call_out(...,0)`：玩家下一条指令前基本已登记，行为等价；
- wolfdog/dushe 这类 `call_out("kill_ob", 1)`：原版存在 1 个心跳的偷袭窗口，复刻没有。

### 修法（已实施）

1. `Entity` 新增 `aggro_tick` / `aggro_target`（`aggro_tick=0` 表示无待生效）；
   `npc_aggression(mode=trigger)` 命中时只写
   `aggro_tick = intent.tick + delay`，暂不写 `combat_target`。`delay` 由规则携带
   （always=0，chance_kar_per/unless_item=1）。
2. ambient 每 tick 把 `state.tick >= aggro_tick` 的待生效条目转成
   `npc_aggression(mode=promote)`（写 `combat_target/combat_tick/combat_round`，
   多敌时补 `combat_enemies`）；未到 `aggro_tick` 直接跳过，不出手。
3. **`TouxiAction` 无需改动**：待生效期间 `combat_target` 未写、不在
   `effective_enemies` 中，`combat.py:587-592` 的现有判定自然放行——与原版
   `touxi.lpc:18-19` 只查 `obj->is_fighting(me)` 等价。
4. `to_dict()` 仅在 `aggro_tick` 非 0 时输出；旧世界规则为空，不受影响。
   转正时 `aggro_tick/aggro_target` 清零，不在健康实体上残留。

### 可判定规则（修复后，seed=42 实测）

- **R4**：`always`（laohu）`delay=0` → 进房同 tick 登记，同 tick 及之后 `touxi`
  一律 `already_fighting`。
- **R5**：`chance_kar_per`（wolfdog）/`unless_item`（dushe）掷中后挂起
  `aggro_tick = 进房 tick + 1`；**进房同 tick** 的 `touxi` 允许，
  下一 tick `promote` 后拒绝。掷不中/带雄黄的目标永远可偷袭。
- **R6**：规则按**单个目标**判定，同模板其他 clone 各自独立（一只登记不影响另一只）。

实测（`/tmp/touxi_window_after.json`）：

- A：wolfdog canonical，`trigger_tick=0`、`touxi_tick=0` → **ok**，
  `opening={"branch":"triple","mode":"touxi"}`，三刀 17/20/0，hp 50→13；
  终态 `combat_target=""`、`aggro_tick=1`。
- B：同目标 tick 1 先 `promote`（`combat_target=player:xkx_win`、`aggro_tick=0`）
  → `touxi` = `rejected:already_fighting`。
- C：tiger `trigger_tick=0`、`touxi_tick=0` → `rejected:already_fighting`
  （`combat_target=player:xkx_win`、`aggro_tick=0`）。

验收（`tests/test_xkx_npc_aggression.py` 三例）：wolfdog 挂起期 ok / promote 后拒；
laohu 无窗口；掷不中的 clone 始终可偷袭。
