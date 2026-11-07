# 招式序列与技能钩子接入战报 — 任务清单

对应 `specs/xkx-parity/skills-actions.spec.md`。分支 `xkx-parity`；禁止推 `master`；显式路径 `git add`。

## T1 内容读取模块

- [x] 新增 `src/wsim/sim/skill_actions.py`
  - `load_action_table(content_dir=None)`：读 `skills_actions.json`，标准化
    `{index,name,text,level,coeff{dodge,parry,force,damage}}`；缺键=0；空动作跳过；
    `lru_cache` + `clear_cache()`。
  - `load_hook_table(content_dir=None)`：读 `skills_stats.json` 的 `hooks`。
  - `select_action(actions, level, prng)`：按 `query_action` 语义；
    `seq=0` 时 `prng.below(0)` 后取第 0 条；空表返回 `None` 不掷点。
  - `mapped_skill(entity, base)`、`has_code_hook(hooks, name)`。
- 验证：22 技能 / 247 动作 / 126 个 default 系数（全 0），见 `tests/test_skill_actions.py`。

## T2 Entity 与内容落值

- [x] `src/wsim/world/state.py`：`Entity` 增 `map_skills/prepare_skills/race/neili/max_neili/jiali`，
      `to_dict()` 非空/非零才输出。
- [x] `src/wsim/world/effects.py`：`create_entity`、`vitals_set` 接受并落上述键。
- [x] `src/wsim/sim/vitals.py::resolve_npc_vitals`：从 `npcs.json` 的
      `map_skills/prepare_skills/vitals.neili/max_neili/jiali` 落值；`race` 按 id/gender 最小推断。
- [x] `src/wsim/actions/player.py`：玩家创世补 `race="人类"`（旧九地点路径不带该键）。
- 验证：旧九地点测试哈希不变；xkx NPC 创建后带 `map_skills`。

## T3 战斗结算选招

- [x] `src/wsim/sim/combat_rules.py`
  - `_Actor` 读新字段；`mapped()`、`force_skill`、`martial_skill`、
    `apply_armor_vs_force`。
  - `simulate_fight(..., action_table=None, hook_table=None)`；默认懒加载。
  - 每次 `strike()` 先选招（一笔 `random(seq)`），再走 dodge/parry/damage；
    无映射技能回落种族 `combat_action`（人类 5 条 / 走兽 3 条）。
  - 系数传给 `resolve_dodge(dodge_action_bonus=)`、`resolve_parry(parry_action_bonus=)`、
    `roll_weapon_damage(action_damage=)`、`resolve_damage(action_force=)`。
  - `entry` 增 `move/move_text/move_index/action_skill/selection`。
- 验证：无 `map_skills` 且无 `race` 实体的 `draws` 与旧值一致。

## T4 技能钩子

- [x] `force_hit`：6 个 code 内功按 `force.lpc:5-30` 实现，含 `neili` 扣减、
      反震改攻击者气血、`random` 顺序；整数增量传 `resolve_damage(force_hit=)`。
- [x] `martial_hit`：mantian-xing / yunlong-zhua 按原文消费随机，整数增量恒 0。
- [x] `victim_force_hit / victim_parry_hit / weapon_hit` 保持 0。
- [x] `specs/xkx-parity/combat-implementation.md` §7 第 1/2 项更新并写原因。

## T5 战报文字

- [x] `src/wsim/api/xkx_shell.py::_combat_report_lines`：有 `move` 时输出
      `“{who}使出一招「{move}」”`；只有种族默认 `move_text` 时替换占位符后展示；
      两者都没有时保持旧文案。
- [x] 断言玩家可见文本不含 `$N/$n/$w/$l`（`_render_action_text` 清残留 `$`）。

## T6 测试与证据

- [x] `tests/test_skill_actions.py`：动作/系数计数（22/247/126）、缺键=0、
      `select_action` 语义与 `(0,0)` 记录。
- [x] `tests/test_combat_moves.py`：
  - 有招式场景战报出现 `move` 且两跑一致；
  - 有招式取数顺序锁死（首笔 `(2,1)`）；
  - 同 seed 同一只怪伤害序列对比（无差异或逐项注明分支）；
  - `force_hit` 正/负/反震三例；
  - `martial_hit` 取数消费、增量为 0。
- [x] `tests/test_combat_fight.py::test_prng_draw_order_is_locked`：无招式路径未变，原值保持；
      有招式锁序在 `test_combat_moves.py`。
- [x] `pytest tests`：884 passed，18 skipped。

## T7 确定性证据

- [x] 旧九地点：`tests/test_xkx_determinism_legacy.py`（state `009076f0…` /
      event `02d5f812…`）、`tests/test_p3e_golden.py` 原值通过。
- [x] 新战斗基线：`state_final` 仍为 `91097d90…`（无数值变化）；
      `event_final` 因战报新增字段显式重采为 `273b5455…`。
- [x] 同 seed 双跑（真实老虎场景）`state_hash/event_hash` 逐字节一致，见实施记录。

## T8 提交

- [ ] `git add` 仅本任务显式路径（见 spec §8）。
- [ ] `git commit`，`git push origin xkx-parity`。
- [ ] 回报：哈希、取数序列、伤害差异归因表、钩子开关说明。
