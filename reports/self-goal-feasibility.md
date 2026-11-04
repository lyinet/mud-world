# 自提案路径最后一轮：阈值 0.85 + 机器 rubric（stub，零模型调用）

**判断：这条线不值得继续投钱，可以冻结。** 三条数字支撑：

1. **护栏仍成立**：规则臂 K1 62/91=68.13%，自提案臂 62/91=68.13%，差 **0.00pp**；自额度动作记到内容目标 **0 次**。
2. **收紧阈值没有把 SGR 拉起来**：target/capacity 上限从 0.90 降到 0.85 后，基线难度均值 0.8228→0.7753、中位 0.90→0.84，但成熟 SGR 只从 52.38% 到 **53.33%**（+0.95pp），raw SGR 反而 39.29%→38.71%（−0.58pp）。归因是执行，不是目标难度：9 个到期目标平均排了 **6.33** 次自额度动作（够用），但只有 **4.0** 次真记到目标，平均兑现 0.52——动作排上了却没记到账。
3. **类型覆盖仍只有 2/6**，且另外 4 类不是校验器误拒：探针用创世状态直接构造 6 类提案，**6 类在 0.8 capacity 下全部放行**；零提案的原因是 stub 只写了两条分支。受 stub 覆盖面限制，STC 结论不能推广到真实模型。

因此：机制层（独立额度、可行性校验）已经证明可用；剩下的问题（提案类型多样性、拟人性、执行与目标匹配）都需要真实模型输出，而当前 stub 证据不足以申请预算。**本轮后冻结，不再提交新机制、不再申请真实模型预算，除非项目负责人明确解除。**

## 1. 护栏：K1 仍在范围内

| 指标 | 数值 |
|---|---|
| 纯规则臂 K1（in_scope） | **62/91 = 68.13%** |
| 自提案臂 K1（in_scope） | **62/91 = 68.13%** |
| 差 | **0.00pp**（护栏线 63.13%，PASS） |
| 自提案动作占用既有额度 | **0 次** |

占用口径（两条独立证据）：

- 147 个自额度动作，`goal_progress` 记到内容目标 **0** 次、记到自目标 **110** 次（`reports/self-goal-metrics.json` → `quota`）。
- 内容目标进度事件没有下降：规则臂 374 条、自提案臂 **425** 条；每 NPC·天 1.1687 → 1.3281。

本报告不把“K1 提升”当卖点；K1 在这里只回答“自提案有没有拖累既有目标”。

## 2. SGR：没有回升，归因是执行

### 2.1 两种分母一起报

10 天跑完后 3 天内的新目标截止日还没到，raw SGR 会把它们算成“未完成”。所以同时给 raw 与 mature（只算截止日已过的激活）：

| 口径 | 上一轮 | 本轮 | 变化 |
|---|---|---|---|
| 激活 / 完成 / 到期 | 56 / 22 / 8 | 62 / 24 / 9 | — |
| raw SGR（分母=全部激活） | 39.29% | **38.71%** | −0.58pp |
| mature SGR（分母=截止日已过） | 52.38%（22/42） | **53.33%**（24/45） | **+0.95pp** |

mature 分母里仍有 12 个“截止日已过但日志未记 expired/completed”的悬置目标（两轮都是 12 个，口径一致）。所以 mature SGR 也只能说明“大致五成兑现”，不是精确值。

### 2.2 基线难度：目标确实变容易了

| 分布 | 上一轮（n=56） | 本轮（n=62） |
|---|---|---|
| 均值 | 0.8228 | **0.7753** |
| 中位 | 0.90 | **0.84** |
| 区间 | 0.1544~0.9278 | 0.1544~0.84 |
| p10 / p90 | 0.428 / 0.90 | 0.428 / 0.84 |
| ratio > 0.85 的激活数 | 49 | **0** |
| 所需动作数中位 | 3 | 3 |

阈值 `target/capacity ≤ 0.85` 生效：上一轮 49/56 个激活在 0.9 附近，本轮全部压到 0.84 以下；但**所需动作数中位仍是 3**，说明“目标变容易”并没有把任务从 3 次动作降到 2 次。

### 2.3 归因：动作排上了，但没记到目标

mature 目标拆成“完成”和“到期”两组：

| 组 | n | 平均排班动作 | 平均记到目标动作 | 平均兑现进度 |
|---|---|---|---|---|
| 完成 | 24 | 3.96 | 3.46 | 1.00 |
| 到期 | 9 | **6.33** | **4.0** | **0.52** |

