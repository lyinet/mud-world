# Presence 超时（断线玩家不得永远站在房里）— 规格

状态：已批准（补充边界已并入） | 作者：TraeCode | 日期：2026-11-08
关联：`docs/W1/spec.md`、`spec.md`（根）、`docs/player-system.md` §6

## 1. 问题与根因

老板档里 `p5peek`、`evidence`、`probe1`、`probe2`、`shots`、`verify2`、`s1hunter`、
`s4spar`、`s4accept` 等历史账号长期 `presence=online`，会出现在房间里、被 NPC 选为
目标、被 `fight/loot` 当作活人、占用在场人数。根因不是一个 bug，而是三处叠加：

1. 在线三态由 `WorldSession._last_active`（`time.monotonic()` 墙钟）驱动，只在“该玩家
   自己发请求”时惰性判断（`_perform` 约 2861-2872 行）；玩家不再发请求就**永远不会**
   被判定离线。
2. 进程重启 replay 时把所有 online 玩家的 `_last_active` 重置为 `now`（约 666-669 行），
   历史账号每次重启都被“续命”。
3. 没有全局 tick 扫描。`SystemPlayerPresenceAction`（`src/wsim/actions/player.py:653`）
   已被改成 no-op，`spec.md:62` 的日界 1/3/7 日状态机停用。

结论：必须把在线/离线判定搬回确定性世界钟（`WorldState.tick` / `profile.last_intent_tick`），
由系统动作在 tick 推进时扫描，不再使用任何墙钟。

## 2. 决策（Decision-complete）

### D1 超时阈值 N = 36 tick

- 世界钟口径：1 tick = 10 游戏分钟（`TICKS_PER_HOUR=6`，`TICKS_PER_DAY=144`）。
- **36 tick = 6 游戏小时 = 1/4 游戏日**。
- 折算真实静置：活世界 `tick_step=6` 秒/tick（`/private/tmp/xkx-play` 启动参数），
  36 × 6s = **216 秒 ≈ 3.6 分钟**。
- 取值理由：
  - 3.6 分钟覆盖浏览器关闭、笔记本合盖短睡、Wi-Fi 切换、页面卡死等常见断线；
  - 6 游戏小时足够长，不会把“站在原地想事情/翻背包”的在线玩家误判离线；
  - 36 = 144/4，与日界（144）整除，便于运维心算，且不是 1 个日界的整数倍，
    避免与日结系统动作耦合。
  - 与既有 `player_heartbeat` 12 tick 冷却成 3 倍关系：只要客户端每 ≤12 tick 有一次
    请求，就绝不会触发 36 tick 超时。
- 该常量写入 `src/wsim/actions/player.py`：`PRESENCE_TIMEOUT_TICKS = 36`。
- **禁止墙钟**：判定只用 `int(state.tick) - int(profile.last_intent_tick)`；
  同一 seed、同一事件序必须得到同一超时时刻。

### D2 活跃时钟的事实来源

- 权威字段：`PlayerProfile.last_intent_tick`（已有）。由以下写入刷新：
  1. 任意成功的玩家 intent —— `Writer` 自动追加 `player_presence online` effect；
  2. `player_enter`（重连）/`player_heartbeat`/`player_leave` 的显式 effect；
  3. API 只读入口（`GET /api/player/state`、`GET /api/player/status`、
     `GET /api/world/feed`、`keepalive`）经 `WorldSession._touch_tick(pid)` 补一次
     `player_heartbeat`（仅当距上次 <12 tick 才发，避免刷事件）。
- 投影字段：`Entity.last_seen_tick` 继续由 `player_presence` effect 同步刷新（在线/离线
  转换都会写），供 UI 展示；**扫描不读它**，避免离线 effect 把时间戳写新导致永不超时。
- 不新增任何 `time.*` 调用到判定路径。`WorldSession._presence_clock`、`_last_active`、
  `_activity_of`、`_mark_offline`、`activity_stage` 全部删除。

### D3 超时扫描与系统动作

