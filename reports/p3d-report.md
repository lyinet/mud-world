# P3-D 报告：接入真实 Xing 意图槽，跑 2 个游戏日

- 日期：2026-11-04
- 范围：只把 `intent` 槽接到真实 Xing；`decision` 仍是本地假端点（3427），
  `dialogue` 未参与本轮运行。**世界轨迹由假决策的自然语言驱动，意图理解是真实模型。**
- 端点：`http://127.0.0.1:3425/v1`，模型
  `openrouter/xingchen-agi/xing4.0-29b-a4b:free`
- 规模：seed 42 / 12 NPC / 2 游戏日（288 tick）
- **没有跑 72 天真实调用**（按要求 2 天足够）。

---

## 1. 适配层位置与测试

### 代码位置

| 能力 | 位置 |
|---|---|
| 剥围栏 / 中英文键归一 / unknown_key / grounding / 数量单位归一 | [intent_model.py](../src/wsim/agent/intent_model.py) `_strip_payload`、`TOP_ALIASES`、`FIELD_ALIASES`、`_normalize_payload`、`_ground_string`、`_ground_number`、`_ground_value`、`parse_quantity_unit`、`parse_intent_response` |
| 429 分类与 25s 退避、usage/latency/错误统计 | [provider.py](../src/wsim/agent/provider.py) `ProviderError.RETRYABLE`、`_http_transport`、`ProviderClient._run`、`stats_snapshot` |
| 意图调用统计 / grounding 案例 / 延迟分布 | [provider.py](../src/wsim/agent/provider.py) `LLMReasoner.summary` |
| 录制与回放 | [provider.py](../src/wsim/agent/provider.py) `prompt_sha`、`make_replay_transport`；[headless.py](../src/wsim/sim/headless.py) `WSIM_INTENT_RECORD` / `WSIM_INTENT_REPLAY` |
| 限额配置 | [llm.yaml](../config/llm.yaml) `models.intent`：1500 tokens / 60s / 2 retries，base_url 3425 |

### 测试（`tests/test_p3d_xing_adapter.py`，16 条，全部离线）

| 测试名 | 覆盖 |
|---|---|
| `test_strip_fence_and_prose` | 裸 JSON / ```json / ``` / 围栏前后杂文本 |
| `test_category_needs_clarification_variant` | 模型把 `needs_clarification` 当 category 返回 |
| `test_invalid_json_rejected` | 非 JSON 拒绝，不落地 |
| `test_zh_and_en_keys_normalize_to_same_structure` | 中文键与英文键归一为同一 args |
| `test_unknown_keys_dropped_and_recorded` | 白名单外键丢弃并记 `unknown_keys` |
| `test_grounding_blocks_fabricated_qty_and_seller` | **“去买点米” → 数量=1、卖家="买家" 两者被拦，missing=[from]，needs_clarification** |
| `test_grounding_accepts_values_backed_by_utterance` | “去集市买两斗米” 落地，qty=2/from=market |
| `test_grounding_blocks_number_not_in_utterance` | 原文“两斗”，模型数量=1 → 拦截；上下文里的 1 不能背书 |
| `test_grounding_can_use_context_entity_names` | 字符串值可回指本轮上下文的实体名 |
| `test_grounding_disabled_without_utterance_keeps_legacy_callers` | 旧调用不传 utterance 时 grounding 关闭 |
| `test_generative_reply_exempt_from_grounding` | `reply` 是生成型字段，不参与原文 grounding |
| `test_429_retries_twice_with_25s_backoff` | 429 固定 25s，最多 2 次，共 3 次尝试 |
| `test_429_then_success_records_latency_and_usage` | 429 后成功，usage/latency 记账 |
| `test_http_4xx_not_retried` | 4xx 不重试 |
| `test_intent_slot_limits_from_config` | 3425 / 正式模型 id / 1500 / 60 / 2 |
| `test_recorded_intent_output_replays_deterministically` | 录制文件按 prompt 指纹回放 |

全量：**161 passed**（P3-C 为 145，本轮新增 16）。`framework_check`：**6/6 PASS**。

### 适配规则摘要

1. **剥围栏**：围栏内容 → 原文 → 首个 `{` 到末个 `}` 三级回退。
2. **键名归一**：`意图/intent/category/type/动作` → `category`；
   `参数/args/params/slots` → `args`；字段名中英文都映射到模板字段；
   白名单外键丢弃并记 `unknown_keys`。
3. **grounding**：字符串值必须出现在「用户原话 + 本轮上下文实体名（地点/相邻地点/附近人物）」；
   数字必须回指**用户原话**里的数字词，避免上下文里的 tick/库存整数给编造数量背书；
   数字量词归一（“两斗”→ qty=2、unit=斗、source=两斗）。
   找不到依据：必填字段记为 missing → `needs_clarification`；可选字段只丢弃并记 `blocked`。
   `reply` 是模型替 NPC 生成的台词，不参与抽取型 grounding。
4. **兼容**：不传 `utterance` 的旧调用 grounding 关闭，P3-B 语义不变。

---

## 2. 真实 2 天数字表

