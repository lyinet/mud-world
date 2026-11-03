# P3-C 交付报告：四个缺陷 + 缺 location 追问 + 玩家最小切片

- 基线：`delivery-p3b`（commit `222a2b96d6ffa963c84c4a5b1cc2a4d9e11b9cf1`）
- 本轮 tag：`delivery-p3c`（commit 见文末“冻结记录”）
- 内容包 `content_hash`：`ced9cd63fe96177015b9be56259a9dbdd8745362cb8b9509f7a6ffb98b266fdd`
- 真实 Xing / 真实决策端点调用：**0 次**（等 key；本轮只用假端点与单元测试）
- pytest：**145 passed**（P3-B 为 124，本轮新增 21）
- framework_check：**6/6 PASS**

## 1. 四个缺陷：修复位置与测试名

### 1.1 对话身份不再写死 `player:visitor`

- 修复位置：
  - [social.py](../src/wsim/actions/social.py) `_speaker_id(state, intent)`：
    优先 `args.player_id`；否则 `actor=player:*` 的会话身份；玩家来源若两者都缺返回空；
    NPC 必须有已注册实体才可用 actor，防止伪造未知实体。
  - `TalkAction.preconditions` 空身份返回 `missing_player_id`；`apply` 抛
    `Reject("missing_player_id")`。
  - 非 `player:` 命名空间的 `args.player_id`（例如冒用 NPC `n1`）同样按缺失拒绝。
  - 迁移兼容：`Event.legacy` + `to_canonical_dict()`；
    [store.py](../src/wsim/world/store.py) 旧库无 `legacy` 列时不 ALTER，
    读取时把 `player:visitor` 的 talk/presence 事件标 `legacy=True`；
    [hash.py](../src/wsim/world/hash.py) 与 `EventStore.event_hash()` 用 canonical
    dict，`legacy` 不进哈希。旧 run 哈希与回放不变。
- 测试：
  - `test_talk_action.py::test_player_id_arg_overrides_actor`
  - `test_talk_action.py::test_actor_session_identity_is_used`
  - `test_talk_action.py::test_talk_without_identity_is_rejected`
  - `test_p3c_security.py::test_legacy_events_marked_without_hash_change`

### 1.2 服务端忽略调用方关系增量与回复

- 修复位置：
  - [social.py](../src/wsim/actions/social.py) `TalkAction.apply` 用
    `compose_talk(...)` 重新生成 `reply/affection_delta/trust_delta`；
    `intent.args` 中的 `affection_delta` / `trust_delta` / `reply` 全部丢弃。
  - 命中字段时追加 `Effect("audit_log", {"kind":"spoofed_arg", ...})`，
    不整包拒绝。
  - [effects.py](../src/wsim/world/effects.py) 新增 `audit_log` 原语（kind 非空、
    上限 500 条）；[state.py](../src/wsim/world/state.py) `audit_log` 仅在非空时
    进入 `to_dict()`，旧 run 状态哈希不变。
- 测试：
  - `test_talk_action.py::test_talk_ignores_spoofed_reply_and_deltas`
  - `test_talk_action.py::test_talk_records_spoofed_arg_audit`
- 运行证据：72d 假端点 run 的 `state_final.json` 有 500 条 `audit_log`（达到上限），
  全部为 `kind=spoofed_arg`、`fields=["reply"]`：假决策端点给 NPC 对话输出
  `reply`，服务端已忽略并留痕。

### 1.3 Writer 校验玩家 tick

- 修复位置：[writer.py](../src/wsim/world/writer.py)
  - `_is_player_intent`：`actor` 以 `player:` 开头且 `source not in (npc, planner, system)`。
  - `submit` 在 action 前校验 `tick >= _player_ticks[actor]` 且 `tick <= state.tick`；
    否则写 `rejected:stale_tick`。通过后更新 `_player_ticks`。
  - NPC / planner / system 不经过该校验。
- 测试：
  - `test_p3c_security.py::test_writer_rejects_stale_player_tick`
  - `test_p3c_security.py::test_writer_rejects_future_player_tick`
  - `test_p3c_security.py::test_writer_npc_tick_not_checked`

### 1.4 `found_sect` 在 Action 层执行 `allowed_roles`

- 修复位置：[business.py](../src/wsim/actions/business.py)
  `FoundSectAction.preconditions` 在 `require_entity` 后调用
  `role_allowed(self.content, "found_sect", ent.role)`，越权返回
  `role_not_allowed`。
- 内容一致性修正：[content/verbs.yaml](../content/verbs.yaml) 的
  `found_sect.allowed_roles` 增加 `镖师`。原因：`content/sects.yaml` 把
  `zhuo_bufan` / `pei_wujiu`（均为镖师）指定为镖行同盟发起人；若不加入，
  内容包自相矛盾，10d 产生 6 条必然失败的 `found_sect`，拒绝率 2.12% 超过门禁。
  这是修正内容声明冲突，不是降低门槛或为指标调参。
