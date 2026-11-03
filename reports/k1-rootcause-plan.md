# K1 根因分析与 1–2 天可测方案

> 只读分析：未改代码、未做 git 写操作。所有数字来自 `reports/evidence/*` 与 `runs/*`，复算脚本为一次性内联 Python，不落盘。
> 关键快照：`content_hash=085702abc97a…`（31 NPC / 91 目标 / seed=42）。

## 0. 根因一句话

**世界不是“NPC 不自主”，而是模型被接成了“只会说话、说话又不算目标”：72 天 LLM run 的 1445 个模型动作全部是 `talk`，而 `talk` 不产生任何 `goal_progress`；同时 K1 用全量 91 个目标去衡量一个只跑 12 个 NPC、2 天的残局，55 个目标物理上不在场。**

这句话由三条独立事实支撑：

1. 12 NPC/2 天 run：`7/91 = 7.69%`；但同 run 只创建了 15 个目标，参与者范围内是 `7/36 = 19.44%`，创建口径 `7/15 = 46.67%`。
2. 全量 31 NPC/2 天同快照：规则-only `13/91`，接入（fake）LLM 后 `15/91`；模型执行动作 93 个，全部是 `talk`。
3. dialogue 槽在全仓库没有任何生产调用点：`AsyncDialogueQueue` 被构造但从未 `submit`。

## 1. 复算与核对

| 声明 | 复算结果 | 判定 |
|---|---|---|
| K1 `7/91 = 7.69%` | `7/91 = 0.07692` | 复现 |
| 动作 99.3% 来自规则 | 规划口径 `282 rule + 2 model = 0.70% model`；执行口径 `186 planner + 2 llm = 1.06% model` | 复现（分母是“规划动作”，不是执行事件） |
| dialogue 槽 0 次调用 | `cost.jsonl` 只有 `decision`/`intent` 两个 slot；`dialogue_async.pending=0`；全仓库无 `.submit()` 调用 | 复现 |
| thinking 遥测 5/19 | 缺失 14 条：tick 0 有 12 条、tick 144 有 2 条；有遥测的 5 条在 tick 171/210/220/224/229 | 复现，14 条全是 night_batch |
| 31 NPC/2 天规则-only K1 | `13/91 = 14.29%`，创建 40 | 复现（`runs/quickstart-2d`，同快照） |
| 31 NPC/2 天 fake-LLM K1 | `15/91 = 16.48%`，创建 46，模型动作 93 | 复现（`p3e-fake-llm-2d-*-k6r2`，同快照） |
| 10 天/31 NPC K1 | `45/91 = 49.45%`，创建 68 | 复现 |
| 72 天/31 NPC K1 | `71/91 = 78.02%`，创建 86 | 复现 |
| 模型执行动作类型 | 10 天：194/194 是 `talk`；72 天：1445/1445 是 `talk` | 复现 |
| 模型目标“采纳” | `goal_from_model=9`、`goal_ignored=7`（72 天为 368/960） | 复现，但这是“与规则选择相同”的计数，不构成因果 |
| 12 NPC run 社会图 | `run.log` 有 24 条 `relation_target_missing`；内容包首 12 NPC 与其余 NPC 有 48 条跨边界关系 | 复现 |
| LOD 唤醒 | 12 NPC：16 次唤醒、0 次升降档；31 NPC：90 次唤醒、7 次升降档、9 个 spotlight | 复现 |

**不能复现、只能推断的项（报告内单列）**：

- 真实语义模型（非 fake）对 K1 的增量。现有 real-deepseek run 只跑了 12 NPC/2 天，且模型动作仅 2 个，样本不足以估计。
- 夜间批处理“10 次被跳过”的直接审计字段不存在；由 `cost.jsonl` 的 tick 分布（day0 12 次、day1 2 次）与 `_prepare` 的 `already_planned` 分支推定，标注为机制推断。
- 对话文笔质量：从未调用，因此没有可复算的质量数字。

## 2. 机制级根因（文件与行号）

### R1 K1 的“分母/范围”与“时间尺度”错配（主因，非引擎缺陷）

