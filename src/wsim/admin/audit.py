"""管理审计：所有写操作的可追溯记录。

- 审计值一律先掩码（`api_key` / `token` / `secret` / `password` 及其变体）。
- 审计与配置写入共用同一事务，避免“改了没记”或“记了没改”。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .clock import now_utc

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|"
    r"authorization|credential)",
    re.IGNORECASE,
)


def is_sensitive_key(name: str) -> bool:
    """整名匹配，避免把 `per_capability_daily_tokens` 误当密钥字段。"""
    return bool(SENSITIVE_KEY_RE.fullmatch(str(name)))

MAX_VALUE_CHARS = 2000


def _mask_string(value: str) -> str:
    from .secrets import mask_secret

    return mask_secret(value)


def mask_payload(value: Any, _depth: int = 0) -> Any:
    """递归掩码敏感字段；非 JSON 值按字符串截断。"""
    if _depth > 8:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(str(key)):
                out[str(key)] = _mask_string(str(item)) if item not in (None, "") else ""
            else:
                out[str(key)] = mask_payload(item, _depth + 1)
        return out
    if isinstance(value, list):
        return [mask_payload(item, _depth + 1) for item in value]
    if isinstance(value, str):
        return value[:MAX_VALUE_CHARS]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_VALUE_CHARS]


def _dump(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(mask_payload(value), ensure_ascii=False, sort_keys=True)[:4000]


def append(
    conn: sqlite3.Connection,
    *,
    who: str,
    action: str,
    key: str,
    before: Any = None,
    after: Any = None,
    detail: Any = None,
    at: float | None = None,
) -> None:
    """写一条审计；调用方负责事务提交。"""
    conn.execute(
        "INSERT INTO admin_audit(at, who, action, key, before_json, after_json, detail_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            float(now_utc() if at is None else at),
            str(who or "unknown"),
            str(action),
            str(key),
            _dump(before),
            _dump(after),
            _dump(detail),
        ),
    )


def list_records(path: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    """按时间倒序返回审计；已落库的值仍再过一遍掩码（防历史脏数据泄漏）。"""
    limit = max(1, min(1000, int(limit)))
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT id, at, who, action, key, before_json, after_json, detail_json"
            " FROM admin_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        def _load(raw: str) -> Any:
            if not raw:
                return None
            try:
                return mask_payload(json.loads(raw))
            except (TypeError, ValueError):
                return _mask_string(str(raw))

        out.append(
            {
                "id": int(row[0]),
                "at": float(row[1]),
                "who": str(row[2]),
                "action": str(row[3]),
                "key": str(row[4]),
                "before": _load(str(row[5] or "")),
                "after": _load(str(row[6] or "")),
                "detail": _load(str(row[7] or "")),
            }
        )
    return out


def fingerprint(value: Any) -> str:
    """审计里用于对齐的短摘要（非密钥场景）。"""
    raw = json.dumps(mask_payload(value), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
