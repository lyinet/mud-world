# 可玩性审计（人类玩家视角，tag 冻结前）

- 审计对象：当前 HEAD 交付形态（`./start.sh`，legacy 匿名模式，`XKX_AMBIENT` 默认开启）
- 审计方式：真实 API 调用，按新玩家认知顺序操作；未修改任何代码；零模型调用（`talk` 回复均为 `compose_talk` 模板）
- 环境：`/Users/lyinet/projects/XKX/world-sim`
  - 主审计：`--auth off`，`XKX_AMBIENT=1`、`XKX_AMBIENT_SPEED=4`（等价于 `./start.sh` 默认自走），端口 8123，run `audit-ambient`
  - 对照：`XKX_AMBIENT=0`，端口 8122，run `audit-p3i2`（用于验证"世界静止"差异）
  - 玩家：`auditor_newbie` / 新客阿九 / 游侠（角色 镖师），初始 300 文 + 炊饼×3 + 粗布×1
- 说明：本轮只走 API 语义，未点击前端；前端读取的是同一份状态。

## 结论

**当前是「5 分钟可玩」。**

前 3 个动作就能拿到第一笔真实收益（委托 +10 文、关系 +15、声望 +10.5、成长线升级提示），随后 10 分钟内会连续撞上"买不了、卖不了、吃不了、雇不了、婚不了、建不了派"的硬墙。30 分钟里玩家能做的只有：接跑腿/打听/代工委托、打工、修炼、开店（空壳）、入行会（无权益）、看世界 feed。钱可以继续增加，但没有商品/服务可以买，也没有正收益的投资出口。

## 一、10 项判定表

| # | 目标 | 判定 | 玩家入口动作 | 前置条件 | 成功后的实际收益（实测） | 失败/断点原因码 |
|---|---|---|---|---|---|---|
| 1 | 修炼 | **半成品** | `cultivate`（hours 1–8） | 体力 ≥ 4×hours；角色不限 | 4 小时：镖师技能 0.3548→0.3648（+0.01），体力 −16，mastery 目标 +0.01 | `insufficient_stamina`；断点：技能数值不被工资、开店、任务读取，只用于展示/里程碑 |
| 2 | 与 NPC 互动 | **半成品** | `talk`（target + message ≤200 字） | 与目标同地点、玩家在线、消息非空 | 好感 +1、信任 +0.5；返回模板台词；关系写入状态 | `player_offline`、`not_together`、`bad_message`；无委托触发、无价格变化、无行为改变 |
| 3 | 结婚/结亲 | **不存在** | 无（`marry` 被白名单拦截） | — | — | `unknown_action`（"未知操作"）；NPC 侧世界会 `marry`，玩家侧无入口 |
| 4 | 做任务赚钱 | **可玩**（含采购断点） | `quest_accept` / `quest_deliver` | 在发布人同地点、任务 open、未过 deadline、同时 ≤2 单、部分需声望 | 引导 1/4 +10 文 + 关系 15；引导 2/4 +12 文 + 关系 15；打听酒价 +9 文 + 关系 10；客栈搬柴 +12 文 + 关系 10 | `quest_not_found`、`quest_not_open`、`not_at_giver`、`too_many_quests`、`reputation_too_low`、`quest_item_missing`、`quest_expired` |
| 5 | 消费/买装备 | **不存在** | 无（`buy`/`sell`/`consume` 均被拦截） | — | — | 三个动作全部 `unknown_action`；`trade` 只能送礼/物物，实测"1 炊饼换 10 文"为 `trade_unfair` |
| 6 | 开店（含雇人） | **半成品** | `open_shop`（stall/storefront/branch） | 同地点、无自有店、资金足、区域金库缓冲足 | 付 300 文开摊成功；生成空 business：无库存、无员工、无日结；`money 349→49` | `wrong_location`、`bad_shop_type`、`already_owns_shop`、`insufficient_money`、`treasury_buffer`、`treasury_guard`；雇人 `recruit` = `unknown_action` |
| 7 | 开镖局/押镖 | **半成品**（仅委托皮） | `quest_accept`/`quest_deliver` 接"押镖类委托" | 在镖局发布人处接、到目标地点交 | 短镖 22 文、送铁器 12 文、护药 22 文；无货物、无风险、无镖局分成 | 真正的 `escort` 动词 = `unknown_action`；NPC 侧参数为 120–260 文、抽成 30%、风险 15%、24 小时 |
| 8 | 招聘（雇 NPC） | **不存在** | 无 | — | — | `recruit` = `unknown_action`；开店后没有岗位/工资结算 |
| 9 | 建门派/当掌门 | **不存在** | 仅 `join_sect`（加入，不是创建） | 目标为组织/地点 id、同地点、未加入其它门派、会费足 | 实测 `join_sect target=pharmacy dues=50` 成功，扣 50 文；状态无 faction/sect 字段、无门派收益 | `unknown_sect`（传门派 id 时）、`not_at_sect`、`already_in_faction`、`already_member`、`insufficient_money`；`found_sect` = `unknown_action` |
| 10 | 观察世界 | **可玩**（依赖自走） | `GET /api/world/feed?limit=≤50`；`GET /api/player/state` 的 `nearby` / `today` | 无 | 自走开启时 feed 有 NPC 事件：动身前往集市、在集市做工、加入了门派；`today.next_action` 随世界变化 | 断点：`nearby`/`present_npcs` 的 `doing` 恒为空，看不到 NPC 当前动作；`XKX_AMBIENT=0` 时 feed 恒空、世界静止 |

