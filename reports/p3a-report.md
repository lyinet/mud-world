# Delivery P3-A · 真实模型接线与预算护栏

- 日期：2026-11-03
- 范围：Provider 真实 HTTP、本地假端点、三种决策时机接入主循环、预算护栏、端到端对比、真实网关实测
- 内容哈希：`c9d6bb66f2858616c3b5a926dbc1bfad32f245ca71be5b18fda846ad9aaedc98`（与 `delivery-p2.2` 相同，内容包未改）
- tag：`delivery-p3a`（附注 tag，附 commit 与 content_hash）
- 冻结后干净重跑结果见第 10 节

## 0. 结论

1. **真实通路已打通**：用户只需改 `provider.base_url` 与 `provider.model`，key 放环境变量；代码零改动。真实本地网关 2 个游戏日跑通，Intent 进入世界，事件数增长，守恒 0，replay MATCH。
2. **Provider 错误分类与重试可测**：`timeout / network / http_4xx / http_5xx / parse` 五类各有测试；前 3 类指数退避重试，后 2 类不重试。
3. **schema 不放宽**：网关返回的字段差异用“只提取、不改语义”的适配器处理；未知动词、缺失参数仍被拒绝并回退 Mock，半截输出不入世界。
4. **三种决策时机落地**：`night_batch` / `realtime` / `replan` 均有独立计数；LOD 唤醒计数与模型调用数可对账。
5. **预算护栏实测**：上限设极小后真实降档并降级 Mock，世界不停摆（拒绝率 0%），每天一条 `system_budget` 审计事件。
6. **真实模型质量是新的风险点**：2 日真实小跑拒绝率 18%（假端点 0%）。世界未停摆，但 `k6_illegal_pass=false`。本报告不把它粉饰为通过。

## 1. Provider 真实实现

位置：[provider.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/agent/provider.py)

| 项 | 实现 |
| --- | --- |
| HTTP | 标准库 `urllib.request`，POST 到 OpenAI 兼容 `/v1/chat/completions`（`_chat_url` 兼容 base 末尾带 `/v1` 或完整路径） |
| 请求体 | `model` / `messages(system,user)` / `temperature` / `max_tokens`；`json_mode=true` 时附 `response_format={"type":"json_object"}` |
| 超时 | `urlopen(timeout=cfg.timeout_s)`，连接与读取共用真实 socket 超时 |
| 重试 | `max_retries` + `backoff_base_s` 指数退避（0.5s、1.0s…）；仅 `timeout / network / http_5xx` |
| 错误分类 | `ProviderError(kind)`：`timeout / network / http_4xx / http_5xx / parse` |
| usage | 解析网关 `usage.prompt_tokens/completion_tokens/total_tokens`；失败的响应也保留 usage，避免成本低估 |
| 空内容 | `content` 非字符串或为空 → `parse:empty_content`（典型为 reasoning 吃光 max_tokens） |
| 密钥 | 只从 `api_key_env`（默认 `LLM_API_KEY`）读；`fingerprint()` 只含 base_url/model/timeout/retries/max_tokens/temperature/json_mode |

错误分类测试（`tests/test_provider.py`，全部用注入假 transport，不联网）：

| 类别 | 测试 | 断言 |
| --- | --- | --- |
| timeout | `test_error_classification_and_retry_policy[timeout]` | 3 次尝试，sleeps `[0.5, 1.0]` |
| network | `... [network]` | 同上 |
| http_5xx | `... [http_5xx]` | 同上 |
| http_4xx | `... [http_4xx]` | 1 次尝试，不重试 |
| parse | `... [parse]` | 1 次尝试，不重试 |
| 真实异常映射 | `test_http_transport_maps_real_exceptions_without_network` | monkeypatch `urlopen`：429→4xx、503→5xx、URLError(timeout)→timeout、ConnectionRefused→network、坏 JSON→parse、`content=null`+usage→parse 且 usage 保留 |
| 请求体/超时 | `test_http_transport_body_and_timeout` | body 含 model/messages/temperature/max_tokens/response_format，timeout 传入 urlopen |

## 2. 本地假端点（CI，不联网）

