# Delivery P3-B · 混合决策、三模型槽位与意图理解层

- 日期：2026-11-03
- 范围：规则/模型混合决策、世界状态 Prompt、修复重试、异步对话、批处理、三槽位（decision / dialogue / intent）、意图功能表、假端点 10d/72d 验收
- 内容哈希：`835217231c0e595221ac1d91bf7892d77aa812850d6f875b37cf2a2d92e73986`
- tag：`delivery-p3b`
- 本轮真实端点调用：**0 次**（环境无 key；真实 Xing API 同样未调用）
- 结论：**72 天 K1 71/91 = 78.02%，达到 ≥60% 硬指标**；拒绝率 1.46% ≤ 2%；守恒 0；replay MATCH

## 0. 结论

1. **“人不会过日子”的根因是分工错了，不是模型能力**：P3-A 让模型整包替换规则计划，生存动作（上工/吃饭/修炼/通勤/睡觉）消失，72d K1 塌到 3/91。P3-B 把生存动作交还规则调度器，模型只做目标选择、社交与重规划，K1 回到 **71/91（78.02%）**。
2. **与 Mock 基线同值**：P3-B 假端点 72d K1 = `delivery-p2.2` Mock 72d 的 71/91；10d 都是 45/91。说明分工修复保住了原有生存节奏，模型层没有额外提升 K1——本轮目标是“恢复过日子”，不是用模型刷 K1。
3. **模型动作全部过预检**：72d 模型动作 1,086 条、规则动作 9,839 条；预检丢弃 97 条，全部是 `rule_owned_survival`（模型试图替换生存动作），**0 条因 adjacency/物品/钱/时间窗被丢弃**。
4. **三槽位分开记账**：`decision` 2,064 次、`intent` 2,064 次、`dialogue` 0 次。`intent` 上游桩不返回 usage，token 按要求记 `null`（`usage_source=missing`），不再把字符估算当账单；**不提供混合总价**。
5. **延迟**：实时对话改为异步队列，提交不阻塞 tick；非对话决策按批并发（默认 4）。12 NPC 一个游戏日墙钟 **0.364s**（假端点 50ms 固定延迟）。
6. **本轮未做真实端点抽样**：无 key。接入前需要用户提供三样信息，见第 8 节；真实 72 天跑等预算批准。

## 1. 混合决策（核心改动）

### 1.1 分工

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| 规则调度器（`MockReasoner` / `Planner`） | 生存类动作：`work` / `consume` / `cultivate` / 通勤 `move` / 睡觉；按日程与 LOD 排 | 目标选择、社交、失效重规划 |
| `decision` 槽 | 今天想做什么（自然语言意图）、社交意图、计划失效后的 replan | 不直接给 JSON 动作，不替换生存动作 |
| `intent` 槽 | 把自然语言意图落成参数（`verb + args`） | 不做目标选择，不做生存排程 |
| `dialogue` 槽 | 玩家在场对话、聚光档 NPC 发言、角色扮演输出 | 不参与世界决策 |

实现位置：[provider.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/agent/provider.py)、[intent.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/intent.py)、[prompt.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/agent/prompt.py)。

关键守卫：

- `RULE_VERBS` 拥有生存动作；模型输出命中这些动词时，在预检阶段以 `rule_owned_survival` 丢弃，不整包拒绝。
- 模型给出的“今日目标”只有在与规则选择器 `GoalSelector.choose` 选出的 `goal_id` 一致时才采纳（`model` 来源），否则以规则为准（`goal_ignored`）。这样既保留模型表达，又不会重建目标丢进度。
- 夜间批处理（`night_batch`）只预计算意图并缓存，不提前生成规则动作；白天 LOD 唤醒时再套规则底稿落地，避免打乱原有节奏。

### 1.2 动作占比（72d / 10d，假端点，seed 42）

