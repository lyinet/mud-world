"""`GET /api/state` 只读别名：与 `/api/player/state` 走同一 handler，行为完全一致。

服务模式（auth_required=True）下的读口径（docs/AUTH-API-W1.md §3）：
- 无令牌：200，只返回公共摘要（白名单 PUBLIC_STATE_KEYS）；
- 无效令牌：401 `auth_invalid_token`；
- 有效令牌：200，返回本人完整状态。
三种情况下两条路径的状态码与响应字节必须逐字节相同。
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
from wsim.api.session import PUBLIC_STATE_KEYS, WorldSession

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


def _post(base: str, path: str, body: dict, token: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _login(base: str, username: str = "u_demo", password: str = "secret123") -> str:
    status, _ = _post(base, "/api/auth/register", {"username": username, "password": password})
    assert status in (200, 409), status
    status, payload = _post(base, "/api/auth/login", {"username": username, "password": password})
    assert status == 200, payload
    return str(payload["token"])


@pytest.fixture()
def service(tmp_path):
    # activity/idle 全由世界 tick 推导；两次读取之间没有 tick 推进，结果逐字节一致。
    session = WorldSession(
        ROOT, run_id="alias", run_dir=tmp_path / "run", seed=42,
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
    status, payload = _post(
        base, "/api/player/enter",
        {"display_name": "陈捕快", "archetype": "捕快"}, token=token,
    )
    assert status == 200 and payload["ok"] is True, payload

    # 1) 无令牌：公共摘要，200
    main_status, main_body = _raw(base, MAIN)
    alias_status, alias_body = _raw(base, ALIAS)
    assert alias_status == main_status == 200
    assert alias_body == main_body
    assert set(json.loads(alias_body)) == set(PUBLIC_STATE_KEYS)

    # 2) 无效令牌：401 auth_invalid_token
    main_status, main_body = _raw(base, MAIN, "not-a-real-token")
    alias_status, alias_body = _raw(base, ALIAS, "not-a-real-token")
    assert alias_status == main_status == 401
    assert alias_body == main_body
    assert json.loads(alias_body)["code"] == "auth_invalid_token"

    # 3) 有效令牌：本人完整状态，200
    main_status, main_body = _raw(base, MAIN, token)
    alias_status, alias_body = _raw(base, ALIAS, token)
    assert alias_status == main_status == 200
    assert alias_body == main_body
    body = json.loads(alias_body)
    assert body["ok"] is True and body["player"]["wallet"]["money"] == 300


def test_alias_matches_main_in_legacy_mode(tmp_path):
    session = WorldSession(
        ROOT, run_id="alias-legacy", run_dir=tmp_path / "run", seed=42,
    )
    server = create_server(session, "127.0.0.1", 0, accounts=None, auth_required=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        _post(base, "/api/player/enter",
              {"account_id": "u_demo", "display_name": "陈捕快", "archetype": "捕快"})
        main_status, main_body = _raw(base, MAIN)
        alias_status, alias_body = _raw(base, ALIAS)
        assert alias_status == main_status == 200
        assert alias_body == main_body
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()
