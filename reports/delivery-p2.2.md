# Delivery P2.2 · 最后一轮整改与冻结

- 日期：2026-11-03
- tag：`delivery-p2.2`（附注 tag，未做 GPG 签名）
- tag 指向 commit：`62bf4f7`
- 内容哈希：`c9d6bb66f2858616c3b5a926dbc1bfad32f245ca71be5b18fda846ad9aaedc98`
- 干净重跑：从 tag 检出到 `/tmp/wsim-p22b`（独立 worktree），`sim/run_headless.py` 重跑 10d / 72d
- replay：两个 run 均 `MATCH`
- framework：`FRAMEWORK CHECK: PASS`（5/5）
- pytest：`89 passed`

本报告只陈述 P2.2 的结果。`reports/delivery-p2.md` 与 `reports/delivery-p2.1.md` 原样保留；其中被复核指出的三处错误在本文第 4 节更正。

---

## 1. 对话持久化（P2.2 最重要）

### 改法

`talk` 不再写进临时 session。UI 适配层把该 run 的 `events.sqlite` 直接交给 `Writer`，玩家对话仍走 `world.propose_intent → admin.request → admin.approve → admin.apply`，审批通过后 effect 以 `talk` 事件追加到同一 run 的 SQLite；事件写入后同步重放只读投影，并重写 `world_hash.txt`、`event_hash.txt` 与 `summary.json` 的 `hashes` / `events.total`，使刷新与 `tools/replay.py` 都能重建同一状态。

关系限幅不变：`talk_ledger` 继续 clamp ±2/tick、±8/日；跨日按自然日重新计额度，因此可以累积。

Mock 回应按 `sha256(npc|message|tick|turn)` 选人设台词，`turn` 推动台词索引，`last_reply` 避免相邻两轮选同一句；第二轮起句首会引用上一句（`听你又提起「…」`），第 1/2/3 轮使用不同引导句式。

### 实测（干净 72d run 的副本 `/tmp/p22v-ui`）

| 指标 | 结果 |
| --- | --- |
| 连续对话轮次 | 3 轮（同一 NPC `liu_sanniang`、同一 tick） |
| 追加事件 seq | 12617 / 12618 / 12619 |
| 持久化事件数 | 12,616 → 12,619（+3） |
| 连续重复率 | 0/3 = 0% |
| 重启适配器后仍可见 | 3 个 `talk` 事件 |
| 关系 | affection `+2.0`（±2/tick 封顶）、trust `+1.5` |
| 回放 | `tools/replay.py --run /tmp/p22v-ui` → `MATCH` |

三句回应全文不同；由于每个 persona 只有 2 句固定台词，第 3 轮会复用其中一句台词，但引导句与上一句引用不同，完整回应不重复。报告口径以“完整回应重复率”为准。

跨日累积测试 `test_talk_cross_day_accumulates_at_most_24`：3 天 × 每天 8 个 tick、每 tick 请求 +2，最终 `affection = 24.0`、`trust = 64.0`（起始 trust 40 + 累积 24），证明日上限 ±8、三日累计 24 且非零。

`test_gateway_propose_approve_apply_lands_talk` 在 apply 后关闭并重开 `events.sqlite`，仍能读到同一条 `talk` 事件（`seq` 一致），证明 effect 落在持久 store 而非内存。

---

## 2. `open_shop` 门槛重算

### 改法

```
门槛 = 保底线(employer_reserve=2000) + 开店成本 + expected_wage_days(3) × Σ该店岗位日薪
```

`expected_staff` 与 `expected_wage_days` 写进 `content/economy.yaml`；全区域工资池（`daily_wage_bill`）不再进入该公式。

| 店铺类型 | 成本 | 岗位 | 新门槛 | 旧门槛 |
| --- | --- | --- | --- | --- |
| stall | 300 | 掌柜(36) | **2,408** | 7,613 |
| storefront | 1,200 | 掌柜(36)+酒保(20) | **3,368** | 7,613 |
| branch | 3,000 | 掌柜(36)+酒保(20)+脚夫(18) | **5,222** | 7,613 |

### 72 天结果

| 指标 | P2.1 | P2.2 |
| --- | --- | --- |
| `open_shop` intent 数 | 10 | 12 |
| 开店成功 | 9 | **11** |
| treasury 门槛延后（`open_shop_postponed`） | 72 | **0** |
| 被门槛延后的 NPC | 多人、第 15/18 天两次 | **无** |
| 非门槛拒绝 | 1 | 1（`zhuo_bufan` day5，`insufficient_money`，本金不足） |

