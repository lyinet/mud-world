# 玩家角色系统规格（P3 / P4）

> 版本：`player-system-v1`  
> 状态：实现规格（可直接照此写代码）  
> 适用：`world-sim` 现有 30 NPC、8 地点、3 门派内容包与 17 个 NPC 动词  
> 约束：本文档只定义玩家层，不改动既有 NPC 行为、内容包和指标基线。

这套设计让玩家以一个**有钱包、有体力、有职业身份的普通实体**进入 30 NPC 世界：能移动、做工、吃饭、买卖、交易、拜师、入派、开铺、押镖、立派、招人、接/交任务、还债、对话；所有写操作仍然走 `propose_intent` → MCP → `Writer` 单写者，玩家不能直接改状态。明确不做：**不提供杀害、抢掠、强迫婚配、逐出师门的正式玩家动作；不删除任何实体；不做离线收益；不做跨 run 转档；P3 一个 run 只允许一个玩家**。这份规格的目标是让玩家“能深度参与现有经济/社交循环”，而不是另起一套战斗或沙盒系统。

---

## 0. 现状基线（实现前必须对齐）

| 事实 | 证据 |
|---|---|
| 内容包有 30 个 NPC，玩家进入后是世界第 31 个实体 | `content/npcs.yaml`；`src/wsim/actions/social.py` 的 `TalkAction` |
| 现有玩家只是对话时懒创建的 `player:visitor`，没有钱包、技能、账号、生死流程 | `src/wsim/agent/dialogue.py#L12-L13`；`src/wsim/actions/social.py#L78-L91` |
| `with_players` 目前只是把玩家位置设为 `market` 并给附近 NPC 聚光，不创建玩家实体 | `src/wsim/sim/headless.py#L291-L305`、`#L371-L376` |
| K3 统计 17 个 NPC 动词，`talk` 是第 18 个玩家入口动词，不计入 K3 | `docs/metrics.md#L27`、`#L36-L39`；`content/verbs.yaml` |
| 现有动词没有杀害、攻击、抢掠、逐出、复活 | `src/wsim/actions/` 与 `content/verbs.yaml` |
| 关系值 `affection` 量纲 0-100；`trust` 字段旧默认是 `0.4`，新对话写入是 `40.0`，量纲不一致 | `src/wsim/world/state.py#L112-L117`；`src/wsim/actions/social.py#L124-L129` |
| 玩家写入只能通过 `propose_intent`，`admin_apply` 需要两个不同身份；玩家没有 admin 权限 | `src/wsim/mcp/gateway.py#L23-L29`、`#L182-L201` |
| `Writer` 目前不校验 `intent.tick` 单调性，客户端 tick 可被直接写入事件 | `src/wsim/world/writer.py#L54-L96` |
| `TalkAction` 接受客户端传入 `reply`、`affection_delta`、`trust_delta`；虽有 ±2/tick、±8/日 clamp，但回复内容可由客户端伪造 | `src/wsim/actions/social.py#L39-L49`、`#L56-L75` |

---

## 1. 玩家进入

### 1.1 两种进入模式

| 模式 | 允许场景 | 初始资产 | 说明 |
|---|---|---|---|
| **原生 run（P3 默认）** | 新开 run 时 `--with-players 1 --player-archetype ...` | 300 文 + 3 个炊饼 + 1 件粗布 | 在创世阶段一次性铸币/造物，之后永不再补 |
| **运行中新账号（P3-E 首发路径）** | 已有 run 中途首次 `player_enter` | 300 文 + 3 个炊饼 + 1 件粗布 | 由紧随 enter 的独立事件 `player_endowment` 一次性发放；同账号不重复 |
| **访客加入（仅兼容/测试）** | 显式关闭发放的测试场景 | 0 文、0 物品、100 体力 | 不作为产品路径；生产 run 不得出现 |

**硬规则**

1. 玩家初始资产有两条**显式铸币**路径，来源分别标记为 `genesis_player_endowment` / `genesis_player_carry` 与 `player_endowment` / `player_endowment_carry`：
   - 创世预置账号：在 genesis 事务中发放；
   - 运行中新账号：第一次 `player_enter` 后由独立事件 `player_endowment` 发放，`mint` 计入 `money_supply.minted`（`reason=first_entry`），并记录 `endowment_total`。
   `player_enter` 事件本身仍不铸币、不造物；任何发放都不得从 NPC 钱包扣款或偷偷修改创世总额。
2. 首次发放按 `account_id` 幂等：`PlayerProfile.endowed=true` 后，`already_online` 与离开后重进都不得再发。跨 run 重启是新世界，可重新领取。
3. 防刷上限：单区域最多 200 个玩家席位、初始资产累计发放上限 60000 文；超限返回 `player_cap_reached` / `endowment_cap_reached`。
4. 原生 run 的 `money_supply` 必须包含玩家初始资产；每新增一个首次进入的账号，`minted` 与 accounts 等额增加，K6 守恒误差必须仍为 0。
5. 玩家 ID 必须显式注册；不允许无档案的 0 资产“幽灵玩家”进入生产世界。

### 1.2 账号与角色创建

玩家账号在 API 层完成；角色进入世界是一个事件。

- 账号 ID：`[a-z0-9_-]{3,32}`，由服务端映射为实体 ID `player:<account_id>`。客户端**不得**自带实体 ID。
- 角色名：2-12 个字符，不能与现有 30 NPC 重名；只用于显示和记忆文本，不参与规则。
- 同一账号重复进入：幂等返回 `player_resume`，不得创建第二个实体。
- `player_enter` 分三种情况：
  - 创世时已预创建（原生 run）：首次登录只把 `online` 置为 true；资产已在 genesis 发放，不重复；
  - 运行中新账号（P3-E 首发路径）：创建 profile 后由独立事件 `player_endowment` 发放 300 文 + 配给，只发一次；
  - 已在线重复进入：返回 `already_online`，不发放；离开后重进只恢复状态。
- `player_enter` 事件自身永远不 mint、不造物；所有资产都由 genesis 或 `player_endowment` 显式产生。

### 1.3 出生点、初始资产、技能

| 项目 | 值 |
|---|---|
| 出生地点 | `market`（内容包存在时）；不存在则排序后的第一个地点 |
| 住所 | `inn`（用于离线托管回 home；P3 不自动扣房租） |
| 复活点（P4） | `temple` |
| 初始现金 | 300 文（= 摊位开张成本，但低于 `build_shop` 的 600 文周转目标） |
| 初始物品 | `flatbread × 3`、`coarse_cloth × 1`（约 34 文） |
| 体力 | `100 / 100` |
| 初始技能 | 对应职业技能 `0.35`（NPC 常见 0.4-0.55，拜师门槛 0.5） |
| 初始目标 | 无。玩家不进入 K1 分母，也不自动接任何目标 |
| 初始负债/门派/师父/配偶/店铺/押镖 | 无 |

