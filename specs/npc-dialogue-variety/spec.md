# NPC 对话同质化：根因与修复 Spec

> 目标 run：`player-demo-20261107-002916-86448`（API `8031` / UI `8000`）
> 约束：只动对话/内容相关代码；**不修改** `src/wsim/api/session.py`；改完提交 + `CHANGELOG.md`。

## 1. 已确认的真实现场

### 1.1 运行面

- `GET /api/health`：`mode=live`、`run=player-demo-20261107-002916-86448`、`upstream_ok=true`、`readonly=false`。
- 管理面 `GET http://127.0.0.1:8032/api/admin/providers`：
  - `capabilities.dialogue.status = "unconfigured"`（不是 `active`，也不是 `stub` 的静态 persona 生成）。
  - `capabilities.dialogue.model = ""`、`provider = ""`、`error = null`。
  - `providers = 0`（管理面 `health.providers`）。
- API 进程环境：**没有** `WSIM_DIALOGUE`；`LLM_API_KEY=sk-your-key-here`（占位值）。
- run 日志 `logs/api-restart.log`：`ambient_hour ... llm_calls=0 cost=$0.0000` 连续多行。
- run 目录 **不存在** `dialogue.jsonl`，即 `AsyncDialogueQueue.reply_sync` 一次都没有被调用。
- 结论：**当前对话 100% 走确定性模板 `compose_talk`，模型生成路径从未激活**；不存在“模型回复被 sanitizer 洗成同一句”或“预算拒掉后统一 fallback”的现网证据。

### 1.2 代码路径与变量

| 位置 | 问题 |
|---|---|
| `src/wsim/actions/registry.py:111` | `reg.register(TalkAction(content))`，API 会话用的 registry 从未注入 `dialogue` 队列；即便配置了模型，API 路径也不会走模型。 |
| `src/wsim/actions/social.py:116-118` | `compose_talk(persona, target.id, ..., tick, places=places)` **没有传** `turn` / `last_message` / `last_reply`。`turn` 恒为默认值 `0`。 |
| `src/wsim/agent/dialogue.py:776` | `turn: int = 0` 默认值。 |
| `src/wsim/agent/dialogue.py:652-653` | `ab(base)` 用 `turn % 2` 选 `.a/.b`；`turn=0` → 永远 `.a`。同一 NPC 同一意图每轮命中同一骨架。 |
| `src/wsim/agent/dialogue.py:77-224` | `TEMPLATES` 是全局共享模板，只按意图/对象分支，不按 NPC 分支。 |
| `src/wsim/agent/dialogue.py:788-799` | 人设只提取 `role` / `place` 做字符串替换；`voice` 只在 `intent == "deflection"` 时用于选台词。 |
| `src/wsim/agent/dialogue.py:801` | `_pick_quote(...) if intent == "deflection" else ""`，其他人设台词在绝大多数意图下被丢弃。 |
| `src/wsim/actions/social.py:161` | `talk_ledger` 只记关系增量，没有轮次计数，因此即使调用方想传 `turn` 也无数据源。 |
| `src/wsim/world/effects.py:844-854` | `talk_ledger` 落库白名单只有 6 个字段，新增 `turns` 必须同步写入。 |
| `src/wsim/agent/prompt.py:220-224` | `DIALOGUE_SYSTEM_PROMPT` 未要求沿用上一轮/体现关系与处境；即使接上模型也缺少“不同轮次不同”的显式约束。 |

### 1.3 同质化的三层根因

1. **模型没接上**：`dialogue` capability `unconfigured`，`WSIM_DIALOGUE` 未设置，`llm_calls=0`，无 `dialogue.jsonl`。
2. **模板路径把轮次压扁**：`turn` 从未传入、从未落库，`turn % 2` 恒为 `.a`；同一 NPC 同一意图无论聊几轮都是同一句。
3. **模板路径把 NPC 压扁**：模板只按意图选，不同 NPC 仅替换 `role/place`；人设台词只在 `deflection` 意图使用，导致 5 个 NPC 对同一意图给出近乎同一句。

## 2. 修复目标与边界

目标（验收口径）：

- 同一 NPC 连续 3 轮（同意图）回复互不相同。
- 5 个 NPC 对同一意图的回复互不相同，且至少包含各自人设台词/口吻。
- 不引入随机、不读墙钟；同一 `(npc, message, tick, turn)` 仍可回放。
- **不把“更花哨的模板”说成模型生成**：报告必须明确当前模型仍未配置，模板命中率为 100%。

