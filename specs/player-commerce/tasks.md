# player-commerce 任务清单

- [ ] 1. 基线采样：先跑现有 `tests/test_p3e_golden.py`，记录 V3 数值是否因本次改动漂移。
- [ ] 2. `src/wsim/actions/player.py`：`PLAYER_VERBS` 加入 `buy` / `sell` / `consume` / `escort`。
- [ ] 3. `src/wsim/api/session.py`：
  - `WRITE_ACTIONS` / `ACTION_ALIASES` 加入四动词；
  - `PLAYER_UNIMPLEMENTED_VERBS = {"marry","found_sect","apprentice"}` 与 `not_implemented_for_player`；
  - `_args` 增加 buy/sell/consume/escort 参数映射（`item`/`qty`/`from`/`to`/`route`/`route_id`/`cargo_value`/`cargo`/`fee`）；
  - `REASON_TEXT` 补齐新码与押镖旧码、`bad_arg:*` 文案。
- [ ] 4. `src/wsim/actions/commerce.py`：`BuyAction`/`SellAction` 对 `player:` 主体返回
  `not_here` / `item_not_available` / `shop_closed` / `not_a_merchant` / `not_enough_money` / `item_missing`；
  NPC 分支保持旧码。
- [ ] 5. `src/wsim/actions/basic.py`：`ConsumeAction` 对 `player:` 主体返回
  `shop_closed` / `not_a_merchant` / `item_not_available` / `not_enough_money`；NPC 分支不变。
- [ ] 6. `src/wsim/actions/escort.py`：玩家起点不符返回 `not_here`；其余码与效果不变。
- [ ] 7. `src/wsim/api/session.py` 只读视图：
  - `_location_view(loc_id, pid)` 新增 `services_detail`（price/sell_price/tax/stock/buy_limit/sell_limit/available）；
  - `player_state` 新增顶层 `shop`（businesses/buy/sell）；
  - `player.wallet.items` 增 `price`/`sell_price`；现有键与匿名白名单不动。
- [ ] 8. 新增 `tests/test_player_commerce.py`：
  - 正常买/吃/卖：钱包、库存、商家现金、税分流、守恒；
  - 负面路径 ≥5 个码；NPC 专属三动词 `not_implemented_for_player`；
  - 镖师押镖参数入库与到达结算；
  - `state` 价格字段样例与 `services` 原样不变；
  - 一条采购委托（买→交）端到端。
- [ ] 9. 基线：`tests/test_p3e_golden.py` 保留 V3 数值并 skip，新增 V4 常量与两条测试；
  按实跑结果逐项注释 events/经济流量/拦截计数变化（零漂移也要写原因）。
- [ ] 10. 文档：`docs/player-api.md` 增动词/参数/原因码/state 字段；`CHANGELOG.md` 一条。
- [ ] 11. 验收：真实起服务跑买→吃→卖、押镖、负面路径、state 价格样例、5 单采购委托。
- [ ] 12. 全量 `PYTHONPATH=src python -m pytest`、`./scripts/selfcheck.sh`。
- [ ] 13. 50 客户端并发 P95 实测，与 22.6ms 对照；守恒/seq/无半写复核。
- [ ] 14. 显式 pathspec 提交、push；干净 HEAD (`git archive`) 复验 pytest/selfcheck；回报 SHA。
</content>
</file_path>
