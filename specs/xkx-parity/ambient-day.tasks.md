# ambient-day.tasks — 执行清单（按裁定 1–6）

## T0 先推已批准修复
- [ ] 显式 add 6 文件 → `git push origin master`（不 force），记录输出

## T1 时钟透传机制
- [ ] `core/models.py`：`Intent.clock_tick: int | None = None`，不进 `to_dict()`
- [ ] `core/clock.py`：`npc_tick_of(intent)`
- [ ] `scheduler.py`：`_submit()` 统一补 `clock_tick`；两处 `world_view` 加 `clock_tick`
- [ ] `session.py`：所有玩家 `Intent(...)` 补 `clock_tick=self.clock_tick()`

## T2 委托截止判定改 NPC 时钟
- [ ] `quests.py:52` 用 `npc_tick_of`；264/333/412/418 同
- [ ] 接单 `accepted_tick` 用 NPC tick
- [ ] `sim/intent.py` 847/853/878/899/1053/1118/1791 改用 `world_view["clock_tick"]`
- [ ] `actions/player.py:425` 任务板窗口改 NPC tick
- [ ] `session.py` `_quest_row`/`_quest_lock_reason`/1923 用 `clock_tick`

## T3 工资/出勤改 NPC 日
- [ ] `basic.py` 上工记忆在有 `clock_tick` 时追加 `：D{npc_day}`
- [ ] `business.py` `_worked_on_day(..., clock_marker)`；`build_shop_settlements(..., ledger_day, clock_day)`
- [ ] `system.py` `SystemShopSettleAction` 从 `args["day"]` 传 `ledger_day`/`clock_day`
- [ ] 生活费债务 id（`system.py:530`）用 `args["day"]`（headless 恒等）

## T4 门派派俸 + 玩家日界
- [ ] `player_sect.py:_day()` 默认 NPC 日
- [ ] `session.py` `_settled_today`/`_disciple_settled_today` 用 NPC 日
- [ ] `session.py:_advance` `day_boundary` 改 NPC 日（维护 `_last_clock_day`）

## T5 结算时钟登记表 + 测试
- [ ] 新增 `core/settlement_clock.py`（NPC/WORLD + 理由）
- [ ] `scheduler.py` 提 `DAY_START_VERBS` / `DAY_END_VERBS` 常量
- [ ] `tests/test_settlement_clock_registry.py`（覆盖 + 漏登记 fail + WORLD 必须有理由）
- [ ] `tests/test_ambient_clock_visibility.py`

## T6 UI
- [ ] `app.js:1686,1689` 改 `clockOf(st.clock_tick ?? st.tick)`
- [ ] `app.js:2430-2431` 改 `clockOf(t.clock_tick ?? t.tick)`

## T7 哈希硬门禁
- [ ] 旧九地点三项哈希实测贴出（变了就停、写原因）
- [ ] headless 双跑字节一致
- [ ] legacy test PASS
- [ ] 全量 pytest 全绿

## T8 变更记录 + 推送
- [ ] CHANGELOG：`8b2ae49` 六文件逐条 + 日界语义变更单列（含 `lag_steps`/`lag_steps_streak`）
- [ ] 新提交显式 add → push（不 force）

## T9 15 分钟短验（6h 前门禁）
- [ ] `--duration 900 --players 50 --think-time 2 --warmup 0`
- [ ] 生活费/工资/出勤 ≈1–2 次/NPC；负余额 0；`lag_steps`=截断次数；`lag_ticks` 不扩大
- [ ] 报原始计数，等用户确认后才启 6h

## T10 6h（短验确认后）
- [ ] 报“开始了”，报告含 spec §8.3 五项；三项中止条件任一出现立即停

