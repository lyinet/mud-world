"""战斗公式表驱动测试。

**所有期望值都是“推导值”**：由 `fluffos/xkx100 @ c1b87db` 的 LPC 源码逐行手算得出，
不是在真实 MUD 驱动上跑出来的实测值。每条用例都带 `basis` 标注，测试会校验该标注存在。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from xkx_formula import (  # noqa: E402
    SKILL_USAGE_ATTACK,
    SKILL_USAGE_DEFENSE,
    STATE_ALIVE,
    STATE_DEAD,
    STATE_UNCONCIOUS,
    apply_power_point,
    apply_qi_damage,
    apply_qi_wound,
    damage_after_armor,
    damage_rewards,
    death_state,
    dodge_rewards,
    kongming_mitigate,
    lpc_div,
    modify_damage_by_exp,
    parry_rewards,
    resolve_damage,
    resolve_dodge,
    resolve_parry,
    roll_armor,
    roll_strength_bonus,
    roll_weapon_damage,
    roll_wound,
    select_parry_power,
    skill_power,
)
from xkx_formula.rng import SequenceRandom  # noqa: E402

DERIVED = "推导值（LPC 源码手算，非实测）"


class MaxRandom:
    """每次取最大值：below(n) -> n-1。"""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def below(self, n: int) -> int:
        self.calls.append(n)
        return n - 1 if n > 0 else 0


class MinRandom:
    """每次取最小值：below(n) -> 0。"""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def below(self, n: int) -> int:
        self.calls.append(n)
        return 0


# ---------------------------------------------------------------- LPC 整数语义


@pytest.mark.parametrize(
    ("a", "b", "expect"),
    [(7, 2, 3), (-7, 2, -3), (7, -2, -3), (-7, -2, 3), (1, 3, 0), (0, 5, 0)],
)
def test_lpc_div_truncates_toward_zero(a: int, b: int, expect: int) -> None:
    assert lpc_div(a, b) == expect


# ---------------------------------------------------------------- skill_power


SKILL_POWER_CASES = [
    {
        "args": dict(
            skill_level=0, combat_exp=1000, attr_effective=20, attr_base=15, usage=SKILL_USAGE_ATTACK
        ),
        "expect": 500,
        "basis": DERIVED,
    },
    {
        "args": dict(
            skill_level=300, combat_exp=1_000_000, attr_effective=30, attr_base=20,
            usage=SKILL_USAGE_ATTACK,
        ),
        "expect": 3_437_500,
    },
    {
        "args": dict(
            skill_level=900, combat_exp=0, attr_effective=5, attr_base=5,
            usage=SKILL_USAGE_ATTACK,
        ),
        "expect": 15_960_000,
    },
    {
        "args": dict(
            skill_level=100, combat_exp=0, attr_effective=20, attr_base=15,
            usage=SKILL_USAGE_DEFENSE, dodge_power_point_permille=1200,
        ),
        "expect": 126_000,
    },
    {
        "args": dict(
            skill_level=100, combat_exp=0, attr_effective=20, attr_base=15,
            usage=SKILL_USAGE_DEFENSE,
        ),
        "expect": 70_000,
    },
    {
        "args": dict(
            skill_level=100, combat_exp=0, attr_effective=20, attr_base=15,
            usage=SKILL_USAGE_DEFENSE, dodge_power_point_permille=600,
        ),
        "expect": 70_000,
    },
    {
        "args": dict(
            skill_level=0, apply_bonus=50, combat_exp=0, attr_effective=10, attr_base=10,
            usage=SKILL_USAGE_ATTACK,
        ),
        "expect": 5_000,
    },
    {
        "args": dict(
            skill_level=0, combat_exp=1001, attr_effective=10, attr_base=10,
            usage=SKILL_USAGE_ATTACK,
        ),
        "expect": 500,
    },
    {
        "args": dict(
            skill_level=0, combat_exp=-3, attr_effective=10, attr_base=10,
            usage=SKILL_USAGE_ATTACK,
        ),
        "expect": -1,
    },
    {
        "args": dict(
            skill_level=-5, combat_exp=0, attr_effective=10, attr_base=10,
            usage=SKILL_USAGE_ATTACK,
        ),
        "expect": 0,
    },
    {
        "args": dict(
            skill_level=0, combat_exp=1000, attr_effective=10, attr_base=10,
            usage=SKILL_USAGE_ATTACK, living=False,
        ),
        "expect": 0,
    },
]

# 表内每条期望值同源：LPC 手算推导值，非实测。
for _case in SKILL_POWER_CASES:
    _case["basis"] = DERIVED


@pytest.mark.parametrize("case", SKILL_POWER_CASES, ids=lambda c: str(c["expect"]))
def test_skill_power_table(case: dict) -> None:
    assert case["basis"] == DERIVED  # 期望值是推导值，不是实测值
    assert skill_power(**case["args"]) == case["expect"]


def test_skill_power_rejects_unknown_usage() -> None:
    with pytest.raises(ValueError):
        skill_power(
            skill_level=1, combat_exp=0, attr_effective=10, attr_base=10, usage=0
        )


# ---------------------------------------------------------------- 闪避


@pytest.mark.parametrize(
    ("random_source", "expected"),
    [(MaxRandom, False), (MinRandom, True)],
)
def test_dodge_boundary_probability(random_source, expected: bool) -> None:
    prng = random_source()
    assert resolve_dodge(ap=100, dp=80, prng=prng) is expected
    assert prng.calls == [180]


def test_dodge_busy_and_action_bonus() -> None:
    prng = MaxRandom()
    assert resolve_dodge(ap=100, dp=80, victim_busy=True, prng=prng) is False
    # dp 夹取后 80 -> busy 80/3=26；random(126)=125 < 26 False
    assert prng.calls == [126]

    prng = MaxRandom()
    assert resolve_dodge(ap=100, dp=2_000_000, dodge_action_bonus=20, prng=prng) is False
    # dp>1e6：2,000,000/100*(100+20)=2,400,000
    assert prng.calls == [2_400_100]

    prng = MaxRandom()
    assert resolve_dodge(ap=100, dp=1_000_000, dodge_action_bonus=20, prng=prng) is False
    # dp<=1e6：(100+20)*1,000,000/100=1,200,000
    assert prng.calls == [1_200_100]


def test_dodge_clamps_and_exact_threshold() -> None:
    prng = MaxRandom()
    assert resolve_dodge(ap=0, dp=0, prng=prng) is False  # 双方夹到 1
    assert prng.calls == [2]

    prng = SequenceRandom([79])
    assert resolve_dodge(ap=100, dp=80, prng=prng) is True

    prng = SequenceRandom([80])
    assert resolve_dodge(ap=100, dp=80, prng=prng) is False

    prng = MaxRandom()
    assert resolve_dodge(ap=300_000_000, dp=1, prng=prng) is False
    assert prng.calls == [200_000_001]

    # 辟邪刺足：随机判定为“可躲”，但 cizu 标志使其必不能躲
    prng = MinRandom()
    assert resolve_dodge(ap=100, dp=80, cizu=True, prng=prng) is False
    assert prng.calls == [180]


# ---------------------------------------------------------------- 招架


@pytest.mark.parametrize(
    ("victim_has_weapon", "attacker_has_weapon", "expect"),
    [
        (True, True, 100),
        (True, False, 200),
        (False, True, 0),
        (False, False, 50),
    ],
)
def test_select_parry_power(
    victim_has_weapon: bool, attacker_has_weapon: bool, expect: int
) -> None:
    assert (
        select_parry_power(
            victim_has_weapon=victim_has_weapon,
            attacker_has_weapon=attacker_has_weapon,
            parry_power=100,
            unarmed_power=50,
        )
        == expect
    )


@pytest.mark.parametrize(
    ("random_source", "expected"),
    [(MaxRandom, False), (MinRandom, True)],
)
def test_parry_boundary_probability(random_source, expected: bool) -> None:
    prng = random_source()
    assert resolve_parry(ap=100, pp=0, prng=prng) is expected
    # pp 夹到 1
    assert prng.calls == [101]


def test_parry_busy_and_action_bonus() -> None:
    prng = MaxRandom()
    assert resolve_parry(ap=100, pp=90, victim_busy=True, prng=prng) is False
    assert prng.calls == [130]  # pp: 90/3=30

    prng = MaxRandom()
    assert resolve_parry(ap=100, pp=2_000_000, parry_action_bonus=20, prng=prng) is False
    assert prng.calls == [2_400_100]

    prng = MaxRandom()
    assert resolve_parry(ap=100, pp=1_000_000, parry_action_bonus=20, prng=prng) is False
    assert prng.calls == [1_200_100]

    prng = MaxRandom()
    assert resolve_parry(ap=100, pp=300_000_000, prng=prng) is False
    assert prng.calls == [200_000_100]

    # 辟邪刺腕：随机判定为“可招架”，但 ciwan 标志使其必不能招架
    prng = MinRandom()
    assert resolve_parry(ap=100, pp=80, ciwan=True, prng=prng) is False
    assert prng.calls == [180]


# ---------------------------------------------------------------- 伤害基础


def test_roll_weapon_damage_table() -> None:
    cases = [
        (0, MinRandom, 0, [0]),
        (100, MinRandom, 50, [100]),
        (100, MaxRandom, 99, [100]),
        (300, MaxRandom, 299, [300]),
        (301, MaxRandom, 299, [1, 300]),
        (301, MinRandom, 150, [1, 300]),
    ]
    for raw, source, expect, calls in cases:
        prng = source()
        assert roll_weapon_damage(raw, prng) == expect, raw
        assert prng.calls == calls, raw

    prng = MinRandom()
    assert roll_weapon_damage(100, prng, action_damage=50) == 62
    assert prng.calls == [100]


def test_roll_armor_table() -> None:
    cases = [
        (0, MinRandom, 0, [0]),
        (300, MinRandom, 150, [300]),
        (300, MaxRandom, 299, [300]),
        (301, MaxRandom, 299, [1, 300]),
        (100_000, MaxRandom, 99_998, [99_700, 99_999]),
        (100_000, MinRandom, 150, [99_700, 300]),
    ]
    for armor, source, expect, calls in cases:
        prng = source()
        assert roll_armor(armor, prng) == expect, armor
        assert prng.calls == calls, armor

    assert damage_after_armor(100, 99) == 1
    assert damage_after_armor(100, 100) == 0
    assert damage_after_armor(100, 200) == 0


def test_roll_strength_bonus_table() -> None:
    prng = MinRandom()
    assert roll_strength_bonus(30, prng) == 15
    assert prng.calls == [30]

    prng = MaxRandom()
    assert roll_strength_bonus(30, prng) == 29
    assert prng.calls == [30]

    prng = MinRandom()
    assert roll_strength_bonus(0, prng) == 0
    assert prng.calls == []

    prng = MinRandom()
    assert roll_strength_bonus(-5, prng) == 0
    assert prng.calls == []

    prng = MinRandom()
    assert roll_strength_bonus(30, prng, action_force=50) == 18
    assert prng.calls == [37]

    prng = MinRandom()
    assert roll_strength_bonus(30, prng, force_hit=10) == 20
    assert prng.calls == [40]

    prng = MinRandom()
    assert roll_strength_bonus(30, prng, force_hit=10, victim_force_hit=-5) == 17
    assert prng.calls == [35]

    prng = MaxRandom()
    assert roll_strength_bonus(30, prng, weapon_hit=10) == 39
    assert prng.calls == [40]


def test_modify_damage_by_exp_table() -> None:
    prng = MaxRandom()
    assert modify_damage_by_exp(100, attacker_exp=1000, defender_exp=1000, prng=prng) == 100
    assert prng.calls == [1000]

    prng = MaxRandom()
    assert modify_damage_by_exp(100, attacker_exp=0, defender_exp=1000, prng=prng) == 4
    assert prng.calls == [1000, 500, 250, 125, 62, 31, 15, 7, 3, 1]

    prng = MaxRandom()
    assert modify_damage_by_exp(100, attacker_exp=60, defender_exp=1000, prng=prng) == 14
    assert prng.calls == [1000, 500, 250, 125, 62, 31]

    prng = MinRandom()
    assert modify_damage_by_exp(100, attacker_exp=0, defender_exp=1000, prng=prng) == 100
    assert prng.calls == [1000]

    prng = MaxRandom()
    assert modify_damage_by_exp(100, attacker_exp=999, defender_exp=1000, prng=prng) == 100
    assert prng.calls == [1000]

    with pytest.raises(ValueError):
        modify_damage_by_exp(100, attacker_exp=-1, defender_exp=1000, prng=MinRandom())


@pytest.mark.parametrize(
    ("damage", "expect"),
    [(100, 20), (51, 10), (50, 10), (10, 10), (0, 10)],
)
def test_kongming_mitigate(damage: int, expect: int) -> None:
    assert kongming_mitigate(damage) == expect


@pytest.mark.parametrize(
    ("damage", "permille", "expect"),
    [(100, 1200, 120), (100, 600, 60), (100, 0, 100), (100, 1500, 150)],
)
def test_apply_power_point(damage: int, permille: int, expect: int) -> None:
    assert apply_power_point(damage, permille) == expect


def test_resolve_damage_full_order_max() -> None:
    prng = MaxRandom()
    result = resolve_damage(
        raw_damage=100,
        armor=20,
        str_effective=30,
        attacker_exp=0,
        defender_exp=1000,
        prng=prng,
    )
    assert result.weapon_damage == 99
    assert result.armor_absorbed == 19
    assert result.after_armor == 80
    assert result.strength_bonus == 29
    assert result.after_exp == 4
    assert result.final_damage == 4
    assert prng.calls == [100, 20, 30, 1000, 500, 250, 125, 62, 31, 15, 7, 3, 1]


def test_resolve_damage_full_order_min_with_kongming_and_power_point() -> None:
    prng = MinRandom()
    result = resolve_damage(
        raw_damage=100,
        armor=20,
        str_effective=30,
        attacker_exp=1000,
        defender_exp=1000,
        prng=prng,
        kongming=True,
        power_point_permille=1200,
    )
    assert result.weapon_damage == 50
    assert result.armor_absorbed == 10
    assert result.after_armor == 40
    assert result.strength_bonus == 15
    assert result.after_exp == 55
    assert result.final_damage == 13  # 55 -> 空明 11 -> 1.2 倍 = 13
    assert prng.calls == [100, 20, 30, 1000]


# ---------------------------------------------------------------- 扣血 / 死亡


@pytest.mark.parametrize(
    ("qi", "damage", "expect"),
    [(10, 50, -1), (10, 10, 0), (10, 0, 10), (10, -5, 15)],
)
def test_apply_qi_damage(qi: int, damage: int, expect: int) -> None:
    assert apply_qi_damage(qi, damage) == expect


def test_apply_qi_wound() -> None:
    result = apply_qi_wound(qi=100, eff_qi=50, damage=20)
    assert (result.qi, result.eff_qi) == (30, 30)

    result = apply_qi_wound(qi=60, eff_qi=50, damage=0)
    assert (result.qi, result.eff_qi) == (50, 50)

    result = apply_qi_wound(qi=10, eff_qi=10, damage=50)
    assert (result.qi, result.eff_qi) == (-1, -1)


@pytest.mark.parametrize(
    ("kwargs", "expect"),
    [
        (dict(eff_qi=-1, eff_jing=10, qi=10, jing=10), STATE_DEAD),
        (dict(eff_qi=10, eff_jing=-1, qi=10, jing=10), STATE_DEAD),
        (dict(eff_qi=10, eff_jing=10, qi=-1, jing=10), STATE_UNCONCIOUS),
        (dict(eff_qi=10, eff_jing=10, qi=-1, jing=10, living=False), STATE_DEAD),
        (dict(eff_qi=10, eff_jing=10, qi=10, jing=-1), STATE_UNCONCIOUS),
        (dict(eff_qi=0, eff_jing=0, qi=0, jing=0), STATE_ALIVE),
    ],
)
def test_death_state(kwargs: dict, expect: str) -> None:
    assert death_state(**kwargs) == expect


def test_roll_wound_draw_order() -> None:
    prng = SequenceRandom([50, 0])
    assert roll_wound(damage=100, armor=0, killing=True, has_weapon=True, prng=prng) is True
    assert prng.calls == [100, 2]

    prng = SequenceRandom([50, 1, 0])
    assert roll_wound(damage=100, armor=0, killing=True, has_weapon=True, prng=prng) is True
    assert prng.calls == [100, 2, 4]

    prng = SequenceRandom([50, 0])
    assert roll_wound(damage=100, armor=0, killing=False, has_weapon=True, prng=prng) is True
    assert prng.calls == [100, 4]

    prng = SequenceRandom([50, 3])
    assert roll_wound(damage=100, armor=0, killing=False, has_weapon=True, prng=prng) is False
    assert prng.calls == [100, 4]

    prng = SequenceRandom([50, 0])
    assert roll_wound(damage=100, armor=0, killing=True, has_weapon=False, prng=prng) is True
    assert prng.calls == [100, 4]

    prng = SequenceRandom([50, 1, 0])
    assert roll_wound(damage=100, armor=0, killing=True, has_weapon=False, prng=prng) is True
    assert prng.calls == [100, 4, 7]

    prng = SequenceRandom([99])
    assert roll_wound(damage=100, armor=99, killing=True, has_weapon=True, prng=prng) is False
    assert prng.calls == [100]

    prng = SequenceRandom([0])
    assert roll_wound(damage=0, armor=0, killing=True, has_weapon=True, prng=prng) is False
    assert prng.calls == [0]


# ---------------------------------------------------------------- 奖励


def test_dodge_rewards_victim_and_draw_order() -> None:
    prng = SequenceRandom([119])
    reward = dodge_rewards(
        ap=100, dp=80, attacker_is_player=False, victim_is_player=False,
        attacker_int=20, attacker_attack_skill_level=10,
        victim_jing=100, victim_max_jing=100, victim_int=20,
        victim_dodge_skill_level=10, prng=prng,
    )
    assert reward.victim_combat_exp == 1
    assert reward.victim_skill_increase == 1
    assert reward.attacker_combat_exp == 0
    assert prng.calls == [120]


def test_dodge_rewards_attacker_npc_and_draw_order() -> None:
    prng = SequenceRandom([19, 5])
    reward = dodge_rewards(
        ap=80, dp=100, attacker_is_player=False, victim_is_player=True,
        attacker_int=20, attacker_attack_skill_level=10,
        victim_jing=100, victim_max_jing=100, victim_int=20,
        victim_dodge_skill_level=10, prng=prng,
    )
    assert reward.attacker_combat_exp == 1
    assert reward.attacker_skill_increase == 5
    assert reward.victim_combat_exp == 0
    assert prng.calls == [20, 20]


def test_dodge_rewards_players_draw_nothing() -> None:
    prng = MinRandom()
    reward = dodge_rewards(
        ap=100, dp=80, attacker_is_player=True, victim_is_player=True,
        attacker_int=20, attacker_attack_skill_level=10,
        victim_jing=100, victim_max_jing=100, victim_int=20,
        victim_dodge_skill_level=10, prng=prng,
    )
    assert reward.victim_combat_exp == 0
    assert prng.calls == []


def test_parry_rewards_only_victim() -> None:
    prng = SequenceRandom([119])
    reward = parry_rewards(
        ap=100, dp=80, attacker_is_player=False, victim_is_player=False,
        victim_jing=100, victim_max_jing=100, victim_int=20,
        victim_parry_skill_level=10, prng=prng,
    )
    assert reward.victim_combat_exp == 1
    assert reward.victim_skill_increase == 1
    assert reward.attacker_combat_exp == 0
    assert prng.calls == [120]

    prng = MinRandom()
    reward = parry_rewards(
        ap=80, dp=100, attacker_is_player=False, victim_is_player=False,
        victim_jing=100, victim_max_jing=100, victim_int=20,
        victim_parry_skill_level=10, prng=prng,
    )
    assert prng.calls == []
    assert reward == type(reward)()


def test_parry_rewards_skill_cap() -> None:
    prng = SequenceRandom([119])
    reward = parry_rewards(
        ap=100, dp=80, attacker_is_player=False, victim_is_player=False,
        victim_jing=100, victim_max_jing=100, victim_int=20,
        victim_parry_skill_level=300, prng=prng,
    )
    assert reward.victim_combat_exp == 1
    assert reward.victim_skill_increase == 0


def test_damage_rewards_table() -> None:
    common = dict(
        attacker_jing=100, attacker_max_jing=100, attacker_int=20,
        attacker_attack_skill_level=10, attacker_potential=100,
        attacker_learned_points=0, victim_max_qi=100, victim_qi=60,
        victim_potential=100, victim_learned_points=0,
    )

    prng = SequenceRandom([49, 99])
    reward = damage_rewards(
        ap=80, dp=100, damage=100, attacker_is_player=False, victim_is_player=False,
        prng=prng, **common,
    )
    assert reward.attacker_combat_exp == 1
    assert reward.attacker_potential == 1
    assert reward.attacker_skill_increase == 1
    assert reward.victim_combat_exp == 1
    assert reward.victim_potential == 1
    assert prng.calls == [50, 160]

    prng = SequenceRandom([99])
    reward = damage_rewards(
        ap=200, dp=150, damage=100, attacker_is_player=False, victim_is_player=False,
        prng=prng, **common,
    )
    assert reward.attacker_combat_exp == 0
    assert reward.victim_combat_exp == 1
    assert prng.calls == [160]

    prng = MinRandom()
    reward = damage_rewards(
        ap=80, dp=100, damage=100, attacker_is_player=True, victim_is_player=True,
        prng=prng, **common,
    )
    assert reward.attacker_combat_exp == 0
    assert reward.victim_combat_exp == 0
    assert prng.calls == []


def test_damage_rewards_potential_cap_is_strict() -> None:
    prng = SequenceRandom([49, 99])
    reward = damage_rewards(
        ap=80, dp=100, damage=100, attacker_is_player=False, victim_is_player=False,
        attacker_jing=100, attacker_max_jing=100, attacker_int=20,
        attacker_attack_skill_level=10,
        attacker_potential=100_000, attacker_learned_points=0,
        victim_max_qi=100, victim_qi=60,
        victim_potential=100_000, victim_learned_points=0,
        prng=prng,
    )
    assert reward.attacker_potential == 0
    assert reward.victim_potential == 0
    assert reward.attacker_combat_exp == 1
    assert reward.victim_combat_exp == 1
    assert prng.calls == [50, 160]


def test_damage_rewards_zero_qi_window() -> None:
    # max_qi + qi == 0 时 LPC random(0)=0，0 < damage 成立，受害者仍得经验。
    prng = SequenceRandom([0])
    reward = damage_rewards(
        ap=200, dp=150, damage=1, attacker_is_player=False, victim_is_player=False,
        attacker_jing=100, attacker_max_jing=100, attacker_int=20,
        attacker_attack_skill_level=300,
        attacker_potential=100, attacker_learned_points=0,
        victim_max_qi=0, victim_qi=0, victim_potential=100, victim_learned_points=0,
        prng=prng,
    )
    assert reward.victim_combat_exp == 1
    assert reward.victim_potential == 1
    assert prng.calls == [0]
