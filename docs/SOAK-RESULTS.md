# 长跑稳定性测试结果（SOAK）

> 问题：这个服务能不能连续跑一个月不崩、不变慢、不涨内存？
>
> 结论：**按本次 2 小时实测，不能。** 内存线性增长、延迟随时长单调恶化、吞吐腰斩；
> 正确性（事件/钱包/完整性/停机/重启）全部通过。必须引入归档 + 定期重启 + 有界结构，才有资格谈 30 天。
>
> 数据来源：`reports/soak/soak-20261106-015816/`（`samples.jsonl` 233 行 / `gc_probe.jsonl` 22 行 /
> `summary.json` / `server.log` / `soak_stdout.log`）。复现命令见 §11。
>
> **重要前提（代码版本）**：本次 soak 于 **01:58:16 CST 启动**，服务进程加载的是**当时的代码**
> （`Writer._seen` 无上限、`AuditLog.records` 无上限）。运行期间 02:31/03:48，仓库在其他工作流里
> 提交了 `116231a feat(audit): 审计日志按大小轮转 + 无界内存结构全部加上限`，**该修复没有进入本次进程**。
> 因此本报告的内存数字描述的是**修复前**的行为；对修复后的代码需要复跑才能给出同样的结论（§10）。
> 另外 `MCPGateway.tokens` / `outbox` 和 `state.quests` 的 completed 清理**在 HEAD 仍未修复**（§9）。

---

## 1. 测试配置

| 项 | 值 |
|---|---|
| run id | `soak-20261106-015816` |
| 起止 | 2026-11-06 01:58:16 → 03:59:13 CST（`duration_actual_s=7205.85`） |
| 服务进程 | `python -m wsim.api --auth off --run soak-20261106-015816 --port 60737`，pid 77953 |
| 玩家负载 | 50 个虚拟玩家，2s 思考时间，30s ramp-up，`--timeout 30` |
| 采样 | 每 30s 一行；服务进程内每 ~300s 一次 gc 普查（`XKX_SOAK_PROBE_INTERVAL=300`） |
| ambient | `XKX_AMBIENT=1`（生产默认 1x：1 tick/600s，本次世界 tick 主要由玩家动作推进） |
| 预热 | 600s（前 600s 不计入增长斜率） |
| Python | 3.12.7（`.venv/bin/python`） |
| 机器 | macOS，16GB RAM；**测试前后系统 swap 已用约 22GB**（不是本进程单独造成，但会放大尾部延迟） |
| 启动就绪 | `spawn_to_health_s=0.437` |

合计：**239,305 次请求 / 119,728 个玩家动作周期 / 239,207 条事件**。

---

## 2. 总体判定

| 验收项 | 门槛 | 实测 | 判定 |
|---|---|---|---|
| RSS 预热后增长 | < 5%/小时 | **695.6 MB/h（高水位），+121.3%/h（相对预热均值）** | **FAIL** |
| P95 漂移（read） | 末10min/首10min < 1.3 | **32.622**（238.8ms → 7,790.0ms） | **FAIL** |
| P95 漂移（write） | 末10min/首10min < 1.3 | **24.213**（245.4ms → 5,943.0ms） | **FAIL** |
| 0 error | =0 | **201**（全部为尾部客户端超时） | **FAIL** |
| 0 个 5xx | =0 | 0 | PASS |
| `seq` 连续无洞 | 连续 | 1..239,207，distinct=count | PASS |
| 钱包守恒 delta | =0 | 0 | PASS |
| `integrity_check` | ok | ok | PASS |
| SIGTERM 优雅退出 | rc=0 | rc=0，31.241s | PASS |
| 重启后状态重建 | 一致 | `state_match=true`，15.755s | PASS |
| **整体** | 全 PASS | — | **FAIL** |

工具原样输出（`reports/soak/soak-20261106-015816/soak_stdout.log` 末尾）：

