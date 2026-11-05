# W4 店铺经营 UI 规格（进货 / 定价 / 辞退 / 提现 / 7 日账本）

## 1. 目标与边界

在「商业与经营 → 店铺」页签内补齐店主操作与 7 日账本。后端五个动作由另一会话实现，
本规格只做前端与 mock 验收，不改 `src/**`、`content/**`、`sim/**`。

允许改动：

- `ui/player/index.html`、`ui/player/app.js`、`ui/player/style.css`
- `tools/ui_check.py`、`tools/ui_check_real_shopops.py`（新建）
- `reports/w4-shopops/**`（证据）
- `docs/**`（本规格、`docs/PLAYER-GUIDE.md` 玩家说明）
- `CHANGELOG.md`

禁止：改后端、伪造真服务成功、回滚其他会话的未提交 reports、零模型调用。

## 2. 后端契约（只读引用，不实现）

| 动作 | 参数 | 成功要点 | 拒绝码 |
|---|---|---|---|
| `restock` | `{item, qty}` | `total`、`stock_before/after` | `not_shop_owner` / `not_enough_money` / `bad_arg:qty` / `item_not_available` |
| `set_price` | `{item, price}` | `price`、`base_price`、`delta_pct` | `price_out_of_range` / `not_shop_owner` / `item_not_available` |
| `dismiss` | `{target}` | `wage_owed` | `not_shop_owner` / `target_not_employed` |
| `withdraw` | `{amount}` | `shop_cash_after` | `insufficient_shop_cash` / `bad_arg:amount` / `not_shop_owner` |
| `shop_report` | `{}` | `days[]`（`day/revenue/restock/wage/tax/net_profit/trades`） | — |

## 3. 状态字段与兼容口径

后端 `state.shop` 已有 `businesses[]` / `cash` / `employees[]` / `stock[]` / `stock_count`
（`docs/player-api.md` §3.2）。本 UI 读取顺序：

- 店主与现金：`shop.businesses[]` 内 `id == "shop:player:<account>"`（沿用 `ownShopOf`）；
  现金优先 `shop.cash`，缺失回退 `own.cash`。
- 雇工：优先 `shop.employees[]`，缺失回退 `own.employees[]`。
- 库存/定价行：优先 `shop.stock[]`（每行 `item/name/qty`，可选 `price`/`base_price`/
  `restock_price`/`sell_price`）。`price` 缺失时用 `sell_price`；`base_price` 缺失时用
  `price` 或 `content` 同价；`restock_price` 缺失时用 `base_price` 或 `price`。
  UI 显示「以服务端结算为准」仅用于完全无价格字段的旧世界。
- 账本：优先 `shop.report.days[]`；也接受 `shop.report[]` 或顶层 `shop_report.days[]`。

mock 使用与上述同名的最完整形状（`shop.cash`/`employees`/`stock`/`report`），
真服务若字段较少则按回退口径展示，不报错。

## 4. 界面结构

### 4.1 店铺页签（`#pane-business`）

按顺序：

1. `#ownShopInfo`：保留现有店铺摘要，追加「店现」。
2. **进货** `#restockSection`
   - `#restockList`：每件库存一行，显示名称、现有库存、进货单价、数量输入、合计。
   - 数量输入 `data-restock-qty="<item>"`；合计 `#restockTotal`。
   - 按钮 `data-restock-go`，文案「进货」。
3. **定价** `#priceSection`
   - `#priceList`：每件库存一行，显示当前价、基准价、`±20%` 输入（`type=number`）
     与滑块（`input[type=range]`），实时显示 `±%`；按钮「改价」。
   - 价格范围 = `floor(base*0.8)` ~ `ceil(base*1.2)`；非法即时提示并禁用。
   - 后果提示必须可见：「当前基准价 X，你的价 Y，比基准高/低 Z%」，
     并附「定价过高可能没人买；过低则每件赚得少」。
4. **店内雇工** `#employeeList`：每行追加「辞退」按钮（`data-dismiss`）。
   点击打开二次确认 `#dismissModal`，文案写明「欠薪仍计，辞退后不自动结清」。
5. **提现** `#withdrawSection`
   - `#withdrawAmount` 数字输入、`#shopCashNow` 当前店现、按钮「提现」。
6. **7 日账本** `#ledgerSection`
   - `#ledgerTable`：列 世界日 / 营收 / 进货 / 工资 / 税 / 净利 / 笔数。
   - 净利正数 `.profit-pos`（绿），负数 `.profit-neg`（红）。
   - 净利为负的行尾追加简短原因（由该行数值推导，不写死）：
     - `wage > revenue` → 「工资高于营收」
     - `revenue <= 0` → 「当日无客流」
     - `restock >= revenue` 且 `restock > 0` → 「进货占去营收」
     - 其余 → 「入不敷出」
   - 底部 `#ledgerTotal`：额外一行「7 日合计」（营收/进货/工资/税/净利/笔数），
     与该表 7 行数值求和一致。
   - 空态 `#ledgerEmpty`：「尚无日结记录，世界日结束后来看」。真服务若返回空数组，
     如实显示空态并截图，不等到有数据再截。

