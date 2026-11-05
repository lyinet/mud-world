# 备份与恢复（P0）

> 适用版本：W5（`events.sqlite` 为唯一事实源）。本文所有数字均来自本机实测。
> 两套口径：**宿主机**（macOS / Apple Silicon / Python 3.12 / sqlite 3.4x，run 为
> `runs/p0-backup`，由 `runs/w5-fixed/events.sqlite` 复制而来，29.4k 事件、约 29MB）；
> **容器**（colima 29.5.2 / aarch64，镜像 `xkx/world-sim:w5`，run 为 `p0ctr`，
> 445 事件、400KB 备份）。
> 演练时间：2026-11-06（UTC 2026-11-05 17:2x–17:5x）。

## 0. 结论速览

| 指标 | 实测值 | 说明 |
|---|---|---|
| 备份方式 | `sqlite3.Connection.backup` 分页复制 | 不用 `cp`，不拷 `-wal`/`-shm` |
| 备份对写路径的阻塞 | 存储层最大写延迟 **31.252ms**（另一次 8.226ms） | 阈值 200ms，见 §7-D |
| 单次备份耗时（29MB / 29.4k 事件，宿主） | **2.46–6.29s** | 其中 SQLite copy 91.9–334.8ms，其余是 manifest 的 state_hash 重放 |
| 单次备份耗时（400KB / 445 事件，容器） | **1.818s** | 容器内 `docker exec` 实测，见 §7-E |
| RPO（6 小时周期） | **约 6h00m07s**（宿主 29MB）/ **6h00m02s**（容器 400KB） | = 6h 间隔 + 实测最长备份耗时；演练中实际丢失窗口 52s / 20 条事件 |
| RTO（宿主 30MB 备份 → `/api/state` 200） | **6.510s** | restore+verify 5.002s + API 启动 1.505s |
| RTO（容器 400KB 备份，停服→服务可用） | **3.174s** | 停服 1.135s + restore/verify/apply 0.605s + 启动到可用 1.435s，见 §7-E |
| 演练 A/B/C/D | 全部通过 | 原始输出见 §7 |
| 演练 E（容器） | 通过 | 真实容器内备份/损坏/恢复，原始输出见 §7-E |
| 异地副本（offsite） | 已实现并实测 | `tools/backup_offsite.sh`，远端取 sha256 逐项比对；见 §5.3 |

一句话：**备份默认执行、失败即非零退出且 `unhealthy`；异地副本在远端取 sha256 校验；
恢复必须停服、先校验装回再起服。**

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
| `BK_RETRIES` | `2` | 单轮失败重试次数（总尝试 = RETRIES+1） |
| `BK_RETRY_DELAY` | `30` | 重试间隔秒 |
| `BK_STARTUP_WAIT` | `300` | 首轮等源库出现的窗口（world 建库竞态） |
| `BK_PYTHON` | `.venv/bin/python` 或 `python3` | 解释器 |

**失败语义（不再静默）**：单轮按 `BK_RETRIES` 重试；仍失败 → 写
`last_backup_status.json`（`ok=false` + `stage` + `exit_code` + `error`）→ 打印原因 →
`exit 1`。收到 `TERM/INT` 时打印 `收到停止信号，退出` 并退出 0（人工停服不算失败）。

**实测循环漂移**（`BK_INTERVAL=10s`，小 run，SQLite copy 耗时 4–12ms）：
备份目录时间戳 `17:36:18Z / 17:36:29Z / 17:36:40Z / 17:36:50Z / 17:37:00Z`，
相邻间隔 `11s / 11s / 10s / 10s`，**漂移 ≤1s，不累积**。

### 5.2 容器：默认即备份（`docker compose up` 不带 profile）

```bash
cd deploy
docker compose up -d --build        # 备份容器随主服务一起起来，不需要 --profile
```

`backup` 服务是 **compose 默认服务**（`profiles` 已移除），只挂
`xkx-w5-data:/app/runs` 与 `xkx-w5-backups:/app/backups`，运行
`scripts/backup_loop.sh`。`restart: "no"`：失败后保持 `Exited(非零)` 等人工介入，
不在坏盘上无限重启。

- **健康检查**：`deploy/backup_healthcheck.py` 读 `last_backup_status.json`，以下任一
  判 `unhealthy`：文件缺失（一轮都没跑成）/ `ok=false` / 距今超过
  `BK_INTERVAL × 2 + 600s`（进程卡死或未调度）。
