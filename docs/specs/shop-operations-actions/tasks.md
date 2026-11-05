# 实施任务：店铺经营动作与 branch 盈利性（v2）

基线 `21f107a`；越界文件已获批准：`state.py` / `registry.py` / `player.py` / `commerce.py` / `intent.py`。
动作集提交 `3d0ceab`；验证文档与 CHANGELOG 为第二笔（`origin/shop-operations`）。

## Phase 1：基础设施（先证零污染，再谈盈利）

- [x] T1 `state.py::Business`：`price_overrides` / `day_history` 仅非空序列化；`day_customers` 运行期。
- [x] T2 `effects.py`：`shop_dismiss` / `shop_price` / `shop_history`；`transfer` 归类
      （`restock`→进货、`withdraw` 不计、玩家店 `buy/consume`→营收+客流）；`shop_day_close` 清零客流。
- [x] T3 `common.py`：`shop_price()`；`selling_shops` 最低价优先、同价轮转。
- [x] T4 无玩家 2 日 golden：`state_final` / `event_final` 对 `21f107a` 逐字节一致。

## Phase 2：五个动作 + API

- [x] T5 `business.py`：`RestockAction` / `SetPriceAction` / `DismissAction` / `WithdrawAction` / `ShopReportAction`。
- [x] T6 `registry.py` 注册 5 动作；`player.py::PLAYER_VERBS` 加 5 verb。
- [x] T7 `api/session.py`：`WRITE_ACTIONS` / `ACTION_ALIASES` / `_args`；仅 `shop_report` details 透传。

## Phase 3：定价 / 客流机制

- [x] T8 `commerce.py::BuyAction`：按卖家当店价成交。
- [x] T9 `intent.py::_propensity_buy`（仅 player_market_active）：
      口粮篮 + 耐用品篮各 1 笔/日，`price*3<=cash`，确定性轮转。
- [x] T10 `intent.py::_player_shop_trip` 用 `shop_attraction(kind, present)`；`business.py` 提供常数与产出按技能。
- [x] T11 `build_shop_settlements` 行内加 `tx/customers` 并产 `shop_history`。

## Phase 4：测试

- [x] T12 五动作成功 + 拒绝各一；守恒/边界/欠薪保留/7 日对账。
- [x] T13 同地点不同价 → 买主选便宜店；同价轮转。
- [x] T14 全量 `pytest`：573 passed / 18 skipped；仅 1 项上游既有失败
      `test_determinism_lint`（`177749d` 的 `wsim/admin` 引入 `time.time`，与本轮无关）；
      无玩家 golden 字节一致；同 seed 两次一致。

## Phase 5：真实服务 10 日

- [x] T15 branch 3000+3 人 10 日：逐日六项 + 逐笔明细 + `shop_report` 对账。
- [x] T16 摊档单人对照 10 日。
- [x] T17 NPC 保护：同 seed 无玩家 10 日基线 vs 有玩家，NPC `buy/consume/sell` 总量与玩家份额；
      份额 >10% 必须解释，NPC 店总量下降 >10% 则启用同品类轮转重跑。
- [x] T18 硬条件：连续 ≥3 日净利 >0；否则按 §5 调机制或给“摊档单人盈利/雇人需升档”结论。
- [x] T19 护栏：守恒 delta=0、K1 ≥63.13%、旧基线 skip + 新基线归因。

## Phase 6：文档 / 提交

- [x] T20 `docs/shop-operations.md` 更新；`docs/shop-profitability.md` 10 日流水 + 盈亏平衡表 + 结论。
- [x] T21 `CHANGELOG.md` 记录动作集 `3d0ceab` 与本笔验证文档。
- [x] T22 两笔提交（动作集 / 机制+验证文档），显式 pathspec，各自 push，回报 SHA。
