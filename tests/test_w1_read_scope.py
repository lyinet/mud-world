"""W1 读端点可见面：服务模式匿名只看公共摘要，本人令牌看完整状态。

契约见 docs/AUTH-API-W1.md §3。legacy 模式（auth off）保持匿名全量，
用于本地试玩/selfcheck，不受影响。
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

# 匿名响应体里绝不允许出现的私有字段键（防回归 grep）。
FORBIDDEN_KEYS = (
    "wallet",
    "items",
    "skills",
    "relations",
    "active_quests",
    "growth",
    "today",
)


def _request(base, path, method="GET", body=None, token=""):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(base, path, method="GET", body=None, token=""):
    status, raw = _request(base, path, method, body, token)
    return status, json.loads(raw.decode("utf-8")), raw


def _login(base, username, password="secret123"):
    status, payload, _ = _json(base, "/api/auth/register", "POST",
                               {"username": username, "password": password})
    assert status in (200, 409), status
    status, payload, _ = _json(base, "/api/auth/login", "POST",
                               {"username": username, "password": password})
    assert status == 200, payload
    return str(payload["token"])


def _enter(base, token, name="测试甲", archetype="游侠"):
    status, payload, _ = _json(base, "/api/player/enter", "POST",
                               {"display_name": name, "archetype": archetype}, token)
    assert status == 200 and payload["ok"] is True, payload


@pytest.fixture()
def service(tmp_path):
    session = WorldSession(
        ROOT, run_id="read-scope", run_dir=tmp_path / "run", seed=42,
    )
    accounts = AccountStore(tmp_path / "service.sqlite", iterations=1_000, token_ttl=3_600)
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        token = _login(base, "u_alice")
        _enter(base, token, "阿甲", "游侠")
        yield base, token
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()


def test_anonymous_state_is_whitelisted_public_summary(service):
    base, _ = service
    status, payload, raw = _json(base, "/api/player/state?account_id=u_alice")
    assert status == 200 and payload["ok"] is True
    # 白名单精确比对：多一个键（将来新增私有字段）就失败。
    assert set(payload) == set(PUBLIC_STATE_KEYS)
    body = raw.decode("utf-8")
    leaked = [key for key in FORBIDDEN_KEYS if key in body]
    assert not leaked, f"匿名响应体泄漏私有字段: {leaked}"


def test_token_reads_full_own_state(service):
    base, token = service
    status, payload, _ = _json(base, "/api/player/state?account_id=u_alice", token=token)
    assert status == 200 and payload["ok"] is True
    player = payload["player"]
    assert player["wallet"]["money"] == 300
    assert {"money", "items"} <= set(player["wallet"])
    assert "skills" in player and "relations" in player
    assert "active_quests" in player


def test_cross_account_read_with_token_is_403(service):
    base, token = service
    bob = _login(base, "u_bob")
    _enter(base, bob, "阿乙", "游侠")
    status, payload, _ = _json(base, "/api/player/state?account_id=u_alice", token=bob)
    assert status == 403 and payload["code"] == "auth_account_mismatch"
    status, payload, _ = _json(base, "/api/player/state", token=bob)
    assert status == 200 and payload["player"]["account_id"] == "u_bob"
    # 令牌账号是唯一事实源：显式传自己也没问题。
    status, payload, _ = _json(base, "/api/player/state?account_id=u_bob", token=bob)
    assert status == 200 and payload["player"]["account_id"] == "u_bob"


def test_intents_require_token_in_service_mode(service):
    base, token = service
    path = "/api/player/intents/req-001?account_id=u_alice"
    status, payload, _ = _json(base, path)
    assert status == 401 and payload["code"] == "auth_missing_token"
    bob = _login(base, "u_bob")
    status, payload, _ = _json(base, path, token=bob)
    assert status == 403 and payload["code"] == "auth_account_mismatch"
    status, payload, _ = _json(base, path, token=token)
    assert status == 200 and payload["found"] is False and payload["reject_reason"] == "intent_not_found"


def test_anonymous_messages_empty_and_quests_public_only(service):
    base, _ = service
    status, payload, _ = _json(base, "/api/player/messages?account_id=u_alice")
    assert status == 200 and payload["messages"] == []

    status, payload, _ = _json(base, "/api/player/quests?account_id=u_alice")
    assert status == 200 and payload["active"] == []
    for row in payload["open"]:
        assert "reward_money" not in row and "reward_relation" not in row


def test_legacy_mode_keeps_anonymous_full_state(tmp_path):
    session = WorldSession(ROOT, run_id="read-scope-legacy", run_dir=tmp_path / "run", seed=42)
    server = create_server(session, "127.0.0.1", 0, accounts=None, auth_required=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, payload, _ = _json(
            base, "/api/player/enter", "POST",
            {"account_id": "u_demo", "display_name": "陈捕快", "archetype": "捕快"},
        )
        assert status == 200 and payload["ok"] is True
        status, payload, _ = _json(base, "/api/player/state?account_id=u_demo")
        assert status == 200 and payload["ok"] is True
        # legacy 本地试玩：匿名仍能读完整状态（钱包/技能/关系都在）。
        assert payload["player"]["wallet"]["money"] == 300
        assert "skills" in payload["player"] and "relations" in payload["player"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()
