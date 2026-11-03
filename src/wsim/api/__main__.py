"""`python3 -m wsim.api --run player-demo --port 8020` 的启动入口。"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from .server import create_server
from .session import WorldSession


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
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    session = WorldSession(
        root,
        run_id=args.run,
        seed=args.seed,
        npc_limit=args.npcs,
        tick_step=args.tick_step,
        seed_accounts=args.seed_account,
    )
    server = create_server(session, args.host, args.port)
    host, port = server.server_address
    print(
        json.dumps(
            {
                "ok": True,
                "run_dir": str(root / "runs" / args.run),
                "listen": f"http://{host}:{port}",
                "profile_version": 1,
                "tick": session.state.tick,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
