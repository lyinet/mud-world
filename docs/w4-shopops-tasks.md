# W4 店铺经营 UI 任务清单

- [ ] 1. `ui/player/index.html`：`#pane-business` 增加进货、定价、提现、账本区块；
  `#employeeList` 行加辞退按钮；新增 `#dismissModal` 二次确认。
- [ ] 2. `ui/player/app.js`
  - 状态：`state.commerce.shopops = {qty:{}, price:{}, busy:{}, report:null, reportLoaded:false}`、`dismissTarget`。
  - 读取助手：`shopCashOf`、`shopEmployeesOf`、`shopStockRowsOf`、`shopReportOf`、
    `restockPriceOf`、`priceBoundsOf`。
  - 渲染：`renderRestockSection`、`renderPriceSection`、`renderWithdrawSection`、
    `renderLedger`；在 `renderCommerce` 调用。
  - 动作：`doRestock`、`doSetPrice`、`doOpenDismiss`/`doDismiss`、`doWithdraw`、
    `loadShopReport`；复用 `commerceAction`，成功文案带数字。
  - 防重复：`withBusy(key, fn)` 只允许一次在途；按钮 disabled +「处理中…」。
  - 原因码：`REASON_TEXT` 补 `price_out_of_range`、`insufficient_shop_cash`、
    `target_not_employed`、`bad_arg:price`、`bad_arg:amount`；修正 `not_shop_owner`。
  - `CHECK_MODE` 暴露 `window.__XKX.shopOpsState()` 供断言。
- [ ] 3. `ui/player/style.css`：账本表格、正负净利、横向滚动、粘性首列、
  44px 控件、忙碌态；375×812 媒体查询。
- [ ] 4. `tools/ui_check.py`
  - mock：`shop` 增加 `cash/employees/stock[]/report`；`act` 实现五动作与拒绝码；
    `/__check/shop_report` 注入样例。
  - 新增 `run_shopops` 与 `--only shopops`、help 文案、main 分组、证据
    `reports/w4-shopops/mock-*` + `mock-shopops.json`。
  - ≥18 项断言（规格 §5.1 的 18 项，含 7 日合计、负净利原因、定价后果提示、能力探测）。
- [ ] 5. 跑 `--only shopops`、全量 `ui_check.py`、`ui_check_proxy.py`、pytest。
- [ ] 6. `docs/PLAYER-GUIDE.md` 增「店铺经营」玩家操作说明；`CHANGELOG.md` 第①条。
- [ ] 7. commit①（显式 pathspec）+ push。
- [ ] 8. 新建 `tools/ui_check_real_shopops.py`：显式五动作能力探测，缺失即
  `blocked_backend_not_ready` 并跳过（禁止 mock 顶替）；就绪时跑全流程 + 每步证据。
- [ ] 9. 跑真服务；账本为空时如实截空态（`ledger_empty: true`），不延长等待凑数据。
- [ ] 10. `CHANGELOG.md` 第②条；commit②（显式 pathspec）+ push。
