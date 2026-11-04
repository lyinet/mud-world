# 自提案目标：零成本可行性验证报告

**判断：机制在「提案是否可达」这一层成立；在「K1 目标完成率」上是负增益，不成立为增益机制。**

- 阶段 0 全部判据通过：stub 提案通过率 100%（≥60%）、两次重跑 state/event/文件 hash 完全一致、无真实模型调用。
- 校验器能放行可达提案、拒绝超预算与窗口内不可达提案；拒绝均带可复算式。
- 但自提案目标占用深度动作额度，纯规则臂 K1 62/91=68.13%，自提案臂 38/91=41.76%，**差 −26.37pp**。
- 因此本报告只证明“提案可达性校验闭环成立”，**不证明提案有意义/像人，也不证明能提升 K1**；后者需另立评测。

## 1. 提案格式

模型只输出 JSON，不输出动作、不直接改 state：

```json
{
  "npc": "npc_id",
  "goal_type": "earn_money",
  "target": 75.6,
  "deadline_days": 3,
  "preconditions": ["has_employer"],
  "expected_actions": 6,
  "expected_money": 0,
  "objects": ["loc_id"],
  "purpose": "攒够换季盘缠"
}
```

约束：`goal_type` 仅限既有可执行类型（`earn_money/advance_skill/build_relation/build_shop/join_sect/escort`）；`deadline_days` 1..3；`preconditions` 仅限可机器检查的 token；`expected_actions/expected_money` 必须声明；`purpose` 只作审计文本。解析失败记 `schema:<字段>:<原因>`。

创建路径：校验通过 → 入队 pending → 激活硬门通过 → `system_goal`（`source=self`）→ Writer/Effect 建目标。**提案层没有 state 写权限。**

## 2. 校验器算式

```text
days            = deadline_days
quota           = DEPTH_GOAL_ACTIONS[tier]   # silent/background=1, active=2, spotlight=3
action_capacity = days × quota
capacity_units  = action_capacity × per_action_units
```

`per_action_units`：

| kind | 上限产能/动作 | 不可达/拒绝 |
|---|---|---|
| wealth | `max(工资净额, 可卖库存最高收益)` | 两者为 0 → `no_income_source` |
| mastery | `0.0025 × 8 = 0.02` | 技能已满 → `skill_capped` |
| bond | 对象在场 5.0；否则有商业体 3.0 | 都没有 → `bond_target_absent` |
| shop | 1.0 | 现金<300 / 已有店 / 无地主 / 金库 guard → 对应 `shop_*` |
| faction | 1.0 | 当地无组织 → `org_absent` |
| escort | 1.0 | 无可用镖路 → `escort_route_absent` |

判定：

1. `capacity_units < target` → `target_exceeds_capacity`，算式 `capacity=<days>×<per>=<capacity> < target=<target>`。
2. `expected_actions > action_capacity` → `budget_actions_exceeded`。
3. `expected_money > cash` → `budget_money_exceeded`。
4. 全部通过 → `accepted`，同一算式写入 `equation`。

**激活时二次校验**：窗口从激活时刻重新起算，按当前 state 与当前档位重算容量；避免“入队时可达、激活时窗口被等待吃掉”的旧问题。

## 3. 生命周期硬门

1. **初始目标保护**：当前 `entity.goal_id` 仍是该 NPC 内容包排序第一条目标且 active → 不激活，记 `initial_goal_protected`。制度上不允许覆盖。
2. **同 NPC 活跃自提案 ≤1**：已有 `source=self` 且 active 的目标 → 不激活，记 `self_goal_active`。
3. pending 不是世界目标；过期作废，不产生事件。

## 4. 阶段 0 对照表（10 天 / 31 NPC / seed 42）

| 指标 | 纯规则臂 R | 自提案 competent |
|---|---:|---:|
| 提交提案数 | — | 89 |
| 校验通过 | — | 89 |
| 通过率 | — | **100%** |
| 提案被拒原因分布 | — | 无（0 条） |
| 激活创建数 | — | 69 |
| 真正完成数 | — | **53** |
| 到期未完成 | — | 6 |
| 激活被拒 | — | `initial_goal_protected=38` |
| K1（in_scope） | **62/91 = 68.13%** | **38/91 = 41.76%** |
| K1 差 | — | **−26.37pp** |
| state_final | `1130a5d1…` | 同左 |
| event_final | `b26ec81c…` | 同左 |
| events.jsonl sha256 | `9e0b178f…` | 同左 |
| 提案 trace sha256 | `47038396…` | 同左 |

