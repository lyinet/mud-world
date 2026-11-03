# P3-E 复核数字（脚本生成，勿手写）

生成命令：`PYTHONPATH=src .venv/bin/python tools/p3e_report_stats.py`

## 1. 模板覆盖（P0-5）

- 17 个模板 / 34 条样本；成功 25/34 = 73.53%

| 模板 | 动词 | 样本 | 成功 | 成功率 | 状态 |
| --- | --- | ---: | ---: | ---: | --- |
| 去某地 | move | 2 | 2 | 100% | ok=2 |
| 上工 | work | 2 | 2 | 100% | ok=2 |
| 吃饭 | consume | 2 | 1 | 50% | ok=1, reject=1 |
| 修炼 | cultivate | 2 | 0 | 0% | reject=2 |
| 交易 | trade | 2 | 2 | 100% | ok=2 |
| 购买 | buy | 2 | 2 | 100% | ok=2 |
| 出售 | sell | 2 | 2 | 100% | needs_confirmation=2 |
| 拜师 | apprentice | 2 | 2 | 100% | ok=2 |
| 婚配 | marry | 2 | 2 | 100% | needs_confirmation=2 |
| 入门派 | join_sect | 2 | 1 | 50% | reject=1, ok=1 |
| 开宗立派 | found_sect | 2 | 2 | 100% | needs_confirmation=2 |
| 开店 | open_shop | 2 | 0 | 0% | needs_clarification=2 |
| 护送 | escort | 2 | 2 | 100% | ok=2 |
| 接任务 | quest_accept | 2 | 1 | 50% | reject=1, ok=1 |
| 交任务 | quest_deliver | 2 | 1 | 50% | reject=1, ok=1 |
| 对话 | talk | 2 | 1 | 50% | ok=2 |
| 还债 | debt_settle | 2 | 2 | 100% | needs_confirmation=2 |

### 失败归因（脚本按 JSONL 逐条判定）

| 归因 | 数量 |
| --- | ---: |
| `unknown_category`（模型输出 `unknown`（类别未识别）） | 6 |
| `shop_type_grounding`（类目正确，`shop_type` 参数被 grounding 拦） | 2 |
| `category_drift`（类目漂移（如 talk 被判成 trade）） | 1 |

失败明细：

- `吃饭` / consume：喝一壶浊酒暖暖身子 → attribution=`unknown_category` status=reject parsed_category=unknown blocked=[] missing=[]
- `修炼` / cultivate：练两个时辰绣娘手艺 → attribution=`unknown_category` status=reject parsed_category=unknown blocked=[] missing=[]
- `修炼` / cultivate：练四个时辰绣娘针法 → attribution=`unknown_category` status=reject parsed_category=unknown blocked=[] missing=[]
- `入门派` / join_sect：我要入清议社 → attribution=`unknown_category` status=reject parsed_category=unknown blocked=[] missing=[]
- `开店` / open_shop：在集市摆个摊位卖炊饼 → attribution=`shop_type_grounding` status=needs_clarification parsed_category=开店 blocked=[{'field': 'shop_type', 'raw': 'stall'}] missing=['shop_type']
- `开店` / open_shop：在客栈盘一间店铺 → attribution=`shop_type_grounding` status=needs_clarification parsed_category=开店 blocked=[{'field': 'shop_type', 'raw': 'stall'}] missing=['shop_type']
- `接任务` / quest_accept：接「送一匹绸样」这趟差事 → attribution=`unknown_category` status=reject parsed_category=unknown blocked=[] missing=[]
- `交任务` / quest_deliver：把送一匹绸样交了 → attribution=`unknown_category` status=reject parsed_category=unknown blocked=[] missing=[]
- `对话` / talk：对金满仓说「这批铁器我全要了」 → attribution=`category_drift` status=ok parsed_category=交易 blocked=[] missing=[]

## 2. 2 天真实跑（换模型）披露