```text
[soak] invariants: ok=True errors=[]
[soak] restart check: re-launching same run dir (ambient off)
[soak] summary -> /Users/lyinet/projects/XKX/world-sim/reports/soak/soak-20261106-015816/summary.json
[soak] check rss_growth_lt_5pct_per_hour: FAIL
[soak] check p95_drift_read_lt_1_3: FAIL
[soak] check p95_drift_write_lt_1_3: FAIL
[soak] check zero_errors: FAIL
[soak] check zero_5xx: PASS
[soak] check seq_contiguous: PASS
[soak] check wallet_delta_zero: PASS
[soak] check integrity_ok: PASS
[soak] check sigterm_graceful: PASS
[soak] check restart_state_match: PASS
[soak] overall: FAIL
```

---

## 3. 内存

### 3.1 曲线与斜率

RSS 用 macOS `ps` 采样（3 次中位数），在系统有 22GB swap 的情况下会因页压缩/换页出现**瞬时假读数**
（例如末次采样 9.61MB、run 中出现 160MB），`summary.json` 里的 `resources.rss.end_mb=9.61` 与
其 `extrapolation` 就是被这个假读数污染的结果，**不可用**。本报告改用两个不受单次采样影响的指标：

- `ru_maxrss` 高水位（进程常驻峰值，单调不减）；
- `gc.get_objects()` 对象数与 `sys.get_allocatedblocks()`（与服务端 RSS 无关的精确计数）。

| 指标 | 首（300s/600s） | 末（6922s） | 斜率 | r² |
|---|---|---|---|---|
| RSS 高水位 `ru_maxrss` | 161.44 MB @600s | **1356.06 MB** | **695.6 MB/h** | 0.999 |
| 采样 RSS 中位数（参考） | 168.95 MB @600s | 1313.28 MB 峰值 | 223.6 MB/h（噪声大） | 0.16 |
| gc 对象数 | 343,857 | 2,747,999 | **1,369,015 个/h** | 0.991 |
| allocated blocks | 1,703,453 | 13,602,832 | 约 6.8M 个/h | — |

- 相对 600s 基线：600s→6922s（1.756h）涨了 1194.6MB，即 **680 MB/h**；相对预热均值 573.42MB 为 **121.3%/h**
  （`summary.memory_profile.maxrss_peak_mb.slope_mb_per_hour=695.644`），门槛是 5%/h。
- 按 695.6 MB/h 线性外推，**约 24 小时就会超过 16GB 物理内存**（§8）。

### 3.2 热点：到底哪个结构在涨

gc 普查（`_probe/gc_probe.jsonl`，22 个点，600s→6922s，跨度 1.839h）：

| 类型 | 600s | 6922s | 增量 | 速率 | 归属（代码） |
|---|---|---|---|---|---|
| `AuditRecord` | 27,696 | **471,160** | +443,464 | **241,086/h** | `AuditLog.records`，旧代码 `list` 无上限（每次写 4 条） |
| `Event` | 13,804 | **235,770** | +221,966 | **120,670/h** | `Writer._seen`，旧代码 `dict` 无上限（每条 intent 1 个） |
| `Intent` | 13,848 | **235,610** | +221,762 | **120,559/h** | `MCPGateway.tokens`（1/写）+ `MCPGateway.outbox`（1/写） |
| `_AdminToken` | 6,924 | **117,790** | +110,866 | **60,271/h** | `MCPGateway.tokens` 字典（1/写，永不删除） |
| `dict` | 80,950 | **1,264,716** | +1,183,766 | **643,545/h** | `AuditRecord.result`、`Event.args/effects` 的载荷 |
| `list` | 18,306 | **329,463** | +311,157 | **169,158/h** | 审计 result / outbox / clone 临时容器 |
| `Quest` | 209 | **25,342** | +25,133 | 13,663/h | 绝大部分是 `state.clone()` 的临时深拷贝；真实 `state.quests` 257→874 |
| `Relation` | 669 | **26,673** | +26,004 | 14,137/h | 同上临时深拷贝；真实 `state.relations` 591→624 |

