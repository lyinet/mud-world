# XKX 世界服务运维手册（W5）

本文面向值守同学：如何启动、升级、备份、恢复、清理僵尸会话、看图告警与排查错误码。
所有命令默认在仓库根目录 `world-sim/` 执行。

架构前提（不要擅自改）：

- **单进程、单写者、sqlite**。没有 Redis / Celery / 消息队列；所有写请求经 `WorldSession.lock` 串行化。
- `runs/<RUN_ID>/events.sqlite` 是唯一存档（事件日志 + `meta.content_hash`）。
- `runs/<RUN_ID>/service.sqlite` 是账号与令牌库（PBKDF2）。
- 一个区域上限 200 席（`player_cap_reached` → HTTP 429）；创世预置的 `u_demo` 也占 1 席，所以默认只有 199 个新账号能进入。

---

## 1. 启动

### 1.1 本机（开发/单机）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock

# 引擎/API（默认 127.0.0.1:8020；鉴权 auto 会读取 deploy/service.yaml）
PYTHONPATH=src .venv/bin/python -m wsim.api --root . --run service --port 8020 --auth on

# 玩家前端（另开终端；默认 8030，代理到 8020）
.venv/bin/python ui/player/server.py --upstream http://127.0.0.1:8020 --port 8030
```

健康检查：

```bash
curl -s http://127.0.0.1:8020/api/service/info   # {"ok": true, ...}
curl -s http://127.0.0.1:8030/api/health         # {"ok": true, ...}
```

### 1.2 容器（一条命令）

```bash
cd deploy
# 可选：把运行时配置放进 world-sim/.env（只读注入，不进入镜像层）
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100 world
```

- API `8020`、前端 `8030` 映射到宿主同端口（可用 `API_PORT` / `UI_PORT` 覆盖）。
- 存档在命名卷 `xkx-w5-data` 的 `/app/runs/<RUN_ID>/`，同时包含 `events.sqlite` 与 `service.sqlite`。
- 容器以非 root（uid 10001）运行；镜像构建上下文通过 `deploy/Dockerfile.dockerignore` 排除 `.env`。
- 校验镜像确实没有密钥：

```bash
docker run --rm --entrypoint sh xkx/world-sim:w5 -c 'env | grep -Ei "api[_-]?key|secret|token" || echo no-secrets'
```

> W5 已在 colima 29.5.2 / aarch64 上实测：用本仓 `deploy/Dockerfile` 构建镜像后，
> `docker-compose up -d` 一条命令起 API(8020)+前端(8030)，容器 `Health=healthy`、
> uid 10001、镜像内无 `.env`、环境变量无密钥，`/app/runs`（命名卷 `xkx-w5-data`）内同时生成
> `events.sqlite` 与 `service.sqlite`。
> 注意：本机只有独立版 `docker-compose`（legacy builder，不读 `deploy/Dockerfile.dockerignore`，
> 构建上下文会偏大）。生产请用 Docker Compose v2 / BuildKit，`docker compose up -d --build`。
> 容器**默认世界自走**（与 `./start.sh` 一致）：compose/entrypoint 注入 `XKX_AMBIENT=1`、
> `XKX_AMBIENT_CPU=70`、`XKX_AMBIENT_SPEED=1`，不必额外设环境变量。需要静止容器时：
> `XKX_AMBIENT=0 docker compose up -d --force-recreate`。

### 1.3 关键启动参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--root` | `.` | 含 `content/`、`config/`、`runs/` 的根目录 |
| `--run` | `player-demo` | run 目录名，存档在 `<root>/runs/<run>/` |
| `--tick-step` | `6` | 每个玩家动作推进的世界 tick（6=1 小时） |
| `--auth` | `auto` | `auto/on/off`；有 `deploy/service.yaml` 时 auto=on |
| `--auth-iterations` | 生产默认 | PBKDF2 迭代数；压测可降到 `1000`，生产不要降 |
| `--token-ttl` | 默认 24h | 令牌有效期（秒） |
| `--reconnect-grace` | 默认 60s | 断线重连宽限 |
| `--session-db` | `<run>/service.sqlite` | 账号库位置 |

退出码：

- `0` 正常退出；`2` 端口/参数错误；`3` **内容 hash 不符**（拒绝启动，不写任何事件）。

---

## 2. 升级

