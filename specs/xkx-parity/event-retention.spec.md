# 事件表增长治理（events 保留期 / 归档 / 快照）— 规格

状态：待评审 | 作者：TraeCode | 日期：2026-11-08
分支：`feat/xkx-event-retention`（基点 `origin/xkx-parity@70ab046`）
关联：`src/wsim/world/store.py`、`src/wsim/world/writer.py`、`src/wsim/api/persistence.py`

## 1. 问题与实测

`events` 表是唯一事实源，当前无保留期、无归档、无裁剪，append-only 线性增长。

- soak 实测：50 名玩家连续 6 小时，`events.sqlite` 线性增长约 **570MB**（≈ **15MB/10min**）。
- 按 500 席外推：约 **23GB/天**。这与“线上长期运行”直接冲突，属于上线级隐患。
- 结构原因：每条事件都完整保存 `args` / `effects` JSON；玩家动作、系统日结、拒绝事件全部永久在线。
- 当前 `EventStore.count()` 用 `COUNT(*)`，等价于“最后 seq”的前提是表从未删除；一旦裁剪就会
  破坏 seq 单调性，必须先修。

### 换算口径（用于验证，不作为硬编码）

- `1 tick = 10 游戏分钟`，`1 游戏日 = 144 tick`（`src/wsim/core/clock.py`）。
- soak 环境 `tick_step=6s`：6 小时真实时间 = 3600 tick = 25 游戏日。
- 570MB / 3600 tick ≈ **158KB/tick** ≈ **22.8MB/游戏日**（50 玩家负载）。
- 500 席 ≈ 10 倍写入量 ≈ **228MB/游戏日**。保留 14 游戏日 ≈ **3.2GB 稳态**，
  相比 23GB/天不再是无界增长。

## 2. 目标与非目标

### 目标

1. 给 `events` 定义**保留期**（默认按游戏日）与**前缀裁剪**（只裁 seq 前缀，不产生洞）。
2. 裁剪前把被裁事件写成**可独立回放的归档段**（压缩、带校验和、可断点恢复）。
3. 在裁剪水位写**状态快照**，使 `snapshot + 尾段事件` 能重建到与全量事件一致的状态。
4. 归档文件本身也能独立重建：`全部归档段 + 尾段事件` 等价于全量事件。
5. 一切能力**默认关闭**；关闭态的事件流、状态哈希、事件哈希、导出文件与今天逐字节一致。
6. 失败/中断可恢复：任何一步崩溃都不会出现“事件已删但归档/快照没落盘”的不可逆状态。

### 非目标

- 不改 `src/wsim/sim/**` 的模拟语义；不改 Effect / Intent 契约；不新增/删除任何动作。
- 不改 `events` 表 DDL，不做列级迁移（见 §4）。
- 不做在线自动调度（本次提供维护 CLI；何时执行由部署侧 cron/低峰窗口决定）。
- 不承诺裁剪后仍能从在线表做“全史审计”；全史审计改读归档（见 §9）。

## 3. 硬约束与验收红线

- 无玩家世界（`days=2, seed=42, npc_limit=0`）三项必须一字不变：
  - `events = 735`
  - `state = 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9`
  - `event = 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`
- 改动后必须实跑 **两次** `tests/test_xkx_determinism_legacy.py` 并记录两次结果。
- 关闭态：不写 `archive/`、不写 `snapshots/`、不写维护 journal；`events.sqlite` 逻辑内容
  （`read_all()` 规范序列化）与导出 `events.jsonl` 逐字节一致。
- 归档/快照/裁剪代码仅允许新增 `src/wsim/world/retention.py`，并对
  `store.py` / `api/persistence.py` / `api/session.py` 做必要的最小增量；禁止动
  `src/wsim/sim/**`、`content/xkx/**`、`combat_rules.py`。

## 4. Schema 影响分析

### 4.1 DDL：无变更

