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
| [reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6r2/](../reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6r2/) | 最新一次独立采样的验收副本（`events.sqlite` / `world_hash.txt` / `summary.json` / `pruned.jsonl` / `blocked.jsonl`），干净 clone 可直接回放 |
| [reports/evidence/p3e-deepseek-real-2d-12npc-k6r2-summary.json](../reports/evidence/p3e-deepseek-real-2d-12npc-k6r2-summary.json) | 该 run 的 `summary.json`（严格 0.00%、含剪枝 0.90%） |
| [reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6fix/](../reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6fix/) | 前一次独立采样（保留供采样波动对照） |
| [reports/evidence/runs/p3e-deepseek-real-2d-12npc/](../reports/evidence/runs/p3e-deepseek-real-2d-12npc/) | K6 修复前的旧验收副本（严格 0.90%） |
| [reports/evidence/p3e-deepseek-real-2d-12npc-summary.json](../reports/evidence/p3e-deepseek-real-2d-12npc-summary.json) | 旧 run 的 `summary.json`（修复前基线） |
| [reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl](../reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl) | 该 run 19 次意图调用录制（`prompt_sha` / 返回 / usage / 延迟） |
| [reports/evidence/p3e-deepseek-intent-coverage.jsonl](../reports/evidence/p3e-deepseek-intent-coverage.jsonl) | P0-5 覆盖探针：17 模板 × 2 条真实意图调用录制（34 行） |
| [reports/evidence/p3e-intent-coverage.json](../reports/evidence/p3e-intent-coverage.json) | 按模板成功率汇总（含 slot 指纹与延迟） |
| [reports/evidence/p3e-intent-coverage.md](../reports/evidence/p3e-intent-coverage.md) | 同一数据的可读表格与失败样本清单 |
| [reports/evidence/p3e-fake-llm-2d-summary-k6r2.json](../reports/evidence/p3e-fake-llm-2d-summary-k6r2.json) | 2 天假端点 run 的 `summary.json`（golden 基线） |
| [reports/evidence/runs/p3e-fake-llm-2d-k6r2/](../reports/evidence/runs/p3e-fake-llm-2d-k6r2/) | 假端点 run 副本（`events.sqlite` + 全量拦截记录），供逐条对账 |
| [reports/evidence/p3e-fake-llm-2d-pruned-k6r2.jsonl](../reports/evidence/p3e-fake-llm-2d-pruned-k6r2.jsonl) | 提案前剪枝全量记录（不截断） |
| [reports/evidence/p3e-fake-llm-2d-blocked-k6r2.jsonl](../reports/evidence/p3e-fake-llm-2d-blocked-k6r2.jsonl) | 提案前拦截全量记录（不截断） |
| [reports/evidence/p3e-deepseek-real-2d-12npc-k6r2-pruned.jsonl](../reports/evidence/p3e-deepseek-real-2d-12npc-k6r2-pruned.jsonl) | deepseek run 的剪枝全量记录 |
| [reports/evidence/p3e-fake-llm-2d-summary.json](../reports/evidence/p3e-fake-llm-2d-summary.json) | 修复前假端点 run 的 `summary.json`（基线：816 events / 27 blocked） |
| [reports/evidence/p3e-partial-replay-2d-summary.json](../reports/evidence/p3e-partial-replay-2d-summary.json) | 仅 intent 录制重跑的负面对照（220 events / 18 次 miss） |
| [reports/evidence/p3e-report-stats.md](../reports/evidence/p3e-report-stats.md) | 报告全部数字的脚本生成结果（勿手写） |

P3-E 2 天真实跑关键哈希（最新一次独立采样）：

- `state_final = e607d2008e8c53890ab2d30478e09e383d0020bd0f7715a1b81ad51c6eb6767e`
- `event_final = 9a5f070a31857368fe12631362e236796719435b00e84b6fd480a2fbdd0efc54`
- `events = 222`、`rejected = 0`、`precondition_blocked = 0`、`precondition_pruned = 2`、
  严格非法率 `0.00%`、含剪枝 `0.90%`、`conservation_error = 0`
- **采样说明**：该 run 与 `-k6fix`、`-12npc` 是三次独立模型采样，不是受控前后对照；
  tick 0 的 talk 文案与 state hash 会变，但经济聚合一致（sell 69 / buy 342 / burned 57 / 商户现金 11564）。

复现命令（干净归档中没有 `.venv`，先按 README 安装依赖；以下用本机现有 `.venv`）：

```bash
# 1) 事件日志回放（入库的证据 run；只证明 effect 引擎可重建同一状态）
PYTHONPATH=src .venv/bin/python tools/replay.py \
  --run reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6r2
# 实测：events=222；replayed=stored=e607d200…；MATCH

# 2) 仅 intent 槽录制重跑（不是整跑复现；decision 槽未录制）
LLM_API_KEY=fake XING_API_KEY=fake \
WSIM_INTENT_REPLAY=reports/evidence/p3e-deepseek-intent-recordings-2d.jsonl \
.venv/bin/python sim/run_headless.py --days 2 --seed 42 --npc 12 --enable-llm \
  --out runs --run-id p3e-replay-deepseek-2d-12npc
# 实测：events=220（原 222）；state=7a5ce1bd…（原 e607d200…）；
#       event=a35cd8bd…（原 9a5f070a…）；replay_miss=18；intent_reject=19；pruned=2

# 3) P0-5 模板覆盖探针（真实端点，key 只从环境变量读）
LLM_API_KEY=<你的 key> .venv/bin/python tools/intent_coverage.py

# 4) 报告数字复算（覆盖/归因/K6/换模型披露）
PYTHONPATH=src .venv/bin/python tools/p3e_report_stats.py
```

> 真实模型调用不可逐字复现。P3-E 只录了 intent 槽，decision 槽未录制；
> 第 2 条命令**不能复现原 run**，只用于证明缺记录会报 `replay_miss`，不伪造返回。
> P3-E 与 P3-D（Xing）使用不同内容 hash 与不同端点，两份数据**不可直接比较**，只作并列观察。

## 5. 截图与重跑产物（2026-11-06 入库口径）

- **历史已入库的截图/采样是冻结证据**：`reports/w4-*/**.png`、`reports/w5-sect/*.png`、
  `reports/evidence/**/world_hash.txt` 等已入库文件保持跟踪、**不再随重跑更新**。
  其中 `world_hash.txt` / `event_hash.txt` 是“干净 clone 可直接回放/可复现”的证据，见
  第 4 节的 run 目录与 [specs/delivery-p3h/spec.md](../specs/delivery-p3h/spec.md)。
- 2026-11-06 已把被重跑覆盖的 43 个截图/键盘采样（42 png + `reports/w4-ui/keyboard.txt`）
  用 `git rm --cached` 移出索引，**本地文件保留**、由 `.gitignore` 忽略；不改写历史。
- **重跑产出一律不入库**：`reports/**/*.json`、`reports/**/*.md` 才是入库证据；
  png/jpg/jpeg/webp、log/txt/jsonl、`_probe/` 都是一次性产物，可由报告 JSON 与脚本复现。
- **自检**：`git status --short reports/` 应始终为空——被跟踪文件无未提交修改，
  未跟踪产物已按口径忽略。若出现新的 png/log/txt 条目，说明有人绕过了 `rm --cached`
  或改动了已冻结证据，按本节口径处理，不要把它们提交回索引。
