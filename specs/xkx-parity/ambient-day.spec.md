# ambient-day.spec — NPC 日按 ambient tick（判定与显示同源 + 结算时钟登记）

状态：按用户 6 条裁定修订（2/3/6 不批准原方案，已改为判定层同步修改）
上游提交：`8b2ae49 fix(ambient): 共享模式按自身配速调度；日界改 ambient tick；health 心跳语义`

## 0. 裁定对应的修订

| 用户裁定 | 本 spec 的处理 |
|---|---|
| 1 批准 | `clock_tick` 为玩家可见日期**唯一入口**（§4），禁止各处自行换算 |
| 2 不批准 | 委托截止 `deadline_tick` 语义改为 **NPC 时钟单位**，显示与判定同源（§6.2）；哈希必须实测不变，变了就停（§8.1） |
| 3 条件批准 | 工资/出勤改 NPC 日；新增结算时钟登记表 + 测试（§7、§9） |
| 4 批准 | `lag_steps` 改累计；`lag_streak`/`lag_ticks_max`/`world_ticks_per_npc_day` 新增；旧字段不静默改义（§10） |
| 5 批准 | 先推 `8b2ae49`，再新提交 + CHANGELOG，再推（§11） |
| 6 不批准 | 取消“遗留”豁免：委托判定、门派派俸全部进表并改到 NPC 日（§7） |

## 1. 日界口径（已批准，不回退）

共享模式 `NPC 日 = AmbientScheduler.tick // TICKS_PER_DAY`（144 ambient 步）。理由：原版 xkx 的
NPC 生计挂后端心跳，与在线人数无关；绑玩家世界 tick 时 50 玩家 15 分钟替 NPC 过约 140 天、交 140 次生活费，
是口径错误。`WorldState.tick` 只做事件排序与结算时间轴，不再决定 NPC 日。

## 2. 144 tick = 1 NPC 日的推导

- `TICKS_PER_DAY=144`、`TICKS_PER_HOUR=6`。
- 1x：`ticks_per_second_1x = 10/60 ≈ 0.167 ambient tick/s`（`scheduler.py:42-59`）→ 1 ambient tick = 6s，
  144 tick = 864s = **14.4 分钟真实时间 = 1 NPC 日**；2x/4x 为 7.2/3.6 分钟。
- **不用墙钟**：护栏暂停/运维 pause/重启期间墙钟照走，会在没有结算的时段凭空变老并恢复后成批补结算；
  墙钟不进状态哈希，破坏回放。
- **不用玩家世界 tick**：每次玩家写 +6 tick，50 玩家 think-time 2s ≈ 149 tick/s，144 tick < 1 秒；
  按世界日结算会每秒触发日界（verify5 已复现 5 条负余额）。
- **玩家可感知后果**：1x 下 1 NPC 日 ≈ 14.4 分钟；50 玩家时世界**事件**加速，但 NPC 生计/日界/日结仍按 NPC 日；
  玩家日历跟 NPC 日，因此不会出现“玩家日子飞快、NPC 按另一套日子交钱”。

## 3. 两个时钟的唯一定义

| 时钟 | 载体 | 用途 |
|---|---|---|
| NPC 时钟（日历/生计） | `AmbientScheduler.tick`（步数计数器），无自走时 = `WorldState.tick` | NPC 日界、生活费、工资/出勤、委托截止/刷新、门派派俸、玩家可见日历、店铺 7 日账 |
| 世界时钟（事件轴） | `WorldState.tick` | 事件 seq 排序、写动作校验、LOD/队列步进、计划 slot、押镖到达、`created_tick/last_action_tick` |

所有“按天”的**结算**只能读 NPC 时钟；世界时钟只允许用于事件轴，且必须在 §7 表里登记理由。

## 4. 玩家可见日期的单一入口

**唯一入口：`WorldSession.clock_tick()`**。规则：
1. `clock_tick()` = `self.ambient.clock_tick`（ambient 存在且未 stopped），否则 `self.state.tick`。
2. 服务端在 `_status_view` 写入 `view["clock_tick"]` / `view["clock_day"]` / `today["clock_tick"]`；
   **UI 只读这两个字段**，不得自行用 `tick` 换算日/时辰。
3. `deadline_day` 由 `deadline_tick // 144 + 1` 计算（`deadline_tick` 已是 NPC 时钟单位，见 §6.2）。
4. 店铺 7 日账 `row["day"]` 由日结事件的 `args["day"]`（NPC 日）写入。
5. `look`/`time`/`score` 等动作文本中无日期/时辰输出（`src/wsim` 全域检索 `第{`/`时辰` 只命中记忆与台词）；
   此项检索结果作为“动作文本未暴露”的证据，不写“整体未暴露”。