| 项 | 值 |
| --- | --- |
| events / rejected / blocked / pruned | 222 / 0 / 0 / 2 |
| 非法率（已执行动作口径） | 0/222 = 0.00% |
| 非法率（严格：rejected+blocked） | (0+0)/222 = 0.00%（PASS=True） |
| 非法率（含剪枝：rejected+blocked+pruned） | (0+0+2)/222 = 0.90%（PASS=True） |
| K1 | 7/91 = 7.69%（k1_pass=False） |
| 动作来源 | model=0.7% / rule=99.3% |
| LLM 调用 | {'decision': 19, 'intent': 19}；calls_by_timing={'night_batch': 14, 'realtime': 5} |
| thinking 标记 | {'decision': {'reasoning_effort=none': 5}, 'intent': {'reasoning_effort=none': 19}} |
| intent 结果 | ok=7 / clarification=4 / reject=8 |
| grounding 拦截 | 3（{'message': 2, 'skill': 1}） |
| dialogue 调用 | slot=0；budget={} |
| 延迟 p50/p95 | decision={'n': 19, 'p50': 0.807, 'p95': 1.125}；intent={'n': 19, 'p50': 0.785, 'p95': 0.938} |
| 费用 / fallback | $0.082321 / 0 |
| state/event hash | `e607d2008e8c53890ab2d30478e09e383d0020bd0f7715a1b81ad51c6eb6767e` / `9a5f070a31857368fe12631362e236796719435b00e84b6fd480a2fbdd0efc54` |

## 3. 确定性 fake run 的世界漂移（金标准基线）

| 指标 | 修复前 | 修复后 |
| --- | ---: | ---: |
| events | 816 | 824 |
| state_final | `b1a7edaff511de1f…` | `1bd870c4c8270da6…` |
| event_final | `8cbab6add8ba1208…` | `4d4019291eaaeb6b…` |
| sell 流入 | 1165 | 849 |
| buy 流出 | 956 | 795 |
| burned | 112 | 113 |
| 商户现金 | 11925 | 12058 |

- **这是两个世界，不是“机制不变”**：18 条原本被前置拦截的 `talk` 现在成为成功事件，其关系/记忆效果改变后续规划，进而改变经济流量。报告已按新世界重新基线。
- 已新增 `tests/test_p3e_golden.py`：锁死事件总数、事件序哈希、state hash、sell/buy/burned/商户现金、拦截/剪枝计数；故意把调度集合改回全量会 FAIL（实测 `event_final` 4d4019…→c20ad8…）。
- 调度选择：全量社交优先 vs **只提前 `talk`** 两套 A/B 的事件数、state、经济流量、K6 完全相同，仅事件序哈希不同；故收窄为只提前 `talk`，把调度改动的面缩到最小。

## 4. K6 非法率（对外结论取含剪枝口径）

口径定义：`已执行 = rejected/events`；`严格 = (rejected+blocked)/events`；`含剪枝 = (rejected+blocked+pruned)/events`（**对外结论**，把提交前剪枝也算进分子，避免“换桶即达标”）。

| run | events | rejected | blocked | pruned | 已执行 | 严格 | 含剪枝（对外） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fake 旧（修复前） | 816 | 0 | 27 | 0 | 0.00% | 3.31% FAIL | 3.31% FAIL |
| **fake 新** | 824 | 0 | 2 | 5 | 0.00% | 0.24% | **0.85% PASS** |
| deepseek 旧（修复前） | 222 | 0 | 2 | 0 | 0.00% | 0.90% FAIL | 0.90% FAIL |
| **deepseek 新** | 222 | 0 | 0 | 2 | 0.00% | 0.00% | **0.90% PASS** |

