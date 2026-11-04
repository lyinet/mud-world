"""W1 单区域 200 席：第 201 个连接必须被拒（429 + player_cap_reached，不是 500）。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from wsim.api.auth import AccountStore
from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


def _post(base, path, body, token=""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_201st_connection_rejected_with_reason_code(tmp_path):
    session = WorldSession(
        ROOT, run_id="w1-cap", run_dir=tmp_path / "run", seed=42, endow=False
    )
    accounts = AccountStore(tmp_path / "service.sqlite", iterations=1_000)
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        tokens: list[str] = []
        for i in range(201):
            username = f"u{i:03d}"
            assert accounts.register(username, "secret123")["ok"] is True
            login = accounts.login(username, "secret123")
            assert login["ok"] is True
            tokens.append(login["token"])

        for i in range(200):
            status, payload = _post(
                base, "/api/player/enter",
                {"display_name": f"玩家{i:03d}", "archetype": "游侠"},
                token=tokens[i],
            )
            assert status == 200 and payload["ok"] is True, (i, status, payload)

        assert len(session.state.players) == 200

        status, payload = _post(
            base, "/api/player/enter",
            {"display_name": "玩家200", "archetype": "游侠"},
            token=tokens[200],
        )
        assert status == 429, (status, payload)
        assert payload["ok"] is False
        assert payload["reject_reason"] == "player_cap_reached"
        assert payload["code"] == "player_cap_reached"
        assert "席位" in payload["reason_text"]
        assert "player:u200" not in session.state.players
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        accounts.close()
        session.close()
