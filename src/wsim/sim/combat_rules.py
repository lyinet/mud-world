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
    select_weapon_action,
)

# 原版 `combatd.lpc:728 fight()` 每 heartbeat 只走一次交手，靠
# `feature/attack.lpc:228 attack()` 反复调用，直到一方倒下或被 `halt` 打断，
# **没有任何回合上限**。本引擎一次 `fight` 请求必须自行终止，所以默认不封顶，
# 另留一个远高于真实对局的防挂死硬保护（不参与玩法语义）。
SAFETY_ROUNDS = 10000
SKILL_MAX = 800
_ATTR_DEFAULTS = {"str": 10, "int": 10, "con": 10, "dex": 10, "sta": 10, "spi": 10}
# 原版玩家 cps/cor 由 `logind` 掷点（`race/human.lpc:67-68` 的 `10+random(21)`），
# 不可确定性复刻。复刻玩家创世**不写**这两个键（保持旧世界状态哈希不变），
# 战斗层按原版 MID 快照实测值兜底；仅 `initiative_gate` 打开时会被读取。
PLAYER_MID_CPS = 23
PLAYER_MID_COR = 24

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
            # 与 LPC 驱动 `random(0)` 一致：返回 0 且**不调用引擎 randint**。
            # 计数仍记一次（`draws` 是既有事件字段，关开关时必须逐字节不变）；
            # 是否真的消耗随机流由底层 `rng.calls` 观测。
            if self.trace is not None:
                self.trace.append((0, 0))
            return 0
        value = self.rng.randint(0, n - 1)
        if self.trace is not None:
            self.trace.append((n, value))
        return value


def _base(ent: Any, name: str) -> int:
    return int((getattr(ent, "attrs", None) or {}).get(name, _ATTR_DEFAULTS.get(name, 10)))


