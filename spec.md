# Spec · delivery-p3e（玩家系统垂直切片 + 关闭思考链）

> 目标：一个真人能玩十分钟的主线；玩家所有写操作走 `propose_intent → MCP → Writer` 单写者。
> 约束：不改 NPC 行为与经济基线；不开放伤害/抢掠/强迫婚配；旧 run 哈希不变；守恒误差 0、replay MATCH。
> 参照：`docs/player-system.md`（P3 规格）；本文件只覆盖 P3-E 垂直切片，不实现完整 8 人周。

## 0. 验收命令（最终在 tag 干净 worktree 执行）

```bash
.venv/bin/python -m pytest -q
.venv/bin/python tools/framework_check.py            # 6/6 PASS
.venv/bin/python tools/playthrough.py --run-id p3e-playthrough
.venv/bin/python tools/replay.py --run runs/p3e-playthrough
```

硬门槛：守恒误差 0；replay MATCH；事件 seq 从 1 连续；每次拒绝都有原因码；叙事文本落入报告。

## 1. E1 玩家实体

### 1.1 `src/wsim/world/state.py`

新增 `PlayerProfile`（放在 `Entity` 之后）：

```python
@dataclass
class PlayerProfile:
    id: str                 # player:<account_id>
    name: str
    archetype: str          # 游侠/商人/捕快
    role: str               # 镖师/掌柜/捕快
    online: bool = False
    presence: str = "offline"   # online/offline/custody/dormant
    created_tick: int = 0
    last_intent_tick: int = 0
    offline_since_tick: int = 0
    custody_since_tick: int = 0
    dormant_since_tick: int = 0
```

- `WorldState.players: dict[str, PlayerProfile] = field(default_factory=dict)`。
- `clone()` 深拷贝；`to_dict()` **仅当 players 非空**时输出 `"players"`（排序键），保证旧 run 哈希不变。
- 关系表沿用全局 `relations`（`a|b|kind`），不重复存储。
- 身份映射常量放在 `actions/player.py`：`ARCHETYPES={"游侠":"镖师","商人":"掌柜","捕快":"捕快"}`；技能名 = role，初始 0.35。

### 1.2 `src/wsim/world/effects.py`

`EFFECT_OPS` 增加 `player_upsert`、`player_presence`，并实现：

- `player_upsert`：`{id, name, archetype, role, created_tick}`；id 必须以 `player:` 开头；已存在 profile 时 `archetype/role` 不可改（冲突抛 `EffectError`）；只创建/更新档案，不碰钱与物品。
- `player_presence`：`{id, presence, tick}`；presence ∈ online/offline/custody/dormant；同步实体 `presence`/`last_seen_tick`；实体不存在则报错（enter 先 create_entity）。
- 不新增 mint/removal 类 op。初始资产只在 genesis 事件里用既有 `mint/item_create/create_account`。

### 1.3 `src/wsim/actions/player.py`

- 保留并修正 `_player_id`：只接受 `args.player_id` 或 actor 的 `player:` 命名空间；新增 `_account_id` 正则 `[a-z0-9_-]{3,32}`。
- `PlayerEnterAction`（verb `player_enter`）：
  - 前置：account_id 合法；`display_name` 2-12 字且不与现有实体重名；`archetype` ∈ 映射；`at`（缺省 market，否则排序第一个地点）。
  - 已有 profile 且 archetype 不同 → `identity_locked`；已 online → `already_online`。
  - apply：无实体则 `create_entity`（role=映射值、home=at、stamina 100）+ `create_account(wallet,0)` + `skill(role,+0.35)` + `player_upsert`；有实体则复用，不改 role；最后 `player_presence online` + `last_action_tick`。**绝不 mint/item_create**。
