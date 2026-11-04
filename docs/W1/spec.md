# W1 服务化基座（定稿规格）

> 方案已由需求方定稿：不换数据库、不改单进程单写者模型、不做分布式。
> 本文件是该方案的决策级实现规格，后续实现与测试以本文件为准。

## 1. 目标与边界

把 `delivery-p3j` 的单机 demo 变成“多人同时在线、重启不丢档”的单进程服务：
- 账号：用户名 + PBKDF2 口令摘要，口令绝不明文落盘。
- 存档：事件日志为唯一事实源；重启回放恢复位置/钱包/物品/任务/成长线/NPC 关系。
- 鉴权：服务模式（存在 `deploy/service.yaml`）下所有写端点必须携带 Bearer 令牌；读端点匿名只读。
- 并发：沿用单进程 `WorldSession.lock` + 单 Writer；两客户端动作串行、互见、不串档。
- 限额：单区域 200 席，第 201 个 `enter` 返回 HTTP 429 + `player_cap_reached`。
- 断线重连：`leave` 记录断线 tick；60 秒内同一会话恢复，超过 60 秒新会话但存档不变。
- 部署文档：启动、备份/恢复、清理僵尸会话。

明确不做：真实支付、跨机分布式、压测（W5）。

## 2. 文件与所有权

新增：
- `src/wsim/api/auth.py`：`AccountStore`（sqlite，stdlib）。
- `src/wsim/api/persistence.py`：回放、`ContentHashMismatch`、退出码常量。
- `src/wsim/api/admin.py`：`purge-zombies` 运维 CLI。
- `deploy/service.yaml`：服务模式标记 + 配置项。
- `deploy/run_service.sh`：启动脚本。
- `docs/AUTH-API-W1.md`：鉴权接口固定契约。
- `docs/DEPLOYMENT-W1.md`：部署/备份/恢复/清理。
- `tests/test_w1_*.py`。

修改：
- `src/wsim/api/session.py`：启动回放、`perform(advance=)`、`resume()`、leave 断线信息。
- `src/wsim/api/server.py`：鉴权中间层、auth 路由、reconnect、429 映射、`/api/service/info`。
- `src/wsim/api/__main__.py`：`--auth/--session-db/--token-ttl/--reconnect-grace/--auth-iterations`、hash 不匹配退出码。

禁止改：`content/**`、`docs/QUICKSTART.md`、`docs/known-limitations.md`、`docs/RELEASE-NOTES-p3i.md`、`dist/`、`dialogue*.py`、`text_guard.py`、任何 tag、`scripts/**`、`ui/**`、`src/wsim/actions/player.py`、`src/wsim/world/**`。

## 3. 账号与令牌（`auth.py`）

### 3.1 摘要
- `salt = secrets.token_bytes(16)`；`hash_hex = pbkdf2_hmac("sha256", password, salt, iterations).hex()`。
- 默认迭代 200000；测试用 1000（可注入）。
- 比较用 `hmac.compare_digest`；未知用户与错误口令统一返回 `auth_invalid_credentials`。
- 用户名规则与 `ACCOUNT_RE` 一致：`^[a-z0-9_-]{3,32}$`（服务端先 `strip().lower()`）。
- 口令长度 6..128。

### 3.2 表
`<run_dir>/service.sqlite`
```sql
users(username TEXT PRIMARY KEY, salt TEXT NOT NULL, hash_hex TEXT NOT NULL,
      iterations INTEGER NOT NULL, created_at REAL NOT NULL);
sessions(id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
         token_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL,
         expires_at REAL NOT NULL, last_seen_at REAL NOT NULL,
         disconnected_at REAL, tick_at_disconnect INTEGER,
         status TEXT NOT NULL);  -- active/disconnected/expired
```
- 令牌：`secrets.token_urlsafe(32)`；数据库只存 `sha256(token).hexdigest()`。
- 默认 `token_ttl=86400` 秒，`reconnect_grace=60` 秒。
- `now_fn` 可注入（默认 `time.time`），所有时间判断走它。
- `RLock` + `check_same_thread=False`。

