# delivery-p3g Release Notes

- 基线提交：`60e8596`（独立完整提交，已取代 `1c88cae` 过渡状态）
- 对外首发：是；`delivery-p3f`（`943645f`）仅内部里程碑
- 验证：`pytest 194 passed`、framework `6/6`、事件回放 `MATCH`（222 events，`e607d200…`）

## 1. 本版修了什么

### 1.1 新增 golden 基线，锁死 824 事件世界

`tests/test_p3e_golden.py` 锁死确定性 fake run 的事件总数、事件序哈希、最终 state hash 与经济流量。
任何调度/前置校验/行为改动导致世界漂移，测试立即 FAIL，不再允许“静默换世界”。

### 1.2 同 tick 调度只让 `talk` 先于 `move`

`SOCIAL_FIRST_VERBS` 从“talk/message/trade/sell/gift/quest_* /recruit”收敛为仅 `talk`，
消除无关动词的顺序依赖；这是 816→824 世界改写的直接原因。

### 1.3 `blocked` / `pruned` 全量审计

- `summary.json` 的 `blocked.records` / `pruned.records` 不再截断；
- run 目录额外落盘 `blocked.jsonl` / `pruned.jsonl`，可逐条对账；
- 本版证据：
  `reports/evidence/runs/p3e-fake-llm-2d-k6r2/{blocked,pruned}.jsonl`、
  `reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6r2/{blocked,pruned}.jsonl`。

### 1.4 修正失实描述

- 删除“录制 intent 重跑 = 确定性对照”的错误表述：重跑实际为 **220 events**（原 222）、
  `replay_miss=18`、`state=7a5ce1bd…`（原 `e607d200…`），只能做**事件日志回放**，不能承诺整跑复现。
- 明确 deepseek 新旧两次是**独立采样**，2 条 `blocked→pruned` 是换桶，不是行为改善。

## 2. 世界漂移：816 → 824

确定性 fake run 被改写并重新基线：

| 项 | 旧 | 新 |
| --- | --- | --- |
| 事件总数 | 816 | **824** |
| 最终 state | `b1a7edaf…` | **`1bd870c4…`** |
| sell 流入 | 1165 | **849** |
| buy | 956 | **795** |
| burned | 112 | **113** |
| 商户现金 | 11925 | **12058** |

> 后续任何行为变更都必须同步更新 `tests/test_p3e_golden.py` 基线并重新出包；本包数字不可外推到新改动。

## 3. 对外 K6 口径

对外结论统一使用**含剪枝口径**：`(rejected + blocked + pruned) / events`。

| 口径 | fake 2 天 | deepseek 2 天 | 对外 |
| --- | --- | --- | --- |
| 含剪枝（对外结论） | **7/824 = 0.85%** PASS | **2/222 = 0.90%** PASS | 是 |
| 严格（仅分解） | 2/824 = 0.24% | 0/222 = 0.00% | 否 |

- 把提交前就判不可行、连 `blocked` 都不记的提案计入分子，避免“换桶即达标”。
- deepseek 的 2 条新 `pruned` 与旧 `blocked` 是同一批意图（同主体/同目标/同 tick），**不构成行为改善**。
- K1 仍是 **7/91 = 7.69%（FAIL）**；`dialogue` 槽仍是 **0 次调用**，文笔未评测。

## 4. 交付验证

- 干净解包：从本包 zip 解到临时目录，首次 `./start.sh` 自动建 venv、装 `requirements.lock`、生成 2 日种子世界并拉起 API+前端；
- `selfcheck.sh` 4/4 PASS，`pytest 194 passed`，framework 6/6；
- 玩家闭环：enter → state → move → talk → final（接任务）全部 ok；
- 断线：停 API 后前端仍 200，`/api/health` 返回 `mode=degraded, upstream_ok=false`，页面提示“上游不可达”；
- 包内确认不含 `.venv`、`runs`、真实 key；包含 `tests/test_p3e_golden.py` 与 evidence 的 `blocked.jsonl` / `pruned.jsonl`。

详细限制见 [known-limitations.md](known-limitations.md)，实测记录见 [DELIVERY-TEST-p3g.md](DELIVERY-TEST-p3g.md)。
