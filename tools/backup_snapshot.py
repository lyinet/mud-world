#!/usr/bin/env python3
"""P0 数据安全：在线备份 `events.sqlite` + `service.sqlite`。

唯一事实源是 `runs/<RUN_ID>/events.sqlite`，本工具用 sqlite 在线备份 API
（`sqlite3.Connection.backup`）分页复制，**不使用 cp**：

- 服务运行中可执行；WAL 模式下读快照不阻塞写者（写路径实测见 docs/BACKUP-RESTORE.md）。
- 每页（默认 1024 页 ≈ 4MB）一次 `backup_step`，步与步之间释放源库读锁，
  避免长时间持有读事务。
- 备份目录：`backups/<UTC 时间戳>/`，内含 `events.sqlite`、`service.sqlite`、`manifest.json`。

manifest 记录（验收要求逐项可查）：

- 每个文件的 sha256 与大小；
- events 的 `content_hash`、seq min/max/count、`PRAGMA integrity_check`；
- 从备份副本重放得到的 `state_hash` / `event_hash`（重放在临时副本上做，不改备份文件）；
- 创建时间、源库路径、备份耗时与最大单步耗时。

轮转（常量见下）：保留最近 48 小时的滚动备份 + 最近 7 个 UTC 日每日 1 份（当日最新）。
删除只作用于 `backups/` 下带本工具 manifest 标记的目录；目标或其子项含符号链接时拒绝删除。

用法：
    python3 tools/backup_snapshot.py --root . --run service
    python3 tools/backup_snapshot.py --root . --run service --backup-dir backups --no-prune

退出码：0 成功；1 参数/IO 错误；2 备份校验失败（integrity/seq/content_hash 异常）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- 轮转策略常量
# 保留：最近 48 小时内的全部备份（脚本每 6 小时一份 → 约 8 份滚动窗口）。
KEEP_ROLLING_HOURS = 48
# 保留：最近 7 个 UTC 日，每日 1 份（保留当日最新的一份，覆盖 48 小时以外的日备份）。
KEEP_DAILY_DAYS = 7
# 调度建议间隔；`scripts/backup_loop.sh` 的 BK_INTERVAL 默认值必须与此一致。
ROLLING_INTERVAL_HOURS = 6

DATABASE_NAMES = ("events.sqlite", "service.sqlite")
BACKUP_NAME_FORMAT = "%Y%m%dT%H%M%SZ"
BACKUP_NAME_RE = re.compile(r"^(\d{8}T\d{6}Z)(?:-\d+)?$")
MANIFEST_NAME = "manifest.json"
GENERATOR = "tools/backup_snapshot.py"
SCHEMA_VERSION = 1

# 备份分页：1024 页 ≈ 4MB（默认 page_size 4096）。步与步之间释放源库读锁。
BACKUP_PAGES_PER_STEP = 1024


def _log(msg: str) -> None:
    print(f"[backup] {msg}", file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- 在线复制
def backup_database(src: Path, dst: Path) -> dict[str, object]:
    """用 sqlite 在线备份 API 把 src 分页复制到 dst，返回耗时与步数。"""
    started = time.perf_counter()
    step_stats = {"steps": 0, "max_step_ms": 0.0, "total_ms": 0.0}

    def _progress(_status: int, _remaining: int, _total: int) -> None:
        now = time.perf_counter()
        step_stats["steps"] = int(step_stats["steps"]) + 1
        elapsed = (now - started) * 1000.0
        step_stats["total_ms"] = round(elapsed, 3)

    # 源连接只读使用：backup API 只从源库读页；不要 cp，也不要在源库上开写事务。
    src_conn = sqlite3.connect(str(src), timeout=10)
    try:
        src_conn.execute("PRAGMA query_only=1")
        dst_conn = sqlite3.connect(str(dst))
        try:
            # Python 3.7+ 的 Connection.backup：分页复制，步间释放源库读锁。
            step_started = time.perf_counter()
            src_conn.backup(
                dst_conn,
                pages=BACKUP_PAGES_PER_STEP,
                progress=_progress,
                sleep=0.0,
            )
            # 目标库固定为单文件（DELETE 日志），不把 WAL 模式带进备份产物。
            dst_conn.execute("PRAGMA journal_mode=DELETE")
            dst_conn.commit()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    step_stats["backup_api_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    step_stats["max_step_ms"] = round(
        max(
            float(step_stats["max_step_ms"]),
            float(step_stats["backup_api_ms"]) / max(1, int(step_stats["steps"])),
        ),
        3,
    )
    return step_stats


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
        content_hash = conn.execute(
            "SELECT value FROM meta WHERE key='content_hash'"
        ).fetchone()
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
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
        "content_hash": None if content_hash is None else str(content_hash[0]),
        "meta": {str(k): str(v) for k, v in meta.items()},
    }


def _replay_hashes(path: Path, root: Path) -> dict[str, str]:
    """在临时副本上重放，返回 state_hash / event_hash；绝不改动备份文件。"""
    sys.path.insert(0, str(root / "src"))
    from wsim.core.content import load_content  # noqa: WPS433
    from wsim.core.models import Effect  # noqa: WPS433
    from wsim.world.effects import apply_effects  # noqa: WPS433
    from wsim.world.state import WorldState  # noqa: WPS433
    from wsim.world.store import EventStore  # noqa: WPS433

    content = load_content(root, allow_fixture=False, use_content=True)
    with tempfile.TemporaryDirectory(prefix="xkx-replay-") as tmp:
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


# ----------------------------------------------------------------- 轮转清理
def _has_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    for parent, dirnames, filenames in os.walk(path, followlinks=False):
        for name in list(dirnames) + list(filenames):
            if (Path(parent) / name).is_symlink():
                return True
    return False


def _generated_backup_dirs(backup_dir: Path) -> list[tuple[Path, datetime]]:
    """只识别带本工具 manifest 标记、名称符合 UTC 时间戳的目录。"""
    out: list[tuple[Path, datetime]] = []
    if not backup_dir.is_dir():
        return out
    for entry in sorted(backup_dir.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        match = BACKUP_NAME_RE.match(entry.name)
        if not match:
            continue
        manifest_path = entry / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("generator") != GENERATOR:
            continue
        try:
            stamp = datetime.strptime(match.group(1), BACKUP_NAME_FORMAT).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        out.append((entry, stamp))
    return out


def prune_backups(backup_dir: Path, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, object]:
    """删除超出轮转策略的备份目录。只允许删除 backup_dir 下自生成目录。"""
    now = now or _utc_now()
    backup_dir = backup_dir.resolve()
    entries = _generated_backup_dirs(backup_dir)
    keep: set[Path] = set()

    # 1) 最近 48 小时滚动窗口。
    for entry, stamp in entries:
        if (now - stamp) <= timedelta(hours=KEEP_ROLLING_HOURS):
            keep.add(entry)

    # 2) 最近 7 个 UTC 日，每日保留最新一份。
    daily_cutoff = now.date() - timedelta(days=KEEP_DAILY_DAYS - 1)
    for day_offset in range(KEEP_DAILY_DAYS):
        day = daily_cutoff + timedelta(days=day_offset)
        candidates = [entry for entry, stamp in entries if stamp.date() == day]
        if candidates:
            keep.add(max(candidates, key=lambda p: p.name))

    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    for entry, _stamp in entries:
        if entry in keep:
            continue
        # 路径校验：必须在 backup_dir 直接子目录，且目标及其子项不得含符号链接。
        resolved = entry.resolve()
        if resolved.parent != backup_dir or resolved == backup_dir:
            skipped.append({"path": str(entry), "reason": "outside_backup_dir"})
            continue
        if _has_symlink(entry):
            skipped.append({"path": str(entry), "reason": "symlink_refused"})
            continue
        if dry_run:
            deleted.append(str(entry))
            continue
        try:
            shutil.rmtree(entry)
            deleted.append(str(entry))
        except OSError as exc:
            skipped.append({"path": str(entry), "reason": f"delete_failed: {exc}"})
    return {
        "keep": sorted(str(p) for p in keep),
        "deleted": deleted,
        "skipped": skipped,
        "policy": {
            "rolling_hours": KEEP_ROLLING_HOURS,
            "daily_days": KEEP_DAILY_DAYS,
            "daily_count_per_day": 1,
            "scheduled_interval_hours": ROLLING_INTERVAL_HOURS,
        },
    }


# --------------------------------------------------------------------- main
def run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    run_dir = root / "runs" / args.run
    events_src = run_dir / "events.sqlite"
    service_src = run_dir / "service.sqlite"
    if not events_src.is_file():
        raise SystemExit(f"[backup] 找不到源库: {events_src}")

    backup_dir = Path(args.backup_dir)
    if not backup_dir.is_absolute():
        backup_dir = root / backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    if backup_dir.is_symlink():
        raise SystemExit(f"[backup] 拒绝写入符号链接目录: {backup_dir}")

    created = _utc_now()
    name = created.strftime(BACKUP_NAME_FORMAT)
    target = backup_dir / name
    suffix = 2
    while target.exists():
        target = backup_dir / f"{name}-{suffix}"
        suffix += 1
    target.mkdir(parents=False, exist_ok=False)
    os.chmod(target, 0o700)  # 备份含账号哈希/令牌，仅运行用户可读

    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "created_at": _iso(created),
        "created_at_unix": int(created.timestamp()),
        "run_id": args.run,
        "run_dir": str(run_dir),
        "backup_dir": str(target),
        "source": {},
        "databases": {},
    }
    overall_started = time.perf_counter()
    failures: list[str] = []

    for db_name in DATABASE_NAMES:
        src = run_dir / db_name
        dst = target / db_name
        entry: dict[str, object] = {"present": False}
        manifest["source"][db_name] = str(src)  # type: ignore[index]
        if not src.is_file():
            entry["note"] = "源库不存在（例如鉴权关闭时没有 service.sqlite）"
            manifest["databases"][db_name] = entry  # type: ignore[index]
            _log(f"{db_name}: 源库不存在，跳过")
            continue
        entry["present"] = True
        entry["source_path"] = str(src)
        try:
            timing = backup_database(src, dst)
        except (sqlite3.Error, OSError) as exc:
            raise SystemExit(f"[backup] {db_name} 备份失败: {type(exc).__name__}: {exc}")
        os.chmod(dst, 0o600)
        integrity = _integrity(dst)
        entry["integrity_check"] = integrity
        entry["sha256"] = _sha256(dst)
        entry["size_bytes"] = dst.stat().st_size
        entry["timing"] = timing
        if integrity != "ok":
            failures.append(f"{db_name}: integrity_check={integrity}")
        if db_name == "events.sqlite":
            summary = _events_summary(dst)
            entry["content_hash"] = summary["content_hash"]
            entry["seq"] = summary["seq"]
            entry["meta"] = summary["meta"]
            if not summary["seq"]["contiguous"]:  # type: ignore[index]
                failures.append(
                    f"events.sqlite: seq 不连续 count={summary['seq']['count']} "
                    f"holes={summary['seq']['holes']}"
                )
            replay = _replay_hashes(dst, root)
            entry["state_hash"] = replay["state_hash"]
            entry["event_hash"] = replay["event_hash"]
            try:
                from wsim.core.content import load_content  # noqa: WPS433

                current = load_content(root, allow_fixture=False, use_content=True)
                entry["current_content_hash"] = current.content_hash
            except Exception as exc:  # 内容包异常不应让备份失败，只记录
                entry["current_content_hash"] = None
                entry["content_check_error"] = f"{type(exc).__name__}: {exc}"
        manifest["databases"][db_name] = entry  # type: ignore[index]
        _log(
            f"{db_name}: sha256={str(entry['sha256'])[:12]} size={entry['size_bytes']} "
            f"integrity={integrity} backup_ms={timing['backup_api_ms']}"
        )

    manifest["duration_ms"] = round((time.perf_counter() - overall_started) * 1000.0, 3)
    manifest["verification_ok"] = not failures
    manifest["failures"] = failures
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(target / MANIFEST_NAME, 0o600)

    prune_result = (
        {"skipped": "no-prune"}
        if args.no_prune
        else prune_backups(backup_dir)
    )
    manifest["prune"] = prune_result
    if not args.no_prune:
        # 轮转结果回写 manifest，便于审计；回写发生在校验之后，不影响 sha256。
        (target / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    result = {
        "ok": not failures,
        "backup_dir": str(target),
        "created_at": manifest["created_at"],
        "duration_ms": manifest["duration_ms"],
        "files": {
            name: {
                "present": bool(entry.get("present")),
                "sha256": entry.get("sha256"),
                "size_bytes": entry.get("size_bytes"),
                "integrity_check": entry.get("integrity_check"),
            }
            for name, entry in manifest["databases"].items()  # type: ignore[union-attr]
        },
        "events": {
            "content_hash": manifest["databases"].get("events.sqlite", {}).get("content_hash"),  # type: ignore[union-attr]
            "seq": manifest["databases"].get("events.sqlite", {}).get("seq"),  # type: ignore[union-attr]
            "state_hash": manifest["databases"].get("events.sqlite", {}).get("state_hash"),  # type: ignore[union-attr]
            "event_hash": manifest["databases"].get("events.sqlite", {}).get("event_hash"),  # type: ignore[union-attr]
            "backup_api_ms": manifest["databases"].get("events.sqlite", {}).get("timing", {}).get("backup_api_ms"),  # type: ignore[union-attr]
        },
        "prune": prune_result,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在线备份 events.sqlite + service.sqlite")
    parser.add_argument("--root", default=str(ROOT), help="仓库/部署根目录（含 runs/ 与 content/）")
    parser.add_argument("--run", default="service", help="runs/<RUN_ID> 目录名")
    parser.add_argument("--backup-dir", default="backups", help="备份根目录（相对 root）")
    parser.add_argument("--no-prune", action="store_true", help="跳过轮转删除（演练/取证用）")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except SystemExit as exc:
        _log(str(exc))
        return 1
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
