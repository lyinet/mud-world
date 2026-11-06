# XKX world-sim 生产就绪审计

> 审计对象：`/Users/lyinet/projects/XKX/world-sim`
> 审计方式：只读代码 + 本机实测（未修改任何源码、测试或既有文档）
> 审计时间：2026-11-06 01:13 – 01:55 CST
> 代码基线：HEAD `6c84cb0`（`docs(backup): …`）；工作树另有未提交的 `src/wsim/api/server.py`（+132 行，`/api/metrics`）与 `ui/player/*` 改动，`src/wsim/api/metrics.py`、`.github/` 仍为未跟踪文件
> 环境：macOS 10 核 / 16 GB、Python 3.12.7、Docker Server 29.5.2、单机单进程单写者
> 证据目录：`reports/production-readiness/`（本文所有数字均可用其中 JSON/日志复核）

## 0. 口径与限制

- 所有“实测”均来自本机本次运行；命令与时间见附录 A。**没有引用任何他人报告的数字**。
- 容量数字为**工作树代码**（含未提交 metrics 改动）在同一台机器上的复测，口径与 `tools/load_test.py` 默认阈值一致（`/api/state` P95 < 300ms），未改阈值。
- 压测机与被压服务同机（10 核），会互相争抢 CPU；因此 P95 偏悲观，但**瓶颈结论（单核饱和、全局锁）不受影响**。
- 标注“未验证”的项，本轮没有做，不做推测。
- 审计期间有并行会话在仓库内提交/改动（HEAD 由 `af4b64c` 推进到 `6c84cb0`）。已用 `git diff af4b64c..HEAD -- src/wsim/api src/wsim/world` 复核：`auth.py`、`session.py`、`store.py`、`writer.py`、`mcp/audit.py` **无改动**，仅新增 `src/wsim/actions/player_sect.py`。

---

## 1. 汇总

| 档位 | 项数 | 合计人日 | 说明 |
|---|---:|---:|---|
| **P0**（不解决不能对外） | 7 | **10.5** | 数据可恢复性、真实健康、传输安全、认证边界、资源上限、发布可回滚 |
| **P1**（生产级必备） | 8 | **13.0** | 可观测/告警、CI 门禁、迁移、账号生命周期、容量护栏 |
| **P2**（规模化） | 5 | **41.0** | 多区扩容、写路径重构、管理后台、高可用、在线成本闸 |
| **合计** | **20** | **64.5** | 不含日常运维与内容运营 |

**最关键的 3 项**

1. **P0-1 备份默认生效且失败可见**：备份工具本身已验证可恢复，但默认部署根本不跑备份，且失败被 `|| true` 吞掉 → 当前 RPO 实际为“无”。
2. **P0-2 丢库/空库必须 fail-closed**：`events.sqlite` 缺失时服务会静默新建空世界，同时 `/api/health` 返回 `ok:true` → 卷挂错就是静默清档。
3. **P0-3 公网必须 TLS**：当前 8020/8030 明文对外，口令与 Bearer 令牌裸奔；反代是唯一正确解。

---

## 2. P0（不解决不能对外）

### P0-1 备份默认不生效，失败被静默吞掉，且无异地副本

**现状证据**

- 备份工具本身可用：`tools/backup_snapshot.py`（HEAD 已跟踪）执行
  `python tools/backup_snapshot.py --root . --run prod-audit-50 --backup-dir /tmp/xkx-audit/bk --no-prune`
  → `manifest.json` 记录 `events.sqlite` 2117 事件、seq `1..2117` 连续、`integrity_check=ok`、`event_hash=1a03c530…`，`verification_ok=true`；备份 API 单步 8.615ms，含重放校验总耗时 503.9ms（`reports/production-readiness/evidence/backup-manifest-sample.json`）。
- 但**默认不跑**：`deploy/docker-compose.yml:46-51` 的 `backup` 服务在 `profiles: ["backup"]` 下，`docker compose up -d` 不会启动它；宿主机同样没有安装 `scripts/backup_loop.sh`（`crontab -l` 返回 `no crontab for lyinet`，`~/Library/LaunchAgents` 无相关项）。
- **失败不可见**：`deploy/docker-compose.yml:57` 命令为
  `python /app/tools/backup_snapshot.py … || true; sleep …`。实测旧镜像 `xkx/world-sim:w5`（演练时该镜像构建于 `COPY tools` 之前，现已被重建）执行该命令返回 **exit 2 / `No such file or directory`**，`|| true` 直接吞掉，容器仍 `Up`。
- **无异地副本**：备份只写命名卷 `xkx-w5-backups`（同机同盘），宿主/对象存储同步未实现；备份目录 chmod 700/600（`tools/backup_snapshot.py:324,356`）只能防同机越权读。
- 轮转策略：滚动 48h + 最近 7 个 UTC 日各 1 份（`tools/backup_snapshot.py:47-51`），意味着**最坏可接受丢失窗口 6 小时**（`BK_INTERVAL:-21600`），且没有“备份过旧”告警。

**缺口**：默认部署 RPO=∞；备份失败无信号；无异地；无备份时效监控。

**最小达标方案**

1. 默认启动备份：在 `world` 服务加 sidecar/cron，或让 `backup` profile 变默认；至少在 `entrypoint.sh` 增加一次性启动备份 + 定时器。
2. 去掉 `|| true`，失败退出非 0 并写 `backup_status.json`（最后成功时间、耗时、错误）。
3. 每日把最新备份同步到异地（`rclone`/对象存储/另一台主机），保留 7 天。
4. 监控“最后成功备份时间 > 7h”告警（见 P1-1）。

**人日**：2.0

**验收方法**

