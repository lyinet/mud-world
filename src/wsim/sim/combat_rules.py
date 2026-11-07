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

from .skill_actions import (  # noqa: E402  (与库同级的内部模块)
    coefficient,
    has_code_hook,
    load_action_table,
    load_hook_table,
    mapped_skill,
    select_action,
    select_default_action,
)

MAX_ROUNDS = 20
SKILL_MAX = 800
_ATTR_DEFAULTS = {"str": 10, "int": 10, "con": 10, "dex": 10, "sta": 10, "spi": 10}

# ---- 库第 4 节“调用方必须自己算好的东西”：引擎侧当前取值 ----
# 这些参数在 xkx 里依赖技能虚函数、对象临时状态与行动队列；引擎尚无对应建模，
# 一律取源码的中性值（0/False），并在 combat-implementation.md 逐项登记。
#
# 招式系数不再固定 0：每次挥击按 `query_action()` 选招后，把
# `action={dodge,parry,force,damage}` 喂给库的既有参数。无映射技能的实体
# 仍走全 0 路径，取数与旧实现一致。
VICTIM_BUSY = False  # is_busy()
BIXIE_CIZU = False  # query_temp("bixie/cizu")
BIXIE_CIWAN = False  # query_temp("bixie/ciwan")
KILLING = True  # fight 即击杀模式
KONGMING = False  # 空明拳属于守方技能
# 只保留三项“原版没有可调用实现”的钩子；force_hit / martial_hit 由
# `_skill_hooks()` 按技能分派（见 force.lpc hit_ob 与 mantian-xing/yunlong-zhua）。
UNMODELED_HOOKS = {
    "victim_force_hit": 0,  # force.lpc be_hit_ob 整段被注释
    "victim_parry_hit": 0,  # 招架类 be_hit_ob 同样无实现
    "weapon_hit": 0,  # 由武器对象 hit_ob 提供，技能表不可得
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
        # `set_temp("apply/...")`：内容里 known=true 的整数项。语义按源码：
        # - apply/attack / apply/defense 进入 skill_power 的 level（再 800 封顶）；
        # - apply/armor 作为护甲减伤值；
        # - apply/damage 只有持兵器时才是基础伤害，空手用 apply/unarmed_damage。
        self.temp_apply = {
            str(k): int(v) for k, v in (getattr(ent, "temp_apply", None) or {}).items()
        }
        self.apply_attack = int(self.temp_apply.get("apply/attack", 0))
        self.apply_defense = int(self.temp_apply.get("apply/defense", 0))
        self.apply_armor = int(self.temp_apply.get("apply/armor", 0))
        self.apply_armor_vs_force = int(self.temp_apply.get("apply/armor_vs_force", 0))
        self.weapon = has_weapon(ent)
        self.spi = attr(ent, "spi")
        # 原版 `query_skill_mapped(type)` 与内功发力状态（force.lpc hit_ob）。
        self.map_skills = {
            str(k): str(v) for k, v in (getattr(ent, "map_skills", None) or {}).items()
        }
        self.race = str(getattr(ent, "race", "") or "")
        self.neili = max(0, int(getattr(ent, "neili", 0)))
        self.max_neili = max(0, int(getattr(ent, "max_neili", 0)))
        self.jiali = max(0, int(getattr(ent, "jiali", 0)))

    def level(self, skill: str) -> int:
        return max(0, min(SKILL_MAX, int(self.levels.get(skill, 0))))

    def mapped(self, base_skill: str) -> str:
        value = self.map_skills.get(str(base_skill))
        return str(value) if value else ""

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
        apply_bonus=actor.apply_attack,
        combat_exp=actor.exp,
        attr_effective=attr(actor.ent, "str"),
        attr_base=_base(actor.ent, "str"),
        usage=xf.SKILL_USAGE_ATTACK,
    )


def _defense_power(actor: _Actor, skill: str) -> int:
    return xf.skill_power(
        skill_level=actor.level(skill),
        apply_bonus=actor.apply_defense,
        combat_exp=actor.exp,
        attr_effective=attr(actor.ent, "dex"),
        attr_base=_base(actor.ent, "dex"),
        usage=xf.SKILL_USAGE_DEFENSE,
        dodge_power_point_permille=None,
    )


