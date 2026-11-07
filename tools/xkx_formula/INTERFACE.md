# xkx 战斗公式库 · 动作层接口说明

包路径：`tools/xkx_formula/`。把 `tools` 加入 `sys.path` 后：

```python
from xkx_formula import *
```

公式来源与逐条改写见 `specs/xkx-parity/formula-map.md`。本文件只讲怎么接。

## 1. 硬约束

- 所有函数是纯函数：参数进、结果出；不读对象、不写状态、不碰数据库。
- 所有数值是 `int`；没有浮点尾差。
- 所有随机必须由调用方传入；不使用任何全局随机源。
- `RandomSource.below(n)` 等价 LPC `random(n)`：返回 `[0, n)`；`n <= 0` 返回 0。
- 回放要求：同一个 PRNG 实例、同样的调用顺序，结果完全一致。

```python
class RandomSource(Protocol):
    def below(self, n: int) -> int: ...
```

`SequenceRandom(values)` 是测试/回放用的固定序列实现：记录每次 `below` 的 `n`
到 `.calls`；`n <= 0` 只记录、不消费；序列耗尽或取值越界会抛 `AssertionError`。

## 2. 推荐调用顺序

一次 `do_attack` 按源码顺序调用（PRNG 取数顺序也因此固定）：

```python
# 1. 攻防战力
ap = skill_power(skill_level=attack_skill_level, apply_bonus=apply_attack,
                 combat_exp=me_combat_exp, attr_effective=query_str(...),
                 attr_base=base_str, usage=SKILL_USAGE_ATTACK)
dp = skill_power(skill_level=dodge_level, apply_bonus=apply_defense,
                 combat_exp=victim_combat_exp, attr_effective=query_dex(...),
                 attr_base=base_dex, usage=SKILL_USAGE_DEFENSE,
                 dodge_power_point_permille=dodge_pp_ppm)

# 2. 闪避
dodged = resolve_dodge(ap=ap, dp=dp, victim_busy=victim_busy,
                       dodge_action_bonus=action_dodge_bonus,
                       cizu=victim_has_bixie_cizu, prng=rng)
if dodged:
    reward = dodge_rewards(...)          # 取数 0..2 次
    # 落库后 return
else:
    # 3. 招架
    pp = select_parry_power(victim_has_weapon=..., attacker_has_weapon=...,
                            parry_power=parry_power,
                            unarmed_power=unarmed_power)
    parried = resolve_parry(ap=ap, pp=pp, victim_busy=victim_busy,
                            parry_action_bonus=action_parry_bonus,
                            ciwan=victim_has_bixie_ciwan, prng=rng)
    if parried:
        reward = parry_rewards(...)      # 取数 0..1 次
        # 落库后 return
    else:
        # 4. 伤害分步（内部顺序：武器 -> 护甲 -> 膂力 -> 经验修正 -> 空明 -> 威力系数）
        breakdown = resolve_damage(
            raw_damage=raw_damage, armor=raw_armor,
            str_effective=query_str(...), attacker_exp=me_combat_exp,
            defender_exp=victim_combat_exp, prng=rng,
            action_damage=action_damage,
            force_hit=..., victim_force_hit=...,
            action_force=action_force,
            martial_hit=..., victim_parry_hit=..., weapon_hit=...,
            kongming=victim_has_kongming,
            power_point_permille=attack_power_point_ppm,
        )

        # 5. 扣血
        new_qi = apply_qi_damage(victim_qi, breakdown.final_damage)
        new_eff_qi = victim_eff_qi

        # 6. 伤势（用原始护甲，不是 breakdown.armor_absorbed）
        wounded = roll_wound(damage=breakdown.final_damage, armor=raw_armor,
                             killing=is_killing, has_weapon=attacker_has_weapon,
                             prng=rng)
        if wounded:
            vitals = apply_qi_wound(new_qi, new_eff_qi,
                                    breakdown.final_damage - raw_armor)
            new_qi, new_eff_qi = vitals.qi, vitals.eff_qi

        # 7. 经验奖励
        reward = damage_rewards(..., damage=breakdown.final_damage, prng=rng)

        # 8. 心跳判定（在心跳里做）
        state = death_state(qi=new_qi, jing=victim_jing,
                            eff_qi=new_eff_qi, eff_jing=victim_eff_jing,
                            living=victim_living_and_not_noliving)
```

