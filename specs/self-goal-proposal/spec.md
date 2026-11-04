# 自提案目标机制（零成本可行性验证）规格

## 0. 硬约束（机制锁死）

本规格只实现**一条路径**：模型输出目标提案 → 引擎解析 → 引擎可行性/预算/生命周期校验 → 引擎创建目标 → 规则 planner 执行。

明确禁止：
- 不改回「从内容池选目标」的选择器路线；
- 不改回 A4 每 3 日战略层路线；
- 不新增任何替代架构；
- 阶段 0/1 不调用真实模型、不产生 API 成本；
- 失败只修本路径内的 bug、补依赖测试、重跑；若校验器本身不成立，直接写 FAIL，不自行换方向。

新机制默认关闭；关闭时事件流、状态哈希、现有测试行为均不变。

## 1. 提案格式（模型唯一可输出物）

模型只能输出一份 JSON，不能输出动作、不能直接写 state、不能跳过目标创建。

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

字段约束（`src/wsim/sim/self_goal.py::parse_proposal`）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `npc` | str | 必须等于发起 NPC；未知 NPC 拒绝 |
| `goal_type` | str | 仅允许 `earn_money / advance_skill / build_relation / build_shop / join_sect / escort` |
| `target` | float | > 0，且为有限数 |
| `deadline_days` | int | 1..`self_goal_max_window_days`（默认 3） |
| `preconditions` | list[str] | 仅允许 `has_cash:<n>`、`has_employer`、`at_location`、`target_present`、`owns_no_shop`、`has_org`；未知 token 拒绝 |
| `expected_actions` | int | ≥ 1 |
| `expected_money` | int | ≥ 0 |
| `objects` | list[str] | 只能是 state 中存在的实体/地点 id |
| `purpose` | str | 非空，≤120 字符；只作审计文本，不参与可行性判定 |

解析失败统一记 `schema:<字段>:<原因>`；引擎侧生成 `proposal_id = self:<npc>:<tick>:<goal_type>`，不接受模型自带 id。

## 2. 可行性校验器（核心）

`FeasibilityValidator.validate(state, proposal, tick, tier) -> Verdict`

### 2.1 基础量

```python
days = proposal.deadline_days
quota = DEPTH_GOAL_ACTIONS[tier]          # silent/background=1, active=2, spotlight=3
action_capacity = days * quota
cash = state.account_balance(f"wallet:{npc}")
```

### 2.2 每动作上限产能（按 goal kind）

| kind | `per_action_units` | 额外前置 |
|---|---|---|
| `wealth` | `max(wage_net(role), best_sell_revenue)`；两者皆 0 → 拒绝 `no_income_source` | `has_employer` 或可卖库存 |
| `mastery` | `0.0025 * 8 = 0.02`；`headroom = 1.0 - max_skill`，headroom ≤ 0 → 拒绝 `skill_capped` | 无 |
| `bond` | 目标对象在场且未绑定 → 5.0；否则当地有商业体 → 3.0；否则 0 → `bond_target_absent` | `target_present` |
| `shop` | 1.0；现金 < `shop_opening.stall=300`、金库 guard、已有店铺、当地无地主 → 拒绝 | `owns_no_shop`、`has_cash:300` |
| `faction` | 1.0；当地无组织且无入会目标 → 拒绝 `org_absent` | `has_org` |
| `escort` | 1.0；无可用镖路 → 拒绝 `escort_route_absent` | — |

`wage_net = economy.wages_daily[role] * (1 - taxes.income)`；`best_sell_revenue = max(sell_price(item) * qty)`。

```python
capacity_units = action_capacity * per_action_units
# mastery 再与 headroom 取小
capacity_units = min(capacity_units, headroom)  # 仅 mastery
```

### 2.3 判定顺序与拒绝原因（全部可复算）

1. `unknown_npc` / `npc_not_alive`
2. `window_out_of_range`（deadline_days 越界）
3. `unsupported_type`
4. `bad_target`
5. `unknown_precondition` / `missing_precondition:<token>`
6. `no_income_source` / `skill_capped` / `bond_target_absent` / `shop_*` / `org_absent` / `escort_route_absent`
7. `target_exceeds_capacity`：
   `capacity=<action_capacity>×<per_action_units>=<capacity_units> < target=<target>`
