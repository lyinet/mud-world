# NPC 对话同质化：证据、根因、修复与回归

- run：`player-demo-20261107-002916-86448`（API `8031` / UI `8000`）
- 真实账号：`qa_dialogue_01`（修复前）、`qa_dialogue_03`（修复后-模板）、`qa_dialogue_04`（修复后-模型）
- 工具：[`tools/dialogue_probe.py`](../tools/dialogue_probe.py)
- 原始请求/响应：[before](../reports/npc-dialogue-variety-before.json)、[after-template](../reports/npc-dialogue-variety-after-template.json)、[after-model](../reports/npc-dialogue-variety-after-model.json)

## 1. 修复前：真实证据

### 1.1 能力位

- 管理面 `GET 8032 /api/admin/providers`：`capabilities.dialogue.status="unconfigured"`，`providers=0`。
- API 进程没有 `WSIM_DIALOGUE`，`LLM_API_KEY=sk-your-key-here` 为占位值。
- run 无 `dialogue.jsonl`，`llm_calls=0`。
- 结论：**修复前 100% 走确定性模板 `compose_talk`，模型路径从未执行**。

### 1.2 5 NPC × 3 轮（修复前原文）

| NPC | 轮次 | 请求 message | 返回 `details.reply` |
|---|---|---|---|
| duan_wudou | 1 | 你好，今日可好？ | 我在呢，屠户这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| duan_wudou | 2 | 幸会，近来可好？ | 我在呢，屠户这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| duan_wudou | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。 |
| jin_mancang | 1 | 你好，今日可好？ | 我在呢，掌柜这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| jin_mancang | 2 | 幸会，近来可好？ | 我在呢，掌柜这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| jin_mancang | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。 |
| liu_sanniang | 1 | 你好，今日可好？ | 我在呢，绣娘这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| liu_sanniang | 2 | 幸会，近来可好？ | 我在呢，绣娘这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| liu_sanniang | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。 |
| meng_jiuniang | 1 | 你好，今日可好？ | 我在呢，媒婆这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| meng_jiuniang | 2 | 幸会，近来可好？ | 我在呢，媒婆这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| meng_jiuniang | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。 |
| ruan_qingfu | 1 | 你好，今日可好？ | 我在呢，货郎这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| ruan_qingfu | 2 | 幸会，近来可好？ | 我在呢，货郎这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。 |
| ruan_qingfu | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。 |

基线指标：`unique=6/15`、同 NPC 相邻轮相同 `5` 对、同意图跨 NPC 完全相同 `10` 对、同意图 LCS `26/29` 字。

## 2. 根因（行号为修复后）

