#!/usr/bin/env python3
"""显式运维入口：对单个 run 的事件库跑一次全库 `PRAGMA integrity_check`。

这是“按需全量校验”的唯一工具入口，**不挂在 `/api/health` 请求路径上**，
也不持有服务会话的写锁；只开独立只读连接。

用法：
    python3 tools/check_integrity.py --root . --run <RUN_ID>

输出单行 JSON：
    {"ok": bool, "result": str, "elapsed_ms": float, "run": str, "db": str}

退出码：0 = integrity ok；1 = 校验失败 / 库缺失 / 打不开。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path


def check(root: str | Path, run: str) -> dict[str, object]:
    db = (Path(root) / "runs" / str(run) / "events.sqlite").resolve()
    started = time.perf_counter()
    if not db.exists():
        return {
            "ok": False,
            "result": "db_missing",
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "run": str(run),
            "db": str(db),
        }
    try:
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=10.0)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result = str(row[0]) if row else "unknown"
        finally:
            conn.close()
    except Exception as exc:  # 损坏/权限/路径问题都收敛为一次可诊断的失败
        result = f"error: {type(exc).__name__}: {exc}"
    return {
        "ok": result == "ok",
        "result": result,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "run": str(run),
        "db": str(db),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按需校验 events.sqlite（只读，全库）")
    parser.add_argument("--root", default=".", help="仓库根目录（含 runs/）")
    parser.add_argument("--run", required=True, help="runs/ 下的 run 目录名")
    args = parser.parse_args(argv)
    payload = check(args.root, args.run)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
