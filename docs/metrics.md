# world-sim 指标口径 v1.1.0

- 版本：`1.1.0`
- 生效日期：2026-11-03
- 适用范围：P1（单区域、日程驱动、30 NPC、72 游戏日）；P2-B 起意图驱动
- 变更记录见文末 CHANGELOG。任何口径改动（新增签名、修改粒度、恢复阈值判定）都必须先更新本文档的版本号和 CHANGELOG，再改代码。

> **v1.1.0 变更只做两件事**：(1) 把 K1 的跨版本对比规则写死；
> (2) 为 P2-B 新增的 4 个引擎动词补齐对象层签名（v1.0.0 只定义 13 个动词）。
> 本节先于对应代码提交，作为“事前定义、非事后拟合”的证据；同一次运行必须同时
> 输出 v1.0.0 与 v1.1.0 两个口径，便于复核两种粒度。

## 0. P1 的指标定位

P1 的 K1 / K3 / K6 只负责**诊断与守恒**，不用于证明“世界已经足够多样”。

- K6 是硬约束：货币守恒误差必须为 0，债务必须能从事件日志独立复算。
- K1 是诊断：报告全量内容目标的推进情况，而不是只报引擎已创建的那部分。
- K3 是诊断：P1 是日程驱动（上班、吃饭、移动由固定日程决定），verb 层集中是**预期现象**。
  多样性门槛推迟到 P3 引入意图层（目标选择、社交自主行为、LLM/规划器）后再判定。
  P1 不设 K3 pass/fail，也不允许用事后拟合的阈值宣称“通过”。

## 1. K3 双层报告

K3 必须同时输出两层，任何一层都不能被另一层代替：

1. **verb 层**：全部已注册 NPC 动词的计数（v1.1.0 为 17 个）、Top-10 占比、熵、每 NPC 独立 verb 数。
2. **对象层**：`action signature`（动词 + 参数语义 + target 类型）的计数、Top-10 占比、熵、
   每 NPC 归一化熵、NPC 间 JS 散度。

### 1.1 签名的统计对象

只统计**成功执行的 NPC 动作**：

- `event.outcome == "ok"`
- `event.source != "system"`
- `event.verb` 不以 `system_` 开头

被拒绝的意图、系统定时动作（`system_living` / `system_plan` / `system_debts` 等）不进入 K3。

### 1.2 签名规格（spec v1.1.0）

`signature = verb + ":" + <参数语义> + <target 类型>`

v1.1.0 保留 v1.0.0 的 13 个动词签名不变，只补齐 P2-B 新增的 4 个引擎动词：

| 动词 | 签名（v1.1.0） | 说明 / v1.0.0 的兜底 |
|---|---|---|
| move | `move:<目的地>` | 目的地 id；不含路线长度 |
| cultivate | `cultivate:<技能>` | 技能 id；不含小时数 |
| work | `work:<工作地点>` | 工作地点 id；不含小时数 |
| consume | `consume:<物品>` | 物品 id；不含数量 |
| buy | `buy:<物品>` | 物品 id；不含数量与卖家 |
| sell | `sell:<物品>` | 物品 id；不含数量与买家 |
| trade | `trade:<offer>|<want>|<pay|recv|even>` | offer/want 为排序后的物品 id 集合，不含数量；方向由 `money_delta` 符号决定 |
| apprentice | `apprentice:npc` | 只保留 target 类型，不保留师傅 id |
| marry | `marry:npc` | 只保留 target 类型，不保留配偶 id |
| join_sect | `join_sect:<sect|org|location>` | 只保留 target 类型，不保留组织 id |
| open_shop | `open_shop:<shop_type>` | stall / storefront / branch |
| escort | `escort:<起点>-><终点>` | 路径首尾；不含中间路径与镖银 |
| found_sect | `found_sect:settlement` | 建派地点类型；不含门派名 |
| **debt_settle** | `debt_settle:<initial|wage|living|escort|other>` | 按债务来源分类；不含债务 id、金额、债权人 |
| **quest_accept** | `quest_accept:<quest_verb>` | 按任务模板的 verb 分类（move/escort/buy/work/trade）；不含任务 id、报酬 |
| **quest_deliver** | `quest_deliver:<quest_verb>` | 同上；交付与接单分开，避免两个语义不同的动作被并进同一桶 |
| **recruit** | `recruit:npc` | 只保留 target 类型，不保留被招揽者 id |