1. **turn 从未传入、从未落库**：[social.py:136](../src/wsim/actions/social.py#L136) 修复前调用 `compose_talk(...)` 只传 `tick`；`turn` 默认 0，`dialogue.py` 的 `turn % 2` 永远命中 `.a`。
2. **模板只按意图选**：`TEMPLATES` 是全局共享骨架，不同 NPC 只替换 `{role}/{place}`；gossip 等骨架不含角色，直接全体相同。
3. **人设台词只在 deflection 用**：修复前 `quote = _pick_quote(...) if intent == "deflection" else ""`，其余 8 类意图丢弃人设台词。
4. **API 未注入模型**：`registry.py` 只构造 `TalkAction(content)`，模型路径默认不存在。

## 3. 修复

- `turn/last_message/last_reply` 从 NPC memory 读取并传入：`social.py:49`、`social.py:136`；`turn` 由 `talk:npc` 条数推导，不新增 `talk_ledger` 字段，避免 `state_hash` / golden 基线漂移。
- `_pick_quote` 增加 `turn`，人设台词参与所有意图并按轮次轮换：`dialogue.py:742`；`_trim` 优先保留台词：`dialogue.py:768`；同文再取变体：`dialogue.py:824-832`。
- 模型接入双路（管理台优先、env 回退）统一到**唯一调用路径**：`api/session.py::WorldSession.finalize_dialogue`
  （事务提交后、锁外）→ `agent/dialogue_async.py::AsyncDialogueQueue.reply_sync`；开关 `XKX_DIALOGUE_MODEL=1`（默认关）。
  API 启动固定 `XKX_RUN/XKX_SERVICE_DB`：`api/__main__.py`。
- 当前实际走的是**管理台 8032 的 `local-gw` provider**（`base_url=127.0.0.1:3425/v1`，`model=auto/deepseek-v4.1-flash`），不是 env 回退；env 回退由 `test_dialogue_runtime_supports_admin_and_env_sources` 单测覆盖。

### 红线实现

- 状态/事件只写 `compose_talk` 模板：`social.py` 不再用模型文本覆盖 `reply`。
- 模型文本只进入 `dialogue_aside.jsonl`（键 `(event_seq, npc_id)`，字段 `event_seq/npc/text/model/created_at`）
  与本次 HTTP `details.reply`（`api/server.py` 在 `session.action()` 返回后调用 `finalize_dialogue`）。
- 验证：`events.sqlite` 中模型句“我好得很，天没亮就杀完一口猪”出现 **0** 次；“腰都酸” **0** 次；模板句“金掌柜那三百文，我段五斗认”按轮次出现 3 次。模型句只在 `dialogue.jsonl` 出现 1 次。
- `content_hash` 仍为启动值 `5267a8a84f7c`；模型文本不参与目标、关系、经济与回放。

## 4. 修复后：逐轮对照

| # | NPC | 轮 | 请求 | 修复前 | 修复后（模型） |
|---|---|---|---|---|---|
| 1 | duan_wudou | 1 | 你好，今日可好？ | 我在呢，屠户这行我干惯了… | 我好得很，天没亮就杀完一口猪，这会儿腰都酸。你要肥要瘦？先说来路，我才好下刀。 |
| 2 | duan_wudou | 2 | 幸会，近来可好？ | 我在呢，屠户这行我干惯了… | 好着呢，寅时刚放倒一头，肉还热。我把刀往案板上一搁，你要肥要瘦？账的事先不提，我段五斗认账，就是手头紧。 |
| 3 | duan_wudou | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶… | 我把刀往案板上一搁，新事嘛，崔捕快这几天老在赌摊边转，闹得人心里发毛。金掌柜那三百文我认，慢慢还。你听谁说的？ |
| 4 | jin_mancang | 1 | 你好，今日可好？ | 我在呢，掌柜这行我干惯了… | 今日还行，刚把仓窗开了通通风，手上这把算盘还闲不下来。你问得客气，我便说实话：日子要稳着过，账也得一笔一笔算。 |
| 5 | jin_mancang | 2 | 幸会，近来可好？ | 我在呢，掌柜这行我干惯了… | 还算顺当。五更我进仓捻了把米，成色压得住；午时柜上排了几笔赊账，利钱照旧。你先坐，我把算盘归了位，再听你细说。 |
| 6 | jin_mancang | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶… | 新鲜事啊，我五更验粮时倒见着一桩：仓里新米成色不差。柜上赊账的也排了名，熟户三个月，生户一个月。你要问价，先说说今年收成如何。 |
| 7 | liu_sanniang | 1 | 你好，今日可好？ | 我在呢，绣娘这行我干惯了… | 好呀，我这摊上刚理完晨市新到的丝，你来得正巧。这两日绸价往上抬了些，我还没把话报死。你这眼光，先坐下看看料子，阿是？ |
| 8 | liu_sanniang | 2 | 幸会，近来可好？ | 我在呢，绣娘这行我干惯了… | 好呀，劳你记挂。今早我看过丝价，回来又把样布理了一遍。你这趟来，先别急着问价，瞧瞧我这匹熟绸的色头——家里人都还安顺吧？ |
| 9 | liu_sanniang | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶… | 新鲜事倒有一桩。罗家妹妹拿了块新花样来比，我看了半晌。她那六十文赊了两季，也不知还记不记得。阿是？ |
| 10 | meng_jiuniang | 1 | 你好，今日可好？ | 我在呢，媒婆这行我干惯了… | 好着呢，刚在茶摊听了一耳朵闲话。你来得巧，我这儿正翻庚帖，你家里可有待议的亲事？ |
| 11 | meng_jiuniang | 2 | 幸会，近来可好？ | 我在呢，媒婆这行我干惯了… | 好着呢，刚在茶摊上听了一耳朵闲话，手里这几张庚帖又得重排。你今日来，是替谁问八字，还是只陪我嗑会儿瓜子？ |
| 12 | meng_jiuniang | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶… | 新鲜事哪日没有？我今早坐茶摊，听来一桩也没敢说准。你先告诉我，问的是娶亲还是铺子里的闲话？ |
| 13 | ruan_qingfu | 1 | 你好，今日可好？ | 我在呢，货郎这行我干惯了… | 好什么呀，担绳上那枚压担钱今早晃得叮当响。我这腿脚跑三个村，卖的多是针头线脑，本钱轻得风一吹就跑。你站我摊前，是看布还是问秤？先开口，我好琢磨这价怎么让。 |
| 14 | ruan_qingfu | 2 | 幸会，近来可好？ | 我在呢，货郎这行我干惯了… | 担子翻两个村，还行，就是腿比嘴累。粗布炭头都在这，赊账还是老规矩，名字日子写清楚。 |
| 15 | ruan_qingfu | 3 | 听说镇上最近有新鲜事？ | 街面上传得凶… | 新鲜事倒有一桩，东村井边听人嚼的，我不说没影的。河湾有人嫁女，红布头问价问到我这担上了。我摸着算盘珠跟你讲，这价也就眼下能看。 |

### 2–3 组同题对照（看得出人格）

**“你好，今日可好？”**

- 段五斗（屠户）：“我好得很，天没亮就杀完一口猪，这会儿腰都酸。你要肥要瘦？先说来路，我才好下刀。”
- 金满仓（掌柜）：“今日还行，刚把仓窗开了通通风，手上这把算盘还闲不下来。你问得客气，我便说实话：日子要稳着过，账也得一笔一笔算。”
- 柳三娘（绣娘）：“好呀，我这摊上刚理完晨市新到的丝，你来得正巧。这两日绸价往上抬了些，我还没把话报死。你这眼光，先坐下看看料子，阿是？”

**“听说镇上最近有新鲜事？”**

- 段五斗：“我把刀往案板上一搁，新事嘛，崔捕快这几天老在赌摊边转，闹得人心里发毛。金掌柜那三百文我认，慢慢还。你听谁说的？”
- 金满仓：“新鲜事啊，我五更验粮时倒见着一桩：仓里新米成色不差。柜上赊账的也排了名，熟户三个月，生户一个月。你要问价，先说说今年收成如何。”
- 柳三娘：“新鲜事倒有一桩。罗家妹妹拿了块新花样来比，我看了半晌。她那六十文赊了两季，也不知还记不记得。阿是？”

## 5. 量化指标

| 指标 | 修复前 | 修复后-模板 | 修复后-模型 |
|---|---|---|---|
| `unique_replies` | 6/15 | 15/15 | 15/15 |
| 同 NPC 相邻轮相同 | 5 | 0 | 0 |
| 同意图跨 NPC 完全相同 | 10 | 0 | 0 |
| 同意图最长公共子串 | 29 | 2 | 1 |
| 模板命中率 | 15/15 | 15/15 | 0/15 |
| 模型生成率 | 0/15 | 0/15 | 15/15 |

## 6. 回归与提交

- 新增 `tests/test_dialogue_variety.py`（4 项）与更新 `tests/test_dialogue_guard_integration.py` 红线断言；对话相关测试 `101 passed`。
- 指标与红线写入 [`docs/DIALOGUE-QUALITY.md`](../docs/DIALOGUE-QUALITY.md)，作为发布回归项。
- `src/wsim/api/session.py` 的模型接线由后端线提交（`3a293bb`），本线只读引用，不改其实现；`ui/**` 未动。

## 7. 统一模型旁路（2026-11-07）

两条历史实现（本线 `dialogue_runtime.py` 的同步直出 + 后端线 `dialogue_aside.py` 的旁路）合并为**一条**：

- **唯一模型调用路径**：`src/wsim/api/session.py::WorldSession.finalize_dialogue`（[L992](../src/wsim/api/session.py#L992)）
  → `AsyncDialogueQueue.submit_background`（[dialogue_async.py:306](../src/wsim/agent/dialogue_async.py#L306)）。
  事务提交后只登记后台任务，**不等待模型**；成功结果写 `dialogue_aside.jsonl`。
- 本线删除第二条链：`TalkAction.apply` 内的同步 `reply_sync` 与 `dialogue_runtime.remember/overlay_reply`
  全部移除；`sim/headless.py` 不再构造 `AsyncDialogueQueue`（离线 `dialogue_calls=0`）。
- 模板侧能力保留并继续生效：`turn` 由 memory 推导、人设台词参与全部意图、同文防重。
- **开关**：`XKX_DIALOGUE_MODEL=1`（默认关，显式 opt-in）；判定顺序“开关 → 管理台 provider 优先 → env 兜底”，
  任一步不满足即模板。实测 8031 `admin_registry`（local-gw）、8036 关（`talk.dialogue=None`）。
- **超时/重试**：`XKX_DIALOGUE_TIMEOUT_S` 默认 8s（clamp 0.5–20）；对话槽 `max_retries=0`（管理台降级链同样钳制），
  超时/异常保留模板、不写事件、不阻塞 tick。
- **延迟**（统一后、改造为异步前，20 次 `talk`，8037/8038）：关模型 p50/p95 `0.0023s/0.0037s`（20/20 模板）；
  开模型 `3.35s/7.56s`（19/20 模型，1 次 8s 超时降级，`max=8.01s`）。同步等待模型导致产品级延迟，
  因此改造为异步配音，见 7.1。
- **旁路字段**：`dialogue_aside.jsonl` 每行 `event_seq/npc/text/model/created_at`，键 `(event_seq, npc_id)`，
  与 `specs/dialogue-model-aside/spec.md` 一致；仓库不再有第二套旁路存储。
- **三个多样性指标不变**：`unique 15/15`、同 NPC 相邻相同 `0`、同意图跨 NPC 完全相同 `0`（同文断言在测试中固化）。

### 7.1 异步配音（2026-11-07）

- **立即返回**：`POST /api/player/action` 的 `details.reply` 只用模板句，附
  `dialogue_source="template"`；开关开启且后台受理时 `dialogue_pending=true`。
- **后台交付**：`finalize_dialogue` → `submit_background`（[dialogue_async.py:306](../src/wsim/agent/dialogue_async.py#L306)）
  → `_answer_background`；事务已提交、不持 `WorldSession.lock`。成功回调写
  `dialogue_aside.jsonl`，字段与格式不变。
- **背压**：`ThreadPoolExecutor(max_workers=2)`，在飞 + 排队上限 `2 + 5 = 7`；超出直接丢弃、
  不排队、不重试，计 `health.llm.dropped_backlog`。硬超时 `XKX_DIALOGUE_TIMEOUT_S`
  （默认 8s，clamp 0.5–20），超时计 `health.llm.timeouts`。
- **取回通道**：`GET /api/player/messages?since_seq=N&account_id=<account>`，只返回
  `seq > N` 且旁路已有条目的 `talk:npc` 事件；`dialogue_source="model"`，模型文本只来自
  `dialogue_aside.jsonl`，无旁路条目不返回、不以模板冒充模型。

**20 次样本（run `accept-async-on2-20261107` / `accept-async-off2-20261107`，两个独立进程）**

| 模式 | 请求 | p50 | p95 | max |
|---|---|---|---|---|
| 开模型（8041，env provider） | talk ×20 | 0.0019s | 0.0027s | 0.0027s |
| 开模型 | rest ×20 | 0.0014s | 0.0027s | 0.0040s |
| 关模型（8040） | talk ×20 | 0.0018s | 0.0034s | 0.0041s |
| 关模型 | rest ×20 | 0.0019s | 0.0029s | 0.0047s |

开模型响应样例（第 1 条）：

```json
{"i":0,"seq":7,"latency_s":0.0027,"ok":true,"source":"template","pending":true,
 "reply":"我在呢，屠户这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。金掌柜那三百文，我段五斗认！"}
```

字段名（动作响应）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `details.reply` | string | 本次 HTTP 响应的模板句（立即返回） |
| `details.dialogue_source` | `"template"` | 本次响应文本来源；模板永远是模板 |
| `details.dialogue_pending` | bool | 后台配音已被受理；开关开启且未触发背压时为 `true` |
| `health.llm.dropped_backlog` | int | 背压丢弃计数 |
| `health.llm.timeouts` | int | 后台配音超时计数 |

真实 `POST /api/player/action` 响应（run `accept-sample-20261107`，`data` 玩家状态视图省略）：

```json
{"action":"talk","client_tick":null,"client_tick_ignored":false,"confirm":false,
 "event_seq":7,"ok":true,"player":"player:qa_sample_async","profile_version":1,
 "status":"executed","tick":6,"verb":"talk",
 "details":{"dialogue_pending":true,"dialogue_source":"template",
   "reply":"我在呢，屠户这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。金掌柜那三百文，我段五斗认！",
   "relation":{"affection":-1.0,"kind":"acquaintance","target":"duan_wudou","trust":39.5}}}
```

同一次请求的迟到配音（`GET /api/player/messages?since_seq=0&account_id=qa_sample_async`）：

```json
{"account_id":"qa_sample_async","messages":[{"created_at":"2026-11-06T19:05:23+00:00",
 "dialogue_source":"model","from":"duan_wudou","id":"msg:7","kind":"talk_reply",
 "model":"auto/deepseek-v4.1-flash","seq":7,
 "text":"好着呢，天没亮就宰了一头，刀口还热。兄弟你来得巧，这块后腿肥瘦正好，我拿砍刀给你比划比划，要哪块说一声。",
 "tick":6,"to":"player:qa_sample_async"}],
 "next_since_seq":7,"ok":true,"profile_version":1,"since_seq":0,"tick":12}
```

字段名（取回响应）：`since_seq`（请求水位）、`next_since_seq`（下次水位）、
`messages[].seq/tick/from/to/text/dialogue_source/model/created_at`；
`dialogue_source` 只会是 `"model"`，文本只来自旁路。

背压实测（20 次突发）：受理 7，`dropped_backlog=13`，`timeouts=0`，`calls=7/ok=7`。
7 条旁路全部落地（`aside_file_rows=7`）。

3 条迟到旁路记录（`dialogue_aside.jsonl` 原文）：

```json
{"created_at":"2026-11-06T19:02:43+00:00","event_seq":7,"model":"auto/deepseek-v4.1-flash","npc":"duan_wudou","text":"好！天没亮就杀了一头猪，刀都磨亮了。你要肥要瘦？我这儿刚开摊。"}
{"created_at":"2026-11-06T19:02:44+00:00","event_seq":9,"model":"auto/deepseek-v4.1-flash","npc":"duan_wudou","text":"嘿，托你的福，还站得住！天没亮就起来放血剔骨，手上有油，心上也有账。金满仓那三百文我还认，就是这阵子手头紧，你别跟着来催。要肉不，割一块肥的？"}
{"created_at":"2026-11-06T19:02:48+00:00","event_seq":11,"model":"auto/deepseek-v4.1-flash","npc":"duan_wudou","text":"新鲜事多着呢，我手里这把刀还热着。金满仓那账又催上门了，赌摊昨儿也挤破头——官爷可别顺耳听，我可没押。"}
```

`since_seq=0` 取回 7 条（前 2 条）：

```json
{"id":"msg:7","seq":7,"tick":6,"kind":"talk_reply","from":"duan_wudou","to":"player:qa_async_on3",
 "text":"好！天没亮就杀了一头猪，刀都磨亮了。你要肥要瘦？我这儿刚开摊。",
 "dialogue_source":"model","model":"auto/deepseek-v4.1-flash","created_at":"2026-11-06T19:02:43+00:00"}
{"id":"msg:9","seq":9,"tick":12,"kind":"talk_reply","from":"duan_wudou","to":"player:qa_async_on3",
 "text":"嘿，托你的福，还站得住！天没亮就起来放血剔骨，手上有油，心上也有账。金满仓那三百文我还认，就是这阵子手头紧，你别跟着来催。要肉不，割一块肥的？",
 "dialogue_source":"model","model":"auto/deepseek-v4.1-flash","created_at":"2026-11-06T19:02:44+00:00"}
```

`since_seq=7` 只回 6 条（`next_since_seq=19`），seq=7 那条被排除。

**三条死线（原始哈希）**

1. 开关前后 golden 逐字节一致：同一 2 天 headless world（`/tmp/xkx-deadline`）跑三次，
   `off_a == off_b == on_c`，`events_total=791`，`content_hash=5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4`，
   `state_final=6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55`，
   `event_final=0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49`；
   `events.jsonl` sha256 `670e09592afdafb8b5bcc4d7976b2372ff4219124520982f88261df08cc9b19d`，
   `state_final.json` sha256 `63b8979afac03183a4e09c6be99e3b22f2390263960d9bda62ada6b10dcf6811`；
   开关开启时离线 `dialogue_calls=0`（模型只走 API 会话）。
2. 同 seed 两次一致：`off_a == off_b`，上述全部哈希逐项相同。
3. 删旁路世界照常：`accept-async-on2-20261107` 删 `dialogue_aside.jsonl` 前后，
   `tick=246`、`events=86`、`state=9a073d9d0cce408faf62210b56551dfe33f2c75528e36a4748e2f3b3bcb54bd5`、
   `event=fdb87bc68f1212aabc82c094026c1158ca477b03536eb6f54aa42223a7193f7d`；
   重开后 `messages` 20 条全为 `dialogue_source="template"`，`since_seq=0` 返回 0 条模型。

**事件库检索**：7 条模型句在 `events.sqlite` 的 `effects/args/outcome` 命中 **0**；
模板 `我答：` 20 条。多样性指标维持 `15/15`、相邻雷同 `0`、跨 NPC 同文 `0`。