## 二、四个必答问题

### Q1：30 分钟里第一个正反馈在第几分钟、是什么？

**第 1–3 分钟（进入后的第 3 个动作）**，不是 5 分钟后。

- 实测路径：进入集市 → 接 `tutorial_meet_sanniang` → 原地交付：**+10 文（300→310）**，与金满仓关系 **15**，声望 10.5，成长线出现升级提示。
- 次一级正反馈：第一次 `work` 8 小时，工资 30 文、扣税 3 文，**净 +27 文**；或第二单引导 **+12 文**。
- 因此"5 分钟内有没有正反馈"的答案是：有，而且很早。问题不在第一分钟，而在第 10 分钟后没有可持续的正反馈（见下）。

### Q2：赚钱之后能花在哪？

能花的出口只有 3 个，且都不产生可玩收益：

| 出口 | 实测/配置价格 | 说明 |
|---|---|---|
| 开摊 / 店面 / 分号 | 300 / 1200 / 3000 文 | `today.business` 明码标价；实测扣 300 文成功，但得到空店 |
| 行会会费 | 50 文（可传 0–100000） | 实测扣 50 文成功；加入后玩家状态无门派字段、无权益 |
| 送礼给 NPC | 任意 `money_delta < 0` | 实测"1 炊饼 + 20 文"成交，只换关系 +3；不能换物（对方无库存时 `want_not_held`） |

**不能花的地方（商品/服务价格存在，但玩家不可达）**：

- 食物 2–25 文（米糕 2、炊饼 3、阳春面 5、糙米 12、腊肉 25）
- 材料 9–60 文（麻绳 9、小米 11、竹篮 14、铁锭 45、生丝 60）
- 工具 25–450 文（绣针 25、镰刀 46、菜刀 80、铁锤 120、百炼刀 450）
- 药 6–120 文（艾草 6、艾条 14、金疮药 25、参片 120）
- 服务 2–180 文（渡船 2、说书 3、客房 12、诊金 20、护送 180）
- 奢侈品 180–500 文（绸缎 180、银簪 260、玉佩 500）

结论：**钱只能囤、送人、交租**；唯一"大额消费"是开店，但开店不产生收益，反而是负收益（见死胡同第 4 条）。

### Q3：死胡同清单（按出现频率排序）

