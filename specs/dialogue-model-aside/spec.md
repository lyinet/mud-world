# 对话模型旁路 Spec：账本记剧本，模型只做配音

> 状态：待确认（确认后才进入实现）
> 目标仓库：`/Users/lyinet/projects/XKX/world-sim`
> 关联事实来源：`specs/npc-dialogue-variety/spec.md`（上一轮只做模板多样性，明确禁止改 `session.py`）
> 本轮是新的架构决策，不复用上一轮“模板多样性”的验收口径。

## 1. 用户决策（硬约束）

1. `talk` 的 `memory_add` 继续写模板台词，逐字节可复现；模型文本**绝不**进入 `events` / `state` / NPC 记忆 / `state_hash` / `event_hash` / `content_hash`。
2. 模型台词写入独立旁路存储，键 `(event_seq, npc_id)`，值包含文本、模型名、时间；删除旁路不影响世界。
3. 只在构造 `TalkAction` 时注入 `AsyncDialogueQueue`；不构造 decision provider、不启用 `LLMReasoner`、不改 `llm_enabled`。
4. API 下发对话优先取旁路的模型文本，缺失则用事件里的模板文本；前端尽量不改。
5. 独立开关 `XKX_DIALOGUE_MODEL=1`，默认关；中转站不可用 / 预算耗尽 / 超时一律静默退回模板，不拒绝动作、不阻塞 tick。
6. 验收六项（见 §5），缺一不可。
7. 文档写清“为什么模型台词不能进账本”和运维清空/关闭方法；提交 + CHANGELOG。

## 2. 已核实的现状（实现依据）

| 位置 | 现状 |
|---|---|
| `src/wsim/actions/social.py:104-165` | `TalkAction.apply` 已用 `compose_talk` 生成模板 `reply`；有 `self.dialogue` 时调用 `reply_sync(timeout_s=8.0)`，成功后只 `dialogue_runtime.remember(...)`。 |
| `src/wsim/actions/social.py:250-268` | 两条 `memory_add` 分别写 `talk:player` 与 `talk:npc`，`talk:npc` 使用模板 `reply`。**红线当前已满足。** |
| `src/wsim/agent/dialogue_runtime.py`（未跟踪） | `enabled()` 读 `WSIM_DIALOGUE`；`build_queue` 用 `budget=None, telemetry=None`；`attach_if_enabled` 无会话调用点；`remember` 是进程内 LRU；`overlay_reply` 按 `(player,target,tick,message)` 弹缓存并改 `details.reply`。 |
| `src/wsim/actions/registry.py:147-153` | 已有一段按 `WSIM_DIALOGUE` 自动挂载到 `TalkAction` 的逻辑，使用 `XKX_RUN` 猜 run 目录，无预算/遥测，且不是会话级注入。 |
| `src/wsim/api/server.py:728-744` | action 路由在 `session.action()` 之后调用 `dialogue_runtime.overlay_reply(result, args)`；此时 `result["event_seq"]` 已存在。 |
| `src/wsim/api/session.py:2364-2418` | `_response` 生成 `details.reply`（来自 `talk:npc` 模板），并返回 `event_seq`。 |
| `src/wsim/api/session.py:918-956` | `messages()` 直接读事件里的 `talk:npc` 文本，无旁路。 |
| `src/wsim/api/session.py:740-756` | `public_messages()` 匿名返回空列表，无需处理。 |
| `src/wsim/api/server.py:162-179, 641-647` | 健康/指标里的 `llm_calls` 只读 ambient 状态文件，API 对话调用不会计入。 |
| `src/wsim/agent/dialogue_async.py:264-329` | `_finish_sync` 记录 `slot="dialogue"` 的遥测、预算与 `dialogue.jsonl`；预算/超时/异常均返回 `fallback=True`。 |
| `src/wsim/sim/headless.py:372-382` | headless 通过 `WSIM_DIALOGUE=1` 把队列直接挂到 `TalkAction`，用于既有 golden 的 `dialogue_calls` 基线。 |

