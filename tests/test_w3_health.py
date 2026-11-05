"""W3 health 契约：只追加 ambient，现有键与语义不变。"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


def _api_world(tmp_path: Path) -> Path:
    root = tmp_path / "world"
    root.mkdir(exist_ok=True)
    (root / "content").symlink_to(ROOT / "content", target_is_directory=True)
    (root / "config").symlink_to(ROOT / "config", target_is_directory=True)
    return root


def _serve(session):
    server = create_server(session, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _write_status(run_dir: Path, state: str, tick: int, reason: str = "") -> None:
    out = Path(run_dir) / "ambient"
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    (out / "ambient_status.json").write_text(
        json.dumps(
            {
                "state": state,
                "reason": reason,
                "tick": tick,
                "last_tick_at": now,
                "updated_at": now,
                "pid": os.getpid(),
                "speed": 1.0,
            }
        ),
        encoding="utf-8",
    )


def test_health_only_adds_ambient_fields(tmp_path):
    root = _api_world(tmp_path)
    session = WorldSession(root, run_id="w3-health", run_dir=root / "runs" / "w3-health", seed=42)
    _write_status(session.run_dir, "running", 7)
    server, thread, base = _serve(session)
    try:
        health = _get(base + "/api/health")
        assert health["ok"] is True
        assert health["mode"] == "live"
        assert health["readonly"] is False
        assert health["profile_version"] == 1
        assert health["upstream_ok"] is True
        assert health["ambient"] == "running"
        # 自走与玩家共用同一份事件日志：ambient_tick 必须等于世界 tick，
        # 而不是状态文件里的旧值（那会是隔壁世界的时钟）。
        assert health["ambient_tick"] == session.state.tick
        assert health["ambient_last_tick_at"]
        info = _get(base + "/api/service/info")
        # 原键语义不变，只追加 ambient。
        assert info["ok"] is True and info["profile_version"] == 1
        assert info["run"] == "w3-health" and info["tick"] == 0
        assert "auth_required" in info
        assert info["ambient"] == "running"
        # 状态文件缺失 => stopped，但不影响 ok/profile_version。
        (session.run_dir / "ambient" / "ambient_status.json").unlink()
        health2 = _get(base + "/api/health")
        assert health2["ambient"] == "stopped"
        assert health2["ok"] is True and health2["profile_version"] == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def _load_ui_server():
    path = ROOT / "ui" / "player" / "server.py"
    spec = importlib.util.spec_from_file_location("ui_player_server_w3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ui_health_helpers_add_ambient_only(tmp_path):
    module = _load_ui_server()
    upstream = {
        "ok": True,
        "profile_version": 1,
        "ambient": "paused",
        "ambient_tick": 9,
        "ambient_last_tick_at": "2026-11-05T12:00:00+08:00",
        "ambient_reason": "cpu_ratio_over_limit",
        "ambient_pid": 123,
        "ambient_speed": 1.0,
    }
    ambient = module._upstream_ambient(upstream)
    assert ambient["ambient"] == "paused" and ambient["ambient_tick"] == 9
    assert module._upstream_ambient(None)["ambient"] == "stopped"
    assert module._service_info_ok(upstream) is True
    assert module._service_info_ok({"ok": True}) is False
    assert module._local_ambient(tmp_path)["ambient"] == "stopped"
    _write_status(tmp_path, "running", 3)
    assert module._local_ambient(tmp_path)["ambient"] == "running"
