"""多人同屏战斗（XKX_MULTI_COMBAT）单元测试。

覆盖评审七条：
1. 被动 NPC 还手（4004 实测 5/5 还手 -> PASSIVE_RETALIATION=True）；
4. trigger 全量登记 + ≥200 tick 选敌分布（2 人 75/25、4 人各 25%）；
5. 击杀无额外经验（无 kill_reward 事件）；
6. 逃跑只摘自己，不整只收兵；
7. 单 victim + RecordingPRNG 骰序（select 骰独立流，其余骰序不变）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

import wsim.sim.aggression as aggression_mod  # noqa: E402
from wsim.actions.aggression import NpcAggressionAction  # noqa: E402
from wsim.actions.combat import FightAction, PASSIVE_RETALIATION  # noqa: E402
from wsim.actions.registry import ActionRegistry  # noqa: E402
from wsim.core.models import Effect, Intent  # noqa: E402
from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim.aggression import MAX_OPPONENT, effective_enemies, select_index  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

LOCATIONS = {
    "inn": {"id": "inn", "name": "客栈", "exits": ["elsewhere"]},
    "elsewhere": {"id": "elsewhere", "name": "别处", "exits": []},
}
NPC_ID = "/d/hangzhou/npc/laohu"
P1 = "player:hero"
P2 = "player:second"
P3 = "player:third"
P4 = "player:fourth"
RULES = {NPC_ID: {"kind": "always", "name": "测试NPC"}}


@pytest.fixture()
def multi_on(monkeypatch):
    monkeypatch.setattr(aggression_mod, "_MULTI_COMBAT_ENABLED", True)
    yield
    monkeypatch.setattr(aggression_mod, "_MULTI_COMBAT_ENABLED", False)


@pytest.fixture()
def multi_off(monkeypatch):
    monkeypatch.setattr(aggression_mod, "_MULTI_COMBAT_ENABLED", False)
    yield


def test_world_config_multi_combat_key(tmp_path):
    from wsim.api.session import WorldSession

    cfg = tmp_path / "world.yaml"
    cfg.write_text("combat:\n  multi_combat: true\n", encoding="utf-8")
    assert WorldSession._load_multi_combat(cfg) is True
    cfg.write_text("combat:\n  lethal_fight: true\n", encoding="utf-8")
    assert WorldSession._load_multi_combat(cfg) is False
    assert WorldSession._load_multi_combat(tmp_path / "missing.yaml") is False


def test_configure_multi_combat_sets_process_flag(monkeypatch):
    monkeypatch.setattr(aggression_mod, "_ENV_MULTI_COMBAT", False)
    monkeypatch.setattr(aggression_mod, "_MULTI_COMBAT_ENABLED", False)
    aggression_mod.configure_multi_combat(True)
    assert aggression_mod.multi_combat_enabled() is True
    aggression_mod.configure_multi_combat(False)
    assert aggression_mod.multi_combat_enabled() is False


def test_configure_multi_combat_env_override(monkeypatch):
    monkeypatch.setattr(aggression_mod, "_ENV_MULTI_COMBAT", True)
    monkeypatch.setattr(aggression_mod, "_MULTI_COMBAT_ENABLED", False)
    aggression_mod.configure_multi_combat(False)
    assert aggression_mod.multi_combat_enabled() is True


def _entity(eid: str, role: str, *, hp: int = 100, attack: int = 15, defense: int = 5):
    return Effect(
        "create_entity",
        {
            "id": eid,
            "name": eid,
            "role": role,
            "location": "inn",
            "max_hp": hp,
            "hp": hp,
            "attack": attack,
            "defense": defense,
            "attrs": {"str": 20, "con": 20, "dex": 18, "kar": 20, "per": 20},
            "combat_exp": 10,
            "skill_levels": {"unarmed": 20},
        },
    )


def _world(*, players: tuple[str, ...] = (P1,), npc_hp: int = 500, player_attack: int = 1):
    state = WorldState(locations=LOCATIONS)
    store = EventStore(":memory:")
    reg = ActionRegistry()
    reg.register(NpcAggressionAction(RULES))
    reg.register(FightAction())
    writer = Writer(state, store, reg, seed=42)
    effects = [_entity(NPC_ID, "npc", hp=npc_hp, attack=40, defense=10)]
    for pid in players:
        # 高气血/高防，保证 400 tick 采样时不会提前死亡影响分布。
        effects.append(_entity(pid, "wanderer", hp=1_000_000, attack=player_attack, defense=5000))
    writer.bootstrap(effects, tick=0, verb="genesis")
    # 单元测试直接给玩家意图留出 tick 空间（生产由会话推进 state.tick）。
    writer.state.tick = 1000
    return writer, store


def _trigger(writer: Writer, pid: str, tick: int):
    return writer.submit(
        Intent(NPC_ID, "npc_aggression", {"mode": "trigger", "target": pid}, tick, "npc")
    )


def _continue(writer: Writer, tick: int, targets: list[str]):
    return writer.submit(
        Intent(
            NPC_ID,
            "npc_aggression",
            {"mode": "continue", "target": targets[0], "targets": targets},
            tick,
            "npc",
        )
    )


def _log(event) -> dict:
    for eff in event.effects:
        if eff.get("op") == "aggression_log":
            return dict(eff.get("data") or {})
    return {}


# ---------------------------------------------------------------- trigger 全量登记
def test_trigger_registers_all_players_in_order(multi_on):
    writer, _ = _world(players=(P1, P2))
    assert _trigger(writer, P1, 0).outcome == "ok"
    assert _trigger(writer, P2, 1).outcome == "ok"
    npc = writer.state.entities[NPC_ID]
    assert npc.combat_enemies == [P1, P2]
    assert npc.combat_target == P1  # 主目标不被第二人覆盖


def test_trigger_alone_still_rejects_already_registered(multi_on):
    writer, _ = _world(players=(P1,))
    _trigger(writer, P1, 0)
    second = _trigger(writer, P1, 1)
    assert second.outcome == "rejected"
    assert second.reason == "rejected:already_fighting"


# ---------------------------------------------------------------- 选敌分布
def test_two_target_distribution_400_ticks(multi_on):
    writer, _ = _world(players=(P1, P2), npc_hp=1_000_000_000)
    _trigger(writer, P1, 0)
    _trigger(writer, P2, 1)
    counts = {0: 0, 1: 0}
    for tick in range(1, 401):
        event = _continue(writer, tick + 1, [P1, P2])
        assert event.outcome == "ok", event.reason
        counts[int(_log(event)["opponent_index"])] += 1
    # 原版 random(4)：2 人时 0 吃 0/2/3 三个面 -> 75%，1 吃 1 个面 -> 25%。
    assert 272 <= counts[0] <= 328, counts
    assert 72 <= counts[1] <= 128, counts
    assert sum(counts.values()) == 400


def test_four_target_distribution_400_ticks(multi_on):
    writer, _ = _world(players=(P1, P2, P3, P4), npc_hp=1_000_000_000)
    for i, pid in enumerate((P1, P2, P3, P4)):
        _trigger(writer, pid, i)
    assert writer.state.entities[NPC_ID].combat_enemies == [P1, P2, P3, P4]
    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for tick in range(400):
        event = _continue(writer, tick + 5, [P1, P2, P3, P4])
        assert event.outcome == "ok", event.reason
        counts[int(_log(event)["opponent_index"])] += 1
    for idx, n in counts.items():
        assert 72 <= n <= 128, counts  # 25% ±7 个百分点
    assert sum(counts.values()) == 400


def test_five_target_window_never_selects_fifth(multi_on):
    P5 = "player:fifth"
    writer, _ = _world(players=(P1, P2, P3, P4, P5), npc_hp=1_000_000_000)
    for i, pid in enumerate((P1, P2, P3, P4, P5)):
        _trigger(writer, pid, i)
    assert writer.state.entities[NPC_ID].combat_enemies == [P1, P2, P3, P4, P5]
    counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    for tick in range(400):
        event = _continue(writer, tick + 6, [P1, P2, P3, P4, P5])
        assert event.outcome == "ok", event.reason
        counts[int(_log(event)["opponent_index"])] += 1
    # 原版 `random(4)` 只有 0..3 四个面；第 5 个敌人永远进不了窗口。
    assert counts[4] == 0, counts
    for idx in range(4):
        assert 72 <= counts[idx] <= 128, counts
    assert sum(counts.values()) == 400


def test_select_index_matches_attack_lpc_fallback():
    class Fixed:
        def __init__(self, value: int):
            self.value = value

        def randint(self, a: int, b: int) -> int:
            assert (a, b) == (0, MAX_OPPONENT - 1)
            return self.value

    # 2 人：2/3 越界 -> 回退 0；0/1 有效。
    assert [select_index(2, Fixed(v)) for v in range(4)] == [(0, 0), (1, 1), (0, 2), (0, 3)]
    # 3 人：3 越界 -> 0。
    assert [select_index(3, Fixed(v))[0] for v in range(4)] == [0, 1, 2, 0]
    # 4 人：每个面都有效。
    assert [select_index(4, Fixed(v))[0] for v in range(4)] == [0, 1, 2, 3]
    # 5 人：只有前 4 个进入窗口，第 5 个永远不会被选中。
    assert [select_index(5, Fixed(v))[0] for v in range(4)] == [0, 1, 2, 3]


# ---------------------------------------------------------------- 骰序锁死
class RecordingPRNG:
    """只记录调用形状，不改变底层 PRNG 的取数。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[tuple] = []

    @property
    def seed(self) -> int:
        return self.inner.seed

    @property
    def seq(self) -> int:
        return self.inner.seq

    def randint(self, a: int, b: int) -> int:
        self.calls.append(("randint", a, b))
        return self.inner.randint(a, b)

    def random(self) -> float:
        self.calls.append(("random",))
        return self.inner.random()

    def chance(self, p: float) -> bool:
        self.calls.append(("chance", p))
        return self.inner.chance(p)


