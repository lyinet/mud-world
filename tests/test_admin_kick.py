"""管理强制离线：鉴权、响应码、审计字段与脱战清理。

规格：`specs/player-presence-timeout/spec.md` D7。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]
PATH = "/api/admin/players/kick"


def _request(base: str, body: dict | None, token: str = "", operator: str = "") -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if operator:
        headers["X-Operator"] = operator
    req = urllib.request.Request(base + PATH, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("XKX_ADMIN_TOKEN", "secret-admin-token")
    session = WorldSession(
        ROOT, run_id="admin-kick", run_dir=tmp_path / "run", seed=42,
        endow=False, post_quests=False, npc_limit=0,
    )
    server = create_server(session, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, session
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def _enter(session: WorldSession, account: str, name: str) -> str:
    assert session.perform(
        account, "enter",
        {"account_id": account, "display_name": name, "archetype": "游侠"},
        advance=False,
    )["ok"]
    return f"player:{account}"


def test_admin_kick_auth_and_validation(world):
    base, session = world
    status, payload = _request(base, {"account": "u_kick"})
    assert status == 401 and payload["code"] == "invalid_admin_token"

    status, payload = _request(base, {"account": "u_kick"}, token="wrong-token", operator="ops")
    assert status == 401 and payload["code"] == "invalid_admin_token"

    status, payload = _request(base, {"account": "u_kick"}, token="secret-admin-token")
    assert status == 400 and payload["code"] == "missing_operator"

    status, payload = _request(base, {}, token="secret-admin-token", operator="ops-zhang")
    assert status == 400 and payload["code"] == "missing_account"

    status, payload = _request(
        base, {"account": "u_never"}, token="secret-admin-token", operator="ops-zhang"
    )
    assert status == 404 and payload["reject_reason"] == "player_not_entered"
    assert session.store.count() == 0 or all(e.verb != "player_leave" for e in session.store.read_all())


def test_admin_kick_disabled_without_token(world, monkeypatch):
    base, _session = world
    monkeypatch.delenv("XKX_ADMIN_TOKEN")
    status, payload = _request(base, {"account": "u_kick"}, token="anything", operator="ops")
    assert status == 403 and payload["code"] == "admin_disabled"


def test_admin_kick_success_audit_and_combat_clear(world):
    base, session = world
    pid = _enter(session, "u_kick", "被踢者")
    npc_id = next(eid for eid in sorted(session.state.entities) if not eid.startswith("player:"))
    npc = session.state.entities[npc_id]
    npc.location = session.state.entities[pid].location
    npc.combat_target = pid
    npc.combat_enemies = [pid]
    session.state.entities[pid].combat_target = npc_id
    session.state.entities[pid].combat_enemies = [npc_id]

    status, payload = _request(
        base,
        {"account": "u_kick", "reason": "operator_kick_cleanup"},
        token="secret-admin-token",
        operator="ops-zhang",
    )
    assert status == 200, payload
    assert payload["ok"] is True
    assert payload["presence"] == "offline"
    assert payload["operator"] == "ops-zhang"
    assert payload["event_hash"]

    assert session.state.players[pid].online is False
    assert session.state.entities[pid].presence == "offline"
    assert session.state.entities[pid].combat_target == ""
    assert session.state.entities[pid].combat_enemies == []
    npc = session.state.entities[npc_id]
    assert npc.combat_target == ""
    assert npc.combat_enemies == []

    event = session.store.read_all()[-1]
    assert event.verb == "player_leave"
    assert event.source == "admin"
    assert event.args["operator"] == "ops-zhang"
    assert event.args["reason"] == "operator_kick"
    assert event.args["note"] == "operator_kick_cleanup"
    effect = next(e for e in event.effects if e.get("op") == "player_presence")
    assert effect["data"]["reason"] == "operator_kick"
    assert effect["data"]["operator"] == "ops-zhang"

    status, payload = _request(
        base, {"account": "u_kick"}, token="secret-admin-token", operator="ops-zhang"
    )
    assert status == 409 and payload["reject_reason"] == "already_offline"
