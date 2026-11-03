# Tasks · delivery-p3b（P3-A / P2.2 / P2.1 任务保留）

## P3-B 混合决策、三槽位与意图理解

### B1 混合决策
- [x] `prompt.py`：17 动词白名单、`ACTION_SPECS`、世界状态注入、`build_decision_prompt` / `parse_decision_payload`。
- [x] `provider.py`：规则底稿 + 模型增量社交；`_filter_model_actions` 逐条预检，命中生存动词记 `rule_owned_survival` 丢弃。
- [x] `intent.py`：模型目标只在和规则 `GoalSelector` 一致时采纳，保留 `create` 与目标进度。
- [x] `lod.py`：`advance_to_batched` 批量唤醒，不改变 jitter/事件顺序。
- 验证：`tests/test_p3b_hybrid.py::test_model_cannot_replace_rule_actions`。

### B2 Prompt 与修复重试
- [x] Prompt 注入地点/相邻地点、钱与物品、可执行动作与前置条件、时间与剩余工时。
- [x] schema 失败 1 次修复重试，回喂错误原因；仍失败才回退 Mock，记 `repair_attempted` / `repair_succeeded`。
- 验证：`test_schema_repair_succeeds_once`；72d 首次通过率 100%，修复 0 次。

### B3 延迟
- [x] `dialogue_async.py`：玩家发话入队立即返回，NPC 回应异步写回，不阻塞 tick。
- [x] `ProviderClient.complete_many` 线程池批处理（默认并发 4）。
- [x] 12 NPC 1 日墙钟 0.364s；decision p50 0.061s / p95 0.133s。
- 验证：`test_async_dialogue_does_not_block_tick`、`test_complete_many_runs_concurrently`。

### B4 三槽位与分槽成本
- [x] `config/llm.yaml`：`models.decision` / `models.dialogue` / `models.intent`；用户只改 base_url 与两个模型名。
- [x] `cost.jsonl` 每条带 `slot` / `model` / `usage_source`；summary 与报告按槽汇总，不给混合总价。
- [x] intent 上游不返回 usage → token 记 null（`usage_source=missing`）。
- [x] `budget.py`：决策槽日 token、对话槽日调用数、意图槽日调用数；任一槽超限只降级该槽。
- [x] P3-A 72d 6,812 次全部归 decision、dialogue 0；旧 $12.05 混合口径作废。
- 验证：`test_cost_summary_splits_slots`、`test_intent_slot_records_null_tokens_when_upstream_has_no_usage`、`test_budget_slots_are_independent`。

### B5 意图理解层
- [x] `config/intents.yaml`：17 个意图模板，required/optional/source/destructive 齐全。
- [x] `intent_model.py`：中文键映射、中文数量词、缺字段 `needs_clarification`、未知 category/verb 拒绝、破坏性二次确认。
- [x] `tools/fake_intent_server.py`：参数缺失/中文数量词/否定句/改口四类用例。
- [x] 功能表与 `verbs.yaml` 双向对齐测试；故意改 verbs 会失败。
- 验证：`tests/test_p3b_intent_model.py`（10 项）。

### B6 验收
- [x] 假端点 72d：K1 **71/91 = 78.02%**（≥60%）、拒绝率 1.46%（≤2%）、守恒 0、replay MATCH。
- [x] 10d 对照：K1 45/91 = 49.45%，与 Mock 同值；P3-A 10d 1/91。
- [x] 与 P3-A 72d 3/91 并列，标注差异来自分工而非模型能力；content_hash 变化与 `non_comparable` 口径写明。
- [x] `reports/p3b-report.md`。
- [ ] 真实端点 ≤10 次抽样：本轮 0 次（无 key）。
- [ ] 真实 Xing API：本轮 0 次（等 key）。

### B7 冻结
- [x] commit → `git tag -a delivery-p3b`（指向 `222a2b9`，content_hash `83521723…`）。
- [x] 从 tag 干净重跑 pytest（124 passed）/ framework（6/6）/ 假端点 10d / 72d，哈希与主工作区一致，replay MATCH；已回填报告第 10 节。

---

## P3-A 真实模型接线

### A1 Provider 真实现
- [x] `src/wsim/agent/provider.py`：标准库 `urllib` POST `/v1/chat/completions`，body 含 model/messages/temperature/max_tokens。
- [x] 真实 socket 超时 + 指数退避（`max_retries` / `backoff_base_s` 可配）。
- [x] 错误分类 `timeout / network / http_4xx / http_5xx / parse`；前 3 类重试，后 2 类不重试。
- [x] `Completion(text, usage)`：实测 token 计入成本；失败响应也保留 usage。
- [x] 密钥只从 `api_key_env` 读；`fingerprint()` 不含 key。
- 验证：`python3 -m pytest -q tests/test_provider.py`。

