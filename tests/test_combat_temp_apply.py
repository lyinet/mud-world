"""`set_temp("apply/...")` 接入：创世落地、串进战斗、未知项只诊断。

源码依据（`combatd.lpc`）：
- 197-210：`apply/attack` / `apply/defense` 加进 `skill_power` 的 level，再 800 封顶；
- 436-440：持兵器取 `apply/damage`，空手取 `apply/unarmed_damage`；
- 451-456 / 560-565：`apply/armor` 是护甲减伤值，也用于伤势判定。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.api.xkx_content import load_xkx_content  # noqa: E402
from wsim.sim import combat_rules as cr  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import Entity, WorldState  # noqa: E402


def _content_state():
    content = load_xkx_content(ROOT / "content" / "xkx")
    state = WorldState(locations=content.locations)
    result = build_genesis(content, content.npcs, root=ROOT, state=state)
    apply_effects(state, result.effects)
    return state, result


def _npc(**kw) -> Entity:
    base = dict(
        id="npc.target",
        name="木桩",
        role="monster",
        location="/d/hangzhou/street",
        max_hp=10000,
        hp=10000,
        attrs={"str": 10, "dex": 10},
    )
    base.update(kw)
    return Entity(**base)


def test_xkx_genesis_lands_known_temp_apply():
    state, _ = _content_state()
    tiger = state.entities["/d/hangzhou/npc/laohu"]
    assert tiger.temp_apply == {
        "apply/attack": 20000,
        "apply/defense": 30000,
        "apply/armor": 1000,
    }
    assert state.entities["/d/hangzhou/npc/xiaoer2"].temp_apply == {}


def test_unknown_temp_apply_is_diagnostic_only():
    state, result = _content_state()
    unknown = [m for m in result.mismatches if m.startswith("unknown_temp_apply:")]
    assert len(unknown) == 5
    assert any(m.startswith("unknown_temp_apply:/d/shiliang/npc/fish:armor:") for m in unknown)
    assert any(m.startswith("unknown_temp_apply:/d/shiliang/npc/atu:qingshu:") for m in unknown)
    # known=false 的项就算带数字也不许进实体；同一条 NPC 的 known=true 项照常保留。
    fish = state.entities["/d/shiliang/npc/fish"]
    assert "armor" not in fish.temp_apply
    assert fish.temp_apply == {"apply/attack": 45, "apply/damage": 60}
    assert "qingshu" not in state.entities["/d/shiliang/npc/atu"].temp_apply


def test_temp_apply_is_serialized_only_when_present():
    assert "temp_apply" not in Entity(id="npc.legacy", name="旧村民", role="farmer", location="inn").to_dict()
    ent = _npc(temp_apply={"apply/defense": 30000, "apply/attack": 20000})
    assert ent.to_dict()["temp_apply"] == {"apply/attack": 20000, "apply/defense": 30000}


def test_apply_attack_and_defense_enter_skill_power_with_800_cap():
    plain = _npc(skill_levels={"unarmed": 40, "dodge": 10}, combat_exp=250)
    boosted = _npc(
        skill_levels={"unarmed": 40, "dodge": 10},
        combat_exp=250,
        temp_apply={"apply/attack": 20000, "apply/defense": 30000},
    )
    capped = _npc(
        skill_levels={"unarmed": 40, "dodge": 10},
        combat_exp=250,
        temp_apply={"apply/attack": 800, "apply/defense": 800},
    )
    ids: set[str] = set()
    p, b, c = cr._Actor(plain, ids), cr._Actor(boosted, ids), cr._Actor(capped, ids)
    assert cr._attack_power(b, "unarmed") > cr._attack_power(p, "unarmed")
    assert cr._defense_power(b, "dodge") > cr._defense_power(p, "dodge")
    # level = 技能 + apply，再封顶 800：20000 与 800 结果相同。
    assert cr._attack_power(b, "unarmed") == cr._attack_power(c, "unarmed")
    assert cr._defense_power(b, "dodge") == cr._defense_power(c, "dodge")


def test_apply_armor_absorbs_unarmed_damage():
    # 用基础伤害（`Entity.attack`，等价于空手/兵器的原始伤害）观察护甲；
    # 膂力加成按源码在护甲之后加，不会被护甲吃掉。
    attacker = _npc(
        id="player:x", skill_levels={"unarmed": 40}, combat_exp=250,
        attrs={"str": 20, "dex": 18}, attack=100,
    )
    bare = _npc()
    armored = _npc(temp_apply={"apply/armor": 1000})
    a = cr.simulate_fight(attacker, bare, random.Random(7), max_rounds=1, player_ids={"player:x"})
    b = cr.simulate_fight(attacker, armored, random.Random(7), max_rounds=1, player_ids={"player:x"})
    a_hit = a["rounds"][0]["actions"][0]
    b_hit = b["rounds"][0]["actions"][0]
    assert a_hit["damage"] > b_hit["damage"] > 0
    assert b_hit["armor_absorbed"] > 0
    # 护甲扣完原始伤害后，最终伤害等于膂力加成。
    assert b_hit["damage"] == b_hit["strength_bonus"]


def test_apply_damage_is_ignored_when_unarmed():
    """源码空手取 apply/unarmed_damage；内容 schema 没有该键，apply/damage 不得生效。"""
    attacker = _npc(id="player:x", skill_levels={"unarmed": 40}, combat_exp=250, attrs={"str": 20, "dex": 18})
    with_damage = _npc(
        id="player:x",
        skill_levels={"unarmed": 40},
        combat_exp=250,
        attrs={"str": 20, "dex": 18},
        temp_apply={"apply/damage": 500},
    )
    a = cr.simulate_fight(attacker, _npc(), random.Random(7), max_rounds=1, player_ids={"player:x"})
    b = cr.simulate_fight(with_damage, _npc(), random.Random(7), max_rounds=1, player_ids={"player:x"})
    assert a["rounds"][0]["actions"][0]["damage"] == b["rounds"][0]["actions"][0]["damage"]
