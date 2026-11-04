# A4「模型作为战略层」受控实验结果

> 结论：**不值得做（FAIL）**。在 10 天 / 31 NPC、$0.4767 的真实战略调用下，PCR 20.93%、CDC 0.8484 均低于最低门槛，CEG 因“新增完成目标 0 个”不可定义；K1 护栏通过。按纪律不再跑第二轮。

- 基线快照：`runs/a3-rule-10d-31npc-frozen`（content_hash 见其 summary；R 臂与旧 M 臂指标见 spec）
- 实验 arm：`runs/a4-real-10d-31npc`（本 run 未入库，`runs/` 被 gitignore）
- 战略频率：每 3 游戏日一次；计划 horizon 3 日；覆盖 31 NPC × 4 轮 = 124 次调用
- 战略层**替代** LLM decision 选择器；tick 级动作仍由规则 `MockReasoner/Planner` 执行

## 0. K1 口径核对（零成本复跑，2026-11-04 补测）

结论：进度公式修正对 K1 **影响为 0.00pp**；A4 的 K1 提升全部来自战略层。但原报告把 A4（无 player）与冻结 R（含 player）并列，混入了配置差，需按下表重新归因。

| 口径 | run | K1 | 进度/总数 | 完成 |
|---|---|---:|---:|---:|
| 旧公式 R（冻结快照，含 player） | `runs/a3-rule-10d-31npc-frozen` | 56.04% | 51/91 | 23 |
| 新公式 R（当前 HEAD，同配置含 player） | `runs/a4-rule-recheck-players-10d-31npc` | 56.04% | 51/91 | 23 |
| 新公式 R（当前 HEAD，无 player，与 A4 arm 同配置） | `runs/a4-rule-recheck-10d-31npc` | 49.45% | 45/91 | 20 |
| A4 arm（无 player） | `runs/a4-real-10d-31npc` | 64.84% | 59/91 | 23 |

- **公式修正贡献 = 0.00pp**：同配置（含 player）下 56.04% → 56.04%；K1 审计用 `progress > 0.1` 判定，而 `Goal.progress` 本就是 0..1 比例，不受该除法 bug 影响。
- **配置差异贡献 = −6.59pp**：含 player → 无 player，56.04% → 49.45%。
- **战略层贡献（同配置）= +15.39pp**：49.45% → 64.84%。若强行与冻结 R 并列会显示 +8.80pp，其中 0 来自公式修正，其余来自战略层但混入 player 配置差。
- 判定：新公式 R（56.04%）< A4 arm（64.84%），不触发“战略层 K1 收益为零甚至为负”。
- 冻结快照可精确复现：在 pricing 前的 `20dc739` 以 `--with-players 1` 重跑，event `11fa6c02…`、world `f36df7…` 与冻结逐字一致；当前 HEAD 因 pricing 提交 `64365f8` 改变金额导致哈希不同，但事件数（3837）、决策数（249）、K1（51/91）完全一致。
- 确定性：同一命令连跑两次（`a4-rule-recheck-10d-31npc` / `-b`），事件哈希与 K1 完全一致。
- 口径纪律：重跑 R 臂必须带 `--with-players 1` 才能对齐冻结快照；A4 arm 是无 player 运行，唯一可比口径是 49.45%（差距 +15.39pp）。

## 0.1 生产口径确认（收工）

- A4 战略层**默认关闭**（`strategy_enabled=False`），不作为生产路径，不改变默认 run 行为。
- 唯一可复用价值：**每 3 游戏日的计划文本 + 成本 $0.4767/10 天**，可作叙事层素材。
- 该素材必须同时标注 **20 份非法计划**，根因是战略 prompt 的 `active_goal` 未剔除 urgent 目标：`unknown_goal` 30 / `duplicate_goal` 10 / `too_many_goals` 7。

## 0.2 最后一次复跑：仅修 urgent 目标泄漏（预算 $0.5）

**改动（单一修复）**：`build_strategy_prompt` 不再把 `urgent_*` 目标作为 `active_goal` 下发，并在 system prompt 增加一句“urgent_* 不是计划目标”。未改阈值、指标、其他逻辑；战略层仍默认关闭。

**零成本验证**：