关键风险：`reply_sync` 在 `apply` 内同步执行会占用 Writer 事务；API 侧若直接沿用会阻塞 tick。必须把 API 的模型调用移到事务提交之后。

## 3. 目标架构

### 3.1 旁路存储 `src/wsim/agent/dialogue_aside.py`（新增）

```python
class DialogueAside:
    def __init__(self, run_dir: Path | str, filename: str = "dialogue_aside.jsonl")
    def get(self, event_seq: int, npc: str) -> str | None
    def put(self, event_seq: int, npc: str, text: str, model: str) -> None
    def count(self) -> int
    @property
    def path(self) -> Path
```

- 文件：`runs/<run>/dialogue_aside.jsonl`，只追加，不参与任何哈希/投影/回放。
- 行格式（`sort_keys=True`）：
  ```json
  {"created_at":"2026-11-07T12:00:00+00:00","event_seq":123,"model":"deepseek-chat","npc":"npc:xxx","text":"..."}
  ```
- `__init__` 启动时加载索引；文件缺失/行损坏都跳过，不抛异常。
- `put` 加锁追加并同步更新内存索引；重复 `(event_seq,npc)` 以后写为准。
- 读取优先级只由调用方实现：旁路有则模型，无则模板。

### 3.2 运行时 `src/wsim/agent/dialogue_runtime.py`（改造）

- `enabled()`：只读 `XKX_DIALOGUE_MODEL`（`1/true/yes/on`），默认 `False`；`WSIM_DIALOGUE` 不再控制 API 注入。
- `build_queue(root, run_dir)`：
  - 配置来源保持“管理台 provider 优先、env 兜底”；
  - 用 `load_provider_settings(root)` 的 `budget` + `load_cost_rates(root)` 构造独立 `BudgetGuard`，传入队列；
  - 传入 `CostTelemetry(rates=load_cost_rates(root))`；
  - `log_dir=Path(run_dir)`；
  - 新增参数 `defer_calls=True`（见 3.3）；
  - 任何配置异常返回 `None`。
- `attach_if_enabled(talk, root, run_dir) -> AsyncDialogueQueue | None`：开关关闭/配置失败返回 `None`，绝不抛异常；成功时设置 `talk.dialogue = queue` 并返回队列。
- `remember(player, npc, tick, message, text, model)`：缓存值改为 `(text, model)`。
- `overlay_reply(result, args, aside, model)`：
  - `details.setdefault("dialogue_source", "template")`；
  - 只在 `verb=="talk"` 且 `result["ok"]` 时弹缓存；
  - 命中：`aside.put(result["event_seq"], npc, text, model)` → `details["reply"]=text`、`dialogue_source="model"`；
  - 未命中：保持模板，不写旁路。

### 3.3 不阻塞 tick：`AsyncDialogueQueue` 增加 `defer_calls`

`src/wsim/agent/dialogue_async.py` 构造函数新增 `defer_calls: bool = False`，默认保持 headless 现有行为：

- `defer_calls=False`（headless / 既有 golden）：`TalkAction.apply` 仍同步调用 `reply_sync`，调用计数与 `dialogue.jsonl` 不变。
- `defer_calls=True`（API 会话）：`TalkAction.apply` **不发起模型调用**，只在实例内暂存一份对话上下文；HTTP 层在事务提交、锁释放后调用 `reply_sync`。

`TalkAction` 新增（`social.py`）：

```python
self._pending: OrderedDict[tuple[str, str, int, str], dict]  # 上限 256
def take_pending(self, key) -> dict | None
```

- `apply` 在 `self.dialogue.defer_calls` 为真时，把 `pid/target/tick/message/persona/summary/day` 暂存；不写任何事件字段。
- 配偶专属台词的覆盖场景不暂存（保持模板优先）。
- 暂存结构只在进程内存在，不是世界状态。

### 3.4 会话接线 `src/wsim/api/session.py`