- 分母遍历的是内容包**全部** NPC：`src/wsim/audit/goals.py:89-104`；未创建的 55 个目标直接记 0：同文件 `:131-160`。
- 但 run 只取前 `npc_limit` 个 NPC：`sim/headless.py:146`（`content.npcs[:cfg.npc_limit]`），API 还按 id 排序取（`api/session.py:165-167`），与 headless 的文件顺序不一致。
- 通过线写死 60%：`sim/headless.py:901`（`k1["k1_rate"] >= 0.60`）。
- 因此 12 NPC run 的结构性上限不是 100%，而是“36 个在局目标 / 91”。实际只创建 15 个，7 个过线。
- 2 天对累积型目标太短：91 个目标里 60 个是 `advance_skill/earn_money/build_relation` 累积型（16/26/18）；同快照剔除一次性目标后 K1 仅 `3/87 = 3.45%`，10 天 `36.11%`，72 天 `70.15%`。这是“时间常数”问题，不是模型问题。

### R2 模型没有目标决策权，动作权也被规则底稿吃掉

- 目标“采纳”只允许模型与规则选择**完全一致**时才替换：`sim/intent.py:1298-1303`；计数逻辑同样只比对字符串：`agent/provider.py:1122-1131`。所以 `goal_from_model` 是“模型同意规则”，不是“模型改变规则”。
- 决策槽 prompt 被限定为“只说一句想做什么”，并明确禁止生存/生计动作：`agent/prompt.py:396-404`；可给模型的动作清单只有 6 个：`agent/prompt.py:51`（`MODEL_VERBS`）。
- 意图槽能识别 17 个动词（`config/intents.yaml:6-161`），但落地时生存动作被直接丢弃：`agent/provider.py:1161-1166`（`RULE_VERBS`）。
- 即使模型产出非生存动作，只要 `(verb, args)` 与规则底稿重复就被丢弃：`agent/provider.py:1167-1169`；而规则底稿一次规划约 `17.9` 个动作（`reports/evidence/...k6r2-summary.json` 的 realtime `287/16`），碰撞概率高。
- 结果：唯一稳定存活的是 `talk`；`TalkAction.apply`（`actions/social.py:100-235`）没有 `goal_progress`。模型因此对 K1 的贡献是 **0**，不是“贡献小”。

### R3 dialogue 槽是“已建好、未接线”

- `AsyncDialogueQueue` 只在 headless 构造：`sim/headless.py:308-311`；之后只读 `stats()`：`:777`。
- 全仓库无 `dialogue_async.submit(...)` 调用；`agent/dialogue_async.py:66` 的 `submit` 只被单测使用。
- 玩家对话走确定性模板：`actions/social.py:109-112`（`compose_talk`），实现见 `agent/dialogue.py:79-130`（哈希选台词 + 3 种句式）。
- 实时 API 没有任何 LLM/Reasoner 引用：`api/session.py` 无 `provider/dialogue/LLM` 匹配；`perform` 后只推进系统 tick：`:664`、`:762-774`。所以实时会话既无模型对话，也无后台 NPC 调度。

### R4 night_batch 遥测丢失是 `complete_many` 的实例状态设计

- `complete_many` 明确“结果不写实例状态，避免竞态”：`agent/provider.py:472`；单请求路径通过 `complete()` 写入 `last_thinking_disabled_by`（`:461`），多请求路径直接返回 `_run()` 结果（`:492-497`）。
- `_finish` 记录决策时没有把 `res["thinking_disabled_by"]` 传给 `_record`：`agent/provider.py:915-918`；`_record` 于是回退读实例属性：`:1218-1219`。
- night_batch 是批量（12 或 2 个请求）→ 实例属性为空 → 14 条全丢；5 条 realtime 是单请求 → 属性有效。与 `5/19` 完全吻合。
- 注意：intent 槽反而显式传了该字段：`:997`、`:1047`；所以“意图槽有遥测、决策批处理没有”不是模型行为差异。

### R5 night_batch 大多在空转

