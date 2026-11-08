# 多人同屏战斗 · 真机与离线证据（2026-11-08）

> 实例：`127.0.0.1:8031`，run `multi-combat-qa`，`XKX_MULTI_COMBAT=1`。
> **未触碰 8011 / 8041 老板档**（8011 仍代理 8041 的 `/private/tmp/xkx-play`）。
> 启动命令：
>
> ```bash
> XKX_MULTI_COMBAT=1 PYTHONPATH=src python -m wsim.api \
>   --root /private/tmp/xkx-multi-combat-wt --run multi-combat-qa \
>   --host 127.0.0.1 --port 8031 --seed 20261014 --tick-step 6 --npcs 0 \
>   --spawn /d/hangzhou/jiulou --auth off \
>   --content-root /private/tmp/xkx-multi-combat-wt/content/xkx \
>   --world-config /private/tmp/xkx-multi-combat-wt/config/worlds/xkx.yaml
> ```
>
> 账号：A=`mcqa_a_20261108`（甲侠），B=`mcqa_b_20261108`（乙侠）。
> 地点：`/d/hangzhou/guozhuang`（郭庄，2 只大狼狗）。
> 两只实例 id：`/d/hangzhou/npc/wolfdog`（0 号）、`/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2`（2 号）。

## 1. 两人同打同一只大狼狗：两份战报

### 战报 1（0 号大狼狗）

| seq | tick | actor | target | 回合 | 结果 | 伤害 | 气血 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 145 | 136 | `player:mcqa_b_20261108` | `/d/hangzhou/npc/wolfdog` | 1 | 命中 | 22 | 39 → 17 |
| 145 | 136 | `/d/hangzhou/npc/wolfdog` | `player:mcqa_b_20261108` | 1 | 闪开 | 0 | 92 → 92 |
| 148 | 142 | `player:mcqa_a_20261108` | `/d/hangzhou/npc/wolfdog` | 1 | 命中（最后一击） | 21 | 17 → 0 |

- `seq=148` 的 `combat_log`：`rounds_used=1`、`winner="attacker"`、`dead=["/d/hangzhou/npc/wolfdog"]`。
- 同一事件内 `combat_contrib` 落值：`{"player:mcqa_a_20261108": 130, "player:mcqa_b_20261108": 70}`（实际扣气血累计，不参与选敌）。
- 同事件 `entity_status` 置 `dead`，`loot_create` 狗皮 ×1 到郭庄地面，`combat_enemies=[]`。
- **该事件没有任何对玩家的 `combat_exp_add`**；A 是最后一击，未获得击杀经验。

### 战报 2（2 号大狼狗）

| seq | tick | actor | target | 回合 | 结果 | 伤害 | 气血 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 287 | 160 | `player:mcqa_b_20261108` | `/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2` | 1 | 被闪开 | 0 | 3 → 3 |
| 287 | 160 | `/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2` | `player:mcqa_b_20261108` | 1 | 被闪开 | 0 | 92 → 92 |
| 290 | 166 | `player:mcqa_a_20261108` | `/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2` | 1 | 命中（最后一击） | 17 | 3 → 0 |

- `seq=290` 的 `combat_log`：`winner="attacker"`、`dead=["/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2"]`。
- 同事件有 `combat_exp_add {"entity": "/d/hangzhou/npc/wolfdog@...", "delta": 1}`：这是**逐招交手成长记在防御方 NPC 上**，不是给最后一击玩家的击杀奖励；A 的 `combat_exp` 仍为 6400。
- 同事件 `combat_enemies=[]`、`loot_create` 狗皮 ×1 到地面。

## 2. NPC 侧 `targets` 含两人

`seq=133`（tick 132，0 号狼狗心跳）：

```json
{"mode": "continue",
 "target": "player:mcqa_a_20261108",
 "targets": ["player:mcqa_a_20261108", "player:mcqa_b_20261108"]}
```

同一只狼狗在 tick 132/133/134/135/136 的 `continue` 均为双目标；`aggression_log` 中 `opponent_index` 逐 tick 可读（选敌骰最先，随后才是 `simulate_fight` 原有骰序）。

0 号狼狗死亡后由 `room_reset` 重生（`seq=151/152`，tick 148）：

