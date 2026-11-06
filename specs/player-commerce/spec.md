# player-commerce 规范（玩家消费类动作开闸）

## 目标与边界

在 HEAD `7881432`（容器默认自走修正之后）上，只打通玩家
`buy` / `sell` / `consume` / `escort` 四个已有动作：

- 复用 `BuyAction` / `SellAction` / `ConsumeAction` / `EscortAction`，不新写交易逻辑。
- 不改 `ui/player/**`、`content/**` 数值、`sim/intent.py`、`sim/scheduler.py`、`start.sh`。
- 不新增开店/结婚/建派能力；`marry` / `found_sect` / `apprentice` 只返回明确的未开放原因码。
- 不打 tag、不重打包（p3l 打包在跑）。

## 现状（已核实）

- [session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py) 的
  `WRITE_ACTIONS` / `ACTION_ALIASES` / `PLAYER_VERBS` 均未收录四个动词，故玩家调用统一回落
  `unknown_action`；NPC 规划器直接提交 Intent，因此同一套动作 NPC 可用。
- `BuyAction` / `SellAction` 在 `actions/commerce.py`，`ConsumeAction` 在 `actions/basic.py`，
  `EscortAction` 在 `actions/escort.py`；前置校验与效果、税、库存、金库现金均已实现。
- `player_state` 的 `location.services` 只是 item id 列表；无价格、无买卖上限。
- `QuestDeliverAction` 对 `category=procurement` 只校验 `target_item` 持有量，购买入口缺失时必报
  `quest_item_missing`。

## 设计决策

### 1. 动词与参数映射（`src/wsim/api/session.py`）

- `WRITE_ACTIONS` 增加：`buy`→`buy`、`sell`→`sell`、`consume`→`consume`、`escort`→`escort`。
- `ACTION_ALIASES` 增加同名四项（含内部名直通）。
- `src/wsim/actions/player.py` 的 `PLAYER_VERBS` 增加 `buy`、`sell`、`consume`、`escort`。
- `_perform` 在查 `WRITE_ACTIONS` 前先判 `PLAYER_UNIMPLEMENTED_VERBS = {"marry","found_sect","apprentice"}`，
  命中直接 `_reject_early(..., "not_implemented_for_player")`，不再落到 `unknown_action`。
- `_args` 新增：
  - `buy`：`item`（必填）、`qty`（默认 1）、`from`（可选，缺省当前地点）。
  - `sell`：`item`、`qty`、`to`（可选，缺省当前地点）。
  - `consume`：`item`（可选，缺省最便宜食物）、`qty`。
  - `escort`：`route`（list[str] 或逗号分隔字符串；若只给 `route_id` 则从
    `content.escort_routes[route_id]["route"]` 解析）、`cargo_value`（兼容入参 `cargo`，默认 100）、
    `fee`（默认 0，由动作层校验 `fee_too_low`）。
- 角色闸门保持不变：`content.allowed_roles("escort")` 含 `镖师/脚夫/捕快/船夫`；游侠落地角色为
  `镖师`，商人落地为 `掌柜`（会被 `role_not_allowed` 拒绝，符合现有语义）。

### 2. 前置失败原因码（API 前置校验，只对玩家请求生效；NPC 路径与旧基线不变）

为避免改动 `commerce.py` / `basic.py` 里其他会话的在途修改，玩家专属码在
`session._precheck_player_action` 里用现有 `content` / `state` 只读助手（
`business_selling`、`business_with_cash`、`sell_price`、`transaction_tax`、`cheapest_food`）
先行判定，命中即 `_reject_early`（写入 reject 事件，码与响应一致）；未命中再提交原有动作。
校验语义与 `BuyAction` / `SellAction` / `ConsumeAction` 完全一致，不落任何交易效果。

| 场景 | 玩家码 | NPC 旧码（保持不变） |
|---|---|---|
| `from`/`to` 与当前位置不符 | `not_here` | `wrong_location` |
| 物品不在内容包 | `item_not_available` | `unknown_item` |
| 当前地点没有任何商业体 | `shop_closed` | `item_not_available` / `no_buyer_cash` |
| 有商业体但不卖/不收该物品 | `not_a_merchant` | `item_not_available` / `no_buyer_cash` |
| 钱（含交易税）不够 | `not_enough_money` | `insufficient_money` |
| `sell` 持有量不足 | `item_missing` | `item_not_held` |
| 押镖起点不是当前位置 | `not_here` | `not_at_origin` |
| NPC 专属动作 | `not_implemented_for_player` | 不适用 |

