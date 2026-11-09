"""原版 `feature/attack.lpc` killer 名单复刻测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.sim.aggression import killer_chase_enabled, configure_killer_chase  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.world.state import Entity  # noqa: E402

LEGACY = {
    "events_total": 735,
    "state_final": "009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9",
    "event_final": "02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae",
    "business_cash": 11442,
    "precondition_blocked": 6,
    "precondition_pruned": 3,
}


def _restore(value: bool) -> None:
    configure_killer_chase(value)


def test_entity_serialization_omits_empty_killer_and_pursuer():
    ent = Entity(id="npc:test", name="测试", role="monster", location="room")
    data = ent.to_dict()
    assert "killer" not in data
    assert "pursuer" not in data

    ent.killer = ["player:p1"]
    ent.pursuer = True
    data = ent.to_dict()
    assert data["killer"] == ["player:p1"]
    assert data["pursuer"] is True


def test_killer_chase_off_keeps_legacy_baseline(tmp_path):
    old = killer_chase_enabled()
    try:
        configure_killer_chase(False)
        summary = run_headless(
            ROOT,
            RunConfig(days=2, seed=42, npc_limit=0, out_root=tmp_path, run_id="legacy-killer-off"),
        )
        assert summary["events"]["total"] == LEGACY["events_total"]
        assert summary["hashes"]["state_final"] == LEGACY["state_final"]
        assert summary["hashes"]["event_final"] == LEGACY["event_final"]
        assert summary["economy"]["business_cash_now"] == LEGACY["business_cash"]
        assert summary["precondition_blocked"]["total"] == LEGACY["precondition_blocked"]
        assert summary["precondition_pruned"]["total"] == LEGACY["precondition_pruned"]
    finally:
        _restore(old)
