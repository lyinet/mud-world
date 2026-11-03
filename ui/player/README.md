# 玩家门面 · `ui/player/`

面向真人的首个可玩前端。默认**反向代理真 API**（`world-sim/docs/player-api.md`，`profile_version=1`），
进入、移动、说话、接任务都会真实落 `events.sqlite`；`--readonly` 时降级为“提案不落地”。

## 结论（先看这里）

- `ui/player/server.py` 是薄代理：`/api/player/*` 原样转发到 `http://127.0.0.1:8020`，
  不重写业务逻辑。页面对齐冻结字段：`player`、`player.wallet.money|items`、`quest_board`、`tick`、`account_id`。
- 默认连接真 API；上游不可达或显式 `--readonly` 时回到只读回放 + `propose_intent`，页面顶栏和提示条都会显示“只读模式”。
- 可写实验只落在 `runs/player-demo*`；`runs/p3c-players-1d`、`runs/p3d-*` 等既有 run **只读**，本服务不会打开写入。
- 启动脚本检测到 `runs/player-demo/events.sqlite` 已存在时，**自动换名**为 `runs/player-demo-<YYYYmmddHHMMSS>`，
  不静默追加第二段创世；`--reset` 删除后重来，`--reuse` 仅在确认上游会 replay 时才允许。
- 五步闭环已实测：300 文到账、`market → smithy` 位置真变且 `event_seq` 4 → 6、`tick` 6 → 12、
  `messages?since_tick=11` 读到 NPC 回复、接任务后 `accepted_by_me` 0 → 1。
  注意：每个写动作 `event_seq` 实测 **+2**（4→6→8→10→12），不是按动作 +1；验收标准应为“增长”，不是“+1”。