**HTTP 路径实证**（本次临时诊断：起真实服务，走 `POST /api/player/action` 写 400 次，再在服务进程内
`gc.get_referrers` 聚合持有容器；脚本未入库）：

```text
counts: {"Event": 808, "Intent": 804, "AuditRecord": 1608, "_AdminToken": 402, ...}
Event       owners -> OrderedDict (Writer._seen)      # 304/308 由 _seen 持有（上限内）
Intent      owners -> list len 402 (gateway.outbox) + dict len 402 (gateway.tokens)
AuditRecord owners -> deque (AuditLog.records)        # 4 条/写 = 402×4
_AdminToken owners -> dict len 402 (gateway.tokens)
```

> 说明：这次诊断跑在 **HEAD（含 116231a）** 上，所以 `AuditLog.records` 已是 `deque(maxlen=4096)`、
> `_seen` 已是 `OrderedDict(20000)`；但 `tokens`(402) 与 `outbox`(402) 仍然随写次数线性增长——
> **这两个无界结构在本轮修复后依然存在**。

`WorldState` 内部真正增长的只有两处（其余 accounts/businesses/entities/players/memories/
view_cache 等 17 个结构全程 0 增长）：

| 结构 | 600s | 6922s | 增量 | 速率 |
|---|---|---|---|---|
| `state.quests` | 257 | 874 | +617 | **351.3/h**（completed 永不清理） |
| `state.relations` | 591 | 624 | +33 | 18.8/h |

### 3.3 CPU

`total_seconds=5882.94`（7205.85s 内），**mean 81.67%、peak 97.23%**（单核百分比），
即服务长期跑在约 0.82 核。按窗口折算的**每请求 CPU**：12.6ms（0–10min）→ 19.5 → 23.7 → 26.7 →
36.7 → **44.2ms（110–120min）**，恶化 3.5 倍。

---

## 4. 延迟漂移

### 4.1 首 10 分钟 vs 末 10 分钟（`summary.latency.window_10m` 原样）

| 端点类 | 窗口 | n | P50 | P95 | P99 | max |
|---|---|---|---|---|---|---|
| read（`/api/state`） | 首 10min | 13,954 | 16.7ms | **238.8ms** | 782.4ms | 1,820.8ms |
| read | 末 10min | 4,658 | 2,469.6ms | **7,790.0ms** | 10,625.6ms | 13,979.4ms |
| write（`POST /api/player/*`） | 首 10min | 14,004 | 33.1ms | **245.4ms** | 707.3ms | 1,825.5ms |
| write | 末 10min | 4,467 | 751.5ms | **5,943.0ms** | 10,223.6ms | 14,340.8ms |

`p95_ratio`：read **32.622**，write **24.213**（门槛 1.3）。

### 4.2 全程按 20 分钟窗口

| 窗口 | 吞吐 rps | 请求数 | error | RSS 中位数 | CPU 均 | read P50 | read P95 | read P99 | write P95 | 事件累计 |
|---|---|---|---|---|---|---|---|---|---|---|
| 0–20min | 43.5 | 51,789 | 0 | 169 MB | 68.2% | 86ms | 493ms | 855ms | 495ms | 51,889 |
| 20–40min | 40.7 | 48,654 | 0 | 373 MB | 86.9% | 160ms | 679ms | 1,052ms | 710ms | 100,538 |
| 40–60min | 37.1 | 44,349 | 0 | 604 MB | 90.7% | 283ms | 1,026ms | 1,487ms | 902ms | 144,879 |
| 60–80min | 34.2 | 41,234 | 0 | 820 MB | 89.2% | 430ms | 1,399ms | 2,030ms | 942ms | 186,111 |
| 80–100min | 25.6 | 30,199 | 3 | 878 MB | 81.3% | 1,338ms | 3,598ms | 4,330ms | 2,289ms | 216,311 |
| 100–120min | 18.8 | 22,777 | 180 | 555 MB* | 74.2% | 2,305ms | 5,749ms | 6,555ms | 4,564ms | 238,906 |

