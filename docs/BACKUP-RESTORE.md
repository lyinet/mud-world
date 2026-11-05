# 备份与恢复（P0）

> 适用版本：W5（`events.sqlite` 为唯一事实源）。本文所有数字均来自本机实测，
> 环境：macOS / Apple Silicon / Python 3.12 / sqlite 3.4x，run 为 `runs/p0-backup`
> （由 `runs/w5-fixed/events.sqlite` 复制而来，29.4k 事件、约 29MB）。
> 演练时间：2026-11-06（UTC 2026-11-05 17:2x–17:3x）。

## 0. 结论速览

| 指标 | 实测值 | 说明 |
|---|---|---|
| 备份方式 | `sqlite3.Connection.backup` 分页复制 | 不用 `cp`，不拷 `-wal`/`-shm` |
| 备份对写路径的阻塞 | 存储层最大写延迟 **31.252ms**（另一次 8.226ms） | 阈值 200ms，见 §7-D |
| 单次备份耗时（29MB / 29.4k 事件） | **2.46–6.29s** | 其中 SQLite copy 91.9–334.8ms，其余是 manifest 的 state_hash 重放 |
| RPO（6 小时周期） | **约 6h00m07s** | = 6h 间隔 + 实测最长备份耗时；演练中实际丢失窗口 52s / 20 条事件 |
| RTO（30MB 备份 → `/api/state` 200） | **6.510s** | restore+verify 5.002s + API 启动 1.505s |
| 演练 A/B/C/D | 全部通过 | 原始输出见 §7 |

一句话：**备份可在线执行、不阻塞写路径；恢复必须停服、先校验装回再起服。**

---

## 1. 资产与目录

| 文件 | 作用 | 丢失后果 |
|---|---|---|
| `runs/<RUN_ID>/events.sqlite` | 唯一事实源：事件日志 + `meta.content_hash` | 世界状态全部丢失（只能回到旧备份） |
| `runs/<RUN_ID>/service.sqlite` | 账号 / 令牌库（PBKDF2） | 账号与令牌丢失，玩家需重新注册/登录 |

备份产物：

```
backups/<YYYYMMDDTHHMMSSZ>/
├── events.sqlite      # 单文件，journal_mode=DELETE，无 -wal/-shm
├── service.sqlite     # 鉴权关闭的 run 可能不存在
└── manifest.json      # 校验与审计元数据
```

容器内备份写到独立卷 `xkx-w5-backups` 的 `/app/backups`；宿主机默认写到仓库根 `backups/`。
`backups/` 已加入 `.gitignore`，备份文件不入库。

---

## 2. manifest.json 字段

示例（数值取自演练 A）：

```json
{
  "schema_version": 1,
  "generator": "tools/backup_snapshot.py",
  "created_at": "2026-11-05T17:27:30Z",
  "created_at_unix": 1793899650,
  "run_id": "p0-backup",
  "run_dir": "/Users/lyinet/projects/XKX/world-sim/runs/p0-backup",
  "source": {
    "events.sqlite": ".../runs/p0-backup/events.sqlite",
    "service.sqlite": ".../runs/p0-backup/service.sqlite"
  },
  "databases": {
    "events.sqlite": {
      "present": true,
      "sha256": "fe24bfbc...b0a486",
      "size_bytes": 29401088,
      "integrity_check": "ok",
      "content_hash": "7ec670c1...c3d53f",
      "seq": {"min": 1, "max": 29438, "count": 29438, "distinct": 29438,
              "contiguous": true, "holes": []},
      "state_hash": "724384fd...24bdc2c",
      "event_hash": "b56125bf...54ccd4",
      "timing": {"backup_api_ms": 91.89, "steps": ...},
      "current_content_hash": "7ec670c1...c3d53f"
    },
    "service.sqlite": {"present": true, "sha256": "2426478d...be07262",
                       "size_bytes": 28672, "integrity_check": "ok"}
  },
  "duration_ms": 2459.841,
  "verification_ok": true,
  "failures": [],
  "prune": {"deleted": [], "keep": [...], "policy": {...}}
}
```

字段语义：

- `sha256` / `size_bytes`：备份文件本身的摘要与大小，`restore_verify.py` 逐项比对。
- `content_hash`：备份库 `meta.content_hash`，必须与当前 `content/` 一致才能启动。
- `seq`：备份时刻事件序号；`contiguous=true` 要求 `count=distinct=max-min+1` 且无洞。
- `state_hash` / `event_hash`：在**临时副本**上重放得出，用于恢复后一致性校验；
  重放不会修改备份文件本身。