1. **先备份**（见第 3 节）。
2. 拉代码/镜像并构建：`cd deploy && docker compose build`。
3. 检查 `content/` 是否变化：若内容变更，`content_hash` 会变，旧存档会拒绝启动（退出码 3）。这是保护机制，不是故障。
4. 滚动重启（单机只有一个写者，必须停旧起新，不能双写）：

```bash
docker compose up -d --build   # 会重建容器并挂回同一卷
docker compose logs -f world   # 看到 [player-ui] mode=live 与 API 监听
```

5. 升级后自检：`pytest -q`、`bash scripts/selfcheck.sh`、`curl /api/service/info`。

---

## 3. 备份

唯一事实源是 `runs/<RUN_ID>/events.sqlite`；`service.sqlite` 必须一起备份（账号/令牌）。
**统一使用 `tools/backup_snapshot.py`**（sqlite 在线备份 API，不用 `cp`）：

```bash
# 宿主机（仓库根）
.venv/bin/python tools/backup_snapshot.py --root . --run service

# 容器内（容器已带 tools/；等价于 backup profile 的循环动作）
docker exec xkx-world python /app/tools/backup_snapshot.py --root /app --run service
```

产物在 `backups/<UTC 时间戳>/`（`events.sqlite`、`service.sqlite`、`manifest.json`）。
完整字段、轮转策略（48 小时滚动 + 最近 7 日每日 1 份）、删除安全校验、定时方案与
实测 RPO/RTO 见 [docs/BACKUP-RESTORE.md](BACKUP-RESTORE.md)。

要点：

- 不要只 `cp events.sqlite` 而不拷 `-wal`/`-shm`；要么停服后拷贝，要么用 `.backup`。
- 备份工具在服务运行中执行，`manifest.json` 里有 `sha256`、`seq`、`content_hash`、
  `state_hash`，可直接用于恢复校验。
- 备份目录权限 `700`、文件 `600`（含账号哈希与令牌，勿对全员开放）。

---

## 4. 恢复

1. **停止服务**（单写者，禁止边恢复边写）：`docker compose stop world` 或 kill API 进程。
2. 选备份：看 `manifest.json` 的 `created_at` 与 `seq.max`，选故障前最近一份。
3. 校验并装回（非零退出即中止）：
   ```bash
   .venv/bin/python tools/restore_verify.py --root . \
     --backup-dir backups/20261105T172730Z --apply-dir runs/service
   ```
4. 启动并验证：
   ```bash
   curl -s http://127.0.0.1:8020/api/service/info
   curl -s -o /dev/null -w '%{http_code}\n' \
     'http://127.0.0.1:8020/api/state?account_id=<account>'     # 期望 200
   .venv/bin/python tools/load_test.py --verify-only --run-dir runs/service
   ```

`restore_verify.py` 逐项校验：文件 sha256/大小、`integrity_check=ok`、seq 连续且
min/max/count 与 manifest 一致、重放 `state_hash` 一致、`content_hash` 与当前内容一致、
`event_hash` 一致。任何一项失败退出码为 `2`，**不要继续起服**。

5. 内容 hash 不符（起服退出码 3）：说明备份对应旧内容或内容被改。要么恢复对应版本的
   `content/`，要么用新内容开新 run；**不要手改 hash 绕过**。
6. `service.sqlite` 回滚后，备份之后签发的令牌/新账号会失效，通知玩家重新登录。

---

## 5. 僵尸会话清理

现象：玩家已断线，但 `online=true` / 仍占席位；或大量 `player_offline` 拒绝（历史现象，见 7.3 已修复）。

W1 契约：断线 60 秒内可重连；超时后会话作废但**存档保留**，玩家可重新 `enter`。

处理顺序：

1. 让玩家重新 `POST /api/player/enter`（会恢复同一角色，不重复发放初始资产）。
2. 若账号/令牌异常，调用 `POST /api/auth/logout` 或等待 TTL 过期；查看令牌：

```bash
sqlite3 runs/service/service.sqlite \
  "SELECT account_id, datetime(expires_at,'unixepoch'), datetime(last_seen_at,'unixepoch'), status
   FROM sessions ORDER BY last_seen_at DESC LIMIT 20;"
```

3. 清理过期令牌（**停服后**执行，运行中直接写 service.sqlite 会与 API 竞争）：

```bash
docker compose stop world
sqlite3 runs/service/service.sqlite \
  "DELETE FROM sessions WHERE expires_at < strftime('%s','now') OR status='revoked';"
docker compose start world
```