\* 末窗口 RSS 中位数被页压缩拉低（实际高水位 1356MB，见 §3.1）。

- 吞吐：**45.9 rps（首10min）→ 15.2 rps（末10min），-66.9%**；末窗口统计期内 18.8 rps。
- `pearson(events_total, read_p95) = 0.741`；read P95 线性斜率约 **+3.46 s/h**。
- 前 80 分钟 0 error；最后 20 分钟出现 183 个 error（见 §6）。

### 4.3 变慢的疑似原因（静态定位，未改代码）

按证据强度排序，均为**疑似**，本轮不做因果定论：

1. **每次写都全量深拷贝状态**：`src/wsim/world/writer.py:125` `trial = self.state.clone()`；
   clone 覆盖 quests/relations/memories/talk_ledger 等（`src/wsim/world/state.py` 的 clone 实现），
   而 `state.quests` 正以 351/h 增长，于是每写一次的成本随时长线性上升。写入前的 precondition
   路径还会再 clone（`src/wsim/actions/player.py:548`、`src/wsim/world/effects.py:946`）。
   对应实测：每请求 CPU 12.6→44.2ms。
2. **读路径随世界规模线性变慢**：`src/wsim/api/session.py:661`、`:1007`、`:1018`、`:1362` 每次都
   `sorted(self.state.quests.values(), key=q.id)`，`:1294` 遍历全部 relations 生成视图；
   `_view_cache` 每个 tick 清空（`session.py:529`），
   即每个动作都要重建视图。对应实测：read P50 86ms→2,305ms。
3. **对象数 8 倍增长 → GC 成本**：`gc_objects` 34.4 万→274.8 万（+1.37M/h），生成 2 代回收的扫描量
   随之线性上升（本工具只计数，没有测单次 GC 暂停时长）。
4. **内存/换页放大**：进程高水位 1.36GB，机器 swap 已用 22GB；尾部 20 分钟 CPU 从 ~90% 降到 74%
   而 P95 反而从 1.4s 跳到 5.7s，形态更像等待换页/IO，而非纯 CPU 饱和。此项只能在干净机器上复测排除。

---

## 5. 存储增长

| 项 | 起始 | 结束 | 斜率（全程，含预热） | r² |
|---|---|---|---|---|
| `events.sqlite`（含 WAL） | 4.99 MB / 957 条 | **228.03 MB / 239,207 条** | **106.1 MB/h**，119,056 条/h | 0.983 / 0.988 |
| `events.sqlite`（主文件） | — | 224 MB（WAL 4.18 MB） | 约 112 MB/h | — |
| `mcp_audit.jsonl` | 0 | **176,020,997 B（167.9 MiB）** | **83.9 MB/h** | 线性（无轮转） |
| 合计磁盘 | — | **约 400 MB / 2h** | 约 200 MB/h | — |

- 换算：**1,100 事件/MB**（`summary.db.events_per_mb`）。
- 注意：全程斜率被吞吐下降**低估**。按 30–60 分钟稳态（136,536 事件/h、**122.9 MB/h**）重建更接近
  恒定负载的真实速率；本节外推采用 30–60 分钟稳态值，并在表里同时给全程值。

---

## 6. 正确性

`summary.totals` 原样：

```json
{"requests": 239305, "ok": 213097, "errors": 201, "http_5xx": 0, "http_401": 0,
 "capacity_rejections": 0, "business_rejections": 26007, "error_rate_pct": 0.084,
 "rejections_by_code": {"quest_expired": 2674, "quest_not_accepted_by_actor": 211, "quest_not_open": 23122}}
```

