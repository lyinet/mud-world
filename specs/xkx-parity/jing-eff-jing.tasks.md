# tasks：jing / eff_jing 子系统（规格 → 实现拆分）

依赖 spec：`specs/xkx-parity/jing-eff-jing.spec.md`
基线：`fix/xkx-aggro-delay@3d06896`（= `ebf5d50` + 偷袭窗口 / `eff_hp` 修复）
约束：本清单只用于后续实现；本轮不写代码、不提交、不推分支。开关默认关闭，关闭时旧基线必须逐字节不变。

## T1 字段与序列化（无行为变化）

- [x] T1.1 `src/wsim/world/state.py`：`Entity` 增加 `jing: int = 0`、`max_jing: int = 0`、
      `eff_jing: int = -2`；不新增 `jingli/max_jingli`。
- [x] T1.2 `to_dict()` 增加 jing 组：`max_jing` 非 0 才输出 `jing/max_jing`；
      `eff_jing >= -1 且 != max_jing` 才输出 `eff_jing`。
- [x] T1.3 `heal_credit/heal_cd` 的输出条件由「仅 `eff_hp` 受伤」改为
      「`eff_hp` 或 `eff_jing` 受伤」。
- [x] T1.4 新增 `combat.jing` 开关读取（独立于 `combat.eff_qi`，默认 false），
      `combat.jing && !combat.eff_qi` → 配置错误并按 false 处理。
- [x] T1.5 验证：`python -m pytest -q tests/test_combat_eff_qi.py tests/test_xkx_determinism_legacy.py`
      与 `to_dict` 快照全绿；关闭开关时旧九地点六值不变。

## T2 初始化、解析与迁移

- [x] T2.1 `src/wsim/sim/vitals.py`：新增 `jing_for_npc(max_hp, content_max_jing)`，
      按 `npcd.lpc:305-309` 取「内容值，否则 `max_hp//2` 下限」；**不用** beast 年龄公式
      （npcd 覆写在其后，叠加会得到 laohu=240/wolfdog=70，偏离原版 212/25）。
- [x] T2.2 `resolve_npc_vitals` 增加 `jing: bool=False`：只有开关打开才落
      `max_jing/jing`，关闭态创世载荷逐字节不变。
- [x] T2.3 `create_entity` / `entity_respawn` 写 `jing=eff_jing=max_jing`
      （`max_jing==0` 保持默认）。
- [x] T2.4 玩家建档写 `jing=eff_jing=max_jing=100`（`logind.lpc:918-922`）。
- [x] T2.5 旧存档回填：`vitals.backfill_jing` 在会话回放旧事件后、开关打开时补一次；
      已有 `max_jing>0` 不覆盖；`vitals_set` 接受三个新字段。
- [x] T2.6 验证 J1-J3：玩家 100/100/100；`laohu max_jing=212`、`wolfdog=25`；
      带 `vitals.max_jing` 的 NPC 取内容值。

## T3 伤害与恢复原语

- [x] T3.1 `src/wsim/world/effects.py`：新增 `jing_add/jing_set/eff_jing_add/eff_jing_set`，
      钳制与 `hp` 组一致（值 ≥0；有效值下限 -1；`eff_jing ≤ max_jing`）。
- [ ] T3.2 `heal_apply`（`effects.py:589`）支持 `jing/eff_jing`，同一事件内与 hp 一并写。
- [x] T3.3 昏迷事件把 `jing` 与 `qi` 一起清 0（`damage.lpc:137-138`）；苏醒不清 jing。
- [x] T3.4 `entity_status` / `entity_respawn` 的死亡前置条件允许「qi 满但 jing 见底」
      （不再只要求 `hp==0`；事件显式带 `cause="jing"`）。
- [x] T3.5 验证 J8-J9：`eff_jing` 0→-1 判 dead（qi 满）；`jing` 0→-1 判 unconcious
      且事件中 `jing=0, qi=0`。原始值：J8 `damage seq=2 tick=2`、`heal seq=3 tick=42`、
      `hp=425/eff_jing=-1`；J9 同 seq/tick，`hp_set 0 → jing_set 0 → unconcious`，
      `revive_tick=108`。

