# 店铺经营闭环（开店 → 雇佣 → 日结）

> 对应改动：`open_shop` 初始库存、玩家店铺 `recruit`、独立日界事件 `system_shop_settle`、
> 自雇工资口径。基线以 HEAD `48b512f` 为参照，content_hash 未变。

## 1. 初始库存：钱从玩家出，货进店铺

`open_shop` 一次完成两笔真实转账（均为 `transfer`，不 `mint`）：

| 流向 | 收款方 | reason | 金额 |
| --- | --- | --- | --- |
| 铺面费 | 当前地点第一个商业体的公账（无商业体时用组织金库） | `shop_rent` | 档位价：摊 300 / 店 1200 / 分号 3000 |
| 初始库存 | `treasury:region`（区域公账） | `shop_stock` | 按 `sell_price`（零售价 60%）× 数量 |

- 库存单价来自既有内容包 `items` 与 `sell_ratio`，不新造价格、不改既有数值。
- 库存明细（`SHOP_INITIAL_STOCK`，只取内容包里真实存在的物品）：

| 档位 | 品类与数量 | 进货成本 | 可雇人数 |
| --- | --- | --- | --- |
| stall | flatbread 6 / noodle 4 / rice 3（共 13 件） | 按 sell_price 计 | 1 |
| storefront | flatbread 10 / noodle 8 / rice 6 + coarse_cloth 4 / cleaver 2 / needle 2（共 32 件） | 按 sell_price 计 | 2 |
| branch | flatbread 14 / noodle 10 / rice 8 + coarse_cloth 6 / cleaver 3 / needle 3 / wound_powder 3 / mugwort 3（共 50 件） | 按 sell_price 计 | 3 |

- 初始库存以 `item_create(reason=initial_stock)` 计入 `items_created`，可被审计追溯。
- 没有赠送：档位不额外白送库存，也不产生 `mint`。后续补货同样走 `treasury:region` 进货
  （本轮未新增独立补货动作，补货路径与开店一致）。
- 前置：钱包必须同时覆盖铺面费 + 进货成本，否则 `insufficient_money`，不会出现“扣了钱没货”。

## 2. 雇佣契约

玩家 `recruit` 带 `shop`/`position`/`wage` 时走店铺雇佣；不带这些参数时保持原门派招揽语义。

请求示例：

```json
{"verb": "recruit", "args": {"target": "yan_shouyi", "shop": "shop:player:u_demo",
                             "position": "庙祝", "wage": 18}}
```

前置（失败写可读原因码，拒绝事件照常落库）：

| 原因码 | 含义 |
| --- | --- |
| `not_shop_owner` / `no_shop` | 不是店主或店铺不存在 |
| `wrong_location` / `target_not_at_shop` | 店主或目标不在店铺地点 |
| `shop_full` | 超过档位可雇人数（1/2/3） |
| `relation_too_low` | 双向好感 < 6 |
| `skill_too_low` | 目标最高技能 < 0.15 |
| `wage_too_low` / `wage_too_high` | 日薪超出岗位市场价的 0.8–1.5 倍 |
| `unknown_position` | 岗位不在 `economy.wages_daily` |
| `self_recruit` | 不能雇自己 |

成功写入 `shop_hire` 效果：`Business.employees` / `employee_wages` / `employee_positions`，
`Entity.employed_at = 店铺`（若原雇主是另一家店，会从原名册摘除），并给双方写记忆。

## 3. 日结：独立事件 `system_shop_settle`

日界调度在 `system_maintenance` 之后发出独立事件（不并入 `system_debts`）：

```
营收   = 当日顾客 buy/consume 进入店铺公账的金额（transfer reason=buy|consume）
进货   = 当日店铺公账付给卖方的金额（transfer reason=sell）
工资   = Σ 雇工契约日薪（先付工资，现金不足记欠薪）
税     = 月租 / 30（至少 1 文），现金不足的部分结转到次日（tax_arrears）
净利   = 营收 - 进货 - 工资 - 税
```