- `prune`：本次轮转的删除/保留结果，便于审计。

---

## 3. 工具用法

### 3.1 在线备份

```bash
# 仓库根目录执行；服务运行中直接跑
.venv/bin/python tools/backup_snapshot.py --root . --run service

# 演练 / 取证：不触发轮转删除
.venv/bin/python tools/backup_snapshot.py --root . --run service --no-prune
```

- 退出码：`0` 成功；`1` 参数/IO/源库缺失；`2` 备份产物校验失败（integrity / seq / content_hash）。
- 标准输出是一行 JSON（备份目录、耗时、seq、hash、prune 结果），可直接被监控采集。
- 备份目录权限 `700`，文件 `600`（含账号哈希与令牌，别对全员开放）。

### 3.2 恢复并校验

```bash
# 1) 只恢复到临时目录并校验（不动生产 run）
.venv/bin/python tools/restore_verify.py --root . --backup-dir backups/20261105T172730Z

# 2) 校验通过后装回指定 run（务必先停服）
.venv/bin/python tools/restore_verify.py --root . --backup-dir backups/20261105T172730Z \
  --apply-dir runs/service
```

校验项（任一失败即退出码 `2`，并在 JSON 的 `checks` 里列出失败项）：

1. 文件存在，`sha256` 与 `size_bytes` 与 manifest 一致；
2. `PRAGMA integrity_check = ok`；
3. `seq` 连续无洞，`min/max/count` 与 manifest 一致；
4. 重放 `state_hash` 与 manifest 一致；
5. `content_hash` 与 manifest 一致，且与当前 `content/` 一致；
6. `event_hash` 与 manifest 一致。

`--apply-dir` 会先删除目标端陈旧的 `*.sqlite-wal` / `*.sqlite-shm`，再覆盖
`events.sqlite` 与 `service.sqlite`（备份里 `present=false` 的文件不覆盖）。

---

## 4. 轮转策略

常量写在 `tools/backup_snapshot.py` 顶部（改策略只改这里，并同步本文）：

```python
KEEP_ROLLING_HOURS = 48       # 滚动窗口：最近 48 小时全留
KEEP_DAILY_DAYS = 7           # 日备份：最近 7 个 UTC 日，每日保留最新 1 份
ROLLING_INTERVAL_HOURS = 6    # 调度间隔（scripts/backup_loop.sh 默认 21600 秒）
```

规则 = **最近 48 小时全部保留** ∪ **最近 7 个 UTC 日每日 1 份**，其余删除。
按 6 小时一份，48 小时窗口约 8 份，日备份再兜住更早的 7 天。

安全约束（删除代码里逐条校验）：

- 只扫描 `backups/` 的**直接子目录**，名称必须匹配 `YYYYMMDDTHHMMSSZ`（可带 `-2` 后缀）；
- 目录内 `manifest.json` 的 `generator` 必须等于 `tools/backup_snapshot.py`（不删来历不明的目录）；
- 目标目录是符号链接 → 跳过；目录内任何子项是符号链接 → 跳过；
- `resolve()` 后父目录不等于 `backup_dir` → 跳过；
- 删除失败只记录到 `prune.skipped`，不影响本次备份成功。

演练（合成目录，`now=2026-11-05T19:00Z`）：删除 `20261001T000000Z`、`20261028T000000Z`
（超 7 天）；保留 `20261030`、`20261101`、`20261103`、`20261105T120000Z`、
`20261105T180000Z`（48 小时窗口与日备份的并集）；`victim` 目录与符号链接目录
`20261104T120000Z` 未被删除。

---

## 5. 调度

### 5.1 宿主机：`scripts/backup_loop.sh`

```bash
# 默认每 6 小时（21600 秒）一次；启动时先备份一次，然后 sleep
scripts/backup_loop.sh

# 演练可缩短间隔（单位：秒）
BK_INTERVAL=10 BK_RUN=service scripts/backup_loop.sh
```

环境变量：

| 变量 | 默认 | 含义 |
|---|---|---|
| `BK_ROOT` | 仓库根 | 含 `runs/`、`content/`、`tools/` 的根 |
| `BK_RUN` | `service` | `runs/<RUN_ID>` |
| `BK_DIR` | `$BK_ROOT/backups` | 备份根目录 |
| `BK_INTERVAL` | `21600` | 间隔秒数（6 小时） |
| `BK_PYTHON` | `.venv/bin/python` 或 `python3` | 解释器 |

