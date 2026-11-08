"""P2 战斗属性与四类 Effect 原语。

覆盖：
- `Entity` 新字段只在内容显式声明后序列化，旧实体字节不变；
- 伤害（hp_add/hp_set）、死亡（entity_status）、复活（revive）、掉落（loot_create）
  都是纯 Effect，必须经 `apply_effects` 投影；
- 死亡必须先扣血（不允许绕过 hp_add 直接置死）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.core.models import Effect, EffectError  # noqa: E402
from wsim.world.effects import EFFECT_OPS, apply_effects  # noqa: E402
from wsim.world.state import Entity, WorldState  # noqa: E402


def _state() -> WorldState:
    return WorldState(locations={"inn": {"id": "inn", "name": "客栈", "exits": []}})


def _hero(max_hp: int = 100) -> dict:
    return {
        "id": "npc.hero",
        "name": "英雄",
        "role": "wanderer",
        "location": "inn",
        "max_hp": max_hp,
        "hp": max_hp,
        "attack": 20,
        "defense": 5,
        "attrs": {"str": 30, "dex": 20},
        "combat_exp": 10,
        "skill_levels": {"unarmed": 60},
        "skill_xp": {"unarmed": 0},
    }


def test_four_combat_ops_are_registered():
    for op in ("hp_add", "hp_set", "entity_status", "revive", "loot_create"):
        assert op in EFFECT_OPS


def test_legacy_entity_serialization_adds_no_new_keys():
    ent = Entity(id="npc.old", name="旧人", role="wanderer", location="inn")
    data = ent.to_dict()
    assert "hp" not in data and "max_hp" not in data
    assert "attrs" not in data and "combat_exp" not in data
    assert "skill_levels" not in data and "skill_xp" not in data
    assert "death_tick" not in data and "death_location" not in data


def test_combat_entity_round_trips_combat_fields():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    ent = state.entities["npc.hero"]
    assert (ent.hp, ent.max_hp, ent.attack, ent.defense) == (100, 100, 20, 5)
    assert ent.attrs == {"str": 30, "dex": 20}
    assert ent.skill_levels == {"unarmed": 60}
    data = ent.to_dict()
    assert data["hp"] == 100 and data["max_hp"] == 100
    assert data["attrs"] == {"dex": 20, "str": 30}
    assert data["skill_levels"] == {"unarmed": 60}


def test_damage_clamps_and_death_requires_zero_hp():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    apply_effects(state, [Effect("hp_add", {"entity": "npc.hero", "delta": -30})])
    assert state.entities["npc.hero"].hp == 70
    apply_effects(state, [Effect("hp_add", {"entity": "npc.hero", "delta": -999})])
    assert state.entities["npc.hero"].hp == 0

    with pytest.raises(EffectError):
        apply_effects(_state(), [
            Effect("create_entity", _hero()),
            Effect("entity_status", {"entity": "npc.hero", "status": "dead", "tick": 1}),
        ])

    apply_effects(state, [
        Effect("entity_status", {"entity": "npc.hero", "status": "dead", "tick": 3, "location": "inn"})
    ])
    ent = state.entities["npc.hero"]
    assert ent.status == "dead" and ent.death_tick == 3 and ent.death_location == "inn"


def test_revive_restores_hp_and_clears_death_fields():
    state = _state()
    apply_effects(state, [
        Effect("create_entity", _hero()),
        Effect("hp_add", {"entity": "npc.hero", "delta": -100}),
        Effect("entity_status", {"entity": "npc.hero", "status": "dead", "tick": 9, "location": "inn"}),
    ])
    apply_effects(state, [Effect("revive", {"entity": "npc.hero", "hp": 50})])
    ent = state.entities["npc.hero"]
    assert (ent.status, ent.hp) == ("alive", 50)
    assert ent.death_tick == 0 and ent.death_location == ""
    assert "death_tick" not in ent.to_dict()


def test_loot_create_goes_to_ground_and_loot_take_moves_it_to_inventory():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    apply_effects(state, [Effect("loot_create", {"entity": "npc.hero", "item": "item.hide", "qty": 2})])
    # 掉落在房间地面，不直接进背包。
    assert state.ground["inn"]["item.hide"] == 2
    assert "item.hide" not in state.entities["npc.hero"].inventory
    assert state.items_created["item.hide"] == 2

    apply_effects(
        state,
        [Effect("loot_take", {"entity": "npc.hero", "location": "inn", "item": "item.hide", "qty": 2})],
    )
    assert state.entities["npc.hero"].inventory["item.hide"] == 2
    assert "inn" not in state.ground
    # 取物不是造物：items_created 不重复计。
    assert state.items_created["item.hide"] == 2


def test_skill_xp_levels_up_and_keeps_remainder():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    apply_effects(state, [Effect("skill_xp_add", {"entity": "npc.hero", "skill": "unarmed", "delta": 105})])
    ent = state.entities["npc.hero"]
    assert ent.skill_levels["unarmed"] == 61
    assert ent.skill_xp["unarmed"] == 5


def _hp_add(entity: str, delta: int) -> Effect:
    return Effect("hp_add", {"entity": entity, "delta": delta, "reason": "fight"})


def test_hp_add_overkill_is_clamped_into_the_event_delta():
    """单次伤害超过剩余血量：事件增量必须等于状态实际变化。"""
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero(max_hp=100))])
    apply_effects(state, [_hp_add("npc.hero", -30)])
    assert state.entities["npc.hero"].hp == 70

    over = _hp_add("npc.hero", -999)
    apply_effects(state, [over])
    assert state.entities["npc.hero"].hp == 0
    assert over.data["delta"] == -70  # 不是 -999，也不是 0


def test_hp_add_multi_hit_overflow_accumulates_to_state_delta():
    """一次动作里的多段伤害：逐段增量之和必须等于状态总变化。"""
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero(max_hp=40))])
    hits = [_hp_add("npc.hero", -30) for _ in range(3)]
    apply_effects(state, hits)
    assert [h.data["delta"] for h in hits] == [-30, -10, 0]
    assert sum(h.data["delta"] for h in hits) == -40 == state.entities["npc.hero"].hp - 40


def test_hp_add_on_zero_hp_records_zero_delta():
    """hp 已为 0 时再受击：不允许记成负数增量。"""
    state = _state()
    apply_effects(state, [
        Effect("create_entity", _hero(max_hp=10)),
        _hp_add("npc.hero", -50),
    ])
    assert state.entities["npc.hero"].hp == 0
    again = _hp_add("npc.hero", -7)
    apply_effects(state, [again])
    assert again.data["delta"] == 0
    assert state.entities["npc.hero"].hp == 0


# ---- 地面来源分桶（loot 只搜刮击杀掉落）----


def _kill_drop(item: str, qty: int, entity: str = "npc.dog", tick: int = 7):
    return Effect(
        "loot_create",
        {
            "location": "inn",
            "item": item,
            "qty": qty,
            "reason": "death",
            "source": "kill",
            "source_entity": entity,
            "source_tick": tick,
        },
    )


def test_loot_create_with_kill_source_buckets_and_merges_same_kill():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    apply_effects(state, [_kill_drop("item.hide", 1), _kill_drop("item.tusk", 2)])
    assert state.ground["inn"] == {"item.hide": 1, "item.tusk": 2}
    entries = state.ground_sources["inn"]
    assert len(entries) == 1
    assert entries[0]["key"] == "kill:7:npc.dog"
    assert entries[0]["items"] == {"item.hide": 1, "item.tusk": 2}


def test_loot_take_with_source_only_consumes_that_bucket():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    # 地面既有 5 张皮（无来源），击杀再掉 1 张（有来源）。
    apply_effects(state, [Effect("loot_create", {"location": "inn", "item": "item.hide", "qty": 5})])
    apply_effects(state, [_kill_drop("item.hide", 1)])
    apply_effects(
        state,
        [
            Effect(
                "loot_take",
                {
                    "entity": "npc.hero",
                    "location": "inn",
                    "item": "item.hide",
                    "qty": 1,
                    "source": "kill:7:npc.dog",
                },
            )
        ],
    )
    assert state.ground["inn"]["item.hide"] == 5
    # 最近一次击杀被搜空后保留墓碑，避免再次 loot 退回去搜上一具尸体。
    entries = state.ground_sources["inn"]
    assert len(entries) == 1 and entries[0]["items"] == {}
    assert state.entities["npc.hero"].inventory["item.hide"] == 1


def test_loot_take_without_source_consumes_untracked_first():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    apply_effects(state, [Effect("loot_create", {"location": "inn", "item": "item.hide", "qty": 5})])
    apply_effects(state, [_kill_drop("item.hide", 2)])
    apply_effects(
        state,
        [Effect("loot_take", {"entity": "npc.hero", "location": "inn", "item": "item.hide", "qty": 3})],
    )
    # 3 件先吃未归因的 5 件，击杀桶原样保留 2 件。
    assert state.ground["inn"]["item.hide"] == 4
    assert state.ground_sources["inn"][0]["items"] == {"item.hide": 2}


def test_legacy_loot_create_has_no_sources_and_keeps_old_hash_shape():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    apply_effects(state, [Effect("loot_create", {"location": "inn", "item": "item.hide", "qty": 2})])
    assert state.ground_sources == {}
    assert "ground_sources" not in state.to_dict()
    assert state.to_dict()["ground"] == {"inn": {"item.hide": 2}}


def test_loot_take_with_unknown_source_rolls_back():
    state = _state()
    apply_effects(state, [Effect("create_entity", _hero())])
    apply_effects(state, [_kill_drop("item.hide", 1)])
    with pytest.raises(EffectError):
        apply_effects(
            state,
            [
                Effect(
                    "loot_take",
                    {
                        "entity": "npc.hero",
                        "location": "inn",
                        "item": "item.hide",
                        "qty": 1,
                        "source": "kill:999:npc.none",
                    },
                )
            ],
        )
    assert state.ground["inn"]["item.hide"] == 1
    assert "item.hide" not in state.entities["npc.hero"].inventory

