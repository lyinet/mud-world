"""SQLite 事件日志。append-only，事件是唯一事实源。

表结构刻意保持扁平：seq / tick / day / intent_id / actor / verb / args /
effects / outcome / reason / source。任何修正都靠追加补偿事件，不 UPDATE/DELETE。

分库归档（长跑必需）：
- `events.sqlite` 始终是**活动分片**；达到阈值（默认 >5 GiB 或 >550 万条）时，
  把它改名为 `events-000001.sqlite` 留档，再建新的 `events.sqlite` 接续写入。
- seq 是**全局连续**的：新分片的 meta `base_seq` 记录上一分片的末位 seq，
  因此 `count()` 跨分片单调递增，不会重号。
- 归档文件是同一 schema 的独立 sqlite，可单独打开查询/审计；完整回放由
  `read_all()` 按分片顺序读取（恢复流程据此选对分片）。
- 重启时若活动分片缺失（切库中途崩溃），自动以编号最大的归档为尾部分片、
  新建活动分片并从其后接续，不丢事件、不重号。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from ..core.canonical import dumps, hash_obj
from ..core.models import Event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY,
    tick INTEGER NOT NULL,
    day INTEGER NOT NULL,
    intent_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    verb TEXT NOT NULL,
    args TEXT NOT NULL,
    effects TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    legacy INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_tick ON events(tick);
CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_ACTIVE_NAME = "events.sqlite"
_ARCHIVE_RE = re.compile(r"^events-(\d{6,})\.sqlite$")
_DEFAULT_MAX_BYTES = 5 * 1024**3
_DEFAULT_MAX_EVENTS = 5_500_000


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


class EventStore:
    def __init__(self, path: str | Path = ":memory:", check_same_thread: bool = True):
        self.path = str(path)
        self._is_memory = self.path == ":memory:"
        self._check_same_thread = check_same_thread
        self._max_bytes = _env_int("XKX_EVENT_SEGMENT_MAX_BYTES", _DEFAULT_MAX_BYTES)
        self._max_events = _env_int("XKX_EVENT_SEGMENT_MAX_EVENTS", _DEFAULT_MAX_EVENTS)
        self._rotate_checks = 0
        self._base_seq = 0
        self._segment_index = 0
        self._active_rows = 0
        self._archives: list[Path] = []
        if not self._is_memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._archives = self._discover_archives()
        self.conn = self._connect(self.path)
        self._init_meta()
        cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(events)")}
        self._has_legacy = "legacy" in cols
        self._cached_count: int | None = None
        # 初始化活动分片的行数（用于阈值判断，避免每次 append 都 COUNT(*)）。
        self.count()

    # --------------------------------------------------------------- lifecycle
    def _connect(self, path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path, check_same_thread=self._check_same_thread)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn

    def _is_primary(self) -> bool:
        return not self._is_memory and Path(self.path).name == _ACTIVE_NAME

    def _discover_archives(self) -> list[Path]:
        """只对活动库做分片发现；直接打开归档文件时只读它自己。"""
        if not self._is_primary():
            return []
        parent = Path(self.path).parent
        found: list[tuple[int, Path]] = []
        for entry in parent.iterdir():
            match = _ARCHIVE_RE.match(entry.name)
            if match and entry.is_file():
                found.append((int(match.group(1)), entry))
        return [p for _, p in sorted(found)]

    def _init_meta(self) -> None:
        """恢复 base_seq / segment_index；活动库缺失时从最新归档接续。"""
        base = self.get_meta("base_seq")
        index = self.get_meta("segment_index")
        if base is not None and index is not None:
            self._base_seq = int(base)
            self._segment_index = int(index)
            return
        if not self._is_primary():
            self._base_seq = int(base or 0)
            self._segment_index = int(index or 0)
            self._write_meta("base_seq", str(self._base_seq))
            self._write_meta("segment_index", str(self._segment_index))
            return
        # 活动库没有 meta：要么是旧库（base=0），要么是切库中断后新建的空库。
        last_index = 0
        last_seq = 0
        carried: dict[str, str] = {}
        if self._archives:
            tail = self._archives[-1]
            last_index = int(self.get_meta_from(tail, "segment_index") or 0)
            last_seq = self._max_seq_in(tail)
            carried = self._read_meta(tail)
        self._segment_index = last_index + 1 if self._archives else 0
        self._base_seq = last_seq if self._archives else 0
        for key, value in carried.items():
            if key not in ("base_seq", "segment_index"):
                self._write_meta(key, value)
        self._write_meta("base_seq", str(self._base_seq))
        self._write_meta("segment_index", str(self._segment_index))

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- meta
    def _read_meta(self, path: Path) -> dict[str, str]:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT key, value FROM meta").fetchall()
        finally:
            conn.close()
        return {str(r[0]): str(r[1]) for r in rows}

    def get_meta_from(self, path: Path, key: str) -> str | None:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        finally:
            conn.close()
        return None if row is None else str(row[0])

    def _max_seq_in(self, path: Path) -> int:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT MAX(seq) FROM events").fetchone()
        finally:
            conn.close()
        return int(row[0]) if row and row[0] is not None else 0

    # -------------------------------------------------------------- segment io
    def count(self) -> int:
        """全局最后一条事件的 seq（跨分片单调，切库不重号）。"""
        if self._cached_count is None:
            row = self.conn.execute("SELECT MAX(seq) FROM events").fetchone()
            local = int(row[0]) if row and row[0] is not None else 0
            self._cached_count = max(self._base_seq, local)
            self._active_rows = max(0, local - self._base_seq)
        return self._cached_count

    def segments(self) -> list[str]:
        """归档分片（按编号升序）+ 活动分片，供运维/恢复审计。"""
        return [str(p) for p in self._archives] + [self.path]

    def active_rows(self) -> int:
        return int(self.count() - self._base_seq)

    def append(self, event: Event) -> None:
        self._maybe_rotate()
        if self._has_legacy:
            self.conn.execute(
                "INSERT INTO events(seq,tick,day,intent_id,actor,verb,args,effects,outcome,reason,source,legacy)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.seq,
                    event.tick,
                    event.day,
                    event.intent_id,
                    event.actor,
                    event.verb,
                    dumps(event.args),
                    dumps(event.effects),
                    event.outcome,
                    event.reason,
                    event.source,
                    int(event.legacy),
                ),
            )
        else:
            self.conn.execute(
                "INSERT INTO events(seq,tick,day,intent_id,actor,verb,args,effects,outcome,reason,source)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.seq,
                    event.tick,
                    event.day,
                    event.intent_id,
                    event.actor,
                    event.verb,
                    dumps(event.args),
                    dumps(event.effects),
                    event.outcome,
                    event.reason,
                    event.source,
                ),
            )
        self.conn.commit()
        # append-only 且 seq 连续，直接缓存最新 seq，避免每次 COUNT(*) 退化为 O(n^2)
        self._cached_count = event.seq
        self._active_rows += 1

    def _maybe_rotate(self) -> None:
        """阈值触发切库：>max_events 立即切；否则每 256 次检查一次磁盘大小。"""
        if self._is_memory or not self._is_primary():
            return
        if self._active_rows >= self._max_events:
            self.rotate()
            return
        self._rotate_checks += 1
        if self._rotate_checks % 256:
            return
        pages = int(self.conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(self.conn.execute("PRAGMA page_size").fetchone()[0])
        size = pages * page_size
        wal = Path(self.path + "-wal")
        if wal.exists():
            size += wal.stat().st_size
        if size > self._max_bytes:
            self.rotate()

    def rotate(self) -> Path | None:
        """把活动库归档并新建空库继续写；返回归档路径。调用方需持有写锁。"""
        if self._is_memory or not self._is_primary():
            return None
        base = self.count()
        self.conn.commit()
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
        self.conn.close()
        target = Path(self.path).with_name(f"events-{self._segment_index + 1:06d}.sqlite")
        if target.exists():
            raise RuntimeError(f"archive_exists:{target}")
        os.replace(self.path, target)
        for suffix in ("-wal", "-shm"):
            side = Path(self.path + suffix)
            if side.exists():
                side.unlink()
        # 新活动库继承身份 meta（content_hash 等）与全局 seq 起点。
        carried = self._read_meta(target)
        self.conn = self._connect(self.path)
        for key, value in carried.items():
            if key not in ("base_seq", "segment_index"):
                self._write_meta(key, value)
        self._segment_index += 1
        self._base_seq = base
        self._write_meta("base_seq", str(self._base_seq))
        self._write_meta("segment_index", str(self._segment_index))
        self._archives.append(target)
        self._cached_count = base
        self._active_rows = 0
        cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(events)")}
        self._has_legacy = "legacy" in cols
        return target

    def read_all(self) -> list[Event]:
        out: list[Event] = []
        for path in self._archives:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                out.extend(self._rows_to_events(conn))
            finally:
                conn.close()
        out.extend(self._rows_to_events(self.conn))
        return out

    def recent_rows(self, where: str, params: tuple, limit: int) -> list[tuple]:
        """最近事件行（活动分片优先，不足时回溯归档），供 feed 等读路径使用。"""
        sql = f"SELECT seq,tick,actor,verb,args FROM events WHERE {where} ORDER BY seq DESC LIMIT ?"
        rows = list(self.conn.execute(sql, (*params, limit)).fetchall())
        for path in reversed(self._archives):
            if len(rows) >= limit:
                break
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                rows.extend(conn.execute(sql, (*params, limit - len(rows))).fetchall())
            finally:
                conn.close()
        return rows

    def _rows_to_events(self, conn: sqlite3.Connection) -> list[Event]:
        cols = "seq,tick,day,intent_id,actor,verb,args,effects,outcome,reason,source"
        has_legacy = "legacy" in {str(r[1]) for r in conn.execute("PRAGMA table_info(events)")}
        if has_legacy:
            cols += ",legacy"
        rows = conn.execute(f"SELECT {cols} FROM events ORDER BY seq ASC").fetchall()
        out: list[Event] = []
        for r in rows:
            actor = str(r[4])
            verb = str(r[5])
            legacy = bool(r[11]) if has_legacy else _infer_legacy(actor, verb)
            out.append(
                Event(
                    seq=int(r[0]),
                    tick=int(r[1]),
                    day=int(r[2]),
                    intent_id=str(r[3]),
                    actor=actor,
                    verb=verb,
                    args=json.loads(r[6]),
                    effects=json.loads(r[7]),
                    outcome=str(r[8]),
                    reason=str(r[9]),
                    source=str(r[10]),
                    legacy=legacy,
                )
            )
        return out

    def set_meta(self, key: str, value: str) -> None:
        self._write_meta(key, value)

    def _write_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def event_hash(self) -> str:
        return hash_obj([e.to_canonical_dict() for e in self.read_all()])


def _infer_legacy(actor: str, verb: str) -> bool:
    """旧 run 没有 legacy 列：把写死 player:visitor 的事件标记为历史事件。"""
    return actor == "player:visitor" and verb in {
        "talk",
        "player_enter",
        "player_presence",
        "player_leave",
    }
