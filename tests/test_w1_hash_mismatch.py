"""W1 拒绝启动契约：存档 content_hash 与当前内容包不符。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from wsim.api.__main__ import main
from wsim.api.persistence import (
    CONTENT_HASH_MISMATCH,
    EXIT_CONTENT_HASH_MISMATCH,
    ContentHashMismatch,
)
from wsim.api.session import WorldSession

ROOT = Path(__file__).resolve().parents[1]


def _world_root(tmp_path: Path) -> Path:
    root = tmp_path / "world"
    root.mkdir()
    (root / "content").symlink_to(ROOT / "content", target_is_directory=True)
    (root / "config").symlink_to(ROOT / "config", target_is_directory=True)
    return root


def _make_run(root: Path, run: str = "w1-hash") -> Path:
    session = WorldSession(root, run_id=run, seed=42)
    try:
        assert session.perform("u_demo", "enter", {"display_name": "陈捕快", "archetype": "捕快"})["ok"]
    finally:
        session.close()
    return root / "runs" / run / "events.sqlite"


def test_content_hash_mismatch_refuses_startup(tmp_path, capsys):
    root = _world_root(tmp_path)
    db = _make_run(root)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value='deadbeef-not-the-real-content-hash' WHERE key='content_hash'")
    conn.commit()
    count_before = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    conn.close()

    # 直接构造：抛出带错误码/退出码的异常。
    with pytest.raises(ContentHashMismatch) as excinfo:
        WorldSession(root, run_id="w1-hash", seed=42)
    exc = excinfo.value
    assert exc.code == CONTENT_HASH_MISMATCH
    assert exc.exit_code == EXIT_CONTENT_HASH_MISMATCH
    assert exc.stored == "deadbeef-not-the-real-content-hash"
    assert exc.current and exc.current != exc.stored

    # CLI 契约：退出码 3 + stderr 单行 JSON；不追加事件。
    rc = main(["--root", str(root), "--run", "w1-hash", "--port", "0"])
    assert rc == EXIT_CONTENT_HASH_MISMATCH
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    payload = json.loads(err[0])
    assert payload["ok"] is False
    assert payload["error"] == "content_hash_mismatch"
    assert payload["stored_content_hash"] == "deadbeef-not-the-real-content-hash"
    assert payload["current_content_hash"] == exc.current
    assert payload["run_dir"].endswith("w1-hash")

    conn = sqlite3.connect(db)
    assert int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]) == count_before
    conn.close()
