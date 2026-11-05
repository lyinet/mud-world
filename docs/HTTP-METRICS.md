# HTTP 指标端点（Prometheus）

`GET /api/metrics` 输出 Prometheus 文本格式（version 0.0.4），只读，不改动任何既有端点响应。
实现：[src/wsim/api/metrics.py](../src/wsim/api/metrics.py)（累加器与渲染）、
[src/wsim/api/server.py](../src/wsim/api/server.py)（HTTP 层采集与鉴权）。

## 采集边界

- 采集只发生在 HTTP 层：每次 GET/POST 在 `do_*` 入口取一次 `time.perf_counter()`，出口把
  路径、状态码、耗时写入进程内计数。
- 不进写事务：不碰 `Writer` / `EventStore` / `WorldSession` 写路径，也不持有会话锁。
- 只有一把 `threading.Lock` 保护内存计数，临界区是纯 dict 自增，无 IO；不同路径的渲染在
  同一把锁下做一次浅拷贝后即释放。
- 标签基数受控：`/api/player/intents/<id>` 统一归一化为 `/api/player/intents/:id`，
  未知路由按原样记录（`/api/not-a-route` 这类由调用方决定，不会自动展开参数）。

## 指标清单

| 指标 | 类型 | 标签 | 来源 |
| --- | --- | --- | --- |
| `xkx_http_requests_total` | counter | `path`, `code` | HTTP 层状态码 |
| `xkx_http_rejections_total` | counter | `code`（业务原因码） | 响应体 `ok=false` 的 `reject_reason`/`code` |
| `xkx_http_request_duration_seconds` | histogram | `path` | `perf_counter` 差值 |
| `xkx_world_tick` | gauge | — | `WorldSession.state.tick` |
| `xkx_events_total` | counter | — | `EventStore.count()`（缓存的最大 seq） |
| `xkx_players_online` | gauge | — | `state.players` 中 `online=True` 的个数 |
| `xkx_ambient_paused_total` | counter | — | `ambient_status.json` 的 `cpu.pauses` |
| `xkx_llm_calls_total` | counter | — | `ambient_status.json` 的 `llm_calls` |
| `xkx_db_bytes` | gauge | `file` | `events.sqlite` / `service.sqlite` 的 `st_size` |

直方图分桶：`0.005 / 0.01 / 0.025 / 0.05 / 0.1 / 0.25 / 0.5 / 1.0 / +Inf`（秒）。
渲染时按累积桶输出，`+Inf` 等于 `_count`。

`ambient` 相关计数取自后台自走的 `ambient_status.json`（累计值，非本进程内计数）；
文件缺失或损坏时输出 `0`，不会阻塞或抛错。

## 鉴权

| 绑定地址 | `XKX_METRICS_TOKEN` | 结果 |
| --- | --- | --- |
| `127.0.0.1` / `::1` / `localhost` | 不需要 | 匿名可抓 |
| 非回环 | 未配置 | 一律 **404**（与未知路由同形，不暴露端点存在） |
| 非回环 | 已配置 | 需 `Authorization: Bearer <token>` 或 `X-Metrics-Token: <token>`，否则 404 |

默认值由 `create_server(..., metrics_public=None)` 按绑定地址推断；也可显式传
`metrics_public=True/False` 与 `metrics_token=...`（便于测试与私有部署）。

服务启动示例（非回环绑定）：

```bash
XKX_METRICS_TOKEN="$(cat /etc/xkx/metrics-token)" \
python -m wsim.api --root . --run player-demo --host 0.0.0.0 --port 8080 --auth on
```

Prometheus 抓取配置：

```yaml
scrape_configs:
  - job_name: xkx
    metrics_path: /api/metrics
    static_configs:
      - targets: ["127.0.0.1:8080"]
    authorization:
      credentials_file: /etc/xkx/metrics-token
```

## 实测（2026-11-06）

命令：

```bash
python3 /tmp/xkx_metrics_p95.py /Users/lyinet/projects/XKX/world-sim 8170 metrics-p95 after-metrics
```

原样输出：

```json
{"label": "after-metrics-commit", "phase": "sequential", "n": 300, "p50_ms": 0.405, "p95_ms": 1.395, "p99_ms": 2.801, "max_ms": 3.571}
{"label": "after-metrics-commit", "phase": "concurrent", "concurrency": 50, "duration_s": 11.99, "requests": 25921, "rps": 2161.1, "errors": 21, "p50_ms": 2.545, "p95_ms": 55.334, "p99_ms": 215.096, "max_ms": 5009.934}
```

- 单请求顺序 300 次：**P95 = 1.395ms**（要求 < 10ms）。
- 50 并发压测（10s，25921 请求，2161 rps）：P95 55.3ms；该值是 50 路并发下的排队延迟，
  不是端点自身处理成本，对照 `/api/state` 同口径压测可看到同量级。
- 采集对 `/api/state` 的影响见 CHANGELOG 的同一小节（50 并发 30s 前后对照）。