1. **任何"想买东西/卖东西/吃东西"** → `buy` / `sell` / `consume` 全部 `unknown_action`。玩家 100% 会先尝试消费，这是最高频硬墙。
2. **采购类委托（含引导 3/4）** → 需要持有物品，但买不到；交付报 `quest_item_missing`，向 NPC 求购报 `want_not_held`。当前 27 张开放单里有 5 张采购单，且新手引导第 3 步必踩。
3. **与 NPC 聊天** → 只有模板回复 + 好感 +1/信任 +0.5；不触发委托、不改价格、不改 NPC 行为。实测绣娘柳三娘回的是"肉价按今日行情走"（台词与身份不匹配），且委托板不变。
4. **开店后继续打工** → `employer_of` 优先返回自己的店，店内现金为 0；实测"应得 30 文，实收 0 文"，并生成 `debt:wage:shop:player:...` 30 文（自己欠自己）。
5. **开店本身** → 300 文换空壳：不能进货（`buy` 不可用）、不能雇人（`recruit` 不可用）、没有日结收益。
6. **加入门派用门派 id** → `unknown_sect`；改用地点 id 能成功，但扣 50 文后看不到任何门派字段/收益/权限。
7. **跨地点移动** → 只能走相邻出口；实测 客栈→药铺 报 `not_adjacent`，必须 客栈→集市→药铺。玩家会误以为"点了就该到"。
8. **静置超过 60 秒** → 所有动作报 `player_offline`；重新 `enter` 若不带 `display_name` 会报 `bad_display_name`。真人读文本/看界面很容易触发。
9. **NPC 专属动作** → `marry` / `recruit` / `found_sect` / `escort` / `apprentice` / `debt_settle` 全是 `unknown_action`，提示为"未知操作"，而不是"该玩法未开放"。

### Q4：玩家与 NPC 的动作差

**同一套动作（同一实现，双方都走）**：`move`、`work`、`cultivate`、`talk`、`trade`、`quest_accept`、`quest_deliver`、`open_shop`、`join_sect`。

差异：玩家的 `work` 可在任意店铺顶替（`employer_of` 回退到当前地点第一家），NPC 受雇主/规划器约束；玩家的 `open_shop` 不进货不雇人，NPC 店铺由经济系统日结。

**只有 NPC 能做（动词存在，但不在玩家白名单）**：

| 动作 | 现状 | 原因 |
|---|---|---|
| `buy` / `sell` | NPC 规划器正常使用 | API 的 `WRITE_ACTIONS` / `ACTION_ALIASES` / `PLAYER_VERBS` 均未收录 |
| `consume` | NPC 日常进食使用 | 同上；玩家无法进食/恢复体力（只能等每日 +40） |
| `escort` | NPC 押镖：120–260 文、抽成 30%、风险 15%、24h | 未收录；但 `allowed_roles` 本就含"镖师"，玩家角色正是镖师 |
| `marry` | NPC 会结婚（feed 可见） | 未收录；P3-E 明确关闭 NPC 对玩家的婚配/强迫婚配 |
| `apprentice`（拜师） | NPC 师徒关系由系统生成 | 未收录 |
| `recruit`（招聘） | NPC 由规划器招揽 | 未收录；开店后无雇人入口 |
| `found_sect`（建派） | NPC 可立派（门槛 2000 文 / 声望 40 / 5 人 / 注册费 500） | 未收录 |
| `debt_settle` | NPC 债务结算 | 未收录；玩家只能被欠薪 |

## 三、下一波建议（按性价比排序）

### 候选 1（最高性价比）：让钱能花出去——开放玩家 `buy` / `sell` / `consume` + 价目曝光

- **目标**：300 文变成可用资源；修复引导 3/4 与 5 张采购单；让已接线的物价模型第一次被玩家感知。
- **改动面**：`src/wsim/api/session.py`（`WRITE_ACTIONS`、`ACTION_ALIASES`、`_args` 已支持 item/qty/from/to）、`src/wsim/actions/player.py`（`PLAYER_VERBS` 补 3 个）、`state.location.services` 带 `price`；复用 `BuyAction` / `SellAction` / `ConsumeAction`。
- **预估工作量**：0.5–1 人日（含 1 个端到端测试与 `docs/player-api.md` 更新）。
- **可玩性增量**：直接解锁第 5 项；补上第 4 项断点；第 6、10 项获得信息基础；新手 30 分钟第一次有"赚钱→消费→变强/补给"的闭环。
- **风险**：低。库存、税、金库现金都已实现，只需开闸 + 价目字段。

