# P3-E 报告（已冻结）

> 状态：**已冻结（`delivery-p3e`）**。P0-1 / P0-2 / P0-4 / P0-5 与换模型 2 天真实跑均已闭合；
> 冻结验收：`selfcheck.sh` 4/4、pytest 182 passed、framework 6/6、干净 worktree replay MATCH。
> 唯一未验证项是 `dialogue` 槽真实文笔（本轮 2 天跑该槽 0 次调用），已在 §7 与 tag 附注列明。
> 契约文档：[docs/player-api.md](../docs/player-api.md)

---

## 1. 玩家实体与动作（E1）

`player:<account_id>` 实体字段（`PlayerProfile`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | `player:<account_id>` |
| `name` | string | 角色名 |
| `archetype` | string | 游侠 / 商人 / 捕快 |
| `role` | string | 镖师 / 掌柜 / 捕快 |
| `online` | bool | 是否在线 |
| `presence` | string | `online` / `offline` / `custody` / `dormant` |
| `created_tick` | int | 首次进入 tick |
| `last_intent_tick` | int | 最后一次成功玩家意图 tick（离线降级计时基准） |
| `offline_since_tick` | int | 最近离线 tick |
| `custody_since_tick` | int | 最近托管 tick |
| `dormant_since_tick` | int | 最近休眠 tick |
| `endowed` | bool | 是否已领取初始资产（按 account_id 幂等） |
| `endowed_tick` | int | 首次发放 tick |
| `endowed_source` | string | `genesis_player_endowment` / `player_endowment` |

钱包、物品、位置、技能、关系表都在实体/账户/关系表上，`/api/player/state` 汇总返回。

动作全部走 `propose_intent → MCP → Writer` 单写者：

- `player_enter`、`player_leave`、`player_heartbeat`
- `move`、`talk`、`trade`
- `quest_accept`、`quest_deliver`
- `player_endowment`（内部：首次进入发放，不暴露给客户端直接调用）

不可逆动作（伤害、抢掠、强迫婚配、驱逐）本轮不开放。

离线策略：`(tick - last_intent_tick) // 144` → 1 日 `offline`、3 日 `custody`、7 日 `dormant`；NPC 不得主动影响离线玩家（`target_offline` / `player_consent_required`）。

**关键回归**：
- 无玩家时 `WorldState.to_dict()` 不写 `players` 键，旧 run 哈希不变（待补固定哈希断言，见 §7）。
- 初始资产有两条显式铸币路径：创世预置（genesis）与运行中新账号首次进入（独立事件 `player_endowment`）。`player_enter` 本身不铸币；`test_p3e_endowment.py` 断言新账号得 300、二次进入不重发、`minted` 增量 == `endowment_total`、replay 一致。
- 所有玩家写仍经 MCP；API 不直连 Writer（`WorldSession._submit` 固定四步：propose → request → approve → apply）。

---

## 2. HTTP API（E2）与契约冻结

实现：`src/wsim/api/`（标准库 `ThreadingHTTPServer`，仅 JSON）。

端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/player/enter` | 进入世界；`account_id` 幂等；创世预置账号直接 resume，运行中新账号由 `player_endowment` 发放 300 文 + 炊饼×3 + 粗布×1 |
| GET | `/api/player/state` | `player` / `location{exits}` / `present_npcs` / `quest_board` / `online_players` |
| POST | `/api/player/action` | 统一动词入口；`client_tick` 只回显；`status = accepted / executed / rejected` |
| GET | `/api/player/intents/<id>` | 按 `request_id` 或 `intent_id` 查询 |
| GET | `/api/player/messages?since_tick=` | 拉取 `talk` 的 NPC 回复 |
| POST | `/api/player/leave` / `heartbeat` / `move` / `talk` / `trade` / `quest/accept` / `quest/deliver` | 细粒度兼容端点 |

契约要点：

- `profile_version = 1`，字段名已冻结；改名前必须追加 CHANGELOG。
- 每个写动作 +6 tick；拒绝也 +6。响应 `tick` 是事件 tick，`next_tick = tick + 6`。
- `talk` 成功时 `details.reply` 同步产生（同一事件的 `memory_add`）；`/messages` 返回同一条。
- 拒绝必须带 `reason_text` 人话原因；不允许只返回 `rejected:xxx`。
- 可启动 run 目录：`runs/player-demo`；命令：
  `PYTHONPATH=src python3 -m wsim.api --run player-demo --port 8020`

与前端现有 `ui/player/server.py` 的 14 条冲突已逐条写入契约 §8。

---

## 3. 十分钟走查（E3，真实输出）

脚本：`tools/playthrough.py`；产物：`runs/p3e-playthrough/narrative.txt`、`report.json`、`events.sqlite`。

验收（`report.json`）：

| 项 | 值 |
|---|---|
| 事件数 | 35 |
| seq 连续 | `true`（1..35） |
| `conservation_error` | 0 |
| `minted_total` | 47450 |
| `burned_total` | 0 |
| replay MATCH | `true` |
| 拒绝带原因 | `true`（`not_together`，seq 20，人话原因） |
| 钱包变化 | 300 → 310 |
| 关系变化 | 金满仓 `acquaintance → friend`，affection 10 |
| 首次发放 | enter seq=3，独立 `player_endowment` seq=4，`endowment_total=300` |
| 二次进入 | `granted=false`、`already_granted=true`，钱包仍 310 |

完整叙事：

```text
# 十分钟走查 · 我是新到任的捕快