def _force_hit(
    src: _Actor,
    dst: _Actor,
    damage_bonus: int,
    prng: Any,
    hook_table: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    """`inherit/skill/force.lpc:5-30 hit_ob` 的整数增量与反震气血。

    只有同时满足原版入口条件才执行：
    `my["jiali"] && my["neili"] > my["jiali"] && 映射内功有 code 钩子`。
    返回值 `(damage_bonus 增量, 反震对攻击者造成的 qi 伤害)`。
    字符串返回分支（反震）对 damage_bonus 的增量是 0，但攻击者会真的掉血。
    """
    force_skill = src.mapped("force")
    if src.jiali <= 0 or src.neili <= src.jiali:
        return 0, 0
    hooks = hook_table.get(force_skill) if force_skill else None
    if not has_code_hook(hooks, "force_hit"):
        return 0, 0

    factor = src.jiali
    src.neili -= factor
    # LPC 的 `/` 在非负操作数上就是整除。
    damage = src.neili // 20 + factor - dst.neili // 25

    if damage < 0:
        if not src.weapon and prng.below(dst.level("force")) > src.level("force") // 2:
            damage = -damage
            src.hp = xf.apply_qi_damage(src.hp, damage * 2)
            vitals = xf.apply_qi_wound(src.hp, src.eff_hp, damage)
            src.hp, src.eff_hp = vitals.qi, vitals.eff_qi
            return 0, damage * 2
        if damage_bonus + damage < 0:
            return -damage_bonus, 0
        return damage, 0

    damage -= dst.apply_armor_vs_force
    if damage_bonus + damage < 0:
        return -damage_bonus, 0
    if prng.below(src.level("force")) < damage:
        return damage, 0
    return 0, 0


def _martial_hit(
    src: _Actor,
    dst: _Actor,
    skill: str,
    prng: Any,
    hook_table: dict[str, dict[str, Any]],
) -> int:
    """`martian-xing` / `yunlong-zhua hit_ob`：只上毒，整数增量恒 0。

    毒（`apply_condition`）未建模；这里按原文消费随机数，保证取数流一致：
    - mantian-xing：`random(skill) > 50` → `random(2)`；
    - yunlong-zhua：`yunlong-shengong > 70` → `random(skill) > 30` → `random(ningxue/10)`。
    """
    if not skill:
        return 0
    hooks = hook_table.get(skill)
    if not has_code_hook(hooks, "martial_hit"):
        return 0
    if skill == "mantian-xing":
        if prng.below(src.level("mantian-xing")) > 50:
            prng.below(2)
    elif skill == "yunlong-zhua":
        if src.level("yunlong-shengong") > 70:
            if prng.below(src.level("yunlong-zhua")) > 30:
                prng.below(src.level("ningxue-shenzhua") // 10)
    return 0


def _skill_hooks(
    src: _Actor,
    dst: _Actor,
    martial_skill: str,
    prng: Any,
    hook_table: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], int]:
    """按源码顺序求五项钩子的整数增量；后三项保持 0（见 `UNMODELED_HOOKS`）。"""
    force_delta, reflected = _force_hit(
        src, dst, attr(src.ent, "str"), prng, hook_table
    )
    martial_delta = _martial_hit(src, dst, martial_skill, prng, hook_table)
    hooks = dict(UNMODELED_HOOKS)
    hooks["force_hit"] = force_delta
    hooks["martial_hit"] = martial_delta
    return hooks, reflected


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
    action_table: dict[str, list[dict[str, Any]]] | None = None,
    hook_table: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """原子结算最多 `max_rounds` 回合；全部数值来自 `xkx_formula`。

    先手按 (dex + dodge 等级)，平手由发起方先手。每次挥击先按原版
    `query_action()` 选招（走同一个 PRNG），再把招式系数喂给库的既有参数。
    无 `map_skills` 的实体不选招、不掷点，取数与旧实现一致。
    """
    player_ids = player_ids or set()
    actions_by_skill = load_action_table() if action_table is None else action_table
    hooks_by_skill = load_hook_table() if hook_table is None else hook_table
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
            mapped = src.mapped(src_skill)
            skill_actions = actions_by_skill.get(mapped, []) if mapped else []
            if skill_actions:
                move = select_action(skill_actions, src.level(mapped), prng)
                selection = "skill"
            else:
                # 原版 `reset_action()` 无映射技能时回落到种族 `combat_action`。
                move = select_default_action(src.race, prng)
                selection = "default" if move else ""
            entry: dict[str, Any] = {
                "round": round_no,
                "actor": src.id,
                "target": dst.id,
                "skill": src_skill,
                "action_skill": mapped,
                "selection": selection,
                "move": str(move["name"]) if move else "",
                "move_text": str(move["text"]) if move else "",
                "move_index": int(move["index"]) if move else -1,
            }
            hp_before = dst.hp

            if xf.resolve_dodge(ap=ap, dp=dp, victim_busy=VICTIM_BUSY,
                                dodge_action_bonus=coefficient(move, "dodge"),
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
                                parry_action_bonus=coefficient(move, "parry"),
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

            # 钩子先算整数增量（force/martial 的随机在此消费），再交给库。
            hooks, force_reflected = _skill_hooks(src, dst, mapped, prng, hooks_by_skill)
            breakdown = xf.resolve_damage(
                # `combatd.lpc:436-440`：持兵器取 apply/damage，空手取
                # apply/unarmed_damage；`Entity.attack` 是旧内容的等价基础伤害入口。
                raw_damage=src.attack
                + int(src.temp_apply.get("apply/damage" if src.weapon else "apply/unarmed_damage", 0)),
                armor=dst.defense + dst.apply_armor,
                str_effective=attr(src.ent, "str"),
                attacker_exp=src.exp,
                defender_exp=dst.exp,
                prng=prng,
                action_damage=coefficient(move, "damage"),
                action_force=coefficient(move, "force"),
                kongming=KONGMING,
                power_point_permille=power_point_permille(src.ent, src_skill),
                **hooks,
            )
            new_hp = xf.apply_qi_damage(dst.hp, breakdown.final_damage)
            wounded = xf.roll_wound(
                damage=breakdown.final_damage,
                armor=dst.defense + dst.apply_armor,
                killing=KILLING,
                has_weapon=src.weapon,
                prng=prng,
            )
            new_eff = dst.eff_hp
            if wounded:
                vitals = xf.apply_qi_wound(
                    new_hp, dst.eff_hp, breakdown.final_damage - (dst.defense + dst.apply_armor)
                )
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
                "force_hit": hooks["force_hit"],
                "martial_hit": hooks["martial_hit"],
                "force_reflected": force_reflected,
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
