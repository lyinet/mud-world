"""S1 战斗节奏三件套：出手门 + guard + 反击 + 昏迷补刀。

锁死四件事：
1. `random(0)` 返回 0 且不消耗引擎随机流（走兽战不扰动全局骰流）；
2. 首次守卫恰好消耗两次 `random(sizeof(guard_msg))`，已守卫 0 次；
3. 骰序：gate 在 `do_attack` 之前；反击在本次结算之后；
4. 走兽主动出手 0 次，但可通过反击 + 昏迷补刀被击杀（打猎闭环不破）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.core.rng import PRNG  # noqa: E402
from wsim.sim.combat_rules import TraceRandom, simulate_fight  # noqa: E402
from wsim.world.state import Entity  # noqa: E402


class RecordingRng:
    """记录每一次真正落到引擎的 `randint`，用来测“骰流长度”。"""

    def __init__(self, seed: int):
        self.inner = PRNG(seed, 1, "test:initiative_gate")
        self.calls: list[tuple[int, int, int]] = []

    def randint(self, low: int, high: int) -> int:
        value = self.inner.randint(low, high)
        self.calls.append((low, high, value))
        return value


def _ent(
    eid: str,
    *,
    role: str = "wanderer",
    hp: int = 100,
    max_hp: int | None = None,
    attack: int = 20,
    defense: int = 5,
    attrs: dict | None = None,
    skills: dict | None = None,
    combat_exp: int = 10,
    levels: dict | None = None,
) -> Entity:
    return Entity(
        id=eid,
        name=eid,
        role=role,
        location="inn",
        max_hp=max_hp if max_hp is not None else hp,
        hp=hp,
        attack=attack,
        defense=defense,
        attrs=attrs or {},
        skills=skills or {},
        combat_exp=combat_exp,
        skill_levels=levels or {},
    )


def _player() -> Entity:
    return _ent(
        "player:hero",
        hp=200,
        attack=50,
        defense=50,
        attrs={"str": 30, "dex": 20, "cps": 23, "cor": 24},
        skills={"dodge": 0.4, "parry": 0.4},
        combat_exp=6400,
        levels={"unarmed": 40, "dodge": 40, "parry": 40},
    )


def _wolfdog() -> Entity:
    # d/hangzhou/npc/wolfdog.lpc:8-20：走兽，set("cor", 30)，不设 cps。
    return _ent(
        "npc.wolfdog",
        role="monster",
        hp=50,
        attack=15,
        defense=2,
        attrs={"str": 26, "cor": 30},
        combat_exp=1000,
        levels={},
    )


def _guard_only(cps_a: int, cps_b: int):
    a = _ent("a", attack=0, defense=0,
             attrs={"str": 10, "dex": 10, "cps": cps_a, "cor": 10**9})
    b = _ent("b", attack=0, defense=0,
             attrs={"str": 10, "dex": 10, "cps": cps_b, "cor": 10**9})
    rng = RecordingRng(7)
    result = simulate_fight(a, b, rng, max_rounds=3, initiative_gate=True)
    return result, rng


def test_random_zero_returns_zero_without_consuming_dice():
    rng = RecordingRng(3)
    prng = TraceRandom(rng)
    assert prng.below(0) == 0
    assert prng.below(-5) == 0
    # `draws` 计数仍按“调用次数”记（旧事件字段），但引擎随机流一次都没动。
    assert prng.count == 2 and rng.calls == []
    assert prng.below(5) >= 0
    assert len(rng.calls) == 1 and rng.calls[0][1] == 4


def test_gate_dice_length_diff_equals_gate_draws():
    """victim.cps=0 vs 15：3 轮 × 2 人，骰流长度差 = 6 次门判断。"""
    _, zero = _guard_only(0, 0)
    _, fifteen = _guard_only(15, 15)
    assert len(zero.calls) == 4       # 仅首次守卫的 guardmsg×2 ×2 人
    assert len(fifteen.calls) == 4 + 6
    assert all(high >= 0 for _, high, _ in fifteen.calls)


def test_first_guard_draws_guardmsg_twice_then_silent():
    result, rng = _guard_only(0, 0)
    # guard_msg 有 5 条 → random(5) 的 high=4；两名角色各首次守卫一次。
    assert [high for _, high, _ in rng.calls] == [4, 4, 4, 4]
    actions = [act for rnd in result["rounds"] for act in rnd["actions"]]
    assert len(actions) == 6
    assert sum(1 for act in actions if act["attack_type"] == "guard") == 6
    assert sum(1 for act in actions if not act["guard_silent"]) == 2


def test_gate_is_rolled_before_any_hit_dice():
    actor = _ent("a", attack=40, defense=10, attrs={"str": 30, "dex": 99, "cor": 10**9})
    target = _ent("b", attack=1, defense=50, attrs={"str": 10, "dex": 1, "cps": 15, "cor": 0})
    rng = RecordingRng(11)
    simulate_fight(actor, target, rng, max_rounds=1, initiative_gate=True)
    assert rng.calls, "至少应发生一次门判断"
    # 15*3 = 45 → randint(0, 44)；它必须排在招式/命中/闪避之前。
    assert rng.calls[0][1] == 44


def test_dice_order_snapshot_gate_guard_hit_riposte():
    """固定 seed=2 / 2 轮：把 gate→guardmsg→命中/闪避→反击 的整条骰序钉死。"""
    player, wolf = _player(), _wolfdog()
    trace: list[tuple[int, int]] = []
    result = simulate_fight(
        player, wolf, PRNG(2, 1, "test:snapshot"), max_rounds=2,
        initiative_gate=True, trace=trace,
    )
    assert len(trace) == result["draws"]
    assert trace == [
        (0, 0),                # 玩家门：random(0) 被调用，但引擎不取骰
        (5, 4), (5, 3),        # 玩家首次守卫：guardmsg×2
        (69, 24),              # 狼狗第 1 次门：random(69) 未过 cor=30 → 守卫
        (5, 2), (5, 2),        # 狼狗首次守卫：guardmsg×2
        (0, 0),                # 第 2 轮玩家门：仍不取骰（已守卫，静默）
        (69, 48),              # 第 2 轮狼狗门通过（48 > cor=30）
        (4680, 612),           # 狼狗出手：resolve_dodge random(ap+dp)
        (10, 8), (10, 5),      # dodge_rewards ×2
        (0, 0),                # _riposte：random(狼狗.cps=0)，不取引擎骰
        (6580, 5432),          # 反击：resolve_dodge
        (6580, 2742),          # 反击：resolve_parry
        (50, 4),               # 反击：roll_weapon_damage
        (2, 1), (34, 27), (1000, 856), (56, 43),  # 反击：护甲/膂力/经验/伤势
        (4, 2), (7, 3),        # 反击：伤势二次判定
        (50, 6),               # 反击：damage_rewards
    ]
    round2 = result["rounds"][-1]["actions"]
    assert any(a["actor"] == wolf.id and a["attack_type"] == "regular"
               and a["result"] == "dodge" for a in round2)
    rp = [a for a in round2 if a["actor"] == player.id and a["attack_type"] == "quick"]
    assert rp and rp[0]["riposte"] is True


def test_beast_never_acts_but_is_killed_by_riposte_and_finisher():
    seen_kill = False
    seen_riposte = False
    seen_finisher = False
    for seed in range(1, 41):
        player, wolf = _player(), _wolfdog()
        result = simulate_fight(player, wolf, PRNG(seed, 1, "test:beast"), initiative_gate=True)
        actions = [act for rnd in result["rounds"] for act in rnd["actions"]]
        player_actions = [a for a in actions if a["actor"] == player.id]
        # 玩家主动出手必须恒为 0：走兽 cps=0 → random(0)=0 > cor(24) 恒假。
        assert not [a for a in player_actions if a["attack_type"] == "regular"]
        if result["winner"] == "attacker":
            seen_kill = True
            assert any(a["attack_type"] in ("riposte", "quick") for a in player_actions)
            seen_riposte = True
            # `riposte` 字段把反击 quick 与昏迷补刀 quick 分开：补刀恰好一次且为 False。
            finisher = [
                a for a in player_actions
                if a["attack_type"] == "quick" and a["riposte"] is False
            ]
            assert len(finisher) == 1
            assert all(
                a["riposte"] is True for a in player_actions
                if a["attack_type"] in ("regular", "quick", "riposte") and a is not finisher[0]
            )
            seen_finisher = True
            break
    assert seen_kill and seen_riposte and seen_finisher


def test_riposte_fires_after_dodge_or_parry():
    found = None
    for seed in range(1, 61):
        player, wolf = _player(), _wolfdog()
        result = simulate_fight(player, wolf, PRNG(seed, 1, "test:evade"), initiative_gate=True)
        for rnd in result["rounds"]:
            for i, act in enumerate(rnd["actions"]):
                if (act["actor"] == wolf.id and act["result"] in ("dodge", "parry")
                        and i + 1 < len(rnd["actions"])):
                    nxt = rnd["actions"][i + 1]
                    if (nxt["actor"] == player.id
                            and nxt["attack_type"] in ("quick", "riposte")):
                        found = (act, nxt)
                        break
            if found:
                break
        if found:
            break
    assert found, "反击应在闪避/招架后也触发"
    missed, riposte = found
    assert missed["result"] in ("dodge", "parry")
    assert riposte["attack_type"] in ("quick", "riposte")
    assert riposte["riposte"] is True