- `docker compose up -d` 后 10 分钟内 `backups/` 出现带 manifest 的目录；停止 `world`，用该备份恢复到空卷，`tools/restore_verify.py` 退出码 0。
- 人为把 `/app/tools` 改名 → 备份容器必须变为 unhealthy/重启，且告警触发；不得仍显示 `Up`。
- 异地副本可在另一台机器拉取并完成同一恢复校验。

---

### P0-2 `events.sqlite` 缺失时静默新建空世界，且健康检查仍报 `ok:true`

**现状证据**

- 实测：删除 `runs/prod-audit-missing2/events.sqlite` 后启动服务（`PYTHONPATH=src .venv/bin/python -m wsim.api --root . --run prod-audit-missing2 --auth on --port 8169`，2026-11-06 01:38）
  → 进程正常启动，`runs/prod-audit-missing2/events.sqlite` 被重新创建，`events` 表 **count=3**（创世事件），`/api/health` 返回 `{"ok": true, …}`。证据：`reports/production-readiness/missing-db-startup.log`。
- 健康响应是硬编码常量，不读取数据库：`src/wsim/api/server.py:286-302`（`"ok": True, "mode": "live", "readonly": False …`），无 `db_ok`/`last_seq`/`disk_free`。
- 对照：DB 文件损坏时会快速失败退出（`sqlite3.DatabaseError: file is not a database`，`reports/production-readiness/corrupt-db-startup.log`）——**损坏能发现，缺失不能**，这是最危险的组合。
- `deploy/healthcheck.py` 只探 `/api/service/info` 与 UI `/api/health`，同样不查 DB/seq/磁盘。

**缺口**：卷未挂载、路径写错、误删文件 → 服务“健康”地开一个空世界，旧档事实性丢失且无告警。

**最小达标方案**

1. 启动时若 `run_dir` 存在 `service.sqlite`/ambient 痕迹但 `events.sqlite` 缺失（或 `events` 表为空但 `meta.content_hash` 存在），**拒绝启动，退出码非 0**；仅允许显式 `--allow-new-world` 新建。
2. 启动即校验 `PRAGMA integrity_check` 与 seq 连续性，失败 fail-closed。
3. `/api/health` 增加 `db_ok`、`event_count`、`last_seq`、`wal_bytes`、`disk_free_bytes`、`last_backup_age_s`；任一异常返回 503。
4. `deploy/healthcheck.py` 改用上述字段判定。

**人日**：1.0

**验收方法**

- 删除 `events.sqlite`（保留 `service.sqlite`）→ 启动退出码非 0，日志明确“missing events db”；`/api/health` 不返回 200。
- 正常库启动 → `db_ok=true`、`event_count` 与实际一致；写入一条事件后 `last_seq` +1。
- 磁盘剩余 <10% 时健康返回 503（可用临时小分区/环境变量模拟）。

---

### P0-3 无 TLS、无反向代理，口令与令牌明文传输

**现状证据**

- 直接对 8020 发 TLS 握手：`ssl` 连接返回 `WRONG_VERSION_NUMBER`（对端是明文 HTTP），`reports/production-readiness/security-probe.json` 的 `tls` 记录为失败。
- `deploy/` 目录只有 `Dockerfile / compose / entrypoint / healthcheck / run_service.sh / service.yaml / wait_for.py / crash_drill.sh`，**无 nginx/caddy/traefik 配置**；compose 直接把 `${API_PORT:-8020}` 与 `${UI_PORT:-8030}` 发布到宿主（`deploy/docker-compose.yml:17-20`）。
- 登录响应体含明文 Bearer（`security-probe.json` 的 `auth_baseline.login.body`），全程 HTTP。
- 同时无安全响应头（无 HSTS、无 `X-Content-Type-Options` 等）；CORS 预检 `OPTIONS` 返回 **501**、无 `Access-Control-Allow-Origin`（同源部署下可用，但不是安全加固）。

**缺口**：任何同链路（公共 Wi-Fi、代理、日志）都能拿到账号口令与 24h 有效令牌。

**最小达标方案**

1. 前置 Caddy（自动证书）或 nginx：仅暴露 443，8020/8030 只监听 127.0.0.1/内网。
2. 强制 HTTPS + HSTS；HTTP 301 到 HTTPS。
3. 加基础安全头；限制请求体（与 P0-5 合并落地）。
4. 容器端口映射改为 `127.0.0.1:8020:8020`，公网只开反代端口。

**人日**：1.5

**验收方法**

- 外部仅 443 可达，`http://` 301；`ssllabs`/`testssl.sh` 无高危项。
- 直接访问宿主 8020 被拒绝（连接拒绝或仅回环）。
- 抓包/日志中不再出现 `Authorization` 明文与登录口令。

---

### P0-4 登录/注册无任何限流与防爆破

**现状证据**

- 实测同一账号连续 50 次错误口令：**50 次全部 401**，无 429、无锁定、无延迟增长；延迟 min 29.58 / median 30.64 / max 68.80ms（`reports/production-readiness/security-probe.json`，2026-11-06 01:18）。
- 实测连续注册 30 个账号：**30 次全部 200**，无限流。
- 代码中无 rate limit 实现：`grep -rn "rate_limit|throttle|lockout|backoff" src/wsim/api/` 无命中；认证路由仅 4 个（`src/wsim/api/server.py:39-44`），`_do_POST` 直接进入 `_handle_auth`（`server.py:480-485`）。
- 每次失败口令都执行 PBKDF2 200,000 次（`src/wsim/api/auth.py:20`），实测约 30ms CPU/次 → **50 次 ≈ 1.5 CPU 秒**，是天然的 CPU 放大点，与 P0-6 的全局锁叠加可拖垮在线玩家。

