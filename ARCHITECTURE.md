# 架构说明（P2-B）

## 数据流

```
content/*.yaml ──load──> Content
                         │
                         ├─ genesis.py ──Effect[]──> Writer.bootstrap
                         │
系统调度 / NPC Intent ──> Writer.submit(intent)
                         │
                         ├─ Action.preconditions(state, intent) -> ["reason"] | []
                         ├─ Action.apply(state, intent, rng) -> Effect[]     # 纯函数
                         ├─ trial = state.clone()
                         ├─ apply_effects(trial, effects)                    # 原子
                         ├─ ok  -> Event 追加到 SQLite events（唯一事实源）
                         │        state = trial（投影切换）
                         └─ 失败 -> Event(outcome=rejected, effects=[])
```

启动时 `WorldState` 从空状态 + genesis 事件建立；之后每一条状态变化都必须经过
`Writer.submit` 和事件日志。`tools/replay.py` 只读 `events.sqlite`，按顺序把
`outcome=ok` 的 effects 重新应用到空投影，哈希必须与实跑一致。

## 模块边界

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `core/` | canonical JSON/SHA-256、虚拟时钟（1 tick=10min，144 tick/日）、SplitMix64 PRNG、内容加载、Intent/Effect/Event 数据类 | 不碰世界状态 |
| `world/state.py` | `WorldState`：实体、账户、商业体、门派、债务、关系、物品、目标、日程、记忆；`clone()` / `to_dict()` / `hash()` | 不实现业务规则 |
| `world/effects.py` | 所有状态变更原语：`mint/burn/transfer/create_*/set_*/item_*/relation_*/debt_*/goal_*/schedule_*`；非法变更抛 `EffectError` | 不做决策 |
| `world/writer.py` | 唯一写者；一个 Intent 一个事务：前置检查 → 纯 apply → clone 上原子应用 → 追加事件 → 切换投影 | 不解释内容 |
| `actions/` | 17 个 NPC/意图动词（move/cultivate/work/consume/buy/sell/trade/apprentice/join_sect/marry/open_shop/escort/found_sect/recruit/quest_accept/quest_deliver/debt_settle）+ `system_*` 计划/回复/生活/债务/店税/储蓄税/分红/任务发布与超时/押镖到达 | 不直接改 state，只返回 Effect[] |
| `sim/genesis.py` | 把内容包投影成创世事件；唯一允许 `mint` 的地方（初始资产 + 区域 float） | 不做日常增发 |
| `sim/headless.py` | 虚拟时钟主循环、系统动作排程、日终结算、K6/K1/K3 采集、输出 | 不做业务计算 |
| `audit/` | 独立采集：守恒/债务投影、非法率、K1 全量目标、K3 双层诊断、关系一致性、现金流、工资链路、债务事件级独立审计、内容对齐 | 不修改状态 |
| `tools/replay.py` | 事件回放与哈希校验 | 不写事件 |

## 指标口径（v1.0.0，见 `docs/metrics.md`）

- **K6**：守恒误差 + 债务事件级独立审计。`debt_ledger` 只是投影，“应收 == 应付”
  是恒真式，不作校验；`audit/debt_audit.py` 从事件日志重建账户与每笔债务来源。
- **K1**：分母为内容包全部目标（当前 91），未创建记失败；同时报告 `k1_rate_excluding_one_shot`
  （剔除一次性目标后的口径）。P1 基线 68/91 = 74.73%。
- **K3**：verb 层 + 对象层双层诊断，P1 只做诊断、不做 pass/fail；v2 阈值已标记
  `revoked`，并强制同时报告 move 折叠视图。

## 关键约束

1. **唯一写者**：只有 `Writer` 持有可变投影；动作拿到的是只读 state。
2. **纯 apply**：`Action.apply` 只读 state + 注入 PRNG，返回 `Effect[]`；所有变更落到
   `apply_effects`，事件日志记录的是规范化的 effect data。
