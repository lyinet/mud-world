"""W1 断线重连：60 秒内同会话恢复；超时新会话但存档仍在。"""
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
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _start(tmp_path, clock, run_dir=None, store_path=None):
    session = WorldSession(
        ROOT, run_id="w1-reconnect", run_dir=run_dir or (tmp_path / "run"), seed=42
    )
    accounts = AccountStore(
        store_path or (tmp_path / "service.sqlite"),
        iterations=1_000,
        token_ttl=3_600,
        reconnect_grace=60,
        now_fn=clock,
    )
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return session, accounts, server, thread, base


def _stop(server, thread, session, accounts):
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
    accounts.close()
    session.close()


def _login(base):
    assert _request(base, "/api/auth/register", body={"username": "u_demo", "password": "secret123"})[0] in (200, 409)
    status, payload = _request(base, "/api/auth/login", body={"username": "u_demo", "password": "secret123"})
    assert status == 200 and payload["ok"] is True
    return payload["token"]


def _play(base, token):
    """进世界、接任务、工作、移动、对话；留下可被逐项断言的状态。"""
    status, payload = _request(
        base, "/api/player/enter",
        body={"display_name": "测试甲", "archetype": "游侠"}, token=token,
    )
    assert status == 200 and payload["ok"] is True
    state = _request(base, "/api/player/state?account_id=u_demo", method="GET")[1]
    acceptable = [row["id"] for row in state["quest_board"]["rows"] if row["acceptable"]]
    assert acceptable
    assert _request(base, "/api/player/quest/accept", body={"quest": acceptable[0]}, token=token)[1]["ok"]
    npc = state["present_npcs"][0]["id"]
    assert _request(base, "/api/player/talk", body={"target": npc, "message": "你好。"}, token=token)[1]["ok"]
    assert _request(base, "/api/player/move", body={"to": "academy"}, token=token)[1]["ok"]
    assert _request(
        base, "/api/player/action", body={"verb": "work", "args": {"hours": 8}}, token=token
    )[1]["ok"]


def _snapshot(state_payload):
    player = state_payload["player"]
    growth = state_payload["growth"]
    return {
        "location": player["location"],
        "money": player["wallet"]["money"],
        "items": tuple(sorted((row["item"], row["qty"]) for row in player["wallet"]["items"])),
        "quests": tuple(player["active_quests"]),
        "skills": dict(player["skills"]),
        "reputation": growth["reputation"]["value"],
        "assets": growth["assets"]["value"],
        "relations": tuple(
            (row["target"], row["kind"], row["affection"], row["trust"])
            for row in player["relations"]
        ),
    }


def test_reconnect_within_60s_restores_same_session(tmp_path):
    clock = Clock()
    session, accounts, server, thread, base = _start(tmp_path, clock)
    try:
        token = _login(base)
        _play(base, token)
        status, left = _request(base, "/api/player/leave", body={}, token=token)
        assert status == 200 and left["ok"] is True and left["session_disconnected"] is True
        tick_at_disconnect = session.state.tick
        before = _snapshot(_request(base, "/api/player/state?account_id=u_demo", method="GET")[1])

        clock.t += 59
        status, res = _request(base, "/api/player/reconnect", body={}, token=token)
        assert status == 200 and res["ok"] is True
        assert res["resumed"] is True and res["new_session"] is False
        assert res["session_id"] == left["session_id"]
        assert res["tick_at_disconnect"] == tick_at_disconnect
        assert res["tick"] == tick_at_disconnect  # 重连不推进 tick
        assert _snapshot(res["state"]) == before
        assert res["state"]["player"]["online"] is True

        # 同一令牌继续可写。
        assert _request(base, "/api/player/move", body={"to": "market"}, token=token)[1]["ok"]
    finally:
        _stop(server, thread, session, accounts)


def test_reconnect_after_60s_new_session_keeps_save(tmp_path):
    clock = Clock()
    session, accounts, server, thread, base = _start(tmp_path, clock)
    try:
        token = _login(base)
        _play(base, token)
        status, left = _request(base, "/api/player/leave", body={}, token=token)
        assert status == 200 and left["ok"] is True
        tick_at_disconnect = session.state.tick
        before = _snapshot(_request(base, "/api/player/state?account_id=u_demo", method="GET")[1])

        clock.t += 61
        status, res = _request(base, "/api/player/reconnect", body={}, token=token)
        assert status == 200 and res["ok"] is True
        assert res["resumed"] is True and res["new_session"] is True
        assert res["session_id"] != left["session_id"]
        assert res["token"] != token
        assert _snapshot(res["state"]) == before
        assert res["tick_at_disconnect"] == tick_at_disconnect

        # 旧令牌失效；新令牌可用。
        status, old = _request(base, "/api/auth/me", method="GET", token=token)
        assert status == 401 and old["code"] == "auth_expired_token"
        status, new = _request(base, "/api/auth/me", method="GET", token=res["token"])
        assert status == 200 and new["account_id"] == "u_demo"
    finally:
        _stop(server, thread, session, accounts)


def test_reconnect_after_process_restart_keeps_save(tmp_path):
    clock = Clock()
    run_dir = tmp_path / "run"
    store_path = tmp_path / "service.sqlite"
    session, accounts, server, thread, base = _start(
        tmp_path, clock, run_dir=run_dir, store_path=store_path
    )
    try:
        token = _login(base)
        _play(base, token)
        status, left = _request(base, "/api/player/leave", body={}, token=token)
        assert status == 200 and left["ok"] is True
        tick_at_disconnect = session.state.tick
        before = _snapshot(_request(base, "/api/player/state?account_id=u_demo", method="GET")[1])
    finally:
        _stop(server, thread, session, accounts)

    # 重启：同一 events.sqlite + 同一 service.sqlite。
    clock.t += 30
    session2, accounts2, server2, thread2, base2 = _start(
        tmp_path, clock, run_dir=run_dir, store_path=store_path
    )
    try:
        assert session2.replayed is True
        status, res = _request(base2, "/api/player/reconnect", body={}, token=token)
        assert status == 200 and res["ok"] is True
        assert res["new_session"] is False
        assert _snapshot(res["state"]) == before
    finally:
        _stop(server2, thread2, session2, accounts2)
