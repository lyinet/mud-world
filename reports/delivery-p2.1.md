# P2.1 交付报告（delivery-p2.1）

- 交付日期：2026-11-03
- 冻结 tag：`delivery-p2.1`
- tag 指向 commit：`33a51f5f8ef2f291ddf7357135d4498b821d8892`
- `content_hash`：`ff09d8a814ce6f514294c2952d043bf11fc10bdd2338f000509b3485e18b2c9d`
- 世界规模：31 NPC / 9 地点 / 31 物品 / 15 内容动词 / 4 门派
- 旧报告保留：[delivery-p2.md](delivery-p2.md)

本交付**不含真实模型接入**。默认推理器是确定性 `MockReasoner`；
`ProviderClient` / `LLMReasoner` 只是已接好的离线开关，未配置环境变量时不会联网。
与本轮前的差异：**`talk` 已落地**（记忆 + 关系写入事件日志），UI 从“提案”变为“已落地”。

---

## 0. 复核纠错（文档口径）

| 项 | 旧写法 | 修正 |
|---|---|---|
| 人设章节 | “60 个章节（30 卡 × 6 节）” | **180 个章节（30 卡 × 6 节）**；`>` 台词 60 条 |
| 决策数 | “决策 931” | `decisions_total = 931` / `model_calls = 754` / `replan = 177` |
| replan 成本 | 未说明 | replan 只做本地重规划，**不建 prompt、不记成本**，所以 931 ≠ 931 次付费调用 |
| 护栏归因 | “止血无效” | 护栏期余额企稳（**−1.25 文/天** vs 前 **−533 文/天**）；主因是工资流出 **866 → 182 文/天**（`work` 38.6 → 8.8）；护栏暂停采购反而让流入 **239 → 30 文/天**，方向为负 |

931 是旧 run 的口径；P2.1 的 72d 实际为 `decisions_total = 959` / `model_calls = 779` /
`replan = 180`，同样满足 `decisions_total = model_calls + replan`。

---

## 1. 运行方式

```bash
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --run-id delivery-72d
python3 sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id delivery-10d
python3 tools/replay.py --run runs/delivery-72d
python3 tools/replay.py --run runs/delivery-10d
python3 tools/framework_check.py
python3 -m pytest -q
python3 ui/server.py --run runs/delivery-72d --port 8000
```

---

## 2. `talk` 真实执行器

### 改动

- 新增 [dialogue.py](../src/wsim/agent/dialogue.py)：`extract_persona_voice()` 从人设卡拉
  `>` 台词与「遇事反应倾向 / 说话风格」；`compose_talk()` 用
  `sha256(npc|message|tick)` 确定性选台词，回复正文**必须包含台词原文**。
- 新增 `TalkAction`（[social.py](../src/wsim/actions/social.py)）：
  - `memory_add` 两条：玩家原话 `source=talk:player`、NPC 回话 `source=talk:npc`，带 tick；
  - `relation_upsert`：`kind=acquaintance`，新关系 `trust=40+Δ`；
  - 上限：单 tick 好感/信任各 **±2**，单日各 **±8**（`talk_ledger` 记录，跨 tick/跨日重置）；
  - **零钱变动**：不产生 `mint/burn/transfer`；
  - 玩家实体 `player:visitor` 惰性创建（无钱包）。
- [state.py](../src/wsim/world/state.py) 新增 `talk_ledger`；[effects.py](../src/wsim/world/effects.py)
  新增 `talk_ledger` op，并让 `memory_add` 支持 `source`。
- [registry.py](../src/wsim/actions/registry.py) 注册 `talk`；[verbs.yaml](../content/verbs.yaml)
  声明 `talk`（15 个内容动词）。
- UI（[adapter.py](../ui/adapter.py) / [index.html](../ui/index.html)）：
  `propose_intent → admin.request → admin.approve → admin.apply`；
  复制 run 的 `events.sqlite` 到临时 session，绑定 Writer；原 run 不被修改。
  页面显示“已落地”、事件 `seq`、`outcome`、记忆条数、关系变化、审批链审计。