- `PlayerHeartbeatAction`（verb `player_heartbeat`）：profile 与实体必须存在且 online；距 `last_intent_tick < 12` → `heartbeat_too_soon`；apply 更新 `last_intent_tick`/`last_seen_tick`。
- `PlayerLeaveAction`（verb `player_leave`）：置 offline、`offline_since_tick`、实体 presence=offline；未知玩家 `player_not_entered`。
- `SystemPlayerPresenceAction`（verb `system_player_presence`）：`days = (tick-last_intent_tick)//144`；`>=1` offline，`>=3` custody，`>=7` dormant；只降不升；在线且未超时不动。NPC 动作不得主动影响离线玩家（见 1.4）。
- `SystemTickAction`（verb `system_tick`）：返回空 Effect，仅用于 API 会话推进世界钟；source=system，不进入 K3。
- `player_genesis_effects(pid, name, at, money=300, items={"flatbread":3,"coarse_cloth":1})`：给 `sim`/`tools` 在**创世 bootstrap**里使用；产出 `create_entity(role="visitor")`、`create_account`、`mint(genesis_player_endowment)`、`item_create`、`player_upsert(archetype="", role="visitor")`、`player_endow`、`player_presence offline`。
- `player_endowment`（运行中新账号首次 enter）：独立事件，显式 `mint(source="player_endowment", reason="first_entry")` + `item_create(source="player_endowment_carry")` + `player_endow`；`PlayerProfile.endowed` 幂等，计入 `endowment_total`，K6 守恒仍为 0。
- `player_status(state, pid)`：纯读，供 API 返回钱包/物品/位置/技能/关系/在线状态/进行中任务。

### 1.4 既有动作的最小改动

- `actions/registry.py`：注册 `PlayerHeartbeatAction`、`SystemPlayerPresenceAction`、`SystemTickAction`。
- `actions/social.py`：
  - `TalkAction.preconditions` 增加：speaker 必须有实体且 `presence=="online"`（否则 `player_offline`）；target 必须同地点（`not_together`）；`player_visitor` 以外不再懒创建（`player_not_entered`）。
  - apply 保留 spoofed_arg 审计与 ±2/tick、±8/日 clamp；删除 `presence` 强制 online 的副作用（在线校验已在前置）。
  - `ApprenticeAction`/`MarryAction`/`RecruitAction`：目标为玩家且离线 → `target_offline`；actor 为 NPC 且 target 为玩家时，`marry/recruit` 一律 `player_consent_required`（P3 关闭 NPC→玩家）。
- `actions/commerce.py` `TradeAction`：
  - actor/target 为玩家且非 online → `player_offline`。
  - actor=玩家、target=NPC 时公平闸门：`value(offer)+max(0,-money_delta) >= ratio*(value(want)+max(0,money_delta))`，ratio=0.70（affection<60）/0.50（>=60），失败 `trade_unfair`；`value` 用 `content.item_price`，`service_*` 物品拒 `service_item`。
- `actions/quests.py` `QuestAcceptAction`：actor 为玩家时统计 `status=="accepted" and acceptor==actor`，`>=2` → `too_many_quests`。
- `world/writer.py`：不改；玩家 tick 校验（stale_tick）已是 E1 要求，补测试固定行为。

## 2. E2 HTTP API

### 2.1 `src/wsim/api/session.py`

`WorldSession`：

- 构造：`load_content(root, allow_fixture=False)`；`npc_limit` 默认 0（全量）；`WorldState(locations)`；`EventStore(run_dir/events.sqlite)`；`build_registry`；`Writer`；`build_genesis` bootstrap tick 0；再 bootstrap `player_genesis_effects("player:u_demo","陈捕快","market")` verb=`genesis_player`；`writer.submit(system_quest_post, tick=0)`；写 meta content_hash/seed。
- `identities = {"player:<id>":"propose_intent", "ops:request":"admin_apply", "ops:approve":"admin_apply"}`；`MCPGateway` 挂 audit `mcp_audit.jsonl`。
- `perform(account_id, action, payload, request_id)`：
  1. 校验 account_id；忽略客户端 actor/tick；`tick = state.tick`。
  2. request_id 命中缓存直接返回原响应（幂等）。
  3. verb 白名单：enter/heartbeat/leave/move/talk/trade/quest_accept/quest_deliver；role 白名单查 `content.allowed_roles(verb)`。
  4. `Intent(actor=pid, verb, args, tick, source="player")`。
  5. `gateway.call(pid,"world.propose_intent",{...})`；再 `ops:request admin.request` → `ops:approve admin.approve` → 同身份 `admin.apply`（满足“两个人”：申请人与批准人不同）。
  6. 取 Writer 事件 seq/outcome/reason；成功后 `writer.submit(system_tick, tick+6, source=system)`。
  7. 返回 `{ok, reject_reason, reason_text, event_seq, tick, verb, request_id, data}`。
