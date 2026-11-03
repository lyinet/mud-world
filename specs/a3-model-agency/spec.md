# Spec · A3 模型目标选择权 + 动作留存

> 来源：`reports/k1-rootcause-plan.md` §A3。预算上限 **$2.0**（含 10 天/31 NPC 真实跑约 $1.15 与重试余量），
> 累计估算接近 **$1.75** 必须先停下评估，不得跑超。
> 目标：让模型选出的目标真正进入规则规划器，并让模型社交动作在 `(verb,target)` 层面优先于规则重复项；
> 计划 prompt 收窄为“今天推动哪一个具体目标动作”，减少 `unknown_category` 语义错配。

## 0. 非目标 / 硬约束

- 不碰 `deploy/README.md`、`docs/QUICKSTART.md`、`docs/DELIVERY-TEST*.md`、`docs/known-limitations.md`；不改写任何 tag。
- 紧急/生存抢占仍由规则负责（`GoalSelector._urgent` 不动）；`RULE_VERBS` 仍 100% 规则所有，模型不得替换。
- 不降低验收线；K6 严格与含剪枝口径必须全 pass；`event_hash` replay 一致。
- golden 基线若漂移：新增独立基线并在报告写明漂移来源，不许静默改旧基线。
- 盲评 30 对由独立评审负责；本任务只保证盲评包（隐藏标签）已落盘并给出路径。

## 1. 五条验收判据（10 天 / 31 NPC / 同 seed）

1. **目标相关动作**：`source=llm` 且事件 `effects` 含 `goal_progress` 的 ok 事件数 **≥155**（当前 0）。
2. **K1 提升**：模型臂相对纯规则臂 K1 **≥ +5pp**（官方口径 `goals_progressed/91`）。
3. **K6**：严格口径与含剪枝口径全部 pass（`k6_conservation_pass` / `k6_illegal_executed_pass` /
   `k6_illegal_incl_pruned_pass` / `k6_relations_pass`）。
4. **模型事件占比**：`source=llm` 的 ok 非 system 事件 / 全部 ok 事件 **≥5%**。
5. **归因链**：`source=llm → goal_progress` 可追条数 **≥10**，每条能定位到 goal_id、npc、verb、tick；
   落盘 `model_goal_chains.jsonl`，summary 内给计数与样本。

## 2. 代码改动（最小集）

### 2.1 `src/wsim/sim/intent.py`

- `MockReasoner.decide`（约 1297-1305）：`goal_override` 合法且可规划时**直接采用**，不再要求与规则选择器一致。
  - 仅当 `choice.source != "urgent"` 时才允许覆盖；紧急目标继续规则抢占。
  - 覆盖时 `replace(choice, source="model", reason="模型选择今日目标")`，保留 `create`/`abandon` 语义。
- `MockReasoner._choice_for_goal`（约 1367-1387）：
  - 支持“内容包里存在但 world 尚未创建”的目标：按 `self.selector._content_goals[npc]` 查 `content_goal_id`，
    构造 `GoalChoice(create=True, source="model")`，`kind=goal_kind(ctype)`、`target=goal_target(content, kind)`。
  - 可规划校验：`content_type in CONTENT_GOAL_TYPES` 且非 `completed/abandoned`；否则返回 `None`。
  - 已存在目标维持 `create=False`，避免清零进度。

### 2.2 `src/wsim/actions/system.py`

- `SystemGoalAction.apply`（约 460 行）：`source` 映射修正为
  `"urgent"` 当且仅当 `goal_source=="urgent"`，其余（`content`/`model`）一律 `"npc"`。
  否则模型创建的目标会被标成 urgent，被 `goal_stats` 的 `state_rows` 过滤，K1 分母反而变差。

### 2.3 `src/wsim/agent/provider.py`

- `_filter_model_actions`（约 1152-1192）改为返回 `(draft, kept)`：
  - `RULE_VERBS`、`not_model_scope`、重复模型动作的丢弃逻辑不变。
  - 对 `MODEL_VERBS` 的冲突键从完整 `(verb, args_key)` 改为 **`(verb, target)`**
    （`target = args["target"]`；`found_sect` 用 `args["at"]` 或 `sect_id`）。
  - 命中规则重复项时：从 `draft` 中删除该条规则动作，**保留模型动作**（`dropped_by_reason` 记 `rule_dup_replaced`）。
  - 前置条件校验与 `model_actions` 计数不变。
