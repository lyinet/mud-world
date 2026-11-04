"""W1 HTTP 鉴权：写端点 401 全覆盖、auth 契约、账号不一致 403、失败关闭。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wsim.api.auth import AccountStore
from wsim.api.server import _POST_ROUTES, create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]
WRITE_PATHS = list(_POST_ROUTES) + ["/api/player/reconnect"]


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
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def service(tmp_path):
    session = WorldSession(ROOT, run_id="w1-http", run_dir=tmp_path / "run", seed=42)
    clock = Clock()
    accounts = AccountStore(
        tmp_path / "service.sqlite", iterations=1_000, token_ttl=3_600, now_fn=clock
    )
    server = create_server(
        session, "127.0.0.1", 0, accounts=accounts, auth_required=True
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, session, accounts, clock
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()


def _login(base, username="u_demo", password="secret123"):
    status, payload = _request(base, "/api/auth/register", body={"username": username, "password": password})
    assert status in (200, 409), (status, payload)
    status, payload = _request(base, "/api/auth/login", body={"username": username, "password": password})
    assert status == 200, (status, payload)
    return payload["token"], payload


def test_every_write_endpoint_rejects_missing_token(service):
    base, _, _, _ = service
    for path in WRITE_PATHS:
        status, payload = _request(base, path, body={})
        assert status == 401, (path, status, payload)
        assert payload["code"] == "auth_missing_token"
        assert payload["reject_reason"] == "auth_missing_token"


def test_every_write_endpoint_rejects_invalid_token(service):
    base, _, _, _ = service
    for path in WRITE_PATHS:
        status, payload = _request(base, path, body={}, token="not-a-real-token")
        assert status == 401, (path, status, payload)
        assert payload["code"] == "auth_invalid_token"


def test_expired_token_covers_writes_and_auth_me(service):
    base, _, _, clock = service
    token, _ = _login(base)
    clock.t += 3_601
    for path in WRITE_PATHS:
        status, payload = _request(base, path, body={}, token=token)
        assert status == 401, (path, status, payload)
        assert payload["code"] == "auth_expired_token"
    status, payload = _request(base, "/api/auth/me", method="GET", token=token)
    assert status == 401 and payload["code"] == "auth_expired_token"


def test_reads_are_anonymous_but_writes_require_token(service):
    base, session, _, _ = service
    # 服务模式匿名读只给公共摘要：不再泄漏钱包/技能/关系等私有字段。
    status, payload = _request(base, "/api/player/state?account_id=u_demo", method="GET")
    assert status == 200 and payload["ok"] is True
    assert "wallet" not in payload and "player" not in payload
    status, info = _request(base, "/api/service/info", method="GET")
    assert status == 200 and info["auth_required"] is True

    token, _ = _login(base)
    status, payload = _request(
        base, "/api/player/enter",
        body={"display_name": "陈捕快", "archetype": "捕快"}, token=token,
    )
    assert status == 200 and payload["ok"] is True
    assert session.state.players["player:u_demo"].online is True


def test_account_mismatch_is_403_and_token_account_wins(service):
    base, session, _, _ = service
    token, _ = _login(base)
    status, payload = _request(
        base, "/api/player/enter",
        body={"account_id": "u_other", "display_name": "冒名", "archetype": "游侠"},
        token=token,
    )
    assert status == 403 and payload["code"] == "auth_account_mismatch"
    assert session.state.players.get("player:u_other") is None

    status, payload = _request(
        base, "/api/player/enter",
        body={"display_name": "陈捕快", "archetype": "捕快"}, token=token,
    )
    assert status == 200 and payload["ok"] is True
    assert payload["player"] == "player:u_demo"


def test_auth_contract_register_login_me_refresh_logout(service):
    base, _, _, _ = service
    status, reg = _request(
        base, "/api/auth/register", body={"username": "u_flow", "password": "secret123"}
    )
    assert status == 200
    assert reg == {"ok": True, "account_id": "u_flow", "username": "u_flow", "created": True}

    status, bad = _request(base, "/api/auth/register", body={"username": "u_flow", "password": "x"})
    assert status == 400 and bad["code"] == "auth_bad_password"
    status, taken = _request(
        base, "/api/auth/register", body={"username": "u_flow", "password": "secret123"}
    )
    assert status == 409 and taken["code"] == "auth_username_taken"

    status, login = _request(base, "/api/auth/login", body={"username": "u_flow", "password": "badpass"})
    assert status == 401 and login["code"] == "auth_invalid_credentials"
    status, login = _request(
        base, "/api/auth/login", body={"username": "u_flow", "password": "secret123"}
    )
    assert status == 200
    assert set(login) >= {
        "ok", "account_id", "username", "session_id", "token", "token_type",
        "expires_at", "expires_in",
    }
    assert login["token_type"] == "Bearer" and login["expires_in"] == 3_600
    token = login["token"]

    status, me = _request(base, "/api/auth/me", method="GET", token=token)
    assert status == 200 and me["account_id"] == "u_flow" and me["status"] == "active"

    status, rotated = _request(base, "/api/auth/refresh", body={}, token=token)
    assert status == 200 and rotated["rotated"] is True and rotated["token"] != token
    status, old = _request(base, "/api/auth/me", method="GET", token=token)
    assert status == 401 and old["code"] == "auth_invalid_token"

    status, out = _request(base, "/api/auth/logout", body={}, token=rotated["token"])
    assert status == 200 and out["logged_out"] is True
    status, after = _request(base, "/api/auth/me", method="GET", token=rotated["token"])
    assert status == 401 and after["code"] == "auth_expired_token"


def test_auth_required_without_store_fails_closed(tmp_path):
    session = WorldSession(ROOT, run_id="w1-closed", run_dir=tmp_path / "run", seed=42)
    server = create_server(session, "127.0.0.1", 0, accounts=None, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _request(base, "/api/player/enter", body={"account_id": "u_demo"})
        assert status == 503 and payload["code"] == "auth_disabled"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_legacy_mode_stays_anonymous(tmp_path):
    session = WorldSession(ROOT, run_id="w1-legacy", run_dir=tmp_path / "run", seed=42)
    server = create_server(session, "127.0.0.1", 0, accounts=None, auth_required=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload = _request(
            base, "/api/player/enter",
            body={"account_id": "u_demo", "display_name": "陈捕快", "archetype": "捕快"},
        )
        assert status == 200 and payload["ok"] is True
        # legacy 匿名试玩口径不变：无令牌仍能读完整状态（含钱包/技能/关系）。
        status, state = _request(base, "/api/player/state?account_id=u_demo", method="GET")
        assert status == 200 and state["player"]["wallet"]["money"] == 300
        assert "skills" in state["player"] and "relations" in state["player"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()