- 动作参数严格白名单：move `{to}`；talk `{target,message}`（客户端 aff/trust/reply 可传入但被 TalkAction 审计并忽略）；trade `{target,offer,want,money_delta}`；quest `{quest}`；enter `{display_name,archetype}`；heartbeat/leave `{}`。
- 拒绝原因码映射表 `REASON_TEXT`（中文可读），未知码显示码本身。

### 2.2 `src/wsim/api/server.py`

标准库 `ThreadingHTTPServer` + `BaseHTTPRequestHandler`，只 JSON，不开页面：

| 方法 | 路径 | 入参 | 说明 |
|---|---|---|---|
| POST | `/api/player/enter` | account_id/display_name/archetype | 首次创建或 resume |
| GET | `/api/player/status` | `?account_id=` | 档案/钱包/物品/位置/技能/关系/在线/进行中任务/附近 NPC |
| POST | `/api/player/move` | account_id/to | `move` 事件（source=player） |
| POST | `/api/player/talk` | account_id/target/message | `talk` |
| POST | `/api/player/trade` | account_id/target/offer/want/money_delta | `trade`（补充端点） |
| GET | `/api/player/quests` | `?account_id=` | open 任务 + 发布人地点/目标地点/奖励/可接性 |
| POST | `/api/player/quest/accept` | account_id/quest | 同地点接单 |
| POST | `/api/player/quest/deliver` | account_id/quest | 到目标/发布人交付 |
| POST | `/api/player/leave` | account_id | 下线 |
| POST | `/api/player/heartbeat` | account_id | 心跳 |

- 所有响应 HTTP 200（业务失败也 200）；结构 `{ok:false, reject_reason, reason_text, event_seq, tick}`。
- 未知路径 404 JSON；坏 JSON `bad_json` 且不写事件。
- `run(session, host="127.0.0.1", port=0)` 返回 server + 实际端口。
- `src/wsim/api/__init__.py` 导出 `WorldSession`、`run`。

### 2.3 `docs/player-api.md`

必写：每个端点的字段名、类型、示例请求、成功/拒绝响应、错误码表（至少覆盖 §1-§2 全部 reason）、seq/tick 语义、request_id 幂等、客户端不得传 actor/tick、talk 的三个伪造字段会被忽略、以及 P3 不开放的 L3 动作。

## 3. E3 `tools/playthrough.py`

- 启动 `WorldSession(run_id="p3e-playthrough")` + 后台 `serve`（ephemeral port）。
- 只用 `urllib.request` 调 API；移动路径用 `content.locations` 做 BFS（只读规划，不直接写状态）。
- 固定剧本：
  1. `enter` archetype=捕快 → 记录钱包/位置。
  2. 移动到 3 个不同地点（含至少一次 2 跳），每次打印叙事。
  3. 与 3 个不同 NPC `talk`；第 3 次故意挑一个不在当前地点的 NPC，断言 `ok=false` 且 `reject_reason=="not_together"`，再补一次成功对话凑满 3 人。
  4. `quests` 选 1 条可完成（优先 category=errand，其次 procurement 且能买到），`quest/accept` 成功后移动到目标，`quest/deliver`。
  5. 对比钱包增量与发布人关系（friend/acquaintance affection/trust）。
  6. `leave` → `enter`（同 account/archetype）→ `status` 确认位置/钱包/关系/任务状态仍在。
  7. 每个响应记录 seq/tick；断言 seq 严格连续、拒绝都有 reason。
- 结束：关服务器；独立 replay（仅 `outcome=="ok"` 的 effects）→ `conservation_error`、hash 对比、`seq` 连续；写 `runs/p3e-playthrough/narrative.txt`、`report.json`（含所有响应）。
- 控制台/报告输出完整叙事（中文第一人称，含金额与关系数值）。
- 若任务板无可用任务，脚本 MUST FAIL（不允许伪造任务）。

