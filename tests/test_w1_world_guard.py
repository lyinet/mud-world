"""世界丢失 fail-closed：有账号但事件库缺失/损坏时拒绝启动。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from wsim.api.__main__ import main
from wsim.api.auth import AccountStore

ROOT = Path(__file__).resolve().parents[1]


def _api_world(tmp_path: Path) -> Path:
    root = tmp_path / "world"
    root.mkdir(exist_ok=True)
    (root / "content").symlink_to(ROOT / "content", target_is_directory=True)
    (root / "config").symlink_to(ROOT / "config", target_is_directory=True)
    return root


def _accounts(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    store = AccountStore(run_dir / "service.sqlite", iterations=1_000)
    store.register("u_demo", "secret123")
    store.close()


def _start_process(root: Path, run: str, extra: list[str] | None = None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["XKX_AMBIENT"] = "0"
    proc = subprocess.Popen(
        [sys.executable, "-m", "wsim.api", "--root", str(root), "--run", run,
         "--port", "0", "--auth", "off", *(extra or [])],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    line = proc.stdout.readline()
    return proc, json.loads(line) if line.strip() else None


def test_fresh_world_starts(tmp_path):
    root = _api_world(tmp_path)
    proc, info = _start_process(root, "fresh")
    try:
        assert info and info["ok"] is True
        assert info["world"] == "new"
        assert (root / "runs" / "fresh" / "world_id.json").exists()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_accounts_without_events_refuses_start(tmp_path, capsys):
    root = _api_world(tmp_path)
    run_dir = root / "runs" / "lost"
    _accounts(run_dir)
    code = main(["--root", str(root), "--run", "lost", "--port", "0", "--auth", "off"])
    assert code == 4
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["code"] == "events_missing"
    assert "疑似卷挂载错误" in payload["reason"]


def test_accounts_with_corrupt_events_refuses_start(tmp_path, capsys):
    root = _api_world(tmp_path)
    run_dir = root / "runs" / "corrupt"
    _accounts(run_dir)
    (run_dir / "events.sqlite").write_bytes(b"this is not a sqlite database" * 100)
    code = main(["--root", str(root), "--run", "corrupt", "--port", "0", "--auth", "off"])
    assert code == 5
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["code"] == "events_corrupt"


def test_allow_new_world_override(tmp_path):
    root = _api_world(tmp_path)
    run_dir = root / "runs" / "renew"
    _accounts(run_dir)
    proc, info = _start_process(root, "renew", ["--allow-new-world"])
    try:
        assert info and info["ok"] is True
        assert info["world"] == "new"
        base = info["listen"]
        with urllib.request.urlopen(base + "/api/health", timeout=10) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200 and health["ok"] is True
        assert health["world"]["new_world"] is True
        assert health["world"]["run"] == "renew"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
