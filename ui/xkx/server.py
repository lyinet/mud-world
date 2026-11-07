"""xkx 文字 MUD 静态页 + API 反向代理（只用标准库）。

用法：python3 ui/xkx/server.py --port 8011 --upstream http://127.0.0.1:8041
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import posixpath
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class XKXHandler(BaseHTTPRequestHandler):
    server_version = "xkx-ui/1.0"
    static_dir: Path
    upstream: str

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json(404, {"ok": False, "reason_text": "页面不存在。"})
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self, method: str, raw_body: bytes | None) -> None:
        path = self.path
        url = self.upstream.rstrip("/") + path
        req = urllib.request.Request(url, data=raw_body, method=method)
        ctype = self.headers.get("Content-Type")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(resp.status)
                self.send_header(
                    "Content-Type",
                    resp.headers.get("Content-Type", "application/json; charset=utf-8"),
                )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self._json(
                502,
                {
                    "ok": False,
                    "reject_reason": "upstream_unreachable",
                    "reason_text": f"玩家服务连不上：{exc}",
                },
            )

    def _static_path(self) -> Path | None:
        parsed = urlsplit(self.path)
        clean = posixpath.normpath(parsed.path or "/")
        if clean == "/":
            clean = "/index.html"
        target = (self.static_dir / clean.lstrip("/")).resolve()
        root = self.static_dir.resolve()
        if root not in target.parents and target != root:
            return None
        return target

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"ok": True, "service": "xkx-ui"})
            return
        if urlsplit(self.path).path.startswith("/api/"):
            self._proxy("GET", None)
            return
        target = self._static_path()
        if target is None:
            self._json(404, {"ok": False, "reason_text": "页面不存在。"})
            return
        self._file(target)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            self._json(413, {"ok": False, "reason_text": "请求体过大。"})
            return
        raw = self.rfile.read(length) if length else b""
        if not urlsplit(self.path).path.startswith("/api/"):
            self._json(404, {"ok": False, "reason_text": "页面不存在。"})
            return
        self._proxy("POST", raw)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[xkx-ui] {self.address_string()} {fmt % args}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="xkx 文字 MUD 前端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--upstream", default="http://127.0.0.1:8041")
    parser.add_argument("--static-dir", default=str(Path(__file__).resolve().parent))
    args = parser.parse_args(argv)

    XKXHandler.static_dir = Path(args.static_dir).resolve()
    XKXHandler.upstream = args.upstream
    server = ThreadingHTTPServer((args.host, args.port), XKXHandler)
    print(
        json.dumps(
            {
                "ok": True,
                "service": "xkx-ui",
                "listen": f"http://{args.host}:{args.port}",
                "upstream": args.upstream,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