- 工资：按雇工 id 升序逐人支付；付不出的部分记为“店铺欠雇工”的债
  （`creditor=雇工、debtor=店铺`，`note=unpaid_wage`），由既有 `system_debts` 用店铺现金补付；
  旧债已结清后再欠会开带 tick 的新 id，避免 id 冲突。
- 税：先工资后税；当日现金不足的税不 burn，写入运行期 `tax_arrears` 结转到次日，
  仍由下一次 `system_shop_settle` 补缴（不混入 `system_debts`）。
- 雇工产出：每个有效雇工每日 `item_create(reason=shop_output)` 1 件（档位首个库存品），
  计入店铺库存，可继续售卖。
- 可读性：给店主写一条记忆（营收/进货/工资/税/净利；亏损追加“亏损，店主需补足”；
  欠税追加“欠税 N 文结转次日”），给雇工写“出工 + 日结工资”记忆。
- 事件本身含全部效果：`transfer`（工资）、`burn`（个税与日摊税）、`debt_create/increase`（欠薪）、
  `item_create`（产出）、`memory_add`（账本摘要），可按 seq 逐笔对账。
- 结算后 `shop_day_close` 清零当日 `day_revenue/day_restock` 并结转 `tax_arrears`；
  这些是运行期字段，不进 `to_dict()`，没有玩家店铺的旧世界 state hash 不变。

## 4. 自雇口径（玩家在自己的店里打工）

**在自己的店里干活不发工资，收益从店铺销售来。**

实现上 `work` 识别“店主自雇”（`Entity.business` 就是当前店铺，或与店铺签了日结合约）：

- `received = 0`，不产生 `transfer`，也**不生成任何债务**（尤其不再出现“自己欠自己”）；
- 只结算体力与技能，并写记忆“店主自雇，工资记0文（店主收益走分红）”；
- 店铺收益通过日结净利进入店铺公账，玩家可后续从店铺分红/提现（本轮不新增提现动作）。

## 5. 调度器与 headless 的最小改动

`sim/scheduler.py` 只在 `_day_end` 的系统动作元组之后增加一处条件守卫：

```python
from ..actions.business import has_player_shop
...
if has_player_shop(self.writer.state):
    self._emit_system(
        Intent("system", "system_shop_settle", {"day": day}, tick, "system")
    )
```

`sim/headless.py` 用同一判断（`has_player_shop`）在日界发出同一事件，口径一致。

- 守卫只做一件事：世界里存在 `owner` 以 `player:` 开头的店铺才 emit。
- **NPC 开的店不触发日结**：`has_player_shop` 只认 `owner.startswith("player:")`。NPC 商业体
  （创世生成的 `biz:<loc>:<item>` 等，`owner` 为空）继续走既有机制：按动作即时付款、月度税等，
  没有店铺 P&L 日结。若未来需要 NPC 店铺也按日结算，那是另一套口径（需要 NPC 店铺的收入/进货
  计数与负债语义），本轮不改变。
- 无店铺世界不发该事件，日界事件序列与改动前逐字节一致（测试对整份事件列表序列化后比对哈希）。
- 调度循环、配速、时钟、护栏、`_day_end` 其它步骤顺序均未改动。
- 不在调度侧挡掉的非法结算仍会按 `Writer.submit` 既有语义写 rejected 事件（保持可审计性）；
  本事件的 `preconditions` 用 `no_player_shop` 兜底，避免手工误发。

## 6. 雇工日程：玩家店铺是 workplace 候选

`sim/intent.py` 的日程层按雇主类型区分资金门槛（这是让雇工“真的去上班”的关键）：

| 雇主类型 | 上工条件 | 付不出日薪时 |
| --- | --- | --- |
| NPC 商业体（`owner` 为空） | 公账现金 ≥ 日薪 + `employer_reserve`（2000 文），原规则不变 | 不去整班，留在原地练手艺 |
| 玩家店铺（`owner=player:<account>`） | 契约成立即出工；偿付判断只看**玩家钱包余额 + 店铺当日营收**，不套 2000 文储备 | 仍然出工，日结记“店铺欠雇工”的欠薪债 |