3. **原子性**：`Writer.submit` 在 `clone()` 上应用；任一 Effect 失败则整笔 Intent
   回滚，只追加 `rejected` 事件（effects 为空）。
4. **确定性**：无 `random` / `time.time()` / `datetime.now()`（`tests/test_determinism_lint.py`
   静态拦截）；随机数 `PRNG(seed, event_seq, salt)`；时间只来自 tick。
5. **浮点规范化**：effect data 在写入日志和应用时都经过 `normalize()`（round 6），
   避免“实跑保留 1e-17 精度、回放用 6 位精度”导致哈希跨界不一致。
6. **经济闭环**：genesis 之后只有 `transfer` / `burn`，没有日常 `mint`；工资不足、
   生活成本不足都记 IOU（`debt_create`），不增发。K6 误差必须为 0，且债务必须由
   `audit/debt_audit.py` 从事件日志独立复算通过。

## 事件与投影规模（seed=42，72 日）

- 事件 19,322 条（其中 rejected 64，0.3312%）
- 账户约 50，实体 30，商业体约 50，债务 118 条（初始 12 + 工资欠条 105 + 押镖 1）
- 状态哈希与事件流哈希见 `runs/rev72d/` 与 README

---

# P2：框架层扩展边界

P2 的目标不是加功能，而是验证三条扩展路径：

1. 内容只写数据就能扩展；
2. 规则只加插件就能扩展；
3. 能力只能经沙箱调用。

```
┌──────────────────────────────────────────────────────────────────────┐
│                           不可扩展的内核                              │
│  Writer 单写者 / 一 Intent 一事务 / 事件日志 / canonical 哈希          │
│  Effect 原语语义 / 守恒校验 / 虚拟时钟 / PRNG / MCP 权限与审计         │
└───────────────▲──────────────────────▲──────────────────────▲───────┘
                │                      │                      │
      ┌─────────┴─────────┐  ┌──────────┴─────────┐  ┌─────────┴─────────┐
      │ 内容层（数据）      │  │ 规则层（插件）      │  │ 能力层（沙箱）     │
      │ content/           │  │ plugins/actions/    │  │ src/wsim/mcp/     │
      │ manifest.yaml      │  │ manifest.yaml       │  │ read              │
      │ additions/*.yaml   │  │ apply(state,args)   │  │ propose_intent    │
      │ NPC/物品/地点/职业/ │  │ -> Effect[]         │  │ admin_apply       │
      │ 门派/动词元数据     │  │ 只能返回已登记原语   │  │ + 审计 + 双人确认  │
      └────────────────────┘  └─────────────────────┘  └───────────────────┘
```

## 内容层 `content/`

- `content/manifest.yaml` 是唯一入口：声明 `manifest_version`、`engine_min_version`
  和每个包（type/path/version）。additions/ 下的文件必须显式登记，否则校验失败。
- 可新增：NPC、物品、地点、职业、门派、动词元数据、经济参数。
- 加载器校验：缺字段、id 重复、引用不存在的 NPC/地点/物品/职业、师徒关系成环、
  地点图不连通，错误格式为 `content/<file>:<line>: <原因>`。
- 新职业只需写 `wage/skill/workplace/living_cost`；加载器合并进 `economy` 的
  工资/生活成本/技能表，`sim/genesis.py` 不需要知道 `roles.yaml`。
- 新门派只需写 `id/name/location`；genesis 按 location 实例化为公共组织
  （`sect:<id>`，`founder=""`），`join_sect` 仍走原 `organization_at`。
- `tools/new_content.py <npc|item|location|role|sect|verb>` 生成带注释片段，
  自动登记 manifest 并跑校验；校验失败回滚本次写入。
- **动词分两层**：`content/` 只描述元数据（args/allowed_roles）；行为实现必须写
  `plugins/actions/` 插件。只写动词 YAML 不会凭空产生可执行动作。

## 规则层 `plugins/actions/`

