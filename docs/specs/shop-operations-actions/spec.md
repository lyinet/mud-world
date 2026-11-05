# 店铺经营动作与 branch 盈利性验证（Spec，v2）

状态：待批准（Spec Mode）
基线：`21f107a`（worktree `/tmp/xkx-shop`）
本轮已获准越界的 5 个文件：`world/state.py`、`actions/registry.py`、`actions/player.py`、
`actions/commerce.py`、`sim/intent.py`。`api/session.py` 仅白名单 / `_args` / `shop_report` 透传。

## 1. 目标

A. 五个玩家经营动作：`restock` / `set_price` / `dismiss` / `withdraw` / `shop_report`。
B. 真实服务 + ambient，branch（3000 文）+ 3 名雇工连续 10 个世界日，给出逐日
`revenue / restock / wage / tax / net_profit / tx`，并证明存在连续 ≥3 天 `net_profit > 0`
的可持续配置（允许结论是“摊档单人可盈利、雇人需升档”，但不允许“怎么配都亏”）。

## 2. 五个动作（语义 / 拒绝码 / 事件）

实现于 `src/wsim/actions/business.py`。

| verb | 前置 | 效果 | 拒绝码 |
|---|---|---|---|
| `restock` | 店东、在店所在地、`item` 在内容包、`qty` 1..50 | 批发自 `treasury:region`（价 = `sell_price`，零售 60%）或同地点指定店（价 = 该店当店价，`item_move`）；**店现** `transfer` 给卖方；入库 `item_create(reason="restock")` / `item_move`；返回 `total`、`stock_before`、`stock_after` | `no_shop` / `not_shop_owner` / `wrong_location` / `bad_arg:item` / `bad_arg:qty` / `unknown_item` / `insufficient_shop_cash` / `no_supply` |
| `set_price` | 店东、`price` 在基准价 ±20% | 写 `price_overrides[item]`；`price<=0` 撤销；返回 `price/base_price/band` | `no_shop` / `not_shop_owner` / `bad_arg:item` / `unknown_item` / `bad_arg:price` / `price_out_of_range` |
| `dismiss` | 店东、目标是本店雇工 | `shop_dismiss`：摘除 `employees/employee_wages/employee_positions`，`employed_at` 置空；**欠薪债保留** | `no_shop` / `not_shop_owner` / `employee_not_found` / `not_employed` |
| `withdraw` | 店东、`qty` 1..店现 | 店现 → 玩家钱包 `transfer(reason="withdraw")`，不计营收/进货 | `no_shop` / `not_shop_owner` / `bad_arg:qty` / `insufficient_shop_cash` / `not_enough_money` |
| `shop_report` | 店东 | 只读最近 7 个世界日的 `day_history` | `no_shop` / `not_shop_owner` |

补充：`dismiss` 后旧欠薪仍由 `system_debts` 对 debtor=店铺 清偿；`restock` 向指定 NPC 店进货
必须真实减其库存并付钱，货币与物品均守恒；每动作写中文记忆。

## 3. 状态 / 效果基础设施（含无玩家世界零污染）

1. `world/state.py::Business`：
   - `price_overrides: dict[str,int]`（**仅非空时**写入 `to_dict()`）；
   - `day_history: list[dict]`（**仅非空时**写入 `to_dict()`，最多 7 条）；
   - `day_customers: int = 0`（运行期，**不进** `to_dict()`）。
   空值/运行期字段在 `normalize()` 或序列化时剔除。
2. `world/effects.py` 新增 op：`shop_dismiss` / `shop_price` / `shop_history`；
   `transfer` 归类：`restock` → `day_restock`，玩家店 `buy/consume` → `day_revenue` 且
   `day_customers+1`，`withdraw` 不计；`shop_day_close` 清零 `day_customers`。
3. `shop_settlement_effects` 用与 `system_shop_settle` 同一份 `build_shop_settlements` 行
   生成 `shop_history`，保证 `shop_report` 与事件库同源。

**硬证据**：无玩家 2 日 golden 的 `state_final` / `event_final` 与 `21f107a` 基线逐字节一致；
同 seed 两次一致。若不等，本 spec 视为失败，先修序列化再谈盈利。

## 4. 定价如何影响 NPC（commerce.py + intent.py）

- `common.shop_price(content, state, biz, item)`：有覆盖价用覆盖价，否则 `content.item_price`。
- `selling_shops`：先筛“同一件货最低价”的卖家，再在同价卖家间用
  `stable_slot(location|item|buyer|day)` 轮转。**同地点两家不同价时，买主选便宜的那家**；
  同价时玩家店与 NPC 店长期各占约 1/N（确定性、无 random/墙钟）。
- `BuyAction` 用卖家当店价计价；`set_price` 因此改变实际成交价。
- `_propensity_buy`（仅 `player_market_active` 分支）改成**两个购物篮**：
  - `bought_food`：口粮 1 笔/日（食品/饮品；与午饭 `consume` 分开）；
  - `bought_durable`：耐用品 1 笔/日（材料/工具/药材/奢侈品），
    只选 `price*3 <= cash` 且 `cash >= 120` 的品，按
    `stable_slot(f"{npc}|{day}|durable")` 确定性轮转（富买主买贵的，穷买主买便宜的）。
  两类互不挤占，NPC 店的口粮生意不被玩家店“整锅端”。
