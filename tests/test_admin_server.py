"""管理 HTTP API 单测：鉴权/限流/IP/settings/providers/test/audit/killswitch/持久化。

用线程内的 mock 中转站验证真实 HTTP 调用（零真实模型调用）。
"""
from __future__ import annotations

import base64
import contextlib
import http.client
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.admin import providers, secrets, server, settings  # noqa: E402
from wsim.agent.provider import ProviderClient, ProviderConfig, ProviderUnavailable  # noqa: E402

ADMIN_TOKEN = "admin-token-for-tests"


class _RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        relay = self.server
        relay.calls.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization", ""),
                "model": body.get("model"),
                "max_tokens": body.get("max_tokens"),
                "body": body,
            }
        )
        status = relay.statuses.pop(0) if relay.statuses else 200
        if status >= 400:
            payload = {"error": {"message": f"mock_{status}", "type": "mock_error"}}
        else:
            payload = {
                "id": "mock-completion",
                "object": "chat.completion",
                "model": body.get("model") or "mock-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            }
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextlib.contextmanager
def mock_relay():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RelayHandler)
    httpd.calls = []
    httpd.statuses = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


@contextlib.contextmanager
def admin_server(db_path: Path, root: Path):
    app = server.AdminApp(db_path, root=root, run="test")
    httpd = server.create_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(httpd, method: str, path: str, payload: dict | None = None, token: str | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload) if payload is not None else None
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, json.loads(raw.decode("utf-8") or "{}")


