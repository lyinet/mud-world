"""同 seed 复现 + 事件回放：日志是唯一事实源。"""
from __future__ import annotations

from pathlib import Path

from conftest import ROOT
from wsim.core.content import load_content
from wsim.core.models import Effect
from wsim.sim.headless import RunConfig, run_headless
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState
from wsim.world.store import EventStore


def _run(tmp_path: Path, run_id: str) -> dict:
    cfg = RunConfig(days=2, seed=7, npc_limit=5, out_root=tmp_path, run_id=run_id)
    return run_headless(ROOT, cfg)


def test_same_seed_same_hashes(tmp_path):
    a = _run(tmp_path, "a")
    b = _run(tmp_path, "b")
    assert a["hashes"]["state_final"] == b["hashes"]["state_final"]
    assert a["hashes"]["event_final"] == b["hashes"]["event_final"]
    assert a["events"]["total"] == b["events"]["total"]


def test_event_log_replays_to_same_state(tmp_path):
    summary = _run(tmp_path, "replay")
    run_dir = tmp_path / "replay"
    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    state = WorldState(locations=content.locations)
    store = EventStore(run_dir / "events.sqlite")
    for e in store.read_all():
        if e.outcome == "ok":
            apply_effects(state, [Effect.from_dict(d) for d in e.effects])
        state.tick = e.tick
    store.close()
    assert state.hash() == summary["hashes"]["state_final"]


def test_debt_audit_rebuilds_from_events(tmp_path):
    from wsim.audit.debt_audit import debt_audit

    summary = _run(tmp_path, "audit")
    run_dir = tmp_path / "audit"
    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    state = WorldState(locations=content.locations)
    store = EventStore(run_dir / "events.sqlite")
    events = store.read_all()
    for e in events:
        if e.outcome == "ok":
            apply_effects(state, [Effect.from_dict(d) for d in e.effects])
        state.tick = e.tick
    store.close()
    audit = debt_audit(events, state, content)
    assert audit["status"] == "pass"
    assert audit["checks"]["reconstruction_matches_state"]["ok"]
    assert audit["checks"]["event_double_entry"]["ok"]
    assert audit["checks"]["debt_amount_matches_origin"]["ok"]