### 3.3 方法
`register` / `authenticate` / `login` / `create_session` / `resolve_token` /
`refresh` / `disconnect` / `reconnect` / `purge_zombies` / `close`。
`purge_zombies` 把 `disconnected_at` 超过 grace 或 `expires_at` 已过的会话置 `expired`，
返回计数；事件存档完全不受影响。

## 4. 鉴权 HTTP 契约（固定）

所有错误响应统一为：
```json
{"ok": false, "code": "<枚举>", "reject_reason": "<枚举>", "reason_text": "...", "tick": 123}
```
`code` 与 `reject_reason` 始终同值，前端可按任一字段分支。

### 4.1 `POST /api/auth/register`
请求：`{"username": "u_alice", "password": "secret123"}`
- 200：`{"ok": true, "account_id": "u_alice", "username": "u_alice", "created": true}`
- 400 `auth_bad_username` / `auth_bad_password` / `bad_json`
- 409 `auth_username_taken`
- 503 `auth_disabled`（legacy 匿名模式）

### 4.2 `POST /api/auth/login`
请求：`{"username": "u_alice", "password": "secret123"}`
- 200：
```json
{"ok": true, "account_id": "u_alice", "username": "u_alice",
 "session_id": "s:...", "token": "...", "token_type": "Bearer",
 "expires_at": 1790000000, "expires_in": 86400}
```
- 401 `auth_invalid_credentials`
- 400 `auth_bad_username` / `auth_bad_password` / `bad_json`
- 503 `auth_disabled`

### 4.3 `POST /api/auth/refresh`
请求 `{}`，必须带 `Authorization: Bearer <token>`。
- 200（轮换令牌，旧令牌立即失效）：
```json
{"ok": true, "account_id": "u_alice", "session_id": "s:...",
 "token": "<new>", "token_type": "Bearer",
 "expires_at": 1790000000, "expires_in": 86400, "rotated": true}
```
- 401 `auth_missing_token` / `auth_invalid_token` / `auth_expired_token`

### 4.4 `POST /api/auth/logout`（Bearer）
- 200：`{"ok": true, "logged_out": true, "session_id": "s:..."}`
- 401 同上。

### 4.5 `GET /api/auth/me`（Bearer）
- 200：`{"ok": true, "account_id", "session_id", "expires_at", "status", "disconnected": bool}`
- 401 同上。

### 4.6 `POST /api/player/reconnect`（Bearer）
请求 `{}` 或 `{"account_id": "u_alice"}`。
- 200：
```json
{"ok": true, "resumed": true, "new_session": false,
 "session_id": "s:...", "token": "<可用令牌>", "token_type": "Bearer",
 "expires_at": 1790000000, "tick": 120, "tick_at_disconnect": 114,
 "reconnect_grace_seconds": 60, "state": { ...与 /api/player/state 同结构... }}
```
- 60 秒内：`resumed=true, new_session=false`，同一 `session_id`，不推进 tick，位置/钱包/任务原样。
- 超过 60 秒：`resumed=true`（存档仍在），`new_session=true`，新 `session_id` + 新 `token`；
  旧会话标记 `expired`，旧令牌失效。
- 401 同上；403 `auth_account_mismatch`（body account 与令牌不一致）。

### 4.7 错误码枚举（稳定，不得改名）
`auth_missing_token` / `auth_invalid_token` / `auth_expired_token` /
`auth_account_mismatch` / `auth_bad_username` / `auth_bad_password` /
`auth_username_taken` / `auth_invalid_credentials` / `auth_disabled` / `bad_json`。

### 4.8 写端点鉴权矩阵
服务模式下，以下端点无令牌一律 401 `auth_missing_token`：
`/api/player/action`、`/api/player/enter`、`/api/player/heartbeat`、`/api/player/leave`、
`/api/player/move`、`/api/player/talk`、`/api/player/trade`、`/api/player/quest/accept`、
`/api/player/quest/deliver`、`/api/player/reconnect`。
令牌有效但 body `account_id` 与令牌账号不一致 → 403 `auth_account_mismatch`。
读端点 `/api/player/state|status|quests|messages|intents/<id>` 匿名为只读，不受令牌影响。
`GET /api/service/info` 匿名：`{"ok": true, "auth_required": bool, "tick": n, "run": "..."}`。

