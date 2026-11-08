# B/C/D 任务清单

> 规格：[`respawn-parity.spec.md`](./respawn-parity.spec.md)。S4 见 [`fight-kill-split.tasks.md`](./fight-kill-split.tasks.md)。
> 验收证据：[`reports/xkx-ref/bcd-verification.md`](../../reports/xkx-ref/bcd-verification.md)。

## T0 准备

- [x] `git status` 确认工作树；记录当前 `fix/xkx-room-parity` HEAD。
- [x] 跑一次现有全量 pytest，记录基线（1020 collected / 1002 passed / 18 skip）。
- [x] 记录 legacy 九地点与 xkx 世界的当前哈希（`735 / 009076f0… / 02d5f812…`）作为对照。

## T1 B：`_baseline()` 字段对齐

- [x] `genesis.py`：抽出纯函数 `npc_relation_maps` / `npc_spawn_skill`，输出不变。
- [x] `room_reset.py::_baseline()`：按规格 B.1/B.2 重写载荷，新增 `name/role/home/personality/skills/drops/status/template/instance`，删除 `max_stamina/skill_xp`。
- [x] `genesis.py` clone 创世改为按实例 `roll_key` 重算 vitals（与 C 同一次提交）。
- [x] `effects.py::entity_respawn`：复位 `max_stamina=100.0`、`skill_xp={}`；应用 `name/role/home/personality/drops/status`；`template/instance` 一致性校验。
- [x] 新增 `tests/test_xkx_respawn_fields.py`：集合完全相等 + 值断言 + 负向护栏。
- [x] 跑 `tests/test_xkx_room_reset.py` 与新增测试。

## T2 B：活世界验证

- [x] 同步代码到 `/private/tmp/xkx-play`，重启 API 8041（不带 `--reset`），确认 `world: existing`。
- [x] 等下一次 `system_room_reset`，sqlite 贴 `entity_respawn` 原始行（seq 7199/7214，含 `drops=goupi×1`）。
- [x] 真 HTTP `look` → `fight` → `look` → `loot`，贴 `loot_create`（seq 7212）、地面狗皮、背包 +1（seq 7216）。

## T3 C：逐实例属性掷点

- [x] `vitals.py::roll_attrs` 改为接收 `roll_key`；`resolve_npc_vitals` 增加 `roll_key` 参数（默认 npc id）。
- [x] genesis 规范实体用 `npc_id`，clone 用 `instance_id`，重生用 `f"{entity_id}#{death_tick}"`。
- [x] 新增 `tests/test_attrs_roll.py`（clone 差异、确定性、重生重掷、分布）。
- [x] 实测 legacy 九地点哈希不变：`1 passed`，`735 / 009076f0… / 02d5f812…`。
- [x] 记录 xkx 旧/新状态哈希并归因。

## T4 C：店小二 20 场

- [x] 复用 `/private/tmp/s1_accept.py` 口径重跑 20 场（seed 101-120）。
- [x] 汇总守卫下界比例：玩家 118/386=30.57%（31%±8pp 内）；店小二 38/249=15.26%（差 24%±8pp 下限 0.74pp，**未闭合，已单报**）。
- [x] 输出 20 场实际 `cor`：min 12 / mean 21.6 / max 30。

## T5 D：狼狗血量与伤害溯源（只分析）

- [x] 读原版 `beast.lpc:75-76`/`wolfdog.lpc:8`，确认 `max_qi=50` 由 age=4 公式给出，wolfdog 未覆盖。
- [x] 确认复刻 200 来自 `vitals.py:25 DEFAULT_MAX_HP_TIERS (0,200)`，非内容字面值。
- [x] 原版 10 场/54 刀 每刀中值 14.5（均值 19.22）vs 复刻 10 场/124 刀 中值 19（均值 18.15）。
- [x] 结论：血量 4 倍 + 每刀 1.0 倍 → 不等价，TTK 约 4 倍；建议补 `max_qi=50`，**未改，等批**。

## T6 老板库审计

- [x] 列验收期间全部非环境写入（`p5peek/shots/verify2/s1hunter` 的 fight/loot、`entity_respawn`、被拒 look）。
- [x] 证明无临时实体：`create_entity` 仅创世 + 8 个测试账号 `player_enter`；当前郭庄仅 `wolfdog#2`。
- [x] 证明 `player:boss` 序列无验收写操作；唯一一条 `seq 3965 look` 为被拒只读探测，已披露。

## T7 收尾

- [x] 更新 CHANGELOG。
- [x] `git diff --stat -- tools/xkx_formula` 为空。
- [x] 全量 pytest 全绿（1034 收集 / 0 failed / exit 0）。
- [ ] 显式 `git add`（逐文件），提交并 push（不 force）。
