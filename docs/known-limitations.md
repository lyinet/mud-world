# 已知限制（首发交付，不美化）

> **过渡版本说明**：`delivery-p3f`（`943645f`）含他人在途的 `headless.py` 改动，仅作内部里程碑，**不作对外最终版**；对外以后续 `delivery-p3g` 为准。

本文只记录**本次交付能证实**的范围。任何未达标项都按 FAIL / 未验证列出，不用“基本可用”“已优化”之类措辞。

## 1. 硬指标：未达标项

| 项 | 实测 | 判定 | 证据 |
| --- | --- | --- | --- |
| K1 自主目标推进率 | **7/91 = 7.69%** | **FAIL** | [p3e-report-stats.md](../reports/evidence/p3e-report-stats.md) §2；2 天 / 12 NPC / seed 42 / deepseek 端点 |
| `dialogue` 槽调用 | **0 次** | **未验证** | 同上 §2：`LLM 调用 {'decision': 19, 'intent': 19}`，没有 dialogue；文笔/角色扮演质量未评测 |
| 整跑级复现 | 只保证**事件日志回放** | **未承诺** | `tools/replay.py` 对同一 `events.jsonl` 回放 **MATCH**；用录制 intent 重跑得到 220 事件（原 222）、`replay_miss=18`，状态不同 |
| K6 严格口径（违规尝试） | **2/824 = 0.24%** | PASS（本次口径） | fake 2 天 run；`rejected+blocked / attempts` |
| K6 含剪枝口径 | **7/824 = 0.85%** | PASS（本次口径） | fake 2 天 run；额外计入 `pruned` |

补充披露（同一份证据）：

- deepseek 真实 2 天：K6 严格 **0/222 = 0.00%**、含剪枝 **2/222 = 0.90%**；但动作来源 model **0.7%** / rule **99.3%**，主要仍是规则驱动，不能当作模型质量验收。
- K1 的 7.69% 是“2 天长窗口、无玩家在场”的真实进度，不是模型能力结论。72 天无玩家 run 的 75.56% 未进入本次首发表，不能拿来替代 7.69%。
- 模板覆盖只到 **25/34 = 73.53%**（17 模板），`修炼`、`开店` 等仍有失败项；详见 [p3e-report-stats.md](../reports/evidence/p3e-report-stats.md) §1。

### 复现命令

```bash
# 指标汇总
PYTHONPATH=src .venv/bin/python tools/p3e_report_stats.py

# 事件日志回放（应输出 MATCH）
PYTHONPATH=src .venv/bin/python tools/replay.py \
  --run reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6fix
```

## 2. 玩家侧限制（本次实测确认）

1. **实时会话是动作驱动的**：`wsim.api` 只在玩家动作后推进 6 tick；没有后台 NPC 调度器。因此进入后静静等待，世界时钟不会自己走，NPC 也不会在实时会话里自主移动。
   - 可观察的 NPC 连续自主行动来自首次运行生成的 2 日种子 run（`runs/quickstart-2d`），用 `scripts/world_feed.py` 查看；它是只读回放，不与你的实时会话同步。
   - 状态里的 `present_npcs[*].doing` 当前恒为空字符串，不要把它当成 NPC 正在做的事。
2. **默认对话不是大模型生成**：离线默认走规则/人格模板，回复稳定、可复现，但不代表 LLM dialogue 槽可用；真实 `dialogue` 槽在 2 天 run 里 0 次调用（见 §1）。
3. **单区域 + 无鉴权**：一个 API 实例只跑一个区域；`account_id` 是纯字符串，没有密码/令牌。**不要把 8020/8000 直接暴露到公网**。
4. **容量上限**：单区域 200 个玩家席位；初始资产累计上限 60000 文。超限返回 `player_cap_reached` / `endowment_cap_reached`。
5. **存档语义**：`wsim.api` 每次启动都从 genesis 新开世界，不 replay 旧 `events.sqlite`；`demo.sh` 检测到旧 `player-demo` 会自动换新目录，不静默追加。事件日志可用 `tools/replay.py` 重建状态，但**不保证重新跑一遍得到同一事件序列**。
6. **断线提示延迟**：前端每 10 秒轮询 `/api/health`，引擎掉线后最多约 10 秒显示“上游不可达”；不是 WebSocket 实时推送。
7. **只验证 macOS**：本次端到端在 macOS + Python 3.12.7 上跑通；Linux 理论可用，未做首发实测。Windows WSL 未验证。

## 3. 交付包里没有的东西

- 没有真实模型 key，也没有把任何 `.env`/密钥打进包；
- 没有 72 天全量 run、没有 GPU/向量库依赖；
- 没有账号体系、支付、跨区域迁移、后台管理界面；
- 没有“NPC 在实时会话中持续自主行动”的调度能力（见 §2.1）。

## 4. 仍然成立的能力边界

- 事件日志回放可重建同一状态（`MATCH`）；
- K6 严格口径 0.24% / 含剪枝 0.85% 在 fake 2 天 run 下 PASS；
- 玩家闭环（进入 → 移动 → 对话 → 接任务 → 刷新保留）已在本次干净解包环境实测通过；
- `selfcheck.sh` 4/4 PASS、`pytest` 193 passed。