## T11 严格口径归因（spec §12）
- [x] `attribution.json` 逐 30s 窗口对齐 `step_ms` 与墙钟写 p95（锁内/锁外/CPU 拆分）
- [x] 坏窗口 cause 域 + GC 代际/停顿时长 + checkpoint 计数 + 同机 top-N 负载
- [x] 对照组 `--players 10 --no-ambient --duration 900`（0 坏窗口 → 非机器噪声）
- [x] 严格短验 `--duration 900 --players 50` 逐窗口复核，坏窗口 0 才算过
  （`strict15m-20261108` 30 窗全 ≤500ms；`shop-preflight6-20261108` 40 窗 0 坏）

## T12 内存中间跑（spec §12.4）
- [x] `--duration 2700 --players 50` 每 10 分钟 RSS/relations/goals/PlayerProfile 表
- [x] 结论：relations/goals 增量递减、PlayerProfile 定值 51；RSS 锯齿轮 0.645MB/min，6h 峰值 ≈300MB
- [x] DB 体积 6h 投影：按 15MB/10min 线性 → 6h ≈570MB（业务表事件量线性，非内存泄漏）
- [ ] 治理方案裁定（events 保留期/归档 VACUUM）需用户确认后再进 6h

## T13 玩家店铺注入（spec §13）
- [x] `--duration 2100 --players 50 --player-shop 1`（`shop-preflight7-20261108`）：`player_shop.jsonl` 出现 `done`
- [x] `system_shop_settle=2`，原始计数：day0 出勤 wage=30、revenue=62、net=31；day1 未到店 wage=0
- [x] 出勤凭证改为店铺 `attendance_day`（记忆 80 条上限会淘汰），清空记忆后仍认（`test_work_writes_attendance_marker_in_npc_day`）

## T14 6h 配置
- [ ] 6h 命令追加 `--player-shop 1`，报告单列 `system_shop_settle` 原始计数
- [!] 严格口径：0 坏窗口不变；高负载窗口不得剔除，改为测量环境前置（T15）

## T15 测量环境前置（spec §16）
- [x] spec 写死：loadavg ≤4 才启动、跑前 10min/跑中每 10s 采样、连续 5min >4 主动中止 `run_invalid(external_load)`
- [x] 贴入本轮 loadavg/坏窗口对照表，并写明“不是放宽阈值”
- [x] soak 实现 loadavg 采样与 5 分钟中止标记（`--max-loadavg`/`--pre-idle`、`loadavg*.jsonl`、`run_invalid.json`、`checks.run_valid`）

## T16 CPU 健康度（spec §17）
- [x] `process_time` 差算锁内 step CPU p95/p99/max，进 `ambient_steps.jsonl` 与 `attribution.json.cpu_metric`
- [x] 分项 CPU（progress_hash/write/flush、status_file）p95 进 status
- [x] 基线：锁内 CPU max 122–397ms（wall 2.2–3.5s 的坏窗口）

## T17 易失容量容器审计（spec §15）
- [x] 店铺出勤 → `Business.attendance_day`（持久）
- [x] 门派日结幂等 → `Faction.settle_day`（持久），显示层同源
- [x] 清单落 spec：memories 80 / day_history 7 / audit 4096 / probe 512 / debts/accounts 合格
- [x] 回归：清空记忆后出勤仍认、门派仍不重复发俸；legacy 哈希不变

## T18 lag 峰值定位（spec §19 待补）
- [ ] 定位 `lag_ticks_max=1176` 出现的 tick 与触发场景
- [ ] 说明 1200 上限触发 `ambient_catchup_truncated` 的丢步后果

## T19 6h 固定 commit 运行（spec §18）
- [ ] 固定 commit 的独立 worktree + `git rev-parse HEAD` 写死
- [ ] 磁盘 ≥5GB 原始输出；6h 内不加清理逻辑
- [ ] 参数 `--duration 21600 --players 50 --think-time 2 --warmup 600 --player-shop 1`
- [ ] 交付原始值：negative_rows、seq 连续、integrity_check、重启一致、九地点哈希、同 commit 全量 pytest