### 前后数字（对 `delivery-72d` 实际跑两次对话，同一 tick=10367）

| 指标 | 前（提案 Only） | 后（P2.1 落地） |
|---|---|---|
| 对话落地数 | 0（只进 outbox） | **2 / 2** |
| 事件 seq | — | **12778、12779** |
| 记忆条数 | 0 | **4 条**（每轮 2 条：玩家话 + NPC 回话） |
| 关系变化（2 轮） | 无 | affection **0 → 2.0**、trust **40 → 41.5** |
| 单轮上限实测 | — | 第 1 轮 affection +2、trust +1；第 2 轮 affection **0**（同 tick 已用满 ±2），trust +0.5 |
| 硬上限 | — | ±2/tick、±8/日（`talk_ledger`） |
| 钱变动 | 0 | **0**（账户总额与 minted 不变） |
| 守恒误差 | 0 | **0** |
| 回放 | — | MATCH（回应文本与 Effect 都在事件里） |

第 2 轮 affection 被压回 0 不是 bug，而是**单 tick ±2 上限真的生效**：两轮都在
`tick=10367`，第 1 轮已用满 +2，第 2 轮只能记 0；trust 仍在 ±2/tick 内，所以 +0.5 放行。

测试：[test_talk_action.py](../tests/test_talk_action.py) 5 条（台词消费、记忆 source、
±2/±8 上限、守恒 0、审批链落地）。

---

## 3. 任务板健康度

### 改动

- [economy.yaml](../content/economy.yaml)：`expired_cap_per_template=3`、
  `expired_cap_global=20`、`min_samples_for_rate=10`、`min_completion_rate=0.2`、
  `expired_retention_days=7`。
- 发单预检（[quests.py](../src/wsim/actions/quests.py)）：giver 钱包不足、giver 物品不足、
  模板历史完成率 <20%（样本 ≥10）、模板 retained expired ≥3、全局 retained expired ≥20
  → **不发新单**（不产生 intent，不计入拒绝率）。
- `system_quest_fail` 每晚对过期/失败超过 7 天的任务发 `quest_prune`；`quest_stats`
  保留历史，不因清理回退。
- 状态新增 `quest_stats`、`quests_expired_total`；[headless.py](../src/wsim/sim/headless.py)
  输出 `quests.health`（active / expired_on_board / expired_total / 各模板完成率）。

### 前后数字（72d）

| 指标 | 前（delivery-p2） | 后（delivery-p2.1） |
|---|---|---|
| 活跃单 | 3 | **3** |
| 板上过期单 | **206** | **4**（↓ 98.1%） |
| 历史过期总数 | 206 | **75** |
| 事件拒绝数 / 拒绝率 | 184 / 1.4595% | **189 / 1.4792%**（仍 ≤2%，余量 0.52 点） |
| 任务完成 / 各模板累计失败 | 203 / 10 | **191 / 13** |
| 5 个 0 完成模板 | 各持续发单至 72d | 各 **created=10** 后停发（样本门槛），板上各 ≤3 张 |

各模板完成率（`quests.health.by_template`）：

| 模板 | created | completed | expired | failed | 完成率 |
|---|---|---|---|---|---|
| `inquiry_academy_lecture` | 70 | 69 | 1 | 0 | 98.57% |
| `procure_charcoal` | 68 | 66 | 0 | 2 | 97.06% |
| `errand_academy_notice` | 44 | 30 | 9 | 4 | 68.18% |
| `escort_iron_short` | 27 | 14 | 12 | 0 | 51.85% |
| `escort_medicine_temple` | 21 | 10 | 3 | 7 | 47.62% |
| `commission_herb_sorting` | 10 | 0 | 10 | 0 | 0% |
| `commission_stonework` | 10 | 0 | 10 | 0 | 0% |
| `errand_silk_sample` | 10 | 0 | 10 | 0 | 0% |
| `inquiry_luo_marriage` | 10 | 0 | 10 | 0 | 0% |
| `procure_ginseng` | 10 | 0 | 10 | 0 | 0% |
| `debt_duan_to_jin` | 1 | 1 | 0 | 0 | 100% |
| `debt_lu_to_song` | 1 | 1 | 0 | 0 | 100% |

