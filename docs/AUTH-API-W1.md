# W1 鉴权接口契约（固定）

> 前端（W4）按本文件接入。字段名与错误码枚举已冻结；改名视为破坏性变更。
> 所有响应均为 JSON；错误体统一带 `code` 与 `reject_reason`（两者同值）。

## 1. 最小样例

### 注册

```bash
curl -s -X POST http://127.0.0.1:8020/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"u_alice","password":"secret123"}'
```

```json
{"ok": true, "account_id": "u_alice", "username": "u_alice", "created": true}
```

### 登录

```bash
curl -s -X POST http://127.0.0.1:8020/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"u_alice","password":"secret123"}'
```

```json
{"ok": true, "account_id": "u_alice", "username": "u_alice",
 "session_id": "s:1f...", "token": "…", "token_type": "Bearer",
 "expires_at": 1790000000, "expires_in": 86400}
```

### 刷新（轮换令牌）

```bash
curl -s -X POST http://127.0.0.1:8020/api/auth/refresh \
  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{}'
```

```json
{"ok": true, "account_id": "u_alice", "session_id": "s:1f...",
 "token": "<new>", "token_type": "Bearer",
 "expires_at": 1790000000, "expires_in": 86400, "rotated": true}
```

旧令牌立即失效（之后返回 401 `auth_invalid_token`）。

### 我是谁 / 注销

```bash
curl -s http://127.0.0.1:8020/api/auth/me -H 'Authorization: Bearer <token>'
# {"ok": true, "account_id": "u_alice", "username": "u_alice",
#  "session_id": "s:...", "expires_at": 1790000000,
#  "status": "active", "disconnected": false}

curl -s -X POST http://127.0.0.1:8020/api/auth/logout \
  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{}'
# {"ok": true, "logged_out": true, "session_id": "s:..."}
```

### 重连

```bash
curl -s -X POST http://127.0.0.1:8020/api/player/reconnect \
  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{}'
```

```json
{"ok": true, "resumed": true, "new_session": false,
 "session_id": "s:...", "token": "<token>", "token_type": "Bearer",
 "expires_at": 1790000000, "tick": 120, "tick_at_disconnect": 114,
 "reconnect_grace_seconds": 60,
 "state": { "...": "与 GET /api/player/state 同结构" },
 "player": { "...": "state.player 的便捷副本" },
 "quests": ["..."]}
```

- 断线 ≤ 60 秒：`new_session=false`，同一 `session_id` / token，tick 不推进。
- 断线 > 60 秒：`new_session=true`，返回新 `session_id` 与新 `token`；旧会话/旧令牌失效，
  但事件存档（位置/钱包/任务/成长线/NPC 关系）原样保留。
- 客户端未调用 `leave`（崩溃/断网）时，以服务端记录的 `last_seen_at` 判断：
  60 秒内重连沿用原会话，超过 60 秒按新会话处理；存档同样不受影响。

## 2. 错误码枚举（冻结）

| code / reject_reason | HTTP | 含义 |
|---|---|---|
| `auth_missing_token` | 401 | 缺少 `Authorization: Bearer` |
| `auth_invalid_token` | 401 | 令牌不存在、已轮换或已注销 |
| `auth_expired_token` | 401 | 令牌过期 |
| `auth_account_mismatch` | 403 | body 的 `account_id` 与令牌账号不一致 |
| `auth_bad_username` | 400 | 用户名非 3-32 位小写字母/数字/下划线/连字符 |
| `auth_bad_password` | 400 | 口令长度不在 6-128 |
| `auth_username_taken` | 409 | 用户名已注册 |
| `auth_invalid_credentials` | 401 | 用户名或口令错误（不区分两者） |
| `auth_disabled` | 503 | legacy 匿名模式下调用了鉴权接口 |
| `bad_json` | 400 | 请求体不是 JSON 对象 |

错误体样例：

```json
{"ok": false, "code": "auth_expired_token", "reject_reason": "auth_expired_token",
 "reason_text": "令牌已过期；请重新登录。", "tick": 120}
```

## 3. 写端点鉴权矩阵

服务模式下以下端点**全部**必须带令牌，缺失即 401 `auth_missing_token`：

`/api/player/action`、`/api/player/enter`、`/api/player/heartbeat`、`/api/player/leave`、
`/api/player/move`、`/api/player/talk`、`/api/player/trade`、`/api/player/quest/accept`、
`/api/player/quest/deliver`、`/api/player/reconnect`。

读端点保持匿名只读：`/api/player/state`、`/api/state`、`/api/player/status`、`/api/player/quests`、
`/api/player/messages`、`/api/player/intents/<id>`、`/api/service/info`。

`/api/state` 是 `/api/player/state` 的只读别名，二者等价（同一 handler、同一鉴权行为、响应体逐字节相同）。

`GET /api/service/info` 返回：

```json
{"ok": true, "auth_required": true, "tick": 120, "run": "service", "profile_version": 1}
```

## 4. 模式与安全边界

- `--auth auto`：存在 `deploy/service.yaml` → 强制鉴权；不存在 → legacy 匿名模式。
- `--auth on` / `--auth off` 显式覆盖。
- 强制鉴权时，即使账号库未配置也失败关闭（503 `auth_disabled`），不存在匿名写旁路。
- legacy 模式仅供本地 selfcheck/试玩，不得对外暴露。

## 5. 席位与业务错误

单区域上限 200 席。第 201 个 `enter` 返回 HTTP **429**：

```json
{"ok": false, "code": "player_cap_reached", "reject_reason": "player_cap_reached",
 "reason_text": "本区域玩家席位已满，暂时无法创建新账号。", "tick": 120}
```

初始资产发放总额达上限同理返回 429 `endowment_cap_reached`。其他业务拒绝仍为 200，
按 `reject_reason` 判断。