`events` 表与两个索引保持原样；不新增列、不重建表、不改 `legacy` 兼容逻辑。
因此不存在“新库/旧库 schema 分叉”，旧 run 目录可直接被新代码打开。

### 4.2 语义修正（必须）

`EventStore.count()` 从 `COUNT(*)` 改为 `MAX(seq)`：

- 未裁剪时二者恒等（seq 从 1 连续递增），关闭态行为不变；
- 裁剪后 `COUNT(*)` 会变小，但 `MAX(seq)` 仍是全史最后 seq，写路径 `count()+1` 继续单调；
- 空表返回 0，保持创世语义；
- `append()` 已缓存最新 seq，运行期不增加查询成本。

### 4.3 `meta` 键（仅在开启/执行维护时写入）

| key | 含义 |
| --- | --- |
| `event_retention_version` | 保留机制版本，当前 `1` |
| `event_pruned_through_seq` | 已裁掉的 seq 前缀水位 S（不含 S+1） |
| `event_archive_last_day` | 最近一次归档覆盖到的游戏日（审计/运维用） |

关闭态不写这些键；旧库没有这些键时按“未裁剪”处理。

### 4.4 不改动的契约

- `Event.to_canonical_dict()` / `state.hash()` / `event_hash()` 公式不变。
- `Writer` 的 seq 分配、幂等缓存、`player_ticks` 语义不变。
- `apply_effects()` 与所有 Effect 的语义不变。

## 5. 总体架构

三层事实：

1. **在线事件表（热数据）**：保留期内的尾段事件，供实时读写与增量审计。
2. **归档段（冷数据，权威长期事实源）**：被裁剪事件的 gzip JSONL，按 seq 连续区间分段，
   每段带 sha256；`archive/manifest.json` 记录段顺序与裁剪水位。
3. **状态快照（加速层）**：裁剪水位 S 处的 `WorldState` + `player_ticks`，用于进程重启时
   跳过全史回放；快照失效时必须能退回“归档段 + 尾段”重建。

重建优先级（`replay_into`）：

```
存在且校验通过的 snapshot
    -> 从 snapshot 恢复 state/player_ticks，再回放 seq > snapshot.last_seq 的尾段事件

snapshot 缺失/损坏
    -> 若在线表已裁剪且归档前缀未被轮转淘汰（min_available_seq == 1）：
       读取全部归档段（seq 1..S）回放，再回放 seq > S 的尾段
    -> 若归档前缀已被轮转淘汰（min_available_seq > 1）：明确报错 archive_prefix_evicted，
       绝不半重建
    -> 若 store 未裁剪：维持今天的全量 read_all() 回放（逐字节一致）
```

## 6. 归档格式与分段

### 6.1 目录

```
<run_dir>/
  events.sqlite
  archive/
    manifest.json
    events-s000001-s020000.jsonl.gz
    events-s020001-s041120.jsonl.gz
    ...
  snapshots/
    snapshot.json                 # 指向最新有效快照（原子替换）
    state-d000003-s0041120.pkl.gz # 不可变快照文件
  retention_journal.json          # 维护过程日志；正常完成后删除
```

### 6.2 段文件

- 命名：`events-s{start_seq:012d}-s{end_seq:012d}.jsonl.gz`
- 内容：每行一条事件，`canonical.dumps(event.to_dict())`（键排序、紧凑、UTF-8），
  **严格按 seq 升序**，文件末尾一个 `\n`。
- 压缩：`gzip`，`mtime=0`、不写文件名头，保证同输入字节可复现；压缩级别 6。
- 分段粒度：
  - 只按 **seq 连续前缀**切段，绝不跨洞、绝不打乱事件顺序；
  - 优先在游戏日界切段（归档时记录 `day_start` / `day_end`）；
  - 单段事件数上限 `segment_events=20000`，防止单段过大导致回放/校验内存尖刺。
- 段元数据（manifest 内）：`start_seq`, `end_seq`, `events`, `day_start`, `day_end`,
  `sha256`, `bytes`。

