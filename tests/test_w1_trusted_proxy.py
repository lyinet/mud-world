"""W1 可信代理：只有可信直连来源的转发头才被采信，限流按真实客户端 IP 生效。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wsim.api.auth import AccountStore
from wsim.api.server import create_server, parse_trusted_proxies, resolve_client_ip
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, t: float = 1_000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t


class Headers(dict):
    """模拟 email.message.Message 的 `.get()` 行为。"""


def _request(base, path, method="POST", body=None, headers=None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=payload, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers)


@pytest.fixture()
def service_factory(tmp_path):
    """按需创建带 `trusted_proxies` 的 API 服务；返回 (base, accounts, stop)。"""
    started = []

    def build(trusted):
        clock = Clock()
        session = WorldSession(ROOT, run_id="w1-proxy", run_dir=tmp_path / "run", seed=42)
        accounts = AccountStore(
            tmp_path / "service.sqlite", iterations=1_000, token_ttl=3_600, now_fn=clock
        )
        assert accounts.register("u_demo", "secret123")["ok"] is True
        server = create_server(
            session, "127.0.0.1", 0, accounts=accounts, auth_required=True,
            trusted_proxies=trusted,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}", accounts, clock

    try:
        yield build
    finally:
        for server, thread in started:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def _failure_ips(accounts: AccountStore) -> list[str]:
    rows = accounts.conn.execute(
        "SELECT ip FROM login_failures ORDER BY failed_at"
    ).fetchall()
    return [str(r["ip"]) for r in rows]


def test_parse_trusted_proxies_ignores_invalid():
    nets = parse_trusted_proxies("172.18.0.0/16, 10.0.0.0/8, not-a-cidr")
    assert [str(n) for n in nets] == ["172.18.0.0/16", "10.0.0.0/8"]
    assert parse_trusted_proxies("") == ()


def test_resolve_client_ip_default_ignores_forwarded_headers():
    headers = Headers({"X-Forwarded-For": "203.0.113.9", "X-Real-IP": "203.0.113.9"})
    assert resolve_client_ip("10.1.2.3", headers, ()) == "10.1.2.3"


def test_resolve_client_ip_trusted_rightmost_untrusted():
    trusted = parse_trusted_proxies("172.18.0.0/16")
    headers = Headers({"X-Forwarded-For": "198.51.100.7, 203.0.113.9, 172.18.0.5"})
    assert resolve_client_ip("172.18.0.4", headers, trusted) == "203.0.113.9"
    # 全部是可信代理时取最左（链路起点）
    assert resolve_client_ip("172.18.0.4", Headers({"X-Forwarded-For": "172.18.0.1, 172.18.0.5"}), trusted) == "172.18.0.1"
    # 无 XFF 时回退 X-Real-IP
    assert resolve_client_ip("172.18.0.4", Headers({"X-Real-IP": "203.0.113.9"}), trusted) == "203.0.113.9"
    # 畸形头 fail-closed
    assert resolve_client_ip("172.18.0.4", Headers({"X-Forwarded-For": "203.0.113.9, evil"}), trusted) == "172.18.0.4"


def test_untrusted_source_spoof_is_ignored(service_factory):
    base, accounts, _ = service_factory("172.18.0.0/16")
    status, body, _ = _request(
        base, "/api/auth/login", body={"username": "u_demo", "password": "wrong"},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert status == 401 and body["code"] == "auth_invalid_credentials"
    assert _failure_ips(accounts) == ["127.0.0.1"]


def test_no_config_spoof_is_ignored(service_factory):
    base, accounts, _ = service_factory("")
    status, _, _ = _request(
        base, "/api/auth/login", body={"username": "u_demo", "password": "wrong"},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert status == 401
    assert _failure_ips(accounts) == ["127.0.0.1"]


def test_trusted_proxy_records_real_ip(service_factory):
    base, accounts, _ = service_factory("127.0.0.0/8")
    status, body, _ = _request(
        base, "/api/auth/login", body={"username": "u_demo", "password": "wrong"},
        headers={"X-Forwarded-For": "203.0.113.9, 127.0.0.1"},
    )
    assert status == 401 and body["code"] == "auth_invalid_credentials"
    assert _failure_ips(accounts) == ["203.0.113.9"]


def test_throttle_is_per_real_ip_and_recovers_from_other_ip(service_factory):
    base, accounts, _ = service_factory("127.0.0.0/8")
    ip_a = {"X-Forwarded-For": "203.0.113.50"}
    statuses = []
    retry_headers = []
    for _ in range(4):
        status, _, headers = _request(
            base, "/api/auth/login", body={"username": "u_demo", "password": "wrong"}, headers=ip_a
        )
        statuses.append(status)
        retry_headers.append(headers.get("Retry-After"))
    # 触发锁定前的失败记录，落的是真实客户端 IP
    assert _failure_ips(accounts) == ["203.0.113.50"] * 4
    for _ in range(46):
        status, _, headers = _request(
            base, "/api/auth/login", body={"username": "u_demo", "password": "wrong"}, headers=ip_a
        )
        statuses.append(status)
        retry_headers.append(headers.get("Retry-After"))
    assert statuses.count(401) == 4
    assert statuses.count(429) == 46
    assert {h for h in retry_headers[4:]} == {"900"}
    locks = accounts.conn.execute("SELECT ip, account FROM login_locks").fetchall()
    assert [(r["ip"], r["account"]) for r in locks] == [("203.0.113.50", "u_demo")]

    status, body, _ = _request(
        base, "/api/auth/login", body={"username": "u_demo", "password": "secret123"},
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert status == 200 and body.get("ok") is True