- **为什么保留独立容器而不是塞进 entrypoint**：世界重启不打断备份节奏；备份失败不会
  混进世界容器的退出码/健康检查；职责单一，无需在 trap/wait 里管理子进程。
- **启动顺序**：首次 `up` 时 `backup` 可能先于 `world` 建库，`BK_STARTUP_WAIT`
  （默认 300s）会等 `events.sqlite` 出现；等不到仍 `exit 1`，不是吞错。

**实测（2026-11-06，colima 29.5.2 / aarch64）**

```text
$ docker-compose up -d          # 不带 --profile
Creating xkx-world ... done
Creating xkx-backup ... done
$ docker ps --format '{{.Names}}\t{{.Status}}'
xkx-world    Up 40 seconds (healthy)
xkx-backup   Up 40 seconds (healthy)
$ docker exec xkx-backup ls -la /app/backups
drwx------ 2 app app 4096 … 20261105T181308Z
-rw-r--r-- 1 app app 1560 … last_backup_status.json
```

失败可见（只读备份卷 / 源库截断）的原始输出见 §7-F。

若改用宿主机 cron/systemd 而不跑 backup 容器，等价方案是：
`*/6 * * * * cd /path/world-sim && BK_RUN=service scripts/backup_loop.sh`，
备份目录用宿主机卷或直接写到另一块盘（见 §5.3 异地要求）。

### 5.3 异地副本：`tools/backup_offsite.sh`

把**最新一份**备份推送到异机/异地，并在**远端**重新取 sha256 与 size，与本地
`manifest.json` 的 `databases.<name>.sha256/size_bytes` 逐项比对。任一文件缺失、
读取失败、sha256 或 size 不一致 → 非零退出。**不存在"只比对本地文件"的路径。**

| 环境变量 | 默认 | 含义 |
|---|---|---|
| `XKX_OFFSITE_MODE` | `disabled` | `disabled` / `local` / `rsync` / `rclone` |
| `XKX_OFFSITE_TARGET` | 空 | 目录（`local`，可写 `file://` 前缀）/ `user@host:/path`（`rsync`）/ remote 路径（`rclone`） |
| `XKX_OFFSITE_REQUIRED` | `0` | `1` = 生产语义：未配置目标即失败（退出码 3） |
| `XKX_OFFSITE_SSH_HOST` | 空 | `rsync` 模式远端主机（`TARGET` 只给路径时使用） |
| `XKX_OFFSITE_SSH_OPTS` | `-o BatchMode=yes -o ConnectTimeout=10` | 额外 ssh 参数 |

三种模式：

```bash
# a) 本机另一块盘 / NAS 挂载点（也可用它做演练）
XKX_OFFSITE_MODE=local XKX_OFFSITE_TARGET=/mnt/offsite sh tools/backup_offsite.sh --backup-dir backups

# b) rsync over ssh（镜像已自带 rsync + openssh-client）
XKX_OFFSITE_MODE=rsync XKX_OFFSITE_SSH_HOST=backup-host \
  XKX_OFFSITE_TARGET=/srv/xkx-backups sh tools/backup_offsite.sh --backup-dir backups

# c) 对象存储（需 image/主机里有 rclone，并已 rclone config 配置 remote）
XKX_OFFSITE_MODE=rclone XKX_OFFSITE_TARGET=s3:xkx-backups sh tools/backup_offsite.sh --backup-dir backups
```

**失败语义**：

| 场景 | 退出码 | 输出 / 状态 |
|---|---|---|
| 未配置（`disabled`）| `0` | `{"ok": true, "offsite": "disabled", ...}`，状态文件里显式 `offsite=disabled` |
| 未配置且 `XKX_OFFSITE_REQUIRED=1` | `3` | `{"ok": false, "error": "offsite_required_but_disabled"}` |
| 推送失败（mkdir/cp/rsync/rclone） | `1` | `stage=push` + `error=*_failed` |
| 远端校验失败（sha256/size 不一致、远端文件缺失） | `1` | `stage=verify`，逐文件列出 `sha256` / `remote_sha256` / 大小 |
| 用法/配置错误（缺 `--backup-dir`、非法 mode） | `2` | `stage=config` |

`scripts/backup_loop.sh` 每轮备份成功后都会调用本工具；**offsite 失败会让整个
backup 容器 `exit 1`**，状态文件 `stage="offsite"`，容器转 `Exited(非零)`。

运维复核（不推送，只核对远端已有副本）：`--verify-only`。

