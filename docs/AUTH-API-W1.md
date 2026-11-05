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

### 读端点的可见面（服务模式）

读端点不强制登录，但匿名可见面收窄为公共摘要；私有字段只在带本人令牌时返回。

- **带令牌**：先校验令牌；通过后 `account_id` 一律以令牌账号为准。请求显式传了
  不同账号 → **403 `auth_account_mismatch`**（与写端点同规则），返回本人完整状态。
- **无令牌**：仍 **200**，但只返回公共摘要。字段白名单固定为：
  `ok`、`name`、`place`、`activity`、`idle_seconds`、`online`、`sect_name`、`shop_name`、`server_tick`。
  白名单之外的键（`wallet`（含 `items`）、`skills`、`stamina`、`relations`、`active_quests`、
  `active_quests_detail`、`growth`、`today`、`last_intent_tick`）**不会出现**；白名单是
  正向实现，将来新增私有字段默认不可见。
- `/api/player/messages` 匿名 → 200 `messages: []`；带令牌 → 本人私聊。
- `/api/player/quests` 匿名 → 200 只含公共开放任务（无奖励明细、无任何人的已接任务）。
- `/api/player/intents/<id>` **例外：服务模式必须带令牌**。它是个人动作结果，且
  `intent_id` / `request_id` 可枚举，匿名 → **401 `auth_missing_token`**；令牌账号与 actor
  不一致 → **403 `auth_account_mismatch`**。
- `/api/player/status`、`/api/player/state` 同一口径：匿名公共摘要，本人完整状态。

`/api/state` 是 `/api/player/state` 的只读别名，二者等价（同一 handler、同一鉴权行为、响应体逐字节相同）。

- **legacy 模式（`--auth off`）行为不变**：匿名仍可读完整状态，仅限本地试玩/selfcheck。

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

## 6. 登录限流与防爆破

按 `(ip, account)` 统计失败次数（TCP 对端地址，不信任 `X-Forwarded-For`）：

- 5 分钟窗口内失败 5 次 → 锁定 15 分钟；
- 解除后再次失败 5 次 → 锁定 1 小时；
- 再解除后失败 5 次 → 锁定 24 小时（上限）。
- 锁定期内所有登录（**包括正确口令**）返回 HTTP **429**：

```json
{"ok": false, "code": "too_many_attempts", "reject_reason": "too_many_attempts",
 "reason_text": "登录失败次数过多；请稍后再试。", "tick": 120, "retry_after": 900}
```

并带 `Retry-After: 900` 响应头。锁定判定在 PBKDF2 之前，不消耗 CPU。
成功登录清零该 `(ip, account)` 的失败记录与退避级别。

同一 IP 的锁定只作用于该 IP + 该账号组合，不影响其他 IP；反过来，一个 IP
被锁也不会波及其他账号。运维可解锁：

```bash
python3 tools/unlock_account.py --account u_demo            # 解锁该账号全部 IP
python3 tools/unlock_account.py --account u_demo --ip 10.0.0.8
python3 tools/unlock_account.py --all
```

## 7. 令牌吊销

```bash
python3 tools/revoke_sessions.py --account u_demo
python3 tools/revoke_sessions.py --all
```

吊销后该账号的活动/断线会话立即变为 `revoked`，旧令牌在 `resolve_token` /
`refresh` / `reconnect` 一律返回 **401 `auth_invalid_token`**（不是
`auth_expired_token`）。吊销与审计在同一次事务写入 `service.sqlite` 的
`audit_log` 表（`action=revoke_sessions`、`account`、`ip`、`detail` 含吊销数量）。
选择写库而不是日志文件的理由：审计与会话状态同事务提交，不会出现“吊销成功但
审计丢失”；`service.sqlite` 本来就在备份/恢复工具覆盖范围内，无需额外日志轮转。