| 指标 | 72d（31 NPC） | 10d（31 NPC） |
| --- | ---: | ---: |
| 模型动作 | 1,086 | 188 |
| 规则动作 | 9,839 | 2,700 |
| 模型动作占比 | **9.94%** | **6.51%** |
| 规则动作占比 | **90.06%** | **93.49%** |
| 预检丢弃 | 97 | 27 |
| 丢弃原因 | `rule_owned_survival` 97 | `rule_owned_survival` 27 |
| `goal_from_model` / `goal_ignored` | 364 / 615 | 91 / 119 |

**被丢弃动作的前置条件原因分布**：72d 只有 `rule_owned_survival: 97`；其余 adjacency / 物品 / 钱 / 时间窗原因均为 0。也就是说，模型在“允许它做的社交动作”上没有产生前置条件脏数据。运行期另有 206 条规则动作被世界拒绝（见 4.3），与模型动作无关。

## 2. Prompt 世界状态与校验修复

`build_decision_prompt` / `build_state_summary` 注入：

- NPC 当前地点、相邻地点与出口；
- 手上钱与物品清单；
- 可执行动作清单（17 个动词白名单，含必填参数与前置条件）；
- 当天时间、剩余工时、LOD 档位；
- 最近事件与角色语气预算。

校验链：

1. `verb` 必须在 17 个 NPC 白名单内；
2. 参数齐全、`target` 存在、动作数量不超过上限；
3. 失败时把错误原因回喂，**只重试 1 次**（`repair_attempted` / `repair_succeeded`）；
4. 仍失败才回退 Mock，`fallback_count += 1`。

### 2.1 通过率（72d / 10d）

| 指标 | 72d | 10d |
| --- | ---: | ---: |
| 决策模型调用 | 2,064 | 267 |
| 首次通过 | 2,064（100%） | 267（100%） |
| 修复尝试 | 0 | 0 |
| 修复后总通过率 | 100% | 100% |
| 最终失败 | 0（0%） | 0（0%） |

假端点始终返回合法结构，因此修复路径在验收 run 中未被触发；`tests/test_p3b_hybrid.py::test_schema_repair_succeeds_once` 用“首次坏 JSON、第二次合法”证明修复重试确实生效。

## 3. 延迟与吞吐

### 3.1 实时对话异步

[dialogue_async.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/agent/dialogue_async.py)：玩家发话 `submit()` 立即返回 `request_id`，工作线程调用对话模型，结果写回队列；tick 不等待模型。测试断言 `submit()` < 0.1s 且 tick 继续推进。

对话槽预算超限返回固定兜底：`今日对话额度用尽，请明天再来。`；调用失败返回 `（对方似乎没听清，稍后再试。）`。

### 3.2 批处理与并发

- `ProviderClient.complete_many(requests, concurrency=N)` 用线程池并发；单请求仍走 `complete()` 以保留 `last_usage` / `last_latency_s`。
- `LODScheduler.advance_to_batched(target_tick, batch_size)` 先收集到期唤醒，再按批提交，jitter 与事件顺序不变。
- 测试 `test_complete_many_runs_concurrently`：4 个 0.5s 请求并发完成 < 0.8s。

### 3.3 一个游戏日的墙钟

12 NPC、1 游戏日、假端点固定 50ms 延迟：

| 指标 | 值 |
| --- | ---: |
| 墙钟总耗时 | **0.364s** |
| decision 调用 | 12 |
| intent 调用 | 12 |
| decision 延迟 p50 / p95 | **0.061s / 0.133s** |
| intent 延迟 p50 / p95 | 0.002s / 0.004s |

72d、31 NPC：墙钟 172.1s，4,128 次调用，约 **24.0 calls/s**（含假端点每请求 50ms 的人为延迟；真实延迟另计）。

> P3-A 实测的真实端点 p50 11s / p95 22s 仍不能用于同步实时路径；P3-B 用异步对话 + 批处理把等待移出 tick，而不是假装延迟消失。

## 4. 验收（假端点，固定 seed 42）

### 4.1 72d 主结果