文件：[fake_llm_server.py](file:///Users/lyinet/projects/XKX/world-sim/tools/fake_llm_server.py)

启动：

```bash
python3 tools/fake_llm_server.py --port 64310 --seed 42
# 输出 PORT=64310 与 FAKE_LLM_BASE_URL=http://127.0.0.1:64310/v1
```

可注入故障：`--delay`（延迟秒）、`--fail-rate`（HTTP 500）、`--bad-json-rate`（截断 JSON）、`--long-output`（8 个动作触发 `too_many_actions`）。输出按 seed 稳定，动作只用 `move` / `cultivate`，因此 CI 可重复。

`framework_check` 第 6 项 `check_fake_llm_endpoint`：进程内启动 127.0.0.1 → 2 天推理 → Intent 落地 → 关闭；断言 `source=llm` 且有 effects 的事件、`fingerprint.base_url` 是 127.0.0.1、`schema_reject=0`、`fallback=0`、三种时机落地、拒绝率 ≤2%、独立重放守恒 0 且 MATCH。

## 3. 接进主循环

位置：[headless.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/headless.py)

| 时机 | 触发点 | 说明 |
| --- | --- | --- |
| `night_batch` | 日界 `run_night_batch(base, day)` | 批量生成次日计划；`invocations` 按“发起批次数”计，`model_calls` 按实际模型调用计 |
| `realtime` | LOD 唤醒（`LODScheduler.advance_to`），玩家在场时升 spotlight | 每个唤醒先取 Mock 底稿，再请求模型 |
| `replan` | 事件被拒且 `payload.source in (planner, llm)` | 只走 Mock 重决策（不再请求模型，避免拒绝风暴放大成本） |

LOD 档位决定谁被调用：只有产生底稿（有目标/计划）的 NPC 才会发起模型请求；无底稿的唤醒直接返回空。`run_night_batch` 也按 NPC 当前档位决定是否纳入。

### 3.1 三种时机实测（假端点，seed 42）

| run | 时机 | invocations | model_calls | decisions | actions | avg_actions | submitted | accepted | applied | accept_rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fake-10d | night_batch | 152 | 98 | 98 | 212 | 2.163 | 212 | 212 | 212 | 100% |
| fake-10d | realtime | 657 | 483 | 483 | 1041 | 2.155 | 1041 | 1041 | 1041 | 100% |
| fake-10d | replan | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| fake-72d | night_batch | 476 | 203 | 203 | 415 | 2.044 | 415 | 415 | 415 | 100% |
| fake-72d | realtime | 6422 | 6170 | 6170 | 13414 | 2.174 | 13414 | 13414 | 13414 | 100% |
| fake-72d | replan | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| real-2d | night_batch | 24 | 18 | 18 | 53 | 2.944 | 53 | 35 | 35 | 66.04% |
| real-2d | realtime | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| real-2d | replan | 18 | 0 | 18 | 18 | 1.0 | 18 | 18 | 18 | 100% |

`accepted` 是写出 `outcome=ok` 的 Intent 数，`applied` 是带 effects 且成功落地的 Intent 数；本轮的通过/落地口径一致，因此两列相等。

### 3.2 LOD 档位 ↔ 调用次数对账

| run | LOD decide_calls | processed_wakes | state_provider_calls | active / background / silent / spotlight | 模型调用（night + realtime） | 差额解释 |
| --- | ---: | ---: | ---: | --- | ---: | --- |
| fake-10d | 657 | 657 | 657 | 47 / 38 / 3 / 569 | 98 + 483 = 581 | 76 个唤醒无底稿，跳过模型 |
| fake-72d | 6422 | 6422 | 6422 | 92 / 64 / 21 / 6245 | 203 + 6170 = 6373 | 49 个唤醒无底稿，跳过模型 |
| real-2d | 16 | 16 | 16 | 11 / 4 / 1 / 0 | 18 + 0 = 18 | 18 次全部来自夜间批处理；无玩家，spotlight 为 0 |

三个独立计数器（调度器唤醒、处理队列、状态视图请求）逐 run 完全相等，说明“LOD 决定谁被调用”没有旁路。spotlight 唤醒占 10d 的 86.6%，与 36 tick 的 spotlight 间隔和玩家在场升档一致；silent 仅 3/1 次，与 1440 tick 间隔一致。

## 4. 预算护栏

位置：[budget.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/agent/budget.py)

- 日/月 token 与金额四类上限均可配（`config/llm.yaml` 的 `budget`，或 CLI `--budget-*`）。
- `check(day, tier, prompt, max_tokens)`：估算 `input=len(prompt)//4`、`output=max_tokens`；取日/月 token 与成本比例的最大值。
  - `ratio >= 1.0` → 返回 `None`，降级 Mock，记 `mocked`。
  - `ratio >= 0.9` → 降 2 档；`ratio >= soft_ratio`（默认 0.8）→ 降 1 档；顺序 spotlight→active→background→silent。
- 日终 `take_audit(day)` 为真时写一条 `system_budget` 审计事件（每天最多一条），args 含当日/当月 token、成本、mocked 与降档次数。
- `BudgetGuard.summary()` 输出 `limits / day_tokens / day_cost / month_tokens / month_cost / degraded / mocked / *_by_day`。

### 4.1 实测（假端点，2 天，5 NPC，seed 42）

| 日 token 上限 | 模型调用 | 降档次数 | 降级 Mock | 事件 | 拒绝率 | 守恒 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 6,000 | 8 | 4（day0 3、day1 1） | 0 | 33 | 0.00% | 0 |
| 4,000 | 0 | 0 | 8（day0 5、day1 3） | 90 | 0.00% | 0 |
| 900 | 0 | 0 | 8 | 90 | 0.00% | 0 |

- 6,000 档：先降档（spotlight/active 被降级），剩余预算内继续调用模型。
- 4,000 及以下：首个检查即超过上限，直接全部转 Mock；每天仍有动作，世界不停摆。
- 两天各一条 `system_budget` 审计事件（`test_tiny_budget_degrades_to_mock_without_stalling`）。
- 说明：`4,000` 档“一上来就 Mock”是因为 `output=max_tokens(2048)` 的保守预留；这是有意保守，不是 bug。

## 5. 端到端数字（假端点 vs `delivery-p2.2` Mock 基线）

> **跨 reasoner 不可直接比较（`non_comparable`）**：P2.2 是规则式 Mock，P3-A 是“假端点 LLM + Mock 兜底”。两者的事件语义、动作来源和成本口径不同；下表并列只为说明形状，不能写成“提升 X%”。

| 指标 | P2.2 10d（Mock） | P3-A 10d（假端点） | P2.2 72d（Mock） | P3-A 72d（假端点） |
| --- | ---: | ---: | ---: | ---: |
| 模型/推理调用 | 199 | 581 | 751 | 6,373 |
| input tokens | 76,851 | 238,767 | 290,849 | 2,637,148 |
| output tokens | 73,829 | 27,277 | 272,104 | 293,254 |
| 估算成本（仓库费率） | $0.5306 | $0.9363 | $1.9814 | $11.2017 |
| schema_reject | 0 | 0 | 0 | 0 |
| fallback | 0 | 0 | 0 | 0 |
| 事件总数 | 2,841 | 1,310 | 12,616 | 13,193 |
| 拒绝数 / 拒绝率 | 44 / 1.55% | 0 / 0.00% | 188 / 1.49% | 0 / 0.00% |
| K1（全量 91） | 45 (49.45%) | 0 (0.00%) | 71 (78.02%) | 3 (3.30%) |
| K3 action_signatures | 86 | 34 | 100 | 34 |
| 守恒误差 | 0 | 0 | 0 | 0 |
| replay | MATCH | MATCH | MATCH | MATCH |

K1 大幅下降的原因不是“模型更差”，而是假端点只输出 `move` / `cultivate`，不会推进财富/门派/开店类目标；它验证的是接线与守恒，不是内容质量。真实模型 2 日小跑的 K1 也仍为 0/91（只有 2 天，且动作多为移动/修炼）。

### 5.1 每 NPC 动作多样性

- 假端点 10d：K3 signatures 34；每 NPC 通常 2 个不同动词（`move` + `cultivate`），归一化熵约 0.95–1.0。
- 假端点 72d：signatures 34；与 10d 相同，说明假端点动作空间恒定，适合回归不适合做质量结论。
- 真实 2d：signatures 23；每 NPC 1–4 个不同动词（如 `gu_qingya` 4 个，`wen_xiaoman` 1 个）。

### 5.2 守恒独立复算

不读 `summary.json`，直接从 `events.sqlite` 重放并重新统计（见第 10 节命令）：

| run | events | minted | burned | accounts | 账户合计 | 误差 | 负余额 | replay |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fake-10d | 1,310 | 47,150 | 0 | 41 | 47,150 | 0 | 0 | MATCH |
| fake-72d | 13,193 | 47,150 | 302 | 41 | 46,848 | 0 | 0 | MATCH |
| real-2d | 100 | 26,200 | 40 | 22 | 26,160 | 0 | 0 | MATCH |

## 6. 真实端点实测

网关：本地聚合网关，OpenAI 兼容 `/v1/chat/completions`；`base_url=http://127.0.0.1:3425/v1`，`model=group/all`。key 由用户线下提供，只进环境变量，未写入任何被 git 跟踪的文件。

### 6.1 通路探测（共 5 次请求）

| # | 请求 | HTTP | 延迟 | 返回结构 | 结论 |
| ---: | --- | ---: | ---: | --- | --- |
| 1 | `"hi"` 最小请求 | 200 | 未记录具体值 | OpenAI 标准 `choices[0].message` | 通路可用 |
| 2 | 严格 JSON + `response_format=json_object` | 200 | 2.927s | `{"actions":[{"verb":"move","args":{"destination":"teahouse"}}...]}` | 认 `response_format`；动作参数名是 `destination` |
| 3 | 真实 Prompt，`max_tokens=512` | 200 | 5.637s | `content=null`，`finish_reason=length`，reasoning 513 | reasoning 吃光预算，不可用 |
| 4 | 真实 Prompt，`max_tokens=2048` | 200 | 12.489s | `{"actions":[work at academy, cultivate, recruit target, found_sect name]}` | 可解析；`work` 用 `location`；`found_sect` 缺 `at` |
| 5 | 严格 JSON + `response_format`，`max_tokens=1024` | 200 | 6.501s | 见下方原始样例 | content 合法 JSON；模型用了不在词表的 `go` |

成功率：HTTP 200 = 5/5；能产出非空 `content` = 4/5（#3 为空）；能通过我们的 Intent schema = 2/4（#5 是 `go`，不在 `NPC_VERBS`）。---

### 6.2 原始返回样例（脱敏、截断）

请求 4 的 `content`（真实 Prompt 下）：

```json
{"actions":[{"verb":"work","args":{"at":"academy"},"reason":"..."},{"verb":"cultivate","args":{},"reason":"..."},{"verb":"recruit","args":{"target":"..."},"reason":"..."},{"verb":"found_sect","args":{"name":"..."},"reason":"..."}]}
```

请求 5 的完整返回（`reasoning_content` 截断，key 不在响应中）：

```json
{"choices":[{"finish_reason":"stop","index":0,
 "message":{"content":"{\"actions\":[{\"verb\":\"go\",\"args\":{\"exit\":\"academy\"}},{\"verb\":\"cultivate\",\"args\":{}}]}",
 "reasoning_content":"We need answer only JSON. ... (截断)"}}],
 "usage":{"completion_tokens":756,"completion_tokens_details":{"reasoning_tokens":728},
 "prompt_tokens":92,"total_tokens":848}}
```

### 6.3 与 schema 的差异与处理（不放宽 schema）

| 差异 | 是否放宽 | 处理 |
| --- | --- | --- |
| `move.args.destination` / `work.args.location` | 否 | 适配器只做参数名归一：`destination/target_location/to_location→to`，`location/workplace/place→at` |
| markdown ```json 围栏 | 否 | 提取器剥围栏 |
| 外层 `{"data"/"result"/"output": {...}}` | 否 | 提取器只解一层包装 |
| `message.reasoning_content` 额外字段 | 否 | 只取 `content`，其余忽略 |
| 动词 `go`（不在 17 词表） | **否** | `validate_intent_json` 拒绝 `unknown_verb` → `schema_reject` → 回退 Mock |
| `found_sect` 缺 `at` | **否** | 拒绝 `missing_arg:at`，不补造参数 |
| `max_tokens=512` 返回 `content=null` | 否 | 归类 `parse:empty_content`，回退 Mock；配置改 4096 |
| `response_format` | — | 本网关支持；不支持时把 `json_mode` 改 false |

适配器位置：[prompt.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/agent/prompt.py) 的 `extract_intent_payload`。测试 `test_extract_adapter_keeps_raw_and_is_replayable` 证明原始输出保留在 `cost.jsonl.output_text`、同一原始串重复解析结果一致；`test_raw_model_output_is_preserved_in_telemetry` 断言原始输出可回放；`test_partial_or_invalid_output_never_enters_world` 断言半截输出只产生 `fallback/schema_reject`，不产生残缺世界状态。

### 6.4 失败时的降级路径

```
HTTP 超时 / 网络 / 5xx → 指数退避重试 max_retries 次
   └─ 仍失败 → ProviderUnavailable → 回退 Mock（记 fallback，cost.jsonl 保留已产生的 usage）
HTTP 4xx → 不重试 → 回退 Mock
content 为空 / 非 JSON / JSON 不合 schema → ProviderError(parse) 或 SchemaError
   └─ 回退 Mock（parse 记 fallback，schema 记 schema_reject；原始输出写入 output_text）
预算比例 ≥ 1.0 → 不请求模型 → 回退 Mock + 日终 system_budget 审计事件
```

任何路径都不会把半截动作写入 `events.sqlite`。

### 6.5 真实端点 2 日小跑

命令（`--npc 12`，不开 players，`max_tokens=4096`；72 天留待预算确认）：

```bash
export LLM_API_KEY=<网关 key>          # 不写入仓库
python3 sim/run_headless.py --days 2 --seed 42 --npc 12 \
  --enable-llm --llm-max-tokens 4096 --run-id real-2d
```

| 指标 | 值 |
| --- | --- |
| 模型调用 / 成功 | 18 / 18（成功率 100%） |
| schema_reject / fallback | 0 / 0 |
| input / output tokens | 12,455 / 27,705 |
| 估算成本（仓库费率） | $0.1511 |
| 延迟 min / p50 / p95 / max / mean | 3.334s / 10.964s / 22.453s / 34.228s / 12.326s |
| 事件数 | 100（day1 63、day2 40） |
| 拒绝数 / 拒绝率 | 18 / 18% |
| 拒绝原因 | `not_at_workplace` 5、`missing_arg:at` 4、`missing_arg:target` 2、`unknown_item` 2、`missing_arg:item/shop_type/to` 各 1、`not_adjacent` 1、`not_together` 1 |
| Intent 落地（night_batch） | 53 submitted → 35 accepted → 35 applied（66.04%） |
| replan | 18 次全部 Mock，18/18 落地 |
| 守恒 | 误差 0；minted 26,200、burned 40、账户合计 26,160 |
| replay | MATCH（state `0970096b…`、event `080a3c7c…`） |
| LOD 终态 | active 6 / background 4 / silent 2 / spotlight 0 |
| K1 | 0/91 |
| K3 signatures | 23 |

**必须如实标注**：真实端点 2 日拒绝率 18%，超过 2% 门槛，`summary.verdict.k6_illegal_pass=false`。原因是真实模型会生成“语义像但参数缺失/地点不对”的动作（`not_at_workplace`、`missing_arg:*`），不是世界停摆。世界继续运行、守恒为 0、replay MATCH。该指标只覆盖 2 天，样本 100 条，不能外推为稳定质量。

另：`--npc 12` 是 NPC 子集，启动日志会出现 `relation_target_missing:*` 与 `money_supply_npc_total:35650!=14700`，属于子集 genesis 的正常现象，不是 P3-A 回归。

### 6.6 72 天真实端点预估（未跑）

按真实 2 日实测的单次均值（约 692 input / 1,539 output tokens）与仓库费率/档位倍数折算，**不是网关真实账单**：

| 配置 | 预估调用 | 估算成本 | 串行耗时（按 mean 12.3s） |
| --- | ---: | ---: | ---: |
| 31 NPC + 玩家（同假端点 72d 负载） | ≈6,400 | ≈$125–135 | ≈21–22 小时 |
| 31 NPC 关玩家（以夜间批处理为主） | ≈200–300 | ≈$4–7 | ≈40–60 分钟 |

**72 天真实跑需用户确认预算后再执行**；本报告不把折算值当成已发生费用。

## 7. 密钥不落盘

- `ProviderConfig.from_env` 只从 `api_key_env` 指定的环境变量读 key；`fingerprint()` 不含 key。
- `run_headless` 把 fingerprint、model、calls/tokens 写入 summary，但不写 key。
- `cost.jsonl` 的 `output_text` 是模型输出，不含请求头。
- 测试 `test_api_key_never_lands_in_run_files`：用合成 key 跑一个 run，逐文件扫描 run 目录，断言 key 字节不出现；同时断言 `config/llm.yaml` 只含 `api_key_env`。
- 对全部被跟踪文件做字符串扫描，网关 key 无命中。

## 8. 冻结前验证

| 项 | 结果 |
| --- | --- |
| pytest | `105 passed` |
| framework_check | 6/6 PASS（含第 6 项假端点端到端） |
| 假端点 10d replay | MATCH |
| 假端点 72d replay | MATCH |

framework 第 6 项当次输出：`127.0.0.1:61322 启动→2 天→关闭；source=llm 落地 157/157 条，night_batch=29 realtime=65 replan=0（入口触发 39/105/0）；calls=94 schema_reject=0 fallback=0 拒绝率=0.00% 守恒=0 replay=MATCH`。

## 9. 复现命令

```bash
# CI 假端点
python3 tools/fake_llm_server.py --port 64310 --seed 42 &
export LLM_BASE_URL=http://127.0.0.1:64310/v1
export LLM_API_KEY=test-key
export LLM_MODEL=fake/model
python3 sim/run_headless.py --days 10 --seed 42 --npc 0 --with-players 1 \
  --enable-llm --llm-max-tokens 2048 --run-id fake-10d
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --with-players 1 \
  --enable-llm --llm-max-tokens 2048 --run-id fake-72d
kill %1

# 重放
python3 tools/replay.py --run runs/fake-10d   # MATCH
python3 tools/replay.py --run runs/fake-72d   # MATCH
```

独立守恒复算（不读 summary）：

```bash
python3 - <<'PY'
# 从 events.sqlite 重放 state，累计 mint/burn，比较 accounts_sum-(minted-burned)
PY
```

结果见 5.2 表。

## 10. 冻结与干净重跑

- tag：`delivery-p3a`
- tag 指向 commit：待回填
- content_hash：`c9d6bb66f2858616c3b5a926dbc1bfad32f245ca71be5b18fda846ad9aaedc98`
- 干净 worktree：待回填
- pytest：待回填
- framework：待回填
- 10d / 72d 假端点哈希：待回填
- replay：待回填

## 11. 仍未验证 / 未达标

1. **真实端点 72d 未跑**：只做了 2 天；72 天需要预算确认（见 6.6）。
2. **真实模型质量未知**：2 天 18% 拒绝率、K1 0/91；样本小，不能判断长期质量。模型动作参数缺失是主要失败模式。
3. **并发多实例未测**：单进程单 Writer；多实例共享 `events.sqlite`、多 key 并发限流、网关侧并发配额均未验证。
4. **真实账单未知**：所有成本是按仓库费率折算的估算值；网关实际计价可能不同（尤其 reasoning token 是否计费）。
5. **真实端点稳定性未知**：5 次探测均 200，但没有做长时间压力/限流/断连测试。
6. **假端点质量不代表模型质量**：假端点只用于 CI 接线与守恒回归，K1/多样性数字不能当作模型效果。