- 玩家店铺的日薪取契约 `employee_wages`（不再用岗位市场价），日程里的 `work` 发生在店铺地点。
- 玩家店铺的 `work` 参数额外带 `employer=shop:player:<account>`，事件可归因到具体店铺；
  NPC 雇主的 `work` 参数保持 `{at, hours}`，所以无玩家雇佣的世界事件哈希逐字节不变。
- 签约会把 `Entity.employed_at` 指向玩家店铺，雇工离开原 workplace 属于正常调动；
  日程保证他确实到新店铺上工，而不是“两边都不去”。
- 计划文案会区分“上工糊口”与“上工糊口（店主现金紧，工资走欠薪）”，便于日志审计。
- 该改动只影响存在玩家雇佣契约的 NPC：无玩家雇佣的世界里，两条分支都走原 NPC 规则，
  golden 基线的 state/event 哈希逐字节不变（`test_p3e_golden.py` V5）。

## 6.1 已知限制

- 日结只统计店铺公账上的 `buy/consume` 与 `sell` 流水；玩家私下的物品转移不计营收。
- 欠薪由 `system_debts` 在次日日界补付；补付不重复计入当日净利（成本已在欠薪当日计提）。

## 7. 验收记录（真实服务 3 个世界日，run `shop-demo-v7`）

参考 commit：`48b512f` + 本改动；content_hash `7ec670c1`；seed 42；真实 `wsim.api` + ambient。
下表取雇工入职后的 day2–day4（每个世界日恰好一条 `system_shop_settle`）：

| 世界日 | 营收 | 进货 | 工资(应付/实付/欠) | 税(应付/实付) | 净利 | 雇工产出 | 日末库存 | 日末店铺现金 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| day2 | 65 | 0 | 18/18/0 | 10/10 | +37 | 1 | 39 件 | 45 |
| day3 | 59 | 0 | 18/18/0 | 10/10 | +31 | 1 | 35 件 | 76 |
| day4 | 210 | 0 | 18/18/0 | 10/10 | +182 | 1 | 28 件 | 258 |

- day0（开店当日）营收 0、无雇工，日摊税 10 结转欠税；day1 营收 46、工资 18、税实付 20
  （10 当期 + 10 补缴 day0 欠税），净利 +18。
- 雇工 `yan_shouyi`：雇佣前在庙里“上工糊口”；雇佣后 `employed_at=shop:player:u_demo`，
  每日领 18 文（个税 1 文 burn）。本轮修复前受 `intent.py` 的 2000 文储备限制不会到店；
  修复后日程改走玩家店铺分支（第 6 节），契约成立即到店上工，欠薪场景仍出工。
- 自雇：`work` 事件只有体力/技能/记忆效果，`transfer` 与 `debt_create` 均为 0。
- 不变量：3,203 条事件 seq 连续 1..3203；`conservation_error=0`；
  `minted_total=53150`、`burned_total=1175`、`accounts_sum=51975`（= minted − burned）；
  玩家钱包 2550 = 6000 − 3000 铺面费 − 450 进货，未从自雇拿到工资。

## 7.1 雇工行为修复验收：引擎自走（3 个世界日）

环境：HEAD `1709524` + 本轮 `intent.py`；content_hash `7ec670c1`；seed 42；
`AmbientScheduler` 自走（不配速、3×144 ticks），玩家店铺 `shop:player:u_demo`（branch，market），
雇工 `yan_shouyi`，契约日薪 18。

| 项目 | 结果 |
| --- | --- |
| 到店上工 | day0 tick 32/75/113；day1 158/200/239；day2 289/318，均为 `work at=market hours=8` |
| 同地点 talk | 回放 tick 318：雇工 `market`、玩家 `market`，`TalkAction.preconditions=[]`（非 `not_together`） |
| 日结（注资 200 文） | 每日 `wage=18`、`tax=10`、`output=1`；日末店现 116（200 − 3×18 − 3×10），钱包 300 |
| 欠薪场景（钱包/店现 0） | 每日仍 `work at=market`，计划文案带“店主现金紧，工资走欠薪”；日结 day0 `debt_create` 18、day1/day2 `debt_increase` 18，活动债 54，`creditor=yan_shouyi / debtor=shop` |
| 事件量与 NPC 覆盖（同 seed 对照） | 有雇佣 1,331 条非系统事件 / 31 名 NPC；无雇佣 1,384 / 31。总量 −3.8%（雇工日程改写导致其与庙里同伴的互动减少），NPC 覆盖 31/31 不变 |
| 确定性 | 同 seed、同输入两次：state hash `ff2bed00…`、event hash `70065d9f…` 完全一致 |

