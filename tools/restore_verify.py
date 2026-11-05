#!/usr/bin/env python3
"""P0 数据安全：从备份恢复并逐项校验。

默认恢复到系统临时目录（不改动运行中的 run 目录），校验全部通过后可选择
`--apply-dir runs/<RUN_ID>` 把备份文件装回目标目录（**必须先停服**）。

校验项（任一项失败即非零退出）：

1. 备份文件存在，且 sha256 / 大小与 manifest 一致；
2. `PRAGMA integrity_check = ok`；
3. events 的 seq 连续无洞，min/max/count 与 manifest 一致；
4. 事件重放得到的 `state_hash` 与 manifest 一致；
5. `content_hash`：备份库 meta 与 manifest 一致，且与当前 `content/` 一致
   （若内容包已升级，则备份无法直接启动，属预期拒绝）；
6. `event_hash` 与 manifest 一致。

退出码：0 全部通过；1 参数/IO 错误；2 校验失败（非零）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "manifest.json"
DATABASE_NAMES = ("events.sqlite", "service.sqlite")
EXIT_VERIFY_FAILED = 2


def _log(msg: str) -> None:
    print(f"[restore] {msg}", file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        return ";".join(str(r[0]) for r in rows) if rows else "empty"
    finally:
        conn.close()


def _events_summary(path: Path) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        count, seq_min, seq_max, distinct = conn.execute(
            "SELECT COUNT(*), MIN(seq), MAX(seq), COUNT(DISTINCT seq) FROM events"
        ).fetchone()
        seqs = [int(r[0]) for r in conn.execute("SELECT seq FROM events ORDER BY seq").fetchall()]
        holes: list[int] = []
        if seqs:
            expected = seqs[0]
            for seq in seqs:
                while expected < seq:
                    holes.append(expected)
                    expected += 1
                expected = seq + 1
        content_hash_row = conn.execute(
            "SELECT value FROM meta WHERE key='content_hash'"
        ).fetchone()
    finally:
        conn.close()
    seq_min_i = None if seq_min is None else int(seq_min)
    seq_max_i = None if seq_max is None else int(seq_max)
    contiguous = bool(
        seq_min_i is not None
        and int(count) == int(distinct)
        and int(count) == (int(seq_max_i) - int(seq_min_i) + 1)
        and not holes
    )
    return {
        "seq": {
            "min": seq_min_i,
            "max": seq_max_i,
            "count": int(count),
            "distinct": int(distinct),
            "contiguous": contiguous,
            "holes": holes[:20],
        },
        "content_hash": None if content_hash_row is None else str(content_hash_row[0]),
    }


def _replay_hashes(path: Path, root: Path) -> dict[str, str]:
    sys.path.insert(0, str(root / "src"))
    from wsim.core.content import load_content  # noqa: WPS433
    from wsim.core.models import Effect  # noqa: WPS433
    from wsim.world.effects import apply_effects  # noqa: WPS433
    from wsim.world.state import WorldState  # noqa: WPS433
    from wsim.world.store import EventStore  # noqa: WPS433

    content = load_content(root, allow_fixture=False, use_content=True)
    with tempfile.TemporaryDirectory(prefix="xkx-restore-replay-") as tmp:
        copy = Path(tmp) / "events.sqlite"
        shutil.copyfile(path, copy)
        store = EventStore(copy)
        try:
            state = WorldState(locations=content.locations)
            for event in store.read_all():
                state.tick = int(event.tick)
                if event.outcome != "ok":
                    continue
                apply_effects(state, [Effect.from_dict(d) for d in event.effects])
            return {"state_hash": state.hash(), "event_hash": store.event_hash()}
        finally:
            store.close()


def _check(checks: list[dict[str, object]], name: str, ok: bool, **detail: object) -> None:
    checks.append({"name": name, "ok": bool(ok), **detail})


def _apply(backup_dir: Path, manifest: dict, apply_dir: Path) -> dict[str, object]:
    """校验通过后把备份文件装回目标 run 目录；先清掉目标端陈旧 WAL/SHM。"""
    apply_dir.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    for db_name in DATABASE_NAMES:
        entry = (manifest.get("databases") or {}).get(db_name) or {}
        if not entry.get("present"):
            continue
        src = backup_dir / db_name
        dst = apply_dir / db_name
        for stale in (dst.with_name(dst.name + "-wal"), dst.with_name(dst.name + "-shm")):
            if stale.exists():
                stale.unlink()
        shutil.copyfile(src, dst)
        applied.append(str(dst))
    return {"apply_dir": str(apply_dir), "files": applied}


def run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    backup_dir = Path(args.backup_dir)
    if not backup_dir.is_absolute():
        backup_dir = root / backup_dir
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SystemExit(f"[restore] 找不到 manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    work_dir_arg = args.work_dir
    if work_dir_arg:
        work_dir = Path(work_dir_arg)
        if not work_dir.is_absolute():
            work_dir = root / work_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="xkx-restore-"))
        cleanup = not args.keep

    checks: list[dict[str, object]] = []
    report: dict[str, object] = {
        "ok": False,
        "backup_dir": str(backup_dir),
        "work_dir": str(work_dir),
        "manifest_created_at": manifest.get("created_at"),
        "checks": checks,
    }

    content_hash_current: str | None = None
    try:
        sys.path.insert(0, str(root / "src"))
        from wsim.core.content import load_content  # noqa: WPS433

        content_hash_current = load_content(root, allow_fixture=False, use_content=True).content_hash
    except Exception as exc:
        report["content_load_error"] = f"{type(exc).__name__}: {exc}"

    events_entry = (manifest.get("databases") or {}).get("events.sqlite") or {}
    events_src = backup_dir / "events.sqlite"
    events_ok = bool(events_entry.get("present")) and events_src.is_file()
    _check(checks, "events_file_present", events_ok, path=str(events_src))
    if not events_ok:
        report["ok"] = False
        report["failure_reason"] = "events.sqlite 缺失"
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return report

    # 1) 文件完整性与 sha256/大小
    for db_name in DATABASE_NAMES:
        entry = (manifest.get("databases") or {}).get(db_name) or {}
        if not entry.get("present"):
            # events 必须在创建备份时就存在（已在上方单独校验）；service.sqlite 允许
            # 在未启用鉴权的 run 中缺失，manifest 会显式标记 present=false。
            _check(checks, f"{db_name}_present", True, note="manifest 标记未备份")
            continue
        src = backup_dir / db_name
        if not src.is_file():
            _check(checks, f"{db_name}_file", False, path=str(src))
            continue
        dst = work_dir / db_name
        shutil.copyfile(src, dst)
        sha = _sha256(dst)
        size = dst.stat().st_size
        _check(
            checks,
            f"{db_name}_sha256",
            sha == entry.get("sha256"),
            expected=entry.get("sha256"),
            actual=sha,
        )
        _check(
            checks,
            f"{db_name}_size",
            size == int(entry.get("size_bytes") or -1),
            expected=entry.get("size_bytes"),
            actual=size,
        )
        try:
            integrity = _integrity(dst)
        except sqlite3.DatabaseError as exc:
            _check(checks, f"{db_name}_integrity", False, error=f"{type(exc).__name__}: {exc}")
            continue
        _check(
            checks,
            f"{db_name}_integrity",
            integrity == "ok",
            expected="ok",
            actual=integrity,
        )

    restored_events = work_dir / "events.sqlite"
    summary = None
    if restored_events.is_file():
        try:
            summary = _events_summary(restored_events)
        except sqlite3.DatabaseError as exc:
            _check(checks, "events_queryable", False, error=f"{type(exc).__name__}: {exc}")
            summary = None
    if summary is not None:
        manifest_seq = events_entry.get("seq") or {}
        _check(
            checks,
            "events_seq_contiguous",
            bool(summary["seq"]["contiguous"]),  # type: ignore[index]
            seq=summary["seq"],
        )
        for key in ("min", "max", "count"):
            _check(
                checks,
                f"events_seq_{key}",
                summary["seq"][key] == manifest_seq.get(key),  # type: ignore[index]
                expected=manifest_seq.get(key),
                actual=summary["seq"][key],  # type: ignore[index]
            )
        _check(
            checks,
            "events_content_hash_matches_manifest",
            summary["content_hash"] == events_entry.get("content_hash"),
            expected=events_entry.get("content_hash"),
            actual=summary["content_hash"],
        )
        _check(
            checks,
            "content_hash_matches_current_content",
            content_hash_current is not None
            and summary["content_hash"] == content_hash_current,
            manifest_content_hash=events_entry.get("content_hash"),
            current_content_hash=content_hash_current,
        )
        replay = _replay_hashes(restored_events, root)
        _check(
            checks,
            "state_hash_matches_manifest",
            replay["state_hash"] == events_entry.get("state_hash"),
            expected=events_entry.get("state_hash"),
            actual=replay["state_hash"],
        )
        _check(
            checks,
            "event_hash_matches_manifest",
            replay["event_hash"] == events_entry.get("event_hash"),
            expected=events_entry.get("event_hash"),
            actual=replay["event_hash"],
        )

    ok = all(bool(c["ok"]) for c in checks)
    report["ok"] = ok
    report["checks_passed"] = sum(1 for c in checks if c["ok"])
    report["checks_total"] = len(checks)
    if ok and args.apply_dir:
        applied = _apply(backup_dir, manifest, Path(args.apply_dir).resolve())
        report["applied"] = applied
        _log(f"已装回 {applied['apply_dir']}")
    elif not ok:
        report["failure_reason"] = "; ".join(
            str(c["name"]) for c in checks if not c["ok"]
        )

    if cleanup:
        shutil.rmtree(work_dir, ignore_errors=True)
        report["work_dir_removed"] = True

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验并从备份恢复 run 数据")
    parser.add_argument("--root", default=str(ROOT), help="仓库/部署根目录（含 content/ 与 runs/）")
    parser.add_argument("--backup-dir", required=True, help="备份目录（含 manifest.json）")
    parser.add_argument("--work-dir", default="", help="恢复到的临时目录；默认系统临时目录")
    parser.add_argument("--keep", action="store_true", help="保留默认临时目录（不使用 --work-dir 时）")
    parser.add_argument(
        "--apply-dir",
        default="",
        help="校验通过后装回该 run 目录（如 runs/<RUN_ID>；必须先停服）",
    )
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except SystemExit as exc:
        _log(str(exc))
        return 1
    return 0 if result["ok"] else EXIT_VERIFY_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