4. 若席位被已不存在的玩家占用（数据修复场景），从事件日志确认该 `player:*` 的 `player_leave`/`presence` 状态，再决定是否重放/开新 run；**不要直接改 events.sqlite**。

---

## 6. 容量告警阈值

单区硬上限 200 席，第 201 席返回 `429 player_cap_reached` 属正常限流，不计入错误率。

W5 修复后（commit `500369e3`）实测容量曲线（50 并发口径 `think=3s`，单进程单机）：

| 席位 | 请求/s | `/api/state` P95 | CPU（单核%） | 结论 |
|---|---|---|---|---|
| 50（10 分钟） | 49 | 29ms | 37% | 验收通过 |
| 100（60s） | 68 | 33ms | 43% | 余量充足 |
| 150（60s） | 100 | 156ms | 79% | 接近 300ms 门槛 |
| 199（120s） | 85（被延迟反压） | 1,762ms | 90% | 单核已饱和，超 300ms |

**首发交付边界（按 W5 实测定）**：单区按 **150 席对外**；200 席只承诺“可连入、第 201 席返回 429”，
**不承诺 P95**（199 席实测 1,762ms）。要对外承诺 300ms，必须按下面的分区方案扩容。

- **单进程/单区在 300ms P95 下的实际上限约 150–165 席**（瓶颈是 GIL + 全局 `WorldSession.lock`，见 7.3）。
- 199 席实测 `/api/state` P95 = **1,762ms**、CPU 90%（单核饱和），所以 200 席不是“再挤一挤”的量。
- 外推到 500 席：按 150 席 ≈ 100 req/s、每请求约 7–9ms CPU 估算，500 席约需 311 req/s ≈ **2.5–3 核**的纯计算量；
  按每区 ≤150 席拆 **3–4 个区（每区 125–167 席）**，保守按 **每区 4 核 / 2GB 预留（整机 12–16 核 / 8GB）**，
  实测 4 核 / 2GB 整机即可跑满 500 席的计算量，每区 4 核属于冗余预留。内存按 0.3–0.5MB/席 ≈ 250–350MB。
- 单机“一个 500 席大区”不可行：单区硬上限 200，且单进程在 ~160 席后延迟即越过 300ms；扩席位靠**加区（加进程）**，不是加单进程线程。

建议告警（连续 5 分钟）：

| 指标 | Warning | Critical | 说明 |
|---|---|---|---|
| `GET /api/player/state` P95 | >300ms | >600ms | 验收门槛 300ms |
| 任意写端点 P95 | >500ms | >1000ms | 写请求串行，先看锁等待 |
| HTTP 5xx | >0 | >0 持续 1 分钟 | 任何 500 都要当事故 |
| HTTP 401 | >1%/分钟 | >5%/分钟 | 令牌风暴/客户端时钟漂移 |
| 错误率（非业务拒绝） | >0.5% | >2% | 与验收口径一致 |
| `player_cap_reached` 429 | 持续出现 | 持续出现 + 玩家投诉 | 说明需要扩区，不是扩容单机 |
| 进程 RSS | >1.5GB | >2.5GB | 实测 200 席峰值约 100MB |
| CPU（单核百分比） | >70% | >90% | 单进程受 GIL 限制，单核到顶即延迟拐点 |
| `events.sqlite` 事件数 | >200,000 | >500,000 | 写路径已改为按 seq 取尾，事件数不再线性抬延迟；此阈值只用于备份/归档规划 |
| `events.sqlite` 文件 | >200MB | >500MB | 备份/恢复窗口同步变长 |
| WAL 文件 | >128MB | >512MB | 检查长事务/备份拖尾 |

排查第一步永远是：

```bash
curl -s http://127.0.0.1:8020/api/service/info
.venv/bin/python tools/load_test.py --verify-only --run-dir runs/service
tail -100 runs/service/*.log 2>/dev/null
```

---

## 7. 故障排查 / 错误码对照

### 7.1 HTTP 层