## 7.2 HTTP 真实服务端到端复跑（同一仓库工作树，3 个世界日）

环境：真实仓库工作树（HEAD `1709524` + 本轮 `intent.py` + W1 未提交的 `recruit`
放行）；`wsim.api` + ambient（seed 42、speed 4x），数据根 `/tmp/xkx-live-intent`，
run `hire-e2e`；content_hash `7ec670c1`。

- 现实差异：真实 HEAD 的 `STARTING_MONEY=300`（演示副本曾是 6000）。因此本轮没有
  预置资金，玩家先卖掉随身粗布/炊饼（+21）、打短工（+33）凑到 354 文，在 **temple**
  开 `stall`（开店 + 初始库存共 345 文，钱包余 9 文）；店现完全来自顾客消费，无 mint。
  地点级事实：`open_shop` 事件 tick 194 `at=temple`，即玩家店铺 **`shop:player:u_demo` @ temple**，
  因此后面的 `work at=temple` 是玩家店铺所在地点（不是“回原工作地”）；该批旧事件没有
  `employer` 字段，W1 合入后按 7.3 的口径复跑即可看到 `employer=shop:player:u_demo`。
- 雇工：tick 326 通过 HTTP `recruit` 签约 `yan_shouyi`（岗位庙祝、日薪 18），
  `shop_hire` 效果落库，`employed_at=shop:player:u_demo`。
- 行程：tick 333/362（签约当天）、439/473/515、576/607/640、720，均
  `move temple → work at=temple hours=8`，reason“上工糊口”，店铺地点与玩家同址。
- 同地点 `talk`：tick 561 在 temple 对 `yan_shouyi` 执行 `talk`，`ok`（非 `not_together`）。
- 日结（签约后的 3 个日界）：

| tick | 营收 | 工资 | 税 | 净利 | 产出 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 432 | 16 | 18 | 1 | −3 | 1 炊饼 | 亏损提示“店主需补足”；工资 `transfer` 到雇工钱包，个税 1 burn |
| 576 | 52 | 18 | 1 | +33 | 1 炊饼 | 正常盈利 |
| 720 | 3 | 18 | 1 | −16 | 1 炊饼 | 店现仍够付工资，未生成欠薪债 |

- 店铺现金全程由 temple 的 NPC 顾客消费入账（`buy/consume` 的 `transfer` 到
  `cash:shop:player:u_demo`），没有外部注资；工资三天各 18 文均实付。
- 欠薪场景（店现/钱包不足）已在 7.1 的引擎自走中验证：仍然出工、日结走
  店铺欠雇工的活动债；HTTP 放行合并后可用同一脚本复跑。同一 run 在 day6 日界也真实出现过
  一次部分欠薪：日薪 18 只付 9，`debt_create=9`（`creditor=yan_shouyi`），雇工随后仍到店。

## 7.3 地点级证据：`work` 事件的 employer 归因（引擎自走）

环境：真实代码 + 真实 content，seed 42，`AmbientScheduler` 自走 3×144 ticks；玩家店铺
`shop:player:u1` @ **market**、owner `player:u1`、`employees=['yan_shouyi']`、契约日薪 18。
雇工原 workplace 是 temple，签约后 `employed_at=shop:player:u1`。

- 首次上工：tick 32 `work`，`args={'at': 'market', 'employer': 'shop:player:u1', 'hours': 8}`，
  `outcome=ok`；apply 后雇工 `location=market`（不在原 workplace temple）。
- day0 关键行程（逐条）：tick 28 `sell at=ferry` → tick 30 `move inn` → tick 31 `move market`
  → **tick 32 `work at=market employer=shop:player:u1`** → tick 33 `consume` → tick 34
  `move academy` → tick 36 `move temple`。可见“去上工”是移动到玩家店铺所在地点，
  `employer` 明确指向玩家店铺，而不是 market 里的 NPC 摊位。