到期目标在 3 天窗口里平均排了 6.33 次自额度动作（比需要的 3 次还多），但只有 4.0 次真正产生 `goal_progress`，平均只兑现 52%。**这不是“目标定得太高”**，而是动作选错/记不到账：典型是 wealth 目标在 NPC 没有可卖库存时回落到 `cultivate`，而 `cultivate` 只记 mastery，不会记 wealth（显式 `goal_id` + 类型守卫把它正确地挡掉了，于是这一格就是空转）。

结论：**SGR 没回升，不能归因于阈值；阈值只改变了难度分布，没有改变执行结果。** 真正的瓶颈是 stub 提案与可执行动作不匹配（尤其 wealth 无库存），下一版要修的是“提案前检查可执行路径”，不是继续压 ratio。

## 3. STC：仍是 2/6，限制在 stub 覆盖面

| 类型 | 激活数 | 占比 |
|---|---|---|
| `advance_skill` | 38 | 61.29% |
| `earn_money` | 24 | 38.71% |

**覆盖 2/6**：`build_relation / build_shop / join_sect / escort` 零提案。提案分布同样是 `advance_skill` 50 条、`earn_money` 33 条。

“是不是校验器把 4 类误拒了？”——不是。用创世状态直接构造 6 类提案（target = min(内容目标值, 0.8×capacity)）：

| 类型 | per_action | capacity | 探针 target | ratio | 结果 |
|---|---|---|---|---|---|
| advance_skill | 0.005 | 0.015 | 0.012 | 0.80 | accepted |
| earn_money | 306.52 | 919.56 | 142.0 | 0.154 | accepted |
| build_relation | 3.0 | 9.0 | 7.2 | 0.80 | accepted |
| build_shop | 1.0 | 3.0 | 1.0 | 0.333 | accepted |
| join_sect | 1.0 | 3.0 | 1.0 | 0.333 | accepted |
| escort | 1.0 | 3.0 | 2.4 | 0.80 | accepted |

**结论：4 类从未被提出是 stub 侧没覆盖**（`_stub_proposal` 只有 advance_skill / earn_money 两条分支），不是校验器误拒。**该结论受 stub 覆盖面限制，不能推广**：不能据此说“模型只会挑容易的”，也不能说“引擎不支持这 4 类”。

顺带修了一个真实误拒：押镖动词会自建镖单，旧校验器却要求“已存在开放镖单”，属于把可达提案误拒；现在改为校验“角色允许 + 镖局资金 ≥ base_fee”，探针里的 escort 因此放行。bond 的 3/次是保守下界（实际 trade=3、apprentice=5、marry=10），会把 (9,11] 的可达目标误拒，但 stub 不提 bond，本轮无影响；未放宽，因为放宽会引入不可保证的容量。

## 4. SGC：不许再拿 stub 节奏当证据

| 指标 | 数值 |
|---|---|
| 完成 24 个后 3 天内又提一个 | 22 → **91.67%** |
| 其中相关（同类型/顺承） | 22 → 91.67% |

这个 91.67% 比上一轮的 86.36% 更高，但**仍然不能当“它有自己的打算”的证据**：stub 每 3 天固定发一条提案，SGC 窗口也是 3 天，比例高只是发言节奏。上一轮已经明确这一点，本轮继续不引用它作意图证据。要让 SGC 有意义，必须改成“完成后才触发下一条”，这属于新机制，不在本轮。

## 5. Rubric：机器可判定，分歧 0/60

4 维、各 1~5 分；其中 3 维改成机器规则，第 4 维只留“待评”：

| 维度 | 机器规则 | A 均值 | B 均值 | 分歧 |
|---|---|---|---|---|
| 处境贴合 | 5=objects 非空；4=无 objects 但有 preconditions；1=两者都空 | 5.00 | 5.00 | 0/20 |
| 目标具体可执行 | 1=校验器拒绝（任何原因）；5=放行且 ratio≤0.85；3=放行且 ratio∈(0.85,1.0]（仅历史样本） | 3.80 | 3.80 | 0/20 |
| 与目标的因果链 | 5=purpose 含「现状数值 → 目标数值」且差值等于 target（容差 max(5e-4, 0.5%)）；否则 2 | 4.70 | 4.70 | 0/20 |
| 是否像本人会说 | **待评**：无真实模型输出前不评分 | — | — | 不参与 |

