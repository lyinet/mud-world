"""最小可玩界面的 HTTP 服务端（只用 Python 标准库）。

启动：
    python3 ui/server.py --run runs/p2b-72d --port 8000

边界：
- 读：全部通过 ui/adapter.py -> MCPGateway.world.read_snapshot；
- 写：对话只通过 MCPGateway.world.propose_intent，进 outbox，不绑定 Writer；
- 不读 SQLite、不修改 run 目录、不修改 src/ content/ tests/。
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from adapter import NOTICE, SnapshotError, WorldUIAdapter

UI_DIR = Path(__file__).resolve().parent


class UIHandler(BaseHTTPRequestHandler):
    adapter: WorldUIAdapter

    server_version = "WorldSimUI/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._static_index()
                return
            if path == "/api/health":
                self._json(200, self._health())
                return
            if path == "/api/overview":
                self._json(200, self.adapter.overview())
                return
            if path.startswith("/api/npc/"):
                npc_id = unquote(path[len("/api/npc/"):])
                self._json(200, self.adapter.npc_detail(npc_id))
                return
            if path == "/api/timeline":
                query = parse_qs(parsed.query)
                day_raw = (query.get("day") or [""])[0]
                npc = (query.get("npc") or [""])[0]
                location = (query.get("location") or [""])[0]
                limit_raw = (query.get("limit") or [""])[0]
                day = int(day_raw) if day_raw else None
                limit = int(limit_raw) if limit_raw else 500
                self._json(200, self.adapter.timeline(day=day, npc=npc, location=location, limit=limit))
                return
            self._json(404, {"ok": False, "error": f"未知接口：{path}"})
        except SnapshotError as exc:
            self._run_unavailable(exc)
        except KeyError as exc:
            self._json(404, {"ok": False, "error": f"NPC 不存在：{exc.args[0]}"})
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # 不让浏览器白屏
            self._json(500, {"ok": False, "error": f"服务端错误：{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/dialogue":
            self._json(404, {"ok": False, "error": f"未知接口：{parsed.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 64 * 1024:
                raise ValueError("请求体为空或过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
            npc = str(payload.get("npc") or "")
            message = str(payload.get("message") or "")
            self._json(200, self.adapter.dialogue(npc, message))
        except SnapshotError as exc:
            self._run_unavailable(exc)
        except KeyError as exc:
            self._json(404, {"ok": False, "error": f"NPC 不存在：{exc.args[0]}"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "error": f"请求不合法：{exc}"})
        except Exception as exc:
            self._json(500, {"ok": False, "error": f"服务端错误：{type(exc).__name__}: {exc}"})

    # ---------------------------------------------------------------- helpers
    def _health(self) -> dict:
        error = self.adapter.error
        return {
            "ok": error is None,
            "run": self.adapter.run_label,
            "state_loaded": error is None,
            "error": error.message if error else "",
            "hint": error.hint if error else "",
            "notice": NOTICE,
        }

    def _static_index(self) -> None:
        file_path = UI_DIR / "index.html"
        if not file_path.exists():
            self._json(500, {"ok": False, "error": "ui/index.html 不存在"})
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

    def _run_unavailable(self, exc: SnapshotError) -> None:
        self._json(
            503,
            {
                "ok": False,
                "error": exc.message,
                "hint": exc.hint,
                "run": self.adapter.run_label,
                "notice": "run 目录不可用。请检查路径，或换一个包含 summary.json / events.jsonl / lod.json 的 run。",
            },
        )

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[ui] %s - %s\n" % (self.address_string(), fmt % args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="世界审计器最小可玩界面")
    parser.add_argument("--run", default="runs/p2b-72d", help="run 目录（默认 runs/p2b-72d）")
    parser.add_argument("--port", type=int, default=8000, help="端口（默认 8000）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    args = parser.parse_args(argv)

    adapter = WorldUIAdapter(args.run)
    UIHandler.adapter = adapter

    if adapter.error is not None:
        print(f"[ui] run 不可用：{adapter.error.message}", file=sys.stderr)
        print(f"[ui] 提示：{adapter.error.hint}", file=sys.stderr)
    else:
        print(f"[ui] run={adapter.run_label}")
        print(f"[ui] 状态已从事件日志回放：{len(adapter.events)} 条事件，hash_ok={adapter.state_hash_match}", file=sys.stderr)

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), UIHandler)
    except OSError as exc:
        print(f"[ui] 无法监听 {args.host}:{args.port}：{exc}", file=sys.stderr)
        return 2
    httpd.daemon_threads = True
    print(f"[ui] 打开 http://{args.host}:{args.port}/  （Ctrl+C 停止）", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[ui] 已停止", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