收到 `TERM/INT` 时打印 `收到停止信号，退出` 并退出；单轮失败只记日志，下一轮继续。

**实测循环漂移**（`BK_INTERVAL=10s`，小 run，SQLite copy 耗时 4–12ms）：
备份目录时间戳 `17:36:18Z / 17:36:29Z / 17:36:40Z / 17:36:50Z / 17:37:00Z`，
相邻间隔 `11s / 11s / 10s / 10s`，**漂移 ≤1s，不累积**。

### 5.2 容器：compose `backup` profile（已选方案）

```bash
cd deploy
docker compose --profile backup up -d        # Compose v2
# 本机 legacy CLI 同样可用：
docker-compose -f docker-compose.yml --profile backup up -d
```

备份服务 `backup` 用同一镜像，`profiles: ["backup"]`，只挂
`xkx-data:/app/runs` 与 `xkx-backups:/app/backups`，循环执行
`tools/backup_snapshot.py`。主服务 `world` 不受影响。

**为什么选 profile 而不是 entrypoint 侧开关：**

- entrypoint 开关把备份塞进世界容器：世界重启会打断备份节奏；备份失败会混进
  世界容器的退出码/健康检查；还要在 trap/wait 里额外管理一个子进程，踩坑面更大。
- profile 是独立容器：备份崩了不影响世界，世界重启也不影响备份窗口；职责单一。
- 宿主零依赖：不需要 cron/systemd，一条命令即可。
- 代价：镜像必须重建（`deploy/Dockerfile` 已加 `COPY tools /app/tools`）；
  `docker compose up` 不带 `--profile backup` 时不会启动备份容器（这是基于 profile 的预期行为）。

已做静态校验：`docker-compose -f deploy/docker-compose.yml --profile backup config`
渲染出 `world` + `backup` 两个服务，卷映射与命令正确。
**本机未做容器运行时演练**（避免重建镜像影响在跑的世界容器）；首次上线请按
§6 在容器里跑一次备份 + `restore_verify` 再转为定时。

---

## 6. 恢复步骤（生产）

1. **停服**（单写者，不能边恢复边写）：
   ```bash
   docker compose stop world        # 或 kill API 进程
   ```
2. **选备份**：看 `manifest.json` 的 `seq.max` / `created_at`，选故障前最近一份。
3. **先校验、再装回**：
   ```bash
   .venv/bin/python tools/restore_verify.py --root . \
     --backup-dir backups/20261105T172730Z --apply-dir runs/service
   ```
   退出码必须是 `0`（JSON `ok=true`、`checks_passed=checks_total`）。任何非零都**不要**继续。
4. **起服并验证**：
   ```bash
   curl -s http://127.0.0.1:8020/api/service/info
   curl -s http://127.0.0.1:8020/api/state?account_id=<account>   # HTTP 200
   .venv/bin/python tools/load_test.py --verify-only --run-dir runs/service
   ```
5. **通知玩家重新登录**（`service.sqlite` 回到备份时点，备份后签发的令牌/新账号会丢失）。
6. 内容 hash 不符（起服退出码 `3`）：备份对应旧 `content/`，要么恢复同版本内容包，
   要么用新内容开新 run；**不要手改 `meta.content_hash`**。

---

## 7. 演练记录（原始输出）

### A. 正常恢复演练

步骤：真实 API（`--auth on`，端口 8099）起在 `runs/p0-backup` → 注册 `p0test` 并写入
6 个动作 → 在线备份 → 停服 → 移走 `events.sqlite` → `restore_verify --apply-dir` →
重启 → 比对状态。

备份输出（节选）：

```json
{"backup_dir": ".../backups/20261105T172730Z", "duration_ms": 2459.841,
 "files": {"events.sqlite": {"integrity_check": "ok",
   "sha256": "fe24bfbcfd760cddaf044ff103a5468411c88ae9a93b12fb9f7e102261b0a486",
   "size_bytes": 29401088},
  "service.sqlite": {"integrity_check": "ok", "size_bytes": 28672}},
 "events": {"backup_api_ms": 91.89,
   "content_hash": "7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f",
   "seq": {"min": 1, "max": 29438, "count": 29438, "contiguous": true, "holes": []},
   "state_hash": "724384fdf46a52aedff2288f9db2a91c554b5c65bb55036b385a56a5d24bdc2c",
   "event_hash": "b56125bf62a9834c785c1ce11fc223463690f9bbb2012dfa56ea49372654ccd4"}}
```