- **201 个 error 全部是客户端 30s 超时**，样本清一色
  `GET /api/state: URLError: <urlopen error [Errno 60] Operation timed out>`；
  其中 **194 个发生在最后 10 分钟**（`tail_errors=194 / tail_requests=9,125`）。服务端没有 5xx、
  没有崩溃、没有重启——是“已经慢到超过客户端超时”，归入**变慢证据**，不是可用性事故。
- 业务拒绝 26,007 全部是 HTTP 200 且 `ok=false`（体力/任务状态类），按工具口径不计 error：
  `quest_not_open 23,122`、`quest_expired 2,674`、`quest_not_accepted_by_actor 211`。
- 玩家侧：`cycles=119,728`，`player_errors=0`，`cycles_per_second=16.615`。

不变量（`summary.invariants` 原样）：

```json
{"verify": {"db_exists": true, "ok": true, "errors": [], "integrity_check": "ok",
 "event_count": 239207,
 "seq": {"first": 1, "last": 239207, "count": 239207, "distinct": 239207, "contiguous": true},
 "wallet": {"accounts": 92, "sum_balance": 62450, "minted_total": 62450, "burned_total": 0,
            "expected_sum": 62450, "delta": 0, "transfers": 1266},
 "negative_rows": [], "half_write_rows": [], "genesis_events": 2, "genesis_ok_events": 1,
 "outcomes": {"ok": 213200, "rejected": 26007}},
 "seq_contiguous": true, "wallet_delta": 0, "integrity_check": "ok"}
```

---

## 7. 停机与重启恢复

```json
"shutdown": {"signal": "SIGTERM", "exit_code": 0, "shutdown_s": 31.241, "note": "SIGTERM 正常退出"}
"restart": {"started": true, "recovery_s": 15.755, "pre_events": 239207, "post_events": 239207,
            "pre_seq_last": 239207, "post_seq_last": 239207, "seq_ok": true,
            "pre_tick": 717462, "post_tick": 717462, "state_match": true, "state_diff": null,
            "restart_exit": {"signal": "SIGTERM", "exit_code": 0, "shutdown_s": 0.613}}
```

- SIGTERM 后 **31.241s** 才退出（优雅、rc=0，但比预期慢；与尾部 10s 级延迟一致）。
- 用同一 run 目录重启：**15.755s 从 239,207 条事件重建完整状态**（约 15.2k 事件/s），
  `tick`、`seq`、`state_match` 全部一致，重启后无新增事件。**恢复能力本身没有问题**，
  但恢复耗时随事件量线性增长（30 天量级将不可接受，见 §8）。

---

## 8. 外推表（7 / 30 / 90 天）

外推基准（恒定负载、无归档、无重启、旧代码）：

- 内存：1356.06MB 起点 + **695.6 MB/h**（`ru_maxrss` 实测斜率）
- 事件：239,207 起点 + **136,536/h**（30–60 分钟稳态；全程实测为 119,056/h）
- 事件库：217.5MB 起点 + **122.9 MB/h**（稳态；全程实测 106.1 MB/h）
- 审计文件：**83.9 MB/h**（旧代码无轮转；HEAD 已改为 100MB×5 轮转）

| 指标 | 2h 实测 | 7 天 | 30 天 | 90 天 |
|---|---|---|---|---|
| 事件条数 | 239,207 | **23.18 M** | **98.55 M** | **295.5 M** |
| `events.sqlite` | 217.5 MB | **20.4 GB** | **86.6 GB** | **259.5 GB** |
| `mcp_audit.jsonl` | 168 MB | **13.8 GB** | **59.0 GB** | **177.0 GB** |
| 进程内存（高水位） | 1356 MB | **115.4 GB** | **490.4 GB** | **1468.6 GB** |
| 重启恢复耗时（按 15.2k 事件/s） | 15.8 s | 25 min | 108 min | 5.4 h |