**为什么必须升版**（事前定义理由）：

1. v1.0.0 的签名表只覆盖 13 个动词，P2-B 新增的 `debt_settle` / `quest_accept` /
   `quest_deliver` / `recruit` 全部落入 `return f"{verb}:?"` 兜底，各自只有一个桶。
   这是**欠粒度**（同类行为合并），不会虚高 Top-10 占比，但意味着 4 个类别不在 spec
   覆盖范围内，任何跨版本 K3 对比都缺少共同定义。
2. 新签名必须保持既有粒度原则：**不含 NPC id、数量、金额、时间；target 只用类型或
   内容包定义的动作语义**。`debt_settle` 的来源类别由债务 id 前缀（`debt:init` /
   `debt:wage` / `debt:living` / `debt:escort`）决定，属于内容语义而非运行时自增 id；
   `quest_*` 按模板 verb 分类，`recruit` 只保留 target 类型。
3. 升版在跑数之前提交，且同一 run 同时报告 v1.0.0（兼容口径）与 v1.1.0（主口径）。
   若 v1.1.0 的数值比 v1.0.0 差，必须如实报告，不得回退到旧口径。

粒度规则：

- **对象 id 必须来自内容包**（地点 / 物品 / 技能 / 组织），不来自运行时自增序号。
- **不含 NPC id**：签名衡量“做了什么类型的事”，不衡量“和谁做”。把关系对手放进签名会让
  两两关系机械稀释 Top-10 占比，属于用维度堆砌制造多样性。
- **不含时间、数量、金额、随机种子**：这些会让同一行为在不同 tick 生成新签名，属于假粒度。
- 目标类型只分 `npc` / `sect` / `org` / `location` / `settlement`，不展开具体目标 id。
- 同一内容包、同一规则下，签名集合与 NPC 数量无关（扩展 NPC 不会改变签名定义）。

### 1.3 折叠视图（必须同时报告）

为了检验“通过是否来自拆桶”，对象层必须同时给出两个折叠视图：

- `move_collapsed`：把 `move:<地点>` 折叠为 `move:any`，其余签名不变。
- `verb_only`：折叠为纯动词，等同 verb 层。

折叠视图只用于诊断“拆桶红利”，不是替代口径。

### 1.4 报告项（不含 pass/fail）

verb 层：

- `verbs` 计数、`top10_share`、`entropy_bits`、`normalized_entropy`、`distinct_verbs`
- `per_npc_distinct_min`、`per_npc_normalized_entropy_mean`、`per_npc_normalized_entropy_min`

对象层：

- `signature_top10_share`、`signature_entropy_bits`、`effective_signatures`
- `npc_normalized_entropy_mean`、`npc_normalized_entropy_min`
- `pairwise_js_mean`、`pairwise_js_min`、`pairwise_js_max`
- `js_vs_global_mean`、`null_js_mean`、`signal_to_noise`（零假设为重采样同分布）
- `signatures_top`（前 20 个签名及计数）

折叠视图：以上核心六项（Top-10、熵、NPC 熵均值/最小、两两 JS、独立 verb 最小值）。

### 1.5 历史阈值（已废止）

v2 曾使用 `Top-10 <0.80`、`签名熵 ≥4.0`、`NPC 熵均值 ≥0.70 / 最小 ≥0.50`、
`两两 JS ≥0.30`、`独立 verb ≥3`。独立复核证明这些阈值有两个问题：

1. 它们是在看到 v2 实跑数值之后设定的，不是事前预注册；
2. 其中 5 项可以通过“把 move 按地点拆桶”获得，`move_collapsed` 后不达标。

因此 v1.0.0 **取消 K3 pass/fail**。上述数字仅作为历史参照保留在 `reference_v2_thresholds`
字段中，字段名与文档均标注 `revoked`，不得用于结论。

## 2. K1 目标推进率

### 2.0 对比规则（v1.1.0 写死，任何报告不得绕过）

- **K1 只允许在同一快照内比较**：同一 `content_hash`、同一目标集合、同一 seed、同一
  模拟天数。分子与分母必须来自同一次运行。
- **跨内容版本一律标记 `non_comparable`**：只要两边的 `content_hash` 不同，就不得把
  一方的分子放到另一方的分母上，也不得把两个版本的 K1 百分比当成“能力提升”。
  跨版本可报告的是**同内容包、同 seed、两个代码版本**的对照运行；否则只能报告
  “本快照 K1 = X/Y”，不能报告“从 A% 提升到 B%”。
