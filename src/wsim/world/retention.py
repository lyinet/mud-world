"""事件表保留期维护：归档 -> 快照 -> 裁剪（默认关闭）。

设计要点（详见 `specs/xkx-parity/event-retention.spec.md`）：

- 归档段是长期事实源：gzip JSONL，按 seq 连续前缀分段，可独立回放；
- 快照只是重启加速缓存：落盘后必须复核 `state_hash`，失败则退回归档重建；
- 裁剪只删 `seq <= S` 的连续前缀，绝不产生 seq 洞；只裁完整游戏日；
- 所有落盘走“临时文件 + fsync + os.replace”，阶段机 journal 保证中断可恢复；
- 不设置开关 / 不执行 CLI 时不产生任何新文件，行为与今天一致。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pickle
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ..core.canonical import dumps
from ..core.clock import TICKS_PER_DAY
from ..core.content import load_content
from ..core.models import Effect, Event
from .effects import apply_effects
from .state import WorldState
from .store import EventStore

ARCHIVE_FORMAT = "xkx-event-archive"
ARCHIVE_VERSION = 1
SNAPSHOT_FORMAT = "xkx-event-snapshot"
SNAPSHOT_VERSION = 1

ARCHIVE_DIR = "archive"
SNAPSHOT_DIR = "snapshots"
MANIFEST_FILE = "manifest.json"
SNAPSHOT_POINTER = "snapshot.json"
JOURNAL_FILE = "retention_journal.json"

DEFAULT_RETENTION_DAYS = 14
DEFAULT_SEGMENT_EVENTS = 20_000
GZIP_LEVEL = 6


# ---------------------------------------------------------------- 基础 IO
def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _atomic_write_json(path: Path, obj: Any) -> None:
    _atomic_write_bytes(path, (dumps(obj) + "\n").encode("utf-8"))


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


# ---------------------------------------------------------------- 策略计算
def cutoff_tick_for(last_tick: int, retention_days: int) -> int:
    """保留最近 N 个游戏日（含当前未结束日）后允许裁剪的 tick 下界。

    `retention_days=14`、当前在第 20 日：cutoff=(20-14+1)*144=1008，
    即裁掉第 0..6 日，保留第 7..20 日。
    """
    days = max(1, int(retention_days))
    current_day = max(0, int(last_tick) // TICKS_PER_DAY)
    cutoff_day = max(0, current_day - days + 1)
    return cutoff_day * TICKS_PER_DAY


def prefix_watermark(store: EventStore, cutoff_tick: int) -> int:
    """返回可安全删除的最大 seq 前缀水位 S。

    只裁“所有 tick >= cutoff 的事件之前”的连续前缀：找最小 retained seq，
    S = 它 - 1。这样 stale_tick 等旧 tick 的晚到事件仍留在尾段，下一轮再处理，
    不会把保留窗口内的事件误删，也不会产生 seq 洞。
    """
    row = store.conn.execute(
        "SELECT MIN(seq) FROM events WHERE tick>=?", (int(cutoff_tick),)
    ).fetchone()
    first_retained = int(row[0]) if row and row[0] is not None else 0
    if first_retained <= 0:
        return 0
    return first_retained - 1


# ---------------------------------------------------------------- 归档
def default_manifest() -> dict[str, Any]:
    return {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "last_pruned_seq": 0,
        "last_day": -1,
        "segments": [],
    }


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = Path(run_dir) / ARCHIVE_DIR / MANIFEST_FILE
    if not path.exists():
        return default_manifest()
    data = load_json(path)
    if not isinstance(data, dict) or data.get("format") != ARCHIVE_FORMAT:
        raise ValueError(f"archive_manifest_invalid: {path}")
    data.setdefault("segments", [])
    data.setdefault("last_pruned_seq", 0)
    data.setdefault("last_day", -1)
    return data


def _segment_name(start_seq: int, end_seq: int) -> str:
    return f"events-s{int(start_seq):012d}-s{int(end_seq):012d}.jsonl.gz"


def _write_segment(path: Path, events: list[Event]) -> tuple[str, int]:
    """把一段事件写成确定性 gzip JSONL；返回 (sha256, bytes)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, compresslevel=GZIP_LEVEL, mtime=0
        ) as gz:
            for event in events:
                gz.write((dumps(event.to_dict()) + "\n").encode("utf-8"))
        raw.flush()
        os.fsync(raw.fileno())
    digest = sha256_file(tmp)
    size = file_size(tmp)
    os.replace(tmp, path)
    _fsync_dir(path.parent)
    return digest, size


