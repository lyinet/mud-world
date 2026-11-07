# 招式序列与技能钩子接入战报（xkx parity）

状态：待批准 → 批准后执行。
分支：`xkx-parity`（禁止推 `master`）。仓库：`/Users/lyinet/projects/XKX/world-sim`。

## 1. 目标

把内容侧已抽出的招式数据接进 `fight` 的数值结算与战报，同时保持数值手感不变：

1. 每次挥击按原版 `reset_action()` / `query_action()` 语义选一条普通招式，随机走引擎 PRNG，取数顺序写进测试；
2. 战报 `rounds[].actions[]` 与文字壳都能显示“谁使出一招「招式名」”；
3. `SKILL_HOOKS` 五项从固定 0 改成按技能分派：`force_hit`（6 个有 code）与 `martial_hit`（2 个）接入，其余三项保持 0 并在 `combat-implementation.md` 写明原因；
4. 126 个 `default` 系数按 LPC mapping 语义取 0，缺键也是 0，战报不崩、不出现异常显示；
5. 不引入任何新伤害来源：只把原版已有的 `action={dodge,parry,force,damage}` 系数和 `hit_ob` 整数增量喂给 `tools/xkx_formula` 的既有参数。

## 2. 已确认的源数据（不重复读取）

`content/xkx/skills_actions.json`（`skills_actions` 顶层键）：

- 48 个技能；22 个有普通招式，共 247 条；`special_actions` 4 条（taiji-jian 3、taiji-quan 1）。
- 每条普通招式字段：`index`、`name`、`text`、`skill_level_required`、`coefficients{dodge,parry,force,damage}`、`coefficient_source{...}`。
- `coefficient_source == "default"` 的系数共 **126** 个（damage 92、parry 21、force 13），值全部已经是 `0`；所有动作的四个键都齐全。
- `special_actions` 是 `perform` 分支（带 `requirements/neili/cooldown`），**不属于 `reset_action()`**，本任务不自动触发。

`content/xkx/skills_stats.json`（`skills_stats` 顶层键）的 `hooks`：

| 钩子 | 有 code 的技能 | 结论 |
|---|---|---|
| `force_hit` | bibo-shengong、force、huntian-qigong、hunyuan-yiqi、taiji-shengong、yunlong-shengong | 同一份 `inherit/skill/force.lpc hit_ob` |
| `martial_hit` | mantian-xing、yunlong-zhua | 只上毒 `apply_condition(...)`，整数增量为 0 |
| `victim_force_hit` | 无（`force.lpc be_hit_ob` 被注释） | 固定 0 |
| `victim_parry_hit` | 无 | 固定 0 |
| `weapon_hit` | 无（由武器对象提供，技能表不可得） | 固定 0 |

`content/xkx/npcs.json` 已带 `map_skills`（基础技能→具体武功）、`prepare_skills`、
`vitals{max_qi,neili,max_neili,jiali,combat_exp,...}`、`temp_apply`。引擎 Entity 目前
只落 `max_hp/hp/attrs/combat_exp/skill_levels`，缺 `map_skills/prepare_skills/neili/jiali`。

## 3. 原版语义（实现依据）

`feature/attack.lpc:200-225` `reset_action()`：只决定“用哪个技能 daemon 的
`query_action`”，本身不掷点；`combatd.lpc:280-295` 每回合调用一次取 `action`。

具体技能 `query_action()`（以 `bixie-jian.lpc:86-111` 为准）：

```
ttl = sizeof(action)
seq = 0
for (i = ttl; i > 0; i--)
  if (lvl > action[i-1]["lvl"]) { seq = i; break; }
seq = random(seq)
return action[seq]  // seq=0 时 random(0)=0，仍取 action[0]
```

系数使用位置（`combatd.lpc`）：

- `action["dodge"]` → `dp = (100 + d)*dp/100`（`dp>1000000` 走另一分支），然后 clamps；
- `action["parry"]` → `pp = (100 + p)*pp/100`；
- `action["damage"]` → `damage += action["damage"]*damage/200`；
- `action["force"]` → `damage_bonus += action["force"]*damage_bonus/200`。