- 3 日全部出工：day0 tick 32/75/113、day1 158/200/239、day2 289/318，均
  `work at=market employer=shop:player:u1 outcome=ok`。
- 同地点可见性：tick 32 玩家 `state` 的 `location=market`，同地点 `nearby` 含
  `{"id":"yan_shouyi","name":"严守一","role":"庙祝","location":"market","presence":"online"}`。
- `talk` 原始事件：tick 32，`actor=player:u1`、`verb=talk`、`args={target: yan_shouyi,
  message: 今日辛苦了}`、`outcome=ok`（不是 `not_together`），效果含 `memory_add`、
  `relation_upsert`（affection +2）、`talk_ledger`。
- 日结：tick 143/287/431 各 `wage=18 → wallet:yan_shouyi`、`tax=10`、`output=cleaver×1`；
  产出与工资都归到 `shop:player:u1` 的账上。
- 无玩家雇佣对照：V5 golden 继续通过，state `0ee1f724…` / event `6a919efe…` 逐字节不变。

## 7.4 正式 HEAD 放行 `recruit` 后复跑（2026-11-05）

环境：真实仓库 HEAD + 本轮 `recruit` 放行（`actions/player.py` 的 `PLAYER_VERBS`、
`api/session.py` 的 `WRITE_ACTIONS` / `ACTION_ALIASES` / `_args`）；`wsim.api` + ambient
（seed 42、speed 4x），数据根 `/tmp/xkx-shop-e2e-run`，run `shop-e2e`。

- 开店：tick 486 在 temple 执行 HTTP `open_shop`（`stall`、名“庙前香摊”）。
  真 HEAD 新玩家只有 300 文，脚本先跑 3 趟空货短镖攒够开店 + 首批库存（345 文）。
- 负样例（同一服务实例）：
  - 未经营人情直接雇 → `reject_reason=relation_too_low`；
  - 关系达标后日薪给 1 文 → `reject_reason=wage_too_low`。
- 雇工：tick 2904 HTTP `recruit`（`target=yan_shouyi` / `position=庙祝` / `wage=18`）
  一次成功，`shop_hire` 效果落库。
- 日结：tick 3024 / 3170 / 3317 三次 `system_shop_settle`，每天一笔
  `transfer(amount=18, src=cash:shop:player:u_demo, dst=wallet:yan_shouyi)`；
  3 天工资合计 54 文，未付欠薪 0。
- 对账：`accounts_sum=47241 = minted 47450 − burned 209`（`delta=0`），`escrow_total=0`。



## 8. NPC 多跳寻路（全 NPC 生效，不只是雇工）

> 对应改动：`sim/intent.py` 的 `Planner._move`、`actions/basic.py` 的 `MoveAction`。
> 旧行为：日程只把目的地当成“相邻一跳”发出去，跨地点行程一律 `move:not_adjacent` 落空，
> 世界看起来在动、实际原地空转。

### 8.1 改法

- **最短路逐跳**：`Planner._move` 用 `actions/common.py::path_between`（地点邻接图上的确定性
  BFS，邻接表顺序固定、平局按地点 id 排序）产出整条路线，按 tick 逐跳发 `move`；
  禁止随机游走。
- **每跳带 `dest`**：`args={"to": 这一跳的落点, "dest": 最终目的地}`。玩家动作会一次推进
  6 tick，途中意图被调度器丢弃后，`MoveAction` 用 `dest` 从**实际所在地**重算剩余最短路
  并一次补足，不再 `not_adjacent`。
- **移动先于动作**：`_move` 返回 `cursor - 1`，最后一个移动与紧随其后的 `work`/`consume`/买卖
  共用同一 tick；同 tick 按入队顺序执行（先移动后动作），动作落点必然已到达。
- **跨日段补通勤**：计划跨日时，计划里对所在地的假设已隔了一整天；护送抵达等系统瞬移、
  玩家跳 tick 丢意图都会让 `work` 落在人不在的工位。跨日段上工前再排一次同 tick 的
  `move(to=work_loc, dest=work_loc)`：与 `work` 同 tick 且入队在前，二者要么一起被丢、
  要么一起执行；已在工位时是 0 步空操作（不扣体力、不写记忆）。
