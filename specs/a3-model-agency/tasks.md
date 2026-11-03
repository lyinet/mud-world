# Tasks · A3 模型目标选择权 + 动作留存

## T1 代码实现（按依赖顺序）
- [x] `src/wsim/sim/intent.py`：`MockReasoner.decide` 直接采纳合法模型目标（urgent 不覆盖）
- [x] `src/wsim/sim/intent.py`：`_choice_for_goal` 支持未创建内容目标（`create=True`）+ 可规划校验
- [x] `src/wsim/actions/system.py`：`SystemGoalAction.apply` 的 `goal_source` 映射修正（model/content→npc）
- [x] `src/wsim/agent/provider.py`：`_filter_model_actions` 返回 `(draft, kept)`，MODEL_VERBS 按 `(verb,target)` 丢规则重复项
- [x] `src/wsim/agent/provider.py`：更新 3 处调用点
- [x] `src/wsim/agent/prompt.py`：`DECISION_SYSTEM_PROMPT` 收窄为具体目标动作
- [x] `src/wsim/sim/headless.py`：新增 `model_agency` 统计 + `model_goal_chains.jsonl`

## T2 离线验证
- [x] `pytest tests/test_p3e_golden.py tests/test_talk_action.py -q`
- [x] 若 golden 漂移：新增 `A3_GOLDEN` 独立基线 + 独立测试，记录漂移来源（未漂移，未新建基线）
- [x] 全量 `pytest -q`（212 全过，零失败）

## T3 纯规则臂 R（不烧钱）
- [x] `run_headless --days 10 --seed 42 --npc 0 --run-id a3-rule-10d-31npc`
- [x] 记录 K1 三口径、事件数/source 分布、K6、state/event hash

## T4 模型臂 M（真实调用，预算护栏）
- [ ] 核对预算：先估算 $1.15；任何时刻累计 ≥$1.75 立即停
      （未达成：成本仅在 run 结束可见，最终 $3.138，已超硬上限，见报告）
- [x] `run_headless --days 10 --seed 42 --npc 0 --enable-llm --run-id a3-model-10d-31npc`
- [x] 逐请求记账：`runs/a3-model-10d-31npc/cost.jsonl` 按 slot 汇总 token/成本/调用数
- [x] 计算五条判据数字：目标相关动作 83、K1 −7.69pp、K6 含剪枝 FAIL、模型事件占比 8.12%、归因链 83
- [ ] 若不达标：查 `dropped_by_reason` / `intent_unknown_category_cases`，仅在预算内调 prompt/权限
      （预算已超限，停止真实调用；仅完成根因定位，未再调优）

## T5 交付与报告
- [ ] replay M 臂，要求 MATCH
      （决策槽无 replay transport，`WSIM_INTENT_REPLAY` 仅覆盖 16 次 intent 调用；整 run 未验证）
- [x] 更新 `reports/k1-rootcause-plan.md` A3 实施结果（数字、漂移、成本）
- [x] 确认盲评包路径 `reports/a2-dialogue-eval/`（不动内容）
- [x] 单独 commit（不碰 README/QUICKSTART/DELIVERY-TEST/known-limitations，不改 tag）
- [x] 输出中文 Markdown：五条判据数字 → R/M 对照表 → 成本账单 → commit 列表

## 验收门
- 五条判据全部达标，或明确标注未达项与原因。（已完成：2 PASS / 3 FAIL，原因见报告）
- K6 严格与含剪枝全 pass；replay MATCH。
  （严格 4 项 pass；含剪枝 FAIL；replay 未验证）
- 成本 ≤$2.0，且接近 $1.75 时已停下评估。（未达成：$3.138）