### 候选 2：把押镖还给镖师——开放 `escort`

- **目标**：玩家镖师能真正"接镖→带货→按路线走→承担风险→结算"，而不是用 `quest_deliver` 假装押镖。
- **改动面**：`session.py` 白名单 + `_args`（route/cargo_value/fee）、`PLAYER_VERBS`、复用 `EscortAction`；与任务槽的 `busy_on_escort` 互斥已存在。
- **预估工作量**：0.5–1 人日。
- **可玩性增量**：解锁第 7 项；给"镖师"身份一个独占玩法（当前身份差异只有初始技能 0.35 与一句文案）。
- **风险**：中。需要处理离线/超时/路线校验；NPC 经济参数已定，平衡改动小。

### 候选 3：开店经营闭环——进货 + 雇人 + 日结

- **目标**：300 文开店后有库存、能雇 NPC、每日有净收益；让第 6、8 项从"入口"变成"闭环"。
- **改动面**：`business.py`（初始库存/进货/岗位）、`OpenShopAction` 起始库存、向玩家开放 `recruit`（复用 `RecruitAction`）、系统日结工资/月租/营业税；`content` 岗位与工资表已存在。
- **预估工作量**：2–3 人日（含经济平衡与 golden 更新）。
- **可玩性增量**：解锁第 6、8 项；给钱一个正收益出口；让"雇人"成为玩家与 NPC 共用的动作。
- **风险**：中高。涉及金库缓冲、工资欠付、店铺盈亏与基线漂移。

**暂不建议本轮做**：结婚/结亲、建门派/收徒。它们依赖好感门槛、双方同意、组织成员与声望体系，单独立项 2–4 人日，且当前连"消费"都未闭环，做了也无法形成可玩循环。

## 四、审计中发现的两处实现级问题（供下一波参考，未改动）

1. **自走开关决定"世界是否可玩"**：`XKX_AMBIENT=0` 时 feed 恒空、世界完全静止；`XKX_AMBIENT=1` 时 feed 才显示 NPC 动作。若未来有部署默认关闭自走，第 10 项会从"可玩"直接掉到"不存在"。
2. **开店即自雇**：`employer_of` 先看 `ent.business`，导致玩家开店后再 `work` 的工资由自家空店支付，实收 0 并生成自己欠自己的工资债。这会让"开店"从目标变成惩罚。

## 五、证据索引

- 原始响应与脚本输出：`/tmp/audit/`（`talk_jin.json`、`qa1.json`、`qd1.json`、`buy1.json`、`trade1.json`、`work1.json`、`shop1.json`、`sect3.json`、`live_feed.json` 等）
- 事件库核对：`runs/audit-p3i2/events.sqlite`（`work` 两次：tick 72 实收 30 文；tick 108 实收 0 文 + `debt_create` 30）
- 内容价格与门槛：`content/items.yaml`、`content/economy.yaml`、`content/sects.yaml`、`content/verbs.yaml`
- 玩家动词白名单：`src/wsim/actions/player.py` 第 43–59 行；API 动词表：`src/wsim/api/session.py` 第 66–107 行

---

# 第二轮：商业化开闸后的复审

- 审计对象：商业化开闸后的 master（`buy` / `sell` / `consume` / `escort` / `recruit` / `open_shop` 已开放；雇工日程与 `work employer` 归因已修；押镖有货物托管）
- 环境：`--auth off`，`XKX_AMBIENT=1`、`XKX_AMBIENT_SPEED=4`（等价 `./start.sh` 默认自走），API `127.0.0.1:8124`，run `audit-r2`
- 玩家：`auditor2` / 新客阿七 / 游侠（角色 镖师），初始 600 文；审计期间跑过 12+ 个世界日（tick 0→1930）
- 边界：只审不改、零模型调用、未提交 git；证据来自真实 HTTP 动作与 `runs/audit-r2/events.sqlite`
- 注：审计时工作树本身已有未提交改动（`src/` 与 `ui/player/` 多个文件为 M，另有 `src/wsim/actions/marriage.py` 未跟踪）；本轮只追加本报告，未新增任何源码改动。运行中的服务对应当前工作树。