def test_draw_order_select_on_separate_stream(multi_on, monkeypatch):
    # 选敌骰走独立盐 `action:npc_aggression:opponent`，不进入本 tick 战斗流。
    writer_on, _ = _world(players=(P1, P2))
    _trigger(writer_on, P1, 0)
    _trigger(writer_on, P2, 1)
    action = NpcAggressionAction(RULES)
    on_rng = RecordingPRNG(PRNG(42, 7, "action:npc_aggression"))
    effects = action.apply(
        writer_on.state,
        Intent(
            NPC_ID,
            "npc_aggression",
            {"mode": "continue", "target": P1, "targets": [P1, P2]},
            7,
            "npc",
        ),
        on_rng,
    )
    log = next(dict(e.data or {}) for e in effects if e.op == "aggression_log")
    expected = PRNG(42, 7, "action:npc_aggression:opponent").randint(0, MAX_OPPONENT - 1)
    assert log["opponent_roll"] == expected
    assert log["opponent_index"] == (expected if expected < 2 else 0)

    # 同一状态、只翻开关：战斗流逐笔形状与取值完全一致，没有插队。
    monkeypatch.setattr(aggression_mod, "_MULTI_COMBAT_ENABLED", False)
    off_rng = RecordingPRNG(PRNG(42, 7, "action:npc_aggression"))
    action.apply(
        writer_on.state,
        Intent(NPC_ID, "npc_aggression", {"mode": "continue", "target": P1}, 7, "npc"),
        off_rng,
    )
    assert on_rng.calls == off_rng.calls