```bash
XKX_OFFSITE_MODE=local XKX_OFFSITE_TARGET=/mnt/offsite \
  sh tools/backup_offsite.sh --backup-dir backups --verify-only
```

**实测（本地目录模拟异地，2026-11-06）**

推送 → 远端校验通过：

```text
$ XKX_OFFSITE_MODE=local XKX_OFFSITE_TARGET=/tmp/xkx-offsite \
    sh tools/backup_offsite.sh --backup-dir backups
[offsite] 最新备份: 20261105T181943Z
{"ok": true, "mode": "local", "target": "/tmp/xkx-offsite/20261105T181943Z", "offsite": "enabled",
 "files": [{"name": "events.sqlite", "sha256": "d65cf12d…cbfb4", "remote_sha256": "d65cf12d…cbfb4",
            "size_bytes": 32788480, "remote_size_bytes": 32788480, "verified": true},
           {"name": "service.sqlite", "sha256": "910dcf93…93ad", "remote_sha256": "910dcf93…93ad",
            "size_bytes": 45056, "remote_size_bytes": 45056, "verified": true}]}
EXIT=0
```

远端被截断（4096 字节）→ 校验失败：

```text
$ XKX_OFFSITE_MODE=local XKX_OFFSITE_TARGET=/tmp/xkx-offsite \
    sh tools/backup_offsite.sh --backup-dir backups --verify-only
{"ok": false, … "stage": "verify",
 "error": "events.sqlite: sha256 mismatch remote=310bbc65946b manifest=d65cf12da0fc",
 "files": [{"name": "events.sqlite", "remote_sha256": "310bbc65…", "remote_size_bytes": 4096,
            "verified": false}, …]}
[offsite] 远端校验失败
EXIT=1
```

远端文件缺失 → `remote_read_failed`：

```text
{"ok": false, … "stage": "verify",
 "error": "events.sqlite: remote_read_failed (remote file missing: /tmp/xkx-offsite/20261105T181943Z/events.sqlite)"}
EXIT=1
```

`scripts/backup_loop.sh` 接入后的两种语义：

```text
# 远端不可写（目标目录 500）
[backup-loop] 备份成功（第 1 次，1072 字节结果）
[backup-loop] 异地推送失败 exit=1：[offsite] mkdir: /tmp/xkx-ro/sub: Permission denied …
[backup-loop] 异地推送失败，退出码 1，容器将进入 Exited(非零) 状态
LOOP_EXIT=1
状态文件: {"ok": false, "stage": "offsite", "exit_code": 1,
           "error": "… Permission denied …", "offsite": {"ok": false, "stage": "push",
           "error": "target_mkdir_failed"}}

# 生产语义但未配置远端
[backup-loop] 异地推送失败 exit=3：[offsite] XKX_OFFSITE_REQUIRED=1 但未配置异地目标
LOOP_EXIT=1
状态文件: {"ok": false, "stage": "offsite", "exit_code": 3,
           "offsite": {"ok": false, "offsite": "disabled",
           "error": "offsite_required_but_disabled"}}
```

**容器内的注意点**（实测）：

- 镜像已装 `rsync` 与 `openssh-client`（`rclone` 未装，对象存储模式请用派生镜像或宿主执行）。
- `local` 模式的目标若是 bind mount，宿主目录必须对容器内的 `uid 10001` 可写；
  实测未授权时 `mkdir: Permission denied` → `stage=push` 退出 1（不会静默）。
- `rsync` 模式需把私钥/known_hosts 以只读方式挂进容器，或使用 `ssh-agent`；
  `BatchMode=yes` 禁止交互式口令，避免容器里挂起。

---

## 6. 恢复步骤（生产）

1. **停服**（单写者，不能边恢复边写）：
   ```bash
   docker compose stop world        # 或 kill API 进程
   ```
2. **选备份**：看 `manifest.json` 的 `seq.max` / `created_at`，选故障前最近一份。
   本地可用时优先用本地最新一份；本地丢失时才用异地副本（选哪份、怎么核对见 §9.2）。