## 第二轮结论

**仍是「5 分钟可玩」，但这 5 分钟的闭环终于成立；30 分钟仍然断在经营与任务供给。**

- 买→吃→卖、委托、打工、押镖、开店、雇人都有真实事件与钱包/背包变化，不再是 `unknown_action`。
- 但四个关键闭环仍不成立：**工资不发只记欠款**、**店铺营收恒为 0**、**委托板约第 7 世界日后停发**、**押镖不带货也能白拿镖银（带货反而净亏）**。
- 前端「商业与经营」整块只有 HTML，没有 JS 绑定：标签页点不动、列表不渲染。后端已开的商业化能力，玩家在 UI 上基本看不到。

## 一、10 项判定表（第二轮）

| # | 目标 | 第一轮 | 第二轮 | 真实动作序列与事件证据 | 断点/原因码 |
|---|---|---|---|---|---|
| 1 | 修炼 | 半成品 | **半成品** | tick 678→1058 连续 `cultivate`，每次 −8 体力、技能 +0.005；技能到 0.482（熟练 Lv3），但工资/开店/委托都不读技能等级 | `insufficient_stamina`（本 run 38 次，最高频）；无突破/无等级解锁 |
| 2 | 与 NPC 互动 | 半成品 | **可玩（弱）** | tick 1819 `talk xing_pojun` 成功：好感 +1、信任 40.5、模板回复；`trade` 真实转物转钱（tick 293 艾条→曹半仙 +8 文）；委托可接可交 | `not_together`、`player_offline`、`bad_message`；对话内容仍是模板，不改价格/行为 |
| 3 | 结婚/结亲 | 不存在 | **不存在** | tick 1884 `marry` → `not_implemented_for_player`（“这个玩法还没对玩家开放。”） | `not_implemented_for_player` |
| 4 | 做任务赚钱 | 可玩（含采购断点） | **半成品** | 引导 4/4 全通：+12、+14、+10、+14；`tutorial_first_buy` 买 2 艾条 −28、交 1 得 +14；采购模板在 NPC 侧 5/5 都有完成记录；但 tick 1705/1740 集市委托板为 0 单 | 第 1 天可玩；`system_quest_post` 在 tick 1296/1488/1701/1741 均为空；`quest_not_open`、`not_at_giver`、`quest_not_accepted_by_actor` |
| 5 | 消费/买装备 | 不存在 | **可玩（消费）；买装备不存在** | tick 57 `buy flatbread` −3；tick 69 `consume` −3、体力 88→100（+25）；tick 75 `sell flatbread` +2；背包数不变（consume 是“现买现吃”，不吃背包存货） | 买卖闭环成立；无装备槽/装备动作，物品只有价格没有属性 |
| 6 | 开店（含雇人） | 半成品（空壳） | **半成品** | tick 426 `open_shop stall`：−300 租金、−45 初始进货，生成摊（炊饼×6/面×4/米×3）；tick 540 `recruit xing_pojun` 日薪 21；日结 tick 1059/1159/1296/1488/1701/1741 均“营收 0、工资 21、净利 −22”，工资未付→`debt:wage:shop:player:auditor2:xing_pojun` 每日 +21；雇工在集市（店内）可见、可对话；店铺投影只有 `id/kind/name/cash` | 工资不发只记欠款；营收恒 0；`state.shop` 无 employees/wage/debt；无进货/定价/辞退动作 |
| 7 | 开镖局/押镖 | 半成品（委托皮） | **半成品** | 无货单 tick 1309：`escort route_market_short fee130` 接单，tick 1457 到达 +91（税 −1），玩家被送到 market，**未扣任何货**；带货单 tick 1510：`cargo=coarse_cloth×4` 发单即扣 4 件进 `escrow:...:1510`，tick 1654 到达 +89，托管清空、货物被“送达”消耗 | 短镖 6% 风险本 run 未触发失败；失败退货有代码路径 `escort_cargo_return` 与测试，但无 live 样本；带货运费 89 < 货值 100，净亏 11；无货单是印钞口；不能“开镖局” |
| 8 | 招聘（雇 NPC） | 不存在 | **半成品** | tick 540 `recruit` 成功（捕快、日薪 21）；雇工每日出工产出 1 炊饼（`shop_output`），但工资进欠款、产出不卖 | `target_owns_shop`、`target_not_at_shop`、`unknown_position`（岗位不在 wages_daily） |
| 9 | 建门派/当掌门 | 不存在 | **不存在** | tick 1884 `found_sect` / `apprentice` → `not_implemented_for_player`；`join_sect` 仅入门 | `not_implemented_for_player` |
| 10 | 观察世界 | 可玩（依赖自走） | **半成品** | feed 有 `system_escort_arrive`、`system_shop_settle`、`quest_post`、NPC 移动；但 `nearby/present_npcs.doing` 仍恒为空，玩家看不到“他正在做什么” | doing 为空；委托板停发后 feed 只剩 NPC 日常 |

