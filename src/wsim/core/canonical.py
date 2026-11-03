"""规范化 JSON 与哈希。

浮点统一 round(6)，避免不同路径产生 1e-17 级差异影响哈希；
容器按键排序，保证跨进程一致。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize(obj: Any) -> Any:
    if isinstance(obj, float):
        v = round(obj, 6)
        return 0.0 if v == 0 else v
    if isinstance(obj, dict):
        return {str(k): normalize(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [normalize(v) for v in obj]
    if isinstance(obj, set):
        return [normalize(v) for v in sorted(obj, key=str)]
    return obj


def dumps(obj: Any) -> str:
    return json.dumps(normalize(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_text(dumps(obj))