3. **先校验、再装回**：
   ```bash
   # 宿主机（备份在仓库根 backups/）
   .venv/bin/python tools/restore_verify.py --root . \
     --backup-dir backups/20261105T172730Z --apply-dir runs/service

   # 容器（备份在 xkx-w5-backups 卷；backup 容器同时挂数据卷与备份卷）
   docker exec xkx-backup python /app/tools/restore_verify.py --root /app \
     --backup-dir /app/backups/20261105T172730Z --apply-dir /app/runs/service
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

### E. 容器内备份与恢复演练（2026-11-06，colima 29.5.2 / aarch64）

环境：镜像 `xkx/world-sim:w5`（legacy builder 构建，BuildKit 的 buildx 组件在本机缺失），
run `p0ctr`，数据卷 `xkx-w5-data`、备份卷 `xkx-w5-backups`，宿主端口 8020/8030。

**E.1 世界与备份服务同时在线，产物在卷上**

```
$ docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
xkx-world    xkx/world-sim:w5   Up 35 seconds (healthy)  0.0.0.0:8020->8020/tcp, [::]:8020->8020/tcp, 0.0.0.0:8030->8030/tcp, [::]:8030->8030/tcp
xkx-backup   xkx/world-sim:w5   Up 6 minutes             8020/tcp, 8030/tcp

$ docker inspect xkx-backup --format '{{range .Mounts}}{{if .Name}}{{.Name}}{{end}} -> {{.Destination}}{{"\n"}}{{end}}'
xkx-w5-data -> /app/runs
xkx-w5-backups -> /app/backups
```

`/app/backups` 是命名卷 `xkx-w5-backups`，**不是容器临时层**。同一路径用独立容器挂同卷可见同一批文件：

```
$ docker run --rm --entrypoint sh -v xkx-w5-backups:/b xkx/world-sim:w5 -c 'ls -la /b; stat -c "%d %i %n" /b/20261105T175328Z/events.sqlite'
total 16
drwxr-xr-x 4 app  app  4096 Nov  5 17:53 .
drwxr-xr-x 1 root root 4096 Nov  5 17:49 ..
drwx------ 2 app  app  4096 Nov  5 17:52 20261105T175254Z
drwx------ 2 app  app  4096 Nov  5 17:53 20261105T175328Z
64785 149641 /b/20261105T175328Z/events.sqlite
```

`64785` 是卷所在设备号，说明文件在卷上而非容器可写层。

**E.2 容器内执行备份**

```
$ docker exec xkx-backup python /app/tools/backup_snapshot.py --root /app --run p0ctr --backup-dir /app/backups
{"ok": true, "backup_dir": "/app/backups/20261105T175328Z", "created_at": "2026-11-05T17:53:28Z",
 "duration_ms": 1818.409, "failures": []}
events seq: {'contiguous': True, 'count': 445, 'distinct': 445, 'holes': [], 'max': 445, 'min': 1}
events sha256: c8d4bf78aa135f6a673d066df07d16048e9327552c576b6cdaaadca08b6b5378 size 409600
service sha256: f71de750c5b29e5ccfc70cc0aab52669c33b0b57849a412d954234c7a33ad043 size 28672
state_hash: 0ed32b7a0959cddde9fa0ffcc7521f1b675cad6de18eaf4f5e1be675acd3d8a5
backup_api_ms: 307.302
```

```
$ docker exec xkx-backup ls -la /app/backups/20261105T175328Z
-rw------- 1 app app 409600 Nov  5 17:53 events.sqlite
-rw------- 1 app app   2229 Nov  5 17:53 manifest.json
-rw------- 1 app app  28672 Nov  5 17:53 service.sqlite
drwx------ 2 app app   4096 Nov  5 17:53 .          # 目录 700、文件 600
```

**E.3 容器内造损坏 → 起服失败 → 恢复 → 起服成功**

```
# 停服后在数据卷内把 events.sqlite 截断到 4096 字节
$ docker run --rm --entrypoint python -v xkx-w5-data:/app/runs xkx/world-sim:w5 -c "
from pathlib import Path
p=Path('/app/runs/p0ctr/events.sqlite'); n=p.stat().st_size
p.write_bytes(p.read_bytes()[:4096]); print(f'truncated {n} -> {p.stat().st_size}')"
truncated 430080 -> 4096

# 起服：API 进程在 EventStore 打开时即失败，entrypoint 30s 等不到 API，容器退出
$ docker start xkx-world && sleep 40
$ docker ps -a --filter name=xkx-world --format '{{.Names}}\t{{.Status}}'
xkx-world    Exited (1) 9 seconds ago
$ docker inspect xkx-world --format 'status={{.State.Status}} exit_code={{.State.ExitCode}}'
status=exited exit_code=1