- 测试：
  - `test_p3c_security.py::test_found_sect_role_not_allowed`（铁匠被拒）
  - `test_p3c_security.py::test_found_sect_allowed_role_passes`（掌柜通过角色检查）

## 2. 缺 location 追问：9.45% → 0%

实现：
- [prompt.py](../src/wsim/agent/prompt.py) `build_state_summary` 新增
  `reachable_locations`（当前地点 `exits` 的 `[{id,name}]`，来自世界状态）与
  `previous_location`。
- [intent_model.py](../src/wsim/agent/intent_model.py) `build_intent_prompt` 新增：
  `【你刚才在哪】`、`【当前可到达的相邻地点（只可从这些里选 destination）】`，
  并把两个字段写入 `【当前上下文】` JSON；system prompt 明确“不能猜，缺则只缺 destination”。
- [provider.py](../src/wsim/agent/provider.py) 维护 `_last_locations[npc]`，把上一轮
  决策时的地点注入 `previous_location`。
- [fake_llm_server.py](../tools/fake_llm_server.py) decision 模式改用
  `reachable_locations.name` 组装“去{name}看看”，不再用 `exits[0]` 的地点 id。
- [fake_intent_server.py](../tools/fake_intent_server.py) 去掉“看看/走走/逛逛”尾缀，
  并把地点 id 归一成相邻地点中文名。

| run | 意图调用 | needs_clarification | 比例 | 缺失字段分布 |
| --- | --- | --- | --- | --- |
| P3-B 72d（基线） | 2,064 | 195 | 9.45% | destination 195（100%） |
| P3-C 72d | 2,201 | 0 | **0.00%** | 无 |
| P3-C 10d | 267 | 0 | **0.00%** | 无 |

仍缺的类别：**无**。P3-C 的假决策端点已按相邻地点名称生成目的地，
意图层全部解析成功；真实 Xing 尚未接入，比例需用真实 key 复测。

测试：
- `test_p3c_intent_location.py::test_state_summary_lists_only_reachable_locations`
- `test_p3c_intent_location.py::test_intent_prompt_lists_reachable_locations`
- `test_p3c_intent_location.py::test_intent_prompt_without_neighbors_tells_model_to_clarify`
- `test_p3c_intent_location.py::test_fake_intent_resolves_reachable_name`
- `test_p3c_intent_location.py::test_fake_intent_clarifies_when_no_destination`

## 3. 玩家最小切片

实现：
- 新建 [player.py](../src/wsim/actions/player.py)：
  - `player_enter`：校验 `at` 在世界地点；创建 `player:<id>` 实体（role `visitor`）、
    `wallet:<id>`（余额 0）、位置/物品表；`presence=online`、`last_seen_tick`。
  - `player_presence`：更新位置与 `presence/last_seen_tick`。
  - `player_leave`：`presence=offline`、`last_seen_tick`。
- [registry.py](../src/wsim/actions/registry.py) 注册三个动词；
  [report.py](../src/wsim/audit/report.py) 把它们列入 `ENGINE_ONLY_ALLOWED`
  （UI/会话层动作，不属于 NPC 决策动词）。
- [headless.py](../src/wsim/sim/headless.py)：`with_players=1` 时在 tick 0 提交
  `player_enter`，因此终端/UI 的 status 显示“已注册”。
- 不开放杀害、抢掠、强迫婚配；玩家写操作仍只走 `propose → approve → apply → Writer`。

完整链路实测（临时 EventStore，真实内容包，seed 42）：

| seq | verb | outcome | effects |
| --- | --- | --- | --- |
| 1 | `genesis`（测试目标 NPC，bootstrap） | ok | 2 |
| 2 | `player_enter` | ok | 5 |
| 3 | `talk` | ok | 8 |
| 4 | `player_presence`（集市 → academy） | ok | 3 |
| 5 | `player_leave` | ok | 2 |

结果：
- 玩家实体：`name=阿青, role=visitor, location=academy, presence=offline, last_seen_tick=0`
- 钱包：`wallet:player:u_9001 = 0`
- 关系（服务端计算）：`affection=2.0, trust=41.0`（调用方未传任何关系字段）
- `state_hash=b2490b0e7e09f1d00b9f0998ee553eb970cfbbf8e850e9c51010d38540b034c9`
- `event_hash=faad190e10b2923d2e0492edfb892a70e8e1f1f5164b7ac37489d11a66797a9e`
- `tools/replay.py` 回放：`MATCH`

终端 status（`runs/p3c-players-1d`，`--with-players 1`）实际输出：

```text
角色：访客（player:visitor，客户端视角；世界状态中已注册）
位置：集市（客户端本地坐标）
世界时间：第 1 天 23:50（tick 143）
钱：0 文
物品：炊饼×1
目标：无
待落地提案：0 条（仅在本客户端内存 outbox，未写入世界）
```