恢复校验：`15/15` 项通过，`ok=true`，退出码 `0`；`--apply-dir` 装回
`events.sqlite` + `service.sqlite`。

备份前 / 恢复后 `GET /api/state?account_id=p0test` 的同一子集（HTTP 200）：

```json
{"location": "market", "tick": 88152, "stamina": 100.0, "online": true,
 "wallet": {"money": 300,
   "items": [{"item": "coarse_cloth", "qty": 1}, {"item": "flatbread", "qty": 3}]},
 "endowed": true, "endowed_tick": 88110, "present_npcs":
 ["duan_wudou","jin_mancang","liu_sanniang","luo_qingxu","meng_jiuniang","ruan_qingfu","xing_pojun"]}
```

比对结果：`subset_equal=true`（脚本逐字段比对，无 DIFF）。
恢复后 `events.sqlite`：`count=min=1, max=29438, distinct=29438`，seq 连续。

### B. 损坏恢复演练（最有说服力的一项）

1. 把 `events.sqlite` 截断到 4096 字节（`head -c 4096 ...` 覆盖）。
2. 直接检查：`sqlite3 ... "PRAGMA integrity_check;"` → `Error: ... database disk image is malformed (11)`，退出码 **11**。
3. 起服：退出码 **1**，日志末尾：

```text
File ".../src/wsim/world/store.py", line 43, in __init__
    self.conn.execute("PRAGMA journal_mode=WAL")
sqlite3.DatabaseError: database disk image is malformed
```

4. `restore_verify --apply-dir runs/p0-backup`：`ok=true`、`checks_passed=15 / checks_total=15`、退出码 **0**。
5. 再次起服成功：`/api/service/info` 返回 200；`/api/health`：

```json
{"ok": true, "mode": "live", "run": "p0-backup", "tick": 88152,
 "ambient": "stopped", "readonly": false, "upstream_ok": true}
```

`/api/state?account_id=p0test` 返回 HTTP **200**，`seq max=29438`。

### C. 恢复后继续写

在恢复后的同一 run 上（期间跑过 D，起始 seq 为 32580）写入 10 个动作：

```json
{"success": 10, "ticks": [97464,97470,97476,97482,97488,97494,97500,97506,97512,97518]}
```

写后 Seq：

```text
before: 32580|32580
after : 32600|1|32600|32600      -- count|min|max|distinct
32581|talk|player:p0test
32582|system_tick|system
...
32600|system_tick|system
```

结论：从备份末尾接续（32581 = 32580+1），20 条事件（10 动作 × talk+system_tick），
`count=distinct=32600`，无重复。

### D. 备份对写路径的影响

同口径：20 并发读（`GET /api/state`）+ 10 并发写（`talk` / `cultivate`），`think=50ms`，
每组 30 秒；**每个试验都从同一份 `runs/w5-fixed` 副本冷启动**，交替顺序跑两次基线、
两次备份并发，避免世界演化带来的偏差。备份在压测第 10 秒启动。

| 试验 | 写入次数 | 写 P50 | 写 P95 | 写 P99 | 写 max | 读 P95 | 备份 SQLite copy |
|---|---|---|---|---|---|---|---|
| 基线 a1 | 670 | 163.1ms | 394.95ms | 669.3ms | 1127.5ms | 354.8ms | — |
| 基线 a2 | 723 | 168.0ms | 358.52ms | 570.3ms | 1027.7ms | 324.3ms | — |
| 备份 b1 | 896 | 130.0ms | 269.86ms | 492.5ms | 750.0ms | 242.8ms | 291.5ms |
| 备份 b2 | 673 | 180.8ms | 368.44ms | 671.2ms | 2100.7ms | 335.1ms | 334.8ms |

读数：基线中位 P95 **376.7ms**，备份期间中位 P95 **319.2ms**——备份组不高于基线组，
差异被单机噪声/世界演化吞没（两组的 max 都有 0.75–2.1s 的离群值）。
**不能把 HTTP 层 P95 的波动归因于备份**；真正的锁竞争证据看下面的存储层探针。

存储层探针（直接对 `events.sqlite` 连续写，不用 HTTP，避免 GIL/全局锁噪声；
备份在中途启动，覆盖后半段全部写入）：

```json
{"writes": 400, "backup_start_offset_s": 0.57, "backup_total_s": 6.289,
 "all": {"p50": 0.084, "p95": 0.407, "max": 31.252},
 "outside_backup": {"n": 90, "p95": 0.445, "max": 1.388},
 "during_backup": {"n": 310, "p95": 0.378, "max": 31.252}}
```