- 每次运行的 summary 必须输出 `k1.comparison_basis`：
  `{"basis": "same_snapshot_only", "content_hash": ..., "goals_total": ..., "cross_version_comparable": false}`。
- 历史 P1 的 `68/90` 与 P2-B 的 `70/91` 属于不同的 `content_hash`，任何差值都为
  `non_comparable`；报告只能并列陈述，不能相减。

### 2.1 分子与分母

- 分母：`content/npcs.yaml` 中的**全部**目标（当前快照 91 个），不是引擎在本轮实际创建的目标数。
- 分子：目标已创建，且满足
  - 进度序列随 tick 单调不降；
  - 期末进度 > 0.1。
- 未创建的目标一律记为未推进，并列出阻塞原因（前序目标未完成）。
- 同时报告：
  - `k1_rate_content`（全量口径，主口径）
  - `k1_rate_created_only`（旧口径，仅参考）
  - `one_shot_*`：一次动作即完成（`progress_log` 只有 1 条且进度 ≥0.999）的目标数量、
    按类型分布、占已推进目标比例
  - `k1_rate_excluding_one_shot`（剔除一次性目标后的全量口径）
    = `(progressed - one_shot_progressed) / (total - one_shot_created)`

一次性目标的判定只依赖状态里的进度日志，不依赖 verb 名称；`join_sect` / `open_shop` /
`found_sect` / 部分 `gain_status` 通常属于此类。

## 3. K6 货币守恒与债务

### 3.1 守恒

`sum(accounts) - (minted - burned) == 0`，且不存在负余额。每笔事件在审计侧独立复算，
不得直接使用 `WorldState` 的既有汇总作为“已校验”。

### 3.2 债务独立审计

`debt_ledger` 只是 canonical 投影，**不得**以“应收合计 == 应付合计”作为校验，因为两者
由同一个 `amount` 同时加减，恒真。

独立审计从事件日志重建：

1. 从 `debt_create` 记录每笔债务的 creditor / debtor / amount / note / 创建 tick；
2. 逐事件累加实际账户变动：`transfer` 的 src/dst、`debt_settle` 按债务两端账户、
   `mint` / `burn`；
3. 对每笔 `debt_settle` 校验：应收方账户增加额 == 应付方账户减少额 == 债务金额；
4. 对每笔债务金额与其来源事件独立核对：
   - `initial_debt` ↔ 内容包 creditor/debtor 关系金额；
   - `unpaid_wage` ↔ 同事件 `work` 的应发 − 实付；
   - `living_*` ↔ 同事件生活成本 − 实付；
   - `escort_fee` ↔ 镖单分成 − 实付；
5. 核对债务声明金额 − 已结算金额 == 期末 `WorldState.debts` 金额与状态；
6. 检查逐事件复式平衡：非 genesis 事件中，所有账户正变动之和 == 负变动之和；
   **任何“钱包增加但付款方未减少”都判 K6 失败**。

## CHANGELOG

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.1.0 | 2026-11-03 | K1 写死“同快照内比较”规则并强制输出 `comparison_basis`；K3 签名 spec 升版，补齐 P2-B 新增 4 个动词（`debt_settle` / `quest_accept` / `quest_deliver` / `recruit`）的签名规则与理由；同一 run 同时输出 v1.0.0 兼容口径与 v1.1.0 主口径。本变更先于实现与跑数提交，用于证明事前定义。K3 仍为诊断，无 pass/fail。 |
| 1.0.0 | 2026-11-03 | 固定双层 K3（verb + 对象）；签名 spec v1.0.0；P1 K3 改为纯诊断，取消 pass/fail；v2 阈值标为 revoked；K1 分母改为内容包全部目标并新增一次性目标口径；K6 增加事件级债务独立审计；`found_sect` 遵守 `min_members`；`home` / `goal_targets` 改为读取内容包并标注默认回退 |
| （历史）v2 | 2026-11 | 签名阈值 0.80 / 4.0 / 0.70 / 0.50 / 0.30 / 3；经复核为事后拟合且受 move 拆桶影响，已废止 |
| （历史）v0 | 2026-11 | “verb Top-10 占比 <40%”；13 个动词时数学下界 10/13≈76.9%，不可达，已废止 |
