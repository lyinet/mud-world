# 自提案目标：独立额度 + 活指标（stub，零模型调用）

**判断一：独立额度把 K1 拉回护栏内。** 纯规则臂 62/91=68.13%，自提案臂 62/91=68.13%，**差 0.00pp**（护栏要求 ≥63.13%，PASS）；自提案动作**占用既有额度 0 次**。

**判断二：NPC 有了“自己的打算”的雏形，但还谈不上稳定。** 自提案完成率 SGR 39.29%（56 个激活里完成 22 个），进入后 5 天明显衰减（前 5 天 48.15% → 后 5 天 31.03%，到期 8 个）；类型只覆盖 2/6，且 68% 集中在最容易的 `advance_skill`。SGC 86.4% 看着高，但它主要是“固定 3 天发一条提案”的节奏产物，不能当“主动延续意图”的证据。

## 1. 配额分开（修 bug，不改架构）

### 改动

自目标从“主目标”降为**次级目标**：

| 位置 | 改动 | 原因 |
|---|---|---|
| `world/effects.py` `goal_create` | 新增 `secondary` 分支：建目标但**不写** `entity.goal_id` | 原来 `goal_create` 无条件接管行动目标，这是 K1 全崩的真正根因（不是动作额度） |
| `actions/system.py` `system_goal` | 透传 `secondary`；已存在目标时不发 `set_field` | 保证次级目标永远不顶掉内容主线 |
| `sim/intent.py` `Planner.plan` | 在内容目标的 `depth` 个动作**之后**，每天额外排 1 个自目标动作；该动作的 intent 带 `_goal_id` | 自目标有独立额度；额度用满只是“不做”，不会回流挤占内容目标 |
| `sim/intent.py` `Planner` | 自额度动作使用**独立的 memo** | `_goal_step` 会在 memo 里记 opened/joined/apprentice/married；共享 memo 会压掉次日的**内容**动作 |
| `actions/common.py` `goal_progress` | 新增 `goal_id` 参数：显式指定时**只认该目标**，类型不匹配直接返回空，绝不回落到活跃目标 | 结构上保证自目标进度不会记账到内容目标 |
| `actions/{basic,commerce,business,social,escort}.py` | 13 个 `goal_progress` 调用点透传 `explicit_goal_id(intent)` | 让上行保障真正生效 |
| `sim/self_goal.py` | `SELF_GOAL_ACTIONS_PER_DAY=1`；校验器 `action_capacity = days × 1`；`mastery per_action=0.0025×2`、`wealth per_action=max(单件净售价, 5)` | 额度模型与独立额度动作的真实产出对齐，否则“基线难度”是假的 |
| `sim/self_goal.py` `can_activate` | 只保留“同 NPC 同时只有一个活跃自目标” | 自目标不再接管主目标，旧豁免条件不再需要 |

### 三条数字

| 指标 | 数值 |
|---|---|
| 纯规则臂 K1（in_scope） | **62/91 = 68.13%** |
| 自提案臂 K1（in_scope） | **62/91 = 68.13%**（created_only 62/78 = 79.49%，规则臂 70/88 = 79.49%） |
| 自提案占用既有额度的次数 | **0** |

“占用 0 次”的复算口径（两条独立证据）：

- 140 个带 `_goal_id` 的自额度动作里，`goal_progress` 落到内容目标的次数 = **0**（`reports/self-goal-metrics.json` → `quota.self_actions_credited_to_content`）。
- 内容目标进度事件数没有下降：规则臂 374 条、自提案臂 417 条；每 NPC·天 1.17 → 1.30。

自额度动作构成：`cultivate` 103 次（其中 62 次真记到自目标）、`sell` 37 次（35 次记到自目标）；41 次 `cultivate` 是“自目标要钱但没货、动作回落到练手艺”造成的空转，记不到 wealth，属于动作选择问题，不影响配额隔离。

## 2. 换度量：量“活的”

全部可从 `events.jsonl` + `self_goal_proposals.jsonl` 复算（`tools/self_goal_metrics.py`）。

### SGR 自主完成率

| 口径 | 数值 |
|---|---|
| 激活 56 / 完成 22 | **SGR = 39.29%** |
| 到期未完成 | 8 |
| 校验器预筛后的基线难度 `target/capacity`（n=56） | 均值 **0.823**，中位 **0.90**，区间 0.154~0.928 |

基线难度说明：多数提案把目标定在“独立额度的 90%”，属于**贴着额度上限**的难度；所以 SGR 不是“目标太松白送”，而是“定了要用满额度的活，实际只有 4 成兑现”。

**SGR 高只证明提案与执行一致，不证明提案质量。** 这里是反过来的证据：SGR 只有 39%，说明执行端（动作类型选错、窗口到期）没跟上提案。

分段稳定性（10 天）：

| 区段 | 激活 | 完成 | 到期 | SGR | 类型分布 |
|---|---|---|---|---|---|
| 前 5 天 | 27 | 13 | 0 | 48.15% | advance_skill 18 / earn_money 9 |
| 后 5 天 | 29 | 9 | 8 | 31.03% | advance_skill 20 / earn_money 9 |

后段衰减主要来自“到期 8 个”：3 天窗口内没排够动作就过期，不是被放弃。

### STC 类型覆盖

| 类型 | 激活数 | 占比 |
|---|---|---|
| `advance_skill` | 38 | 67.9% |
| `earn_money` | 18 | 32.1% |

**覆盖 2/6**（`build_relation / build_shop / join_sect / escort` 一条没提）。stub 的选题规则就是“没雇主练手艺、有雇主攒钱”，所以这不是模型偏好，而是 stub 写死的分布；只能说明指标能区分类型，不能说明模型只会挑软柿子——要那结论得等真实模型。