# ---------------------------------------------------------------- 单 victim
def test_only_selected_victim_is_attacked(multi_on):
    writer, _ = _world(players=(P1, P2))
    _trigger(writer, P1, 0)
    _trigger(writer, P2, 1)
    before = {P1: writer.state.entities[P1].hp, P2: writer.state.entities[P2].hp}
    event = _continue(writer, 2, [P1, P2])
    log = _log(event)
    chosen = P1 if int(log["opponent_index"]) == 0 else P2
    other = P2 if chosen == P1 else P1
    assert writer.state.entities[chosen].hp <= before[chosen]
    assert writer.state.entities[other].hp == before[other]
    combat_logs = [dict(eff) for eff in event.effects if eff.get("op") == "combat_log"]
    assert combat_logs and combat_logs[0]["data"]["rounds"]
    for rnd in combat_logs[0]["data"]["rounds"]:
        for action_row in rnd["actions"]:
            assert action_row.get("target") in (None, chosen, NPC_ID)


def test_combat_contrib_equals_actual_hp_loss(multi_on):
    writer, _ = _world(players=(P1, P2), npc_hp=1_000_000, player_attack=500)
    _trigger(writer, P1, 0)
    _trigger(writer, P2, 1)
    for tick in range(3):
        _continue(writer, tick + 2, [P1, P2])
    npc = writer.state.entities[NPC_ID]
    total = sum(int(v) for v in npc.combat_contrib.values())
    assert total == 1_000_000 - int(npc.hp)
    assert set(npc.combat_contrib) <= {P1, P2}
    assert all(int(v) > 0 for v in npc.combat_contrib.values())


# ---------------------------------------------------------------- prune / wimpy
def test_prune_removes_only_target_that_left_room(multi_on):
    writer, _ = _world(players=(P1, P2))
    _trigger(writer, P1, 0)
    _trigger(writer, P2, 1)
    # 单元测试直接改写位置，模拟 `go` 离开房间；生产路径由 go 命令写 set_location。
    writer.state.entities[P1].location = "elsewhere"
    event = _continue(writer, 4, [P1, P2])
    assert event.outcome == "ok"
    npc = writer.state.entities[NPC_ID]
    assert npc.combat_enemies == [P2]
    assert npc.combat_target == P2
    # 另一个玩家仍在战斗，没有被整只 reset。
    assert int(npc.combat_round) >= 1