测试：
- `test_p3c_player.py::test_player_enter_registers_entity_wallet_presence`
- `test_p3c_player.py::test_player_enter_twice_is_rejected`
- `test_p3c_player.py::test_player_presence_updates_location`
- `test_p3c_player.py::test_player_leave_sets_offline`
- `test_p3c_player.py::test_player_minimal_chain_replay_match`

## 4. 验收数字

| 指标 | P3-B 72d 基线 | P3-C 72d |
| --- | --- | --- |
| K1 全量 | 71/91 = 78.02% | **71/91 = 78.02%** |
| K1 仅新建目标 | — | 82.56% |
| 剔除一次性目标 | — | 70.15% |
| 拒绝率 | 1.46% | **1.35%（191/14,098）** |
| 守恒误差 | 0 | **0**（minted 47,150 / burned 4,361 / net 42,789） |
| replay | MATCH | **MATCH** |
| state_final | `96a869a4…`（P3-B） | `5b71b5d6e19650c73522b533336d71e1654d79a4230db540c97a6a563e36cff5` |
| event_final | `9c11fb3f…`（P3-B） | `fd9c74573a6a841d3c6a66880d965b87d2d993466c451b1841ac4ecec16bb576` |
| 10d state | — | `c9200801997d394864003869773216da5b8c6224ef9d529ce8e5835f7ffb68f0` |

说明：P3-C 的哈希与 P3-B 不同是预期行为——新增 `audit_log`/`presence` 状态字段与
`player_enter` 事件、`found_sect` 角色拦截都改变了事件流；事件/状态 canonical
哈希保持旧 run 可回放，P3-B 旧库仍可读（`legacy` 推断）。

成本（假端点，仅内部对账口径）：decision 2,201 次 / 估算 $2.9276，
intent 2,201 次 token 全为 null（上游无 usage），dialogue 0 次。
真实账单未发生。

## 5. 取舍与未做

- **取舍 1**：`found_sect.allowed_roles` 加入 `镖师`。依据是 `sects.yaml` 的
  `founder_candidate=zhuo_bufan`（镖师）与 `fallback_founder=pei_wujiu`（镖师）。
  不加入则内容包自相矛盾且 10d 拒绝率 2.12% 超门禁。角色拦截逻辑本身未放宽：
  铁匠仍被 `role_not_allowed` 拦截（有测试）。
- **取舍 2**：NPC 对话允许 `actor` 作为服务端会话身份，但要求 actor 已在
  `state.entities`；玩家来源缺 `player_id` 且非 `player:*` actor 仍拒绝。
  这样既消除 `player:visitor` 共用身份，又不打断 P3-B 的 NPC 社交事件。
- **取舍 3**：`audit_log` 上限 500 条，72d 达到上限；更早的 spoofed_arg 记录被丢弃，
  但不影响关系值（字段本来就被忽略）。
- **未做**：完整 P3 玩家系统（8 人周）、多玩家账号/会话管理、玩家背包 UI、
  杀害/抢掠/强迫婚配；真实 Xing 意图模型接入（本轮 0 次调用，比例需 key 复测）。

## 6. 冻结记录

- tag `delivery-p3c` → commit `a54d439343dd8ae4ac85a28c96e2b1f56f402445`
- tag 附注包含 `content_hash`：`ced9cd63fe96177015b9be56259a9dbdd8745362cb8b9509f7a6ffb98b266fdd`
- 从 tag 干净 worktree（`/tmp/wsim-p3c`，分离头指针）重跑：

| 检查 | 干净重跑结果 |
| --- | --- |
| pytest | **145 passed** |
| framework_check | **6/6 PASS** |
| 10d state_final | `c9200801997d394864003869773216da5b8c6224ef9d529ce8e5835f7ffb68f0` |
| 10d event_final | `0a453afb73970035d9494f2ad4de3f959daa603df95b6ae2ebac05283f147e15` |
| 10d replay | MATCH（3,035 events，1.45% 拒绝，clarification 0/267） |
| 72d state_final | `5b71b5d6e19650c73522b533336d71e1654d79a4230db540c97a6a563e36cff5` |
| 72d event_final | `fd9c74573a6a841d3c6a66880d965b87d2d993466c451b1841ac4ecec16bb576` |
| 72d K1 / 拒绝率 | 71/91 = 78.02% / 1.35%（191/14,098） |
| 72d clarification | 0/2,201 |
| 72d 守恒 | 0（minted 47,150 / burned 4,361 / net 42,789） |
| 72d replay | MATCH |
| 72d 墙钟 | 197.59s（主工作区 193.55s，哈希一致，差异来自机器负载） |

主工作区与干净 worktree 的 10d/72d 事件哈希、状态哈希完全一致；
本报告采用干净 worktree 的墙钟值。

