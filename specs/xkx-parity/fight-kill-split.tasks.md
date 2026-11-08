# fight / kill 分离任务清单（S4）

> 规格：[`fight-kill-split.spec.md`](./fight-kill-split.spec.md)。验收证据写入 `reports/xkx-ref/fight-kill-split.md`。

## S4-1 引擎：半血停战

- [x] `combat_rules.simulate_fight` 增加 `killing: bool = True`、`spar: bool = False`；`roll_wound` 使用 `killing`。
- [x] 每次命中后：`spar && damage>0 && dst.hp*2 <= dst.max_hp` → 记录 `stopped`、winner=出手方、立即跳出；不置 `dead`。
- [x] `spar` 下跳过“昏迷目标下一心跳 die()”分支；返回 `spar/killing/stopped`。
- [x] 默认参数下行为与现在逐字节一致（淘汰/掉落/回合数不变）。

## S4-2 动作层：fight/kill

- [x] `combat_effects` 增加 `spar`：`killing = (not spar) or defender.combat_target == attacker.id`；再传给引擎。
- [x] `spar` 且未死亡时跳过 `entity_status dead`、尸体、`drops`/`loot_create`、玩家死亡；保留 `combat_exp_add` 与技能进度。
- [x] `combat_log` 增加 `lethal`/`stopped`（仅在 spar 或 kill 时写，避免旧事件载荷漂移）。
- [x] 新增 `KillAction`（verb=`kill`，`spar=False`）；`FightAction` 增加 `spar` 参数。

## S4-3 注册与命令

- [x] `registry.build_registry(..., lethal_fight=False)`：false 时 `fight.spar=False` 且不注册 `kill`；true 时 `fight.spar=True` + 注册 `kill`。
- [x] `session.py` 读取 `combat.lethal_fight` 并传入 registry。
- [x] `xkx_shell.py`：`kill` 加入 `XKX_EXTRA_PLAYER_VERBS` / `_XKX_VERBS`，从 `FORBIDDEN_VERBS` 移除；未注册时仍被拒。
- [x] `_cmd_kill` 复用 `_match_fight_target`；停战回复“点到即止”，kill 回复致命文案。

## S4-4 测试

- [x] `tests/test_fight_kill_split.py`：切磋半血停战/存活/无掉落/给经验；killer NPC 下 fight 仍致命；kill 致死并掉皮；flag off 时无 kill 且 fight 致命；同 seed 双跑一致。
- [x] 旧九地点哈希 `735 / 009076f0… / 02d5f812…` 不变。

## S4-5 活世界验收（老板库 8011，不 --reset）

- [x] 同步代码，临时配置开 `lethal_fight`，重启 API。
- [x] `fight 店小二`：停战（HP 200→100）、仍活着、`look`/买卖可用，`seq 8127/8136`。
- [x] `kill 大狼狗`：死亡 + `loot_create` 狗皮，`seq 8139/8160`。
- [x] 同 seed 双跑一致；旧九地点哈希不变。

## S4-6 收尾

- [x] `config/worlds/xkx.yaml` 开启 `combat.lethal_fight: true`，**单独提交**。
- [x] 更新 `reports/xkx-ref/fight-kill-split.md`。
- [x] CHANGELOG、全量 pytest 全绿、`tools/xkx_formula` 零改动、显式 `git add`、提交推送。