- **结论（对外）**：含剪枝口径 fake **0.85% PASS**、deepseek **0.90% PASS**。
- **deepseek 的 2 条变化是换桶，不是行为改善**：新 pruned 的 `wen_xiaoman@151`、`cui_tieyi@232` 与旧 blocked 是同一批意图（同主体/同目标/同 tick），只从 blocked 改记到 pruned。
- deepseek 前后是**两次独立采样**，不是受控对照：旧 state `9129402e…`、新 state `e607d200…`；tick 0 的 talk 文案不同。但经济聚合相同（sell=69 / buy=342 / burned=57 / 商户现金=11564），说明差异在文案/关系而非经济行为。

## 5. blocked 逐条对账（旧 27 条 → 新 2 blocked + 5 pruned）

### 5.1 按动作 + 原因

| 动作 | 原因 | 旧 blocked | 新 blocked | 新 pruned | 处置 |
| --- | --- | ---: | ---: | ---: | --- |
| quest_accept | not_at_giver | 2 | 0 | 2 | 提交前剪枝：未在委托人处 |
| quest_deliver | quest_not_accepted_by_actor | 2 | 2 | 0 | 合理拦截：依赖更早已发生的 quest_accept，提案时不可知 |
| sell | item_not_held | 1 | 0 | 0 | 既有 SellAction.preconditions 校验持有量；本轮只接入提案前拦截，未改动作 |
| talk | not_together | 18 | 0 | 0 | 提交前剪枝：位置不一致，直接判不可行（零事件/零副作用） |
| trade | not_together | 4 | 0 | 3 | 提交前剪枝：位置不一致，直接判不可行（零事件/零副作用） |
| **合计** | | **27** | **2** | **5** | |

### 5.2 旧 20 条可捕获样本逐条对账

| # | 动作 | 原因 | 主体 | 旧 tick | 目标/任务 | 新世界去向 | 证据 |
| ---: | --- | --- | --- | ---: | --- | --- | --- |
| 1 | talk | not_together | jin_mancang | 6 | duan_wudou | 成功事件 | seq=42 tick=6 outcome=ok |
| 2 | talk | not_together | liu_sanniang | 7 | cao_banxian | 成功事件 | seq=45 tick=7 outcome=ok |
| 3 | talk | not_together | xing_pojun | 7 | cao_banxian | 成功事件 | seq=46 tick=7 outcome=ok |
| 4 | talk | not_together | duan_wudou | 13 | cao_banxian | 成功事件 | seq=72 tick=13 outcome=ok |
| 5 | sell | item_not_held | jin_mancang | 22 | — | 成功事件 | seq=115 tick=22 outcome=ok |
| 6 | talk | not_together | jin_mancang | 45 | duan_wudou | 成功事件 | seq=205 tick=45 outcome=ok |
| 7 | trade | not_together | meng_jiuniang | 49 | luo_qingxu | 换桶（blocked→pruned） | tick=49 reason=not_together |
| 8 | talk | not_together | duan_wudou | 52 | cao_banxian | 成功事件 | seq=240 tick=52 outcome=ok |
| 9 | talk | not_together | liu_sanniang | 52 | cao_banxian | 成功事件 | seq=241 tick=52 outcome=ok |
| 10 | talk | not_together | xing_pojun | 52 | cao_banxian | 成功事件 | seq=242 tick=52 outcome=ok |
| 11 | trade | not_together | luo_qingxu | 83 | meng_jiuniang | 换桶（blocked→pruned） | tick=83 reason=not_together |
| 12 | talk | not_together | jin_mancang | 89 | duan_wudou | 成功事件 | seq=347 tick=89 outcome=ok |
| 13 | talk | not_together | duan_wudou | 90 | cao_banxian | 成功事件 | seq=353 tick=90 outcome=ok |
| 14 | talk | not_together | liu_sanniang | 97 | jin_mancang | 成功事件 | seq=376 tick=97 outcome=ok |
| 15 | talk | not_together | jin_mancang | 129 | duan_wudou | 成功事件 | seq=465 tick=129 outcome=ok |
| 16 | talk | not_together | duan_wudou | 134 | cao_banxian | 成功事件 | seq=482 tick=134 outcome=ok |
| 17 | talk | not_together | liu_sanniang | 139 | cao_banxian | 未再提交 | 新 run 无同主体/同目标事件，也无拦截记录 |
| 18 | trade | not_together | cao_banxian | 148 | xing_pojun | 未再提交 | 新 run 无同主体/同目标事件，也无拦截记录 |
| 19 | talk | not_together | yan_shouyi | 149 | cao_banxian | 成功事件 | seq=588 tick=149 outcome=ok |
| 20 | talk | not_together | ruan_qingfu | 150 | fang_cunxin | 成功事件 | seq=595 tick=150 outcome=ok |

