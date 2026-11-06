"""`python3 -m wsim.api --run player-demo --port 8020` 的启动入口。"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from .auth import DEFAULT_ITERATIONS, DEFAULT_RECONNECT_GRACE, DEFAULT_TOKEN_TTL, AccountStore
from .server import create_server, resolve_auth_required
from .session import WorldSession
from .persistence import ContentHashMismatch
from .world_guard import check_startup, write_world_id


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
    parser.add_argument(
        "--allow-new-world",
        action="store_true",
        help="已有账号但事件库缺失时，显式允许开新世界（默认拒绝启动，退出码 4）。",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    run_dir = root / "runs" / args.run
    service_db = Path(args.session_db) if args.session_db else run_dir / "service.sqlite"
    # 对话 provider 的 DB 路径解析依赖运行目录；在 session（动作注册表）构建前固定。
    os.environ.setdefault("XKX_RUN", args.run)
    os.environ.setdefault("XKX_ROOT", str(root))
    os.environ.setdefault("XKX_SERVICE_DB", str(service_db))
    guard = check_startup(service_db, run_dir / "events.sqlite", allow_new_world=args.allow_new_world)
    if guard is not None:
        # 固定契约：单行 JSON 到 stderr；退出码 4=丢库、5=库损坏；不监听、不写事件。
        print(json.dumps(guard, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)
        return int(guard["exit_code"])
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
    # 首次创世落“世界身份”标记；既有世界不覆盖（标记只在造新世界时写）。
    world_state = "existing" if session.replayed else "new"
    if world_state == "new":
        write_world_id(
            session.run_dir,
            run_id=args.run,
            content_hash=session.content.content_hash,
            seed=args.seed,
        )
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
    # 世界自走与玩家在同一进程、同一 Writer、同一份 events.sqlite；
    # XKX_AMBIENT=0 时不启动，行为与旧版一致。
    ambient_info = session.start_ambient_from_env()
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
                "world": world_state,
                "allow_new_world": bool(args.allow_new_world),
                "ambient": (ambient_info or {}).get("ambient", "stopped"),
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
