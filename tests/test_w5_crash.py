"""W5 崩溃恢复演练：压测中途 kill -9，重启后校验存档、创世、令牌与内容哈希。"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _http(base: str, path: str, *, method: str = "GET", body: dict | None = None,
          token: str = "", timeout: float = 10.0) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start(root: Path, run: str, port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.Popen(
        [sys.executable, "-m", "wsim.api", "--root", str(root), "--run", run,
         "--host", "127.0.0.1", "--port", str(port), "--auth", "on",
         "--auth-iterations", "1000"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_ready(base: str, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(f"服务提前退出 code={proc.returncode}:\n{out[-2000:]}")
        try:
            status, payload = _http(base, "/api/service/info", timeout=1.0)
            if status == 200 and payload.get("ok"):
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError("服务未在 20s 内就绪")


def _event_stats(run_dir: Path) -> tuple[int, int, int]:
    conn = sqlite3.connect(f"file:{run_dir / 'events.sqlite'}?mode=ro", uri=True)
    try:
        total = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        genesis = int(conn.execute("SELECT COUNT(*) FROM events WHERE verb='genesis'").fetchone()[0])
        max_seq = int(conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])
        return total, genesis, max_seq
    finally:
        conn.close()


def _copy_run(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


def test_kill9_then_restart_recovers(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    shutil.copytree(ROOT / "content", root / "content")
    shutil.copytree(ROOT / "config", root / "config")
    (root / "runs").mkdir()

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = _start(root, "w5-crash", port)
    try:
        _wait_ready(base, proc)

        status, payload = _http(base, "/api/auth/register", method="POST",
                                body={"username": "crash001", "password": "crash-secret"})
        assert status == 200 and payload["ok"], payload
        status, payload = _http(base, "/api/auth/login", method="POST",
                                body={"username": "crash001", "password": "crash-secret"})
        assert status == 200 and payload["ok"], payload
        token = payload["token"]

        status, payload = _http(base, "/api/player/enter", method="POST",
                                body={"display_name": "崩测001", "archetype": "游侠"}, token=token)
        assert status == 200 and payload["ok"], payload
        for _ in range(3):
            status, payload = _http(base, "/api/player/talk",
                                    method="POST", body={"target": "", "message": "测试"}, token=token)
            assert status == 200, payload
            time.sleep(0.05)

        run_dir = root / "runs" / "w5-crash"
        before = _event_stats(run_dir)
        assert before[1] == 1, before  # 一次创世

        # 模拟压测中途进程被强杀
        proc.kill()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # 重启同一 run：令牌应仍有效，存档可读，不重复创世
    port2 = _free_port()
    base2 = f"http://127.0.0.1:{port2}"
    proc2 = _start(root, "w5-crash", port2)
    try:
        _wait_ready(base2, proc2)
        status, payload = _http(base2, "/api/auth/me", token=token)
        assert status == 200 and payload["ok"], payload
        assert payload["account_id"] == "crash001"

        status, payload = _http(base2, "/api/player/state?account_id=crash001")
        assert status == 200 and payload["ok"], payload
        # 服务模式匿名读只返回公共摘要（name 在顶层，不再有 player.player 私有块）。
        assert payload["name"] == "崩测001"

        after = _event_stats(run_dir)
        assert after[1] == 1, after  # 0 重复创世
        assert after[0] >= before[0], (before, after)
        assert after[2] == after[0], (after, "seq 必须连续")

        # 只读校验：integrity_check / seq / 钱包守恒 / 无半写
        spec = spec_from_file_location("w5_load_test", ROOT / "tools" / "load_test.py")
        module = module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        invariants = module.verify_event_log(run_dir)
        assert invariants["ok"] is True, invariants["errors"]
        assert invariants["integrity_check"] == "ok"
        assert invariants["seq"]["contiguous"] is True
    finally:
        if proc2.poll() is None:
            proc2.terminate()
            try:
                proc2.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc2.kill()
                proc2.wait(timeout=5)

    # 内容哈希校验在崩溃+重启后仍然生效：篡改副本 meta 后必须退出码 3。
    tampered = root / "runs" / "w5-crash-tampered"
    _copy_run(run_dir, tampered)
    conn = sqlite3.connect(tampered / "events.sqlite")
    try:
        conn.execute("UPDATE meta SET value='deadbeef' WHERE key='content_hash'")
        conn.commit()
    finally:
        conn.close()

    port3 = _free_port()
    proc3 = _start(root, "w5-crash-tampered", port3)
    try:
        code = proc3.wait(timeout=20)
    finally:
        if proc3.poll() is None:
            proc3.kill()
            proc3.wait(timeout=5)
    output = proc3.stdout.read() if proc3.stdout else ""
    assert code == 3, f"内容哈希不符应退出码 3，实际 {code}\n{output[-1000:]}"
    assert "content_hash_mismatch" in output, output[-1000:]