| 检查 | 结果 |
|---|---|
| 单测 `test_strategy_prompt_hides_urgent_active_goal` | 通过（7/7 全绿） |
| fake 10d/31npc 合法率 | 124/124 = **100%**（修复前 fake 亦为 100%，非法仅出现在真实模型） |
| 两次 fake 重跑哈希 | `a4-fix-fake-10d-v1` / `-v2`：event `c21d642e…`、world `fbec750b…` 完全一致 |
| fake 成本 | $0 |

**真实 run**（`runs/a4-fix-real-10d-31npc`；seed 42、10 天、31 NPC、无 player、每 3 日规划、硬闸 $0.5）：

| 指标 | 门槛 | 本次 | 判定 |
|---|---:|---:|---|
| PCR 计划完成率 | ≥28% | **22.73%（20/88）** | **FAIL → 永久收工** |
| PIR 计划中断率 | ≤55%（最低） | **0.00%（0/69）** | PASS |
| CDC 跨日一致性 | ≥0.88（最低） | **0.8538**（240 对） | FAIL |
| CEG 单位成本收益 | ≤$0.30 且新增 ≥5 | **+2 新增 / $0.2367 每新增** | FAIL（新增不足） |
| LHGA | ≥0.88（最低） | **0.710** | FAIL |
| K1 护栏 | ≥47.45%（同配置 R−2pp） | **64.84%** | PASS |
| 计划合法率 | ≥95% | **124/124 = 100%**（修复前 83.87%） | PASS |
| fallback / 超时 | 0 | 0 / 0 | PASS |

CEG 是按同配置 R 臂（`a4-rule-recheck-10d-31npc`，完成 20）计算：本次完成 22，新增 2；若按冻结 R（完成 23）则新增为 −1、CEG 不可定义。

**成本账单**：124 次调用，input 224,674 / output 22,731 tokens，单次 $0.003818，10 天 **$0.4734**，72 天外推 **$3.4085**；`hard_triggered=false`，软阈值 $0.42 在最后一次批量后触发一次告警但未中止。

**计划烂尾的具体表现**（88 个唯一计划条目）：

| 类别 | 数量 | 占比 | 说明 |
|---|---:|---:|---|
| 窗口内完成 | 20 | 22.7% | build_shop 10/12、gain_status 9/12 |
| 条目已创建但进度 <10% | 42 | 47.7% | 规则层有安排动作，但 3 天内推进极小（中位 0.1%） |
| 条目从未创建 | 15 | 17.0% | 被同 NPC 上游内容目标阻塞，前置未完成 |
| 进度 ≥10% 但未完成 | 11 | 12.5% | 最长 28% |
| 被模型/紧急事件放弃 | 0 | 0% | urgent 只暂停 24 次，均未 abandon |
| 完成后被换掉 | 0 | 0% | — |

按类型完成率：`build_shop` 83.3%、`gain_status` 75.0%、`found_sect` 16.7%、`earn_money` 0/26、`advance_skill` 0/16、`build_relation` 0/16。68 个未完成条目中 61 个被后续计划继续携带（89.7%），仅 7 个被丢弃；且 0 个在窗口外补完——说明不是“晚一点完成”，而是 3 日窗口内根本完不成。

**原因诊断（决定产品方向用）**：

1. **主因：计划粒度/目标体量与 3 日窗口不匹配（约 60%）**。计划偏好 `earn_money`/`advance_skill`/`build_relation` 这类长周期目标（target 常为 600 或高阈值），而规则层每 NPC 每天只有 2 次深度目标动作额度；3 天窗口内中位进度仅 0.1%，达 10% 的也停在 28%。短周期目标（build_shop / gain_status）完成率 75%–83%，证明计划本身并非不可达。
2. **次因：前置链阻塞（17%）**。15 个条目从未创建，因为同 NPC 的前序内容目标尚未完成；计划把后续目标写进来，但规则层按内容优先级仍卡在前置。
3. **非主因：紧急事件抢占**。24 次 pause、0 次 abandon、0 次模型自我推翻；`prereq_infeasible_or_fallback` 仅 10 次。紧急层不是 PCR 低的原因。
4. **结论**：若继续做，方向应是“缩短规划窗口 / 只规划可在 3 天内完成的目标”，而不是继续压缩成本或加强护栏。

## 1. 阶段 0：确定性 fake 端点接线（零成本）

