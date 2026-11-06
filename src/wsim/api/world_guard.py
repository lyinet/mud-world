"""启动前世界身份检查：防止卷挂错/误删事件库导致静默清档。

判定规则：
- `service.sqlite` 里已有账号（>0）而 `events.sqlite` 缺失或 0 条事件 → 拒绝启动
  （退出码 4 `events_missing`），除非显式 `--allow-new-world`。
- `events.sqlite` 存在但 `PRAGMA integrity_check` 不为 `ok` → 拒绝启动
  （退出码 5 `events_corrupt`），`--allow-new-world` 也不放行（损坏库不能当新世界）。
- 首次启动（无 service.sqlite / 无账号）正常创世。

首次创世时在 run 目录写 `world_id.json` 作为世界身份持久标记（run_id / content_hash /
seed / created_at），便于卷审计与健康检查区分“新世界 / 既有世界”。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

EXIT_EVENTS_MISSING = 4
EXIT_EVENTS_CORRUPT = 5

WORLD_ID_FILE = "world_id.json"

# 启动守卫已经跑过一次全库校验时，把结果留给 `HealthProbe.run_integrity()` 复用，
# 避免同一次启动重复跑两遍 `PRAGMA integrity_check`。键为事件库绝对路径。
_INTEGRITY_RESULTS: dict[str, str] = {}


def cached_integrity(events_db: Path | str) -> str | None:
    """返回本进程内启动守卫跑出的 integrity 结果；没跑过返回 None。"""
    try:
        key = str(Path(events_db).resolve())
    except OSError:
        return None
    return _INTEGRITY_RESULTS.get(key)


def account_count(service_db: Path) -> int:
    """只读统计账号数；文件不存在或无 accounts 表都按 0（不建库、不建表）。"""
    if not service_db.exists():
        return 0
    try:
        conn = sqlite3.connect(str(service_db))
    except sqlite3.Error:
        return 0
    try:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "users" not in tables:
            return 0
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def events_info(events_db: Path) -> dict[str, Any]:
    """事件库状态：存在/可读/事件数/integrity。缺失或打不开都收敛为不可用。"""
    info = {"exists": events_db.exists(), "readable": False, "events": 0, "integrity": ""}
    if not info["exists"]:
        return info
    try:
        conn = sqlite3.connect(str(events_db))
    except sqlite3.Error as exc:
        info["integrity"] = f"open_error: {exc}"
        return info
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        info["integrity"] = str(row[0]) if row else "unknown"
        _INTEGRITY_RESULTS[str(events_db.resolve())] = info["integrity"]
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "events" in tables:
            max_seq = conn.execute("SELECT MAX(seq) FROM events").fetchone()
            info["events"] = int(max_seq[0] or 0) if max_seq else 0
        info["readable"] = True
    except sqlite3.DatabaseError as exc:
        info["integrity"] = f"error: {exc}"
        info["readable"] = False
    finally:
        conn.close()
    return info


def check_startup(
    service_db: Path, events_db: Path, allow_new_world: bool = False
) -> dict[str, Any] | None:
    """返回 None 表示可启动；否则返回要输出的错误 payload（含 exit_code）。"""
    info = events_info(events_db)
    if info["exists"] and info["integrity"] and info["integrity"] != "ok":
        return {
            "ok": False,
            "code": "events_corrupt",
            "exit_code": EXIT_EVENTS_CORRUPT,
            "reason": (
                f"事件库 integrity_check 失败（{info['integrity']}）；"
                "拒绝启动，请从备份恢复 events.sqlite。"
            ),
            "events_db": str(events_db),
        }
    accounts = account_count(service_db)
    if accounts > 0 and (not info["exists"] or info["events"] <= 0):
        if allow_new_world:
            return None
        return {
            "ok": False,
            "code": "events_missing",
            "exit_code": EXIT_EVENTS_MISSING,
            "reason": (
                "检测到已有账号数据但事件库缺失，疑似卷挂载错误；"
                "如需开新世界请显式 --allow-new-world"
            ),
            "service_db": str(service_db),
            "events_db": str(events_db),
            "accounts": accounts,
        }
    return None


def write_world_id(
    run_dir: Path, *, run_id: str, content_hash: str, seed: int
) -> Path:
    """首次创世写入世界身份标记；已存在且是既有世界时不覆盖（调用方决定）。"""
    payload = {
        "run_id": str(run_id),
        "content_hash": str(content_hash),
        "seed": int(seed),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path = Path(run_dir) / WORLD_ID_FILE
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_world_id(run_dir: Path) -> dict[str, Any] | None:
    path = Path(run_dir) / WORLD_ID_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