$ docker logs --tail=6 xkx-world
  File "/app/src/wsim/world/store.py", line 43, in __init__
    self.conn.execute("PRAGMA journal_mode=WAL")
sqlite3.DatabaseError: database disk image is malformed
wait_for: http://127.0.0.1:8020/api/service/info 在 30.0s 内不可达
[entrypoint] API 未在 30s 内就绪，退出
```

（`restart: unless-stopped` 下会持续重启，实测 `RestartCount` 递增 0→1→2→3，属预期：
损坏不可自愈，必须人工恢复。演练时用 `docker update --restart=no` 才拿到干净的
`Exited (1)` 与退出码。）

```
# 从备份恢复（backup 容器同时挂数据卷与备份卷，无需额外容器）
$ docker exec xkx-backup python /app/tools/restore_verify.py --root /app \
    --backup-dir /app/backups/20261105T175328Z --apply-dir /app/runs/p0ctr
RESTORE_EXIT=0
ok= True passed= 15 / 15
  PASS events_file_present
  PASS events.sqlite_sha256
  PASS events.sqlite_size
  PASS events.sqlite_integrity
  PASS service.sqlite_sha256
  PASS service.sqlite_size
  PASS service.sqlite_integrity
  PASS events_seq_contiguous
  PASS events_seq_min
  PASS events_seq_max
  PASS events_seq_count
  PASS events_content_hash_matches_manifest
  PASS content_hash_matches_current_content
  PASS state_hash_matches_manifest
  PASS event_hash_matches_manifest
applied: {"apply_dir": "/app/runs/p0ctr",
          "files": ["/app/runs/p0ctr/events.sqlite", "/app/runs/p0ctr/service.sqlite"]}

# 恢复后文件大小回到备份值，integrity ok
$ docker run --rm --entrypoint sh -v xkx-w5-data:/app/runs xkx/world-sim:w5 -c 'ls -la /app/runs/p0ctr/'
-rw-r--r-- 1 app app 409600 Nov  5 17:56 events.sqlite
-rw-r--r-- 1 app app  28672 Nov  5 17:56 service.sqlite
```

```
# 起服并验证
$ docker start xkx-world && sleep 25
$ docker ps --format '{{.Names}}\t{{.Status}}'
xkx-world    Up 35 seconds (healthy)
$ docker inspect xkx-world --format 'health={{.State.Health.Status}}'
health=healthy
$ GET /api/state?account_id=p0ctr_user  ->  http= 200
persistent= {"location": "market", "wallet": {"items": [...], "money": 300},
             "endowed": true, "endowed_tick": 13, "stamina": 100.0}   # 与备份前一致

$ docker exec xkx-world python -c "..."   # 恢复后（含恢复后 ambient 继续推进）
{"count": 461, "min": 1, "max": 461, "distinct": 461, "integrity": "ok"}
```

恢复点为 `seq 445`（manifest `seq.max`），起服后世界自走继续写 446…，因此现场是
`min=1 / max=461 / count=distinct=461`，**seq 连续、无重复**；玩家持久字段（地点、钱包、
初始资产、体力）与备份前逐字段一致。

**E.4 RTO（容器，停服→服务可用）**

```json
{"stop_ms": 1135, "restore_verify_apply_ms": 605, "start_to_available_ms": 1435,
 "rto_total_ms": 3174}
```

备份 400KB / 445 事件；`restore_verify` 纯校验（不装回）单独测为 **624ms**。
容器比宿主 30MB 那组快，原因是备份体量与重放事件数小两个数量级，不是容器更快。

**E.5 备份文件本身被截断时也判失败**

与「主库被截断」不同，这里截断的是**备份产物本身**，manifest 仍保留原始 sha256：

```
$ docker exec xkx-backup sh -c '
D=/app/backups/20261105T175328Z
sha256sum $D/events.sqlite; stat -c "size=%s" $D/events.sqlite
rm -rf /tmp/bad && mkdir -p /tmp/bad && cp $D/events.sqlite $D/service.sqlite $D/manifest.json /tmp/bad/
head -c 4096 $D/events.sqlite > /tmp/bad/events.sqlite
sha256sum /tmp/bad/events.sqlite; stat -c "size=%s" /tmp/bad/events.sqlite
python /app/tools/restore_verify.py --root /app --backup-dir /tmp/bad; echo "EXIT=$?"'
c8d4bf78aa135f6a673d066df07d16048e9327552c576b6cdaaadca08b6b5378  .../events.sqlite
size=409600
773462ec10030a41d6a3fd40348a1edb254965ac962a3e293efdd499c044b3c5  /tmp/bad/events.sqlite
size=4096
{"backup_dir": "/tmp/bad", "checks": [
  {"name": "events_file_present", "ok": true},
  {"name": "events.sqlite_sha256", "ok": false,
   "expected": "c8d4bf78...b5378", "actual": "773462ec...44b3c5"},
  {"name": "events.sqlite_size", "ok": false, "expected": 409600, "actual": 4096},
  {"name": "events.sqlite_integrity", "ok": false,
   "error": "DatabaseError: database disk image is malformed"},
  {"name": "service.sqlite_sha256", "ok": true},
  {"name": "service.sqlite_size", "ok": true},
  {"name": "service.sqlite_integrity", "ok": true},
  {"name": "events_queryable", "ok": false,
   "error": "DatabaseError: database disk image is malformed"}],
 "checks_passed": 4, "checks_total": 8, "ok": false,
 "failure_reason": "events.sqlite_sha256; events.sqlite_size; events.sqlite_integrity; events_queryable"}