| 指标 | 结果 | 门槛 | 判定 |
| --- | ---: | ---: | --- |
| K1（全量 91） | **71/91 = 78.02%** | ≥60% | **PASS** |
| 拒绝率 | 206 / 14,111 = **1.46%** | ≤2% | PASS |
| 守恒误差 | **0** | 0 | PASS |
| replay | **MATCH**（state `96a869a4…`、event `9c11fb3f…`） | MATCH | PASS |
| `verdict.k1_pass` | true | — | — |
| `verdict.k6_*` | 全部 true | — | — |

K1 细分：`goals_created=86`、`goals_completed=45`、`goals_progressed=71`。按内容类型：bond 18/18、shop 11/12、mastery 14/16、faction 14/19、wealth 14/26。

### 4.2 10 天对照

| run | K1 | 拒绝率 | 模型/规则动作占比 |
| --- | ---: | ---: | ---: |
| P3-A 10d（假端点） | 1/91 = 1.10% | 0.00% | 模型替换规则 |
| **P3-B 10d（假端点）** | **45/91 = 49.45%** | **1.08%** | 6.51% / 93.49% |
| P2.2 10d（Mock） | 45/91 = 49.45% | 1.55% | 规则 |

### 4.3 与 P3-A 的 72d 并列

| run | K1 | 拒绝率 | 说明 |
| --- | ---: | ---: | --- |
| P3-A 72d（假端点） | **3/91 = 3.30%** | 0.00% | 模型整包替换 Mock 计划，生存动作消失 |
| **P3-B 72d（假端点）** | **71/91 = 78.02%** | **1.46%** | 规则底稿回归，模型只增量社交 |
| P2.2 72d（Mock） | 71/91 = 78.02% | 1.49% | 纯规则 |

**差异归因**：两版 K1 差异来自**分工改动**（生存动作交还规则调度器），不是模型能力变化。P3-B 与 Mock 同值，进一步证明模型层不是 K1 的来源。

**口径必须如实标注**：P3-A 的 content_hash 是 `c9d6bb66…`，P3-B 因 `verbs.yaml` 补齐 `recruit` / `quest_*` / `debt_settle` 参数变为 `83521723…`；K1 分母仍是同一组 91 条内容目标，但 `summary.k1.comparison_basis.cross_version_comparable=false`。本表是“同目标集、不同内容快照”的并列，不能当作严格 A/B 提升。

### 4.4 运行期拒绝分布（72d）

206 条拒绝全部来自 `planner` / `system`，**没有一条来自模型动作**：

| 原因 | 次数 |
| --- | ---: |
| `not_adjacent` | 66 |
| `not_together` | 63 |
| `not_at_workplace` | 14 |
| `quest_not_accepted_by_actor` | 13 |
| `not_at_giver` | 12 |
| `item_not_available` | 9 |
| `wrong_location` | 7 |
| `effect_invalid（余额不足）` | 6 |
| `not_at_target` | 6 |
| `quest_expired` | 5 |
| `offer_not_held` | 4 |
| `self_trade` | 1 |

## 5. 三槽位配置与成本分账

### 5.1 配置