### A2 本地假端点
- [x] `tools/fake_llm_server.py`：标准库、OpenAI 兼容、seed 稳定；可配延迟/失败率/畸形 JSON/超长输出。
- [x] `tools/framework_check.py` 第 6 项：127.0.0.1 启动→2 天推理→Intent 落地→关闭。
- 验证：`python3 tools/framework_check.py` → 6/6 PASS。

### A3 接进主循环
- [x] `src/wsim/sim/headless.py`：`night_batch` / `realtime` / `replan` 三种时机独立计数。
- [x] LOD 档位决定谁被调用；无底稿的唤醒跳过模型。
- [x] summary 输出 `decisions.*`（invocations/model_calls/actions/avg_actions/submitted/accepted/applied/accept_rate/apply_rate）。
- 验证：10d/72d 假端点，三计数器对账，见 `reports/p3a-report.md` 第 3 节。

### A4 预算护栏
- [x] `src/wsim/agent/budget.py`：日/月 token 与金额上限、soft_ratio、降档 spotlight→active→background→silent→mock。
- [x] 超限回退 Mock，日终写 `system_budget` 审计事件（每天最多一条）。
- [x] `SystemBudgetAction` 注册（verb `system_budget`）。
- [x] `tests/test_p3a_wiring.py`：极小上限实测降级且不停摆（拒绝率 0%）。
- 验证：`python3 -m pytest -q tests/test_p3a_wiring.py`。

### A5 提取适配器（不放宽 schema）
- [x] `prompt.py` `extract_intent_payload`：剥围栏、解一层包装、参数别名（destination→to / location→at）。
- [x] 未知动词（如 `go`）、缺失参数仍拒绝；原始输出保留在 `cost.jsonl.output_text`。
- [x] 测试证明原始输出可回放、半截输出不入世界。

### A6 真实端点实测
- [x] 5 次探测（≤5）：HTTP 200 ×5；`response_format` 可用；`max_tokens` 需 4096。
- [x] 真实 2 日小跑（12 NPC，18 次调用，$0.1511，replay MATCH，守恒 0）。
- [x] `config/llm.yaml`：用户侧只改 base_url/model；key 走 `api_key_env`，无明文。
- [ ] 真实 72 日跑：待用户确认预算（预估见报告 6.6）。

### A7 端到端对比与冻结
- [x] 假端点 10d/72d（seed 42）：调用数/token/成本/schema_reject/fallback/落地率/拒绝率/K1/K3/守恒。
- [x] 与 `delivery-p2.2` 基线并列并标 `non_comparable`。
- [x] commit → `git tag -a delivery-p3a`（附注 commit + content_hash）。
- [x] 从 tag 干净重跑 10d/72d + pytest + framework，回填 `reports/p3a-report.md` 第 10 节。

---

## P2.2 整改

### P1 对话持久化
- [x] `ui/adapter.py`：直接把 run 的 `events.sqlite` 交给 `Writer`，对话 effect 追加到持久 store。
- [x] 对话写入后同步 `world_hash.txt` / `event_hash.txt` / `summary.json`，刷新与 replay 可重建。
- [x] `src/wsim/agent/dialogue.py`：按 `turn` + `last_reply` 轮换台词与引导句，连续 3 轮不重复。
- [x] `tests/test_talk_action.py`：3 轮不重复、跨 3 日累积（affection 24 / trust 64）、关库重开仍在。
- 验证：`python3 -m pytest -q tests/test_talk_action.py`；`tools/replay.py --run /tmp/p22v-ui` → MATCH。

### P2 `open_shop` 门槛
- [x] `content/economy.yaml`：`shop_opening.expected_wage_days: 3` + `expected_staff`。
- [x] `src/wsim/actions/common.py`：`treasury_open_shop_buffer` 只按店铺岗位工资计算。
- [x] `business.py` / `intent.py` 同步新缓冲；`tests/test_treasury_revenue.py` 更新断言。
- 验证：72d `open_shop_postponed=0`，开店 11 次。

### P3 UI 任务指引
- [x] `/api/quests` + 任务板：发布人地点 / 同行 / 可接 / 提示。
- [x] `_event_hint`：拒绝事件输出可执行一句话。
- [x] NPC 详情显示当前地点与最近动作。

### P4 文档
- [x] `reports/delivery-p2.2.md`：四项改动、前后数字、K1 `non_comparable`、税差舍入、失败数口径。
- [x] `reports/delivery-p2.1.md` 顶部加 P2.2 更正指针；旧两份报告保留。