## 二、四个必答（第二轮）

### Q1：第一个正反馈在第几分钟？

**进入后 1–3 分钟。** 三条真实路径任选其一：

- tick 63 `work 8h`：+30 文、扣税 3，净 **+27**；
- tick 119 交付引导 2/4：**+12 文**；
- tick 137 交付引导 3/4：**+14 文**（先垫 28 买艾条）。

比第一轮更早、更实：第一轮只有任务文本奖励，这轮有买卖与工资事件。问题仍是“第 10 分钟后没有可持续正反馈”。

### Q2：钱能花在哪？

| 出口 | 实测价格 | 是否有回报 |
|---|---|---|
| 集市/药铺/镖局买货 | 炊饼 3、艾条 15、粗布 25、硝皮 35、护送服务 180 | 食物恢复体力；材料只能囤/交付 |
| `consume` 现买现吃 | 3 文/份，+25 体力 | 是（体力） |
| 押镖自带货 | 4 粗布=100 货值，短镖镖银 130→到手 89 | **负回报（−11）** |
| 开摊/铺/分号 | 300 / 1200 / 3000 + 初始进货 45 | 否（营收 0） |
| 雇工 | 21 文/日（实际转欠款） | 产出 1 炊饼/日，但不卖 |
| 交易/送礼 | `money_delta` 任意 | 只换关系 |

结论：**钱能花了，但花出去没有正收益出口**；装备类物品只有价格，没有装备动作与属性，因此“买装备”仍不存在。

### Q3：死胡同清单（按本 run 频率，附原样 code）

1. **38× `insufficient_stamina`**：连续修炼/移动后体力不足；只能等日结 +40 或花钱吃，没有“休息”动作。
2. **7× `already_online`**：重复 `enter`；无副作用，但 UI/脚本容易踩。
3. **6× `player_offline`**：静置 >60 秒后所有动作被拒；必须重新 `enter`。
4. **3× `quest_not_accepted_by_actor`**：接单后跨日界/实例轮换，原 id 失效；引导第 4 步实测踩到两次，重接新实例才成功。
5. **3× `not_at_workplace`**：开店后去别处 `work` 被拒；在自家店 `work` 记“店主自雇，工资 0 文”。
6. **2× `not_together`**：对话目标已随自走离开当前地点。
7. **1× `unknown_position`**：雇人岗位写“杂役”，不在 `wages_daily` 表。
8. **1× `target_owns_shop`**：想雇另一个有店的人。
9. **1× `target_not_at_shop`**：候选人不在店内。
10. **1× `quest_not_open`**：委托已被 NPC 接走/过期。
11. **1× `not_enough_money`**：钱不够买/吃。
12. **1× `not_at_giver`**：发布人随自走离开，板上的地点过期。
13. **1× `bad_arg:fee`**：`escort` 只给 `route_id` 不给 `fee`，服务端默认 0 后按线路下限拒绝（文档要求给 fee，但这是最容易漏的参数）。
14. **1× `bad_message`**：`talk` 必须带非空 `message`，只给 target 会拒。
15. **`not_implemented_for_player`**：`marry` / `found_sect` / `apprentice`，提示已明确。

