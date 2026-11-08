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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ..core.canonical import dumps
from ..core.clock import TICKS_PER_DAY
from ..core.models import Event
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