### SGC 目标链

| 指标 | 数值 |
|---|---|
| 完成 22 个后 3 天内又提一个 | 19 → **86.4%** |
| 其中“相关”（同类型或顺承类型） | 19 → **86.4%** |
| 没接上的 | 3 |

**这条不能当“它有自己的打算”的证据。** stub 固定每 3 天发一条提案，而窗口也是 3 天，所以“完成后 3 天内又有一条”几乎必然成立；19 条全部同类型，是因为 stub 一直选 `advance_skill`。这个 86.4% 是**发言节奏**，不是**意图延续**。要让它有意义，下一版得把“提案触发”改成“完成后才触发”，并允许模型自己选类型。

## 3. 提案理由 rubric（先验 rubric，不做盲评）

4 维、各 1~5 分：**处境贴合 / 目标具体可执行 / 与目标的因果链 / 是否像本人会说**。
从 87 条提案里分层抽 20 条（14 条通过 + 6 条被拒），两个独立打分者用同一 rubric 打分：

- 打分者 A：严格复算（用 `capacity_units`、`preconditions`、`purpose` 里的数字对账）；
- 打分者 B：读者直觉（只看文本是否成立，不复算额度）。

| 维度 | A 均值 | B 均值 | 分歧条数 | 最大分差 |
|---|---|---|---|---|
| 处境贴合 | 4.20 | 5.00 | 8/20 | 2 |
| 目标具体可执行 | 3.70 | 5.00 | 7/20 | 4 |
| 与目标的因果链 | 2.80 | 4.40 | 20/20 | 2 |
| 是否像本人会说 | 3.00 | 5.00 | 20/20 | 2 |

### 分歧项与修订

1. **`与目标的因果链` 20/20 全分歧。** A 给 2 分（`攒142文盘缠` 只有一个数，没有“现状”），B 给 4 分（文本非空即算讲通）。
   - 修订：这一维必须写成“**现状数值 → 结果数值，且差值等于 target**”，明确“只有一个数值记 2 分、没有数值记 1 分”，并附一条正例（`把0.440的手艺再精进到0.4535`）和一条反例（`攒142文盘缠`）。
2. **`目标具体可执行` 最大分差 4。** 6 条被拒提案（如 `target=142 / capacity=43.65`）A 给 1 分，B 给 5 分——B 没有额度概念。
   - 修订：该维**强制引用校验器算式**（`target/capacity`），不允许凭感觉；并且被拒提案也必须能被评分（现在 B 的规则对拒案完全失效）。
3. **`处境贴合` 8/20 分歧。** `advance_skill` 的 `preconditions` 本来就合法为空，A 却因此扣到 3 分。
   - 修订：增加“该类型本身不需要前置条件时，空 preconditions 不扣分”，把“无依据”和“不需要依据”分开。
4. **`是否像本人会说` 20/20 全分歧且 B 全部满分。** 20 条 purpose 都是 9~13 字的同一模板，B 的“短句=像话”规则直接饱和。
   - 修订：这一维必须先给**人物锚点**（该 NPC 的语气词/称呼/忌讳），否则不可评；同时注明**模板文本对该维无判别力**。

**不做拟人性结论。** 以上只验证“rubric 可被两个打分者独立执行、且能定位规则漏洞”；真实模型的拟人性盲评等预算批准后再做。

## 4. 验收与纪律

- **确定性**：同一配置两次重跑，`state_final / event_final / events.jsonl sha256 / trace sha256` 全部一致。
  - state_final `ce14b58a…`
  - event_final `3de0710d…`
  - events.jsonl `bc8ec623…`
  - trace `9d266e36…`
- **全量 pytest**：收集 382，通过 380，失败 2。
  - `tests/test_p3e_golden.py::test_p4_milestone_deterministic_2d_fake_run_is_golden`
  - `tests/test_p3e_golden.py::test_p4_milestone_dialogue_enabled_fake_run_is_golden`
  - 两条都是 P4 会话新增 `system_milestone/system_maintenance` 日界动作导致的 golden 基线漂移（失败信息自身写明 796→800 事件），**不是本次改动引入**，按纪律不修别人的文件。
  - `test_w1_reconnect` 本轮已绿（W1 会话已修）。
- **K1 只作护栏**：本报告不把“K1 提升”当卖点；K1 的结论是“没被自提案拖累”。
- **零模型调用**：全部 stub，无 API 调用、无费用。
- 未改动 `api/**`、`ui/player/**`、`content/**`、`sim/scheduler.py`、`sim/headless.py`。

## 5. 阶段 0/1 复核（本次重跑）

| 项 | 数值 |
|---|---|
| 提案数 / 放行 / 通过率 | 87 / 75 / **86.21%** |
| 提案级拒绝 | 12，全部 `target_exceeds_capacity`，均带可复算 `equation` |
| 激活 / 完成 / 到期 | 56 / 22 / 8 |
| 激活硬门拒绝 | 35，全部 `initial_goal_protected` |
| 复核缺算式的拒绝 | 0（`feasibility_rejects_without_equation=0`） |
| 场景臂（覆盖非法/超预算/不可达/初始保护/活跃自目标） | 通过，见 `reports/self-goal-stage0.json` `scenarios` |

## 6. 复算入口

```bash
PYTHONPATH=src .venv/bin/python tools/self_goal_feasibility.py --out reports/self-goal-stage0.json
PYTHONPATH=src .venv/bin/python tools/self_goal_metrics.py --out reports/self-goal-metrics.json
PYTHONPATH=src .venv/bin/python tools/self_goal_rubric.py --out reports/self-goal-rubric.json
PYTHONPATH=src .venv/bin/python -m pytest -q
```