- 无玩家店世界走旧分支，逐字节不变。

## 5. 盈利机制（只改机制，不改 content 数值）

1. **专程客流随雇工**：`shop_attraction(kind, present)` =
   `base(stall .32 / storefront .45 / branch .55) + 0.12 * present`，上限 0.80；
   `_player_shop_trip` 用它决定“专程进店”概率。branch 满员 3 人 → 0.80。
2. **雇工产出随技能**：日产件数 = `Σ max(1, round(1 + skill))`，技能 ≥0.5 的雇工产 2 件；
   branch 3 人通常 4–6 件/日。
3. 工资契约、市场日薪、`content/**` 不改；`net_profit` 仍含全额工资，欠薪记债不消失。
4. 若 10 日实测仍不转正，只允许调整 §5.1 的 base/slope 与 §4 的选品轮转，
   每次调整给出前后逐日流水；禁止改 content、禁止压低契约工资。
5. **允许且必须诚实给出的结论**：若 branch +3 人结构性不划算，则结论写成
   “摊档单人（店主自营，0 工资）可盈利；雇 1 人需更高客流/更高客单价；branch 需 X 客流才转正”，
   并给出可复现的摊档/店面盈利配置。

## 6. API 接入（严格不碰既有视图）

- `actions/player.py::PLAYER_VERBS` 加 5 verb（服务端实际门禁）。
- `api/session.py`：
  - `WRITE_ACTIONS` / `ACTION_ALIASES` 加 5 项；
  - `_args` 加 5 个分支，空串参数不透传；
  - 仅新增 `shop_report` 的 `report` effect 到 `details`，**不修改任何既有字段/结构**（W1 定稿于 `8ff784e`）。

## 7. 测试（`tests/test_shop_operations.py` 扩展）

1. 五动作各一条成功 + 各一条拒绝（断言 code / effect）。
2. `restock` 守恒；店现不足拒绝且无半写；指定店进货真实减库存。
3. `set_price` ±20% 边界；`+21%` → `price_out_of_range`；成交按覆盖价。
4. **同地点不同价：买主选便宜店**；同价仍按稳定散列轮转。
5. `withdraw` 不超店现；`dismiss` 后欠薪债仍在，再 dismiss → `not_employed`。
6. `shop_report` 最近 7 日与 `system_shop_settle` 事件 args 对得上；成交笔数可观察。
7. 无玩家店 2 日 golden 字节一致；同 seed 两次一致。

## 8. 真实服务验收（10 日）

- 真实 `python -m wsim.api` + `XKX_AMBIENT=1`、speed=4、cpu=98、零模型调用。
- **主场景**：3000 文、market、`branch`、3 名契约雇工（工资≥市场 0.8 倍），连续 10 世界日。
- **对照场景**：摊档单人（自营，不雇人）10 日，用于“允许结论”。
- 逐日输出 `revenue/restock/wage/tax/net_profit/tx`；逐笔买主/货品/金额；`shop_report` 对账。
- **NPC 保护**：同 seed 10 日无玩家基线 vs 有玩家运行，统计 NPC 侧
  `buy/consume/sell` 总量；若玩家店份额 >10%，必须解释（新增耐用品购物篮 vs 替代），
  并给出 NPC 店 `sell/consume` 总量未下降 >10% 的证据；否则启用同品类轮转并重跑。
- 若某日 `tx=0`，逐条说明原因（库存/现金/客流/定价），不许“概率低”。
- 护栏：守恒 delta=0；K1 ≥63.13%；无玩家世界逐字节不变；旧基线保留 skip、新基线归因；
  同 seed 两次一致。

## 9. 盈亏平衡表（交付必含）

公式：`日盈亏 = 客流 × 客单价 - 工资 - 日摊税 - 日均进货`；
`平衡客流 = (工资 + 税 + 进货) / 客单价`。
按实测客单价与客流，分别给出：

| 档位 | 雇工/日成本 | 税/日 | 建议价位 | 平衡日营收 | 按实测客单价所需客流 |
|---|---|---|---|---|---|
| 摊档自营 | 0（店主自营） | 1 | 基准～+20%（食品） | 1 + 进货 | 实测 |
| 摊档雇 1 人 | 市场日薪 | 1 | 基准～+20% | 工资+1+进货 | 实测 |
| 店面雇 2 人 | 市场日薪×2 | 4 | 基准 | 工资+4+进货 | 实测 |
| 分号雇 3 人 | 市场日薪×3 | 10 | 基准～+10%（耐用品） | 工资+10+进货 | 实测 |

表中“实测客单价/客流”取自 10 日真实服务，不用估算；若未达标，用该表指出差多少客流/客单价。

## 10. 文档 / 提交

- `docs/shop-operations.md`：五动作、拒绝码、定价/客流/购物篮说明。
- `docs/shop-profitability.md`（新）：10 日流水、NPC 保护对照、盈亏平衡表、结论。
- `CHANGELOG.md` 两条。
- 两笔提交、显式 pathspec、各自 push：
  1. `feat(shop): 经营动作 restock/set_price/dismiss/withdraw/shop_report`
  2. `feat(shop): 定价/客流机制与 branch 10 日盈利验证`
</content>
