# 交付证据（P0-4）

本文件固定 P3-D / P3-E 的关键运行产物路径。`runs/` 不随仓库分发，
下列文件是从真实运行复制进来的**不可变证据副本**，复现命令直接引用它们；
P3-E 的验收 run 目录本身也已在 `reports/evidence/runs/` 入库。

**两种复现必须分开说**：

- **事件日志回放**：`tools/replay.py` 从 `events.sqlite` 重新套 effect，只证明 effect 引擎
  能把同一事件日志重建为同一 `state hash`。
- **整跑复现**：同 seed + 同输入再跑一遍并得到同一世界。P3-E 只录制了 intent 槽，
  decision 槽未录制，**整跑不可复现**，不得把“录制回放”当作整跑确定性验收。

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

## 2. P3-D 复现口径

```bash
# 仅 intent 槽录制重跑：产出的是新 run，不是 p3d-real 的复现
LLM_API_KEY=fake \
LLM_DECISION_BASE_URL=http://127.0.0.1:3427/v1 \
XING_API_KEY=fake \
WSIM_INTENT_REPLAY=reports/evidence/p3d-xing-intent-recordings-2d-final.jsonl \
.venv/bin/python sim/run_headless.py --days 2 --seed 42 --npc 12 --enable-llm \
  --out runs --run-id p3d-replay-2d-12npc

# 事件日志回放（需要本机保留 runs/p3d-real-2d-12npc；干净 clone 不含该 run）
.venv/bin/python tools/replay.py --run runs/p3d-real-2d-12npc   # MATCH
```

> P3-D 只录制 intent 槽，decision 槽未录制；上面前一条命令**不是整跑复现**。
> 缺记录会报 `replay_miss`，不伪造返回。

## 3. 密钥

- 所有 key 只通过环境变量传入（`LLM_API_KEY` / `XING_API_KEY` 等），仓库内不保存明文。
- 历史报告里出现过的测试 key 已替换为 `<REDACTED>`；如该值为真实凭证，见仓库根 README 的轮换提示。

## 4. P3-E 证据（换模型 2 天 / 模板覆盖 / 走查）

| 文件 | 说明 |
|---|---|
| [reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6fix/](../reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6fix/) | K6 收口后的验收副本（`events.sqlite` / `world_hash.txt` / `summary.json`），干净 clone 可直接回放 |
| [reports/evidence/p3e-deepseek-real-2d-12npc-k6fix-summary.json](../reports/evidence/p3e-deepseek-real-2d-12npc-k6fix-summary.json) | 该 run 的 `summary.json`（严格非法率 0.00%、含剪枝 0.90%） |
| [reports/evidence/runs/p3e-deepseek-real-2d-12npc/](../reports/evidence/runs/p3e-deepseek-real-2d-12npc/) | K6 修复前的旧验收副本（保留供对照，严格非法率 0.90%） |
| [reports/evidence/p3e-deepseek-real-2d-12npc-summary.json](../reports/evidence/p3e-deepseek-real-2d-12npc-summary.json) | 旧 run 的 `summary.json`（修复前基线） |
| [reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl](../reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl) | 该 run 19 次意图调用录制（`prompt_sha` / 返回 / usage / 延迟） |
| [reports/evidence/p3e-deepseek-intent-coverage.jsonl](../reports/evidence/p3e-deepseek-intent-coverage.jsonl) | P0-5 覆盖探针：17 模板 × 2 条真实意图调用录制（34 行） |
| [reports/evidence/p3e-intent-coverage.json](../reports/evidence/p3e-intent-coverage.json) | 按模板成功率汇总（含 slot 指纹与延迟） |
| [reports/evidence/p3e-intent-coverage.md](../reports/evidence/p3e-intent-coverage.md) | 同一数据的可读表格与失败样本清单 |
| [reports/evidence/p3e-fake-llm-2d-summary-k6fix.json](../reports/evidence/p3e-fake-llm-2d-summary-k6fix.json) | 2 天假端点 run 的 `summary.json`（修复后；K6 三种口径复算输入） |
| [reports/evidence/p3e-fake-llm-2d-summary.json](../reports/evidence/p3e-fake-llm-2d-summary.json) | 修复前假端点 run 的 `summary.json`（基线：27 条 blocked） |
| [reports/evidence/p3e-partial-replay-2d-summary.json](../reports/evidence/p3e-partial-replay-2d-summary.json) | 仅 intent 录制重跑的负面对照（220 events / 18 次 miss） |
| [reports/evidence/p3e-report-stats.md](../reports/evidence/p3e-report-stats.md) | 报告全部数字的脚本生成结果（勿手写） |

P3-E 2 天真实跑关键哈希（K6 收口版）：

- `state_final = 28b733facdcf8044b75f74fc0568ffd4da642579b3f85ec388dd98adb809f452`
- `event_final = b327360550d00c48f430045d4d353e48bd77439f62477cee22b36357f130c6f5`
- `events = 222`、`rejected = 0`、`precondition_blocked = 0`、`precondition_pruned = 2`、
  严格非法率 `0.00%`、含剪枝 `0.90%`、`conservation_error = 0`

复现命令：

```bash
# 1) 事件日志回放（入库的证据 run；只证明 effect 引擎可重建同一状态）
PYTHONPATH=src .venv/bin/python tools/replay.py \
  --run reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6fix
# 实测：events=222；replayed=stored=28b733fa…；MATCH

# 2) 仅 intent 槽录制重跑（不是整跑复现；decision 槽未录制）
LLM_API_KEY=fake XING_API_KEY=fake \
WSIM_INTENT_REPLAY=reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl \
.venv/bin/python sim/run_headless.py --days 2 --seed 42 --npc 12 --enable-llm \
  --out runs --run-id p3e-replay-deepseek-2d-12npc
# 实测：events=220（原 222）；state=7a5ce1bd…（原 28b733fa…）；
#       event=a35cd8bd…（原 b3273605…）；replay_miss=18；intent_reject=19；pruned=2

# 3) P0-5 模板覆盖探针（真实端点，key 只从环境变量读）
LLM_API_KEY=<你的 key> .venv/bin/python tools/intent_coverage.py

# 4) 报告数字复算（覆盖/归因/K6/换模型披露）
PYTHONPATH=src .venv/bin/python tools/p3e_report_stats.py
```

> 真实模型调用不可逐字复现。P3-E 只录了 intent 槽，decision 槽未录制；
> 第 2 条命令**不能复现原 run**，只用于证明缺记录会报 `replay_miss`，不伪造返回。
> P3-E 与 P3-D（Xing）使用不同内容 hash 与不同端点，两份数据**不可直接比较**，只作并列观察。
