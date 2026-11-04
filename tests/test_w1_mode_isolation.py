"""W1 模式隔离：有 deploy/service.yaml 强制鉴权，没有则保持 legacy 匿名；
不存在“配置存在但仍匿名写”的路径。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from wsim.api.auth import AccountStore
from wsim.api.server import create_server, resolve_auth_required
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


def _world_root(tmp_path: Path, marker: bool) -> Path:
    root = tmp_path / "world"
    root.mkdir()
    (root / "content").symlink_to(ROOT / "content", target_is_directory=True)
    (root / "config").symlink_to(ROOT / "config", target_is_directory=True)
    if marker:
        (root / "deploy").mkdir()
        (root / "deploy" / "service.yaml").write_text("service:\n  auth_required: true\n", encoding="utf-8")
    return root


def _post(base, path, body):
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


def _serve(session, root, marker, tmp_path):
    auth_required = resolve_auth_required(root, "auto")
    assert auth_required is marker
    accounts = AccountStore(tmp_path / "service.sqlite", iterations=1_000) if marker else None
    server = create_server(session, "127.0.0.1", 0, accounts=accounts, auth_required=auth_required)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}", accounts


def test_resolver_modes(tmp_path):
    assert resolve_auth_required(tmp_path, "auto") is False
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "service.yaml").write_text("service: {}\n", encoding="utf-8")
    assert resolve_auth_required(tmp_path, "auto") is True
    assert resolve_auth_required(tmp_path, "off") is False
    assert resolve_auth_required(tmp_path / "missing", "on") is True


def test_marker_present_has_no_anonymous_write_path(tmp_path):
    root = _world_root(tmp_path, marker=True)
    session = WorldSession(root, run_id="w1-iso", run_dir=root / "runs" / "w1-iso", seed=42)
    server, thread, base, accounts = _serve(session, root, True, tmp_path)
    try:
        status, payload = _post(base, "/api/player/enter", {"account_id": "u_demo"})
        assert status == 401 and payload["code"] == "auth_missing_token"
        info = json.loads(urllib.request.urlopen(base + "/api/service/info", timeout=10).read())
        assert info["auth_required"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        if accounts is not None:
            accounts.close()
        session.close()


def test_no_marker_keeps_legacy_anonymous(tmp_path):
    root = _world_root(tmp_path, marker=False)
    session = WorldSession(root, run_id="w1-iso", run_dir=root / "runs" / "w1-iso", seed=42)
    server, thread, base, accounts = _serve(session, root, False, tmp_path)
    try:
        status, payload = _post(
            base, "/api/player/enter",
            {"account_id": "u_demo", "display_name": "陈捕快", "archetype": "捕快"},
        )
        assert status == 200 and payload["ok"] is True
        info = json.loads(urllib.request.urlopen(base + "/api/service/info", timeout=10).read())
        assert info["auth_required"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()