- 20 条捕获样本：成功事件 **16**、换桶 **2**、仍 blocked **0**、未再提交 **2**。
- 旧 27 条中另有 **7 条**只有计数、没有 actor/tick（examples 上限），按 §5.1 的差值对账：1 条 trade、2 条 quest_accept 落入新 pruned，2 条 quest_deliver 落入新 blocked；2 条 talk 没有 actor/tick，无法逐条定位。
- 判定依据：新 run 的 `events.sqlite` + `pruned.jsonl` + `blocked.jsonl` 覆盖全部提案；三者皆无即“从未提交”。

### 5.3 新 residual 全量记录（不截断，导出为 jsonl）

| 类别 | 动作 | 原因 | 主体 | tick | 参数 |
| --- | --- | --- | --- | ---: | --- |
| blocked | quest_deliver | quest_not_accepted_by_actor | yan_shouyi | 158 | `{"quest": "quest:debt_duan_to_jin:0", "quest_verb": "trade"}` |
| blocked | quest_deliver | quest_not_accepted_by_actor | ruan_qingfu | 160 | `{"quest": "quest:errand_academy_notice:0", "quest_verb": "trade"}` |
| pruned | trade | not_together | meng_jiuniang | 49 | `{"money_delta": 0, "offer": [{"item": "silk", "qty": 1}], "target": "luo_qingxu", "want": []}` |
| pruned | trade | not_together | luo_qingxu | 83 | `{"money_delta": 5, "offer": [], "target": "meng_jiuniang", "want": []}` |
| pruned | quest_accept | not_at_giver | yan_shouyi | 152 | `{"quest": "quest:debt_duan_to_jin:0", "quest_verb": "trade"}` |
| pruned | trade | not_together | yan_shouyi | 155 | `{"money_delta": 300, "offer": [], "quest": "quest:debt_duan_to_jin:0", "target": "duan_wudou", "want": []}` |
| pruned | quest_accept | not_at_giver | ruan_qingfu | 156 | `{"quest": "quest:errand_academy_notice:0", "quest_verb": "trade"}` |

- 导出文件：`reports/evidence/p3e-fake-llm-2d-pruned-k6r2.jsonl`、`...-blocked-k6r2.jsonl`（deepseek 同名 `-k6r2` 各一份）；`summary.precondition_pruned.records` 共 5 条、`precondition_blocked.records` 共 2 条。

## 6. 复现链

- 事件日志回放：`reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6r2` → events=222，replayed=`e607d2008e8c53890ab2d30478e09e383d0020bd0f7715a1b81ad51c6eb6767e`，stored=`e607d2008e8c53890ab2d30478e09e383d0020bd0f7715a1b81ad51c6eb6767e`，**MATCH**
- 仅 intent 槽录制重跑：events=220，state=`7a5ce1bd69e45283f2d098bd173ce8dad6e5c04580a96ba09146a5b94efe7988`，event=`a35cd8bd48d3a8bf7e51d0a0406a68a2af3988c0e922a7c66759f1efcc113820`，intent_calls=19，replay_miss=18，intent_reject=19，blocked=0，pruned=2，cost_calls=38，fallbacks=0
- 结论：事件日志可回放到同一 state hash；整跑不可复现（decision 槽未录制）。

## 7. 意图录制复算

- 19 条；prompt/completion tokens = 25898/354；p50=0.823s，p95=0.952s