> 这些是**数学外推**，不含 OOM/降速截断。实际会在内存到达物理上限前被 OOM kill 或先因延迟不可用；
> 事件数与库体积也会因吞吐下降而低于表中值。表中数字的用途是判断“量级是否可行”，不是预测精确值。

**结论：30 天不可行，且不是“跑久一点看看”的问题**——内存与库体积都是线性，7 天内存就要 118GB。

---

## 9. 未修复问题清单（按纪律未改代码，交你决定由谁修）

以下 1–3 在 HEAD（含 116231a）**仍然存在**；4–5 是变慢的静态热点；6 已在别处修复但本次未覆盖。

1. **`MCPGateway.tokens` 无界** — `src/wsim/mcp/gateway.py:71`（`self.tokens: dict`）、
   `:169`（每次 `admin.request` 写 1 条 `_AdminToken`，`admin.apply` 后不删除）。
   实测：`_AdminToken` +110,866（60,271/h），HTTP 400 写后 dict 长度 = 402。
   建议：apply/超时后删除，或以 token 序号封顶。
2. **`MCPGateway.outbox` 无界** — `src/wsim/mcp/gateway.py`（`world.propose_intent` 里 `self.outbox.append`，
   API 写路径没有消费者 drain）。实测：400 写 → outbox len 402；`Intent` 总量 ≈ tokens + outbox。
   建议：消费后出队，或按容量上限保留最近 N 条。
3. **completed quest 永不清理** — `src/wsim/actions/quests.py:399-408`，`quest_prune` 只清
   `expired/failed`；`state.quests` 实测 257→874（**351.3/h**）。它同时推高 `state.clone()` 成本。
   建议：completed 超过 N 天（如 7 天）也 prune。
4. **每次写全量 `state.clone()`（深拷贝）** — `src/wsim/world/writer.py:125`；precondition 另有
   clone（`src/wsim/actions/player.py:548`、`src/wsim/world/effects.py:946`）。实测每请求 CPU 12.6→44.2ms。
   建议：写路径改为 copy-on-write，或只 clone 被影响的子结构。
5. **读路径随时长线性变慢** — `src/wsim/api/session.py:661/1007/1018/1362`（每次读都
   `sorted(self.state.quests.values())`）、`:1294`（遍历全部 relations）、`:529`（`_view_cache` 每 tick 清空）。
   实测 read P50 86ms→2,305ms。
6. **已在别处修复（116231a，本次进程未生效）**：`AuditLog.records` 改 `deque(maxlen=4096)` +
   审计文件 100MB×5 轮转；`Writer._seen` 改 `OrderedDict` 上限 20,000；`session.requests/intents`
   上限 4,096。**需要复跑 soak 才能确认这些修复的实际效果**（该 commit 自带一个 60 分钟 soak 结论，
   但配置与本次不同，不能直接替代表中数字）。

复现（任选其一）：

```bash
cd world-sim
# 2 小时正式 soak（本次同款）
XKX_SOAK_PROBE_INTERVAL=300 .venv/bin/python tools/soak_test.py \
  --duration 7200 --players 50 --think-time 2 --ramp-up 30 \
  --sample-interval 30 --warmup 600 --timeout 30

# 30 分钟快速复现（足以看到斜率与 P95 漂移方向）
.venv/bin/python tools/soak_test.py --duration 1800 --players 50 \
  --think-time 2 --ramp-up 30 --warmup 300

# 只看内存热点（进程内 tracemalloc，20 分钟）
.venv/bin/python tools/soak_test.py --mode heap --heap-minutes 20 --players 20
```

---

## 10. 归档 / 重启策略（明确结论）

**结论：必须有归档 + 定期重启，而且重启解决不了库增长，必须同时做归档/切库。**
仅靠重启：内存回落，但 `events.sqlite` 与事件表仍单调增长，读路径与恢复时间继续变慢。

按实测给出的触发阈值（保守起点，可在修复后复测再放宽）：

