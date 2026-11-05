"""`python -m wsim.admin`：启动独立的管理服务。"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from typing import Sequence

from . import server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m wsim.admin", description="XKX 管理控制台后端")
    parser.add_argument("--root", default=".", help="仓库根目录（默认 .）")
    parser.add_argument("--run", default="", help="运行目录 id（默认 $XKX_RUN 或 player-demo）")
    parser.add_argument("--db", default="", help="service.sqlite 路径（覆盖环境变量与默认值）")
    parser.add_argument(
        "--host", default=os.environ.get("XKX_ADMIN_BIND", "127.0.0.1"), help="绑定地址"
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("XKX_ADMIN_PORT", "8031") or 8031)
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    httpd = server.run(
        root=args.root,
        run_id=args.run,
        db_path=args.db or None,
        host=args.host,
        port=args.port,
    )

    def _stop(*_args) -> None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