## T4 战斗结算接线

- [x] T4.1 `src/wsim/sim/combat_rules.py:517,551`：`victim_jing/victim_max_jing`
      改传真实 `jing/max_jing`（奖励公式已按 jing% 实现）。
- [x] T4.2 `src/wsim/sim/combat_rules.py:618`：`attacker_jing/attacker_max_jing`
      改传真实 `jing/max_jing`。
- [x] T4.3 `src/wsim/sim/combat_rules.py:633`：`death_state` 传
      `jing=dst.jing, eff_jing=dst.eff_jing`，删除「jing=hp」复用。
- [x] T4.4 通道语义由 `jing_add`（damage）/`eff_jing_add`（wound）+ `floor=-1`
      固定；`max_jing==0` 的实体不产生任何 jing 事件（关闭态逐字节不变）。
      常规招式只打 qi，jing 伤害技能不在本期内容（见 spec §7.4）。
- [ ] T4.5 `src/wsim/actions/aggression.py:37-105`：wimpy 同时比较
      `jing*100/max_jing`（`char.lpc:87-92`），`max_jing==0` 时只看 qi。
- [ ] T4.6 验证 J10：`jing=10%`、qi 满 → flee 事件出现；qi/jing 都高于阈值时不出逃。

## T5 动作接入

- [ ] T5.1 `AnsuanAction`（`actions/combat.py:624+`）：precondition `jing < 100` →
      `rejected:cannot_focus`；apply `jing -= 50`，日志带 `jing_before/jing_after`。
- [x] T5.2 `SystemHealAction`（`actions/system.py:514-578`）：同一心跳恢复
      `jing += con//3`（`max_jingli` 项按 0），封 `eff_jing` 后 `eff_jing += 1`；
      与 hp 共用 `heal_credit/heal_cd`。
- [x] T5.3 `sleep`（`actions/player.py:623-699`）：醒来 `jing = eff_jing`
      （`sleep.lpc:207-217`）；`healing_ceiling` 增加 jing 版；`rest` 同样封 `eff_jing`。
      过量睡眠分支按 `last_slp`（tick）：同一 tick 内再次 sleep 且 `max_jing>160`
      走 `jing += eff_jing//2 - jing//2`。
- [ ] T5.4 验证 J4-J7：ansuan 99 拒 / 100 过后 50；sleep 10→80 不回 100；
      heal 每触发 +6 且封顶后 eff+1。

## T6 接口与展示

- [x] T6.1 `api/session.py` 增加 `_load_jing`（与 `_load_eff_qi:1623` 同构），
      传给 `WorldSession`。
- [ ] T6.2 `_vitals_view`（`session.py:809`）在开关开时输出
      `jing/max_jing/eff_jing`；关闭时不加键。
- [ ] T6.3 `hp` 等展示命令按原版口径输出（仅在开关开时）。
- [ ] T6.4 验证：关闭开关的 API 响应与当前逐字节一致。

## T7 验收与基线

- [ ] T7.1 开关关闭：旧九地点连跑两次 → `735 / 009076f0…780ce9 / 02d5f812…078ae /
      11442 / 6 / 3`；全量 `pytest` 绿。
- [ ] T7.2 开关打开：xkx 世界 seed `20261014` 跑一场，记录
      `state_final/event_final/events`，同 seed 两次一致；新增键只出现在
      `max_jing>0` 的实体。
- [ ] T7.3 新增单测：J1-J11（见 spec §7.2），每条断言原始字段值，不做近似。
- [ ] T7.4 报告：开关关闭前后哈希对比、开关打开前后的 events 差值分类、
      未实现项清单（jing 伤害技能 / age 加成 / max_jingli / 睡太多分支）。

## 依赖

- T2 依赖 T1（字段与开关）。
- T3 依赖 T1/T2（字段、上限解析）。
- T4 依赖 T3（原语）。
- T5 依赖 T2/T3/T4（ansuan 只需 T2；sleep/heal 需要 T3）。
- T6 依赖 T1/T2。
- T7 依赖全部。