def archive_events(
    store: EventStore,
    run_dir: Path,
    start_seq: int,
    end_seq: int,
    *,
    segment_events: int = DEFAULT_SEGMENT_EVENTS,
    manifest: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], int]:
    """归档 `(start_seq, end_seq]`，幂等；返回 (manifest, 新写段文件名, 新写字节数)。

    分段规则：seq 连续前缀；尽量在游戏日界切段；单段不超过 `segment_events` 条。
    已存在且 sha256 与 manifest 一致的段直接复用。
    """
    run_dir = Path(run_dir)
    manifest = manifest if manifest is not None else load_manifest(run_dir)
    seg_dir = run_dir / ARCHIVE_DIR
    seg_dir.mkdir(parents=True, exist_ok=True)
    limit = max(1, int(segment_events))

    existing = {
        str(row.get("file")): row for row in manifest.get("segments", []) if row.get("file")
    }
    written: list[str] = []
    written_bytes = 0
    buffer: list[Event] = []
    buffer_day: int | None = None
    first_seq = 0

    def flush() -> None:
        nonlocal buffer, buffer_day, first_seq, written_bytes
        if not buffer:
            return
        last_seq = int(buffer[-1].seq)
        name = _segment_name(first_seq, last_seq)
        path = seg_dir / name
        expected = existing.get(name)
        if path.exists() and expected is not None:
            digest = sha256_file(path)
            if str(expected.get("sha256") or "") == digest:
                buffer = []
                buffer_day = None
                first_seq = 0
                return
        digest, size = _write_segment(path, buffer)
        row = {
            "file": name,
            "start_seq": int(first_seq),
            "end_seq": last_seq,
            "events": len(buffer),
            "day_start": int(buffer[0].day),
            "day_end": int(buffer[-1].day),
            "sha256": digest,
            "bytes": size,
        }
        existing[name] = row
        written.append(name)
        written_bytes += size
        buffer = []
        buffer_day = None
        first_seq = 0

    for event in store.iter_range(int(start_seq) + 1, int(end_seq) + 1):
        if not buffer:
            first_seq = int(event.seq)
            buffer_day = int(event.day)
        elif int(event.day) != buffer_day or len(buffer) >= limit:
            flush()
            first_seq = int(event.seq)
            buffer_day = int(event.day)
        buffer.append(event)
    flush()

    # manifest 按 (start_seq, end_seq) 排序，保证多次增量归档后顺序正确。
    manifest["segments"] = sorted(
        existing.values(), key=lambda r: (int(r.get("start_seq") or 0), int(r.get("end_seq") or 0))
    )
    covered = sum(int(r.get("events") or 0) for r in manifest["segments"])
    if manifest["segments"]:
        manifest["last_day"] = int(manifest["segments"][-1].get("day_end", -1))
    manifest["covered_events"] = covered
    _atomic_write_json(seg_dir / MANIFEST_FILE, manifest)
    return manifest, written, written_bytes


def verify_archive(run_dir: Path, manifest: dict[str, Any] | None = None) -> list[str]:
    """逐段校验存在性与 sha256；返回问题列表（空表示全部通过）。"""
    run_dir = Path(run_dir)
    manifest = manifest if manifest is not None else load_manifest(run_dir)
    problems: list[str] = []
    expected_start = 1
    for row in manifest.get("segments", []):
        name = str(row.get("file") or "")
        if not name:
            problems.append("segment_missing_file")
            continue
        path = run_dir / ARCHIVE_DIR / name
        if not path.exists():
            problems.append(f"segment_missing:{name}")
            continue
        digest = sha256_file(path)
        if digest != str(row.get("sha256") or ""):
            problems.append(f"segment_sha256:{name}")
        start = int(row.get("start_seq") or 0)
        end = int(row.get("end_seq") or 0)
        if start != expected_start:
            problems.append(f"segment_gap:{name}:expected_start={expected_start}")
        expected_start = end + 1
    return problems