## 4. LLM 追加项：关闭思考链 + 最低档模型

### 4.1 `config/llm.yaml`

每个槽（decision/dialogue/intent）新增：

```yaml
extra_body:
  - {"reasoning_effort": "none"}
  - {"enable_thinking": false}
  - {"thinking": {"type": "disabled"}}
```

- `decision`/`intent` 的 `model` 改为 `/models` 中最低价可用档；若选型未到，先用 `group/all` 最便宜档并在 `model_note: "待选型替换"` 标注。
- 解析：`config` 读取支持 list；缺省空列表 = 不改请求体。

### 4.2 `src/wsim/agent/provider.py`

- `ProviderConfig` 增 `extra_body: list[dict]`，校验必须是 dict 列表。
- 请求体：`_build_body`（约 L262）先取候选 0 的键值合并；HTTP 400/422 且错误文本含未知字段 → 换下一候选重试（不计入普通重试）；成功记录候选序号。
- `Completion` 增 `thinking_disabled_by: str`（`"reasoning_effort=none"` / `"enable_thinking=false"` / `"thinking.type=disabled"` / `"none"`）。
- `complete_many` 结果字典增同字段；`ProviderClient.last_thinking_disabled_by`。
- **不把“发送了参数”当成“已生效”**：生效判定只由 `tools/thinking_probe.py` 的 token/延迟对照给出。

### 4.3 `src/wsim/agent/cost.py`

- `record()` 增 `thinking_disabled_by: str = ""`，写进 `cost.jsonl` 每条记录。
- 槽汇总增加 `thinking_disabled_by` 集合与计数；不影响既有字段。

### 4.4 `tools/thinking_probe.py`

- 参数：`--base-url/--api-key/--model/--prompt/--max-tokens 1500/--out`。
- 同一 prompt 跑基线（无 extra_body）与三候选（各自单独请求）；记录 `latency_s`、`usage`、`content`、`json_valid`、HTTP 错误。
- 输出 JSON + 控制台对照表（p50/p95 只对多次采样有意义；单次给实测值并注明）。
- 判定：`completion_tokens` 相对基线下降且内容非空/JSON 合法 → `effective`；否则 `ineffective`；上游拒绝 → `rejected`；不可达 → `unreachable`。报告如实写。

### 4.5 测试

`tests/test_p3e_llm_extra_body.py`：假端点接收 body，断言候选 0 生效并记录 `thinking_disabled_by`；候选 0 返回 400 unknown field 时回退候选 1；全部被拒时 body 不含候选且字段为 `none`；cost.jsonl 含字段。

## 5. 测试清单

- `tests/test_p3e_player.py`：
  - enter 幂等 resume/identity_locked/离线资产不增；
  - heartbeat 频率、leave 状态、离线状态机 1/3/7 日边界；
  - talk 同地点拒绝、离线拒绝、懒创建关闭、spoofed_arg；
  - trade 公平闸门（1 烧饼换名剑被拒）与离线守卫；
  - quest 并发上限；
  - 旧 run（无 players）哈希不变（players 空不写 to_dict）。
- `tests/test_p3e_api.py`：全端点成功/拒绝 JSON、错误码、seq/tick、request_id 幂等、客户端 actor/tick 被忽略。
- `tests/test_talk_action.py`：为 u_1001/u_2002 补 enter 前置；visitor 旧用例保留。
- 回归：`test_p3c_player.py`、`test_p3d_*` 全过。

## 6. 报告与冻结

`reports/p3e-report.md` 必含：玩家字段与事件表、API 契约摘要、**真实 playthrough 叙事全文**、守恒 0 与 replay MATCH 证据、seq 连续性、拒绝原因码清单、LLM 开关前后对照（延迟/completion_tokens/JSON 合法率/grounding 拦截）、模型能否承担意图层的判断、仍未开放部分。

冻结：`git add` 仅 P3-E 相关文件（不含 `client/`）→ commit → `git tag -a delivery-p3e` → `git worktree add` 到临时目录 → 从 tag 重跑 pytest/framework/playthrough/replay → 回填报告。