注意：

- **闪避/招架的随机先取，再判 `cizu`/`ciwan`**；即使必不能躲/招架，也消费一次随机。
- `skill_power` 返回值未夹取到 `[1, 200000000]`；命中/招架内部会夹取。
- `resolve_damage` 的 `final_damage` 就是源码送入 `receive_damage("qi", ...)` 的值。
- `roll_wound` 的 `armor` 参数是 `apply/armor` 原值；`receive_wound` 的扣减量是
  `final_damage - raw_armor`。
- 经验奖励的取数顺序：命中分支先攻击者（若 `ap < dp`）、再受害者（无条件）；
  闪避/招架分支按源码短路规则。

## 3. 函数签名与返回

### 3.1 战力与命中

```python
skill_power(*, skill_level, apply_bonus=0, combat_exp, attr_effective,
            attr_base, usage, dodge_power_point_permille=None, living=True) -> int
```
- `usage`：`SKILL_USAGE_ATTACK`(1) / `SKILL_USAGE_DEFENSE`(2)。
- 攻击：`skill_level`=攻击技能等级，`attr_effective`=`query_str()`，`attr_base`=`query("str")`。
- 防御：`skill_level`=轻功等级，`attr_effective`=`query_dex()`，`attr_base`=`query("dex")`；
  `dodge_power_point_permille=None` 表示无映射轻功。
- 返回原始战力 `int`。

```python
clamp_power(value) -> int
```
夹取到 `[1, 200000000]`。

```python
resolve_dodge(*, ap, dp, victim_busy=False, dodge_action_bonus=0,
              cizu=False, prng) -> bool
```
`True` = 躲避成功（未命中）。

```python
select_parry_power(*, victim_has_weapon, attacker_has_weapon,
                   parry_power, unarmed_power) -> int
resolve_parry(*, ap, pp, victim_busy=False, parry_action_bonus=0,
              ciwan=False, prng) -> bool
```
`True` = 招架成功（未命中）。

### 3.2 伤害

```python
roll_weapon_damage(raw_damage, prng, *, action_damage=0) -> int
roll_armor(armor, prng) -> int
damage_after_armor(damage, armor_absorbed) -> int
roll_strength_bonus(str_effective, prng, *, force_hit=0, victim_force_hit=0,
                    action_force=0, martial_hit=0, victim_parry_hit=0,
                    weapon_hit=0) -> int
modify_damage_by_exp(damage, *, attacker_exp, defender_exp, prng) -> int
kongming_mitigate(damage) -> int
apply_power_point(damage, permille) -> int
```
钩子参数（`force_hit` 等）是已经算好的整数增量，顺序与源码一致。

```python
resolve_damage(*, raw_damage, armor, str_effective, attacker_exp,
               defender_exp, prng, action_damage=0, force_hit=0,
               victim_force_hit=0, action_force=0, martial_hit=0,
               victim_parry_hit=0, weapon_hit=0, kongming=False,
               power_point_permille=0) -> DamageBreakdown
```
`DamageBreakdown`：

| 字段 | 含义 |
|------|------|
| `weapon_damage` | 基础伤害 + 招式 `action["damage"]` 后 |
| `armor_absorbed` | 护甲随机减伤值 |
| `after_armor` | 扣护甲后（>=0） |
| `strength_bonus` | 膂力/钩子/action force 后的加成 |
| `after_exp` | 经验压制修正后、空明/威力系数前 |
| `final_damage` | 最终送入 `receive_damage` 的伤害 |

### 3.3 扣血、死亡、伤势

