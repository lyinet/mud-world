"""`jing` / `eff_jing` 字段、序列化与初始化的行为锁（段 1）。

对应 `specs/xkx-parity/jing-eff-jing.spec.md` §6.1-§6.3：
- 关闭态（`combat.jing` 未开）不写字段、不序列化，旧世界哈希不变；
- 打开后 `laohu=212`、`wolfdog=25`（npcd 下限），内容 `vitals.max_jing` 优先；
- 旧档回填幂等，已有 `max_jing>0` 不覆盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.player import _player_vitals, player_genesis_effects  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.actions.system import SystemHealAction  # noqa: E402
from wsim.api.xkx_content import load_xkx_content  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.sim.vitals import backfill_jing, jing_for_npc  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import Entity, WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}
LAOHU = "/d/hangzhou/npc/laohu"
WOLFDOG = "/d/hangzhou/npc/wolfdog"
AN = "/d/hangzhou/honghua/an"


def _state() -> WorldState:
    return WorldState(locations=LOCATIONS)


def _xkx():
    return load_xkx_content(ROOT / "content" / "xkx")


# ---- 字段与序列化 -------------------------------------------------------

def test_default_entity_does_not_serialize_jing():
    data = Entity(id="npc.a", name="甲", role="npc", location="inn").to_dict()
    assert "jing" not in data
    assert "max_jing" not in data
    assert "eff_jing" not in data


def test_create_entity_without_jing_keys_stays_off():
    state = _state()
    apply_effects(state, [Effect("create_entity", {
        "id": "npc.a", "name": "甲", "role": "npc", "location": "inn",
        "max_hp": 50, "hp": 50,
    })])
    ent = state.entities["npc.a"]
    assert ent.max_jing == 0 and ent.jing == 0 and ent.eff_jing == -2
    assert "jing" not in ent.to_dict() and "max_jing" not in ent.to_dict()


def test_create_entity_with_jing_keys_fills_effective():
    state = _state()
    apply_effects(state, [Effect("create_entity", {
        "id": "npc.a", "name": "甲", "role": "npc", "location": "inn",
        "max_hp": 50, "hp": 50, "max_jing": 25, "jing": 25,
    })])
    ent = state.entities["npc.a"]
    assert (ent.jing, ent.max_jing, ent.eff_jing) == (25, 25, 25)
    data = ent.to_dict()
    assert data["jing"] == 25 and data["max_jing"] == 25
    # 未受伤（eff_jing == max_jing）不写 eff_jing，与 eff_qi 判据一致。
    assert "eff_jing" not in data


def test_hurt_jing_serializes_eff_and_shared_heal_counters():
    ent = Entity(id="npc.a", name="甲", role="npc", location="inn",
                 max_hp=50, hp=50, max_jing=25, jing=5, eff_jing=10,
                 heal_credit=7, heal_cd=3)
    data = ent.to_dict()
    assert data["jing"] == 5
    assert data["eff_jing"] == 10
    assert data["heal_credit"] == 7 and data["heal_cd"] == 3


# ---- 初始值与迁移 -------------------------------------------------------

def test_jing_for_npc_uses_content_value_or_half_max_hp():
    assert jing_for_npc(425, 0) == 212
    assert jing_for_npc(50, 0) == 25
    assert jing_for_npc(1000, 500) == 500


def test_xkx_genesis_off_has_no_jing_fields():
    content = _xkx()
    result = build_genesis(content, content.npcs, root=ROOT, jing=False)
    assert all("max_jing" not in e.data for e in result.effects if e.op == "create_entity")
    state = WorldState(locations=content.locations)
    apply_effects(state, result.effects)
    assert all(ent.max_jing == 0 for ent in state.entities.values())
    assert all("max_jing" not in ent.to_dict() for ent in state.entities.values())


def test_xkx_genesis_on_laohu_wolfdog_and_content_value():
    content = _xkx()
    result = build_genesis(content, content.npcs, root=ROOT, jing=True)
    state = WorldState(locations=content.locations)
    apply_effects(state, result.effects)
    laohu = state.entities[LAOHU]
    wolfdog = state.entities[WOLFDOG]
    an = state.entities[AN]
    assert (laohu.jing, laohu.max_jing, laohu.eff_jing) == (212, 212, 212)
    assert (wolfdog.jing, wolfdog.max_jing, wolfdog.eff_jing) == (25, 25, 25)
    # 内容 `vitals.max_jing=500` 优先于 max_qi/2=500（两者同为 500，取内容值）。
    assert an.max_jing == 500 and an.eff_jing == 500


def test_player_genesis_jing_gate():
    off = _player_vitals("游侠", None, jing=False)
    assert "max_jing" not in off and "jing" not in off
    on = _player_vitals("游侠", None, jing=True)
    assert (on["jing"], on["max_jing"], on["eff_jing"]) == (100, 100, 100)
    # 创世效果载荷同样受开关控制。
    off_effects = player_genesis_effects("player:x", "测试", "inn", jing=False)
    on_effects = player_genesis_effects("player:x", "测试", "inn", jing=True)
    off_entity = next(e for e in off_effects if e.op == "create_entity").data
    on_entity = next(e for e in on_effects if e.op == "create_entity").data
    assert "max_jing" not in off_entity
    assert on_entity["max_jing"] == 100


def test_backfill_jing_is_idempotent_and_skips_existing():
    state = _state()
    apply_effects(state, [
        Effect("create_entity", {"id": "player:hero", "name": "英雄", "role": "visitor",
                                 "location": "inn", "max_hp": 100, "hp": 100}),
        Effect("create_entity", {"id": LAOHU, "name": "老虎", "role": "beast",
                                 "location": "inn", "max_hp": 425, "hp": 425}),
        # 已经带 jing 的实体：不能被 max_hp/2 覆盖。
        Effect("create_entity", {"id": "npc.keep", "name": "保留", "role": "npc",
                                 "location": "inn", "max_hp": 1000, "hp": 1000,
                                 "max_jing": 700, "jing": 700}),
    ])
    assert backfill_jing(state) == 2
    assert (state.entities["player:hero"].jing, state.entities["player:hero"].max_jing) == (100, 100)
    assert (state.entities[LAOHU].jing, state.entities[LAOHU].max_jing) == (212, 212)
    assert state.entities["npc.keep"].max_jing == 700
    assert state.entities["npc.keep"].jing == 700
    # 第二次回填不再改动任何实体。
    assert backfill_jing(state) == 0


def test_respawn_restores_full_jing():
    state = _state()
    apply_effects(state, [Effect("create_entity", {
        "id": "npc.a", "name": "甲", "role": "npc", "location": "inn",
        "max_hp": 50, "hp": 0, "max_jing": 25, "jing": 0,
    })])
    apply_effects(state, [Effect("entity_status", {"entity": "npc.a", "status": "dead", "tick": 5})])
    apply_effects(state, [Effect("entity_respawn", {
        "entity": "npc.a", "max_hp": 50, "hp": 50, "max_jing": 25,
    })])
    ent = state.entities["npc.a"]
    assert (ent.jing, ent.max_jing, ent.eff_jing) == (25, 25, 25)


# ---- 伤害通道下限（J8/J9 的原语） ---------------------------------------

def test_jing_damage_and_wound_floor_minus_one():
    state = _state()
    apply_effects(state, [Effect("create_entity", {
        "id": "npc.a", "name": "甲", "role": "npc", "location": "inn",
        "max_hp": 425, "hp": 425, "max_jing": 212, "jing": 212,
    })])
    # receive_damage("jing", n)：jing 扣到 0 以下写 -1。
    apply_effects(state, [Effect("jing_add", {"entity": "npc.a", "delta": -9999, "floor": -1})])
    # receive_wound("jing", n)：eff_jing 扣到 0 以下写 -1。
    apply_effects(state, [Effect("eff_jing_add", {"entity": "npc.a", "delta": -9999})])
    ent = state.entities["npc.a"]
    assert ent.jing == -1
    assert ent.eff_jing == -1
    assert ent.hp == 425  # qi 完全不受影响
    # 无 floor 的旧语义仍钳 0（关闭态路径不会被这条事件走到）。
    apply_effects(state, [Effect("jing_add", {"entity": "npc.a", "delta": -5})])
    assert state.entities["npc.a"].jing == 0


def _jing_writer(*, max_jing: int, jing: int, eff_jing: int) -> Writer:
    state = _state()
    store = EventStore(":memory:")
    reg = ActionRegistry()
    reg.register(SystemHealAction(None, jing=True))
    writer = Writer(state, store, reg, seed=42)
    writer.bootstrap([Effect("create_entity", {
        "id": "npc.a", "name": "甲", "role": "npc", "location": "inn",
        "max_hp": 425, "hp": 425, "max_jing": max_jing, "jing": jing,
        "eff_jing": eff_jing,
    })], tick=0)
    return writer


def test_jing_wound_zero_then_minus_one_is_death_with_full_qi():
    """J8：eff_jing 0 → -1 → 心跳判 dead，qi 仍满。"""
    writer = _jing_writer(max_jing=212, jing=212, eff_jing=212)
    writer.bootstrap([Effect("eff_jing_set", {"entity": "npc.a", "value": 0})], tick=1)
    event = writer.bootstrap(
        [Effect("eff_jing_add", {"entity": "npc.a", "delta": -1})], tick=2
    )
    assert writer.state.entities["npc.a"].eff_jing == -1
    heal = writer.submit(Intent("npc.a", "system_heal", {}, 42, "system"))
    assert heal.outcome == "ok"
    ent = writer.state.entities["npc.a"]
    assert ent.status == "dead"
    assert ent.hp == 425  # qi 仍满
    status = [e for e in heal.effects if e["op"] == "entity_status"][0]
    assert status["data"]["cause"] == "jing"
    assert (status["data"]["tick"], event.tick) == (42, 2)


def test_jing_damage_zero_then_minus_one_is_unconcious_and_zeroes_both():
    """J9：jing 0 → -1 → 心跳判 unconcious，事件里 qi=0、jing=0。"""
    writer = _jing_writer(max_jing=100, jing=0, eff_jing=100)
    event = writer.bootstrap(
        [Effect("jing_add", {"entity": "npc.a", "delta": -1, "floor": -1})], tick=2
    )
    assert writer.state.entities["npc.a"].jing == -1
    heal = writer.submit(Intent("npc.a", "system_heal", {}, 42, "system"))
    assert heal.outcome == "ok"
    ent = writer.state.entities["npc.a"]
    assert ent.status == "unconcious"
    assert (ent.hp, ent.jing) == (0, 0)
    ops = [e["op"] for e in heal.effects]
    assert ops == ["hp_set", "jing_set", "entity_status"]
    assert heal.effects[-1]["data"]["cause"] == "jing"
    assert (heal.effects[-1]["data"]["revive_tick"], event.tick) == (108, 2)
