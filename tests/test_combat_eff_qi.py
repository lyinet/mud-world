"""`eff_qi`（有效气血）行为锁：0 血不死、负血才死、跨场伤势、昏迷与补刀。

对应 `specs/xkx-parity/eff-qi.spec.md` §1.3/§3/§4/§6。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.player import PlayerRestAction  # noqa: E402
from wsim.actions.system import SystemHealAction  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim import combat_rules  # noqa: E402
from wsim.sim import combat_rules as cr  # noqa: E402
from wsim.sim.combat_rules import simulate_fight  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}
HERO = "player:hero"
MON = "npc.boar"


def _hero(**over) -> dict:
    data = {
        "id": HERO,
        "name": "英雄",
        "role": "wanderer",
        "location": "inn",
        "max_hp": 100,
        "hp": 100,
        "attack": 60,
        "defense": 0,
        "attrs": {"str": 30, "dex": 20, "con": 20},
        "skills": {"dodge": 0.0, "parry": 0.0},
        "combat_exp": 10,
        "skill_levels": {"unarmed": 120, "dodge": 120, "parry": 120},
    }
    data.update(over)
    return data


def _monster(**over) -> dict:
    data = {
        "id": MON,
        "name": "野猪",
        "role": "monster",
        "location": "inn",
        "max_hp": 200,
        "hp": 200,
        "attack": 5,
        "defense": 0,
        "attrs": {"str": 20, "dex": 15, "con": 20},
        "skills": {"dodge": 0.0, "parry": 0.0},
        "combat_exp": 5,
        "skill_levels": {"unarmed": 10, "dodge": 0, "parry": 0},
    }
    data.update(over)
    return data


def _state(hero=None, monster=None) -> WorldState:
    state = WorldState(locations=LOCATIONS)
    apply_effects(
        state,
        [
            Effect("create_entity", hero or _hero()),
            Effect("create_entity", monster or _monster()),
        ],
    )
    return state


def _fight(state: WorldState, **kw):
    return simulate_fight(
        state.entities[HERO],
        state.entities[MON],
        PRNG(42, 7, "action:kill"),
        eff_qi=True,
        killing=True,
        **kw,
    )


# ---------------------------------------------------------------- 死亡门槛
def test_zero_hp_stays_alive_and_negative_qi_is_unconcious():
    state = _state(monster=_monster(max_hp=60, hp=60))
    result = _fight(state, max_rounds=40)
    mon = state.entities[MON]
    # 只有有效气血 < 0 才死；qh 到 0 或以下先昏迷。
    if result["eff_hp_raw"][MON] >= 0:
        assert MON not in result["dead"]
    if result["hp_raw"][MON] < 0 and result["eff_hp_raw"][MON] >= 0:
        assert MON in result["unconscious"]
    assert mon.status in ("alive", "unconcious", "dead")


def test_eff_qi_below_zero_is_death(monkeypatch):
    state = _state(monster=_monster(max_hp=30, hp=30, attrs={"str": 1, "dex": 1, "con": 1}))
    apply_effects(
        state,
        [
            Effect("hp_set", {"entity": MON, "value": 0}),
            Effect("entity_status", {"entity": MON, "status": "unconcious", "revive_tick": 9}),
        ],
    )
    # 破防通道成立时，补刀把 eff_qi 打到 <0 才 die()；qi 仍 >= -1。
    monkeypatch.setattr(cr.xf, "roll_wound", lambda **kw: True)
    result = _fight(state, max_rounds=1)
    assert result["eff_hp_raw"][MON] < 0
    assert MON in result["dead"]
    assert result["hp_raw"][MON] <= 0


def test_knockout_ends_fight_without_death(monkeypatch):
    """原版 `unconcious()` 会 `remove_all_enemy()`：本场立即结束，不再同回合补刀。"""
    monkeypatch.setattr(cr.xf, "roll_wound", lambda **kw: False)  # 只看 qi<0 的昏迷
    state = _state(monster=_monster(max_hp=60, hp=60))
    result = _fight(state, max_rounds=300)
    assert MON in result["unconscious"]
    assert MON not in result["dead"]
    # 昏迷即退场，不存在"同一场里继续快攻到死"。
    assert result["winner"] == "attacker"
    last = result["rounds"][-1]["actions"][-1]
    assert last["state"] != "alive"


def test_unconcious_entity_can_be_revived_by_tick():
    state = _state()
    apply_effects(
        state,
        [
            Effect("hp_set", {"entity": HERO, "value": 0}),
            Effect("entity_status", {"entity": HERO, "status": "unconcious", "revive_tick": 5}),
        ],
    )
    assert state.entities[HERO].status == "unconcious"
    action = SystemHealAction()
    early = action.apply(state, Intent("system", "system_heal", {}, 4, "system"), PRNG(1, 1, "x"))
    assert not [e for e in early if e.op == "entity_status"]
    wake = action.apply(state, Intent("system", "system_heal", {}, 5, "system"), PRNG(1, 2, "x"))
    st = [e for e in wake if e.op == "entity_status"]
    assert st and st[0].data["status"] == "alive"
    apply_effects(state, wake)
    assert state.entities[HERO].status == "alive"
    assert state.entities[HERO].hp == 0  # 原版 revive() 不补 qi


def test_hp_add_can_rescue_negative_hp_only_with_floor():
    state = _state()
    apply_effects(state, [Effect("hp_add", {"entity": HERO, "delta": -40})])
    assert state.entities[HERO].hp == 60
    # 关闭态（没有 floor）：钳 0；eff_qi 态：允许 -1。
    apply_effects(state, [Effect("hp_add", {"entity": HERO, "delta": -100})])
    assert state.entities[HERO].hp == 0
    apply_effects(state, [Effect("hp_add", {"entity": HERO, "delta": -100, "floor": -1})])
    assert state.entities[HERO].hp == -1
    apply_effects(state, [Effect("hp_add", {"entity": HERO, "delta": 10, "floor": -1})])
    assert state.entities[HERO].hp == 9


# ---------------------------------------------------------------- 跨场 / 序列化
def test_eff_qi_persists_across_fights_and_serializes_only_when_hurt():
    state = _state(monster=_monster(max_hp=400, hp=400))
    ent = state.entities[MON]
    assert "eff_hp" not in ent.to_dict()  # 未受伤：零新增字段
    first = _fight(state, max_rounds=20)
    apply_effects(
        state,
        [
            Effect("eff_hp_set", {"entity": MON, "value": first["eff_hp_raw"][MON]}),
            Effect("hp_add", {"entity": MON, "delta": first["hp_raw"][MON] - ent.hp}),
        ],
    )
    wounded = int(state.entities[MON].eff_hp)
    assert wounded < 400
    assert state.entities[MON].to_dict()["eff_hp"] == wounded
    # 第二场仍从旧伤势继续，不会被重置成 max_hp。
    second = _fight(state, max_rounds=1)
    assert second["eff_hp_raw"][MON] <= wounded


def test_old_entity_without_eff_field_is_treated_as_full():
    state = _state()
    assert state.entities[HERO].eff_hp == -2
    assert "eff_hp" not in state.entities[HERO].to_dict()


# ---------------------------------------------------------------- 昏迷 / 补刀
def test_quick_attack_needs_a_hit_to_finish_unconcious_target(monkeypatch):
    state = _state(monster=_monster(hp=0))
    apply_effects(
        state,
        [Effect("entity_status", {"entity": MON, "status": "unconcious", "revive_tick": 99})],
    )
    monkeypatch.setattr(cr.xf, "resolve_dodge", lambda **kw: True)
    result = _fight(state, max_rounds=1, initiative_gate=True)
    # 未命中：目标保持昏迷，不判死。
    assert MON not in result["dead"]

    monkeypatch.setattr(cr.xf, "resolve_dodge", lambda **kw: False)
    monkeypatch.setattr(cr.xf, "resolve_parry", lambda **kw: False)
    state2 = _state(monster=_monster(hp=0))
    apply_effects(
        state2,
        [Effect("entity_status", {"entity": MON, "status": "unconcious", "revive_tick": 99})],
    )
    result2 = _fight(state2, max_rounds=1, initiative_gate=True)
    # 命中后 qi 重新 <0，下一个心跳 die()。
    assert MON in result2["dead"]


def test_unconcious_clears_enemy_tables_but_not_contrib():
    state = _state()
    apply_effects(
        state,
        [
            Effect(
                "set_field",
                {"entity": MON, "field": "combat_enemies", "value": [HERO]},
            ),
            Effect("set_field", {"entity": MON, "field": "combat_target", "value": HERO}),
            Effect("set_field", {"entity": MON, "field": "combat_contrib", "value": {HERO: 7}}),
            Effect("hp_set", {"entity": MON, "value": 0}),
            Effect(
                "entity_status",
                {"entity": MON, "status": "unconcious", "revive_tick": 9},
            ),
        ],
    )
    ent = state.entities[MON]
    assert ent.combat_enemies == []
    assert ent.combat_target == ""
    assert ent.combat_contrib == {HERO: 7}
    assert ent.revive_tick == 9
    assert ent.to_dict()["revive_tick"] == 9


def test_dead_remove_all_enemy_flag_drives_bidirectional_clear():
    """原版 `char.lpc:66` 的 `remove_all_enemy()` 只在 eff_qi 战斗事件里显式带旗标：
    关闭态（无旗标）保持旧口径零副作用，保证逐字节一致。"""
    def _setup(flag: bool):
        state = _state()
        effects = [
            Effect("set_field", {"entity": MON, "field": "combat_enemies", "value": [HERO]}),
            Effect("set_field", {"entity": MON, "field": "combat_target", "value": HERO}),
            Effect("set_field", {"entity": HERO, "field": "combat_enemies", "value": [MON]}),
            Effect("hp_set", {"entity": MON, "value": 0}),
        ]
        data = {"entity": MON, "status": "dead", "tick": 0}
        if flag:
            data["remove_all_enemy"] = True
        effects.append(Effect("entity_status", data))
        apply_effects(state, effects)
        return state

    off = _setup(False)
    assert off.entities[MON].status == "dead"
    assert off.entities[MON].combat_target == HERO
    assert off.entities[HERO].combat_enemies == [MON]

    on = _setup(True)
    assert on.entities[MON].combat_target == ""
    assert on.entities[MON].combat_enemies == []
    assert on.entities[HERO].combat_enemies == []


# ---------------------------------------------------------------- 停战 / 睡眠
def test_spar_stop_reads_qi_not_eff_qi():
    state = _state()
    apply_effects(state, [Effect("eff_hp_set", {"entity": MON, "value": 10})])
    result = simulate_fight(
        state.entities[HERO],
        state.entities[MON],
        PRNG(42, 7, "action:fight"),
        eff_qi=True,
        killing=False,
        spar=True,
        max_rounds=200,
    )
    assert result["stopped"] is True
    # 停战门槛是 qi*2 <= max_qi，与 eff_qi 无关。
    assert result["hp_raw"][MON] * 2 <= 200
    assert result["eff_hp_raw"][MON] == 10


def test_sleep_caps_at_eff_qi_not_max():
    state = _state()
    apply_effects(
        state,
        [Effect("eff_hp_set", {"entity": HERO, "value": 30}), Effect("hp_set", {"entity": HERO, "value": 5})],
    )
    effects = PlayerRestAction().apply(
        state, Intent(HERO, "sleep", {"hours": 8}, 9, "player"), PRNG(1, 1, "p")
    )
    apply_effects(state, effects)
    assert state.entities[HERO].hp == 30
    assert state.entities[HERO].max_hp == 100


# ---------------------------------------------------------------- 骰序锁
class _Seq:
    """按给定序列返回 below() 的值，并记录每次 n。"""

    def __init__(self, values):
        self.values = list(values)
        self.ns: list[int] = []

    def below(self, n: int) -> int:
        self.ns.append(int(n))
        if not self.values:
            raise AssertionError(f"多取了骰: n={n}")
        return self.values.pop(0)


def test_wound_dice_sequences_match_combatd_560():
    xf = cr.xf
    # 未破门：只取一次 random(damage)。
    s = _Seq([5])
    assert xf.roll_wound(damage=10, armor=10, killing=True, has_weapon=False, prng=s) is False
    assert s.ns == [10]
    # 空手击杀：random(4) 命中。
    s = _Seq([50, 0])
    assert xf.roll_wound(damage=100, armor=0, killing=True, has_weapon=False, prng=s) is True
    assert s.ns == [100, 4]
    # 空手击杀：random(4) 未中再取 random(7)。
    s = _Seq([50, 1, 0])
    assert xf.roll_wound(damage=100, armor=0, killing=True, has_weapon=False, prng=s) is True
    assert s.ns == [100, 4, 7]
    # 空手非击杀：只有 random(7)。
    s = _Seq([50, 0])
    assert xf.roll_wound(damage=100, armor=0, killing=False, has_weapon=False, prng=s) is True
    assert s.ns == [100, 7]
    # 兵器击杀：random(2) 未中再取 random(4)。
    s = _Seq([50, 1, 0])
    assert xf.roll_wound(damage=100, armor=0, killing=True, has_weapon=True, prng=s) is True
    assert s.ns == [100, 2, 4]
    # 兵器非击杀：只有 random(4)。
    s = _Seq([50, 0])
    assert xf.roll_wound(damage=100, armor=0, killing=False, has_weapon=True, prng=s) is True
    assert s.ns == [100, 4]


def test_engine_wound_gate_only_uses_apply_armor(monkeypatch):
    captured = []

    def fake_roll_wound(**kw):
        captured.append(kw)
        return False

    monkeypatch.setattr(cr.xf, "roll_wound", fake_roll_wound)
    state = _state(monster=_monster(attrs={"str": 20, "dex": 15, "con": 20}, temp_apply={"apply/armor": 7}))
    # 基础 defense 再大也不该进入破防门。
    state.entities[MON].defense = 9999
    _fight(state, max_rounds=1)
    assert captured
    assert captured[0]["armor"] == 7