非目标：

- 不改 `src/wsim/api/session.py`。
- 不新增 LLM provider、不伪造 key、不改预算机制。
- 不改任务/商店/技能/UI 等其他工作线。

## 3. 落地方案（按文件）

### 3.1 `src/wsim/actions/social.py`

1. 新增模块级函数 `_talk_context(state, npc_id) -> tuple[int, str, str]`：
   - `turns = int((state.talk_ledger.get(npc_id) or {}).get("turns", 0) or 0)`；
   - 从 `state.memories.get(npc_id) or []` 逆序找最近一条 `source="talk:npc"` 与 `source="talk:player"`；
   - 返回 `(turns, last_player_message, last_npc_reply)`；`talk:npc` 的 `text` 去掉前缀 `我答：`，`talk:player` 取 `说：` 之后的内容。
2. 在 `TalkAction.apply` 的 `compose_talk(...)` 调用前取上下文：
   - 传入 `turn=turns`、`last_message=last_player`、`last_reply=last_npc`。
3. 模型分支 `state_summary` 增加 `turn` / `last_message` / `last_reply` 三个字段（`social.py:122-139`），让模型能看到上一轮。
4. `talk_ledger` effect 增加 `"turns": turns + 1`（`social.py:261-271`），保证轮次跨请求持久化。

### 3.2 `src/wsim/world/effects.py`

- `talk_ledger` 落库白名单增加 `"turns": _as_int(d.get("turns", 0), "turns")`（`effects.py:847-854`）。
- `src/wsim/world/state.py:368-370` 的注释同步补 `turns`，避免字段语义丢失。

### 3.3 `src/wsim/agent/dialogue.py`

1. `_pick_quote(voice, intent, digest, last_reply, turn)` 增加 `turn` 参数：
   - 候选集优先取 `QUOTE_HINTS[intent]` 命中的台词；无命中时用全部台词。
   - 起点 `(digest 偏移 + turn) % len(pool)`；跳过已出现在 `last_reply` 的台词，保证连续轮次不复用同一句。
2. `compose_talk` 中把台词用于**所有意图**，不再只用于 `deflection`（替换 `dialogue.py:801`）：
   - `quote = _pick_quote(voice, intent, digest, last_reply, turn)`。
   - 若 `intent == "deflection"` 保持现有优先逻辑。
3. `_trim(body, quote)` 改为优先保留台词：
   - `quote` 为空：维持现状。
   - `body + quote <= 60`：直接拼接。
   - 超出 60：按句读把 `body` 裁到 `60 - len(quote)`，裁点仍要求 >=19 字；裁不出则只返回 `body`。
   - 这样 5 个 NPC 即使同一意图，也会带出各自人设台词，且逐轮轮换。
4. `QUOTE_HINTS`（`dialogue.py:227`）按意图补关键词（只用台词里实际出现的词）：
   - `greeting`: `("您","阿是","施主","掌柜","姑娘")`
   - `directions`: `("城隍","庙","路","三尺","摊子")`
   - `gossip`: `("金掌柜","罗家","蒋施主","苏掌柜","崔捕快","曹半仙")`
   - `price`: `("文","钱","价","斤两","称","米")`
   - `credit`: `("文","账","利","还","赊","红绳")`
   - `chitchat`: `("酒","刀","庙","摊","面")`
   - `help`: `("跑腿","帮","催","挪","打")`
   - `rejection`: `("不","别","没","认")`
   - 未列出的意图走全部台词轮换。
5. 新增确定性防重：若最终 `reply == last_reply`，用 `turn + 1` 再取一次台词/骨架；仍相同则取 `TEMPLATES` 中同意图的另一个变体。**不使用随机**。

### 3.4 `src/wsim/agent/prompt.py`

- `DIALOGUE_SYSTEM_PROMPT`（`prompt.py:220-224`）追加一句硬约束：
  - “必须利用 `state.last_message` / `state.last_reply` 延续上一轮；不得重复上一轮原句；不同角色的身份、性格、关系与处境必须在措辞上体现差异。”
- 仅影响未来模型路径，不改变当前模板输出。

### 3.5 模型接入说明（如实报告，不在本轮伪造）

当前 API 路径的模型接入缺三件东西，缺一不可：