```python
apply_qi_damage(qi, damage) -> int
apply_qi_wound(qi, eff_qi, damage) -> VitalAfterWound  # .qi, .eff_qi
death_state(*, qi, jing, eff_qi, eff_jing, living=True) -> str
```
`death_state` 返回 `STATE_ALIVE` / `STATE_UNCONCIOUS` / `STATE_DEAD`
（`"alive"` / `"unconcious"` / `"dead"`，拼写与 LPC 原文一致）。

```python
roll_wound(*, damage, armor, killing, has_weapon, prng) -> bool
```

### 3.4 经验与技能

```python
dodge_rewards(*, ap, dp, attacker_is_player, victim_is_player,
              attacker_int, attacker_attack_skill_level,
              victim_jing, victim_max_jing, victim_int,
              victim_dodge_skill_level, prng) -> Reward

parry_rewards(*, ap, dp, attacker_is_player, victim_is_player,
              victim_jing, victim_max_jing, victim_int,
              victim_parry_skill_level, prng) -> Reward

damage_rewards(*, ap, dp, damage, attacker_is_player, victim_is_player,
               attacker_jing, attacker_max_jing, attacker_int,
               attacker_attack_skill_level, attacker_potential,
               attacker_learned_points, victim_max_qi, victim_qi,
               victim_potential, victim_learned_points, prng) -> Reward
```
`Reward`（全部是增量，不写状态）：

| 字段 | 含义 |
|------|------|
| `attacker_combat_exp` | 攻方经验 +1 |
| `attacker_potential` | 攻方潜能 +1 |
| `victim_combat_exp` | 守方经验 +1 |
| `victim_potential` | 守方潜能 +1 |
| `attacker_skill_increase` | 攻方技能增量；闪避分支可能是原始 `random(int)`，0 交给 `improve_skill` 归一 |
| `victim_skill_increase` | 守方技能增量（0 或 1） |

```python
improve_skill(*, skill_level, learned, amount, learned_skill_count,
              spi, is_player, curiousness=False, weak_mode=False) -> SkillProgress
```
`SkillProgress`：`.skill_level`、`.learned`、`.amount`（实际生效增量）、
`.leveled_up`、`.accepted`（`False` 表示源码直接 return，未学习）。

### 3.5 属性

```python
query_str(base_str, apply_strength, skills) -> int
query_int(base_int, apply_intelligence, skills) -> int
query_con(base_con, apply_constitution, skills) -> int
query_dex(base_dex, apply_dexerity, skills) -> int
query_sta(base_sta, apply_stamina, skills) -> int
query_spi(base_spi, apply_spirit, skills) -> int
query_per(base_per, apply_personality) -> int
query_cps(base_cps, apply_composure) -> int
query_cor(base_cor, apply_courage) -> int
query_kar(base_kar, apply_karma) -> int
```
`skills` 是“原始技能等级”的映射（等价 `query_skill(name, 1)`），缺键按 0。

## 4. 调用方必须自己算好的东西

以下依赖对象/全局状态，库内不碰；动作层算好后作为参数传入：

- `ap`/`dp` 的 `skill_level`、`apply/attack|defense`、`combat_exp`、有效/基础属性；
- `action["dodge"|"parry"|"force"|"damage"]`（来自 `reset_action()`）；
- `victim_busy`、`cizu`、`ciwan`、`killing`、`has_weapon`、`kongming`；
- 技能钩子 `hit_ob`/`be_hit_ob` 返回的整数增量；
- 攻击方/守方的 `userp`、`living && !noliving`、技能等级、经验、潜能、已学点数；
- 轻功/攻击招式 `power_point` 的千分位值（`1000` = 1.0，`1500` = 1.5）。

完整不可翻译清单见 `specs/xkx-parity/formula-map.md` 第 4 节。

## 5. 缺失公式提示

xkx `combatd` 没有暴击判定（全仓检索 `暴击`/`critical` 无命中）。本库没有编造暴击公式。
若动作层需要，先补设计（触发条件、倍率、随机取数位置），再补来源与测试。