第 15 天 `luo_qingxu`、第 18 天 `ruan_xiaoluo` 的开店均成功，不再静默延后。11 家店：`liu_sanniang`、`song_henian`、`duan_wudou`、`lu_xinghua`、`jin_mancang`、`ruan_qingfu`、`qin_wangchuan`、`shi_gandang`、`zhuo_bufan`、`luo_qingxu`、`ruan_xiaoluo`。唯一未推进的 `build_shop` 目标是 `su_hexiang:goal_su_hexiang_1`（未开店，不是门槛延后）；`zhuo_bufan` day5 因本金不足被拒一次，day6 补开成功。

### K1 变化

| 版本 | 内容哈希 | K1 | 标注 |
| --- | --- | --- | --- |
| P2 baseline | `420ee2ff…` | 71/91 = 78.02% | `non_comparable` |
| P2.1 | `ff09a8e6…` | 68/91 = 74.73% | `non_comparable` |
| **P2.2** | `c9d6bb66…` | **71/91 = 78.02%** | 本 run 内有效 |

P2.2 恢复到 ≥75%，比 P2.1 多推进 3 个目标：

- `luo_qingxu:goal_luo_qingxu_3`（build_shop）
- `ruan_xiaoluo:goal_ruan_xiaoluo_2`（build_shop）
- `ruan_xiaoluo:goal_ruan_xiaoluo_3`（build_shop）

跨 `content_hash` 的差值一律标 `non_comparable`（`same_snapshot_only`，`cross_version_comparable=false`）。P2 与 P2.2 同为 71/91 只是数值巧合，不能当作同口径回归。

72d K1 分类型：

| 类型 | 推进/总数 | 比率 |
| --- | --- | --- |
| build_relation | 18/18 | 100% |
| build_shop | 11/12 | 91.67% |
| advance_skill | 14/16 | 87.5% |
| gain_status | 12/15 | 80% |
| found_sect | 2/3 | 66.67% |
| earn_money | 14/26 | 53.85% |
| join_sect | 0/1 | 0% |

剩余 20 个未推进目标：

- 5 个未创建：`su_hexiang:goal_su_hexiang_2/3`、`xie_tingyun:goal_xie_tingyun_3`、`fang_cunxin:goal_fang_cunxin_3`、`yan_shouyi:goal_yan_shouyi_3`
- 15 个已创建但 progress=0：`shen_yanzhou_1`、`lu_jiujin_1`、`su_hexiang_1`、`wen_xiaoman_1`、`xie_tingyun_2`、`duan_wudou_1`、`luo_qingxu_1`、`chang_banxia_1`、`shi_gandang_1`、`jiang_tingyu_1`、`ruan_qingfu_1`、`xing_pojun_1`、`lu_xinghua_1`、`yan_shouyi_2`、`ming_chabo_1`

---

## 3. UI 任务指引

- 任务板（`/api/quests` + “任务板” tab）逐单显示：任务、发布人·地点、是否需同行、当前是否可接、给玩家的提示。
- 拒绝事件新增可执行提示（`_event_hint`），例如：
  - `not_at_giver` → “先去城隍庙找严守一接单。”
  - `not_together` → “先走到卓不凡所在的地点，和对方待在一起再交易。”
  - `not_adjacent` → “先移动到与书院相邻的地点，再继续走。”
  - `quest_not_accepted_by_actor` → “先回城隍庙找严守一接单，再去做任务。”
- 任务卡示例：`送书院口信` · 发布人严守一 · 城隍庙 · 同行否 · 可接是 · “可接：先去城隍庙找严守一接单”。
- NPC 详情页新增“现在在做什么”和当前地点。72d 实测：`jin_mancang` 在集市，最近动作“移动（第 6 天 01:10，academy）”；`liu_sanniang` 在集市，最近动作“修炼（第 22 天 00:30，market）”。

72d 拒绝原因与提示覆盖：`not_adjacent` 64、`not_together` 54、`quest_not_accepted_by_actor` 12、`not_at_giver` 10；上述四类均返回非空可执行提示。

---

## 4. 文档三处更正

### 4.1 税差真因：逐笔 `round(T*0.5)` 银行家舍入

P2.1 的 182 文交易税由 86 文回流金库 + 96 文烧毁构成。真因不是“税源自转折叠”，而是每笔交易调用 `round(T*0.5)`：

- `T=1` 出现 30 次，`round(0.5) = 0`，全部回流 0、全部烧 1；
- 逐笔求和 `Σ round(T/2) = 86`，与 `tax_to_treasury` 完全一致；
- `burn` 交易的 `account == treasury:region` 事件数为 **0**（burn 的账户是交易对手钱包，不是金库）。

