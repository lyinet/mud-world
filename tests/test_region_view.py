"""region / region_name 只读透出：内容包覆盖与缺失时的 null 语义。"""
from __future__ import annotations

import json
from pathlib import Path

from wsim.api import WorldSession
from wsim.api.xkx_content import load_xkx_content

ROOT = Path(__file__).resolve().parents[1]
XKX_CONTENT = ROOT / "content" / "xkx"
WORLD_CONFIG = ROOT / "config" / "worlds" / "xkx.yaml"
ACCOUNT = "region_tester"


def test_xkx_content_locations_expose_region():
    raw = json.loads((XKX_CONTENT / "rooms.json").read_text(encoding="utf-8"))["rooms"]
    with_region = [row for row in raw if row.get("region")]
    assert len(raw) >= 200
    assert len(with_region) >= 200

    content = load_xkx_content(XKX_CONTENT)
    assert len(content.locations) >= 190
    for row in content.locations.values():
        assert row.get("region")
        assert row.get("region_name")

    # 唯一缺 region 的房间是边界房，不建地点；按缺失键读取必须得到 None。
    missing = next(row["id"] for row in raw if not row.get("region"))
    assert (content.locations.get(missing) or {}).get("region") is None
    assert (content.locations.get(missing) or {}).get("region_name") is None


def test_player_state_exposes_region_fields(tmp_path):
    session = WorldSession(
        ROOT,
        run_id="region-view-test",
        seed=42,
        npc_limit=0,
        run_dir=tmp_path / "run",
        content_root=XKX_CONTENT,
        world_config=WORLD_CONFIG,
        spawn="/d/hangzhou/jiulou",
    )
    try:
        entered = session.action(
            ACCOUNT,
            "enter",
            {"display_name": "游侠儿", "archetype": "游侠"},
        )
        assert entered["ok"], entered
        state = session.player_state(ACCOUNT)
        player = state["player"]
        location = player["location"]
        meta = session.content.locations[location]
        assert player["region"] == meta.get("region")
        assert player["region_name"] == meta.get("region_name")

        assert state["location"]["region"] == meta.get("region")
        assert state["location"]["region_name"] == meta.get("region_name")
    finally:
        session.close()