### P5 冻结
- [x] commit 代码 → `git tag -a delivery-p2.2`（附注 commit + content_hash）。
- [x] 从 tag 干净重跑 10d / 72d，replay MATCH，framework 5/5，pytest 89 passed。
- [x] 回填 `reports/delivery-p2.2.md` 并提交文档。

---

## P2.1 任务（历史）

## T1 状态与 Effect 底座
- [x] `src/wsim/world/state.py`：加 `talk_ledger`、`quest_stats`、`quests_expired_total`；
      同步 `clone()`、`to_dict()`。
- [x] `src/wsim/world/effects.py`：
      - `EFFECT_OPS` 加 `talk_ledger`、`quest_prune`；
      - `memory_add` 支持可选 `source`；
      - `talk_ledger` 覆盖写入；
      - `quest_prune` 只删 terminal 状态，保留 stats；
      - `quest_create/complete/fail` 维护 `quest_stats` 与 `quests_expired_total`。
- 验证：`python3 -m pytest -q tests/test_replay.py tests/test_effects.py`（如文件名存在）。

## T2 `talk` 执行器
- [x] 新建 `src/wsim/agent/dialogue.py`：`PLAYER_ID`、`extract_persona_voice`、`compose_talk`。
- [x] `src/wsim/actions/social.py`：新增 `TalkAction`（preconditions + apply + 上限 clamp）。
- [x] `src/wsim/actions/registry.py`：注册 `TalkAction`。
- [x] 新建 `tests/test_talk_action.py`（台词消费、记忆、±2/±8 上限、守恒 0、审批链）。
- 验证：`python3 -m pytest -q tests/test_talk_action.py`。

## T3 任务板健康度
- [x] `content/economy.yaml`：`quests` 增加 5 个新阈值。
- [x] `src/wsim/actions/quests.py`：
      - `SystemQuestPostAction` 加资金/物品/完成率/单模板过期/全局过期五道预检；
      - `SystemQuestFailAction` 追加 `quest_prune`。
- [x] `src/wsim/sim/headless.py`：`quests.health`（active/expired_on_board/expired_total/
      by_template + completion_rate）。
- [x] 新建 `tests/test_quest_health.py`（5 条规则）。
- 验证：`python3 -m pytest -q tests/test_quest_health.py`。

## T4 金库收入侧杠杆
- [x] `src/wsim/actions/common.py`：`tax_split_effects`、`daily_wage_bill`、
      `treasury_open_shop_buffer`。
- [x] `content/economy.yaml`：`taxes.transaction_treasury_share: 0.5`。
- [x] `src/wsim/actions/commerce.py`：Buy/Sell/Trade 三处税改分流。
- [x] `src/wsim/actions/business.py`：`OpenShopAction.preconditions` 加 `treasury_buffer`。
- [x] `src/wsim/sim/intent.py`：Planner `postponed_open_shop` + build_shop 延后；
      MockReasoner 汇总计数。
- [x] `src/wsim/sim/headless.py`：treasury `tax_to_treasury`/`tax_burned`/
      `open_shop_postponed`，intent_audit 同步。
- [x] 新建 `tests/test_treasury_revenue.py`。
- 验证：`python3 -m pytest -q tests/test_treasury_revenue.py`。

## T5 UI 落地
- [x] `ui/adapter.py`：session writer（复制 events.sqlite）+ 三身份 gateway；
      `dialogue()` 走 propose → request → approve → apply，返回 landed/seq/memory/relation。
- [x] `ui/index.html`：文案改「已落地」，展示 seq/outcome/记忆/关系变化。
- [x] 手工验证：`python3 ui/server.py --run runs/delivery-72d --port 8000`，
      一次对话后返回 `landed=true` 且 `seq` 非空。
- 验证：`python3 -m pytest -q` 后启动 UI 抽查。

## T6 文档纠错
- [x] 复制 `reports/delivery-p2.md` → `reports/delivery-p2.1.md`。
- [x] 修正 180 章节、931/754/177 拆分、护栏归因。
- [x] 新增 talk / 任务板 / 金库三节改法与前后数字。

## T7 重新冻结
- [x] commit 全部改动。
- [x] 计算新 `content_hash`，`git tag -a delivery-p2.1`（附注 commit + hash）。
- [x] `git worktree add /tmp/wsim-p21b delivery-p2.1`，跑 10d/72d、replay、framework_check、
      pytest。
- [x] 回填 `reports/delivery-p2.1.md`：tag/commit/content_hash、10d/72d 哈希、
      对话落地数、记忆条数、关系变化、过期单数、拒绝率、金库期末、税回流、
      开店延后次数、未达标项。
- [x] 清理临时 worktree；确认 `git status --short` 干净。
