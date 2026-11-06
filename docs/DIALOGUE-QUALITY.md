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
   - `runs/<run>/dialogue.jsonl`（审计日志）；
   - 本次 HTTP 响应的 `details.reply`（进程内缓存覆盖，`details.dialogue_source="model"`）。
3. 模型台词不写 `content_hash`，不参与目标选择、关系增量、经济与事件回放。
4. 回放同一 `(npc, message, tick, turn)` 永远得到同一模板句；`turn` 由 NPC memory 的 `talk:npc` 条数推导，是持久化确定值，不新增世界状态字段。

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

期望：模型台词计数为 0；`grep -c` 只在 `dialogue.jsonl` 命中。

## 模型配置（两条路都支持）

`dialogue_runtime.build_queue` 的解析顺序：

1. 管理台 provider 注册表：`wsim.admin.providers.resolve("dialogue")`（8032 注册，DB 优先）；
2. 环境变量 / `config/llm.yaml`：`ProviderConfig.from_env`（`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`）。

启用开关：`WSIM_DIALOGUE=1`。API 启动时需同时设置：

- `XKX_RUN=<run_id>`、`XKX_SERVICE_DB=<run>/service.sqlite`（供管理台 DB 解析）；
- 走管理台 DB 时还需 `XKX_SECRET_KEY`（与 8032 管理进程一致，用于解密 provider key）。

若两条路都拿不到可用配置，`talk.dialogue` 保持 `None`，能力位显示 `unconfigured`，全部走模板；报告必须如实标注 `model_generated=0`。

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
