"""战斗核心公式（纯函数）。

来源：`adm/daemons/combatd.lpc`（xkx100 @ c1b87db）
- `skill_power()`：197-242
- 命中 / 闪避：328-354
- 招架：387-406
- 伤害 / 护甲 / 膂力 / 经验修正：436-558
- 伤势判定：560-567
- 经验奖励：361-373、417-423、575-591
死亡判定补充自 `inherit/char/char.lpc:64-78` 与 `feature/damage.lpc:14-69`。

约定：
- 所有函数“参数进、结果出”，不读对象、不写状态、不碰存储。
- 所有随机取自调用方传入的 `RandomSource`；取数顺序与源码一致，供回放核对。
- 所有除法走 `lpc_div`（向零截断）；不使用浮点。
- `power_point` 的 LPC 浮点系数改为千分位整数（1000..1500）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .lpc_math import lpc_div
from .rng import RandomSource

SKILL_USAGE_ATTACK = 1
SKILL_USAGE_DEFENSE = 2

POWER_MIN = 1
POWER_MAX = 200_000_000

RESULT_DODGE = -1
RESULT_PARRY = -2

STATE_ALIVE = "alive"
STATE_UNCONCIOUS = "unconcious"  # 与 LPC 原文拼写保持一致
STATE_DEAD = "dead"


def clamp_power(value: int) -> int:
    """`combatd.lpc:329-331/340-342/402-404` 对 ap/dp/pp 的统一上下限。"""
    value = int(value)
    if value < POWER_MIN:
        return POWER_MIN
    if value > POWER_MAX:
        return POWER_MAX
    return value


def _clamp_permille(value: int) -> int:
    """`combatd.lpc:216-218`：轻功威力系数夹到 1.0..1.5。"""
    value = int(value)
    if value < 1000:
        return 1000
    if value > 1500:
        return 1500
    return value


def skill_power(
    *,
    skill_level: int,
    apply_bonus: int = 0,
    combat_exp: int,
    attr_effective: int,
    attr_base: int,
    usage: int,
    dodge_power_point_permille: int | None = None,
    living: bool = True,
) -> int:
    """`combatd.lpc:197-242`。

    参数：
    - `skill_level`：`query_skill(skill)` 的等级（攻击时调用方先取攻击技能等级）。
    - `apply_bonus`：攻击用 `apply/attack`，防御用 `apply/defense`。
    - `combat_exp`：`query("combat_exp")`，整数。
    - `attr_effective`：攻击用 `query_str()`，防御用 `query_dex()`。
    - `attr_base`：攻击用 `query("str")`，防御用 `query("dex")`（原始值，不做 10..200 夹取）。
    - `usage`：`SKILL_USAGE_ATTACK`(1) 或 `SKILL_USAGE_DEFENSE`(2)。
    - `dodge_power_point_permille`：防御时映射轻功的 `power_point` 千分位值；
      `None` 表示没有映射轻功，不做乘法。
    - `living`：`!living(ob) || noliving` 时源码直接返回 0。

    返回值是未夹取到 [1, 200000000] 的原始战力；命中判定前再调用 `clamp_power`。

    实测校准（书生场景，10 轮，N=71）：`level=0` 时确实走 `combat_exp/2` 分支，
    命中前再夹到 ≥1。我方经验 0→6 期间，按每轮 `hp` 实测经验重建 ap 后，
    闪避/招架/命中偏差为 -1.88/+0.10/+1.78pp（详见 formula-map.md 5.2）。
    """
    if usage not in (SKILL_USAGE_ATTACK, SKILL_USAGE_DEFENSE):
        raise ValueError("usage 必须是 SKILL_USAGE_ATTACK 或 SKILL_USAGE_DEFENSE")
    if not living:
        return 0

    level = int(skill_level) + int(apply_bonus)
    if usage == SKILL_USAGE_DEFENSE and dodge_power_point_permille is not None:
        level = lpc_div(level * _clamp_permille(dodge_power_point_permille), 1000)

    if level == 0:
        return lpc_div(int(combat_exp), 2)

    if level > 800:
        level = 800
    if level < 1:
        level = 1

    attr = int(attr_effective)
    if attr < 10:
        attr = 10
    if attr > 200:
        attr = 200

    exp = int(combat_exp)
    power = lpc_div(level, 6) * level * level

    return lpc_div(power + exp, 80) * (attr + int(attr_base))


def resolve_dodge(
    *,
    ap: int,
    dp: int,
    victim_busy: bool = False,
    dodge_action_bonus: int = 0,
    cizu: bool = False,
    prng: RandomSource,
) -> bool:
    """`combatd.lpc:328-354`：返回 True 表示躲避成功（未命中）。

    执行顺序与源码一致：ap/dp 夹取 -> `action["dodge"]` 修正 -> 再夹取 ->
    忙碌 dp/3 -> `random(ap + dp) < dp`。`cizu` 是受害者的
    `query_temp("bixie/cizu")`（辟邪刺足），为真时必不能躲。
    注意忙碌除法发生在夹取之后，结果可能小于 1，源码也如此。

    实测校准（书生场景，10 轮，N=71，ap 按每轮实测经验重建）：实测闪避率 42.25%
    （30/71），预测 44.13%，偏差 -1.88pp；静态按 ap=1 预测为 50%，偏差 -7.75pp，
    差异来自“被打中会涨经验”导致的 ap 漂移，不是公式翻译错误（formula-map.md 5.2）。
    """
    ap = clamp_power(ap)
    dp = clamp_power(dp)

    if dodge_action_bonus:
        if dp > 1_000_000:
            dp = lpc_div(dp, 100) * (100 + int(dodge_action_bonus))
        else:
            dp = lpc_div((100 + int(dodge_action_bonus)) * dp, 100)
        dp = clamp_power(dp)

    if victim_busy:
        dp = lpc_div(dp, 3)

    return prng.below(ap + dp) < dp and not cizu


def select_parry_power(
    *,
    victim_has_weapon: bool,
    attacker_has_weapon: bool,
    parry_power: int,
    unarmed_power: int,
) -> int:
    """`combatd.lpc:387-393` 的 pp 选择规则。

    - 受害者有武器：pp = parry 战力；攻击者空手时 pp *= 2。
    - 受害者空手、攻击者有武器：pp = 0。
    - 双方都空手：pp = unarmed 战力（不翻倍，源码的 `if (!weapon) pp *= 2`
      只写在“受害者有武器”分支里）。
    """
    if victim_has_weapon:
        pp = int(parry_power)
        if not attacker_has_weapon:
            pp *= 2
        return pp
    if attacker_has_weapon:
        return 0
    return int(unarmed_power)


def resolve_parry(
    *,
    ap: int,
    pp: int,
    victim_busy: bool = False,
    parry_action_bonus: int = 0,
    ciwan: bool = False,
    prng: RandomSource,
) -> bool:
    """`combatd.lpc:395-406`：返回 True 表示招架成功（未命中）。

    顺序：忙碌 pp/3 -> `action["parry"]` 修正 -> 夹取 [1, 200M] ->
    `random(ap + pp) < pp`。`ciwan` 是受害者的 `query_temp("bixie/ciwan")`
    （辟邪刺腕），为真时必不能招架。

    实测校准（书生场景，10 轮，N=71，ap 按每轮实测经验重建）：实测招架率 23.94%
    （17/71），预测 23.85%，偏差 +0.10pp（formula-map.md 5.2）。
    """
    ap = clamp_power(ap)
    pp = int(pp)

    if victim_busy:
        pp = lpc_div(pp, 3)

    if parry_action_bonus:
        if pp > 1_000_000:
            pp = lpc_div(pp, 100) * (100 + int(parry_action_bonus))
        else:
            pp = lpc_div((100 + int(parry_action_bonus)) * pp, 100)

    pp = clamp_power(pp)
    return prng.below(ap + pp) < pp and not ciwan


def roll_weapon_damage(
    raw_damage: int,
    prng: RandomSource,
    *,
    action_damage: int = 0,
) -> int:
    """`combatd.lpc:436-445`：基础伤害 -> 高伤截断 -> 随机平均 -> 招式加成。

    高伤截断只对“原始伤害 > 300”生效；`random(0)` 按 LPC 语义为 0。
    """
    damage = int(raw_damage)
    if damage > 300:
        damage = 300 + prng.below(damage - 300)
    damage = lpc_div(damage + prng.below(damage), 2)
    if action_damage:
        damage += lpc_div(int(action_damage) * damage, 200)
    return damage


def roll_armor(armor: int, prng: RandomSource) -> int:
    """`combatd.lpc:451-454`：护甲减伤值 -> 高值截断 -> 随机平均。"""
    defense_factor = int(armor)
    if defense_factor > 300:
        defense_factor = 300 + prng.below(defense_factor - 300)
    return lpc_div(defense_factor + prng.below(defense_factor), 2)


def damage_after_armor(damage: int, armor_absorbed: int) -> int:
    """`combatd.lpc:454-456`：扣护甲并不低于 0。"""
    damage = int(damage) - int(armor_absorbed)
    if damage < 0:
        return 0
    return damage


def roll_strength_bonus(
    str_effective: int,
    prng: RandomSource,
    *,
    force_hit: int = 0,
    victim_force_hit: int = 0,
    action_force: int = 0,
    martial_hit: int = 0,
    victim_parry_hit: int = 0,
    weapon_hit: int = 0,
) -> int:
    """`combatd.lpc:464-513` 的膂力加成。

    钩子（`hit_ob` / `be_hit_ob`）依赖对象与技能全局状态，不能在本库内求值；
    调用方先把它们的整数增量算好，按源码顺序传入。
    顺序：force hit -> victim force be_hit -> action["force"] -> martial hit ->
    victim parry be_hit -> weapon hit -> `(bonus + random(bonus)) / 2`。

    实测校准（书生场景，我方命中 24 次）：我方空手无兵器、书生无护甲、
    `query_str()=20`，实测伤害全部落在 10-19 文本桶，与
    `(20 + random(20)) / 2 ∈ [10,19]` 完全一致（formula-map.md 5.3）。
    桶级之外的分辨率受原版 `damage_msg` 限制。
    """
    bonus = int(str_effective)
    bonus += int(force_hit)
    bonus += int(victim_force_hit)

    if action_force:
        bonus += lpc_div(int(action_force) * bonus, 200)

    bonus += int(martial_hit)
    bonus += int(victim_parry_hit)
    bonus += int(weapon_hit)

    if bonus > 0:
        bonus = lpc_div(bonus + prng.below(bonus), 2)
    else:
        bonus = 0
    return bonus


def modify_damage_by_exp(
    damage: int,
    *,
    attacker_exp: int,
    defender_exp: int,
    prng: RandomSource,
) -> int:
    """`combatd.lpc:524-528`：经验压制循环。

    `while (random(defense_factor) > my_exp) { damage -= damage / 3; defense_factor /= 2; }`
    前置条件：双方经验非负；否则 LPC 会陷入死循环，本库显式报错。

    实测校准状态：**未触发，仍为推导值**。书生场景守方经验 0→1，
    `random(0/1) > 6` 恒假；流氓场景守方经验 1000 但 dp≈2720，我方打不中。
    可复现场景里找不到“经验 >80 且 dp 小”的对手（formula-map.md 5.4）。
    """
    attacker_exp = int(attacker_exp)
    defender_exp = int(defender_exp)
    if attacker_exp < 0 or defender_exp < 0:
        raise ValueError("combat_exp 必须非负（否则 LPC 循环不会终止）")

    damage = int(damage)
    defense_factor = defender_exp
    while prng.below(defense_factor) > attacker_exp:
        damage -= lpc_div(damage, 3)
        defense_factor = lpc_div(defense_factor, 2)
    return damage


def kongming_mitigate(damage: int) -> int:
    """`combatd.lpc:534-538`：空明拳化解。

    注意源码语义：伤害 <= 50（包括 0）时固定变成 10。
    """
    damage = int(damage)
    if damage > 50:
        return lpc_div(damage, 5)
    return 10


def apply_power_point(damage: int, permille: int) -> int:
    """`combatd.lpc:546-549`：招式威力系数。

    源码只在 `power_point > 0` 时相乘；这里不做 1.0..1.5 夹取（夹取只用于
    防御侧 `skill_power` 的轻功系数）。千分位整数替掉浮点乘法。
    """
    if int(permille) <= 0:
        return int(damage)
    return lpc_div(int(damage) * int(permille), 1000)


def apply_qi_damage(qi: int, damage: int) -> int:
    """`feature/damage.lpc:30-33`：`receive_damage` 对 qi 的落值。

    `val = qi - damage; val >= 0 ? val : -1`。
    """
    value = int(qi) - int(damage)
    return value if value >= 0 else -1


@dataclass(frozen=True)
class VitalAfterWound:
    qi: int
    eff_qi: int


def apply_qi_wound(qi: int, eff_qi: int, damage: int) -> VitalAfterWound:
    """`feature/damage.lpc:56-64`：`receive_wound` 对 eff_qi / qi 的落值。

    eff_qi 扣到负数时统一落 -1；随后 `qi` 不得高于新的 eff_qi。
    """
    new_eff = int(eff_qi) - int(damage)
    if new_eff < 0:
        new_eff = -1
    new_qi = int(qi)
    if new_qi > new_eff:
        new_qi = new_eff
    return VitalAfterWound(qi=new_qi, eff_qi=new_eff)


def death_state(
    *,
    qi: int,
    jing: int,
    eff_qi: int,
    eff_jing: int,
    living: bool = True,
) -> str:
    """`inherit/char/char.lpc:64-78` 的心跳死亡/昏迷判定。

    顺序即优先级：
    1. `eff_qi < 0 || eff_jing < 0` -> dead（必死）。
    2. `qi < 0 || jing < 0` -> 非 living 时 dead，否则 unconcious。
    3. 其余 alive。
    """
    if int(eff_qi) < 0 or int(eff_jing) < 0:
        return STATE_DEAD
    if int(qi) < 0 or int(jing) < 0:
        if not living:
            return STATE_DEAD
        return STATE_UNCONCIOUS
    return STATE_ALIVE


def roll_wound(
    *,
    damage: int,
    armor: int,
    killing: bool,
    has_weapon: bool,
    prng: RandomSource,
) -> bool:
    """`combatd.lpc:560-567`：是否造成有效伤势（`receive_wound`）。

    源码布尔式含短路，随机取数次数也随之变化；这里逐步复刻：
    1. `random(damage) > armor` 不成立 -> False（只取一次）。
    2. killing 时：空手 `random(4)==0`，持武器 `random(2)==0`；命中即 True。
    3. 否则（或第 2 步未命中）：空手 `random(7)==0`，持武器 `random(4)==0`。
    """
    if prng.below(int(damage)) > int(armor):
        if killing:
            if not has_weapon:
                if prng.below(4) == 0:
                    return True
            else:
                if prng.below(2) == 0:
                    return True
        if not has_weapon:
            return prng.below(7) == 0
        return prng.below(4) == 0
    return False


@dataclass(frozen=True)
class DamageBreakdown:
    """一次命中（已过闪避/招架）的伤害分步结果。"""

    weapon_damage: int
    armor_absorbed: int
    after_armor: int
    strength_bonus: int
    after_exp: int
    final_damage: int


def resolve_damage(
    *,
    raw_damage: int,
    armor: int,
    str_effective: int,
    attacker_exp: int,
    defender_exp: int,
    prng: RandomSource,
    action_damage: int = 0,
    force_hit: int = 0,
    victim_force_hit: int = 0,
    action_force: int = 0,
    martial_hit: int = 0,
    victim_parry_hit: int = 0,
    weapon_hit: int = 0,
    kongming: bool = False,
    power_point_permille: int = 0,
) -> DamageBreakdown:
    """按 `combatd.lpc:436-549` 顺序执行伤害分步。

    取数顺序被锁死：武器伤害 2 次 -> 护甲 2 次 -> 膂力 1 次 -> 经验修正 N 次。
    返回的 `final_damage` 即源码送入 `receive_damage("qi", ...)` 的值；
    扣血与伤势判定由调用方继续用 `apply_qi_damage` / `apply_qi_wound` /
    `roll_wound` 完成（`roll_wound` 在扣血之后取数）。
    """
    weapon_damage = roll_weapon_damage(raw_damage, prng, action_damage=action_damage)
    armor_absorbed = roll_armor(armor, prng)
    damage = damage_after_armor(weapon_damage, armor_absorbed)

    strength_bonus = roll_strength_bonus(
        str_effective,
        prng,
        force_hit=force_hit,
        victim_force_hit=victim_force_hit,
        action_force=action_force,
        martial_hit=martial_hit,
        victim_parry_hit=victim_parry_hit,
        weapon_hit=weapon_hit,
    )
    damage += strength_bonus
    if damage < 0:
        damage = 0

    damage = modify_damage_by_exp(
        damage,
        attacker_exp=attacker_exp,
        defender_exp=defender_exp,
        prng=prng,
    )
    after_exp = damage

    if kongming:
        damage = kongming_mitigate(damage)

    final_damage = apply_power_point(damage, power_point_permille)
    return DamageBreakdown(
        weapon_damage=weapon_damage,
        armor_absorbed=armor_absorbed,
        after_armor=damage_after_armor(weapon_damage, armor_absorbed),
        strength_bonus=strength_bonus,
        after_exp=after_exp,
        final_damage=final_damage,
    )


@dataclass(frozen=True)
class Reward:
    """一次战斗判定带来的经验/潜能/技能增量；只返回增量，不写状态。"""

    attacker_combat_exp: int = 0
    attacker_potential: int = 0
    victim_combat_exp: int = 0
    victim_potential: int = 0
    attacker_skill_increase: int = 0
    victim_skill_increase: int = 0


def dodge_rewards(
    *,
    ap: int,
    dp: int,
    attacker_is_player: bool,
    victim_is_player: bool,
    attacker_int: int,
    attacker_attack_skill_level: int,
    victim_jing: int,
    victim_max_jing: int,
    victim_int: int,
    victim_dodge_skill_level: int,
    prng: RandomSource,
) -> Reward:
    """`combatd.lpc:361-373` 的闪避奖励。

    取数顺序：先受害者（`random(jing*100/max_jing + int) > 50`），
    再攻击者 NPC（`random(int) > 15`，然后 `random(int)` 作为 skill 增量）。
    攻击者的技能增量是原始值：`improve_skill` 会把 0 归一为 1。
    """
    attacker_combat_exp = 0
    victim_combat_exp = 0
    attacker_skill_increase = 0
    victim_skill_increase = 0

    if dp < ap and (not victim_is_player or not attacker_is_player):
        if victim_max_jing <= 0:
            raise ValueError("max_jing 必须为正（LPC 此处会除零）")
        chance = lpc_div(int(victim_jing) * 100, int(victim_max_jing)) + int(victim_int)
        if prng.below(chance) > 50:
            victim_combat_exp = 1
            if int(victim_dodge_skill_level) < 300:
                victim_skill_increase = 1

    if ap < dp and not attacker_is_player:
        if prng.below(int(attacker_int)) > 15:
            attacker_combat_exp = 1
        attacker_skill_increase = prng.below(int(attacker_int))

    return Reward(
        attacker_combat_exp=attacker_combat_exp,
        victim_combat_exp=victim_combat_exp,
        attacker_skill_increase=attacker_skill_increase,
        victim_skill_increase=victim_skill_increase,
    )


def parry_rewards(
    *,
    ap: int,
    dp: int,
    attacker_is_player: bool,
    victim_is_player: bool,
    victim_jing: int,
    victim_max_jing: int,
    victim_int: int,
    victim_parry_skill_level: int,
    prng: RandomSource,
) -> Reward:
    """`combatd.lpc:417-423` 的招架奖励（源码只有受害者侧，没有攻击者经验）。

    条件按源码使用 `dp < ap`（不是 pp）。
    """
    if dp < ap and (not victim_is_player or not attacker_is_player):
        if victim_max_jing <= 0:
            raise ValueError("max_jing 必须为正（LPC 此处会除零）")
        chance = lpc_div(int(victim_jing) * 100, int(victim_max_jing)) + int(victim_int)
        if prng.below(chance) > 50:
            increase = 1 if int(victim_parry_skill_level) < 300 else 0
            return Reward(victim_combat_exp=1, victim_skill_increase=increase)
    return Reward()


def damage_rewards(
    *,
    ap: int,
    dp: int,
    damage: int,
    attacker_is_player: bool,
    victim_is_player: bool,
    attacker_jing: int,
    attacker_max_jing: int,
    attacker_int: int,
    attacker_attack_skill_level: int,
    attacker_potential: int,
    attacker_learned_points: int,
    victim_max_qi: int,
    victim_qi: int,
    victim_potential: int,
    victim_learned_points: int,
    prng: RandomSource,
) -> Reward:
    """`combatd.lpc:575-591` 的命中奖励。

    取数顺序：攻击者（若 ap<dp）先取 `random(jing*30/max_jing + int)`；
    受害者随后无条件取 `random(max_qi + qi)`。
    双方都是玩家时整段跳过，不取任何随机数。

    实测校准（书生场景）：受害者分支真实可见——我方作为受害者被命中期间，
    `combat_exp` 0→6、`potential` 99→105，步进与源码的 `+= 1`、`+= 1` 形态一致
    （6 次触发 / 14 次被命中）。原版不显示 `max_qi`，触发概率
    `random(max_qi+qi) < damage` 只能给方向，未能精确复算（formula-map.md 5.1）。
    """
    if attacker_is_player and victim_is_player:
        return Reward()

    attacker_combat_exp = 0
    attacker_potential_gain = 0
    attacker_skill_increase = 0
    victim_combat_exp = 0
    victim_potential_gain = 0

    if ap < dp:
        if attacker_max_jing <= 0:
            raise ValueError("max_jing 必须为正（LPC 此处会除零）")
        chance = lpc_div(int(attacker_jing) * 30, int(attacker_max_jing)) + int(attacker_int)
        if prng.below(chance) > 30:
            attacker_combat_exp = 1
            if int(attacker_potential) - int(attacker_learned_points) < 100000:
                attacker_potential_gain = 1
            if int(attacker_attack_skill_level) < 300:
                attacker_skill_increase = 1

    if prng.below(int(victim_max_qi) + int(victim_qi)) < int(damage):
        victim_combat_exp = 1
        if int(victim_potential) - int(victim_learned_points) < 100000:
            victim_potential_gain = 1

    return Reward(
        attacker_combat_exp=attacker_combat_exp,
        attacker_potential=attacker_potential_gain,
        victim_combat_exp=victim_combat_exp,
        victim_potential=victim_potential_gain,
        attacker_skill_increase=attacker_skill_increase,
    )