1. **真实 provider/key 或中转站**：现在 `LLM_API_KEY=sk-your-key-here` 是占位值，管理面 `providers=0`，`dialogue.status=unconfigured`。
2. **启动开关**：`WSIM_DIALOGUE=1`（`sim/headless.py:370-374` 的既有门控）。
3. **API 会话注入点**：`registry.py:111` 目前只 `TalkAction(content)`；若要走模型，需要在 registry 构建后、由会话层把 `AsyncDialogueQueue` 挂到 `talk.dialogue`，而该注入点在 `session.py:409` 附近，属于本轮禁改文件。修复只把“上下文变量”准备好（`turn/last_message/last_reply/persona/state`），不碰该注入点。

因此本轮交付的多样性来自**修复后的确定性模板 + 人设台词轮换**，报告中必须标注 `model_generated = 0/15`、`template_hit = 15/15`，不得宣称模型已接上。

## 4. 验证方案

### 4.1 新增 `tools/dialogue_probe.py`（可重复的前后对照工具）

- 输入：`--base http://127.0.0.1:8031`、`--account qa_dialogue_01`、`--password`、`--out <json>`。
- 流程：`/api/auth/register`（已存在则忽略）→ `/api/auth/login` → `/api/player/enter` → `/api/player/state` 取同地点前 5 个 NPC → 每个 NPC 3 轮 `talk`。
- 固定话术（保证前后同条件）：
  - R1 `你好，今日可好？`（greeting）
  - R2 `幸会，近来可好？`（greeting，换措辞）
  - R3 `听说镇上最近有新鲜事？`（gossip）
- 每轮保存：完整请求 JSON、完整响应 JSON、`details.reply`（缺失时从 `/api/player/messages` 取最近 `kind="talk_reply"`）。
- 输出指标：
  - `unique_replies / 15`
  - `same_intent_cross_npc_identical`（同意图不同 NPC 回复完全相同的对数，目标 0）
  - `same_npc_consecutive_identical`（同 NPC 相邻轮完全相同的对数，目标 0）
  - `longest_common_substring`（15 条回复的全局 LCS 长度）
  - `template_hit_rate`（当前路径 15/15）
  - `model_generated_rate`（0/15）

### 4.2 验收阈值

- 修复前基线必须留档：预期 `unique_replies ≤ 2/15`、`same_intent_cross_npc_identical ≥ 8`、`same_npc_consecutive_identical ≥ 5`（以实测为准）。
- 修复后：
  - `unique_replies ≥ 12/15`（目标 15/15）；
  - `same_intent_cross_npc_identical = 0`；
  - `same_npc_consecutive_identical = 0`；
  - 每条回复都能在对应人设卡中找到至少一个台词片段（允许裁短）。
- 报告 `reports/npc-dialogue-variety.md` 贴修复前后逐轮原文对照，以及上述指标。

### 4.3 回归测试

- 新增 `tests/test_dialogue_variety.py`：
  - 5 个真实 persona × 3 个 `turn`，断言 15 条回复去重数 ≥12；
  - 断言同一 NPC `turn=0/1/2` 互不相同；
  - 断言同一 message 下 5 个 NPC 回复互不相同；
  - 断言回复包含对应 persona 的台词片段（裁短后仍需可匹配）。
- 运行既有测试：
  - `tests/test_talk_action.py`
  - `tests/test_dialogue_intents.py`
  - `tests/test_dialogue_routing.py`
  - `tests/test_dialogue_demand_routing.py`
  - `tests/test_dialogue_guard_integration.py`
  - `tests/test_agent_prompt.py`
- 若既有 golden 因模板文案变化失败，只调整对话相关测试期望；不修改其他工作线文件。

### 4.4 收尾

- 更新 `CHANGELOG.md`：记录“对话轮次上下文丢失/人设台词未用/API 未注入模型”的根因、修复范围、验证指标、模型仍未配置的事实。
- 提交（commit message 以 `fix(dialogue):` 开头），不修改 `session.py`。

## 5. 需要确认的决策

1. 接受“本轮模型仍未接上，多样性由确定性模板 + 人设台词轮换提供”的交付口径（报告如实标注 0/15 模型生成）。
2. 接受把 `turns` 写入 `talk_ledger`（会新增一个状态字段，回放确定性不变）。
3. 接受在 `QUOTE_HINTS` 中补充关键词映射（只使用人设台词原文词，不新增文案）。
