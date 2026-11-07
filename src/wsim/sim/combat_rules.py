"""`fight` 的数值层：只做对象状态适配，公式全部调用 `tools/xkx_formula`。

分工：
- `xkx_formula` 负责 skill_power、命中/闪避、招架、伤害/护甲/膂力/经验压制、
  扣血、伤势、死亡判定、经验奖励与技能成长（逐条来源见 formula-map.md）；
- 本模块只负责把 `Entity` 映射成库的参数（属性、技能等级、武器、玩家身份等），
  并按库约定的顺序调用，记录每次取数供测试锁序。
- 本模块不保留任何重复公式实现。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLS_DIR = _REPO_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import xkx_formula as xf  # noqa: E402

MAX_ROUNDS = 20
SKILL_MAX = 800
_ATTR_DEFAULTS = {"str": 10, "int": 10, "con": 10, "dex": 10, "sta": 10, "spi": 10}

# ---- 库第 4 节“调用方必须自己算好的东西”：引擎侧当前取值 ----
# 这些参数在 xkx 里依赖技能虚函数、对象临时状态与行动队列；引擎尚无对应建模，
# 一律取源码的中性值（0/False），并在 combat-implementation.md 逐项登记。
ACTION_SEQUENCE = {"dodge": 0, "parry": 0, "force": 0, "damage": 0}  # reset_action()
VICTIM_BUSY = False  # is_busy()
BIXIE_CIZU = False  # query_temp("bixie/cizu")
BIXIE_CIWAN = False  # query_temp("bixie/ciwan")
KILLING = True  # fight 即击杀模式
KONGMING = False  # 空明拳属于守方技能
# force/martial/weapon 的 hit_ob / be_hit_ob 整数增量，按源码顺序。
SKILL_HOOKS = {
    "force_hit": 0,
    "victim_force_hit": 0,
    "martial_hit": 0,
    "victim_parry_hit": 0,
    "weapon_hit": 0,
}


class TraceRandom:
    """把引擎 PRNG 适配成库的 `RandomSource`，并记录 (n, value) 供顺序锁测试。"""

    def __init__(self, rng: Any, trace: list | None = None):
        self.rng = rng
        self.trace = trace
        self.count = 0

    def below(self, n: int) -> int:
        n = int(n)
        self.count += 1
        if n <= 0:
            # 库对 non-positive 上界直接返回 0，不消费引擎随机流；这里如实记录。
            if self.trace is not None:
                self.trace.append((0, 0))
            return 0
        value = self.rng.randint(0, n - 1)
        if self.trace is not None:
            self.trace.append((n, value))
        return value


def _base(ent: Any, name: str) -> int:
    return int((getattr(ent, "attrs", None) or {}).get(name, _ATTR_DEFAULTS.get(name, 10)))


def skill_level(ent: Any, skill: str) -> int:
    """技能等级：新字段优先；旧 0..1 浮点兼容为 int(value * 100)（截断）。"""
    levels = getattr(ent, "skill_levels", None) or {}
    if skill in levels:
        return max(0, min(SKILL_MAX, int(levels[skill])))
    legacy = float((getattr(ent, "skills", None) or {}).get(skill, 0.0))
    return max(0, min(SKILL_MAX, int(legacy * 100)))


def skill_map(ent: Any) -> dict[str, int]:
    """`query_skill(name, 1)` 的近似：合并新等级与旧 0..1 浮点（新字段优先）。"""
    out = {str(k): int(v) for k, v in (getattr(ent, "skill_levels", None) or {}).items()}
    for key, value in (getattr(ent, "skills", None) or {}).items():
        out.setdefault(str(key), int(float(value) * 100))
    return out


def attr(ent: Any, name: str) -> int:
    base = _base(ent, name)
    skills = skill_map(ent)
    if name == "str":
        return xf.query_str(base, 0, skills)
    if name == "dex":
        return xf.query_dex(base, 0, skills)
    if name == "int":
        return xf.query_int(base, 0, skills)
    if name == "spi":
        return xf.query_spi(base, 0, skills)
    if name == "con":
        return xf.query_con(base, 0, skills)
    if name == "sta":
        return xf.query_sta(base, 0, skills)
    return base


def attack_skill(ent: Any, explicit: str | None = None) -> str:
    """`combatd.lpc:268-274`：有武器取武器技能，否则取 prepare，再否则 `unarmed`。

    这里没有建模武器技能与 `prepare_skill`，所以默认固定 `unarmed`。
    注意：`role`（“镖师”“wanderer”等身份/职业）不是武功，绝不能当技能兜底：
    旧实现会让玩家的 `unarmed=40` 被 `skills={"镖师":0.35}` 遮蔽，也让 NPC 用
    职业等级 41 代替 `unarmed`，属于参数映射错误。
    """
    levels = skill_map(ent)
    if explicit and explicit in levels:
        return str(explicit)
    return "unarmed"


def has_weapon(ent: Any) -> bool:
    return bool((getattr(ent, "equipped", None) or {}).get("weapon"))


def power_point_permille(ent: Any, skill: str) -> int:
    """招式威力系数千分位。

    xkx 的 `power_point()` 由具体技能实现（部分还会读对象状态），引擎目前没有
    技能配置文件，只能由内容通过 `attrs["power_point"]` 提供；缺省 1000（1.0），
    不自行编造按等级线性增长的公式。
    """
    return int((getattr(ent, "attrs", None) or {}).get("power_point", 1000))


def _is_player(ent: Any, player_ids: set[str]) -> bool:
    eid = str(getattr(ent, "id", ""))
    return eid.startswith("player:") or eid in player_ids


class _Actor:
    """一次战斗内单个对象的可变副本；只读 Entity，不写 state。"""

    def __init__(self, ent: Any, player_ids: set[str]):
        self.ent = ent
        self.id = str(ent.id)
        self.max_hp = int(getattr(ent, "max_hp", 0))
        self.hp = int(getattr(ent, "hp", 0))
        self.eff_hp = self.max_hp  # 本次战斗从满 eff_qi 开始；跨场伤势未建模
        self.levels = dict(skill_map(ent))
        self.learned = {str(k): int(v) for k, v in (getattr(ent, "skill_xp", None) or {}).items()}
        self.exp = int(getattr(ent, "combat_exp", 0))
        self.is_player = _is_player(ent, player_ids)
        self.curiousness = bool((getattr(ent, "personality", None) or {}).get("curiousness", False))
        self.attack = max(0, int(getattr(ent, "attack", 0)))
        self.defense = max(0, int(getattr(ent, "defense", 0)))
        self.weapon = has_weapon(ent)
        self.spi = attr(ent, "spi")

    def level(self, skill: str) -> int:
        return max(0, min(SKILL_MAX, int(self.levels.get(skill, 0))))

    def learn(self, skill: str, amount: int) -> None:
        progress = xf.improve_skill(
            skill_level=self.level(skill),
            learned=int(self.learned.get(skill, 0)),
            amount=int(amount),
            learned_skill_count=max(1, len(self.levels)),
            spi=self.spi,
            is_player=self.is_player,
            curiousness=self.curiousness,
        )
        if progress.accepted:
            self.levels[skill] = progress.skill_level
            self.learned[skill] = progress.learned


def _attack_power(actor: _Actor, skill: str) -> int:
    return xf.skill_power(
        skill_level=actor.level(skill),
        apply_bonus=0,
        combat_exp=actor.exp,
        attr_effective=attr(actor.ent, "str"),
        attr_base=_base(actor.ent, "str"),
        usage=xf.SKILL_USAGE_ATTACK,
    )


def _defense_power(actor: _Actor, skill: str) -> int:
    return xf.skill_power(
        skill_level=actor.level(skill),
        apply_bonus=0,
        combat_exp=actor.exp,
        attr_effective=attr(actor.ent, "dex"),
        attr_base=_base(actor.ent, "dex"),
        usage=xf.SKILL_USAGE_DEFENSE,
        dodge_power_point_permille=None,
    )


def simulate_fight(
    attacker: Any,
    defender: Any,
    rng: Any,
    *,
    skill: str | None = None,
    defender_skill: str | None = None,
    max_rounds: int = MAX_ROUNDS,
    trace: list | None = None,
    player_ids: set[str] | None = None,
) -> dict[str, Any]:
    """原子结算最多 `max_rounds` 回合；全部数值来自 `xkx_formula`。

    先手按 (dex + dodge 等级)，平手由发起方先手。
    """
    player_ids = player_ids or set()
    me = _Actor(attacker, player_ids)
    victim = _Actor(defender, player_ids)
    if me.max_hp <= 0 or victim.max_hp <= 0:
        raise ValueError("战斗双方必须都有 max_hp")
    skill_a = attack_skill(attacker, skill)
    skill_b = attack_skill(defender, defender_skill)
    prng = TraceRandom(rng, trace)

    speed_a = attr(attacker, "dex") + me.level("dodge")
    speed_b = attr(defender, "dex") + victim.level("dodge")
    first = "a" if speed_a >= speed_b else "b"

    rounds: list[dict[str, Any]] = []
    dead: set[str] = set()
    for round_no in range(1, max(1, int(max_rounds)) + 1):
        actions: list[dict[str, Any]] = []

        def strike(src: _Actor, dst: _Actor, src_skill: str) -> str:
            ap = _attack_power(src, src_skill)
            dp = _defense_power(dst, "dodge")
            entry: dict[str, Any] = {
                "round": round_no,
                "actor": src.id,
                "target": dst.id,
                "skill": src_skill,
            }
            hp_before = dst.hp

            if xf.resolve_dodge(ap=ap, dp=dp, victim_busy=VICTIM_BUSY,
                                dodge_action_bonus=ACTION_SEQUENCE["dodge"],
                                cizu=BIXIE_CIZU, prng=prng):
                reward = xf.dodge_rewards(
                    ap=ap, dp=dp,
                    attacker_is_player=src.is_player, victim_is_player=dst.is_player,
                    attacker_int=attr(src.ent, "int"), attacker_attack_skill_level=src.level(src_skill),
                    victim_jing=max(0, dst.hp), victim_max_jing=dst.max_hp,
                    victim_int=attr(dst.ent, "int"), victim_dodge_skill_level=dst.level("dodge"),
                    prng=prng,
                )
                src.exp += int(reward.attacker_combat_exp)
                if reward.attacker_skill_increase:
                    src.learn(src_skill, reward.attacker_skill_increase)
                dst.exp += int(reward.victim_combat_exp)
                if reward.victim_skill_increase:
                    dst.learn("dodge", reward.victim_skill_increase)
                entry.update({"result": "dodge", "damage": 0,
                              "hp_before": hp_before, "hp_after": dst.hp})
                actions.append(entry)
                return "alive"

            pp = xf.select_parry_power(
                victim_has_weapon=dst.weapon,
                attacker_has_weapon=src.weapon,
                parry_power=_defense_power(dst, "parry"),
                unarmed_power=_defense_power(dst, "unarmed"),
            )
            if xf.resolve_parry(ap=ap, pp=pp, victim_busy=VICTIM_BUSY,
                                parry_action_bonus=ACTION_SEQUENCE["parry"],
                                ciwan=BIXIE_CIWAN, prng=prng):
                reward = xf.parry_rewards(
                    ap=ap, dp=dp,
                    attacker_is_player=src.is_player, victim_is_player=dst.is_player,
                    victim_jing=max(0, dst.hp), victim_max_jing=dst.max_hp,
                    victim_int=attr(dst.ent, "int"),
                    victim_parry_skill_level=dst.level("parry"),
                    prng=prng,
                )
                dst.exp += int(reward.victim_combat_exp)
                if reward.victim_skill_increase:
                    dst.learn("parry", reward.victim_skill_increase)
                entry.update({"result": "parry", "damage": 0,
                              "hp_before": hp_before, "hp_after": dst.hp})
                actions.append(entry)
                return "alive"

            breakdown = xf.resolve_damage(
                raw_damage=src.attack,
                armor=dst.defense,
                str_effective=attr(src.ent, "str"),
                attacker_exp=src.exp,
                defender_exp=dst.exp,
                prng=prng,
                action_damage=ACTION_SEQUENCE["damage"],
                action_force=ACTION_SEQUENCE["force"],
                kongming=KONGMING,
                power_point_permille=power_point_permille(src.ent, src_skill),
                **SKILL_HOOKS,
            )
            new_hp = xf.apply_qi_damage(dst.hp, breakdown.final_damage)
            wounded = xf.roll_wound(
                damage=breakdown.final_damage,
                armor=dst.defense,
                killing=KILLING,
                has_weapon=src.weapon,
                prng=prng,
            )
            new_eff = dst.eff_hp
            if wounded:
                vitals = xf.apply_qi_wound(new_hp, dst.eff_hp, breakdown.final_damage - dst.defense)
                new_hp, new_eff = vitals.qi, vitals.eff_qi

            reward = xf.damage_rewards(
                ap=ap, dp=dp, damage=breakdown.final_damage,
                attacker_is_player=src.is_player, victim_is_player=dst.is_player,
                attacker_jing=max(0, src.hp), attacker_max_jing=src.max_hp,
                attacker_int=attr(src.ent, "int"),
                attacker_attack_skill_level=src.level(src_skill),
                attacker_potential=0, attacker_learned_points=0,
                victim_max_qi=dst.max_hp, victim_qi=max(0, new_hp),
                victim_potential=0, victim_learned_points=0,
                prng=prng,
            )
            src.exp += int(reward.attacker_combat_exp)
            if reward.attacker_skill_increase:
                src.learn(src_skill, reward.attacker_skill_increase)
            dst.exp += int(reward.victim_combat_exp)

            dst.hp = new_hp
            dst.eff_hp = new_eff
            # xkx 的心跳判定按理只把 qi<0 判为昏迷；本引擎的 `fight` 是击杀模式，
            # 没有昏迷计时/苏醒流程，所以 `unconcious` 在动作层视同战斗失败（倒下）。
            state = xf.death_state(
                qi=new_hp, jing=new_hp, eff_qi=new_eff, eff_jing=new_eff, living=True
            )
            down = state != xf.STATE_ALIVE
            entry.update({
                "result": "hit",
                "damage": breakdown.final_damage,
                "wounded": wounded,
                "hp_before": hp_before,
                "hp_after": max(0, new_hp),
                "eff_hp_after": max(0, new_eff),
                "armor_absorbed": breakdown.armor_absorbed,
                "strength_bonus": breakdown.strength_bonus,
                "state": state,
            })
            actions.append(entry)
            return "down" if down else "alive"

        if first == "a":
            outcome = strike(me, victim, skill_a)
            if outcome == "alive":
                outcome = strike(victim, me, skill_b)
        else:
            outcome = strike(victim, me, skill_b)
            if outcome == "alive":
                outcome = strike(me, victim, skill_a)

        rounds.append({"round": round_no, "actions": actions})
        for actor in (me, victim):
            # 气血/有效气血任一为负即视为倒下（库的 death_state 已把 eff<0 判死）。
            if actor.hp < 0 or actor.eff_hp < 0:
                dead.add(actor.id)
        if dead:
            break

    winner = ""
    if victim.id in dead and me.id not in dead:
        winner = "attacker"
    elif me.id in dead and victim.id not in dead:
        winner = "defender"

    skill_updates: list[dict[str, Any]] = []
    for actor in (me, victim):
        for sk in sorted(actor.levels):
            old = skill_level(actor.ent, sk)
            new = actor.level(sk)
            old_learned = int((getattr(actor.ent, "skill_xp", None) or {}).get(sk, 0))
            new_learned = int(actor.learned.get(sk, 0))
            if new != old or new_learned != old_learned:
                skill_updates.append({
                    "entity": actor.id, "skill": sk,
                    "level": new, "learned": new_learned,
                })

    return {
        "skill": skill_a,
        "target_skill": skill_b,
        "rounds": rounds,
        "rounds_used": len(rounds),
        "hp": {me.id: max(0, me.hp), victim.id: max(0, victim.hp)},
        "hp_raw": {me.id: me.hp, victim.id: victim.hp},
        "dead": sorted(dead),
        "winner": winner,
        "combat_exp": {me.id: me.exp - int(getattr(attacker, "combat_exp", 0)),
                       victim.id: victim.exp - int(getattr(defender, "combat_exp", 0))},
        "skill_updates": skill_updates,
        "draws": prng.count,
    }
