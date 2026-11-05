#!/usr/bin/env python3
"""运维解锁：清理登录失败计数与锁定记录（`(ip, account)` 维度）。

触发条件是同一 `(ip, account)` 在 5 分钟内失败 5 次，随后按 15 分钟 → 1 小时 →
24 小时指数退避；锁定期内即使口令正确也返回 429 `too_many_attempts`。
本工具用于运维确认后人工解锁，不改变账号口令与会话。

用法：
    python3 tools/unlock_account.py --account u_demo
    python3 tools/unlock_account.py --account u_demo --ip 10.0.0.8
    python3 tools/unlock_account.py --all

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
    parser = argparse.ArgumentParser(description="清理登录失败计数与锁定")
    parser.add_argument("--account", default="", help="账号 id；与 --all 二选一")
    parser.add_argument("--ip", default="", help="只解锁该来源 IP（可选）")
    parser.add_argument("--all", action="store_true", help="清理全部锁定记录")
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
        cleared = store.clear_login_locks(
            account=None if args.all else args.account, ip=args.ip or None
        )
    finally:
        store.close()
    scope = "all" if args.all else args.account
    if args.ip:
        scope += f" ip={args.ip}"
    print(f"unlocked scope={scope} locks={cleared} db={db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
