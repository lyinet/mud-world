"""W1 鉴权加固：登录限流/指数退避/运维解锁、令牌吊销与审计。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wsim.api.auth import AccountStore
from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, t: float = 1_000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t


def _request(base, path, method="POST", body=None, token=""):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers)


@pytest.fixture()
def store(tmp_path):
    clock = Clock()
    accounts = AccountStore(
        tmp_path / "service.sqlite", iterations=1_000, token_ttl=3_600, now_fn=clock
    )
    assert accounts.register("u_demo", "secret123")["ok"] is True
    try:
        yield accounts, clock
    finally:
        accounts.close()


@pytest.fixture()
def service(tmp_path):
    clock = Clock()
    session = WorldSession(ROOT, run_id="w1-auth-hard", run_dir=tmp_path / "run", seed=42)
    accounts = AccountStore(
        tmp_path / "service.sqlite", iterations=1_000, token_ttl=3_600, now_fn=clock
    )
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    assert accounts.register("u_demo", "secret123")["ok"] is True
    try:
        yield base, session, accounts, clock
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()


def test_failure_threshold_locks_with_backoff(store):
    accounts, clock = store
    for i in range(4):
        res = accounts.login("u_demo", "wrong", now=clock.t, ip="10.0.0.1")
        assert res["code"] == "auth_invalid_credentials", (i, res)
    res = accounts.login("u_demo", "wrong", now=clock.t, ip="10.0.0.1")
    assert res["code"] == "too_many_attempts"
    assert res["retry_after"] == 900
    # 锁定期内正确口令也拒绝
    res = accounts.login("u_demo", "secret123", now=clock.t, ip="10.0.0.1")
    assert res["code"] == "too_many_attempts"
    assert 780 <= res["retry_after"] <= 900


def test_backoff_escalates_15m_1h_24h(store):
    accounts, clock = store
    ip = "10.0.0.1"
    for expected in (900, 3_600, 86_400):
        for _ in range(5):
            res = accounts.login("u_demo", "wrong", now=clock.t, ip=ip)
        assert res["code"] == "too_many_attempts", res
        assert res["retry_after"] == expected, (expected, res)
        clock.t += expected  # 锁定到期


def test_success_clears_failures(store):
    accounts, clock = store
    ip = "10.0.0.1"
    for _ in range(4):
        accounts.login("u_demo", "wrong", now=clock.t, ip=ip)
    ok = accounts.login("u_demo", "secret123", now=clock.t, ip=ip)
    assert ok["ok"] is True
    # 清零后重新从 0 计数：再错 4 次仍是 401
    for _ in range(4):
        res = accounts.login("u_demo", "wrong", now=clock.t, ip=ip)
        assert res["code"] == "auth_invalid_credentials"


def test_other_ip_is_not_locked(store):
    accounts, clock = store
    for _ in range(5):
        accounts.login("u_demo", "wrong", now=clock.t, ip="10.0.0.1")
    assert accounts.login("u_demo", "wrong", now=clock.t, ip="10.0.0.1")["code"] == "too_many_attempts"
    # 同账号、不同 IP 不受影响
    ok = accounts.login("u_demo", "secret123", now=clock.t, ip="10.0.0.2")
    assert ok["ok"] is True and ok["token"]


def test_unlock_account_restores_login(store):
    accounts, clock = store
    for _ in range(5):
        accounts.login("u_demo", "wrong", now=clock.t, ip="10.0.0.1")
    assert accounts.login("u_demo", "secret123", now=clock.t, ip="10.0.0.1")["code"] == "too_many_attempts"
    cleared = accounts.clear_login_locks(account="u_demo")
    assert cleared == 1
    ok = accounts.login("u_demo", "secret123", now=clock.t, ip="10.0.0.1")
    assert ok["ok"] is True
    audit = [row["action"] for row in accounts.list_audit()]
    assert "unlock_account" in audit


def test_revoke_sessions_invalidates_tokens_and_audits(store):
    accounts, clock = store
    login = accounts.login("u_demo", "secret123", now=clock.t, ip="10.0.0.1")
    token = login["token"]
    row, err = accounts.resolve_token(token, now=clock.t)
    assert err is None and row is not None

    result = accounts.revoke_sessions(account="u_demo", ip="tool:revoke")
    assert result["ok"] is True and result["revoked"] == 1

    row, err = accounts.resolve_token(token, now=clock.t)
    assert row is None and err == "auth_invalid_token"
    assert accounts.refresh(token, now=clock.t)["code"] == "auth_invalid_token"

    audit = accounts.list_audit()
    assert audit[0]["action"] == "revoke_sessions"
    assert "revoked=1" in audit[0]["detail"]


def test_http_429_sequence_retry_after_and_unlock(service):
    base, _session, accounts, _clock = service
    statuses = []
    retry_headers = []
    for _ in range(50):
        status, payload, headers = _request(
            base, "/api/auth/login", body={"username": "u_demo", "password": "wrong"}
        )
        statuses.append(status)
        if status == 429:
            retry_headers.append(headers.get("Retry-After"))
            assert payload["code"] == "too_many_attempts"
    assert statuses[:4] == [401, 401, 401, 401]
    assert set(statuses[4:]) == {429}
    assert len(retry_headers) == 46
    assert set(retry_headers) == {"900"}

    # 锁定期内正确口令同样 429
    status, payload, headers = _request(
        base, "/api/auth/login", body={"username": "u_demo", "password": "secret123"}
    )
    assert status == 429 and payload["retry_after"] == 900
    assert headers.get("Retry-After") == "900"

    # 运维解锁后立刻可登录
    accounts.clear_login_locks(account="u_demo")
    status, payload, _headers = _request(
        base, "/api/auth/login", body={"username": "u_demo", "password": "secret123"}
    )
    assert status == 200 and payload["ok"] is True


def test_http_revoked_token_is_401_auth_invalid_token(service):
    base, _session, accounts, _clock = service
    status, payload, _headers = _request(
        base, "/api/auth/login", body={"username": "u_demo", "password": "secret123"}
    )
    assert status == 200
    token = payload["token"]
    status, _payload, _headers = _request(base, "/api/auth/me", method="GET", token=token)
    assert status == 200

    accounts.revoke_sessions(account="u_demo", ip="tool:revoke")
    status, payload, _headers = _request(base, "/api/auth/me", method="GET", token=token)
    assert status == 401 and payload["code"] == "auth_invalid_token"
    # 写端点同样立即失效
    status, payload, _headers = _request(
        base, "/api/player/heartbeat", body={}, token=token
    )
    assert status == 401 and payload["code"] == "auth_invalid_token"
