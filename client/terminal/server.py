"""经典 MUD 风格的终端客户端（Python 标准库 HTTP 服务）。

启动（在 world-sim/ 目录下）：
    python3 client/terminal/server.py --run runs/p3a-clean-72d --port 8010

边界：
- 读：run 目录只做只读解析，world 状态经 MCPGateway.world.read_snapshot；
- 写：只有 talk 的 world.propose_intent 进入内存 outbox，不绑定 Writer，
  不写 events.sqlite，不新增事件；
- 不修改 src/ ui/ config/ tests/ content/ sim/ tools/ reports/ runs/。

这只是个“壳”：世界仍由主引擎驱动，终端不推进时间、不改状态。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.dont_write_bytecode = True  # 不往 src/ 或本目录写 __pycache__

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from commands import TerminalSession  # noqa: E402
from runview import RunView, clock_label  # noqa: E402

NOTICE = "这是只读终端外壳：世界仍由主引擎驱动，终端不推进时间、不改状态。"
MAX_BODY = 8 * 1024


class TerminalHandler(BaseHTTPRequestHandler):
    view: RunView
    session: TerminalSession
    lock: threading.RLock

    server_version = "WorldSimTerminal/0.1"

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._static_index()
                return
            if path == "/api/health":
                with self.lock:
                    self._json(200, self._health())
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            self._json(404, {"ok": False, "error": f"未知接口：{path}"})
        except Exception as exc:  # 不让浏览器白屏
            self._json(500, {"ok": False, "error": f"服务端错误：{type(exc).__name__}: {exc}"})

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/cmd":
            self._json(404, {"ok": False, "error": f"未知接口：{parsed.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("请求体为空或过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
            line = str(payload.get("line") or "")
            with self.lock:
                result = self.session.handle(line)
                payload_out = {
                    "ok": result.kind != "error",
                    "kind": result.kind,
                    "lines": result.lines,
                    "meta": result.meta or self._meta(),
                    "notice": NOTICE,
                }
            self._json(200, payload_out)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json(400, {"ok": False, "kind": "error", "lines": [f"请求不合法：{exc}"], "meta": self._meta()})
        except Exception as exc:
            self._json(
                500,
                {
                    "ok": False,
                    "kind": "error",
                    "lines": [f"服务端错误：{type(exc).__name__}: {exc}"],
                    "meta": self._meta(),
                },
            )

    # --------------------------------------------------------------- helpers
    def _meta(self) -> dict:
        return {"prompt": self.session.prompt, "position": self.session.position}

    def _health(self) -> dict:
        error = self.view.error
        return {
            "ok": error is None,
            "run": self.view.run_label,
            "run_id": self.view.summary.get("run_id"),
            "days": self.view.days,
            "view_day": self.view.view_day,
            "tick": self.view.tick,
            "clock": clock_label(self.view.tick),
            "events": self.view.events_count,
            "truncated": self.view.truncated,
            "state_hash": self.view.state_hash_value,
            "world_hash_expected": self.view.world_hash_expected,
            "state_hash_match": self.view.state_hash_match,
            "prompt": self.session.prompt,
            "outbox": self.view.outbox_count,
            "error": error.message if error else "",
            "hint": error.hint if error else "",
            "notice": NOTICE,
        }

    def _static_index(self) -> None:
        file_path = HERE / "index.html"
        if not file_path.exists():
            self._json(500, {"ok": False, "error": "client/terminal/index.html 不存在"})
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[terminal] %s - %s\n" % (self.address_string(), fmt % args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="世界审计器 · 经典 MUD 终端客户端（只读）")
    parser.add_argument("--run", default="runs/p3a-clean-72d", help="run 目录（默认 runs/p3a-clean-72d）")
    parser.add_argument("--port", type=int, default=8010, help="端口（默认 8010）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--day", type=int, default=None, help="回放到第 N 天结束（默认整段 run）")
    parser.add_argument("--start", default="market", help="客户端初始地点（默认 market）")
    args = parser.parse_args(argv)

    view = RunView(args.run, day=args.day)
    session = TerminalSession(view, start=args.start)

    if view.error is not None:
        print(f"[terminal] run 不可用：{view.error.message}", file=sys.stderr)
        print(f"[terminal] 提示：{view.error.hint}", file=sys.stderr)
    else:
        print(f"[terminal] run={view.run_label}", file=sys.stderr)
        print(
            f"[terminal] 回放 {view.events_count} 条事件到 {clock_label(view.tick)}"
            f"（第 {view.view_day} 天结束）",
            file=sys.stderr,
        )
        match = view.state_hash_match
        print(
            f"[terminal] state_hash={view.state_hash_value[:16]}… "
            f"world_hash={view.world_hash_expected[:16]}… match={match}",
            file=sys.stderr,
        )
        print(f"[terminal] 起始地点={view.loc_name(session.position)}", file=sys.stderr)

    TerminalHandler.view = view
    TerminalHandler.session = session
    TerminalHandler.lock = threading.RLock()

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), TerminalHandler)
    except OSError as exc:
        print(f"[terminal] 无法监听 {args.host}:{args.port}：{exc}", file=sys.stderr)
        return 2
    httpd.daemon_threads = True
    print(f"[terminal] 打开 http://{args.host}:{args.port}/  （Ctrl+C 停止）", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[terminal] 已停止", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
