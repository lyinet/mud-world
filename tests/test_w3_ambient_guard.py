"""W3 世界自走默认开启：CPU 护栏三态、状态文件、父进程看护。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from wsim.sim.scheduler import AmbientConfig, AmbientScheduler

ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += max(0.0, float(seconds))


def _scheduler(tmp_path: Path, name: str, *, clock: FakeClock | None = None, **kw):
    cfg = AmbientConfig(out_dir=tmp_path / name, run_id=name, **kw)
    return AmbientScheduler(ROOT, cfg, clock=clock)


def _status(s: AmbientScheduler) -> dict:
    return json.loads(s.status_path.read_text(encoding="utf-8"))


def _control_rows(s: AmbientScheduler) -> list[dict]:
    path = s.out_dir / "ambient_control.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_default_cpu_limit_is_70_percent():
    cfg = AmbientConfig(out_dir=Path("/tmp/xkx-ambient-default"), run_id="default")
    assert cfg.max_cpu_ratio == 0.70
    assert cfg.cpu_pause_after_s == 2.0
    assert cfg.cpu_resume_after_s == 2.0
    assert cfg.llm_enabled is False and cfg.llm_max_calls == 0


def test_cpu_guard_throttle_then_pause_then_auto_resume(tmp_path):
    clock = FakeClock()
    s = _scheduler(
        tmp_path,
        "cpu-guard",
        clock=clock,
        max_cpu_ratio=0.5,
        cpu_pause_after_s=2.0,
        cpu_resume_below_ratio=0.2,
        cpu_resume_after_s=0.5,
    )
    # 首个 0.3s 窗口忙 0.9s CPU：补睡 1.5s 后比例回落到 50%，但累计超限 1.8s，尚未暂停。
    s._cpu_guard(0.3, 0.9)
    assert s._cpu_guard_sleeps == 1
    assert s._cpu_guard_slept_s == 1.5
    assert not s.paused
    # 第二个窗口把持续超限推到 3.6s：暂停自走，而不是继续拖慢服务端。
    s._cpu_guard(0.3, 0.9)
    assert s.paused and s._cpu_paused and s._cpu_pauses == 1
    assert s._pause_reason == "cpu_ratio_over_limit"
    status = _status(s)
    assert status["state"] == "paused"
    assert status["cpu"]["pauses"] == 1
    assert any(
        row["command"] == "pause" and row["source"] == "cpu_guard" for row in _control_rows(s)
    )
    # 暂停后空闲比例低于恢复线，持续 0.5s 自动恢复，回到正常速度。
    for _ in range(3):
        s._cpu_pause_watch(0.25, 0.01)
    assert not s.paused and not s._cpu_paused and s._cpu_resumes == 1
    assert _status(s)["state"] == "running"
    assert any(
        row["command"] == "resume" and row["source"] == "cpu_guard" for row in _control_rows(s)
    )


def test_status_file_tracks_tick_pause_resume_stop(tmp_path):
    s = _scheduler(tmp_path, "status", max_cpu_ratio=0.0)
    initial = _status(s)
    assert initial["state"] == "running" and initial["tick"] == 0
    s.run_ticks(5)
    running = _status(s)
    assert running["state"] == "running" and running["tick"] == s.tick == 4
    assert running["last_tick_at"]
    assert running["cpu"]["limit"] == 0.0
    s.pause(source="test", reason="manual")
    assert _status(s)["state"] == "paused"
    s.resume(source="test")
    assert _status(s)["state"] == "running"
    s.stop(source="test")
    assert _status(s)["state"] == "stopped"


def test_parent_pid_watchdog_stops_when_parent_gone(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    clock = FakeClock()
    s = _scheduler(tmp_path, "parent", clock=clock, parent_pid=child.pid, max_cpu_ratio=0.0)
    s.start(minutes=0.01)
    assert s.stopped and s.tick == 0
    assert s._stop_reason == "parent_gone"
    status = _status(s)
    assert status["state"] == "stopped" and status["reason"] == "parent_gone"