**缺口**：暴力破解、账号枚举（注册 409/登录 401 差异）、批量注册、CPU DoS 均无成本。

**最小达标方案**

1. 按 `IP + 账号` 双维度令牌桶：登录 5 次/分钟、注册 3 次/分钟/IP，超限 429 + `Retry-After`。
2. 连续失败 10 次锁定账号 15 分钟（成功登录清零），锁定事件写审计。
3. 认证失败延迟下限（如 200ms 常量时间）与失败计数指标。
4. 反代层再加一层连接/请求限速（与 P0-3 同批）。

**人日**：1.5

**验收方法**

- 第 6 次错误口令返回 429 且带 `Retry-After`；等待窗口后恢复。
- 第 11 次触发账号锁定，正确口令也返回 429/423，15 分钟后自动恢复。
- 30 次注册在第 4 次起被 429；正常用户不受影响（灰度白名单可验证）。

---

### P0-5 请求体无大小上限，可被单连接放大内存

**现状证据**

- `src/wsim/api/server.py:212-218`：`_body()` 直接 `self.rfile.read(int(Content-Length))`，无上限、无超时、无 header 限制。
- 实测 5 MB 登录体：401，39ms；**20 MB 登录体：401，28ms**（`reports/production-readiness/body-limit-probe.json`，2026-11-06 01:19）——服务端完整读入并解析，无 413。
- 解析发生在处理线程内、鉴权后/前均有可能（`_handle_write` 在鉴权后读体，认证路由在 `_handle_auth` 内读体），N 个 20MB 并发即可打满内存。

**缺口**：内存 DoS、慢速攻击（无 socket timeout）、超大 JSON 解析 CPU。

**最小达标方案**

1. `MAX_BODY_BYTES=64KB`（玩家动作实际 <10KB），超限直接 413 且不读体。
2. `BaseHTTPRequestHandler.timeout=10s`，限制 header 数量/长度。
3. 反代层再设 `client_max_body_size 64k`。

**人日**：0.5

**验收方法**

- 100KB 请求体 → 413，连接不中断服务；20MB → 413 且 RSS 无明显上升。
- 慢速发送 header/body 的连接 10s 被断开。

---

### P0-6 审计日志与内存结构无界增长（磁盘打满 + 长跑 OOM 风险）

**现状证据**

- `src/wsim/mcp/audit.py:44,49`：`self.records: list[AuditRecord] = []` 每次写追加，无上限、无轮转；审计文件每次 open-append-close，无按大小切分。
- 实测 `runs/prod-audit-50/mcp_audit.jsonl` = **1,567,682 B / 2,117 事件 = 740.5 B/事件**（`reports/production-readiness/disk-growth-projection.json`）。
- `src/wsim/world/writer.py:28` `_seen` 保存全部 Event；`src/wsim/api/session.py:298-299` `requests`/`intents` 缓存只写不清（`session.py:1286-1297`）。
- 实测 RSS 在 60–70s 压测内单调上升：50 席 44.78→46.50MB、150 席 39.09→70.75MB、199 席 39.31→**82.28MB 峰值**（`reports/production-readiness/capacity-*.json`）。150 席约 **+0.49 MB/s**，是否收敛**未验证**（只跑了 60–70s）。
- Docker 日志 `json-file` 未设 `max-size/max-file`（`deploy/docker-compose.yml` 无 `logging` 段）；UI 每请求写 stderr（`ui/player/server.py:1689` 起），容器日志同样无上限。

**缺口**：24/7 运行下 `mcp_audit.jsonl`、容器日志、进程内缓存三者都只增不减；磁盘/内存耗尽没有护栏。

**最小达标方案**

1. `mcp_audit.jsonl` 按天/按大小轮转（如 100MB×10），保留 30 天，压缩归档。
2. `_seen`、`requests`、`intents` 改为有界 LRU（如各 10k 条）或直接查库。
3. compose 增加 `logging: json-file: max-size: 50m, max-file: 5`；日志目录加磁盘配额。
4. 指标暴露 `mcp_audit_bytes`、`process_rss`，并设阈值告警（P1-1）。

**人日**：2.0

**验收方法**

- 连续写入 10 万事件（可用 `tools/load_test.py --players 50 --duration 600`）后：`mcp_audit.jsonl` 不超过配置上限且生成归档；RSS 增长收敛（60 分钟窗口斜率 < 1MB/min）。
- 容器日志目录超过上限后自动滚动，磁盘占用稳定。

---

### P0-7 发布不可追溯、回滚靠手工，且无数据库版本机制

**现状证据**

- compose 固定镜像 `xkx/world-sim:w5`（`deploy/docker-compose.yml:15`），这是**可变标签**；`Dockerfile` 支持 `XKX_VERSION/XKX_REVISION` 构建参数，但 compose 未传（`deploy/Dockerfile:8-20`），镜像内 `/app/VERSION` 实测为 `dev`。
- 实测回滚可用：用同一数据卷从新镜像切到上一版镜像，健康 4.34s→5.20s，登录 200、`/api/state` 200、心跳写入成功，事件 12→14 不丢（`reports/production-readiness/rollback-drill.json`，2026-11-06 01:34）。但这是**本机手工 tag**（`rb-current`/`rb-previous`），仓库没有发布流程/tag 规范/镜像仓库。
- **内容 hash 是硬门槛**：内容变更后 `content_hash` 变，旧镜像会以退出码 3 拒绝启动（`docs/OPS-RUNBOOK.md` §2.3）。回滚到旧版本时若 `content/` 也回退，必须同时回退内容并确认与存档 `meta.content_hash` 一致，否则起不来。
- 无迁移机制：全仓库无 `PRAGMA user_version` / `schema_version`（`grep -rn "user_version|schema_version" src/ tools/ deploy/` 仅命中备份 manifest 的 `schema_version`）；建表全是 `CREATE TABLE IF NOT EXISTS`（`src/wsim/api/auth.py:42,49`、`src/wsim/world/store.py:16,32`）。
- CI：仓库中**没有已入库的 CI**。`.github/workflows/ci.yml` 是未跟踪文件（`git ls-files .github` 为空），HEAD `6c84cb0` 不含任何 workflow；因此当前合并/发布没有任何自动门禁。该 workflow 本身也未在 GitHub Actions 上跑过（**未验证**）。

