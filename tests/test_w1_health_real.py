"""/api/health 真实探活：seq 连续、磁盘阈值、写者停滞、库不可读。"""
from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
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


def _serve(session, **kwargs):
    server = create_server(session, "127.0.0.1", 0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_health_normal_200(tmp_path):
    root = _api_world(tmp_path)
    session = WorldSession(root, run_id="h-ok", run_dir=root / "runs" / "h-ok", seed=42)
    server, thread, base = _serve(session)
    try:
        status, health = _get(base + "/api/health")
        assert status == 200 and health["ok"] is True
        assert health["storage"]["events_ok"] is True
        assert health["storage"]["seq_gap"] == 0
        assert health["storage"]["integrity"] == "ok"
        assert health["storage"]["disk_free_mb"] > 0
        assert health["storage"]["last_commit_age_s"] >= 0
        assert health["world"]["new_world"] is True
        assert health["world"]["tick"] == session.state.tick
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_health_disk_threshold_503(tmp_path):
    root = _api_world(tmp_path)
    session = WorldSession(root, run_id="h-disk", run_dir=root / "runs" / "h-disk", seed=42)
    server, thread, base = _serve(session, disk_min_free_pct=100.0)
    try:
        status, health = _get(base + "/api/health")
        assert status == 503 and health["ok"] is False
        assert health["storage"]["events_ok"] is True
        assert health["storage"]["disk_free_pct"] < 100.0
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_health_seq_gap_503(tmp_path):
    root = _api_world(tmp_path)
    run_dir = root / "runs" / "h-gap"
    session = WorldSession(root, run_id="h-gap", run_dir=run_dir, seed=42)
    total = session.store.count()
    session.close()
    # 人为制造断洞：把最后一条事件的 seq 抬高 5，COUNT(*) 与回放顺序不变、MAX(seq) 出现缺口。
    conn = sqlite3.connect(run_dir / "events.sqlite")
    conn.execute("UPDATE events SET seq = seq + 5 WHERE seq = ?", (total,))
    conn.commit()
    conn.close()
    session2 = WorldSession(root, run_id="h-gap", run_dir=run_dir, seed=42)
    server, thread, base = _serve(session2)
    try:
        status, health = _get(base + "/api/health")
        assert status == 503 and health["ok"] is False
        assert health["storage"]["seq_gap"] >= 1
        assert health["storage"]["events_ok"] is False
        assert health["world"]["new_world"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session2.close()


def test_health_writer_stall_503(tmp_path):
    root = _api_world(tmp_path)
    session = WorldSession(root, run_id="h-stall", run_dir=root / "runs" / "h-stall", seed=42)
    server, thread, base = _serve(session, writer_idle_max_s=-1.0)
    try:
        status, health = _get(base + "/api/health")
        assert status == 503 and health["ok"] is False
        assert health["storage"]["last_commit_age_s"] >= 0
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()