新增纯函数（`src/wsim/actions/player.py`）：

```python
PRESENCE_TIMEOUT_TICKS = 36

def _in_live_combat(state, pid) -> bool: ...
def presence_timeout_pids(state, tick, timeout=PRESENCE_TIMEOUT_TICKS) -> list[str]: ...
```

`presence_timeout_pids` 条件（全部满足才返回）：
- `state.players` 中存在 profile 且 `profile.online is True`；
- `pid in state.entities`；
- `tick - profile.last_intent_tick >= timeout`；
- 不处于 D6 的战斗豁免。

返回 `sorted(list)`，保证事件 args 确定性。

`SystemPlayerPresenceAction.apply` 实装（替换 no-op）：
- 重新计算 `due = presence_timeout_pids(state, tick)`（幂等，防止双路径重复）；
- 对每个 `pid` 产出：
  - `Effect("player_presence", {"id": pid, "presence": "offline", "tick": tick,
     "reason": "presence_idle_timeout", "source": "system"})`；
  - 清理**所有**实体上对该玩家的交战登记（确定性排序，不耗 RNG）：
    - `set_field` `combat_enemies` 去掉 `pid`（空表保留空表，不删字段）；
    - `combat_target == pid` 时置 `""`；
  - 清理该玩家自己的 `combat_target`/`combat_enemies`（`""`/`[]`），避免离线实体残留目标。

触发路径（两处，均只在 `due` 非空时发事件，避免空事件污染历史哈希）：
1. `src/wsim/sim/scheduler.py::_process_tick`，在 `self._process_npc_aggression(t)` 之后
   （约 694 行）、`self.writer.state.tick = t`（约 700 行）之前：
   ```python
   due = presence_timeout_pids(self.writer.state, t)
   if due:
       self._emit_system(Intent("system", "system_player_presence",
                                {"tick": t, "pids": due}, t, "system"))
   ```
2. `src/wsim/api/session.py::_advance`（约 3503 行）在 escort 结算之后、锁内：
   同上逻辑，用 `self._submit(...)`。由于动作 apply 会重新计算 due，第二路径看到空集即
   不写事件；同 tick 不会重复。

### D4 离线语义（presence=offline 的实体）

| 语义 | 实现位置 | 说明 |
|---|---|---|
| 不进 `look` 在场人物 | `WorldSession.public_state` / `_online_players_view`（已过滤 `prof.online`）；`xkx_shell._present_view` 只渲染 `present_npcs`（不含 `player:`） | 超时后 `prof.online=False`，自然消失；补测试锁定 |
| 不被选为目标 | `NpcAggressionAction.preconditions`（`src/wsim/actions/aggression.py:185`）trigger 分支新增 `target_offline`；`FightAction.preconditions`（`src/wsim/actions/combat.py:351`）新增 `target_offline` | **明确拒绝**，原因文本“对方已离线”；不得静默、不得照样结算伤害 |
| 不可被搜身/取物 | `LootAction.preconditions`（`src/wsim/actions/loot.py:43`）对 `args.target` 为玩家时返回 `target_is_player`；离线优先返回 `target_offline`。当前 `loot` 只读 `state.ground[location]`，从不读玩家 `inventory` | 杜绝“对尸体/离线角色搜身”的隐式路径；玩家主动 `drop` 到地面的物品仍属房间地面，可被取走，不算搜身 |
| 不登记进 `combat_enemies` | 超时动作清理（D3）；`npc_aggression` trigger 已因上一条拒绝 | continue 分支已有 `prof.online` 过滤，保留 |
| 不占房间人数上限 | 仓库**没有房间级人数上限**；唯一上限是 `MAX_REGION_PLAYERS=200` 的建档席位（`player.py:119`） | 规格明确：离线玩家仍保留目录席位（账号存在），但不进入任何“在场/在线”聚合；测试断言 `public_state.players` 与 `_online_players` 不含离线者 |
| 不被 `npc_aggression` 触发 | 同上 `target_offline` | 拒绝原因计入 `blocked`，不进世界 |