6. 原始 `tick` 字段仍可展示为 `tick X`（事件序列调试），不得当日期解释。

## 5. 玩家可见出口审计

| 出口 | 位置 | 现值来源 | 处置 |
|---|---|---|---|
| 顶部日历 + 标题 | `ui/player/app.js:1686,1689` | `state.tick`（世界） | 改读 `st.clock_tick` |
| “今天能做什么” | `ui/player/app.js:2430-2431` | `today.tick`（世界） | 服务端补 `today.clock_tick`，UI 改读 |
| 委托“截止第 X 天” | `app.js:2559` / `session.py:1428` | `deadline_tick//144+1`（现为世界） | `deadline_tick` 改 NPC 单位，公式不变 |
| 店铺 7 日账“第 X 日” | `app.js:3333` / `business.py:428` | `settled_day_of(intent.tick)`（世界） | 日结行 `day` 取 NPC 日 |
| 动作文本 | `src/wsim` 检索 | 无日期字符串 | 不改（检索结果即证据） |

## 6. 判定层同步修改（裁定 2/3/6）

### 6.1 时钟透传（唯一机制）

1. `core/models.py`：`Intent` 增加 `clock_tick: int | None = None`（**不进 `to_dict()`**，事件/哈希不变）。
2. `core/clock.py` 增加：
   ```python
   def npc_tick_of(intent) -> int:
       ct = getattr(intent, "clock_tick", None)
       return int(intent.tick) if ct is None else int(ct)
   ```
3. `scheduler.py`：`_submit()` 在 `shared` 且 `intent.clock_tick is None` 时
   `intent = replace(intent, clock_tick=int(self.tick))`；共享调度器的 `world_view`
   （`_world_view()`）加 `"clock_tick": int(self.tick)`；headless 的 provider 不加。
4. `session.py`：玩家/系统 `Intent(...)` 统一经 `_intent_clock()`：ambient 运行时补
   `clock_tick=<ambient 步数>`，无自走时返回 `None`（**不是** `state.tick`），
   让判定层回退到 `intent.tick`，行为与旧版逐字节一致。`clock_tick()` 另供视图与日结辅助读取。

### 6.2 委托截止（判定 = NPC 时钟）

- `actions/quests.py:52`：`deadline_tick = npc_tick_of(intent) + deadline_days * 144`
  （`created_tick`/`id` 仍用 `intent.tick`，事件溯源不变）。
- `actions/quests.py:264,333,412,418`：`intent.tick` → `npc_tick_of(intent)`。
- 接单 `accepted_tick` 落库改为 `npc_tick_of(intent)`（NPC 任务冷却/临期判断同源）。
- `sim/intent.py`：`_now(cursor) = cursor if clock_tick is None else int(clock_tick)`；
  截止/冷却比较与 `_quest_attempt/_quest_served` 写入都用 `_now(cursor)`。
  **headless 保持“随 cursor 推进”的旧语义**（不能冻结成循环开始时的 cursor，否则接多单时哈希会漂）。
  采购/消费/探配偶的 `day_of(cursor)` 在共享世界改用 `day_of(clock_tick)`，headless 回退 `day_of(cursor)`。
- `actions/player.py:425`：任务板窗口用 `npc_tick_of(intent)`。
- `session.py:1428`：公式不变（NPC 单位）；`1435`、`1923` 用 `self.clock_tick()`。
- `session.py:_quest_lock_reason` 原 `tick=state.tick` → `tick=self.clock_tick()`。

### 6.3 工资/出勤（判定 = NPC 日）

- `actions/basic.py` 的日结雇工记忆在上工日追加 NPC 日标记：
  `已签日结契约，工资本日由日结发放{gross}文` → 有 `intent.clock_tick` 时追加 `：D{npc_day}`
  （无自走不追加，旧哈希不变）。
- `business.py:_worked_on_day(state, eid, day, clock_marker=False)`：
  `clock_marker=True` 时只认 `ATTENDANCE_MARKER:D{day}` 记忆；否则沿用旧 tick 窗口。
- `build_shop_settlements(..., ledger_day=None, clock_day=None)`：
  - 出勤/工资判定 `day` = `clock_day`（NPC 日），`_worked_on_day(..., clock_marker=clock_day is not None)`；
  - `row["day"]` = `ledger_day if ledger_day is not None else day`。