插件目录契约：

```
plugins/actions/<id>/
  manifest.yaml   id / verb / version / entry / args / preconditions / effects
  action.py       def apply(state, args) -> list[Effect]
```

- 启动时扫描目录注册，新增插件不需要改 `src/`；插件 verb 不得覆盖内置 verb。
- 适配器把插件包成 `Action`：先跑 manifest 声明的前置条件，再调用 `apply`。
- 插件拿到的是 **state 深拷贝**；调用后比较深拷贝哈希，若被修改则抛
  `plugin_mutated_state`，Writer 拒绝整笔事务。
- 返回的 Effect 必须同时出现在 manifest.effects 和全局 `EFFECT_OPS`；未知原语、
  未声明原语、`mint` 都整笔回滚。
- 示例 `borrow_money`：`transfer + debt_create + memory_add`，证明不加引擎代码
  也能新增动词。测试覆盖“只读入参、不改 state”、余额不足拒绝、恶意插件回滚。

### 内核不可扩展的部分（插件不许绕过）

- 单写者与事务边界：插件不能调用 `Writer.submit`、不能写 SQLite events。
- 事件日志、seq、canonical 哈希、回放语义。
- Effect 原语语义（尤其 `transfer` 守恒、`mint` 仅 genesis/system）。
- 虚拟时钟与 PRNG：插件不读墙钟、不用全局随机。
- MCP 权限、审计、双人确认。
- 插件可以做的是“读 state + 返回已登记原语”，其余一律由内核执行。

## 能力层 `src/wsim/mcp/`

| 权限 | 能做什么 | 不能做什么 |
|---|---|---|
| `read` | 读世界快照（深拷贝） | 产生 Intent、改状态 |
| `propose_intent` | 生成 Intent 放入 outbox，交给 Writer | 直接提交、直接改状态 |
| `admin_apply` | 改状态 | 必须二次 token + 第二人批准，且经 Writer 提交 |

- 所有调用（含越权）写审计：caller、permission、method、params_digest、结果、
  error、虚拟 tick、audit seq、`identity_source=server_map`。审计不读墙钟，保证回放一致。
- 权限不再由调用方自报：`MCPGateway` 持有服务端 `identity -> permission` 映射；
  `admin.request` 的 token 绑定 requester + tick + intent 摘要，`admin.approve`
  必须由另一 identity 执行，`admin.apply` 只接受“批准人本人且不是申请者”。
- `MockMCPServer` 提供本地函数调用与 stdio JSON 模式，不接外部服务。

## 调度层 `src/wsim/sim/lod.py`

- 四档：`silent / background / active / spotlight`，配置在 `config/lod.yaml`
  （`share` 控制初始分布，`wake_interval_ticks` 控制唤醒间隔，`cost_weight`
  供未来成本模型）。
- `LODScheduler` 用最小堆维护下次唤醒；`advance_to(tick)` 只弹出 `<= tick` 的
  到期项，复杂度 O(到期数·log N)，不遍历全部 NPC。
- `Reasoner.decide(npc_state, world_view) -> Intent[]` 是真实模型接入点；
  本阶段只有确定性 `MockReasoner`（规则 + PRNG）。
- 动态升降档：`update_tiers(active_players/relations)` 依据玩家邻近、关系活跃度、
  长期无交互调整档位；档位变化写入 `lod_tiers.jsonl`，供成本模型验证。
- `stats()` 输出每个 NPC 的 LOD 档位与累计唤醒次数，`framework_check` 写
  `runs/framework/lod.json`。

## 意图层 `src/wsim/sim/intent.py`（P2-B）

- `GoalSelector`：按内容目标优先级轮转；紧急状态（stamina<25 / 欠债≥50 / 现钱<40）
  可抢占主线，同一 NPC 两次紧急抢占至少间隔 2 天；`found_sect` 目标不轮转、不放弃。
