# 玩家 HTTP API 契约（P3-E · 冻结版）

> 状态：**字段名冻结（`profile_version = 1`）**。前端可直接按本文实现。
> 实现：`src/wsim/api/`（Python 标准库，仅 JSON 进出，无页面）。
> 变更规则：字段名不得随意修改；确需修改必须在本文末尾追加 `CHANGELOG`，写清影响前端/后端哪一方，并递增 `profile_version`。

---

## 0. 启动与 run 隔离（不污染既有 run）

### 0.1 推荐启动命令

在 `world-sim/` 目录下：

```bash
PYTHONPATH=src python3 -m wsim.api --run player-demo --port 8020
```

可直接使用的 run 目录名：**`runs/player-demo`**（可写）。
默认预置账号：`u_demo`（对应玩家实体 `player:u_demo`）。如需预置其他账号：

```bash
PYTHONPATH=src python3 -m wsim.api --run player-demo --seed-account alice --port 8020
```

> 启动后先 `POST /api/player/enter`。创世预置玩家初始 `presence=offline`，未 enter 前调用其它动作会返回 `player_offline`。

### 0.2 `WorldSession` 的目录行为

- `WorldSession(root, run_id=...)` 只使用 `<root>/runs/<run_id>/`：
  - `mkdir(parents=True, exist_ok=True)`，目录不存在会新建；
  - `events.sqlite` 由 `EventStore` 打开/新建（append-only 事件日志）；
  - `mcp_audit.jsonl` 由 `MCPGateway` 写入。
- **不会读取**既有 run 的 `events.jsonl` / `summary.json`；服务启动时从 `content/` 重新 bootstrap（含 12 NPC、任务板、预置玩家资产）。
- 注意：当前启动流程**不 replay 既有 `events.sqlite`**。若 `runs/player-demo` 已存在，新的 genesis 事件会追加到旧日志，导致同一日志里出现两段创世、seq 语义失真。因此：
  - 每次启动前删除 `runs/player-demo`，或换一个全新 run 名（推荐 `--run player-demo-<日期>`）。
  - 不要传 `runs/p3c-players-1d`、`runs/p3d-*` 等既有 run 名；它们对本 API 是只读资产，不应被打开写入。
  - 若要复看旧 run，先复制成新目录，例如 `cp -R runs/p3c-players-1d runs/player-demo-copy`，再对新目录做写入实验；不要直接在旧目录启动本 API。

---

## 1. 通用约定

所有响应均为 `application/json; charset=utf-8`。

### 1.1 只读响应公共字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 查询是否成功 |
| `profile_version` | integer | 契约版本，当前固定 `1` |
| `tick` | integer | 服务端当前 tick（1 tick = 10 游戏分钟） |

### 1.2 写操作响应公共字段

写操作包括 `/api/player/enter`、`/api/player/action`、`/api/player/leave`、`/api/player/heartbeat` 等。

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 是否执行成功 |
| `status` | string | `accepted` / `executed` / `rejected`，见 §4.3 |
| `profile_version` | integer | 固定 `1` |
| `action` | string | 客户端动作名（`enter` / `move` / `talk` / `trade` / `quest_accept` / `quest_deliver` / `heartbeat` / `leave`） |
| `verb` | string | 服务端内部 verb（`player_enter` / `move` / `talk` / `trade` / `quest_accept` / `quest_deliver` / `player_heartbeat` / `player_leave`） |
| `player` | string | 玩家实体 id，格式 `player:<account_id>` |
| `intent_id` | string | 本次意图 id（`i:<sha256>`），可用于 `/api/player/intents/<id>` |
| `event_seq` | integer | 本次落库事件 seq；拒绝也会产生一条 reject 事件 |
| `tick` | integer | 本次事件发生的 tick |
| `next_tick` | integer | 本次请求处理后的 tick（默认 `tick + 6`） |
| `reject_reason` | string | 失败原因码；成功为空字符串 |
| `reason_text` | string | 失败的人话原因；成功为空字符串 |
| `details` | object | 动作详情（关系变化、对话回复、enter 配给等） |
| `data` | object | 动作成功后的旧版扁平快照（`id`/`wallet`/`inventory`/`nearby` 等）；拒绝时为空对象。字段名与 `/state.player` 不同，见 §2.2 |

### 1.3 身份与幂等

- **身份键是 `account_id`**：`account_id` → 实体 `player:<account_id>`。同一 `account_id` 重复进入返回同一个玩家，不新建实体、不重复发放资产。
- **请求幂等键是 `request_id`**（可选）：同一 `account_id + request_id` 重复提交返回第一次的响应，不重复写事件。
- `account_id` 规则：`^[a-z0-9_-]{3,32}$`。
- `client_tick` 只回显、不参与调度。服务端 tick 是唯一事实源；响应固定带 `client_tick_ignored: true`（未传时为 `null` 且为 `false`）。
- `confirm` 只回显；本轮不开放不可逆动作，因此不会触发二次确认。

---

## 2. `POST /api/player/enter`

请求体：

