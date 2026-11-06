"""事件库按阈值切库归档：seq 全局连续、归档独立可查、恢复选对分片。"""
from __future__ import annotations

from pathlib import Path

from wsim.core.models import Event
from wsim.world.store import EventStore


def _event(seq: int) -> Event:
    return Event(
        seq=seq,
        tick=seq,
        day=seq // 144,
        intent_id=f"i:{seq}",
        actor="system",
        verb="system_tick",
        args={},
        effects=[],
        outcome="ok",
        reason="",
        source="system",
    )


def _append(store: EventStore, start: int, count: int) -> None:
    for seq in range(start, start + count):
        store.append(_event(seq))


def test_rotation_keeps_global_seq_and_archives_are_queryable(tmp_path, monkeypatch):
    monkeypatch.setenv("XKX_EVENT_SEGMENT_MAX_EVENTS", "3")
    store = EventStore(tmp_path / "events.sqlite")
    store.set_meta("content_hash", "abc")
    _append(store, 1, 10)
    archives = sorted(p.name for p in Path(tmp_path).iterdir() if p.name.startswith("events-") and p.name.endswith(".sqlite"))
    assert archives == ["events-000001.sqlite", "events-000002.sqlite", "events-000003.sqlite"]
    assert store.count() == 10
    assert [e.seq for e in store.read_all()] == list(range(1, 11))
    # 读路径跨分片回溯：活动分片不足时向归档要最近事件。
    assert [r[0] for r in store.recent_rows("actor='system'", (), 5)] == [10, 9, 8, 7, 6]
    # 归档文件可独立打开查询/审计（不依赖活动库）。
    archived = EventStore(Path(tmp_path) / archives[0])
    assert [e.seq for e in archived.read_all()] == [1, 2, 3]
    assert archived.count() == 3
    archived.close()
    # 重启后继续接续写，seq 不重号，身份 meta 也随分片继承。
    store.close()
    reopened = EventStore(tmp_path / "events.sqlite")
    assert reopened.count() == 10
    assert reopened.get_meta("content_hash") == "abc"
    _append(reopened, 11, 1)
    assert reopened.count() == 11
    assert [e.seq for e in reopened.read_all()] == list(range(1, 12))
    reopened.close()


def test_size_threshold_also_triggers_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv("XKX_EVENT_SEGMENT_MAX_EVENTS", "1000000")
    monkeypatch.setenv("XKX_EVENT_SEGMENT_MAX_BYTES", "1")
    store = EventStore(tmp_path / "events.sqlite")
    _append(store, 1, 300)
    archives = [p for p in Path(tmp_path).iterdir() if p.name.startswith("events-") and p.name.endswith(".sqlite")]
    assert archives, "size 阈值应在 256 次检查点触发一次切库"
    assert [e.seq for e in store.read_all()] == list(range(1, 301))
    store.close()


def test_recovery_picks_latest_archive_when_active_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XKX_EVENT_SEGMENT_MAX_EVENTS", "3")
    store = EventStore(tmp_path / "events.sqlite")
    _append(store, 1, 7)
    assert sorted(p.name for p in Path(tmp_path).iterdir() if p.name.startswith("events-") and p.name.endswith(".sqlite")) == [
        "events-000001.sqlite",
        "events-000002.sqlite",
    ]
    store.close()
    # 模拟切库中途崩溃：活动分片缺失，只剩归档。
    for name in ("events.sqlite", "events.sqlite-wal", "events.sqlite-shm"):
        path = Path(tmp_path) / name
        if path.exists():
            path.unlink()
    recovered = EventStore(tmp_path / "events.sqlite")
    assert recovered.count() == 6
    assert [e.seq for e in recovered.read_all()] == list(range(1, 7))
    _append(recovered, 7, 1)
    assert recovered.count() == 7
    assert [e.seq for e in recovered.read_all()] == list(range(1, 8))
    recovered.close()