- `SystemShopSettleAction.apply`：从 `intent.args["day"]` 取 `ledger_day`/`clock_day`。
- headless 兼容：headless 的 `args["day"]` == `settled_day_of(base+143)`，且 `clock_tick=None` → 走旧路径，
  `day_history` 逐字节不变。

### 6.4 门派派俸

- `actions/player_sect.py:_day()`：默认 `npc_tick_of(intent)//144`，上界同源。
- `session.py:_settled_today` / `_disciple_settled_today`：用 `self.clock_tick()//144`。

### 6.5 玩家日界触发（避免第二套时钟复活）

`session.py:_advance` 的 `day_boundary` 判断：
- ambient 运行中：`if self.clock_tick()//144 != self._last_clock_day`（`_last_clock_day` 在会话内维护），
  `args["day"] = clock_day`；
- 无 ambient：沿用旧的 `day_of(tick) != day_of(state.tick)`，事件序列逐字节不变。
这样 50 玩家下不再每秒触发 `system_tick` 的委托下架/补发。

## 7. NPC 侧周期性结算时钟登记表（进代码与测试）

新增 `src/wsim/core/settlement_clock.py`：
```python
NPC = "npc"
WORLD = "world"
SETTLEMENTS = {
    "system_recover":            (NPC,   "日界恢复"),
    "system_quest_post":         (NPC,   "委托刷新/保供"),
    "system_living":             (NPC,   "NPC 生活费"),
    "system_debts":              (NPC,   "债务日结"),
    "system_quest_fail":         (NPC,   "过期委托下架"),
    "system_milestone":          (NPC,   "里程碑"),
    "system_maintenance":        (NPC,   "维护"),
    "system_shop_settle":        (NPC,   "玩家店铺工资/出勤/7日账"),
    "system_savings_tax":        (NPC,   "月税"),
    "system_shop_dividend":      (NPC,   "店铺分红"),
    "system_shop_tax":           (NPC,   "店铺月税"),
    "quest.deadline":            (NPC,   "委托截止判定与显示"),
    "quest.accept_cooldown":     (NPC,   "NPC 接单冷却/临期"),
    "player_shop.attendance":    (NPC,   "雇工出勤"),
    "player_shop.wage":          (NPC,   "雇工工资"),
    "sect.pay_disciples":        (NPC,   "门派会费/俸禄/欠饷"),
    "shop_ledger_day":           (NPC,   "7 日账日标签"),
    "system_plan":               (WORLD, "事件队列 slot 轴；每个 NPC 日只在日界生成一次，不结算金钱"),
    "lod_wake_schedule":         (WORLD, "唤醒节奏；不转移/销毁任何账户余额，且受 wake/intent 预算限制"),
    "escort_arrive":             (WORLD, "押镖到达时间轴；一次性事件，不按天重复"),
    "event_created_tick":        (WORLD, "事件溯源字段，不参与任何日结判定"),
    "player_rest_day_boundary":  (WORLD, "玩家体力/在线日界；只影响玩家自身，委托下架已改用 NPC 截止"),
}
```
- 每个登记项显式 `(clock, reason)`；`WORLD` 必须带理由。
- 日界触发的 verb 列表在 `scheduler.py` 提为模块常量 `DAY_START_VERBS` / `DAY_END_VERBS`，
  `system_shop_settle`、30/60/90 月结动词也列入 `DAY_END_VERBS`。

新增 `tests/test_settlement_clock_registry.py`：
1. 断言 `DAY_START_VERBS ∪ DAY_END_VERBS` 全部登记，且每项 `(clock, reason)` 显式；
2. 断言 NPC 生计/玩家店铺/门派日结项全部为 `NPC`；
3. 用 AST 扫描 `scheduler._day_start/_day_end` 中出现的每个 `system_*` 动词，未登记即 fail
   （新增日界结算忘记登记时自动拦住）。

## 8. 验收

### 8.1 哈希硬门禁（裁定 2）

- 旧九地点 headless 三项哈希**实测**与基线一致：
  `events_total 735`、`state_final 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9`、
  `event_final 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`、cash `11442`、blocked `6`、pruned `3`。
- headless 同 seed 双跑 `ambient.jsonl` 字节一致。
- **若实测哈希变化：停下，写明差异原因，不放宽阈值、不改基线。**
- `tests/test_xkx_determinism_legacy.py` PASS（`/Users/lyinet/projects/XKX/world-sim`）。

### 8.2 短验（6h 之前，硬性）