### 6.3 manifest

```json
{
  "format": "xkx-event-archive",
  "version": 1,
  "last_pruned_seq": 41120,
  "last_day": 3,
  "min_available_seq": 1,
  "evicted_through_seq": 0,
  "evicted_bytes_total": 0,
  "segments": [
    {"file": "events-s000001-s020000.jsonl.gz", "start_seq": 1, "end_seq": 20000,
     "events": 20000, "day_start": 0, "day_end": 1,
     "sha256": "…", "bytes": 123456}
  ]
}
```

- `manifest.json` 与段文件都用“临时文件 + fsync + `os.replace`”原子落盘。
- 段已存在且 sha256 匹配时跳过写入（幂等重跑）。
- 删除事件前，manifest 必须已经覆盖 `[1..S]` 且逐段校验通过。
- `min_available_seq`：仍保留在盘上的最早 seq；本地轮转淘汰最老段后前移（§13）。
- `evicted_through_seq` / `evicted_bytes_total`：已被轮转淘汰的水位与累计字节（只记水位，
  不保留内容）；用于说明“为什么重建必须依赖快照”。

## 7. 裁剪边界（唯一合法口径）

设当前世界最后事件 tick 为 `T`，`D = T // 144`（当前游戏日）：

```
cutoff_tick = max(0, (D - retention_days + 1) * 144)
first_retained_seq = min(seq | event.tick >= cutoff_tick)
S = first_retained_seq - 1
```

规则：

1. **只裁 seq 前缀** `[1..S]`，`S` 必须是“所有 tick >= cutoff 的事件之前”的最后一个 seq；
   后续事件即使 tick 较旧（例如 stale_tick 拒绝事件）也留在尾段，下一轮再处理。
2. **只裁完整游戏日**：`cutoff_tick` 是 144 的整数倍；当前未结束的一天永远不裁。
3. `retention_days >= 1`；默认 14，即保留最近 14 个游戏日（含当前日）。
4. 若 `S <= 0`、或 `S >= max_seq`、或裁剪后在线表将为空 → **不裁剪**（安全阀）。
5. 裁剪 SQL：`DELETE FROM events WHERE seq <= ?`，单事务；随后
   `PRAGMA wal_checkpoint(TRUNCATE)`。
6. 已裁水位写入 `meta.event_pruned_through_seq=S`；重复执行必须幂等。
7. 裁剪只按 seq，不按 tick/actor/day，避免产生 seq 洞或把新事件误删。

## 8. 快照格式与重建

### 8.1 快照文件

`snapshots/state-d{day:06d}-s{S:012d}.pkl.gz`，内容是 gzip 压缩的 pickle 包装：

```python
{
  "format": "xkx-event-snapshot",
  "version": 1,
  "tick": int,
  "day": int,
  "last_seq": int,           # 即裁剪水位 S
  "state_hash": str,         # state.hash()，恢复后必须复核
  "content_hash": str,
  "seed": int,
  "player_ticks": dict[str, int],
  "state": WorldState,
}
```

- 快照是不可变文件（按 day/seq 命名），`snapshot.json` 只是指针，原子替换。
- 选择 pickle 的理由：`WorldState` 目前没有 `from_dict()`；pickle 是 `clone()` 已经在用的
  精确值语义。**快照是快速路径缓存，不是长期事实源**；归档段才是。
- 换取的安全性：快照只从本 run 目录读取，属于受信本地文件；恢复后必须复核
  `version/content_hash/state_hash/last_seq`，任一不符即判失效并走归档重建。

### 8.2 生成快照（维护时）

1. 用“上一份有效快照 + 中间事件”或“全量事件”回放到 `S`；
2. 复核 `state.hash()`；
3. 写快照 + 指针，fsync；
4. 再次从落盘文件读回并复核 `state_hash`（防止压缩/截断损坏）。

### 8.3 `snapshot + 尾段` 重建