这些分支 `tools/xkx_formula` **已经实现**，参数名：
`dodge_action_bonus`、`parry_action_bonus`、`action_damage`、`action_force`。
本任务只负责“选哪条 action、把系数传进去”。

钩子顺序（`combatd.lpc:465-513`）：

```
damage_bonus = me->query_str()
force hit_ob            (my["jiali"] && my["neili"] > my["jiali"])
victim force be_hit_ob  (未实现，0)
action["force"] 修正
martial hit_ob
victim parry be_hit_ob  (未实现，0)
weapon / me hit_ob      (未实现，0)
random(damage_bonus) 掷膂力
```

`force.lpc:5-30` 原文：

```
me->add("neili", -factor);
damage = me->query("neili")/20 + factor - victim->query("neili")/25;
if (damage < 0) {
  if (!me->query_temp("weapon")
      && random(victim->query_skill("force")) > me->query_skill("force")/2) {
    damage = -damage;
    me->receive_damage("qi", damage*2);
    me->receive_wound("qi", damage);
    return <string>;              // 字符串 → damage_bonus 增量为 0
  }
  if (damage_bonus + damage < 0) return -damage_bonus;
  return damage;
}
damage -= victim->query_temp("apply/armor_vs_force");
if (damage_bonus + damage < 0) return -damage_bonus;
if (random(me->query_skill("force")) < damage) return damage;
```

`martial_hit`（mantian-xing）：`if (random(skill) > 50) apply_condition("anqi_poison", ...)`；
（yunlong-zhua）：`if force>70 { if (random(skill) > 30) apply_condition("zhua_poison", ...) }`。
两者都无返回值 → 整数增量 0；毒/condition 系统未建模，只保留随机取数以保证流一致。

## 4. 设计

### 4.1 新模块 `src/wsim/sim/skill_actions.py`

纯只读、无墙钟、无全局可变随机：

- `load_action_table(content_dir: Path | None = None) -> dict[str, list[dict]]`
  - 默认 `_REPO_ROOT/content/xkx/skills_actions.json`；解析失败/文件缺失返回 `{}`。
  - 每条动作标准化为：
    `{index:int, name:str, text:str, level:int, coeff:{dodge:int,parry:int,force:int,damage:int}}`。
  - `coeff` 读取规则：`int(coefficients.get(key, 0))`，**缺键与 default 都是 0**。
  - 只收 `actions` 非空的技能。
  - 模块级 `@lru_cache` 缓存；提供 `clear_cache()` 供测试。
- `load_hook_table(content_dir: Path | None = None) -> dict[str, dict]`
  - 默认读 `content/xkx/skills_stats.json`；返回 `skill_id -> hooks`。
- `select_action(actions, level, prng) -> dict | None`
  - 严格按上文 `query_action` 语义；`actions` 为空返回 `None` 且**不掷点**。
  - `seq <= 0` 时调用 `prng.below(0)`（记录一次 `(0,0)`，不消费引擎流）并取第 0 条，忠实原版 `random(0)=0`。
- `mapped_skill(entity, base_skill) -> str`
  - 优先 `entity.map_skills[base_skill]`；否则 `""`。
  - 不猜、不用 `role` 兜底。
- `has_code_hook(hooks, name) -> bool`
  - 只认 `value_kind == "code"` 且 `implemented is True`。

### 4.2 Entity 扩展（`src/wsim/world/state.py`）

新增带默认值字段：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `map_skills` | `dict[str,str]` | `{}` | 基础技能→具体武功（原版 `query_skill_mapped`） |
| `prepare_skills` | `list[str]` | `[]` | 原版 `query_skill_prepare` 的键（保留，供后续） |
| `neili` | `int` | `0` | 当前内力（`force_hit` 用） |
| `max_neili` | `int` | `0` | 内力上限 |
| `jiali` | `int` | `0` | 加力（`force_hit` 的 `factor`） |
| `race` | `str` | `""` | 原版 `set("race", ...)`；无映射技能时取种族 `combat_action` |

`to_dict()` 只在非空/非零时输出；旧九地点内容不提供这些键 → 哈希逐字节不变。

`src/wsim/world/effects.py` 的 `create_entity` / `vitals_set` 分支接受并落这些键（缺省不写）。

`src/wsim/sim/vitals.py::resolve_npc_vitals` 增补：