EXIT=2
```

**结论：备份文件被截断 → 校验失败、退出码 2，且三项独立信号（sha256、size、integrity）
同时报警，不会误装回。**

### F. 备份失败不再静默（2026-11-06，colima 29.5.2 / aarch64）

`F.1 默认部署真的备份`：`docker-compose up -d`（**不带任何 profile**）后，
`xkx-world` 与 `xkx-backup` 均 `Up (healthy)`，备份卷出现新产物：

```text
$ docker ps --format '{{.Names}}\t{{.Status}}'
xkx-backup   Up 23 seconds (healthy)
xkx-world    Up 23 seconds (healthy)

$ docker exec xkx-backup ls -la /app/backups | tail -3
drwx------ 2 app app 4096 Nov  5 18:26 20261105T182613Z
drwx------ 2 app app 4096 Nov  5 18:26 20261105T182656Z
-rw-r--r-- 1 app app  957 Nov  5 18:26 last_backup_status.json

$ docker logs xkx-backup | tail -3
[backup-loop] root=/app run=service dir=/app/backups interval=21600s retries=2 python=/usr/local/bin/python3
[backup-loop] 备份成功（第 1 次，1930 字节结果）
[backup-loop] 本轮完成：{"backup_dir": "/app/backups/20261105T182656Z", …
              "duration_ms": 816, "events": {"seq": {"count": 42, "contiguous": true, …}},
              "offsite": {"ok": true, "offsite": "disabled", "mode": "disabled"}}
```

`F.2 备份目录只读 → 容器 Exited(1)`（`-v xkx-w5-backups:/app/backups:ro`）：

```text
$ docker ps -a --filter name=xkx-backup-ro --format '{{.Names}}\t{{.Status}}'
xkx-backup-ro   Exited (1) 8 seconds ago
$ docker inspect xkx-backup-ro --format 'status={{.State.Status}} exit_code={{.State.ExitCode}}'
status=exited exit_code=1
$ docker logs xkx-backup-ro | tail -4
OSError: [Errno 30] Read-only file system: '/app/backups/status.7.tmp'
[backup-loop] 写状态文件失败: /app/backups/last_backup_status.json
[backup-loop] 备份连续失败 1 次，退出码 2，容器将进入 Exited(非零) 状态，等待人工介入
```

注意：目录只读时状态文件也写不进去，但**写失败本身会打日志且退出码仍非零**，
不会出现"容器还 Up、状态文件却写成功"的假象。

`F.3 源库损坏 → 重试后 Exited(1) + 状态文件 ok=false`（`events.sqlite` 截断到 4096 字节）：

```text
$ docker run … -e BK_RUN=bad -e BK_RETRIES=2 -e BK_RETRY_DELAY=2 … sh /app/scripts/backup_loop.sh
[backup-loop] 备份失败 exit=1 第 1/2 次：[backup] events.sqlite 备份失败: DatabaseError: database disk image is malformed
[backup-loop] 备份失败 exit=1 第 2/2 次：…
[backup-loop] 备份失败 exit=1 第 3/2 次：…
[backup-loop] 备份连续失败 3 次，退出码 1，容器将进入 Exited(非零) 状态，等待人工介入
EXIT=1

$ cat last_backup_status.json
{"duration_ms": 4379, "error": "[backup] events.sqlite 备份失败: DatabaseError: database disk image is malformed",
 "exit_code": 1, "generated_at": "2026-11-05T18:27:52Z", "host": "7b72ff1267fe",
 "interval_seconds": 21600, "ok": false, "stage": "backup"}
