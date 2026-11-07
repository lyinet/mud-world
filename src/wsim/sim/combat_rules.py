"""xkx 战斗规则（P2）：combatd.lpc 的整数化翻译。

来源与行号（`adm/daemons/combatd.lpc`，本地 fluffos-xkx100）：
- `skill_power()` 197–242；
- 命中/闪避 328–354；
- 招架 387–406；
- 伤害 436–558；
- 破防/受伤 560–591。

约束：
- 全部整数运算；唯一随机源是调用方传入的 `PRNG(seed, seq, salt="action:fight")`。
- 取数顺序固定：dodge → parry → damage_cap → damage_roll → armor_cap → armor_roll
  → str_bonus → exp_check → wound；`trace` 参数把每一次取数记下来，供测试锁序。
- 本模块不做任何状态写入，只返回纯数据；Effect 由动作层生成。
"""
from __future__ import annotations

from typing import Any

MAX_ROUNDS = 20
SKILL_MAX = 800
ATTR_MIN = 10
ATTR_MAX = 200
POWER_CAP = 200000000
_ATTR_DEFAULTS = {"str": 10, "int": 10, "con": 10, "dex": 10, "sta": 10}


def attr(ent: Any, name: str) -> int:
    """取属性；旧实体没有 attrs 时按 xkx 下界 10 计算。"""
    value = int((getattr(ent, "attrs", None) or {}).get(name, _ATTR_DEFAULTS.get(name, 10)))
    if name in ("str", "dex"):
        return max(ATTR_MIN, min(ATTR_MAX, value))
    return max(0, value)


def skill_level(ent: Any, skill: str) -> int:
    """技能等级：新字段优先；旧 0..1 浮点兼容为 int(value * 100)（截断，不四舍五入）。"""
    levels = getattr(ent, "skill_levels", None) or {}
    if skill in levels:
        return max(0, min(SKILL_MAX, int(levels[skill])))
    legacy = float((getattr(ent, "skills", None) or {}).get(skill, 0.0))
    return max(0, min(SKILL_MAX, int(legacy * 100)))


def attack_skill(ent: Any, explicit: str | None = None) -> str:
    """出手技能：显式参数优先，其次角色本职，最后拳脚。"""
    levels = getattr(ent, "skill_levels", None) or {}
    legacy = getattr(ent, "skills", None) or {}
    if explicit and (explicit in levels or explicit in legacy):
        return str(explicit)
    role = str(getattr(ent, "role", "") or "")
    if role and (role in levels or role in legacy):
        return role
    return "unarmed"


def has_weapon(ent: Any) -> bool:
    return bool((getattr(ent, "equipped", None) or {}).get("weapon"))


