"""B：重生载荷字段集必须与 genesis 创世载荷完全相等。

这是防漂移的护栏：以后任何人往 `genesis.build_genesis()` 的 `create_entity`
载荷里加字段而忘了同步 `room_reset._baseline()`，这里会直接红。
"""
from __future__ import annotations

from conftest import ROOT

from wsim.api.xkx_content import load_xkx_content
from wsim.core.models import Effect
from wsim.sim.genesis import build_genesis
from wsim.sim.room_reset import RESET_PERIOD_TICKS, RoomResetPolicy
from wsim.world.effects import apply_effects
from wsim.world.state import WorldState

CLONE = "/d/hangzhou/npc/wolfdog@/d/hangzhou/guozhuang#2"
CANON = "/d/hangzhou/npc/xiaoer2"
GOUPI = "/d/hangzhou/npc/obj/goupi"


def _due_tick(policy: RoomResetPolicy, room: str) -> int:
    for tick in range(1, RESET_PERIOD_TICKS * 2):
        if policy.due(room, tick):
            return tick
    raise AssertionError("no due tick")


def _genesis_payloads() -> tuple[dict[str, dict], WorldState]:
    content = load_xkx_content(ROOT / "content" / "xkx")
    state = WorldState(locations=content.locations)
    genesis = build_genesis(content, content.npcs, root=ROOT, seed=20261014)
    apply_effects(state, genesis.effects)
    payloads = {e.data["id"]: dict(e.data) for e in genesis.effects if e.op == "create_entity"}
    return payloads, state


def _respawn_payload(state: WorldState, entity_id: str, death_tick: int = 77) -> tuple[dict, RoomResetPolicy, WorldState]:
    content = load_xkx_content(ROOT / "content" / "xkx")
    ent = state.entities[entity_id]
    ent.status = "dead"
    ent.hp = 0
    ent.death_tick = death_tick
    ent.death_location = ent.location
    policy = RoomResetPolicy(content=content, seed=20261014, enabled=True)
    room = ent.home or ent.location
    effects = policy.effects(state, _due_tick(policy, room))
    rows = [e.data for e in effects if e.op == "entity_respawn" and e.data["entity"] == entity_id]
    assert len(rows) == 1, f"{entity_id} 未重生: {[e.op for e in effects]}"
    return dict(rows[0]), policy, state


def _normalize_genesis(payload: dict) -> set[str]:
    # genesis 的随身物品走独立 item_create 事件，重生载荷直接带 inventory。
    return set(payload) | {"inventory"}


def _normalize_respawn(payload: dict) -> set[str]:
    data = dict(payload)
    data["id"] = data.pop("entity")
    data.pop("death_tick", None)
    data.pop("death_location", None)
    return set(data)


def _assert_same_fields(genesis_payload: dict, respawn_payload: dict) -> None:
    lhs = _normalize_genesis(genesis_payload)
    rhs = _normalize_respawn(respawn_payload)
    assert lhs == rhs, f"字段集不一致 仅创世有={sorted(lhs - rhs)} 仅重生有={sorted(rhs - lhs)}"


def test_respawn_field_set_equals_genesis_for_clone_with_drops():
    payloads, state = _genesis_payloads()
    respawn, _, _ = _respawn_payload(state, CLONE)
    _assert_same_fields(payloads[CLONE], respawn)
    assert respawn["drops"] == {GOUPI: 1}
    assert respawn["template"] == "/d/hangzhou/npc/wolfdog"
    assert respawn["instance"] == 2
    assert respawn["hp"] == respawn["max_hp"]


def test_respawn_field_set_equals_genesis_for_canonical_with_equipment():
    payloads, state = _genesis_payloads()
    respawn, _, _ = _respawn_payload(state, CANON)
    _assert_same_fields(payloads[CANON], respawn)
    assert respawn["equipment"] == payloads[CANON]["equipment"]
    assert respawn["equipped"] == payloads[CANON]["equipped"]
    assert respawn["equipped_apply"] == payloads[CANON]["equipped_apply"]
    assert respawn["name"] == payloads[CANON]["name"]
    assert respawn["personality"] == payloads[CANON]["personality"]
    assert "drops" not in respawn


def test_probe_field_breaks_equality():
    """负向护栏：谁往创世载荷加字段而不同步重生，字段集比较必须失败。"""
    payloads, state = _genesis_payloads()
    respawn, _, _ = _respawn_payload(state, CLONE)
    poisoned = {**payloads[CLONE], "__probe__": 1}
    try:
        _assert_same_fields(poisoned, respawn)
    except AssertionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("字段集护栏失效：新增字段没有让测试失败")


def test_respawn_rerolls_attrs_per_death_tick():
    """原版 destruct+new() 会重掷六维：不同死亡 tick 的重生载荷属性不同。"""
    _, state = _genesis_payloads()
    first, _, _ = _respawn_payload(state, CANON, 77)
    second, _, _ = _respawn_payload(state, CANON, 78)
    assert first["attrs"] != second["attrs"]
    assert _respawn_payload(state, CANON, 77)[0]["attrs"] == first["attrs"]


def test_respawn_applies_drops_and_creation_defaults():
    payloads, state = _genesis_payloads()
    respawn, _, state = _respawn_payload(state, CLONE)
    apply_effects(state, [Effect("entity_respawn", respawn)])
    dog = state.entities[CLONE]
    assert dog.status == "alive"
    assert dog.drops == {GOUPI: 1}
    assert dog.max_stamina == 100.0 and dog.skill_xp == {}
