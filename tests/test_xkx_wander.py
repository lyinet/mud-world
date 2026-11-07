"""P4b：xkx NPC 游走（默认关闭、复用 LOD + Writer）。"""
from __future__ import annotations

import json

import yaml

from conftest import ROOT

from wsim.api.xkx_content import load_xkx_content
from wsim.core.content import Content
from wsim.core.rng import PRNG
from wsim.sim.headless import RunConfig, run_headless
from wsim.sim.lod import LODConfig, LODScheduler, LODTier
from wsim.sim.scheduler import AmbientConfig
from wsim.sim.wander import WanderPolicy
from wsim.world.state import Entity, WorldState
from wsim.world.store import EventStore


def _tiny_world() -> tuple[Content, WorldState]:
    content = Content()
    content.locations = {
        "a": {"id": "a", "name": "甲", "exits": ["b"], "services": []},
        "b": {"id": "b", "name": "乙", "exits": ["a"], "services": []},
    }
    content.npcs = [
        {"id": "/npc/master", "is_master": True},
        {"id": "/npc/shop"},
        {"id": "/npc/quest"},
        {"id": "/npc/sleep"},
        {"id": "/npc/ok"},
    ]
    content.shop_by_npc = {"/npc/shop": {"id": "/npc/shop"}}
    content.quest_npc_refs = {"/npc/quest"}
    state = WorldState(locations=content.locations)
    for npc in ("/npc/master", "/npc/shop", "/npc/quest", "/npc/sleep", "/npc/ok"):
        state.entities[npc] = Entity(id=npc, name=npc, role="wanderer", location="a")
    state.entities["/npc/sleep"].status = "unconcious"
    return content, state


def test_npc_wander_is_off_by_default_and_only_xkx_config_enables_it():
    assert WanderPolicy().enabled is False
    assert RunConfig().npc_wander is False
    assert AmbientConfig(out_dir=ROOT / "runs" / "unused").npc_wander is False

    xkx = yaml.safe_load((ROOT / "config" / "worlds" / "xkx.yaml").read_text(encoding="utf-8"))
    legacy = yaml.safe_load((ROOT / "config" / "worlds" / "default.yaml").read_text(encoding="utf-8"))
    assert xkx["npc_wander"]["enabled"] is True
    assert "npc_wander" not in legacy


def test_world_session_reads_npc_wander_only_when_config_opens_it(tmp_path):
    from wsim.api.session import WorldSession

    assert WorldSession._load_npc_wander(None) is False
    empty = tmp_path / "empty.yaml"
    empty.write_text("id: legacy\n", encoding="utf-8")
    assert WorldSession._load_npc_wander(empty) is False
    assert WorldSession._load_npc_wander(ROOT / "config" / "worlds" / "xkx.yaml") is True


def test_wander_same_tick_wakes_are_stably_sorted_for_submission():
    """锁住同 tick 多个到期项的提交顺序：至少 (wake_tick, npc_id)。"""
    content, state = _tiny_world()
    ids = ["/npc/z", "/npc/m", "/npc/a"]
    for npc in ids:
        state.entities[npc] = Entity(id=npc, name=npc, role="wanderer", location="a")
    config = LODConfig(
        version="wander-test",
        tiers={name: LODTier(name, 1, 0.0, 1.0) for name in ("silent", "background", "active", "spotlight")},
    )
    scheduler = LODScheduler(
        npc_ids=ids,
        config=config,
        reasoner=object(),
        seed=20261014,
        state_provider=lambda npc: {"id": npc},
        world_view_provider=lambda tick: {"tick": tick, "state": state, "content": content},
        wander=WanderPolicy(enabled=True),
    )
    # 所有 NPC 的首个唤醒都落在 tick=1；堆内顺序不能成为唯一保证。
    wakes = scheduler.advance_to(1)
    assert [(w.tick, w.npc_id) for w in wakes] == sorted((w.tick, w.npc_id) for w in wakes)
    assert [w.npc_id for w in wakes] == sorted(ids)
    assert [i.actor for w in wakes for i in w.intents] == sorted(ids)


def test_wander_excludes_master_shopkeeper_quest_and_unconcious_npcs():
    content, state = _tiny_world()
    policy = WanderPolicy(enabled=True)
    assert policy.intent_for(seed=1, tick=10, npc="/npc/ok", state=state, content=content) is not None
    for npc in ("/npc/master", "/npc/shop", "/npc/quest", "/npc/sleep"):
        assert policy.intent_for(seed=1, tick=10, npc=npc, state=state, content=content) is None


def test_wander_decision_uses_tick_and_npc_salt_not_seq():
    content, state = _tiny_world()
    policy = WanderPolicy(enabled=True)
    intent_a = policy.intent_for(seed=7, tick=42, npc="/npc/ok", state=state, content=content)
    intent_b = policy.intent_for(seed=7, tick=42, npc="/npc/ok", state=state, content=content)
    expected = PRNG(7, 42, "npc_wander:/npc/ok").choice(sorted(content.locations["a"]["exits"]))
    assert intent_a == intent_b
    assert intent_a.verb == "move"
    assert intent_a.args["to"] == expected


def test_xkx_wander_on_is_reproducible_and_first_day_matches_one_day_run(tmp_path):
    content = load_xkx_content(ROOT / "content" / "xkx")
    one = run_headless(
        ROOT,
        RunConfig(seed=20261014, days=1, out_root=tmp_path, run_id="wander-1d", npc_wander=True),
        content=content,
    )
    two_a = run_headless(
        ROOT,
        RunConfig(seed=20261014, days=2, out_root=tmp_path, run_id="wander-2d-a", npc_wander=True),
        content=content,
    )
    two_b = run_headless(
        ROOT,
        RunConfig(seed=20261014, days=2, out_root=tmp_path, run_id="wander-2d-b", npc_wander=True),
        content=content,
    )
    assert two_a["hashes"]["state_final"] == two_b["hashes"]["state_final"]
    assert two_a["hashes"]["event_final"] == two_b["hashes"]["event_final"]
    assert two_a["events"]["total"] == two_b["events"]["total"]

    day1 = json.loads((tmp_path / "wander-2d-a" / "daily.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert day1["state_hash"] == one["hashes"]["state_final"]

    store = EventStore(tmp_path / "wander-2d-a" / "events.sqlite")
    events = store.read_all()
    store.close()
    wander_moves = [e for e in events if e.verb == "move" and e.source == "npc"]
    assert wander_moves
    assert all(e.outcome == "ok" for e in wander_moves)
    assert all(not e.actor in content.shop_by_npc and not e.actor in content.quest_npc_refs for e in wander_moves)
    # 同 tick 的落地提交顺序实际落在 (wake_tick, npc_id) 的稳定序上。
    by_tick: dict[int, list[str]] = {}
    for event in wander_moves:
        by_tick.setdefault(event.tick, []).append(event.actor)
    assert all(actors == sorted(actors) for actors in by_tick.values())
