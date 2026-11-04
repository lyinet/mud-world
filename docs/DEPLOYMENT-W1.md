# W1 部署与运维（服务化基座）

本文覆盖：启动服务、注册/登录、备份与恢复存档、清理僵尸会话、内容哈希拒绝启动。

## 1. 前置

- Python ≥ 3.10；仓库根目录含 `content/` 与 `config/`。
- 依赖仅标准库 + PyYAML/pytest（测试用）。
- 服务是**单进程单写者**：不要用多进程/多 worker 同时写同一个 run。

## 2. 启动

```bash
cd /path/to/world-sim
export PYTHONPATH="$PWD/src"
./deploy/run_service.sh
# 默认: RUN=service HOST=127.0.0.1 PORT=8020
```

或手动：

```bash
PYTHONPATH=src python3 -m wsim.api \
  --root . --run service --host 127.0.0.1 --port 8020 \
  --auth auto --token-ttl 86400 --reconnect-grace 60
```

启动后 stdout 单行 JSON 包含 `"auth_required": true`。健康检查：

```bash
curl -s http://127.0.0.1:8020/api/service/info
# {"auth_required": true, "ok": true, "profile_version": 1, "run": "service", "tick": 0}
```

`deploy/service.yaml` 是服务模式标记：存在则 `--auth auto` 强制鉴权；删除它即回到
legacy 匿名模式（仅本地试玩，**不要对外暴露**）。接口契约见
[docs/AUTH-API-W1.md](AUTH-API-W1.md)。

## 3. 账号与令牌

```bash
curl -s -X POST http://127.0.0.1:8020/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"u_alice","password":"secret123"}'

curl -s -X POST http://127.0.0.1:8020/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"u_alice","password":"secret123"}'
```

登录返回的 `token` 用于所有写请求：`Authorization: Bearer <token>`。
口令只以 PBKDF2-HMAC-SHA256 摘要存于 `service.sqlite`，令牌只存 SHA-256 摘要。

## 4. 数据文件

`runs/<run>/` 下：

| 文件 | 内容 | 备份 |
|---|---|---|
| `events.sqlite` | 世界事件日志（唯一事实源：位置/钱包/物品/任务/成长线/NPC 关系） | 必须 |
| `events.sqlite-wal` / `-shm` | sqlite WAL；停机后一般已合并 | 停机后可选 |
| `service.sqlite` | 账号、令牌、断线会话 | 建议 |
| `mcp_audit.jsonl` | 审批审计 | 可选 |

## 5. 备份

```bash
# 1) 先停服（SIGTERM 会触发 server_close 与 sqlite close）
kill -TERM <pid>

# 2) 整个 run 目录打包（含账号库）
tar -czf backup-$(date +%Y%m%d-%H%M%S).tgz runs/service

# 3) 校验事件数与内容哈希
sqlite3 runs/service/events.sqlite \
  "SELECT (SELECT COUNT(*) FROM events), (SELECT value FROM meta WHERE key='content_hash');"
```

不要只拷 `events.sqlite` 而漏 `service.sqlite`：前者是存档，后者是账号与会话；
两者独立，缺账号库会导致所有人需要重新注册/登录，但世界存档仍在。

## 6. 恢复

```bash
# 1) 停服
kill -TERM <pid>

# 2) 保留现场，解包到临时目录并替换
mv runs/service runs/service.broken-$(date +%s)
mkdir -p runs
tar -xzf backup-YYYYMMDD-HHMMSS.tgz -C runs

# 3) 启动并检查
PYTHONPATH=src python3 -m wsim.api --root . --run service --auth auto --port 8020
```

恢复后必须与备份时的 `content/` 完全一致。若有任何内容改动，服务会**拒绝启动**：

- 退出码：`3`
- 错误码：`content_hash_mismatch`
- stderr 单行 JSON：

```json
{"ok": false, "error": "content_hash_mismatch",
 "stored_content_hash": "…", "current_content_hash": "…",
 "run_dir": "runs/service"}
```

此时不要删档重开；先恢复与存档匹配的 `content/`（或用同一内容包重新部署）。

## 7. 清理僵尸会话

- 客户端正常离开（`POST /api/player/leave`）会记录断线时刻与 tick。
- 断线 60 秒内 `POST /api/player/reconnect` 沿用原会话；超过 60 秒自动作废旧会话并新建，
  事件存档不受影响。
- 令牌默认 24 小时过期。运维可批量把“断线超宽限 / 令牌过期”的会话置为 `expired`：

```bash
PYTHONPATH=src python3 -m wsim.api.admin purge-zombies \
  --run-dir runs/service --reconnect-grace 60
# {"ok": true, "purged": N, "session_db": ".../service.sqlite", "reconnect_grace_seconds": 60}
```

该命令只改 `service.sqlite` 的 `sessions.status`，**不会删除或修改** `events.sqlite`。
可放进 cron（例如每小时一次）。清理后玩家仍可用原账号重新登录，存档原样恢复。

## 8. 停机

`SIGINT` / `SIGTERM` 会停止监听并关闭 sqlite；建议总在停机后再备份。
强杀（SIGKILL）后 WAL 可能仍在，启动时 sqlite 会自动恢复；仍建议恢复前先做一份备份。

## 9. 已知边界（W1 范围）

- 单进程、单区域 200 席；第 201 个 `enter` 返回 HTTP 429 + `player_cap_reached`。
- 不做真实支付、跨机分布式、压测（W2/W5）。
- legacy 匿名模式与 W4 前端登录流程不互通；对外部署必须保留 `deploy/service.yaml`。
