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
    "active_quests": []
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

不可逆动词（`rob`、`attack`、`kill`、`marry_force`、`player_expel`、`item_create`、`mint`、`set_field`）**不在本轮开放**；命中返回 `forbidden_verb` 并附人话原因。

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

## 7. 错误码表（拒绝原因枚举）

> 前端必须优先展示 `reason_text`，`reject_reason` 只用于分支判断。
> 前端已使用的 `not_adjacent` / `unknown_location` / `not_together` / `not_at_giver` / `insufficient_stamina` / `quest_*` 全部保持同名。

| `reject_reason` | 人话原因（`reason_text`） |
|---|---|
| `missing_account` | 缺少账号；请先创建账号再进入世界。 |
| `bad_account` | 账号格式不合法（3-32 位小写字母、数字、下划线或连字符）。 |
| `unknown_action` | 未知操作；可用操作见 docs/player-api.md。 |
| `forbidden_verb` | 该操作在本阶段不开放（伤害/抢掠/强迫婚配等不可逆动作被禁用）。 |
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

---

## 12. 仍未开放（前端不要实现入口）

- 不可逆动作：伤害、抢掠、强迫婚配、驱逐。
- 玩家间 PvP 交易/抢劫。
- 离线托管（custody）与 dormant 的托管代理操作。
- 异步意图队列（`accepted` 分支保留，但当前不会返回）。
- 对话文笔质量未评测；`dialogue` 槽只按延迟与落单准确选型。