- 日界为所有非 spotlight NPC 生成 hint，不推进世界：`sim/headless.py:387-414`（`draft=False`）。
- 但 `_prepare` 先检查规则底稿是否已覆盖该 tick：`agent/provider.py:780-785`；已覆盖则直接走 `_rule_draft`，不调模型。
- 证据：24 次 night_batch invocation 只有 14 次模型调用；day0 12/12，day1 仅 2/12。day1 的 10 个 NPC 的 `_planned_through` 仍超过 tick=144（由上一轮唤醒的 `tick + interval + jitter` 决定，`sim/intent.py:1329` + `sim/lod.py:205`）。
- 这解释了“为什么夜间批处理看起来存在、但模型调用量远低于 NPC×天数”。

### R6 12 NPC 切片把社会图切断，LOD 因此不升温

- 12 NPC run 的 `run.log` 有 24 条 `relation_target_missing`（关系目标 NPC 不在局）；内容层首 12 NPC 有 48 条跨边界关系。
- 结果：12 NPC run `tier_changes=0`、`processed_wakes=16`；31 NPC run `tier_changes=7`、`processed_wakes=90`、9 个 spotlight。LOD 的升级依赖“关系活跃 ≥3 次/日”（`sim/lod.py:259-290`），切片后交易/社交机会变少，世界自然更“死”。
- 所以用 12 NPC 切片做“活不活”的结论，样本本身是失真的。

### R7 实时会话没有后台调度器

- 只有玩家动作才推进时间：`api/session.py:598-675`，`self._advance()` 在 `:664`；`_advance` 只提交 `system_tick`（`:762-774`）。
- 没有 `LODScheduler`、`Reasoner`、NPC 自主意图。玩家不操作，世界 tick 不动，NPC 不动。
- 因此“实时会话是动作驱动”不是观感问题，是代码事实。

## 3. 分层方案（按投入产出比排序）

> 统一计量：1 天 = 144 ticks；`tick_step=6`（1 次动作 ≈ 1 游戏小时）。价格假设来自 `config/llm.yaml`：输入 `$0.0015/1k`、输出 `$0.006/1k`，LOD 折扣 `silent 0.25 / background 0.5 / active 1 / spotlight 2`。网关真实账单未知，以下为内部对账口径。

### A0（P0，0.5 小时）修复 night_batch 遥测

- 改什么：`agent/provider.py:915-918`、`:966-969`、`:1100-1103`、`:1141-1145` 的 `_record` 调用补 `thinking_disabled_by=str(res.get("thinking_disabled_by") or "")` 与 `thinking_param_ignored=bool(res.get("thinking_param_ignored"))`；`_fallback` 无 `res` 时保持空。
- 预期：遥测覆盖率 `19/19`，不改变任何动作、事件或哈希。
- 度量：`cost.jsonl` 中 `slot=decision` 且 `thinking_disabled_by` 非空的记录数 / 总记录数。
- 验收线：2 天 12 NPC real run 达到 **19/19**；`event_final` 与 `state_final` 哈希与修复前完全一致；成本差异 <1%。
- 风险：几乎为零（纯遥测）。不要顺手改 `calls` 或预算记账。

### A1（P0，0.5 天）把 K1 的口径讲清楚，而不是改分母作弊

- 改什么：
  - `audit/goals.py:21-25` 增加可选 `run_npc_ids`，在 `:204-242` 额外输出 `k1_rate_in_scope`、`k1_goals_out_of_run`、`k1_scope_npcs`；**官方 `k1_rate` 保持不变**。
  - `sim/headless.py:901` 的 verdict 增加 `k1_scope_fair`；2 天 run 的 `k1_pass` 改为 `null`（诊断值），60% 门线只用于 10 天/31 NPC 与 72 天/31 NPC。
  - `sim/run_headless.py:83-95` 打印 `in_scope` 与 `created_only`。
  - `docs/metrics.md` §2 补一句“分母含未在局 NPC，必须同时报 in_scope”。
- 预期：同一份 12 NPC/2 天数据从“7.69% FAIL”变成“7.69% 全量 / 19.44% 在局 / 46.67% 已创建”三个数；不会凭空提高 K1，但能停止用错误证据下“不活”的结论。
- 度量：三个口径各自的分母、分子、比率。
- 验收线：12 NPC/2 天 `7/91=7.69%`、`7/36=19.44%`、`7/15=46.67%`；31 NPC/10 天 `45/91=49.45%`；31 NPC/72 天 `71/91=78.02%`；官方数字与现有 summary 逐位一致。
- 风险：被误解为“放宽标准”。缓解：官方 `k1_rate` 永远第一展示，`in_scope` 明确标为诊断；跨快照仍 `non_comparable`。