```json
{"mode": "trigger", "target": "player:mcqa_a_20261108"}
{"mode": "trigger", "target": "player:mcqa_b_20261108"}
```

即 `trigger` 对房内两名玩家**逐个登记**，重生后的新实例 `combat_enemies` 长度为 2；旧实例死亡时已清空敌人表。重生后满血是**新实例**，不是旧实例回血。

## 3. 两人都能 loot

| seq | tick | actor | verb | 结果 |
| --- | --- | --- | --- | --- |
| 153 | 153 | `player:mcqa_a_20261108` | `loot` | 获得 `/d/hangzhou/npc/obj/goupi` ×1 |
| 175 | 175 | `player:mcqa_b_20261108` | `loot` | 获得 `/d/hangzhou/npc/obj/goupi` ×1 |

两具尸体各掉 1 张狗皮（原版 `wolfdog.lpc` 每次死亡掉 1 张）；共享地面先到先得。

## 4. 合杀经验归属实测（一行结论）

| 玩家 | 战斗前 `combat_exp` | 战斗后 `combat_exp` | 最后一击 | 击杀奖励 |
| --- | --- | --- | --- | --- |
| A `mcqa_a_20261108` | 6400 | **6400** | 两具狼狗均为 A | **0** |
| B `mcqa_b_20261108` | 6400 | 6401 | 无 | **0**（+1 来自 `seq=141` 逐招成长，不是击杀） |

- 原版判定：`feature/damage.lpc:230` 取 `last_damage_from` 作为 `killer`，`:253` 调 `killer_reward`；大狼狗 `die()` 自定义且不调 `::die()`，直接跳过 `killer_reward`（见 `kill-reward-matrix.md`）。**最后一击独占，不是分摊，也不是首个 `kill_ob`。**
- 复刻侧：死亡分支不写任何击杀 `combat_exp_add`；本次只新增回归测试锁死该行为。

## 5. 选敌分布（离线，同 seed，选敌骰独立流）

| 敌人数 | tick 数 | index 0 | index 1 | index 2 | index 3 | index 4 |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 400 | **321（80.2%）** | **79（19.8%）** | - | - | - |
| 4 | 400 | 103（25.8%） | 79（19.8%） | 108（27.0%） | 110（27.5%） | - |
| 5 | 400 | 104（26.0%） | 78（19.5%） | 108（27.0%） | 110（27.5%） | **0（永不被选）** |

- 采样为单一 seed（42）的确定性序列，不是随机抽样：seed 42 在 400 tick 内 `roll==1` 恰为 79 次（19.8%），落在测试容差 72–128 内但低于理论 25%。
- 横向对照：10 个 seed × 400 tick 汇总为 `{0:23.8%, 1:23.7%, 2:27.3%, 3:25.2%}`，回到均匀，说明上述偏差是单 seed 抖动而非实现偏置。
- 断言容差：2 人 index0 272–328、index1 72–128；4/5 人每个前 4 位 72–128。原版 `random(4)` 只有 0..3 四个面；5 人时第 5 个永远进不了窗口，真正回退只发生在敌人少于 4 个时（roll ≥ count → `targets[0]`）。

## 6. 关闭态逐字节一致

同一场景（老虎 trigger + 玩家 fight + NPC continue）在父提交 `1cbbcb3` 与实现分支各跑一次，关闭 `XKX_MULTI_COMBAT`：

```text
event_hash 0bcf5ec2181f9ade4929656d6dc017c9eda440055a712d31d3dba3be17eeeb39
state_hash d29d1b07f9fb307188bfa13ea7124975af21b62568d970a59e49cc7cedd8d304
diff: BYTE_IDENTICAL
```

关闭态零新增字段、零新增事件、零额外骰。

### 6.1 单人 1 敌：开关 off/on 逐项相同（路径 2 之后）

同 seed 42 打大狼狗 10 场，`XKX_MULTI_COMBAT=0/1` 各跑一次，回合数 / 双方命中 / 双方伤害 / 结束气血 / 胜负逐项比对：