| HTTP | code | 含义 | 处置 |
|---|---|---|---|
| 400 | `bad_json` | 请求体不是合法 JSON | 检查客户端 |
| 400 | `bad_args` / `bad_display_name` / `bad_archetype` | 参数/名字/职业不合法 | 角色名 2–12 字符且唯一 |
| 401 | `auth_missing_token` | 没带 Bearer | 客户端补头 |
| 401 | `auth_invalid_token` / `auth_expired_token` | 令牌无效/过期 | 重新登录或 `POST /api/auth/refresh` |
| 403 | `auth_account_mismatch` | 令牌账号与请求账号不一致 | 不要跨账号用令牌 |
| 409 | `auth_username_taken` | 注册重名 | 换用户名；压测复用账号时属预期 |
| 400 | `auth_bad_password` / `auth_invalid_credentials` | 密码错误 | 检查密码 |
| 429 | `player_cap_reached` | 200 席已满 | **容量拒绝，不是错误**；扩区或等待空位 |
| 429 | `endowment_cap_reached` | 单区初始资产总额触顶 | 同上；注意 `u_demo` 也占额度 |
| 404 | `quest_not_found` | 委托不存在 | 刷新 `/api/player/state` |
| 500 | — | 未预期异常 | 立刻保留日志与 events.sqlite，按第 7.3 节定位 |

### 7.2 业务拒绝（HTTP 200 + `ok=false`）

| code | 含义 | 处置 |
|---|---|---|
| `player_not_entered` | 账号还没进世界 | 先 `POST /api/player/enter` |
| `player_offline` | 玩家被判定离线 | 重新 `enter`；见第 5 节 |
| `player_consent_required` | 需要玩家同意条款 | 客户端确认后再进入 |
| `quest_not_open` | 委托未开放/已被接走 | 刷新委托板 |
| `quest_expired` | 委托过了截止 tick | 放弃，重新接单 |
| `quest_item_missing` | 交付缺少物品 | 先完成前置动作 |
| `quest_not_accepted_by_actor` | 不是接单人交付 | 用接单账号操作 |
| `bad_location` | 目标地点不可达/不合法 | 检查出口与移动路径 |
| `bad_message` | 对话内容为空/过长 | 2–200 字符 |
| `endowment_already_granted` | 初始资产只发一次 | 正常，忽略 |

### 7.3 高负载现象与修复状态（W5 实测）

已修复（commit `500369e3`，改前 → 改后，同一 50 并发 ×10 分钟口径）：

| 现象 | 改前 | 改后 |
|---|---|---|
| `/api/state` P95 | 3,700ms | 29ms |
| 全端点 P95 | 3,750ms | 40ms |
| 单线程写吞吐（17k 事件） | 4.28 次/s | 95.8 次/s |
| 每次写的 `read_all` | 1 次（≈160ms） | 0 次（`EventTail.event_at(seq)` 单行查询） |
| `player_offline` 业务拒绝 | 4,727 次 | 0 次（wall-clock 30s 活跃 / 60s 离线） |
| 每个世界日系统事件 | 5 条 | 1 条（`system_tick` 合并日界结算） |

修复后的**剩余瓶颈**（尚未改，扩容时按此判断）：

1. **单进程 GIL + 全局写锁**：`src/wsim/api/session.py:680` 的 `WorldSession.lock` 把整个
   `perform`（propose→approve→apply→advance）串行化；`/api/player/state` 也在同一把锁内构图
   （`session.py:278`）。实测 1 核约 100 req/s 到顶，150 席 P95 156ms、199 席 P95 1.76s。
2. **状态视图在锁内构建**：`_status_view` / `_quest_board_view` / `_online_players_view`（
   `session.py:444/602/582`）随在线人数与委托量增长，仍是 O(在线玩家) 成本。
3. 写路径已无 O(n) 读；事件条数增长只影响备份/恢复窗口，不直接影响在线延迟。

扩容方式：**加区（多进程、各自 sqlite），不要试图靠多线程/加核提升单区吞吐**。单区仍受 200 席硬上限保护。

### 7.4 常用诊断命令

```bash
# 事件总量 / seq 连续性 / 钱包守恒 / 半写
.venv/bin/python tools/load_test.py --verify-only --run-dir runs/service

# 200 席与第 201 席 429
.venv/bin/python tools/load_test.py --capacity-check --players 200 --probe-count 3 \
  --preexisting-seats 1 --base-url http://127.0.0.1:8020 --run-dir runs/service

# 50 并发 ×10 分钟（验收口径）
.venv/bin/python tools/load_test.py --players 50 --duration 600 --think-time 2 \
  --base-url http://127.0.0.1:8020 --run-dir runs/service --server-pid <API_PID> \
  --report reports/w5/load-50x10m.json

# sqlite 自检
sqlite3 runs/service/events.sqlite "PRAGMA integrity_check; SELECT COUNT(*),MIN(seq),MAX(seq) FROM events;"
```

