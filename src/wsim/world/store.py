"""SQLite 事件日志。append-only，事件是唯一事实源。

表结构刻意保持扁平：seq / tick / day / intent_id / actor / verb / args /
effects / outcome / reason / source。任何修正都靠追加补偿事件，不 UPDATE/DELETE。

写路径约束（soak 实测）：WAL 自动 checkpoint 原先在 `commit()` 里同步执行，
磁盘繁忙时单次 commit 会卡 200–580ms；因为 session 写锁跨整个请求，
会把所有写请求排成 write.max 秒级尖刺。这里关闭自动 checkpoint，
改由后台线程每 N 秒做一次 `wal_checkpoint(PASSIVE)`：PASSIVE 不等写者，
commit 只追加 WAL 页，不再承担回写/fsync。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from ..core.canonical import dumps, hash_obj
from ..core.models import Event

CHECKPOINT_INTERVAL_S = max(0.0, float(os.environ.get("XKX_SQLITE_CHECKPOINT_INTERVAL", "2.0")))

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
CREATE INDEX IF NOT EXISTS idx_events_intent ON events(intent_id);
CREATE INDEX IF NOT EXISTS idx_events_actor_tick ON events(actor, tick);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class EventStore:
    def __init__(self, path: str | Path = ":memory:", check_same_thread: bool = True):
        self.path = str(path)
        self._ck_stop = threading.Event()
        self._ck_thread: threading.Thread | None = None
        self._ck_conn: sqlite3.Connection | None = None
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # API 服务器在独立线程处理请求；调用方必须自行串行化（WorldSession 持锁）。
        self.conn = sqlite3.connect(self.path, check_same_thread=check_same_thread)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        # 旧 run 的 sqlite 没有 legacy 列：不 ALTER（保持只读兼容），读取时按
        # actor/verb 推断并标记；新库直接带列。
        cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(events)")}
        self._has_legacy = "legacy" in cols
        self._cached_count: int | None = None
        # checkpoint 归因：后台 PASSIVE 回写耗时。若某窗口写延迟尖刺与这里
        # 的 ck_ms_max 同步，说明停顿来自 WAL 回写而非锁内自走。
        self._ck_runs = 0
        self._ck_ms_total = 0.0
        self._ck_ms_max = 0.0
        self._ck_busy_runs = 0
        self._ck_last: dict[str, int] = {}
        if self.path != ":memory:" and CHECKPOINT_INTERVAL_S > 0:
            self._start_checkpointer()

    # -------------------------------------------------------------- checkpoint
    def _start_checkpointer(self) -> None:
        """把 WAL 回写挪出 commit 路径；写线程只追加 WAL 帧。"""
        try:
            self._ck_conn = sqlite3.connect(self.path, check_same_thread=False)
        except sqlite3.Error:
            self._ck_conn = None
            return
        # 只有后备 connection 可用时才关自动 checkpoint，避免 WAL 无人回收。
        self.conn.execute("PRAGMA wal_autocheckpoint=0")
        self._ck_thread = threading.Thread(
            target=self._checkpoint_loop, name="wal-checkpoint", daemon=True
        )
        self._ck_thread.start()

    def _checkpoint_loop(self) -> None:
        while not self._ck_stop.wait(CHECKPOINT_INTERVAL_S):
            if self._ck_conn is None:
                return
            t0 = time.clock_gettime(time.CLOCK_MONOTONIC)
            try:
                # PASSIVE 不等写者/读者；拿不到帧就下轮再来，绝不阻塞 commit。
                rows = self._ck_conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
                dt = (time.clock_gettime(time.CLOCK_MONOTONIC) - t0) * 1000.0
                self._ck_runs += 1
                self._ck_ms_total += dt
                if dt > self._ck_ms_max:
                    self._ck_ms_max = dt
                if rows:
                    busy, log_frames, ck_frames = (int(rows[0][0]), int(rows[0][1]), int(rows[0][2]))
                    self._ck_last = {"busy": busy, "log": log_frames, "checkpointed": ck_frames}
                    if busy:
                        self._ck_busy_runs += 1
            except sqlite3.Error:
                self._ck_stop.wait(1.0)

    def ck_stats(self) -> dict[str, float | int | dict[str, int]]:
        """checkpoint 计数与耗时（只读快照，归因用）。"""
        return {
            "runs": self._ck_runs,
            "ms_total": round(self._ck_ms_total, 3),
            "ms_max": round(self._ck_ms_max, 3),
            "busy_runs": self._ck_busy_runs,
            "last": dict(self._ck_last),
        }

    def _stop_checkpointer(self) -> None:
        self._ck_stop.set()
        thread = self._ck_thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._ck_thread = None
        if self._ck_conn is not None:
            try:
                self._ck_conn.close()
            except sqlite3.Error:
                pass
            self._ck_conn = None
>>>>>>> origin/feat/xkx-event-retention

    def close(self) -> None:
        self._ck_stop.set()
        thread = self._ck_thread
        if thread is not None:
            thread.join(timeout=2.0)
        if self._ck_conn is not None:
            try:
                self._ck_conn.close()
            except sqlite3.Error:
                pass
            self._ck_conn = None
        self.conn.close()

    def count(self) -> int:
        """全史最后 seq（裁剪后仍单调）。

        未裁剪时 `MAX(seq) == COUNT(*)`；裁剪只删 seq 前缀，行数会变小但写路径
        `count()+1` 必须继续递增，因此这里取 MAX(seq)。空表返回 0（创世语义）。
        """
        if self._cached_count is None:
            row = self.conn.execute("SELECT MAX(seq) FROM events").fetchone()
            self._cached_count = int(row[0]) if row and row[0] is not None else 0
        return self._cached_count

    def max_seq(self) -> int:
        row = self.conn.execute("SELECT MAX(seq) FROM events").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def min_seq(self) -> int:
        row = self.conn.execute("SELECT MIN(seq) FROM events").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def iter_range(self, start_seq: int, end_seq: int):
        """按 seq 升序流式读取 `[start_seq, end_seq)`；不全量载入内存。"""
        cur = self.conn.execute(
            f"SELECT {self._select_cols()} FROM events"
            " WHERE seq>=? AND seq<? ORDER BY seq ASC",
            (int(start_seq), int(end_seq)),
        )
        for row in cur:
            yield self._row_to_event(row)

    def delete_through(self, seq: int) -> int:
        """删除 `seq <= N` 的前缀事件，单事务；返回删除行数。"""
        cur = self.conn.execute("DELETE FROM events WHERE seq<=?", (int(seq),))
        self.conn.commit()
        return int(cur.rowcount or 0)

    def checkpoint_truncate(self) -> tuple[int, int, int]:
        """把 WAL 截断到 0（在线裁剪后回收 WAL 体积）；不阻塞读者。"""
        row = self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is None:
            return (0, 0, 0)
        return (int(row[0]), int(row[1]), int(row[2]))

    def vacuum_offline(self) -> None:
        """离线回收磁盘（VACUUM 需要独占锁）；调用方必须保证没有其他写入者。

        会停掉后台 checkpoint 线程，之后本实例只剩主连接可用，适合 CLI 收尾。
        """
        self._stop_checkpointer()
        self.conn.execute("VACUUM")
        self.conn.commit()
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def append(self, event: Event) -> None:
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

    def _select_cols(self) -> str:
        cols = "seq,tick,day,intent_id,actor,verb,args,effects,outcome,reason,source"
        if self._has_legacy:
            cols += ",legacy"
        return cols

    def _row_to_event(self, r: tuple) -> Event:
        actor = str(r[4])
        verb = str(r[5])
        legacy = bool(r[11]) if self._has_legacy else _infer_legacy(actor, verb)
        return Event(
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

    def read_all(self) -> list[Event]:
        rows = self.conn.execute(
            f"SELECT {self._select_cols()} FROM events ORDER BY seq ASC"
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def event_by_seq(self, seq: int) -> Event | None:
        """按主键取单条事件（O(log n)）；供幂等缓存按 seq 回读，避免内存常驻 Event。"""
        row = self.conn.execute(
            f"SELECT {self._select_cols()} FROM events WHERE seq=?", (int(seq),)
        ).fetchone()
        return None if row is None else self._row_to_event(row)

    def find_intent(self, intent_id: str, actor: str) -> Event | None:
        """定向查一条意图事件；ASC 与 `read_all()` 首次命中语义一致。"""
        row = self.conn.execute(
            f"SELECT {self._select_cols()} FROM events"
            " WHERE intent_id=? AND actor=? ORDER BY seq ASC LIMIT 1",
            (str(intent_id), str(actor)),
        ).fetchone()
        return None if row is None else self._row_to_event(row)

    def events_for_actor_since(
        self, actor: str, since_tick: int, limit: int | None = None
    ) -> list[Event]:
        """取某 actor 在 `since_tick` 之后的事件（seq 升序）；走 (actor,tick) 索引。"""
        sql = (
            f"SELECT {self._select_cols()} FROM events"
            " WHERE actor=? AND tick>? ORDER BY seq ASC"
        )
        params: tuple = (str(actor), int(since_tick))
        if limit is not None:
            sql += " LIMIT ?"
            params += (int(limit),)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def event_hash(self) -> str:
        # 离线 / 关停 final_summary 专用：全量读日志，禁止请求路径调用。
        return hash_obj([e.to_canonical_dict() for e in self.read_all()])


def _infer_legacy(actor: str, verb: str) -> bool:
    """旧 run 没有 legacy 列：把写死 player:visitor 的事件标记为历史事件。"""
    return actor == "player:visitor" and verb in {
        "talk",
        "player_enter",
        "player_presence",
        "player_leave",
    }