### 1.4 身份选择（进入时必须选，且不可改）

| UI 身份 | 实体 `role` | 技能名 | 日薪 | 可押镖 | 可立派 |
|---|---|---|---|---|---|
| 游侠 | `镖师` | `镖师` | 30 | 是 | 按内容白名单：否 |
| 商人 | `掌柜` | `掌柜` | 36 | 否 | 按内容白名单：是 |
| 捕快 | `捕快` | `捕快` | 26 | 是 | 按内容白名单：否 |

实现要点：

- 身份是**职业角色的显示别名**，不新增 `role`，避免破坏 `wages_daily`、`living_costs`、`skill` 映射。
- 玩家层必须读取并强制执行 `content/verbs.yaml` 的 `allowed_roles`。当前 `EscortAction` 已经强制校验，而 `FoundSectAction` 没有校验；玩家层不能依赖动作层，必须在入队前检查 `allowed_roles`。因此：
  - 游侠/捕快不能 `found_sect`；
  - 商人不能 `escort`；
  - 三者都能 `open_shop`、`join_sect`、`talk`、`trade`、`work` 等。
- 若产品要求三种身份都能立派，**改 `content/verbs.yaml` 的 `allowed_roles`**，不要在玩家层偷偷绕过。
- 身份不可中途切换；现有 `set_field role` 不对玩家开放。

### 1.5 与 30 个 NPC 的初始关系

- **默认陌生**：创世不为玩家写任何 `acquaintance`、`friend`、`sworn` 关系，也不写 30 条 NPC 记忆。
- 关系查询在无记录时显示“陌生”；`affection_between` 返回 0，`trust` 显示“未知”，不把旧字段默认 `0.4` 当作 40。
- 第一次成功 `talk` 才创建 `acquaintance`，初始 `trust=40 ± d_trust`；第一次成功 `trade` 才创建 `friend`。
- 现有 NPC 与 NPC 的初始关系原样保留，玩家不继承、不复制。
- 不为玩家批量写关系的原因：避免 30 条无意义事件污染回放与关系统计，也避免把“陌生”误判成“熟人”。

---

## 2. 玩家能动什么

### 2.1 命令模型

玩家命令统一为：

```json
POST /api/player/action
{"verb": "move", "args": {"to": "inn"}, "confirm": false, "request_id": "..."}
```

服务端负责：
1. 从会话映射出 `actor=player:<account_id>`，**不接受客户端 actor**；
2. 忽略客户端 `tick`，一律使用当前 `state.tick`；
3. 校验 `verb` 在玩家白名单内、`args` 符合内容包 schema；
4. 执行玩家级前置条件（位置、目标在场、consent、限额、role 白名单）；
5. `world.preview_intent`（可选）→ `propose_intent` → 策略身份审批 → `Writer`。

`talk` 的 `reply`、`affection_delta`、`trust_delta` **必须由服务端生成**，客户端只能提交 `target` 和 `message`。现有 `TalkAction` 允许这三个字段来自 args，必须在玩家 API 入口剥掉或覆盖；否则玩家可以伪造 NPC 回复和关系变化。

### 2.2 17 个 NPC 动词的玩家行为要点

以下是玩家层需要额外补充或强调的规则；完整映射见文末表格。

- `move`：目标必须与当前地点相邻；使用现有 `MoveAction` 的耗时/体力规则。玩家没有 `player_move` 这个新动词，事件就是 `move`，`source=player`。
- `cultivate`：玩家可修炼当前职业技能；`hours 1..8`；体力不足由动作拒绝。
- `work`：玩家不预先绑定雇主；沿用 `WorkAction` 的 `employer_of`/`first_business_at` 规则，在当前地点的第一家有现金的企业做工；工资按职业日薪比例，10% 所得税；企业现金不足时按现有规则记欠薪，不得 mint。
- `consume`：从当前地点购买并吃掉食物；3% 交易税；恢复体力按内容包。
- `buy` / `sell`：只能对当前地点有库存/现金的企业；`sell` 按售价 60%，3% 交易税。玩家不能对远处地点买卖。
- `trade`：目标 NPC 必须同地点且非敌对（`affection >= -20`）。玩家对 NPC 的交易新增**公平性闸门**（见 3.4），防止用 1 个烧饼换走名剑。
- `apprentice`：目标必须是同地点 NPC，且满足现有 `max_skill >= 0.5` 且 `>= 玩家技能 + 0.1`；玩家只能有一个师父。`duration_days` 只记录，当前引擎不执行出师/到期，**不做假实现**。
- `join_sect`：目标必须是门派或地点组织；玩家必须在场、未加入其他门派；会费从钱包转到门派公账；同一时间只能有一个 `faction`。
- `marry`：双方未婚、同地点、`affection >= 60`；玩家向 NPC 求婚时新增 NPC consent（信任 >= 50）；NPC 向玩家求婚在 P3 关闭（默认不允许 NPC 对玩家发起婚配），P4 才通过玩家 consent 开启。
- `open_shop`：玩家同地点、无现有店铺；`stall/storefront/branch` 成本 300/1200/3000；金库低于“保底线 + 开店成本 + 该店 3 日工资”时延后；玩家只能有一家店。
- `escort`：玩家必须在路线起点，职业在 `镖师/脚夫/捕快/船夫`，镖局现金足够，未在押镖；费用上限按路线配置；押镖期间不能接第二单。
- `found_sect`：同地点、2500 文（500 注册费销毁 + 2000 进公账）、声望 >= 40、5 名发起成员（含玩家自己）、未加入其他 `sect:`；成员必须由真实 `recruit`/`apprentice` 建立且关系 >= 30。玩家层还要校验 `allowed_roles`（当前内容白名单里的玩家身份只有“商人”）。
- `recruit`：同地点、目标不在其他 `sect:`、未与玩家结为 `sworn`；成功后双向 `sworn 30/45`，若玩家已有门派则目标入派。**“结拜”在 P3 用 `recruit` 表达，没有独立动词**；如果 UI 要写“结拜”，必须标明底层是招揽/结盟，不能假装有独立机制。
- `quest_accept`：玩家必须在发布人地点，未在押镖；新增玩家并发上限：最多 2 个进行中任务、1 个进行中押镖。超过时拒绝 `too_many_quests`。
- `quest_deliver`：必须由接单玩家本人交付，未过截止时间，采购类任务需持有物品；奖励从发布人钱包转账，关系奖励按现有规则写入。
- `debt_settle`：玩家只能清偿**自己作为债务人的 active 债务**；金额不超过债务余额和钱包余额；现有 `debt_settle` 插件只接受 `debtor=actor`，玩家不能替 NPC 还债，也不能向 NPC 强行收债。
- `talk`：玩家专用入口（NPC 对 NPC 未接线）。新增同地点校验 `not_together`；消息 <= 200 字；服务端生成 `reply`；不产生金钱变动；关系变化被 ±2/tick、±8/日 clamp；记录 NPC 记忆。

