# tasks：`eff_qi` 实现与验收（对应 eff-qi.spec.md 第二版）

约定：每步完成后可单独回滚；`content/xkx/**` 与 `tools/xkx_formula/**` 全程零改动；提交用显式 `git add`。

---

## T0 分支基点（已完成）

- [x] `git fetch` + `rebase` 到 `origin/xkx-parity@0e84be6`（多人同屏已合入）。
- [ ] 收尾合并前再 `fetch` + `rebase` 一次；冲突时保留 `combat_enemies/combat_contrib`。
- [ ] 选敌骰改为独立派生流 `PRNG(seed, seq, "action:npc_aggression:opponent")`，不并入主战斗流。
- [ ] `combat_rules.py` 合并骰序按 spec §1.7 表重写，`RecordingPRNG` 锁死。

## T1 实测心跳与 heal_up（先测后定，阻塞 T2）

- [ ] 备份 `run/mudlib/inherit/char/char.lpc`、`run/mudlib/feature/damage.lpc`（`cp -p` 到 `/tmp`）。
- [ ] 按要求在 `heart_beat` / `heal_up` 顶部加 `log_file("hbprobe", …)`。
- [ ] `tools/xkx_ref/service.sh restart`；`probe_mud.py --port 4004` 登录、静置 120 s、`logout`。
- [ ] 统计：心跳频率、heal_up 间隔分布（6..15，均值 10.5）、`qi` 单次增量。
- [ ] 还原两个文件并 `service.sh restart`；记录实测原始日志到 `reports/xkx-ref/eff-qi/heartbeat.md`。
- 验收：心跳 0.9–1.1 Hz；heal_up 均值 10.5±0.5。偏离则按 spec §2.3 公式重算 `points_per_tick` 并回写 spec。

## T2 字段与序列化（默认关闭，零字节影响）

- [ ] `src/wsim/world/state.py`：`Entity` 增 `eff_hp/heal_credit/heal_cd/revive_tick`（默认 0）。
- [ ] `to_dict()`：仅 `max_hp and eff_hp != max_hp` 输出 `eff_hp/heal_credit/heal_cd`；`status=="unconcious"` 才输出 `revive_tick`。
- 验收：`pytest -q tests/test_world_state*.py`；legacy 哈希 `735 / 009076f0… / 02d5f812…` 不变。

## T3 开关与结算主链路

- [ ] `config/worlds/xkx.yaml:14-16` 增 `eff_qi: false`（引擎默认 False）。
- [ ] `src/wsim/sim/combat_rules.py`：`_Actor.eff_hp` 从实体读取（`>0` 才用，否则 `max_hp`）；`?` 全链路以开关约束。
- [ ] 删除 `combat_rules.py:671-681` 无条件 `target.dead = True`。
- [ ] 伤害护甲参数只传 `apply/armor`（`combat_rules.py:555/569`），`defense` 仅用于命中。
- [ ] `src/wsim/actions/combat.py:147-156` 回写 `hp_add` + `eff_hp_set`；`eff_hp<0→dead`、`hp<0&&eff_hp>=0→unconcious`（`revive_tick`）。
- 验收：单测 1/2/3/4/5/10（spec §6.1）。

## T4 恢复封顶与疗伤缺口

- [ ] `src/wsim/world/effects.py:465-482`：开关打开时 `hp_add` 下限 -1；新增 `eff_hp_set`（钳 `[-1,max_hp]`，值不变不发）。
- [ ] `src/wsim/actions/player.py:580-594`：`sleep` 醒来 `hp=eff_hp`；`rest` 封顶 `eff_hp`。
- [ ] `effects.py:488` 接受 `unconcious`；`revive` 不改 `eff_hp`、`hp=max(1, hp)` 仅用于还阳路径。
- [ ] 不实现 `receive_curing` 对应命令；`eff_hp_add` 只留事件接口（spec §3.4）。
- 验收：单测 6/7。

## T5 被动心跳恢复与苏醒（`system_heal`）

- [ ] `src/wsim/actions/system.py` 新增 `SystemHealAction`（verb=`system_heal`）：`heal_credit` 累积、heal_up（`eff_hp+1`、`hp+=con//3` 封顶 `eff_hp`）、昏迷到期苏醒。
- [ ] `src/wsim/sim/scheduler.py::_process_tick` 与 `headless.py` 的日循环：仅当存在需要治疗的实体且开关打开时发一次 `system_heal`。
- 验收：单测 2/5；关闭开关时事件序列与基线逐字节一致。

## T6 昏迷 / revive 换算

- [ ] `revive_ticks = max(1, round((30 + rng.randrange(100-con)) * 0.6083))`，随机走 `WorldSession` PRNG；载荷写 `revive_tick`。
- [ ] 苏醒：`status=alive`、`hp=0`、`eff_hp` 不变（spec §4.3）。
- [ ] 进入 `unconcious`/`dead` 清空 `combat_enemies` 与 `combat_target`，保留 `combat_contrib`；苏醒后不自动恢复仇恨。
- [ ] 昏迷 NPC 不生成出手/游走/买卖意图；昏迷玩家除只读命令外一律拒绝。
- 验收：单测 1/2/5；测试覆盖“跨 tick 存续 / 到期苏醒 / 补刀多次（含未命中）”。

## T7 骰序锁定

- [ ] 在 `tests/` 用 `RecordingPRNG` 锁定 spec §1.3 四种场景的 `random()` 次数与顺序。
- [ ] 断言 `apply/defense` 不参与破防：`defense=100, apply_armor=0` 时仍走 `random(damage) > 0`。
- 验收：单测 10；公式库 `git diff -- tools/xkx_formula` 为空。

## T8 全量回归与重跑

- [ ] `cd /private/tmp/xkx-parity-fix && PYTHONPATH=src python3 -m pytest -q`（全绿）。
- [ ] 狼狗 `kill` 10 场：回合均值/区间 vs 7–24；每刀、每回合伤害、有效血量、昏迷次数、补刀次数分布。
- [ ] 老虎 `kill` 10 场（`max_qi=425`）；店小二 `fight` 20 场 + 停手战报（`qi` 到阈值、`eff_hp>qi`）。
- [ ] 开关关闭态与开启前逐字节一致（事件序列 + 状态哈希 + legacy 哈希）。
- [ ] 多目标回归（8031/独立实例）：两人同打一只怪，`combat_enemies` 仍两人、75/25 分布成立、昏迷/死亡清空敌人表。
- [ ] 基线变化单报。

## T9 活体验收与提交

- [ ] 8011/8041：`fight 店小二` 半血停手 → `look` → `buy`；`kill 大狼狗` → 掉皮、背包 +1。
- [ ] 真实 HTTP：玩家打到 `hp<0` 昏迷（不死）→ `look` → 苏醒/治疗 → 继续活动；再打到 `eff_hp<0` → 死亡 → 鬼门关 → 还阳。
- [ ] 活体“被打死 → 鬼门关 → 还阳”，记录 `exp -= exp/50`、技能降级、还阳后的 `hp/eff_hp`。
- [ ] xkx 活世界不 `--reset`：迁移前后同实体字段对比 + 旧九地点哈希。
- [ ] 显式 `git add` 本批文件；提交；`config/worlds/xkx.yaml` 的 `eff_qi: true` **单独提交**；不 force。