8. `budget_actions_exceeded`：`expected_actions > action_capacity`
9. `budget_money_exceeded`：`expected_money > cash`
10. `budget_money_understated`（仅 shop）：`expected_money < shop_opening.stall`
11. 通过：`accepted=True`，并写入同一算式

Verdict 必须携带：`reason`、`equation`、`days`、`quota`、`per_action_units`、`capacity_units`、`cash`、`checks[]`（每项含 `name/ok/actual/limit/equation`）。

## 3. 生命周期护栏（制度性，不是启发式）

`SelfGoalLedger` 采用「提案先入队、激活再建目标」的两段式，避免提案层直接改世界：

1. **提案入队**：每个 NPC 每轮最多 1 份通过校验的待激活提案（`pending`）。`pending` 不是世界里的 Goal，不写 state。
2. **激活前检查（制度性硬门）**：
   - 初始目标保护：`initial_goal_id` 由内容包确定（每个 NPC `goals` 按 `(priority,id)` 排序后的第一条，`f"{npc}:{goal_id}"`）。若当前 `entity.goal_id == initial_goal_id` 且该目标仍 `active` → 不激活，记 `initial_goal_protected`；该提案保留待激活，直到初始目标结束。
   - 同 NPC 活跃自提案 ≤1：若该 NPC 已存在 `source=="self"` 且 `status=="active"` 的目标 → 不激活，记 `self_goal_active`。
3. **唯一创建入口**：只有激活通过后才发 `system_goal`（`source="self"`），经 Writer/Effect 建目标；提案层无 state 写权限，也不能跳过目标创建。
4. 同一 NPC 的 `pending` 若在窗口内未激活，到期作废（`pending_expired`），不产生世界事件。

## 4. 代码接入点

> 并行会话占用：`src/wsim/api/**`、`ui/player/**`、`content/**`、`sim/scheduler.py`、`sim/headless.py` 一律不改。自提案通过环境变量在 `MockReasoner` 内部启用，避免改 `headless.py`。

| 文件 | 改动 |
|---|---|
| `src/wsim/sim/self_goal.py`（新增） | `SelfGoalProposal`、`parse_proposal`、`FeasibilityValidator`、`SelfGoalLedger`、确定性 stub（`competent` / `scenarios`） |
| `src/wsim/actions/system.py` | `goal_source=="self"` → 目标 `source="self"`（其余保持 `urgent`/`npc`） |
| `src/wsim/sim/intent.py` | `MockReasoner` 读 `WSIM_SELF_GOAL_MODE`（空=关闭）；enable 时建 ledger；`decide()` 在 selector 之后消费 self choice；到期返回 `abandon`。改完立即 commit+push |
| `tests/test_self_goal.py`（新增） | schema、五类判定、生命周期、determinism |
| `tools/self_goal_feasibility.py`（新增） | 设环境变量跑 R 基线 + 两次 competent + 一次 scenarios，比较 hash，输出对照 JSON |
| `reports/self-goal-feasibility.md`（新增） | 结论、格式、算式、阶段 0 对照、判据、局限 |

环境变量（默认全部未设置 = 关闭）：
- `WSIM_SELF_GOAL_MODE`：`competent` / `scenarios`；
- `WSIM_SELF_GOAL_EVERY_DAYS`：默认 3；
- `WSIM_SELF_GOAL_MAX_WINDOW_DAYS`：默认 3；
- `WSIM_SELF_GOAL_TRACE`：提案/激活/完成留痕 JSONL 路径。

## 5. 验证

### 阶段 0（competent stub，零成本）

- 10 天 / 31 NPC / seed 42；`self_goal_mode=competent`，每 3 日提案。
- stub 规则（确定性，不读模型）：
  - 有雇主 → `earn_money`；否则 → `advance_skill`；
  - `target = min(goal_targets[kind], 0.9 * days * quota * per_action_units)`；
  - `expected_actions = days * quota`；`expected_money = 0`；
  - **对每个存活 NPC 每轮都提交一份提案**，不做 `initial_goal_protected` 预过滤；通过率分母 = 全部已提交提案。