`/api/world/feed` 的玩家可见面（`_online_players_view`）同样只含 online；
历史离线事件仍留在事件流（审计不可删）。

### D5 重连语义

- 同一账号再次 `enter/resume` **接回原角色**：`PlayerEnterAction` 走 profile 已存在分支，
  `_at()` 返回 `entity.location`，**留在原地**（不回客栈、不瞬移 market）；
- 同一 `entity`（`player:<account>`）、原位置、原行囊（`inventory` 不增不减）、原钱包；
- 超时离线**不是删除角色**：不 `create_entity`、不 `player_upsert`、不
  `player_endowment`、不重放 genesis；`profile.endowed` 使初始资产只发一次；
- presence 恢复 `online`，`last_intent_tick`/`last_seen_tick` 刷新为当前 tick；
- 重连后下一 tick 起重新按 36 tick（战斗态 18）计算，不会立刻再次离线；
- 交付证据：一次真实 HTTP 记录 `进房 → 停止请求 → 超时 → look 不可见 →
  同账号重连 → 同位置/同行囊`（请求/响应原文，见 tasks T7/T9）。

### D6 战斗中断线：立即脱战（无宽限），并提前到 18 tick

规则（二选一，本规格选**立即脱战**）：

- 战斗中（`_in_live_combat`：与同房间、status=alive 的 NPC 有交战登记）的玩家，
  超时阈值取 `COMBAT_PRESENCE_TIMEOUT_TICKS = 18`；非战斗玩家仍为 36。
- 达到 18 tick 无任何请求 → 该 tick 内立即：
  1. `player_presence offline`（reason `presence_idle_timeout`）；
  2. 清空该玩家自身的 `combat_target`/`combat_enemies`；
  3. 清空所有 NPC 上指向该玩家的 `combat_target`/`combat_enemies`。
- 下一 tick 的 `_process_npc_aggression` 因 `prof.online=False` 走 `reset` 收兵，
  **绝不再对该离线目标结算一回合伤害**。
- 不选“保留 N tick 宽限”：宽限期内目标仍是 online，NPC 会继续按 tick 造成伤害，
  等于让掉线玩家白挨打，正好违反“回来就死了”的约束。
- 18 的依据：既有 `player_heartbeat` 冷却为 12 tick，任何正常在线客户端（UI 轮询 /
  keepalive）在 12 tick 内必刷新一次；18 留 6 tick 网络与调度余量，不会误踢在线玩家；
  同时 18 已是 36 的一半，把“掉线到脱战”的挨打回合数压到可接受范围。
- 掉线瞬间仍存活者，重连后原地恢复、气血按当时值（不补伤害、不复活）；
  若在 18 tick 内已被打到倒地/死亡，按既有倒地/死亡规则处理（不是超时造成的额外伤害）。
- 玩家对玩家切磋是单事件原子结算（最多 20 回合、一条事件），不产生持久 PvP 战斗态，
  不存在“两个掉线玩家互相续命”。

### D7 管理手段：强制离线（踢人）

新增 HTTP 接口（世界服务 `src/wsim/api/server.py`）：

```
POST /api/admin/players/kick
Authorization: Bearer <XKX_ADMIN_TOKEN>
X-Operator: <操作人>
Content-Type: application/json

{"account": "p5peek", "reason": "operator_kick_cleanup"}
```

- 鉴权：`XKX_ADMIN_TOKEN` 未配置 → `403 {"code":"admin_disabled"}`；
  token 不匹配 → `401 {"code":"invalid_admin_token"}`；
  `X-Operator` 缺失/为空 → `400 {"code":"missing_operator"}`。