- **陈旧一跳**：已在最终目的地时，剩下的 `to` 在别处的陈旧一跳按 0 步成功，不报 `not_adjacent`。
- **确定性**：路线只由邻接图与 BFS 决定；同 seed 两次 `state_final`/`event_final` 逐字节一致。

### 8.2 10 个世界日对照（headless、seed 42、真实 content、`npc_limit=0`）

改动前基线取自 `origin/master` 的干净工作树，改动后在 HEAD + 本改动的工作树；两者
`content_hash` 均为 `7ec670c1`。

| 指标 | 改动前 | 改动后 |
| --- | --- | --- |
| 事件总数 | 2822 | 3084 |
| `precondition_blocked` / `pruned` | 46 / 11 | 21 / 4 |
| `move:not_adjacent` | 1 | **0** |
| `work:not_at_workplace` | 3 | **0** |
| `work:insufficient_stamina` | 4 | **0** |
| `consume:item_not_available` | 16 | 5 |
| NPC 覆盖人数（有事件的活动实体） | 31 | 31 |
| K1（规则臂，判定/在范围内的） | 62/91 = 68.13% | 62/91 = 68.13% |
| `state_final`（10 日） | `bb939b4b…` | `68b05948…` |
| `event_final`（10 日） | `65278147…` | `73026568…` |

地点到达分布（`move` 的 `set_location.to` 计数）：

| 地点 | 改动前 | 改动后 | 变化 |
| --- | --- | --- | --- |
| ferry | 119 | 130 | +11 |
| escort_agency | 62 | 69 | +7 |
| academy | 143 | 147 | +4 |
| inn | 203 | 205 | +2 |
| pharmacy | 56 | 57 | +1 |
| market | 272 | 266 | −6 |
| smithy | 61 | 52 | −9 |
| temple | 121 | 120 | −1 |
| teahouse | 2 | 2 | 0 |

**此前到不了、现在能到的重点地点（按 NPC 维度）**：`ferry` 新增
`hua_jieyu`/`ming_chabo`/`xie_tingyun`，`escort_agency` 新增 `ruan_xiaoluo`，
`pharmacy` 新增 `jiang_tingyu`/`qin_wangchuan`/`wei_changgeng`，
`smithy` 新增 `cao_banxian`/`ruan_xiaoluo` —— 这些 NPC 在改动前 10 个世界日从未到过对应地点
（到访总人次下降的 `smithy`/`temple` 见下方归因）。

**归因（世界事件量变化是预期内的）**：
- `move` +181：跨日段上工前的同 tick 通勤移动（多数为 0 步空操作），换来 `work` 真的落到工位。
- `work` +16、`consume` +25、`buy` +6、`sell` +11、`trade` +6、`quest_deliver` +7、
  `quest_accept` +5：原计划不再因移动落空中断，NPC 按自己的日程走完，买卖/交付随之发生。
- `cultivate` −1：原先“走不到”而改练手艺的兜底少了。
- `blocked` 46→21：`work:insufficient_stamina`（4）与 `work:not_at_workplace`（3）归零，
  `consume:item_not_available` 16→5（到店后真的买到了），任务类拦截随行程完成而减少。
- `smithy`/`temple` 到访下降：这两处此前集中了“移动失败后原地兜底”的落点，寻路修好后
  兜底减少；`ferry`/`escort_agency` 等远端地点因此增加。

### 8.3 真实服务验收（`wsim.api` + ambient，3 个世界日）

- 雇工 `yan_shouyi`（`employer=shop:player:u_live`）三个世界日每天都出工：
  tick 85/119（day0）、194/276（day1）、323/357/398（day2），全部 `at=temple outcome=ok`。
- 日结 tick 153/289/432 均按出勤计薪（18 / 13+欠 5 / 18）。
- `ambient_summary.blocked`：`move:not_adjacent` = **0**、`work:not_at_workplace` = **0**；
  残余拦截仅 `consume:item_not_available`、`trade:*`、`quest_deliver:*`、`join_sect:*` 等。