### 2.3 仅玩家可用 / NPC 也可用

- **仅玩家可用**：`talk`，以及新增生命周期动词 `player_enter`、`player_leave`、`player_resume`、`player_heartbeat`、`consent_request`、`consent_resolve`。`player_enter/leave/resume/heartbeat` 不是 NPC 动作。
- **NPC 也可用**：上述 17 个 NPC 动词。NPC 规划器继续按原逻辑使用；玩家只是把同一个 `Action` 作为另一个 actor 调用。
- **需要目标在场**：`trade`、`apprentice`、`marry`、`recruit`、`talk` 要求目标同地点；`join_sect` 要求组织地点同地点；`work`/`buy`/`sell`/`consume` 要求对应企业在当前地点；`quest_accept` 要求发布人地点；`quest_deliver` 要求在交付地点（或发布人地点）；`open_shop`/`found_sect` 要求当前位置就是目标地点。

---

## 3. 不可逆与冲突

### 3.1 动作风险分级

| 等级 | 动作 | 确认方式 | P3 状态 |
|---|---|---|---|
| L1 普通 | `move/cultivate/work/consume/buy/talk` | 无额外确认，服务端前置条件 + 策略身份 | 开放 |
| L2 承诺 | `sell/trade/apprentice/join_sect/marry/open_shop/escort/found_sect/recruit/quest_accept/quest_deliver/debt_settle` | 玩家确认（preview 展示效果）；NPC 目标另加 consent；策略身份自动审批 | 开放 |
| L3 破坏性 | 杀害、抢掠、逐出师门、PvP 伤害 | 双方 consent + 人工复核身份 `ops:reviewer` + 每日上限 | **P3 不开放；P4 仅逐出/非致死决斗，玩家死亡/复活默认关闭** |

“确认”只是 UI/审计要求，不是权限边界；真正的边界是服务端前置条件、consent、限额和 Effect 白名单。

### 3.2 NPC 同意（P3 必须实现）

NPC 同意是**确定性规则**，不产生 pending 状态，也不依赖模型自由发挥。判定失败直接返回 rejection，事件日志保留原因。

| 动作 | NPC 同意条件 |
|---|---|
| `apprentice` | `affection >= 20` 且 `trust >= 35`；同时满足师父技能门槛 |
| `recruit` | `affection >= 20` 且 `trust >= 35`；目标不在其他门派、未 sworn |
| `marry` | 现有 `affection >= 60` 且 `trust >= 50`；双方未婚、同地点 |
| `trade` | 非敌对（`affection >= -20`）且通过公平性闸门（见 3.4） |

- `trust` 统一按 **0-100** 解释；旧默认 `0.4` 视为“无信任数据/未知”，不得当成 40。玩家与 NPC 的新关系必须显式写入 `trust=40`。
- NPC 拒绝时返回 `npc_declined:<reason>`，UI 必须显示可读原因（如“对方还不够信任你”“对方已有门派”），不能只显示错误码。
- NPC 主动向玩家求婚、求婚、招揽在 P3 **一律关闭**；P4 开启时必须走玩家 `consent_request/resolve`。

### 3.3 玩家同意（P4，多玩家与 NPC 提案）

- 目标是玩家时，任何 L2/L3 动作先创建 `consent_request`，目标玩家调用 `consent_resolve`。
- consent 记录字段：`id`、`action`、`from`、`to`、`status`（pending/granted/denied/expired）、`args_digest`、`created_tick`、`expires_tick`、`decided_tick`、`reason`。
- 默认 1 个游戏日（144 tick）过期，过期自动 `expired`；`args_digest` 不一致的 consent 不能复用。
- 目标离线时：L3 一律拒绝；L2 可以在目标下次上线后处理，不能由系统代替接受。
- P3 单玩家，不产生玩家对玩家的 consent；但 NPC→玩家婚配关闭，避免离线被婚配。

### 3.4 交易公平性与冲突裁决

玩家对 NPC 的 `trade` 增加估值闸门：

```text
收到价值 = value(offer) + max(0, -money_delta)
给出价值 = value(want) + max(0, money_delta)

要求：收到价值 >= ratio * 给出价值
ratio = 0.70（affection < 60）
ratio = 0.50（affection >= 60）
```

- `value` 使用 `content.items` 的 `price`（买入价），服务类物品不允许进入玩家交易（`service_item`）。
- 这条规则只作用于 `actor=player` 且 `target=NPC`，不改变 NPC 对 NPC 的既有交易行为。
- 交易双方钱包/库存不足、目标敌对、物品不存在，仍由现有前置条件拒绝。
- “抢掠”不通过交易实现：没有 `rob` 动词；公平闸门阻止玩家用明显不对等交易掏空 NPC。

冲突裁决顺序：

1. `Writer` 单线程按 `seq` 串行执行；同一 tick 两个动作先到先得。
2. 第一个事务成功后，第二个事务在**新状态**上重新校验；失败返回 rejection，不做回滚。
3. 同一 NPC 同时被两个玩家求婚/招揽：第一个成功，第二个 `already_married`/`already_sworn` 拒绝。
4. P3 玩家与 NPC 之间没有伤害结算；P4 决斗只在双方 consent 后进入，失败方进入 `downed`，不删除实体。

### 3.5 明确禁止与世界规则

