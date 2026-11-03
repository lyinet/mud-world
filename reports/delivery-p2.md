# P2 交付报告（delivery-p2）

- 交付日期：2026-11-03
- 冻结 tag：`delivery-p2`
- tag 指向 commit：`7137e629b7d5ed4c41d7f4f57d63597ed4cd916f`
- `content_hash`：`420ee2ff307bb7539c1ae1b20606faa0a864aaf5975a4b982d764bb82730fb26`
- 世界规模：31 NPC / 9 地点 / 31 物品 / 14 内容动词 / 4 门派

本交付**不含真实模型接入**。默认推理器是确定性 `MockReasoner`；
`ProviderClient` / `LLMReasoner` 只是已接好的离线开关，未配置环境变量时不会联网。
UI 是**离线 run 快照 + 提案**，`talk` 不落地（提案只进 `gateway.outbox`，不绑定 Writer，
不推进世界）。

---

## 1. 运行方式（三条命令）

### 1.1 模拟

```bash
# 72 天全量
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --run-id delivery-72d

# 10 天
python3 sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id delivery-10d
```

输出目录 `runs/<run-id>/`：`events.sqlite` + `events.jsonl`（唯一事实源）、
`summary.json`、`daily.jsonl`、`cost.jsonl`、`decisions.jsonl`、`goal_changes.jsonl`、
`lod_tiers.jsonl`、`k3_details.json`、`wage_trace.json`、`run.log`。

### 1.2 验收

```bash
python3 tools/replay.py --run runs/delivery-72d   # 期望 MATCH
python3 tools/replay.py --run runs/delivery-10d   # 期望 MATCH
python3 tools/framework_check.py                  # 期望 5/5 PASS
python3 -m pytest -q                              # 期望 72 passed
```

### 1.3 UI（离线快照 + 提案）

```bash
python3 ui/server.py --run runs/delivery-72d --port 8000
# 浏览器打开 http://127.0.0.1:8000/
```

启动时会从 `events.sqlite` 回放（本次日志：`12607 条事件，hash_ok=True`）。
`POST /api/dialogue` 只返回“提案”，`state_unchanged=true`，无 `talk` 执行器。

---

## 2. 本轮三件事

### 2.1 人设完整注入（C8 遗留）

**改动**

- [content.py](../src/wsim/core/content.py)：`content/personas/*.md` 从“首行 ≤160 字摘要”
  改为**整卡读入**。
- [prompt.py](../src/wsim/agent/prompt.py)：新增 `compose_persona()`，按章节优先级
  `一句话性格 > 说话风格与台词 > 在意的东西 > 遇事反应倾向 > 日常锚点 > 张力`
  压进预算；默认预算 `PERSONA_TOKEN_BUDGET = 450`（约 300–500 区间中部）；
  超预算才截断并加省略号，单章节超长按字符截断。
- [cost.py](../src/wsim/agent/cost.py)：`cost.jsonl` 每条记录新增 `persona_tokens`，
  `summary` 顶层与 `by_tier` 都累加该项。
- 遥测接线：[intent.py](../src/wsim/sim/intent.py)（Mock）与
  [provider.py](../src/wsim/agent/provider.py)（LLM）都写入 `persona_tokens`。

**数字**

| 项目 | 数值 |
|---|---|
| 人设卡数量 | 30 |
| 单卡原始 token（min/median/max） | 71 / 75 / 80 |
| 注入后 token（min/median/max） | 69 / 73.5 / 79 |
| 触发预算截断的卡 | 0（80 << 450） |
| 60 个章节（30 卡 × 6 节）全部在场 | 是 |
| 全部 `>` 台词原文注入 | 是（30/30） |
| 72d `persona_tokens` 合计 | 55,921（754 次决策，均值 74.2/次） |
| 10d `persona_tokens` 合计 | 14,589（199 次决策） |

**测试（证明台词原文确实在 prompt 里）**

`tests/test_agent_prompt.py::test_persona_full_card_keeps_dialogue_within_token_budget`
取 `bai_lusheng` 卡里所有 `>` 台词，断言 `any(q in prompt.user for q in quotes)`；
同时断言靠后章节“遇事反应倾向”“与谁有张力”在场、`persona_tokens <= 450`。

另两条：
`test_compose_persona_truncates_by_section_priority`（造一张超预算卡，验证低优先级
“与谁有张力”被挤掉、台词仍保留、以“…”结尾）；
`test_cost_telemetry_records_persona_tokens`（验证 `cost.jsonl` 与 summary 写入）。

**成本影响（如实报）**

- 72d 成本 814 → 754 次调用（少了 60 次），但估算费用 1.7217 → **1.9867 USD**（+15.4%）。
  原因是每次决策都多带 ~74 token 人设。