### 4.2 交互状态

- 禁用（无店/非店主）：四个操作区与辞退按钮全部 `disabled`，并显示原因
  「你还没有店铺」或「这家店铺不是你的」；账本显示空态。
- 进行中：每个动作写 `state.commerce.shopops.busy[verb+item/target] = true`；
  按钮 `disabled` + 文案「处理中…」；`finally` 释放；同一动作重复点击直接忽略。
- 成功提示必须带数字：
  - 进货：`进货 炊饼×6，花费 18 文，库存 4 → 10`
  - 改价：`炊饼 3 → 4 文（+33%）`
  - 辞退：`已辞退 严守一，欠薪 18 文仍计`
  - 提现：`提现 20 文，店现 100 → 80`
- 失败统一走 `notifyError` / `reasonText`，新增原因码补进 `REASON_TEXT`：
  `price_out_of_range`、`insufficient_shop_cash`、`target_not_employed`、
  `bad_arg:price`、`bad_arg:amount`；`not_shop_owner` 文案改为「这家店铺不是你的，不能经营」。

### 4.3 移动端

- 375×812：账本容器 `overflow-x:auto`，表格 `min-width` 固定，页面 `scrollWidth` 不超 `innerWidth`。
- 所有新增按钮/输入/滑块 `min-height:44px`（滑块用外层 44px 触控区）。
- 表格首列可粘性定位，横向滚动不挤压其余面板。

## 5. 验收

### 5.1 `tools/ui_check.py --only shopops`（≥12 项）

mock 扩展：

- `_shop_view` 输出 `shop.cash/employees/stock[]/report`；开店后库存与价格齐备。
- `act` 实现五个动作与契约拒绝码；`dismiss` 移除雇工并累计 `wage_owed`；
  `shop_report` 返回 7 日样例（含正负净利与 `trades`）。
- 新增 `/__check/shop_report` 测试路由，用于注入空账本/7 日样例（CHECK_MODE 仅测试）。

检查项（至少）：

1. 无店时四区禁用且提示无店。
2. 开店后四区启用，店现/库存/价格可见。
3. 进货成功：数字文案与库存 4→10。
4. 进货拒绝 `bad_arg:qty` 映射中文。
5. 进货拒绝 `not_enough_money` 映射中文。
6. 改价成功：显示 `3 → 4` 与 `+33%`。
7. 改价拒绝 `price_out_of_range` 映射中文。
8. 辞退二次确认含「欠薪仍计」，确认成功并显示欠薪数字。
9. 辞退拒绝 `target_not_employed` 映射中文。
10. 提现成功：店现 100 → 80。
11. 提现拒绝 `insufficient_shop_cash` / `bad_arg:amount` 映射中文。
12. 账本 7 行渲染；合计正确。
13. 净利正数绿色、负数红色（computed style）。
14. 空账本显示「尚无日结记录，世界日结束后来看」。
15. 375×812 不横向溢出、新增控件 ≥44px、账本能横滚。
16. 防重复点击：请求挂起时按钮 `disabled` 且只发一次请求。

### 5.2 `tools/ui_check_real_shopops.py`

真 `wsim.api` + 真代理 + ambient（seed 42、4x），数据根 `/tmp/xkx-w4-shopops`：

- 能力探测：先显式探测五个动作是否存在于当前 master（优先调 `shop_report`，再看五个动作
  是否返回 `unknown_action` / `not_implemented_for_player` / HTTP 404）。任一缺失即写
  `blocked_backend_not_ready` 并退出 0，跳过真服务流程；**禁止用 mock 数据顶替真服务证据**。
- 就绪路径：进入 → 开摊 → 进货 → 改价 → 等一个世界日日结 → 账本出现一行 →
  提现 → 辞退；每步截图与请求/响应落 `reports/w4-shopops/real-*.png|json`。
- 账本为空（开店不足一日或日结未到）：如实渲染空态并截图存证，不延长等待以凑数据；
  证据 JSON 标记 `ledger_empty: true`，其余动作照常执行。

## 6. 提交与纪律

两笔显式 pathspec：

1. `ui/player/**` + `tools/ui_check.py` + `reports/w4-shopops/mock-*` +
   `docs/PLAYER-GUIDE.md` + `docs/w4-shopops-spec.md` + `CHANGELOG.md`（第①条）。
2. `tools/ui_check_real_shopops.py` + `reports/w4-shopops/real-*` + `CHANGELOG.md`（第②条）。

各自 push。零模型调用；不动后端；不 stage 其他会话的未提交文件。