| 行为 | 裁定 |
|---|---|
| **杀害 NPC/玩家** | 17 动词中不存在。P3/P4-A 都不开放；P4-C 可选“玩家死亡/复活”，默认关闭；NPC 始终只做“决斗→倒地（downed）”，不永久删除。若未来开放 NPC 永久死亡，必须双方同意、在线、每日 1 次、非关键 NPC、无实体删除、遗产只做转移不铸造。 |
| **抢掠** | 永不作为玩家动词。物品只能通过 `trade`/`buy`/`sell`/`transfer` 转移；禁止 `rob` UI。 |
| **强迫婚配** | 永不实现。`marry` 必须双方 consent；NPC 拒绝即失败；离线目标不能结婚。 |
| **逐出师门** | P3 不实现。P4 只允许门派掌门/师父发起 `player_expel`，必须有理由、目标在线或延迟 1 个游戏日生效、不得没收财产；玩家不能逐出 NPC 门派成员，除非玩家是该门派掌门。 |
| 直接改状态 | 玩家不能提交任意 Effect，不能 `mint`/`item_create`/`debt_create`/`set_field` 的 `role` 或 `status`/`remove_entity`。 |
| 跳 tick | 玩家 API 不接受客户端 tick；服务端拒绝 `intent.tick != current_tick`。 |
| 离线目标 | 不能对离线玩家/NPC 发起 L3；不能对离线玩家发起婚配/招揽。 |
| 批量改写 | 一次 intent 只能影响一个目标；禁止一次写 30 个关系。 |

---

## 4. 社交与组织

### 4.1 关系值与态度

现有关系类型：`acquaintance`、`friend`、`family`、`master`、`disciple`、`marriage`、`sworn`、`ally`。玩家使用同一套。

玩家视角态度（`affection` 为准）：

| 区间 | 显示 | 交易/招揽含义 |
|---|---|---|
| `< -20` | 敌对 | `trade/recruit/marry/apprentice` 拒绝；`talk` 可尝试但收益为负 |
| `-20 .. 0` | 戒备 | 可对话，交易需公平闸门 |
| `0 .. 20` | 陌生/中立 | 可正常交易 |
| `20 .. 60` | 友善 | 可拜师/招揽（还需 trust >= 35） |
| `>= 60` | 亲近 | 可求婚（还需 trust >= 50） |
| `>= 80` | 至亲 | 现有上限内，关系效果不再额外加成 |

`trust` 区间：`<20` 不信任；`20-50` 普通；`50-70` 信任；`>=70` 深信。无记录时显示“未知”，不参与判定（视为不满足 consent）。

### 4.2 增减规则与上限

| 来源 | affection | trust | 频率上限 |
|---|---|---|---|
| 首次/持续 `talk` | 每次 ±2，日累计 ±8 | 新关系初始 40；每次 ±2，日累计 ±8 | 每 tick 最多 ±2；每 NPC 每日 ±8 |
| 成功 `trade` | 双方 `+3` | 双方 `+2` | 每 NPC 每日最多计入 2 次交易（+6/+4） |
| `quest_deliver` 关系奖励 | 新关系 `min(100, reward_relation*5)`；已有关系不重复加 | 新关系 45 | 同一任务一次；任务奖励不计入日上限 |
| `apprentice` | 师父/徒弟 `+5` | `+5` | 一次性 |
| `join_sect` | 与掌门 `ally +3` | 无 | 一次性 |
| `recruit` | 双向 `sworn 30`（不足则补到 30） | 双向 45 | 一次性/目标 |
| `marry` | `marriage 80` | 70 | 一次性 |
| 负面（仅 `talk` 冲突、P4 背叛/逐出） | 每次 -2，日累计 -20 | 每次 -2，日累计 -20 | 每 NPC 每日 -20 |

实现要求：

1. 现有 `TalkAction` 的 `talk_ledger` 键是 `target.id`，单玩家够用；P4 多玩家必须改为 `actor + target` 复合键，否则多个玩家的对话额度会互相污染。P3 若直接扩展多玩家，必须先改键。
2. 新增 `relation_ledger`（键为 `a + b` 复合键）统一记录 repeatable 来源（trade/quest）的日累计；`talk_ledger` 保留。一次性社交动作（apprentice/join/recruit/marry）不走日上限，但只能发生一次。
3. 所有写入必须 clamp 到 `[-100, 100]`；`trust` 必须 clamp 到 `[-100, 100]`，且新玩家关系显式初始 40。
4. 不做每日自然衰减、不做随机态度漂移；NPC 态度只能由事件改变。记忆和提示词可以影响 NPC 说话方式，但不能反向改关系值。

### 4.3 拜师、结拜、婚配

- **拜师**：玩家是徒弟，NPC 是师父；只能有一个师父；师父技能 >= 0.5 且比玩家高 0.1；不能形成师徒环。玩家成为师父后（技能 >= 0.5）可以收 NPC 徒弟，走同一 `apprentice`，目标 NPC 的 consent 规则相同。`duration_days` 当前只记录不执行，规格不新增出师逻辑。
- **结拜/招揽**：P3 没有独立“结拜”动词；`recruit` 建立 `sworn` 关系，功能上等价于结拜/入伙。UI 可显示“招揽/结为同伙”，不能宣称有独立的结拜系统。P4 若要做独立结拜，新增 `swear_brotherhood` 动词，且必须同时改 K3 口径。
- **婚配**：玩家向 NPC 求婚需 `affection >= 60` 且 `trust >= 50`，双方未婚；成功后 `marry` effect 写双方 spouse 与 `marriage` 关系。P3 不提供离婚；NPC 不能主动向玩家求婚。玩家与玩家婚配 P4 才开放，双方 consent。
- **加入门派**：玩家在门派地点、未加入其他 `sect:`、会费足够；会费进入门派公账，玩家与掌门写 `ally +3`。玩家已在地点行会（`org:`）时加入 `sect:` 会因 `already_in_faction` 被拒绝，这是现有规则，不修改。
- **创建门派**：
  - 2500 文：500 注册费销毁，2000 进公账；
  - 声望 `>= 40`；
  - 5 名发起成员，含玩家自己；成员必须是 `disciple` 或 `sworn` 且关系值 `>= 30`；
  - 玩家未加入其他 `sect:`；
  - 玩家层校验 `allowed_roles`（当前身份中只有“商人/掌柜”能立派）。
  - 声望计算沿用 `FoundSectAction._reputation`：只统计 `a=玩家` 且 kind 为 `friend/family/master/disciple/ally/marriage` 的**正 affection**；`sworn` 和 `acquaintance` 不计。也就是说：
    - 玩家 + 4 名 `sworn` NPC = 5 名发起成员；
    - 但 `sworn` 不贡献声望，声望 40 必须靠 `trade` 形成的 `friend`、`apprentice` 形成的 `disciple` 或 `marry` 形成的 `marriage` 累积；
    - 路径示例：与 14 个不同 NPC 各完成 1 次公平交易（每个 friend +3）或与同一 NPC 分多日交易（受每 NPC 每日 2 次上限），同时招募 4 名 sworn。
