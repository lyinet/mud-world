# NPC 对话质量与回归指标

本文件把“NPC 不该说同一句话”变成可执行的回归门槛。探针与产物：

- 工具：`tools/dialogue_probe.py`
- 产物：`reports/npc-dialogue-variety-{before,after-template,after-model}.json`
- 场景：真实玩家账号 → `enter` → 同地点 5 个 NPC × 每人 3 轮 `talk`
- 固定话术：R1 `你好，今日可好？`、R2 `幸会，近来可好？`、R3 `听说镇上最近有新鲜事？`

## 指标定义

| 指标 | 定义 | 发布门槛 |
|---|---|---|
| `unique_replies` | 15 条回复的完全去重数 | ≥ 12/15，目标 15/15 |
| `same_intent_cross_npc_identical` | 同一问题下不同 NPC 回复完全相同的对数 | 0 |
| `same_npc_consecutive_identical` | 同一 NPC 相邻两轮回复完全相同的对数 | 0 |
| `longest_common_substring_by_intent_max` | 同一问题下 5 条回复的最长公共子串最大值 | ≤ 10 字 |
| `template_hit_rate` | 走确定性模板的比例 | 记录，不设上限 |
| `model_generated_rate` | 走真实模型的比例 | 记录，不设下限 |

说明：`unique_replies` 是主门槛；LCS 用来防止“只换一个角色名词”的伪多样性。基线 LCS 为 26–29 字，修复后模板为 2 字、模型为 1 字。

## 红线（模型不得影响世界演进）

1. `memory_add` / `events.sqlite` / 世界状态只写 `compose_talk` 的确定性模板回复。
2. 模型台词只进入：
   - `runs/<run>/dialogue_aside.jsonl`（旁路存储，键 `(event_seq, npc_id)`，字段
     `event_seq` / `npc` / `text` / `model` / `created_at`）；
   - `GET /api/player/messages?since_seq=N` 的返回（只从旁路读取，`dialogue_source="model"`）。
   本次 HTTP 动作响应**只回模板句**，并带 `dialogue_source="template"`、`dialogue_pending=true`。
   `dialogue.jsonl` 仅作队列审计日志（调用/降级/延迟），不是第二套旁路。
3. 模型台词不写 `content_hash`，不参与目标选择、关系增量、经济与事件回放。
4. 回放同一 `(npc, message, tick, turn)` 永远得到同一模板句；`turn` 由 NPC memory 的 `talk:npc` 条数推导，是持久化确定值，不新增世界状态字段。

## 模型路径与调用时机（只有一条）

- 唯一入口：`src/wsim/api/session.py::WorldSession.finalize_dialogue`（[session.py:992](../src/wsim/api/session.py#L992)）
  → `AsyncDialogueQueue.submit_background`（[dialogue_async.py:306](../src/wsim/agent/dialogue_async.py#L306)）
  → `_answer_background` 内的 `client.complete`。
- 时机：`TalkAction.apply` 只写模板记忆与事件；API 在 `session.action()` 返回（事件事务已提交）后调用
  `finalize_dialogue`，**只登记后台任务、不等待模型**。该方法不持 `WorldSession.lock`，tick / Writer /
  其他请求都不受影响；离线 `sim/headless.py` 不挂模型队列（`dialogue_calls=0`）。
- 超时：`XKX_DIALOGUE_TIMEOUT_S`，默认 8 秒，clamp 0.5–20 秒；超时/网络/auth/预算失败一律保留模板，
  `health.llm.timeouts` 单独计数。
- 重试：对话槽强制 `max_retries=0`（含管理台降级链），失败即模板，不为同一句话重打网关。
- 背压：同时最多 2 条在飞，排队积压超过 5 条直接丢弃该句（不排队、不重试），计入
  `health.llm.dropped_backlog`；`health.llm.calls/ok/failures/timeouts/dropped_backlog` 为分项计数。
- 实测（20 次 `talk` + 20 次 `rest`，run `accept-async-on2-20261107` / `accept-async-off2-20261107`）：
  开模型 `talk` p50/p95/max `0.0019s/0.0027s/0.0027s`、`rest` `0.0014s/0.0027s/0.0040s`
  （7 受理、13 突发丢弃、0 超时）；关模型 `talk` `0.0018s/0.0034s/0.0041s`、
  `rest` `0.0019s/0.0029s/0.0047s`。两组同一量级（<50ms）。

迟到台词取回：

```http
GET /api/player/messages?since_seq=6&account_id=<account>
```

```json
{"ok":true,"since_seq":6,"next_since_seq":19,
 "messages":[{"id":"msg:7","seq":7,"tick":6,"kind":"talk_reply",
   "from":"duan_wudou","to":"player:qa_async_on",
   "text":"我把刀往案板上一搁，笑起来露金牙。今日还行……你要肥要瘦？",
   "dialogue_source":"model","model":"auto/deepseek-v4.1-flash",
   "created_at":"2026-11-06T18:56:04+00:00"}]}
```

动作响应字段样例（开关开启时）：

```json
{"ok":true,"event_seq":7,"details":{
  "reply":"我在呢，屠户这行我干惯了，从早忙到晚；你有话直说……",
  "dialogue_source":"template","dialogue_pending":true}}
```

验证方式：

```bash
python3 - <<'PY'
import sqlite3
con = sqlite3.connect("runs/<run>/events.sqlite")
for text in ["我好得很，天没亮就杀完一口猪", "腰都酸"]:
    n = con.execute(
        "select count(*) from events where effects like ? or args like ? or outcome like ?",
        (f"%{text}%",) * 3,
    ).fetchone()[0]
    print(text, n)
PY
```

期望：模型台词计数为 0；`grep -c` 只在 `dialogue_aside.jsonl`（与队列审计的 `dialogue.jsonl`）命中。

## 模型配置（两条路都支持）

`dialogue_runtime.build_queue` 的解析顺序（仅在开关开启时才执行）：

1. 管理台 provider 注册表：`wsim.admin.providers.resolve("dialogue")`（8032 注册，DB 优先）；
2. 环境变量 / `config/llm.yaml`：`ProviderConfig.from_env`（`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`）。

启用开关：`XKX_DIALOGUE_MODEL=1`（**显式 opt-in，默认关**；取值 `1/true/yes/on`）。
判定顺序：先看开关 → 关则一律模板（不解析 provider、不构造队列）；开则管理台优先、env 兜底，
两条都拿不到可用 provider 时同样一律模板。API 启动时需同时设置：

- `XKX_RUN=<run_id>`、`XKX_SERVICE_DB=<run>/service.sqlite`（供管理台 DB 解析）；
- 走管理台 DB 时还需 `XKX_SECRET_KEY`（与 8032 管理进程一致，用于解密 provider key）。

实际生效值（2026-11-07 实测）：8031 为 `XKX_DIALOGUE_MODEL=1` + 管理台 `local-gw`（`config_source=admin_registry`）；
8036 未开开关且无 provider，`talk.dialogue` 保持 `None`，能力位 `unconfigured`，`model_generated=0`。

## 回归命令

```bash
# 对话单测
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_agent_prompt.py tests/test_dialogue_demand_routing.py \
  tests/test_dialogue_guard_integration.py tests/test_dialogue_intents.py \
  tests/test_dialogue_routing.py tests/test_dialogue_variety.py tests/test_talk_action.py

# 真实 API 探针（先 enter 后逐轮 talk）
.venv/bin/python tools/dialogue_probe.py \
  --account qa_dialogue_04 --display 问话人丁 \
  --out reports/npc-dialogue-variety-after.json
```