**缺口**：无法从制品反查 commit；旧版本能否启动取决于内容 hash；schema 演进无版本号，未来改表只能“改代码+祈祷”。

**最小达标方案**

1. 发布流水线：`XKX_VERSION=$(git describe --tags --always)`、`XKX_REVISION=$(git rev-parse HEAD)` 注入镜像，打不可变 tag + digest，推送镜像仓库；compose 用 digest 固定。
2. 发布清单记录 `{image_digest, content_hash, schema_version}`；回滚脚本按清单切回上一 digest。
3. 引入 `PRAGMA user_version`，启动时 `schema_version < required` 则执行只向前迁移；不兼容变更必须双写/回填。
4. 把 `.github/workflows/ci.yml` 提交入库（见 P1-3）。

**人日**：2.0

**验收方法**

- 从镜像 label/`/app/VERSION` 能读到 commit；同一 commit 两次构建 digest 一致（或记录 digest）。
- 用清单回滚到上一版并成功启动、读写、seq 连续；故意改 schema 版本，旧镜像必须明确拒绝而不是写坏库。
- CI 在 PR 上必跑并阻断失败合并。

---

## 3. P1（生产级必备）

### P1-1 最小可用可观测性与告警

**现状证据**

- `/api/health` 字段实测为：`ok / mode / readonly / profile_version / upstream_ok / run / tick / ambient*`（`reports/production-readiness/security-probe.json`），**无 DB、无磁盘、无 seq、无备份状态**。
- `/api/metrics` 只存在于**未提交工作树**（`src/wsim/api/metrics.py` 未跟踪；`git show HEAD:src/wsim/api/server.py | grep metrics` 无命中）。工作树实测 200 + Prometheus 文本（`reports/production-readiness/metrics-sample.txt`，2026-11-06 01:38）。默认仅回环匿名可抓，非回环需 `X-Metrics-Token`（`src/wsim/api/server.py:403-412`）。
- 现有指标仅 9 类：`xkx_http_requests_total`、`xkx_http_rejections_total`、`xkx_http_request_duration_seconds`、`xkx_world_tick`、`xkx_events_total`、`xkx_players_online`、`xkx_ambient_paused_total`、`xkx_llm_calls_total`、`xkx_db_bytes{file}`。
- 无告警通道：无 Prometheus/Alertmanager/Sentry/webhook 配置；无错误追踪。

**缺口**：进程活着但“世界不推进/备份不成功/磁盘快满”都不可见；指标端点未入库，按 HEAD 构建的镜像根本没有指标。

**最小达标方案**

1. 提交 metrics 端点并纳入镜像构建；反代后仅对监控网段开放。
2. 补 6 个指标：`xkx_disk_free_bytes`、`xkx_backup_last_success_timestamp`、`xkx_backup_last_duration_seconds`、`xkx_event_seq_gaps`、`xkx_auth_failures_total{reason}`、`xkx_process_rss_bytes` / `xkx_process_cpu_ratio`。
3. 部署 Prometheus + 以下告警规则（阈值建议）：

| 指标 | 告警阈值 | 级别 |
|---|---|---|
| `up{job="xkx"}` | 连续 60s = 0 | P1 |
| `xkx_http_requests_total{code=~"5xx"}` 比率 | 5min > 1% | P1 |
| `xkx_http_request_duration_seconds`（path=/api/state）P95 | 5min > 300ms | P2 |
| `xkx_players_online` | > 150 持续 10min（软容量） | P2 |
| `xkx_events_total` 增量 | 有在线玩家时 120s 无增长 | P1 |
| `xkx_world_tick` | 300s 无增长（自走开启时） | P2 |
| `xkx_disk_free_bytes` | < 10%（<5% 升级为 P0） | P1 |
| `xkx_backup_last_success_timestamp` | 距现在 > 7h | P0 |
| `xkx_event_seq_gaps` | > 0 | P0 |
| `xkx_db_bytes` 日增长 | > 20%/天 | P2 |
| `xkx_auth_failures_total` | 1min > 20 | P2 |

**人日**：2.0

**验收方法**：人为停进程、删事件增长、塞满磁盘（tmpfs）、制造一次 5xx、停止备份 → 每类告警在阈值内触发；恢复后自动解除。

---

### P1-2 无访问日志、无错误追踪、无日志轮转

**现状证据**

- API 访问日志被主动静默：`src/wsim/api/server.py:185-187` 的 `log_message` 直接 `return`，实测启动后发请求，日志仅一行启动 JSON，无请求行（`reports/production-readiness/access-log-probe.log`）。
- UI 侧每请求输出 stderr，但没有格式/级别/request-id（`ui/player/server.py:1689` 起）。
- 容器日志驱动 `json-file` 未设上限（同 P0-6）。
- 全仓库无 Sentry/OpenTelemetry/结构化 access log 配置。

**缺口**：线上排障只能靠猜；错误没有 request-id 关联；日志会打满磁盘。

**最小达标方案**