def test_wimpy_removes_only_fleeing_player(multi_on):
    writer, _ = _world(players=(P1, P2))
    _trigger(writer, P1, 0)
    _trigger(writer, P2, 1)
    p1 = writer.state.entities[P1]
    p1.max_hp = 100
    p1.hp = 1
    p1.wimpy = 100
    escaped = False
    for tick in range(60):
        event = _continue(writer, tick + 4, [P1, P2])
        current = writer.state.entities[P1]
        if current.location == "elsewhere":
            escaped = True
            break
        assert current.status == "alive", f"P1 在逃跑前死亡（tick={tick}）"
    assert escaped, "60 tick 内未触发 wimpy 逃跑"
    npc = writer.state.entities[NPC_ID]
    assert P1 not in npc.combat_enemies
    assert P2 in npc.combat_enemies
    assert npc.combat_target == P2
    assert npc.combat_enemies == [P2]  # 只摘逃跑者，不整只收兵


# ---------------------------------------------------------------- 被动还手
def test_passive_retaliation_registers_attacker(multi_on):
    assert PASSIVE_RETALIATION is True
    writer, _ = _world(players=(P1,))
    event = writer.submit(
        Intent(P1, "fight", {"target": NPC_ID, "max_rounds": 1}, 1, "player")
    )
    assert event.outcome == "ok", event.reason
    npc = writer.state.entities[NPC_ID]
    assert npc.combat_enemies == [P1]
    assert npc.combat_target == P1
    assert npc.combat_round == 0


def test_fight_death_clears_enemies_and_grants_no_kill_reward(multi_on):
    writer, _ = _world(players=(P1,), npc_hp=1, player_attack=1000)
    event = writer.submit(
        Intent(P1, "fight", {"target": NPC_ID, "max_rounds": 5}, 1, "player")
    )
    assert event.outcome == "ok", event.reason
    ops = {str(eff.get("op")) for eff in event.effects}
    fields = {
        str((eff.get("data") or {}).get("field"))
        for eff in event.effects
        if eff.get("op") == "set_field"
    }
    assert "combat_enemies" in fields
    assert not any("reward" in op for op in ops)
    assert not any(op in ("kill_reward", "quest_reward") for op in ops)


# ---------------------------------------------------------------- 关闭态
def test_off_mode_zero_new_fields_and_events(multi_off):
    writer, _ = _world(players=(P1,))
    event = writer.submit(
        Intent(P1, "fight", {"target": NPC_ID, "max_rounds": 1}, 1, "player")
    )
    assert event.outcome == "ok", event.reason
    for eff in event.effects:
        assert eff.get("op") != "set_field" or eff.get("data", {}).get("field") not in (
            "combat_enemies",
            "combat_contrib",
        )
    data = writer.state.entities[NPC_ID].to_dict()
    assert "combat_enemies" not in data and "combat_contrib" not in data
    assert not writer.state.entities[NPC_ID].combat_enemies


def test_multi_on_same_seed_double_run(multi_on):
    import json

    def run():
        writer, store = _world(players=(P1, P2), npc_hp=1_000_000)
        _trigger(writer, P1, 0)
        _trigger(writer, P2, 1)
        for tick in range(10):
            _continue(writer, tick + 2, [P1, P2])
        events = json.dumps(
            [e.to_canonical_dict() for e in store.read_all()],
            sort_keys=True,
            ensure_ascii=False,
        )
        return events, json.dumps(writer.state.to_dict(), sort_keys=True, ensure_ascii=False)

    first = run()
    second = run()
    assert first == second


def test_effective_enemies_falls_back_to_combat_target(multi_off):
    ent = WorldState(locations=LOCATIONS).entities
    from wsim.world.state import Entity

    legacy = Entity(id=NPC_ID, name="旧目标", role="npc", location="inn", combat_target=P1)
    assert effective_enemies(legacy) == [P1]
    empty = Entity(id=NPC_ID, name="空", role="npc", location="inn")
    assert effective_enemies(empty) == []
    modern = Entity(id=NPC_ID, name="新", role="npc", location="inn", combat_target=P1)
    modern.combat_enemies = [P2, P1, P2]
    assert effective_enemies(modern) == [P2, P1]