WAL 异常或磁盘满时：先停服，保留 `events.sqlite`/`-wal`/`-shm` 原样，再执行
`sqlite3 events.sqlite "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA integrity_check;"`，不要删除 WAL。

---

## 8. 扩容路线：150 → 500 席（方案，**未实现**）

> 本章只是可执行方案与风险清单，代码/脚本均未落地；不要按此直接改生产。
> 原则不变：不引入 Redis / Celery / 消息队列，仍是“每区单进程、单写者、sqlite”。

### 8.1 分区怎么切

- **区（region）= 一个独立 run + 独立进程**：`runs/region-<id>/`（各自 `events.sqlite`、`service.sqlite`）、
  独立 API/UI 端口、独立命名卷。区之间不共享事件日志，避免跨区写锁。
- 首发 1 区 150 席；扩到 500 席时起 **3–4 个区，每区 125–167 席**（硬上限 200 留缓冲）。
- 分区键优先按**地理/主题**切（如 江南/塞北），保证同区玩家社交密度；不足时再按注册序号均衡。
- 所有区的 `content/` 必须一致（`content_hash` 相同），否则跨区委托模板与物品不一致，启动会退出码 3。

### 8.2 账号在哪个区

- 新增**全局账号索引** `runs/_global/accounts.sqlite`（`username → region_id`、状态、创建时间），
  由注册入口单写者维护；用户名唯一性在这里判定（各区的 `service.sqlite` 不再是全局唯一来源）。
- 注册时按负载/玩家选择分配**归属区**并写索引；账号终生归属该区，**只允许在归属区 `enter`**，
  防止同一角色在两个区被双写。
- 令牌仍由归属区的 `service.sqlite` 签发与校验；跨区不做令牌漫游（需要跨区登录时重新在目标区认证，
  且目标区必须已有该角色，当前不支持）。

### 8.3 跨区社交 / 跨区委托怎么处理

- **跨区聊天/好友/世界频道**：共享 `runs/_global/cross_region.sqlite` 的 `outbox` 表
  （`message_id PK, from_region, to_region, payload_json, status, created_at`）。
  各区后台以 1–5s 轮询自己的收件箱，按 `message_id` 幂等去重；允许秒级延迟，不承诺实时强一致。
- **跨区镖单（A 区发布、B 区交付）**：两段式 + 托管（escrow）：
  1. A 区接单时冻结物品与报酬，写 `cross_region_quests(status=escrowed)`；
  2. B 区交付确认写 `delivered` 并生成回执；
  3. A 区轮询到回执后释放报酬、标记 `completed`；
  4. 任一段超时/失败进入 `failed`，由对账任务补偿或人工处理。
  跨区没有原子事务，必须靠**幂等键 + 唯一约束 + 对账**兜底，禁止“先发货后记账”。
- 世界 tick 各区分开推进，跨区业务一律用真实时间戳（UTC）做超时与排序，不能用世界 tick 比较。

### 8.4 共享哪些数据

| 数据 | 位置 | 读写方式 |
|---|---|---|
| 账号归属索引 | `runs/_global/accounts.sqlite` | 注册入口单写；各区只读 |
| 跨区聊天/好友投递 | `runs/_global/cross_region.sqlite` | 各区投递 + 轮询消费（幂等） |
| 跨区委托托管/回执 | 同上 `cross_region_quests` | 两段式状态机 + 对账 |
| 全局榜单/在线快照 | 同上 `global_stats` | 各区定期写，展示端聚合读 |
| 玩家存档/钱包/事件 | **各区私有** | 不共享、不跨区写 |
| 账号密码/令牌 | 各区私有 `service.sqlite` | 仅归属区读写 |

### 8.5 落地步骤（按顺序）

1. 固化“单区 150 席对外、200 席仅可连入”的交付口径与告警（第 6 节已定）。
2. 引入 `region_id` 与全局账号索引；注册路由按归属区分配；改造前端按账号归属路由到区端口。
3. 起 3–4 个区进程（不同 `--run`/端口/卷，均 `--auth on`），验证单区 150 席 P95 仍 <300ms。
4. 先上跨区聊天（低风险），再上跨区镖单（escrow + 对账）；每步都补幂等键与死信处理。
5. 按区复跑 `tools/load_test.py` 50×10 与 200 席基线；告警、备份、容量全部按区独立配置。
6. 升级流程改为**逐区停旧起新**（单写者不可双写）；内容升级需全区间时停机，避免 hash 分叉。