命令：`PYTHONPATH=src python3 tools/soak_test.py --duration 900 --players 50 --think-time 2 --warmup 0`
（真实 50 玩家 + ambient）。报告原始计数，必须满足：
1. 生活费 `system_living` ok 次数 / 30 NPC ≈ **1–2**；工资/出勤结算次数同量级；出现几十上百次即未修干净，停下改。
   工资/出勤由 `system_shop_settle` 驱动，与 `system_living` 同一个 NPC 日界事件；若本次短验没有玩家店铺（`system_shop_settle=0`），报告须明确写 N/A 并给出该事件与 `system_living` 的同一日界证据，不得当作已验证。
2. 负余额 **0**。
3. `lag_steps` 累计与 `ambient_catchup_truncated` 次数一致（同一计数，见 §10）。
4. `lag_ticks` 末值不随时间扩大（对比末 3 个 5 分钟采样的 `lag_ticks`/`lag_ticks_max`）。
5. 玩家日历 15 分钟推进 ≈ 1 NPC 日；`/api/player/state.clock_tick` == `ambient_status.json.clock_tick`。

短验通过后先报原始计数，等用户确认再启 6h。

### 8.3 6h 报告（条件 4）

命令：`PYTHONPATH=src python3 tools/soak_test.py --duration 21600 --players 50 --think-time 2 --warmup 600`
报告 `reports/soak/master-6h2-20261108/report.md` 必须含：
1. `ambient` 事件数与 `npc` 行为事件数；
2. ambient 时钟与玩家世界时钟绝对值 + 比值；`lag_ticks` 最大/末值；`lag_steps` 累计；
   `ambient_catchup_truncated` 次数（= `lag_steps`，另附日志行数）；
3. 负余额条数（必须 0）与按 NPC 日生活费次数（≈6h/14.4min ≈ **25 次/NPC**，上百次即回归）；
4. 写 p95 用 **严格 30 秒窗口口径**（见 §12.1，2026-11-08 裁定取代“末 10 分钟平均”口径）；
   末 10 分钟 p95 与 2h2 基线（`9.8 / 32ms`）只作趋势参考，不作为通过依据；内存按峰值；
5. 一句明确结论：“负载结构是否与设计一致”。

**中止条件（任一出现立即停）**：lag 持续扩大不收敛（连续 3 个 5 分钟 `lag_ticks_max` 创新高或
`lag_ticks` 持续 >1200）；负余额回归；**任一 30 秒窗口写 p95 >500ms**（严格口径，见 §12.1）。

## 9. 测试清单

- `tests/test_settlement_clock_registry.py`（§7）。
- `tests/test_ambient_clock_visibility.py`：`clock_tick` 视图字段、无自走回退、`deadline_day` 同源、
  店铺 7 日账 day 与出勤都在 NPC 日。
- `tests/test_player_commerce.py` 既有 `last_settle_tick` 断言不回归。
- 全量 `PYTHONPATH=src python3 -m pytest -q` 全绿。

## 10. 记账字段（裁定 4，旧字段不静默改义）

| 字段 | 定义 |
|---|---|
| `lag_ticks` | `world_tick − processed_world_tick`（当前值） |
| `lag_ticks_max` | 历史最大值；每次写 status 时用实时差额刷新，保证任意时刻 `lag_ticks ≤ lag_ticks_max` |
| `lag_steps` | **累计**触发 `max_catchup_ticks` 的调度步数（= `ambient_catchup_truncated` 次数） |
| `lag_streak` | 旧 `lag_steps`（连续截断步数）改名保留；同时保留 `lag_steps_streak` 别名，不删 |
| `ambient_catchup_truncated` | status 计数，与 `lag_steps` 同一变量；日志行数是采样（首次 + 每 60 连续步），报告需分开列 |
| `skipped_days` | 相邻 processed 世界时间点跨过的整世界日数（只记账，不触发日结） |
| `skipped_wakes` / `dropped_stale` / `queue_backlog` / `max_queue_backlog` | 同 8b2ae49 |
| `step_ms_max` / `step_ms_p95` | 调度步（持有写锁的自走整步）墙钟耗时峰值/滚动 512 步 p95；用于区分“锁内变慢”与进程/系统级停顿 |
| `clock_tick` | NPC 日历 tick（status 与玩家视图同源） |
| `world_ticks_per_npc_day` | 每 NPC 日推进的世界 tick 数（EMA，≥144） |

## 11. 推送与变更记录

