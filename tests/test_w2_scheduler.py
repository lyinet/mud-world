"""W2 自走调度器验收测试（独立于 HTTP 层）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from wsim.core.canonical import hash_obj
from wsim.core.models import Intent
from wsim.sim.intent import MockReasoner
from wsim.sim.scheduler import (
    AmbientConfig,
    AmbientCostModel,
    AmbientReasoner,
    AmbientScheduler,
)

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


def _make(tmp_path: Path, name: str, **kw) -> AmbientScheduler:
    out = tmp_path / name
    shutil.rmtree(out, ignore_errors=True)
    cfg = AmbientConfig(out_dir=out, run_id=name, max_cpu_ratio=0.0, **kw)
    return AmbientScheduler(ROOT, cfg)


@pytest.fixture(scope="module")
def run_300(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("w2-300")
    s = _make(tmp, "run300", seed=42, max_ticks=300)
    s.run_ticks(300)
    return s.final_summary()


def test_rule_mode_acceptance_numbers(run_300):
    sm = run_300
    assert sm["ticks"] >= 300
    assert sm["npc_events"] >= 12
    assert sm["active_npcs"] >= 4
    assert sm["distinct_verbs"] >= 3
    assert sm["llm_calls"] == 0
    assert sm["cost"]["llm_enabled"] is False
    assert sm["cost_usd"] == 0.0
    assert sm["cpu_guard_sleeps"] >= 0


def test_same_seed_hashes_identical(tmp_path):
    a = _make(tmp_path, "det-a", seed=42, max_ticks=150)
    b = _make(tmp_path, "det-b", seed=42, max_ticks=150)
    a.run_ticks(150)
    b.run_ticks(150)
    sa, sb = a.final_summary(), b.final_summary()
    assert sa["state_hash"] == sb["state_hash"]
    assert sa["event_hash"] == sb["event_hash"]
    assert sa["event_chain"] == sb["event_chain"]
    assert sa["progress_chain"] == sb["progress_chain"]
    assert (a.out_dir / "ambient_progress.jsonl").read_text() == (
        b.out_dir / "ambient_progress.jsonl"
    ).read_text()
    assert (a.out_dir / "ambient.jsonl").read_text() == (b.out_dir / "ambient.jsonl").read_text()


def test_player_enter_keeps_progress_and_chain(tmp_path):
    s = _make(tmp_path, "player", seed=42)
    s.run_ticks(120)
    progress_path = s.out_dir / "ambient_progress.jsonl"
    prefix = progress_path.read_text()
    assert s.tick >= 100
    rows = [json.loads(x) for x in prefix.strip().splitlines()]
    assert [r["tick"] for r in rows] == list(range(len(rows)))
    enter = s.enter_player()
    assert enter["outcome"] == "ok"
    assert enter["tick"] == rows[-1]["tick"]
    assert enter["seq"] > rows[-1]["seq"]
    s.run_ticks(30)
    # 新语义：36 tick 无真实请求算断线。模拟客户端每 30 tick 一次心跳，
    # 让“玩家在线跨一小时”的断言仍然成立（心跳本身不改世界进度链）。
    with s._guard():
        s.writer.submit(
            Intent(
                actor="player:visitor",
                verb="player_heartbeat",
                args={"player_id": "player:visitor"},
                tick=int(s.writer.state.tick),
                source="player",
                reason="test_keepalive",
            )
        )
    s.run_ticks(30)
    leave = s.leave_player()
    assert leave["outcome"] == "ok"
    after = progress_path.read_text()
    assert after.startswith(prefix), "玩家进入不得改写/回滚已推进的 hash 记录"
    rows2 = [json.loads(x) for x in after.strip().splitlines()]
    assert [r["tick"] for r in rows2] == list(range(len(rows2)))
    # 逐 tick 重算链：任意相邻两条的 chain 都应由 prev 决定，且无重置。
    prev = ""
    for r in rows2:
        expected = hash_obj(
            {
                "prev": prev,
                "tick": r["tick"],
                "seq": r["seq"],
                "state": r["state_hash"],
                "event_chain": r["event_chain"],
            }
        )
        assert r["chain"] == expected
        prev = r["chain"]
    sm = s.final_summary()
    assert len(sm["player_actions"]) == 2
    assert sm["progress_records"] == 180


def test_pause_five_minutes_then_resume(tmp_path):
    clock = FakeClock()
    s = AmbientScheduler(
        ROOT,
        AmbientConfig(out_dir=tmp_path / "pause", run_id="pause", max_cpu_ratio=0.0),
        clock=clock,
        sleeper=clock.sleep,
        cpu_clock=lambda: 0.0,
    )
    sm = s.start(minutes=12, pause_at_tick=40, resume_after_s=300)
    ctl = sm["control"]
    pauses = [c for c in ctl if c["command"] == "pause"]
    resumes = [c for c in ctl if c["command"] == "resume"]
    assert pauses and resumes
    assert pauses[0]["tick"] == resumes[0]["tick"] == 40
    assert resumes[0]["elapsed_s"] - pauses[0]["elapsed_s"] >= 299.0
    # 暂停 5 分钟世界不前进；恢复后继续推进。
    rows = [
        json.loads(x)
        for x in (s.out_dir / "ambient_progress.jsonl").read_text().strip().splitlines()
    ]
    assert [r["tick"] for r in rows] == list(range(len(rows)))
    assert len(rows) == sm["ticks"]
    assert sm["ticks"] == 70
    assert 0 <= max(r["tick"] for r in rows) < 40 + 31


def test_speed_multiplier_4x(tmp_path):
    results = {}
    for speed in (1.0, 4.0):
        clock = FakeClock()
        s = AmbientScheduler(
            ROOT,
            AmbientConfig(
                out_dir=tmp_path / f"spd-{int(speed)}",
                run_id=f"spd-{int(speed)}",
                speed=speed,
                max_cpu_ratio=0.0,
            ),
            clock=clock,
            sleeper=clock.sleep,
            cpu_clock=lambda: 0.0,
        )
        results[speed] = s.start(minutes=1)["ticks"]
    assert results[4.0] / results[1.0] == pytest.approx(4.0, abs=0.2)
    assert 3 <= results[4.0] / results[1.0] <= 5


def test_speed_4x_density_within_30pct(tmp_path):
    slow = _make(tmp_path, "slow", seed=42, max_ticks=300)
    slow.run_ticks(300)
    fast = _make(tmp_path, "fast", seed=42, max_ticks=1200)
    fast.run_ticks(1200)
    s1, s4 = slow.final_summary(), fast.final_summary()
    d1 = s1["events_total"] / s1["ticks"] * 100.0
    d4 = s4["events_total"] / s4["ticks"] * 100.0
    deviation = abs(d4 - d1) / d1
    assert deviation <= 0.30, (d1, d4, deviation)


def test_llm_hard_cap_degrades_to_rule(tmp_path):
    s = _make(tmp_path, "llm", seed=42)
    content = s.content
    cost = AmbientCostModel(llm_enabled=True, max_calls=2)
    rule = MockReasoner(seed=42, content=content, telemetry=None)
    calls = {"n": 0}

    class FakeModel:
        def decide(self, npc_state, world_view):
            calls["n"] += 1
            return []

    def factory():
        return FakeModel()

    router = AmbientReasoner(rule, cost, factory)
    view = {
        "tick": 10, "tier": "spotlight",
        "state": s.writer.state, "content": content,
    }
    for _ in range(6):
        router.decide({"id": s.npc_ids[0]}, view)
    assert cost.calls == 2
    assert calls["n"] == 2
    assert cost.degraded_to_rules == 4
    assert cost.last_denied_reason == "hard_cap_reached"
    # 非 spotlight 永不调用模型。
    router.decide({"id": s.npc_ids[0]}, {**view, "tier": "active"})
    assert calls["n"] == 2


def test_rule_mode_never_calls_model(tmp_path):
    s = _make(tmp_path, "rule", seed=42, max_ticks=60)
    s.run_ticks(60)
    sm = s.final_summary()
    assert sm["cost"]["llm_calls"] == 0
    assert sm["cost"]["degraded_to_rules"] == 0
    assert sm["cost"]["spotlight_wakes"] > 0


def test_cpu_guard_sleeps(tmp_path):
    clock = FakeClock()
    s = AmbientScheduler(
        ROOT,
        AmbientConfig(out_dir=tmp_path / "cpu", run_id="cpu", max_cpu_ratio=0.5),
        clock=clock,
        sleeper=clock.sleep,
        cpu_clock=lambda: 0.0,
    )
    s._cpu_guard(tick_wall=0.3, tick_cpu=0.9)
    assert s._cpu_guard_sleeps == 1
    assert sum(clock.sleeps) == pytest.approx(1.5, abs=1e-6)
    sm = s.final_summary()
    assert sm["cpu_guard_sleeps"] == 1


def test_error_written_not_swallowed(tmp_path):
    s = _make(tmp_path, "boom", seed=42)
    for npc in s.npc_ids:
        s.lod.set_tier(npc, "spotlight", now=0)

    def boom(npc_state, world_view):
        raise RuntimeError("model exploded")

    s.reasoner.decide = boom
    with pytest.raises(RuntimeError, match="model exploded"):
        s.run_ticks(40)
    path = s.out_dir / "ambient_error.json"
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["phase"] == "tick"
    assert "RuntimeError: model exploded" in payload["error"]
    assert "Traceback" in payload["traceback"]


def test_hourly_summary_and_audit(tmp_path):
    s = _make(tmp_path, "obs", seed=42, max_ticks=61)
    s.run_ticks(61)
    sm = s.final_summary()
    summaries = [
        json.loads(x)
        for x in (s.out_dir / "ambient_summaries.jsonl").read_text().strip().splitlines()
    ]
    assert len(summaries) == 11  # tick 0,6,...,60
    for row in summaries:
        for key in (
            "tick", "events_total", "npc_count", "verbs_total", "llm_calls",
            "cost_usd", "elapsed_s",
        ):
            assert key in row
    audit = [
        json.loads(x)
        for x in (s.out_dir / "ambient.jsonl").read_text().strip().splitlines()
    ]
    assert len(audit) == sm["events_total"]
    assert [r["seq"] for r in audit] == list(range(1, len(audit) + 1))
    prev_chain = ""
    for row in audit:
        expected = hash_obj({"prev": prev_chain, "event": row["canonical"]})
        assert row["event_chain"] == expected
        prev_chain = row["event_chain"]


def test_control_file_and_step(tmp_path):
    control = tmp_path / "ctl.json"
    s = AmbientScheduler(
        ROOT,
        AmbientConfig(
            out_dir=tmp_path / "ctl-run", run_id="ctl", control_file=control,
            max_cpu_ratio=0.0,
        ),
        clock=FakeClock(),
        sleeper=lambda _s: None,
        cpu_clock=lambda: 0.0,
    )
    s.run_ticks(10)
    control.write_text(json.dumps({"command": "pause"}))
    s._poll_control()
    assert s.paused is True
    before = s.tick
    s.step(2)
    assert s.tick == before + 2
    control.write_text(json.dumps({"command": "speed", "speed": 4}))
    s._poll_control()
    assert s.speed == 4.0
    control.write_text(json.dumps({"command": "resume"}))
    s._poll_control()
    assert s.paused is False
    sm = s.final_summary()
    commands = [c["command"] for c in sm["control"]]
    assert commands == ["pause", "step", "speed", "resume"]