- 初始目标未结束时，提案只入队不激活；初始目标结束后，下一日界/决策点激活并建目标。
- 判据：
  1. 通过率 ≥ 60%（分母为全部提交提案）；
  2. 被拒案例 100% 带可复算 `equation`；
  3. 两次重跑 `state_final`、`event_final`、`events.jsonl` sha256、`self_goal_proposals.jsonl` sha256 完全一致。

### 阶段 1（scenarios stub，零成本）

同一 10 天配置，`self_goal_mode=scenarios`，强制覆盖：
- 合法可达（放行）
- 超预算（`budget_actions_exceeded` / `budget_money_exceeded`）
- 窗口内不可达（`target_exceeds_capacity`）
- 初始目标保护（激活被拒 `initial_goal_protected`）
- 已有活跃自目标（激活被拒 `self_goal_active`）

单元测试逐类断言 `accepted` 与 `reason`，运行产物逐条留痕；生命周期两类另以强制激活调用验证。

### 对照表字段

`提交提案数 / 校验通过数 / 通过率 / 被拒原因分布 / 激活创建数 / 真正完成数 / 过期数 / 与纯规则臂 K1 的差`。

## 6. 判据（先定死）

- 阶段 0 全过，才允许申请真实模型预算；申请时必须先报请求数/token 上限/美元上限。
- 校验器放行不可达提案、或误拒可达提案 → 直接写 FAIL 收工。
- 不得把「提案是否可达」与「提案是否有意义/是否像人」混为一谈；后者另立评测。

## 7. 边界

只 stage 自有文件，禁止 `git add -A`；不碰 `docs/QUICKSTART.md`、`docs/known-limitations.md`、`docs/RELEASE-NOTES-p3i.md`、`dist/`、`dialogue*.py`、`text_guard.py`；不改 tag；不动 `content/`（冻结 hash `7ec670c1…`）。默认关闭。

## 7b. 独立额度（第二轮，修 bug）

机制不变，只把自目标从“主目标”降为“次级目标”：

- `goal_create` 支持 `secondary=True`：建目标但**不写** `entity.goal_id`，内容主线不受影响。
- Planner 在内容目标 `depth` 个动作之后，**每天额外排 1 个**自目标动作（`SELF_GOAL_ACTIONS_PER_DAY=1`），并把该动作 intent 打上 `_goal_id`。
- `goal_progress(state, npc, kind, units, tick, goal_id=...)`：显式 `goal_id` 只认该目标，类型不匹配返回空，**绝不回落**到活跃目标。
- 校验器配额改为 `action_capacity = deadline_days × 1`；`per_action` 与独立额度真实产出对齐（`mastery=0.0025×2`，`wealth=max(单件净售价,5)`）。
- 自额度动作使用独立 memo，避免 `_goal_step` 的 opened/joined/apprentice/married 标记压掉次日内容动作。

判据：自提案臂 K1 ≥ 纯规则臂 −5pp（≥63.13%），且“自提案占用既有额度次数”= 0。

## 7c. 代理指标（替代 K1 主指标）

- **SGR**：激活的自目标中真正完成的比例；同时报 `target/capacity`（校验器预筛后的基线难度）。SGR 高只证明提案与执行一致，不证明提案质量。
- **STC**：自提案涉及的类型数与分布。
- **SGC**：完成一个自目标后 3 天内是否提出下一个（及是否同类型/顺承）。须注明 stub 固定 3 天节奏会抬高该值。
- 三项均可仅由 `events.jsonl` + `self_goal_proposals.jsonl` 复算（`tools/self_goal_metrics.py`）。
- 提案理由 rubric 4 维（处境贴合 / 目标具体可执行 / 与目标的因果链 / 是否像本人会说），先做双打分者可操作性验证（`tools/self_goal_rubric.py`），不做拟人性结论。

## 8. 已知风险

- 规则 planner 只认识既有 `content_type` 分支，因此自提案复用既有类型；这是「可执行」而非「有意义」的保证，报告须写明。
- 深度额度与 3 日窗口决定了可声明目标的上限；目标体量必须落在容量内，否则被拒是预期行为。
- competent stub 的通过率不等于真实模型质量；阶段 0 只证明引擎与校验器闭环。