1. 结构化 access log（JSON lines）：时间、method、path、status、耗时、account_id、request_id、reject_reason；写 `logs/api-YYYYMMDD.log`，按天轮转保留 14 天。
2. 未捕获异常写 `logs/error.jsonl` 并附 request_id；接入 Sentry 或 webhook（最小可用）。
3. compose 设 `max-size/max-file`；日志目录纳入磁盘告警。

**人日**：1.5

**验收方法**：任一请求都能在 access log 中找到对应行；故意抛异常，error 日志含堆栈与 request_id；日志轮转后旧文件被压缩/删除。

---

### P1-3 CI 门禁未入库

**现状证据**

- `git ls-tree -r --name-only HEAD | grep .github` 为空；`.github/workflows/ci.yml` 为 `??` 未跟踪。因此**当前仓库没有生效的 CI**。
- 该 workflow 内容（未提交）包含：pytest、内容校验（关闭态 + `XKX_CONTENT_PACKS=scaleup_w3` 开启态）、2 日 headless 冒烟、`scripts/selfcheck.sh`，失败上传 `reports/`。缺少：`pip-audit`、Docker 构建/健康检查、容量冒烟、备份恢复演练。
- CI 使用 Python 3.11，而镜像/本机运行 3.12（`deploy/Dockerfile` 基于 `python:3.12-slim`），存在版本漂移。

**缺口**：没有合并门禁；回归只能靠人肉。

**最小达标方案**：提交 workflow；把 Python 对齐 3.12；增加 `pip-audit -r requirements.lock`、`docker build` + `healthcheck.py`、`tools/load_test.py --players 50 --duration 60`（P95 门禁）、`tools/backup_snapshot.py` + `restore_verify.py` 冒烟。必需 job 设为 required check。

**人日**：2.0

**验收方法**：故意提交一个失败用例/漏洞依赖/容量回归 PR，CI 必须红并阻止合并；主干绿后镜像自动构建。

---

### P1-4 数据库无版本与迁移机制

**现状证据**：无 `PRAGMA user_version`/迁移脚本；建表 `CREATE TABLE IF NOT EXISTS`（`auth.py:42,49`；`store.py:16,32`）。事件表与账号表结构变更时，旧库不会被识别，也没有升级路径。

**缺口**：任何加列/改索引都只能停机手改；旧镜像遇到新库可能写坏数据。

**最小达标方案**：引入 `schema_version`（`PRAGMA user_version` 或 `meta` 行），启动时校验并执行幂等迁移；迁移前自动备份；迁移失败拒绝启动。补“旧库 → 新库”与“新库 → 旧镜像拒绝启动”两个测试。

**人日**：1.5

**验收方法**：用 v1 库启动 v2 代码 → 自动迁移且 seq/hash 不变；用 v2 库启动 v1 镜像 → 明确拒绝并提示版本。

---

### P1-5 账号生命周期缺失（密码重置/找回、删除、导出）

**现状证据**

- 认证路由只有 register/login/logout/refresh（`src/wsim/api/server.py:39-44`）。
- 实测 `POST /api/auth/password/reset`、`/api/auth/account/delete`、`/api/account/export`、`/api/player/export` **全部 404**（`security-probe.json` 的 `lifecycle_*`，2026-11-06 01:18）。
- 令牌能力：logout 后旧令牌复用返回 401、refresh 后 401（`security-probe.json`），**单会话吊销可用**；但没有“踢出全部设备/管理员封禁/改密后失效所有令牌”的接口。
- 账号表无 `deleted_at`/导出逻辑；`sessions` 只增不删（实测 52 用户 → 55 会话，`backup-restore-live-drill.json`）。

**缺口**：用户忘密码即永久失联；无法合规删除/导出个人数据；被盗令牌在 24h 内无法全量吊销。

**最小达标方案**

1. 一次性重置令牌（管理员 CLI 生成，30 分钟有效，一次性消费）+ 改密接口；改密后吊销该账号全部会话。
2. 账号删除（软删 + 90 天清理，事件日志保留匿名化 ID）与 JSON 数据导出（角色、资产、任务、事件摘要）。
3. 管理员“封禁/踢出全部会话”接口 + 审计。

**人日**：3.0

**验收方法**：完整演练“忘记密码→重置→登录→改密→旧令牌全失效”；删除账号后无法登录且导出接口 404/403；导出 JSON 能被人工复核。

---

### P1-6 备份演练未自动化、RPO/RTO 未持续验证

**现状证据**

- 工具链已验证：热备份 `.backup` 0.044s，源与备份 `event_hash` 一致（`backup-restore-live-drill.json`）；裸 `cp`（不带 `-wal`）丢 46 条事件（2117→2071），`naive_cp_lost_events=true`。
- `tools/restore_verify.py` 可校验 sha256/integrity/seq/state_hash，但**没有定时执行**；没有每日自动恢复演练、没有“恢复耗时”记录、没有演练报告归档。
- 文档 `docs/BACKUP-RESTORE.md` 记录 RPO≈6h、RTO≈6.5s，但这是文档口径；本轮我未按该口径计时（**未验证**）。

**缺口**：备份“写了”不等于“能恢复”；轮转/恢复脚本改动后无人发现回归。

**最小达标方案**：每日 02:00 对最新备份跑 `restore_verify.py`，结果写 `backup_status.json` 并接告警；每月一次“删库→恢复→比对 seq/hash→启动→登录→写入”全链路演练，产出 JSON 报告。

**人日**：1.0

**验收方法**：连续 7 天自动校验全绿；人为篡改备份 1 字节，校验必须失败并告警；月度演练 RTO < 10 分钟。

---

### P1-7 容量护栏与首发边界未落到运行时

