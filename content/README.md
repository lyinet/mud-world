# world-sim 内容包

P1 世界审计器 + P2 内容储备。核心数据为 30 NPC / 8 地点 / 72 游戏日；本目录只放数据与校验脚本，不含引擎代码；引擎在 `world-sim/src` 中读取这里的 YAML。

> **重要：本目录已出现 P3 生效数据。** `quests.yaml` 已被 P3 任务系统读取（接单/执行/交付/过期），`personas/` 已被 LLM 决策层注入 prompt；`sects.yaml`、`escort_routes.yaml` 仍只作为 P2/P3 预留输入。逐字段的读取状态见[引擎读取范围](#引擎读取范围p1-已生效-vs-p2p3-预留)。

## 文件一览

| 文件 | 作用 | P1 引擎读取 |
| --- | --- | --- |
| `npcs.yaml` | 30 个 NPC 的初始状态：身份、性格、目标、资产、关系 | 是 |
| `locations.yaml` | 8 个地点的出口与服务，构成连通的经济活动图 | 是 |
| `items.yaml` | 30 种可交易物品/服务及其基准价 | 是 |
| `verbs.yaml` | 19 个动词声明与角色权限（13 玩家动词 + 5 计划器/内部动词 + 1 玩家心跳） | 是 |
| `economy.yaml` | 工资、生活成本、税率、开店成本、押镖、门派门槛与闭环说明 | 是 |
| `sects.yaml` | 3 个门派模板：宗旨、门槛、技能方向、驻地、招揽对象 | 否（P2/P3） |
| `escort_routes.yaml` | 4 条镖局线路：路径、天数、货值、镖银、风险事件 | 否（P2/P3） |
| `quests.yaml` | 14 个任务模板，覆盖跑腿/护送/采购/打听/代工/讨债，含 2 张零成本新手起步单 | 是（P3 任务系统） |
| `personas/` | 30 张 NPC 人设卡，供 LLM 决策层做 prompt 采样 | 是（P3 对话层） |
| `validate_content.py` | 纯标准库校验脚本，校验以上全部数据 | 是（工具） |

运行校验：

```bash
cd world-sim/content
python3 validate_content.py
```

退出码 `0` 表示硬性检查全部通过，`1` 表示有错误。脚本不 import `yaml`，也不 import 引擎代码；内置解析器只覆盖本目录用到的 YAML 子集。PyYAML 不是运行依赖，但用 PyYAML 打开这些文件也应无语法错误。

## 字段含义

### npcs.yaml

```yaml
npcs:
  - id: shen_yanzhou          # 小写字母 + 下划线，全局唯一
    name: 沈砚舟
    role: 铁匠                 # 必须出现在 economy.wages_daily 与 living_costs.by_role
    home: smithy              # 必填，必须是 locations.yaml 中真实存在的地点 id
    personality:
      traits: [寡言, 较真, 护短] # 2-4 项
      speech_style: "..."       # 给对话生成用的口吻提示
    goals:                      # 每人 2-3 条，priority 1 最高
      - {id: goal_shen_yanzhou_1, type: advance_skill, priority: 1}
    assets:
      money: 1000               # 非负整数，单位文
      items: [{id: iron_ingot, qty: 6}]
    relations:                  # 每人 2-4 条
      - {target: pei_wujiu, type: friend, value: 30}
      - {target: liu_sanniang, type: debtor, value: -25, amount: 60}
```

**关系方向很重要。** 每条关系是 `owner -> target`：

- `creditor`：owner 是债主，target 欠 owner 钱；对方必须有一条 `debtor` 回指。
- `debtor`：owner 欠 target 钱；对方必须有一条 `creditor` 回指。
- `master`：owner 是 target 的师父；对方必须是 `disciple`。
- `disciple`：owner 是 target 的徒弟；对方必须是 `master`。
- `family`：双向 `family`。
- `friend` / `rival`：允许单向，用来制造"我拿你当朋友、你未必"的张力。
- `value`：-100..100。债务/竞争取负，亲情/师承取正。
- `amount`：**仅 `creditor` / `debtor` 必填**，正整数，单位文；同一对 NPC 的两个方向必须写同一个数额。
- `long_term`：可选布尔值，只用于债务。若某债务人所有 `debtor` 的 `amount` 合计超过 `assets.money × 2`，该人的所有 `debtor` 关系都必须显式写 `long_term: true`。

当前共有 **12 笔债务、合计 1,230 文**，涉及 11 名债务人、8 名债权人：

| 债务人 | 债权人 | amount |
| --- | --- | ---: |
| 罗轻絮 | 柳三娘 | 60 |
| 陆九斤 | 宋鹤年 | 80 |
| 温小满 | 宋鹤年 | 120 |
| 邢破军 | 宋鹤年 | 60 |
| 常半夏 | 苏合香 | 80 |
| 沈砚舟 | 阮青蚨 | 90 |
| 石敢当 | 裴无咎 | 100 |
| 段五斗 | 金满仓 | 300 |
| 阮青蚨 | 金满仓 | 120 |
| 卢杏花 | 金满仓 | 150 |
| 蒋听雨 | 严守一 | 40 |
| 蒋听雨 | 顾青崖 | 30 |

全部债务都低于债务人初始现金的 2 倍，因此没有任何一笔标 `long_term`。最高负债率是温小满与卢杏花，均为现金的 30%（120/400、150/500）。

`goal.type` 取值固定为：`earn_money / advance_skill / gain_status / build_relation / build_shop / join_sect / found_sect`。

`home` 表示居住地，不是工作地。当前 30 人全部有 `home`，8 个地点均有人居住，单地点不超过 8 人：

| 居住地 | 人数 | 主要职业 |
| --- | ---: | --- |
| market | 7 | 绣娘、屠户、媒婆、货郎、掌柜、捕快 |
| inn | 5 | 脚夫、酒保、捕快、说书人、掌柜 |
| ferry | 4 | 船夫、更夫、渔娘、菜农 |
| pharmacy | 4 | 掌柜、游医、药童 |
| temple | 4 | 香客、算命先生、抄经人、庙祝 |
| smithy | 2 | 铁匠、石匠 |
| escort_agency | 2 | 镖师 |
| academy | 2 | 师爷、学究 |

### locations.yaml

- `exits` 是无向图的边，必须双向声明（A 写了 B，B 也必须写 A）。
- `services` 是物品 id 列表，表示此地可买到/可提供的物品与服务；至少一项，保证没有"纯过路空地"。
- 8 个地点必须连通。当前结构：集市是主枢纽，铁匠铺—镖局—河渡—客栈—城隍庙—药铺/书院互相咬合。

### items.yaml

- `category`：`food / material / tool / medicine / luxury / service`。
- `base_price`：单位文。当前量级为阳春面 5 文、炊饼 3 文、好刀 450 文、参片 120 文、玉佩 500 文。
- `service` 类物品（客房、诊金、护送、说书、渡船、香火、抄经）应挂在某个地点的 `services` 里，而不是塞进 NPC 背包。

### verbs.yaml

- **P1 不声明 `cooldown`。** 冷却约束将在 P2 计划器实现后随动作窗一起回归，P1 阶段以 `last_action_tick` 轻量节流代替。`verbs.yaml` 里保留该字段只会造成文档与引擎行为不一致。
- `args`：简化的 JSON-Schema 描述，引擎自行解释；`int>=1`、`array<...>` 等是约定字符串，不是完整 JSON Schema。
- `allowed_roles`：`"*"` 表示所有职业；否则必须是 `economy.wages_daily` 中存在的职业名。
- 当前 `verbs.yaml` 声明 **19 个动词**：13 个玩家动词（`move / cultivate / work / consume / buy / sell / trade / apprentice / join_sect / marry / open_shop / escort / found_sect`）+ 5 个意图层/计划器动词（`talk / recruit / quest_accept / quest_deliver / debt_settle`）+ 1 个玩家在线心跳（`player_heartbeat`）。`player_enter / player_leave / player_presence` 由引擎侧白名单解释，不在内容包声明。引擎内的 `system_*` 动作不是玩家动词，按设计不出现在 `verbs.yaml`。
- `apprentice` 只表示**拜师**，`args.target` 必须是 NPC；`join_sect` 表示**加入门派/讲会**，`args.target` 是门派组织 id 或地点 id，`dues` 是入会费。两者不再互相兼任。
- 方寸心的 `join_sect` goal 指向书院讲会（组织/地点），由 `join_sect` 承载；她与白露生的师徒关系仍由 `apprentice` 和 `master/disciple` 关系承载。

### economy.yaml

- `wages_daily`：税前日薪。
- `living_costs.by_role`：每日 `food / housing / upkeep / dues` 固定支出。
- `taxes`：所得税 10%、交易税 3%、店铺月税 60 文。
- `money_supply`：NPC 初始货币 35,650 文；地点金库 11,500 文；`sources: []` 表示没有造币来源。
- `payroll_rules`：金库不足时记欠薪，不允许负余额或增发。
- `shop_opening`、`escort`、`sect`：开店、押镖、立派的门槛与报酬。
- `goal_targets`：目标量化。5 个键 `wealth / mastery / bond / shop / faction` 是**引擎契约**，引擎按 goal kind 读取标量值，因此这一段只放标量。`goal_targets_by_type` 另给出 7 类内容目标的完整定义、推导依据与派生门槛，引擎不直接读取。
- `closure_model`：闭环公式、假设与估值标记。

**goal_targets 的 7 类量化目标**：

| 目标类型 | 引擎 kind | 引擎值 | 量化含义与推导 |
| --- | --- | ---: | --- |
| `earn_money` | wealth | 600 文 | 约人均初始资产 1,188 文的 50%；最低日薪职业 72 日毛净收入 907 文的 66%，需约 48 个工作日 |
| `advance_skill` | mastery | 0.60 | 0-1 熟练度，等价 5 级制的 3 级；每级 120 小时，共 240 小时，占 72 日 576 小时上限的 42% |
| `gain_status` | faction | 1 次 | 一次组织事件；配套声望门槛 40，与 `sect.min_reputation` 对齐 |
| `build_relation` | bond | 40 | 交易 +3、拜师 +5、成婚 +10；需约 14 次交易，高于现有 friend 上限 35 |
| `build_shop` | shop | 1 家 | 资金门槛 600 = 摊位 300 + 首月周转 300；引擎实际只校验 `shop_opening.stall=300` |
| `join_sect` | faction | 1 次 | 会费 50，约为中位 NPC 8 天净结余，最低收入者 72 日可攒 101 文 |
| `found_sect` | faction | 1 次 | 2000 会底 + 500 注册费 = 2500 文，占全区域净资产 47,150 文的 5.3%；5 人成派 |

`found_sect.min_members: 5` 保留。30 人单区域下 5 人约占 17% 人口：2-3 人太容易，过半则单区域无法容纳两个门派；配合 2500 文与声望 40 的前置，5 人是中高门槛。引擎后续会强制该人数。

**闭环公式**：

```
日净结余 = 日薪 × (1 - 10% 所得税) - 食物 - 住房 - 工具维护 - 行会/人情
```

按当前参数，24 个职业的日净结余为 **1.4 ~ 10.4 文/日**，72 日累计 **101 ~ 749 文**。最低是更夫（1.4），最高是掌柜（10.4）。因此没有一个职业长期净负，也没有一个职业在 72 日内翻倍式暴增。

## P2 内容资产

以下四块数据是为 P2 计划器与未来 LLM 决策层准备的。**P1 引擎不读它们**，校验脚本只保证其内部自洽、引用有效、数值合理。

### sects.yaml（3 个门派模板）

| id | 门派 | 驻地 | 发起人 / 备选 | 技能方向 | 招揽对象 |
| --- | --- | --- | --- | --- | --- |
| `qingyi_society` | 清议社 | academy | 白露生 / 谢停云 | 治学、算术、辩才 | 方寸心、谢停云、严守一、蒋听雨、韩杏林 |
| `pharmacy_guild` | 药行会 | pharmacy | 苏合香 / 顾青崖 | 医术、辨药、账目 | 韩杏林、柳三娘、顾青崖、宋鹤年、常半夏 |
| `biao_league` | 镖行同盟 | escort_agency | 卓不凡 / 裴无咎 | 武艺、押镖、谈判 | 裴无咎、崔铁衣、邢破军、秦望川、石敢当 |

- 三个门派统一使用 `economy.sect` 的门槛：会费 50、声望 40、成员 5、会底 2000、注册费 500，`founding_cost = 2500`。任何一项与 `economy.sect` 不一致都会被校验拒绝。
- **72 日可达性**（校验脚本按职业日净结余实算，口径与引擎 `found_sect` 前置一致——只校验 2,000 文会底，注册费 500 在成立时另扣）：
  - 白露生：1,600 文 + 学究净结余 6.6/日 × 72 ≈ 2,075 文；现有声望 70。
  - 苏合香：3,100 文，直接越过 2,000 文门槛；现有声望 70。
  - 卓不凡：2,400 文，直接越过门槛；现有声望 100。
  - 注意：若 P2 把 `founding_cost=2,500` 解释为“成立时必须一次性持有 2,500 文”，白露生到第 72 日仍差约 425 文，届时应与谢停云/方寸心合股或延后数日。这是预留数据与引擎现有前置的真实差异，详见下文冲突字段。
- 三人当前都只有 1 名达到 `min_relation_value=30` 的成立成员（引擎口径只算 `disciple/friend/family/ally`），因此每个门派都配置了 5 名可招揽对象，按现有关系值再补 2-5 次互动即可凑齐 5 人。
- `seat` 与 `activity_locations` 全部指向 `locations.yaml` 中真实存在的地点。

### escort_routes.yaml（4 条镖局线路）

| id | 线路 | 天数 | 货值区间 | 镖银区间 | 风险率 |
| --- | --- | ---: | ---: | ---: | ---: |
| `route_market_short` | 镖局→铁匠铺→集市 | 1 | 100-180 | 120-140 | 0.06 |
| `route_ferry_temple` | 镖局→河渡→客栈→城隍庙 | 2 | 180-300 | 150-190 | 0.12 |
| `route_academy_temple` | 镖局→铁匠铺→集市→书院→城隍庙 | 3 | 300-500 | 190-230 | 0.18 |
| `route_full_loop` | 镖局→河渡→客栈→集市→药铺→城隍庙 | 5 | 500-800 | 230-260 | 0.26 |

- 4 条线路全部从 `escort_agency` 出发，路径逐跳合法（校验脚本按 `locations.yaml` 的 `exits` 检查）。
- 按 `duration_days` 排序后，`cargo_value_max / fee_max / risk_loss_prob` 严格递增；最短线路 1 日，满足“2 天内可完成”。
- `risk_events` 的 `effect` 只使用 `delay / toll / theft / injury / cargo_loss` 五种；每条线路的事件概率合计等于 `risk_loss_prob`。
- `fee_min/fee_max` 落在 `economy.escort.fee_min=120` 与 `fee_max=260` 之间，`cargo_value_min` 不低于 `cargo_min_value=100`，`agency_cut` 与全局 0.30 一致。

### quests.yaml（14 个任务模板）

| 类别 | id | 委托人 | 动词 | 时限 | 现金报酬 |
| --- | --- | --- | --- | ---: | ---: |
| 跑腿·起步 | `errand_market_ledger` | 金满仓（market） | trade | 2 日 | 10 文 |
| 跑腿·起步 | `errand_market_sweet` | 孟九娘（market） | trade | 2 日 | 9 文 |
| 跑腿 | `errand_silk_sample` | 柳三娘 | trade | 2 日 | 8 文 |
| 跑腿 | `errand_academy_notice` | 严守一 | trade | 3 日 | 8 文 |
| 护送 | `escort_iron_short` | 裴无咎 | escort | 2 日 | 12 文 |
| 护送 | `escort_medicine_temple` | 卓不凡 | escort | 4 日 | 22 文 |
| 采购 | `procure_mugwort` | 严守一 | buy | 2 日 | 28 文 |
| 采购 | `procure_charcoal` | 宋鹤年 | buy | 2 日 | 36 文 |
| 打听 | `inquiry_luo_marriage` | 孟九娘 | trade | 2 日 | 8 文 |
| 打听 | `inquiry_academy_lecture` | 曹半仙 | trade | 2 日 | 8 文 |
| 代工 | `commission_stonework` | 石敢当 | work | 2 日 | 12 文 |
| 代工 | `commission_herb_sorting` | 韩杏林 | work | 2 日 | 14 文 |
| 讨债 | `debt_duan_to_jin` | 金满仓 | trade | 3 日 | 25 文 |
| 讨债 | `debt_lu_to_song` | 宋鹤年 | trade | 2 日 | 8 文 |

- 六类覆盖，共 14 个；跑腿 4、护送 2、采购 2、打听 2、代工 2、讨债 2。全部只使用 `verbs.yaml` 已注册的动词，`intent_args` 可直接作为 `Intent.args`（buy 用 `item/qty/from`、trade 用 `target/offer/want/money_delta`、work 用 `at/hours`、escort 用 `route/cargo_value/fee`）。
- **跑腿类改用零金额 `trade` 而不是 `move`**：P3 执行器会先把任务执行者移动到目标点，再执行 `quest.verb`；若 `quest.verb=move`，第二次同点 move 必然触发 `not_adjacent`。零金额 trade 表示“与目标 NPC 当面交接”，既符合送绸样/送口信的语义，也不再制造必然拒绝。
- 报酬区间 **8-36 文**；最高日薪 36 文，单任务上限 2 日 = 72 文，折合每日不超过 36 文，不会出现“跑一趟赚 300 文”。
- 12 个任务可重复，2 个讨债任务 `repeatable: false`。讨债本金（300 / 80 文）通过 `settlement.kind: remit_to_giver` 交回债权人，不计入跑腿报酬；账本金额与 `npcs.yaml` 的 `creditor.amount` 一致。
- 任务依赖的目标 NPC/地点/物品全部来自现有数据；采购与打听任务允许 `offer`/`want` 为空列表。
- **所有模板 `deadline_days >= 2`**：规则规划器只有在 `deadline_tick - cursor >= TICKS_PER_DAY` 时才肯接单；1 日单在当天计划时剩余不足 1 整天，会被直接跳过并最终过期。这是 5 个零完成模板的共同根因。
- **采购单必须写 `entry_cost`，且 `name` 必须带“垫付N文”**：任务板直接显示 `name`，新手接单前就能看到要垫多少钱。`entry_cost` 由基准价 × qty 自动校验，不是随口写的估值。
- **人参 120 文不设任务模板**：它只在药铺作为可选商品出售，不上海任务板，避免新手点单后才发现买不起。校验器保留一条规则：任何 `entry_cost > 50` 的采购模板必须标记 `optional: true` 且名称带垫付金额，防止以后把高价单悄悄加回任务板。
- `procure_mugwort`：艾草 6 文/捆 × 3 = 垫付 18 文，报酬 28 文，净赚 10 文；`procure_charcoal`：木炭 15 文/担 × 2 = 垫付 30 文，报酬 36 文，净赚 6 文。两者都只做正收益，避免把玩家逼进 `urgent_money`（现钱 < 40 文）。

#### 新手起步单与集市可接任务

- `errand_market_ledger`（金满仓 → 宋鹤年，market→inn，1 跳）和 `errand_market_sweet`（孟九娘 → 白露生，market→academy，1 跳）是零垫付起步单：`entry_cost: 0`、无 `target_item`、报酬 10 / 9 文、时限 2 日、发布人 `home=market`（玩家出生点）。
- 静态校验要求 **≥3 个 distinct 发布人常驻 market，且各自至少 1 张 repeatable 模板**；当前 3 位发布人 / 4 张模板：柳三娘 `errand_silk_sample`、孟九娘 `inquiry_luo_marriage` + `errand_market_sweet`、金满仓 `errand_market_ledger`。
- **发布人会按日程走动，接单前必须确认位置。** 任务板 `_quest_board_view` 已返回每个任务的 `giver_location`（发布人当前地点）和 `can_accept_here`（玩家是否就在发布人所在点），前端应直接展示这两个字段，不要只写发布人的固定 home。
- 10 天 headless run（`runs/framework/content10d`，seed=42，与 framework_check 第 1 项同源）逐 tick 统计“发布人在 market 且任务仍 open”的数量：每日峰值 day0 3、day1 6、day2 5、day3 3、day4 5、day5 6、day6 5、day7 6、day8 4、day9 5，**10/10 天都曾达到 ≥3**；但每日中位数多为 1，全天约 27/144 tick（18.6%）达到 ≥3。**首发体验不能依赖峰值**：NPC 会先接单、发布人会出门，集市随时可能只剩 0-2 张。README 明确写明“发布人会走动，接单前确认位置”，任务板已返回 `giver_location` 与 `can_accept_here`，前端按实时位置显示，而不是按 home 写死。
- 两张起步单在 10 天 headless run 中的实测：`errand_market_sweet` 被接 8 次、交付 7 次；`errand_market_ledger` 被接 4 次、交付 4 次。headless 没有玩家实体，走的是同一套 `quest_accept → trade → quest_deliver` 路径。

### personas/（30 张 NPC 人设卡）

- 每个 NPC 一张 `<npc_id>.md`，标题结构固定为：身份/居住地、`一句话性格`、`说话风格`、`日常锚点`、`在意的东西`、`与谁有张力`、`遇事反应倾向`。
- **每卡 300-500 token（按项目口径 `tokens = ceil(len(text)/4)`，即 1,200-2,000 字符）**，30 张全部达标。当前分布：min 300 / median 336.5 / max 361 token。
- 每卡 4 句 `>` 示例台词，30 张共 **116 句**，跨卡逐字去重全部通过。扩写分两批完成：第一批 15 张旧短卡（71-80 token）整体重写，第二批 11 张半成品卡增量补写；每批落地前后都跑过 `validate_content.py` 与 `framework_check.py` 第 1 项。
- 新增内容必须是**可在对话里引用的事实**，而不是形容词：口头禅原句、明确忌讳话题、对某类人的态度、固定时辰/物件/路线、与具名 NPC 的旧账（金额与方向对齐 `npcs.yaml`）。
- 口吻按 `npcs.yaml` 的 `traits` 与 `speech_style` 区分：铁匠说“火候不到”，捕快说“依律”，师爷说“依在下愚见”，货郎把一文钱掰两半，等等。
- 校验脚本会检查 30 张卡齐全、6 个标题齐全、token 300-500、每卡至少 2 句台词，并做**跨卡逐字去重**：任意两张卡的台词或“一句话性格”完全相同都会报错。

## 2026-11-04 内容收口记录

本轮只改 `content/` 下的数据与校验脚本，没有改 `src/`、`ui/`、`client/`、`config/`、`tests/`，也没有执行 git 写操作。

### 5 个零完成任务模板与 2 个高过期模板

| 模板 | 字段 | 修改前 | 修改后 | 为什么能完成 |
| --- | --- | --- | --- | --- |
| `errand_silk_sample` | `deadline_days` / `verb` / `reward.money` | 1 / move / 6 | 2 / trade / 8 | 1 日单剩余不足 1 整天，规划器从不接单；改为 2 日后可接。`move` 任务会被执行器重复下发同点 move，改零金额 trade 消除必然拒绝。委托人在 market，目标药铺 1 跳。 |
| `errand_academy_notice` | `deadline_days` / `verb` / `reward.money` | 2 / move / 5 | 3 / trade / 8 | 委托人严守一在 temple，书院 1 跳（temple→academy）；3 日给足接单与往返窗口。 |
| `escort_medicine_temple` | `deadline_days` / `reward.money` | 3 / 18 | 4 / 22 | 镖路 escort_agency→ferry→inn→temple 共 3 跳，4 日覆盖路程与交付；22 文仍低于 2 日上限 72 文。 |
| `procure_ginseng` → `procure_mugwort` | `giver` / `target_npc` / `target_item` / `qty` / `deadline_days` / `reward.money` / `entry_cost` | 苏合香 / 苏合香 / ginseng / 1 / 1 / 8 / 无 | 严守一 / 严守一 / mugwort / 3 / 2 / 28 / 18 | 人参 120 文、报酬 8 文，净亏 112 文；艾草 6 文/捆，3 捆 18 文、报酬 28 文，净赚 10 文。药铺 `services` 含 mugwort，严守一在 temple，1 跳可达。 |
| `inquiry_luo_marriage` | `deadline_days` / `reward.money` | 1 / 6 | 2 / 8 | 委托人与目标都在 market，0 跳；1 日接不到，2 日可接。 |
| `commission_stonework` | `deadline_days` | 1 | 2 | 委托人与工作地都在 smithy，0 跳；只需把接单门槛补足。 |
| `commission_herb_sorting` | `deadline_days` | 1 | 2 | 委托人与工作地都在 pharmacy，0 跳；同上。 |

另有两条全局改动：`errand` 类的校验映射从 `move` 改为 `trade`；`verbs.yaml` 增加 `player_heartbeat`（引擎已注册该玩家生命周期动词，内容包补齐元数据）。

### 依据与估值

**有依据（可由数据或代码直接验证）：**

- `deadline_days >= 2`：规则规划器接单条件为 `deadline_tick - cursor >= TICKS_PER_DAY`（144 tick），1 日单当天计划时必然不足。
- 报酬上限 72 文：`economy.yaml` 最高日薪 36 文 × 2 日。
- 艾草 6 文/捆、人参 120 文：`items.yaml` 基准价；药铺 `services` 含 `mugwort`。
- market↔pharmacy、temple↔academy 均为 1 跳：`locations.yaml` 的 `exits`。
- 跑腿改 `trade` 的依据：引擎任务执行器对 `move` 任务会在到达后再次执行同点 move，`move` 动作要求目标相邻，故必然 `not_adjacent`。10 天 headless 实测拒绝率最终为 0.03%（1/3307，唯一一条是药铺库存不足的 `buy`），`quest_accept:not_at_giver` 归零。
- 完成率证据：2026-11-04 的 10 天 headless run 中 14 个模板全部有完成：`errand_market_sweet` 8 接/7 交、`errand_market_ledger` 4 接/4 交、`errand_academy_notice` 5/5、`errand_silk_sample` 6/6、`escort_iron_short` 5/5、`escort_medicine_temple` 3/3、`procure_mugwort` 4/4、`procure_charcoal` 7/7、`inquiry_luo_marriage` 5/5、`inquiry_academy_lecture` 7/7、`commission_stonework` 7/7、`commission_herb_sorting` 4/4、两张讨债单各 1/1。
- 货币闭环：任务报酬全部由发布人钱包转给接单者（`quest_reward` transfer），引擎不凭空铸钱；提高艾草/木炭报酬只是把 giver 的存量货币转给玩家。`yan_shouyi` 初始 1300 文、`song_henian` 3300 文，按 10 天各完成 4/7 次计算，分别转出约 112 / 252 文，不触发发布人破产，也不改变全服 `minted/burned` 统计。
- 集市可接任务：静态校验保证 4 个 repeatable 模板的发布人 `home=market`；动态 10 天 run 每日峰值 3-6（10/10 天达 ≥3），但中位数为 1，只有约 18.6% 的白天 tick 同时有 ≥3 张。因此按 fallback 处理：README 明确写明“发布人会走动，接单前确认位置”，任务板已返回 `giver_location` 与 `can_accept_here`。
- 高价采购门槛：`entry_cost` 字段 + `name` 的“垫付N文”双写；人参 120 文不设模板，只作为药铺商品。`entry_cost > 50` 的模板必须 `optional: true`，否则校验 FAIL。

**估值（没有唯一正确值，按经济区间取的保守值）：**

- 报酬上调到 8/28/36/22 文：按“单任务不超过 2 日最高日薪”与“采购需覆盖成本并有正收益”取整，没有做边际收益建模。
- `escort_medicine_temple` 的 4 日：按 3 跳 + 1 日交付缓冲估，不是实测最优值。
- persona 扩写中的口头禅、忌讳、旧账细节：属于创作内容，只保证与 `npcs.yaml` 的债务/关系/职业不矛盾，不是从数据推导出的唯一答案。

## 引擎读取范围：P1 已生效 vs P2/P3 预留

| 文件 / 字段 | P1 引擎行为 | 状态 |
| --- | --- | --- |
| `npcs.yaml` 全字段、`home` | 实际读取并初始化世界 | 已生效 |
| `locations.yaml`、`items.yaml`、`verbs.yaml` | 实际读取 | 已生效 |
| `economy.yaml` 的工资/生活成本/税率/开店/`escort` 全局参数/`sect` | 实际读取 | 已生效 |
| `economy.yaml` 的 `goal_targets_by_type` | 不读，仅供审计与 P2 计划器 | 预留 |
| `sects.yaml` **全部字段** | 不读；`found_sect` 只用 `economy.sect`，且不自动扣除会费、不自动招募 | 预留 |
| `escort_routes.yaml` 的 `origin/destination/route` | 不读文件；线路由 `Intent.args.route` 自带 | 预留 |
| `escort_routes.yaml` 的 `duration_days` | 不读；P1 统一 `economy.escort.duration_hours=24` | 预留 |
| `escort_routes.yaml` 的 `risk_loss_prob` | 不读；P1 统一 `economy.escort.risk_loss_prob=0.15` | 预留 |
| `escort_routes.yaml` 的 `fee_min/fee_max/agency_cut` | 不读；P1 按 `Intent.args.fee` 结算，抽成固定 `agency_cut=0.30` | 预留 |
| `escort_routes.yaml` 的 `risk_events` | 不读；P1 只有成功/失败二元结算 | 预留 |
| `quests.yaml` 的 `giver/target_location/verb/intent_args/deadline_days/reward/settlement` | P3 任务系统读取：接单要求 actor 在 giver 当前地点，交付要求 `ready` 且 `at_target`/`at_giver`；采购交付消耗 `target_item` | 已生效 |
| `quests.yaml` 的 `category` | 引擎不读，只用于内容审计与校验映射 | 预留 |
| `personas/` **全部内容** | P3 LLM 决策层把整张卡注入 prompt（`intent.py` 里 reason 取前 26 字符） | 已生效 |

**最可能与引擎产生冲突的字段**（P2 接入时必须先统一口径）：

1. `escort_routes.duration_days` vs `economy.escort.duration_hours`：逐线天数与全局固定 24 小时是两套模型，P2 必须改成“按线路取时长”，否则线路天数永远不生效。
2. `escort_routes.risk_loss_prob` vs 全局 `economy.escort.risk_loss_prob=0.15`：P1 完全忽略逐线风险，P2 若直接叠加会出现 0.15 与 0.06-0.26 的双重计算。
3. `escort_routes.agency_cut` 与 `fee_min/fee_max`：P1 只吃调用方传入的 `fee`，P2 若改为按线路定价，需要明确“报价来自文件还是调用方”。
4. `sects.min_money/founding_cost/dues/min_members` vs `economy.sect`：内容模板的门槛与引擎全局门槛目前数值一致，但引擎只读 `economy.sect`，且前置只校验 `min_money=2000`，不会校验 `founding_cost=2500` 是否一次性备齐。若 P2 允许门派自定义门槛或改为一次性校验 2500，必须决定以哪一份为准；白露生的 2,075 文正好卡在这个差异上。
5. `quests.intent_args.money_delta` 与 `settlement`：`trade` 的现有语义是双方自愿交换，讨债是强制结算，P2 需要新增结算步骤或限制 `trade` 的用法。
6. `quests.deadline_days` 与规划器门槛：引擎接单要求剩余 `>= TICKS_PER_DAY`，所以 1 日单永远接不到；内容侧已统一改为 `>= 2`，但若未来引擎放宽门槛，需要同步回内容审计。
7. `quests` 的跑腿动词：引擎对 `move` 任务会重复下发同点 move（到达后必然 `not_adjacent`）；内容侧改用零金额 `trade` 规避，属于内容对引擎执行顺序的适配，不是引擎修复。


## 修改方式

### 加一个新 NPC

1. 在 `npcs.yaml` 的 `npcs` 列表末尾追加一项，`id` 用新的小写字母+下划线组合。
2. 填 `home`，必须是 `locations.yaml` 中已有的地点 id；避免让单地点超过 8 人。
3. 填 2-3 条 goal，至少包含两种不同 `type`。
4. 填 2-4 条 relation；凡是 `creditor/debtor/master/disciple/family`，都要在目标 NPC 那里补一条镜像关系。债务两端写相同的 `amount`；单人所欠总额超过 `assets.money × 2` 时，该人的所有 `debtor` 关系都要加 `long_term: true`。
5. 新职业需要同时在 `economy.yaml` 的 `wages_daily` 和 `living_costs.by_role` 中补条目。
6. 把新增 NPC 的 `assets.money` 计入 `money_supply.initial_npc_total`（否则校验会报"声明不一致"）。
7. 运行 `python3 validate_content.py`。

### 加一个新地点

1. 在 `locations.yaml` 追加地点，`id` 小写字母+下划线。
2. 在至少一个已有地点的 `exits` 里加上它，并在新地点的 `exits` 里写回对方（双向）。
3. 给它至少一个 `services` 物品 id。
4. 在 `economy.yaml` 的 `money_supply.location_float` 加一笔金库，并同步 `initial_location_float_total`。
5. 运行校验，确认"地点图连通"仍通过。

### 加一个新物品

1. 在 `items.yaml` 追加 `id / name / category / base_price`。
2. 至少让一个 NPC 的 `assets.items` 或一个地点的 `services` 引用它，否则会报"物品被引用"失败。
3. 若是 `service` 类，必须挂到地点的 `services`。

### 调经济数值

只改 `economy.yaml` 的 `wages_daily` / `living_costs` / `taxes` 即可；校验脚本会自动重算每个职业的日净结余，并检查：

- 最低净结余 > 0 且 ≥ 0.5 文/日；
- 最高净结余 ≤ 15 文/日；
- 初始货币总量与 `money_supply.initial_npc_total` 一致；
- `money_supply.sources` 为空。

## 校验覆盖范围

`validate_content.py` 会检查：

- 8 个 YAML 文件可被内置解析器解析，顶层键正确；
- NPC id 唯一、格式合法、姓名唯一、goal id 唯一；
- NPC 字段完整，`home` 必填且指向有效地点，8 个地点都有人居住，单地点 home 不超过 8 人；
- goals 2-3 条且类型不单一，relations 2-4 条；
- relations 指向存在、不含自指、value 在 -100..100；
- 债务金额必填且为正、双向金额一致、单人所欠总额不超过初始现金 × 2（超额需 `long_term: true`）；
- 债务/师徒/亲属关系的镜像一致，债务链与师承链无闭环，关系网连通；
- 30 人货币总量 24,000-45,000、人均 800-1,500；
- 资产物品存在、qty ≥ 1、money ≥ 0；
- 地点 8 个、出口对称、整图连通、services 非空且指向存在的物品；
- 物品 20-30 种、category 合法、base_price 为正且落在常见区间；
- 所有物品至少被 NPC 资产或地点服务引用一次，所有 service 类物品挂在地点；
- 19 个内容包声明动词齐全（13 玩家 + 5 计划器/内部 + `player_heartbeat`）、不再声明 `cooldown`、参数与 allowed_roles 合法；
- `join_sect` goal 与 `join_sect` 动词对齐，`apprentice` 与 `join_sect` 同时存在；
- 工资/生活成本覆盖所有职业，净结余为正且不暴增，货币存量声明一致；
- 7 类 goal 均至少有 NPC 使用；
- `goal_targets` 只含 5 个引擎 kind 且均为正数，`goal_targets_by_type` 覆盖 7 类目标且 `engine_value` 与引擎标量一致；
- `earn_money` 与 `advance_skill` 目标落在 72 日能力区间内（可达成但不轻易），`build_relation` 高于现有 friend 上限；
- `found_sect` 的人数/资金/声望门槛与 `economy.sect` 一致，2500 文总资本不超过全区域净资产；
- **门派**：3 个、id 唯一、必填字段齐全、`seat`/`activity_locations` 指向有效地点、发起人/备选/招揽对象指向有效 NPC、门槛与 `economy.sect` 一致、`founding_cost = min_money + registration_fee`、技能方向 2-4 项、招揽对象 ≥5 且不重复；发起人在 72 日内资金可达、现有声望达标、现有成员 + 潜在招募 ≥ 5；
- **镖局线路**：4 条、id 唯一、`route` 逐跳相邻且首尾与 `origin/destination` 一致、天数 > 0、最短 ≤2 日、货值与镖银区间合法且落在 `economy.escort` 范围内、`agency_cut` 与全局一致、风险率 0-1、`risk_events` 非空且概率合计等于风险率、effect 枚举合法；按天数排序后货值上限/镖银上限/风险率严格递增；
- **任务**：14 个、id 唯一、六类每类至少 2 个且动词映射正确（跑腿→trade、护送→escort、采购→buy、打听→trade、代工→work、讨债→trade）、委托人/目标 NPC/地点/物品指向有效、时限 ≥2 日、现金报酬为正且不超过 2 日最高日薪、折合每日不超过最高日薪、关系奖励 0-5；按动词校验 `intent_args` 字段；讨债任务不可重复、`settlement.kind` 为 `remit_to_giver`、金额与 `money_delta` 和账本 `creditor.amount` 三方一致；
- **新手起步单**：至少 1 个 `starter: true`，且必须是 errand、`entry_cost=0`、无 `target_item`、发布人 `home=market`、目标地点距 market ≤1 跳、报酬 8-15 文、时限 ≥2 日；
- **集市常驻任务**：至少 3 个 repeatable 模板的发布人 `home=market`；
- **采购门槛**：`entry_cost` 必须是整数且等于 `base_price × qty`；`entry_cost > 0` 时 `name` 必须包含“垫付N文”；`entry_cost > 50` 必须标记 `optional: true`；
- **任务可完成性静态检查**：`giver.home` 到 `target_location` 在 `locations.yaml` 出口图上可达；`move` 的 `args.to`、`buy` 的 `args.from/item`、`trade` 的 `args.target`、`work` 的 `args.at` 必须与任务目标字段一致；采购物品必须在该目标地点的 `services` 中出售；护送线路首跳等于委托人 home、末跳等于 `target_location` 且逐跳相邻；
- **人设卡**：`personas/` 存在、30 张卡与 NPC 一一对应、6 个标题齐全、token 300-500、每卡至少 2 句台词，且台词与“一句话性格”在任意两张卡之间不逐字重复。


## 动词语义边界

需求给定的 12 个动词里没有独立的 `join_sect`，会造成“拜师”与“入派”语义混淆。本内容包已把 `join_sect` 补为第 13 个玩家动词，当前共 19 个声明动词（13 玩家 + 5 计划器/内部 + `player_heartbeat`），边界如下：

| 场景 | 动词 | `target` | 配套数据 |
| --- | --- | --- | --- |
| 拜师学艺 | `apprentice` | NPC id | `master/disciple` 关系 |
| 加入门派/讲会/香会 | `join_sect` | 组织 id 或地点 id | `found_sect` 建立的组织、或书院/城隍庙等地点 |

`found_sect` 只负责"建立"组织，不负责"加入"；`join_sect` 只负责"加入"，不建立组织。这样引擎不会再把拜师当成入派处理。