- 恢复快照得到 `state` 与 `player_ticks`；
- 回放 `seq > S` 的在线事件（只应用 `outcome=ok` 的 effects，并刷新 `state.tick` 与
  player tick 下限），与今天 `replay_into` 的逐事件语义一致；
- 结果必须满足 `state.hash() == 全量事件回放后的 state.hash()`（测试断言）。

### 8.4 `归档段 + 尾段` 重建

- 按 manifest 顺序流式读段，逐事件 `apply_effects`；
- 再回放尾段；
- 该路径不依赖 pickle，跨代码版本时仍可用（归档是纯 JSON 事件流）。

## 9. 失败、中断与恢复

`retention_journal.json` 记录阶段与水位：

```json
{"version": 1, "phase": "archive|snapshot|prune|done",
 "cutoff_tick": 432, "target_seq": 41120, "snapshot": "snapshots/state-d000003-s0041120.pkl.gz",
 "segments": ["events-s000001-s020000.jsonl.gz", "..."], "pruned": false}
```

阶段顺序与崩溃语义：

1. **archive**：先写段临时文件→fsync→rename→更新 manifest。崩溃重跑时已存在的段按
   sha256 校验后跳过；临时文件忽略并可清理。
2. **snapshot**：manifest 完整后才允许写快照。崩溃后重跑直接重写快照（幂等）。
3. **prune**：快照落盘并复核通过后才执行 DELETE。SQLite 单事务保证要么全删要么不删；
   崩溃后按 journal 的 `target_seq` 重跑 DELETE（幂等）。
4. **done**：写 `meta.event_pruned_through_seq`、清空 journal。
5. 任一阶段发现归档缺失/校验失败或快照复核失败：**停止且不删除任何事件**，journal 保留，
   CLI 返回非零并输出 JSON 错误。
6. 进程被 kill 在任意点：下次运行先 `recover()`；若在线表已裁但 `meta` 水位缺失，以
   `MIN(seq)-1` = manifest `last_pruned_seq` 交叉校验后补齐。

## 10. VACUUM 策略

- 在线服务运行中：只做 `PRAGMA wal_checkpoint(TRUNCATE)`，不执行 `VACUUM`（VACUUM 需要
  独占锁，会把写路径卡死）。
- 空间真正回收：维护 CLI 显式 `--vacuum`，且要求服务已停止（本 CLI 以独占方式打开库）；
  `VACUUM` 后再次 `wal_checkpoint(TRUNCATE)`。
- 不启用 `auto_vacuum`，避免改库级 PRAGMA 影响现有写路径；不改变 page_size。
- SQLite 删除行后 freelist 会复用页，稳态大小 ≈ 保留窗口大小 + 归档/快照目录体积。

## 11. 配置与开关

| 项 | 默认 | 说明 |
| --- | --- | --- |
| `XKX_EVENT_RETENTION` | `0` | 总开关；0 时维护 CLI 拒绝执行（需 `--enable`） |
| `--retention-days` / `XKX_EVENT_RETENTION_DAYS` | `14` | 保留游戏日数（含当前日） |
| `XKX_EVENT_SEGMENT_EVENTS` | `20000` | 单归档段事件上限 |
| `--vacuum` | 关 | 服务停止后回收磁盘 |
| `--dry-run` | 关 | 只计算边界/预估与轮转计划，不写盘、不删除 |
| `--verify` | 关 | 维护后从快照+尾段重建并比对哈希 |
| `--archive-max-bytes` / `XKX_ARCHIVE_MAX_BYTES` | `50GB` | 归档目录总量上限；`0` = 不设上限 |
| `--archive-min-window-days` / `XKX_ARCHIVE_MIN_WINDOW_DAYS` | `7` | 轮转时至少保留最近 N 个有归档的游戏日 |
| `--min-free-bytes` / `XKX_MIN_FREE_BYTES` | `5GB` | 剩余空间低于该值时拒绝继续归档；`0` = 关闭 |

CLI：

