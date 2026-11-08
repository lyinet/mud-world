# 验收清单：player-presence-timeout

## 1. 超时判定
- [x] `PRESENCE_TIMEOUT_TICKS=36`、`COMBAT_PRESENCE_TIMEOUT_TICKS=18` 写在 `actions/player.py`，依据进 spec（1 tick=10 游戏分钟）
- [x] 扫描只读世界 tick：`presence_timeout_pids(state, tick)`，不出现 `time.time()`/墙钟
- [x] `SystemPlayerPresenceAction` 在 apply 重算 due，天然幂等；调度器与 `_advance` 双路径触发
- [x] 同 seed 双跑 `event_hash`/`state_hash` 一致（`test_same_seed_twice_identical`）

## 2. 离线语义
- [x] `look`/`public_state`/`player_state.nearby`/`online_players` 不出现 presence=offline 玩家
- [x] `fight`/`kill` 目标离线 → `target_offline`，不结算伤害
- [x] `loot` 不读/不拿离线玩家 `inventory`，返回 `target_offline`
- [x] `npc_aggression` trigger 拒绝离线目标；continue 过滤 online
- [x] 离线实体不进入 `combat_enemies`；仓库内无“房间人数上限”机制（`w5_capacity` 是建档上限），无需改
- [x] `tests/test_presence_timeout.py` 逐条覆盖（9 例）

## 3. 战斗中断线
- [x] 交战玩家 18 tick 超时立即脱战：清玩家与 NPC 双方 `combat_target`/`combat_enemies`
- [x] 下一 tick NPC 不结算伤害（`test_combat_timeout_eighteen_ticks_then_disengage`）
- [x] 管理踢人/主动离开复用同一清扫逻辑（`combat_clear_effects`）

## 4. 重连与创角
- [x] 同账号 `enter` 重连：同一 `player:<account>`、原位置、原行囊；不触发 genesis 重放
- [x] 真实 HTTP 记录：进房 7878 → 断线 7908（36 tick）→ 重连 7908 → 踢人 7914（`/tmp/presence-http-demo.json`）

## 5. 老板档清理
- [x] 10 个历史账号全部 presence=offline（8 个 tick 7814 系统超时、2 个 tick 7852 系统超时）
- [x] 每次操作有审计：`system_player_presence`（source=system, reason=presence_idle_timeout）或 `player_leave`（source=admin, operator/note）
- [x] 未直接 UPDATE 数据库；仅通过世界事件与管理接口
- [x] 清理后 `look`：郭庄=二只大狼狗、楼外楼=店小二、青石大道=红娘、沿湖大道=无人物
- [x] `/api/world/feed` 中历史玩家命中 0；`online_players` 为空

## 6. 管理踢人
- [x] `POST /api/admin/players/kick`：`XKX_ADMIN_TOKEN` + `X-Operator`
- [x] 403 admin_disabled / 401 invalid_admin_token / 400 missing_operator / 400 missing_account / 404 player_not_entered / 409 already_offline / 200
- [x] 审计字段 who=operator、when=tick+seq、target=player:<account>、why=reason+note
- [x] `docs/player-api.md` 有 curl 示例与字段表；`tests/test_admin_kick.py` 覆盖

## 7. 红线
- [x] 公式库 `tools/xkx_formula` 零改动
- [x] `content/xkx/**` 零改动
- [x] 显式 `git add`，未 force push
- [x] 全量 pytest 退出码 0
- [x] 旧九地点 `735` 事件、`state_final=009076f0…`、`event_final=02d5f812…` 不变