### 8.6 已知风险（未实现部分）

- **全局索引/投递库成为新的单写者热点**：轮询频率高时 `cross_region.sqlite` 会排队；备选是每区独立 outbox 文件 + 文件投递（仍不引入 MQ），但要自己做顺序与去重。
- **跨区最终一致**：网络/进程中断会造成“已交付未结算”，必须有对账任务与人工兜底；不能当作强一致交易。
- **角色迁移未实现**：目前只能整档 replay 到新区，单角色跨区搬档需要新工具与内容一致性校验。
- **世界时间不同步**：各区的世界日不同，跨区活动/榜单要按真实时间而非 tick。
- **运维面 ×N**：备份、恢复、告警、僵尸会话清理都要按区执行；全局索引需单独备份，损坏时从各区 `service.sqlite` 重建。
- **容量不可共享**：席位、钱包铸币额度、200 席上限都是区内概念；跨区不能“借席位”。

---

## 9. P0 备份与恢复（工具化，实测）

> 完整设计、manifest 字段、轮转算法与全部原始输出见
> [docs/BACKUP-RESTORE.md](BACKUP-RESTORE.md)。本节只放值守要用的结论与数字。

### 9.1 资产与命令

| 资产 | 位置 | 备份后丢失的后果 |
|---|---|---|
| 事件日志（唯一事实源） | `runs/<RUN_ID>/events.sqlite` | 世界状态全部丢失 |
| 账号/令牌 | `runs/<RUN_ID>/service.sqlite` | 玩家需重新注册/登录 |
| 备份产物 | `backups/<UTC 时间戳>/`（容器为 `xkx-w5-backups:/app/backups`） | — |

```bash
# 在线备份（服务不用停）
.venv/bin/python tools/backup_snapshot.py --root . --run service

# 校验并装回（必须先停服）
.venv/bin/python tools/restore_verify.py --root . \
  --backup-dir backups/<STAMP> --apply-dir runs/service
```

退出码：备份 `0/1/2`；恢复 `0` 通过、`2` 校验失败（任一失败都不得起服）。

### 9.2 轮转与调度

- 保留：**最近 48 小时全部** + **最近 7 个 UTC 日每日最新 1 份**；其余删除。
- 删除仅限 `backups/` 直接子目录，且必须带 `generator=tools/backup_snapshot.py` 的
  `manifest.json`；目录或其子项含符号链接时跳过并记录 `symlink_refused`。
- 宿主机：`scripts/backup_loop.sh`，默认 `BK_INTERVAL=21600`（6 小时），启动先备一次。
- 容器：`cd deploy && docker compose --profile backup up -d`（独立容器，不侵入世界进程；
  镜像需重建以包含 `tools/`）。主 `docker compose up -d` 不含备份容器，属 profile 的预期行为。

### 9.3 实测 RPO / RTO

| 项目 | 实测值 | 口径 |
|---|---|---|
| 备份耗时 | 2.46–6.29s（29MB / 29.4k 事件） | `duration_ms`，含 state_hash 重放 |
| 备份 SQLite copy | 91.9–334.8ms | `timing.backup_api_ms` |
| 备份对写路径阻塞 | 最大 **31.252ms**（另次 8.226ms） | 存储层连续写探针，阈值 200ms |
| RPO（6h 周期） | ≈ 6h00m07s | 6h + 最长备份耗时；事件增多会变长 |
| 演练实际丢失 | 52 秒 / 20 条事件 | 最后备份 17:27:30Z → 故障 17:28:22Z |
| RTO | **6.510s** | restore+verify 5.002s + API 到 `/api/state` 200 1.505s |
| 循环漂移 | ≤1s（10s 间隔，5 次） | 时间戳 17:36:18/29/40/50/17:37:00Z |

### 9.4 演练结论（2026-11-06）

- **A 正常恢复**：写入 6 个动作 → 备份（seq 29438）→ 停服 → 移走 `events.sqlite` →
  恢复（15/15 校验）→ 起服；`/api/state` HTTP 200，`location/market`、`wallet.money=300`、
  `tick=88152` 与备份前逐字段一致，seq `count=distinct=max=29438`。