- 语义：解析 `pid = player:<account>`；profile 不存在 → `404 player_not_entered`；
  已离线 → `409 already_offline`；否则提交 `player_leave` intent：
  - `source="admin"`，`args={"player_id": pid, "reason": "operator_kick",
    "operator": "<X-Operator>", "note": "<body.reason>"}``；
  - `PlayerLeaveAction.apply` 把这些字段带进 `player_presence` effect（新增
    `reason`/`operator`/`note` 透传，未知键对旧事件无影响）。
- 响应：`200 {"ok": true, "pid": ..., "presence": "offline",
  "event_seq": ..., "event_hash": ...}`。
- 审计事件字段（一条 `player_leave` 事件，可被 `GET /api/world/feed` 与
  `events.sqlite` 查到）：
  - `who`：`args.operator`（来自 `X-Operator`，必填）；
  - `when`：`event.seq` + `event.tick` + 事件写入时刻（审计日志行）；
  - `target`：`actor=player:<account>`；
  - `why`：`args.reason="operator_kick"` + `args.note`（运营备注）；
  - 证据：`effect.player_presence` 中同样的 `reason/operator/note` 与 `event_hash`。
- 权限校验：未配置 token → 403；错 token → 401；缺 operator → 400；
  以上都不写世界、不产生事件。
- 附 curl 示例（见 §6）。
- 已有的 MCP `admin.request → admin.approve → admin.apply` 双人令牌流可提交
  `player_leave`，继续作为第二管理入口；文档写清两者都落到同一世界事件。

### D8 历史残留清理（正常机制）

清理名单（全部置 offline）：
`p5peek`、`evidence`、`probe1`、`probe2`、`shots`、`verify2`、`s1hunter`、
`s4spar`、`s4accept`、`smoke_v1`。

- **禁止 `UPDATE`/`DELETE` 数据库**、禁止手改存档 JSON、禁止脚本直接改 `state`。
  只允许：D3 的 tick 超时扫描，或 D7 的 `POST /api/admin/players/kick`。
- 部署新代码到 `/private/tmp/xkx-play`（`world: existing`，不 reset）。重启后第一个
  tick 的 `system_player_presence` 会把名单里 `last_intent_tick` 早于 `tick-36` 的
  online 账号置 offline（这是“补齐欠账”，不是新规则）。
- 若某账号在前一 tick 已被自动超时（已 offline），管理接口返回 `already_offline`，
  以该账号的超时事件作为审计；**不为凑“管理操作”而伪造事件**。
- 审计要求（每个账号一条可检索记录）：
  - 自动超时：`verb=system_player_presence`，`source=system`，effect target=该 pid，
    `reason=presence_idle_timeout`，`tick`/`event_seq`/`event_hash` 可查；
  - 管理踢人：`verb=player_leave`，`source=admin`，`args.operator`/`args.note`/
    `reason=operator_kick`，`tick`/`event_seq`/`event_hash` 可查。
  - 交付时逐账号列出：谁（system 或 operator）、何时（tick + event_seq）、对谁（pid）、
    原因（reason），并给出清理后 `GET /api/player/state` 的 `presence/online`。
- 验收对比房间（清理前后各一次 `look` 原文 + `present_npcs`）：
  **郭庄、沿湖大道、大道、客栈/酒楼**；郭庄清理后只应剩“二只大狼狗”。
- 验收时给出清理前后每个常去房间的在场人物对比（`look` 原文或
  `GET /api/player/state` 的 `present_npcs`/`online_players`）。

### D9 确定性与红线

- 同 seed 双跑：事件序、`event_hash` 链、`state_hash` 必须逐字节一致；超时事件
  的 tick 与 pid 顺序由世界钟和 `sorted()` 固定，不含墙钟/随机。
- 旧九地点哈希不变：`tests/test_xkx_determinism_legacy.py::test_no_player_legacy_world_hash_is_unchanged`
  继续绿；无玩家世界不新增任何事件。
- 公式库零改动：`git diff -- tools/xkx_formula content/xkx` 必须为空。
- 显式 `git add` 指定文件；不 force；全量 pytest 全绿。

## 3. 代码改动清单（精确到文件/函数）

| 文件 | 改动 |
|---|---|
| `src/wsim/actions/player.py` | 删除 `PRESENCE_ACTIVE_SECONDS`/`PRESENCE_OFFLINE_SECONDS`/`activity_stage`（28-29、220-235）；新增 `PRESENCE_TIMEOUT_TICKS=36`、`_in_live_combat`、`presence_timeout_pids`；实装 `SystemPlayerPresenceAction.apply`（653-663）；`PlayerLeaveAction.apply`（613-621）透传 `reason/operator/note` |
| `src/wsim/api/session.py` | 删 `activity_stage` 导入（43）、`_presence_clock`/`_last_active`（551-554）、replay 重置（666-669）、`_perform` 墙钟自动离线（2855-2872）、`resume`/`keepalive` 的 `_last_active` 写入（2759、2831）、`_mark_offline`/`_activity_of`（3554-3577）；`player_state`（930）与 `public_state`（1095）去掉 `now`/`idle_seconds` 墙钟字段；新增 `_touch_tick(pid)`；`status`/`player_state`/`public_state`/`xkx_command`/`keepalive` 入口调用；`_advance`（3503）加超时结算；新增 `kick_player(account, operator, note)` |
| `src/wsim/sim/scheduler.py` | 导入 `presence_timeout_pids`；`_process_tick`（617）在 `_process_npc_aggression(t)` 后、`world.tick=t` 前发系统动作 |
| `src/wsim/actions/aggression.py` | `NpcAggressionAction.preconditions`（185-229）trigger 分支加“玩家目标必须 online”，否则 `target_offline`；continue 保留 online 过滤 |
| `src/wsim/actions/combat.py` | `FightAction.preconditions`（351）：`player:` 目标先判 offline → `target_offline`，再判 `target_is_player`；`KillAction` 继承同规则；绝不进入 `apply` 结算 |
| `src/wsim/actions/loot.py` | `LootAction.preconditions`（43）新增：`args.target` 指向玩家 → 离线 `target_offline`，在线 `target_is_player`；不读玩家 `inventory` |
| `src/wsim/api/server.py` | 路由表（44 行附近）加 `POST /api/admin/players/kick`；`_do_POST`/`_handle_write`（745-827）加鉴权与调用 `session.kick_player`；`heartbeat`/`keepalive`（808-810）注释更新为“刷新 tick 活跃时间” |
| `src/wsim/world/effects.py` | 仅确认 `player_presence` handler（约 1459）接受未知键；不改变量/哈希路径 |
| 文档 | 根 `spec.md` 62 行与 §6.2（345/352 行）、`docs/player-system.md` §6.2（329-356 行）改为 36 tick 口径并指向本规格；`docs/player-api.md` 加管理踢人；`specs/xkx-parity/multi-combat-evidence.md` 分布段改 100 seed 口径（25.10/24.73/25.19/24.98，χ²=1.86，p=0.6025，40,000 次） |

## 4. 测试清单

| 文件 | 用例 |
|---|---|
| `tests/test_presence_timeout.py`（新增） | ① 35 tick 仍 online、36 tick 变 offline（非战斗边界）；② 同 tick 重复扫幂等；③ look/`public_state`/`_online_players_view`/`/api/world/feed` 不含离线者；④ `npc_aggression` trigger 对离线目标返回 `target_offline`；⑤ `fight`/`kill` 对离线玩家返回 `target_offline` 且 hp 不变（不结算伤害）；⑥ `loot` 带 `target=player:<offline>` 返回 `target_offline`，且该玩家 `inventory` 不变；⑦ 战斗中断线：17 tick 仍 online、18 tick 立即 offline + 清双方 `combat_enemies`/`combat_target`；下一 tick `npc_aggression` reset 且不造成伤害；⑧ 超时清理 NPC 的 `combat_enemies`/`combat_target` 与该玩家自身战斗字段；⑨ 重连：enter 恢复 online、同一 entity、位置不变、`inventory`/钱包不变、不重放 endowment；⑩ 只读请求 `_touch_tick` 在 12 tick 内不重复写心跳、跨 12 tick 写一次；⑪ 管理踢人：无 token 403、错 token 401、缺 operator 400、成功 200 且事件 `source=admin`/`reason=operator_kick`、重复踢 409；⑫ 同 seed 双跑事件链/状态哈希一致（含超时事件） |
| `tests/test_w1_presence.py` | 改写为 tick 语义：删墙钟三态断言；保留 50 并发“活跃玩家不被批量误判”；日界不再批量降级 |
| `tests/test_w5_capacity.py` | 若引用 `activity_stage` 则同步；断言 200 席位是建档上限、与超时无关 |
| `tests/test_xkx_determinism_legacy.py` | 不改断言，必须继续绿（旧九地点哈希不变） |
| `tools/soak_test.py` | 移除 `activity_stage` 用法（改用 `prof.online` / `last_intent_tick`），不改变报告字段则保留输出键 |

## 5. 验收步骤（可复现）

1. `PYTHONPATH=src python -m pytest -q` → 全绿、EXIT=0。
2. `PYTHONPATH=src python -m pytest tests/test_presence_timeout.py tests/test_w1_presence.py tests/test_xkx_determinism_legacy.py -q`。
3. 同 seed 双跑脚本（测试内用 `RunConfig(seed=42, days=2, npc_limit=0)` 两次）对比
   `event_hash` 链与 `state_hash`，逐字节一致；旧世界保持 **735 事件 / 两个哈希**不变。
4. `git diff --stat -- tools/xkx_formula content/xkx` 为空。
5. 部署 `/private/tmp/xkx-play`，`world: existing` 重启；验证：
   - `GET /api/player/state?account_id=<观察号>` 的 `present_npcs` 在郭庄只有
     “二只大狼狗”；
   - 常去房间（沿湖大道等）无历史账号残留；
   - `/api/world/feed` 的在线玩家列表不含历史账号；
   - 记录清理前后每房间在场人物对比。
6. 真实账号断线实验：进入某房间 → 停止所有请求 → 经过 36 tick（活世界约 216 秒）→
   另一账号 `look` 看不到该角色；该账号重新 enter 后同位置恢复 online。
7. 管理接口示例：
   ```
   curl -sS -X POST http://127.0.0.1:8041/api/admin/players/kick \
     -H "Authorization: Bearer $XKX_ADMIN_TOKEN" \
     -H "X-Operator: ops-zhang" \
     -H "Content-Type: application/json" \
     -d '{"account":"s4spar","reason":"operator_kick_cleanup"}'
   ```

## 6. 非目标

- 不实现托管（trustee/dormant）与离线收益；1/3/7 日状态机保持停用。
- 不新增房间级人数上限；不改 `MAX_REGION_PLAYERS`。
- 不改公式库、`content/xkx/**`、战斗伤害/选敌骰。
- 不做离线排队/离线消息补发。

## 7. 交付分段（用户要求：每段推一次、报一次）

1. **提交 1：字段与超时判定** —— `last_intent_tick`/`last_seen_tick` 口径、
   `PRESENCE_TIMEOUT_TICKS=36`、`presence_timeout_pids`、系统动作、
   scheduler/`_advance` 双路径、拆除墙钟、`_touch_tick`、T1/T2/T3/T4 测试。
2. **提交 2：离线语义与战斗排除** —— `target_offline`（fight/kill/loot/npc_aggression）、
   `COMBAT_PRESENCE_TIMEOUT_TICKS=18` 立即脱战、离线不可见聚合、T5/T6 测试。
3. **提交 3：清理与踢人** —— `POST /api/admin/players/kick`、审计字段、文档、
   老板档名单清理、清理前后房间对比、真实断线/重连 HTTP 记录、100 seed 报告口径。

每段：显式 `git add` → 全量 pytest → 普通 push（不 force）→ 汇报该段证据，
然后进入下一段。旧世界 735 事件/两哈希在每段后都必须不变。

## 8. 批准状态

已批准（用户 2026-11-08，“presence 超时 spec 批准，执行”），并按补充边界更新本规格。
本规格无开放问题；按 `tasks.md` 顺序执行。
