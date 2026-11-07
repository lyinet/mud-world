"""对话模型旁路：只存“配音”，不属于世界状态。

硬约束：
- 本文件内容绝不进入 `events` / `state` / NPC 记忆 / `state_hash` / `event_hash` /
  `content_hash`，也不参与回放；
- 键为 `(event_seq, npc_id)`，缺失即回退到事件里的确定性模板；
- 删除本文件不影响世界，只是下一次读取退回模板。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wsim.admin.clock import now_utc

DEFAULT_FILENAME = "dialogue_aside.jsonl"


class DialogueAside:
    """追加写入的模型台词旁路；启动时加载索引，损坏行跳过。"""

    def __init__(self, run_dir: Path | str, filename: str = DEFAULT_FILENAME):
        self._path = Path(run_dir) / filename
        self._lock = threading.Lock()
        self._index: dict[tuple[int, str], dict[str, Any]] = {}
        self._count = 0
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            try:
                seq = int(row.get("event_seq"))
            except (TypeError, ValueError):
                continue
            npc = str(row.get("npc") or "")
            text = str(row.get("text") or "")
            if not npc or not text:
                continue
            self._index[(seq, npc)] = row
            self._count += 1

    def get(self, event_seq: int, npc: str) -> str | None:
        try:
            seq = int(event_seq)
        except (TypeError, ValueError):
            return None
        row = self._index.get((seq, str(npc)))
        if row is None:
            return None
        text = str(row.get("text") or "")
        return text or None

    def put(self, event_seq: int, npc: str, text: str, model: str = "") -> None:
        text = str(text or "").strip()
        npc = str(npc or "")
        if not text or not npc:
            return
        try:
            seq = int(event_seq)
        except (TypeError, ValueError):
            return
        row = {
            "event_seq": seq,
            "npc": npc,
            "text": text,
            "model": str(model or ""),
            "created_at": datetime.fromtimestamp(now_utc(), tz=timezone.utc).isoformat(timespec="seconds"),
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            self._index[(seq, npc)] = row
            self._count += 1

    def entries_after(self, event_seq: int, limit: int = 100) -> list[dict[str, Any]]:
        """返回 `event_seq > since` 的旁路条目（按 seq 升序），供迟到台词取回。"""
        try:
            since = int(event_seq)
        except (TypeError, ValueError):
            since = 0
        with self._lock:
            rows = [dict(row) for (seq, _npc), row in self._index.items() if int(seq) > since]
        rows.sort(key=lambda row: (int(row.get("event_seq") or 0), str(row.get("npc") or "")))
        return rows[: max(0, int(limit))]

    def get_row(self, event_seq: int, npc_id: str) -> dict[str, Any] | None:
        """按 `(event_seq, npc_id)` 返回整条旁路记录（含 model/created_at）。"""
        try:
            seq = int(event_seq)
        except (TypeError, ValueError):
            return None
        with self._lock:
            row = self._index.get((seq, str(npc_id)))
            return dict(row) if row else None

    def count(self) -> int:
        return self._count
