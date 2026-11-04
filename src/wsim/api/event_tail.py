"""写路径的常量时间事件尾部缓存。

`WorldSession` 每次写操作只需要“刚追加的那条事件”，旧实现为此调用
`EventStore.read_all()`（O(n)）；事件数上万时单次 150ms+，写吞吐被日志长度锁死。
本模块只保存最后一条事件及其 `(seq, tick, hash)`，写路径按 seq 取单行或直接读缓存。

约束：
- 事务（sqlite commit）成功后由调用方显式 `observe()` 同步，绝不先于落盘更新。
- 进程启动/崩溃恢复时从 sqlite 的 `seq` 最大行重建，不把状态只留在内存。
- `EventStore.read_all()` 保留给审计、回放、导出等只读路径，不参与写路径。
"""
from __future__ import annotations

import json
from typing import Any

from ..core.canonical import hash_obj
from ..core.models import Event
from ..world.store import EventStore

_COLS = "seq,tick,day,intent_id,actor,verb,args,effects,outcome,reason,source"


def _event_from_row(row: Any, has_legacy: bool) -> Event:
    actor = str(row[4])
    verb = str(row[5])
    legacy = bool(row[11]) if has_legacy else False
    return Event(
        seq=int(row[0]),
        tick=int(row[1]),
        day=int(row[2]),
        intent_id=str(row[3]),
        actor=actor,
        verb=verb,
        args=json.loads(row[6]),
        effects=json.loads(row[7]),
        outcome=str(row[8]),
        reason=str(row[9]),
        source=str(row[10]),
        legacy=legacy,
    )


class EventTail:
    """最后一条事件的缓存；`last_seq` / `last_tick` / `last_hash` 供写路径直接读取。"""

    def __init__(self, store: EventStore):
        self.store = store
        cols = {str(r[1]) for r in store.conn.execute("PRAGMA table_info(events)")}
        self._has_legacy = "legacy" in cols
        self._select = _COLS + (",legacy" if self._has_legacy else "")
        self._event: Event | None = None
        self.last_seq = 0
        self.last_tick = 0
        self.last_hash = ""
        self.reload_from_sqlite()

    # ------------------------------------------------------------------ load
    def reload_from_sqlite(self) -> Event | None:
        """从 sqlite 重建尾部缓存；空库时返回 None。"""
        row = self.store.conn.execute(
            f"SELECT {self._select} FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            self._event = None
            self.last_seq = 0
            self.last_tick = 0
            self.last_hash = ""
            return None
        event = _event_from_row(row, self._has_legacy)
        self._cache(event)
        return event

    def observe(self, event: Event) -> None:
        """单写者事务提交成功后调用；只接受更新的 seq，避免旧事件覆盖缓存。"""
        if self._event is not None and int(event.seq) <= int(self._event.seq):
            return
        self._cache(event)

    def event_at(self, seq: int) -> Event | None:
        """按主键取单条事件（O(log n)），用于取本次事务刚追加的那条。"""
        row = self.store.conn.execute(
            f"SELECT {self._select} FROM events WHERE seq=?", (int(seq),)
        ).fetchone()
        if row is None:
            return None
        event = _event_from_row(row, self._has_legacy)
        self._cache(event)
        return event

    def last_event(self) -> Event | None:
        return self._event

    # ---------------------------------------------------------------- helpers
    def _cache(self, event: Event) -> None:
        self._event = event
        self.last_seq = int(event.seq)
        self.last_tick = int(event.tick)
        self.last_hash = hash_obj(event.to_canonical_dict())