| 检查 | 结果 |
|---|---|
| 计划被规则层消费 | 131 次消费；93 个唯一计划条目中 73 个被安排动作（PEG 21.5%） |
| 计划不会被每 tick 推翻 | 43 次偏差 / 31 NPC / 10 天；其中计划内轮转 44 次、前置不可行 7 次，无逐 tick 抖动 |
| 两次重跑哈希 | `a4-fake-10d-31npc-v8` 与 `-v8b`：event_hash、world_hash 完全一致 |
| PCR / PIR / CDC（fake 口径） | 22.58% / 0.00% / 0.8416 |
| 计划合法率 | 124/124（100%） |
| 真实花费 | $0（fake transport，无网络） |

计算脚本：`tools/strategy_audit.py`（PCR 去重口径、PIR 全条目口径、CDC 与 R 臂逐字一致：复算 R 得 0.87635）。

## 2. 阶段 1：2 天 / 12 NPC 真实小跑（预算 ≤$0.30）

| 项 | 结果 |
|---|---|
| 战略调用 | 12 次，12 合法，0 fallback，0 schema reject |
| 成本 | $0.0445 |
| 延迟 | p95 2.467 s |
| 回放 | `a4-replay-2d-12npc` event/world hash 与真实 run 一致，成本 $0 |
| 结论 | 接线不炸、成本可估；不作性能结论 |

## 3. 阶段 2：10 天 / 31 NPC 真实对照（预算 ≤$1.5）

| 指标 | R / 旧 M 基线 | A4 arm | 严格门槛 | 最低门槛 | 判定 |
|---|---:|---:|---:|---:|---|
| PCR 计划完成率 | 20.51% / 25.22% | **20.93%** | ≥30% | ≥28% | **FAIL** |
| PIR 计划中断率 | 67.52% / 64.35% | **0.00%** | ≤45% | ≤55% | PASS |
| CDC 跨日一致性 | 0.8764 / 0.8274 | **0.8484** | ≥0.90 | ≥0.88 | **FAIL** |
| CEG 单位成本收益 | $0.60/新增目标 | **不可定义（新增 0）** | ≤$0.20 且 ≥5 | ≤$0.30 且 ≥5 | **FAIL** |
| K1 护栏 | 56.04% | **64.84%** | ≥54.04% | ≥54.04% | PASS（口径修正见 §0） |
| LHGA（spec M2 附加） | 0.774 | **0.742** | ≥0.90 | ≥0.88 | **FAIL** |

> K1 口径说明：A4 arm 为无 player 运行；与它同配置的 R 臂是 49.45%，门槛相应为 47.45%，K1 仍 PASS。冻结 R 的 56.04% 含 player，不能与 A4 arm 直接并列（详见 §0）。

补充：10 天完成目标 23 个，与 R 臂 23 个持平；新增完成目标 0，未达到“≥5”。PEG 23.26%（20/86 唯一计划条目从未被规则层安排动作）。

### 计划合法率

124 份计划中 104 合法（83.87%），20 份非法。原因计数（可重叠）：

- `unknown_goal` 30 次：模型把当时的 **urgent 目标** 放进 `preserve_goal_ids`，而 urgent 不属于内容目标；
- `duplicate_goal` 10 次；
- `too_many_goals` 7 次。

这是本次接线的一个明确缺陷：战略 prompt 的 `active_goal` 未剔除 urgent 目标，诱导模型保留一个不可计划 id。按“不再跑第二轮”的纪律，未修后重跑。

### 中断原因分布

| 类别 | 计数 | 说明 |
|---|---:|---|
| 被规则紧急事件打断 | 0 | urgent 仅暂停计划：26 次 pause，解除后回原主目标，未 abandon |
| 被前置不可行转条 | 2 | `infeasible_prereq`，计划内切换 |
| 被模型自己推翻 | 0 | 计划只在 3 日边界整体替换，无到期前 replan |
| 计划内达阈值轮转 | 31 | `in_plan_rotate`，不计中断 |
| 计划内全部触达后交回规则 | 10 | `fallback_default_content`，不计中断 |
| 实际中断条目 | **0** | PIR 0/86 |

## 4. 成本账单（strategist 槽）