| 策略 | 触发阈值 | 依据 |
|---|---|---|
| **事件库切库/归档** | 单库 > **5 GB** 或 > **5.5 M 事件**（≈ 40h @136.5k/h，≈ 5,000 MB ÷ 1,100 事件/MB） | DB 122.9 MB/h、1,100 事件/MB；30 天 88.7GB 不可运维 |
| 历史库保留 | 归档为只读库，保留 **90 天**，归档后跑一次 `integrity_check` | 与 integrity 校验口径一致 |
| **进程重启** | `ru_maxrss` > **800 MB**，或 read P95 连续 3 个 30s 采样 > **500 ms**，或每请求 CPU > **2× 基线**（>25ms） | 旧代码 2h 到 1.36GB；500ms 出现在 20–40min；25ms 出现在 40–60min |
| 定时兜底 | 每 **4–6 小时**滚动重启 | 按旧代码 695.6 MB/h，从 160MB 到 800MB 约 55 分钟；修复后待复测 |
| 审计文件 | 100MB × 5 份轮转（HEAD 已有），保留 7 天 | `116231a` |
| token / quest 清理 | token apply/超时 > 10 分钟删除；completed quest > 7 天 prune | §9.1/§9.3 |

对“30 天”的直接回答：

- **旧代码（本次实测）**：不可能。内存 1 天量级就超物理内存。
- **HEAD（116231a 之后）**：审计与幂等缓存已封顶，但 tokens/outbox/quests 仍线性；DB 仍线性。
  即使全部修完，仍需要**归档 + 4–6h 滚动重启 + 清理策略**，并有干净机器上的 ≥24h 复测数据支撑，
  才能给出“30 天可行”的结论。**目前没有这个数据，不做结论。**

---

## 11. 附：数据与复现

- `reports/soak/soak-20261106-015816/samples.jsonl` — 233 行，30s 一行（ts/rss/cpu/events/db/tick/
  窗口请求数/错误数/读写 P50-P95-P99）
- `reports/soak/soak-20261106-015816/summary.json` — 汇总（首末 10 分钟对照、斜率、外推、验收）
- `reports/soak/soak-20261106-015816/gc_probe.jsonl` — 22 个点的对象普查
- `reports/soak/soak-20261106-015816/server.log`、`soak_stdout.log` — 服务与工具原始输出
- `runs/soak-20261106-015816/`（gitignored）— `events.sqlite` 224MB、`mcp_audit.jsonl` 176MB

工具：`tools/soak_test.py`（仅标准库 + 复用 `tools/load_test.py` 的虚拟玩家与校验；
服务进程按规格 `python -m wsim.api --auth off --run <id>` + `XKX_AMBIENT=1` 启动；
gc 普查通过报告目录内的 `sitecustomize.py` 注入，不改源码）。

---

## 12. 补充归因：探活不得做重活（第二次 2h soak，50 席）

> 本节记录第二次 2 小时 soak（50 席）暴露的**请求路径重活**问题及其修复。
> 与 §1–§11 的第一次 soak 是不同进程/不同采样窗口；数字按本节口径为准。

### 12.1 现象

| 指标 | 实测 | 判定 |
|---|---|---|
| 单窗口 RSS 尖峰 | **816 MB / 1221 MB** | 异常 |
| `peak_sampled_mb` | **1323 MB** | — |
| `peak_growth_pct_per_hour` | **485.59%** | `rss_growth_lt_5pct_per_hour` **FAIL** |
| `p95_drift_write_lt_1_3` | ratio **1.617** | **FAIL** |
| `write.max` | **4.7s** | 长尾 |
| `/api/health` 等锁 | **1.07 ~ 18s** | 探活被写路径拖住 |

读/写延迟尾部同时恶化；`/api/health` 每约 30 秒被轮询一次，却经常比业务请求还慢。

### 12.2 归因（已确认，不再靠压测复验）