即：备份执行 6.289s 期间 310 次写，**最大写延迟 31.252ms**，远低于 200ms 门槛
（另一次探针为 8.226ms）。这证明 `Connection.backup` 的读快照不会阻塞 WAL 写者。

---

## 8. RPO / RTO（实测口径）

### RPO

- 调度常量 `BK_INTERVAL=21600s`；按 6 小时周期，最坏丢失窗口 = 6 小时 + 单次备份耗时。
  本机 29MB / 29.4k 事件实测备份耗时 **2.46–6.29s** → 生产 RPO ≈ **6h00m07s**（取最长）。
  事件数增长会拉长 `state_hash` 重放，因此 RPO 会随 `events.sqlite` 体量增加；
  到 200k 事件前请重新测一次并把该值写回本节。
- 演练实测（B）：最后备份 `17:27:30Z`（seq 29438）→ 故障 `17:28:22Z`（52 秒后），
  期间写入 20 条事件（10 个动作）；恢复后这 20 条丢失。这是“从备份到故障”的
  **实际丢失窗口**，不是生产上限。

### RTO

用备份 `backups/20261105T173139Z`（30,195,712 字节 / seq 30205）实测：

```json
{"restore_verify_s": 5.002, "api_start_to_ready_s": 1.505, "rto_total_s": 6.51}
```

分解：restore_verify（复制 + sha256 + integrity + seq + 两次重放）5.0s；
API 从进程启动到 `/api/state` 200 为 1.5s。生产 RTO 还需加上人工发现/决策与
`docker compose stop/start` 的时间，本值只覆盖“拿到备份后到服务可用”。

---

## 9. 已知限制

1. **备份与主库同盘**：宿主 `backups/` 或容器卷 `xkx-w5-backups` 都在同一台机器，
   磁盘/主机损坏会同时丢主库和备份。P0 不包含异地/对象存储；如需要，另加 `rsync`/S3 同步。
2. **无加密**：备份包含 `service.sqlite`（密码哈希、令牌）。目录 `700`、文件 `600` 仅防同机其他用户；
   拷离机器前应加密（如 `age`/`gpg`）。
3. **state_hash 重放成本**：备份时会完整重放事件日志算 hash，耗时随事件数线性增长；
   当前 29.4k 事件占 2.5–6.3s 总耗时（SQLite copy 仅约 0.1–0.3s）。
4. **内容包升级后的旧备份**：`content_hash` 不匹配时服务会拒绝启动（退出码 3）。
   恢复旧备份必须同时恢复对应的 `content/`，或用新内容开新 run；工具会以退出码 2 拒绝装回。
5. **service.sqlite 时点回滚**：恢复后备份之后注册的账号、签发的令牌失效，玩家需重新登录。
6. **恢复必须停服**：工具只做文件装回，不检测端口占用；并发写会造成新旧库混写。
7. **定时任务不做自动恢复演练**：轮转只保证备份存在；建议每周对最新一份跑一次
   `restore_verify.py`（不带 `--apply-dir`），失败告警。
8. **容器 profile 未做运行时演练**：只做了 `docker-compose config` 静态校验；
   首次上线请重建镜像并在容器内执行一次备份 + 校验。
9. **备份目录不能是符号链接**：`--backup-dir` 指向符号链接时工具直接拒绝；
   轮转删除也会跳过任何含符号链接的备份目录。

---

## 10. 常见故障

| 现象 | 原因 | 处置 |
|---|---|---|
| 备份退出码 2，`failures` 含 `integrity_check` | 源库已损坏或正在被非 sqlite 方式改写 | 立即停服，保留现场，改用更早的完好备份 |
| 备份退出码 1，找不到源库 | `--run` 写错 / 服务还没创建 `events.sqlite` | 确认 `runs/<RUN_ID>/events.sqlite` 存在 |
| 恢复退出码 2，`content_hash_matches_current_content=false` | 备份对应旧 `content/` | 恢复对应内容包，或换备份 |
| 恢复成功但起服退出码 3 | 同上（content_hash 不符） | 不要手改 hash；按 §6.6 处理 |
| 恢复后玩家 401 | `service.sqlite` 回滚，旧令牌失效 | 让玩家重新登录 |
| 备份耗时突然变长 | 事件数增长 / 磁盘慢 / 同时有大量写 | 看 `duration_ms` 与 `timing.backup_api_ms`，必要时扩备份频率 |
| `prune.skipped` 出现 `symlink_refused` | 备份目录被替换成符号链接（可疑） | 人工检查，不要自动删除 |