@pytest.fixture
def env(db_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv(secrets.MASTER_KEY_ENV, key)
    monkeypatch.setenv("XKX_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("XKX_ADMIN_DB", str(db_path))
    cache = providers.SnapshotCache(db_path)
    monkeypatch.setattr(providers, "_CACHE", cache)
    return cache


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "service.sqlite"
    settings.init_schema(path)
    return path


def login(httpd) -> str:
    status, payload = call(httpd, "POST", "/api/admin/login", {"token": ADMIN_TOKEN})
    assert status == 200 and payload["ok"] is True
    return str(payload["token"])


def test_health_without_auth(db_path: Path, env, tmp_path: Path) -> None:
    with admin_server(db_path, tmp_path) as (_app, httpd):
        status, payload = call(httpd, "GET", "/api/admin/health")
    assert status == 200
    assert payload["ok"] is True
    assert payload["crypto_backend"] in ("cryptography", "builtin-aesgcm")


def test_auth_required_and_login(db_path: Path, env, tmp_path: Path) -> None:
    with admin_server(db_path, tmp_path) as (_app, httpd):
        assert call(httpd, "GET", "/api/admin/settings")[0] == 401
        assert call(httpd, "POST", "/api/admin/login", {"token": "wrong"})[0] == 401
        token = login(httpd)
        status, payload = call(httpd, "GET", "/api/admin/settings", token=token)
    assert status == 200
    assert payload["world"]["ambient_speed"] == 1
    assert payload["hot"]["ambient_speed"] is False
    assert payload["capabilities_status"]["validation"] == "stub"
    assert payload["capabilities_status"]["dialogue"] == "active"
    assert "sim/self_goal.py" in payload["capabilities_evidence"]["self_goal"]


def test_put_settings_requires_restart(db_path: Path, env, tmp_path: Path) -> None:
    with admin_server(db_path, tmp_path) as (_app, httpd):
        token = login(httpd)
        status, payload = call(
            httpd, "PUT", "/api/admin/settings",
            {"world": {"ambient_speed": 3}, "llm": {"global_enabled": True}},
            token,
        )
        assert status == 200
        assert payload["version"] == 1
        assert payload["requires_restart"] == ["ambient_speed"]
        status, payload = call(httpd, "GET", "/api/admin/settings", token=token)
        assert payload["world"]["ambient_speed"] == 3
        assert payload["llm"]["global_enabled"] is True


def test_provider_put_requires_master_key(
    db_path: Path, env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with mock_relay() as relay:
        base = f"http://127.0.0.1:{relay.server_address[1]}/v1"
        with admin_server(db_path, tmp_path) as (_app, httpd):
            token = login(httpd)
            monkeypatch.delenv(secrets.MASTER_KEY_ENV, raising=False)
            status, payload = call(
                httpd, "PUT", "/api/admin/providers/relay-a",
                {"base_url": base, "model": "mock-model", "api_key": "sk-new-secret"},
                token,
            )
    assert status == 503
    assert payload["code"] == "master_key_missing"
    assert "sk-new-secret" not in json.dumps(payload)
    assert settings.SettingsStore(db_path).providers() == []


def test_provider_crud_test_and_masked_key(db_path: Path, env, tmp_path: Path) -> None:
    with mock_relay() as relay:
        base = f"http://127.0.0.1:{relay.server_address[1]}/v1"
        with admin_server(db_path, tmp_path) as (_app, httpd):
            token = login(httpd)
            status, payload = call(
                httpd, "PUT", "/api/admin/providers/relay-a",
                {"name": "中转站A", "base_url": base, "model": "mock-model",
                 "api_key": "sk-very-secret-key-value",
                 "params": {"max_tokens": 240, "max_retries": 0}},
                token,
            )
            assert status == 200, payload
            assert payload["has_key"] is True
            assert payload["key_masked"] == "sk-v…alue"
            assert "api_key" not in payload

            status, listing = call(httpd, "GET", "/api/admin/providers", token=token)
            assert status == 200
            assert listing["providers"][0]["key_masked"] == "sk-v…alue"

            status, result = call(httpd, "POST", "/api/admin/providers/relay-a/test", token=token)
            assert status == 200
            assert result["ok"] is True, result
            assert isinstance(result["latency_ms"], float)
            assert result["model"] == "mock-model"
            assert "key" not in result
            assert len(relay.calls) == 1
            assert relay.calls[0]["body"]["max_tokens"] == 1

            status, payload = call(
                httpd, "PUT", "/api/admin/providers/relay-a",
                {"base_url": "http://127.0.0.1:1/v1"}, token,
            )
            assert status == 200
            status, result = call(httpd, "POST", "/api/admin/providers/relay-a/test", token=token)
            assert status == 200
            assert result["ok"] is False
            assert result["error"]
            assert "sk-very-secret-key-value" not in json.dumps(result)


def test_change_key_and_audit_masking(db_path: Path, env, tmp_path: Path) -> None:
    with mock_relay() as relay:
        base = f"http://127.0.0.1:{relay.server_address[1]}/v1"
        with admin_server(db_path, tmp_path) as (_app, httpd):
            token = login(httpd)
            call(httpd, "PUT", "/api/admin/providers/relay-a",
                 {"base_url": base, "model": "mock-model", "api_key": "sk-old-key-0001"}, token)
            call(httpd, "PUT", "/api/admin/settings",
                 {"routing": {"dialogue": {"provider": "relay-a", "fallback": []}}}, token)
            providers._CACHE.invalidate()
            client = _client()
            assert client.complete("s", "u") == "pong"
            assert relay.calls[-1]["auth"] == "Bearer sk-old-key-0001"

            call(httpd, "PUT", "/api/admin/providers/relay-a",
                 {"api_key": "sk-new-key-0002"}, token)
            providers._CACHE.invalidate()
            assert client.complete("s", "u") == "pong"
            assert relay.calls[-1]["auth"] == "Bearer sk-new-key-0002"

            status, payload = call(httpd, "GET", "/api/admin/audit?limit=50", token=token)
            assert status == 200
            text = json.dumps(payload, ensure_ascii=False)
            assert "sk-old-key-0001" not in text
            assert "sk-new-key-0002" not in text
            actions = {row["action"] for row in payload["records"]}
            assert {"provider.put", "secret.put", "settings.update"} <= actions
            secret_rows = [row for row in payload["records"] if row["action"] == "secret.put"]
            assert secret_rows[0]["after"]["api_key"] == "sk-n…0002"


def test_killswitch_blocks_calls(db_path: Path, env, tmp_path: Path) -> None:
    with mock_relay() as relay:
        base = f"http://127.0.0.1:{relay.server_address[1]}/v1"
        with admin_server(db_path, tmp_path) as (_app, httpd):
            token = login(httpd)
            call(httpd, "PUT", "/api/admin/providers/relay-a",
                 {"base_url": base, "model": "mock-model", "api_key": "sk-killswitch-01"}, token)
            call(httpd, "PUT", "/api/admin/settings",
                 {"routing": {"dialogue": {"provider": "relay-a", "fallback": []}}}, token)
            providers._CACHE.invalidate()
            client = _client()
            assert client.complete("s", "u") == "pong"
            calls_before = len(relay.calls)

            status, payload = call(httpd, "POST", "/api/admin/llm/killswitch",
                                   {"enabled": True}, token)
            assert status == 200 and payload["enabled"] is True
            providers._CACHE.invalidate()
            with pytest.raises(ProviderUnavailable) as exc:
                client.complete("s", "u")
            assert "llm_killswitch" in str(exc.value)
            assert len(relay.calls) == calls_before
            assert client.stats_snapshot()["calls"] == 2


def test_fallback_chain_via_http(db_path: Path, env, tmp_path: Path) -> None:
    with mock_relay() as relay:
        relay.statuses = [429]
        base = f"http://127.0.0.1:{relay.server_address[1]}/v1"
        with admin_server(db_path, tmp_path) as (app, httpd):
            token = login(httpd)
            for pid, key in (("relay-a", "sk-a-0000001"), ("relay-b", "sk-b-0000002")):
                call(httpd, "PUT", f"/api/admin/providers/{pid}",
                     {"base_url": base, "model": "mock-model", "api_key": key,
                      "params": {"max_retries": 0, "backoff_429_s": 0}}, token)
            call(httpd, "PUT", "/api/admin/settings",
                 {"routing": {"dialogue": {"provider": "relay-a", "fallback": ["relay-b"]}}}, token)
            providers._CACHE.invalidate()
            client = _client()
            assert client.complete("s", "u") == "pong"
            assert [item["auth"] for item in relay.calls[-2:]] == [
                "Bearer sk-a-0000001", "Bearer sk-b-0000002"
            ]
            snapshot = client.stats_snapshot()
            assert snapshot["fallbacks"] == 1
            assert snapshot["http_429"] == 1
            assert client.last_fallback is True


def test_overview_reports_runtime(db_path: Path, env, tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "test"
    (run_dir / "ambient").mkdir(parents=True)
    (run_dir / "ambient" / "ambient_status.json").write_text(
        json.dumps(
            {
                "state": "running",
                "speed": 2.0,
                "tick": 480,
                "events_total": 1234,
                "active_npcs": 42,
                "llm_calls": 7,
                "cpu": {"limit": 70},
                "updated_at": "2026-11-06T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    with admin_server(db_path, tmp_path) as (_app, httpd):
        token = login(httpd)
        status, payload = call(httpd, "GET", "/api/admin/overview", token=token)
    assert status == 200
    assert payload["world"]["tick"] == 480
    assert payload["world"]["events_total"] == 1234
    assert payload["llm"]["calls"] == 7
    assert payload["runtime"]["ambient_speed"] == 2.0
    assert payload["runtime"]["configured"]["ambient_speed"] == 1
    assert payload["health"]["ambient_state"] == "running"


def test_login_rate_limit(db_path: Path, env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XKX_ADMIN_TOKEN", ADMIN_TOKEN)
    app = server.AdminApp(db_path, root=tmp_path, run="test")
    code = ""
    for _ in range(10):
        with pytest.raises(server.HttpError) as exc:
            app.login("bad", "203.0.113.9")
        code = exc.value.code
    assert code == "invalid_admin_token"
    with pytest.raises(server.HttpError) as exc:
        app.login(ADMIN_TOKEN, "203.0.113.9")
    assert exc.value.status == 429
    assert exc.value.code == "too_many_attempts"


def test_admin_ip_not_allowed(db_path: Path, env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XKX_ADMIN_ALLOW_IPS", raising=False)
    app = server.AdminApp(db_path, root=tmp_path, run="test")
    handler_like = _IPProbe("203.0.113.9", app)
    with pytest.raises(server.HttpError) as exc:
        handler_like._check_ip()
    assert exc.value.status == 403
    assert exc.value.code == "admin_ip_not_allowed"
    monkeypatch.setenv("XKX_ADMIN_ALLOW_IPS", "203.0.113.9")
    handler_like._check_ip()


def test_config_survives_admin_restart(db_path: Path, env, tmp_path: Path) -> None:
    with admin_server(db_path, tmp_path) as (_app, httpd):
        token = login(httpd)
        call(httpd, "PUT", "/api/admin/settings", {"world": {"ambient_speed": 5}}, token)
        call(httpd, "PUT", "/api/admin/providers/relay-a",
             {"base_url": "http://127.0.0.1:9/v1", "model": "m", "api_key": "sk-persist-01"}, token)
    reopened = server.AdminApp(db_path, root=tmp_path, run="test")
    payload = reopened.get_settings()
    assert payload["world"]["ambient_speed"] == 5
    assert reopened.list_providers()[0]["key_masked"] == "sk-p…t-01"
    assert reopened.store.version() >= 2


class _IPProbe:
    """只复用 AdminHandler 的 IP 校验逻辑，不启动 socket。"""

    def __init__(self, ip: str, app: server.AdminApp):
        self.client_address = (ip, 0)
        self.server = type("S", (), {"app": app})()

    _check_ip = server.AdminHandler._check_ip
    _client_ip = server.AdminHandler._client_ip


def _client() -> ProviderClient:
    return ProviderClient(
        ProviderConfig(
            base_url="http://env.example",
            key="sk-env",
            model="env-model",
            timeout_s=5,
            max_retries=0,
            offline=False,
            max_tokens=8,
            temperature=0.0,
            slot="dialogue",
        )
    )