- **招人**：`recruit` 只建立 `sworn`，不是“NPC 变成玩家下属”；NPC 仍保留自己的目标、职业、钱包和行动。玩家不能指挥 NPC 做事；P4 也不做 NPC 跟随/自动劳动。
- **逐出师门**：P3 无。P4 `player_expel` 只能由掌门/师父发起，目标在线或延迟 1 日；解除 faction/师徒/婚姻关系中的对应一项，不改钱包、不删除实体；必须有理由与审计。

玩家在关系中的位置：一个普通实体，可同时是徒弟、师父、配偶、sworn、门派成员/掌门；不能同时属于两个 `sect:`；不能把 NPC 变成“宠物/下属”。

---

## 5. 死亡与损失

### 5.1 P3：不死亡，只耗尽体力

- 现有引擎没有伤害/死亡动作；`status` 虽有 `dead` 字段，但没有任何玩家动作能写入。P3 保持这一点。
- 体力来源：移动、修炼、做工、押镖；体力 `<= 0` 时动作返回 `insufficient_stamina`，不自动扣钱、不掉物品。
- 每日 `system_recover` 会为所有 alive 实体恢复体力（玩家实体也在内）；玩家不参加 `system_living` 的自动伙食/房租扣款，必须手动 `consume` 或 `buy`。
- 玩家可能损失的只有：交易/消费花掉的钱、体力、时间、押镖失败拿不到镖银（镖货损失属于镖局/客户，不是玩家库存）；任务超时目前不罚款。
- 店铺月税、储蓄税仍会作用于玩家钱包；如果余额不足，按现有规则拒绝或记欠账，不得 mint。

### 5.2 P4：死亡与复活（默认关闭，需世界规则开关）

| 项目 | 规则 |
|---|---|
| 触发 | 仅同意决斗/明确危险事件；玩家不能被离线杀死；NPC 不能对玩家发起致死动作 |
| 死亡状态 | `status="dead"`，实体保留，不删除；不能行动、不能被 `talk`、不参与 LOD |
| 复活 | 1 个游戏日（144 tick）后在 `temple` 复活；体力 50 |
| 代价 | 钱包 20%（最低 50、最高 500）转到 `treasury:org:temple`，不是 burn；保留物品与技能；`death_count +1` |
| 频率 | 每玩家每日最多死亡 1 次；决斗双方必须在线 |
| NPC 死亡 | P4-A 不做永久死亡，只做 `downed` 3 天后在 temple 恢复；恢复费 100 从 NPC 钱包支付，不足记债务 |
| 未来永久死亡 | 禁止 `remove_entity`；遗产/债务/任务/记忆做转移；关键 NPC 白名单保护；每日全局上限 3 |

### 5.3 防“一行代码毁掉 72 天世界”的兜底

必须同时具备以下九项，缺一不可：

1. **单写者**：所有玩家写操作最终由 `Writer.submit` 在一个事务里执行；API 不直接改 `state`。
2. **动词白名单**：玩家只能提交 17 动词 + `talk` + 生命周期/consent 动词；不能提交任意 Effect。
3. **tick 护栏**：玩家入口覆盖客户端 tick；`intent.tick != state.tick` 直接拒绝 `bad_tick`；`Writer` 对玩家来源增加单调性校验。
4. **preview**：L2 动作先 `world.preview_intent`，返回将产生的 Effect 列表和拒绝原因；preview 在 clone 上执行，不写 outbox、不改 state、不改 hash。
5. **consent 与上限**：NPC consent 确定性校验；L2 每日合计最多 3 次，L3 每日 1 次；L3 需要 `ops:reviewer` 第二身份。
6. **不删除/不增发/不负余额**：玩家无 `remove_entity`、`mint`、`item_create`、`debt_create`；余额不足一律拒绝或按现有规则记欠账。
7. **完整性护栏**：如果动作会使某地点失去唯一服务企业、使金库低于保底线、或让玩家持有第二家店/第二个门派，直接拒绝。
8. **频率限制**：每玩家每 tick 最多 1 个写意图；每分钟最多 30 个；每日最多 300 个；超过返回 `rate_limited`。
9. **可关闭与可回放**：`config/player.yaml` 的 `enabled: false` 或 CLI `--player-readonly` 能整体关闭玩家写入；所有玩家事件进入 `events.sqlite`，能 replay MATCH；无玩家 run 的 K1/K3/K6 基线不受影响。

---

## 6. 玩家不在线时

### 6.1 角色是否留在世界

- 留在世界：实体、位置、钱包、物品、关系、店铺、债务全部保留，不删除、不冻结经济身份。
- 不会被 NPC 主动影响：NPC 规划器的目标过滤必须排除所有 `player:` 实体；NPC 不能对玩家发起 `trade/apprentice/marry/recruit/escort/quest`，也不能对玩家造成伤害。
- 可以被看见：在 UI/快照中显示“离线”，但 NPC 不与其互动。玩家重新上线后可继续原位置。
- 经济仍会继续：店铺月税、储蓄税、已有债务按系统规则结算；不会获得工资、经验、关系增长。

### 6.2 离线时间线与托管

以**虚拟 tick** 计算，不写墙钟时间，保证回放一致。1 天 = 144 tick。

| 阶段 | 触发 | 状态 | 行为 |
|---|---|---|---|
| 在线 | 有 `player_enter/resume/heartbeat` 或最近有玩家意图 | `online=true` | 正常游戏；NPC 附近聚光 |
| 离线 | 连续 36 tick（6 游戏小时）无玩家意图；交战中 18 tick（3 游戏小时） | `online=false`，`offline_since_tick` | 实体保留、位置保留；不聚光；不进 `look` 在场人物；不可被 `fight`/`kill`/`loot`；不被 `npc_aggression` 选为目标；不登记 `combat_enemies`；不占房间人数 |
| 战斗中断线 | 交战中 18 tick 无意图 | 立即脱战 | 清玩家与 NPC 双方 `combat_target`/`combat_enemies`；无宽限，避免“回来就死了” |
| 托管 | 连续 432 tick（3 游戏日） | `trustee=true`，写 `player_trusteeship` | 每日边界：回 `home`；体力 < 30 且现金 >= 15 时买最便宜食物一次；不工作、不社交、不交易、不接任务、不开店 |
| 休眠 | 连续 1008 tick（7 游戏日） | `dormant=true` | 停止托管行为；实体保留、位置保留；不再聚光；上线即恢复在线 |
| 恢复 | `player_resume` / 同账号重连 | online=true, trustee/dormant=false | 接回同一 entity、原位置、原行囊；不补发离线收益；不重放 genesis |