测试：[test_quest_health.py](../tests/test_quest_health.py) 5 条（资金/完成率/模板上限/
全局上限/prune 保留统计）。

---

## 4. 金库收入侧杠杆

### 改动

- 交易税分流：`tax_split_effects()`（[common.py](../src/wsim/actions/common.py)），
  `taxes.transaction_treasury_share = 0.5`，50% `transfer` 到 `treasury:region`，50% `burn`；
  **Buy / Sell / Trade / Consume / Escort 五条交易税路径全部统一**（复核时发现
  Consume 与 Escort 仍是整笔 burn，已补齐）；**是账户间转移，不是 mint**，总额严格守恒。
- `open_shop` 缓冲：金库余额 ≥ `employer_reserve(2000) + 开店成本 + 7 日工资`
  才允许；不足时 Planner **延后**（不生成 intent），不计拒绝；
  `postponed_open_shop` 计入 summary。
- 报告新增 `treasury.tax_to_treasury`、`tax_burned`、`open_shop_postponed`、
  `treasury.guard`。

### 前后数字（72d）

| 指标 | 前（delivery-p2） | 后（delivery-p2.1） |
|---|---|---|
| 金库期末 | 2,009 | **2,083**（+74） |
| `treasury_guard` 触发天数 | 50 / 72 | **52 / 72**（第 21 天进入） |
| 交易税回流金库 | 0 | **86 文** |
| 交易税 burn | 全部 | **96 文**（total 182，回流占 47.3%） |
| 开店延后次数 | — | **72 次**（`open_shop_postponed`） |
| minted_total | 47,150 | **47,150（不变）** |
| 守恒误差 | 0 | **0** |

说明：182 文交易税里 86 文回到金库、96 文销毁，未到严格的 91/91。原因是有一部分
税源账户本身就是 `treasury:region`，这半边若发生 `treasury → treasury` 自转会被
`tax_split_effects` 折回 burn，避免无意义自转；取整不再造成损耗。税回流方向为正，
但体量仍小；金库企稳的主因是工资流出下降（866→182 文/天）。

测试：[test_treasury_revenue.py](../tests/test_treasury_revenue.py) 5 条（50/50 分流、
奇数金额守恒、Consume 税分流、`treasury_buffer` 前置、Planner 延后计数）。

---

## 5. P2.1 指标表

| 指标 | 10 天 | 72 天 |
|---|---|---|
| content_hash | `ff09d8a814ce…` | `ff09d8a814ce…` |
| event_final | `aa67d879f9da…` | `28d3c12a5268…` |
| state_final | `2f7d5eb13f2b…` | `51fb61e6ef26…` |
| state_day10 | `2f7d5eb13f2b…` | `2f7d5eb13f2b…`（与 10 天 run 一致） |
| 事件数 / 拒绝数 / 拒绝率 | 2,841 / 44 / **1.549%** | 12,777 / 189 / **1.4792%** |
| 决策 `decisions_total` | 238 | **959** |
| `model_calls` | 199 | **779** |
| `replan` | 39 | **180** |
| K1 全量（分母 91） | 45/91 = 49.45% | **68/91 = 74.73%** |
| K1 剔除一次性 | 36.11% | **66.67%** |
| K1 已创建口径（参考） | 66.18% | **80.00%** |
| K3 verb 层：类数 / Top-10 / 熵 | — | 17 / 99.47% / 2.5080 |
| K3 对象层 v1.1：类数 / Top-10 / 熵 | — | **99 / 60.43% / 5.0687** |
| NPC 现金期末 | 35,182 | **31,563** |
| 商业体（区域金库）期末 | 8,040 | **2,083** |
| 账户总额 / minted / burned | 45,830 / 47,150 / 1,320 | **42,842 / 47,150 / 4,308** |
| **守恒误差** | **0** | **0** |
| 工资 应发/实付/欠薪 | 9,564 / 9,564 / 0 | **27,640 / 27,640 / 0** |
| 任务 完成/累计失败/板上过期/活跃 | 30 / 1 / 13 / 0 | **191 / 13 / 4 / 3** |
| 镖单 发起/到站/完成 | 8 / 7 / 7 | **37 / 36 / 34** |
| 门派成立 | — | 第 3、20、61 天 |
| 成本 calls / persona_tokens / USD | 199 / 14,589 / 0.5306 | **779 / 57,588 / 1.9938** |
| `treasury_guard` 进入/退出/天数 | 0 / 0 / 0 | **1 / 0 / 52** |
| UI 对话 | 提案 | **已落地**（seq 12778/12779） |