## 5. 存档回放与内容哈希（`persistence.py` + `session.py`）

启动流程：
1. 打开 `events.sqlite`。
2. 若 `count() > 0`：
   - 读取 `meta["content_hash"]`；与 `content.content_hash` 不等 → 抛 `ContentHashMismatch`
     （含 `stored` / `current` / `run_dir`）。
   - 新建 `WorldState(locations=content.locations)`，对每条事件：
     `state.tick = event.tick`；若 `outcome == "ok"`，`apply_effects(state, effects)`。
     **不重新 bootstrap、不发放初始资产、不补委托。**
   - 重建 `Writer._player_ticks`（所有 `ok` 且 actor 以 `player:` 开头的最大 tick）。
3. 若 `count() == 0`：保持原 bootstrap 路径。
4. 写入/刷新 `meta`（seed/content_hash/player_id）。

拒绝启动契约（固定）：
- 退出码 `3`（`EXIT_CONTENT_HASH_MISMATCH`）。
- 错误码 `content_hash_mismatch`。
- stderr 单行 JSON：
```json
{"ok": false, "error": "content_hash_mismatch",
 "stored_content_hash": "...", "current_content_hash": "...", "run_dir": "..."}
```
- 不启动监听、不写任何事件。

## 6. 模式隔离（安全边界）

`resolve_auth_required(root, mode)`：
- `auto`：`(root / "deploy" / "service.yaml").exists()` 为真才 `True`；
- `on`：恒 `True`；`off`：恒 `False`。

`create_server(session, ..., accounts=None, auth_required=False, ...)` 把两个值存到 server。
处理器只有 **一个** 写入口闸门：`auth_required` 为真且 `accounts` 非空时，写请求必须通过令牌校验；
不存在“配置存在但仍匿名写”的旁路。legacy（无 yaml）时 `accounts=None`，保持匿名可用（selfcheck 依赖）。
`--auth on` 即使没有 yaml 仍强制鉴权。

## 7. 会话、限额、重连

- `WorldSession.perform(..., advance=True)`；`_perform` 仅在 `advance` 为真时调用 `_advance()`。
- `WorldSession.resume(account, ...)`：档案离线则 `perform("enter", advance=False)`，已在线则只取快照；
  返回 `session_id/new_session/tick/tick_at_disconnect/state`。
- `leave` 成功且带令牌时，HTTP 层调用 `accounts.disconnect(session_id, tick)`。
- 200 席沿用 `MAX_REGION_PLAYERS=200`；`player_cap_reached` 映射 HTTP 429。
  `endowment_cap_reached` 同样 429。其他业务拒绝保持 200。

## 8. 测试与验收映射

| 验收项 | 测试文件 | 断言 |
|---|---|---|
| 口令不落盘 | `test_w1_auth.py` | sqlite 中无明文口令；错误口令统一拒绝 |
| 重启 6 类字段 0 丢失 | `test_w1_persistence.py` | 位置/钱包/物品/任务/声望+技能+资产/NPC 关系逐项断言 |
| 401 全覆盖 | `test_w1_http_auth.py` | 遍历 `_POST_ROUTES`，缺失/无效/过期令牌均 401 |
| 两模式互不串 | `test_w1_mode_isolation.py` | 有 yaml→匿名写 401；无 yaml auto→匿名写成功；`--auth on` 无 yaml→401 |
| hash 不匹配拒绝启动 | `test_w1_hash_mismatch.py` | 异常码/退出码 3/stderr 日志行 |
| 并发 20 轮 0 串档 | `test_w1_concurrency.py` | 双客户端交错、互见、钱包/任务独立、同 NPC 关系不脏写 |
| 201 席 429 | `test_w1_capacity.py` | 第 201 个返回 429 + `player_cap_reached` |
| 60s 重连 | `test_w1_reconnect.py` | 假时钟：60s 内同会话恢复；>60s 新会话、存档仍在 |

最终：`pytest` 全绿并记录计数；`./scripts/selfcheck.sh` 4/4；旧 golden 基线不改。