```

`F.4 healthcheck 读到失败状态即判 unhealthy`：

```text
$ docker run --rm -v <失败状态目录>:/app/backups --entrypoint python xkx/world-sim:w5 /app/deploy/backup_healthcheck.py
unhealthy: 最近一轮失败 stage=backup exit=1 error=… database disk image is malformed
HEALTHCHECK_EXIT=1

$ docker exec xkx-backup python /app/deploy/backup_healthcheck.py
healthy: last_ok age=62s backup_dir=/app/backups/20261105T182656Z
HEALTHCHECK_EXIT=0
```

---

## 8. RPO / RTO（实测口径）

### RPO

- 调度常量 `BK_INTERVAL=21600s`；按 6 小时周期，**最坏丢失窗口 = 6 小时 + 单次备份耗时**。
  - 宿主机（29MB / 29.4k 事件）：备份耗时 2.46–6.29s → **RPO ≈ 6h00m07s**。
  - 容器（400KB / 445 事件）：备份耗时 1.818s → **RPO ≈ 6h00m02s**（这是本次容器演练的
    实际口径，生产按宿主/线上体量取更大值）。
  - 事件数增长会拉长 `state_hash` 重放，因此 RPO 会随 `events.sqlite` 体量增加；
    到 200k 事件前请重新测一次并把该值写回本节。
- 演练实测（B）：最后备份 `17:27:30Z`（seq 29438）→ 故障 `17:28:22Z`（52 秒后），
  期间写入 20 条事件（10 个动作）；恢复后这 20 条丢失。这是“从备份到故障”的
  **实际丢失窗口**，不是生产上限。

### RTO

宿主机口径，用备份 `backups/20261105T173139Z`（30,195,712 字节 / seq 30205）：

```json
{"restore_verify_s": 5.002, "api_start_to_ready_s": 1.505, "rto_total_s": 6.51}
```

容器口径（§7-E，400KB / 445 事件，停服→服务可用全程）：

```json
{"stop_ms": 1135, "restore_verify_apply_ms": 605, "start_to_available_ms": 1435,
 "rto_total_ms": 3174}