**未达标**

- 卡片本身只有 71–80 token/张，远低于“300–500 token/卡”的目标。
  代码侧的预算与优先级已就绪，但要把单卡做到 300–500 token 需要**扩写内容卡**，
  不是代码问题；本轮未扩写。

### 2.2 区域金库保底告警与自动刹车（`treasury_guard`）

**定义**

- 金库账户：`treasury:region`（创世时所有商业体共用该 `cash_account`）。
- 保底线：`work.employer_reserve = 2000`。
- 阈值：`2000 × work.treasury_guard_multiplier(1.2) = 2400` 文。
- 判定点：每个日界按实际余额判定；进入/退出各写一条 `system_treasury_guard` 事件
  （`args` 含 `day/active/balance/threshold`），`apply` 返回空 Effect，**不改变任何余额**。

**暂停项（guard 期间）**

| 动作 | 处理 | 依据 |
|---|---|---|
| `open_shop`（新店铺租） | `preconditions` 返回 `treasury_guard` | [business.py](../src/wsim/actions/business.py) |
| 非紧急采购（消费倾向买物） | `_propensity_buy` 直接跳过 | [intent.py](../src/wsim/sim/intent.py) |
| 月度 `system_shop_dividend` | `apply` 返回空 | [system.py](../src/wsim/actions/system.py) |
| 工资 `work` | 不暂停 | 优先保工资 |
| 已承诺镖单 `escort` / `quest_deliver` | 不暂停 | 优先保已承诺 |

**触发情况（delivery-72d）**

| 指标 | 数值 |
|---|---|
| 阈值 | 2,400 |
| 首次进入 | 第 23 天（余额 2,026） |
| `enters` | **1** |
| `exits` | **0** |
| `days_active` | **50 / 72** |
| 结束时仍 active | 是 |
| 期末余额 | **2,009** |
| 事件数 | 1 条（`tick=3311`） |
| 回放 | MATCH（事件无副作用） |
| 守恒误差 | **0** |
| minted_total | 47,150（与基线相同，**未 mint 补钱**） |

**刹车实测（必须如实看）**

- `open_shop`：72d 共 11 次成功开店全部发生在第 19 天之前；
  guard 从第 23 天才生效，因此**这套刹车一次都没有真正拦住开店**
  （拒绝原因里 `treasury_guard` 出现 0 次）。
- 非紧急采购：`buy` 从基线 363 次降到 246 次（少 117 次）。
  但**本模型中 `buy` 是 NPC → 商业体（=金库）的流入**，暂停采购反而减少金库回流；
  这是本轮口径上的反直觉点。
- 月度分红：第 30/60 天的 `system_shop_dividend` 事件均返回 0 个 Effect（被暂停）；
  但基线 `b10-72d` 同样 0 个 Effect（没有“有主且现金 >100 的私店”），
  所以本轮**分红暂停没有实际金额**。

**结论**

- 告警、事件日志、可回放、触发天数/次数：**达标**。
- 自动刹车对金库余额**没有实际止血效果**：金库仍从 2,014 附近滑到 2,009，
  贴着 2,000 保底线；延长到 72 天以上仍会因工资/生活成本停摆。
- 本轮**没有发钱、没有 mint**，守恒误差 0。
- 真正能改观的是收入杠杆（税率/任务拨款/工资标尺），属于 P3；不能用 mint。

### 2.3 镖单 32→25 的原因

**先排除“镖局现金不足”**

- P1 归档 run `runs/final72d`：`agency_insufficient_cash` **22 笔**；
- P2-B `runs/b10-72d`：**0 笔**；
- 本轮 `delivery-72d`：**0 笔**（`escort` 动词 38 次，0 次被拒）。
- `escort` 的前置（镖局/区域现金、路线逐跳合法、角色允许）在本轮都没有触发拒绝，
  所以 32→25 **不是现金拒单，也不是路线/报酬变化导致没人接**。

**根因：过期未接的 `open` 镖单永久占住模板名**

`system_quest_post` 发现同一模板存在 `open/accepted` 单就跳过发新单；
而 `system_quest_fail` 只关闭 `accepted` 超时单，**`open` 无单单永不关闭**。
结果某个模板一次没人接，就永久停止发单。证据（`escort_iron_short` 最后发单日）：

| run | 短程镖单发单数 | 最后发单日 | 长途镖单发单数 | 最后发单日 | `escort` 成功次数 |
|---|---|---|---|---|---|
| `p2b-72d`（P2-B 基线） | 18 | 第 35 天 | 15 | 第 46 天 | 32 |
| `b10-72d`（整改前） | 9 | **第 17 天** | 15 | 第 48 天 | **25** |
| `delivery-72d`（本轮） | 30 | **第 72 天** | 21 | 第 71 天 | **38** |