**真正的“无下一步”**（不是参数错）：委托板停发后集市 0 单；开店后营收恒 0 且工资只记欠款；押镖不带货可无限白拿、带货净亏；雇工可见但无管理入口。

### Q4：玩家与 NPC 共用哪些动作？

- **完全共用**：`move`、`work`、`cultivate`、`talk`、`trade`、`quest_accept`、`quest_deliver`、`open_shop`、`join_sect`、`buy`、`sell`、`consume`、`escort`、`recruit`。
- **只有 NPC 能做**：`marry`、`apprentice`（拜师）、`found_sect`（建派/当掌门）、`debt_settle`；以及 NPC 规划器的内部采购/消费决策。
- **共用但语义分叉**：玩家 `escort` 可自带货，NPC 押镖由系统给货值；玩家 `open_shop` 有初始库存，NPC 店铺由经济系统维护；玩家 `work` 在自家店是 0 工资自雇。

## 三、玩家看不到的玩法（后端已开、UI 点不到）

1. **整个「商业与经营」面板是死标记**：`ui/player/index.html` 有 商店/店铺/雇人/押镖/江湖 五个 tab 与 `buyList`、`sellList`、`consumeBtn`、`employeeList`、`hireList`、`escortGo` 等元素，但 `ui/player/app.js` 对 `buyList` / `sellList` / `consumeBtn` / `escortGo` / `employee` / `data-tab` **零引用**；没有 tab 切换代码，列表永远空白，按钮无 handler。
2. **买/卖/吃/押镖/雇人/开店全部只在 API 可用**：`POST /api/player/action` 的 `buy`、`sell`、`consume`、`escort`、`recruit`、`open_shop` 均成功；UI 没有发这些请求的代码路径。
3. **店铺账本完全不可见**：`GET /api/player/state` 的 `shop.businesses` 只有 `{id, kind, name, cash}`；没有员工名单、日薪、欠薪、营收、日结、欠税。玩家看不到“我雇了谁、欠了多少钱、今天赚没赚”。
4. **雇工状态不可见**：`nearby.doing` 恒为空；玩家只知道邢破军在集市，不知道他“在自家摊出工”。对话是通用模板，不会提到雇佣关系。
5. **押镖托管不可见**：`escort` 返回的 `escort_orders` / `escrow` 有数据，但 UI 没有订单列表/托管桶/到达倒计时；`reconciliation.escrow_items` 也没绑定。
6. **没有店铺管理动作**：即使接上 UI，后端也没有进货、定价、辞退、提现、查看日结的玩家动作；只能开店、雇人，不能经营。

## 四、还缺什么才算活（按性价比前三）

### 1. 把「商业与经营」面板真正接上（0.5–1.0 人日）

- **改动面**：`ui/player/app.js`（tab 切换、`buyList/sellList` 渲染、`consumeBtn`、`escortGo`、`hireList`、`employeeList`、结果提示）；`ui/player/index.html` 现有标记直接复用；`ui/player/server.py` 的静态/只读分支确认不覆盖。
- **动作/事件**：前端调用 `buy` / `sell` / `consume` / `escort` / `recruit` / `open_shop`；复用现有 `transfer` / `item_move` / `escort_order_create` / `shop_hire` 事件，无需改后端。
- **验收**：浏览器打开后点 5 个 tab 能切换；买 1 炊饼产生 `buy` 事件且钱包 −3；雇人产生 `shop_hire` 并出现在 employeeList。
- **性价比**：最高。后端已全通，只差一层绑定；直接把“玩家看不到的玩法”变成可点。

### 2. 店铺日结闭环：工资真发、产出真卖（1.5–2.5 人日）

