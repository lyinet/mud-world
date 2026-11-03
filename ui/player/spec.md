# 玩家门面接线 Spec（当前实现）

## 目标

把 `ui/player/` 从“能点、只提案”接到真写链路：默认反向代理 `wsim.api`（8020），页面按
`docs/player-api.md`（`profile_version=1`）渲染；`--readonly` 保留只读降级。

## 架构

```text
浏览器
  └─ http://127.0.0.1:8030  ui/player/server.py（静态页 + 薄代理）
       └─ /api/player/* 原样转发 → http://127.0.0.1:8020  wsim.api（单写者）
            └─ runs/player-demo-<时间戳>/events.sqlite（可写）
```

- 代理不实现业务：enter/state/action/intents/messages 方法、路径、query、body 原样透传。
- 代理只做两件事：
  1. 写动作被拒 → HTTP 409，补 `reason`/`message` 别名（契约原字段保留）；
  2. 读接口业务失败 → HTTP 200 + `ok:false`。
- live 模式不碰任何 run；readonly 模式只读 `events.jsonl`/`summary.json`，写操作进 `propose_intent` outbox。

## 模式

| 条件 | 模式 | 页面 |
|---|---|---|
| 8020 可达（默认） | live | `可写 · 真 API · <upstream>` |
| `--readonly` | readonly | `只读模式 · <run>`，提示条“提案不落地” |
| 8020 不可达且未加 `--readonly` | readonly（自动降级） | 同上，启动日志告警 |
| live 中途上游掉线 | degraded | 提示“上游不可达” |

## 启动与 run 隔离

- `ui/player/start.sh` 检测 `runs/${RUN}/events.sqlite`：
  - 默认自动换名 `player-demo-<YYYYmmddHHMMSS>`；
  - `--reset` 删除重来；
  - `--reuse` 默认拒绝（上游不 replay），仅 `WSIM_REPLAY_CONFIRMED=1` 放行并告警。
- 既有 run（`runs/p3c-players-1d`、`runs/p3d-*` 等）只读，不写入。
- 原因：`WorldSession` 每次启动重新 bootstrap genesis，不 replay 既有事件。

## 页面字段（冻结契约）

- `state.player.{entity_id,account_id,name,archetype,role,location,location_name,stamina,max_stamina,presence,online,wallet:{money,items},active_quests}`
- `state.tick`：页面本地换算 144 tick/天、6 tick/小时、10 分钟/tick。
- `state.present_npcs`：只含当前地点、非 `player:` 的 alive 实体。
- `state.quest_board.{counts,rows,active}`：`rows` 只有 `open`，`active` 只有本人已接。
- `state.location.{id,name,description,exits,services}`。
- `state.online_players`。

## 动作

- 移动：`POST /action {verb:"move", args:{to}}`；成功以 `event_seq/tick/next_tick` 提示，并重新 `GET /state`。
- 说话：`POST /action {verb:"talk", args:{target,message}}`；成功用 `messages?since_tick=<tick-1>` 轮询，
  只渲染 `kind=talk_reply`；`details.reply` 仅兜底并标注。
- 接任务：`POST /action {verb:"quest_accept", args:{quest}}`；成功后 `quest_board.counts` 与
  `accepted_by_me` 应变化，卡片显示 `accepted_by_me`。
- 拒绝：页面按 `status/ok` 分支，展示 `message`（= `reason_text`）+ `reason`（= `reject_reason`）。

## 恢复与缓存

- 身份键是 `account_id`；localStorage 只存 `{account_id, display_name, archetype}`。
- 刷新/重启代理：`GET /state?account_id=` 恢复位置、钱物、任务；失败才回进入弹层。
- 代理响应 `Cache-Control: no-store`；页面 `fetch(..., {cache:'no-store'})`；旧 `ws_player_session` 启动时清除。

## 验收

1. enter：`wallet.money=300`、炊饼×3、粗布×1、`location=market`。
2. move：`market→smithy`，`location` 变化、`event_seq` 增加、`tick` 推进。
3. talk：`/messages?since_tick=` 返回 `kind=talk_reply`，文本与 `details.reply` 一致。
4. quest_accept：`counts.open` 14→13、`accepted_by_me` 0→1、`active` 含 `status=accepted`。
5. 重启 UI 代理：按 `account_id` 恢复 `smithy`、300 文、任务计数。
6. 拒绝：`not_adjacent` / `not_together` / `not_at_giver` 为 409，枚举同名、`reason_text` 可读。

## 已知歧义（详见 README §8）

- 契约写 rejected=HTTP 200，验收裁定 409；代理归一。
- 重启 `wsim.api` = 新 genesis（不 replay）；“恢复”只对重启 UI 代理成立。
- talk 双通道（`details.reply` 与 `/messages`）；验收以 `/messages` 为准。
- `/state` 不提供 NPC 详情与世界日志；页面相应降级展示。
