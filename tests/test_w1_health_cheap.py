"""探活不得做重活：`/api/health` 请求路径禁止 integrity_check / read_all / session.lock。"""
from __future__ import annotations

import threading
from pathlib import Path

from wsim.api.health import HealthProbe, probe_from_session
from wsim.api.server import create_server
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


def _session(tmp_path: Path, run_id: str = "cheap-health") -> WorldSession:
    root = tmp_path / "world"
    root.mkdir(exist_ok=True)
    (root / "content").symlink_to(ROOT / "content", target_is_directory=True)
    (root / "config").symlink_to(ROOT / "config", target_is_directory=True)
    return WorldSession(root, run_id=run_id, run_dir=root / "runs" / run_id, seed=42)


class _ExplodingLock:
    """一旦被 health 路径获取就失败，用于证明它不是读锁的参与者。"""

    def __enter__(self):
        raise AssertionError("health 请求路径获取了 session.lock")

    def __exit__(self, *_exc):
        return False

    def acquire(self, *_args, **_kwargs):
        raise AssertionError("health 请求路径获取了 session.lock")

    def release(self):
        return None


def test_snapshot_uses_cached_integrity_and_never_takes_session_lock(tmp_path, monkeypatch):
    session = _session(tmp_path)
    probe = HealthProbe(session)

    def _boom():
        raise AssertionError("snapshot() 不得执行全库 PRAGMA integrity_check")

    monkeypatch.setattr(probe, "run_integrity", _boom)
    session.lock = _ExplodingLock()  # type: ignore[assignment]
    snapshot = probe.snapshot()
    assert snapshot["ok"] is True
    # 没播种过：缓存为 unknown，仍按“未知不等于已知损坏”判健康。
    assert snapshot["storage"]["integrity"] == "unknown"
    assert snapshot["storage"]["integrity_checked_at"] is None
    assert snapshot["storage"]["db_openable"] is True
    assert snapshot["storage"]["disk_writable"] is True
    probe.close()


def test_snapshot_executes_no_integrity_sql_on_its_readonly_connection(tmp_path):
    session = _session(tmp_path)
    probe = HealthProbe(session)
    probe._query("SELECT 1")  # 预热独立只读连接
    seen: list[str] = []
    assert probe._ro_conn is not None
    probe._ro_conn.set_trace_callback(seen.append)
    probe.snapshot()
    lowered = " ".join(seen).lower()
    assert "integrity_check" not in lowered
    assert "select seq from events order by seq desc limit 2" in lowered
    probe.close()


def test_snapshot_never_calls_read_all(tmp_path, monkeypatch):
    session = _session(tmp_path)
    probe = HealthProbe(session)
    monkeypatch.setattr(
        session.store, "read_all",
        lambda: (_ for _ in ()).throw(AssertionError("health 调用了 read_all")),
    )
    assert probe.snapshot()["storage"]["events_ok"] is True
    probe.close()


def test_startup_integrity_default_seeds_ok(tmp_path, monkeypatch):
    monkeypatch.delenv("XKX_STARTUP_INTEGRITY", raising=False)
    session = _session(tmp_path, "seed-ok")
    probe = probe_from_session(session)
    assert probe.integrity() == "ok"
    snapshot = probe.snapshot()
    assert snapshot["storage"]["integrity"] == "ok"
    assert snapshot["storage"]["integrity_checked_at"] is not None
    probe.close()


def test_startup_integrity_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("XKX_STARTUP_INTEGRITY", "0")
    session = _session(tmp_path, "seed-off")
    probe = probe_from_session(session)
    assert probe.integrity() == "unknown"
    probe.close()


def test_create_server_seeds_integrity_once_for_requests(tmp_path, monkeypatch):
    monkeypatch.delenv("XKX_STARTUP_INTEGRITY", raising=False)
    session = _session(tmp_path, "seed-server")
    server = create_server(session, "127.0.0.1", 0)
    try:
        assert server.health_probe.integrity() == "ok"  # type: ignore[attr-defined]
    finally:
        server.server_close()
        session.close()


def test_health_endpoint_stays_responsive_with_writer_lock_held(tmp_path, monkeypatch):
    """写锁被长期持有时，探活仍应立刻返回——它只读独立连接与缓存。"""
    monkeypatch.delenv("XKX_STARTUP_INTEGRITY", raising=False)
    session = _session(tmp_path, "lock-free")
    server = create_server(session, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    held = threading.Event()
    release = threading.Event()

    def _hold_lock():
        with session.lock:
            held.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    try:
        assert held.wait(timeout=2)
        import json
        import urllib.request

        with urllib.request.urlopen(base + "/api/health", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200 and payload["ok"] is True
        assert payload["storage"]["integrity"] == "ok"
    finally:
        release.set()
        holder.join(timeout=2)
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        session.close()


def test_intent_and_messages_fallbacks_use_targeted_queries(tmp_path, monkeypatch):
    """请求路径不再 `read_all`：定向查询必须与全量遍历的首次命中/过滤结果一致。"""
    session = _session(tmp_path, "targeted")
    assert session.perform(
        "u_probe", "enter",
        {"display_name": "测试甲", "archetype": "游侠"}, request_id="req-1",
    )["ok"]
    pid = session._pid("u_probe")
    all_events = session.store.read_all()
    first = next(e for e in all_events if e.actor == pid)
    assert session.store.find_intent(first.intent_id, pid).seq == first.seq
    since = 3
    expected = [e.seq for e in all_events if e.actor == pid and int(e.tick) > since]
    assert [e.seq for e in session.store.events_for_actor_since(pid, since)] == expected

    # 清掉最近缓存，强制走事件日志回退分支。
    session.intents.clear()
    session.requests.clear()
    monkeypatch.setattr(
        session.store, "read_all",
        lambda: (_ for _ in ()).throw(AssertionError("请求路径调用了 read_all")),
    )
    view = session.intent("u_probe", first.intent_id)
    assert view["found"] is True and view["event_seq"] == first.seq
    messages = session.messages("u_probe", 0)
    assert messages["next_since_tick"] == int(session.state.tick)
    session.close()

