# multi-combat 实现任务清单

- 分支：`xkx-multi-combat`（基于 `origin/xkx-parity` 1cbbcb3）
- 关联 spec：`specs/xkx-parity/multi-combat.spec.md`
- 证据：`specs/xkx-parity/multi-combat-evidence.md`
- 红线：`content/xkx/**`、`room_reset.py`、`respawn`、`combat_rules.py`、`tools/xkx_formula/**` 零改动；旧世界 735 + 两项哈希不变。

## T1 4004 被动还手实测（已完成）

- [x] 连接 4004，醉仙楼 `kill xiao er` × 5 场。
- [x] 记录：5/5 还手、5/5 玩家死亡；NPC 出手 6/3/4/2/3 次，玩家均掉血。
- [x] 结果写入 spec §4.3，`PASSIVE_RETALIATION = True`。
- 验收：5 场记录齐全；不按理论推断（已满足）。

## T1b 击杀经验系统性对齐（已完成）

- [x] 枚举 157 个自定义 `die()` NPC，产出 `specs/xkx-parity/kill-reward-matrix.md`（是否调 `::die()`/`killer_reward`、掉落、击杀经验）。
- [x] 结论：原版普通怪无击杀经验；复刻侧死亡分支也无任何对玩家的 `combat_exp_add`，不存在需关闭的“击杀经验”。
- [x] 新增回归测试：NPC 死亡事件前后，除逐招增量外无额外 `combat_exp_add`；任务奖励若未来实现按 `last_damage_from` 单人归属。

## T2 状态与 Effect 白名单

- [x] `src/wsim/world/state.py`：`Entity` 增 `combat_enemies: list[str]`、`combat_contrib: dict[str,int]`。
- [x] 同文件 `to_dict()`：两字段仅非空写出（`combat_contrib` 按 key 排序），不改 `combat_target` 块。
- [x] `src/wsim/world/effects.py`：`_ENTITY_FIELDS` 增两字段。
- [x] 同文件 `set_field`：`combat_enemies` 校验为 `list[str]` 并去重；`combat_contrib` 校验 `dict[str,int]` 且值 ≥0。
- [x] 同文件 `entity_respawn`（约 `:515-533`）：清空两字段（纯增量）。
- 验收：关闭态旧世界 `to_dict` 无新键；`pytest tests/test_xkx_determinism_legacy.py -q` 仍 735 + 两项哈希。

## T3 选择器与开关工具

- [x] `src/wsim/sim/aggression.py`：`MAX_OPPONENT = 4`。
- [x] 同文件：`multi_combat_enabled()`（模块导入时读一次 `XKX_MULTI_COMBAT`，`1/true/on/yes` 为真）。
- [x] 同文件：`effective_enemies(ent)`（dedup `combat_enemies`，回退 `[combat_target]`）。
- [x] 同文件：`select_index(n, rng)`（`randint(0,3)`，越界回退 0）。
- 验收：单测覆盖长度 1..5 × index 0..3 的映射；关态不调用不耗骰。

## T4 动作层

- [x] `src/wsim/actions/aggression.py` `preconditions`：trigger 在 multi 开时只拒绝“目标已在敌人表”，不再用 `combat_target` 全拒。
- [x] 同文件 `apply` trigger：multi 开时登记**全部**合法玩家（intent 传 `targets` 或由调用方逐人提交），去重追加；`combat_target` 为空时写三件套；日志增 `targets`。
- [x] 同文件 `apply` continue：prune → `select_index` → `chosen` → 仅对 `chosen` 跑 wimpy → `combat_effects(npc, chosen)`；日志增 `opponent_index/targets/pruned`。
- [x] 同文件 `_wimpy_escape`：成功时从**所有** NPC 的 `combat_enemies` 只移除该玩家；表空才清三件套，不再整只 reset 别人。
- [x] 同文件 `_reset`：multi 开时同时清两字段；关态保持原 3 条事件。
- [x] `src/wsim/sim/scheduler.py` `_process_npc_aggression`/`_actor_in_combat`：multi 开按 `effective_enemies` 判交战，intent 带 `targets`；关态走原单目标路径。
- [x] `src/wsim/actions/combat.py` `FightAction.apply`：multi 开且 `PASSIVE_RETALIATION=True` 时，为 defender 追加 attacker 到 `combat_enemies`（及空表时三件套）；`combat_effects` 产出 `combat_contrib` 增量（双向实际扣血，仅记非玩家）。
- [x] `src/wsim/api/session.py` `_trigger_npc_aggression`：multi 开时 `already_fighting` 判定改为“pid 是否已在 `effective_enemies`”；响应契约不动。
- 验收：所有新写入均为 `set_field`，无内存旁路；关闭态零字段/零事件/零额外骰。

## T5 单元测试

新增 `tests/test_xkx_multi_combat.py`：

- [x] A 被动还手常量与 4004 证据一致。
- [x] B 两人同房 trigger → 表长 2；≥400 tick 采样 75/25（±7 个百分点，输出实际计数）；4 人各 25%；5 人时只有前 4 个进入窗口、index 4 永不被选中。
- [x] C `RecordingPRNG` 锁骰序：select → simulate 原序列；开态只在最前多一笔。
- [x] D 单 victim：另一目标 hp 不变，`combat_log` 只含 chosen。
- [x] E prune 与 wimpy 只摘一个。
- [x] F `combat_contrib` 求和正确、overkill 不计、不影响分布。
- [x] G 关闭态 vs 父提交 `1cbbcb3`：同场景事件 JSON 逐字节 diff 为空。
- [x] H multi-on 同 seed 双跑一致。

## T6 真机 8031（独立实例，不动 8011）

- [x] 启动 8031 前 `XKX_MULTI_COMBAT=1`；A/B 两账号进同一大狼狗房间。
- [x] A/B 分别 fight 同一 wolfdog；保存两份 `combat_log.rounds`。
- [x] 保存 NPC 侧 `continue` 事件：`targets` 含两人、`opponent_index` 可读、同一实例 hp 单调下降、`combat_contrib` 按人累加。
- [x] 合杀后贴一行经验归属实测（A/B 均无击杀奖励；B 的 +1 来自逐招成长）。
- [x] A/B 都能 `look` 狗皮并各自 `loot`（两具尸体各 1 张）。
- 验收：全部证据落盘 `specs/xkx-parity/multi-combat-evidence.md`；不触碰 8011 存档。

## T7 收尾

- [x] `python -m pytest tests/test_xkx_determinism_legacy.py -q` 735 + 两项哈希不变。
- [x] 全量 `python -m pytest -q` 全绿（1009 passed, 20 skipped）。
- [x] `git diff origin/xkx-parity... -- tools/xkx_formula` 为空。
- [x] 显式 `git add` 指定文件；`git pull --rebase`；不 force；中文提交并 push `xkx-multi-combat`。
- [x] 交付报告逐条列出：通过/未通过、已知偏差（spec §5）。
