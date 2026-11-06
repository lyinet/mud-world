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
  → `AsyncDialogueQueue.reply_sync`（[dialogue_async.py:202](../src/wsim/agent/dialogue_async.py#L202)）。
- 本线删除第二条链：`TalkAction.apply` 内的同步 `reply_sync` 与 `dialogue_runtime.remember/overlay_reply`
  全部移除；`sim/headless.py` 不再构造 `AsyncDialogueQueue`（离线 `dialogue_calls=0`）。
- 模板侧能力保留并继续生效：`turn` 由 memory 推导、人设台词参与全部意图、同文防重。
- **开关**：`XKX_DIALOGUE_MODEL=1`（默认关，显式 opt-in）；判定顺序“开关 → 管理台 provider 优先 → env 兜底”，
  任一步不满足即模板。实测 8031 `admin_registry`（local-gw）、8036 关（`talk.dialogue=None`）。
- **超时/重试**：`XKX_DIALOGUE_TIMEOUT_S` 默认 8s（clamp 0.5–20）；对话槽 `max_retries=0`（管理台降级链同样钳制），
  超时/异常保留模板、不写事件、不阻塞 tick。
- **延迟**（统一后新代码 20 次 `talk`，8037/8038）：关模型 p50/p95 `0.0023s/0.0037s`（20/20 模板）；
  开模型 `3.35s/7.56s`（19/20 模型，1 次 8s 超时降级，`max=8.01s`）。
- **旁路字段**：`dialogue_aside.jsonl` 每行 `event_seq/npc/text/model/created_at`，键 `(event_seq, npc_id)`，
  与 `specs/dialogue-model-aside/spec.md` 一致；仓库不再有第二套旁路存储。
- **三个多样性指标不变**：`unique 15/15`、同 NPC 相邻相同 `0`、同意图跨 NPC 完全相同 `0`（同文断言在测试中固化）。