整改前短程镖单在第 17 天就冻结（第 17 天那单 `deadline` 过期后无人可接，
`open` 状态一直挂着）；长途镖单在第 48 天后同样冻结。押镖产能不是被现金卡住，
而是发单侧被占位卡死。

**修复（不动镖银）**

1. `quest_fail` 允许关闭 `open` 单，状态记为 `expired`（[effects.py](../src/wsim/world/effects.py)）；
   `system_quest_fail` 每晚会关闭 `deadline` 已过的 `open` 单
   （[quests.py](../src/wsim/actions/quests.py)）。模板占位被释放，次日可重发。
2. 押镖单使用独立并发额度 `quests.max_concurrent_escort = 1`，
   不与 2 个普通跑腿/采购额度抢位（[economy.yaml](../content/economy.yaml)）。
3. **未提高镖银**：`escort_iron_short` 报酬仍为 12 文 + 镖局结算 140 文，内容未改。

**修复前后**

| 指标 | b10-72d（前） | delivery-72d（后） |
|---|---|---|
| 镖单发起（`escort` 成功） | 25 | **38** |
| 镖单完成 / 失败 / 在途 | 23 / 2 / 0 | **33 / 4 / 1** |
| 镖单任务完成数 | 17 | **26** |
| 任务总完成 / 失败 / 过期 / 在途 | 94 / 5 / — / 9 | **203 / 10 / 206 / 3** |

**拒绝原因 top5（delivery-72d，全动词）**

| # | 原因 | 次数 |
|---|---|---|
| 1 | `not_adjacent` | 62 |
| 2 | `not_together` | 55 |
| 3 | `quest_not_accepted_by_actor` | 11 |
| 4 | `not_at_giver` | 10 |
| 5 | `quest_expired` | 8 |

其中与镖单路径相关（`quest` 参数含 `escort`）仅 7 笔：
`quest_not_accepted_by_actor` 2、`not_at_giver` 1、`quest_expired` 4；
`escort` 动词本身 0 笔。

**修复的副作用（如实报）**

- 5 个模板在 72 天内始终 0 完成：`commission_herb_sorting`、`commission_stonework`、
  `errand_silk_sample`、`inquiry_luo_marriage`、`procure_ginseng`。
  以前它们各占 1 个 `open` 位；现在会“发单 → 过期 → 重发”，
  于是出现 **206 张过期单**，事件拒绝率 0.908% → 1.4595%（仍低于 2% 门禁）。
- 这些模板的“可接性”问题本轮未修，列入 P3。

---

## 3. P2 全部指标表

| 指标 | 10 天 | 72 天 |
|---|---|---|
| content_hash | `420ee2ff307b…` | `420ee2ff307b…` |
| event_final | `0004571c3847…` | `474043e6bca5…` |
| state_final | `ef8d206acb1a…` | `62cad7989deb…` |
| state_day10 | `ef8d206acb1a…` | `ef8d206acb1a…`（与 10 天 run 一致） |
| 事件数 / 拒绝数 / 拒绝率 | 2,841 / 44 / **1.549%** | 12,607 / 184 / **1.4595%** |
| 决策数 / reason 覆盖率 | 238 / 1.0 | 931 / 1.0 |
| LOD 末态（silent/background/active/spotlight） | 5 / 13 / 12 / 1 | 25 / 4 / 1 / 1 |
| **K1 全量（分母 91）** | **45/91 = 49.45%（未达标）** | **71/91 = 78.02%（达标）** |
| K1 剔除一次性目标 | 36.11% | 70.15% |
| K1 已创建口径（参考） | — | 82.56% |
| K3 verb 层：类数 / Top-10 / 熵 | 16 / 98.37% / 2.5554 | 16 / 99.45% / 2.5235 |
| K3 对象层 v1.1：类数 / Top-10 / 熵 | 86 / 63.15% / 5.0090 | **100 / 60.57% / 5.0728** |
| K3 v1.0 兼容口径：类数 / Top-10 / 熵 | — | 94 / 60.57% / 4.9894 |
| K3 敏感性（剔除 4 新动词） | — | 90 / 63.50% / 4.8871 |
| NPC 现金期末 | 35,182 | 31,499 |
| 商业体（区域金库）期末 | 8,016 | **2,009** |
| 账户总额 / minted / burned | 45,806 / 47,150 / 1,344 | 42,716 / 47,150 / 4,434 |
| **守恒误差** | **0** | **0** |
| 债务审计 | — | **9/9 PASS**，active 48 条 |
| 关系审计 | 检查 131，冲突 0 | 检查 180，冲突 0 |
| 工资 应发/实付/欠薪 | 9,564 / 9,564 / **0** | 28,171 / 28,171 / **0** |
| 任务 完成/失败/过期/在途 | 30 / 1 / 28 / 0 | 203 / 10 / 206 / 3 |
| 镖单 发起/到站/完成/失败 | 8 / 7 / 7 / 0 | 38 / 37 / 33 / 4 |
| 门派成立 | — | 第 3 天（镖行同盟）、第 20 天（清议社）、第 61 天（药行会） |
| 成本 calls / persona_tokens / USD | 199 / 14,589 / 0.5306 | 754 / 55,921 / 1.9867 |
| `treasury_guard` 进入/退出/天数 | 0 / 0 / 0（余额 8,016） | **1 / 0 / 50**，第 23 天进入 |
| 离线 UI 快照 | — | 回放 12,607 事件，`hash_ok=True` |

