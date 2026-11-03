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
| intent 结果 | ok=10 / clarification=2 / reject=7 |
| grounding 拦截 | 1（{'message': 1}） |
| dialogue 调用 | slot=0；budget={} |
| 延迟 p50/p95 | decision={'n': 19, 'p50': 0.911, 'p95': 1.209}；intent={'n': 19, 'p50': 0.72, 'p95': 0.883} |
| 费用 / fallback | $0.081921 / 0 |
| state/event hash | `28b733facdcf8044b75f74fc0568ffd4da642579b3f85ec388dd98adb809f452` / `b327360550d00c48f430045d4d353e48bd77439f62477cee22b36357f130c6f5` |

## 3. K6 非法率（两套 2 天 run，三种口径）

口径定义：`已执行 = rejected/events`；`严格 = (rejected+blocked)/events`（结论口径）；`含剪枝 = (rejected+blocked+pruned)/events`（防止“把违规移出指标”的质疑）。

| run | events | rejected | blocked | pruned | 已执行 | 严格 | 含剪枝 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fake 旧（修复前） | 816 | 0 | 27 | 0 | 0.00% | 3.31% FAIL | n/a |
| fake 新（修复后） | 824 | 0 | 2 | 5 | 0.00% | 0.24% PASS | 0.85% PASS |
| deepseek 旧（修复前） | 222 | 0 | 2 | 0 | 0.00% | 0.90% FAIL | n/a |
| deepseek 新（修复后） | 222 | 0 | 0 | 2 | 0.00% | 0.00% PASS | 0.90% PASS |

## 4. blocked 逐条分布（旧 27 条 → 新 residual）

### 4.1 修复前后按动作 + 原因对比

| 动作 | 原因 | 旧 blocked | 新 blocked | 新 pruned | 处置 |
| --- | --- | ---: | ---: | ---: | --- |
| quest_accept | not_at_giver | 2 | 0 | 2 | 提交前剪枝：未在委托人处 |
| quest_deliver | quest_not_accepted_by_actor | 2 | 2 | 0 | 合理拦截：前置不可知（依赖当日规划是否成功接取） |
| sell | item_not_held | 1 | 0 | 0 | 可由 sell 持有量前置校验消除 |
| talk | not_together | 18 | 0 | 0 | 提交前剪枝：位置不一致，直接判不可行（零事件/零副作用） |
| trade | not_together | 4 | 0 | 3 | 提交前剪枝：位置不一致，直接判不可行（零事件/零副作用） |
| **合计** | | **27** | **2** | **5** | |

### 4.2 旧 27 条逐条明细（summary 的 examples 上限 20 条）

| # | 动作 | 原因 | actor | tick | source |
| ---: | --- | --- | --- | ---: | --- |
| 1 | talk | not_together | jin_mancang | 6 | llm |
| 2 | talk | not_together | liu_sanniang | 7 | llm |
| 3 | talk | not_together | xing_pojun | 7 | llm |
| 4 | talk | not_together | duan_wudou | 13 | llm |
| 5 | sell | item_not_held | jin_mancang | 22 | planner |
| 6 | talk | not_together | jin_mancang | 45 | llm |
| 7 | trade | not_together | meng_jiuniang | 49 | planner |
| 8 | talk | not_together | duan_wudou | 52 | llm |
| 9 | talk | not_together | liu_sanniang | 52 | llm |
| 10 | talk | not_together | xing_pojun | 52 | llm |
| 11 | trade | not_together | luo_qingxu | 83 | planner |
| 12 | talk | not_together | jin_mancang | 89 | llm |
| 13 | talk | not_together | duan_wudou | 90 | llm |
| 14 | talk | not_together | liu_sanniang | 97 | llm |
| 15 | talk | not_together | jin_mancang | 129 | llm |
| 16 | talk | not_together | duan_wudou | 134 | llm |
| 17 | talk | not_together | liu_sanniang | 139 | llm |
| 18 | trade | not_together | cao_banxian | 148 | planner |
| 19 | talk | not_together | yan_shouyi | 149 | llm |
| 20 | talk | not_together | ruan_qingfu | 150 | llm |

剩余 7 条未落入 examples 上限，按上表计数分布统计。

### 4.3 新 residual 逐条明细

| 类别 | # | 动作 | 原因 | actor | tick | source |
| --- | ---: | --- | --- | --- | ---: | --- |
| blocked | 1 | quest_deliver | quest_not_accepted_by_actor | yan_shouyi | 158 | planner |
| blocked | 2 | quest_deliver | quest_not_accepted_by_actor | ruan_qingfu | 160 | planner |
| pruned | 1 | trade | not_together | meng_jiuniang | 49 | planner |
| pruned | 2 | trade | not_together | luo_qingxu | 83 | planner |
| pruned | 3 | quest_accept | not_at_giver | yan_shouyi | 152 | planner |
| pruned | 4 | trade | not_together | yan_shouyi | 155 | planner |
| pruned | 5 | quest_accept | not_at_giver | ruan_qingfu | 156 | planner |

## 5. 复现链

- 事件日志回放：`reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6fix` → events=222，replayed=`28b733facdcf8044b75f74fc0568ffd4da642579b3f85ec388dd98adb809f452`，stored=`28b733facdcf8044b75f74fc0568ffd4da642579b3f85ec388dd98adb809f452`，**MATCH**
- 仅 intent 槽录制重跑：events=220，state=`7a5ce1bd69e45283f2d098bd173ce8dad6e5c04580a96ba09146a5b94efe7988`，event=`a35cd8bd48d3a8bf7e51d0a0406a68a2af3988c0e922a7c66759f1efcc113820`，intent_calls=19，replay_miss=18，intent_reject=19，blocked=0，pruned=2，cost_calls=38，fallbacks=0
- 结论：事件日志可回放到同一 state hash；整跑不可复现（decision 槽未录制）。

## 6. 意图录制复算

- 19 条；prompt/completion tokens = 25898/354；p50=0.823s，p95=0.952s