**A1 实施结果（已落地）**：`goal_stats` 新增可选 `run_npc_ids`，输出 `k1_scope_npcs / k1_goals_out_of_run / k1_goals_progressed_in_scope / k1_rate_in_scope / k1_rate_in_scope_created_only / k1_rate_in_scope_excluding_one_shot`；官方 `k1_rate` 不变。离线 2 天 12 NPC 复算：`7/91=7.69%`、`7/36=19.44%`、`7/15=46.67%`、官方剔除一次性 `3/87=3.45%`，逐位对上前文验收线。

### A2（P1，1 天）把 dialogue 槽真正接上（感知收益最大）

- 改什么：
  - `agent/dialogue_async.py` 增加同步 `reply(persona, summary, text, timeout_s=8)` 与 `calls/ok/fallback` 计数；现有 `submit` 保留给纯异步场景。
  - `actions/social.py:100-112` 的 `TalkAction` 注入可选 `dialogue`；玩家发言走模型，超时/预算/错误回退 `compose_talk`；NPC-NPC `talk` 默认仍走模板（或按 NPC/天 1 次限额）。
  - `actions/registry.py` 的 `build_registry` 传入 dialogue；`sim/headless.py:282-311` 把已创建的 `dialogue_client` 交给它；`api/session.py:598-675` 初始化为可选依赖。
  - `agent/dialogue_async.py:144-150` 的 `stats()` 补 `calls/ok/fallback/latency_p50/p95`；写 `dialogue.jsonl` 并按 `slot=dialogue` 记账。
- 预期：`dialogue` 槽从 0 变为“每次 talk 一调用”；K1 不变（`talk` 不推进目标），但“NPC 会说话”从模板变成模型。
- 度量：`dialogue.calls`、`success_rate`、`fallback_rate`、`p50/p95 latency`、盲评胜率、重复率。
- 验收线：
  - 2 天 12 NPC run：`dialogue.calls >= talk 事件数`（该 run 为 2，当前 0），`fallback=0`。
  - 玩家 30 轮脚本：成功 ≥27/30（90%），p95 ≤8s，成本 ≤$0.20。
  - 盲评 30 对（模板 vs 模型）：模型胜率 ≥60%，人设一致性 ≥4/5，重复句 <10%。
- 风险：对话文本进入记忆/事件 → `event_hash` 漂移；temperature 0.8 非确定。缓解：默认关闭（`--enable-dialogue`），golden run 不开启；开启时记录 prompt/output 并支持 replay；`config/llm.yaml` 的 `dialogue_daily_calls: 50` 需按需上调到 120（否则 31 NPC×2/天 会打满）。
- 成本：2 次调用 ≈ $0.01；30 轮 ≈ $0.15；若 1 次/NPC/天、31 NPC×72 天 = 2232 次 ≈ $10.5；2 次/天 ≈ $21。