10 天 K1 不达标是**预期口径**：10 天只创建了少量内容目标，全量分母 91 下必然低；
K1 的评估期是 72 天。不作为“已达标”处理。

---

## 4. 哪些是桩

| 组件 | 状态 |
|---|---|
| `MockReasoner` | **桩**：确定性规则，默认推理器；不消费人设卡（只有 26 字进 reason） |
| `LLMReasoner` / `ProviderClient` | **已实现但默认关闭**；未配 3 个环境变量时不会联网 |
| MCP Gateway | 进程内 **mock server**；权限/审计是真实现，资源是桩 |
| UI `ui/server.py` | **离线快照 + 提案**；`/api/dialogue` 只入 `outbox`，无 `talk` 执行器 |
| 经济 / 任务 / 门派 | 规则驱动（意图 → 前置 → Effect），非模型生成 |
| 门派 `found_sect` 等多步目标 | 规则状态机，非 LLM 规划 |

---

## 5. 未达标 / 风险清单

1. **`treasury_guard` 的刹车没有实际止血**：进入第 23 天、活跃 50 天，期末 2,009，
   仍贴 2,000 保底线；延长天数仍会停摆。暂停采购在本模型里还是减少金库流入。
2. **人设卡 71–80 token/卡**，未达到 300–500 token 目标（代码已就绪，内容需扩写）。
3. **5 个任务模板 72 天 0 完成**，产生 206 张过期单；拒绝率从 0.908% 升到 1.4595%
   （未超 2% 门禁，但需 P3 处理可接性）。
4. **10 天 K1 49.45% 不达标**（10 天不是评估期，口径原因已说明）。
5. **真实模型未接入**；所有 LLM 路径默认关闭，成本为 Mock 估算。
6. **`talk` 不落地**；UI 对话只是提案，世界不会推进。
7. 经济仍是“工资驱动、金库贴地”的结构，收入侧没有正反馈；这是 P3 的核心课题。

---

## 6. 下一步 P3 需要什么

### 6.1 三个环境变量（真实模型接入）

```bash
export LLM_BASE_URL="https://<endpoint>/v1"
export LLM_API_KEY="<key>"
export LLM_MODEL="<model-name>"
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --run-id p3-llm --enable-llm
```

只有三者齐全且显式 `--enable-llm` 才会走真实 HTTP；任一缺失 → `ProviderConfigError`
→ 自动降级回 Mock（`llm.enabled=false`，`reason` 写入 summary）。

### 6.2 其他 P3 前置

- 收入杠杆（税率 / 任务拨款 / 工资标尺）替代“金库贴地”，禁止 mint。
- 5 个 0 完成模板的可接性修复（距离/角色/目标地点/物品）。
- 人设卡扩写到 300–500 token/卡（内容工作）。
- 真实 LLM 接入后的 schema 拒绝率、降级率与成本回归。

---

## 7. 验收记录（从 tag 干净重跑）

在 `git worktree add --detach /tmp/xkx-delivery-p2 delivery-p2` 的干净工作区执行：

```
delivery-10d: events=2841
replayed=ef8d206acb1ab29457788cf5fc7d27f6f983e906794de441d4db95bbe2cb4ca9
stored  =ef8d206acb1ab29457788cf5fc7d27f6f983e906794de441d4db95bbe2cb4ca9
MATCH

delivery-72d: events=12607
replayed=62cad7989deb1d467a9b6b1e8715cdbc8acd96b4376df975f3a21f4d90647b97
stored  =62cad7989deb1d467a9b6b1e8715cdbc8acd96b4376df975f3a21f4d90647b97
MATCH

FRAMEWORK CHECK: PASS   （5/5，含 10 天 1.55% 拒绝率 < 2% 门禁）
python3 -m pytest -q    （72 passed，exit 0）
```

两份 run 的 `state_day10` 与 10 天 run 的 `state_final` 完全一致，
说明确定性回放在 tag 上可复现。