def power_point(ent: Any, skill: str) -> int:
    """轻功/武功威力系数，千分位整数 1000..1500（对应 LPC 1.0..1.5）。"""
    return 1000 + min(500, skill_level(ent, skill) * 500 // SKILL_MAX)


def skill_power(ent: Any, skill: str, usage: str) -> int:
    """combatd.lpc:197-242 的整数版。usage: attack / defense。"""
    level = skill_level(ent, skill)
    if usage == "attack":
        pass
    elif skill == "dodge":
        level = level * power_point(ent, skill) // 1000
    if level == 0:
        return int(getattr(ent, "combat_exp", 0) // 2)
    level = max(1, min(SKILL_MAX, level))
    if usage == "attack":
        value = attr(ent, "str")
    else:
        value = attr(ent, "dex")
    exp = int(getattr(ent, "combat_exp", 0))
    power = (level // 6) * level * level
    out = (power + exp) // 80 * (value + value)
    return max(1, min(POWER_CAP, out))


def power(ent: Any, skill: str, usage: str) -> int:
    """对外别名：攻击/防御威力（已 clamp 到 1..2e8）。"""
    return max(1, min(POWER_CAP, skill_power(ent, skill, usage)))


def below(rng: Any, n: int, trace: list | None = None, label: str = "") -> int:
    """LPC random(n)：返回 0..n-1。n<=0 不消耗随机数；n==1 消耗一次并返回 0。"""
    if n <= 0:
        if trace is not None:
            trace.append((label, 0))
        return 0
    if trace is not None:
        trace.append((label, n))
    return rng.randint(0, n - 1)


def _action(attacker, defender, skill: str, rng, trace: list | None, round_no: int) -> dict[str, Any]:
    """一次出手：闪避 → 招架 → 伤害/护甲/膂力/经验 → 受伤。"""
    ap = power(attacker, skill, "attack")
    dp = power(defender, "dodge", "defense")

    entry: dict[str, Any] = {
        "round": round_no,
        "actor": str(attacker.id),
        "target": str(defender.id),
        "skill": skill,
    }

    if below(rng, ap + dp, trace, "dodge") < dp:
        entry.update({"result": "dodge", "damage": 0})
        return entry

    if has_weapon(defender):
        pp = power(defender, "parry", "defense")
        if not has_weapon(attacker):
            pp *= 2
    elif has_weapon(attacker):
        pp = 0
    else:
        pp = power(defender, "unarmed", "defense")
    pp = max(1, min(POWER_CAP, pp))
    if below(rng, ap + pp, trace, "parry") < pp:
        entry.update({"result": "parry", "damage": 0})
        return entry

    damage = max(1, int(getattr(attacker, "attack", 0)))
    if damage > 300:
        damage = 300 + below(rng, damage - 300, trace, "damage_cap")
    damage = (damage + below(rng, damage, trace, "damage_roll")) // 2

    armor = max(0, int(getattr(defender, "defense", 0)))
    armor_factor = 300 + below(rng, armor - 300, trace, "armor_cap") if armor > 300 else armor
    armor_factor = (armor_factor + below(rng, armor_factor, trace, "armor_roll")) // 2
    damage -= armor_factor
    if damage < 0:
        damage = 0

    strv = attr(attacker, "str")
    bonus = (strv + below(rng, strv, trace, "str_bonus")) // 2 if strv > 0 else 0
    damage += bonus
    if damage < 0:
        damage = 0

    defense_exp = int(getattr(defender, "combat_exp", 0))
    my_exp = int(getattr(attacker, "combat_exp", 0))
    while defense_exp > 0:
        if below(rng, defense_exp, trace, "exp_check") <= my_exp:
            break
        damage -= damage // 3
        defense_exp //= 2
    if damage < 0:
        damage = 0

    wounded = False
    if damage > 0:
        wounded = below(rng, damage, trace, "wound") > armor

    damage = damage * power_point(attacker, skill) // 1000
    entry.update({"result": "hit", "damage": damage, "wounded": wounded})
    return entry


def simulate_fight(
    attacker: Any,
    defender: Any,
    rng: Any,
    *,
    skill: str | None = None,
    defender_skill: str | None = None,
    max_rounds: int = MAX_ROUNDS,
    trace: list | None = None,
) -> dict[str, Any]:
    """原子结算最多 `max_rounds` 回合；只读实体，不写状态。

    先手按 (dex + dodge 等级) 排序，平手由发起方（attacker）先手。
    返回：rounds[]、双方剩余 hp、死亡列表、winner（"attacker"/"defender"/""）。
    """
    if int(getattr(attacker, "max_hp", 0)) <= 0 or int(getattr(defender, "max_hp", 0)) <= 0:
        raise ValueError("战斗双方必须都有 max_hp")
    skill_a = attack_skill(attacker, skill)
    skill_b = attack_skill(defender, defender_skill)

    hp_a = int(getattr(attacker, "hp", 0))
    hp_b = int(getattr(defender, "hp", 0))
    speed_a = attr(attacker, "dex") + skill_level(attacker, "dodge")
    speed_b = attr(defender, "dex") + skill_level(defender, "dodge")
    first_is_a = speed_a >= speed_b

    rounds: list[dict[str, Any]] = []
    for round_no in range(1, max(1, int(max_rounds)) + 1):
        actions: list[dict[str, Any]] = []

        def strike(src, dst, src_hp: int, dst_hp: int, src_skill: str) -> tuple[int, int]:
            entry = _action(src, dst, src_skill, rng, trace, round_no)
            before = dst_hp
            if entry["result"] == "hit":
                dst_hp = max(0, dst_hp - int(entry["damage"]))
            entry.update({"hp_before": before, "hp_after": dst_hp})
            actions.append(entry)
            return src_hp, dst_hp

        if first_is_a:
            hp_a, hp_b = strike(attacker, defender, hp_a, hp_b, skill_a)
            if hp_b > 0:
                hp_b, hp_a = strike(defender, attacker, hp_b, hp_a, skill_b)
        else:
            hp_b, hp_a = strike(defender, attacker, hp_b, hp_a, skill_b)
            if hp_a > 0:
                hp_a, hp_b = strike(attacker, defender, hp_a, hp_b, skill_a)

        rounds.append({"round": round_no, "actions": actions})
        if hp_a <= 0 or hp_b <= 0:
            break

    winner = ""
    if hp_b <= 0 < hp_a:
        winner = "attacker"
    elif hp_a <= 0 < hp_b:
        winner = "defender"
    return {
        "rounds": rounds,
        "rounds_used": len(rounds),
        "hp": {str(attacker.id): max(0, hp_a), str(defender.id): max(0, hp_b)},
        "dead": [str(e.id) for e, hp in ((attacker, hp_a), (defender, hp_b)) if hp <= 0],
        "winner": winner,
    }
