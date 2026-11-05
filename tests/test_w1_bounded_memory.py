"""W1 无界内存结构上限：writer 幂等缓存与 session 请求回看缓存。"""
from __future__ import annotations

from pathlib import Path

from wsim.api.session import WorldSession
from wsim.core.models import Intent
from wsim.world.state import WorldState
from wsim.world.store import EventStore
from wsim.world.writer import Writer

ROOT = Path(__file__).resolve().parents[1]


class _NoActions:
    def get(self, verb):
        return None


def test_writer_seen_cache_is_bounded(tmp_path):
    store = EventStore(tmp_path / "events.sqlite", check_same_thread=False)
    try:
        writer = Writer(WorldState(locations={}), store, _NoActions(), 42, seen_max=5)
        for i in range(20):
            writer.submit(
                Intent(
                    actor="player:p1",
                    verb="noop",
                    args={"i": i},
                    tick=0,
                    source="player",
                    intent_id=f"i:test-{i}",
                )
            )
        assert len(writer._seen) <= 5
        assert len(store.read_all()) == 20
    finally:
        store.close()


def test_session_recent_cache_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("XKX_RECENT_INTENTS_MAX", "3")
    session = WorldSession(ROOT, run_id="bounded", run_dir=tmp_path / "run", seed=42)
    try:
        for i in range(10):
            session.perform("u_demo", "noop", {"i": i}, request_id=f"r{i}")
        assert len(session.requests) <= 3
        assert len(session.intents) <= 6
        for key in session.requests:
            account, _, request_id = key.partition(":")
            assert account == "u_demo" and request_id
    finally:
        session.store.close()