- `/api/health` 的 `HealthProbe.integrity()` 以 60 秒为缓存周期；一旦过期，就在
  **`session.lock`（全局 RLock，所有 API 读写都持有）内**执行 `PRAGMA integrity_check`。
- 实测单次全库 `PRAGMA` 约 **0.68 ~ 1.8s**；health 线程为此等锁 **1.07 ~ 18s**。
- 同一窗口 `read.max` / `write.max` 达 **2.0 ~ 4.7s**：写/读请求都在等这把被全库校验占住的锁。
- `read_all` / `event_hash` 在请求路径的调用次数为 **0**（只在启动 replay 与关停
  `final_summary` 使用）。也就是说，本轮不是全量回放的问题，而是**探活自己在做全库重活**。

### 12.3 修复方式

1. **`/api/health` 只做便宜检查**
   - `integrity()` 变成**纯只读缓存**：直接返回上次校验结果，不执行 PRAGMA、不拿
     `session.lock`；默认 `"unknown"`，启动校验成功后为 `"ok"`。
   - 尾部两跳连续性检查改用**独立只读连接**（`mode=ro`，探针自己的 `threading.Lock`，
     不参与会话写锁），SQL 仍是 `SELECT seq FROM events ORDER BY seq DESC LIMIT 2`，O(log n)。
   - 追加 O(1) 检查：`db_openable`（只读连接 `SELECT 1`）、`disk_writable`
     （`os.access(events.sqlite 父目录, W_OK)`）。
   - `ok = events_ok and disk_ok and writer_ok`；其中
     `events_ok = seq_gap==0 and integrity in ("ok","unknown")`。语义只为
     “不把已知损坏的库报成健康”，`unknown` 不判死。
   - `storage` 保留原键，并追加 `integrity_checked_at`（wall 时间，可为 null）、
     `db_openable`、`disk_writable`。
2. **全库校验下沉**
   - **启动**：`probe_from_session(startup_integrity=...)` 默认读
     `XKX_STARTUP_INTEGRITY`（默认 `"1"`），在 `create_server` 启动阶段同步跑一次
     `run_integrity()` 播种缓存（此时还没有请求，允许耗时）；启动守卫已经跑过时复用
     其结果，不重复跑第二遍。
   - **备份/恢复**：沿用 `tools/restore_verify.py` 的 `PRAGMA integrity_check`。
   - **显式运维入口**：新增 `tools/check_integrity.py --root . --run <RUN_ID>`，只读连接、
     输出 JSON、退出码 0/1；不挂请求路径。
3. **请求路径去全量扫描**（master 上 `session.intent()` / `messages()` 仍在用 `read_all`）
   - `WorldStore` 增加 `idx_events_intent(intent_id)`、`idx_events_actor_tick(actor, tick)`；
   - 新增 `find_intent(intent_id, actor)`（`ORDER BY seq ASC LIMIT 1`，与 `read_all` 首次命中一致）
     与 `events_for_actor_since(actor, since_tick, limit=None)`；
   - 两个端点改用定向查询，返回字段、`next_since_tick`、`memory_add` / `talk:npc` 过滤逐字节保持；
   - `event_hash()` 保持语义，仅注明“离线/关停专用，禁止请求路径调用”。

### 12.4 设计约束（长期有效）

> **探活不得做重活**：`/api/health` 禁止 `integrity_check`、`read_all`、全表扫描、全量哈希；
> 这些只允许在**启动 / 备份恢复 / 显式运维入口**执行，且**不得持有会话写锁**。

### 12.5 待跟进（本次不修）

- 2h 堆快照中 `Event` / `AuditRecord` 等对象**按事件全量驻留**（独立于探活问题）。
  这是下一热点：即使 health 修好，RSS 仍会随事件量增长。需要单独处理（有界缓存 /
  归档 / 关闭不必要的全量引用），并复跑 2h soak 验证 `rss_growth_lt_5pct_per_hour`。
</shell_command>

