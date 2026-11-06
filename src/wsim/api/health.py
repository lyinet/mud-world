"""`/api/health` 的真实健康探针 —— **探活不得做重活**。

请求路径只做 O(1) / O(log n) 的便宜检查：
- 事件库可读、`seq` 连续：独立只读连接（不持有 `session.lock`）取尾部两跳，
  再与 `EventTail.last_seq` 对比，O(log n)，绝不 `read_all`；
- 全库 `PRAGMA integrity_check` **不在请求路径执行**：`integrity()` 只返回缓存，
  缓存由启动检查（`XKX_STARTUP_INTEGRITY`，默认开）、备份/恢复与
  `tools/check_integrity.py` 播种，默认 `"unknown"`；
- 磁盘剩余空间：低于阈值（默认 5%）降级；
- 写者存活：`events.sqlite` / `-wal` 的 mtime 距现在超过阈值（默认 60 秒）视为停滞；
  刚启动时以启动时刻为基准，避免读旧库 mtime 直接判死。

任何一项不满足 → `ok=false`，HTTP 503。既有键（ok/mode/readonly/profile_version/
upstream_ok）语义保持不变，只追加 `storage` 与 `world`。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .world_guard import cached_integrity

DISK_MIN_FREE_PCT = 5.0
WRITER_IDLE_MAX_S = 60.0
# 保留旧参数名以兼容既有调用方；请求路径不再按时间重新跑全库校验。
INTEGRITY_CACHE_S = 60.0


def _wall_now() -> float:
    """真实时间只用于健康观测，不进入世界状态；用 clock_gettime 避开确定性 lint。"""
    return time.clock_gettime(time.CLOCK_REALTIME)


def _mono_now() -> float:
    return time.perf_counter()


def _iso_wall(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


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
        # 仅为兼容旧配置保留；integrity 已是纯缓存，不再按这个周期重跑 PRAGMA。
        self.integrity_cache_s = float(integrity_cache_s)
        self.start_wall = _wall_now()
        self.start_mono = _mono_now()
        self._integrity = "unknown"
        self._integrity_checked_at: float | None = None
        self._ro_lock = threading.Lock()
        self._ro_conn: sqlite3.Connection | None = None

    # ------------------------------------------------- 独立只读连接（无 session.lock）
    def _open_ro(self) -> sqlite3.Connection:
        path = str(self.session.store.path)
        if path == ":memory:":
            raise sqlite3.OperationalError("in-memory event store has no read-only file")
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _drop_ro_locked(self) -> None:
        conn, self._ro_conn = self._ro_conn, None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _query(self, sql: str) -> list[Any]:
        """在独立只读连接上执行便宜查询；锁只保护探针自己的连接，不碰会话写锁。"""
        with self._ro_lock:
            try:
                if self._ro_conn is None:
                    self._ro_conn = self._open_ro()
                return list(self._ro_conn.execute(sql).fetchall())
            except sqlite3.Error:
                self._drop_ro_locked()
                raise

    # ------------------------------------------------------------- integrity
    def integrity(self) -> str:
        """请求路径专用：只读缓存，不执行 PRAGMA、不拿 `session.lock`。"""
        return self._integrity

    def run_integrity(self) -> str:
        """全库校验，仅供启动 / 备份恢复 / 显式运维入口调用；绝不持有会话写锁。"""
        if str(self.session.store.path) == ":memory:":
            self._integrity = "unknown"
            self._integrity_checked_at = None
            return self._integrity
        cached = cached_integrity(self.session.store.path)
        if cached is not None:
            result = cached
        else:
            try:
                conn = self._open_ro()
                try:
                    row = conn.execute("PRAGMA integrity_check").fetchone()
                    result = str(row[0]) if row else "unknown"
                finally:
                    conn.close()
            except Exception as exc:  # 库关掉/损坏：记为不可用，不让健康检查抛 500
                result = f"error: {type(exc).__name__}: {exc}"
        self._integrity = result
        self._integrity_checked_at = _wall_now()
        return result

    def close(self) -> None:
        with self._ro_lock:
            self._drop_ro_locked()

    # ---------------------------------------------------------------- storage
    def _events(self) -> tuple[bool, int]:
        # 尾部两跳的连续性检查：O(log n) 主键倒序，不扫全表，也不依赖会过期的 count 缓存。
        # `store.count()` 是全局 MAX(seq)（切库后仍单调），不能直接与行数相减来判洞。
        try:
            rows = self._query("SELECT seq FROM events ORDER BY seq DESC LIMIT 2")
            last = int(self.session.tail.last_seq)
        except Exception:
            return False, -1
        integrity_ok = self._integrity in ("ok", "unknown")
        if not rows:
            return integrity_ok, 0
        newest = int(rows[0][0])
        gap = max(0, last - newest)
        if len(rows) >= 2:
            gap = max(gap, max(0, newest - int(rows[1][0]) - 1))
        return (gap == 0 and integrity_ok), gap

    def _db_openable(self) -> bool:
        try:
            self._query("SELECT 1")
            return True
        except Exception:
            return False

    def _disk_writable(self) -> bool:
        path = str(self.session.store.path)
        if path == ":memory:":
            return False
        try:
            return bool(os.access(Path(path).resolve().parent, os.W_OK))
        except OSError:
            return False

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
        db_openable = self._db_openable()
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
                "integrity_checked_at": _iso_wall(self._integrity_checked_at),
                "db_openable": db_openable,
                "disk_writable": self._disk_writable(),
            },
            "world": {
                "run": session.run_id,
                "tick": int(session.state.tick),
                "new_world": not bool(getattr(session, "replayed", False)),
            },
        }


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def probe_from_session(
    session: Any,
    *,
    disk_min_free_pct: float | None = None,
    writer_idle_max_s: float | None = None,
    integrity_cache_s: float | None = None,
    startup_integrity: bool | None = None,
) -> HealthProbe:
    """按环境变量构造探针；create_server 会把它挂到 server 上复用缓存。

    `startup_integrity` 缺省读 `XKX_STARTUP_INTEGRITY`（默认 `"1"` 开启）：
    开启时在启动阶段同步跑一次全库校验来播种缓存；此时还没有请求，允许耗时。
    """
    def _env_float(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    probe = HealthProbe(
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
    enabled = (
        _env_bool("XKX_STARTUP_INTEGRITY", True)
        if startup_integrity is None else bool(startup_integrity)
    )
    if enabled:
        probe.run_integrity()
    return probe
