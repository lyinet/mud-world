# Tasks · A4 长周期战略层（设计后待执行）

> 状态：设计稿已产出；以下为**待执行**任务，不在本任务中写实现代码。
> 总真实预算硬上限：**$2.25**（阶段 1 $0.25 + 阶段 2 $2.0）；累计 $2.5 必须停。
> 默认开关：**关闭**；不合并默认路径，除非阶段 2 全部门槛通过。

## T0 · 设计冻结（本任务）

- [x] 写 `specs/a4-strategist/spec.md`
- [x] 写 `specs/a4-strategist/tasks.md`
- [x] 只 stage 这两个文件，不使用 `git add -A`
- [x] 不改 `src/wsim/**`、不改 tag、不实现代码

## T1 · 阶段 0：离线 fake 端点（$0）

- [ ] 新增 fake `StrategyPlan` 夹具：3 天/8 NPC、10 天/31 NPC
- [ ] 实现 `src/wsim/sim/strategy.py` 的 schema 校验与消费接口（默认关闭）
- [ ] 实现 `src/wsim/agent/strategy_client.py` 的 record/replay 路径（fake 模式）
- [ ] `GoalSelector` 接入计划读取、3 日承诺窗口、G1/G3 护栏
- [ ] `tools/strategy_audit.py` 能计算 PCR/PIR/LHGA/CDC/CEG/PEG
- [ ] 单测：计划合法率 100%、规则动词泄漏 0、紧急覆盖 0、周期内换计划 0
- [ ] 回放：开启策略时 `event_hash` replay MATCH；关闭时 golden 不变
- [ ] 在 R 臂上零成本计算“规则隐式 3 日计划”精确基线，写入审计输出

**退出条件（全部满足才进入 T2）**
- 计划合法率 100%，PEG ≤20%，回放 MATCH，关闭开关 golden 不变。
- 失败处理：修 2 轮仍失败 → 判“战略产出物不可消费”，路线失败，转 `spec.md` 第 9 节替代方向。

## T2 · 阶段 1：小规模真实 run（3 天/8 NPC）

- [ ] 选 8 个 NPC（覆盖高/中/低活跃档），seed 固定
- [ ] day 0 每个 NPC 调用 1 次战略层，共 8 次
- [ ] 预算：估算 $0.08–0.12，硬上限 **$0.25**
- [ ] 记录 `strategy.jsonl`、成本、调用数、schema 合法率、PIR、PEG
- [ ] 跑 `tools/strategy_audit.py`，输出与 R 的同口径对照

**退出条件**
- schema 合法 ≥95%；至少 2 个计划条目完成；PIR ≤60%；K6 pass；单次成本 ≤$0.02。
- 停止：0 个计划条目完成、PIR >70%、schema <90%、或成本 >$0.25 → 停，不进 T3。
- 失败处理：如实标记未达，不用“优化”掩盖；预算剩余不自动滚动到 T3 以外。

## T3 · 阶段 2：一次对照（10 天/31 NPC/seed 42）

- [ ] S 臂：战略层 + 规则 planner，`content_hash=fac4ac31…`
- [ ] R 臂：直接复用冻结 `runs/a3-rule-10d-31npc-frozen`，不重跑
- [ ] 调用：124 次（day 0/3/6/9）
- [ ] 预算：**soft $1.5 / hard $2.0**；成本记账到 `summary.json`
- [ ] 中期检查（day 5）：累计成本、PCR、K1、PEG
- [ ] 终局检查：M1–M5 全部指标 + K1 护栏 + K6 含剪枝 + replay MATCH
- [ ] 输出：run 目录、summary、审计报告、失败项清单

**中期停止规则**
- 累计成本 >$1.5，或 PCR 低于 R 基线 20.51%，或 K1 <R−3pp → 立即停。
- 触发 soft/hard、K6 失败、replay 不一致、`content_hash` 变化 → 整轮无效，不降阈值、不重试。
- 仅当失败原因是实现 bug 且预算未用尽时，允许一次修复后重跑；不得用第二次机会调阈值。

**通过条件（全部满足）**
- PCR ≥30%（严格）/ ≥28%（最低）；`PCR_ex_one_shot` ≥R 基线
- PIR ≤45%（严格）/ ≤55%（最低）
- LHGA ≥1.10（严格）/ ≥1.00（最低）个/NPC/10 天
- CDC ≥0.90（严格）/ ≥0.88（最低）
- CEG ≤$0.20（严格）/ ≤$0.30（最低），且新增完成目标 ≥5 个
- K1 ≥R−2pp；K6 严格与含剪枝 pass；replay MATCH；`content_hash` 不变

## T4 · 报告与决策

- [ ] 按“已核实 / 推断 / 未达”三段式写报告
- [ ] 每个指标给计算方式、基线、实测、pass/fail，不使用模糊措辞
- [ ] 明确结论：`默认开启` / `仅实验` / `路线失败`
- [ ] 若失败，列出替代方向选择：内容扩充、对话/叙事层、离线内容生成、Spotlight 咨询
- [ ] 若通过，另立 72 天确认任务（预算 ≥$9，不在本轮）

## T5 · 明确不做

- [ ] 不做每 tick 战略调用
- [ ] 不做“叠加在现有 decision 选择器之上”（必须替代）
- [ ] 不把战略计划写入 `events.jsonl` / `WorldState`
- [ ] 不改 K1 分母、阈值或内容包
- [ ] 不改 tag、不 `git add -A`
- [ ] 不在阶段 0/1 失败后继续烧阶段 2 预算
