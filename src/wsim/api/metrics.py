"""HTTP 观测指标（Prometheus 文本格式，纯只读）。

设计约束（对应上线前的性能要求）：

- 采集只发生在 HTTP 层：每次请求一次 `perf_counter` 差值、一次 µs 级短临界区字典更新。
  不进入 Writer/事件写事务，也不持有 `WorldSession.lock`。
- 唯一的一把 `threading.Lock` 只保护内存计数，临界区内无 IO/无状态读取，正常开销 < 10µs。
- 标签基数受控：动态路径归一化为 `:id`，避免任意 URL 把内存打爆。
- `/api/metrics` 渲染时的磁盘/状态读取都在只读路径：事件数取 EventStore 缓存、
  数据库大小用 `Path.stat()`、ambient 计数读 `ambient_status.json`。
"""
from __future__ import annotations

import threading
from typing import Any

# 直方图分桶：5ms / 10 / 25 / 50 / 100 / 250 / 500 / 1000 / +Inf
BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
_BUCKET_LABELS: tuple[str, ...] = ("0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1.0")


def normalize_path(path: str) -> str:
    """把动态路径折叠成固定标签，控制 Prometheus 标签基数。"""
    raw = str(path or "")
    if raw.startswith("/api/player/intents/"):
        return "/api/player/intents/:id"
    return raw or "/"


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class HttpMetrics:
    """进程内 HTTP 指标累加器；线程安全，全部内存操作。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str], int] = {}
        self._rejections: dict[str, int] = {}
        # 每个 path 的非累积桶计数（len(BUCKETS)+1，最后一桶是 +Inf）
        self._buckets: dict[str, list[int]] = {}
        self._duration_sum: dict[str, float] = {}
        self._duration_count: dict[str, int] = {}

    def observe(self, path: str, status: int, seconds: float) -> None:
        label = normalize_path(path)
        code = str(int(status or 0))
        seconds_f = max(0.0, float(seconds))
        with self._lock:
            key = (label, code)
            self._requests[key] = self._requests.get(key, 0) + 1
            counts = self._buckets.get(label)
            if counts is None:
                counts = [0] * (len(BUCKETS) + 1)
                self._buckets[label] = counts
            idx = len(BUCKETS)
            for i, edge in enumerate(BUCKETS):
                if seconds_f <= edge:
                    idx = i
                    break
            counts[idx] += 1
            self._duration_sum[label] = self._duration_sum.get(label, 0.0) + seconds_f
            self._duration_count[label] = self._duration_count.get(label, 0) + 1

    def rejection(self, code: str) -> None:
        label = str(code or "")
        if not label:
            return
        with self._lock:
            self._rejections[label] = self._rejections.get(label, 0) + 1

    def render(
        self,
        *,
        world_tick: int,
        events_total: int,
        players_online: int,
        ambient_paused_total: int,
        llm_calls: int,
        db_sizes: dict[str, int] | None = None,
    ) -> str:
        """渲染 Prometheus 文本格式（version 0.0.4）。"""
        with self._lock:
            requests = dict(self._requests)
            rejections = dict(self._rejections)
            buckets = {k: list(v) for k, v in self._buckets.items()}
            sums = dict(self._duration_sum)
            counts = dict(self._duration_count)

        lines: list[str] = []

        lines.append("# HELP xkx_http_requests_total HTTP requests handled, by path and status code.")
        lines.append("# TYPE xkx_http_requests_total counter")
        for (path, code), value in sorted(requests.items()):
            lines.append(
                f'xkx_http_requests_total{{code="{_escape(code)}",path="{_escape(path)}"}} {value}'
            )

        lines.append("# HELP xkx_http_rejections_total Business rejections, by reason code.")
        lines.append("# TYPE xkx_http_rejections_total counter")
        for code, value in sorted(rejections.items()):
            lines.append(f'xkx_http_rejections_total{{code="{_escape(code)}"}} {value}')

        lines.append("# HELP xkx_http_request_duration_seconds HTTP request duration, by path.")
        lines.append("# TYPE xkx_http_request_duration_seconds histogram")
        for path in sorted(buckets):
            cumulative = 0
            for i, edge_label in enumerate(_BUCKET_LABELS):
                cumulative += buckets[path][i]
                lines.append(
                    f'xkx_http_request_duration_seconds_bucket{{le="{edge_label}",path="{_escape(path)}"}} {cumulative}'
                )
            cumulative += buckets[path][len(BUCKETS)]
            lines.append(
                f'xkx_http_request_duration_seconds_bucket{{le="+Inf",path="{_escape(path)}"}} {cumulative}'
            )
            lines.append(
                f'xkx_http_request_duration_seconds_sum{{path="{_escape(path)}"}} {sums.get(path, 0.0):.6f}'
            )
            lines.append(
                f'xkx_http_request_duration_seconds_count{{path="{_escape(path)}"}} {counts.get(path, 0)}'
            )

        lines.append("# HELP xkx_world_tick Current world tick (1 tick = 10 game minutes).")
        lines.append("# TYPE xkx_world_tick gauge")
        lines.append(f"xkx_world_tick {int(world_tick)}")

        lines.append("# HELP xkx_events_total Total appended events in the world log (monotonic).")
        lines.append("# TYPE xkx_events_total counter")
        lines.append(f"xkx_events_total {int(events_total)}")

        lines.append("# HELP xkx_players_online Players currently online.")
        lines.append("# TYPE xkx_players_online gauge")
        lines.append(f"xkx_players_online {int(players_online)}")

        lines.append("# HELP xkx_ambient_paused_total Ambient scheduling pauses (from ambient status).")
        lines.append("# TYPE xkx_ambient_paused_total counter")
        lines.append(f"xkx_ambient_paused_total {int(ambient_paused_total)}")

        lines.append("# HELP xkx_llm_calls_total LLM calls made by the ambient reasoner.")
        lines.append("# TYPE xkx_llm_calls_total counter")
        lines.append(f"xkx_llm_calls_total {int(llm_calls)}")

        lines.append("# HELP xkx_db_bytes SQLite database file size in bytes.")
        lines.append("# TYPE xkx_db_bytes gauge")
        for name, size in sorted((db_sizes or {}).items()):
            lines.append(f'xkx_db_bytes{{file="{_escape(name)}"}} {int(size)}')

        return "\n".join(lines) + "\n"