1. `__init__` 在 `self.registry = build_registry(...)`（约 409 行）之后：
   ```python
   self.dialogue_aside = DialogueAside(self.run_dir)
   talk = self.registry.get("talk")
   self.dialogue_queue = dialogue_runtime.attach_if_enabled(talk, self.root, self.run_dir)
   self.dialogue_model = self.dialogue_queue.client.config.model if self.dialogue_queue else ""
   ```
   不构造 decision provider，不改 `llm_enabled`。
2. 新增 `finalize_dialogue(self, result, args) -> None`：
   - 条件：`verb=="talk"`、`result["ok"]`、`dialogue_queue is not None`、`dialogue_queue.defer_calls`；
   - 从 `talk.take_pending((player, target, tick, message))` 取上下文，缺失直接返回（模板）；
   - 在会话锁外调用 `reply_sync(timeout_s=XKX_DIALOGUE_TIMEOUT_S，默认 8)`；任何异常/超时/预算拒绝都保持模板；
   - 成功且文本非空：`aside.put(event_seq, npc, text, model)`、`details["reply"]=text`、`dialogue_source="model"`；
   - 整个方法 `try/except` 包裹，绝不向 HTTP 抛错。
3. `messages()`：对每条 `talk:npc`，`aside.get(event.seq, npc)` 命中则覆盖 `text`，并新增 `"dialogue_source": "model"|"template"`。事件本身不变。
4. 若 `player_state` / `status` 有回显最近回复的字段，同样按旁路覆盖；实现时全局搜索确认，没有则不改。
5. `public_messages()` 仍返回空，不改。

### 3.5 HTTP 层 `src/wsim/api/server.py`

- action 路由把 `dialogue_runtime.overlay_reply(result, args)` 替换为 `session.finalize_dialogue(result, args)`（避免全局缓存与 HTTP 层耦合）。
- `/api/health` 新增只读块（不改变原有键语义）：
  ```json
  "llm": {
    "calls": 3,
    "by_slot": {"dialogue": 3},
    "decision_calls": 0,
    "dialogue": {"enabled": true, "model": "...", "source": "env|admin_registry|off",
                 "ok": 3, "fallback": 0, "fallback_by_reason": {}}
  }
  ```
  `calls` 取 `dialogue_queue.stats()["calls"]`；没有队列时为 0。
- `/metrics`：保留 `xkx_llm_calls_total`（ambient）语义，新增 `xkx_dialogue_llm_calls_total`（API 对话计数），便于验收“只来自 dialogue 槽”。

### 3.6 注册表 `src/wsim/actions/registry.py`

- 删除 147-153 的 `WSIM_DIALOGUE` 自动挂载（它按 `XKX_RUN` 猜目录、无预算/遥测，且会绕过会话级注入）。
- headless 的显式注入（`sim/headless.py:372-382`）保持不变。

### 3.7 文档与提交

- `docs/OPS-RUNBOOK.md` 新增“对话模型旁路”：
  - 为什么模型台词不能进账本（哈希/回放/审计确定性）；
  - 开启：`XKX_DIALOGUE_MODEL=1`（并确保启动时加载 `.env`）；
  - 关闭：删除该变量或置 0；
  - 清空旁路：`rm runs/<run>/dialogue_aside.jsonl`（可在线删，重启后索引为空）；
  - 失败语义：`network/auth/budget_exhausted/timeout` 均静默退回模板，动作仍成功；
  - 排查：`/api/health` 的 `llm` 块、`runs/<run>/dialogue.jsonl`。
- `CHANGELOG.md` 顶部新增条目。
- 提交只包含本特性文件：
  - 新增：`src/wsim/agent/dialogue_aside.py`、`src/wsim/agent/dialogue_runtime.py`、`tests/test_dialogue_aside.py`、`specs/dialogue-model-aside/*`
  - 修改：`src/wsim/actions/social.py`、`src/wsim/actions/registry.py`、`src/wsim/agent/dialogue_async.py`、`src/wsim/api/session.py`、`src/wsim/api/server.py`、`src/wsim/api/metrics.py`、`docs/OPS-RUNBOOK.md`、`CHANGELOG.md`
  - **不提交**无关的既有改动（`reports/*`、`ui/player/*`、`actions/basic.py` 等）。

