# NPC 对话同质化修复 Tasks

## Phase 1 · 采集修复前真实证据

- [x] 1.1 新建 `tools/dialogue_probe.py`：注册/登录/enter/state，取同地点 5 个 NPC，每人 3 轮 talk，保存完整请求与响应原文。
- [x] 1.2 对 run `player-demo-20261107-002916-86448`（API `8031`）跑基线，产出 `reports/npc-dialogue-variety-before.json`。
- [x] 1.3 计算基线指标：unique/15、同意图跨 NPC 相同对数、同 NPC 相邻轮相同对数、全局 LCS、模板命中率、模型生成率。
- [x] 1.4 记录“修复前”证据对应的进程/HEAD；修复后用新进程重跑同场景。

## Phase 2 · 代码修复

- [x] 2.1 `src/wsim/actions/social.py`：新增 `_talk_context`，`turn` 由 NPC memory 的 `talk:npc` 条数推导（不新增 `talk_ledger` 字段，避免 `state_hash` 漂移）；`state_summary` 增加 `turn/last_message/last_reply`。
- [x] 2.2 `src/wsim/agent/dialogue.py`：`_pick_quote` 增加 `turn` 并按意图轮换人设台词；台词用于所有意图；`_trim` 优先保留台词；补 `QUOTE_HINTS`；同文防重取下一轮变体。
- [x] 2.3 `src/wsim/agent/prompt.py`：`DIALOGUE_SYSTEM_PROMPT` 增加“延续上一轮、不得重复、体现身份/性格/关系/处境差异”的硬约束。
- [x] 2.4 `src/wsim/agent/dialogue_runtime.py`：管理台 provider 注册表优先、env 兜底；`WSIM_DIALOGUE=1` 时挂载到 `talk`。
- [x] 2.5 红线：模型台词只进 `dialogue.jsonl` + 本次 HTTP 响应；`social.py` 不再用模型文本覆盖状态里的模板回复。

## Phase 3 · 测试

- [x] 3.1 新增 `tests/test_dialogue_variety.py`：5 persona × 3 turn 去重 ≥12；同 NPC 三轮互异；同意图 5 NPC 互异；回复含人设台词片段。
- [x] 3.2 跑既有对话测试：`test_talk_action.py`、`test_dialogue_intents.py`、`test_dialogue_routing.py`、`test_dialogue_demand_routing.py`、`test_dialogue_guard_integration.py`、`test_agent_prompt.py`。
- [x] 3.3 golden V6 因对话文本变化漂移：更新 V6 哈希并在测试常量写明原因，旧 V3/V4/V5 基线原样保留，不动其他工作线。

## Phase 4 · 修复后验证与交付

- [x] 4.1 用修复后代码重启 API（明确的新进程），跑同一 `dialogue_probe.py` 场景，产出 `reports/npc-dialogue-variety-after-template.json` 与 `...-after-model.json`。
- [x] 4.2 计算修复后指标并与基线对照；`same_intent_cross_npc_identical=0`、`same_npc_consecutive_identical=0`、`unique_replies=15/15`。
- [x] 4.3 写 `reports/npc-dialogue-variety.md`：根因（含行号）、`capabilities` 证据、修复前后逐轮原文对照、指标表、模型接入条件。
- [x] 4.4 更新 `CHANGELOG.md`。
- [x] 4.5 提交（`fix(dialogue): ...`）；确认未修改 `src/wsim/api/session.py`，工作区不含其他工作线改动。

## 完成判定

- 15 条回复修复后逐条不同，无同意图跨 NPC 重复。
- 同一 NPC 三轮不同，不同 NPC 同题不同，且带各自人设口吻。
- 报告明确：修复前 `dialogue.status=unconfigured`、`model_generated=0/15`、`template_hit=15/15`；不把模板多样性冒充模型生成。