- 三处调用点改为 `draft, kept = self._filter_model_actions(...)`，返回 `draft + kept`：
  `_finish`（约 930）、`_finish_hint`（约 939）、`_finish_legacy`（约 968）。

### 2.4 `src/wsim/agent/prompt.py`

- `DECISION_SYSTEM_PROMPT`（约 396-404）收窄：
  - 只允许从模型可执行社交动作中选一个具体动作：
    `trade / apprentice / join_sect / marry / found_sect / talk`；
  - `goal` 必从 `goal_candidates` 选（没有合适目标填 null）；
  - `intent` 必须是一句“对谁做什么”的具体动作，`target` 用 `nearby_npcs` 里的名字；
  - 明确禁止泛化日常计划（巡视/摆摊/看病/练手艺/赶路/打听等），这些由规则调度器负责。

### 2.5 `src/wsim/sim/headless.py`（只加观测）

- 事件审计后新增 `model_agency` 汇总：
  - `model_events`、`model_event_share`、`model_goal_relevant_actions`、`attribution_chains`、
    `model_goal_driven`（可选：goal.source=="model" 的 planner 事件数）。
  - 把 `source=llm` 且含 `goal_progress` 的事件写到 `out_dir/model_goal_chains.jsonl`
    （字段：`seq/tick/day/npc/verb/goal_id/content_type/delta/reason`）。
- `summary["model_agency"]` 暴露给 CLI/report；不改变事件流。

## 3. 两臂对照

- 同 seed（42）、同天数（10）、同 NPC 数（31，`--npc 0`）。
- **R 臂**：`python3 sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id a3-rule-10d-31npc`（不烧钱）。
- **M 臂**：同参数 + `--enable-llm`，`run-id a3-model-10d-31npc`；网关 `http://127.0.0.1:3425/v1`，
  `LLM_API_KEY=sk-test`；decision/intent 模型 `deepseek/deepseek-flash`，`WSIM_DIALOGUE` 默认关闭。
- 输出：两臂 K1（三口径）、事件数、`source` 分布、K6、`model_agency`、事件 diff。

## 4. 成本与记账

- 每个请求 token/成本写 `runs/<id>/cost.jsonl`（已有 CostTelemetry）。
- 逐请求汇总脚本/命令：按 `slot` 求和 `estimated_cost`、token 数、调用数。
- 软阈值：累计估算 **≥$1.75** 立即停止并在报告说明；硬上限 $2.0。
- 预算护栏：decision 日 token 上限 200k、intent 日调用 2000（config/llm.yaml 默认），10 天 31 NPC 预计 534 次。

## 5. 验证命令

```bash
.venv/bin/python -m pytest tests/test_p3e_golden.py tests/test_talk_action.py -q
.venv/bin/python -m pytest -q                       # 全量回归
.venv/bin/python tools/framework_check.py           # 6/6（若存在）
.venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id a3-rule-10d-31npc
.venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0 --enable-llm --run-id a3-model-10d-31npc
```

- golden：若默认 `GOLDEN` 或 `DIALOGUE_GOLDEN` 漂移，新增 `A3_GOLDEN`（独立常量与独立测试），
  报告写清漂移来源（prompt/动作留存/system_goal source 映射），不改旧基线数值。
- replay：对 M 臂 `events.sqlite` 跑 `tools/replay.py`，要求 MATCH。

## 6. 交付物

- 代码 commit（单独 A3，只 stage 相关文件）。
- `reports/k1-rootcause-plan.md` 增补 A3 实施结果（五条判据数字、两臂表、成本、golden 漂移）。
- 最终中文 Markdown：五条判据逐条数字 → R/M 对照表 → 成本账单 → commit 列表。
- 盲评包路径：`reports/a2-dialogue-eval/`（`blind_pairs.jsonl` 无标签、`answer_key.jsonl`、`score_blind.py`）。

## 7. 风险与回退

- 模型选不可达/低价值目标 → 生存由规则底稿兜底；K6 回归则只保留“目标选择权”不保留“动作留存”，分步定位。
- 模型动作大量 `not_together` 被丢 → 先看 `dropped_by_reason`，必要时只调 prompt 要求选 `nearby_npcs`，不新增 move 注入。
- 预算超限 → 立即停真实调用，用已有 run 数据出报告并标明未达项。
