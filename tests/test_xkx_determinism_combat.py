"""确定性红线（新战斗世界）：新基线 + 同 seed 双跑 + 事件日志回放等价。

场景：创世 2 实体 → system_spawn 刷 1 只 → 两场 fight（各 1 条事件）。
基线值在 P2 首次落地后采样；后续任何战斗公式、取数顺序、Effect 顺序的变化
都会让哈希漂移，必须显式迁移基线而不是静默改值。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.combat import FightAction, SystemSpawnAction  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.sim.spawn import normalize_spawns  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}
SPAWNS = [
    {
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
]
GENESIS = [
    {
        "id": "player:hero",
        "name": "英雄",
        "role": "wanderer",
        "location": "inn",
        "max_hp": 100,
        "hp": 100,
        "attack": 20,
        "defense": 5,
        "attrs": {"str": 30, "dex": 20},
        "skills": {"dodge": 0.4},
        "combat_exp": 10,
        "skill_levels": {"unarmed": 60},
    },
    {
        "id": "npc.boar",
        "name": "野猪",
        "role": "monster",
        "location": "inn",
        "max_hp": 40,
        "hp": 40,
        "attack": 10,
        "defense": 2,
        "attrs": {"str": 20, "dex": 15},
        "skills": {"dodge": 0.2},
        "combat_exp": 5,
        "skill_levels": {"unarmed": 40},
    },
]

BASELINE = {
    "events_total": 4,  # genesis + system_spawn + fight + fight
    # 切换到 tools/xkx_formula 后重新采样（2026-11-07）：
    # 旧值 dc0aa76e… / 7968aebf… 属于引擎自维护公式，已废弃。
    # 再次重采（2026-11-07，掉落改落地面）：loot_create 不再直塞攻击者背包，
    # 状态里出现 ground（item.boar_hide / item.tusk），旧值 3eae4e85… 仅作漂移记录。
    "state_final": "92dd560641e3301218671b6da19462fd4b63fd1e1a203c323d8fffda872d2ed3",
    # event 哈希在 hp_add 增量钳制修复后重采样：状态未变（仍是 3eae4e85…），
    # 只有事件里的 delta 从“超额值”改成实际变化（如 -41 → -40）。
    # 掉落改地面后，loot_create 的 data 新增 location 并去掉 entity，事件哈希同步重采。
    # 招式接入后重采（2026-11-07）：state 哈希未变（91097d90…），说明无 map_skills
    # 的实体不选招、不新增取数，数值投影逐字节等价；event 哈希变化只来自
    # rounds[].actions[] 新增的 action_skill/selection/move/move_text/move_index/
    # force_hit/martial_hit/force_reflected 字段。
    # 移除 20 回合硬上限后重采（2026-11-07）：state 哈希未变（仍是 91097d90…，
    # 两场都在 3 回合内结束），event 哈希变化只来自 combat_log 的
    # `max_rounds` 由 20 变为 null（默认不封顶）。
    # 击杀来源分桶后重采（2026-11-09）：state 新增 ground_sources（击杀来源条目），
    # 事件里的 loot_create 新增 source/source_entity/source_tick；旧世界哈希不受影响
    # （见 test_xkx_determinism_legacy）。
    "event_final": "97d261031d95e40dce14f76f2a70e84ce677bc833bee48987017dca531937d3e",
}


def _run(seed: int = 42):
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    registry = ActionRegistry()
    registry.register(FightAction())
    registry.register(SystemSpawnAction(normalize_spawns(SPAWNS)))
    writer = Writer(state, store, registry, seed)
    writer.bootstrap([Effect("create_entity", e) for e in GENESIS], tick=0, verb="genesis")
    writer.submit(Intent("system", "system_spawn", {"day": 0}, 0, "system"))
    writer.submit(
        Intent("player:hero", "fight", {"target": "npc.boar", "loot": {"item.boar_hide": 1}}, 0, "player")
    )
    writer.submit(
        Intent("player:hero", "fight", {"target": "npc.spawn_boar", "loot": {"item.tusk": 1}}, 0, "player")
    )
    return writer, store


def test_new_combat_world_matches_baseline():
    writer, store = _run()
    events = store.read_all()
    assert len(events) == BASELINE["events_total"]
    assert [e.verb for e in events] == ["genesis", "system_spawn", "fight", "fight"]
    assert state_hash(writer.state) == BASELINE["state_final"]
    assert store.event_hash() == BASELINE["event_final"]
    # 每场战斗只写 1 条事件：两场战斗只对应 2 条 fight 事件。
    assert len([e for e in events if e.verb == "fight"]) == 2
    assert writer.state.entities["npc.boar"].status == "dead"
    assert writer.state.entities["npc.spawn_boar"].status == "dead"


def test_same_seed_double_run_is_identical_and_log_replay_equals_state():
    writer_a, store_a = _run(seed=42)
    writer_b, store_b = _run(seed=42)
    assert state_hash(writer_a.state) == state_hash(writer_b.state)
    assert store_a.event_hash() == store_b.event_hash()

    replay = WorldState(locations=LOCATIONS)
    for event in store_a.read_all():
        apply_effects(replay, [Effect.from_dict(e) for e in event.effects])
        replay.tick = event.tick
    assert state_hash(replay) == state_hash(writer_a.state)
