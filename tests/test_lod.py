"""调度层：四档 LOD、事件驱动、Reasoner 接口、确定性统计。"""
from __future__ import annotations

from collections import Counter

from conftest import ROOT
from wsim.core.content import load_content
from wsim.sim.lod import LODScheduler, MockReasoner, TIER_ORDER, load_lod_config


def _scheduler(content, seed=42, horizon=144):
    config = load_lod_config(ROOT / "config" / "lod.yaml")
    ids = [n["id"] for n in content.npcs]
    exits = {lid: list(loc["exits"]) for lid, loc in content.locations.items()}
    home = {n["id"]: n.get("home") or "market" for n in content.npcs}
    scheduler = LODScheduler(
        npc_ids=ids,
        config=config,
        reasoner=MockReasoner(seed=seed),
        seed=seed,
        state_provider=lambda npc: {"id": npc, "home": home.get(npc, "market"),
                                    "exits": exits.get(home.get(npc, "market"), []),
                                    "stamina": 80.0},
        world_view_provider=lambda tick: {"tick": tick},
    )
    return scheduler


def test_lod_config_has_four_tiers_and_configurable_weights():
    config = load_lod_config(ROOT / "config" / "lod.yaml")
    assert tuple(config.tiers) == TIER_ORDER
    assert config.tiers["spotlight"].wake_interval_ticks < config.tiers["silent"].wake_interval_ticks
    assert config.tiers["spotlight"].cost_weight > config.tiers["silent"].cost_weight


def test_tick_only_processes_due_events_not_all_npcs():
    content = load_content(ROOT, allow_fixture=False, use_content=True)
    scheduler = _scheduler(content)
    scheduler.advance_to(1000)  # 预热到稳定期
    before = scheduler.process_count
    wakes = scheduler.advance_to(1060)  # 之后 60 tick 的到期窗口
    delta = scheduler.process_count - before
    assert delta == len(wakes)
    # 60 tick 内只有 spotlight/刚跨期的少数 NPC 被唤醒，远小于 NPC 总数。
    assert delta <= len(content.npcs) // 2
    assert delta < len(content.npcs)
    by_tier = Counter(w.lod for w in wakes)
    assert sum(by_tier.values()) == len(wakes)


def test_lod_stats_include_tier_and_wake_count_for_every_npc():
    content = load_content(ROOT, allow_fixture=False, use_content=True)
    scheduler = _scheduler(content)
    scheduler.advance_to(72)
    stats = scheduler.stats()
    assert set(stats) == {n["id"] for n in content.npcs}
    for npc, row in stats.items():
        assert row["lod"] in TIER_ORDER
        assert isinstance(row["wakes"], int)
    assert scheduler.summary()["by_tier"]["spotlight"] >= 1


def test_lod_is_deterministic_without_global_random():
    content = load_content(ROOT, allow_fixture=False, use_content=True)
    a = _scheduler(content, seed=42)
    b = _scheduler(content, seed=42)
    wa = a.advance_to(144)
    wb = b.advance_to(144)
    assert [(w.tick, w.npc_id, w.lod, [i.to_dict() for i in w.intents]) for w in wa] == [
        (w.tick, w.npc_id, w.lod, [i.to_dict() for i in w.intents]) for w in wb
    ]
    assert a.stats() == b.stats()
