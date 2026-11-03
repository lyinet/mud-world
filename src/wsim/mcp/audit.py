"""MCP 审计日志。

确定性约束：不读墙钟。`time` 使用虚拟 tick + 单调 audit_seq，保证回放可复现。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditRecord:
    seq: int          # 审计序号（网关内单调）
    tick: int         # 虚拟时间（1 tick = 10 min）
    caller: str
    permission: str
    method: str
    params_digest: str
    ok: bool
    result: dict[str, Any]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "tick": self.tick,
            "caller": self.caller,
            "permission": self.permission,
            "method": self.method,
            "params_digest": self.params_digest,
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
        }


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else None
        self.records: list[AuditRecord] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: AuditRecord) -> None:
        self.records.append(record)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if self.path is not None and self.path.exists():
            return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]
        return [r.to_dict() for r in self.records]