实现要求：

- 离线/托管判定放在每个世界 tick 的 `system_player_presence` 动作中，按 `last_intent_tick` 计算；不依赖 `time.time()`，同 seed 可复现。非战斗 36 tick、交战 18 tick。
- 活跃来源：玩家真实请求（动作、状态、心跳、look 等）刷新 `last_intent_tick`；只读请求按 12 tick 冷却合并成 `player_heartbeat`，避免刷屏。
- 托管动作 `source=system`，不进入 K3；预算上限：每日最多 1 次食物，金额不超过 15 文；现金不足则什么都不做。
- 托管不会代替玩家接受任何 consent、不会结婚、不会立派、不会开店。
- 玩家上线不追溯补做托管错过的动作；只从当前 tick 继续。
- `with_players` 的旧行为（给 `market` 聚光）要改为读取玩家实体位置，并且只对**在线**玩家聚光；离线玩家不再触发 spotlight。

---

## 7. 与现有系统的接口

### 7.1 新增状态字段

`WorldState` 新增三个字段，且必须进入 `clone()`、`to_dict()`、`full_dict` 与状态哈希：

```python
players: dict[str, PlayerProfile]
consents: dict[str, Consent]
relation_ledger: dict[str, dict[str, Any]]
```

`PlayerProfile`：

```python
id: str                 # player:<account_id>
account_id: str
display_name: str
archetype: str          # 游侠/商人/捕快
role: str               # 镖师/掌柜/捕快
online: bool
created_tick: int
last_intent_tick: int
offline_since_tick: int
trustee_since_tick: int
dormant_since_tick: int
death_count: int
```

`Consent`：见 3.3 字段。`relation_ledger` 使用 `a + b` 复合键，值至少包含 `day`、`day_affection`、`day_trust`、`last_tick`、`sources`。

要求：

- `players` 的 key 排序后进入 `to_dict`，保证 hash 稳定。
- 玩家实体仍用现有 `Entity`（`role/location/home/stamina/inventory/skills/faction/spouse/master/business/escort_id`），不新增 `online/account` 到 Entity，避免影响 NPC 序列化。
- 玩家钱包 `wallet:player:<account_id>` 必须在创世或访客进入时创建；`talk` 懒创建路径不能再创建无钱包玩家。

### 7.2 新增 Effect

| Effect | 参数 | 用途 |
|---|---|---|
| `player_upsert` | `player`, `fields` | 创建/更新 PlayerProfile；不得携带金额 |
| `player_presence` | `player`, `online`, `trustee`, `dormant`, `tick`, `reason` | 离线/托管/恢复状态 |
| `consent_upsert` | `id`, `action`, `from`, `to`, `status`, `args_digest`, `created_tick`, `expires_tick`, `reason` | 玩家 consent 记录 |
| `relation_ledger` | `a`, `b`, `day`, `day_affection`, `day_trust`, `last_tick`, `source` | trade/quest 等重复来源的日上限 |
| `death_mark`（P4） | `entity`, `tick`, `cause` | 只设 `status=dead`，不删除 |
| `respawn`（P4） | `entity`, `location`, `tick` | 复活并设位置/体力 |

所有新 Effect 必须加入 Effect 白名单与 `apply_effects` 分支；未知 Effect 仍触发整单拒绝。插件只能使用白名单内 Effect。

### 7.3 新增事件/动词

| 动词 | actor | 权限 | 说明 |
|---|---|---|---|
| `player_enter` | `player:<id>` | 玩家 | 首次登录；已预创建只置 online，未预创建触发 `player_endowment` 一次性发放 |
| `player_endowment` | `player:<id>` | 系统（首次进入时自动） | 一次性发放 300 文 + 配给；显式 mint，按 account_id 幂等，不计 K3 |
| `player_leave` | `player:<id>` | 玩家 | 主动下线；置 offline 并记录 `offline_since_tick` |
| `player_resume` | `player:<id>` | 玩家 | 恢复在线，清 trustee/dormant |
| `player_heartbeat` | `player:<id>` | 玩家 | 在线心跳，只更新 `last_intent_tick`；无资源变动，不计 K3 |
| `player_presence` | `system` | 系统 | 每日边界推进 offline/trustee/dormant 状态机 |
| `consent_request` | `player`/`system` | 玩家/系统 | 创建玩家-target consent；P3 仅保留接口，默认不产生 |
| `consent_resolve` | 目标玩家 | 玩家 | 接受/拒绝/过期 |
| `player_respawn` | `system` | 系统 | P4 复活 |
| `player_expel` | 掌门/师父玩家 | 玩家 | P4 逐出，默认关闭 |
| `player_duel` | 玩家 | 玩家 | P4 非致死决斗，默认关闭 |

**不新增** `player_move`、`player_talk` 这两个动词。API 可以叫 `/api/player/move`、`/api/player/talk`，但**事件 verb 必须是 `move` / `talk`，`source=player`**。理由：避免 17 动词集合和 K3 口径被复制成 19 个，且 replay/审计只需要一个事实来源。

**必须同时修的现有实现细节**：`TalkAction.apply` 目前把 `PLAYER_ID="player:visitor"`、`PLAYER_NAME="访客"` 写死用于创建实体、写关系和写账本（`src/wsim/actions/social.py#L78-L146`）。P3 必须改为：

1. 关系/账本/记忆使用 `intent.actor` 和对应 `PlayerProfile.display_name`，不能继续写死 `player:visitor`；否则新账号 `player:u_1001` 的对话会落到旧访客身上。
2. 删除 `TalkAction` 里的实体懒创建；玩家实体只能由 `player_enter` 或创世创建，`talk` 不得创建无钱包玩家。
3. `player:visitor` 仅作为“没有 PlayerProfile 的旧 run”兼容回退，并且只在该 run 没有配置玩家时使用。
4. `talk_ledger` 单玩家可以继续用 `target.id` 作为键；P4 多玩家必须改成 `"actor|target"`，否则两个玩家的日上限共享。