**现状证据**

- 实测（同一口径，60s，`--think-time 3 --ramp-up 8`）：
  - 50 席：`/api/state` P95 **11.8ms**，CPU 均值 20.2%；
  - 100 席：P95 **15.8ms**，CPU 43.7%；
  - 150 席：P95 **85.1ms**，CPU 77.7%（峰值 147%）；
  - 199 席（+`u_demo`=200 有效席）：P95 **1361ms**，CPU 均值 **100.2%**、峰值 209%，`pass=false`。
  - 第 201 席返回 429 `player_cap_reached`（`capacity-200-check.json`，fill 199 + preexisting 1）。
- 运行时上限仍是 200（`deploy/service.yaml` 的 `max_region_players: 200`；`docs/OPS-RUNBOOK.md:11`），但 150 席以上 P95 已不达标。
- 文档已明确“首发按 150 席对外”（`docs/OPS-RUNBOOK.md:196-204`），但代码/配置没有 150 的软上限或“接近上限”告警。

**缺口**：容量是文档约束而非系统约束；玩家可把服务压到 1.3s P95。

**最小达标方案**：新增软上限 `MAX_SEATS=150`（或 `150` 时只允许重连/拒绝新 enter），200 仅保留硬 429；`/api/metrics` 暴露 `xkx_players_online` 并告警 >150；对外文案与 `service.yaml` 对齐。

**人日**：1.0

**验收方法**：第 151 个新席进入返回明确的容量拒绝；监控在 145 席触发预警；150 席 10 分钟 P95 < 300ms。

---

### P1-8 依赖/镜像扫描与 CORS、安全头未成体系

**现状证据**

- `requirements.lock` 仅 6 个包（pytest 及其传递依赖 + PyYAML）。`pip-audit -r requirements.lock` 实测 **5 个包、0 漏洞**（`reports/production-readiness/pip-audit.json`，2026-11-06 01:25；`packaging` 未出现在报告中，**该项未验证**）。
- 无镜像扫描（trivy/grype）配置；无依赖升级节奏。
- CORS 预检 `OPTIONS` → **501**，无 `Access-Control-Allow-Origin`（`security-probe.json`）。当前 UI 经 8030 同源代理，所以“默认拒绝”可接受；但一旦拆分域名就会全线失败，且策略未文档化。
- 无 CSP/HSTS/X-Frame-Options 等安全头。

**缺口**：供应链风险无门禁；跨域行为靠巧合。

**最小达标方案**：CI 加 `pip-audit` 与镜像扫描；明确 CORS 策略（同源部署，反代层加安全头）；把“无 CORS”写入部署约束。

**人日**：1.0

**验收方法**：引入一个已知漏洞版本依赖，CI 红；响应头包含 HSTS（HTTPS 下）、`X-Content-Type-Options: nosniff`；跨域预检按文档行为返回。

---

## 4. P2（规模化）

### P2-1 单区超过 150 席：分区/多区改造

**现状证据**：瓶颈是单进程内的全局写锁与全量状态克隆——

- `src/wsim/api/session.py:297` `self.lock = threading.RLock()`；读状态、视图构建、写动作都在同一把锁内（`session.py:399/435/552/587/609/632/749/788/1284/1317`）。
- `src/wsim/world/writer.py:34` 与 `:101` 每个接受事件都 `trial = self.state.clone()`，即**整世界深拷贝**；叠加 GIL 与 `ThreadingHTTPServer`，CPU 在 199 席时饱和（均值 100.2%）。
- 文档已有扩区方案：每区 125–167 席、3–4 区、每区 4 核/2GB（`docs/OPS-RUNBOOK.md:196-204`），但**没有代码**：无区路由、无跨区事件、无账号归属表。

**缺口**：席位增长只能加机器加区，且缺路由/账号归属/跨区聊天。

**最小达标方案**：先做“每区一进程 + 前置按账号一致性哈希路由”；账号表加 `region_id`；世界事件按区独立，跨区只同步公共频道（异步、最终一致）。**不要**在单进程内加线程池。

**人日**：15.0（路由 5 + 账号归属 4 + 跨区消息 4 + 部署/演练 2）

**验收方法**：2 区各 150 席，连续 30 分钟 P95 < 300ms；跨区消息延迟 < 2s；单区宕机不影响另一区登录与存档。

---

### P2-2 写路径重构：消除 O(世界规模) 的每事件克隆

**现状证据**：`writer.py:101` 每事件深拷贝全状态；`_seen` 全量保存（`writer.py:28,126,151`）。150→199 席 CPU 与 P95 的非线性跳变（85ms→1361ms）与该写放大直接相关（实测数据同上）。

**最小达标方案**：状态改为不可变分片/写时复制（只克隆受影响实体）；`_seen` 改 LRU 或按 `intent_id` 查库；锁按 `region/entity` 分片。预期同机可把 150 席 CPU 从 78% 降到 40–50%（**估算，未验证**）。

**人日**：8.0

**验收方法**：同一压测口径 150 席 P95 ≤ 50ms、199 席 P95 < 300ms；事件 hash 与旧实现逐条一致（回归用已有事件日志重放）。

---

### P2-3 管理后台（GM/封禁/公告/看板）

**现状证据**：代码中没有 admin 路由（`grep -rn "admin" src/wsim/api/server.py` 无命中）；运营只能靠 CLI/SQL。MCP 侧虽有 propose/approve/apply 审批（`src/wsim/mcp/gateway.py`），但无 Web 管理界面。

**最小达标方案**：独立 admin 服务（只读事件库 + 受限写操作），RBAC + 审计；首版只做封禁/踢人/公告/在线看板/备份状态。