```

分解：restore_verify（复制 + sha256 + integrity + seq + 两次重放）在宿主 30MB 时为 5.0s、
容器 400KB 时为 0.6s（纯校验 624ms）；API 从进程启动到可用 1.4–1.5s；`docker stop`
约 1.1s。生产 RTO 还需加上人工发现/决策时间，本值只覆盖“拿到备份后到服务可用”。

---

## 9. 已知限制

1. **备份与主库同盘（已由 offsite 副本补偿）**：容器里备份已拆到独立命名卷
   `xkx-w5-backups`（逻辑隔离，避免备份撑爆 `/app/runs`），但**两个卷仍在同一台宿主机、
   同一块物理盘**；宿主机 `backups/` 同理。处置：
   - **已实现异机/异地副本**（§5.3 `tools/backup_offsite.sh`，每轮备份后自动推送，
     远端取 sha256 逐项比对）。生产必须配置 `XKX_OFFSITE_REQUIRED=1`，缺配置即失败。
   - **仍需人工确认的部分**：副本目标本身的可用性（NAS/远端主机/对象存储）不在本仓库
     控制范围内；请把 offsite 目录纳入监控（磁盘水位、快照保留）。
   - **验收**：异机副本与本地 `manifest.json` 的 `sha256` 一致（工具每次自动比对）；
     每季度至少做一次"只从异地副本恢复"的演练。
2. **恢复时用哪一份产物**：
   - 首选**本地最新一份**（`backups/<name>/`，写入路径已验证）；本地不可用（主机/磁盘损坏）
     才用异地副本。
   - 用异地副本恢复前，**先把它拷回恢复机**，并核对 `manifest.json` 的 `sha256`：
     ```bash
     cp -a /mnt/offsite/<name> /tmp/restore-candidate
     XKX_OFFSITE_MODE=local XKX_OFFSITE_TARGET=/mnt/offsite \
       sh tools/backup_offsite.sh --backup-dir <本地备份根> --backup <name> --verify-only
     .venv/bin/python tools/restore_verify.py --root . --backup-dir /tmp/restore-candidate
     ```
   - 异地副本通常**没有更新的那份**（推送发生在备份成功之后），RPO 以本地为准；
     若本地与异地命名不同，按 `manifest.created_at` 选最接近故障点且校验通过的一份。
3. **无加密**：备份包含 `service.sqlite`（密码哈希、令牌）。目录 `700`、文件 `600` 仅防同机其他用户；
   拷离机器前应加密（如 `age`/`gpg`）；异地目标的访问控制由存储侧负责。
4. **state_hash 重放成本**：备份时会完整重放事件日志算 hash，耗时随事件数线性增长；
   当前 29.4k 事件占 2.5–6.3s 总耗时（SQLite copy 仅约 0.1–0.3s）。
5. **内容包升级后的旧备份**：`content_hash` 不匹配时服务会拒绝启动（退出码 3）。
   恢复旧备份必须同时恢复对应的 `content/`，或用新内容开新 run；工具会以退出码 2 拒绝装回。
6. **service.sqlite 时点回滚**：恢复后备份之后注册的账号、签发的令牌失效，玩家需重新登录。
7. **恢复必须停服**：工具只做文件装回，不检测端口占用；并发写会造成新旧库混写。
8. **定时任务不做自动恢复演练**：轮转只保证备份存在；建议每周对最新一份跑一次
   `restore_verify.py`（不带 `--apply-dir`），失败告警。
9. **容器演练的边界**：容器演练里 `events.sqlite` 只有 445 条事件（400KB），所有容器内
   数字（RTO 3.174s、备份 1.818s）都对应小体量；**线上体量请以宿主机 29MB 那组数字
   （RTO 6.510s、备份 2.46–6.29s）为准**，事件数上去后两者都会变长。
10. **备份目录不能是符号链接**：`--backup-dir` 指向符号链接时工具直接拒绝；
    轮转删除也会跳过任何含符号链接的备份目录。
11. **BuildKit 缺失时的构建差异**：本机无 buildx，用 `DOCKER_BUILDKIT=0 docker build`
    构建镜像；`deploy/Dockerfile.dockerignore` 只在 BuildKit 下生效，legacy builder
    会把 `reports/`、`dist/` 等也送进构建上下文（镜像内容不受影响，只是上下文偏大）。
12. **offsite 目标不在本仓库控制内**：工具只保证"推送到目标并在远端校验通过"；
    目标端保留策略、访问控制、加密、以及"目标本身是否与主库同机房"需部署方确认。
    对象存储的 sha256 依赖 rclone 对远端哈希的支持，个别后端取不到哈希会返回非零
    （这是预期：宁可失败也不假装成功）。

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
| 备份容器日志出现 `UNIQUE constraint failed: events.seq` / 同时有两个 `/api/service/info` | `backup` 服务的 `command` 没覆盖镜像 ENTRYPOINT，容器里又起了一套世界服务 | 确认 `deploy/docker-compose.yml` 的 `backup` 有 `entrypoint: ["/bin/sh","-c"]`；停掉备份容器，按 §6 恢复最近备份 |
| 备份容器 `Exited (1)`，日志 `Read-only file system: '/app/backups/...tmp'` | 备份卷/目录被挂成只读（或磁盘只读、写满） | 恢复写权限；确认 `docker exec xkx-backup touch /app/backups/probe` 可写 |
| 备份容器 `unhealthy`，`unhealthy: 最近一轮失败 stage=…` | 当轮备份或 offsite 失败；详细原因在状态文件 | `docker exec xkx-backup cat /app/backups/last_backup_status.json` 看 `stage`/`exit_code`/`error` |
| offsite `exit=1` + `stage=push` | 目标目录不可写 / rsync 目标不可达 / rclone remote 未配置 | 修目标可达性与权限；`local` 模式注意容器 uid 10001 对 bind mount 需可写 |
| offsite `exit=1` + `stage=verify` + `sha256 mismatch` | 远端副本被篡改、截断，或推送中途失败留下残档 | 重新推送覆盖；若反复出现，检查链路（磁盘坏块 / 同步工具过滤规则） |
| offsite `exit=3` + `offsite_required_but_disabled` | 配了 `XKX_OFFSITE_REQUIRED=1` 但没给 `XKX_OFFSITE_MODE/TARGET` | 补配置，或明确降级为 `REQUIRED=0`（不推荐用于生产） |
| 备份容器 `unhealthy` 且 `缺少状态文件` | 首轮还没跑完（`start_period` 90s 内）或目录不可写 | 等 `start_period` 过；再查目录权限与日志 |
