"""W1 运维 CLI：清理僵尸会话（只动 service.sqlite，不碰事件存档）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .auth import DEFAULT_RECONNECT_GRACE, AccountStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="xkx-world 服务运维")
    sub = parser.add_subparsers(dest="command", required=True)

    purge = sub.add_parser("purge-zombies", help="把超过重连宽限/令牌过期的会话置为 expired")
    purge.add_argument("--run-dir", required=True, help="<root>/runs/<run> 目录")
    purge.add_argument("--session-db", default="", help="默认 <run-dir>/service.sqlite")
    purge.add_argument(
        "--reconnect-grace", type=int, default=DEFAULT_RECONNECT_GRACE,
        help="断线多久算僵尸（秒）",
    )
    args = parser.parse_args(argv)

    if args.command != "purge-zombies":  # pragma: no cover - argparse 已限制
        parser.error("unknown command")

    run_dir = Path(args.run_dir).resolve()
    db = Path(args.session_db) if args.session_db else run_dir / "service.sqlite"
    if not db.exists():
        print(
            json.dumps(
                {"ok": False, "error": "session_db_missing", "session_db": str(db)},
                ensure_ascii=False,
            )
        )
        return 2
    store = AccountStore(db, reconnect_grace=args.reconnect_grace)
    try:
        purged = store.purge_zombies()
    finally:
        store.close()
    print(
        json.dumps(
            {
                "ok": True,
                "purged": purged,
                "session_db": str(db),
                "reconnect_grace_seconds": int(args.reconnect_grace),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
