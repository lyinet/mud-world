"""SQLite 事件日志。append-only，事件是唯一事实源。

表结构刻意保持扁平：seq / tick / day / intent_id / actor / verb / args /
effects / outcome / reason / source。任何修正都靠追加补偿事件，不 UPDATE/DELETE。
"""
from __future__ import annotations

import json
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


class EventStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        # 旧 run 的 sqlite 没有 legacy 列：不 ALTER（保持只读兼容），读取时按
        # actor/verb 推断并标记；新库直接带列。
        cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(events)")}
        self._has_legacy = "legacy" in cols
        self._cached_count: int | None = None

    def close(self) -> None:
        self.conn.close()

    def count(self) -> int:
        if self._cached_count is None:
            row = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()
            self._cached_count = int(row[0])
        return self._cached_count

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

    def read_all(self) -> list[Event]:
        cols = "seq,tick,day,intent_id,actor,verb,args,effects,outcome,reason,source"
        if self._has_legacy:
            cols += ",legacy"
        rows = self.conn.execute(
            f"SELECT {cols} FROM events ORDER BY seq ASC"
        ).fetchall()
        out: list[Event] = []
        for r in rows:
            actor = str(r[4])
            verb = str(r[5])
            legacy = bool(r[11]) if self._has_legacy else _infer_legacy(actor, verb)
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