def _raw_attr(ent: Any, name: str) -> int:
    """`query("cps")` / `query("cor")` 的原始值：内容没写就是 0，不用属性缺省 10。"""
    return int((getattr(ent, "attrs", None) or {}).get(name, 0) or 0)


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

    武器技能类型来自 `Entity.weapon_type`（物品 `equip_slot`）；没有建模
    `prepare_skill`，所以空手固定 `unarmed`。
    注意：`role`（“镖师”“wanderer”等身份/职业）不是武功，绝不能当技能兜底：
    旧实现会让玩家的 `unarmed=40` 被 `skills={"镖师":0.35}` 遮蔽，也让 NPC 用
    职业等级 41 代替 `unarmed`，属于参数映射错误。
    """
    levels = skill_map(ent)
    if explicit and explicit in levels:
        return str(explicit)
    if has_weapon(ent):
        weapon_type = str(getattr(ent, "weapon_type", "") or "")
        if weapon_type:
            return weapon_type
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

    def __init__(self, ent: Any, player_ids: set[str], eff_qi: bool = False):
        self.ent = ent
        self.id = str(ent.id)
        self.max_hp = int(getattr(ent, "max_hp", 0))
        self.hp = int(getattr(ent, "hp", 0))
        # 原版 eff_qi：跨场携带的伤势天花板；-2 是"旧实体未初始化"，按 max_qi 处理。
        stored_eff = int(getattr(ent, "eff_hp", -2))
        self.eff_hp = stored_eff if eff_qi and stored_eff >= -1 else self.max_hp
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
        # 装备加成（`feature/equip.lpc`）与原版 set_temp 一样是 `apply/*` 临时加成，
        # 逐键相加后只走既有入参，不改公式。
        self.temp_apply: dict[str, int] = {}
        for source in (
            getattr(ent, "temp_apply", None) or {},
            getattr(ent, "equipped_apply", None) or {},
        ):
            for key, value in source.items():
                self.temp_apply[str(key)] = int(self.temp_apply.get(str(key), 0)) + int(value)
        self.apply_attack = int(self.temp_apply.get("apply/attack", 0))
        self.apply_defense = int(self.temp_apply.get("apply/defense", 0))
        self.apply_armor = int(self.temp_apply.get("apply/armor", 0))
        self.apply_armor_vs_force = int(self.temp_apply.get("apply/armor_vs_force", 0))
        self.weapon = has_weapon(ent)
        self.weapon_type = str(getattr(ent, "weapon_type", "") or "") if self.weapon else ""
        self.spi = attr(ent, "spi")
        # 原版 `query_skill_mapped(type)` 与内功发力状态（force.lpc hit_ob）。
        self.map_skills = {
            str(k): str(v) for k, v in (getattr(ent, "map_skills", None) or {}).items()
        }
        self.race = str(getattr(ent, "race", "") or "")
        self.neili = max(0, int(getattr(ent, "neili", 0)))
        self.max_neili = max(0, int(getattr(ent, "max_neili", 0)))
        self.jiali = max(0, int(getattr(ent, "jiali", 0)))
        # `feature/attribute.lpc:92-99`：query_cps/query_cor 都是“原始值 + apply 临时加成”。
        # 内容没写 cps/cor 的走兽必须按 0 处理（`race/beast.lpc` 不掷这两项）。
        # 玩家创世不写这两个键；按 MID 快照 23/24 兜底（见模块顶部常量）。
        attrs = getattr(ent, "attrs", None) or {}
        raw_cps = _raw_attr(ent, "cps")
        raw_cor = _raw_attr(ent, "cor")
        if self.is_player and "cps" not in attrs:
            raw_cps = PLAYER_MID_CPS
        if self.is_player and "cor" not in attrs:
            raw_cor = PLAYER_MID_COR
        self.cps = raw_cps + int(self.temp_apply.get("apply/composure", 0))
        self.cor = raw_cor + int(self.temp_apply.get("apply/courage", 0))
        # `set_temp("guarding", ...)` 的生命周期见 combatd.lpc:694/744/764/784。
        self.guarding = False
        # 原版 `!living(victim)`（qi<0 昏迷）与 `eff_qi<0`（die）是两种状态。
        # 跨场昏迷要带进来，否则补刀不会走 Quick（combatd.lpc:741-747）。
        self.unconscious = str(getattr(ent, "status", "alive")) == "unconcious"
        self.dead = False

    def level(self, skill: str) -> int:
        """原版 `query_skill(skill, 1)` 的 raw 等级（`feature/skill.lpc:98`）。

        用于：improve_skill、奖励阈值（`combatd.lpc:365/421/583` 传 raw=1）、
        招式选择 `query_action()`（技能文件内均用 raw）。
        """
        return max(0, min(SKILL_MAX, int(self.levels.get(skill, 0))))

    def effective(self, skill: str) -> int:
        """原版 `query_skill(skill)` 非 raw 的有效等级。

        `feature/skill.lpc:87-100`：
        `apply/<skill>` + `skills[skill]/2` + `skills[skill_map[skill]]`。
        `skill_power` 的 `skill_level`（`combatd.lpc:204`）、内功
        `force.lpc:13/28` 的 `query_skill("force")` 都走这个口径。
        """
        apply_bonus = int(self.temp_apply.get(f"apply/{skill}", 0))
        mapped = self.mapped(skill)
        mapped_level = self.level(mapped) if mapped else 0
        return apply_bonus + self.level(skill) // 2 + mapped_level

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
        skill_level=actor.effective(skill),
        apply_bonus=actor.apply_attack,
        combat_exp=actor.exp,
        attr_effective=attr(actor.ent, "str"),
        attr_base=_base(actor.ent, "str"),
        usage=xf.SKILL_USAGE_ATTACK,
    )


def _defense_power(actor: _Actor, skill: str) -> int:
    return xf.skill_power(
        skill_level=actor.effective(skill),
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
        if not src.weapon and prng.below(dst.effective("force")) > src.effective("force") // 2:
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
    if prng.below(src.effective("force")) < damage:
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
    max_rounds: int | None = None,
    trace: list | None = None,
    player_ids: set[str] | None = None,
    action_table: dict[str, list[dict[str, Any]]] | None = None,
    hook_table: dict[str, dict[str, Any]] | None = None,
    initiative_gate: bool = False,
    eff_qi: bool = False,
    killing: bool = True,
    spar: bool = False,
) -> dict[str, Any]:
    """原子结算战斗；`max_rounds=None` 表示不封顶（原版语义）。

    `max_rounds` 只在调用方显式给出时生效（例如审计/演练要固定回合数）；
    无论如何都不会超过 `SAFETY_ROUNDS`，避免退化对局把单次请求挂死。
    先手按 (dex + dodge 等级)，平手由发起方先手。每次挥击先按原版
    `query_action()` 选招（走同一个 PRNG），再把招式系数喂给库的既有参数。
    无 `map_skills` 的实体不选招、不掷点，取数与旧实现一致。
    """
    player_ids = player_ids or set()
    actions_by_skill = load_action_table() if action_table is None else action_table
    hooks_by_skill = load_hook_table() if hook_table is None else hook_table
    me = _Actor(attacker, player_ids, eff_qi=eff_qi)
    victim = _Actor(defender, player_ids, eff_qi=eff_qi)
    if me.max_hp <= 0 or victim.max_hp <= 0:
        raise ValueError("战斗双方必须都有 max_hp")
    skill_a = attack_skill(attacker, skill)
    skill_b = attack_skill(defender, defender_skill)
    prng = TraceRandom(rng, trace)

    # 原版没有显式先手比较，靠 MudOS heartbeat 顺序；这里用 dex + query_skill("dodge")
    # 作为引擎自选的近似（无原版行号可引），与 skill_power 同用非 raw 有效值。
    speed_a = attr(attacker, "dex") + me.effective("dodge")
    speed_b = attr(defender, "dex") + victim.effective("dodge")
    first = "a" if speed_a >= speed_b else "b"

    rounds: list[dict[str, Any]] = []
    dead: set[str] = set()
    stopped = False
    stop_winner = ""
    limit = SAFETY_ROUNDS if max_rounds is None else max(1, min(int(max_rounds), SAFETY_ROUNDS))
    for round_no in range(1, limit + 1):
        actions: list[dict[str, Any]] = []

        def strike(
            src: _Actor,
            dst: _Actor,
            src_skill: str,
            *,
            attack_type: str = "regular",
            allow_riposte: bool = False,
            riposte: bool = False,
            gate: dict[str, Any] | None = None,
        ) -> str:
            nonlocal stopped, stop_winner
            ap = _attack_power(src, src_skill)
            dp = _defense_power(dst, "dodge")
            mapped = src.mapped(src_skill)
            skill_actions = actions_by_skill.get(mapped, []) if mapped else []
            if skill_actions:
                move = select_action(skill_actions, src.level(mapped), prng)
                selection = "skill"
            elif not mapped and src.weapon:
                # `feature/attack.lpc:214-224`：没有映射技能时先让武器对象出招
                # （`weapond.lpc query_action()`），武器类型缺失才回落到种族动作。
                move = select_weapon_action(src.weapon_type, prng)
                selection = "weapon" if move else ""
                if move is None:
                    move = select_default_action(src.race, prng)
                    selection = "default" if move else ""
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
            # 仅在出手门开启时登记攻击类型；关闭时事件字段与旧基线逐字节一致。
            # `riposte` 标记反击通道：原版 TYPE_QUICK 同时用于反击与昏迷补刀，
            # 单看 `attack_type` 无法把两者分开，验收需要这个显式字段。
            if initiative_gate:
                entry["attack_type"] = attack_type
                entry["riposte"] = bool(riposte)
                if gate:
                    # 逐回合出手门审计：门骰原始值、判据两侧取值与结论。
                    entry.update(gate)
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
                # `combatd.lpc:691-718`：dodge 的 damage 是 RESULT_DODGE(<1)，
                # 同样满足反击条件；不是只对“命中但 0 伤”触发。
                if allow_riposte and attack_type == "regular" and dst.guarding:
                    _riposte(src, dst, skill_b if dst is victim else skill_a)
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
                if allow_riposte and attack_type == "regular" and dst.guarding:
                    _riposte(src, dst, skill_b if dst is victim else skill_a)
                return "alive"

            # 钩子先算整数增量（force/martial 的随机在此消费），再交给库。
            hooks, force_reflected = _skill_hooks(src, dst, mapped, prng, hooks_by_skill)
            if src.hp < 0:
                src.unconscious = True
            if src.eff_hp < 0:
                src.dead = True
            breakdown = xf.resolve_damage(
                # `combatd.lpc:436-440`：持兵器取 apply/damage，空手取
                # apply/unarmed_damage；`Entity.attack` 是旧内容的等价基础伤害入口。
                raw_damage=src.attack
                + int(src.temp_apply.get("apply/damage" if src.weapon else "apply/unarmed_damage", 0)),
                # 原版 combatd.lpc:436-456 的护甲减伤：基础护甲(内容 defense)
                # + 装备临时 apply/armor；`apply/defense` 属于 skill_power，
                # 不进减伤，也不进破防门（:560 只认 apply/armor）。
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
            # 原版 combatd.lpc:560 只认 apply/armor；apply/defense 只进命中率。
            # 关闭 eff_qi 时沿用旧口径（base defense + apply/armor），保证关闭态与
            # 开启前逐字节一致；开启后才切到原版 `apply/armor` 单口径。
            wound_armor = dst.apply_armor if eff_qi else dst.defense + dst.apply_armor
            wounded = xf.roll_wound(
                damage=breakdown.final_damage,
                armor=wound_armor,
                killing=killing,
                has_weapon=src.weapon,
                prng=prng,
            )
            new_eff = dst.eff_hp
            if wounded:
                vitals = xf.apply_qi_wound(
                    new_hp, dst.eff_hp, breakdown.final_damage - wound_armor
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
            state = xf.death_state(
                qi=new_hp, jing=new_hp, eff_qi=new_eff, eff_jing=new_eff, living=True
            )
            # 原版 `combatd.lpc` 的切磋半血即止：只要这一击造成伤害且守方
            # `qi*2 <= max_qi`，双方罢手；不进入 die()/昏迷判定。
            stop_hit = bool(
                spar
                and breakdown.final_damage > 0
                and dst.max_hp > 0
                and dst.hp * 2 <= dst.max_hp
            )
            if stop_hit:
                stopped = True
                stop_winner = "attacker" if src is me else "defender"
            # 原版 `char.lpc:65-78`：`eff_qi<0` → die()；`qi<0` → unconcious()，
            # 昏迷者在下一次自身心跳死亡，中间攻击方至多获得一次快攻。
            elif not spar:
                if state == xf.STATE_DEAD:
                    dst.dead = True
                elif state == xf.STATE_UNCONCIOUS:
                    dst.unconscious = True
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
            # `combatd.lpc:691-718`：命中但 damage<1（被护甲吃光等）同样触发反击。
            if (allow_riposte and attack_type == "regular"
                    and breakdown.final_damage < 1 and dst.guarding):
                _riposte(src, dst, skill_b if dst is victim else skill_a)
            if stop_hit:
                return "stop"
            return "down" if down else "alive"

        def _riposte(src: _Actor, dst: _Actor, dst_skill: str) -> None:
            """`combatd.lpc:692-718`：守方无条件反击，两分支只切换类型。"""
            dst.guarding = False  # :694
            roll = prng.below(src.cps)  # :695；cps=0 时不取骰
            rtype = "quick" if roll < 5 else "riposte"
            strike(dst, src, dst_skill, attack_type=rtype, allow_riposte=False, riposte=True)

        if not initiative_gate:
            # 旧口径：双方每轮各挥击一次；开关关闭时骰序与行为保持原样。
            if first == "a":
                outcome = strike(me, victim, skill_a)
                if outcome == "alive":
                    outcome = strike(victim, me, skill_b)
            else:
                outcome = strike(victim, me, skill_b)
                if outcome == "alive":
                    outcome = strike(me, victim, skill_a)
        else:
            # `combatd.lpc:735-800`：每个心跳每个 living 各跑一次三分支决策。
            order = (
                ((me, victim, skill_a), (victim, me, skill_b))
                if first == "a"
                else ((victim, me, skill_b), (me, victim, skill_a))
            )
            for actor, target, actor_skill in order:
                if actor.dead or actor.unconscious or target.dead:
                    continue
                if target.unconscious:
                    # `:742-760`：目标已昏迷，攻击方获得一次 `TYPE_QUICK` 补刀；
                    # 目标在下一次自身心跳 `die()`（`char.lpc:72-77`）。
                    # 只有**命中且 qi 重新 <0** 才转死；未命中/被招架则继续昏迷。
                    # 切磋时，面对已昏迷的对手直接收手，不补刀判死。
                    if spar:
                        stopped = True
                        stop_winner = "attacker" if actor is me else "defender"
                        break
                    actor.guarding = False
                    strike(actor, target, actor_skill, attack_type="quick")
                    if target.hp < 0:
                        target.dead = True
                    continue
                # `:763`：主动门。走兽 cps=0 时 random(0) 不取骰。
                gate_n = max(0, int(target.cps * 3))
                gate_roll = prng.below(gate_n)
                gate_pass = gate_roll > actor.cor
                gate: dict[str, Any] | None = None
                if initiative_gate:
                    # 逐回合审计：把门的输入、骰值与结论写进动作行。
                    gate = {
                        "gate_cor": actor.cor,
                        "gate_cps": target.cps,
                        "gate_n": gate_n,
                        "gate_roll": gate_roll,
                        "gate_pass": gate_pass,
                        "gate_guard": not gate_pass,
                    }
                if gate_pass:
                    actor.guarding = False
                    strike(actor, target, actor_skill, attack_type="regular",
                           allow_riposte=True, gate=gate)
                    if stopped:
                        break
                    continue
                # `:783-800`：守卫分支。首次守卫消耗两次 guard_msg 取数；
                # 已守卫则静默 return，不取骰。
                if actor.guarding:
                    actions.append({
                        "round": round_no,
                        "actor": actor.id,
                        "target": target.id,
                        "attack_type": "guard",
                        "result": "guard",
                        "guard_silent": True,
                        "damage": 0,
                        "hp_before": target.hp,
                        "hp_after": target.hp,
                        **(gate or {}),
                    })
                else:
                    actor.guarding = True
                    guard_message = prng.below(5)   # :785 sizeof(guard_msg)=5
                    guard_redirect = prng.below(5)  # :788 转播文案，照取对齐骰流
                    actions.append({
                        "round": round_no,
                        "actor": actor.id,
                        "target": target.id,
                        "attack_type": "guard",
                        "result": "guard",
                        "guard_silent": False,
                        "guard_message": guard_message,
                        "guard_redirect": guard_redirect,
                        "damage": 0,
                        "hp_before": target.hp,
                        "hp_after": target.hp,
                        **(gate or {}),
                    })

        rounds.append({"round": round_no, "actions": actions})
        if spar:
            # 切磋永不判死；命中半血即止时立即结束。
            if stopped:
                break
            continue
        for actor in (me, victim):
            if eff_qi or initiative_gate:
                # 只认 `die()`（eff<0）与显式补刀；`qi<0` 是昏迷，不是死亡。
                if actor.dead or actor.eff_hp < 0:
                    dead.add(actor.id)
            else:
                # 旧口径：气血/有效气血任一为负即视为倒下。
                if actor.hp < 0 or actor.eff_hp < 0:
                    dead.add(actor.id)
        if (eff_qi or initiative_gate) and me.unconscious and victim.unconscious:
            # 双方同时昏迷：各自下个心跳都会 die()，本引擎直接判同归于尽。
            dead.update((me.id, victim.id))
        if dead:
            break

    winner = stop_winner if stopped else ""
    if not stopped and victim.id in dead and me.id not in dead:
        winner = "attacker"
    elif not stopped and me.id in dead and victim.id not in dead:
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
        "eff_hp_raw": {me.id: me.eff_hp, victim.id: victim.eff_hp},
        "unconscious": sorted({
            actor.id for actor in (me, victim)
            if actor.unconscious and not actor.dead and actor.eff_hp >= 0
        }),
        "dead": sorted(dead),
        "winner": winner,
        "combat_exp": {me.id: me.exp - int(getattr(attacker, "combat_exp", 0)),
                       victim.id: victim.exp - int(getattr(defender, "combat_exp", 0))},
        "skill_updates": skill_updates,
        "draws": prng.count,
        "spar": bool(spar),
        "killing": bool(killing),
        "stopped": bool(stopped),
    }