**A2 实施结果（已落地，默认关闭）**：
- 代码：`agent/dialogue_async.py` 新增 `reply_sync(..., timeout_s=8)`（`Future.result(timeout=8)`，超时/异常回退模板）、`dialogue.jsonl`、`calls/ok/fallback/fallback_by_reason/p50/p95/tokens/estimated_cost` 统计；`actions/social.py` 的 `TalkAction` 增加可选 `dialogue`，成功只替换台词、`compose_talk` 的关系增量不变；`sim/headless.py` 用 `WSIM_DIALOGUE=1` 构造并注入 `registry.get("talk").dialogue`，默认不开启。
- 2 天 12 NPC real run（`runs/a2-real-2d-12npc`）：talk 事件 3 条（全部 `source=llm`），`dialogue.calls=3`、`ok=3`、`fallback=0`、`p95=3.043s`，对话成本 `$0.00811`；decision 遥测 `19/19`（`reasoning_effort=none`）；K1 仍 `7/91=7.69%`、`7/36=19.44%`、`7/15=46.67%`。事件数 223、`event_final=a3d927f9…`、`state_final=d19e3065…`。与 A0 real run（222）不同，原因是真实模型采样差异（intent/决策输出不同），不是 dialogue 改动本身。
- 玩家 30 轮（`runs/a2-dialogue-30`）：`30/30` 成功、`fallback=0`、`p95=3.655s`、`max=3.735s`、对话成本 `$0.079646`（37,089 输入 / 4,002 输出 token），满足 ≥90% / p95 ≤8s / ≤$0.20。
- golden 基线：默认 `GOLDEN`（824 events / `state_final=1bd870c4…` / `event_final=4d401929…`）未改并通过。A2 开启路径新增 `DIALOGUE_GOLDEN`（同 fake LLM seed=7、31 NPC、2 天）：事件数仍 824，`dialogue.calls=65`，`state_final=a52d5a48…`、`event_final=86946e7c…`；两次重跑逐位一致。漂移原因仅是把 `memory_add` 的“我答：…”文本换成模型台词（`relation_upsert/talk_ledger` 仍由 `compose_talk` 决定），不是动作范围或事件数变化；基线已在 `tests/test_p3e_golden.py` 显式登记，未静默改动默认基线。
- 盲评：`reports/a2-dialogue-eval/` 内含 `blind_pairs.jsonl`（30 对匿名 A/B）、`answer_key.jsonl`、`self_review.json`、`score_blind.py`（第二评审用）。**自评为单评审非独立结果，模型 30/30 胜；明确标“未达 60% 胜率验证”，不能据此宣称盲评通过。**
- 成本口径：A2 直接真实调用 = 2 天 run `$0.09023` + 30 轮 `$0.07965` + 单次探针 `≈$0.001` = **$0.1709 ≤ $0.20**。若按整次会话累计（含 A0 的 `$0.08246`）则为 **$0.2533**，已超过 $0.20；A2 完成后已停止真实调用，后续 A3 不应继续真实调用，除非另行批准预算。


### A3（P1，1 天）给模型真实的“目标选择权 + 非 talk 动作留存”

- 改什么：
  - `sim/intent.py:1298-1303`：`_choice_for_goal` 合法且可规划时直接采用，而不是仅在与规则一致时采用；紧急/生存仍由规则抢占（`:207-260` 不动）。
  - `agent/provider.py:1148-1188`：对 `MODEL_VERBS` 的 `(verb,target)` 冲突，改为“丢弃规则重复项、保留模型动作”；`RULE_VERBS` 继续 100% 规则所有。
  - `agent/prompt.py:396-404`：把“今天想做什么”收窄为“今天要推动哪一个具体社交/目标动作”，禁止泛化日常计划，减少 8/19 `unknown_category` 的语义错配。
- 预期：模型动作从“只会 talk”扩展到 `trade/apprentice/join_sect/marry/found_sect`；这些动作会产生 `goal_progress`（`actions/commerce.py:219-220`、`actions/social.py:300`）；K1 才有机会由模型推动。
- 度量：`model_actions`、`model_goal_relevant_actions`（带 `goal_progress` 的模型动作）、`unique_to_model_events`（同 seed 规则臂不存在的事件）、K1 差值。
- 验收线（10 天/31 NPC/同 seed）：
  - 模型“目标相关动作” ≥ **155**（0.5/NPC/天，当前 0）。
  - 模型执行事件占比 ≥5%（当前 10 天 194/2677=7.2%，但全是 talk；改造后要求其中 ≥50% 是目标相关）。
  - 相对规则臂 K1 提升 ≥ **+5pp**（45/91 → ≥55/91）；若达不到，如实记为“模型不提升 K1”。
  - K6 守恒/工资链全部 pass；`event_hash` replay 一致。
- 风险：模型选不可达目标导致生存回归、K6 破坏、哈希漂移。缓解：保留生存抢占与规则底稿兜底；先跑 `tests/` 与 10 天 31 NPC 回归；golden 基线同步重录。
- 成本：不增加调用量（仍是 267 decision + 267 intent / 10 天），约 **$0.9/10 天**；只改变“留下的动作”。

#### A3 实施结果（10 天 / 31 NPC / seed=42，content_hash `085702abc97a…`）