---

## 6. 仍未达标 / 风险

1. **5 个模板 72d 0 完成**：`commission_herb_sorting`、`commission_stonework`、
   `errand_silk_sample`、`inquiry_luo_marriage`、`procure_ginseng`。
   本轮通过“10 次样本后停发 + 板上最多 3 张”把过期单从 206 压到 **4**，
   但**可接性问题本身未修**（距离/角色/目标地点/物品），列入 P3。
2. **K1 74.73% < 75% 目标**（仍 ≥60% 门槛，`k1_pass=true`；比 P2 的 78.02% 下降 3.29 点，
   原因是任务板收紧后完成类事件减少）。
3. **拒绝率 1.4792%**（P2 为 1.4595%），仍 ≤2% 门禁，余量 0.52 个点。
4. **交易税回流体量小**：72d 回流 86 文（税 total 182）；护栏期金库仍贴地，
   收入杠杆需要更大税基或任务拨款，不能靠 mint。
5. **人设卡 71–80 token/卡**，未达到 300–500 token 目标（代码就绪，内容未扩写）。
6. **真实模型未接入**；所有 LLM 路径默认关闭，成本为 Mock 估算。
7. **`talk` 只做玩家→NPC**；NPC→NPC 自动闲聊未接入 Planner。
8. UI 对话写入**临时 session**，不写回原 run；原 run 的哈希与事件保持不变。

---

## 7. 从 tag 干净重跑

```bash
git worktree add --detach /tmp/wsim-p21b delivery-p2.1
cd /tmp/wsim-p21b
python3 sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id delivery-10d
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --run-id delivery-72d
python3 tools/replay.py --run runs/delivery-10d
python3 tools/replay.py --run runs/delivery-72d
python3 tools/framework_check.py
python3 -m pytest -q
```

```
delivery-10d: events=2841
replayed=2f7d5eb13f2bd3069cb7d3977c4460f6de920dca6af4297216476ea734198512
stored  =2f7d5eb13f2bd3069cb7d3977c4460f6de920dca6af4297216476ea734198512
MATCH

delivery-72d: events=12777
replayed=51fb61e6ef264b9cb24eb88375fc41e0bdcc74abdaa739238861cfca36f782ec
stored  =51fb61e6ef264b9cb24eb88375fc41e0bdcc74abdaa739238861cfca36f782ec
MATCH

FRAMEWORK CHECK: PASS   （5/5；10 天拒绝率 1.55% < 2%）
python3 -m pytest -q    （87 passed，exit 0）
```

两份 run 的 `state_day10` 与 10 天 run 的 `state_final` 完全一致。

---

## 8. P3 需要什么

```bash
export LLM_BASE_URL="https://<endpoint>/v1"
export LLM_API_KEY="<key>"
export LLM_MODEL="<model-name>"
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --run-id p3-llm --enable-llm
```

其他前置：

- 5 个 0 完成模板的可接性修复（距离/角色/目标地点/物品）；
- 更大的金库收入杠杆（税基/任务拨款/工资标尺），禁止 mint；
- 人设卡扩写到 300–500 token/卡；
- `talk` 接入 NPC→NPC Planner（本轮只做玩家入口）；
- 真实 LLM 的 schema 拒绝率、降级率与成本回归。
