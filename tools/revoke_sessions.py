#!/usr/bin/env python3
"""运维吊销令牌：让指定账号（或全部账号）的旧令牌立刻失效。

吊销后 `resolve_token` / `refresh` / `reconnect` 均返回 `auth_invalid_token`，
HTTP 层对应 401。审计写入同一 `service.sqlite` 的 `audit_log` 表：
吊销与会话状态在同一次事务提交，回查不依赖外部日志轮转，也会随 service 库备份。

用法：
    python3 tools/revoke_sessions.py --account u_demo
    python3 tools/revoke_sessions.py --all
    python3 tools/revoke_sessions.py --account u_demo --db runs/player-demo/service.sqlite

默认数据库取 `XKX_SERVICE_DB`，否则 `<repo>/runs/player-demo/service.sqlite`。
退出码：0 成功；1 参数/数据库错误。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="吊销账号令牌（立即 401 auth_invalid_token）")
    parser.add_argument("--account", default="", help="账号 id；与 --all 二选一")
    parser.add_argument("--all", action="store_true", help="吊销全部活动会话")
    parser.add_argument(
        "--db",
        default=os.environ.get("XKX_SERVICE_DB", ""),
        help="service.sqlite 路径；默认 runs/player-demo/service.sqlite",
    )
    args = parser.parse_args(argv)
    if not args.all and not args.account:
        parser.error("必须指定 --account 或 --all")
    db = Path(args.db) if args.db else ROOT / "runs" / "player-demo" / "service.sqlite"
    if not db.exists():
        print(f"数据库不存在：{db}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(ROOT / "src"))
    from wsim.api.auth import AccountStore  # noqa: E402

    store = AccountStore(db, iterations=1_000)
    try:
        result = store.revoke_sessions(
            account=None if args.all else args.account,
            all_accounts=args.all,
            ip="tool:revoke_sessions",
        )
    finally:
        store.close()
    if not result.get("ok"):
        print(f"吊销失败：{result}", file=sys.stderr)
        return 1
    print(
        f"revoked account={result['account']} sessions={result['revoked']} db={db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
