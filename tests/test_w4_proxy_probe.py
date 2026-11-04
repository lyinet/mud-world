"""锁住 UI 探活契约：服务模式匿名 state 不含 profile_version，探活必须走 /api/service/info。

回归背景：probe_upstream 曾用匿名 GET /api/player/state 判定 profile_version，
W1 方案 A 把匿名读收成 9 键公共摘要后，探活恒失败、门面降级 readonly、
容器 unhealthy。这个单测用最小 mock 上游锁住修复。
"""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_ui_server():
    path = ROOT / "ui" / "player" / "server.py"
    spec = importlib.util.spec_from_file_location("ui_player_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ProbeHandler(BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def do_GET(self):  # noqa: N802
        _ProbeHandler.hits.append(self.path)
        if self.path.split("?")[0] == "/api/service/info":
            payload = {"ok": True, "auth_required": True, "profile_version": 1,
                       "tick": 12, "run": "probe-check"}
        else:
            # 方案 A：服务模式匿名读 = 9 键公共摘要，没有 profile_version。
            payload = {"ok": True, "name": "探活客", "place": "market", "activity": "idle",
                       "idle_seconds": 0, "online": True, "sect_name": None,
                       "shop_name": None, "server_tick": 12}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_probe_upstream_uses_service_info_when_anonymous_state_lacks_profile_version():
    module = _load_ui_server()
    _ProbeHandler.hits = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        assert module.probe_upstream(base, 2.0) is True
        # 探活实际打的是 service info，而不是匿名 state。
        assert "/api/service/info" in _ProbeHandler.hits
        assert not any(hit.startswith("/api/player/state") for hit in _ProbeHandler.hits)
    finally:
        httpd.shutdown()
        httpd.server_close()