- `map_skills`（str→str）、`prepare_skills`（str 列表）；
- `neili/max_neili/jiali` 来自 `vitals`，缺省 0；
- `race` 推断：内容包没有原版 `set("race", ...)` 字段，按 NPC id 末段（老虎/马/蛇等）
  与 `gender`（男性/女性/无性）做最小推断；未列出的非人类按走兽处理。只影响默认招式文案，
  不参与任何数值公式。

### 4.3 战斗结算（`src/wsim/sim/combat_rules.py`）

- 删除模块级固定 `ACTION_SEQUENCE`/`SKILL_HOOKS` 常量语义，改为：
  - `_Actor` 读 `map_skills/prepare_skills/neili/max_neili/jiali`；
  - `_Actor.mapped_skill(base)`；
  - `force_skill = map_skills.get("force","")`；`martial_skill = mapped_skill(src_skill)`；
  - `apply_armor_vs_force = temp_apply.get("apply/armor_vs_force", 0)`。
- `simulate_fight(..., action_table=None, hook_table=None)`：
  - 默认 `None` 时调用 `load_action_table()/load_hook_table()`（只读缓存）。
  - 每次 `strike()` 在第一笔随机之前选招：
    `action = select_action(action_table.get(src_mapped, []), src.level(src_mapped), prng)`。
  - 选招结果写进 `entry`：`move`（name）、`move_text`（text）、`move_index`、`action_skill`、
    `selection`（`"skill"` / `"default"` / `""`，用于区别“映射招式”和“种族兜底”）。
  - 无映射技能时按原版 `reset_action()` 回落种族 `combat_action`：
    `select_default_action(race, prng)`（人类 5 条全 0；走兽 3 条 `damage` 50/30/30）。
    这是原版 `race/*.lpc::combat_action` 的既有分支，不是新增伤害来源。
  - 只有连种族默认招式都没有（`race` 为空）时才不掷点。
- 系数只走既有参数：
  - `dodge_action_bonus = coeff["dodge"]`
  - `parry_action_bonus = coeff["parry"]`
  - `action_damage = coeff["damage"]`
  - `action_force = coeff["force"]`
- 钩子：
  - `force_hit`：仅当 `src.jiali > 0 && src.neili > src.jiali && force_skill 有 code hook`；
    按 `force.lpc` 逐行实现，使用同一个 `prng`（先 `random(victim force)` 再
    `random(src force)`），返回 `(int_delta, reflected_qi|0, message|"")`。
    - `src.neili -= jiali`；
    - 反震分支：`src.hp = eff.apply_qi_damage(src.hp, damage*2)`，并按
      `xf.apply_qi_wound` 压低 `src.eff_hp`；战报 `entry["force_reflected"]=damage*2`。
    - 整数增量按原版 clamp 后传给 `xf.resolve_damage(force_hit=...)`。
  - `martial_hit`：`martial_skill` 有 code hook 时按原文消费随机
    （mantian-xing `random(skill)`；yunlong-zhua 先判 force 等级再 `random(skill)`），
    整数增量恒 0（只上毒，condition 未建模），在 `combat-implementation.md` 写明。
  - `victim_force_hit` / `victim_parry_hit` / `weapon_hit`：保持 0；不查表、不掷点。
- 战报顶层 `result` 增加 `"actions"` 说明块（可选）：
  `{"selected_count": N}` 仅用于测试；不改变既有键。

### 4.4 文字壳（`src/wsim/api/xkx_shell.py`）

`_combat_report_lines()`：

- 有 `move` 时前缀 `f"{who}使出一招「{move}」"`，再接原有结果句；
- 无 `move` 但有 `move_text`（种族默认招式）时，`_render_action_text()` 替换
  `$N/$n/$p/$P/$l/$w/$W/$d` 占位符后展示原版文案，残留 `$` 一律清掉；
- 无招式也无文案时保持旧文案（旧内容路径不走新分支），冒烟测试禁止把 `$` 泄露给玩家。

## 5. 确定性与“不改手感”

