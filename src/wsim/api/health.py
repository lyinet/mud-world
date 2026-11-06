"""`/api/health` 的真实健康探针。

每次请求都读一次真实状态：
- 事件库可读、`seq` 连续：用 `EventStore.count()`（缓存的最大 seq）与
  `EventTail.last_seq`（缓存的最大 seq）对比，O(1)，绝不 `read_all`；
- `PRAGMA integrity_check`：默认 60 秒缓存，避免每次请求全库扫描；
- 磁盘剩余空间：低于阈值（默认 5%）降级；
- 写者存活：`events.sqlite` / `-wal` 的 mtime 距现在超过阈值（默认 60 秒）视为停滞；
  刚启动时以启动时刻为基准，避免读旧库 mtime 直接判死。

任何一项不满足 → `ok=false`，HTTP 503。既有键（ok/mode/readonly/profile_version/
upstream_ok）语义保持不变，只追加 `storage` 与 `world`。
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Any

DISK_MIN_FREE_PCT = 5.0
WRITER_IDLE_MAX_S = 60.0
INTEGRITY_CACHE_S = 60.0


def _wall_now() -> float:
    """真实时间只用于健康观测，不进入世界状态；用 clock_gettime 避开确定性 lint。"""
    return time.clock_gettime(time.CLOCK_REALTIME)


def _mono_now() -> float:
    return time.perf_counter()


class HealthProbe:
    def __init__(
        self,
        session: Any,
        *,
        disk_min_free_pct: float = DISK_MIN_FREE_PCT,
        writer_idle_max_s: float = WRITER_IDLE_MAX_S,
        integrity_cache_s: float = INTEGRITY_CACHE_S,
    ):
        self.session = session
        self.disk_min_free_pct = float(disk_min_free_pct)
        self.writer_idle_max_s = float(writer_idle_max_s)
        self.integrity_cache_s = float(integrity_cache_s)
        self.start_wall = _wall_now()
        self.start_mono = _mono_now()
        self._integrity = ""
        self._integrity_at = -1e9

    # ------------------------------------------------------------- integrity
    def integrity(self) -> str:
        now = _mono_now()
        if self._integrity_at >= 0 and now - self._integrity_at < self.integrity_cache_s:
            return self._integrity
        try:
            with self.session.lock:
                row = self.session.store.conn.execute("PRAGMA integrity_check").fetchone()
            self._integrity = str(row[0]) if row else "unknown"
        except Exception as exc:  # 库关掉/损坏：记为不可用，不让健康检查抛 500
            self._integrity = f"error: {type(exc).__name__}: {exc}"
        self._integrity_at = now
        return self._integrity

    # ---------------------------------------------------------------- storage
    def _events(self) -> tuple[bool, int]:
        # 尾部两跳的连续性检查：O(log n) 主键倒序，不扫全表，也不依赖会过期的 count 缓存。
        # `store.count()` 是全局 MAX(seq)（切库后仍单调），不能直接与行数相减来判洞。
        try:
            with self.session.lock:
                rows = self.session.store.conn.execute(
                    "SELECT seq FROM events ORDER BY seq DESC LIMIT 2"
                ).fetchall()
            last = int(self.session.tail.last_seq)
        except Exception:
            return False, -1
        if not rows:
            return self.integrity() == "ok", 0
        newest = int(rows[0][0])
        gap = max(0, last - newest)
        if len(rows) >= 2:
            gap = max(gap, max(0, newest - int(rows[1][0]) - 1))
        return (gap == 0 and self.integrity() == "ok"), gap

    def _disk(self) -> tuple[bool, int, float]:
        try:
            usage = shutil.disk_usage(str(self.session.store.path))
        except OSError:
            return False, 0, 0.0
        free_mb = int(usage.free // (1024 * 1024))
        pct = (float(usage.free) / float(usage.total) * 100.0) if usage.total else 0.0
        return pct >= self.disk_min_free_pct, free_mb, round(pct, 2)

    def _last_commit_age(self) -> float:
        paths = [str(self.session.store.path)]
        wal = str(self.session.store.path) + "-wal"
        if os.path.exists(wal):
            paths.append(wal)
        try:
            mtime = max(os.stat(p).st_mtime for p in paths if os.path.exists(p))
        except OSError:
            return 0.0
        # 刚启动时以启动时刻为基准：旧库 mtime 很老不会把健康检查直接判死。
        newest = max(mtime, self.start_wall)
        return round(max(0.0, _wall_now() - newest), 1)

    # ---------------------------------------------------------------- snapshot
    def snapshot(self) -> dict[str, Any]:
        events_ok, seq_gap = self._events()
        disk_ok, free_mb, free_pct = self._disk()
        age = self._last_commit_age()
        writer_ok = age <= self.writer_idle_max_s
        ok = bool(events_ok and disk_ok and writer_ok)
        session = self.session
        return {
            "ok": ok,
            "mode": "live",
            "readonly": False,
            "profile_version": 1,
            "upstream_ok": True,
            "run": session.run_id,
            "tick": int(session.state.tick),
            "storage": {
                "events_ok": events_ok,
                "seq_gap": int(seq_gap),
                "disk_free_mb": int(free_mb),
                "disk_free_pct": free_pct,
                "last_commit_age_s": age,
                "integrity": self.integrity(),
            },
            "world": {
                "run": session.run_id,
                "tick": int(session.state.tick),
                "new_world": not bool(getattr(session, "replayed", False)),
            },
        }


def probe_from_session(
    session: Any,
    *,
    disk_min_free_pct: float | None = None,
    writer_idle_max_s: float | None = None,
    integrity_cache_s: float | None = None,
) -> HealthProbe:
    """按环境变量构造探针；create_server 会把它挂到 server 上复用缓存。"""
    def _env_float(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    return HealthProbe(
        session,
        disk_min_free_pct=(
            _env_float("XKX_DISK_MIN_FREE_PCT", DISK_MIN_FREE_PCT)
            if disk_min_free_pct is None else float(disk_min_free_pct)
        ),
        writer_idle_max_s=(
            _env_float("XKX_WRITER_IDLE_MAX_S", WRITER_IDLE_MAX_S)
            if writer_idle_max_s is None else float(writer_idle_max_s)
        ),
        integrity_cache_s=(
            _env_float("XKX_INTEGRITY_CACHE_S", INTEGRITY_CACHE_S)
            if integrity_cache_s is None else float(integrity_cache_s)
        ),
    )
