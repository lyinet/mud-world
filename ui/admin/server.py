#!/usr/bin/env python3
"""管理台静态页 + 管理 API 同源代理（不修改后端逻辑）。

管理服务 `src/wsim/admin/server.py` 只提供 JSON API、没有静态文件服务、也没有
CORS 头。直接用 `file://` 打开页面会被浏览器跨域拦截，因此这里提供一个最小的
本地静态服务器，把 `/api/admin/*` 原样转发到管理服务（默认 8031），页面与 API
同源，无需 CORS。

用法:
    python3 ui/admin/server.py                 # 默认 127.0.0.1:8039 -> 127.0.0.1:8031
    python3 ui/admin/server.py --port 8039 --upstream http://127.0.0.1:8031
    XKX_ADMIN_UPSTREAM=http://10.0.0.5:8031 python3 ui/admin/server.py --host 0.0.0.0

注意:
- 仅限内网使用；本脚本不做登录，鉴权仍由管理服务（XKX_ADMIN_TOKEN）完成。
- 代理只转发方法、路径、Content-Type、Authorization；不记录请求体或口令。
- 本脚本只服务 ui/admin 下的 3 个静态文件，不暴露任何其它文件。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
PROXY_PREFIX = "/api/admin/"
UPSTREAM = "http://127.0.0.1:8031"
PROXY_TIMEOUT = 45.0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 只记方法/路径/状态，不输出请求头或请求体（避免口令进日志）。
        sys.stderr.write("[admin-ui] %s\n" % (fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(204, b"", {})

    # ------------------------------------------------------------------ route
    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path in STATIC_FILES and method == "GET":
            self._serve_static(STATIC_FILES[path])
            return
        if path == "/favicon.ico":
            self._send(204, b"", "text/plain")
            return
        if path.startswith(PROXY_PREFIX):
            self._proxy(method, self.path)
            return
        self._send_json(404, json.dumps({"ok": False, "code": "ui_path_not_found", "detail": path}).encode(),
                        {"Content-Type": "application/json; charset=utf-8"})

    def _serve_static(self, filename: str) -> None:
        file_path = UI_DIR / filename
        if not file_path.is_file():
            self._send_json(404, b'{"ok":false,"code":"ui_file_missing"}', {})
            return
        body = file_path.read_bytes()
        content_type = CONTENT_TYPES.get(file_path.suffix, "application/octet-stream")
        self._send(200, body, content_type, {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })

    def _proxy(self, method: str, path: str) -> None:
        body = self._read_body()
        url = UPSTREAM.rstrip("/") + path
        headers = {"Accept": "application/json"}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        authorization = self.headers.get("Authorization")
        if authorization:
            headers["Authorization"] = authorization
        request = urllib.request.Request(url, data=body if method in ("POST", "PUT", "PATCH") else None,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=PROXY_TIMEOUT) as response:
                payload = response.read()
                out_headers = {
                    "Content-Type": response.headers.get("Content-Type", "application/json; charset=utf-8"),
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                }
                self._send(response.status, payload, None, out_headers)
        except urllib.error.HTTPError as exc:
            payload = exc.read() or b""
            out_headers = {
                "Content-Type": exc.headers.get("Content-Type", "application/json; charset=utf-8")
                if exc.headers else "application/json; charset=utf-8",
                "Cache-Control": "no-store",
            }
            self._send(exc.code, payload, None, out_headers)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            detail = f"admin upstream unreachable: {UPSTREAM} ({exc})"
            payload = json.dumps({"ok": False, "code": "admin_upstream_unreachable", "detail": detail}).encode()
            self._send_json(502, payload, {})

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    # ------------------------------------------------------------------ output
    def _send_json(self, status: int, payload: bytes, headers: dict) -> None:
        merged = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
        merged.update(headers)
        self._send(status, payload, None, merged)

    def _send(self, status: int, payload: bytes, content_type: str | None = None, headers: dict | None = None) -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="XKX admin UI static server + admin API proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8039)
    parser.add_argument("--upstream", default=os.environ.get("XKX_ADMIN_UPSTREAM", "http://127.0.0.1:8031"))
    args = parser.parse_args()
    global UPSTREAM
    UPSTREAM = args.upstream.rstrip("/")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[admin-ui] serving {UI_DIR} at http://{args.host}:{args.port}/", flush=True)
    print(f"[admin-ui] proxying /api/admin/* -> {UPSTREAM}", flush=True)
    print("[admin-ui] 管理台仅限内网访问；鉴权由 XKX_ADMIN_TOKEN 管理。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
