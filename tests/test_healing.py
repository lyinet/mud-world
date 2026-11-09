"""疗伤原语的行为锁（段 1，对应 specs/xkx-parity/healing.spec.md §6.3/§7.1）。

两条语义必须分开：
- `receive_heal`（damage.lpc:71-84）：只抬当前值，上限是 `eff_*`，**返回入参**；
- `receive_curing`（damage.lpc:86-103）：只抬 `eff_*`，上限是 `max_*`，**返回实际增量**。
"""
from __future__ import annotations

import random as _random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.system import SystemHealAction  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import Entity, WorldState  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}


def _state() -> WorldState:
    return WorldState(locations=LOCATIONS)


def _one(eff_hp: int | None = None, **fields):
    """建一个实体；`eff_hp` 走单独效果（create_entity 会强制 eff=max）。"""
    state = _state()
    base = {"id": "npc.a", "name": "甲", "role": "npc", "location": "inn"}
    base.update(fields)
    apply_effects(state, [Effect("create_entity", base)])
    if eff_hp is not None:
        apply_effects(state, [Effect("eff_hp_set", {
            "entity": "npc.a", "value": eff_hp,
        })])
    return state, state.entities["npc.a"]


def _apply_one(state, op: str, payload: dict) -> Effect:
    effect = Effect(op, payload)
    apply_effects(state, [effect])
    return effect


# ---- receive_heal / receive_curing -------------------------------------

def test_qi_heal_caps_at_eff_and_reports_input():
    """H1：`qi=min(qi+50, eff_qi=80)`，返回值是入参 50（不是 30）。"""
    state, ent = _one(max_hp=100, hp=50, eff_hp=80)
    e = _apply_one(state, "qi_heal", {"entity": "npc.a", "delta": 50})
    assert ent.hp == 80
    assert e.data["healed"] == 50


def test_qi_heal_does_not_raise_eff():
    """receive_heal 不动 eff_qi。"""
    state, ent = _one(max_hp=100, hp=10, eff_hp=40)
    _apply_one(state, "qi_heal", {"entity": "npc.a", "delta": 15})
    assert (ent.hp, ent.eff_hp) == (25, 40)


def test_qi_heal_on_dead_entity_goes_to_minus_one():
    """H2：`eff_qi=-1` 时写 `qi=-1`，复刻"疗伤救不回已死"。"""
    state, ent = _one(max_hp=100, hp=10, eff_hp=-1)
    _apply_one(state, "qi_heal", {"entity": "npc.a", "delta": 50})
    assert ent.hp == -1


def test_qi_cure_caps_at_max_and_reports_actual():
    """H3：`eff=min(80+50, max=100)=100`，返回实际增量 20。"""
    state, ent = _one(max_hp=100, hp=50, eff_hp=80)
    e = _apply_one(state, "qi_cure", {"entity": "npc.a", "delta": 50})
    assert ent.eff_hp == 100
    assert e.data["healed"] == 20


def test_qi_cure_without_truncation_reports_full_delta():
    """H4：`eff=30+50=80`，返回 50；qi 不变。"""
    state, ent = _one(max_hp=100, hp=10, eff_hp=30)
    e = _apply_one(state, "qi_cure", {"entity": "npc.a", "delta": 50})
    assert ent.eff_hp == 80
    assert ent.hp == 10
    assert e.data["healed"] == 50


def test_jing_heal_and_cure_mirror_qi():
    state, ent = _one(max_hp=100, hp=100, max_jing=100, jing=50, eff_jing=80)
    e1 = _apply_one(state, "jing_heal", {"entity": "npc.a", "delta": 50})
    assert ent.jing == 80 and e1.data["healed"] == 50
    e2 = _apply_one(state, "jing_cure", {"entity": "npc.a", "delta": 50})
    assert ent.eff_jing == 100 and e2.data["healed"] == 20


def test_heal_ops_require_combat_vitals():
    state, _ = _one(max_hp=0, hp=0)
    try:
        apply_effects(state, [Effect("qi_heal", {"entity": "npc.a", "delta": 1})])
    except Exception as exc:  # EffectError
        assert "战斗生命值" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当拒绝没有战斗生命值的实体")


# ---- neili / busy ------------------------------------------------------

def test_neili_add_clamps_to_range():
    state, ent = _one(max_hp=100, hp=100, neili=100, max_neili=200)
    _apply_one(state, "neili_add", {"entity": "npc.a", "delta": -150})
    assert ent.neili == 0
    _apply_one(state, "neili_add", {"entity": "npc.a", "delta": 500})
    assert ent.neili == 200


def test_neili_set_lands_value():
    state, ent = _one(max_hp=100, hp=100, neili=0, max_neili=200)
    _apply_one(state, "neili_set", {"entity": "npc.a", "value": 120})
    assert ent.neili == 120


def test_busy_until_is_not_serialized_when_idle():
    data = Entity(id="npc.a", name="甲", role="npc", location="inn").to_dict()
    assert "busy_until" not in data


def test_busy_set_only_extends():
    state, ent = _one(max_hp=100, hp=100)
    _apply_one(state, "busy_set", {"entity": "npc.a", "rounds": 2, "tick": 10})
    assert ent.busy_until == 12
    _apply_one(state, "busy_set", {"entity": "npc.a", "rounds": 1, "tick": 11})
    assert ent.busy_until == 12
    assert ent.to_dict()["busy_until"] == 12


# ---- heal_up 公式 ------------------------------------------------------

def _heal_up(state, healing: bool, hp: int = 10, eff: int = 80) -> dict:
    apply_effects(state, [Effect("heal_apply", {
        "entity": "npc.a", "hp": hp, "eff": eff,
        "heal_credit": 0, "heal_cd": 1,
    })])
    effects = SystemHealAction(None, jing=True, healing=healing).apply(
        state, Intent("npc.a", "system_heal", {}, 7, "system"), _random.Random(42)
    )
    return next(e for e in effects if e.op == "heal_apply").data


def test_heal_up_adds_max_neili_tenth_when_healing_on():
    """H14：`qi += con//3 + max_neili//10 = 6 + 50`。"""
    state, ent = _one(max_hp=100, hp=100, attrs={"con": 20},
                      neili=0, max_neili=500)
    heal = _heal_up(state, healing=True)
    assert heal["hp"] == 66


def test_heal_up_keeps_old_formula_when_healing_off():
    """H15：关闭态仍是 `con//3`，max_neili 不参与。"""
    state, ent = _one(max_hp=100, hp=100, attrs={"con": 20},
                      neili=0, max_neili=500)
    heal = _heal_up(state, healing=False)
    assert heal["hp"] == 16  # 10 + con//3(6) + 0


def test_heal_up_jing_is_not_boosted_by_neili():
    """jing 那一路原版用 max_jingli/10（未建模），不掺 max_neili。"""
    state, ent = _one(max_hp=100, hp=100, attrs={"con": 20},
                      max_jing=100, jing=10, eff_jing=80,
                      neili=0, max_neili=500)
    heal = _heal_up(state, healing=True, hp=100, eff=100)
    assert heal["jing"] == 16