**人日**：10.0

**验收方法**：管理员可封禁并即时踢下线；所有管理操作进审计日志；普通玩家令牌访问 admin 一律 403。

---

### P2-4 高可用与数据层升级

**现状证据**：单进程、单 SQLite 文件、单机命名卷；无副本、无 PITR、无蓝绿。回滚演练只覆盖“同机换镜像”（`rollback-drill.json`）。

**最小达标方案**：先做“热备 + 定时 WAL 归档 + 秒级切换”的半高可用；再评估 Litestream/rqlite 或迁移 PostgreSQL。蓝绿部署与健康门禁配套。

**人日**：6.0（不含迁库）

**验收方法**：主实例 kill -9 后 60s 内备实例接管，seq 连续、最多丢 1 个已确认事件（需产品确认口径）。

---

### P2-5 在线 LLM 成本闸门未接线

**现状证据**

- `src/wsim/agent/budget.py:69-90,142-153` 有 run 级硬闸：`hard_cost=3.0` 美元，超限置 `aborted=True`；`src/wsim/sim/headless.py:628-629,657-658` 在下一 tick 中止。
- 实测 `pytest -q tests/test_a3_phase1.py -k budget` → **2 passed**（2026-11-06 01:51）。
- 但**在线服务没有接**：`src/wsim/api/session.py:2039-2040` 硬编码 `llm_enabled=False, llm_max_calls=0`，因此在线 LLM 调用恒为 0、花费为 0（`metrics-sample.txt` 的 `xkx_llm_calls_total 0`）。
- 结论：硬闸“能停”（离线路径已验证），但**在线路径根本没有启用**，所以不存在“线上烧钱”的现状风险；风险在“未来一旦开 LLM，预算闸门不在 API 路径上”。

**缺口**：`AmbientCostModel` 只有**调用次数**上限（`scheduler.py` 的 `max_calls`），不是美元硬闸；API 侧没有接入 `BudgetGuard`。

**最小达标方案**：若上线 LLM，必须在 API 的 ambient 路径注入 `BudgetGuard`（按美元/月），并加 `xkx_llm_cost_usd` 指标与日预算告警；默认仍关闭。

**人日**：2.0

**验收方法**：把预算设为 $0.01，触发一次调用后在线服务在下一 tick 降级为规则并停止调用；指标与日志可查。

---

## 5. 若只有 1 周，应该只做哪 4 项

按“不可逆损失 > 可被利用 > 可观测”排序：

1. **P0-1 备份默认生效 + 失败告警 + 异地副本**（2 人日）
   —— 现在默认部署等于没有备份；这是唯一“出事就无法挽回”的项。
2. **P0-2 丢库 fail-closed + 真健康检查**（1 人日）
   —— 卷挂错/文件误删会被静默当成新世界，和没备份一样致命。
3. **P0-4 登录/注册限流防爆破 + P0-5 请求体上限**（1.5 + 0.5 = 2 人日）
   —— 攻击成本为零，且 PBKDF2 放大 CPU，能直接打掉在线玩家。
4. **P0-3 TLS/反代**（1.5 人日）
   —— 明文口令与 24h 令牌是最容易被现场问责的问题；用 Caddy 落地，工作量小。

合计 **6.5 人日**：单人需要 6.5 天（紧），**两人并行 3–4 天**可完成并留出验收时间。这 4 项做完，才具备“可对外试运营”的最低门槛；P0-6/P0-7 应紧随其后（第二周）。

---

## 6. 附录 A：证据索引（全部为本轮亲手执行）

| 时间（CST） | 命令/操作（摘要） | 产物 | 关键结果 |
|---|---|---|---|
| 01:13 | `python tools/backup_snapshot.py --root . --run prod-audit-50 --backup-dir /tmp/xkx-audit/bk --no-prune` | `evidence/backup-manifest-sample.json` | 2117 事件、seq 连续、`event_hash=1a03c530…`、`verification_ok=true`，含重放 503.9ms |
| 01:13 | `.backup` → 删库 → 恢复 → 比对（`/tmp/xkx-audit/backup_drill.py`） | `backup-restore-drill.json` | 509 事件，sha256 前后一致，`all_pass=true` |
| 01:14–01:17 | 每档启动 `python -m wsim.api --port 8151` + `tools/load_test.py --players N --duration 60 --think-time 3 --ramp-up 8 --server-pid …` | `capacity-{50,100,150,199}.json/.log` | P95 11.8 / 15.8 / 85.1 / 1361ms；CPU 20.2 / 43.7 / 77.7 / 100.2% |
| 01:18 | `tools/load_test.py --capacity-check --players 199 --preexisting-seats 1 --probe-count 1` | `capacity-200-check.json` | 有效 200 席；第 201 席 429 `player_cap_reached` |
| 01:18 | `/tmp/xkx-audit/security_probe.py`（health/metrics/CORS/50 次错密/30 次注册/令牌吊销/生命周期/TLS） | `security-probe.json` | 50×401 无 429；30×200；4 个生命周期端点 404；TLS 失败 |
| 01:19 | 5MB/20MB 登录体 + `OPTIONS` | `body-limit-probe.json` | 均被解析（401），无 413 |
| 01:21 | 热备份 + 裸 `cp` 对照 + 恢复 + 登录/写入（`/tmp/xkx-audit/live_backup_drill.py`） | `backup-restore-live-drill.json` | 热备 0.044s、hash 一致；裸 cp 丢 46 事件；恢复后 seq 连续、可登录可写 |
| 01:25 | `pip-audit -r requirements.lock`（临时 venv） | `pip-audit.json` | 5 包 0 漏洞（`packaging` 未验证） |
| 01:34 | 新镜像/旧镜像同卷回滚（`/tmp/xkx-audit/rollback_drill.py`） | `rollback-drill.json` | 健康 4.34s/5.20s；旧镜像登录/读/写正常；12→14 事件不丢 |
| 01:38 | 启动后发请求，观察日志 | `access-log-probe.log` | 仅有启动 JSON，无访问日志 |
| 01:38 | 删除 `events.sqlite` 后启动 | `missing-db-startup.log` | 启动成功，重建 3 条创世事件 |
| 01:38 | 写入损坏 DB 后启动 | `corrupt-db-startup.log` | 快速失败退出（`file is not a database`） |
| 01:38 | 抓取工作树 `/api/metrics` | `metrics-sample.txt` | 200，Prometheus 文本，9 类指标 |
| 01:41 | PRAGMA + 负载中 WAL 大小 | `wal-checkpoint-probe.json` | WAL、`synchronous=2`、`wal_autocheckpoint=1000`；负载中 WAL 4.13MB |
| 01:51 | `pytest -q tests/test_a3_phase1.py -k budget` | 终端输出 | 2 passed（硬闸离线有效） |
| 01:53 | 由容量报告 + 运行目录字节数推算 | `disk-growth-projection.json` | 见下表 |