- **改动面**：`src/wsim/actions/business.py`（`SystemShopSettleAction`：工资从店铺现金/店主钱包扣，不足才记债；产出进入可售库存并结算营收）、`src/wsim/world/effects.py`（`shop_output` / `shop_day_close` 增加 `revenue`、`wage_paid`）、`src/wsim/api/session.py`（`shop` 投影补 `employees`、`wage_daily`、`wage_debt`、`revenue_today`）。
- **动作/事件**：复用 `system_shop_settle`；新增或扩展 `shop_day_close` 字段；可选新增 `shop_restock` 玩家动作。
- **验收**：雇 1 人、过 3 个世界日，日结出现 `wage_paid=21`（店铺现金足够时）或明确欠款；产出炊饼计入营收；玩家在 `state.shop` 能看到员工与工资。
- **性价比**：高。它把“开店→雇人”从负收益惩罚变成经营循环；改动集中在一个日结动作与一个投影。

### 3. 委托板保供：giver 有钱、每天有采购单（0.5–1.0 人日）

- **改动面**：`src/wsim/actions/quests.py`（`SystemQuestPostAction` 的 giver 现金预检改为“日结补足/区域金库垫付”，或对 procurement 模板设每日保底 1 单）；`content/economy.yaml`（`quests.min_completion_rate` / `expired_cap` 调整）；`src/wsim/actions/business.py` 或日结里给 giver 记一笔经营收入。
- **动作/事件**：`system_quest_post` 每日至少发出 1 张采购单；`quest_create` / `quest_deliver` 链路不变。
- **验收**：连续 3 个世界日在集市/药铺/河渡都能看到 ≥1 张采购委托；玩家完成 1 单得奖励、发布人现金减少但不归零。
- **性价比**：中高。任务是新玩家唯一的“有指引的收入”，断发比任何数值问题都致命；改动小于押镖重做。

**本轮不建议先做**：押镖经济重做（虽然“无货白拿/带货运费低于货值”是硬伤，但它需要引入“委托方货物”模型，涉及 `escort.py`、`session.py`、`content/escort_routes.yaml` 与失败退货测试，约 1.5–2 人日；排在前三之后）。

## 五、两轮差异（一句话版）

| 维度 | 第一轮 | 第二轮 |
|---|---|---|
| 买/卖/吃 | `unknown_action`，完全不可用 | 可用；买→吃→卖闭环，体力真恢复 +25 |
| 押镖 | 只能用任务假装，无货物/风险 | 真接单、真托管、真到达、真结算；但无货单印钞、带货运费 < 货值 |
| 雇人 | `unknown_action` | 可雇、到店、出工、可对话；工资只记欠款 |
| 开店 | 300 文空壳 | 300+45 有初始库存；日结营收 0、工资欠款、租税照扣 |
| 委托 | 采购单因买不到而断 | 买得到，5 个采购模板 NPC 侧 5/5 完成；但约第 7 日后委托板停发 |
| UI | 前端只读 feed + 基础操作 | 商业面板 HTML 已画好但 JS 未接线，仍点不到 |
| 结论 | 5 分钟可玩，10 分钟后硬墙 | 5 分钟闭环成立，30 分钟断在经营/任务供给 |

## 六、第二轮证据索引

- 事件库：`runs/audit-r2/events.sqlite`（玩家 `player:auditor2`）
  - 买卖吃：tick 57 / 69 / 75（`buy` −3、`consume` −3 +25 体力、`sell` +2）
  - 引导：tick 107/119/125/137/206/212/287/293/349/373/379
  - 开店：tick 426（`open_shop` −300 −45）；
  - 雇人/日结：tick 540（`shop_hire` 21/日）、1059/1159/1296/1488/1701/1741（`system_shop_settle`，营收 0、欠薪 +21/日）
  - 押镖：tick 1309→1457（无货 +91）、1510→1654（托管 4 粗布、+89、托管清空）
  - 未开放：tick 1884 `found_sect`/`marry`/`apprentice` = `not_implemented_for_player`
- 委托板：`system_quest_post` 有内容仅 tick 0/144/433/576/1059；1296/1488/1701/1741 均为 `[]`
- UI 缺口：`ui/player/index.html` 第 143–199 行有商业面板标记；`ui/player/app.js` 对 `buyList/sellList/consumeBtn/escortGo/employee/data-tab` 零引用
- 押镖失败退货：`src/wsim/actions/escort.py` 的 `_escrow_settle_effects`（`escort_cargo_return`）；`tests/test_player_commerce.py:376` 有断言；本 run 未出现失败样本
