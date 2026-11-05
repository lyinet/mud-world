# 验收清单：店铺经营动作与盈利性（v2）

- [x] 五个动作在真实服务各有 1 次成功事件：`restock` seq793 / `set_price` seq127 /
      `dismiss` seq4151 / `withdraw` seq4149 / `shop_report` 11 次。
- [x] 五个动作各有 1 条拒绝事件并覆盖“钱不够”“不是店主”：
      `restock` bad_arg:qty(4135) + no_shop(4159)；`set_price` price_out_of_range(4141)；
      `dismiss` not_employed(4153)；`withdraw` insufficient_shop_cash(4145)；
      `shop_report` 短程服务 seq17 no_shop；短程服务五动作 no_shop（seq 9/11/13/15/17）。
- [x] `shop_report` 最近 7 日与 `events.sqlite::shop_history` 逐字段一致（day5–11）。
- [x] `set_price` 限制在基准价 ±20%，超出返回 `price_out_of_range`；`price<=0` 恢复基准。
- [x] 定价影响成交：同地点两家店不同价时买主选便宜的（单测
      `test_buyer_picks_cheaper_shop_when_prices_differ`）；+20% 后成交笔数下降会体现在
      `shop_report.tx`（分号+3 人：16.9→13.7 笔/日）。
- [x] `dismiss` 解除契约并清空未来工资记录，但已产生的欠薪债务保留（单测
      `test_dismiss_keeps_unpaid_wage_debt` + 事件 seq4151）。
- [x] `withdraw` 真实 `transfer(reason=withdraw)`，不得超过店现（seq4149 成功 /
      seq4145 insufficient_shop_cash）。
- [x] `restock` 真实 `transfer` + `item_create(reason=restock)`，返回总价与库存前后。
- [x] 真实服务 branch（3000 文）+3 雇工 10 日逐日给出
      `revenue/restock/wage/tax/net_profit/tx`，且存在连续 3 日 `net_profit > 0`（day4/5/6）。
- [x] 盈亏平衡表覆盖摊/店面/分号三档：日客流、最优雇工人数、保本价位/保本客流。
- [x] 结论不是“无论怎么配都亏”：摊档单人 +18.6/日、店面自营 +107.1/日、
      分号 +3 人 +93.6/日（受控 10 日，经济净利口径）。
- [x] 未改 `content/**` 数值、未降工资；机制改动仅客流/两篮/技能产出，且写明原因。
- [x] 无玩家 2 日 golden `state_final`/`event_final` 与基线 `21f107a` 逐字节一致
      （`state_final.json` sha256 `91605d57…`、`events.jsonl` sha256 `1958e73b…`，735 行）。
- [x] 无玩家 10 日 seed42 `events 3084`、`state_final 68b05948…`、
      `event_final 73026568…`、K1 68.13% 与基线一致（旧基线不 skip，无新基线）。
- [x] 同 seed 两次一致（玩家店世界 s1/s2 的 events/hashes/K1/份额相同）。
- [x] 真实服务事件回放守恒 `delta=0`（accounts 50128 = minted 51350 − burned 1222）。
- [x] NPC 侧 10 日对照：NPC 合计 547→580（+6.0%），`buy` 114→177（+55.3%），
      NPC 店零售营收 2375→4036（+69.9%）；玩家店份额 3.97% <10%。
- [x] `api/session.py` 只动白名单/别名/`_args`/`shop_report` 透传，未改既有视图结构。
- [x] 未碰 `ui/**`、`sim/scheduler.py`、`content/**`。
- [x] 全量 `pytest` 573 passed, 18 skipped；1 项上游既有失败
      `test_determinism_lint`（`177749d` 的 `wsim/admin` 引入 `time.time`，与本轮无关）。
- [x] 两笔提交（动作集 `3d0ceab` / 验证文档）各自 push。