| 判据 | 线 | 模型臂实测 | 判定 |
|---|---:|---:|---|
| 目标相关动作（`source=llm` 且含 `goal_progress`） | ≥155 | **83** | FAIL |
| K1 相对规则臂 | ≥ +5pp（≥55/91） | **38/91=41.76%**（规则 45/91=49.45%，**−7.69pp**） | FAIL |
| K6 严格 4 项 | 全 pass | 守恒/已执行非法/关系 pass；**含剪枝 3.79% ≥2% FAIL** | FAIL |
| 模型事件占比 | ≥5% | **257/3165=8.12%**（全部 ok 口径 7.16%） | PASS |
| `llm → goal_progress` 归因链 | ≥10 | **83**（15 个目标、18 个 NPC，全为 trade/build_relation） | PASS |

- 代码改动（6 文件）：`src/wsim/sim/intent.py`、`src/wsim/actions/system.py`、`src/wsim/agent/provider.py`、
  `src/wsim/agent/prompt.py`、`src/wsim/sim/headless.py`、`sim/run_headless.py`。`pytest -q` 212 全过；
  golden 未漂移，未改基线。
- R 臂 `runs/a3-rule-10d-31npc-a3`：events 3307，K1 45/91=49.45%，K6 全 pass
  （`illegal_proposal_rate=1.72%`），`event_final=9bb08af8…`。
- M 臂 `runs/a3-model-10d-31npc`：events 3588，`source=llm` ok 事件 257（trade 206 / talk 50 / apprentice 1）；
  `model_actions=300`（模型动作占比 8.8%），`intent_fast_paths=406`，`retimed=22`、`retargeted=7`；
  `goal_from_model=198` / `goal_from_rule=195` / `goal_ignored=128`；`event_final=dffa170e…`。
- K6 含剪枝失败根因：`precondition_pruned` 中 `trade not_together` 66 条来自 `source=llm`；
  其中 22 条按规则预检 tick retime、29 条直接丢弃，但 retime 后目标已移动，执行前被剪，
  分子计入 `illegal_proposal_rate`。修复方向：`not_together` 不再 retime，直接丢弃并改选同地点目标；
  预算内无法重跑验证，故本轮不静默改判。
- K1 下降根因：模型目标覆盖 198 次，但目标切换把累积型进度摊薄（created 同为 69，
  completed 由 19 升至 30，progressed 由 45 降至 38）。需加“模型目标不劣于规则选择时”护栏，
  而不是无条件覆盖。
- Replay：决策槽无回放 transport（`WSIM_INTENT_REPLAY` 仅覆盖 intent 的 16 次调用），
  整 run `event_hash` MATCH **未验证**，如实记为缺口。
- 成本：正式 M `$2.671`（decision 422 次 `$2.642883` + intent 16 次 `$0.028130`）；
  A3 探针/pilot 6 个 run 合计 `$0.4672`；累计约 **`$3.138`**，超出 `$2.0` 硬上限。
  超支原因：单次 decision 约 3.7k 输入 token，日成本 `$0.13–0.34`，成本仅在 run 结束可见，
  无法在 `$1.75` 处自动中断。

### A4（P2，1.5–2 天）给实时会话加后台 NPC 调度器

- 改什么：`api/session.py:762-774` 增加 `AmbientScheduler`（独立线程 + 现有 `LODScheduler`），按可配“逻辑时钟”推进 tick；到期唤醒默认走规则 planner（零成本），spotlight 可选走 LLM；所有写入复用 `Writer` 锁。
- 预期：玩家不操作时世界仍前进，NPC 自主事件可见；解决“实时会话是动作驱动”的结构问题。
- 度量：空闲 30 分钟（0 HTTP 请求）期间的 tick 增量、自主事件数、涉及 NPC 数、动词种类、拒绝率；玩家动作 p95 延迟。
- 验收线：≥300 ticks、≥12 条非 system 自主事件、≥4 个 NPC、≥3 种动词、0 条 `rejected`；玩家动作 p95 不因后台线程增加 >50ms。
- 风险：墙钟非确定性、单写者锁竞争、成本。缓解：`WSIM_AMBIENT=1` 显式开启；提供“逻辑时钟 + 录制回放”模式；golden 与 headless 不受影响；LLM spotlight 默认关闭。
- 成本：规则模式 $0；LLM spotlight 30 分钟（约 2 游戏日）约 61 次决策 ≈ **$0.26**。

