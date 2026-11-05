"""W4 世界接线：自走与玩家共用同一个世界、同一条 seq、同一份 events.sqlite。"""
from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from wsim.api.server import create_server
from wsim.api.session import WORLD_FEED_IMPORTANT_VERBS, WorldSession

ROOT = Path(__file__).resolve().parents[1]
FEED_KEYS = {"tick", "actor", "verb", "place", "summary", "important"}


def _world(tmp_path: Path) -> Path:
    root = tmp_path / "world"
    root.mkdir(parents=True, exist_ok=True)
    (root / "content").symlink_to(ROOT / "content", target_is_directory=True)
    (root / "config").symlink_to(ROOT / "config", target_is_directory=True)
    return root


def _session(tmp_path: Path, run_id: str) -> WorldSession:
    root = _world(tmp_path)
    return WorldSession(root, run_id=run_id, run_dir=root / "runs" / run_id, seed=42)


def _serve(session: WorldSession):
    server = create_server(session, "127.0.0.1", 0, auth_required=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _get(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _seqs(session: WorldSession) -> list[int]:
    with session.lock:
        return [int(ev.seq) for ev in session.store.read_all()]


def test_shared_single_writer_single_ledger(tmp_path: Path) -> None:
    session = _session(tmp_path, "wire-shared")
    scheduler = session.start_ambient(cpu_ratio=0.0)
    try:
        scheduler.run_ticks(30)
        # 只有一份事件日志：没有隔壁世界的 ambient/events.sqlite。
        assert not (session.run_dir / "ambient" / "events.sqlite").exists()
        assert session.run_dir.joinpath("events.sqlite").exists()
        assert scheduler.writer is session.writer
        assert scheduler.store is session.store
        seqs = _seqs(session)
        assert seqs == list(range(1, len(seqs) + 1))
        # 自走走的是同一个世界 tick。
        assert session.state.tick > 0
        ticks = [int(ev.tick) for ev in session.store.read_all()]
        assert ticks == sorted(ticks)
    finally:
        session.close()


def test_player_action_and_ambient_do_not_double_walk(tmp_path: Path) -> None:
    session = _session(tmp_path, "wire-clock")
    scheduler = session.start_ambient(cpu_ratio=0.0)
    try:
        scheduler.run_ticks(10)
        session.perform("u_demo", "enter", {"display_name": "陈捕快", "archetype": "捕快"})
        t1 = int(session.state.tick)
        scheduler.run_ticks(5)
        t2 = int(session.state.tick)
        # 玩家动作推进 6 tick 后，自走只在其上再推进 5 tick，不补跑玩家跨过的 tick。
        assert t2 - t1 == 5
        scheduler.run_ticks(7)
        assert int(session.state.tick) - t2 == 7
        seqs = _seqs(session)
        assert len(seqs) == len(set(seqs))
        ticks = [int(ev.tick) for ev in session.store.read_all()]
        assert ticks == sorted(ticks)
    finally:
        session.close()


def test_online_npc_events_keep_pace(tmp_path: Path) -> None:
    offline = _session(tmp_path / "off", "wire-offline")
    scheduler = offline.start_ambient(cpu_ratio=0.0)
    try:
        scheduler.run_ticks(300)
        offline_npc = int(scheduler._npc_events_total)
    finally:
        offline.close()

    online = _session(tmp_path / "on", "wire-online")
    scheduler = online.start_ambient(cpu_ratio=0.0)
    try:
        online.perform("u_demo", "enter", {"display_name": "陈捕快", "archetype": "捕快"})
        scheduler.run_ticks(300)
        online_npc = int(scheduler._npc_events_total)
    finally:
        online.close()

    assert offline_npc > 0
    assert online_npc >= 0.8 * offline_npc


def test_world_feed_anonymous_public_only(tmp_path: Path) -> None:
    session = _session(tmp_path, "wire-feed")
    scheduler = session.start_ambient(cpu_ratio=0.0)
    try:
        session.perform("u_demo", "enter", {"display_name": "陈捕快", "archetype": "捕快"})
        scheduler.run_ticks(30)
        server, thread, base = _serve(session)
        try:
            # 服务模式（auth_required=True）下匿名可读。
            feed = _get(base + "/api/world/feed?limit=20")
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        assert feed["ok"] is True
        assert feed["events"]
        for item in feed["events"]:
            assert set(item) == FEED_KEYS
            assert isinstance(item["tick"], int)
            assert isinstance(item["important"], bool)
            assert 0 < len(item["summary"]) <= 40
            assert "player:" not in item["actor"]
            assert not item["verb"].startswith("system")
            assert item["important"] == (item["verb"] in WORLD_FEED_IMPORTANT_VERBS)
    finally:
        session.close()


@pytest.mark.parametrize("value", ["0", "off", "false", "no", ""])
def test_ambient_disabled_keeps_legacy_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("XKX_AMBIENT", value)
    session = _session(tmp_path, f"wire-off-{value or 'empty'}")
    try:
        assert session.start_ambient_from_env() is None
        assert session.ambient is None
        session.perform("u_demo", "enter", {"display_name": "陈捕快", "archetype": "捕快"})
        # 旧版语义：玩家动作仍然推进 6 tick。
        assert int(session.state.tick) == 6
    finally:
        session.close()


def test_health_tick_is_same_world(tmp_path: Path) -> None:
    session = _session(tmp_path, "wire-health")
    session.start_ambient(cpu_ratio=0.0)
    try:
        session.ambient.run_ticks(10)
        server, thread, base = _serve(session)
        try:
            health = _get(base + "/api/health")
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        assert health["ambient"] == "running"
        assert health["ambient_tick"] == int(session.state.tick)
        assert health["ok"] is True and health["profile_version"] == 1
    finally:
        session.close()