阶段 0 的 89 条提案全部可达/预算自洽，因此没有提案级拒绝；38 条 `initial_goal_protected` 是**激活层生命周期拒绝**，不是可行性拒绝。

## 5. 阶段 1 校验器覆盖（scenarios stub）

| 类别 | 结果 | 条数 | 算式/原因 |
|---|---|---:|---|
| 合法可达 | 校验放行 | 46 | `capacity=… >= target=…`；其中 12 条随后被 `self_goal_active` 拒绝 |
| 超预算 | 拒绝 | 34 | `expected_actions=12 > action_capacity=6` |
| 窗口内不可达 | 拒绝 | 34 | `capacity=… < target=9999.0` |
| 初始目标保护 | 激活拒绝 | 14 | `initial_goal_protected` |
| 已有活跃自目标 | 激活拒绝 | 12 | `self_goal_active` |

- 提案级被拒 68 条，**68/68 带可复算 equation**。
- 激活创建 29，完成 22，pending 过期 1，活跃到期 3。
- 覆盖完整：合法、超预算、不可达、初始保护、活跃自目标五类均留痕。

## 6. 判据逐项

| 判据 | 结果 | 证据 |
|---|---|---|
| 阶段 0 stub 通过率 ≥60% | **PASS** | 89/89 = 100% |
| 被拒案例 100% 有可复算原因 | **PASS** | 阶段 1 提案级 68/68 有 `equation`；阶段 0 无提案级拒绝 |
| 两次重跑 state/event hash 一致 | **PASS** | state `1130a5d1…`、event `b26ec81c…`、events sha `9e0b178f…`、trace sha `47038396…` 均一致 |
| 不调用真实模型 / 零成本 | **PASS** | `enable_llm=False`；仅 stub |
| 校验器放行不可达 / 误拒可达 | **未发生** | 可达放行；不可达标 `target_exceeds_capacity` |

## 7. 局限（必须一起读）

- **只解决“提案是否可达”，没有解决“提案是否有意义/是否像人”**。`purpose` 只是文本，校验器不判断动机质量、叙事一致性或拟人性。
- competent stub 用 0.9×容量安全边际生成目标，因此 100% 通过只能说明校验器不误拒“合理提案”，不等于真实模型也会提出合理提案。
- K1 下降是机制的真实代价：自提案占用深度动作额度，内容目标被挤占。**本报告不宣称 K1 改善。**
- 规则 planner 只认识既有 `content_type` 分支，所以提案复用既有类型；这是“可执行”保证，不是“有意义”保证。
- 全量测试有 1 项与本机制无关的失败：`tests/test_w1_reconnect.py::test_reconnect_within_60s_restores_same_session`（`tick 36 != 30`），属于并行 W1 会话改动；本机制默认关闭，未触碰 `api/**`、`scheduler.py`、`headless.py`。

## 8. 真实模型预算（仅报价，不申请）

若项目负责人要求进入真实模型阶段，按每轮 89–124 次提案、20% 重试冗余估算：

- 请求数上限：**150 次**
- 输入上限：**300k tokens**；输出上限：**30k tokens**
- 成本上限：**$0.63**（$0.0015/1k 输入 + $0.006/1k 输出）

在未确认“是否接受自提案挤占内容目标”前，不建议直接花这笔钱。

## 9. 复现

```bash
cd /Users/lyinet/projects/XKX/world-sim
PYTHONPATH=src .venv/bin/pytest -q tests/test_self_goal.py
PYTHONPATH=src .venv/bin/python tools/self_goal_feasibility.py \
  --out reports/self-goal-stage0.json
```

运行目录：`runs/sg-rule-10d-s42`、`runs/sg-competent-10d-s42-a|b`、`runs/sg-scenarios-10d-s42`。

## 10. 代码

- 提案/校验器/台账：[self_goal.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/self_goal.py)
- 决策接入：[intent.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/intent.py)
- `source=self` 映射：[system.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/system.py)
- 测试：[test_self_goal.py](file:///Users/lyinet/projects/XKX/world-sim/tests/test_self_goal.py)
- 验证工具：[self_goal_feasibility.py](file:///Users/lyinet/projects/XKX/world-sim/tools/self_goal_feasibility.py)
- 规格：[spec.md](file:///Users/lyinet/projects/XKX/world-sim/specs/self-goal-proposal/spec.md)

commit：`67a14e4`（已 push 到 `origin/master`）。