1. 立刻推 `8b2ae49`（显式 6 文件 `git add`，不 force，推 `master`）。
2. 新提交：本轮文件（`core/models.py`、`core/clock.py`、`core/settlement_clock.py`、`sim/scheduler.py`、
   `sim/intent.py`、`api/session.py`、`actions/quests.py`、`actions/system.py`、`actions/business.py`、
   `actions/basic.py`、`actions/player_sect.py`、`actions/player.py`、`ui/player/app.js`、
   `tests/test_settlement_clock_registry.py`、`tests/test_ambient_clock_visibility.py`、
   `specs/xkx-parity/ambient-day.spec.md`、`CHANGELOG.md`）显式 add。
3. `CHANGELOG.md`：`8b2ae49` 六文件逐条；单列“日界语义变更”（NPC 日=ambient tick、判定与显示同源、
   `lag_steps` 改累计 + `lag_steps_streak` 保留旧义）。
4. 不 force。

## 12. 严格延迟口径与归因（2026-11-08 裁定）

### 12.1 通过口径

- 采样周期固定 30 秒；逐窗口原始读写延迟算 p95。
- **任一 30 秒窗口写 p95 > 500ms 即判未过**；末 10 分钟平均口径作废，不得用于洗掉坏窗口。
- 对照组不能用于放宽阈值：只用于判定“是机器噪声还是代码”。

### 12.2 逐窗口归因（必交）

- 每个 30 秒窗口把 `ambient_steps.jsonl` 的 `step_ms` 分位与墙钟写 p95 对齐；
  坏窗口必须给出“锁内多少 / 锁外多少”：
  `in_lock_ms_p95/max`、`in_lock_cpu_ms_max`、`lock_wait_ms_p95/max`。
- 锁外停顿必须落到具体一项并附证据：GC（代际 + 单次停顿时长）、SQLite checkpoint/WAL
  （`ck_stats`: runs/ms_max/busy）、JSONL flush（`progress_flush_ms`）、CPU 争抢
  （`os.getloadavg` + `ps` 同机 top-N 进程，排除服务/压测进程自身）。
- 归因落盘 `reports/soak/<run>/attribution.json`（schema `soak-attribution/v1`），
  坏窗口 `cause` 取值域：`gc / checkpoint / progress_flush / status_file / state_hash /
  offcpu_stall / in_lock_total / machine_cpu`。

### 12.3 同机对照组

- 命令：`--duration 900 --players 10 --no-ambient`，与服务同一时段、同一台机器。
- 对照组也出现 >500ms 窗口 → 结论是阈值在测机器噪声：换安静环境重跑**同一负载**，
  仍按绝对阈值判定（这是排除干扰，不是放宽阈值）。
- 对照组干净 → 写 p95 高归因于本工程代码，必须修；不接受“环境问题”解释。

### 12.4 内存（不许 15 分钟线性外推）

- 跑一次 45–60 分钟中间跑，输出每 10 分钟的 `RSS / relations / goals / PlayerProfile` 增量表。
- 以 `state.relations` 普查为权威计数（`types_top` 只列 top-N，不可用于趋势）。
- 仍线性增长 → 直接给 6h 投影峰值 + 治理方案（relations / goals / PlayerProfile 裁剪或封顶），
  不接受“6h 再看”。

## 13. 6h 配置的前置条件：玩家店铺注入

- 6h 与短验配置必须带 `--player-shop 1`：脚本用公开 API 开摊（`open_shop`，stall，本钱 300 + 进货），
  对话把 `bond` 攒到 `SHOP_HIRE_MIN_AFFECTION=6`，再 `recruit` 一名掌柜（`position` 默认 NPC role，
  工资取 `wages_daily` 市价）。
- 目的：让 NPC 日界结算 `system_shop_settle` 真跑出来（开张 + 出勤 + 工资），报告给原始计数；
  未跑出时 `system_shop_settle=0` 必须写 N/A，不得计为通过。
- 注入过程逐次落 `reports/soak/<run>/player_shop.jsonl`（`open_shop / hire / done / error` + 拒绝码）。
- 注入前置校验：先跑一次 `--duration 1200 --players 50 --player-shop 1`，确认出现
  `system_shop_settle` 且 `settlement_counts` 非零，再启 6h。

## 14. 单位确认（回答报告混用风险）

- `lag_ticks_max=1122` 的 1122 是**世界 tick 数**，与 1200 上限同量纲（不是步数）。
- `world_tick 132029 / clock_tick 150` 中的 `clock_tick=150` 是 **NPC tick 计数**，
  不是“NPC 日界触发次数”；144 tick = 1 NPC 日，故 150 ≈ 1.042 NPC 日，日界只触发 1 次。