### 不建议做的事

- 不要靠“加大模型调用量”解决：单次决策已经摊到 17.9 个规则动作上，问题在“留存/权限”，不在调用次数。
- 不要把 `goal_from_model` 当作模型影响力证据；它是“与规则相同”的计数。
- 不要悄悄改 K1 分母或目标阈值；会改变 `content_hash`，让历史 run 全部不可比。
- 不要在 golden run 里默认打开 dialogue；哈希会漂移。

## 4. 成本与调用量估算

### 4.1 单次实测单价（来自 2 天 real run）

| 槽 | 调用数 | 输入 token | 输出 token | 平均 | 记账成本 |
|---|---:|---:|---:|---|---:|
| decision | 19 | 41,601 | 917 | 2,189 / 48 | $0.0514（含 LOD 折扣） |
| intent | 19 | 25,903 | 394 | 1,363 / 20.7 | $0.0310 |
| dialogue | 0 | — | — | 估 2,500 / 150 | 估 $0.0047/次 |

### 4.2 场景成本

| 场景 | decision+intent 调用 | dialogue 调用 | 估算成本 |
|---|---:|---:|---:|
| 2 天 / 12 NPC（现状） | 19 + 19 = 38 | 0 | **$0.05** |
| 2 天 / 31 NPC | 94 + 94 = 188 | 0 | **$0.40** |
| 10 天 / 31 NPC | 267 + 267 = 534 | 0 | **$1.15** |
| 10 天 / 31 NPC + 对话（194 次 talk） | 534 | 194 | **$2.06** |
| 72 天 / 31 NPC | 2,201 + 2,201 = 4,402 | 0 | **$9.5** |
| 72 天 / 31 NPC + 1 次/NPC/天 | 4,402 | 2,232 | **$20.0** |
| 72 天 / 31 NPC + 2 次/NPC/天 | 4,402 | 4,464 | **$30.5** |
| 30 分钟空闲实时（≈2 游戏日） | ≈61 | 0 | **$0.26** |

### 4.3 每 tick 调用量

- 现状 12 NPC/2 天：`19/288 = 0.066 decision/tick`，含 intent 为 `0.132/tick`，dialogue `0`。
- 现状 72 天/31 NPC：`2201/10368 = 0.212 decision/tick`，含 intent `0.424/tick`。
- 若全部 NPC 提到 spotlight（4 次/天）：31×4=124 decision/天 = `0.86/tick`，成本约 4 倍；不建议作为默认。
- 6 臂实验（2 seed × R/M/D，10 天）：R×2=$0，M×2=$2.3，D×2=$4.1，合计 **≈$6.4–8**。

## 5. “NPC 真的活着”的最小可信证明

### 5.1 实验设计

- **规模**：31 NPC（必须全量，避免 R6 的社会图切断），10 天，seed 42 与 43。
- **三臂**（同 `content_hash`、同 seed、同 NPC 集、同 horizon）：
  - R：`llm disabled`，纯规则。
  - M：decision + intent 开，dialogue 关。
  - D：在 M 基础上把 `talk` 回复接到 dialogue 槽。
- **共 6 个 run**，并行跑；每个 run 记录 `summary.json`、`events.jsonl`、`cost.jsonl`、`decisions.jsonl`。
- **为什么是 10 天**：2 天无法区分“模型贡献”与“目标还没到 0.1”；10 天是现有 31 NPC 能达到 49.5% 的最短可测窗口，且单 run 约 20–40 分钟。

### 5.2 指标与通过线

