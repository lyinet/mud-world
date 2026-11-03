# 意图模板覆盖（真实 intent 槽）

- 生成时间：2026-11-04T03:03:25+0800；耗时 22.68s
- 模型：`deepseek/deepseek-flash`；extra_body=[{'reasoning_effort': 'none'}]；base=http://127.0.0.1:3425/v1
- 样本 34 条，覆盖 17 个模板（≥2 条/模板：17 个），成功 25/34（73.53%）
- 成功口径：类别一致 + `ok`/`needs_confirmation` + 无 missing 必填字段

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
| 入门派 | join_sect | 2 | 1 | 50% | ok=1, reject=1 |
| 开宗立派 | found_sect | 2 | 2 | 100% | needs_confirmation=2 |
| 开店 | open_shop | 2 | 0 | 0% | needs_clarification=2 |
| 护送 | escort | 2 | 2 | 100% | ok=2 |
| 接任务 | quest_accept | 2 | 1 | 50% | ok=1, reject=1 |
| 交任务 | quest_deliver | 2 | 1 | 50% | ok=1, reject=1 |
| 对话 | talk | 2 | 1 | 50% | ok=2 |
| 还债 | debt_settle | 2 | 2 | 100% | needs_confirmation=2 |

## 失败样本

- `吃饭` / consume：喝一壶浊酒暖暖身子 → status=reject category=unknown missing=[] blocked=[] dropped=[] reason=unknown_category
- `修炼` / cultivate：练两个时辰绣娘手艺 → status=reject category=unknown missing=[] blocked=[] dropped=[] reason=unknown_category
- `修炼` / cultivate：练四个时辰绣娘针法 → status=reject category=unknown missing=[] blocked=[] dropped=[] reason=unknown_category
- `入门派` / join_sect：我要入清议社 → status=reject category=unknown missing=[] blocked=[] dropped=[] reason=unknown_category
- `开店` / open_shop：在集市摆个摊位卖炊饼 → status=needs_clarification category=开店 missing=['shop_type'] blocked=[{'field': 'shop_type', 'raw': 'stall'}] dropped=[] reason=grounding_missing_required
- `开店` / open_shop：在客栈盘一间店铺 → status=needs_clarification category=开店 missing=['shop_type'] blocked=[{'field': 'shop_type', 'raw': 'stall'}] dropped=[] reason=grounding_missing_required
- `接任务` / quest_accept：接「送一匹绸样」这趟差事 → status=reject category=unknown missing=[] blocked=[] dropped=[] reason=unknown_category
- `交任务` / quest_deliver：把送一匹绸样交了 → status=reject category=unknown missing=[] blocked=[] dropped=[] reason=unknown_category
- `对话` / talk：对金满仓说「这批铁器我全要了」 → status=ok category=交易 missing=[] blocked=[] dropped=['want'] reason=unknown