运行目录：`runs/p3d-real-2d-12npc`；记录：`runs/p3d/intent-recordings-2d-final.jsonl`；
对照桩：`runs/p3d-stub-2d-12npc`（相同 seed / NPC / 2 天 / 同一适配层，只把意图槽换回假端点）。

| 指标 | 真实 Xing（intent 槽） | 桩（intent 槽） | 说明 |
|---|---:|---:|---|
| 意图调用（逻辑次） | **19** | 19 | 12 次第 1 天 + 7 次第 2 天 |
| HTTP 尝试次数 | **23** | 19 | 含 429/5xx 重试 |
| 成功（ok） | **18** | 19 | 真实模型 1 次重试耗尽后回退 |
| 成功率（逻辑调用） | **94.7%（18/19）** | 100% | — |
| schema 失败 | **0** | 0 | invalid_json / unknown_category 均为 0 |
| grounding 拦截（调用级） | **0** | 0 | 本轨迹无编造参数 |
| 429 次数 | **4** | 0 | 另 1 次 http_5xx |
| needs_clarification | **0/19 = 0%** | 0/19 | 真实模型本轮未主动追问 |
| 拒绝（transport） | **1/19 = 5.3%** | 0 | 429 两次退避后仍失败 |
| K1 全量 | **7/91 = 7.69%** | 7/91 = 7.69% | 2 天窗口，二者相同 |
| K1 仅新建目标 | **7/15 = 46.67%** | 7/15 = 46.67% | — |
| K1 剔除一次性 | **3.45%** | 3.45% | — |
| 事件数 / 拒绝率 | **208 / 0.48%（1）** | 209 / 0.48%（1） | 差 1 条 talk |
| 守恒误差 | **0** | 0 | minted 26,200 / burned 54 / accounts 26,146 |
| replay（事件回放） | **MATCH** | MATCH | 状态与事件哈希一致 |
| state_final | `c788434f…5fb471` | `1d4f3a12…9c5e4e` | 因 1 条事件差异 |
| event_final | `4f06496e…575bee8` | `d7df84cf…391acfb` | — |
| 墙钟 | **284s**（含 429 退避） | 0.34s | 真实调用不可与桩比耗时 |
| 意图 usage | prompt 23,066 / completion 363，1 次缺 usage | 11,148 / 345 | 真实 prompt 更长 |
| 估算成本（intent 槽） | USD 0.0288 | USD 0.0142 | 按仓库内部口径 |

延迟（秒）：

| 口径 | min | p50 | p90 | mean | max |
|---|---:|---:|---:|---:|---:|
| 逻辑调用（含重试等待） | 0.0 | **17.12** | **43.15** | 19.10 | 45.26 |
| 单次 HTTP 尝试 | 2.02 | **19.81** | **45.05** | 25.18 | 47.23 |

> 单次尝试 p50 约 19.8s、p90 45s，明显高于探测时的 5.2–21.6s；
> 4 次 429 和 1 次 5xx 进一步拉高逻辑调用延迟。

### 与 P3-C（72 天、全桩）并列

| 指标 | P3-C（72d，桩） | P3-D（2d，真实 intent） |
|---|---:|---:|
| 意图调用 | 2,201 | 19 |
| clarification | 0/2,201 | 0/19 |
| 拒绝率 | 1.35%（191/14,098） | 0.48%（1/208） |
| K1 | 71/91 = 78.02% | 7/91 = 7.69% |
| 守恒误差 | 0 | 0 |
| replay | MATCH | MATCH |

**不可直接比较**：P3-C 是 72 天、意图槽为桩；P3-D 是 2 天、意图槽为真实模型。
K1 随观测窗口增长，2 天窗口天然低；两行只作为回归锚点，不能据此判断模型优劣。
P3-D 的 2 天桩对照（上表）才是同窗口、同适配层的对照。

---

## 3. grounding 拦截的实际案例

### 案例 A：2 天真实运行中的一次误分类 + 编造（录制于第 1 次真实跑）

- 原文：`找顾青崖闲聊几句`
- 模型输出：
  `{"category":"open_shop","args":{"at":"城隍庙","shop_type":"storefront","name":"杏林堂"}}`
- 适配层结果：`at`、`shop_type`、`name` 三个值都不在原话/上下文中，全部进 `blocked`；
  `at`、`shop_type` 为必填 → `missing=[at, shop_type]`，
  `status=needs_clarification`，`reason=grounding_missing_required`。
- 说明：这是**语义误分类**（把“闲聊”判成“开店”），grounding 至少拦住了随后 3 个编造参数，
  没有让“城隍庙/杏林堂”落地。

### 案例 B：真实在线探针（`runs/p3d/live-grounding-cases.json`）

- 原文：`去集市买点米`
- 模型输出：`{"category":"move","args":{"destination":"needs_clarification"}}`
- 适配层结果：`destination="needs_clarification"` 不是原文片段，
  进 `blocked`，`missing=[destination]`，`needs_clarification`。
- 同批探针还有 `{"category":"needs_clarification","args":{"destination":[]}}`，
  已按“category 即追问”处理，不再误判成 `unknown_category`。

