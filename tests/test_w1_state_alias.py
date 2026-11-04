"""`GET /api/state` 只读别名：必须与 `/api/player/state` 走同一 handler、行为完全一致。

W1 契约里读端点匿名只读（docs/AUTH-API-W1.md §3）。因此这里的断言是
“任何令牌状态下两个路径的状态码与响应字节完全相同”，而不是给别名单独加鉴权：
- 无令牌：两者都 200；
- 无效令牌：读端点不解析令牌，两者都 200；
- 有效令牌：两者都 200；
- 三种情况下响应体逐字节相同。
"""
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
MAIN = "/api/player/state?account_id=u_demo"
ALIAS = "/api/state?account_id=u_demo"


def _raw(base: str, path: str, token: str = "") -> tuple[int, bytes]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _login(base: str, username: str = "u_demo", password: str = "secret123") -> str:
    def post(path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    status, payload = post("/api/auth/register", {"username": username, "password": password})
    assert status in (200, 409), (status, payload)
    status, payload = post("/api/auth/login", {"username": username, "password": password})
    assert status == 200, (status, payload)
    return str(payload["token"])


@pytest.fixture()
def service(tmp_path):
    # 冻结 presence 时钟，保证两次读取的 idle_seconds/activity 不随时间漂移，
    # 才能做逐字节比较。
    session = WorldSession(
        ROOT, run_id="alias", run_dir=tmp_path / "run", seed=42,
        presence_clock=lambda: 1_000.0,
    )
    accounts = AccountStore(tmp_path / "service.sqlite", iterations=1_000, token_ttl=3_600)
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, session, accounts
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()


def test_alias_matches_main_for_all_token_states(service):
    base, _, _ = service
    token = _login(base)
    for label, bearer in (("missing", ""), ("invalid", "not-a-real-token"), ("valid", token)):
        main_status, main_body = _raw(base, MAIN, bearer)
        alias_status, alias_body = _raw(base, ALIAS, bearer)
        assert alias_status != 404, label
        assert alias_status == main_status, (label, main_status, alias_status)
        assert alias_body == main_body, f"{label}: 响应体不一致"
        assert json.loads(alias_body)["ok"] is True, label


def test_alias_matches_main_in_legacy_mode(tmp_path):
    session = WorldSession(
        ROOT, run_id="alias-legacy", run_dir=tmp_path / "run", seed=42,
        presence_clock=lambda: 1_000.0,
    )
    server = create_server(session, "127.0.0.1", 0, accounts=None, auth_required=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        main_status, main_body = _raw(base, MAIN)
        alias_status, alias_body = _raw(base, ALIAS)
        assert alias_status == main_status == 200
        assert alias_body == main_body
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()
