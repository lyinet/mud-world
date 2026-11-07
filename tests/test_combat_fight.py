"""P2 `fight`：原子结算、整数战报、PRNG 取数顺序锁与回放等价。

关键断言：
- 一次 fight 只追加 1 条事件，事件里带 rounds[]；
- 同 seed + 同 seq + salt="action:fight" 双跑逐字节一致；
- 取数顺序（dodge→parry→weapon_damage→armor→str→exp→wound→killing→reward）写死，
  顺序一旦改变此测试立即失败；
- 所有数值公式委托 `tools/xkx_formula`，引擎侧不允许出现第二份实现；
- 从事件日志重放 Effect 得到的投影与实跑状态哈希一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.actions.combat import FightAction  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim.combat_rules import simulate_fight  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import Entity, WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {"inn": {"id": "inn", "name": "客栈", "exits": []}}


def _hero(**over) -> dict:
    data = {
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
    }
    data.update(over)
    return data


def _monster(**over) -> dict:
    data = {
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
    }
    data.update(over)
    return data


def _writer(hero: dict, monster: dict, seed: int = 42):
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    registry = ActionRegistry()
    registry.register(FightAction())
    writer = Writer(state, store, registry, seed)
    writer.bootstrap(
        [Effect("create_entity", hero), Effect("create_entity", monster)], tick=0, verb="genesis"
    )
    return writer, store


def _fight(writer, args: dict, tick: int = 0):
    return writer.submit(
        Intent(actor="player:hero", verb="fight", args=args, tick=tick, source="player")
    )


def _log(event) -> dict:
    return [e for e in event.effects if e["op"] == "combat_log"][0]["data"]


def test_fight_writes_one_event_with_rounds_and_kills_target():
    writer, store = _writer(_hero(), _monster())
    event = _fight(writer, {"target": "npc.boar", "loot": {"item.boar_hide": 1}})

    assert store.count() == 2  # genesis + fight
    assert event.verb == "fight" and event.outcome == "ok"
    ops = [e["op"] for e in event.effects]
    assert "hp_add" in ops and "entity_status" in ops and "loot_create" in ops
    assert "combat_log" in ops

    log = _log(event)
    assert log["winner"] == "attacker"
    assert log["rounds_used"] <= 20
    assert len(log["rounds"]) == log["rounds_used"]
    # 战报全部整数：不允许出现浮点尾差
    for rnd in log["rounds"]:
        for action in rnd["actions"]:
            assert isinstance(action["damage"], int)
            assert isinstance(action["hp_before"], int) and isinstance(action["hp_after"], int)

    boar = writer.state.entities["npc.boar"]
    assert (boar.status, boar.hp) == ("dead", 0)
    # 掉落到地面（不是直接进攻击者背包）；捡拾由 loot/get 动作完成。
    assert writer.state.ground["inn"]["item.boar_hide"] == 1
    assert "item.boar_hide" not in writer.state.entities["player:hero"].inventory

    # 经验/技能成长由库的 Reward + improve_skill 给出，再落成 Effect。
    xp = [e for e in event.effects if e["op"] == "combat_exp_add"]
    assert any(e["data"]["entity"] == "npc.boar" and e["data"]["delta"] >= 1 for e in xp)

    # xkx combatd 没有暴击公式：战报里不允许出现任何暴击字段。
    assert "critical" not in log and "暴击" not in str(log)

    # 事件是唯一事实源：hp_add 的增量合计必须等于状态实际变化（钳制在 Effect 内完成）。
    for eid, before in (("player:hero", 100), ("npc.boar", 40)):
        delta = sum(
            e["data"]["delta"] for e in event.effects
            if e["op"] == "hp_add" and str(e["data"]["entity"]) == eid
        )
        assert delta == writer.state.entities[eid].hp - before
    assert log["overkill"] == {"npc.boar": 1}

    # 死亡字段同样来自 Effect，不是动作里直接写 state
    dead = [e for e in event.effects if e["op"] == "entity_status"][0]["data"]
    assert dead["entity"] == "npc.boar" and dead["status"] == "dead" and dead["tick"] == 0


def test_fight_loss_kills_the_player_and_still_writes_one_event():
    writer, store = _writer(
        _hero(max_hp=10, hp=10, attack=1, defense=0, attrs={"str": 10, "dex": 10},
              combat_exp=0, skill_levels={}, skills={"dodge": 0.0}),
        _monster(max_hp=200, hp=200, attack=80, defense=10,
                 attrs={"str": 60, "dex": 30}, combat_exp=100,
                 skill_levels={"unarmed": 120, "dodge": 100}),
    )
    event = _fight(writer, {"target": "npc.boar"})
    assert store.count() == 2
    log = _log(event)
    assert log["winner"] == "defender"
    hero = writer.state.entities["player:hero"]
    assert (hero.status, hero.hp) == ("dead", 0)
    assert "entity_status" in [e["op"] for e in event.effects]


def test_round_cap_is_hard_twenty():
    writer, store = _writer(_hero(), _monster())
    event = _fight(writer, {"target": "npc.boar", "max_rounds": 99})
    log = _log(event)
    assert log["max_rounds"] == 20
    assert log["rounds_used"] <= 20


def test_prng_draw_order_is_locked():
    """固定属性 + seed=42 + seq=2 的取数序列必须逐项不变。

    顺序来自 `tools/xkx_formula` 的源码顺序：闪避 -> 招架 -> 武器伤害 ->
    护甲 -> 膂力 -> 经验压制 -> 伤势（两段）-> 命中奖励（受害者）。
    """
    hero = Entity(id="player:hero", name="英雄", role="wanderer", location="inn",
                  attrs={"str": 30, "dex": 20}, skills={"dodge": 0.4}, combat_exp=10,
                  skill_levels={"unarmed": 60}, max_hp=100, hp=100, attack=20, defense=5)
    boar = Entity(id="npc.boar", name="野猪", role="monster", location="inn",
                  attrs={"str": 20, "dex": 15}, skills={"dodge": 0.2}, combat_exp=5,
                  skill_levels={"unarmed": 40}, max_hp=40, hp=40, attack=10, defense=2)
    trace: list = []
    simulate_fight(hero, boar, PRNG(42, 2, "action:fight"), trace=trace)
    # 每项是 (n, value)：n 锁住公式顺序，value 锁住 PRNG 流。
    expected = [
        (30180, 25739),  # resolve_dodge: random(ap + dp)
        (33540, 25907),  # resolve_parry: random(ap + pp)
        (20, 4),         # roll_weapon_damage: random(damage)
        (2, 1),          # roll_armor: random(armor)
        (36, 25),        # roll_strength_bonus: random(bonus)
        (5, 0),          # modify_damage_by_exp: random(defender_exp)
        (41, 14),        # roll_wound: random(final_damage)
        (4, 0),          # roll_wound: killing 空手 random(4)
        (40, 31),        # damage_rewards: random(max_qi + qi)
    ]
    assert trace == expected
    assert len(trace) == 9


def test_formulas_are_delegated_to_xkx_formula(monkeypatch):
    """单一公式源：把库函数换掉必须立刻改变战果，说明引擎侧没有第二份实现。"""
    import wsim.sim.combat_rules as rules

    calls = {"dodge": 0, "damage": 0}
    real_damage = rules.xf.resolve_damage

    def fake_dodge(**kwargs):
        calls["dodge"] += 1
        return True  # 强制必闪：伤害公式永远走不到

    def fake_damage(**kwargs):
        calls["damage"] += 1
        return real_damage(**kwargs)

    monkeypatch.setattr(rules.xf, "resolve_dodge", fake_dodge)
    monkeypatch.setattr(rules.xf, "resolve_damage", fake_damage)

    hero = Entity(id="player:hero", name="英雄", role="wanderer", location="inn",
                  attrs={"str": 30, "dex": 20}, skills={"dodge": 0.4}, combat_exp=10,
                  skill_levels={"unarmed": 60}, max_hp=100, hp=100, attack=20, defense=5)
    boar = Entity(id="npc.boar", name="野猪", role="monster", location="inn",
                  attrs={"str": 20, "dex": 15}, skills={"dodge": 0.2}, combat_exp=5,
                  skill_levels={"unarmed": 40}, max_hp=40, hp=40, attack=10, defense=2)
    result = simulate_fight(hero, boar, PRNG(42, 2, "action:fight"))

    assert calls["dodge"] == 20 * 2  # 每回合双方各一次，且全部判闪避
    assert calls["damage"] == 0
    assert result["hp"] == {"player:hero": 100, "npc.boar": 40}
    assert result["dead"] == [] and result["winner"] == ""
    assert all(a["result"] == "dodge"
               for rnd in result["rounds"] for a in rnd["actions"])


def test_same_seed_same_action_is_byte_identical():
    def run():
        writer, store = _writer(_hero(), _monster())
        event = _fight(writer, {"target": "npc.boar", "loot": {"item.boar_hide": 1}})
        return event.to_canonical_dict(), state_hash(writer.state), store.event_hash()

    first = run()
    second = run()
    assert first == second


def test_replay_from_event_log_equals_live_state():
    writer, store = _writer(_hero(), _monster())
    _fight(writer, {"target": "npc.boar", "loot": {"item.boar_hide": 1}})

    replay = WorldState(locations=LOCATIONS)
    for event in store.read_all():
        apply_effects(replay, [Effect.from_dict(e) for e in event.effects])
        replay.tick = event.tick
    assert state_hash(replay) == state_hash(writer.state)