### 案例 C：缺字段自行编造（用户实测复现，已固化为单测）

- 原文：`去买点米`
- 模型输出：`{"数量":1,"卖家":"买家"}`（配合 `物品=米`）
- 适配层结果：原话中没有数字词 → `qty=1` 被拦；原话/上下文中没有“买家” → `from` 被拦；
  `from` 为必填 → `needs_clarification`，`blocked` 同时记录两个字段。
- 测试：`test_grounding_blocks_fabricated_qty_and_seller`。

---

## 4. 冻结、回放与可复现性

- 真实调用本身**不可保证逐字复现**：同一 prompt 在不同时刻会得到不同输出
  （本轮三次探针分别返回 `needs_clarification` / `move` / `null`）。
- 因此提供录制回放：
  - `WSIM_INTENT_RECORD=<jsonl>`：按 `prompt_sha = SHA256(system + user)` 记录每次返回、
    usage、延迟与解析状态；
  - `WSIM_INTENT_REPLAY=<jsonl>`：intent 槽改用回放 transport，不联网；
    缺记录报 `replay_miss`，不伪造返回。
- 本次验证：用 `runs/p3d/intent-recordings-2d-final.jsonl` 回放同一 2 天配置，
  state/event 哈希与真实跑**逐位一致**：
  - `state_final = c788434f32faa6f6d8afd7430d1878d606d16d44f25b76cce3c813e68e5fb471`
  - `event_final = 4f06496edb064919c8cfc558915d0b2cd957b7bcf07da31d91c7f7c52575bee8`
  - 唯一失败调用在回放中表现为 `transport:replay_miss`，世界结果相同。
- `tools/replay.py` 对事件日志回放：**MATCH**。

---

## 5. 这个模型到底能不能承担意图层？

**结论：在“封闭 17 模板 + 严格适配层 + grounding + 规则兜底”约束下，可以承担；
不能把它当作唯一裁决者，也不建议直接扩到开放式意图。**

支持它的证据：

1. 19 次逻辑调用中 18 次成功（94.7%），schema 失败 0；
   2 天真实跑与桩跑的事件数、K1、拒绝率几乎一致（208 vs 209，K1 均 7/91）。
2. 键名漂移（中/英文、扁平/嵌套）全部被适配层吸收；
   典型输出 `{"category":"talk","args":{"target":"谢停云","message":"找谢停云闲聊几句"}}`
   可直接落地。
3. 1500 tokens 足够；本轮 18 次有 usage 的返回 completion 合计仅 363 tokens，
   没有出现 reasoning 吃空 content。

不能无条件信任的证据：

1. **429 很重**：两轮真实跑分别为 11/19 和 4/19 次调用触发 429；
   第二轮还有 1 次 5xx；1 次调用在 2 次 25s 退避后仍失败并回退规则。
   它是“能用但不稳定”的依赖，必须保留降级路径。
2. **语义误分类**：同一句“找顾青崖闲聊几句”，一次判成 `open_shop`，
   另一次判成 `move`（目的地还是上下文里的“集市”）；后者 grounding 拦不住，
   因为字符串确实出现在上下文实体里，只是意图错了。
3. **会编造参数**：误分类时顺手编出 `城隍庙 / storefront / 杏林堂`；
   探针里还把 `needs_clarification` 写进 `destination`。
   grounding 能拦住“原文找不到”的值，但拦不住“原文/上下文里存在却被安到错误字段”的值。
4. **非确定性**：同 prompt 多次调用返回不同结构；不录制就无法复盘。

建议：

- 保持 intent 槽为**可降级**依赖：429/5xx/解析失败一律回退规则调度，世界不停。
- 对所有实体/数量字段继续强制 grounding；`reply` 这类生成型字段单独豁免。
- 每次真实跑都开 `WSIM_INTENT_RECORD`；验收用录制回放，而不是重调模型。
- 若要把意图层扩到开放域，先补“意图候选集 + 置信度 + 二次确认”，
  并把 grounding 从“字符串存在”升级为“字段-实体类型匹配”。

---

## 6. 复现命令

```bash
# 真实 2 天（决策槽用假端点 3427，意图槽用真实 Xing 3425）
LLM_API_KEY=fake LLM_DECISION_BASE_URL=http://127.0.0.1:3427/v1 \
XING_API_KEY=magpie WSIM_INTENT_RECORD=runs/p3d/intent-recordings-2d.jsonl \
.venv/bin/python sim/run_headless.py --days 2 --seed 42 --npc 12 --enable-llm \
  --out runs --run-id p3d-real-2d-12npc

# 确定性回放（不联网）
LLM_API_KEY=fake LLM_DECISION_BASE_URL=http://127.0.0.1:3427/v1 \
XING_API_KEY=fake WSIM_INTENT_REPLAY=runs/p3d/intent-recordings-2d.jsonl \
.venv/bin/python sim/run_headless.py --days 2 --seed 42 --npc 12 --enable-llm \
  --out runs --run-id p3d-replay-2d-12npc

# 事件日志回放
.venv/bin/python tools/replay.py --run runs/p3d-real-2d-12npc   # MATCH
```
