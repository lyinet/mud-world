# fight / kill 分离 Spec（S4）

## Why

当前复刻只有一个 `fight` 动作，且 `combat_rules` 里 `KILLING=True` 写死：对被动 NPC（店小二）出手即致死，等于“切磋把店主杀了”。原版 `fight`（切磋）与 `kill`（生死战）是两条路径，半血停战只发生在**双方都不在 killer 状态**时。

## What Changes

- `fight` 改为切磋：双方 `fight_ob`；当某一方 `qi * 2 <= max_qi` 且**双方都不是 killer** 时停战，`remove_enemy` 双向解除，不 `die()`、不掉落、不留尸体；仍给 `combat_exp` / `improve_skill`。
- `kill` 为致命：走 `die()`、掉落、尸体；`fight` 后可直接 `kill` 收尾。
- NPC 主动攻击（`combat_target` 已指向玩家）按 killer 处理；此时 `fight` **不会**半血停战——这是原版行为（原版 §1.7 狼狗 fight 到昏迷即因狼狗是 killer）。
- 玩家侧新增 `kill` 命令，目标解析复用 `_match_fight_target`（多命中取第一件，与 `look`/`loot` 的 `present()` 口径一致）。
- 开关 `combat.lethal_fight`：默认 **false**（完全保持现状：`fight` 致命、`kill` 不可用）；置 **true** 后启用“`fight` 切磋 + `kill` 致命”分割。验收用临时配置跑，通过后在 `config/worlds/xkx.yaml` 单独提交开启，便于回滚。
- **BREAKING**：开关打开后，`fight` 不再能杀死被动 NPC；要杀必须 `kill`。

## Impact

- 影响 specs：`combat-tempo`、`s1-initiative-gate`、`respawn-parity`（掉落路径）。
- 影响代码：
  - `src/wsim/sim/combat_rules.py`（`simulate_fight` 增加 `killing` / `spar`，半血停战）
  - `src/wsim/actions/combat.py`（`combat_effects` 按 spar 跳过死亡/掉落；新增 `KillAction`）
  - `src/wsim/actions/registry.py`（按 `lethal_fight` 注册 `fight`/`kill`）
  - `src/wsim/api/session.py`（读取 `combat.lethal_fight`；`kill` 动词）
  - `src/wsim/api/xkx_shell.py`（`kill` 命令 + 停战文案）
  - `config/worlds/xkx.yaml`（验收后开启，单独提交）
  - `tests/test_fight_kill_split.py`（新增）

## ADDED Requirements

### Requirement: fight 是切磋

- **WHEN** `combat.lethal_fight=true` 且玩家 `fight` 一个非 killer 的被动 NPC
- **THEN** 战斗在受击方 `qi*2 <= max_qi`（≤50%）时停止，双方 `remove_enemy`；NPC 存活、可继续买卖；不给掉落/尸体；仍结算 `combat_exp` 与技能进度。

### Requirement: kill 是致命

- **WHEN** 玩家 `kill <目标>`
- **THEN** 按致命战斗结算；目标 `qi<0` 昏迷后下一心跳 `die()`，走 `drops`/`loot_create`；目标解析与 `fight` 完全一致。

### Requirement: 开关与回滚

- **WHEN** `combat.lethal_fight` 缺省或 false
- **THEN** `fight` 维持旧行为（致命），`kill` 不可用；旧九地点哈希逐字节不变。

## MODIFIED Requirements

### Requirement: 半血停战边界

- 阈值：`victim->query("qi") * 2 <= victim->query("max_qi")`（`combatd.lpc:683-691`），即气血 ≤50%。
- 仅当 `!me->is_killing(victim) && !victim->is_killing(me)` 时生效；任一方是 killer 则继续致命。
- 停战后：`me->remove_enemy(victim); victim->remove_enemy(me)`；`guarding` 是每场战斗的临时态，本引擎随模拟结束自然清空；NPC 的 `combat_target/combat_tick/combat_round` 若指向玩家则清零。
- 停战后可立即再次 `fight`，也可转 `kill`；无冷却。
