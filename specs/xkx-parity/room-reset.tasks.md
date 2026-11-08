# P5 修复任务（room reset / NPC 重生）

决策见 [room-reset.spec.md](room-reset.spec.md#61-p5-修复本轮实现前决策)。

- [x] T1 `world/effects.py::entity_respawn` 清空 `combat_target/combat_tick/combat_round`
- [x] T2 `api/session.py::_trigger_npc_aggression` 支持 `only` 过滤；复用同一判定
- [x] T3 `api/session.py::_advance` 在 `system_room_reset` 后对重生 NPC × 在场玩家触发 aggression
- [x] T4 `sim/room_reset.py` `return_home` 增加 `last_action_tick` 空闲 2 tick 节流
- [x] T5 补测试：重生清空交战状态、重生触发、`return_home` 节流与最终回家
- [x] T6 真实 HTTP 闭环：进郭庄 → 多回合 fight 杀狼狗 → loot → reset 重生 → 再 fight 再掉皮
- [x] T7 真实验收：老虎死后原地推进 reset，`system_room_reset` 后紧邻 `npc_aggression/trigger`
- [x] T8 重跑：旧九地点三项哈希、健康 xkx reset 开/关一致与 move 数、`content_hash`、全量 pytest
- [x] T9 更新 CHANGELOG + spec 勾选；显式 `git add`、commit、push `origin/xkx-parity`