### 7.4 API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/player/enter` | 未登录账号 | 创建/恢复角色；body `account_id/display_name/archetype` |
| POST | `/api/player/leave` | 玩家 | 主动下线 |
| POST | `/api/player/resume` | 玩家 | 恢复在线 |
| POST | `/api/player/heartbeat` | 玩家 | 在线心跳；每 12 tick 最多一次，只更新 `last_intent_tick` |
| GET | `/api/player/state` | 玩家 | profile、位置、体力、钱包、库存、关系摘要、active consents、可行动作 |
| GET | `/api/player/actions` | 玩家 | 返回 17+talk 的当前可用性、拒绝原因、目标列表 |
| POST | `/api/player/preview` | 玩家 | 在 clone 上预演，返回 Effect 列表/拒绝原因；不写 outbox |
| POST | `/api/player/action` | 玩家 | 统一写入口；body `verb/args/confirm/request_id` |
| POST | `/api/player/consent` | 玩家 | P4；`consent_id/decision` |
| POST | `/api/player/duel` | 玩家 | P4，默认关闭 |
| POST | `/api/player/expel` | 玩家 | P4，默认关闭；仅掌门/师父 |

所有 POST 返回：

```json
{
  "ok": true,
  "event_seq": 12345,
  "tick": 720,
  "verb": "move",
  "effects": [{"op": "set_location", "args": {"entity": "player:u_1001", "to": "inn"}}],
  "reject_reason": null
}
```

失败时 `ok=false`、`reject_reason` 为可读 code（如 `not_together`、`npc_declined:trust_too_low`、`rate_limited`），并仍写入 rejected 事件。

### 7.5 与 `propose_intent` / `Writer` 的关系

MCP 身份（服务端注册，不接受客户端自报）：

| 身份 | 权限 | 用途 |
|---|---|---|
| `player:<account_id>` | `propose_intent` | 玩家只能提议；不能 admin |
| `policy:request` | `admin_apply` | 策略请求身份（服务端固定） |
| `policy:approve` | `admin_apply` | 普通 L1/L2 自动审批身份（服务端固定，必须与 request 不同） |
| `ops:reviewer` | `admin_apply` | L3 人工复核身份；P3 不产生 L3 |

写流程：

1. API 认证账号，服务端映射到 `player:<id>`；
2. 校验玩家是否 online/alive、速率、动作白名单、role 白名单、tick；
3. 可选 `world.preview_intent`（新增 MCP 方法，权限 `propose_intent`；clone 上执行，不写 outbox）；
4. `world.propose_intent` 写入 outbox，`requester=player:<id>`；
5. 普通动作由 `policy:request` 调 `admin.request`，再由 `policy:approve` 调 `admin.approve` 和 `admin.apply`；
6. L3 动作由 `policy:request` 请求、`ops:reviewer` 批准并 apply；
7. `Writer.submit` 单事务执行，写 `events`；成功/拒绝均记录审计。

不得让玩家直接调用 `admin.*`；不得让 `policy:approve` 与 `policy:request` 是同一个字符串；不得在客户端信任权限等级。

### 7.6 指标与回放

- `docs/metrics.md` 必须升级到 `v1.2.0`，在 K3 过滤条件中排除 `source=player`，并新增玩家专属指标：`player_actions_total`、`player_intents_by_verb`、`player_rejection_rate`、`player_relation_delta`、`player_consent_rate`、`player_offline_ticks`、`player_death_count`。
- K3 仍只统计 NPC 的 17 动词；`talk` 与玩家生命周期动词不计入 K3。
- K1 分母不变：玩家没有目标记录，不能把玩家行为算进 K1 分子。
- K6：玩家初始 300 文/4 件物品必须计入 `money_supply.minted` 与 `endowment_total`；`player_enter` 不允许 mint，首次进入由独立事件 `player_endowment` 显式 mint；独立审计必须包含玩家钱包/物品。
- 有玩家 run 与无玩家 run 的 K1/K3/K6 对比必须标记 `non_comparable`，并写明 `comparison_basis`（内容哈希、commit、是否含玩家配置）。
- Replay：玩家事件必须从头重建出相同的 `players/consents/relation_ledger`、钱包、库存和关系；无玩家 run 的 `rev10d/rev72d` 必须仍 MATCH。

---

## 8. 实现拆解（按 2 人周粒度）

> 1 人周 = 5 人日；下表每项约 2 人周（10 人日）。P3 共 8 人周；P4 共 8 人周（其中 4 人周可砍）。

### P3（必须先做，8 人周）

| 任务 | 工时 | 交付物 | 优先级 | 依赖 |
|---|---|---|---|---|
| **P3-A 身份与创世** | 2 人周 | `config/player.yaml`；创世创建玩家/钱包/物品；`players` 状态；`player_enter/leave/resume`；原生/访客两种模式；守恒测试 | 必须先做 | 无 |
| **P3-B 命令与写路径** | 2 人周 | 17+talk 命令目录；API `/api/player/*`；`world.preview_intent`；MCP 身份与 tick/频率/幂等护栏；玩家层 `allowed_roles`；拒绝原因表 | 必须先做 | P3-A |
| **P3-C 社交 consent、关系与离线** | 2 人周 | NPC consent（拜师/招揽/婚配/交易）；`relation_ledger` 防刷；离线/托管/休眠状态机；LOD 只聚光在线玩家；`talk` 同地点与服务端回复 | 必须先做 | P3-B |
| **P3-D 兜底、UI 与验收** | 2 人周 | 不死亡/体力耗尽；9 项兜底；玩家面板（状态、动作、preview、关系、consent、离线提示）；pytest/replay/framework_check；指标 v1.2；交付报告 | 必须先做（UI 可只做基础版） | P3-C |

**P3 可砍项（不阻塞交付）**：托管自动吃饭可退化为“只移动回家”；玩家面板可先做文本版；角色外观/头像；多语言；`request_id` 之外的离线队列。

### P4（按产品优先级，8 人周）

| 任务 | 工时 | 交付物 | 优先级 | 依赖 |
|---|---|---|---|---|
| **P4-A 多玩家与同意** | 2 人周 | 多 `player:` 档案；`talk_ledger` 改 `actor + target` 复合键；玩家对玩家 consent；玩家交易双确认；非致死决斗 | 必须（若要多玩家） | P3-C |
| **P4-B 门派治理** | 2 人周 | `player_expel`（掌门/师父）；掌门继任；成员上限/公告；玩家门派公账查看；离线延迟生效 | 必须（若要玩家立派） | P4-A |
| **P4-C 致死与遗产（默认关闭）** | 2 人周 | `duel→downed`；P4 死亡/复活；遗产转移；关键 NPC 白名单；全局死亡上限；世界规则开关 | **可以砍** | P4-A |
| **P4-D 内容与观测** | 2 人周 | 新身份/门派内容、平衡数值、玩家漏斗与异常监控、压测 | **可以砍** | P3-D |