def iter_archive(run_dir: Path, manifest: dict[str, Any] | None = None) -> Iterator[Event]:
    """按 manifest 顺序流式读取归档段；调用方应先 `verify_archive`。"""
    run_dir = Path(run_dir)
    manifest = manifest if manifest is not None else load_manifest(run_dir)
    for row in manifest.get("segments", []):
        path = run_dir / ARCHIVE_DIR / str(row["file"])
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield Event(**json.loads(line))


# ---------------------------------------------------------------- 快照
class MaintenanceError(RuntimeError):
    """维护中止（归档不完整、快照校验失败、水位不一致等）；不会继续删事件。"""


def _snapshot_filename(day: int, last_seq: int) -> str:
    return f"state-d{int(day):06d}-s{int(last_seq):012d}.pkl.gz"


def snapshot_pointer_path(run_dir: Path) -> Path:
    return Path(run_dir) / SNAPSHOT_DIR / SNAPSHOT_POINTER


def write_snapshot(
    run_dir: Path,
    state: WorldState,
    player_ticks: dict[str, int],
    last_seq: int,
    *,
    content_hash: str | None,
    seed: int,
) -> dict[str, Any]:
    """写不可变快照 + 指针，并从磁盘读回复核 `state_hash`。"""
    run_dir = Path(run_dir)
    snap_dir = run_dir / SNAPSHOT_DIR
    snap_dir.mkdir(parents=True, exist_ok=True)
    day = int(state.tick) // TICKS_PER_DAY
    name = _snapshot_filename(day, last_seq)
    path = snap_dir / name
    payload = {
        "format": SNAPSHOT_FORMAT,
        "version": SNAPSHOT_VERSION,
        "tick": int(state.tick),
        "day": day,
        "last_seq": int(last_seq),
        "state_hash": state.hash(),
        "content_hash": content_hash,
        "seed": int(seed),
        "player_ticks": {str(k): int(v) for k, v in sorted(player_ticks.items())},
        "state": state,
    }
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, compresslevel=GZIP_LEVEL, mtime=0
        ) as gz:
            pickle.dump(payload, gz, protocol=pickle.HIGHEST_PROTOCOL)
        raw.flush()
        os.fsync(raw.fileno())
    digest = sha256_file(tmp)
    size = file_size(tmp)
    os.replace(tmp, path)
    _fsync_dir(snap_dir)

    # 读回复核，避免压缩/截断损坏的快照被指针引用。
    reloaded = _read_snapshot_file(path)
    if (
        int(reloaded.get("last_seq", -1)) != int(last_seq)
        or str(reloaded.get("state_hash")) != str(payload["state_hash"])
        or int(reloaded["state"].tick) != int(state.tick)
    ):
        raise MaintenanceError("snapshot_readback_mismatch")

    pointer = {
        "format": "xkx-event-snapshot-pointer",
        "version": SNAPSHOT_VERSION,
        "file": name,
        "bytes": size,
        "sha256": digest,
        "tick": payload["tick"],
        "day": day,
        "last_seq": int(last_seq),
        "state_hash": payload["state_hash"],
        "content_hash": content_hash,
        "seed": int(seed),
    }
    _atomic_write_json(snap_dir / SNAPSHOT_POINTER, pointer)
    return pointer