| 场次 | 回合 | 玩家命中 | NPC 命中 | 玩家伤害 | NPC 伤害 | 玩家气血 | NPC 气血 | 结果 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 11 | 4 | 1 | 67 | 23 | 97 | 0 | dead |
| 2 | 4 | 3 | 1 | 56 | 24 | 96 | 0 | dead |
| 3 | 15 | 4 | 6 | 66 | 102 | 18 | 0 | dead |
| 4 | 6 | 4 | 2 | 63 | 40 | 80 | 0 | dead |
| 5 | 5 | 4 | 0 | 63 | 0 | 120 | 0 | dead |
| 6 | 8 | 3 | 0 | 51 | 0 | 120 | 0 | dead |
| 7 | 3 | 3 | 0 | 54 | 0 | 120 | 0 | dead |
| 8 | 6 | 3 | 1 | 55 | 16 | 104 | 0 | dead |
| 9 | 3 | 3 | 0 | 61 | 0 | 120 | 0 | dead |
| 10 | 4 | 3 | 0 | 50 | 0 | 120 | 0 | dead |

`diff off.txt on.txt` 仅 `MODE OFF` / `MODE ON` 一行。事件级 diff（12 条事件）：`fight` 事件 6 条逐字节相同；`npc_aggression` 事件 6 条仅多 `opponent_index` / `opponent_roll` / `pruned` / `targets`，trigger 事件另多一条 `combat_enemies` set_field，继续事件在伤害后多 `combat_contrib` 记账；其余字段与全部战斗骰取值一致。

## 7. 回归与红线

- `tests/test_xkx_determinism_legacy.py`：735 事件 + `state_final=009076f0…`、`event_final=02d5f812…` 不变。
- 全量：`1055 passed, 20 skipped`（junit 统计：1055 tests / 0 failures / 0 errors / 20 skipped）。
- `git diff 1cbbcb3 -- content/xkx room_reset.py src/wsim/respawn.py src/wsim/sim/combat_rules.py tools/xkx_formula`：空。
- 4004 被动还手 5/5 证据见 `multi-combat.spec.md` §4.3。

## 8. 真机验收

### 8.1 活世界 8041/8011：`world: existing`，不 reset

- 重启命令：`scripts/start_world.sh --world xkx`（无 `--reset`）。
- 启动 JSON：`"world": "existing"`、`"multi_combat": true`；tick 从 6409 续跑到 6415，未重建。
- 世界 ID / 事件 seq 连续，无 genesis 重放。

单人冒烟（`player:smoke_v1`，郭庄）：

- `look`：`在场人物：二只大狼狗。地上散落：狗皮×7、粗布、炊饼×3。`
- `fight 大狼狗`：11 回合，大狼狗气血 50→17，“拱手认输”“点到即止，双方各自收手”。
- `kill 大狼狗 2`（另一只）：7 回合，气血 32→0，“你赢了，大狼狗倒下了”，`超额伤害：大狼狗 1 点`。
- `loot 狗皮`：`捡起：狗皮。` 行囊 `狗皮×9`，地面只剩 `粗布、炊饼×3`。
- 事件行：`player:smoke_v1` 的 `fight`/`kill`/`loot` 事件带 `combat_enemies` / `combat_contrib`；狼狗 `npc_aggression` 继续事件带 `opponent_index` / `opponent_roll` / `targets`（seq 11474–11509）。

### 8.2 8031 独立实例：两名真人角色同屏

- 独立 run `accept8031`（`/tmp/xkx-accept8031.yaml`，api 8031 / ui 8034），不碰 `xkx-world`。
- 两名角色 `player:s2a`（验收甲）、`player:s2b`（验收乙）同处郭庄；两只狼狗的 `combat_enemies` = `['player:s2a','player:s2b']`（seq 47/48）。
- 同一只狼狗 `...guozhuang#2` 的 `combat_contrib = {'player:s2a': 40, 'player:s2b': 10}`（seq 324），证明两名玩家对同一 NPC 的实际扣血各自记账。
- 本世界玩家开局档 40 级，单回合反击即可打掉狼狗 50 血，故实时样本只有 `opponent_index` 0/1/1 三次；连续 400 tick 的 75/25 分布以 §5 定长测试为准。
- `fight` 在守方已登记攻击者为敌人时按击杀处理（原版 `is_killing`，`combat.py:132`）：s2a 的 fight 变致命（狼狗 `combat_target == player:s2a`），s2b 的 fight 仍为切磋、停在 22 血。