```
PYTHONPATH=src python -m wsim.world.retention \
  --run-dir runs/service --enable --retention-days 14 \
  [--vacuum] [--dry-run] [--verify] \
  [--archive-max-bytes 53687091200] [--archive-min-window-days 7] [--min-free-bytes 5368709120]
```

默认关闭的体现：不执行 CLI/不设开关时，API 与 headless 行为与今天一致，不产生任何
`archive/`、`snapshots/`、`retention_journal.json`。

## 12. 可观测性

维护输出单行 JSON：`ok`, `cutoff_tick`, `target_seq`, `events_before`, `events_after`,
`segments_written`, `archive_bytes`, `snapshot_state_hash`, `db_bytes_before/after`,
`vacuumed`, `elapsed_s`（仅运维展示，不进入世界哈希）。

本地轮转追加字段（§13）：`archive_bytes_before_rotation`, `archive_bytes_after_rotation`,
`archive_cap_bytes`, `archive_over_cap`, `evicted_segments`, `evicted_bytes`,
`removed_orphan_segments`, `rotation_blocked_reason`, `rotation_still_over_cap_reason`,
`rotation_dry_run`, `disk_free_bytes`, `disk_required_bytes`, `disk_ok`。

告警语义：
- `disk_ok=false` 且非 dry-run：维护会以 `disk_low_free` 失败退出（退出码 1），
  不写段、不裁剪；cron 只看退出码即可告警。
- `archive_over_cap=true`：轮转后仍超过上限（通常被最小窗口或快照覆盖挡住），
  用 `rotation_still_over_cap_reason` 区分原因，需要人工扩盘或调整配置。

`meta` 三个键供健康检查/运维查询；`GET /api/health` 现有 `events` 连续性检查基于尾部两跳，
裁剪后仍成立（`MAX(seq)` 与 `EventTail.last_seq` 一致）。

## 13. 归档生命周期（本地轮转）

在线库已被保留期封顶，但归档目录本身仍会无界增长（500 席约 11GB/天）。本节定义
**单机本地轮转**：在离线维护 CLI 内按总量上限淘汰最老的归档段；不引入对象存储、
不上云、不新增后台常驻进程，仍是 cron 挂离线 CLI。

### 13.1 规则

- 总量上限：`--archive-max-bytes`（默认 50GB，`XKX_ARCHIVE_MAX_BYTES` 可配；`0` = 不设上限）。
- 淘汰顺序：只从 **最老段** 开始，删到总量回到上限内即停；绝不删中间段、绝不制造段间洞。
- 前置条件（不满足即拒绝删除，并输出 `rotation_blocked_reason`）：
  1. 段必须已被快照覆盖：`segment.end_seq <= snapshot.last_seq`，且
     `snapshot.last_seq >= manifest.last_pruned_seq`（否则 `snapshot_behind_pruned`）；
  2. 不存在未完成的 `retention_journal.json`（否则 `journal_pending`）；
  3. 段位于最小保留窗口之外：最近 `--archive-min-window-days`（默认 7）个有归档的游戏日
     不淘汰；被窗口挡住且仍超限时输出 `rotation_still_over_cap_reason=min_window`。
- 落盘顺序：先原子更新 manifest（移除被淘汰段、推进 `min_available_seq`、
  `evicted_through_seq`、`evicted_bytes_total`），再删除段文件。中途崩溃只会留下
  “manifest 未引用的孤儿段”，下次轮转自动清理；不会出现 manifest 指向已删文件的
  不可恢复状态。
- 磁盘水位：写归档前检查 `free >= min_free_bytes + 本次归档估算`，不满足则抛
  `disk_low_free` 并保留 journal，不继续写盘。`--min-free-bytes` 默认 5GB（`0` = 关闭）。
  估算优先用既有段的“压缩字节/事件”比例，无历史段时按 512 字节/事件保守估计。