**磁盘增长（含 `mcp_audit.jsonl`；events.sqlite 约占 52%，audit 约占 48%）**

| 场景 | 事件/天 | GB/天 | 30 天 | 90 天 |
|---|---:|---:|---:|---:|
| 50 席、24/7 满载 | 19.70M | 29.18 | 875 GB | 2,626 GB |
| 150 席、24/7 满载 | 57.90M | 90.57 | 2,717 GB | 8,152 GB |
| 150 席、每天 8h 满载 | 2.76M | 4.31 | 129 GB | 388 GB |
| 150 席、每天 8h、按 1/10 真实活跃 | 0.28M | 0.43 | 12.9 GB | 38.8 GB |

> 口径：`events_per_s = event_count / actual_duration_s`；`bytes_per_event = (events.sqlite + mcp_audit.jsonl) / event_count`。150 席实测 0.638 事件/席/秒、1,564 B/事件。真实业务量需按实际活跃度修正；上表只是边界计算。

**每席成本（估算，非实测价）**

- 空闲常驻：容器 `51.84 MiB / 0.09% CPU`（`docker stats --no-stream`，2026-11-06 01:54 实测）。
- 150 席满载：RSS 峰值 70.75MB、CPU 0.78 核（50.7 CPU-s / 63.87s）。
- 按 2 vCPU / 4GB 云主机 ¥120–200/月估算：150 席 ≈ **¥0.8–1.3/席/月**；按 1 vCPU/2GB ¥60–100/月估算：50 席 ≈ **¥1.2–2.0/席/月**。**主机单价为假设值，未询价**。

---

## 7. 附录 B：本轮发现的真实 bug / 数据风险（未改代码）

1. **备份默认不存在 + 失败被吞**：`deploy/docker-compose.yml:48-51` 的 backup 在可选 profile；`:57` 的 `|| true` 使“工具缺失/备份失败”永远静默。实测旧 `w5` 镜像无 `/app/tools` → exit 2，容器仍 `Up`。**当前默认部署 RPO = 无界。**
2. **丢库静默开新世界**：删除 `events.sqlite` → 服务启动成功、重建 3 条创世事件、`/api/health` 仍 `ok:true`（`missing-db-startup.log`）。卷挂载错误 = 静默清档。
3. **裸 `cp` 会丢已提交事件**：不带 `-wal` 复制 `events.sqlite`，2117→2071（丢 46 条），且 seq 仍“连续”（因为丢的是尾部）。运维按常识拷贝就会丢最近写入。
4. **审计日志无轮转 + 内存无界**：`mcp/audit.py:44,49`、`writer.py:28`、`session.py:298-299`；实测 740 B/事件，150 席满载 **43 GB/天** 仅 audit 文件；RSS 60s 增 31.7MB，是否收敛未验证。
5. **请求体无上限**：`server.py:212-218`；20MB 被完整读入并解析（无 413），可单连接放大内存。
6. **认证无速率限制**：50 次错密 50×401、30 次注册 30×200；PBKDF2 200k 次 ≈30ms/次 → CPU 放大。
7. **健康检查名不副实**：`server.py:286-302` 硬编码 `ok:true`；`deploy/healthcheck.py` 不查 DB/seq/磁盘。容器 `healthy` 不代表世界可用。
8. **指标端点在 HEAD 不存在**：`src/wsim/api/metrics.py` 与 `server.py` 的 metrics 改动均未提交（`git show HEAD:… | grep metrics` 无命中）。按 HEAD 构建的生产镜像没有任何指标；`/api/metrics` 只在工作树可复现（`metrics-sample.txt`）。

---

## 8. 附录 C：未验证项

- **OS 断电/内核崩溃**下 `synchronous=NORMAL` 的实际丢事件数：只做了进程级 kill -9（用户口径 8/8），未做断电模拟。
- **24/7 长跑**内存是否收敛：只测了 60–70s 窗口，RSS 单调上升但未观察拐点。
- **100 万级事件 / 多 GB 库**下的 `/api/state` 延迟与备份耗时：只在 2k–7k 事件库上测过。
- **GitHub Actions 实际运行**：workflow 未入库、无法触发；本地也未完整跑 `scripts/selfcheck.sh`。
- **`packaging` 依赖漏洞**：`pip-audit.json` 未列出该项。
- **云主机单价**：成本估算使用假设价，未实际询价。
- **分区方案**：只做了工作量评估，未做 PoC。
