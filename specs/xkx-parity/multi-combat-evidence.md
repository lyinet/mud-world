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

## 5. 选敌分布（离线，同 seed）

| 敌人数 | tick 数 | index 0 | index 1 | index 2 | index 3 | index 4 |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 400 | **297（74.2%）** | **103（25.8%）** | - | - | - |
| 3 | 200 | 99（49.5%） | 57（28.5%） | 44（22.0%） | - | - |
| 4 | 200 | 58（29.0%） | 57（28.5%） | 44（22.0%） | 41（20.5%） | - |
| 5 | 200 | 57（28.5%） | 57（28.5%） | 44（22.0%） | 42（21.0%） | **0（永不被选）** |

断言容差：2 人 index0 272–328、index1 72–128；4/5 人每个前 4 位 36–64。原版 `random(4)` 只有 0..3 四个面；5 人时第 5 个永远进不了窗口，真正回退只发生在敌人少于 4 个时（roll ≥ count → `targets[0]`）。

## 6. 关闭态逐字节一致

同一场景（老虎 trigger + 玩家 fight + NPC continue）在父提交 `1cbbcb3` 与实现分支各跑一次，关闭 `XKX_MULTI_COMBAT`：

```text
event_hash 0bcf5ec2181f9ade4929656d6dc017c9eda440055a712d31d3dba3be17eeeb39
state_hash d29d1b07f9fb307188bfa13ea7124975af21b62568d970a59e49cc7cedd8d304
diff: BYTE_IDENTICAL
```

关闭态零新增字段、零新增事件、零额外骰。

## 7. 回归与红线

- `tests/test_xkx_determinism_legacy.py`：735 事件 + `state_final=009076f0…`、`event_final=02d5f812…` 不变。
- 全量：`1009 passed, 20 skipped`。
- `git diff 1cbbcb3 -- content/xkx room_reset.py src/wsim/respawn.py src/wsim/sim/combat_rules.py tools/xkx_formula`：空。
- 4004 被动还手 5/5 证据见 `multi-combat.spec.md` §4.3。