我以「捕快」身份进了城，站在market。钱袋里有 300 文，行囊：{'coarse_cloth': 1, 'flatbread': 3}，体力 100.0/100.0。（seq=3 tick=0）
这是我第一次进这个世界，领到 300 文 + 配给，来源 player_endowment（独立事件 seq=4）。
我沿路走到inn（客栈）。体力 95.0/100，seq=6 tick=6。
我对崔铁衣说：初来乍到，跟你打听打听城里的规矩。 对方答：崔铁衣（捕快）听了你的话，先亮身份，再问缘由，三句话内没问清就拔刀。若对方是权贵，他先忍一口气，回头把案子写得更细。若百姓受欺，他宁可违规也要先护人，事后自己写请罪呈。若谢停云又来劝“通融”，他把薄册往桌上一拍，说“你读读这条”。若邢破军抢功，他当场把两人的名字一起。
「律条写在纸上，人情写在人脸上——我只看纸。」（关系 {'affection': 1.0, 'kind': 'acquaintance', 'target': 'cui_tieyi', 'trust': 40.5}，seq=8）
我沿路走到market（集市）。体力 90.0/100，seq=10 tick=18。
我沿路走到pharmacy（药铺）。体力 85.0/100，seq=12 tick=24。
在药铺我问常半夏：城里最近可有什么案子？ 对方答：常半夏（游医）听了你的话，先往后缩，再赔笑，最后才把话说明；若病人危急，他反而不缩了，先下针再谈钱。若顾青崖当众驳他，他不争，回头把方子改两味。若苏合香提八十文，他说“再宽两日”，转身去给穷人义诊。若苏合香当众提账，他把药碾摇得更慢，装没听见。真遇上两家都来请，他先。
「顾大夫的方子太急，病人受不住。」（关系 {'affection': -1.0, 'kind': 'acquaintance', 'target': 'chang_banxia', 'trust': 39.5}，seq=14）
我沿路走到market（集市）。体力 80.0/100，seq=16 tick=36。
我沿路走到academy（书院）。体力 75.0/100，seq=18 tick=42。
我对着空气喊柳三娘，系统拒绝了我：not_together——对方不在你当前所在的地点。（seq=20 tick=48）
我向白露生请教委托。 对方答：白露生（学究）听了你的话，先引经据典，再发现没人听，最后自己生闷气；若涉及书院名声，他立刻挺直腰板，一步不让。若学生被欺负，他会放下书卷去理论，哪怕说不过对方。若曹半仙当众驳他，他当场把《论语》翻到那一页，念完整段。真遇上有人求他写状，他先问“可有冤情”，再问“可付。
「此事于礼不合，恕老夫不能苟同。」（关系 {'affection': -1.0, 'kind': 'acquaintance', 'target': 'bai_lusheng', 'trust': 39.5}，seq=22）
我沿路走到market（集市）。体力 70.0/100，seq=24 tick=60。
我在任务板前看到「quest:errand_market_ledger:0」：金满仓委托我去inn，报酬 10 文。
我接下了这单任务（seq=26 tick=66）。
我沿路走到inn（客栈）。体力 65.0/100，seq=28 tick=72。
我把事情办妥，任务交付成功：钱袋从 300 文变成 310 文（+10），与金满仓的关系：{'affection': 10.0, 'kind': 'friend', 'name': '金满仓', 'target': 'jin_mancang', 'trust': 45.0}。（seq=30 tick=78）
我下线离开世界（seq=32）。
我重新进入世界，仍在inn，钱袋 310 文，与柳三娘的关系还在：[]；进行中任务 []。

