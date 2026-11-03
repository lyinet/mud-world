# 交付证据（P0-4）

本文件固定 P3-D / P3-E 的关键运行产物路径。`runs/` 不保证随仓库分发，
下列文件是从真实运行复制进来的**不可变证据副本**，复现命令直接引用它们。

## 1. P3-D Xing 真实 2 天（意图槽真实、决策槽假端点）

| 文件 | 说明 |
|---|---|
| [reports/evidence/p3d-xing-real-2d-12npc-summary.json](../reports/evidence/p3d-xing-real-2d-12npc-summary.json) | 真实 Xing 2 天 / seed 42 / 12 NPC 的 `summary.json` |
| [reports/evidence/p3d-xing-stub-2d-12npc-summary.json](../reports/evidence/p3d-xing-stub-2d-12npc-summary.json) | 同配置、意图槽换回假端点的对照 |
| [reports/evidence/p3d-xing-intent-recordings-2d-final.jsonl](../reports/evidence/p3d-xing-intent-recordings-2d-final.jsonl) | 每次意图调用的录制（`prompt_sha` / 返回 / usage / 延迟） |

关键哈希（来自真实 summary）：

- `state_final = c788434f32faa6f6d8afd7430d1878d606d16d44f25b76cce3c813e68e5fb471`
- `event_final = 4f06496edb064919c8cfc558915d0b2cd957b7bcf07da31d91c7f7c52575bee8`
- `events = 208`、`K1 = 7/91`、`conservation_error = 0`、`replay = MATCH`

## 2. 复现命令（文件名已与仓库内证据一致）

```bash
# 确定性回放（不联网）
LLM_API_KEY=fake \
LLM_DECISION_BASE_URL=http://127.0.0.1:3427/v1 \
XING_API_KEY=fake \
WSIM_INTENT_REPLAY=reports/evidence/p3d-xing-intent-recordings-2d-final.jsonl \
.venv/bin/python sim/run_headless.py --days 2 --seed 42 --npc 12 --enable-llm \
  --out runs --run-id p3d-replay-2d-12npc

# 事件日志回放
.venv/bin/python tools/replay.py --run runs/p3d-real-2d-12npc   # MATCH
```

> 真实模型调用不可逐字复现；录制回放是验收口径。缺记录会报 `replay_miss`，不伪造返回。

## 3. 密钥

- 所有 key 只通过环境变量传入（`LLM_API_KEY` / `XING_API_KEY` 等），仓库内不保存明文。
- 历史报告里出现过的测试 key 已替换为 `<REDACTED>`；如该值为真实凭证，见仓库根 README 的轮换提示。

## 4. P3-E 证据（换模型 2 天 / 模板覆盖 / 走查）

| 文件 | 说明 |
|---|---|
| [reports/evidence/p3e-deepseek-real-2d-12npc-summary.json](../reports/evidence/p3e-deepseek-real-2d-12npc-summary.json) | deepseek 槽位 2 天 / seed 42 / 12 NPC 的 `summary.json` |
| [reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl](../reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl) | 该 run 19 次意图调用录制（`prompt_sha` / 返回 / usage / 延迟） |
| [reports/evidence/p3e-deepseek-intent-coverage.jsonl](../reports/evidence/p3e-deepseek-intent-coverage.jsonl) | P0-5 覆盖探针：17 模板 × 2 条真实意图调用录制（34 行） |
| [reports/evidence/p3e-intent-coverage.json](../reports/evidence/p3e-intent-coverage.json) | 按模板成功率汇总（含 slot 指纹与延迟） |
| [reports/evidence/p3e-intent-coverage.md](../reports/evidence/p3e-intent-coverage.md) | 同一数据的可读表格与失败样本清单 |

P3-E 2 天真实跑关键哈希：

- `state_final = 9129402e7b8a516705809aacf626b746fab659f8b3ab9067b0c6d1fe55ca647a`
- `event_final = 65fb7c2a506d6fcd1f6140fbb09bb90d8d17c5fc53b18829005ba312e64e4db7`
- `events = 222`、`rejected = 0`、`conservation_error = 0`、`replay = MATCH`

复现命令：

```bash
# 1) 事件日志回放（不联网）
.venv/bin/python tools/replay.py --run runs/p3e-deepseek-real-2d-12npc   # MATCH

# 2) 意图槽录制回放（不联网，确定性对照）
LLM_API_KEY=fake XING_API_KEY=fake \
WSIM_INTENT_REPLAY=reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl \
.venv/bin/python sim/run_headless.py --days 2 --seed 42 --npc 12 --enable-llm \
  --out runs --run-id p3e-replay-deepseek-2d-12npc

# 3) P0-5 模板覆盖探针（真实端点，key 只从环境变量读）
LLM_API_KEY=<你的 key> .venv/bin/python tools/intent_coverage.py
```

> 真实模型调用不可逐字复现；录制回放是验收口径。缺记录会报 `replay_miss`，不伪造返回。
> P3-E 与 P3-D（Xing）使用不同内容 hash 与不同端点，两份数据**不可直接比较**，只作并列观察。
