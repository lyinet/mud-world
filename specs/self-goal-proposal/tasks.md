# 任务清单：自提案目标零成本可行性验证

> 机制锁死：只做「模型提案 + 引擎可行性校验」，不换选择器、不换战略层、不换其他架构。

1. **实现 `src/wsim/sim/self_goal.py`**
   - `SelfGoalProposal` dataclass（字段见 spec §1）。
   - `parse_proposal(raw, npc, tick)`：严格 schema 校验，返回 `(proposal, errors)`。
   - `FeasibilityValidator.validate(...)`：按 spec §2 产出 `Verdict`（含 `equation`、`checks`）。
   - `SelfGoalLedger`：
     - `issue(state, tick, day, tier_fn)`：competent / scenarios 两模式；校验通过的提案进入 `pending`，不写 state；
     - `choose(state, npc, tick)`：激活检查通过才返回 self `GoalChoice`（`create=True`）；已有活跃自目标则保持；到期返回 abandon；
     - `can_activate(state, npc)`：执行 `initial_goal_protected` / `self_goal_active` 制度性硬门；
     - `scan(state)`：统计创建/完成/过期；
     - `records` + `stats`。
   - 确定性 stub：仅用排序、角色、state 数值，不读墙钟/全局随机。

2. **接入引擎（默认关闭）**
   - `src/wsim/actions/system.py`：`goal_source="self"` → `target.source="self"`。
   - `src/wsim/sim/intent.py`：`MockReasoner.attach_self_goals`；`decide()` 在 selector 之后消费；到期返回 `abandon`。
   - `src/wsim/sim/headless.py`：`RunConfig` 四个字段；日界 `issue_self_goals`（在 `run_night_batch` 前）；summary `self_goal` 块；`enable_llm` 与自提案互斥报错；写 `self_goal_proposals.jsonl`。

3. **测试 `tests/test_self_goal.py`**
   - schema 缺字段/越界/未知类型；
   - 合法可达 → accepted，equation 可复算；
   - 超预算（动作数、资金）→ 对应 reason；
   - 窗口内不可达（target > capacity）→ `target_exceeds_capacity`；
   - `initial_goal_protected` / `self_goal_active`；
   - 同一输入两次 stub 提案字节一致。

4. **工具 `tools/self_goal_feasibility.py`**
   - 跑 R 基线（self_goal off，10d/31npc/seed42）；
   - 跑 competent 两次（不同 run_id），比较 state/event/events 文件/提案文件 hash；
   - 跑 scenarios 一次；
   - 输出 `reports/self-goal-stage0.json`（对照表数据源）。

5. **阶段 0 执行与核对**
   - `.venv/bin/python tools/self_goal_feasibility.py --out reports/self-goal-stage0.json`
   - 核对：通过率 ≥60%（分母=全部提交提案）、拒绝 100% 有 equation、两次 hash 一致。

6. **阶段 1 执行与核对**
   - scenarios run 的 `self_goal_proposals.jsonl` 中五类 reason 均出现；
   - `pytest -q tests/test_self_goal.py` 全过。

7. **回归**
   - `.venv/bin/pytest -q`（现有 222 项 + 新增）全过；
   - 默认关闭的 R 基线 hash 与既有规则臂行为一致（同内容同参数）。

8. **报告与提交**
   - 写 `reports/self-goal-feasibility.md`：结论、提案格式、校验器算式、阶段 0 对照表、判据逐项、局限（不宣称意义/拟人）、复现命令。
   - 只 stage 自有文件：`src/wsim/sim/self_goal.py`、`src/wsim/actions/system.py`、`src/wsim/sim/intent.py`、`src/wsim/sim/headless.py`、`tests/test_self_goal.py`、`tools/self_goal_feasibility.py`、`specs/self-goal-proposal/`、`reports/self-goal-feasibility.md`、`reports/self-goal-stage0.json`；禁止 `git add -A`。
   - 提交信息：`feat(self-goal): 模型目标提案与引擎可行性校验（默认关闭）`。

## 第二轮（独立额度 + 活指标）

9. **配额分开**
   - `world/effects.py` `goal_create` 支持 `secondary`：建目标不切 `entity.goal_id`。
   - `actions/system.py` `system_goal` 透传 `secondary`；已存在目标时不发 `set_field`。
   - `sim/intent.py` Planner：内容动作之后每天 +1 自目标动作（带 `_goal_id`），独立 memo。
   - `actions/common.py` `goal_progress` 支持显式 `goal_id` 且不回落；13 个调用点透传。
   - `sim/self_goal.py`：`SELF_GOAL_ACTIONS_PER_DAY=1`，额度与 per_action 对齐。
10. **指标**
    - `tools/self_goal_metrics.py`：SGR / STC / SGC + 前 5/后 5 天分段 + 配额占用复算。
    - `tools/self_goal_rubric.py`：4 维 rubric、20 条分层抽样、双打分者分歧。
11. **验收**
    - 自提案臂 K1 ≥ 63.13%；占用既有额度 = 0；两次重跑 hash 一致；全量 pytest 计数记录。

## 验收线

- 阶段 0 通过率 ≥60%；拒绝全部可复算；两次重跑 hash 一致。
- 校验器若放行不可达或误拒可达 → FAIL，停止，不申请真实模型预算。
- 真实模型预算只在阶段 0 全过后另行申请，且先报请求数/token/美元上限。