```json
{
  "account_id": "u_demo",
  "display_name": "陈捕快",
  "archetype": "捕快",
  "request_id": "req-enter-1"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `account_id` | string | 是 | 身份键，规则见 §1.3；决定实体 `player:<account_id>` |
| `display_name` | string | 否 | 角色名，2-12 字且全局唯一；不传用服务器默认名（默认 `陈捕快`） |
| `archetype` | string | 否 | `游侠` / `商人` / `捕快`；首次进入锁定，后续改身份返回 `identity_locked`；不传默认 `游侠` |
| `request_id` | string | 否 | 幂等键 |

`archetype → role` 映射（服务端固定）：

| `archetype` | `role` | 初始技能 |
|---|---|---|
| `游侠` | `镖师` | `{"镖师": 0.35}` |
| `商人` | `掌柜` | `{"掌柜": 0.35}` |
| `捕快` | `捕快` | `{"捕快": 0.35}` |

### 2.1 初始资产（首次进入发放一次）

| 资产 | 数量 | 运行中新账号来源 | 创世预置账号来源 |
|---|---|---|---|
| 铜钱 | 300 文 | `player_endowment` | `genesis_player_endowment` |
| 炊饼 `flatbread` | 3 | `player_endowment_carry` | `genesis_player_carry` |
| 粗布 `coarse_cloth` | 1 | `player_endowment_carry` | `genesis_player_carry` |

- **运行中新账号第一次 `enter` 一定会拿到上述资产**，不再出现“钱包为 0”的账号。发放由紧随 enter 的独立事件 `player_endowment` 完成：
  - 显式 `mint` 300 文（`source="player_endowment"`、`reason="first_entry"`），计入 `money_supply.minted`；
  - 显式 `item_create`（炊饼×3、粗布×1）；
  - 写入 `PlayerProfile.endowed=true` / `endowed_tick` / `endowed_source`。
- **`player_enter` 本身仍不铸币、不造物**：300 文来自 `player_endowment`，不是 enter 事件。
- **幂等键是 `account_id`**：`endowed=true` 后，二次 enter（含离开后重进）都不会再发；在线重复 enter 直接返回 `already_online`，不进入发放流程。
- **K6 守恒仍为 0**：mint 使 `minted_total` +300，同时 accounts +300，`accounts_sum - (minted - burned) = 0`。不从 NPC 钱包扣款、不改创世总额。
- **跨 run 重启**：新 run 是新世界，档案不存在，同一账号可以重新领取；同一 run 内只能领一次。
- **防刷上限**：单区域最多 `200` 个玩家席位，初始资产累计发放上限 `60000` 文（`200 × 300`）。超限时新账号 enter 返回 `player_cap_reached`，发放返回 `endowment_cap_reached`；累计发放额记录在 `endowment_total`（`/state` 顶层同名字段可读）。
- `details.created` 表示“本次 enter 是否新建档案”（创世已预建的账号为 `false`）。

`details.endowment` 字段（enter 成功时必定返回）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `granted` | boolean | **本次请求**是否新发放初始资产 |
| `already_granted` | boolean | 之前是否已发放（本次没有重复发放） |
| `money` | integer | 该账号已发放的铜钱；未发放为 `0` |
| `items` | array | `{item,name,qty}`；未发放时仍列出配给定义 |
| `money_source` | string | `genesis_player_endowment` / `player_endowment` / `""` |
| `item_source` | string | `genesis_player_carry` / `player_endowment_carry` / `""` |
| `event_seq` | integer | 本次 `player_endowment` 事件的 seq；未发放为 `0` |
| `endowed_tick` | integer | 发放发生的 tick |
| `granted_at_genesis` | boolean | **语义已修正**：该账号资产是否来自创世预置；不再表示“本次未发放” |
| `endowment_total` | integer | 本 run 玩家初始资产累计发放额（含创世预置） |
| `cap` | integer | 初始资产累计发放上限（当前 `60000`） |
| `reject_reason` | string | 发放被拒时的原因码；正常为空 |
| `reason_text` | string | 发放被拒时的人话原因；正常为空 |
| `note` | string | 行为说明 |

### 2.2 响应示例（真实调用，见 §10.1）

关键字段：

```json
{
  "ok": true,
  "status": "executed",
  "profile_version": 1,
  "action": "enter",
  "verb": "player_enter",
  "player": "player:u_demo",
  "event_seq": 4,
  "tick": 0,
  "next_tick": 6,
  "reject_reason": "",
  "reason_text": "",
  "details": {
    "created": false,
    "resumed": true,
    "role": "捕快",
    "endowment": {
      "granted": false,
      "already_granted": true,
      "money": 300,
      "items": [
        {"item": "coarse_cloth", "name": "粗布（一匹）", "qty": 1},
        {"item": "flatbread", "name": "炊饼", "qty": 3}
      ],
      "money_source": "genesis_player_endowment",
      "item_source": "genesis_player_carry",
      "event_seq": 0,
      "endowed_tick": 0,
      "granted_at_genesis": true,
      "endowment_total": 300,
      "cap": 60000,
      "reject_reason": "",
      "reason_text": "",
      "note": "首次进入一次性发放；同账号重复进入不再发放。跨 run 重启是新世界，可重新领取。"
    }
  },
  "data": {
    "id": "player:u_demo",
    "name": "陈捕快",
    "archetype": "捕快",
    "role": "捕快",
    "location": "market",
    "home": "market",
    "stamina": 100.0,
    "max_stamina": 100.0,
    "presence": "online",
    "online": true,
    "tick": 0,
    "wallet": 300,
    "inventory": {"coarse_cloth": 1, "flatbread": 3},
    "skills": {"捕快": 0.35},
    "relations": [],
    "active_quests": [],
    "nearby": [ "...当前地点 NPC..." ],
    "active_quests_detail": []
  }
}
```

> `data` 是动作成功后的**旧版扁平快照**（`wallet` 是整数、`inventory` 是 `{item: qty}`）。完整结构请以 `GET /api/player/state` 的 `player` 字段为准；两者字段名不同，前端不要混用。

---

## 3. `GET /api/player/state?account_id=u_demo`

精确结构（字段名冻结）：

```json
{
  "ok": true,
  "profile_version": 1,
  "account_id": "u_demo",
  "tick": 24,
  "endowment_total": 300,
  "player": {
    "entity_id": "player:u_demo",
    "account_id": "u_demo",
    "name": "陈捕快",
    "archetype": "捕快",
    "role": "捕快",
    "location": "inn",
    "location_name": "客栈",
    "home": "market",
    "stamina": 100.0,
    "max_stamina": 100.0,
    "presence": "online",
    "online": true,
    "created_tick": 0,
    "last_intent_tick": 12,
    "endowed": true,
    "endowed_tick": 0,
    "wallet": {
      "money": 300,
      "items": [
        {"item": "coarse_cloth", "name": "粗布（一匹）", "qty": 1},
        {"item": "flatbread", "name": "炊饼", "qty": 3}
      ]
    },
    "skills": {"捕快": 0.35},
    "relations": [
      {"target": "liu_sanniang", "name": "柳三娘", "kind": "acquaintance", "affection": 1.0, "trust": 40.5}
    ],
    "active_quests": [],
    "sect": {
      "in_sect": false,
      "sect": null
    },
    "marriage": {
      "status": "single",
      "spouse_id": "",
      "spouse_name": "",
      "since_tick": 0,
      "last_daily_tick": 0,
      "daily_note": ""
    }
  },
  "marriage": {
    "status": "single",
    "spouse_id": "",
    "spouse_name": "",
    "since_tick": 0,
    "last_daily_tick": 0,
    "daily_note": ""
  },
  "sect": {
    "in_sect": false,
    "sect": null
  },
  "location": {
    "id": "inn",
    "name": "客栈",
    "description": "",
    "exits": [
      {"id": "ferry", "name": "河渡", "exists": true},
      {"id": "market", "name": "集市", "exists": true},
      {"id": "temple", "name": "城隍庙", "exists": true}
    ],
    "services": ["cured_meat", "noodle", "service_room", "service_storytelling", "wine"]
  },
  "present_npcs": [
    {"id": "cui_tieyi", "name": "崔铁衣", "role": "捕快", "location": "inn", "presence": "online", "doing": ""},
    {"id": "hua_jieyu", "name": "花解语", "role": "说书人", "location": "inn", "presence": "online", "doing": ""},
    {"id": "lu_jiujin", "name": "陆九斤", "role": "脚夫", "location": "inn", "presence": "online", "doing": ""},
    {"id": "song_henian", "name": "宋鹤年", "role": "掌柜", "location": "inn", "presence": "online", "doing": ""},
    {"id": "wen_xiaoman", "name": "温小满", "role": "酒保", "location": "inn", "presence": "online", "doing": ""}
  ],
  "quest_board": {
    "counts": {"open": 11, "acceptable_here": 2, "accepted_by_me": 0},
    "rows": [
      {
        "id": "quest:commission_herb_sorting:0",
        "template": "commission_herb_sorting",
        "name": "药铺分药",
        "category": "commission",
        "giver": "han_xinglin",
        "giver_name": "韩杏林",
        "giver_location": "pharmacy",
        "target_location": "pharmacy",
        "reward_money": 14,
        "reward_relation": 2.0,
        "deadline_tick": 288,
        "deadline_day": 3,
        "status": "open",
        "acceptable": false,
        "can_accept_here": false,
        "accepted_by_me": false,
        "expired": false
      }
    ],
    "active": []
  },
  "online_players": [
    {
      "id": "player:u_demo",
      "name": "陈捕快",
      "archetype": "捕快",
      "role": "捕快",
      "location": "inn",
      "presence": "online",
      "self": true
    }
  ],
  "nearby": [ "...present_npcs 的别名，保留兼容..." ],
  "active_quests_detail": []
}
```

说明：

- `location.description` 来自内容包；当前内容包未提供描述时为 `""`（前端不得依赖非空）。
- `location.exits` 只列当前地点的出口；`exists` 表示内容包里是否存在该地点。
- `present_npcs` 只含**当前地点、status=alive、非 `player:` 前缀**的实体。
- `online_players` 只含 `PlayerProfile.online=true` 的玩家（含自己，`self=true`）。
- `quest_board.rows` 只含 `status=open`；`active` 只含“本人已接”的任务。
- `acceptable` / `can_accept_here` 已综合：发布人在场、任务未过期、本人未押镖、本人活跃任务 < 2。

### 3.1 价格、买卖上限与押镖单（2026-11-05 只增不改）

`location.services` **仍是 item id 字符串列表**，语义不变。新增并列字段：

- `location.services_detail[]`：每个服务/商品一行，字段
  `item`、`name`、`category`、`price`（单件买入价，不含税）、`sell_price`（单件收购价）、
  `tax`（单件交易税）、`stock`（当前店铺库存）、`available`（当前地点是否有货）、
  `business` / `business_name`、`buy_limit`、`sell_limit`。
- 顶层 `shop`：`{location, businesses[], buy[], sell[]}`。`buy[]` 行额外带
  `total = price + tax`；`sell[]` 行带 `qty` 与 `sell_limit`。
- `player.wallet.items[]` 在原 `item/name/qty` 之外新增 `price`、`sell_price`。
- `escort_orders[]`：本人押镖单（`id`/`origin`/`destination`/`cargo_value`/`fee`/`deposit`/
  `status`/`arrive_tick`/`success` 等），供 UI 展示进度。
- `escrow[]`（2026-11-05 押镖托管）：`{account, holder, holder_name, item, qty}`。
  每张在途镖单的托管货物逐桶可见，`account = "escrow:" + 镖单 id`；
  `escrow_total` 是托管总件数。送达/失败结算后该镖单的桶归零。
- `reconciliation`：`accounts_sum`、`minted_total`、`burned_total`、`net_issued`、
  `delta = accounts_sum - net_issued`（应恒为 0），以及 `escrow_accounts`、`escrow_items`。
  托管是物品科目，单列展示，不混进货币等式；但绝不允许出现“隐形口袋”。

`buy_limit`/`sell_limit` 已综合“单笔最多 20 件、店铺库存/现金、玩家钱包/持有量”。
这些字段均为增量；原有键与语义不变，匿名公共摘要白名单也不含这些新字段。

### 3.2 玩家店铺与在场 NPC 视图补充（2026-11-05 只增不改）

顶层 `shop` 在保留 `location` / `businesses[]` / `buy[]` / `sell[]` 的前提下新增以下只读字段；
玩家未开店时形状仍然稳定（`employees=[]`、`stock=[]`，数值键均为 `0`）：

| 字段 | 类型 | 口径 |
|---|---|---|
| `employees[]` | array | 本店雇工，按实体 id 升序。每项 `name`、`position`（岗位）、`wage`（日薪）、`present`（雇工实体 `status=alive` 且 `location == 店铺 location`，即当日到店）、`unpaid_wage`（该雇工在本店 `status=active` 且 `note="unpaid_wage"` 的债务合计） |
| `cash` | integer | 本店现金账户（`cash:<business_id>`）余额 |
| `stock_count` | integer | 本店可售库存**件数合计**；排除 `escrow:` 开头的托管键与 `qty <= 0`，不是品类数 |
| `stock[]` | array | 每项 `item`、`name`、`qty`（件数）、`sell_price`（单件收购价）；同样排除托管键与未知物品 |
| `today_revenue` | integer | 当日顾客买入流水（`Business.day_revenue`） |
| `today_wage` | integer | 本店当日工资合计（`employee_wages` 求和） |
| `today_tax` | integer | 本店日摊税（`daily_shop_tax(content, kind)`，月租/30，至少 1 文） |
| `tax_arrears` | integer | 结转到次日仍未缴的日摊税 |
| `last_settle_tick` | integer | 最近一个日界 tick：`tick - tick % 144`（`TICKS_PER_DAY = 144`） |

`present_npcs[]`（`nearby` 是同一份数据的别名）每项新增：

| 字段 | 类型 | 口径 |
|---|---|---|
| `skills` | object \| null | 最高技能 `{"name": 技能名, "level": 等级}`；无技能为 `null` |
| `employed_by` | string \| null | `Entity.employed_at`，否则 `Entity.business`，都没有为 `null` |
| `bond` | number | `WorldState.affection_between(玩家, NPC)`，无关系为 `0.0` |
| `trust` | number | 玩家与 NPC 双向 `status="active"` 关系中最大的 `trust`；无关系为 `0.0`（`Relation` 默认信任基线是 `0.4`） |

以上均为只读投影，不改动作语义；同一 tick 内按玩家缓存，写动作推进 tick 后才重新计算。

### 3.3 门派私有状态（2026-11-06 只增不改）

`player.sect` 与顶层 `sect` 同源，**只在本人令牌的 `GET /api/player/state` 返回**：

```json
{
  "in_sect": true,
  "sect": {
    "id": "u_demo",
    "faction_id": "sect:u_demo",
    "name": "测试门",
    "location": "market",
    "location_name": "集市",
    "role": "leader",
    "leader": "player:u_demo",
    "leader_name": "测试掌门",
    "member_count": 4,
    "treasury": {"account": "treasury:sect:u_demo", "balance": 600},
    "disciples": [
      {"id": "duan_wudou", "name": "段无斗", "skill": 0.51, "presence": "online",
       "at_sect": true, "arrears": 0, "affection": 45.0, "trust": 55.0,
       "settled_today": true}
    ],
    "limits": {"disciple_cap": 3, "dues_daily": 5, "stipend_daily": 10, "arrears_expel": 30},
    "settled_today": true
  }
}
```

- 未入派：`{"in_sect": false, "sect": null}`。
- `role`：`leader`（立派者/门主）或 `disciple`；门级 `settled_today` 只对掌门有意义，弟子为 `null`。
- `disciples` 只含 `entity.faction` 指向本派且 `status=alive` 的弟子；`arrears` 是未结清的
  `unpaid_stipend` 债务合计（达到 30 即当日离派）。
- `disciples[].affection` / `trust`：**查看者 ↔ 该弟子**的双向关系取值，直接来自
  `player_sect._bond`（缺失关系为 `0.0`）。门主视角与 `recruit_disciple` 裁决用同一份数据，
  不会出现“页面显示 30、后端却判 `favor_too_low`”。
- `disciples[].settled_today`：该弟子**今日**是否已参与日结（布尔）。由日结成功时写入该弟子的
  「…日结：第 N 日…」记忆判定；日结后加入的新弟子当天为 `false`。门级 `settled_today` 保持原语义。
- `treasury.balance` 是门派金库账户真实余额，不是缓存。
- **匿名 `GET /api/player/state` 不返回 `player.sect` / `sect`**；公共摘要里只有顶层
  `sect_name`（门派显示名）。`/api/world/feed` 只收 NPC 事件，玩家门派事件不进入公共流。

### 3.4 `GET /api/player/sects`（可加入门派列表，2026-11-06 新增）

只读、无副作用；**匿名可读**，带本人令牌时只叠加“自己是否已入派”。只暴露安全字段，
不含金库金额、弟子明细、账目或任何账号私有信息。

请求：`GET /api/player/sects`（可选 `Authorization: Bearer <token>`；不接受 `account_id`
个性化，避免匿名借他人 id 探测其门派归属）。

响应（`tick` 为当前世界 tick）：

```json
{
  "ok": true,
  "profile_version": 1,
  "tick": 54,
  "count": 1,
  "sects": [
    {
      "id": "u_gapdemo",
      "faction_id": "sect:u_gapdemo",
      "name": "缺口门",
      "leader": "player:u_gapdemo",
      "leader_name": "缺口验证",
      "location": "market",
      "location_name": "集市",
      "member_count": 4,
      "reputation": 265.0,
      "joinable": true
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `id` / `faction_id` | 去掉 `sect:` 前缀的门派 id / 完整 faction id |
| `name` | 门派名号 |
| `leader` / `leader_name` | 门主实体 id / 显示名；无门主的内容行会不出现在列表 |
| `location` / `location_name` | 驻地 id / 显示名 |
| `member_count` | 在册人数 = 门主 + 活跃弟子（不含已离派者） |
| `reputation` | 门派声望 = 门主的 `reputation_points`（P2 才有独立门派声望），保留 1 位 |
| `joinable` | 匿名：`true`（列表中的门派均有门主与驻地）；本人令牌且已在任何门派中：`false`（`join_sect` 会返回 `already_in_faction`） |

- 排序固定按 `faction_id` 升序（同一 id 再按名号），不依赖字典插入序或运行时状态。
- 列表只含 `sect:*` 且有门主的内容门派；地点行会 `org:*` 与其无名号行会（如 `sect:tea_guild`）不出现。
- 匿名响应与本人令牌响应的字段集合完全一致，仅 `joinable` 按上文规则变化；匿名传
  `account_id` 不会触发任何个性化。

---

## 4. `POST /api/player/action`

统一写入口。所有玩家意图仍走 `propose_intent → MCP → Writer`（单写者），HTTP 层不得直连 Writer。

### 4.1 请求体

```json
{
  "account_id": "u_demo",
  "verb": "move",
  "args": {"to": "inn"},
  "client_tick": 12,
  "request_id": "req-move-1",
  "confirm": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `account_id` | string | 是 | 身份键 |
| `verb` | string | 是 | 统一动词，见 §4.2 |
| `args` | object | 否 | 动作参数；缺省时允许把参数平铺在请求体（服务端取字段） |
| `client_tick` | integer | 否 | 只回显，不参与调度 |
| `request_id` | string | 否 | 幂等键 |
| `confirm` | boolean | 否 | 只回显；不可逆动作本轮不开放 |

### 4.2 统一动词表

| 客户端 `verb` | 内部动作 | `args` 字段 | 说明 |
|---|---|---|---|
| `enter` / `player_enter` | `player_enter` | 同 §2 | 进入世界 |
| `leave` / `player_leave` | `player_leave` | 无 | 离线；NPC 不得再主动影响 |
| `heartbeat` / `player_heartbeat` | `player_heartbeat` | 无 | 刷新在线；距上次 <12 tick 返回 `heartbeat_too_soon` |
| `move` | `move` | `to`（地点 id） | 相邻移动；非相邻 `not_adjacent`，不存在 `unknown_location` |
| `talk` | `talk` | `target`（NPC id）、`message`（≤200 字） | 服务端计算好感/关系；成功时 `details.reply` |
| `trade` | `trade` | `target`、`offer`（`[{item,qty}]`）、`want`、`money_delta` | 公平闸门：价值比 0.70（好感≥60 时 0.50） |
| `quest_accept` / `quest/accept` | `quest_accept` | `quest`（任务 id） | 必须在发布人所在地点；最多同时 2 个任务 |
| `quest_deliver` / `quest/deliver` | `quest_deliver` | `quest` | 必须到达目标地点/发布人处；奖励从发布人钱包转账 |
| `buy` | `buy` | `item`、`qty`（默认 1，1-20）、`from`（可选，默认当前位置） | 向本地店铺买入；扣“货款+交易税”，货入背包 |
| `sell` | `sell` | `item`、`qty`（默认 1，1-20）、`to`（可选，默认当前位置） | 向本地店铺卖出；出货，货款入钱包 |
| `consume` | `consume` | `item`（可选，默认最便宜食物）、`qty` | 先向店铺买，再消耗；恢复体力 |
| `escort` | `escort` | `route`（地点列表或逗号分隔）或 `route_id`；`cargo`（`{item,qty}` 或 item id）或 `cargo_value`；`fee` | 押镖；起点必须是当前位置 |
| `rest` | `rest` | `hours`（1-8，默认 4） | 休息恢复体力；按地点分级（客栈/住处/镇内/野外），押镖或临近委托截止时拒绝 |
| `marriage_propose` / `marriage/propose` | `marriage_propose` | `target`（NPC id）、`gift_money`（可选，整数）、`gift_item`（可选）、`gift_qty`（可选，1-20，默认 1） | 向同地点 NPC 求婚；NPC 按确定性规则裁决，成功时服务端追加 accept/marriage 事件 |
| `marriage_end` / `marriage/end` | `marriage_end` | 无 | 与同地点配偶和离；双方 `spouse` 清空、关系置 ended |
| `found_sect` | `found_sect` | `name`、`at?`、`doctrine?`、`sect_id?` | 立派（800 文，注册费 200 + 启动金 600）；`at` 缺省当前位置 |
| `recruit_disciple` | `recruit_disciple` | `target`、`sect_id?` | 收同地点 NPC 为徒；好感/信任 ≥30、掌门技能 ≥0.4、声望 ≥20、上限 3 人 |
| `expel_disciple` | `expel_disciple` | `target`、`sect_id?` | 掌门逐出弟子；清空欠饷、不退款 |
| `pay_disciples` | `pay_disciples` | `sect_id?`、`day?` | 每日日结：收会费 5/人、发俸禄 10/人、欠饷 ≥30 当日离派；每日一次 |
| `sect_deposit` | `sect_deposit` | `amount`、`sect_id?` | 掌门钱包 → 门派金库（真实 transfer） |
| `sect_withdraw` | `sect_withdraw` | `amount`、`sect_id?` | 门派金库 → 掌门钱包；单次 ≤500，余额须留 ≥100 |

**保活（不写事件）**：`POST /api/player/heartbeat` 传 `{"account_id":"…","keepalive":true}` 时只刷新本机
活跃时钟（活跃 ≤30s / 离线 >60s），返回 `ok`、`online`、`activity`、`idle_seconds`、`tick`，不写 `events.sqlite`。
角色已离线时返回 `player_offline`；客户端应调用 `enter` 恢复在线并重试原动作一次（enter 保留实体当前位置，不会瞬移）。

#### 4.2.1 消费类动作与原因码

- 消费类复用既有 `BuyAction` / `SellAction` / `ConsumeAction` / `EscortAction`，不另起交易逻辑；
  `rest` 由 `PlayerRestAction` 提供。
- `buy`：`from` 缺省为当前位置；扣 `price*qty + 交易税`，货进背包，钱进店铺公账。
- `sell`：`to` 缺省为当前位置；货出背包，`sell_price*qty` 从店铺公账进钱包。
- `consume`：先按买入规则成交，再消耗 `qty` 件并恢复体力/饱腹；净效果是“钱少、食物不增”。
- `rest`：`hours` 小时 × `TICKS_PER_HOUR`（6）tick，体力按地点分级恢复：
  客栈（`service_room`）2.0/tick、住处（`home`）1.5/tick、镇内（有商业体）0.5/tick、
  野外（无商业体）0.25/tick；动作本身推进 `hours × 6` tick。
  押镖中返回 `cannot_rest_while_escorting`；休息窗口会跨过已接委托截止 tick 返回 `cannot_rest_now`；
  `hours` 非 1-8 整数返回 `bad_arg:hours`。
- `escort`：`route` 必须首尾相邻可走且 `route[0] == 当前位置`；**玩家必须带货**，
  `cargo` 给真实物品（服务端按 `item_price * qty` 折算 `cargo_value`，写入
  `escort_order_create.cargo_item/cargo_qty`）。空货一律 `item_missing`，并写一条
  `reason=item_missing` 的 reject 事件，不产生任何 treasury 支出。
  **运费门槛**：`fee ≥ max(20, ceil(1.15 × cargo_value))` 且
  `fee − cargo_value − transaction_tax(fee) > 0`，否则 `fee_too_low`（避免带货净亏）。
  **真实付款人**：`fee` 由目的地收货 NPC（委托方，按余额降序/id 升序取第一个付得起者）
  的**真实钱包**垫付；委托方现金不足返回 `no_payer_funds`，不垫付、不 mint。
  只有显式公账委托（intent 带 `payer=treasury:region` 且非玩家来源）才允许 treasury 付款，
  事件会带 `payer=treasury:region` 区分。委托方承担镖局抽成：
  `payer_total = fee + int(fee × agency_cut)`。
  **发单即托管**：受理成功时立刻从背包扣出 `cargo`，转入镖局库存里的命名托管桶
  `escrow:<镖单id>:<item>`（`reason=escort_cargo_escrow`）；
  同时 `create_account` + `transfer` 把 `payer_total` 从委托方钱包转入运费托管账户
  `escrow:<镖单id>:fee:<payer>`（`reason=escort_fee_escrow`）。两项都是同事务内真实转账。
  背包数量不足返回 `item_missing`，未知物品返回 `item_not_available`。
  **结算（成功）**：运费托管账户 → 玩家 `fee`（`escort_fee_pay`）、→ 镖局 `cut`
  （`escort_agency_cut`）；交易税从玩家钱包分流。货物：货损部分 `escort_cargo_loss`，
  其余 `escort_delivered`（托管离场 + 写入委托方背包，`movement=escrow->consignor`）。
  `escort_settle` 事件同时给出 `payer / cargo_value / fee / tax / gross / net_profit / agency_cut`，
  其中 `net_profit = fee − cargo_value − tax > 0`。
  **结算（失败/取消/超时）**：`escort_cargo_return` 把货物**原量退回**玩家背包，
  `escort_fee_refund` 把运费托管余额**原额退回**委托方（玩家不收款）；
  `escort_settle` 给出 `fee=0 / tax=0 / net_profit=0 / cargo_returned=true` 与 `quoted_fee`。
  承运人实体不存在时才走 `escort_cargo_orphaned`（可解释销毁）。
  **不可重复**：发单后背包不再持有该批货，同一批货连续发第二单会返回 `item_missing`；
  押镖中 `leave` 只改在线状态，托管桶继续可见，到期结算按上述成功/失败路径收口，不蒸发也不双份。
- NPC 专属动作 `marry` / `apprentice` 统一返回
  `not_implemented_for_player`（reason_text=“这个玩法还没对玩家开放。”），不再回 `unknown_action`。
  玩家婚姻改用 `marriage_propose` / `marriage_end`，门派走 §4.2.3 的七个写动词。

#### 4.2.2 玩家婚姻（2026-11-05 新增）

- `marriage_propose`：只能向**同地点、活着、无配偶**的 NPC 求婚。玩家已有配偶返回
  `already_married`，对方有配偶/婚约返回 `target_in_couple`。
- 聘礼钱 `gift_money` 通过真实 `transfer` 从玩家钱包转入 NPC 钱包，不 `mint`；
  信物 `gift_item`/`gift_qty` 通过 `item_move` 转入 NPC 背包。**拒绝或“再处处看”时不转账、不扣物品。**
- NPC 裁决规则（零模型、纯确定性，门槛与判定依据写入 `marriage_propose` 事件）：
  - 接受：好感 ≥ 60 且信任 ≥ 30；
  - 再处处看：好感 40–55（缺口 5–20）且信任 ≥ 30，或信任 20–29 且好感 ≥ 40；
    状态为 `courtship`，有效 3 个世界日；
  - 拒绝：信任 < 20 → `trust_too_low`；好感 < 40 或好感 56–59（缺口 1–4）→ `bond_too_low`。
- 成功事件链：`marriage_propose → marriage_accept → marriage → system_tick`；
  拒绝：`marriage_propose → marriage_reject → system_tick`；
  求处：`marriage_propose → system_tick`。`event_seq` 连号，逐条可见。
- `marriage` 事件落库后：双方 `spouse` 双向写入、关系类型为 `marriage`、
  双方记忆写入，`state.marriage.since_tick` 冻结为成婚 tick。
- 婚后：配偶 NPC 每个世界日至少一次移动到玩家所在地点并执行 `spouse_time`
  （带饭/提醒缴租/问候）；玩家 `talk` 配偶时返回配偶专属台词；快照更新
  `last_daily_tick` / `daily_note`。
- 和离：`marriage_end` 需双方同地点；双方 `spouse=null`、婚姻关系 `status=ended`。
  **共同财产/聘礼分割不在本轮范围**：聘礼视为已赠予，和离不退款、不铸币，货币守恒不变。
- 重婚：已有配偶再求婚固定返回 `already_married`；和离后可再次求婚。

玩家婚姻前置原因码：

| 场景 | `reject_reason` |
|---|---|
| 不同地点 | `not_together` |
| 玩家已有配偶 | `already_married` |
| 和离时配偶不在同地点 | `spouse_not_here` |
| 好感不足 | `bond_too_low` |
| 信任不足 | `trust_too_low` |
| 信物不在背包/数量不足 | `gift_missing` |
| 聘礼钱不足 | `not_enough_money` |
| 对方已有配偶/婚约 | `target_in_couple` |
| 向自己求婚 | `self_propose` |
| 和离时没有配偶 | `not_married` |

玩家消费类动作的六类前置原因码（GET 状态里的价格字段可直接用于预判）：

| 场景 | `reject_reason` |
|---|---|
| 余额（含税）不足 | `not_enough_money` |
| 物品不在内容包 | `item_not_available` |
| 卖出/交付时背包数量不足 | `item_missing` |
| `from`/`to`/押镖起点与当前位置不符 | `not_here` |
| 当前地点没有任何店铺 | `shop_closed` |
| 有店铺但不卖/不收该物品 | `not_a_merchant` |
| NPC 专属玩法 | `not_implemented_for_player` |

押镖另有 `route_not_adjacent`、`already_on_escort`、`cargo_too_low/high`、`fee_too_low/high`、
`no_escort_agency`、`agency_insufficient_cash`，语义不变，均已补 `reason_text`。

不可逆动词（`rob`、`attack`、`kill`、`marry_force`、`player_expel`、`item_create`、`mint`、`set_field`）**不在本轮开放**；命中返回 `forbidden_verb` 并附人话原因。

#### 4.2.3 玩家门派（2026-11-06 接入）

七个动词只经 API 会话注册（`found_sect` 覆盖同名 NPC 动作，非玩家 actor 原样委托，NPC 行为不变）。

**权限**：全部是写端点，必须本人令牌；跨账号 403 `auth_account_mismatch`。师傅/掌门判定按
`Faction.leader|founder`，弟子做掌门动作返回 `not_sect_leader`。

**`sect_id` 可省略**：缺省解析为 `entity.faction`；未入派返回 `no_sect`，显式未知门派返回
`sect_not_found`。传 `"sect:xxx"` 或 `"xxx"` 均可。

| 动作 | 成功后的 `details` |
|---|---|
| `found_sect` | `{sect: <私有快照>}`；钱包扣 800（注册费 200 `transfer` 进区域公账 + 启动金 600 进门派金库） |
| `recruit_disciple` / `expel_disciple` | `{sect: <私有快照>}`，名册已更新 |
| `sect_deposit` / `sect_withdraw` | `{sect: <私有快照>}`，`treasury.balance` 为新余额 |
| `pay_disciples` | `{sect: <私有快照>, settlement: {dues, stipend, leavers[]}}` |

`settlement.leavers[]` 每项 `{"target": "<npc id>", "reason": "arrears_too_high"}`；
`dues`/`stipend` 取自真实 `transfer` 事件（`reason=sect_dues` / `sect_stipend`），不是估算。

**日结规则**（`pay_disciples`，每个世界日只能成功一次，否则 `already_settled_today`）：

1. 先收会费：每名在册弟子钱包 → 金库 `5 文`（`sect_dues` 转账，不足记 `unpaid_dues` 债务）；
2. 再按 `按 id 升序` 给每名弟子发俸禄 10：足够则全发；不足则按余额向下取整，剩余记欠饷；
3. 结算历史欠饷（先欠先还）；某弟子累计欠饷 ≥30 → 当日离派
   （`debt_create`/`debt_increase` → `set_field faction=null`，**欠饷债务保留**：门派仍欠弟子），
   在 `settlement.leavers` 里给出 `arrears_too_high`；
4. 掌门写一条 `sect:settle` 记忆作为当日已结算标记。

**拒绝码（门派专属，全部已补 `reason_text`）**：

| 场景 | `reject_reason`
|---|---|
| 未加入任何门派 | `no_sect` |
| 已在门派中（再立派） | `already_in_sect` |
| 不是掌门 | `not_sect_leader` |
| 收徒满 3 人 | `sect_full` |
| 声望 < 20（立派/收徒） | `insufficient_rep` |
| 钱不够（立派 800 / 存入大于钱包） | `insufficient_funds` |
| 名册里没有该弟子（逐出） | `disciple_not_found` |
| 目标好感 < 30 | `favor_too_low` |
| 目标信任 < 30 | `trust_too_low` |
| 欠饷累计 ≥ 30 | `arrears_too_high`（出现在 `settlement.leavers[].reason`，不是拒绝） |

沿用动作层既有码：`sect_not_found`、`sect_name_taken`、`sect_exists`、`invalid_sect_name`、
`wrong_location`、`target_not_found`（收徒目标不存在）、`target_not_alive`、`self_recruit`、
`target_is_player`、`not_together`、`already_disciple`、`target_in_sect`、`skill_too_low`、
`target_not_disciple`、`no_disciples`、`already_settled_today`、`withdraw_over_cap`、
`insufficient_treasury`、`treasury_reserve`、`bad_arg:<字段>`。

### 4.3 三种结果的区分

| `status` | HTTP | 判定 | 前端处理 |
|---|---|---|---|
| `executed` | 200 | `ok=true` 且 `event_seq > 0`，状态已在 Writer 事务内提交 | 正常渲染；用 `event_seq` / `tick` / `data` 更新 UI |
| `rejected` | 200 | `ok=false` 且 `reject_reason` 非空；拒绝事件已落库（`event_seq > 0`） | 展示 `reason_text`；不要只显示 `rejected:xxx` |
| `accepted` | 202 | 已受理但尚未提交（`event_seq=0`） | **当前同步实现不会返回该值**；若未来引入异步队列，按此分支轮询 `/intents/<id>` |

拒绝示例（真实调用）：

```json
{
  "ok": false,
  "status": "rejected",
  "reject_reason": "unknown_location",
  "reason_text": "地点不存在；请从当前地点可用的出口中选择。",
  "event_seq": 10,
  "tick": 18,
  "next_tick": 24
}
```

### 4.4 每个动作推进多少 tick

- 默认 `tick_step = 6`（1 小时 = 6 ticks）。
- 写操作处理顺序：**先在当前 tick 落事件，再推进 +6**；响应 `tick` 是事件 tick，`next_tick = tick + 6`。
- `enter` 在 tick 0 → `next_tick=6`；`talk` 在 tick 6 → `next_tick=12`。
- **被拒绝的动作同样推进 +6**（当前实现如此；前端不要假设失败不耗时）。若未来改为“拒绝不推进”，会在 CHANGELOG 标注。

### 4.4.1 为什么 `event_seq` 每次写动作是 +2（首次新账号是 +3）

- 每个写动作产生 **两条事件**，不是重复写入：
  1. **动作事件**：`player_enter` / `move` / `talk` / …（响应里的 `event_seq` 就是这一条）；
  2. **`system_tick` 事件**：把世界钟推进到 `next_tick`，本身无状态变更（no-op）。
- 所以连续两次写动作的 `event_seq` 差是 `2`。被拒绝的动作也是“reject 事件 + `system_tick`”，同样是 `+2`。
- **婚姻是唯一的多事件链**：成功求婚一次写 `marriage_propose / marriage_accept / marriage / system_tick`
  四条，`event_seq` 差 `+4`；拒绝 `+3`；求处 `+2`；和离 `+2`。响应顶层的 `event_seq`
  指向最终动作事件（成功求婚指向 `marriage`，拒绝指向 `marriage_reject`）。
- **运行中新账号的第一次 `enter` 是 +3**：`player_enter` → `player_endowment`（一次性发放）→ `system_tick`。第二次写动作起恢复 `+2`。
- 响应只返回动作事件的 `event_seq`；`system_tick` / `player_endowment` 的 seq 不放在顶层。`player_endowment` 的 seq 在 `details.endowment.event_seq`。要核对整条链请直接读 `events.sqlite` 或用 `tools/replay.py`。

### 4.5 `talk` 的 `details.reply` 何时产生

- `talk` 成功时，`TalkAction` 在同一事件里写 `memory_add`（`source="talk:npc"`），服务端据此生成：
  - `details.reply: string`：NPC 的回复正文（去掉内部前缀“我答：”）；
  - `details.relation`：关系变化（`target` / `kind` / `affection` / `trust`）。
- 回复是**同步**产生的，不需要轮询；同时 `/api/player/messages` 会返回同一条消息（`kind="talk_reply"`）。
- 若对话没有产生回复文本，`details.reply` 不存在；`messages` 也不会新增该条。

---

## 5. `GET /api/player/intents/<id>?account_id=u_demo`

`<id>` 可以是 `request_id`（推荐）或事件返回的 `intent_id`。

响应：

```json
{
  "ok": true,
  "found": true,
  "profile_version": 1,
  "intent_id": "i:...",
  "event_seq": 6,
  "tick": 6,
  "verb": "talk",
  "outcome": "ok",
  "reject_reason": "",
  "reason_text": "",
  "status": "executed"
}
```

- 找不到：`ok=false`、`found=false`、`reject_reason="intent_not_found"`、`reason_text="没有找到这个请求；请确认 request_id 或事件 seq。"`。
- 该接口只查**本账号**的请求；跨账号 ID 视为找不到。

---

## 6. `GET /api/player/messages?account_id=u_demo&since_tick=0`

拉取该玩家 `since_tick` **之后**的对话回复（严格 `>`）。

```json
{
  "ok": true,
  "profile_version": 1,
  "account_id": "u_demo",
  "tick": 24,
  "since_tick": 0,
  "next_since_tick": 24,
  "messages": [
    {
      "id": "msg:6",
      "seq": 6,
      "tick": 6,
      "kind": "talk_reply",
      "from": "liu_sanniang",
      "to": "player:u_demo",
      "text": "...NPC 回复正文..."
    }
  ]
}
```

- 轮询建议：下次请求带 `since_tick=next_since_tick`。
- `messages` 只含 `talk` 产生的 NPC 回复；不混入普通系统事件。

---

## 6.1 `GET /api/world/feed?limit=20`（公共世界事件流）

只读、匿名可读（服务模式与 legacy 模式相同），不含任何玩家私有状态。

- `limit`：1–50，默认 20；越界自动收敛。
- 响应：`{"ok": true, "tick": <世界tick>, "limit": n, "events": [...]}`，
  `events` 按时间升序（旧→新）。
- 每条事件字段固定为 6 个：

| 字段 | 类型 | 说明 |
|---|---|---|
| `tick` | int | 事件发生时的世界 tick |
| `actor` | str | NPC 名（玩家 ID 永不出现） |
| `verb` | str | 动作名（`system*` 与玩家事件已过滤） |
| `place` | str | 该 NPC 当前所在地名 |
| `summary` | str | 一句中文短句，≤40 字 |
| `important` | bool | 婚配/开店/入派/押镖/交付等里程碑类为 `true` |

```bash
curl -s 'http://127.0.0.1:8848/api/world/feed?limit=5'
```

```json
{
  "ok": true,
  "tick": 24,
  "limit": 5,
  "events": [
    {"tick": 21, "actor": "石敢当", "verb": "move", "place": "集市", "summary": "动身前往集市", "important": false},
    {"tick": 22, "actor": "曹半仙", "verb": "cultivate", "place": "城隍庙", "summary": "在城隍庙静心修炼", "important": false}
  ]
}
```

---

## 7. 错误码表（拒绝原因枚举）

> 前端必须优先展示 `reason_text`，`reject_reason` 只用于分支判断。
> 前端已使用的 `not_adjacent` / `unknown_location` / `not_together` / `not_at_giver` / `insufficient_stamina` / `quest_*` 全部保持同名。

| `reject_reason` | 人话原因（`reason_text`） |
|---|---|
| `missing_account` | 缺少账号；请先创建账号再进入世界。 |
| `bad_account` | 账号格式不合法（3-32 位小写字母、数字、下划线或连字符）。 |
| `unknown_action` | 未知操作；可用操作见 docs/player-api.md。 |
| `not_implemented_for_player` | 这个玩法还没对玩家开放。 |
| `forbidden_verb` | 该操作在本阶段不开放（伤害/抢掠/强迫婚配等不可逆动作被禁用）。 |
| `not_enough_money` | 你的钱不够完成当前操作。 |
| `item_not_available` | 这件物品不在当前内容包里。 |
| `item_missing` | 你背包里没有足够的这件物品。 |
| `not_here` | 你不在这个交易地点。 |
| `shop_closed` | 这里没有店铺，不能买卖。 |
| `not_a_merchant` | 这里的店铺不做这件货的买卖。 |
| `not_at_origin` | 你不在镖单的起点。 |
| `route_not_adjacent` | 押镖路线不连续；只能沿相邻地点逐段走。 |
| `already_on_escort` | 你正在押镖，不能同时接第二单。 |
| `cargo_too_low` | 货值低于这条线路的最低承运要求。 |
| `cargo_too_high` | 货值超过这条线路的最高承运额度。 |
| `fee_too_low` | 镖银低于门槛：需 ≥ 线路下限、≥ 货值 1.15 倍，且扣税后净收益为正。 |
| `fee_too_high` | 镖银超过这条线路的最高标准。 |
| `no_escort_agency` | 起点没有镖局，不能接镖。 |
| `agency_insufficient_cash` | 镖局现金不足以支付这单镖银（仅 NPC/公账订单）。 |
| `no_payer_funds` | 目的地委托方现金不足，无法支付这单镖银；换线路或降低镖银。 |
| `cannot_rest_while_escorting` | 押镖行程中不能休息；先送达或等镖单收口。 |
| `cannot_rest_now` | 休息会跨过已接委托的截止 tick；先交付委托再休息。 |
| `bad_arg:hours` | 休息时长必须是 1-8 的整数（小时）。 |
| `bad_arg:route` | 路线格式错误；请给地点 id 列表或 route_id。 |
| `bad_arg:cargo_qty` | 货物数量必须是 1-100 的整数。 |
| `bad_arg:cargo_value` | 货值必须是整数。 |
| `bad_arg:fee` | 镖银必须是整数。 |
| `bad_arg:item` | 物品参数不合法。 |
| `bad_arg:qty` | 数量参数不合法（1-20 的整数）。 |
| `player_not_entered` | 你还没有进入世界；请先调用 /api/player/enter。 |
| `player_offline` | 你已离线；请重新进入世界后再操作。 |
| `already_offline` | 你已经是离线状态。 |
| `already_online` | 你已经在线，无需重复进入。 |
| `identity_locked` | 身份进入后不可更改；请用原身份重新登录。 |
| `bad_archetype` | 身份必须是游侠、商人或捕快。 |
| `bad_display_name` | 角色名需 2-12 字，且不能与现有角色重名。 |
| `bad_location` | 地点不存在。 |
| `unknown_location` | 地点不存在；请从当前地点可用的出口中选择。 |
| `not_adjacent` | 目标地点与当前位置不相邻；请沿道路逐段移动。 |
| `insufficient_stamina` | 体力不足；请先休息或进食。 |
| `unknown_target` | 目标人物不存在。 |
| `not_together` | 对方不在你当前所在的地点。 |
| `target_offline` | 对方不在线，不能进行该操作。 |
| `player_consent_required` | 该操作需要对方本人同意；本阶段关闭 NPC 对玩家的婚配与招揽。 |
| `bad_message` | 消息不能为空且不超过 200 字。 |
| `heartbeat_too_soon` | 心跳过于频繁；请稍后再试。 |
| `stale_tick` | 你的请求使用了过期时间点；请刷新状态后重试。 |
| `unknown_verb` | 动作未注册。 |
| `quest_not_found` | 任务不存在。 |
| `quest_not_open` | 任务已被接走或已结束。 |
| `giver_missing` | 任务发布人已不在世界。 |
| `not_at_giver` | 你不在任务发布人所在的地点。 |
| `busy_on_escort` | 你正在押镖，不能同时接单。 |
| `too_many_quests` | 最多同时进行 2 个任务；请先交付其中一个。 |
| `quest_not_accepted_by_actor` | 这个任务不是由你接下的。 |
| `quest_expired` | 任务已超过截止时间。 |
| `not_at_target` | 你尚未到达任务目标地点或发布人处。 |
| `quest_item_missing` | 采购类任务需要你持有目标物品。 |
| `trade_unfair` | 交易价值明显不对等；对方拒绝了这笔买卖。 |
| `service_item` | 服务类物品不能用于玩家交易。 |
| `offer_not_held` | 你给出的物品数量不足。 |
| `want_not_held` | 对方没有你想要的物品。 |
| `insufficient_money` | 你的钱不够。 |
| `target_insufficient_money` | 对方的钱不够支付这笔差额。 |
| `hostile` | 对方对你敌对，拒绝交易。 |
| `role_not_allowed` | 你的身份不允许该操作。 |
| `player_cap_reached` | 本区域玩家席位已满，暂时无法创建新账号。 |
| `already_married` | 你已有配偶；和离后才能再谈婚嫁。 |
| `target_in_couple` | 对方已有婚约或配偶。 |
| `spouse_not_here` | 你的配偶不在当前地点；和离需当面说清。 |
| `bond_too_low` | 你们的好感还不够；先多相处、多说话再提亲。 |
| `trust_too_low` | 对方对你还不够信任；先相处一段时间、建立信任再试。 |
| `gift_missing` | 你指定的信物不在背包里或数量不足。 |
| `self_propose` | 不能向自己求婚。 |
| `not_married` | 你当前没有配偶，无需和离。 |
| `spouse_not_alive` | 你的配偶已不在人世。 |
| `target_not_npc` | 本轮只开放玩家向 NPC 求婚。 |
| `target_not_player` | 配偶目标必须是玩家。 |
| `spouse_offline` | 配偶不在线，今日的陪伴暂缓。 |
| `no_sect` | 你还没有加入任何门派；先立派或拜入一个门派。 |
| `already_in_sect` | 你已经在门派中，不能再立派；退出后才能另立门户。 |
| `not_sect_leader` | 只有掌门（立派者）能做这件事。 |
| `sect_full` | 收徒名额已满（上限 3 名）；先驱逐或等弟子离派。 |
| `insufficient_rep` | 声望不足；立派与收徒都要求声望至少 20。 |
| `insufficient_funds` | 钱不够完成这次门派操作（立派需 800 文）。 |
| `disciple_not_found` | 门派名册里没有这名弟子。 |
| `favor_too_low` | 对方对你的好感不足 30，不愿拜师。 |
| `arrears_too_high` | 欠饷累计已达 30 文，弟子自动离派（见 `settlement.leavers`）。 |
| `sect_not_found` | 门派不存在或已解散。 |
| `sect_name_taken` / `sect_exists` | 这个门派名/id 已被占用。 |
| `invalid_sect_name` | 门派名需 2-12 个汉字，且 id 只能用小写字母、数字、下划线或连字符。 |
| `target_not_found` | 目标人物不存在。 |
| `target_not_alive` | 目标已不在人世。 |
| `self_recruit` | 不能收/逐自己。 |
| `target_is_player` | 只能收 NPC 为徒。 |
| `target_in_sect` | 对方已属其他门派。 |
| `already_disciple` | 对方已经是本派弟子。 |
| `skill_too_low` | 掌门或目标技能不达标（目标 ≥0.2、掌门 ≥0.4）。 |
| `target_not_disciple` | 对方不是本派弟子，不能逐出。 |
| `no_disciples` | 门下没有弟子，无需日结。 |
| `already_settled_today` | 今日已日结；明天再来。 |
| `withdraw_over_cap` | 单次取出不得超过 500 文。 |
| `insufficient_treasury` | 门派金库余额不足。 |
| `treasury_reserve` | 取出后金库余额不足 100 文；请少取一些。 |
| `bad_arg:sect_id` | 门派 id 只允许小写字母、数字、下划线或连字符。 |
| `bad_arg:amount` | 金额必须是正整数。 |
| `bad_arg:name` | 门派名不能为空且不超过 12 字。 |
| `endowment_already_granted` | 该账号已经领取过初始资产，不能重复领取。 |
| `endowment_cap_reached` | 本区域初始资产发放总额已达上限；请联系运维。 |
| `account_missing` | 玩家钱包账户不存在，已被回滚。 |
| `effect_invalid` | 操作违反世界规则，已被回滚。 |
| `bad_args` | 参数不完整或格式错误。 |
| `intent_not_found` | 没有找到这个请求；请确认 request_id 或事件 seq。 |

---

## 8. 与前端现有 `ui/player/server.py` 的冲突（逐条）

前端现有代理是“只读快照 + `propose_intent` 入 outbox”，不绑定 Writer；本契约是“同步执行 + 返回事件 seq”。接入时需逐条改：

1. **路径**：前端现有 `/api/health`、`/api/scene`、`/api/npc/<id>`、`/api/enter`、`/api/move`、`/api/talk`、`/api/quest/accept`；本契约统一为 `/api/player/state`、`/api/player/enter`、`/api/player/action`、`/api/player/intents/<id>`、`/api/player/messages`。前端需改路径或加代理转发。
2. **写模型**：前端写接口只把 intent 放进 `gateway.outbox`，不落 events.sqlite；本契约同步执行并返回 `event_seq`。前端不能再依赖“已提交但未执行”的提示。
3. **玩家对象名**：前端用 `me`；本契约为 `player`。字段差异：
   - 前端 `me.registered`、`me.archetype_role` 本契约没有；
   - 本契约多 `player.home`、`player.online`、`player.created_tick`、`player.last_intent_tick`、`player.wallet`、`player.skills`、`player.relations`、`player.active_quests`。
4. **钱与物品**：前端读 `status.money`、`status.items`；本契约为 `player.wallet.money`、`player.wallet.items`（物品元素结构相同：`item/name/qty`）。
5. **状态栏**：前端期望 `status.goals`、`status.active_quests`（数字）、`status.online_players`（数字）、`status.online_player_ids`、`status.world_npcs`、`status.stamina`、`status.max_stamina`；本契约不提供 `status` 块，`online_players` 是对象数组，体力在 `player.stamina`/`player.max_stamina`，任务数在 `quest_board.counts.accepted_by_me`。
6. **关系字段**：前端用 `relations[].target_name` 与 `relations[].status`；本契约为 `relations[].name`，无 `status`（只返回 active 关系）。
7. **`present_npcs`**：前端期望 `goal_label`、`goal_pct`、`doing`；本契约有 `doing`、`presence`，没有 goal 字段。
8. **`location.description`**：前端硬编码“这里是世界的出生点与集市枢纽。”；本契约来自内容包，当前为 `""`。
9. **任务**：前端 `quests = {counts, rows}`；本契约为 `quest_board = {counts, rows, active}`。行字段差异：
   - 前端有 `hint`、`giver_location_name`、`target_name`、`target_location_name`；
   - 本契约有 `template`、`category`、`deadline_tick`、`acceptable`、`expired`、`can_accept_here`、`accepted_by_me`。
10. **`/api/player/quests`**：现有实现返回 `{ok,tick,location,open,active}`；本契约推荐用 `state.quest_board`，两者结构不同。
11. **日志**：前端期望 `log`（proposal/rejected 条目）；本契约不返回 `log`，对话回复走 `/api/player/messages`，动作结果走每次写响应与 `/intents/<id>`。
12. **时钟**：前端期望 `clock={tick,label,day,day_index}`；本契约只给 `tick`（整数）。前端需自行换算。
13. **账号格式**：前端传 `pid=player:u_demo`；本契约传 `account_id=u_demo`（不带 `player:` 前缀）。
14. **拒绝码**：前端已用的 `not_adjacent` / `unknown_location` / `not_together` / `not_at_giver` / `insufficient_stamina` / `quest_*` 与本契约**同名**；本契约另外补充 `trade_unfair`、`service_item`、`player_offline`、`target_offline`、`too_many_quests` 等。前端只需新增分支，不需要改名。

---

## 9. 安全与密钥

- API 响应不含任何上游模型密钥；`config/llm.yaml` 的 key 只从环境变量读取（`LLM_API_KEY` 等）。
- 真实调用样例中的 `account_id` 是演示账号，不涉及真实用户数据。

---

## 10. 真实调用样例（原样输出）

> 以下 JSON 来自真实启动的 `python3 -m wsim.api --run player-demo` 实例（seed=42，全部 NPC，tick_step=6），仅做格式化，未改字段。
> §10.1–§10.6 是同一实例的连续场景：`u_demo` 选捕快进入 → 与 `liu_sanniang` 说话 → 移动到 `inn` → 尝试移动到不存在的 `moon`（被拒）→ 查询 intent / messages。
> §10.7–§10.8 来自另一个真实实例（`--run player-endow-doc --seed-account alice`），专门验证运行中新账号的首次发放与重复进入。

### 10.1 `POST /api/player/enter`

```json
{
  "action": "enter",
  "data": {
    "active_quests": [],
    "active_quests_detail": [],
    "archetype": "捕快",
    "home": "market",
    "id": "player:u_demo",
    "inventory": {
      "coarse_cloth": 1,
      "flatbread": 3
    },
    "location": "market",
    "max_stamina": 100.0,
    "name": "陈捕快",
    "nearby": [
      {
        "id": "duan_wudou",
        "name": "段五斗",
        "presence": "online",
        "role": "屠户"
      },
      {
        "id": "jin_mancang",
        "name": "金满仓",
        "presence": "online",
        "role": "掌柜"
      },
      {
        "id": "liu_sanniang",
        "name": "柳三娘",
        "presence": "online",
        "role": "绣娘"
      },
      {
        "id": "luo_qingxu",
        "name": "罗轻絮",
        "presence": "online",
        "role": "绣娘"
      },
      {
        "id": "meng_jiuniang",
        "name": "孟九娘",
        "presence": "online",
        "role": "媒婆"
      },
      {
        "id": "ruan_qingfu",
        "name": "阮青蚨",
        "presence": "online",
        "role": "货郎"
      },
      {
        "id": "xing_pojun",
        "name": "邢破军",
        "presence": "online",
        "role": "捕快"
      }
    ],
    "ok": true,
    "online": true,
    "presence": "online",
    "relations": [],
    "role": "捕快",
    "skills": {
      "捕快": 0.35
    },
    "stamina": 100.0,
    "tick": 0,
    "wallet": 300
  },
  "details": {
    "created": false,
    "endowment": {
      "granted": false,
      "already_granted": true,
      "money": 300,
      "items": [
        {
          "item": "coarse_cloth",
          "name": "粗布（一匹）",
          "qty": 1
        },
        {
          "item": "flatbread",
          "name": "炊饼",
          "qty": 3
        }
      ],
      "money_source": "genesis_player_endowment",
      "item_source": "genesis_player_carry",
      "event_seq": 0,
      "endowed_tick": 0,
      "granted_at_genesis": true,
      "endowment_total": 300,
      "cap": 60000,
      "reject_reason": "",
      "reason_text": "",
      "note": "首次进入一次性发放；同账号重复进入不再发放。跨 run 重启是新世界，可重新领取。"
    },
    "resumed": true,
    "role": "捕快"
  },
  "event_seq": 4,
  "intent_id": "i:5865785407da1b07a3a7967130a468b7e29d0c0ad157b56e4f0ae9867171ed1b",
  "next_tick": 6,
  "ok": true,
  "player": "player:u_demo",
  "profile_version": 1,
  "reason_text": "",
  "reject_reason": "",
  "status": "executed",
  "tick": 0,
  "verb": "player_enter"
}
```

### 10.2 `GET /api/player/state`（进入后、说话前）

```json
{
  "account_id": "u_demo",
  "active_quests_detail": [],
  "location": {
    "description": "",
    "exits": [
      {
        "exists": true,
        "id": "academy",
        "name": "书院"
      },
      {
        "exists": true,
        "id": "inn",
        "name": "客栈"
      },
      {
        "exists": true,
        "id": "pharmacy",
        "name": "药铺"
      },
      {
        "exists": true,
        "id": "smithy",
        "name": "铁匠铺"
      },
      {
        "exists": true,
        "id": "teahouse",
        "name": "茶棚"
      }
    ],
    "id": "market",
    "name": "集市",
    "services": [
      "coarse_cloth",
      "flatbread",
      "noodle",
      "raw_silk",
      "rice"
    ]
  },
  "nearby": [
    {
      "doing": "",
      "id": "duan_wudou",
      "location": "market",
      "name": "段五斗",
      "presence": "online",
      "role": "屠户"
    },
    {
      "doing": "",
      "id": "jin_mancang",
      "location": "market",
      "name": "金满仓",
      "presence": "online",
      "role": "掌柜"
    },
    {
      "doing": "",
      "id": "liu_sanniang",
      "location": "market",
      "name": "柳三娘",
      "presence": "online",
      "role": "绣娘"
    },
    {
      "doing": "",
      "id": "luo_qingxu",
      "location": "market",
      "name": "罗轻絮",
      "presence": "online",
      "role": "绣娘"
    },
    {
      "doing": "",
      "id": "meng_jiuniang",
      "location": "market",
      "name": "孟九娘",
      "presence": "online",
      "role": "媒婆"
    },
    {
      "doing": "",
      "id": "ruan_qingfu",
      "location": "market",
      "name": "阮青蚨",
      "presence": "online",
      "role": "货郎"
    },
    {
      "doing": "",
      "id": "xing_pojun",
      "location": "market",
      "name": "邢破军",
      "presence": "online",
      "role": "捕快"
    }
  ],
  "ok": true,
  "online_players": [
    {
      "archetype": "捕快",
      "id": "player:u_demo",
      "location": "market",
      "name": "陈捕快",
      "presence": "online",
      "role": "捕快",
      "self": true
    }
  ],
  "player": {
    "account_id": "u_demo",
    "active_quests": [],
    "archetype": "捕快",
    "created_tick": 0,
    "entity_id": "player:u_demo",
    "home": "market",
    "last_intent_tick": 0,
    "location": "market",
    "location_name": "集市",
    "max_stamina": 100.0,
    "name": "陈捕快",
    "online": true,
    "presence": "online",
    "relations": [],
    "role": "捕快",
    "skills": {
      "捕快": 0.35
    },
    "stamina": 100.0,
    "wallet": {
      "items": [
        {
          "item": "coarse_cloth",
          "name": "粗布（一匹）",
          "qty": 1
        },
        {
          "item": "flatbread",
          "name": "炊饼",
          "qty": 3
        }
      ],
      "money": 300
    }
  },
  "present_npcs": [
    {
      "doing": "",
      "id": "duan_wudou",
      "location": "market",
      "name": "段五斗",
      "presence": "online",
      "role": "屠户"
    },
    {
      "doing": "",
      "id": "jin_mancang",
      "location": "market",
      "name": "金满仓",
      "presence": "online",
      "role": "掌柜"
    },
    {
      "doing": "",
      "id": "liu_sanniang",
      "location": "market",
      "name": "柳三娘",
      "presence": "online",
      "role": "绣娘"
    },
    {
      "doing": "",
      "id": "luo_qingxu",
      "location": "market",
      "name": "罗轻絮",
      "presence": "online",
      "role": "绣娘"
    },
    {
      "doing": "",
      "id": "meng_jiuniang",
      "location": "market",
      "name": "孟九娘",
      "presence": "online",
      "role": "媒婆"
    },
    {
      "doing": "",
      "id": "ruan_qingfu",
      "location": "market",
      "name": "阮青蚨",
      "presence": "online",
      "role": "货郎"
    },
    {
      "doing": "",
      "id": "xing_pojun",
      "location": "market",
      "name": "邢破军",
      "presence": "online",
      "role": "捕快"
    }
  ],
  "profile_version": 1,
  "quest_board": {
    "active": [],
    "counts": {
      "acceptable_here": 3,
      "accepted_by_me": 0,
      "open": 11
    },
    "rows": [
      {
        "acceptable": false,
        "accepted_by_me": false,
        "can_accept_here": false,
        "category": "commission",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "han_xinglin",
        "giver_location": "pharmacy",
        "giver_name": "韩杏林",
        "id": "quest:commission_herb_sorting:0",
        "name": "药铺分药",
        "reward_money": 14,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "pharmacy",
        "template": "commission_herb_sorting"
      },
      {
        "acceptable": false,
        "accepted_by_me": false,
        "can_accept_here": false,
        "category": "commission",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "shi_gandang",
        "giver_location": "smithy",
        "giver_name": "石敢当",
        "id": "quest:commission_stonework:0",
        "name": "铁匠铺代工",
        "reward_money": 12,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "smithy",
        "template": "commission_stonework"
      },
      {
        "acceptable": true,
        "accepted_by_me": false,
        "can_accept_here": true,
        "category": "debt_collection",
        "deadline_day": 4,
        "deadline_tick": 432,
        "expired": false,
        "giver": "jin_mancang",
        "giver_location": "market",
        "giver_name": "金满仓",
        "id": "quest:debt_duan_to_jin:0",
        "name": "代金满仓讨债",
        "reward_money": 25,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "market",
        "template": "debt_duan_to_jin"
      },
      {
        "acceptable": false,
        "accepted_by_me": false,
        "can_accept_here": false,
        "category": "debt_collection",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "song_henian",
        "giver_location": "inn",
        "giver_name": "宋鹤年",
        "id": "quest:debt_lu_to_song:0",
        "name": "代宋鹤年讨债",
        "reward_money": 8,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "inn",
        "template": "debt_lu_to_song"
      },
      {
        "acceptable": false,
        "accepted_by_me": false,
        "can_accept_here": false,
        "category": "errand",
        "deadline_day": 4,
        "deadline_tick": 432,
        "expired": false,
        "giver": "yan_shouyi",
        "giver_location": "temple",
        "giver_name": "严守一",
        "id": "quest:errand_academy_notice:0",
        "name": "送书院口信",
        "reward_money": 8,
        "reward_relation": 3.0,
        "status": "open",
        "target_location": "academy",
        "template": "errand_academy_notice"
      },
      {
        "acceptable": true,
        "accepted_by_me": false,
        "can_accept_here": true,
        "category": "errand",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "liu_sanniang",
        "giver_location": "market",
        "giver_name": "柳三娘",
        "id": "quest:errand_silk_sample:0",
        "name": "送一匹绸样",
        "reward_money": 8,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "pharmacy",
        "template": "errand_silk_sample"
      },
      {
        "acceptable": false,
        "accepted_by_me": false,
        "can_accept_here": false,
        "category": "escort",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "pei_wujiu",
        "giver_location": "escort_agency",
        "giver_name": "裴无咎",
        "id": "quest:escort_iron_short:0",
        "name": "短镖送铁器",
        "reward_money": 12,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "market",
        "template": "escort_iron_short"
      },
      {
        "acceptable": false,
        "accepted_by_me": false,
        "can_accept_here": false,
        "category": "inquiry",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "cao_banxian",
        "giver_location": "temple",
        "giver_name": "曹半仙",
        "id": "quest:inquiry_academy_lecture:0",
        "name": "打听书院讲期",
        "reward_money": 8,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "academy",
        "template": "inquiry_academy_lecture"
      },
      {
        "acceptable": true,
        "accepted_by_me": false,
        "can_accept_here": true,
        "category": "inquiry",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "meng_jiuniang",
        "giver_location": "market",
        "giver_name": "孟九娘",
        "id": "quest:inquiry_luo_marriage:0",
        "name": "打听罗家婚事",
        "reward_money": 8,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "market",
        "template": "inquiry_luo_marriage"
      },
      {
        "acceptable": false,
        "accepted_by_me": false,
        "can_accept_here": false,
        "category": "procurement",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "song_henian",
        "giver_location": "inn",
        "giver_name": "宋鹤年",
        "id": "quest:procure_charcoal:0",
        "name": "采购木炭",
        "reward_money": 10,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "ferry",
        "template": "procure_charcoal"
      },
      {
        "acceptable": false,
        "accepted_by_me": false,
        "can_accept_here": false,
        "category": "procurement",
        "deadline_day": 3,
        "deadline_tick": 288,
        "expired": false,
        "giver": "yan_shouyi",
        "giver_location": "temple",
        "giver_name": "严守一",
        "id": "quest:procure_mugwort:0",
        "name": "采购艾草",
        "reward_money": 20,
        "reward_relation": 2.0,
        "status": "open",
        "target_location": "pharmacy",
        "template": "procure_mugwort"
      }
    ]
  },
  "tick": 6
}
```

### 10.3 `POST /api/player/action`（talk，带 `details.reply`）

```json
{
  "action": "talk",
  "client_tick": 0,
  "client_tick_ignored": true,
  "confirm": false,
  "data": {
    "active_quests": [],
    "active_quests_detail": [],
    "archetype": "捕快",
    "home": "market",
    "id": "player:u_demo",
    "inventory": {
      "coarse_cloth": 1,
      "flatbread": 3
    },
    "location": "market",
    "max_stamina": 100.0,
    "name": "陈捕快",
    "nearby": [
      {
        "id": "duan_wudou",
        "name": "段五斗",
        "presence": "online",
        "role": "屠户"
      },
      {
        "id": "jin_mancang",
        "name": "金满仓",
        "presence": "online",
        "role": "掌柜"
      },
      {
        "id": "liu_sanniang",
        "name": "柳三娘",
        "presence": "online",
        "role": "绣娘"
      },
      {
        "id": "luo_qingxu",
        "name": "罗轻絮",
        "presence": "online",
        "role": "绣娘"
      },
      {
        "id": "meng_jiuniang",
        "name": "孟九娘",
        "presence": "online",
        "role": "媒婆"
      },
      {
        "id": "ruan_qingfu",
        "name": "阮青蚨",
        "presence": "online",
        "role": "货郎"
      },
      {
        "id": "xing_pojun",
        "name": "邢破军",
        "presence": "online",
        "role": "捕快"
      }
    ],
    "ok": true,
    "online": true,
    "presence": "online",
    "relations": [
      {
        "affection": 1.0,
        "kind": "acquaintance",
        "name": "柳三娘",
        "target": "liu_sanniang",
        "trust": 40.5
      }
    ],
    "role": "捕快",
    "skills": {
      "捕快": 0.35
    },
    "stamina": 100.0,
    "tick": 6,
    "wallet": 300
  },
  "details": {
    "relation": {
      "affection": 1.0,
      "kind": "acquaintance",
      "target": "liu_sanniang",
      "trust": 40.5
    },
    "reply": "柳三娘（绣娘）听了你的话，先递笑，再递茶，最后把账本摊开；若对方给足面子，她会让一步，甚至把六十文说成“先还四十，余下再说”。若有人当众揭她短，她不当场翻脸，只把绸价报高一成。真遇到罗轻絮手头断粮，她会嘴上骂“不会过日子”，转头让学徒送半匹粗布过去。若苏合香来赊药囊。\n「价钱好说呀，先看你这人诚不诚。」"
  },
  "event_seq": 6,
  "intent_id": "i:e9d657618b972c8629af27db42fdab2b1d69b085cd4e0d937d7601693801b05d",
  "next_tick": 12,
  "ok": true,
  "player": "player:u_demo",
  "profile_version": 1,
  "reason_text": "",
  "reject_reason": "",
  "status": "executed",
  "tick": 6,
  "verb": "talk"
}
```

### 10.4 `POST /api/player/action`（move 到不存在地点，可解释拒绝）

```json
{
  "action": "move",
  "client_tick": 12,
  "client_tick_ignored": true,
  "confirm": false,
  "data": {},
  "details": {},
  "event_seq": 10,
  "intent_id": "i:ae5fad82004fb21335b82d2370660add101455a577239cfb4559a5d31c15108b",
  "next_tick": 24,
  "ok": false,
  "player": "player:u_demo",
  "profile_version": 1,
  "reason_text": "地点不存在；请从当前地点可用的出口中选择。",
  "reject_reason": "unknown_location",
  "status": "rejected",
  "tick": 18,
  "verb": "move"
}
```

### 10.5 `GET /api/player/intents/req-talk-1`

```json
{
  "event_seq": 6,
  "found": true,
  "intent_id": "i:e9d657618b972c8629af27db42fdab2b1d69b085cd4e0d937d7601693801b05d",
  "ok": true,
  "outcome": "ok",
  "profile_version": 1,
  "reason_text": "",
  "reject_reason": "",
  "status": "executed",
  "tick": 6,
  "verb": "talk"
}
```

### 10.6 `GET /api/player/messages?since_tick=0`

```json
{
  "account_id": "u_demo",
  "messages": [
    {
      "from": "liu_sanniang",
      "id": "msg:6",
      "kind": "talk_reply",
      "seq": 6,
      "text": "柳三娘（绣娘）听了你的话，先递笑，再递茶，最后把账本摊开；若对方给足面子，她会让一步，甚至把六十文说成“先还四十，余下再说”。若有人当众揭她短，她不当场翻脸，只把绸价报高一成。真遇到罗轻絮手头断粮，她会嘴上骂“不会过日子”，转头让学徒送半匹粗布过去。若苏合香来赊药囊。\n「价钱好说呀，先看你这人诚不诚。」",
      "tick": 6,
      "to": "player:u_demo"
    }
  ],
  "next_since_tick": 24,
  "ok": true,
  "profile_version": 1,
  "since_tick": 0,
  "tick": 24
}
```

### 10.7 `POST /api/player/enter`（运行中新账号首次进入，真实输出）

> 实例：`PYTHONPATH=src python3 -m wsim.api --run player-endow-doc --port 8099 --seed-account alice`。
> 请求：`{"account_id":"u_newbie","display_name":"林小满","archetype":"商人","request_id":"req-enter-new-1"}`。
> 关键点：`event_seq=5` 是 enter 事件；`player_endowment` 是紧随其后的 seq 6；`system_tick` 是 seq 7，所以这一轮跨度是 `+3`。

```json
{
  "action": "enter",
  "data": {
    "active_quests": [],
    "active_quests_detail": [],
    "archetype": "商人",
    "home": "market",
    "id": "player:u_newbie",
    "inventory": {
      "coarse_cloth": 1,
      "flatbread": 3
    },
    "location": "market",
    "max_stamina": 100.0,
    "name": "林小满",
    "nearby": [
      {"id": "duan_wudou", "name": "段五斗", "presence": "online", "role": "屠户"},
      {"id": "jin_mancang", "name": "金满仓", "presence": "online", "role": "掌柜"},
      {"id": "liu_sanniang", "name": "柳三娘", "presence": "online", "role": "绣娘"},
      {"id": "luo_qingxu", "name": "罗轻絮", "presence": "online", "role": "绣娘"},
      {"id": "meng_jiuniang", "name": "孟九娘", "presence": "online", "role": "媒婆"},
      {"id": "player:alice", "name": "alice", "presence": "offline", "role": "visitor"},
      {"id": "player:u_demo", "name": "陈捕快", "presence": "offline", "role": "visitor"},
      {"id": "ruan_qingfu", "name": "阮青蚨", "presence": "online", "role": "货郎"},
      {"id": "xing_pojun", "name": "邢破军", "presence": "online", "role": "捕快"}
    ],
    "ok": true,
    "online": true,
    "presence": "online",
    "relations": [],
    "role": "掌柜",
    "skills": {"掌柜": 0.35},
    "stamina": 100.0,
    "tick": 0,
    "wallet": 300
  },
  "details": {
    "created": true,
    "endowment": {
      "granted": true,
      "already_granted": false,
      "money": 300,
      "items": [
        {"item": "coarse_cloth", "name": "粗布（一匹）", "qty": 1},
        {"item": "flatbread", "name": "炊饼", "qty": 3}
      ],
      "money_source": "player_endowment",
      "item_source": "player_endowment_carry",
      "event_seq": 6,
      "endowed_tick": 0,
      "granted_at_genesis": false,
      "endowment_total": 900,
      "cap": 60000,
      "reject_reason": "",
      "reason_text": "",
      "note": "首次进入一次性发放；同账号重复进入不再发放。跨 run 重启是新世界，可重新领取。"
    },
    "resumed": false,
    "role": "掌柜"
  },
  "event_seq": 5,
  "intent_id": "i:0bc3390a4098ecb21015a9ec69008866418a8f1f5537c1e509aa1941e16bbde1",
  "next_tick": 6,
  "ok": true,
  "player": "player:u_newbie",
  "profile_version": 1,
  "reason_text": "",
  "reject_reason": "",
  "status": "executed",
  "tick": 0,
  "verb": "player_enter"
}
```

### 10.8 同账号二次 enter（`already_online`，不重发；真实输出关键字段）

> 同一实例，请求 `request_id=req-enter-new-2`。enter 阶段即被拒，不会进入 `player_endowment`。
> 上一轮 enter(5) + endowment(6) + system_tick(7)，所以这次 reject 落在 seq 8，`+3` 后恢复 `+2`。

```json
{
  "ok": false,
  "status": "rejected",
  "reject_reason": "already_online",
  "reason_text": "你已经在线，无需重复进入。",
  "event_seq": 8,
  "tick": 6,
  "next_tick": 12,
  "verb": "player_enter"
}
```

`GET /state` 同步确认金额未变：

```json
{
  "endowment_total": 900,
  "player": {
    "endowed": true,
    "endowed_tick": 0,
    "wallet": {
      "items": [
        {"item": "coarse_cloth", "name": "粗布（一匹）", "qty": 1},
        {"item": "flatbread", "name": "炊饼", "qty": 3}
      ],
      "money": 300
    }
  }
}
```

---

## 11. CHANGELOG

| 版本 | 日期 | 变更 | 影响 |
|---|---|---|---|
| `1` | 2026-11-04 | 首次冻结：`/enter`、`/state`、`/action`、`/intents/<id>`、`/messages` 字段与拒绝码 | 前端按此实现；后端不得随意改名 |
| `1.1` | 2026-11-04 | **P0 修复：运行中新账号首次进入一次性发放**。新增独立事件 `player_endowment`（显式 mint + 造物，计入 `minted`，`reason=first_entry`）；`details.endowment` 新增 `granted` / `already_granted` / `event_seq` / `endowed_tick` / `endowment_total` / `cap` / `reject_reason` / `reason_text`；`granted_at_genesis` **语义修正**为“来源是否创世预置”；`/state` 新增顶层 `endowment_total` 与 `player.endowed` / `player.endowed_tick`；新增拒绝码 `player_cap_reached` / `endowment_already_granted` / `endowment_cap_reached` / `account_missing`；补充 `event_seq +2` 与首次进入 `+3` 的说明 | 前端：新增字段可忽略；`granted_at_genesis` 判断需改为读 `granted`；后端：`player_enter` 仍不铸币，发放由独立事件完成 |
| `1.2` | 2026-11-06 | 新增 §13「门派动作设计」：冻结 `found_sect` / `recruit_disciple` / `expel_disciple` / `pay_disciples` / `sect_deposit` / `sect_withdraw` 的契约、门槛、拒绝码与钱流（当时未接线，已由 `1.3` 取代） | 历史记录：当时动作层已实现但调用仍被拒 |
| `1.3` | 2026-11-06 | **门派动作接线开放**：六个写动词进白名单/别名/`_args`/`PLAYER_VERBS`，`found_sect` 不再返回 `not_implemented_for_player`；新增私有 `player.sect`（§3.3）与 `details.sect`/`details.settlement`；拒绝码统一为 `no_sect` / `already_in_sect` / `not_sect_leader` / `sect_full` / `insufficient_rep` / `insufficient_funds` / `disciple_not_found` / `favor_too_low` / `trust_too_low` / `arrears_too_high`，全部并入 §7；§13 改为真实契约 | 前端可接入口；匿名状态仍无门派私有字段；NPC/无玩家世界逐字节不变 |
| `1.4` | 2026-11-06 | **门派面板缺口补齐**：`disciples[]` 增 `affection` / `trust`（与收徒裁决同一 `_bond`）与逐人 `settled_today`；新增只读 `GET /api/player/sects`（§3.4，安全字段 + 匿名可读 + 固定排序） | 只加字段/新 handler，既有视图字段不变；无玩家 2 日 golden 逐字节不变 |

---

## 12. 仍未开放（前端不要实现入口）

- 不可逆动作：伤害、抢掠、强迫婚配（门派内 `expel_disciple` 已开放，见 §13）。
- 玩家间 PvP 交易/抢劫。
- 离线托管（custody）与 dormant 的托管代理操作。
- 异步意图队列（`accepted` 分支保留，但当前不会返回）。
- 对话文笔质量未评测；`dialogue` 槽只按延迟与落单准确选型。

---

## 13. 门派动作（2026-11-06 已开放）

> **状态：已接线**。六个写动词进入 `WRITE_ACTIONS` / `ACTION_ALIASES` / `_args` /
> `PLAYER_VERBS`，且只在玩家 API 会话注册；`found_sect` 对非玩家 actor 原样委托 NPC
> 动作，因此无玩家世界与 NPC 行为逐字节不变。参数与拒绝码见 §4.2.3，详细设计见
> `specs/player-sect/spec.md`，接线清单见 `specs/player-sect/checklist.md`。

### 13.1 动词与参数

| 动词 | 参数 | 说明 |
|---|---|---|
| `found_sect` | `name`、`at?`、`doctrine?`、`sect_id?` | 立派；`at` 缺省为当前所在地 |
| `recruit_disciple` | `target`、`sect_id?` | 收同地点 NPC 为徒 |
| `expel_disciple` | `target`、`sect_id?` | 掌门逐出弟子 |
| `pay_disciples` | `sect_id?`、`day?` | 门派日结（收会费 / 发俸禄 / 欠饷判定） |
| `sect_deposit` | `amount`、`sect_id?` | 掌门向金库存钱 |
| `sect_withdraw` | `amount`、`sect_id?` | 掌门从金库提钱 |

`sect_id` 可省略，缺省取 `entity.faction`；传 `"sect:xxx"` 或 `"xxx"` 均可。

### 13.2 门槛（玩家口径，与 NPC 的 2500 文 / 声望 40 / 5 人不同）

- 立派合计 **800 文**：注册费 200 文（`transfer` 到 `treasury:region`）+ 启动金
  600 文（`transfer` 到 `treasury:sect:<sid>`）；声望 **≥ 20**；**单人可立派**。
- 收徒：好感 ≥ 30、信任 ≥ 30、目标技能 ≥ 0.2、掌门技能 ≥ 0.4、掌门声望 ≥ 20，
  掌门与目标同地点，草创期弟子上限 **3**。
- 日结：每徒每日会费 **5 文**（弟子 → 金库）、俸禄 **10 文**（金库 → 弟子）；
  欠饷累计 ≥ **30 文** 当日离派（离派不清债）。
- 提取：单次 ≤ **500 文**，提取后金库须留 **100 文**。

### 13.3 拒绝码

门派专属（全部已补 `reason_text`，见 §7）：

`no_sect`、`already_in_sect`、`not_sect_leader`、`sect_full`、`insufficient_rep`、
`insufficient_funds`、`disciple_not_found`、`favor_too_low`、`trust_too_low`、
`arrears_too_high`（离派原因，出现在 `settlement.leavers[].reason`）。

沿用：`sect_not_found`、`sect_name_taken`、`sect_exists`、`invalid_sect_name`、
`wrong_location`、`target_not_found`、`target_not_alive`、`target_is_player`、
`not_together`、`self_recruit`、`target_in_sect`、`already_disciple`、`skill_too_low`、
`target_not_disciple`、`no_disciples`、`already_settled_today`、`insufficient_treasury`、
`withdraw_over_cap`、`treasury_reserve`、`unknown_location`、`bad_arg:sect_id`、
`bad_arg:amount`、`bad_arg:name`、`bad_arg:day`。

### 13.4 事件与钱流

所有金额只走真实 `transfer` / `debt_*`，不 `mint`、不 `burn`：

| `reason` | 方向 |
|---|---|
| `sect_registration_fee` | 掌门钱包 → 官仓 |
| `sect_endowment` | 掌门钱包 → 门派金库 |
| `sect_dues` | 弟子钱包 → 门派金库 |
| `sect_stipend` | 门派金库 → 弟子钱包 |
| `sect_deposit` / `sect_withdraw` | 掌门钱包 ↔ 门派金库 |

欠付写 `Debt`：`unpaid_dues`（弟子欠门派）、`unpaid_stipend`（门派欠弟子）。
日结响应带 `details.settlement`（`dues`/`stipend`/`leavers[]`），数值直接取自
本次事件里的 `transfer` 与 `set_field`，不是估算。

### 13.5 私有状态与公开边界

- 本人令牌的 `GET /api/player/state` 返回 `player.sect` / `sect`（§3.3）：门主、名册、
  金库余额、欠饷、日结上限与 `settled_today`。
- 匿名只看到顶层 `sect_name`；`/api/world/feed` 只收 NPC 事件，玩家门派事件不进公共流。
- 写端点必须本人令牌，跨账号 403 `auth_account_mismatch`。

### 13.6 验收事件链

`found_sect` → `recruit_disciple` ×3 → `sect_deposit` / `sect_withdraw` →
`pay_disciples`（每日一次）→ 欠饷 30 触发 `debt_create` / `debt_increase` →
`set_field faction=null` + 双向关系 `status=ended` + 记忆「欠饷 30 文离派」→
`expel_disciple`。整链 `minted_total` / `burned_total` 不变，`reconciliation.delta = 0`。

## 14. 任务过程化（2026-11-07，只增不改）

### 14.1 任务行新增字段

`GET /api/player/state` 的 `quest_board.rows[]/locked[]/active[]`、
`GET /api/player/quests` 的 `open[]/locked[]/active[]`、以及
`active_quests_detail[]` 每个任务行新增：

| 字段 | 类型 | 说明 |
|---|---|---|
| `target_npc` | string | 目标人物 id；无目标人物时为 `""` |
| `target_npc_name` | string | 目标人物显示名 |
| `required_action` | object | 目标动作：`verb`、`params`(模板 intent_args)、`target_npc(_name)`、`target_location(_name)`、`hours`；采购单另有 `target_item(_name)` / `qty` |
| `progress` | object | `done`（是否达到完成门槛）、`units`（已完成单位）、`required`（门槛）、`verb`、`tick`、`note`（中文进度说明） |

单位口径：`work` 累计工时；`buy` 累计购入件数；`trade` / `escort` 一次即 1。
`progress.units` 只来自世界状态里的 `quest_step` 事件，可回放，不用进程内变量。

### 14.2 交付校验

玩家接单后，`quest_deliver` 先校验地点/物品（原有），再校验目标动作：

- `work`：在 `target_location` 实际累计上工达到模板 `hours`；
- `buy`：实际买入 `target_item` 达到模板 `qty`；
- `trade`：在 `target_location` 与 `target_npc` 当面完成一次交易（讨债单还要求
  `money_delta` 与模板一致）；
- `escort`：镖单成功送达 `target_location`（失败不算）；
- 未达到时返回 `ok:false`、`reason:"quest_step_incomplete"` 与中文 `reason_text`，
  不会扣除物品/发放报酬。

接单本身会为玩家写一条 `quest_step(units=0)` 事件，因此进度从接单起就可见。

### 14.3 被挡住的任务 `locked[]`

`quest_board.locked[]` 与 `/api/player/quests` 的 `locked[]` 保留因以下原因不可接的单，
并附 `lock_reason` / `lock_text`，不再从列表消失：

| `lock_reason` | 含义 |
|---|---|
| `quest_expired` | 已过截止 tick（日界清理前的窗口） |
| `giver_missing` | 发布人不存在或已死亡 |
| `reputation_too_low` | 玩家声望低于模板 `require_reputation` |

`locked[]` 行与 `rows[]` 行结构一致，`acceptable=false`、`can_accept_here=false`。
`counts` 口径不变：`open` 只统计未锁定的可展示单。

## 15. 世界地图（2026-11-07，只读公共端点）

`GET /api/world/map`：匿名可读，不带令牌；不写事件、不改状态、不参与任何哈希。
数据直接来自世界状态 `locations`（genesis 从 `locations.yaml` 及内容包复制），
边由每个地点的 `exits` 推出，可达性逐对复用 `path_between`，不做任何接口侧补边。

### 15.1 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | 固定 `true` |
| `tick` | int | 当前世界钟 |
| `run` | string | 运行 id |
| `counts.locations` | int | 地点数 |
| `counts.edges` | int | 无向边数（每条真实邻接只算一次） |
| `counts.directed_edges` | int | 有向可走次数（双向边贡献 2） |
| `region_supported` | bool | 内容是否提供区域字段 |
| `region_note` | string | 区域缺失时的如实说明 |
| `nodes[]` | array | `id` / `name` / `region`(可能为 null) / `region_name` / `exits` / `neighbors` |
| `edges[]` | array | `from` / `to` / `from_name` / `to_name` / `direction`(无方位数据时为 null) / `walkable` / `walkable_a_to_b` / `walkable_b_to_a` / `bidirectional` |
| `reachable` | object | 每个地点经 `path_between` 实际可达的其他地点 id 列表 |

不含价格、库存、NPC、任务等敏感或动态字段。

### 15.2 当前样例（2026-11-07，tick 2430）

```json
{
  "ok": true,
  "counts": {"locations": 9, "edges": 11, "directed_edges": 22},
  "region_supported": false,
  "region_note": "locations.yaml 未提供区域字段；所有地点 region/region_name 如实为 null，未编造。",
  "nodes": [
    {"id": "market", "name": "集市", "region": null, "region_name": null,
     "exits": ["academy","inn","pharmacy","smithy","teahouse"],
     "neighbors": ["academy","inn","pharmacy","smithy","teahouse"]}
  ],
  "edges": [
    {"from": "academy", "to": "market", "from_name": "书院", "to_name": "集市",
     "direction": null, "walkable": true, "walkable_a_to_b": true,
     "walkable_b_to_a": true, "bidirectional": true}
  ]
}
```

区域名缺口：当前 9 个地点（academy/escort_agency/ferry/inn/market/pharmacy/smithy/
teahouse/temple）全部没有区域字段，均返回 `region: null`。