- 样本：83 条提案中分层抽 20 条（14 通过 + 6 拒绝；本轮 competent 臂 0 拒绝，拒绝样例取自 scenarios 臂）。
- **总体分歧 0/60 = 0.0%**，低于 15% 目标。
- 两个打分者是同一条规格的两套独立实现：A 直接读 `capacity_units`，B 从 `equation` 字符串解析 capacity；因果链 A 用单条正则，B 先切箭头再取两侧数字。
- 0 分歧只能证明 **rubric 可被不同实现一致执行**，不是两次主观判断的一致性证据；`处境贴合` 在 stub 语料上饱和（20/20 都是 5），只能证明规则可执行，不能证明区分力。
- 历史分歧项的处理：`攒142文件盘缠`（无箭头）两边都 2；`把0.440的手艺再精进到0.014`（无箭头）两边都 2，且 ratio 0.90 在旧阈值下放行记 3；新格式 `手艺 0.4400 → 0.4526` 两边都 5；被拒的“超预算”样例两边都 1。
- **拟人性结论：不做。** 20/20 条 `是否像本人会说` 都是“待评”；stub 文本是模板，对该维无判别力。

## 6. 验收

- **确定性**：competent_a / competent_b 两次重跑一致。
  - state_final `ba7813c6…`
  - event_final `189ebc86…`
  - events.jsonl `76de2348…`
  - trace `b6d69a6d…`
- **全量 pytest**：收集 **411**。
  - 本轮中途的稳定测量：**400 passed / 10 skipped / 1 collection error**。
  - 收尾时并行 W1/API 会话改动导致 13 个模块无法收集（统一是 `wsim.actions.player.PRESENCE_ACTIVE_SECONDS` 缺失）；用 `--continue-on-collection-errors` 复跑：**350 passed / 10 skipped / 1 failed / 13 errors**，14 个问题全部是同一个并行导入错误，**不是本轮改动**。
  - `tests/test_p3e_golden.py` 本轮已绿（P4 基线由另一会话修好）。
  - 本轮自有 `tests/test_self_goal.py` **12/12 通过**。
- **零模型调用**：全部 stub，无 API 调用、无费用。
- 未改动 `api/**`、`ui/player/**`、`content/**`、`sim/scheduler.py`、`sim/headless.py`，也未动 `tests/test_p3e_golden.py`。

## 7. 本轮改动

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `src/wsim/sim/self_goal.py` | 新增 `TARGET_CAPACITY_RATIO=0.85` 与 `target_exceeds_ratio` 拒绝（含算式）；stub 按 `STUB_TARGET_RATIO=0.84` 生成；stub 的 wealth per 改用校验器同口径“单件净售价/ trade 兜底”；purpose 改为 `现状 → 目标`；escort 改为校验“角色+镖局资金” | 阈值收紧；修 stub 用库存件数乘单价导致超额度提案；让因果链可机器判定；修押镖误拒 |
| `tests/test_self_goal.py` | 新增阈值拒绝、purpose 机器可校验、无开放镖单仍可押镖 3 项 | 锁住本轮行为 |
| `tools/self_goal_metrics.py` | 新增 mature SGR、难度分布、执行归因、6 类探针、`--prev-self` 对照 | 支撑“回升归因”与“误拒 vs 未覆盖” |
| `tools/self_goal_rubric.py` | 重写为机器 rubric；拟人维只留“待评”；双实现打分 + 分歧率 | rubric 可操作化 |
| `reports/self-goal-{stage0,metrics,rubric}.json` | 本轮重跑产物 | 复算数据源 |

## 8. 复算入口

```bash
PYTHONPATH=src .venv/bin/python tools/self_goal_feasibility.py --out reports/self-goal-stage0.json
PYTHONPATH=src .venv/bin/python tools/self_goal_metrics.py \
  --rule runs/sg-rule-10d-s42 --self runs/sg-competent-10d-s42-a \
  --prev-self <上一轮 run 目录> --probe-types --out reports/self-goal-metrics.json
PYTHONPATH=src .venv/bin/python tools/self_goal_rubric.py \
  --trace runs/sg-competent-10d-s42-a/self_goal_proposals.jsonl \
  --extra-trace runs/sg-scenarios-10d-s42/self_goal_proposals.jsonl \
  --out reports/self-goal-rubric.json
PYTHONPATH=src .venv/bin/pytest -o addopts="" -q
```

## 9. 冻结说明

本轮是这条路径的最后一轮调优。之后：

- 不提交新机制；
- 不申请真实模型预算；
- 除非项目负责人明确解除，SGR/STC/SGC 与 rubric 的结果保持为“stub 可行性证据”，不作拟人性/意图结论。