## 4. 失败与边界

| 场景 | 行为 |
|---|---|
| 开关关闭 | 不构造队列，不调用模型，零行为变化 |
| 无 key / provider 不可用 | `build_queue` 返回 `None`，模板 |
| 网络错误 / 401 / 402 | `reply_sync` fallback，模板；动作事件照常成功 |
| 预算耗尽 | `BudgetGuard.check_dialogue` 返回 false，fallback，不发起调用 |
| 超时 | `FutureTimeout` → fallback，模板 |
| 旁路文件损坏/被删 | 启动时索引为空，模板；世界不受影响 |
| 并发对话 | 暂存键含 `player+target+tick+message`，旁路追加加锁 |
| 进程重启 | 旁路文件仍在则继续生效；删掉则模板 |

## 5. 验收（缺一不可）

1. **无玩家 2 日 golden 前后逐字节一致**
   - 运行当前启用的确定性基线（`tests/test_p3e_golden.py` 的 `GOLDEN_MILESTONE_V6` 对应用例），分别在 `XKX_DIALOGUE_MODEL=0` 与 `=1` 下各跑一次；
   - 从各自 run 的 `summary.json` 取 `hashes.state_final` / `hashes.event_final`，并对 `events.jsonl` 做 `sha256sum`；
   - 报告贴出两组哈希，必须完全相同。
2. **有玩家世界同 seed 两次完全一致**
   - 新增 `tests/test_dialogue_aside.py`：同一 `RunConfig`/seed、同一脚本化 `talk`，跑两个独立 `WorldSession`；
   - 断言两次的 `state_final`（状态规范 JSON 的 sha256）与 `event_final`（事件序列规范 JSON 的 sha256）完全一致；
   - 断言 `events` 中 `talk:npc` 文本等于模板 `compose_talk` 输出，且不含旁路模型文本。
3. **旁路有真实模型台词**
   - `head -3 runs/<run>/dialogue_aside.jsonl`，贴 3 条原始 `text`（含 `event_seq/npc/model`）。
4. **`llm_calls` 非 0 且只来自 dialogue 槽**
   - `GET /api/health` 的 `llm.calls > 0`、`llm.by_slot == {"dialogue": N}`、`decision_calls == 0`；
   - `runs/<run>/dialogue.jsonl` 每条记录都是对话请求。
5. **删旁路重启退回模板**
   - 停 API → `rm runs/<run>/dialogue_aside.jsonl` → 重启 → `talk` → `GET /api/player/messages`；
   - 文本为模板，`dialogue_source=template`，世界正常推进。
6. **回归**
   - `pytest -q tests/test_p3e_golden.py`（当前启用用例全部通过）；
   - `pytest -q tests/test_talk_action.py tests/test_dialogue_intents.py tests/test_dialogue_routing.py tests/test_dialogue_guard_integration.py`；
   - 新增 `tests/test_dialogue_aside.py` 通过。

## 6. 前端影响

- action 响应：`details.reply` 已由 `finalize_dialogue` 覆盖，前端无需改。
- 历史消息：`GET /api/player/messages` 已按旁路覆盖，前端无需改。
- 若前端另有直接读 `events.sqlite` / `world_feed` 的路径，`world_feed` 已排除 `player:*`，无影响；实现时全局搜索确认没有第三处读取。
- 唯一可见差异：同一句玩家输入后，NPC 回复文本不同；`dialogue_source` 可用于调试。

## 7. 需要确认

1. 接受 §3.3 的 `defer_calls`：API 延迟到事务提交后再调模型（HTTP 响应可能多等最多 8s），以换取“不阻塞 tick”；headless 行为保持不变。
2. 接受 `/api/health` 新增 `llm` 块与 `/metrics` 新增 `xkx_dialogue_llm_calls_total`（不改旧键语义）。
3. 接受提交范围只包含 §3.7 列出的文件，工作区里其他既有未提交改动不进入本次提交。