- `--dry-run`：输出 `archive_bytes_before_rotation/after_rotation`、`evicted_segments`、
  `evicted_bytes`、`rotation_blocked_reason` 等字段，但不删任何文件。
- 轮转不清理快照文件：`snapshot.json` 指向的快照是重建基线；历史快照仍按维护次数累积
  （单次几 MB 量级）。快照清理不属于本次范围，作为已知残留风险记录。

### 13.2 能解决什么

- 单机归档目录有硬上限：磁盘占用从“无界线性增长”变成“上限 + 最小保留窗口”；
- 超限时自动回收最老归档，且只回收已被快照覆盖、不在重建关键路径上的段；
- 配置写错（例如上限误设为 1GB）时，最小保留窗口兜底，不会一次清空历史；
- 磁盘将写满前先拒绝归档并明确报错（`disk_low_free`），避免把机器写死；
- 全部动作在离线 CLI 内完成，可挂 cron / systemd timer，无新增常驻进程与网络依赖。

### 13.3 解决不了什么

- **不是异地冷备**：轮转只在本机磁盘内腾挪，宿主/磁盘损坏仍会丢历史；异地容灾需要
  外部备份通道，本方案不提供。
- **不是合规审计留存**：被淘汰段不可恢复（manifest 只留 `evicted_through_seq` 水位，
  不保留内容）。需要长期审计/取证时，必须在淘汰前转储到外部存储（对象存储、磁带、
  归档库），本方案不提供该通道。
- **不能全史随时可查**：淘汰后只能从快照 + 剩余段重建“当前状态”，无法再逐条回放
  被淘汰区间。快照因此从“加速缓存”升级为“重建必需基线”。
- 不做容量预测、分级存储、多副本：只按字节上限 FIFO 淘汰，不区分冷热。

## 14. 风险与取舍（明确写入验收）

- 裁剪后，`store.read_all()` 只覆盖保留窗口；依赖全史扫描的只读接口（`intent` 历史查询、
  `messages` 的 `since_tick`、`visited_locations` 惰性重建）只能看到窗口内事件。
  这是有意的内存/体积取舍：**全史查询必须改读归档段**。
- 快照是代码版本相关的缓存；升级后若 pickle 不可读，自动退回归档+尾段重建（可能更慢，
  但结果一致）。
- 归档段压缩后不可原地更新；任何修正仍以“追加事件 + 后续快照”表达，不改历史段。
- **归档磁盘由本地轮转封顶（§13）**：总量上限 + 最老段优先 + 最小窗口；但轮转只是单机
  腾挪，不是异地冷备或合规审计留存，长期历史必须在淘汰前外部转储。
- **前缀淘汰后重建依赖快照**：`min_available_seq > 1` 时 `snapshot + 尾段` 是唯一重建
  路径；快照缺失/损坏会明确报错 `archive_prefix_evicted`，不会静默半重建。
- 保留期以游戏日计：`tick_step=6s` 时 14 游戏日 ≈ 3.4 小时真实时间，运维需按实际
  `tick_step` 换算保留窗口与维护频率。
- 关闭态红线由 `test_xkx_determinism_legacy` 与实际两次跑测保证；开启态不要求与关闭态
  事件哈希相同（删了前缀，`event_hash(read_all())` 必然变化），只要求重建后的
  **state_hash** 与全量一致。

## 15. 验收清单

1. 关闭态：2 天无玩家世界三项哈希不变；`events.jsonl` 两次运行逐字节一致；无新增目录/文件。
2. 归档：段文件 sha256 与 manifest 一致；重复运行不重复写段；归档段事件数之和 = S。
3. 裁剪：只删 `seq <= S`；当前日与保留窗口事件保留；`MAX(seq)` 不回退；写路径 seq 继续单调。
4. 快照重建：`snapshot + 尾段` 的 `state.hash()` 等于维护前全量回放的 `state.hash()`。
5. 归档重建：`归档段 + 尾段` 得到同一 `state.hash()`；事件序列逐条等价。
6. 中断恢复：在 archive / snapshot / prune 三个阶段分别注入失败，重跑后结果一致，且
   失败点之前/之后都没有“已删未归档”。