P2.2 同一口径为 183 = 84 回流 + 99 烧，`T=1` 出现 33 次（同样全部舍成 0），`Σ round(T/2) = 84`，`burn` 账户为金库的事件数仍为 0。

### 4.2 K1 归因：是 `open_shop` 门槛，不是任务板停发

P2.1 的 K1 从 71/91 掉到 68/91，直接原因是 `treasury_open_shop_buffer` 把“全区域 7 日工资池”计入门槛（7,613 文），第 15/18 天两次开店被静默延后，连带 3 个 build_shop 目标未推进。任务板在同期仍正常发单（`inquiry` 70 张、`procure_charcoal` 72 张等），不是停发。

P2.2 改按店铺自身岗位重算后，这 3 个目标恢复。P2 / P2.1 / P2.2 的 `content_hash` 不同，K1 差值按 `non_comparable` 标注。

### 4.3 失败数与 talk 证据口径

- 失败数：`summary.quests.failed` 是“当前任务板上仍标记 failed 的单”，P2.2 72d 为 **0**；累计失败读 `state.quest_stats`，为 **10**（`errand_academy_notice` 4 + `escort_medicine_temple` 6）。不再把 0 当成全期失败数。
- 过期：累计 75，当前板上 5（其余已 prune）。
- talk 证据：P2.2 起 `talk` 事件、`talk:player` / `talk:npc` 记忆、关系变化都在 run 的 `events.sqlite` 里，重启后仍在，回放可重建；不再用临时 session 截图做证据。

---

## 5. 干净重跑指标（从 tag `delivery-p2.2`）

| 指标 | 10d | 72d |
| --- | --- | --- |
| content_hash | `c9d6bb66…` | `c9d6bb66…` |
| events / rejected / illegal_rate | 2,841 / 44 / 1.5488% | 12,616 / 188 / 1.4902% |
| state_final | `2f7d5eb1…` | `3d180ec9…` |
| event_final | `aa67d879…` | `16d49cbb…` |
| K1 | 45/91 = 49.45% | 71/91 = 78.02% |
| treasury 期末 | 8,040 | 2,023 |
| treasury_guard 天数 / 进入日 | 0 / — | 50 / 第 23 天 |
| open_shop 延后 | 0 | 0 |
| 交易税回流 / 烧毁 | 24 / 35 | 84 / 99 |
| 任务完成 / 累计失败 / 板上过期 | 30 / 1 / 13 | 203 / 10 / 5 |
| 工资支付（gross=paid，unpaid） | 9,564 / 0 | 28,065 / 0 |
| escort created/arrived/done | 8 / 7 / 7 | 37 / 36 / 34 |
| sect founded | 1 | 3 |
| LLM calls / tokens / cost | 199 / 14,589 / $0.5306 | 751 / 55,685 / $1.9814 |
| replay | MATCH | MATCH |

---

## 6. 仍未达标 / 口径限制

1. K1 已达标（78.02%），但仍有 20 个目标未推进：`earn_money` 12 个（含 5 个未创建）、`advance_skill` 2 个、`gain_status` 3 个、`build_shop` 1 个、`found_sect` 1 个、`join_sect` 1 个。
2. 交易税绝对额仍小：72d 共 183 文，占金库期末 2,023 文的 9.0%；逐笔舍入使 1 文税全部流失，这是设计选择，未在本轮改动。
3. 5 个零完成模板（草药分拣、石作、丝样、罗轻絮婚事、人参采购）各创建 10、完成 0，全部过期；任务健康度只做诊断与 prune，不强制改造内容。
4. 对话仍是离线 Mock：persona 只有 2 句固定台词，3 轮内完整回应不重复，但台词本身会在第 3 轮复用；NPC-NPC 对话未落地。
5. UI 是离线快照 + 单 run 持久化；多人并发审批、鉴权与在线服务化不在本轮范围。
6. tag 为附注 tag，未做 GPG 签名；“干净重跑”是从 tag 检出的独立 worktree，不依赖工作区未提交文件。

---

## 7. 冻结步骤

```bash
# 干净检出
git worktree add --detach /tmp/wsim-p22b delivery-p2.2

# 重跑
python3 sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id delivery-10d
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --run-id delivery-72d

# 校验
python3 tools/replay.py --run runs/delivery-10d   # MATCH
python3 tools/replay.py --run runs/delivery-72d   # MATCH
python3 tools/framework_check.py                  # PASS
python3 -m pytest                                 # 89 passed
```

tag 指向代码 commit `62bf4f7`；本报告与 `tasks.md` 的勾选为 tag 之后的文档 commit。
</content>
