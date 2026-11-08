# Presence 超时 — 任务清单

规格：`specs/player-presence-timeout/spec.md`（已批准；补充边界已并入）
交付：分三段提交，每段显式 `git add` → 全量 pytest → 普通 push → 汇报。

## 提交 1：字段与超时判定

### T1 超时核心（纯函数 + 系统动作）
- [x] `src/wsim/actions/player.py`
  - 删 `PRESENCE_ACTIVE_SECONDS` / `PRESENCE_OFFLINE_SECONDS` / `activity_stage`
  - 加 `PRESENCE_TIMEOUT_TICKS = 36`
  - 加 `presence_timeout_pids(state, tick, timeout=36)`（`prof.last_intent_tick`，sorted；
    跳过 offline/缺实体）
  - 实装 `SystemPlayerPresenceAction.apply`：逐个 pid 产出 `player_presence offline`
    （reason `presence_idle_timeout`、source `system`），并清空该玩家自身的
    `combat_target`/`combat_enemies`
  - `PlayerLeaveAction.apply` 透传 `reason`/`operator`/`note`
- 验证：`pytest tests/test_presence_timeout.py -k "boundary or timeout or idempotent"`

### T2 调度与推进双路径触发
- [x] `src/wsim/sim/scheduler.py::_process_tick`：`_process_npc_aggression(t)` 后发
      `system_player_presence`（仅 due 非空）
- [x] `src/wsim/api/session.py::_advance`：escort 结算后同逻辑（锁内 `_submit`）
- 验证：同 tick 只产生 1 条事件；无玩家世界 0 条新事件

### T3 拆除墙钟 presence
- [x] `src/wsim/api/session.py`：删 `_presence_clock`/`_last_active`/`_activity_of`/
      `_mark_offline`/启动 replay 重置/`_perform` 自动离线；`player_state`/`public_state`
      去掉 `now`/`idle_seconds`；同步 `resume`/`keepalive`
- [x] `tools/soak_test.py`：移除 `activity_stage` 依赖
- 验证：`grep -rn "activity_stage|_presence_clock|_last_active" src tools` 无引用

### T4 只读请求活跃刷新
- [x] `WorldSession._touch_tick(pid)`：仅 online；距 `last_intent_tick < 12` 跳过；
      否则提交 `player_heartbeat`
- [x] 入口调用：`status`、`player_state`、`public_state`、`xkx_command`、`keepalive`
- 验证：连续读 10 次只写 1 次心跳；跨 12 tick 再写

## 提交 2：离线语义与战斗排除

### T5 离线不可见/不可选
- [x] `src/wsim/actions/aggression.py::NpcAggressionAction.preconditions` trigger
      对离线玩家返回 `target_offline`
- [x] `src/wsim/actions/combat.py::FightAction.preconditions`：`player:` 目标先判
      offline → `target_offline`；不进入 `apply`（`KillAction` 继承）
- [x] `src/wsim/actions/loot.py::LootAction.preconditions`：`args.target` 指向玩家 →
      离线 `target_offline`、在线 `target_is_player`；不读玩家 `inventory`
- [x] 确认 `_online_players_view`/`public_state`/`/api/world/feed`/`xkx_shell`
      不含离线者
- 验证：逐条用例（look/选目标/伤害不落地/搜身/房间聚合/npc_aggression）

### T6 战斗中断线：立即脱战
- [x] `src/wsim/actions/player.py`：`_in_live_combat` + `COMBAT_PRESENCE_TIMEOUT_TICKS=18`
      （战斗态用 18，非战斗 36）
- [x] 超时动作清空**双方** `combat_target`/`combat_enemies`（NPC 侧确定性排序）
- [x] 下一 tick `npc_aggression` 因 `prof.online=False` reset，不结算伤害
- 验证：17 tick online、18 tick offline+脱战；脱战后 NPC 无伤害事件

### T7 测试
- [x] 新增 `tests/test_presence_timeout.py`（spec §4 ①–⑫）
- [x] 改写 `tests/test_w1_presence.py`（删墙钟三态，保留并发/日界断言）
- [x] 核对 `tests/test_w5_capacity.py`（200 席位是建档上限）
- [x] `tests/test_xkx_determinism_legacy.py` 保持绿（735 事件/两哈希不变）
- 验证：`PYTHONPATH=src python -m pytest -q` 全绿、EXIT=0

## 提交 3：清理与踢人

### T8 管理踢人
- [x] `src/wsim/api/server.py`：`POST /api/admin/players/kick`
      （`XKX_ADMIN_TOKEN` Bearer、`X-Operator`、body `{account,reason}`）
- [x] `WorldSession.kick_player`：`player_leave` source `admin`，
      args `reason=operator_kick`/`operator`/`note`；返回 event_seq/event_hash
- [x] 审计字段：who=operator、when=tick+seq、target=player:<account>、
      why=reason+note；403/401/400 不写事件
- [x] `docs/player-api.md` 接口与 curl 示例
- 验证：403/401/400/404/409/200；事件 `source=admin`；重复踢 409

### T9 老板档清理（禁止直接改库）
- [ ] 同步代码/内容到 `/private/tmp/xkx-play`，`world: existing` 不 reset
- [ ] 重启 8011/8041（需管理接口时带 `XKX_ADMIN_TOKEN`）
- [ ] 记录清理前 `look`：郭庄、沿湖大道、大道、客栈/酒楼
- [ ] 用正常机制（presence 超时或 admin kick）把 `p5peek`/`evidence`/`probe1`/
      `probe2`/`shots`/`verify2`/`s1hunter`/`s4spar`/`s4accept`/`smoke_v1` 置 offline
- [ ] 逐账号审计：谁/何时/对谁/原因（system 超时或 operator kick）
- [ ] 记录清理后同名房间 `look` 对比；郭庄只剩“二只大狼狗”
- [ ] 验证 `look`/`/api/world/feed` 均看不到历史账号
- [ ] 真实账号实验：进房 → 停请求 → 超时 → 另一账号 look 看不到且 fight 被拒 →
      同账号重连回原角色/原位置/原行囊（贴 HTTP 请求/响应）

### T10 文档与证据
- [ ] 根 `spec.md:62` 与 §6.2、`docs/player-system.md` §6.2：36/18 tick 口径
- [ ] `specs/xkx-parity/multi-combat-evidence.md`：100 seed 口径
      （25.10/24.73/25.19/24.98，χ²=1.86，p=0.6025，40,000 次）
- [ ] `overkill` 处置对复核（显示已删、审计保留、`eff_qi -1` 落地后作废）

### T11 红线与交付
- [ ] `git diff -- tools/xkx_formula content/xkx` 为空
- [ ] 显式 `git add`（只加本次源码/测试/文档）
- [ ] 普通 `git push`（不 force）；三段各自推送并汇报
- [ ] 全量 pytest 全绿；旧世界 735 事件/两哈希不变；同 seed 双跑一致
