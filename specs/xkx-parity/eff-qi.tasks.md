# tasks：`eff_qi` 实现与验收（对应 eff-qi.spec.md 第二版）

约定：每步完成后可单独回滚；`content/xkx/**` 与 `tools/xkx_formula/**` 全程零改动；提交用显式 `git add`。

证据总表见 `eff-qi.spec.md` §10（2026-11-08）。

---

## T0 分支基点（已完成）

- [x] `git fetch` + `rebase` 到 `origin/xkx-parity@0e84be6`（多人同屏已合入）；收尾前再次 `rebase` 到最新（保留 `combat_enemies/combat_contrib`，未回退）。
- [x] 选敌骰走独立派生流 `PRNG(seed, seq, "action:npc_aggression:opponent")`，不并入主战斗流（`tests/test_xkx_initiative_gate.py` 锁定）。
- [x] `combat_rules.py` 骰序按 spec §1.7 表重写（选敌 → 门 → 守卫文案 → 命中/闪避/招架 → 伤害 → 破防 wound → 反击），`RecordingPRNG` 锁死。

## T1 实测心跳与 heal_up（先测后定，阻塞 T2）

- [x] 备份 `char.lpc` / `damage.lpc` 后插桩、`service.sh restart`、`probe_mud.py --port 4004` 静置 120 s、还原并重启。
- [x] 实测：心跳 121 次 / 121 s ≈ **1008 ms**；heal_up 间隔 **6..16，均值 10.548，中位 10**（57 实体 / 416 样本）；分布与源码 6..15 一致（16 为采样边界）。
- [x] 记录于 spec §2.2（原始日志未单建 `reports/` 目录，数值与分布已全文落 spec）。
- 验收通过：心跳 1000±100 ms；heal_up 均值 10.548（与 10.5 偏差 0.46%），采用 `HEAL_CREDIT_PER_TICK=16438`、`HEAL_CD ∈ [6,15]`。

## T2 字段与序列化（默认关闭，零字节影响）

- [x] `src/wsim/world/state.py`：`Entity` 增 `eff_hp`（默认 **-2**）、`heal_credit/heal_cd/revive_tick`（默认 0）。
- [x] `to_dict()`：仅 `eff_hp >= -1 and eff_hp != max_hp` 输出 eff 系列；`revive_tick` 非 0 才输出（等价“仅昏迷期”）。
- 验收：`tests/test_world_state*.py` 通过；legacy 哈希 `735 / 009076f0… / 02d5f812…` 不变（§10.3）。

## T3 开关与结算主链路

- [x] `config/worlds/xkx.yaml` 已有 `combat.eff_qi: false`（引擎默认 False）。
- [x] `src/wsim/sim/combat_rules.py`：`_Actor` 读实体 `eff_hp`（`<= -2` 视作 `max_hp`），全链路受开关约束；关闭态回退旧口径。
- [x] 删除无条件 `target.dead = True`；死亡/昏迷由 `eff_hp` 与 `hp` 分界判定。
- [x] 伤害护甲只传 `apply/armor`；`apply/defense` 仅用于命中/闪避（单测断言）。
- [x] `src/wsim/actions/combat.py` 回写 `hp_add` + `eff_hp_set`；`eff_hp<0→dead`、`hp<0&&eff_hp>=0→unconcious`（带 `revive_tick`）。
- 验收：单测 1/2/3/4/5/10 通过。

## T4 恢复封顶与疗伤缺口

- [x] `effects.py`：开关打开时 `hp_add` 下限 -1；`eff_hp_set` 钳 `[-1, max_hp]`，值不变不发。
- [x] `player.py`：`sleep` 醒来 `hp=eff_hp`；`rest` 封顶 `eff_hp`。
- [x] `effects.py` 接受 `unconcious`；`revive` 不改 `eff_hp`，仅还阳路径抬 `hp`。
- [x] 不实现 `receive_curing` 对应命令；`eff_hp_add` 只留事件接口（spec §3.4 / §10.8）。
- 验收：单测 6/7 通过。

## T5 被动心跳恢复与苏醒（`system_heal`）