- 调用：124 次（10 天），全部成功返回；0 fallback
- Token：输入 223,134；输出 23,668（均值 input 1,799 / output 191 每 call）
- 单价：$0.0015/1k input + $0.0060/1k output
- 单次均价：$0.003844
- 10 天：**$0.4767**；72 天外推：**$3.4323**
- 预算闸：soft/hard/monthly 均设 $1.5，未触发（`hard_triggered=false`）
```

## 5. 战略产出样例（原文，含 3 个 NPC 的多日计划）

### bai_lusheng（发起清议社；horizon 3 日）

- day 0：primary `goal_bai_lusheng_1`（found_sect）；secondary `found_qingyi_society`、`goal_bai_lusheng_2`（advance_skill）；涉及 `fang_cunxin, yan_shouyi, xie_tingyun`；confidence 0.6
  - 理由原文：“立清议社乃名正言顺之事，先与方寸心、严守一诸友商议；学问亦不可荒废，余钱守着不动。”
- day 3：同一主目标与两条副线；涉及 `fang_cunxin, xie_tingyun, yan_shouyi`
  - 理由原文：“清议之社已开其端，不可半途而废；先与方寸心、谢停云、严守一几位把名分议定，再抽空温经进学，钱守得住便好。”
- day 9：主目标不变，副线 `advance_skill`、`found_qingyi_society` 互换次序
  - 理由原文：“立会乃名正言顺之大事，须先与方寸心、严守一诸生议定章程；学问亦不可荒废，故兼修经义，钱守底线不妄花。”

### cui_tieyi（先差事、后攒钱、维系人情；horizon 3 日）

- day 0：primary `gain_status`；secondary `build_relation`、`earn_money`；涉及 `xie_tingyun, wei_changgeng, lu_xinghua`
  - 理由原文：“先把差事办出个名目，立住身份；顺带和谢停云、韦长庚走动，钱上不赊不欠，守住底线。”
- day 3：primary `earn_money`；secondary `build_relation`；涉及 `xie_tingyun, wei_changgeng, jin_mancang`；confidence 0.7
  - 理由原文：“手头这桩攒钱的差事已近半，不能半途松劲；顺带把谢停云、魏长庚几个老交情走动走动，巡街办案也好有个照应。”
- day 9：继续 `earn_money` + `build_relation`
  - 理由原文：“手头这桩差事快收尾了，先把钱挣够；谢停云和卓不凡那边也得走动，人情不能断。”

### gu_qingya（义诊名声、药材关系、诊金；horizon 3 日）

- day 0：primary `gain_status`；secondary `build_relation`、`earn_money`；涉及 `su_hexiang, jiang_tingyu, chang_banxia`
  - 理由原文：“医道清名是根本，先立住义诊的名声；顺带把苏合香的艾草和蒋听雨那笔旧账理一理，钱上不松口也不逼人。”
- day 3：primary `build_relation`；secondary `earn_money`；涉及 `su_hexiang, jiang_tingyu, bai_lusheng`
  - 理由原文：“义诊的人情还得慢慢攒，苏合香那边多走动；蒋听雨那三十文不急催，见了面照旧别过脸。手头钱够，先挣些稳当的，别让药箱空了。”
- day 6：primary `earn_money`；secondary `build_relation`；预算改为 cash_floor 60 / max_discretionary_spend 150
  - 理由原文：“诊金攒到六成还差一截，先稳住进项；苏合香、段五斗处走动走动，顺带把蒋听雨那三十文的话头递一递，钱上不松口也不逼人。”
- day 9：**非法**（`unknown_goal:urgent:gu_qingya:urgent_recover:8`），因 preserve 了 urgent 目标；该轮未进入计划台账。

## 6. 回放证据

- 真实 run：`runs/a4-real-10d-31npc`，event_hash `9145c36d…d54d9`，world_hash `3795ba4e…52f3de`
- 回放 run：`runs/a4-replay-10d-31npc`，event_hash/world_hash **MATCH**
- 回放成本：$0.0000；`strategy.calls=124`，`estimated_cost=0`，预算 `month_cost=0`

## 7. 关键差异与口径说明（诚实披露）

1. PCR 按 spec M1 去重：同一 NPC 重复出现的同一 `goal_id` 只计一次。若按出现次数计，PCR 更低。
2. PIR 按 spec M2 使用“全部唯一计划条目”作分母（0/86），不是执行过的条目；若按执行条目（66）计仍为 0。
3. 计划内条目推进到 10% 后按规则轮转，不计中断；全部触达后交回规则可选第 4 条内容目标，用于保 K1。
4. 计划合法率受 urgent 目标误引用拖累；这是实现缺陷，不是内容改动，按纪律未重跑。
5. 未改 `content/`、`docs/`、`dist/`、`dialogue*.py`、`text_guard.py`，未改任何 tag，未新建 golden 基线。