def _read_snapshot_file(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rb") as fh:
        payload = pickle.load(fh)
    if not isinstance(payload, dict) or payload.get("format") != SNAPSHOT_FORMAT:
        raise ValueError("snapshot_format_invalid")
    if int(payload.get("version", 0)) != SNAPSHOT_VERSION:
        raise ValueError("snapshot_version_invalid")
    return payload


def load_snapshot(
    run_dir: Path | None,
    *,
    content_hash: str | None = None,
    seed: int | None = None,
) -> dict[str, Any] | None:
    """读取并校验最新快照；任何不合法都返回 None（调用方退回归档重建）。"""
    if run_dir is None:
        return None
    pointer_path = snapshot_pointer_path(run_dir)
    if not pointer_path.exists():
        return None
    try:
        pointer = load_json(pointer_path)
        if pointer.get("format") != "xkx-event-snapshot-pointer":
            return None
        if int(pointer.get("version", 0)) != SNAPSHOT_VERSION:
            return None
        path = Path(run_dir) / SNAPSHOT_DIR / str(pointer.get("file") or "")
        if not path.exists() or sha256_file(path) != str(pointer.get("sha256") or ""):
            return None
        payload = _read_snapshot_file(path)
    except Exception:
        return None

    if int(payload.get("last_seq", -1)) != int(pointer.get("last_seq", -2)):
        return None
    if str(payload.get("state_hash")) != str(pointer.get("state_hash")):
        return None
    if content_hash is not None and payload.get("content_hash") != content_hash:
        return None
    if seed is not None and int(payload.get("seed", -1)) != int(seed):
        return None
    try:
        actual = payload["state"].hash()
    except Exception:
        return None
    if str(actual) != str(payload.get("state_hash")):
        return None
    return payload


# ---------------------------------------------------------------- 重建
_SYSTEM_SOURCES = frozenset({"npc", "planner", "system"})


def _apply_event(state: WorldState, event: Event, player_ticks: dict[str, int]) -> None:
    state.tick = int(event.tick)
    if event.outcome != "ok":
        return
    effects = [Effect.from_dict(d) for d in event.effects]
    apply_effects(state, effects)
    if event.actor.startswith("player:") and event.source not in _SYSTEM_SOURCES:
        prev = player_ticks.get(event.actor)
        tick = int(event.tick)
        player_ticks[event.actor] = tick if prev is None else max(prev, tick)


def _load_content_locations(root: Path | None) -> tuple[dict[str, dict[str, Any]], str]:
    content = load_content(root, min_npcs=0, allow_fixture=False, use_content=True)
    locations = dict(content.locations)
    # 与 WorldSession 一致：xkx 内容包存在 /d/ 地点时补死亡门，保证回放不误判。
    if any(str(lid).startswith("/d/") for lid in locations) and "/d/death/gate" not in locations:
        locations["/d/death/gate"] = {
            "id": "/d/death/gate",
            "name": "鬼门关",
            "exits": ["/d/crossroads"],
            "services": [],
            "tags": ["death"],
        }
    return locations, content.content_hash


def state_at_seq(
    store: EventStore,
    run_dir: Path | None,
    target_seq: int,
    locations: dict[str, dict[str, Any]],
    *,
    seed: int = 42,
    content_hash: str | None = None,
) -> tuple[WorldState, dict[str, int], str]:
    """把状态重建到 `target_seq`；返回 (state, player_ticks, 使用来源)。

    来源优先级：有效快照 -> 归档前缀 -> 全量在线事件。只应用 `outcome=ok` 的 effects。
    """
    target = int(target_seq)
    snapshot = load_snapshot(run_dir, content_hash=content_hash, seed=seed)
    state: WorldState | None = None
    player_ticks: dict[str, int] = {}
    base_seq = 0
    source = "full"
    if snapshot is not None and int(snapshot["last_seq"]) <= target:
        if store.min_seq() > 1 and int(snapshot["last_seq"]) < store.min_seq() - 1:
            raise MaintenanceError(
                f"snapshot_behind_pruned:snapshot={snapshot['last_seq']},min_seq={store.min_seq()}"
            )
        state = snapshot["state"]
        player_ticks = dict(snapshot.get("player_ticks") or {})
        base_seq = int(snapshot["last_seq"])
        source = "snapshot"
    elif run_dir is not None and store.min_seq() > 1:
        manifest = load_manifest(run_dir)
        problems = verify_archive(run_dir, manifest)
        if problems:
            raise MaintenanceError("archive_unusable:" + ",".join(problems[:3]))
        covered = int(manifest["segments"][-1]["end_seq"]) if manifest["segments"] else 0
        if covered < store.min_seq() - 1:
            raise MaintenanceError(
                f"archive_gap:covered={covered},min_seq={store.min_seq()}"
            )
        state = WorldState(locations=locations)
        for event in iter_archive(run_dir, manifest):
            if int(event.seq) > target:
                break
            _apply_event(state, event, player_ticks)
            base_seq = max(base_seq, int(event.seq))
        source = "archive"
    elif store.min_seq() > 1:
        raise MaintenanceError(
            f"archive_missing_for_pruned:min_seq={store.min_seq()},run_dir={run_dir}"
        )

    if state is None:
        state = WorldState(locations=locations)
    if base_seq > target:
        raise MaintenanceError(f"snapshot_ahead:base={base_seq},target={target}")
    for event in store.iter_range(base_seq + 1, target + 1):
        _apply_event(state, event, player_ticks)
    return state, player_ticks, source


def rebuild_from_artifacts(
    store: EventStore,
    run_dir: Path | None,
    locations: dict[str, dict[str, Any]],
    *,
    seed: int = 42,
    content_hash: str | None = None,
) -> tuple[WorldState, dict[str, int], str]:
    """重建到在线表最后 seq（重启路径用）。"""
    return state_at_seq(
        store, run_dir, store.max_seq(), locations, seed=seed, content_hash=content_hash
    )


# ---------------------------------------------------------------- journal
def journal_path(run_dir: Path) -> Path:
    return Path(run_dir) / JOURNAL_FILE


def load_journal(run_dir: Path) -> dict[str, Any] | None:
    path = journal_path(run_dir)
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        raise MaintenanceError(f"journal_unreadable:{path}")


def write_journal(run_dir: Path, journal: dict[str, Any]) -> None:
    _atomic_write_json(journal_path(run_dir), journal)


def clear_journal(run_dir: Path) -> None:
    path = journal_path(run_dir)
    if path.exists():
        path.unlink()
        _fsync_dir(path.parent)


# ---------------------------------------------------------------- 维护主流程
@dataclass
class _Options:
    run_dir: Path
    store: EventStore
    locations: dict[str, dict[str, Any]]
    seed: int
    content_hash: str | None
    segment_events: int
    vacuum: bool


def _complete_journal(
    journal: dict[str, Any], opts: _Options, fail_after: str | None
) -> None:
    """按 journal 阶段推进；任一步失败抛错并保留 journal，不越过安全边界。"""
    while True:
        phase = str(journal.get("phase") or "")
        target = int(journal.get("target_seq") or 0)
        if phase == "done" or phase == "":
            clear_journal(opts.run_dir)
            return
        if phase == "archive":
            manifest = load_manifest(opts.run_dir)
            start = int(manifest.get("last_pruned_seq") or 0)
            if start < target:
                manifest, _written, _bytes = archive_events(
                    opts.store,
                    opts.run_dir,
                    start,
                    target,
                    segment_events=opts.segment_events,
                    manifest=manifest,
                )
            problems = verify_archive(opts.run_dir, manifest)
            if problems:
                raise MaintenanceError("archive_verify_failed:" + ",".join(problems[:3]))
            covered = int(manifest["segments"][-1]["end_seq"]) if manifest["segments"] else 0
            if covered != target:
                raise MaintenanceError(
                    f"archive_coverage_mismatch:covered={covered},target={target}"
                )
            if fail_after == "archive":
                raise MaintenanceError("injected_failure:archive")
            journal["phase"] = "snapshot"
            write_journal(opts.run_dir, journal)
            continue
        if phase == "snapshot":
            state, player_ticks, _source = state_at_seq(
                opts.store,
                opts.run_dir,
                target,
                opts.locations,
                seed=opts.seed,
                content_hash=opts.content_hash,
            )
            pointer = write_snapshot(
                opts.run_dir,
                state,
                player_ticks,
                target,
                content_hash=opts.content_hash,
                seed=opts.seed,
            )
            if fail_after == "snapshot":
                raise MaintenanceError("injected_failure:snapshot")
            journal["phase"] = "prune"
            journal["snapshot"] = pointer["file"]
            write_journal(opts.run_dir, journal)
            continue
        if phase == "prune":
            deleted = opts.store.delete_through(target)
            opts.store.checkpoint_truncate()
            manifest = load_manifest(opts.run_dir)
            manifest["last_pruned_seq"] = target
            _atomic_write_json(opts.run_dir / ARCHIVE_DIR / MANIFEST_FILE, manifest)
            opts.store.set_meta("event_pruned_through_seq", str(target))
            opts.store.set_meta("event_retention_version", "1")
            if manifest.get("last_day", -1) >= 0:
                opts.store.set_meta("event_archive_last_day", str(manifest["last_day"]))
            if opts.vacuum:
                opts.store.vacuum_offline()
            journal["phase"] = "done"
            journal["deleted"] = int(deleted)
            write_journal(opts.run_dir, journal)
            if fail_after == "prune":
                raise MaintenanceError("injected_failure:prune")
            continue
        raise MaintenanceError(f"journal_phase_unknown:{phase}")


def _check_consistency(opts: _Options) -> None:
    manifest = load_manifest(opts.run_dir)
    meta_pruned = int(opts.store.get_meta("event_pruned_through_seq") or 0)
    covered = int(manifest["segments"][-1]["end_seq"]) if manifest["segments"] else 0
    if meta_pruned > covered:
        raise MaintenanceError(
            f"archive_incomplete_for_pruned:meta={meta_pruned},covered={covered}"
        )
    min_seq = opts.store.min_seq()
    if min_seq > 0 and min_seq - 1 != max(meta_pruned, int(manifest.get("last_pruned_seq") or 0)):
        raise MaintenanceError(
            f"prune_watermark_mismatch:min_seq={min_seq},meta={meta_pruned},"
            f"manifest={manifest.get('last_pruned_seq')}"
        )


def _planned_segments(
    store: EventStore, start_seq: int, end_seq: int, segment_events: int
) -> int:
    if end_seq <= start_seq:
        return 0
    rows = store.conn.execute(
        "SELECT day, COUNT(*) FROM events WHERE seq>? AND seq<=? GROUP BY day ORDER BY day",
        (int(start_seq), int(end_seq)),
    ).fetchall()
    limit = max(1, int(segment_events))
    return sum(max(1, (int(row[1]) + limit - 1) // limit) for row in rows)


def run_maintenance(
    run_dir: Path,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    segment_events: int = DEFAULT_SEGMENT_EVENTS,
    vacuum: bool = False,
    dry_run: bool = False,
    verify: bool = False,
    fail_after: str | None = None,
    store: EventStore | None = None,
    locations: dict[str, dict[str, Any]] | None = None,
    seed: int = 42,
    content_hash: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """执行一次维护：归档 -> 快照 -> 裁剪（必要时先恢复中断的 journal）。

    `locations` 未提供时从 `root` 的正式内容包加载；CLI 路径必须提供 root。
    """
    run_dir = Path(run_dir).resolve()
    own_store = store is None
    if store is None:
        store = EventStore(run_dir / "events.sqlite")
    if locations is None:
        locations, loaded_hash = _load_content_locations(root)
        if content_hash is None:
            content_hash = loaded_hash
    opts = _Options(
        run_dir=run_dir,
        store=store,
        locations=locations,
        seed=int(seed),
        content_hash=content_hash,
        segment_events=max(1, int(segment_events)),
        vacuum=bool(vacuum),
    )
    try:
        db_path = run_dir / "events.sqlite"
        before_bytes = file_size(db_path) + file_size(db_path.with_name(db_path.name + "-wal"))
        events_before = store.max_seq() - store.min_seq() + 1 if store.min_seq() > 0 else 0

        # 1) 先恢复未完成的维护（不重算策略，按 journal 的 target 推进）。
        #    dry-run 只读，绝不替用户完成一次裁剪。
        journal = load_journal(run_dir)
        pending_journal = str(journal.get("phase") or "") if journal else ""
        segments_before = len(load_manifest(run_dir).get("segments", []))
        if not dry_run:
            if journal is not None and pending_journal != "done":
                _complete_journal(journal, opts, None)
            elif journal is not None:
                clear_journal(run_dir)

        _check_consistency(opts)

        # 2) 计算本轮边界。
        row = store.conn.execute("SELECT MAX(tick) FROM events").fetchone()
        last_tick = int(row[0]) if row and row[0] is not None else 0
        cutoff_tick = cutoff_tick_for(last_tick, retention_days)
        target = prefix_watermark(store, cutoff_tick)
        max_seq = store.max_seq()
        manifest = load_manifest(run_dir)
        start_seq = int(manifest.get("last_pruned_seq") or 0)
        noop_reason = ""
        if target <= 0:
            noop_reason = "nothing_to_prune"
        elif target >= max_seq:
            noop_reason = "would_empty_events"
        elif target <= start_seq:
            noop_reason = "already_pruned"

        planned = _planned_segments(store, start_seq, target, segment_events)
        if dry_run or noop_reason:
            segments_now = len(load_manifest(run_dir).get("segments", []))
            verified = False
            if verify and not dry_run:
                verified = _verify_rebuilds(
                    store, run_dir, locations, seed=seed, content_hash=content_hash
                )
            return {
                "ok": True,
                "dry_run": bool(dry_run),
                "noop": noop_reason or "dry_run",
                "pending_journal": pending_journal,
                "last_tick": last_tick,
                "cutoff_tick": cutoff_tick,
                "target_seq": target,
                "max_seq": max_seq,
                "events_before": events_before,
                "events_after": store.max_seq() - store.min_seq() + 1 if store.min_seq() > 0 else 0,
                "planned_segments": planned,
                "segments_written": 0 if dry_run else max(0, segments_now - segments_before),
                "db_bytes_before": before_bytes,
                "db_bytes_after": file_size(db_path)
                + file_size(db_path.with_name(db_path.name + "-wal")),
                "vacuumed": False,
                "verified": verified,
            }

        # 3) 正常一轮：journal -> archive -> snapshot -> prune。
        journal = {
            "version": 1,
            "phase": "archive",
            "cutoff_tick": cutoff_tick,
            "target_seq": target,
            "target_tick": last_tick,
            "started_from_seq": start_seq,
        }
        write_journal(run_dir, journal)
        _complete_journal(journal, opts, fail_after)

        # 4) 可选校验：快照+尾段 与 归档+尾段 必须得到同一状态。
        verified = False
        if verify:
            verified = _verify_rebuilds(
                store, run_dir, locations, seed=seed, content_hash=content_hash
            )

        after_bytes = file_size(db_path) + file_size(db_path.with_name(db_path.name + "-wal"))
        segments_after = len(load_manifest(run_dir).get("segments", []))
        return {
            "ok": True,
            "dry_run": False,
            "noop": "",
            "pending_journal": "",
            "last_tick": last_tick,
            "cutoff_tick": cutoff_tick,
            "target_seq": target,
            "max_seq": max_seq,
            "events_before": events_before,
            "events_after": store.max_seq() - store.min_seq() + 1 if store.min_seq() > 0 else 0,
            "planned_segments": planned,
            "segments_written": max(0, segments_after - segments_before),
            "db_bytes_before": before_bytes,
            "db_bytes_after": after_bytes,
            "vacuumed": bool(vacuum),
            "verified": verified,
        }
    finally:
        if own_store:
            store.close()


def _verify_rebuilds(
    store: EventStore,
    run_dir: Path,
    locations: dict[str, dict[str, Any]],
    *,
    seed: int,
    content_hash: str | None,
) -> bool:
    """校验两条重建路径结果一致（state hash + player_ticks）。"""
    tail_state, tail_ticks, tail_src = state_at_seq(
        store, run_dir, store.max_seq(), locations, seed=seed, content_hash=content_hash
    )
    archive_state, archive_ticks, archive_src = _rebuild_from_archive_only(
        store, run_dir, locations, seed=seed, content_hash=content_hash
    )
    if tail_state.hash() != archive_state.hash():
        raise MaintenanceError(
            "verify_mismatch:"
            f"{tail_src}={tail_state.hash()},"
            f"{archive_src}={archive_state.hash()}"
        )
    if tail_ticks != archive_ticks:
        raise MaintenanceError("verify_player_ticks_mismatch")
    return True


def _rebuild_from_archive_only(
    store: EventStore,
    run_dir: Path,
    locations: dict[str, dict[str, Any]],
    *,
    seed: int,
    content_hash: str | None,
) -> tuple[WorldState, dict[str, int], str]:
    """强制走“归档 + 尾段”，用于 verify 对照。"""
    manifest = load_manifest(run_dir)
    problems = verify_archive(run_dir, manifest)
    if problems:
        raise MaintenanceError("archive_unusable:" + ",".join(problems[:3]))
    state = WorldState(locations=locations)
    player_ticks: dict[str, int] = {}
    covered = 0
    for event in iter_archive(run_dir, manifest):
        _apply_event(state, event, player_ticks)
        covered = max(covered, int(event.seq))
    if covered != int(store.min_seq()) - 1:
        raise MaintenanceError(f"archive_tail_gap:covered={covered},min_seq={store.min_seq()}")
    for event in store.iter_range(covered + 1, store.max_seq() + 1):
        _apply_event(state, event, player_ticks)
    return state, player_ticks, "archive"


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m wsim.world.retention",
        description="events 保留期维护：归档 -> 快照 -> 裁剪（默认关闭）",
    )
    parser.add_argument("--run-dir", required=True, help="run 目录（含 events.sqlite）")
    parser.add_argument("--root", default=None, help="内容包根目录，默认等于 run-dir")
    parser.add_argument("--enable", action="store_true", help="显式开启（默认关闭）")
    parser.add_argument(
        "--retention-days", type=int, default=int(os.environ.get("XKX_EVENT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    )
    parser.add_argument(
        "--segment-events", type=int, default=int(os.environ.get("XKX_EVENT_SEGMENT_EVENTS", DEFAULT_SEGMENT_EVENTS))
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument("--fail-after", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not (args.enable or os.environ.get("XKX_EVENT_RETENTION") == "1"):
        print(dumps({"ok": False, "error": "retention_disabled", "hint": "--enable"}))
        return 2

    run_dir = Path(args.run_dir).expanduser().resolve()
    db = run_dir / "events.sqlite"
    if not db.exists():
        print(dumps({"ok": False, "error": "events_db_missing", "run_dir": str(run_dir)}))
        return 2

    store = EventStore(db)
    try:
        seed = args.seed
        if seed is None:
            seed = int(store.get_meta("seed") or 42)
        content_hash = store.get_meta("content_hash") or None
        result = run_maintenance(
            run_dir,
            retention_days=args.retention_days,
            segment_events=args.segment_events,
            vacuum=args.vacuum,
            dry_run=args.dry_run,
            verify=args.verify,
            fail_after=args.fail_after,
            store=store,
            seed=seed,
            content_hash=content_hash,
            root=Path(args.root).expanduser().resolve() if args.root else None,
        )
        print(dumps(result))
        return 0
    except MaintenanceError as exc:
        print(dumps({"ok": False, "error": str(exc), "run_dir": str(run_dir)}))
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI 必须给出可读的 JSON 错误
        print(dumps({"ok": False, "error": f"{type(exc).__name__}:{exc}", "run_dir": str(run_dir)}))
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())