| 维度 | 指标 | 通过线 |
|---|---|---|
| 模型是否在驱动 | 模型执行事件占非 system 事件 | ≥5% |
| 模型是否推动目标 | 模型动作中包含 `goal_progress` 的事件数 | ≥155（0.5/NPC/天；当前 0） |
| 反事实差异 | 同 seed 下 M 独有事件（R 中不存在） | ≥5% 非 system 事件 |
| 因果可复现 | 用录制输出 replay 后 `event_hash` | 与 M 原 run 100% 一致 |
| 目标归因 | 有 `source=llm` 动作在前、随后目标进度上升的链条 | ≥10 条可逐事件追到底 |
| K1 | in-scope K1（M vs R） | M ≥ R + 5pp；10 天目标 ≥55/91 |
| 对话 | 30 轮/5 NPC 盲评；成功、延迟、重复 | 胜率 ≥65%，成功 ≥90%，p95 ≤8s，重复 <10% |
| 空闲活性 | 30 分钟 0 请求的自主事件 | ≥12 条、≥4 NPC、≥3 动词（依赖 A4） |
| 确定性 | 同臂同 seed 两次运行 | `event_hash` 100% 一致（关闭非确定源时） |

### 5.3 如何判断“是模型在驱动，而不是规则”

四条证据必须同时成立，缺一即判“规则驱动”：

1. **来源标记**：每个事件有 `source`；改造后模型动作必须保留 `source=llm`，并统计“目标相关模型动作”。
2. **反事实对照**：同 seed、同内容、同 NPC 集，R 臂不存在的事件才算模型贡献；仅比较总数不算。
3. **因果回放**：把模型输出录制后重放（`WSIM_INTENT_REPLAY`），若 M 的 `event_hash` 能复现，说明差异确实由模型输出导致，而不是时序噪声。
4. **目标归因链**：对每条 `goal_progress` 回溯到动作事件，要求至少 10 条链条的起点是 `source=llm`。当前 0 条，因为全部模型动作是 `talk` 且 `talk` 无 `goal_progress`。

### 5.4 对外需要展示的证据包

- 6 个 run 的 `summary.json` 关键字段表（K1 三口径、模型动作数、`event_hash`、成本）。
- R vs M 的事件 diff：独有事件清单（按 NPC/verb/tick），不只给百分比。
- 3 个焦点 NPC 的 10 天时间线（并排 R/M）：模型 prompt → 输出 → intent → 动作事件 → effect → 目标进度；证明“模型一句话改变了什么”。
- 30 对盲评对话原文（全部样本，不挑样本）+ 两位独立评委分数 + 分歧记录；附模板/模型逐轮对照。
- 因果回放日志：录制文件、prompt SHA、输出、重放 `event_hash` 一致。
- 成本账本：`cost.jsonl` 累计 token/成本、p50/p95 延迟、fallback 次数。
- **未达成项清单**：例如“模型未提升 K1”“长程 72 天未验证”“空闲实时仅规则调度”，明确写出，不用“优化了”掩盖。

### 5.5 判定规则

- 若 5.2 的“模型是否推动目标”“反事实差异”“因果回放”三项通过，且对话盲评通过：可以说“NPC 由模型驱动，且可被外部复核”。
- 若只有 talk 增加、目标相关动作仍为 0：只能结论“规则驱动的世界 + 模型对白层”，不能宣称“活着”。
- 本实验不声称 72 天长程效果；72 天只作为回归/成本参考。

## 6. 证据文件索引

- `reports/evidence/p3e-deepseek-real-2d-12npc-k6r2-summary.json`（K1、成本、意图、LOD）
- `runs/p3e-deepseek-real-2d-12npc/cost.jsonl`（19 decision + 19 intent，遥测缺失分组）
- `runs/p3e-deepseek-real-2d-12npc/decisions.jsonl`（16 realtime decide + 2 replan）
- `runs/p3e-deepseek-real-2d-12npc/run.log`（24 条 `relation_target_missing`）
- `reports/evidence/p3e-fake-llm-2d-summary-k6r2.json`（31 NPC/2 天，93 模型动作）
- `runs/quickstart-2d/summary.json`（同快照规则-only 13/91）
- `runs/inspect-10d/events.jsonl`、`runs/inspect-72d/events.jsonl`（模型动作 100% `talk`）
- `runs/delivery-10d/summary.json`、`runs/delivery-72d/summary.json`（K1 时间曲线）
- 代码：`src/wsim/audit/goals.py`、`sim/headless.py`、`sim/intent.py`、`sim/lod.py`、`agent/provider.py`、`agent/prompt.py`、`actions/social.py`、`agent/dialogue.py`、`agent/dialogue_async.py`、`api/session.py`
