# S4：fight / kill 分离验收记录

日期：2026-11-08　分支：`fix/xkx-room-parity`
活世界：`/private/tmp/xkx-play`（API 8041 / UI 8011，run `xkx-world`，**未 `--reset`、未重建**）

## 1. 原版语义（源码）

| 点 | 原版位置 | 内容 |
|---|---|---|
| 半血阈值 | `adm/daemons/combatd.lpc:667` | `victim->query("qi") * 2 <= victim->query("max_qi")` → 气血 ≤50% |
| 停战条件 | `combatd.lpc:665-667` | 仅当 `!me->is_killing(victim) && !victim->is_killing(me)`（双方都非 killer） |
| 停战动作 | `combatd.lpc:668-673` | `me->remove_enemy(victim); victim->remove_enemy(me);` + `winner_msg`，**不 `die()`、不掉落、不留尸体** |
| 昏迷 | `combatd.lpc:742-760` + `char.lpc` | `qi<0` → `unconcious()`，下一心跳 `die()`；killer 状态下继续快攻 |
| 经验 | `combatd.lpc` 攻击结算 | 切磋同样给 `combat_exp` / `improve_skill`，未做删减 |

关键：**狼狗是主动攻击型 NPC，进入房间即 `kill_ob`（killer）**，所以原版 `fight 大狼狗` 也会走到昏迷（§1.7），只有 `fight 店小二`（非 killer）才半血停战。本次实现按“双方都非 killer”动态判定，未把 `fight` 简单写成永远不致命。

## 2. 实现

- `combat_rules.simulate_fight(killing=True, spar=False)`：新增 `spar` 半血停战；默认参数路径 RNG/回合/事件逐字节不变。
- `combat_effects(spar=False, reason="fight")`：`killing = (not spar) or defender.combat_target == attacker.id`；非致命时跳过死亡/尸体/掉落/玩家死亡，保留经验与技能；`combat_log` 仅在切磋或 `kill` 时增加 `lethal/stopped`。
- `FightAction(spar=...)` + 新增 `KillAction`；`build_registry(lethal_fight=False)`：关闭时 `fight` 旧行为、无 `kill`；打开时 `fight` 切磋、`kill` 致命。
- `session.py` 读取 `combat.lethal_fight`（默认 false）；`xkx_shell.py` 新增 `kill` 命令，目标解析复用 `_match_fight_target`，停战文案“点到即止”。
- 开关关闭时 `kill` 未注册，仍被白名单拒绝（旧行为可回滚）。

## 3. 单测与回归

- `tests/test_fight_kill_split.py`：**7 passed**。覆盖：切磋 ≤50% 停战且存活/无掉落/给经验；面对 killer NPC 仍致命；`kill` 致死并 `loot_create`；开关关闭无 `kill` 且 `fight` 致命；开关打开 `fight` 切磋 + `kill` 注册；同 seed 双跑事件与状态哈希完全一致。
- 旧九地点：`tests/test_xkx_determinism_legacy.py` → `1 passed`，`735 / 009076f0… / 02d5f812…` 不变。
- `tests/test_combat*.py + test_xkx_room_reset + test_xkx_respawn_fields + test_attrs_roll`：**92 passed**。
- 全量 pytest：见 §6（含一次并发关系用例偶发失败，单独复跑通过，重跑全量确认）。

## 4. 活世界验收（老板库 8011，不 `--reset`）

### 4.1 `fight 店小二`（新账号 `s4spar`，spawn=楼外楼）

`seq 8127 / tick 5268 / player:s4spar / fight`：

```json
lethal false, stopped true, rounds 6, dead [],
hp {"/d/hangzhou/npc/xiaoer2": 100, "player:s4spar": 100}, winner "attacker"
```

战报原始行（节选）：

```
你提起拳头往店小二的要害捶去，造成18点伤害（气血200→182）。
...
你对准店小二的要害用力挥出一拳，造成23点伤害（气血123→100）。
共6回合，你赢了，店小二倒下了。
气血：你100 / 店小二 100。
点到即止，双方各自收手。
```

> T7 收尾时把停战文案从“你赢了，X倒下了”改为“你占上风，X拱手认输”
> （避免与“点到即止”自相矛盾）。上表是该次运行（`seq 8127`）的原始输出，
> 保留原文不改写。

停战后 `look`：`在场人物：店小二。` + 货架完整；`/api/player/state` 中店小二 `status:"alive"`, `presence:"online"`。
买卖：`buy 龙井虾仁` → `seq 8136 buy`，`你买下龙井虾仁×1，付出50文（含税）。余额250文。`，`i` 显示 `龙井虾仁 ×1`。**店小二仍活着、仍能做买卖。**

### 4.2 `kill 大狼狗`（`p5peek`，郭庄）

第一次 `kill`（seq 8139）目标因上一轮 B 验收遗留的 active combat 已为 0 气血，2 回合收尾，仍产生：

```json
{"op":"entity_status","data":{"entity":"...wolfdog@...#2","status":"dead","tick":5281}},
{"op":"loot_create","data":{"item":"/d/hangzhou/npc/obj/goupi","location":"/d/hangzhou/guozhuang","qty":1,"reason":"kill"}}
```

为排除 0 气血干扰，随后对**满血重生**的狼狗再 `kill` 一次（`seq 8160 / tick 5295`）：`20` 回合、`hp.wolfdog 0`、`overkill 1`、`lethal true`、`stopped false`，同样 `loot_create` 狗皮 `reason:"kill"`。`look`：

```
[npc] 在场人物：大狼狗。
[info] 地上散落：狗皮。
```

`loot 狗皮` → `seq 8152`，`你捡起：狗皮。`，`行囊：狗皮×2`。**kill 致命 + 掉皮成立。**

### 4.3 同 seed 双跑

`test_same_seed_double_run_is_identical` 对 `FightAction(spar=True)` 与 `KillAction` 各跑两次，比较 canonical event + `state_hash`，完全相等。

### 4.4 旧九地点哈希

`735 / 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9 / 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`，不变。

## 5. 写入审计（本轮新增）

| seq | tick | actor | verb | 说明 |
|---|---|---|---|---|
| 8127 | 5268 | player:s4spar | fight | 切磋店小二（停战） |
| 8136 | 5275 | player:s4spar | buy | 验证停战后买卖 |
| 8139 | 5281 | player:p5peek | kill | 收尾 0 气血遗留目标 |
| 8152 | 5288 | player:p5peek | loot | 拾取狗皮 |
| 8160 | 5295 | player:p5peek | kill | 满血干净击杀 |
| — | — | player:s4spar | player_enter | 新验收账号（楼外楼） |

无临时 NPC spawn；`s4spar` 为观察账号，`boss` 序列无新增写入。

## 6. 配置

`config/worlds/xkx.yaml` 验收后开启：

```yaml
combat:
  lethal_fight: true
```

单独提交，便于回滚（去掉该行即恢复旧的 `fight` 致命行为、`kill` 不可用）。

## 7. 遗留说明

- 部署副本 `xkx-play/config/worlds/xkx.yaml` 早已有 `combat.initiative_gate: true`，但仓库 `config/worlds/xkx.yaml` 之前没有该键（S1 的配置开启只存在于部署副本）。本次只按 S4 要求提交 `lethal_fight`，未顺手改 `initiative_gate`；是否补交 S1 配置由你决定。
- `p5peek` 在上一轮 B 验收后残留 `combat.active`（round 364、目标 0 气血），本轮第一次 `kill` 才将其收尾。已如实记录，非本轮代码引入。
