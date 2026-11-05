"""W1 请求加固：8KB 体积上限、请求头上限、CORS 同源默认与覆盖、读取超时。"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


def _request(base, path, method="POST", body=None, headers=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode("utf-8"))
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_body = resp.read().decode("utf-8")
            parsed = json.loads(raw_body) if raw_body else {}
            return resp.status, parsed, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        parsed = json.loads(raw_body) if raw_body else {}
        return exc.code, parsed, dict(exc.headers)


@pytest.fixture()
def service(tmp_path):
    session = WorldSession(ROOT, run_id="w1-http-hard", run_dir=tmp_path / "run", seed=42)
    server = create_server(
        session, "127.0.0.1", 0, auth_required=False,
        max_body_bytes=8 * 1024, max_header_bytes=8 * 1024, request_timeout=0.5,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_body_over_8kb_is_413(service):
    base, _server = service
    big = {"display_name": "x" * 9000, "archetype": "捕快"}
    status, payload, _headers = _request(base, "/api/player/enter", body=big)
    assert status == 413, payload
    assert payload["code"] == "payload_too_large"
    assert payload["reject_reason"] == "payload_too_large"


def test_headers_over_limit_is_431(service):
    base, _server = service
    status, payload, _headers = _request(
        base, "/api/state", method="GET", headers={"X-Pad": "a" * 9000}
    )
    assert status == 431, payload
    assert payload["code"] == "header_too_large"


def test_cors_default_is_same_origin(service):
    base, _server = service
    _status, _payload, headers = _request(base, "/api/health", method="GET")
    assert "Access-Control-Allow-Origin" not in headers
    status, _body, headers = _request(base, "/api/health", method="OPTIONS")
    assert status == 204
    assert "Access-Control-Allow-Origin" not in headers


def test_cors_origin_override(tmp_path):
    session = WorldSession(ROOT, run_id="w1-http-cors", run_dir=tmp_path / "run", seed=42)
    server = create_server(
        session, "127.0.0.1", 0, auth_required=False, cors_origin="https://xkx.example"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        _status, _payload, headers = _request(base, "/api/health", method="GET")
        assert headers.get("Access-Control-Allow-Origin") == "https://xkx.example"
        status, _body, headers = _request(base, "/api/health", method="OPTIONS")
        assert status == 204
        assert headers.get("Access-Control-Allow-Origin") == "https://xkx.example"
        assert "POST" in headers.get("Access-Control-Allow-Methods", "")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_read_timeout_returns_408(service):
    base, _server = service
    host, port = base.removeprefix("http://").split(":")
    sock = socket.create_connection((host, int(port)), timeout=5)
    try:
        # 声明 100 字节只发送 1 字节，触发服务端读取超时（request_timeout=0.5s）
        sock.sendall(
            b"POST /api/player/heartbeat HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
            b"Content-Length: 100\r\nConnection: close\r\n\r\n{"
        )
        raw = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        assert b"408" in raw.split(b"\r\n", 1)[0], raw[:200]
        assert b"request_timeout" in raw
    finally:
        sock.close()