- `not_here` 也覆盖押镖起点：玩家 `route[0] != ent.location` 时 API 先返回 `not_here`。
- `ConsumeAction` 无 `from` 参数，`not_here` 不适用。
- `actions/escort.py` 仅做兼容性增量：把玩家传入的真实货物 `cargo_item` / `cargo_qty`
  写入 `escort_order_create` 事件（不传时事件体逐字节不变，NPC 基线不受影响）。
- 所有新码写入 `session.py` 的 `REASON_TEXT`；同时补齐押镖旧码
  （`not_at_origin`、`route_not_adjacent`、`already_on_escort`、`cargo_too_low/high`、
  `fee_too_low/high`、`no_escort_agency`、`agency_insufficient_cash`）及
  `bad_arg:route/item/qty/cargo_value/fee` 的中文文案。

### 3. `state` 价格与买卖上限（只加字段，不改现有键）

- `_location_view(loc_id, pid="")` 保留 `services`（原样 id 列表），新增
  `services_detail`：每个服务/商品一行：
  `{item, name, category, price, sell_price, tax, stock, business, business_name, available, buy_limit, sell_limit}`。
  - `price` = `content.item_price(item, state)`（单件售价，不含税）；`tax` = 单件交易税；
    `sell_price` = `common.sell_price`。
  - `buy_limit` = `min(店铺库存, 20, 按含税总价可负担的最大件数)`；`sell_limit` =
    `min(玩家持有量, 20, 按收购价与店铺现金可成交的最大件数)`。
  - `available` 仅表示“当前地点有店铺库存”。
- `player_state` 新增顶层 `shop`：`{location, businesses: [{id,name,kind}], buy: [...], sell: [...]}`。
  `buy`/`sell` 行字段同上，`buy` 行额外带 `total`（单件含税到手价），`sell` 行带 `qty`。
- `player.wallet.items` 每行新增 `price`、`sell_price`（原 `item/name/qty` 不动）。
- 匿名 `public_state` 白名单不变，不新增价格字段。

### 4. 采购委托验证（不改任务数值）

开放 `buy` 后，用真实服务逐单走：找到 `category=procurement` 的开放委托 → 到发布人处接单 →
移动到 `target_item` 所在店铺 → 按模板 `intent_args` 数量买入 → 到目标/发布人处交付。
逐单记录结果；若仍失败，只报告原因（资金不足 / 地点无货 / 任务与库存结构不匹配），不改
`content/quests.yaml` 或任何数值。

### 5. 不变量

- 钱包守恒：买/卖/吃都是 `transfer` + 税分流（treasury/burn），`conservation(state)` 的
  `conservation_error == 0`；不出现 mint。
- 无半写：单个 Intent 在一个 Writer 事务内落地；拒绝只写一条 reject 事件。
- `seq` 连续：每次响应 `event_seq` 递增，`store.count()` 与尾部一致。
- 性能：50 客户端并发压测 P95 与对照 22.6ms 同量级（新增字段只在只读 `player_state`，
  写路径仅多几次库存/余额判断）。

### 6. 基线纪律

- 旧基线（`GOLDEN_MILESTONE_V3` / `DIALOGUE_GOLDEN_MILESTONE_V3`）保留数值并显式 skip。
- 新增 `GOLDEN_MILESTONE_V4` / `DIALOGUE_GOLDEN_MILESTONE_V4`，用实跑结果填值；逐项说明
  events 数、`sell_in`、`buy_out`、`burned`、`business_cash`、拦截计数变化。
- 若 2 天 headless 世界不执行玩家 API 动作导致数值与 V3 完全相同，也照实记录“零漂移”，
  并在注释写明原因；不得改旧期望数字。

### 7. 文档与提交

- `docs/player-api.md`：新增四动词参数、原因码表、`state.shop` / `services_detail` 字段说明。
- `CHANGELOG.md` 增加一条（玩家消费类动作开放 + state 价格字段）。
- 提交显式 pathspec（源码、测试、文档、本 spec/tasks），不带 `reports/**`、`ui/**`、`deploy/**`；
  单独 commit、push，回报 SHA 与远程一致性。零模型调用。

## 验收清单

1. 四动作前置/成功/失败码逐项可复现。
2. 真实服务下买→吃→卖流水：钱包、库存、体力、商家现金逐项变化。
3. 镖师押一趟：`route`/`cargo_value`/`fee` 入库，到达结算收益与抽成。
4. 至少 3 个负面路径码（`not_enough_money`、`item_missing`、`not_here`、`shop_closed`、
   `not_a_merchant`、`item_not_available`、`not_implemented_for_player` 中取 3+）。
5. `state` 中 `services_detail` / `shop` 的价格样例。
6. 采购委托逐单结论。
7. 守恒/seq/无半写 + 50 并发 P95。
8. 全量 `pytest`、`selfcheck.sh`、干净 HEAD 复验计数。
9. V4 基线逐项说明。
</content>
</file_path>