- 经受住“持续玩家 move 跳 tick”压力场景（每 2s 推 6 tick）：`move:not_adjacent` = 0。
- `content_hash`：关闭 `7ec670c1…`、开启 `XKX_CONTENT_PACKS=scaleup_w3` `3fd99946…`，均未变；
  全程 0 次模型调用（`llm_calls=0`）。

## 9. 日结按实际出勤（缺勤 0 工资 0 产出）

> 对应改动：`actions/business.py::build_shop_settlements` / `shop_settlement_effects`。
> 旧行为：日结不看雇工当天是否到店，人没来也照发工资、照给产出。

### 9.1 口径

- **出勤判定**：结算日 `[day*144, (day+1)*144)` 内存在该雇工带
  `employer=shop:player:*` 的 `work` 事件（雇工出工时会写一条“工资本日由日结发放”的记忆，
  日结按该标记 + tick 落在当日判定），记 `present`。
- **缺勤**：当日无出工 → `wage=0`、`output_qty=0`、**不产生欠薪债**；
  结算行带 `absent=[雇工 id]`、`present_count`；
  `system_shop_settle` 明细写“某雇工当日未到店，工资0文、产出0件”，
  同时给店主与雇工各写一条记忆。
- **欠薪语义不变**：出勤但当日现金不足才欠薪（`creditor=雇工、debtor=店铺`，
  `note=unpaid_wage`），由既有 `system_debts` 补付。
- **结算日归一**：headless 在当日最后一 tick（143/287…）结算，shared/ambient 在新日首个
  tick（144/150…）结算，`settled_day_of(tick)` 统一推断成“被结算的那个世界日”，
  两种路径判定出勤的天数一致。
- 兼容：`build_shop_settlements(content, state)` 不传 tick 的旧调用保持“视为出勤”，
  只影响测试与历史脚本；真实日界一律传 `intent.tick`。

### 9.2 真实服务验收（`wsim.api` + ambient，seed 42）

**A. 正常出勤（3 个世界日，run `live-attend`）**

| 世界日 | 出工事件（tick/at/employer） | 日结 | 工资 | 产出 |
| --- | --- | --- | --- | --- |
| day0 | 85 temple `shop:player:u_live`；119 同左 | 153 | 18 | 1 炊饼 |
| day1 | 194、276 均 temple `shop:player:u_live`（另有 153 pharmacy `employer=null` 的短工） | 289 | 13 + 欠薪债 5 | 1 |
| day2 | 323、357、398 均 temple `shop:player:u_live` | 432 | 18 | 1 |

- 三个世界日每天都有 `employer=shop:player:*` 的出工事件，日结全部按出勤计薪；
  day1 店现不足只付 13、欠 5（欠薪债语义保持）。

**B. 缺勤（run `live-absent`：日界前签约 + 持续玩家 move 跳 tick）**

| 项目 | 结果 |
| --- | --- |
| day0 出工事件 | 无（签约 tick 141 在日界前，雇工当日没到店） |
| day0 日结 tick 154 | 明细 `["严守一当日未到店，工资0文、产出0件", "当日未到店（庙前香摊），日结工资0文、产出0件", "…营收15文…工资0文…"]`；`wage=[]`、`output=[]`、`debt=[]` |
| day1 日结 tick 294 | 出工 154/238/273 → 工资 18、产出 1 件 |
| day2 日结 tick 434 | 出工 315，店现不足 → 工资 11、欠薪债 7（`unpaid_wage`） |

- 该 run 同时是压力场景（每 2s 玩家动作推进 6 tick）：`move:not_adjacent` = 0、
  `work:not_at_workplace` = 0，`llm_calls=0`。

### 9.3 单测

- `tests/test_shop_operations.py::test_settlement_absent_pays_zero_and_records`：
  当日 1 人出勤、1 人缺勤 → `present_count=1`、`absent=[缺勤者]`、工资只发 1 份、
  `output_qty=1`、净利守恒；apply 后缺勤者无债务、店主记忆含“当日未到店”。