- [x] `SystemHealAction`（verb=`system_heal`）：`heal_credit` 累积、heal_up（`eff_hp+1`、`hp += con//3` 封顶 `eff_hp`）、昏迷到期苏醒。
- [x] scheduler `_process_tick` 与 headless 日循环：仅开关打开且存在待治疗实体时发一次 `system_heal`。
- 验收：单测 2/5 通过；关闭开关事件序列与基线逐字节一致（§10.3）。

## T6 昏迷 / revive 换算

- [x] `revive_ticks = max(1, 30 + rrng.randrange(100-con))` 再按 0.6083 折算 tick，载荷写 `revive_tick`。
- [x] 苏醒：`status=alive`、`hp=0`、`eff_hp` 不变。
- [x] 进入 `unconcious`/`dead` 双向清 `combat_enemies` 与 `combat_target`（死亡经 `remove_all_enemy` 旗标），保留 `combat_contrib`；苏醒后不自动恢复仇恨。
- [x] 昏迷 NPC 不生成出手/游走/买卖意图；昏迷玩家除只读命令外一律拒绝。
- 验收：单测 1/2/5 通过，覆盖跨 tick 存续 / 到期苏醒 / 补刀多次（含未命中）。

## T7 骰序锁定

- [x] `RecordingPRNG` 锁定 spec §1.3 四种场景的 `random()` 次数与顺序。
- [x] 断言 `defense=100, apply_armor=0` 时仍走 `random(damage) > 0`（破防不认 defense）。
- 验收：单测 10 通过；`git diff -- tools/xkx_formula` 为空。

## T8 全量回归与重跑

- [x] 全量 `pytest -q`（结果见提交说明；2026-11-08 本轮全绿）。
- [x] 狼狗 `kill` 10 场：`attack_rounds` 3–8 均值 4.90（低于原版 7–24，归因 `cps=0`，基线变化见 §10.5）；每刀、每回合、有效血量、昏迷 18 次、补刀分布已记录。
- [x] 老虎 `kill` 10 场（`max_qi=425`）10/10 打昏，`eff_qi` 8–88；店小二 `fight` 20/20 半血停手（`qi` 80–100、`eff_qi` 163–189）。
- [x] 关闭态与开启前逐字节一致（事件序列 + 状态哈希 + legacy 哈希，§10.3）。
- [x] 多目标回归（8031 `xkx-eff-live`）：`targets` 6/6 两人、`opponent_index` 11/14=78.6%、昏迷/死亡清敌人表（§10.6）。
- [x] 基线变化单报（`attack_rounds` 偏短 → `cps=0` 映射偏差，不回调数值）。

## T9 活体验收与提交

- [~] 8011/8041 活体 `fight 店小二` 半血 → `look` → `buy`：半血停手已在脚本 20/20 覆盖，`buy` 复检本期未做（记入提交说明）。
- [x] 真实 HTTP：打到 `hp<0` 昏迷（不死）→ 自然苏醒 → 继续活动（§10.7 B）。
- [x] 真实 HTTP：打到 `eff_hp<0` → 死亡 → 鬼门关 → `revive` 还阳（§10.7 C）。
- [x] 活体死亡惩罚：`combat_exp 6401→6273`（`exp/50`）、`potential 99→50`、`skills_lost [dodge,parry,unarmed]`、还阳后 `hp=100`。
- [x] xkx 活世界不 `--reset`：迁移 12899 events / 161 entities 全实体 `to_dict()` BYTE-IDENTICAL、`eff_keys_in_dict=0`（§10.4）。
- [x] 显式 `git add` 本批文件；提交；`config/worlds/xkx.yaml` 的 `eff_qi: true` **单独提交**；不 force。

## T10 待办（开关锁定缺口，合并验收时确认）

- [x] `combat.initiative_gate` 的会话级缺口已补：`da301d8` 新增
  `tests/test_xkx_initiative_gate.py::test_initiative_gate_flag_is_read_from_world_config`，走真实
  `WorldSession` + 仓库 `config/worlds/xkx.yaml`，关掉配置即红（假回退已验证）。
- [x] `combat.eff_qi` 的 `test_xkx_growth::test_combat_death_and_revive_real_session` 已由 `1935024`
  改为开/关两态交叉断言（昏迷 vs 死亡还阳），随本次合并带入。