- 关于“刷新后回到集市”的排查结论见 [§7](#7-刷新回集市的排查六步-curl-复现)。

## 1. 启动

### 一键启动（推荐）

```bash
cd /Users/lyinet/projects/XKX/world-sim
./ui/player/start.sh
```

打开 <http://127.0.0.1:8030/>。

`start.sh` 会：

1. 检查 `runs/${RUN}/events.sqlite`；
2. 若已存在且没给 `--reset`/`--reuse`，自动换名为 `player-demo-<YYYYmmddHHMMSS>` 并打印实际目录；
3. 以 `PYTHONPATH=src python3 -m wsim.api --run "$RUN" --port 8020` 启动真 API；
4. 探活成功后启动 `ui/player/server.py --upstream http://127.0.0.1:8020 --port 8030`。

### 手工两条命令

```bash
cd /Users/lyinet/projects/XKX/world-sim
PYTHONPATH=src python3 -m wsim.api --run player-demo-$(date +%Y%m%d%H%M%S) --port 8020

# 另一个终端
python3 ui/player/server.py --upstream http://127.0.0.1:8020 --port 8030
```

真 API 的启动契约见 `docs/player-api.md` §0。**每次启动 `wsim.api` 都是新 genesis**：
它不 replay 既有 `events.sqlite`，所以不要在同一目录重启，也不要把 `runs/p3c-players-1d` 指给 `wsim.api`。

### 只读降级

```bash
./ui/player/start.sh --readonly          # 默认回放 runs/p3c-players-1d，不启动真 API
./ui/player/start.sh --readonly --run p3c-players-1d
python3 ui/player/server.py --readonly --port 8032 --run runs/p3c-players-1d
```

- 只读模式下写操作只提交提案（`status="proposed"`、`state_unchanged=true`），不写 `events.sqlite`。
- 未加 `--readonly` 但 8020 不可达时，代理会**自动降级**并在启动日志与页面明示；默认仍然是连接真 API。

### 启动守卫

| 情况 | 行为 |
|---|---|
| `runs/player-demo/events.sqlite` 已存在 | 自动换名 `player-demo-<时间戳>`，打印实际 run |
| `--reset` | 删除 `runs/${RUN}` 后重新创世 |
| `--reuse` | 默认拒绝；仅当 `WSIM_REPLAY_CONFIRMED=1` 时允许，并打印警告 |
| `--readonly` | 不启动 API，只启动只读代理 |
| 上游 8020 不可达（未加 `--readonly`） | 代理自动进入只读模式，页面显示“只读模式” |

`--reuse` 默认拒绝的原因：上游当前不 replay `events.sqlite`，复用会追加第二段 genesis，账本语义失真。
这是**契约待补**项（需要上游提供 replay，或由 API 自己拒绝脏 run）。

## 2. 只读 / 可写路径

| 路径 | 用途 | 谁能写 |
|---|---|---|
| `runs/p3c-players-1d` | 既有 demo run，只读回放 | **禁止写入**；`--readonly` 只读打开 |
| `runs/rev72d` 等既有 run | 只读回放 | **禁止写入** |
| `runs/player-demo` | 可写副本（会被启动脚本换名保护） | `wsim.api`（可写） |
| `runs/player-demo-<时间戳>` | 每次启动实际使用的可写 run | `wsim.api`（可写） |

代理层自身不打开任何 run：live 模式它只转发 HTTP；readonly 模式它只读 `events.jsonl`/`summary.json` 做内存回放，
并通过 `propose_intent` 进 outbox。

## 3. 模式显示

`GET /api/health`（代理本地接口）：

```json
{"ok": true, "mode": "live", "readonly": false, "profile_version": 1,
 "upstream": "http://127.0.0.1:8020", "upstream_ok": true, "docs": "world-sim/docs/player-api.md"}
```

- live：顶栏 `可写 · 真 API · http://127.0.0.1:8020`。
- readonly / 自动降级：顶栏 `只读模式 · <run>`，提示条写明“提案不落地”。
- 上游中途掉线：`mode` 变为 `degraded`、`upstream_ok=false`，页面显示“上游不可达”。

## 4. 契约字段映射（14 条差异已按冻结版接线）

| 旧实现 | 冻结契约 | 页面/代理处理 |
|---|---|---|
| `/api/enter`、`/api/move`、`/api/talk`、`/api/quest/accept` | `/api/player/enter`、`/action` | 统一走 `POST /api/player/action`，`verb` 区分 |
| `me` | `player` | 页面只读 `st.player` |
| `me.money` / `me.items` | `player.wallet.money` / `player.wallet.items` | 顶栏与进入结果 |
| `quests` | `quest_board` | `counts`/`rows`/`active`；`rows` 只有 `open`，`active` 是本人已接 |
| `clock` | 只有 `tick` | 页面本地换算：144 tick/天、6 tick/小时、10 分钟/tick |
| `pid=player:u_demo` | `account_id=u_demo` | 代理层兼容 `player:` 前缀，但只下发 `account_id` |
| 写响应里拿对话回复 | `GET /messages?since_tick=` | 页面以 `kind=talk_reply` 为准；`details.reply` 仅兜底并标注 |
| 409 拒绝码同名 | `reject_reason`/`reason_text` | 代理归一为 409 + `reason`/`message` 别名，原字段保留 |
| `/api/scene`、`/api/npc/<id>` | 无 | live 模式不再使用；NPC 弹层只展示 `present_npcs` 字段 |
| 世界日志 | 无 | 页面改为“本次操作记录（本地）” |
| `events`/`event_rows` | `event_seq` | 移动/说话/接任务提示 `event_seq` |
| `clock.label` | `tick` | 见上 |
| `all_npcs` | 无 | 搜索范围只限 `present_npcs` |
| `player_profiles` | 无 | 不再展示 |

## 5. 拒绝状态码（按验收裁定）

`docs/player-api.md` §4.3 写的是“represents rejected as HTTP 200”。本次验收裁定覆盖为：

- **写动作被拒 → HTTP 409**，body 同时包含：
  - 契约原字段：`ok:false`、`status:"rejected"`、`reject_reason`（枚举）、`reason_text`（人话）
  - 验收要求别名：`reason`（= `reject_reason`）、`message`（= `reason_text`）
- **读接口业务失败 → HTTP 200 + `ok:false`**，不返回 4xx，避免页面当成网络错误。
- 网络层上游不可达 → 502（不是业务拒绝）。
- 代理不新增任何业务枚举；`reason` 一律复制上游 `reject_reason`。

已实测：`not_adjacent`、`not_together`、`not_at_giver`，均 409 且枚举与契约同名。

## 6. 五步闭环原始输出

环境：真 API `http://127.0.0.1:8020`，run `player-demo-20261104020825`，代理 `http://127.0.0.1:8030`，
账号 `u_demo`。以下均为代理透传后的原始 JSON（节选关键字段，未改写数值）。

### ① 进入：300 文、炊饼×3、粗布×1、market

```json
{"action": "enter", "details": {"created": false, "resumed": true, "role": "捕快", "endowment": {"money": 300, "items": [{"item": "coarse_cloth", "name": "粗布（一匹）", "qty": 1}, {"item": "flatbread", "name": "炊饼", "qty": 3}], "granted_at_genesis": true}}, "event_seq": 4, "next_tick": 6, "ok": true, "player": "player:u_demo", "status": "executed", "tick": 0, "data": {"location": "market", "wallet": 300, "inventory": {"coarse_cloth": 1, "flatbread": 3}}}
```

`GET /state`：`player.location="market"`、`player.wallet.money=300`、`player.wallet.items=[粗布1, 炊饼3]`、`tick=6`。

### ② 移动：market → smithy，event_seq 增加、tick 推进

```json
{"action": "move", "data": {"location": "smithy", "wallet": 300}, "event_seq": 6, "next_tick": 12, "ok": true, "player": "player:u_demo", "status": "executed", "tick": 6, "verb": "move"}
```

`GET /state`：`player.location="smithy"`、`location_name="铁匠铺"`、`tick=12`；`event_seq` 4 → 6（+2，实测每个写动作递增 2）。

### ③ 说话：`details.reply` 与 `/messages?since_tick=11` 同一条

```json
{"action": "talk", "details": {"reply": "石敢当（石匠）听了你的话……「一百文我记着，等这趟活结了就给裴镖头送去。」", "relation": {"target": "shi_gandang", "affection": -1.0, "trust": 39.5}}, "event_seq": 8, "next_tick": 18, "status": "executed", "tick": 12, "verb": "talk"}
```

```json
{"account_id": "u_demo", "messages": [{"from": "shi_gandang", "id": "msg:8", "kind": "talk_reply", "seq": 8, "text": "石敢当（石匠）听了你的话……「一百文我记着，等这趟活结了就给裴镖头送去。」", "tick": 12, "to": "player:u_demo"}], "next_since_tick": 18, "ok": true, "since_tick": 11, "tick": 18}
```

页面在 `since_tick=11` 轮询后显示“世界回应”，来源标注 `/api/player/messages?since_tick=11`。

### ④ 接任务：counts 与 accepted_by_me 真变

```json
{"action": "quest/accept", "data": {"active_quests": ["quest:commission_stonework:0"], "location": "smithy", "active_quests_detail": [{"id": "quest:commission_stonework:0", "name": "铁匠铺代工", "status": "accepted", "giver": "shi_gandang"}]}, "event_seq": 10, "next_tick": 24, "status": "executed", "tick": 18}
```

接单前：`quest_board.counts = {"open":14,"acceptable_here":1,"accepted_by_me":0}`。
接单后：`quest_board.counts = {"open":13,"acceptable_here":0,"accepted_by_me":1}`，
`quest_board.active = [{"id":"quest:commission_stonework:0","status":"accepted"}]`。

### ⑤ 重启 UI 代理后按 `account_id` 恢复

只重启代理（真 API 不重启），同一 `account_id`：

```text
before restart: location=smithy tick=42 money=300 accepted_by_me=1 active=['quest:commission_stonework:0']
after  restart: location=smithy tick=42 money=300 accepted_by_me=1 active=['quest:commission_stonework:0']
```

浏览器实测：在书院 `academy` 刷新页面，位置仍为 `academy`、任务计数不变。

### 拒绝路径

```json
{"ok": false, "status": "rejected", "reject_reason": "not_adjacent", "reason_text": "目标地点与当前位置不相邻；请沿道路逐段移动。", "reason": "not_adjacent", "message": "目标地点与当前位置不相邻；请沿道路逐段移动。", "event_seq": 12, "tick": 24}
```

```json
{"ok": false, "status": "rejected", "reject_reason": "not_together", "reason_text": "对方不在你当前所在的地点。", "reason": "not_together", "message": "对方不在你当前所在的地点。", "event_seq": 14, "tick": 30}
```

```json
{"ok": false, "status": "rejected", "reject_reason": "not_at_giver", "reason_text": "你不在任务发布人所在的地点。", "reason": "not_at_giver", "message": "你不在任务发布人所在的地点。", "event_seq": 16, "tick": 36}
```

## 7. “刷新回集市”的排查（六步 curl 复现）

全程绕开 UI，直打 `wsim.api`（8020），账号 `u_repro1`（新账号，`created_tick=84`）：

| # | 请求 | 原始结果 |
|---|---|---|
| 1 | `POST /api/player/enter` | `status=executed`、`tick=84`→`next_tick=90`、`data.location=market`、`created=true`、`wallet=0`（运行中新建账号不铸币） |
| 2 | `GET /state` | `tick=90`、`location=market`、`created_tick=84`、`last_intent_tick=84` |
| 3 | `POST action move {to:academy}` | `status=executed`、`event_seq=34`、`tick=90`、`next_tick=96`、`data.location=academy` |
| 4 | `GET /state` | `tick=96`、**`location=academy`**、`last_intent_tick=90` |
| 5 | 等 10 秒再 `GET /state` | `tick=96`、**`location=academy`**（未回退） |
| 6a | 第二次 `POST enter`（同账号） | `status=rejected`、`reject_reason=already_online`、`reason_text="你已经在线，无需重复进入。"`、`event_seq=36`、`next_tick=102` |
| 6a | 随后 `GET /state` | **`location=academy`**、`tick=102` |
| 6b | 第三次 `POST enter` | 同样 `already_online`、`event_seq=38`、`next_tick=108` |
| 6b | 随后 `GET /state` | **`location=academy`**、`tick=108` |

补充：`leave` → `enter` 也不回出生点（`data.location=academy`，`details.resumed=true`）。
补充（同进程长时间观察）：上表第 5 步之后继续在线，tick 从 96 推进到 126，`location` 仍为 `academy`。

结论：

- **API/世界侧没有“动作后回到 market”或“心跳/离线重置位置”的行为**，责任不在 `src/`。
- 当前页面刷新只做 `GET /state?account_id=`，浏览器实测位置保留在 `academy`，**UI 缓存也不是重置源**
  （代理已加 `Cache-Control: no-store`，页面 `fetch(..., {cache:'no-store'})`，旧 `ws_player_session` 启动时清除）。
- 若走查脚本在“刷新”时**重启了 `wsim.api` / `start.sh`，或换了 run/端口**，那就是新 genesis：
  创世预置账号（如 `u_demo`）回到 `market`、`tick` 归零并重新拿到 300 文；运行中新建账号同样是 `market`，但钱包为 0
  （契约 §0.2 明确不 replay）。这类重启必须换 run 名（`start.sh` 已自动换名），否则同一 run 会追加第二段创世。
- 另一条能产生 `market` 的路径是**身份被换掉**：刷新时 localStorage 若没有 `ws_player_account`，页面会预填一个随机账号；
  若此时点了“进入世界”，得到的是新账号在 `market`，不是原账号回退。判断依据看走查日志第 6 步的
  `account_id` / `created_tick` / `wallet.money`：
  - `account_id` 相同、`tick` 很小（0/6）、`money=300` → 重启 API 的新 genesis；
  - `account_id` 变了、`created_tick` 接近当前 tick、`money=0` → UI 用了新账号（身份问题，不是位置重置）。

## 8. 契约仍有歧义 / 契约待补

1. **拒绝状态码**：`docs/player-api.md` §4.3 写 `rejected` 为 HTTP 200；本轮验收裁定为 409。
   代理按裁定归一，body 同时保留 `reject_reason`/`reason_text`。**需要主会话在契约里改口径**。
2. **重启语义**：契约 §0.2 明确 `WorldSession` 不 replay `events.sqlite`，每次启动是新 genesis。
   因此“重启后按 `account_id` 恢复位置”只对**重启 UI 代理**成立；重启 `wsim.api` 后：
   创世账号（如 `u_demo`）回到出生点并重新拿到 300 文，运行中新建账号（如 `u_repro1`）不会出现在新 genesis 里。
   启动脚本用自动换名避免账本污染，但没有 replay 能力。**契约待补：replay 或 API 拒绝脏 run**。
3. **对话双通道**：同一条回复既在写响应 `details.reply`，又在 `/messages`。验收以 `/messages` 为准。
4. **`/state` 不含 NPC 详情**：`present_npcs` 没有目标进度、关系历史；live 模式 NPC 弹层只展示可得字段。
5. **初始资产只发给创世账号**：契约 §2 的 300 文/炊饼×3/粗布×1 只对创世事务里预置的账号（如 `u_demo`）成立；
   运行中新建账号 `player_enter` 返回 `wallet.money=0`、`inventory={}`，`details.endowment.granted_at_genesis=false`。
   验收“进入 300 文”请用创世账号；若要把“新账号也有启动资金”作为产品要求，**契约待补**。
6. **`event_seq` 每动作 +2**：实测 `enter=4`、`move=6`、`talk=8`、`quest_accept=10`、拒绝 12/14/16；
   它不是“动作计数”，验收应写“`event_seq` 增长”，不要写“events +1”。**契约待补：事件序号语义**。
7. **代理扩展码（非契约枚举，页面不当作业务拒绝）**：
   `upstream_unreachable`（网络层，502）、`readonly_unavailable`（只读 run 不可用，503）、
   `status="proposed"`（只读提案，200）。这些不是世界拒绝码，**契约无需补**；若主会话希望统一，可另立 `proxy` 命名空间。

## 9. 边界

- 只改 `ui/player/`：`server.py`、`app.js`、`index.html`、`style.css`、`start.sh`、`README.md`、`spec.md`、`tasks.md`。
- 不改 `src/`、`content/`、`config/`、`tests/`、`runs/` 里的既有内容；不做 git 写操作。
- 页面无登录鉴权；`--host 0.0.0.0` 只建议局域网调试，不要暴露公网。
- Python 3.10+，纯标准库；`sys.dont_write_bytecode=True`，不产生 `__pycache__`。