## 验收
- 事件 35 条，seq 1..35 连续：True
- 守恒误差：0
- replay MATCH：True
- 拒绝：[{'reason': 'not_together', 'text': '对方不在你当前所在的地点。', 'seq': 20}]
全部硬验收 PASS。
```

> 走查账号 `u_demo` 本轮改为 `endow=False`（不预置），因此 seq 3 的 `player_enter` 是“运行中新账号首次进入”，seq 4 的 `player_endowment` 才是 300 文来源；这正好把本轮 P0 修复放进十分钟主路径。

---

## 4. LLM 意图前置位置校验（P0，本轮修复）

### 4.1 选择

选择 **(b)：提案前拒绝 + 记录 `precondition_blocked` + NPC 下一 tick 重新规划**。
不选 (a) 自动拆两步的原因：

1. LLM/NPC 的意图是“候选”，不是玩家承诺；自动 move→talk 需要跨 tick 事务、超时和回滚，容易产生“走到一半停在路上”的幽灵意图。
2. 目标位置可能在计划与执行之间变化；预先生成的第二步 move 会变陈旧。
3. 提案前拦截不消耗 tick/seq，也不污染事件日志；NPC 重新规划后下一 tick 可先移动再说话，效果等价且更简单。
4. 玩家意图不在此拦截：玩家必须拿到 `not_together` + 人话原因，走正常 Reject 事件（API 契约 §7）。

### 4.2 实现

- `src/wsim/sim/headless.py::intent_block_reason`：对 `source not in PLAYER_SOURCES` 且非 `system_*` 的意图，执行 `action.preconditions(state, intent)`。
- `emit` 在**执行时**（不是入队时）校验；返回 `None` 表示不落事件。
- 阻塞计数写入 summary：

```json
"precondition_blocked": {
  "total": 27,
  "by_verb": {
    "talk": {"not_together": 18},
    "trade": {"not_together": 4},
    "quest_accept": {"not_at_giver": 2},
    "quest_deliver": {"quest_not_accepted_by_actor": 2},
    "sell": {"item_not_held": 1}
  },
  "examples": [...]
}
```

### 4.3 framework 第 6 项：修复前后

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 事件总数（2 天 fake LLM） | 782 | 816 |
| 拒绝数 | 22 | 0 |
| 拒绝率 | 2.81% | **0.00%** |
| `talk:not_together` 事件 | 14 | **0** |
| `trade:not_together` | 3 | 0 |
| `quest_accept:not_at_giver` | 2 | 0 |
| `quest_deliver:quest_not_accepted_by_actor` | 2 | 0 |
| `sell:item_not_held` | 1 | 0 |
| 前置拦截（不计事件） | — | 27 |
| LLM 落地 | 47 | 47/47 |
| 守恒 / replay | PASS | PASS |
| framework | PASS（第 6 项 FAIL） | **PASS** |

### 4.4 非法率口径变化（为什么不算掩盖）

- 旧口径：所有 `outcome=rejected` 事件都计入非法率，包括“人不在同一地点”这种正常交互前置条件。
- 新口径：**非法率只统计真正违反世界规则、进入事件日志的 Reject**；前置条件不满足的自主意图在提案前被拦截，单独记 `precondition_blocked`（按 verb/reason/examples 可审计）。
- 为什么不算掩盖：
  1. 拦截发生在提案阶段，没有事件、没有消耗世界动作；
  2. 所有被拦截条目都在 summary 里计数并附最多 20 条 examples，可逐条复核；
  3. 玩家意图没有被静默吞掉，仍产生可解释 Reject（API 返回原因）；
  4. 真正的规则违反（越权、守恒、非法状态变更）仍走 Reject 事件，口径不变。

测试：

- `tests/test_p3e_preconditions.py::test_llm_talk_without_move_is_blocked_before_event`
  - 构造 `source=llm` 的 `talk`，目标在不同地点；断言 `intent_block_reason == "not_together"`，且 Writer 事件日志为空。
- `tests/test_p3e_preconditions.py::test_planner_trade_and_quest_are_blocked_before_event`
- `tests/test_p3e_preconditions.py::test_player_intent_is_not_silently_blocked`

framework 第 6 项输出：

```text
[6/6] 假端点端到端（127.0.0.1） ... PASS
127.0.0.1:62194 启动→2 天→关闭；source=llm 落地 47/47 条，
night_batch=29 realtime=65 replan=0（入口触发 44/90/27）；
calls=94 schema_reject=0 fallback=0 拒绝率=0.00% 守恒=0 replay=MATCH
```

### 4.5 P0：运行中新账号钱包为 0（本轮修复）

**问题**：300 文只在创世预置账号上发放。任何未预置的真实新账号第一次 `enter` 后钱包为 0，买不起饭、接不了需要垫付的任务，第一分钟卡死——首发阻断。

**修复口径**（按 `docs/player-system.md` 与 `docs/player-api.md` 同步更新）：

- `player_enter` **仍不铸币、不造物**；首次进入后由独立事件 `player_endowment` 发放：
  - `mint(account=wallet(pid), amount=300, source="player_endowment", reason="first_entry")`，计入 `money_supply.minted`；
  - `item_create(flatbread×3, coarse_cloth×1, source="player_endowment_carry")`；
  - `player_endow` 写入档案 `endowed=true` / `endowed_tick` / `endowed_source`，并累计 `endowment_total`。
- **不改创世总额、不从 NPC 钱包扣款**。K6 等式保持：minted +300、accounts +300、守恒误差 0。
- **按 `account_id` 幂等**：`PlayerProfile.endowed` 是唯一开关。在线二次 enter 直接 `already_online`；离开后重进 `resumed=true` 但不发放；跨 run 重启是新世界、档案不存在，可重新领取（已写入契约）。
- **防刷上限**：单区域最多 200 个玩家席位、初始资产累计发放上限 60000 文；超限 `player_cap_reached` / `endowment_cap_reached`。`endowment_total` 可在 `/state` 顶层与 `details.endowment` 读取。
- `granted_at_genesis` 保留但**语义修正**为“资产来源是否创世预置”；新增 `granted` 表示“本次是否新发放”。CHANGELOG 已记 `1.1`。

**事件与 `event_seq`**：

- 每个写动作 = 动作事件 + `system_tick`（推进世界钟），所以连续写动作的 seq 差是 **+2**，不是重复写入；被拒绝的动作也是 reject + `system_tick` = +2。
- 运行中新账号第一次 enter = `player_enter` → `player_endowment` → `system_tick`，这一段是 **+3**；从第二次动作起恢复 +2。
- 响应顶层 `event_seq` 只指动作事件；发放事件 seq 在 `details.endowment.event_seq`。契约 §4.4.1 已写明。

**真实证据**：

| 场景 | 结果 | 证据 |
|---|---|---|
| 新账号首次 enter | 钱包 300、行囊炊饼×3/粗布×1、`granted=true`、`money_source=player_endowment` | 契约 §10.7；`tests/test_p3e_endowment.py::test_first_enter_grants_once_and_mints_matching_amount` |
| 同账号二次 enter | `already_online`，钱包仍 300，`minted` 不变 | 契约 §10.8；`::test_second_enter_while_online_is_already_online_and_grants_nothing` |
| 离开后重进 | `granted=false`、`already_granted=true`、金额不变 | `::test_leave_and_reenter_does_not_regrant` |
| 两个不同账号 | 各 300，`minted` 增量 600 == `endowment_total` 600 | `::test_two_accounts_each_get_one_endowment` |
| replay | 金额/endowed/endowment_total/state_hash 全部一致 | `::test_replay_restores_endowment_balance` |
| 十分钟走查 | 首次进入即领 300 文，后续成功买到任务回报 | §3；`tools/playthrough.py`（`endow=False`） |

另外把 `player_endowment` 加入 content audit 的 `ENGINE_ONLY_ALLOWED`（玩家会话层动作，不要求内容包声明），framework 6/6 恢复 PASS。

### 4.6 P0-1：429 退避走生产路径真的等待

- `ProviderClient` 默认 `sleep_fn or time.sleep`（原先是 no-op），生产路径不再“空转退避”。
- `ProviderConfig.backoff_429_s` 默认 **25.0s**，可被 `config/llm.yaml` 覆盖；测试里缩短到 0.05s。
- 新测试 `tests/test_provider.py::test_429_backoff_waits_on_production_path_without_injected_sleep`：
  不注入 `sleep_fn`、传输层连续两次 429，断言 `elapsed >= 0.04s`、`http_429=2`、`retries=1`，
  并断言默认值仍是 25.0s。测试通过。

### 4.7 P0-2：grounding 六用例

文件 `tests/test_p3e_grounding.py`（6/6 通过）。所有数量都必须与目标物品**同分句**且量词兼容：

| # | 原话 | 模型给的参数 | 结果 |
|---|---|---|---|
| 1 | 先来三个人，再去集市买两斗米 | `qty=3斗`（跨分句借了“三个人”的 3） | 丢弃，`blocked=[qty]` |
| 2 | 有三个人要去集市买米 | `qty=3`，单位=人 | 人数单位不给物品数量背书，丢弃 |
| 3 | 去集市买两斗米 | `qty=2斤`（量词不匹） | 丢弃 |
| 4 | 去集市买俩炊饼 | 模型给 `qty=俩` → 归一 `2` | 通过，`normalizations.qty={value:2}` |
| 5 | 去集市买半斤米 | 模型给 `qty=半斤` → 归一 `1` | 通过 |
| 6 | 去集市买米 | 模型自行补 `qty=5` | 丢弃且 `dropped_fields=["qty"]` |

实现要点：`_quantity_tokens` 识别 `三` + 尾字`人/名/位` 的人数单位；`_units_compatible` 禁止人数给物品背书；
可选字段被丢弃时写入 `dropped_fields`，必填字段被丢弃时写 `blocked` 并转 `needs_clarification`。

### 4.8 P0-4：证据入库、复现命令与密钥

- 证据固定路径 `reports/evidence/`（清单与哈希见 [docs/evidence.md](../docs/evidence.md)）。
- P3-D 报告里的复现命令文件名已改为真实存在的 `intent-recordings-2d-final.jsonl`。
- 报告中的明文测试 key 已替换为 `<REDACTED>`，README 顶部写明：该 key 如为真实凭证必须立即轮换。
- 仓库不再保存明文 key；运行期只从 `LLM_API_KEY` / `XING_API_KEY` 等环境变量读取。

### 4.9 P0-5：真实模板覆盖（17 模板 × 2 条）

探针 `tools/intent_coverage.py` 复用生产链路的 `build_intent_prompt` + `parse_intent_response`，
真实调用 intent 槽（`deepseek/deepseek-flash` + `reasoning_effort=none`），34 条原话全部落盘。

- 覆盖 **17 个模板 / 34 条样本**（每个模板 ≥2 条），成功 **25/34 = 73.53%**。
- 成功口径：类别一致 + `ok`/`needs_confirmation`（destructive 未确认）+ 无 missing 必填字段。
- 明细：[p3e-intent-coverage.md](../reports/evidence/p3e-intent-coverage.md)（按模板成功率 + 失败样本）、
  [p3e-intent-coverage.json](../reports/evidence/p3e-intent-coverage.json)、
  [p3e-deepseek-intent-coverage.jsonl](../reports/evidence/p3e-deepseek-intent-coverage.jsonl)。

按模板成功率（17 个模板）：move 100%、work 100%、trade 100%、buy 100%、sell 100%（均为 destructive 待确认）、
apprentice 100%、marry 100%（待确认）、found_sect 100%（待确认）、escort 100%、debt_settle 100%（待确认）、
consume 50%、join_sect 50%、quest_accept 50%、quest_deliver 50%、talk 50%、cultivate 0%、open_shop 0%。

失败集中在模型分类而非 grounding：8 条 `unknown_category`（喝/练功/入社/摆摊/接任务等口语），
1 条 `shop_type=stall` 与“店铺”不匹配被 grounding 拦下，1 条 talk 被模型判成 trade。
这正是“按模板报成功率”要暴露的问题；未调 prompt 掩盖。

### 4.10 换模型 2 天真实跑（与 Xing 并列，不可比）

配置：`intent` / `decision` = `deepseek/deepseek-flash` + `reasoning_effort:none`；
`dialogue` = `auto/deepseek-v4.1-flash` + `enable_thinking:false`。12 NPC / seed 42 / 2 天。

| 指标 | P3-E deepseek 2 天 | P3-D Xing 2 天 | 说明 |
|---|---|---|---|
| 端点 | `127.0.0.1:3425` | `127.0.0.1:3427` | 不同网关 |
| content_hash | `085702ab…` | `ced9cd63…` | **内容版本不同，不可比** |
| 事件 / 拒绝 | 222 / 0（0.00%） | 208 / 1（0.48%） | — |
| 决策槽 p50 / p95 | 0.995s / 1.552s | 0.002s / 0.111s | Xing 决策槽是假端点 |
| 意图槽 p50 / p95 | 0.823s / 0.952s | 17.124s / 45.046s | 关键差距 |
| schema 失败 / fallback | 0 / 0 | 0 / 0 | — |
| grounding 拦截 | 3（hours×1、message×2） | 0 | 新模型会改写字句/空值 |
| 意图 ok / 澄清 / 拒绝 | 4 / 4 / 11（unknown_category 11） | 18 / 0 / 1（429） | 新模型更常输出 unknown |
| 守恒误差 / replay | 0 / MATCH | 0 / MATCH | 均为 0 |
| 墙钟（2 天 12 NPC） | 16.2s | 284.0s | 含真实等待 |

- 费用：`$0.082242`（decision `$0.051331` + intent `$0.030911`），38 次调用，无降级。
- `thinking` 参数：`reasoning_effort=none` 被接受 decision 5/19、intent 19/19；`thinking_param_ignored=0`。
- **`dialogue` 槽 0 次调用**：本轮 2 天没有玩家在场对话，文笔/角色扮演质量未评测，不能据此宣称对话槽可用。
- 事件哈希：`state_final=9129402e…`、`event_final=65fb7c2a…`；事件日志回放 MATCH。

---

## 5. API 契约证据

- 契约：[docs/player-api.md](../docs/player-api.md)
- 真实调用样例（原样 JSON）：契约 §10.1–§10.8，其中 §10.7–§10.8 专门覆盖运行中新账号首次发放与二次进入。
- 回归测试：`tests/test_p3e_api.py`（enter / state / action / talk / messages / intents / leave / reenter / 不铸币 / unknown_location / not_adjacent / tick+6）。
- 首次发放回归：`tests/test_p3e_endowment.py`（5 个用例，见 §4.5）。

---

## 6. 测试与框架（冻结验收）

| 项 | 命令 | 结果 |
|---|---|---|
| selfcheck 4/4 | `./scripts/selfcheck.sh` | **PASS**（4 项全部通过，含玩家闭环） |
| pytest | `.venv/bin/python -m pytest -q` | **182 passed**，0 failed |
| framework 6/6 | `.venv/bin/python tools/framework_check.py` | **FRAMEWORK CHECK: PASS** |
| replay（事件日志） | `.venv/bin/python tools/replay.py --run runs/p3e-deepseek-real-2d-12npc` | **MATCH** |
| 干净 worktree replay | `git worktree add` 后同命令 | **MATCH** |
| 十分钟走查 | `.venv/bin/python tools/playthrough.py` | **EXIT=0**，硬验收全 PASS |

## 7. 关闭状态与未验证项

| 项 | 状态 | 证据 / 影响 |
|---|---|---|
| P0-1 生产路径 429 退避 | **闭合** | 默认 `time.sleep` + 默认 25s；不注入 `sleep_fn` 的测试通过（§4.6） |
| P0-2 grounding 六用例 | **闭合** | `tests/test_p3e_grounding.py` 6/6（§4.7） |
| P0-3 延迟口径 | **闭合** | `attempt_latency` / `call_latency` 分列；2 天 run 分位见 §4.10 |
| P0-4 证据与密钥 | **闭合** | `reports/evidence/` + `docs/evidence.md`；命令文件名修正；key 脱敏与 README 轮换提示（§4.8） |
| P0-5 模板覆盖 | **闭合** | 17 模板 × 2 条真实录制，按模板成功率 73.53%（§4.9） |
| 换模型真实 2 天 | **闭合** | `deepseek-flash` / `deepseek-v4.1-flash` 配置 + 12 NPC / 2 天真实跑 + 与 Xing 并列（§4.10） |
| 冻结验收 | **闭合** | selfcheck 4/4、182 passed、framework 6/6、replay MATCH（含干净 worktree） |
| `dialogue` 槽真实文笔 | **未验证（降级）** | 本轮 2 天 `dialogue` 0 次调用；只验证了配置与参数（`enable_thinking:false`），文笔未评测 |
| P0-5 部分模板 0% 通过 | **已知缺口** | cultivate / open_shop 0/2，5 个模板 50%；均为模型分类问题，已在 §4.9 逐条列出 |
| 不可逆动作 / 托管 | **未开放** | 伤害、抢掠、强迫婚配、驱逐仍关闭；离线 custody/dormant 未开放 |
