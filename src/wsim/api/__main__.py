"""`python3 -m wsim.api --run player-demo --port 8020` 的启动入口。"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from .auth import DEFAULT_ITERATIONS, DEFAULT_RECONNECT_GRACE, DEFAULT_TOKEN_TTL, AccountStore
from .server import create_server, resolve_auth_required
from .session import WorldSession
from .persistence import ContentHashMismatch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="玩家 HTTP API（单写者 + 只读快照）")
    parser.add_argument("--root", default=".", help="仓库根目录（含 content/ 与 runs/）")
    parser.add_argument("--run", default="player-demo", help="可写 run 目录名，位于 <root>/runs/ 下")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--npcs", type=int, default=0, help="0 表示全部 NPC")
    parser.add_argument("--tick-step", type=int, default=6, help="每个动作推进的 tick 数")
    parser.add_argument(
        "--seed-account",
        action="append",
        default=[],
        help="创世预置账号（可重复）；默认 u_demo。其他账号首次 enter 由 player_endowment 发放。",
    )
    parser.add_argument(
        "--auth",
        choices=("auto", "on", "off"),
        default="auto",
        help="auto：存在 deploy/service.yaml 时强制鉴权；on/off 为显式覆盖。",
    )
    parser.add_argument(
        "--session-db",
        default="",
        help="账号/令牌 sqlite 路径；默认 <run_dir>/service.sqlite。",
    )
    parser.add_argument("--token-ttl", type=int, default=DEFAULT_TOKEN_TTL, help="令牌有效期（秒）。")
    parser.add_argument(
        "--reconnect-grace", type=int, default=DEFAULT_RECONNECT_GRACE,
        help="断线重连宽限（秒）；超时后按新会话处理但存档仍在。",
    )
    parser.add_argument("--auth-iterations", type=int, default=DEFAULT_ITERATIONS, help="PBKDF2 迭代次数。")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        session = WorldSession(
            root,
            run_id=args.run,
            seed=args.seed,
            npc_limit=args.npcs,
            tick_step=args.tick_step,
            seed_accounts=args.seed_account,
        )
    except ContentHashMismatch as exc:
        # 固定契约：退出码 3 + 单行 JSON 错误到 stderr；不监听、不写事件。
        print(exc.log_line(), file=sys.stderr, flush=True)
        return exc.exit_code
    auth_required = resolve_auth_required(root, args.auth)
    accounts: AccountStore | None = None
    if auth_required:
        session_db = Path(args.session_db) if args.session_db else session.run_dir / "service.sqlite"
        accounts = AccountStore(
            session_db,
            iterations=args.auth_iterations,
            token_ttl=args.token_ttl,
            reconnect_grace=args.reconnect_grace,
        )
    server = create_server(
        session, args.host, args.port, accounts=accounts, auth_required=auth_required
    )
    host, port = server.server_address
    print(
        json.dumps(
            {
                "ok": True,
                "run_dir": str(root / "runs" / args.run),
                "listen": f"http://{host}:{port}",
                "profile_version": 1,
                "tick": session.state.tick,
                "auth_required": auth_required,
                "auth_mode": args.auth,
                "docs": "docs/player-api.md",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    stop = threading.Event()

    def _shutdown(_signum=None, _frame=None):
        if stop.is_set():
            return
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        session.close()
        if accounts is not None:
            accounts.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