7. 边界：`retention_days=1` 时只保留当前日；`S<=0` 或裁剪后为空时不裁剪。
8. 关闭态字节一致、重建一致、中断恢复、边界四类单测全部通过；全量 `pytest` 不回归。
9. 轮转：超上限只淘汰最老段且不产生段间洞；最小窗口内的段不删；快照未覆盖的段不删；
   有未完成 journal 时一段都不删。
10. 水位：`min_free_bytes` 触发时以 `disk_low_free` 失败退出，不写段、不裁剪，journal 保留。
11. dry-run：列出将淘汰段与释放字节数，文件仍在；轮转后重建仍能通过快照+尾段得到同一
    `state.hash()`，去掉快照时明确报 `archive_prefix_evicted`。

## 16. 实测记录（2026-11-08，本机）

### 16.1 合成数据（40 游戏日 / 12 万事件，单段 2 万）

随机 240 字符负载（避免重复文本虚高压缩率）：

- 生成：12 万事件 / 6.2s；`events.sqlite` **31.8MB**（265B/事件）
- 维护（归档+快照+裁剪+verify）：**3.8s**
- 归档：26 段，**15.50MB**（原始库的 **48.8%**，199B/事件）
- 保留：14 游戏日 → 4.2 万事件；裁剪 + `VACUUM` 后主库 **15.66MB**
- `archive_verify=[]`、`verify=True`（快照+尾段 与 归档+尾段 的 state hash 一致）

重复文本负载压测（仅验证流程，不代表真实压缩率）：12 万事件 38.4MB，
归档 1.01MB（2.6%），维护 2.5s，`VACUUM` 后主库 15.66MB。

### 16.2 真实内容包（4 游戏日 / 无玩家）

- headless `days=4, seed=42, npc_limit=0`：1622 事件
- 维护 `retention_days=1`：归档 1208 事件 / 3 段；保留 414 事件
- `verify=True`；裁剪后 `replay_into` 的 `state.hash()` 与 headless summary
  `state_final` 一致；`player_ticks` 一致
- 人为损坏快照文件后重启重建：自动退回归档段，state hash 仍一致

### 16.3 确定性红线

`tests/test_xkx_determinism_legacy.py` 改动后连续跑两次均通过：
`events=735`、`state=009076f0…780ce9`、`event=02d5f812…078ae`。

### 16.4 本地轮转冒烟（6 游戏日 / 无玩家，2026-11-09）

- headless `days=6, seed=42, npc_limit=0`：2209 事件；维护 `retention_days=1` 归档 1967 条，
  5 段 / **193,890 字节**；`verify=true`。
- `--archive-max-bytes 120000 --archive-min-window-days 3 --dry-run`：列出最老 2 段
  （76,981 字节）将被淘汰，文件仍在；实跑结果一致，剩余 3 段 116,909 字节，`verify=true`
  （前缀淘汰后仍由快照 + 尾段重建成功）。
- `--archive-max-bytes 60000` 且窗口 3 天：`archive_over_cap=true`、
  `rotation_blocked_reason=min_window`，一段未删（窗口兜底生效）。

## 17. 交付物

- 本文档 + `event-retention.tasks.md`
- `src/wsim/world/retention.py`（归档/快照/裁剪/恢复/本地轮转/磁盘水位/CLI）
- `src/wsim/world/store.py`（`MAX(seq)`、范围读、前缀删除、checkpoint 的最小增量）
- `src/wsim/api/persistence.py`（快照/归档感知的 `replay_into`）
- `tests/test_event_retention.py`（关闭态一致、重建一致、中断恢复、裁剪边界、本地轮转、
  最小窗口、journal 保护、磁盘水位）
- 实测报告（合成数据量级、耗时、体积、两次确定性跑测结果、轮转冒烟结果）
