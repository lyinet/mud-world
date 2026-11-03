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
| events / rejected / precondition_blocked | 222 / 0 / 2 |
| 非法率（已执行动作口径） | 0/222 = 0.00% |
| 非法率（违规尝试口径，严格） | (0+2)/222 = 0.90% |
| K1 | 7/91 = 7.69%（k1_pass=False） |
| 动作来源 | model=0.7% / rule=99.3% |
| LLM 调用 | {'decision': 19, 'intent': 19}；calls_by_timing={'night_batch': 14, 'realtime': 5} |
| thinking 标记 | {'decision': {'reasoning_effort=none': 5}, 'intent': {'reasoning_effort=none': 19}} |
| intent 结果 | ok=4 / clarification=4 / reject=11 |
| grounding 拦截 | 3（{'hours': 1, 'message': 2}） |
| dialogue 调用 | slot=0；budget={} |
| 延迟 p50/p95 | decision={'n': 19, 'p50': 0.995, 'p95': 1.552}；intent={'n': 19, 'p50': 0.823, 'p95': 0.952} |
| 费用 / fallback | $0.082242 / 0 |
| state/event hash | `9129402e7b8a516705809aacf626b746fab659f8b3ab9067b0c6d1fe55ca647a` / `65fb7c2a506d6fcd1f6140fbb09bb90d8d17c5fc53b18829005ba312e64e4db7` |

## 3. K6 非法率两种口径（2 天假端点）

| 口径 | 公式 | 值 | 门槛 2% |
| --- | --- | ---: | --- |
| 已执行动作 | rejected / events | 0/816 = 0.00% | PASS |
| 违规尝试（严格，结论） | (rejected + blocked) / events | (0+27)/816 = 3.31% | FAIL |

- 拦截明细：{'quest_accept': {'not_at_giver': 2}, 'quest_deliver': {'quest_not_accepted_by_actor': 2}, 'sell': {'item_not_held': 1}, 'talk': {'not_together': 18}, 'trade': {'not_together': 4}}
- `summary.verdict.k6_illegal_pass`（严格）= False；`k6_illegal_executed_pass` = True

## 4. 复现链

- 事件日志回放：`reports/evidence/runs/p3e-deepseek-real-2d-12npc` → events=222，replayed=`9129402e7b8a516705809aacf626b746fab659f8b3ab9067b0c6d1fe55ca647a`，stored=`9129402e7b8a516705809aacf626b746fab659f8b3ab9067b0c6d1fe55ca647a`，**MATCH**
- 仅 intent 槽录制重跑：events=220，state=`7a5ce1bd69e45283f2d098bd173ce8dad6e5c04580a96ba09146a5b94efe7988`，event=`53c1460f9d9dd7e5b031470cc56d8d6e1b86b3eb043feec1de8a933238d74cab`，intent_calls=19，replay_miss=19，intent_reject=19，cost_calls=38，fallbacks=0
- 结论：事件日志可回放到同一 state hash；整跑不可复现（decision 槽未录制）。

## 5. 意图录制复算

- 19 条；prompt/completion tokens = 25898/354；p50=0.823s，p95=0.952s