1. **旧实现等价路径**：实体 `map_skills` 为空且 `race` 为空 → 不选招、不掷点，
   代码路径与取数顺序完全不变。实测 `test_xkx_determinism_combat` 的 `state_final`
   保持 `91097d90…` 不变；`event_final` 因 `rounds[].actions[]` 新增
   `action_skill/selection/move/move_text/move_index/force_hit/martial_hit/force_reflected`
   字段而重采为 `273b5455…`（state 未变 = 无数值变化，只是战报字段增加）。
   真实 xkx 世界实体带 `race`，会新增一次 `query_action` 掷点（原版每回合必掷），
   因此同 seed 的下游随机序列会平移，逐项归因见 §5.2 与实施记录。
2. **有招式路径**：只新增一笔 `random(seq)`（选招），差异逐项归因到：
   - `combatd.lpc:334-339` `action["dodge"]`；
   - `combatd.lpc:396-401` `action["parry"]`；
   - `combatd.lpc:442-445` `action["damage"]`；
   - `combatd.lpc:482-485` `action["force"]`；
   - `force.lpc:5-30` `hit_ob`。
3. **取数顺序锁**：更新 `test_prng_draw_order_is_locked` 的期望表；新增一个“有招式”
   的锁序测试，把 `(n,value)` 序列写死。
4. **同 seed 双跑**：新增断言，有招式场景两次 `event.to_canonical_dict()`、
   `state_hash`、`event_hash` 逐字节一致。
5. **旧九地点哈希**：`tests/test_xkx_determinism_legacy.py` 与 `tests/test_p3e_golden.py`
   必须原值通过，结果贴进回报。

## 6. 缺系数 / 缺键测试

- `test_skill_actions_coefficients_missing_or_default_are_zero()`：
  遍历 247 条动作，断言 `coeff` 四键齐全且 `default` 源的值全为 0；共 126 个。
- `test_skill_actions_missing_key_is_zero()`：构造一条缺 `force` 的动作 dict，
  断言 `coeff["force"] == 0`，且 `simulate_fight` 能跑完、战报无 `None/NaN`。
- 战报 JSON 断言：`move` 为字符串，`damage/hp_before/hp_after` 仍为 int。

## 7. 验收

1. `pytest tests -q` 全绿（含新增测试）。
2. 贴出：旧九地点 `state/event` 哈希（原值）、新战斗基线哈希（原值或说明迁移）、
   同 seed 双跑哈希、有招式场景的取数序列。
3. 贴出“同 seed 同一只怪”的伤害序列对比表，每处差异注明原版分支（或“无差异”）。
4. `git add` 只加本任务文件（显式路径），推 `origin xkx-parity`。

## 8. 显式改动文件清单

- 新增：`src/wsim/sim/skill_actions.py`
- 修改：`src/wsim/sim/combat_rules.py`
- 修改：`src/wsim/sim/vitals.py`
- 修改：`src/wsim/world/state.py`
- 修改：`src/wsim/world/effects.py`（仅 create_entity/vitals_set 接受新键）
- 修改：`src/wsim/actions/player.py`（玩家创世补 `race="人类"`，旧九地点不带该键）
- 修改：`src/wsim/api/xkx_shell.py`（仅 `_combat_report_lines` 与 `_render_action_text`）
- 修改：`specs/xkx-parity/combat-implementation.md`（§3 取数顺序、§7 第 1/2 项、新增实施节）
- 新增测试：`tests/test_skill_actions.py`、`tests/test_combat_moves.py`
- 修改测试：`tests/test_xkx_determinism_combat.py`（event 基线重采）、
  `tests/test_xkx_api.py`（战报文案断言）
- 不动：`tools/xkx_ref/**`、`tools/xkx_formula/**`、`content/**` 的既有数据文件、
  其他工作流的未提交文件。

## 9. 风险与取舍

- `attack_skill()` 仍固定 `unarmed`；如果内容把 `mantian-xing` 映射到 `throwing`，
  当前引擎不会以 `throwing` 出招。`martial_hit` 分派按“实际使用的映射技能”执行，
  测试用 `map_skills={"unarmed":"mantian-xing"}` 覆盖。
- `force_hit` 反震会改攻击者气血；这是原版 `receive_damage` 行为，不是新伤害来源。
- `special_actions`（perform）不自动触发：原版 `reset_action()` 不选 perform，
  触发需要任务/冷却/内力条件，另立任务。