[llm.yaml](file:///Users/lyinet/projects/XKX/world-sim/config/llm.yaml)：

| 槽 | type | model | max_tokens | temperature | timeout_s | 绑定 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| decision | openai_chat | `group/all`（`LLM_DECISION_MODEL` 可覆盖） | 1024 | 0.2 | 30 | 夜间批处理计划、目标选择、replan |
| dialogue | openai_chat | `group/all`（`LLM_DIALOGUE_MODEL` 可覆盖） | 4096 | 0.8 | 60 | 玩家在场对话、聚光档发言、角色扮演 |
| intent | xing_intent | `Xing4.0-29B-Intent`（`XING_INTENT_MODEL` 可覆盖） | 256 | 0.0 | 10 | 意图分类 + 参数抽取 |

- 用户侧只需改 `provider.base_url` 与两个模型名；接入真实 Xing 时再改 `models.intent.base_url`，key 放 `XING_API_KEY`。
- 允许两个槽位填同一个模型名。若网关只能路由到一个模型，报告口径写 **“单模型双槽（参数区分，成本按 slot 记）”**，当前假端点使用的是 `fake-decision` / `fake-dialogue` / `fake-intent` 三个不同名字，便于验证分账。
- `summary.cost.by_slot` 与 `summary.llm.by_slot` 都按 slot 输出；`cost.jsonl` 每条记录带 `slot`、`model`、`usage_source`。

### 5.2 72d 成本分账（假端点）

| 槽 | 调用数 | input tokens | output tokens | 单价（每 1k） | 小计 |
| --- | ---: | ---: | ---: | --- | --- |
| decision | **2,064** | 1,516,933 | 29,859 | $0.0015 / $0.0060 | **$2.1572** |
| dialogue | **0** | 0 | 0 | $0.0015 / $0.0060 | **$0.0000** |
| intent | **2,064** | **null** | **null** | 按次记账 | **未计算** |

- `intent` 的 `usage_source=missing`：本地桩模拟“上游不返回 usage”，按要求 token 记 `null`、不估成本。若真实 Xing 返回 usage，会自动按实测 token 记账。
- **不提供混合总价**。三槽各自小计独立，互不相加为一个“世界总价”。
- 10d：decision 267 次、$0.3494；intent 267 次、token null；dialogue 0 次。

### 5.3 P3-A 72d 6,812 次调用按槽重算

P3-A 只有单一模型槽，没有 dialogue 槽。按新口径重算：

| 版本 | 槽 | 调用数 | input tokens | output tokens | 小计 |
| --- | --- | ---: | ---: | ---: | --- |
| P3-A 72d | decision | **6,812** | 2,822,304 | 314,111 | **$12.0496** |
| P3-A 72d | dialogue | **0** | 0 | 0 | **$0.0000** |
| P3-A 72d | intent | 不适用（该版本无意图槽） | — | — | — |

**旧数字作废**：此前 `$12.05` 是把“决策 + 对话”用途混在一个模型、一个单价下的单一总价；P3-B 起改为分槽口径。P3-A 的 6,812 次全部归 `decision` 槽，`dialogue` 为 0 次，因此旧总价被拆成 decision `$12.0496` + dialogue `$0.0000`，不再出现混合总价。

### 5.4 1 亿免费额度折算

`intent` 槽的 token 在验收 run 中为 null，无法直接按账单折算；下面用同一 prompt/输出的字符估算（4 字符 ≈ 1 token）做**容量规划**，不是计费数字：

| 项 | 值 |
| --- | ---: |
| intent input（估算） | 1,243,499 tokens / 72d |
| intent output（估算） | 38,915 tokens / 72d |
| 合计（估算） | 1,282,414 tokens / 72d |
| 1 亿免费 tokens 可跑 | **≈ 78 次 72 天**（100,000,000 / 1,282,414） |

真实 Xing 的 prompt 长度、是否返回 usage、免费额度计量口径都可能不同；接入后以平台账单为准。

## 6. 意图理解层

### 6.1 功能表（`config/intents.yaml`）

17 个动词逐个写 `category`（中文）/ `verb` / `required` / `optional` / `source` / `destructive`：

| 指标 | 结果 |
| --- | ---: |
| 模板数 | **17 / 17 = 100%**（work / consume / cultivate / trade / buy / sell / move / apprentice / marry / join_sect / found_sect / open_shop / escort / quest_accept / quest_deliver / talk / debt_settle） |
| 必填字段 | **24 / 24 有参数来源** |
| 可选字段 | **14 / 14 有参数来源** |
| 参数来源完整度 | **38 / 38 = 100%** |
| 破坏性意图 | 5 个：`sell` / `marry` / `found_sect` / `open_shop` / `debt_settle`，默认 `needs_confirmation`，不自动落地 |

`required` 与 [verbs.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/verbs.yaml) 由 `tests/test_p3b_intent_model.py` 双向断言；故意改 verbs.yaml 会让测试失败（`test_catalog_detects_verbs_mismatch`）。引擎共 19 个动词，额外 `recruit`（规划器专用）与 `borrow_money` 不在用户指定的 17 个意图模板内，报告如实标注。

### 6.2 请求、解析与映射

[intent_model.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/agent/intent_model.py)：

- Prompt 三段：①用户/上层说的话（含前几轮）②系统支持哪些意图 ③每个意图要填哪些字段。
- 解析 `{"category": ..., "args": [{...}]}`（中文键值）→ 映射层转成 `{"verb": ..., "args": {...}}`。
- 中文参数值映射到内容包 id：地点、NPC、物品、数量词（“两瓶”→“qty=2”）；映射失败视为字段缺失。
- **缺字段不猜**：返回 `needs_clarification` 并列出缺失字段；上层决定追问或回退规则调度。
- 未知 category / 不在白名单的 verb → `intent_reject`，不落地。
- 破坏性动作返回 `needs_confirmation`，不自动执行。

72d 实测：

| 指标 | 值 |
| --- | ---: |
| intent 调用 | 2,064 |
| 成功落地 | 1,869（90.55%） |
| `needs_clarification` | 195（9.45%），缺失字段全部为 `destination` |
| `intent_reject` | 0 |
| 决策槽 `repair_attempted` | 0 |

### 6.3 本地桩与测试

[tools/fake_intent_server.py](file:///Users/lyinet/projects/XKX/world-sim/tools/fake_intent_server.py) 模拟 Xing 返回结构，覆盖四类用例：参数缺失、中文数量词、否定句、改口。

| 要求 | 测试 |
| --- | --- |
| 参数齐全 → 落地 | `test_full_arguments_land` |
| 缺字段 → `needs_clarification` 不落地 | `test_missing_field_does_not_guess` |
| 否定句 → 不执行 | `test_negation_does_not_execute` |
| 改口（多轮）→ 只改指定字段 | `test_retraction_multiturn_changes_only_named_field` |
| 未知 category → 拒绝 | `test_unknown_category_rejected` |
| 中文数量词 → 映射正确 | `test_chinese_quantity_maps` |
| 破坏性 → 二次确认 | `test_destructive_requires_confirmation` |
| 功能表与 verbs.yaml 不一致 → 测试失败 | `test_catalog_detects_verbs_mismatch` |
| 桩的四类用例 | `test_fake_server_cases` |

## 7. 预算护栏分槽

[budget.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/agent/budget.py)：

| 槽 | 上限 | 超限行为 |
| --- | --- | --- |
| decision | 日 200,000 tokens / 月 5,000,000 tokens / 日 $5 / 月 $50 | 只降级决策槽：回退规则调度，LOD 照常推进 |
| dialogue | 日 50 次调用 | 只降级对话槽：返回固定兜底“今日对话额度用尽，请明天再来。” |
| intent | 日 2,000 次调用 | 只降级意图槽：该槽回退规则调度 |

任一槽超限会在日终写一条 `system_budget` 审计事件；**不会让整个世界停摆**。72d 验收 run：decision/intent 降级次数均为 0。

## 8. 真实端点 / 真实 Xing：本轮未调用

本轮环境没有 `LLM_API_KEY`，也没有 `XING_API_KEY`，因此：

- 真实端点抽样 **0 次**（要求 ≤10 次，未执行）；
- 真实 Xing API **0 次**（等用户 key）；
- 72d 真实跑 **未执行**（等预算批准）。

### 8.1 接入前需要用户提供三样信息

1. **Xing 的 `base_url`**：天翼 AI 开放平台的实际 endpoint（配置默认写 `https://api.ctyun.cn/v1`，需确认）。
2. **API key**：只放环境变量 `XING_API_KEY`，不要写入仓库。
3. **平台上的实际模型名**：例如 `Xing4.0-29B-Intent` 在天翼平台的准确 model id。

### 8.2 一行 curl 自测

```bash
curl -s "${XING_BASE_URL:-https://api.ctyun.cn/v1}/chat/completions" \
  -H "Authorization: Bearer $XING_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"Xing4.0-29B-Intent","messages":[{"role":"user","content":"去南桥找林掌柜"}],"temperature":0}' | head -c 800
```

拿到 key 后建议顺序：curl 自测 → ≤10 次格式通过率抽样 → 2 天小跑 → 72 天（预算批准后）。

## 9. 验证与复现

### 9.1 本轮验证结果

| 项 | 结果 |
| --- | --- |
| pytest | **124 passed**（全绿） |
| framework_check | **6/6 PASS** |
| 假端点 10d replay | MATCH |
| 假端点 72d replay | MATCH |
| 假端点 72d K1 | 71/91 = 78.02% |
| 假端点 72d 拒绝率 | 1.46% |
| 守恒 | 0 |
| 72d state / event hash | `96a869a4…` / `9c11fb3f…` |

framework 第 6 项当次输出：

```text
127.0.0.1:61009 启动→2 天→关闭；source=llm 落地 65/65 条，
night_batch=29 realtime=65 replan=0（入口触发 44/90/1）；
calls=94 schema_reject=0 fallback=0 拒绝率=0.15% 守恒=0 replay=MATCH
```

### 9.2 复现命令

```bash
# 三个假端点
python3 tools/fake_intent_server.py --port 3426 &
python3 tools/fake_llm_server.py --port 3427 --seed 42 --mode decision --delay 0.05 &

export LLM_BASE_URL=http://127.0.0.1:3427/v1
export LLM_API_KEY=fake
export LLM_MODEL=fake-decision
export LLM_DECISION_MODEL=fake-decision
export LLM_DIALOGUE_MODEL=fake-dialogue
export LLM_INTENT_BASE_URL=http://127.0.0.1:3426/v1
export XING_API_KEY=fake
export XING_INTENT_MODEL=fake-intent

python3 sim/run_headless.py --days 10 --seed 42 --npc 0 --enable-llm \
  --dump-state --run-id p3b-fake-10d
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --enable-llm \
  --dump-state --run-id p3b-fake-72d

# 12 NPC 一个游戏日墙钟
python3 sim/run_headless.py --days 1 --seed 42 --npc 12 --enable-llm \
  --dump-state --run-id p3b-wall-1d-12npc

python3 tools/replay.py --run runs/p3b-fake-10d   # MATCH
python3 tools/replay.py --run runs/p3b-fake-72d   # MATCH
```

## 10. 冻结与干净重跑

- tag：`delivery-p3b`
- tag 指向 commit：`222a2b96d6ffa963c84c4a5b1cc2a4d9e11b9cf1`
- content_hash：`835217231c0e595221ac1d91bf7892d77aa812850d6f875b37cf2a2d92e73986`
- 干净 worktree：`/tmp/xkx-p3b-clean`（`git worktree add --detach … delivery-p3b`）
- pytest：**124 passed**
- framework：**6/6 PASS**
- 10d 假端点哈希：`event 0874e50a…` / `state ef0adb91…`；K1 45/91，拒绝 34/3,153 = 1.08%，replay **MATCH**
- 72d 假端点哈希：`event 9c11fb3f…` / `state 96a869a4…`；K1 71/91，拒绝 206/14,111 = 1.46%，replay **MATCH**
- 与主工作区预冻结 run 哈希逐位一致（说明 tag 代码可复现，不依赖未提交文件）
- 12 NPC 1 日墙钟：**0.364s**，decision 12 / intent 12 次，p50 0.061s / p95 0.133s

## 11. 仍未验证 / 未达标

1. **真实 Xing API 未调用**：无 key；意图模型在真实上游的格式通过率、延迟、usage 返回情况未知。
2. **真实端点抽样未执行**：本轮 0 次（≤10 次的要求未使用）；无 `LLM_API_KEY`。
3. **真实 72d 未跑**：等用户批准预算。
4. **10d K1 49.45%**：10 天不足以完成 91 条目标，与 Mock 同值；硬指标是 72d 的 78.02%，未调门槛。
5. **模型层没有提升 K1**：P3-B 与 Mock 同值，说明本轮只是修复分工；若未来要提升 K1，需要内容层/目标层改动，不能靠放宽 K1 门槛。
6. **intent 成本不可按 token 计**：本地桩不返回 usage；接入真实 Xing 后才能确认是否提供 usage。
