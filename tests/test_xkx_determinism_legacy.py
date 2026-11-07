"""确定性红线（旧世界）：无玩家 2 天世界的哈希必须逐字节不变。

基线在 P2 战斗改造**之前**采样；任何 Entity 序列化、Effect 分发或调度改动
只要让这些字面量漂移，本测试立即失败。旧内容包没有 spawns.yaml，
`system_spawn` 不应出现在事件流里。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402

LEGACY = {
    "events_total": 735,
    "state_final": "009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9",
    "event_final": "02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae",
    "business_cash": 11442,
    "precondition_blocked": 6,
    "precondition_pruned": 3,
}


def test_no_player_legacy_world_hash_is_unchanged(tmp_path):
    summary = run_headless(
        ROOT,
        RunConfig(days=2, seed=42, npc_limit=0, out_root=tmp_path, run_id="legacy-p2"),
    )
    assert summary["events"]["total"] == LEGACY["events_total"]
    assert summary["hashes"]["state_final"] == LEGACY["state_final"]
    assert summary["hashes"]["event_final"] == LEGACY["event_final"]
    assert summary["economy"]["business_cash_now"] == LEGACY["business_cash"]
    assert summary["precondition_blocked"]["total"] == LEGACY["precondition_blocked"]
    assert summary["precondition_pruned"]["total"] == LEGACY["precondition_pruned"]

    store = EventStore(tmp_path / "legacy-p2" / "events.sqlite")
    verbs = {e.verb for e in store.read_all()}
    store.close()
    assert "system_spawn" not in verbs
