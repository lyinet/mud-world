# player-sect 规范（玩家立派与收徒）

## 0. 目标与边界

本轮把「玩家门派」从只有 `join_sect`（入会）扩展到「立派 / 收徒 / 会费 / 金库 / 逐出」，
作为玩家侧最后一块核心玩法。

- **本轮只做设计规格 + P1 动作层**，不接线注册表、不改 API 白名单、不跑真实服务。
- **不做**：门派产业、门派间关系/冲突、玩家间门派战、官职任命动作（均列入 P2）。
- **不改**：`content/**`、`src/wsim/world/state.py`、`src/wsim/world/effects.py`、
  `src/wsim/actions/registry.py`、`src/wsim/actions/player.py`、`src/wsim/api/session.py`。
- **零模型调用**；动作层用纯函数 `preconditions` + `effects` 证明事件链与货币守恒。

### 0.1 现状（已核实）

| 项 | 位置 | 现状 |
|---|---|---|
| NPC 立派语义 | [business.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/business.py#L468-L556) `FoundSectAction` | 门槛 `min_money 2000 + registration_fee 500 = 2500`、`min_reputation 40`、`min_members 5`、`min_relation_value 30`；注册费 `burn`，启动金 `transfer` 到 `treasury:sect:<id>` |
| NPC 入会 | [social.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/social.py#L351-L406) `JoinSectAction` | 同地点、未入别的门派、会费真实 `transfer` |
| NPC 招揽 | [social.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/social.py#L409-L470) `RecruitAction` | 只建 `sworn` 关系，不建立弟子/门派职位；`min_members 5` 靠真实招揽凑 |
| 玩家白名单 | [player.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/player.py#L45-L72) `PLAYER_VERBS` | 只有 `join_sect`；`found_sect` / `apprentice` / `marry` 未开放 |
| 未开放码 | [session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L142) `PLAYER_UNIMPLEMENTED_VERBS` | 玩家调 `found_sect` 返回 `not_implemented_for_player` |
| 内容数据 | [sects.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/sects.yaml) | 3 个 NPC 门派模板；有 `dues / min_reputation / min_members / min_money / registration_fee / founding_cost`；**无**职位、弟子、门派等级、产业定义 |
| 经济参数 | [economy.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/economy.yaml#L129-L135) `sect` | `min_money 2000`、`founding_cost 2500`、`min_members 5`、`min_reputation 40`、`min_relation_value 30`、`registration_fee 500` |
| 玩家初始资金 | [player.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/player.py#L32) | `STARTING_MONEY = 300` |
| 玩家收入 | `economy.yaml` | 日薪 18–36 文（税前 10%）；委托奖励 8–36 文，最多同时 2 单 |
| 声望公式 | [common.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/common.py#L350-L366) `reputation_points` | `3×完成委托 + 0.5×好感 + 10×（有组织/产业）` |
| 状态模型 | [state.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L158-L178) `Faction` | 只有 `id/name/location/founder/treasury_account/members/leader`，**没有** doctrine/level/office 字段 |
| effect 原语 | [effects.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/effects.py#L44-L91) | 有 `transfer / create_faction / faction_join / add_disciple / set_field / debt_*`；**没有 `faction_leave`** |
| 世界动态 | [session.py](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L148-L162) | `WORLD_FEED_IMPORTANT_VERBS` 含 `found_sect`，但 feed SQL 明确排除 `actor LIKE 'player:%'`，玩家门派事件当前**进不了公共 feed** |

## 1. 立派（`found_sect`）

### 1.1 门槛：为什么不再沿用 NPC 的 2500 文 / 声望 40 / 5 名弟子

NPC 的数字是为「多个 NPC 发起人 + 长期自走」设计的，单人玩家直接照搬会有两处硬伤：

1. **5 名弟子在立派前凑不齐**。玩家没有 `apprentice`，`recruit` 对玩家只开放店铺雇佣语义；
   NPC 也不会主动向玩家投诚（`RecruitAction` 明确 `player_consent_required`）。
   要求先有 5 名成员，等于把「立派」锁死在未开放动词上。
2. **2500 文超出玩家可支配现金流一个量级**。玩家初始 300 文，日薪 18–36 文（税后 16–32），
   生活成本约 8–20 文/日，委托奖励 8–36 文且同时最多 2 单。一个活跃玩家每天净结余约 **30–40 文**，
   2500 文需要 60–80 个世界日，且中途不能买装备/进货。声望 40 也需要 10+ 单委托，
   两门槛叠加会把「立派」推到玩法末期。

### 1.2 玩家数值（P1 最终口径）

| 项 | 玩家值 | 依据 |
|---|---|---|
| `registration_fee` 注册费 | **200 文** | 官仓登记费，真实 `transfer` 到 `treasury:region`；约为 NPC 500 的 40% |
| `startup_capital` 启动金 | **600 文** | 进 `treasury:sect:<sid>`，供首月俸禄/备货；按 2 名弟子净支出 10 文/日，可覆盖约 60 个世界日 |
| `founding_cost` 合计 | **800 文** | 200 + 600；约为 NPC 2500 的 32%，活跃玩家约 22–27 个世界日可攒到 |
| `min_reputation` | **20** | 约 6–7 单完成委托（3/单）或等价关系好感；`reputation_points` 现口径 |
| `min_members` | **1**（仅掌门本人） | 单人可立派；弟子在立派后收，不要求预置关系 |
| 门派名号 | 2–12 字，全局唯一 | 与角色名规则一致，避免世界内重名 |
| 宗旨 `doctrine` | ≤ 60 字，可空 | 空时用默认「守望相济，不欺乡里」 |
| 驻地 `seat` | 立派时玩家所在地点 | 必须是真实地点；之后收徒/日结/存取款都在驻地 |

> 数值常量的唯一事实来源是 `src/wsim/actions/player_sect.py` 顶部常量。
> 本轮不改 `content/economy.yaml`；接线时若要把数值外提，新增
> `economy.sect.player` 子块，再由动作层读取，**不得**改动 NPC 的 `economy.sect` 旧键。

### 1.3 门派属性与存储限制

| 属性 | P1 落点 | 说明 |
|---|---|---|
| 名号 | `Faction.name` | 唯一 |
| 宗旨 | 创始人记忆 + `found_sect` 事件 `args.doctrine` | `Faction` 无字段；本轮禁改 `state.py`，读取侧由事件回放 |
| 驻地 | `Faction.location` | 立派时的 `at` |
| 等级 | **派生显示，不落库** | P1 固定 1 级（草创）；等级 = `f(members, treasury)`，只用于 UI 与上限校验 |
| 金库 | `Faction.treasury_account = treasury:sect:<sid>` | 真实账户 |
| 职位 | `Faction.leader`（掌门） | P1 只认掌门；长老/执事见 §3 |
| 成员 | `Faction.members` + `Entity.faction` | 权威名册以 `Entity.faction` 为准（见 §2.4） |

**等级派生规则（P1）**：`level = 1 if 活跃弟子 ≤ 3 else 2 if ≤ 6 else 3`。
P1 弟子上限固定 3，因此实际恒为 1 级；升级动作与费用见 §7（P2）。

### 1.4 立派动作契约

- 参数：`{name, at, doctrine?, sect_id?}`
- 前置（顺序即返回顺序，命中第一条即拒绝）：
  1. `unknown_actor` / `actor_not_alive`
  2. `already_in_sect`：`Entity.faction` 以 `sect:` 开头
  3. `wrong_location`：`at != Entity.location`
  4. `unknown_location`：`at` 不在 `state.locations`
  5. `invalid_sect_name`：名号去空白后不在 2–12 字
  6. `sect_name_taken`：任一 `Faction.name` 同名
  7. `sect_exists`：`sect:<sid>` 已存在
  8. `not_enough_money`：钱包 < 800
  9. `reputation_too_low`：`reputation_points < 20`
- 效果（全部真实账户操作，无 `mint` / 无 `burn`）：
  1. `create_account treasury:sect:<sid>`（0 文）
  2. `transfer 200` 钱包 → `treasury:region`，`reason=sect_registration_fee`
  3. `transfer 600` 钱包 → 门派金库，`reason=sect_endowment`
  4. `create_faction {id, name, location, founder, treasury_account}`
  5. `faction_join` 掌门
  6. `set_field last_action_tick`
  7. `memory_add` 创始人：名号、宗旨、注册费、启动金
  8. `goal_progress faction 1.0`（仅当存在显式 `_goal_id`，玩家无目标时为空）

> 与 NPC 的差异：NPC 注册费是 `burn`（销毁）；玩家改为 `transfer` 到官仓，
> 满足「每一笔都是真实转账」的验收口径，且 `burned_total` 不变。

## 2. 收徒（`recruit_disciple`）

### 2.1 NPC 投奔条件

| 条件 | 阈值 | 理由 |
|---|---|---|
| 好感 | **≥ 30** | 与 NPC `recruit` 建立 `sworn` 时的 30 一致；低于 30 视为不信任掌门 |
| 信任 | **≥ 30** | 拜师是长期关系；`talk` 初始信任 40，需至少若干次正向互动 |
| 目标技能 | **≥ 0.2** | 有可教/可用根基，避免收纯白板凑数 |
| 掌门技能 | **≥ 0.4** | 「能服众」；玩家主技能进入世界即为 0.35，需先 `cultivate` 一次 |
| 掌门声望 | **≥ 20** | 与立派门槛一致；立派后自然满足 |
| 同地点 | 目标与掌门同地点，且掌门在驻地 | 收徒是面对面行为 |
| 未入他派 | 目标 `faction` 非 `sect:` | 不挖角；同派内已在名册返回 `already_disciple` |
| 名册 | 活跃弟子 **< 3** | P1 1 级门派上限；见 `disciples_full` |

不满足好感/信任/技能时统一返回 **`target_declined`**（对玩家是「对方婉拒」），
不写转账、不写关系、不产生任何状态变化。

### 2.2 收徒动作契约

- 参数：`{sect_id, target}`
  - 会费/俸禄在 P1 固定为 5 / 10 文/日（模块常量），不接收覆盖参数；只写入记忆，不落库。
- 前置：`sect_not_found` → `not_sect_master` → `wrong_location` → `target_not_found` →
  `target_not_alive` → `self_recruit` → `target_is_player` → `not_together` →
  `target_in_sect` / `already_disciple` → `disciples_full` → `target_declined` → `skill_too_low`
- 效果：
  1. `faction_join {entity: target, faction: fid}`
  2. `add_disciple {master, disciple}`
  3. `set_field target.master = 掌门`
  4. `relation_upsert` 双向：掌门→徒 `kind=master`，徒→掌门 `kind=disciple`，好感 +5、信任 +5
  5. `set_field last_action_tick`（双方）
  6. `memory_add` 双方：收徒/投奔，写明日奉会费与日给俸禄
- **无拜师礼**：P1 不转钱，避免「花钱买弟子」；P2 可选一次性拜师礼（真实 `transfer`）。

### 2.3 弟子每日行为（`pay_disciples` 作为 P1 日结替身）

P1 没有日界系统动作（不允许改 `registry.py` / `session.py`），因此把「门派日结」
收敛为掌门可调用的 `pay_disciples`：

- 参数：`{sect_id, day?}`
- `day` 缺省 = `tick // TICKS_PER_DAY`；同一天重复调用返回 `already_settled_today`
  （以掌门记忆里的 `门派日结:<fid>:D<day>` 标记判定）。
- 对每名活跃弟子（按 id 升序、确定性）：
  1. **出勤/职责**：有在途委托 → `quest`；在驻地 → `cultivate`（`skill +0.02`）；
     否则 → `away`。
  2. **会费**：弟子钱包 → 金库 `transfer` `reason=sect_dues`；不足部分
     `payment_with_shortfall` 记 `debt:sect_dues:<fid>:<disciple>`，`note=unpaid_dues`。
  3. **俸禄**：金库 → 弟子 `transfer` `reason=sect_stipend`；现金不足时
     按 id 顺序先到先得（预算只按日结开始时的金库 + 当日实收会费计算，**不重复看余额**），
     未付部分记 `debt:sect_stipend:<fid>:<disciple>`，`note=unpaid_stipend`。
  4. 有现金时优先补旧欠薪（`debt_settle`），再发当日俸禄。
  5. 写弟子记忆：`在<门派>修炼（第N日）` / `外出任务（第N日）` / `不在驻地（第N日）`。
- 掌门记忆写一条日结摘要（同时作为幂等标记）。

### 2.4 弟子离开条件

| 条件 | 阈值 | P1 是否实现 |
|---|---|---|
| **长期欠饷** | 未付俸禄累计 ≥ `3 × stipend`（默认 30 文，约 3 个世界日） | **实现**：到达阈值的当天日结即逐出 |
| 门派声望下跌 | 门派声望 < 10 连续 5 个世界日 | 设计，P2（需要世界级派生指标与日界系统动作） |
| 掌门主动逐出 | 无 | 实现（`expel_disciple`） |
| 弟子死亡 | `status != alive` | 由既有生死系统处理，P1 日结跳过 |

**离派效果（P1，无 `faction_leave` 原语）**：
- `set_field disciple.faction = null`、`set_field disciple.master = null`
- `relation_upsert` 掌门↔徒的 `master` / `disciple` 关系 `status=ended`
- 双向记忆：`弟子X欠饷<金额>文，离派`
- **欠薪债务保留**（门派仍欠弟子），不因离派而消失
- **名册口径**：`Faction.members` 没有移除原语，会保留历史成员；
  权威「活跃弟子」= `fac.members` 中 `Entity.faction == fid` 且 `alive` 的实体。
  这样离派者不会继续占 `disciples_full` 名额，也不会被日结重复计费。
  接线阶段建议补 `faction_leave` 原语一次性清理 `members`（见 §10）。

### 2.5 逐出（`expel_disciple`）

- 参数：`{sect_id, target}`
- 前置：`sect_not_found` / `not_sect_master` / `wrong_location` (掌门须在驻地) /
  `target_not_found` / `self_recruit` / `target_not_disciple`
- 效果：同 §2.4 的实体与关系清理 + 双向记忆；不退款、不补偿、不 `mint`。

## 3. 职位与治理

| 职位 | P1 落点 | 可收徒 | 可动金库 | 可日结 | 说明 |
|---|---|---|---|---|---|
| 掌门 | `Faction.leader`（默认 `founder`） | ✅ | ✅ 全额（留 100 文保底、单次 ≤ 500） | ✅ | P1 唯一可操作职位 |
| 长老 | **P2** | ✅（上限内） | ✅ 仅「保底以上 30%」 | ❌ | 需要 `appoint_office` 动作与存储字段 |
| 执事 | **P2** | ❌ | ✅ 仅存入，不可提取 | ✅ | 负责收会费/记账 |

- P1 的 `not_sect_master` 判定：`Faction.leader == actor or Faction.founder == actor`。
- 任命/罢免动作 `appoint_office` **不在 P1**（动作清单只有六个）；长老/执事只做设计，
  实现需要 `world/state.py` 增加 `Faction.offices: dict[str, str]` 或独立 `sect_offices`。
- 权限原则：**钱与名册分离**——能收徒的人不一定能提款；提取永远需要掌门，
  防止「执事收会费后卷款」。

## 4. 钱流与守恒

### 4.1 账户

| 账户 | 用途 |
|---|---|
| `wallet:player:<account>` | 玩家/掌门私款 |
| `treasury:region` | 官仓（注册费） |
| `treasury:sect:<sid>` | 门派金库（会费、俸禄、存取款） |

### 4.2 每一笔都是真实 `transfer`

| 流水 | 方向 | `reason` | P1 |
|---|---|---|---|
| 注册费 200 | 掌门钱包 → 官仓 | `sect_registration_fee` | ✅ |
| 启动金 600 | 掌门钱包 → 门派金库 | `sect_endowment` | ✅ |
| 会费 5/日/徒 | 弟子钱包 → 门派金库 | `sect_dues` | ✅ |
| 俸禄 10/日/徒 | 门派金库 → 弟子钱包 | `sect_stipend` | ✅ |
| 掌门存入 | 掌门钱包 → 门派金库 | `sect_deposit` | ✅ |
| 掌门提取 | 门派金库 → 掌门钱包 | `sect_withdraw` | ✅ |
| 升级费（P2） | 门派金库 → 官仓 | `sect_upgrade_fee` | ❌ P2 |
| 门派产业进货/营收（P2） | 金库 ↔ 店铺公账/区域公账 | `sect_business_*` | ❌ P2 |

- **禁止 `mint`**；P1 也**不使用 `burn`**（注册费改走官仓）。
- 欠付不铸币：会费/俸禄不足一律转成 `Debt`（`unpaid_dues` / `unpaid_stipend`），
  债权人/债务人分别指向真实实体与门派。
- 提取上限：
  - 单次 ≤ **500 文**（`withdraw_over_cap`）
  - 提取后金库必须留 **100 文** 保底（`treasury_reserve`）
  - 余额不足（`insufficient_treasury`）

### 4.3 守恒校验

单测与接线后都必须满足：

```python
from wsim.audit.conservation import conservation
before = conservation(state)
# …执行动作…
after = conservation(state)
assert after["minted_total"] == before["minted_total"]
assert after["burned_total"] == before["burned_total"]
assert after["conservation_error"] == 0
```

另有结构性校验：
- 每条 `transfer` 的 `src` 与 `dst` 都在 `state.accounts` 中；
- 门派金库余额 = 初始启动金 + Σ会费 + Σ存入 − Σ俸禄 − Σ提取；
- 活跃债务 `Receivable = Payable`（`debt_ledger` 投影），且欠饷/欠会费 note 可区分。

## 5. 与既有系统接线（设计；本轮不实现）

| 系统 | 接线后的行为 | 落点 |
|---|---|---|
| `quests` | 日结时为每名弟子发一条「门派委托」（`giver=掌门`、`args.sect=<fid>`、`args.duty=sect_quest`）；奖励从门派金库 `transfer`，不发新钱。P2 增加 `content/quests.yaml` 门派专属模板与 `sect_reputation` 奖励 | `actions/quests.py` / `sim/intent.py` |
| `business` | P2：门派可开产业，`Business.owner=<fid>`、`cash_account=treasury:sect:<sid>`、雇工可为本门弟子；营收进金库，日结并入 `pay_disciples` | `actions/business.py` / `world/effects.py` |
| `intent.py` | NPC 弟子的日计划加入门派活动：在驻地 `cultivate`、按技能外出 `escort`/`work`、日界回驻地；掌门不在时不自动日结（避免玩家离线被扣钱） | `sim/intent.py` 的 `DAILY_PLAN`/目标分支 |
| `feed` | 门派事件进世界动态：`found_sect`、`recruit_disciple`、`expel_disciple`、`pay_disciples`（仅摘要，不含金额明细）。当前 feed SQL 排除 `player:%`，需为门派事件开一条公共投影（`actor=player:` 但 `verb` 在白名单时输出） | `api/session.py` `world_feed` / `_feed_summary` |
| `goals` | 立派推进 `faction` 目标 1.0；收徒可推进 0.1/人（P1 已保留 `goal_progress` 调用位） | `actions/common.py` `goal_progress` |
| `debt_audit` | 欠饷/欠会费进入既有债务审计；离派不销债 | `audit/debt_audit.py` |

## 6. 原因码全表（中文文案）

| 码 | 中文文案 | 触发动作 |
|---|---|---|
| `unknown_actor` | 操作者不存在。 | 全部 |
| `actor_not_alive` | 操作者已不在人世。 | 全部 |
| `not_enough_money` | 你的钱不够；立派需 800 文（注册费 200 + 启动金 600）。 | `found_sect` / `sect_deposit` |
| `reputation_too_low` | 声望不足 20，江湖上还没人认你的名号。 | `found_sect` / `recruit_disciple` |
| `already_in_sect` | 你已在一家门派中；先退出原门派才能另立门户。 | `found_sect` |
| `sect_not_found` | 门派不存在或不是你创建的门派。 | 收徒/逐出/日结/存取款 |
| `not_sect_master` | 只有掌门能做这件事。 | 收徒/逐出/日结/存取款 |
| `target_declined` | 对方婉拒了：好感或信任还不到位。 | `recruit_disciple` |
| `disciples_full` | 门派弟子已满（草创期最多 3 名）。 | `recruit_disciple` |
| `wrong_location` | 你必须身处门派驻地才能做这件事。 | 收徒/逐出/日结/存取款 |
| `unknown_location` | 驻地不存在。 | `found_sect` |
| `invalid_sect_name` | 门派名号需 2–12 字。 | `found_sect` |
| `sect_name_taken` | 这个名号已被别的门派用了。 | `found_sect` |
| `sect_exists` | 这个门派 id 已存在。 | `found_sect` |
| `target_not_found` | 找不到这个人物。 | 收徒/逐出 |
| `target_not_alive` | 对方已不在人世。 | 收徒 |
| `target_is_player` | 不能直接收玩家为徒；需玩家本人同意。 | `recruit_disciple` |
| `not_together` | 你与对方不在同一地点，收徒要当面。 | `recruit_disciple` |
| `self_recruit` | 不能收自己为徒。 | 收徒/逐出 |
| `target_in_sect` | 对方已属别的门派。 | `recruit_disciple` |
| `already_disciple` | 对方已在本门名下。 | `recruit_disciple` |
| `skill_too_low` | 对方根基太浅，或你的本事还不足以服众。 | `recruit_disciple` |
| `target_not_disciple` | 对方不是本门弟子。 | `expel_disciple` |
| `no_disciples` | 门中暂无弟子，无需日结。 | `pay_disciples` |
| `already_settled_today` | 今日门派已日结过了。 | `pay_disciples` |
| `insufficient_treasury` | 门派金库余额不足。 | `sect_withdraw` |
| `withdraw_over_cap` | 单次最多提取 500 文。 | `sect_withdraw` |
| `treasury_reserve` | 提取后金库须留 100 文保底。 | `sect_withdraw` |
| `bad_arg:sect_id` | 门派 id 格式不合法。 | 全部 |
| `bad_arg:amount` | 金额必须是 1–10000 的整数。 | 存取款 |
| `bad_arg:day` | 结算日必须是非负整数。 | `pay_disciples` |

接线时以上文案写入 `api/session.py` 的 `REASON_TEXT`；动作层只返回码。

## 7. 验收标准（真实服务链路；P1 用单测等价证明）

目标事件链（每步一条事件，`seq` 连号）：

1. `found_sect`
   - args：`name=五行道`、`at=academy`、`doctrine=…`、`sect_id=wuxing`
   - 期望效果：`transfer 200 wallet→treasury:region (sect_registration_fee)`、
     `transfer 600 wallet→treasury:sect:wuxing (sect_endowment)`、
     `create_faction`、`faction_join`、`memory_add`
2. `recruit_disciple` ×2
   - args：`sect_id=wuxing`、`target=n1|n2`
   - 期望效果：`faction_join`、`add_disciple`、`set_field master`、双向 `relation_upsert`、`memory_add`
3. `sect_withdraw`（把金库压到 0，用于验证欠饷）
   - 期望效果：`transfer treasury→wallet (sect_withdraw)`；受 500/100 上限约束
4. `pay_disciples` 第 0/1/2 日
   - 每日期望效果：弟子 `transfer→金库 (sect_dues)`；金库 `transfer→弟子 (sect_stipend)`
     （现金不足时部分支付）；未付写 `debt_create/debt_increase note=unpaid_stipend`；
     `skill +0.02`（在驻地者）；`memory_add` 日结摘要（含 `门派日结:<fid>:D<day>`）
5. 第 2 日日结（累计欠饷 ≥ 30 文）
   - 期望额外效果：`set_field disciple.faction=null`、`set_field disciple.master=null`、
     `relation_upsert status=ended`（master/disciple 双向）、双向 `memory_add`「欠饷离派」
   - 债务仍 active（门派欠弟子）
6. 整链守恒：`minted_total` / `burned_total` 不变、`conservation_error=0`、
   无负余额；`accounts_sum` 前后一致

P2 验收（只写目标，不实现）：
- 门派产业：开店、进货、营收、日结并入金库；
- 门派间关系：结盟/敌对/冲突（与 NPC 门派的 `sect:*` 互动）；
- 门派等级升级：1→2（500 文 + 6 弟子）、2→3（1500 文 + 9 弟子），费用真实 `transfer`。

## 8. P1 / P2 分期

| 期 | 范围 | 本轮状态 |
|---|---|---|
| **P1** | `found_sect`、`recruit_disciple`、`expel_disciple`、`pay_disciples`、`sect_deposit`、`sect_withdraw`；立派/收徒门槛、会费、俸禄、欠饷离派、金库上限、原因码 | **实现动作层 + 单测**，不接线 |
| **P2** | 门派产业（店铺/镖线/药铺）、门派等级升级、门派专属委托模板、门派声望、门派间结盟/冲突、长老/执事任命 | **仅设计目标**，不实现 |

## 9. 本轮不变量

- 动作层零 IO、零墙钟、零随机；同一 `state + intent` 输出逐字节一致的 effect 列表。
- 所有金额只经 `transfer` / `debt_*`；不出现 `mint` / `burn`。
- 拒绝只由 `preconditions` 返回码决定，`apply` 不二次校验余额（预算在 `apply` 内一次算清）。
- 不新增/修改 `Faction` 字段；doctrine/level/office 的持久化限制已在 §1.3 明说。
- 不触碰其他会话正在改的 `registry.py` / `player.py` / `session.py`。

## 10. 接线清单（由主线会话执行，本轮不改）

见 `tasks.md` 的「接线（不属于本笔提交）」一节，逐文件给出插入点。