**必须先做**：P3-A 的状态/回放/守恒；P3-B 的 tick + 权限 + 幂等；P3-C 的 consent 和关系上限；P3-D 的 replay 与 K6 独立审计。  
**可以砍**：P4-C 全部、P4-D 全部、P4-B 的继任/公告、托管自动吃饭、UI 动效、多语言、玩家店铺深度管理。

### P3 验收清单（必须逐项有命令/输出）

1. `pytest` 全过；新增 `test_player_entry.py`、`test_player_actions.py`、`test_player_consent.py`、`test_player_offline.py`、`test_player_conservation.py`。
2. `python3 tools/framework_check.py` 全过。
3. 10 天/72 天玩家 run replay MATCH；无玩家 `rev10d/rev72d` 仍 MATCH。
4. K6 守恒误差 0；玩家创世后无任何 mint；玩家余额不为负；物品总量对得上。
5. 玩家与 30 个 NPC 的初始关系记录数为 0；首次 talk/trade 后各只有目标一条关系。
6. 17 动词每个至少 1 个成功事件和 1 个拒绝测试；玩家层 `allowed_roles` 拒绝游侠/捕快 `found_sect`、商人 `escort`。
7. 玩家无法调用 `admin.*`；伪造 tick、伪造 actor、伪造 `reply/affection_delta` 全部被拒绝。
8. 离线 144 tick 标记 offline、432 tick 进入托管、1008 tick dormant；NPC 动作对玩家目标为 0。
9. 玩家死亡事件为 0；体力 0 只拒绝动作，不扣钱不掉物。
10. 指标报告同时给出 v1.1.0（兼容）与 v1.2.0（含玩家过滤），并标记 `non_comparable`。

---

## 玩家动作 → 现有动词（映射表）

> 17 个 NPC 动词来自 `docs/metrics.md` 的 K3 v1.1.0；`talk` 是第 18 个玩家入口，不计入 K3。

| 玩家动作/命令 | 现有动词 | 玩家可用 | NPC 可用 | 目标在场 | 关键前置/代价 | 主要效果 | P3 状态 |
|---|---|---|---|---|---|---|---|
| 移动到某地 | `move` | 是 | 是 | 否 | 相邻、体力/时间 | 改位置、扣体力 | 开放 |
| 修炼技能 | `cultivate` | 是 | 是 | 否 | 1-8 小时、体力 | 技能 +0.0025/小时 | 开放 |
| 做工 | `work` | 是 | 是 | 企业在场 | 1-12 小时、体力；企业现金 | 工资（10% 税）、欠薪 IOU | 开放 |
| 吃饭 | `consume` | 是 | 是 | 食物有售 | 钱、3% 税 | 体力 +25、扣库存 | 开放 |
| 买东西 | `buy` | 是 | 是 | 企业在场 | 钱、3% 税、库存 | 物品入包 | 开放 |
| 卖东西 | `sell` | 是 | 是 | 企业在场 | 物品、企业现金 | 60% 售价、3% 税 | 开放 |
| 交易 | `trade` | 是 | 是 | NPC 同地点 | 非敌对；公平闸门；库存/钱包 | 双方物品/金钱转移，关系 +3/+2 | 开放 |
| 拜师 | `apprentice` | 是 | 是 | NPC 同地点 | 师父技能 >=0.5 且 >=玩家+0.1；无师父；consent | master/disciple +5 | 开放 |
| 加入门派 | `join_sect` | 是 | 是 | 门派地点 | 未入其他门派、会费 | faction_join、掌门 ally +3 | 开放 |
| 求婚/成婚 | `marry` | 是 | 是（NPC 对 NPC） | NPC 同地点 | 双方未婚、affection>=60；NPC trust>=50 | spouse、marriage 80/70 | 开放；NPC→玩家关闭 |
| 开店 | `open_shop` | 是 | 是 | 当前地点 | 无店、金库缓冲、300/1200/3000 | 店铺、租金/税 | 开放 |
| 押镖 | `escort` | 是（仅镖师/脚夫/捕快/船夫） | 是 | 路线起点 | 镖局现金、未在押镖、费用上限 | 镖银、体力/风险 | 开放；商人拒绝 |
| 立派 | `found_sect` | 是（按职业白名单） | 是 | 当前地点 | 2500、声望>=40、5 人、未入 sect | 创建 sect、公账、成员入派 | 开放；游侠/捕快按内容拒绝 |
| 招人/结拜 | `recruit` | 是 | 是 | NPC 同地点 | 目标不在 sect、未 sworn；consent | sworn 30/45、入派 | 开放（“结拜”无独立动词） |
| 接任务 | `quest_accept` | 是 | 是 | 发布人地点 | 未押镖、最多 2 个进行中 | quest accepted、押金/期限 | 开放 |
| 交任务 | `quest_deliver` | 是 | 是 | 交付地点 | 本人接单、未过期、物品 | 奖励、关系、完成 | 开放 |
| 还债 | `debt_settle` | 是 | 是 | 否 | 自己是债务人、余额足够 | 还款、债务结清 | 开放 |
| 对话 | `talk` | **仅玩家** | 否（未接线） | NPC 同地点 | 消息<=200；服务端 reply | 记忆、acquaintance、±2/日 8 | 开放，不计 K3 |
| 进入世界 | `player_enter` | **仅玩家** | 否 | 否 | 账号/身份；原生 run 创世 | 创建/恢复 profile、online | 新增 |
| 下线/上线 | `player_leave` / `player_resume` | **仅玩家** | 否 | 否 | 会话 | online 状态 | 新增 |
| 在线心跳 | `player_heartbeat` | **仅玩家** | 否 | 否 | 每 12 tick 最多一次 | 更新 last_intent_tick | 新增，不计 K3 |
| 离线状态推进 | `player_presence` | 系统 | 系统 | 否 | 1/3/7 游戏日 | offline/trustee/dormant | 新增 |
| 同意/拒绝 | `consent_request` / `consent_resolve` | 玩家/系统 | 否 | 目标玩家 | P4 | consent 记录 | P3 接口保留 |
| 逐出师门 | `player_expel` | 玩家（掌门/师父） | 否 | 目标在线或延迟 | 理由；P4 | 解除关系、离派 | P4，默认关闭 |
| 复活 | `player_respawn` | 系统 | 否 | 否 | P4 死亡 | 回 temple、扣费 | P4，默认关闭 |

</Write>
