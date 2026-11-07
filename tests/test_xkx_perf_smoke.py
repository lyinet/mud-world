"""xkx 千房间性能工具的 smoke 测试（小规模、CI 可跑、不依赖大数据）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from bench_xkx.graphgen import SyntheticSpec, build_content, write_content_pack  # noqa: E402

from wsim.actions.common import path_between  # noqa: E402
from wsim.actions.registry import build_registry  # noqa: E402
from wsim.api.persistence import replay_into  # noqa: E402
from wsim.api.session import WorldSession  # noqa: E402
from wsim.core.models import Intent  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402


def test_synthetic_graph_is_connected_and_symmetric() -> None:
    spec = SyntheticSpec(rooms=30, npcs=4, shops=2)
    content = build_content(spec)
    assert len(content.locations) == 30
    assert len(set(content.locations)) == 30
    for lid, loc in content.locations.items():
        for nxt in loc["exits"]:
            assert nxt in content.locations
            assert lid in content.locations[nxt]["exits"], f"{lid}->{nxt} 出口未互指"
    # 从任意起点 BFS 必须覆盖所有房间。
    start = sorted(content.locations)[0]
    seen = {start}
    queue = [start]
    while queue:
        here = queue.pop()
        for nxt in content.locations[here]["exits"]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    assert len(seen) == len(content.locations)


def test_state_hash_excludes_locations_but_full_hash_includes() -> None:
    content = build_content(SyntheticSpec(rooms=30, npcs=4, shops=2))
    empty = WorldState(locations=content.locations)
    bare = WorldState(locations={})
    assert empty.hash() == bare.hash()
    assert empty.full_hash() != bare.full_hash()


def test_replay_path_clone_and_write(tmp_path: Path) -> None:
    spec = SyntheticSpec(rooms=25, npcs=3, shops=1)
    content = build_content(spec)
    genesis = build_genesis(content, content.npcs, root=None)
    store = EventStore(tmp_path / "smoke.sqlite", check_same_thread=False)
    writer = Writer(WorldState(locations=content.locations), store, build_registry(content, spec.seed), spec.seed)
    writer.bootstrap(genesis.effects, tick=0, verb="genesis")
    live = writer.state

    replay_state, _ = replay_into(store, content.locations)
    assert replay_state.hash() == live.hash()

    loc_ids = sorted(content.locations)
    adjacent = content.locations[loc_ids[0]]["exits"][0]
    assert len(path_between(live, loc_ids[0], adjacent)) == 1

    clone = live.clone()
    before = live.hash()
    actor = sorted(live.entities)[0]
    here = live.entities[actor].location
    target = content.locations[here]["exits"][0]
    event = writer.submit(
        Intent(actor=actor, verb="move", args={"to": target}, tick=1, source="npc", reason="smoke")
    )
    assert event.outcome == "ok"
    assert writer.state.hash() != before
    assert clone.hash() == before


def test_player_state_view_smoke(tmp_path: Path) -> None:
    spec = SyntheticSpec(rooms=20, npcs=3, shops=1)
    content = build_content(spec)
    write_content_pack(tmp_path, content)
    session = WorldSession(
        root=tmp_path,
        run_id="smoke",
        seed=spec.seed,
        npc_limit=0,
        player_id="player:p0000",
        display_name="p0000",
        spawn=sorted(content.locations)[0],
        endow=True,
        post_quests=False,
        seed_accounts=["p0001"],
        run_dir=tmp_path / "runs" / "smoke",
    )
    out = session.player_state("p0000")
    assert out.get("ok"), out
    assert out["location"]["id"] in content.locations
    session.store.close()
