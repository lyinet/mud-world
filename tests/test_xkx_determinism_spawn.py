"""P2 刷怪：空转不写事件；有到期刷新才写 1 条 system_spawn。

阻断测试：
- `SystemSpawnAction` 在空 spawns / 全部存活 / 未到期时返回 []；
- headless 调度层只在 `spawn_due()` 为真时才提交意图，因此这些情况下
  events 里不会出现任何 `system_spawn`；
- 有 spawn 声明时，第一天写 1 条并真的创建实体。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from wsim.actions.combat import SystemSpawnAction  # noqa: E402
from wsim.core.content import load_content  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.sim.headless import RunConfig, run_headless  # noqa: E402
from wsim.sim.spawn import normalize_spawns, spawn_due, spawn_effects  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}


def _spec(**over) -> dict:
    spec = {
        "id": "npc.spawn_boar",
        "entity_id": "npc.spawn_boar",
        "name": "刷出的野猪",
        "role": "monster",
        "location": "inn",
        "respawn_ticks": 144,
        "max_hp": 60,
        "hp": 60,
        "attack": 10,
        "defense": 2,
        "attrs": {"str": 20, "dex": 15},
        "combat_exp": 5,
        "skill_levels": {"unarmed": 30},
    }
    spec.update(over)
    return spec


def _events(out_root: Path, run_id: str) -> list:
    store = EventStore(out_root / run_id / "events.sqlite")
    events = store.read_all()
    store.close()
    return events


def test_spawn_action_is_noop_without_specs_or_due():
    state = WorldState(locations=LOCATIONS)
    apply_effects(state, [Effect("create_entity", _spec())])
    action = SystemSpawnAction([])
    assert action.apply(state, Intent("system", "system_spawn", {}, 0, source="system"), None) == []
    # 实体还活着 → 未到期
    assert spawn_due(state, normalize_spawns([_spec()]), 0) is False
    assert spawn_effects(state, normalize_spawns([_spec()]), 0) == []


def test_spawn_revives_dead_entity_after_respawn_ticks():
    state = WorldState(locations=LOCATIONS)
    apply_effects(state, [Effect("create_entity", _spec())])
    apply_effects(state, [
        Effect("hp_add", {"entity": "npc.spawn_boar", "delta": -60}),
        Effect("entity_status", {"entity": "npc.spawn_boar", "status": "dead",
                                  "tick": 10, "location": "inn"}),
    ])
    spawns = normalize_spawns([_spec()])
    assert spawn_due(state, spawns, 10 + 143) is False
    assert spawn_due(state, spawns, 10 + 144) is True
    effects = spawn_effects(state, spawns, 10 + 144)
    assert effects and effects[0].op == "revive"
    apply_effects(state, effects)
    ent = state.entities["npc.spawn_boar"]
    assert (ent.status, ent.hp) == ("alive", 60)


def test_headless_without_spawns_writes_no_spawn_event(tmp_path):
    events = _events(
        tmp_path,
        run_headless(ROOT, RunConfig(days=1, seed=42, npc_limit=2, out_root=tmp_path,
                                     run_id="spawn-empty", spawns=[]))["run_id"],
    )
    assert not [e for e in events if e.verb == "system_spawn"]
    assert [e for e in events if e.verb == "genesis"]


def test_headless_with_spawn_writes_one_event_and_creates_entity(tmp_path):
    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    location = next(iter(content.locations))
    summary = run_headless(
        ROOT,
        RunConfig(days=2, seed=42, npc_limit=2, out_root=tmp_path, run_id="spawn-on",
                  spawns=[_spec(location=location)]),
    )
    events = _events(tmp_path, summary["run_id"])
    spawn_events = [e for e in events if e.verb == "system_spawn"]
    # 第一天创建 1 次；第二天实体存活，不重复写事件。
    assert len(spawn_events) == 1
    assert spawn_events[0].tick == 0
    created = [e for e in spawn_events[0].effects if e["op"] == "create_entity"]
    assert created and created[0]["data"]["id"] == "npc.spawn_boar"
    assert created[0]["data"]["max_hp"] == 60