- **B 损坏恢复**：截断到 4096 字节 → `sqlite3 PRAGMA integrity_check` 退出码 11
  （`database disk image is malformed`）→ API 启动退出码 1（`store.py:43` 同一报错）→
  从备份恢复 15/15 → 起服成功，`/api/health ok=true`、`/api/state` 200。
- **C 恢复后继续写**：恢复后的 run 再写 10 个动作，seq `32581–32600` 接续自 32580，
  `count=32600`、`distinct=32600`，无重复。
- **D 备份不阻塞**：同起点 20 读 + 10 写 ×30s，基线写 P95 `394.95 / 358.52ms`，
  备份期间 `269.86 / 368.44ms`（备份组不高于基线，单机噪声占主导）；
  存储层探针在 6.289s 备份内 310 次写，最大 **31.252ms**，证明 SQLite 备份读快照不阻塞 WAL 写者。

### 9.5 值守检查清单

- 每日看 `backups/` 是否有当天目录，`manifest.json` 的 `verification_ok=true`。
- 每周对最新备份跑一次 `restore_verify.py`（不带 `--apply-dir`），失败立即告警。
- 备份与主库同盘：磁盘损坏会双丢，异地副本不在 P0 范围。
- 内容包升级前先备份；升级后旧备份会因 `content_hash` 不符而无法启动（预期）。
- 备份含账号哈希/令牌：目录 `700`、文件 `600`，拷离机器前加密。

## 10. 世界丢失 fail-closed 与真实健康检查（2026-11-06）

### 10.1 启动前世界身份检查

服务在创建 `WorldSession` 之前先做一次只读检查（`api/world_guard.py`）：

| 场景 | 行为 | 退出码 |
| --- | --- | --- |
| 无 `service.sqlite` 或无账号 | 正常创世 | 0 |
| 有账号 + `events.sqlite` 缺失/0 事件 | 拒绝启动，日志一行 JSON `events_missing` | **4** |
| `events.sqlite` `PRAGMA integrity_check` 非 `ok` | 拒绝启动，`events_corrupt` | **5** |
| 内容包 `content_hash` 不符（既有行为） | 拒绝启动 | 3 |
| 有账号 + 丢库，但显式 `--allow-new-world` | 允许创世，日志 `world: new` | 0 |

拒绝启动时示例（stderr 单行 JSON）：

```json
{"accounts": 1, "code": "events_missing", "events_db": ".../runs/lost/events.sqlite",
 "exit_code": 4, "ok": false,
 "reason": "检测到已有账号数据但事件库缺失，疑似卷挂载错误；如需开新世界请显式 --allow-new-world",
 "service_db": ".../runs/lost/service.sqlite"}
```

首次创世会在 `runs/<run>/world_id.json` 写入世界身份标记
（`run_id` / `content_hash` / `seed` / `created_at`），既有世界不覆盖。

> 注意：`--allow-new-world` 只放行“丢库开新世界”，**不会**放行损坏库；损坏库必须从备份恢复。

### 10.2 `/api/health`

每次请求真实检查，返回 200/503：

```json
{"ok": true, "mode": "live", "readonly": false, "profile_version": 1, "upstream_ok": true,
 "run": "player-demo", "tick": 88152,
 "storage": {"events_ok": true, "seq_gap": 0, "disk_free_mb": 41234,
             "disk_free_pct": 41.2, "last_commit_age_s": 0.2, "integrity": "ok"},
 "world": {"run": "player-demo", "tick": 88152, "new_world": false}, "ambient": "running", ...}
```

判定口径：

- `storage.events_ok`：`EventStore.count()`（COUNT(*) 缓存）与 `EventTail.last_seq`
  （MAX(seq) 缓存）一致，且 `integrity_check = ok`。两者差异即 `seq_gap`；O(1)，不 `read_all`。
- `integrity_check` 默认 60 秒缓存（`XKX_INTEGRITY_CACHE_S`）。
- `disk_free_pct` 低于 5% → 503（`XKX_DISK_MIN_FREE_PCT`）。
- 写者最后提交（`events.sqlite` / `-wal` mtime）超过 60 秒 → 503（`XKX_WRITER_IDLE_MAX_S`）；
  刚启动时以进程启动时刻为基准，不会因旧库 mtime 误判。
- 任一不满足 → `ok=false` + **HTTP 503**；既有键保持不变，只新增 `storage` / `world`。

