"""事件表保留期治理测试（specs/xkx-parity/event-retention.spec.md §14）。

覆盖四类红线：
1. 关闭态逐字节一致、零新文件；
2. 归档 + 尾段重建状态一致、事件序列一致；
3. archive/snapshot/prune 三阶段中断恢复；
4. 裁剪边界（只裁完整日、只裁 seq 前缀、安全阀）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.api.persistence import replay_into  # noqa: E402
from wsim.core.canonical import dumps  # noqa: E402
from wsim.core.clock import TICKS_PER_DAY, day_of  # noqa: E402
from wsim.core.content import load_content  # noqa: E402
from wsim.core.models import Event  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.world.retention import (  # noqa: E402
    MaintenanceError,
    _rebuild_from_archive_only,
    archive_total_bytes,
    clear_journal,
    cutoff_tick_for,
    iter_archive,
    load_journal,
    load_manifest,
    load_snapshot,
    plan_archive_rotation,
    prefix_watermark,
    rotate_archives,
    run_maintenance,
    state_at_seq,
    verify_archive,
    write_journal,
)
from wsim.world.store import EventStore  # noqa: E402


# ---------------------------------------------------------------- helpers
def _make_event(seq: int, tick: int, actor: str = "player:p1") -> Event:
    return Event(
        seq=int(seq),
        tick=int(tick),
        day=day_of(int(tick)),
        intent_id=f"test:{seq}",
        actor=actor,
        verb="look",
        args={"seq": int(seq)},
        effects=[],
        outcome="ok",
        reason="",
        source="player",
    )


def _fill_store(store: EventStore, count: int, *, tick_offset: int = 0) -> None:
    for i in range(1, count + 1):
        store.append(_make_event(i, tick_offset + i - 1))


def _content_locations() -> tuple[dict, str]:
    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    return dict(content.locations), content.content_hash


def _maintain(store: EventStore, run: Path, **kwargs):
    kwargs.setdefault("locations", {})
    kwargs.setdefault("seed", 42)
    kwargs.setdefault("content_hash", "test-content-hash")
    return run_maintenance(run, store=store, **kwargs)


def _canonical_stream(events) -> bytes:
    return b"".join((dumps(e.to_dict()) + "\n").encode("utf-8") for e in events)


# ---------------------------------------------------------------- 1. 关闭态
def test_closed_state_is_byte_identical_and_writes_no_artifacts(tmp_path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    summary_a = run_headless(
        ROOT, RunConfig(days=1, seed=42, npc_limit=0, out_root=out_a, run_id="off")
    )
    summary_b = run_headless(
        ROOT, RunConfig(days=1, seed=42, npc_limit=0, out_root=out_b, run_id="off")
    )
    run_a = out_a / "off"
    run_b = out_b / "off"

    assert summary_a["hashes"] == summary_b["hashes"]
    assert (run_a / "events.jsonl").read_bytes() == (run_b / "events.jsonl").read_bytes()

    store_a = EventStore(run_a / "events.sqlite")
    store_b = EventStore(run_b / "events.sqlite")
    try:
        assert _canonical_stream(store_a.read_all()) == _canonical_stream(store_b.read_all())
    finally:
        store_a.close()
        store_b.close()

    for run in (run_a, run_b):
        assert not (run / "archive").exists()
        assert not (run / "snapshots").exists()
        assert not (run / "retention_journal.json").exists()


# ---------------------------------------------------------------- 2. 归档重建
def test_snapshot_and_archive_rebuild_match_full_replay(tmp_path):
    out = tmp_path / "run"
    summary = run_headless(
        ROOT, RunConfig(days=4, seed=42, npc_limit=0, out_root=tmp_path, run_id="run")
    )
    run = out
    expected_state = summary["hashes"]["state_final"]
    locations, content_hash = _content_locations()

    store = EventStore(run / "events.sqlite")
    try:
        before = list(store.read_all())
        before_bytes = _canonical_stream(before)
        state0, ticks0 = replay_into(
            store, locations, run_dir=run, seed=42, content_hash=content_hash
        )
        assert state0.hash() == expected_state

        result = _maintain(
            store,
            run,
            retention_days=1,
            locations=locations,
            content_hash=content_hash,
            verify=True,
        )
        assert result["ok"] is True and result["verified"] is True
        target = int(result["target_seq"])
        assert target > 0
        assert store.min_seq() == target + 1
        assert store.max_seq() == len(before)

        # 2.1 snapshot + 尾段
        state1, ticks1 = replay_into(
            store, locations, run_dir=run, seed=42, content_hash=content_hash
        )
        assert state1.hash() == expected_state
        assert ticks1 == ticks0

        # 2.2 归档段 + 尾段（完全不走 pickle）
        state2, ticks2, source = _rebuild_from_archive_only(
            store, run, locations, seed=42, content_hash=content_hash
        )
        assert source == "archive"
        assert state2.hash() == expected_state
        assert ticks2 == ticks0

        # 2.3 归档 + 尾段的事件序列与全量逐条等价
        manifest = load_manifest(run)
        assert verify_archive(run, manifest) == []
        archived = list(iter_archive(run, manifest))
        tail = list(store.iter_range(target + 1, store.max_seq() + 1))
        assert _canonical_stream(archived + tail) == before_bytes

        # 2.4 快照本身可校验
        snapshot = load_snapshot(run, content_hash=content_hash, seed=42)
        assert snapshot is not None and int(snapshot["last_seq"]) == target

        # 2.5 快照损坏时自动退回归档重建
        pointer = json.loads((run / "snapshots" / "snapshot.json").read_text("utf-8"))
        (run / "snapshots" / pointer["file"]).write_bytes(b"corrupted")
        state3, ticks3 = replay_into(
            store, locations, run_dir=run, seed=42, content_hash=content_hash
        )
        assert state3.hash() == expected_state
        assert ticks3 == ticks0
    finally:
        store.close()


def test_seq_stays_monotonic_after_prune(tmp_path):
    run = tmp_path / "run"
    store = EventStore(run / "events.sqlite")
    try:
        _fill_store(store, 500)
        result = _maintain(store, run, retention_days=1, segment_events=100)
        target = int(result["target_seq"])
        assert target > 0
        store.append(_make_event(501, 500))
        assert store.count() == 501
        assert store.min_seq() == target + 1
    finally:
        store.close()


# ---------------------------------------------------------------- 3. 中断恢复
def test_interrupted_maintenance_recovers_at_every_phase(tmp_path):
    for phase in ("archive", "snapshot", "prune"):
        run = tmp_path / phase
        store = EventStore(run / "events.sqlite")
        try:
            _fill_store(store, 600)
            kwargs = dict(retention_days=1, segment_events=200)
            before_min = store.min_seq()

            # dry-run 永远不写盘、不删除
            dry = _maintain(store, run, dry_run=True, **kwargs)
            assert dry["noop"] == "dry_run"
            assert store.min_seq() == before_min
            assert not (run / "retention_journal.json").exists()
            assert not (run / "archive").exists()

            try:
                _maintain(store, run, fail_after=phase, **kwargs)
            except MaintenanceError as exc:
                assert "injected_failure" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("failure was not injected")

            journal = load_journal(run)
            assert journal is not None and journal["phase"] != "done"
            if phase in ("archive", "snapshot"):
                assert store.min_seq() == before_min, "中断前不许删除事件"
            if phase == "prune":
                assert store.min_seq() > before_min, "prune 阶段已删除，恢复应补完"

            recovered = _maintain(store, run, verify=True, **kwargs)
            assert recovered["verified"] is True
            assert load_journal(run) is None
            assert verify_archive(run, load_manifest(run)) == []
            assert store.min_seq() == int(recovered["target_seq"]) + 1
        finally:
            store.close()


# ---------------------------------------------------------------- 4. 裁剪边界
def test_prune_boundary_is_full_day_prefix(tmp_path):
    run = tmp_path / "run"
    store = EventStore(run / "events.sqlite")
    try:
        # 第 0..4 日，每天 144 条，最后一日只写 100 条（未结束）。
        ticks = []
        for day in range(5):
            count = TICKS_PER_DAY if day < 4 else 100
            ticks.extend(range(day * TICKS_PER_DAY, day * TICKS_PER_DAY + count))
        for seq, tick in enumerate(ticks, start=1):
            store.append(_make_event(seq, tick))

        result = _maintain(store, run, retention_days=1, segment_events=500)
        target = int(result["target_seq"])
        last_tick = ticks[-1]
        current_day = last_tick // TICKS_PER_DAY
        assert int(result["cutoff_tick"]) == current_day * TICKS_PER_DAY
        # 保留窗口 = 当前日；被裁前缀的最后一条必须是前一日最后一 tick。
        assert target == 4 * TICKS_PER_DAY
        assert store.min_seq() == target + 1
        assert store.count() == len(ticks)
    finally:
        store.close()


def test_cutoff_and_noop_safety(tmp_path):
    assert cutoff_tick_for(9 * TICKS_PER_DAY + 10, 1) == 9 * TICKS_PER_DAY
    assert cutoff_tick_for(9 * TICKS_PER_DAY + 10, 14) == 0
    assert cutoff_tick_for(3 * TICKS_PER_DAY + 5, 0) == 3 * TICKS_PER_DAY

    run = tmp_path / "run"
    store = EventStore(run / "events.sqlite")
    try:
        # 全部事件都在第 0 日：cutoff=0，没有可裁前缀。
        _fill_store(store, 50)
        assert prefix_watermark(store, 0) == 0
        result = _maintain(store, run, retention_days=14)
        assert result["noop"] == "nothing_to_prune"
        assert store.min_seq() == 1
        assert not (run / "archive").exists()
    finally:
        store.close()


def test_stale_tick_events_do_not_create_seq_gap(tmp_path):
    """旧 tick 的晚到拒绝事件留在尾段，不参与前缀裁剪，也不产生 seq 洞。"""
    run = tmp_path / "run"
    store = EventStore(run / "events.sqlite")
    try:
        _fill_store(store, 300)
        # 在第 1 日末尾插入一个旧 tick 的拒绝事件，seq 更大但 tick 更小。
        store.append(
            Event(
                seq=301, tick=5, day=0, intent_id="stale:1", actor="player:p1",
                verb="look", args={}, effects=[], outcome="reject", reason="stale_tick",
                source="player",
            )
        )
        result = _maintain(store, run, retention_days=1, segment_events=100)
        target = int(result["target_seq"])
        assert store.min_seq() == target + 1
        # 被裁前缀必须连续覆盖 1..target；尾段首条允许是旧 tick 的拒绝事件。
        seqs = [e.seq for e in store.iter_range(1, store.max_seq() + 1)]
        assert seqs == list(range(store.min_seq(), store.max_seq() + 1))
    finally:
        store.close()


# ---------------------------------------------------------------- 5. 本地轮转
def _run_with_archives(tmp_path: Path) -> tuple[EventStore, Path, int]:
    """5 个游戏日、按日分段归档并裁剪；返回 (store, run_dir, target_seq)。"""
    run = tmp_path / "run"
    store = EventStore(run / "events.sqlite")
    for day in range(5):
        for t in range(TICKS_PER_DAY):
            seq = day * TICKS_PER_DAY + t + 1
            store.append(_make_event(seq, day * TICKS_PER_DAY + t))
    first = _maintain(store, run, retention_days=1, segment_events=TICKS_PER_DAY)
    assert first["ok"] is True
    assert len(load_manifest(run)["segments"]) == 4
    return store, run, int(first["target_seq"])


def test_rotation_evicts_oldest_segments_and_keeps_rebuild(tmp_path):
    store, run, _target = _run_with_archives(tmp_path)
    try:
        expected_state, expected_ticks = replay_into(store, {})
        manifest = load_manifest(run)
        total = archive_total_bytes(run, manifest)
        oldest = manifest["segments"][0]

        # dry-run 展示计划但不删文件。
        dry = _maintain(
            store, run, dry_run=True, retention_days=1,
            archive_max_bytes=total - 1, archive_min_window_days=0,
        )
        assert dry["evicted_segments"] == [oldest["file"]]
        assert dry["rotation_dry_run"] is True
        assert int(dry["evicted_bytes"]) > 0
        assert (run / "archive" / oldest["file"]).exists()

        result = _maintain(
            store, run, retention_days=1,
            archive_max_bytes=total - 1, archive_min_window_days=0,
        )
        assert result["noop"] == "already_pruned"
        assert result["evicted_segments"] == [oldest["file"]]
        assert not (run / "archive" / oldest["file"]).exists()
        assert int(result["archive_bytes_after_rotation"]) == total - int(oldest["bytes"])
        assert result["archive_over_cap"] is False

        after = load_manifest(run)
        assert int(after["min_available_seq"]) == int(oldest["end_seq"]) + 1
        assert verify_archive(run, after) == []

        # 淘汰后仍有快照：重建结果与全量回放一致。
        state, ticks = replay_into(
            store, {}, run_dir=run, seed=42, content_hash="test-content-hash"
        )
        assert state.hash() == expected_state.hash()
        assert ticks == expected_ticks

        # 删除快照后，剩余归档不足以重建全史，必须明确报错而不是半重建。
        snap_dir = run / "snapshots"
        for path in snap_dir.glob("*"):
            path.unlink()
        snap_dir.rmdir()
        with pytest.raises(MaintenanceError) as ei:
            state_at_seq(store, run, store.max_seq(), {})
        assert "archive_prefix_evicted" in str(ei.value)
        with pytest.raises(RuntimeError) as ei2:
            replay_into(store, {}, run_dir=run, seed=42, content_hash="test-content-hash")
        assert "event_rebuild_failed" in str(ei2.value)
    finally:
        store.close()


def test_rotation_respects_min_window(tmp_path):
    store, run, _target = _run_with_archives(tmp_path)
    try:
        result = _maintain(
            store, run, retention_days=1,
            archive_max_bytes=1, archive_min_window_days=2,
        )
        manifest = load_manifest(run)
        # 最大 day_end=3，窗口 2 天 -> 只允许淘汰 day 0/1 两段。
        assert len(result["evicted_segments"]) == 2
        assert result["archive_over_cap"] is True
        assert result["rotation_still_over_cap_reason"] == "min_window"
        assert int(manifest["min_available_seq"]) == 2 * TICKS_PER_DAY + 1
        assert verify_archive(run, manifest) == []
        for row in manifest["segments"]:
            assert (run / "archive" / str(row["file"])).exists()
    finally:
        store.close()


def test_plan_rotation_requires_covering_snapshot():
    manifest = {
        "last_pruned_seq": 300,
        "segments": [
            {"file": "a", "start_seq": 1, "end_seq": 100, "day_end": 0, "bytes": 10},
            {"file": "b", "start_seq": 101, "end_seq": 200, "day_end": 1, "bytes": 10},
            {"file": "c", "start_seq": 201, "end_seq": 300, "day_end": 2, "bytes": 10},
        ],
    }

    # 没有有效快照：一段都不能删。
    plan = plan_archive_rotation(
        manifest, None, archive_max_bytes=20, min_window_days=0, total_bytes=30
    )
    assert plan["blocked_reason"] == "no_valid_snapshot"
    assert plan["evicted_files"] == []

    # 快照落后于已裁剪水位：整段历史都不可淘汰，整体拒绝。
    plan = plan_archive_rotation(
        manifest, {"last_seq": 250}, archive_max_bytes=1, min_window_days=0, total_bytes=30
    )
    assert plan["blocked_reason"] == "snapshot_behind_pruned"
    assert plan["evicted_files"] == []

    # 快照覆盖全部已裁剪事件：只淘汰最老的一段即可回到上限内。
    plan = plan_archive_rotation(
        manifest, {"last_seq": 300}, archive_max_bytes=20, min_window_days=0, total_bytes=30
    )
    assert plan["evicted_files"] == ["a"]
    assert plan["blocked_reason"] == ""
    assert plan["archive_bytes_after"] == 20
    assert plan["over_cap_after"] is False


def test_rotation_refuses_with_pending_journal_and_cleans_orphans(tmp_path):
    store, run, _target = _run_with_archives(tmp_path)
    try:
        snapshot = load_snapshot(run, content_hash="test-content-hash", seed=42)
        assert snapshot is not None
        write_journal(run, {"version": 1, "phase": "archive", "target_seq": 1})

        plan = rotate_archives(
            run, archive_max_bytes=1, min_window_days=0, snapshot=snapshot
        )
        assert plan["blocked_reason"] == "journal_pending"
        assert plan["evicted_files"] == []
        assert len(load_manifest(run)["segments"]) == 4

        clear_journal(run)
        orphan = run / "archive" / "events-s000000000999-s000000001000.jsonl.gz"
        orphan.write_bytes(b"orphan")
        plan = rotate_archives(
            run, archive_max_bytes=1, min_window_days=0, snapshot=snapshot
        )
        # 窗口 0 天、上限 1 字节：全部段都被快照覆盖，允许删空，重建只靠快照 + 尾段。
        assert len(plan["evicted_files"]) == 4
        assert plan["removed_orphans"] == 1
        assert not orphan.exists()
        assert load_manifest(run)["segments"] == []
        assert int(load_manifest(run)["min_available_seq"]) == _target + 1
    finally:
        store.close()


def test_disk_headroom_refuses_archiving(tmp_path):
    run = tmp_path / "run"
    store = EventStore(run / "events.sqlite")
    try:
        _fill_store(store, 600)

        dry = _maintain(store, run, dry_run=True, retention_days=1, min_free_bytes=1 << 50)
        assert dry["disk_ok"] is False
        assert int(dry["disk_free_bytes"]) > 0

        with pytest.raises(MaintenanceError) as ei:
            _maintain(store, run, retention_days=1, min_free_bytes=1 << 50)
        assert "disk_low_free" in str(ei.value)
        assert store.min_seq() == 1
        assert not (run / "archive").exists()
        # journal 保留在 archive 阶段，磁盘恢复后可继续，不会丢事件。
        journal = load_journal(run)
        assert journal is not None and journal["phase"] == "archive"
    finally:
        store.close()