- `Planner`：routine（就近进食 → 隔日上工 → 目标动作）+ 目标动作深度按 LOD 档位
  （silent/background 1、active 2、spotlight 3）；任务、采购、招揽、押镖都在这里
  展开为 Intent；前置不满足时换路径（先移动/换货源），不是直接拒绝。
- `MockReasoner v1`：确定性（SplitMix64 / 纯排序，无 `random`/墙钟），每次决策写
  `reason`（含 persona 摘要）；运行期被拒后由 `replan` 切换变体。
- 主循环只处理 LOD 到期唤醒 + 事件堆；`system_plan` 已下线，`headless` 不再提交它。

## 内容机制（P2-B）

- **任务**：`content/quests.yaml` 12 个模板；`system_quest_post` 每日发单，NPC
  `quest_accept` / `quest_deliver`，超时 `system_quest_fail`。报酬/时限全部来自内容包，
  状态写入 `quest_*` 事件，可回放。
- **镖路**：以 `content/escort_routes.yaml` **线路值为准**（`duration_days` / `fee_min`
  / `fee_max` / `agency_cut` / `risk_loss_prob` / `risk_events`）；只有未命中线路时才
  回退 `economy.escort` 全局值，并写 `escort_global_fallback` 记忆。**两条路径互斥，
  不再叠加计算**。
- **门派**：`founding_cost=2500`（含 500 注册费，其余 2000 进入门派公账），
  `min_members=5`；清议社/药行会/镖行同盟由白露生/苏合香/卓不凡通过真实
  `recruit` → `found_sect` 发起；创世不再预置有发起人的门派。
- **经济回流**：储蓄税（burn）、店铺分红（transfer 给店主）、店税；`debt_settle`
  作为插件实现（`plugins/actions/debt_settle`），由 urgent_debt 目标触发，产生
  真实还款正样本。
- **personas**：`content/personas/*.md` 首行摘要注入 MockReasoner 的 `reason`，
  真实模型接入时复用同一路径。


## Provider `src/wsim/agent/provider.py`

- 单一配置 `base_url + key + model`，加 `timeout_s / max_retries / offline`。
- `ProviderClient` 在 P2 不联网（`_request` 故意抛 `ProviderUnavailable`）；
  `FallbackReasoner` 捕获后降级到 `MockReasoner`，并记录降级次数/尝试次数。
- 接入真实模型时必须补：HTTP 传输、超时实现、错误分类、密钥管理、
  prompt/响应 schema、成本与速率限制。

## P2 验收

```
python3 tools/framework_check.py
```

五项依次检查，全部 PASS 才算通过：

1. 新增内容不改码 + 停摆门禁（拒绝率 ≤2%、每日最少动作、每 NPC 有动作、`burned>0`、
   `unknown_verb`/`system_plan` 出现即 FAIL；并从 `events.sqlite` 独立重算）；
2. 新增插件不改码（目录扫描注册；apply 前后**完整 state 哈希**不变，含 tick/地点/关系；
   `evil_tick` 改 tick=999 必须被拒）；
3. MCP 身份映射与越权拒绝（服务端 permission、双人 token、申请人/第三人 apply 被拒、
   审计 `identity_source=server_map`）；
4. LOD 不遍历全量（外部计数：60 tick 内 `decide` 调用数 == 唤醒数且 != NPC 总数；
   `headless` 10 天 `system_plan=0`）；
5. 守恒 / 债务独立重算（从 `events.sqlite` 重放：minted/burned/账户和、9 项债务审计、
   `settled_events>0`、与 `state_final` 哈希对账）。

## 冻结与复现

- 每个阶段一个 commit；交付数字前冻结 `src/`，从干净提交重跑并附 commit hash。
- 同 seed 必须同 state/event 哈希；`python3 tools/replay.py --run runs/<id>` 必须 MATCH。
- 测试 fixture 使用真实 `content/`（`real_content` / `real_state`），合成 fixture 只保留
  插件/单元测试的最小场景。

