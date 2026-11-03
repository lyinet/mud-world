# Spec · delivery-p2.1 复核整改

目标：修掉复核报告的 3 处「交给人玩最先崩」+ 3 处文档错误，重新冻结 `delivery-p2.1`。
约束：不 mint、不增发；守恒误差必须为 0；不引入真实模型；确定性回放不变。

## 0. 验收口径

全部改完后在 tag `delivery-p2.1` 的干净 worktree 执行：

```bash
python3 sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id delivery-10d
python3 sim/run_headless.py --days 72 --seed 42 --npc 0 --run-id delivery-72d
python3 tools/replay.py --run runs/delivery-10d
python3 tools/replay.py --run runs/delivery-72d
python3 tools/framework_check.py          # 必须 5/5 PASS
python3 -m pytest -q                      # 全过
```

硬门槛：
- 10d/72d replay 均 MATCH；
- framework_check 5/5；
- 72d `conservation_error == 0`、`minted_total` 与本轮前一致（47,150）；
- 72d 拒绝率 `<= 0.02`；
- 过期单数相对本轮前 206 显著下降（目标 <= 60，实际以健康度为准）；
- `talk` 能在世界内落地（记忆 + 关系变化），且不产生任何钱变动。

## 1. `talk` 真实执行器

### 1.1 新增 `src/wsim/agent/dialogue.py`

- `PLAYER_ID = "player:visitor"`
- `extract_persona_voice(text: str) -> dict`：从人设 Markdown 提取
  - `quotes`: 所有 `> ` 开头的台词行（去掉引号）；
  - `style`: `## 说话风格` 章节正文（最多 120 字）；
  - `reaction`: `## 遇事反应倾向` 章节正文（最多 120 字）。
- `compose_talk(persona_text, npc_id, npc_name, npc_role, message, tick) -> dict`
  - 确定性：用 `sha256(f"{npc_id}|{message}|{tick}")` 选台词，不读墙钟、不用 `random`；
  - 情感：对 message 做关键词判定（感谢/夸/帮忙/请 → 正；骂/滚/骗/笨 → 负），
    幅度 1 或 2（hash 决定），符号由关键词决定，无关键词时按 hash 取 ±1；
  - 返回 `{reply, quote, style, affection_delta, trust_delta}`；
  - `reply` 必须包含所选台词原文；无台词时用 style 兜底；
  - 上限不在这里做，由 Action 统一 clamp。

### 1.2 状态字段（`src/wsim/world/state.py`）

新增：
```python
talk_ledger: dict[str, dict[str, Any]] = field(default_factory=dict)
quest_stats: dict[str, dict[str, int]] = field(default_factory=dict)
quests_expired_total: int = 0
```
`clone()` 与 `to_dict()` 同步（deepcopy / sorted）。`to_dict()` 输出三个新键，
保证 replay 与实时状态哈希一致。

`talk_ledger[npc]` 形状：
```json
{"day": 12, "day_affection": 3.0, "day_trust": 2.0,
 "last_tick": 1728, "tick_affection": 1.0, "tick_trust": 1.0}
```

### 1.3 Effect 扩展（`src/wsim/world/effects.py`）

- `_KNOWN_OPS`/`EFFECT_OPS` 增加 `talk_ledger`、`quest_prune`。
- `memory_add` 可选 `source` 字段，写入 entry：`{tick, text, importance, source}`；
  默认 `source=""`。超过 80 条仍从头部裁剪。
- `talk_ledger`：`{npc, day, day_affection, day_trust, last_tick, tick_affection, tick_trust}`，
  校验 npc 存在；覆盖写入 `state.talk_ledger[npc]`。
- `quest_prune`：`{ids: [str]}`；只允许删除 `status in ("expired","failed","completed")`
  的任务；删除后保留 `quest_stats` 统计（历史不回退）。
- 任务统计维护：
  - `quest_create`：`quest_stats[tid]["created"] += 1`；
  - `quest_complete`：`quest_stats[tid]["completed"] += 1`；
  - `quest_fail` 且 open→expired：`expired += 1`、`quests_expired_total += 1`；
  - `quest_fail` 且 accepted→failed：`failed += 1`。
  - `quest_stats` 缺省键自动补 0。
- `talk` 不产生 `mint/burn/transfer`；测试断言。

### 1.4 `TalkAction`（`src/wsim/actions/social.py`）

`verb = "talk"`。

`preconditions`：
- target 实体存在且存活；
- `message` 非空且 `<=200` 字；
- `reply` 非空；
- 不检查资金/物品。

`apply`：
1. 若 `player:visitor` 不存在，先 `create_entity`（name=`访客`，role=`visitor`，
   location=target.location，stamina/max_stamina=100）。
2. 读取 `state.talk_ledger.get(target.id, {})`，按 `tick = intent.tick`、`day = tick // 144`
   重置跨 tick / 跨日累计。
3. 请求的 `affection_delta` / `trust_delta` 分别 clamp：
   - 单 tick 累计 `[-2, 2]`；
   - 单日累计 `[-8, 8]`；
   - 计算公式：`d = max(lower, min(upper, requested))`，lower/upper 同时受 tick 与 day 余量约束。
4. 产生 Effect：
   - `memory_add`（target）：`text=f"访客说：{message}"`，`source="talk:player"`，
     `importance=0.4`；
   - `memory_add`（target）：`text=f"我答：{reply}"`，`source="talk:npc"`，`importance=0.3`；
   - `relation_upsert`（player → target，kind=`acquaintance`）：
     - 新关系：`affection=d_aff`，`trust=40 + d_trust`；
     - 已有关系：`affection_delta=d_aff`，`trust_delta=d_trust`；
   - `talk_ledger`：写入新的 tick/day 累计；
   - `set_field` 更新双方 `last_action_tick`。
5. 严禁任何钱相关 Effect。

注册：`src/wsim/actions/registry.py` 引入并 `reg.register(TalkAction(content))`。

### 1.5 UI 落地（`ui/adapter.py` + `ui/index.html`）

`WorldUIAdapter._load()` 末尾新增 session writer：
- 读取 `run/events.sqlite`；不存在时 `self.session_writer=None`，对话返回
  `landed=false, error="run_missing_events_sqlite"`，不崩 UI；
- 存在时：`tempfile.mkdtemp(prefix="wsim-ui-")` → 复制 sqlite 到该目录；
- `content = load_content(WORLD_ROOT, min_npcs=0, allow_fixture=False, use_content=True)`；
- `EventStore(session_db)`、`build_registry(content, seed)`；
- `Writer(self.state.clone(), store, registry, seed)`；
- gateway 改为：
  ```python
  identities={
      "ui": PERMISSION_PROPOSE,
      "ui:requester": PERMISSION_ADMIN,
      "ui:reviewer": PERMISSION_ADMIN,
  }
  writer=self.session_writer
  state_provider=lambda: self.session_writer.state
  ```
- `atexit.register(shutil.rmtree, session_dir, True)`。

`WorldUIAdapter.dialogue()` 新流程：
1. `talk = compose_talk(persona, npc_id, name, role, message, tick)`；
2. `intent = {actor: PLAYER_ID, verb: "talk", args: {target: npc_id, message, reply,
   affection_delta, trust_delta, from: "visitor"}, tick, source: "ui:proposal"}`；
3. `ui` 调 `world.propose_intent`（保留 outbox/审计，证明提案入口仍在）；
4. `ui:requester` 调 `admin.request`；
5. `ui:reviewer` 调 `admin.approve`；
6. `ui:reviewer` 调 `admin.apply` → `seq/outcome`；
7. 从 `session_writer.state` 读取 target 的最近记忆与 acquaintance 关系值；
8. 返回：
   ```json
   {"landed": true, "seq": 12608, "outcome": "ok", "verb": "talk",
    "reply": "...", "memory": [...], "relation": {"kind":"acquaintance",
    "affection":1.0,"trust":41.0,"affection_delta":1.0,"trust_delta":1.0},
    "proposal": {"accepted": true}, "notice": "对话已落地：记忆与关系写入事件日志。"}
   ```
9. 同步 `self.state = self.session_writer.state`（UI 后续读一致）。

`ui/index.html`：
- 删除「这是提案，不是直接改世界」「state_unchanged=true」「本版没有 talk 执行器」；
- 标题/提示改为「对话已落地」；
- 结果区展示：事件 `seq`、`outcome`、记忆条数、关系 affection/trust 前后值；
- 按钮文案「发送（落地）」；保留审计详情折叠。

### 1.6 测试 `tests/test_talk_action.py`

1. `compose_talk`：回复包含人设卡中的台词原文。
2. 一次对话：`apply_effects` 后 target 的 `state.memories[target]` 出现
   `访客说：...` 且 `source="talk:player"`。
3. 上限：同一 tick 连续 5 次 +2 请求，累计 `affection <= 2`、`trust <= 2`；
   跨日 10 次后累计 `<= 8`。
4. 守恒：`conservation(state)["conservation_error"] == 0`，且 `minted_total`、
   `burned_total` 未变。
5. 审批链：`MCPGateway` + 临时 `EventStore/Writer` 走
   `propose_intent → admin.request → admin.approve → admin.apply`，断言事件 seq 存在、
   状态中记忆已写入（proposer 与 approver 不同身份）。

## 2. 任务板健康度

### 2.1 配置（`content/economy.yaml`，`quests:` 段）

```yaml
quests:
  max_concurrent: 2
  max_concurrent_escort: 1
  accept_max_hops: 3
  expired_cap_per_template: 3
  expired_cap_global: 20
  min_samples_for_rate: 10
  min_completion_rate: 0.2
  expired_retention_days: 7
```

### 2.2 发单预检（`src/wsim/actions/quests.py` · `SystemQuestPostAction.apply`）

在现有「同模板无 open/accepted」「非 repeatable 已有则跳过」之后，依次增加：
1. **资金**：`reward_money > state.account_balance(wallet(giver))` → 跳过；
2. **物品**：模板含 `giver_item` 时，`giver.inventory[giver_item] < giver_item_qty(默认1)` → 跳过；
3. **模板完成率**：`stats = state.quest_stats.get(tid, {})`；
   `created >= min_samples_for_rate` 且 `completed / created < min_completion_rate` → 跳过；
4. **模板过期上限**：`state.quests` 中该模板 `status == "expired"` 的数量
   `>= expired_cap_per_template` → 跳过；
5. **全局过期上限**：所有 `status == "expired"` 数量 `>= expired_cap_global` → 跳过。

以上全部通过才 `quest_create`。不发单即不产生 intent/事件，不计入非法率。

### 2.3 过期清理（`SystemQuestFailAction.apply`）

在现有超时处理后：
- 收集 `status in ("expired","failed")` 且
  `intent.tick - completed_tick > expired_retention_days * 144` 的任务；
- 若非空，追加 `Effect("quest_prune", {"ids": sorted(ids)})`；
- `quest_prune` 只删 `state.quests` 记录，`quest_stats` 与 `quests_expired_total` 保留。

### 2.4 报告（`src/wsim/sim/headless.py` · quest_summary）

新增：
```json
"health": {
  "active": "open+accepted",
  "expired_on_board": "state 中 status=expired 的数量",
  "expired_total": "state.quests_expired_total",
  "by_template": {
    "<tid>": {"created": n, "completed": n, "failed": n, "expired": n,
              "completion_rate": 0.0}
  }
}
```
保留原有 status/by_template 字段，避免旧报告口径断裂。

### 2.5 测试 `tests/test_quest_health.py`

- giver 钱包 < reward → 不发单；
- 样本 >=10 且完成率 <20% → 不发单；
- 每模板 3 张 retained expired → 不发单；
- 全局 20 张 retained expired → 不发单；
- 过期超过 7 天的任务被 `quest_prune` 删除，`quest_stats` 不变。

## 3. 金库收入侧杠杆

### 3.1 交易税分流（`src/wsim/actions/common.py`）

```python
def tax_split_effects(content, account, amount, reason="transaction_tax"):
    share = float((content.economy.get("taxes") or {}).get("transaction_treasury_share", 0.5))
    to_treasury = int(round(amount * share))
    burned = amount - to_treasury
    ...
```
- `to_treasury > 0` → `Effect("transfer", {src: account, dst: "treasury:region",
  amount: to_treasury, reason: f"{reason}_treasury"})`；
- `burned > 0` → `Effect("burn", {account, amount: burned, reason})`；
- `account == "treasury:region"` 时不产生 transfer；
- 总额必须等于 `amount`。

`content/economy.yaml`：
```yaml
taxes:
  transaction_treasury_share: 0.5
```

`src/wsim/actions/commerce.py`：Buy / Sell / Trade 三处 transaction tax 的
`Effect("burn", ...)` 全部替换为 `tax_split_effects(...)`。

### 3.2 `open_shop` 缓冲延后

`src/wsim/actions/common.py` 新增：
```python
def daily_wage_bill(state, content) -> int:
    return sum(content.wage_for(e.role) for e in state.entities.values()
               if e.status == "alive" and e.employed_at)

def treasury_open_shop_buffer(state, content, cost: int) -> int:
    reserve = int((content.economy.get("work") or {}).get("employer_reserve", 2000))
    return reserve + int(cost) + 7 * daily_wage_bill(state, content)
```

`src/wsim/actions/business.py` · `OpenShopAction.preconditions`：
- 保留原有资金/地点/重复检查；
- 新增：`treasury_balance(state) < treasury_open_shop_buffer(...)` →
  `["treasury_buffer"]`（动作层兜底）。

`src/wsim/sim/intent.py` · `Planner`：
- `__init__` 增加 `self.postponed_open_shop = 0`；
- `build_shop` 分支在 `cash >= cost` 判断前先检查
  `treasury_balance(state) < treasury_open_shop_buffer(state, content, cost)`；
  不满足则**不生成 intent**，`self.postponed_open_shop += 1`，返回原位置/游标/现金。
- `MockReasoner.__init__` 增加 `self.postponed_open_shop = 0`；
  `decide()` 末尾汇总 `self.postponed_open_shop += self.planner.postponed_open_shop`，
  然后 `self.planner.postponed_open_shop = 0`。

### 3.3 报告（`src/wsim/sim/headless.py` · treasury_summary + intent_audit）

从 `events` 逐条 Effect 汇总：
- `tax_to_treasury`：`transfer` 且 `reason == "transaction_tax_treasury"` 的 amount 总和；
- `tax_burned`：`burn` 且 `reason == "transaction_tax"` 的 amount 总和；
- `treasury_summary` 增加：`tax_to_treasury`、`tax_burned`、
  `open_shop_postponed`（取 `mock_reasoner.postponed_open_shop`）；
- `intent_audit` 增加 `"postponed_open_shop"`。

### 3.4 测试 `tests/test_treasury_revenue.py`

- `tax_split_effects(100)` → 50 transfer + 50 burn；守恒 0；`minted_total` 不变；
- `tax_split_effects` 金额守恒（transfer + burn == amount）；
- `open_shop` 在 `treasury < buffer` 时 precondition 返回 `treasury_buffer`；
- Planner 在 buffer 不足时不产生 `open_shop` intent 且 `postponed_open_shop >= 1`。

## 4. 文档纠错

目标文件：`reports/delivery-p2.1.md`（由 `reports/delivery-p2.md` 复制改写，
旧文件原样保留）。

1. 「60 个章节（30 卡 × 6 节）」→「**180 个章节（30 卡 × 6 节）**；60 条台词」；
2. 「决策数 931」→ 拆成：
   - `decisions_total = 931`
   - `model_calls = 754`
   - `replan = 177`
   - 注明：replan 只做本地重规划，不建 prompt、不记成本，因此 931 ≠ 931 次付费调用。
3. 护栏归因改为复核口径：
   - 护栏期余额企稳：**−1.25 文/天**，此前 **−533 文/天**；
   - 主因是工资流出 **866 → 182 文/天**（`work` 38.6 → 8.8 次/天）；
   - 护栏暂停采购反而让金库流入 **239 → 30 文/天**，方向为负；
   - 结论：护栏「有动作但方向错，被更大的正向变化掩盖」，不得写「止血无效」。
4. 报告新增三节：`talk` 落地、任务板健康度、金库收入侧杠杆，
   各含改法 + 前后数字 + 新 tag/哈希。

## 5. 重新冻结

1. `git add` 全部改动并 commit（message：`fix(P2.1): talk 执行器 + 任务板健康度 + 金库税回流`）。
2. 计算新 `content_hash`：`load_content(...).content_hash`。
3. `git tag -a delivery-p2.1 -m "<commit> content_hash=<hash>"`。
4. `git worktree add /tmp/wsim-p21 delivery-p2.1`，在该 worktree 跑第 0 节全部命令；
   记录 10d/72d state/event hash、replay 结果、framework 5/5、pytest 数量。
5. 把干净 run 的数字回填 `reports/delivery-p2.1.md`（tag、commit、content_hash、
   10d/72d 哈希、三项新指标前后值、未达标项）。
6. 清理临时 worktree（`git worktree remove --force /tmp/wsim-p21`），保留 `runs/` 供 UI。

## 6. 明确不做

- 不接真实模型 / Provider；
- 不提高镖银、不降低门派门槛、不 mint 补金库；
- 不修改 K1/K3 口径；
- 不删除旧报告 `reports/delivery-p2.md`；
- 不为 `talk` 增加 NPC→NPC 自动闲聊（本轮只做玩家→NPC 落地）。

# P3-C：安全/正确性四个缺陷 + 缺 location 追问 + 玩家最小切片

不调用真实 Xing API；全部用假端点与单元测试验收。

## 0. 需求到实现的对照（验收时逐条回填）

| 用户要求 | 实现位置 | 验收测试 |
| --- | --- | --- |
| 对话身份不写死 `player:visitor`；缺失拒绝；旧事件标 legacy | `actions/social.py` `_speaker_id`；`core/models.py` `Event.legacy`；`world/store.py` `_infer_legacy` | `test_player_id_arg_overrides_actor`、`test_actor_session_identity_is_used`、`test_talk_without_identity_is_rejected`、`test_legacy_events_marked_without_hash_change` |
| 忽略调用方 `affection_delta` / `reply`；记 `spoofed_arg` 但不整包拒绝 | `actions/social.py` `TalkAction.apply`；`world/effects.py` `audit_log` | `test_talk_ignores_spoofed_reply_and_deltas`、`test_talk_records_spoofed_arg_audit` |
| 玩家 tick 单调且不写未来；`stale_tick`；NPC 不受影响 | `world/writer.py` `_is_player_intent` / `_player_ticks` | `test_writer_rejects_stale_player_tick`、`test_writer_rejects_future_player_tick`、`test_writer_npc_tick_not_checked` |
| `found_sect` 按内容包 `allowed_roles` 拦截 | `actions/business.py` `FoundSectAction.preconditions` | `test_found_sect_role_not_allowed`、`test_found_sect_allowed_role_passes` |
| 意图 prompt 列相邻地点 + 上一句位置；clarification ≤3% | `agent/prompt.py`、`agent/intent_model.py`、`agent/provider.py`、`tools/fake_*_server.py` | `test_intent_prompt_lists_reachable_locations`、`test_intent_prompt_includes_previous_location`、`test_fake_intent_resolves_reachable_name`；72d 假端点统计 |
| `player:<id>` 实体 + enter/presence/leave；status 已登记 | 新建 `actions/player.py`；`actions/registry.py`；`sim/headless.py` | `test_player_enter_registers_entity_wallet_presence`、`test_player_presence_updates_location`、`test_player_leave_sets_offline`、`test_player_minimal_chain_replay_match` |
| 冻结与干净重跑 | `tasks.md`、`reports/p3c-report.md` | pytest / framework / 10d / 72d / worktree 记录 |

## 1. 四个缺陷（必须修，各配测试）

### 1.1 对话身份不再写死 `player:visitor`

- 文件：`src/wsim/actions/social.py`
- 新函数 `_speaker_id(state, intent)`：
  - 优先 `intent.args["player_id"]`；
  - 否则玩家会话身份 `actor.startswith("player:")`；
  - 否则只有 actor 已在 `state.entities` 中（服务端调度的 NPC）才用 actor；
  - 都取不到返回空串。
- `TalkAction.preconditions` 空身份 → `["missing_player_id"]`；
  `TalkAction.apply` 空身份 → `Reject("missing_player_id")`。
- 实体/钱包/记忆/`relation_upsert`/`talk_ledger`/`set_field` 全部使用解析出的身份，
  删除 `PLAYER_ID` 引用（`PLAYER_NAME` 仅作无 `player_name` 时的显示名回退）。
- 迁移：`src/wsim/core/models.py` 的 `Event.legacy`；`src/wsim/world/store.py`
  旧库无 `legacy` 列时不 ALTER，读取时 `_infer_legacy(actor, verb)` 把
  `player:visitor` 的 talk/presence 事件标记 `legacy=True`；
  `src/wsim/world/hash.py`、`EventStore.event_hash` 用 `to_canonical_dict()`，
  `legacy` 不进哈希，旧 run 哈希与回放不变。

### 1.2 服务端忽略调用方关系增量与回复

- 文件：`src/wsim/actions/social.py`
- `TalkAction.apply` 用 `compose_talk(...)` 重新计算 `reply/affection_delta/trust_delta`；
  `intent.args` 里出现的 `affection_delta` / `trust_delta` / `reply` 一律不参与计算。
- 命中字段时追加 `Effect("audit_log", {"kind":"spoofed_arg", ...})`，
  记录 actor/tick/fields，不整包拒绝。
- `src/wsim/world/effects.py`：`EFFECT_OPS` 加 `audit_log`；只接受非空 kind，
  写 `WorldState.audit_log`，最多保留 500 条。
- `src/wsim/world/state.py`：`WorldState.audit_log` 仅在非空时进入 `to_dict()`，
  保持旧 run 状态哈希不变。

### 1.3 Writer 校验玩家 tick

- 文件：`src/wsim/world/writer.py`
- `_is_player_intent`：actor 以 `player:` 开头且 `source not in (npc, planner, system)`。
- `submit` 在取 action 前校验：`tick >= self._player_ticks[actor]` 且
  `tick <= state.tick`；否则 `_record_reject(..., "rejected:stale_tick")`。
- 通过后更新 `_player_ticks[actor]`；NPC/planner/system 不受影响。

### 1.4 `found_sect` 在 Action 层执行 `allowed_roles`

- 文件：`src/wsim/actions/business.py`
- 导入 `role_allowed`；`FoundSectAction.preconditions` 在 `require_entity` 后：
  `if not role_allowed(self.content, "found_sect", ent.role): return ["role_not_allowed"]`。
- 角色清单来自 `content/verbs.yaml` 的 `found_sect.allowed_roles`。
- `apply` 不重复校验，Writer 只会在 preconditions 通过后调用。

## 2. 缺 location 追问

- 文件：`src/wsim/agent/prompt.py`
- `build_state_summary` 新增：
  - `reachable_locations`：当前地点 `exits` 的 `[{id,name}]`（取自世界状态）；
  - `previous_location`：`{id,name}`（由 provider 每轮写入，缺省为当前地点）。
- 文件：`src/wsim/agent/intent_model.py`
- `build_intent_prompt` 新增显式段落：
  - `【你刚才在哪】`；
  - `【当前可到达的相邻地点（只可从这些里选 destination）】`，逐行 `名称（id）`；
  - `【当前上下文】` JSON 同步带 `reachable_locations` / `previous_location`；
  - system prompt 明确：目的地只能来自相邻地点，缺则 `needs_clarification` 且只缺 destination。
- 文件：`src/wsim/agent/provider.py`
- `LLMReasoner._prepare_intent` 维护 `_last_locations[npc]`，把上一轮地点写入
  summary 的 `previous_location`，处理完更新为当前地点。
- 文件：`tools/fake_llm_server.py`
- decision 模式只用 `reachable_locations` 的 `name` 组装 “去{name}看看”，
  不再使用 `exits[0]` 的地点 id。
- 文件：`tools/fake_intent_server.py`
- `去某地` 参数去掉尾部 “看看/走走/逛逛/一趟”，并优先在相邻地点清单里查；
  查不到才返回缺 destination。

验收：72d 假端点 run 中 `needs_clarification / intent_calls` 从 9.45%（195/2064）
降到 ≤3%；仍缺的按 missing 字段类别列出（预期只剩非 destination 类）。

## 3. 玩家最小切片

- 新建 `src/wsim/actions/player.py`：
  - `PlayerEnterAction`（verb `player_enter`）：解析身份；`at` 必须在世界地点；
    缺实体则 `create_entity`（role `visitor`），缺账户则 `create_account wallet:<id>`；
    `presence=online`、`last_seen_tick=tick`、`location=at`；重复进入若已在线返回
    `already_online`。
  - `PlayerPresenceAction`（verb `player_presence`）：实体必须存在；可选 `at`
    校验后 `set_location`；刷新 presence/last_seen_tick。
  - `PlayerLeaveAction`（verb `player_leave`）：实体必须存在；`presence=offline`，
    刷新 last_seen_tick。
- `src/wsim/actions/registry.py` 注册三个动作。
- `src/wsim/sim/headless.py`：`cfg.with_players` 时在 tick 0、创世后提交
  `player_enter`（actor `player:visitor`，`at=player_loc`，source `player`），
  让 UI/终端 `status` 显示“已登记”；不自动 leave。
- 玩家动作仍只走 `propose → admin approve → apply → Writer`；不开放杀害/抢掠/强迫婚配。
- 完整链路实测（测试内用临时 EventStore）：
  `player_enter` → `talk`（服务端算关系）→ `player_presence`（换地点）→
  `player_leave` → `tools/replay.py` 回放 MATCH；报告输出各事件 seq 与最终哈希。

## 4. 测试清单

- `tests/test_talk_action.py`：
  - 身份：`args.player_id` 优先、`actor=player:*` 回退、无身份拒绝；
  - spoofed：传 `reply/affection_delta/trust_delta` 时输出服务端回复，
    `audit_log` 有 `spoofed_arg`，关系与不传时一致；
  - 既有上限/守恒测试改为服务端计算口径。
- `tests/test_p3c_security.py`（新）：
  - Writer `stale_tick`（回退 tick、未来 tick、NPC 不受影响）；
  - 旧 schema EventStore 读取 `player:visitor` → `legacy=True`，event hash 不变；
  - `found_sect` 越权 → `rejected:role_not_allowed`。
- `tests/test_p3c_intent_location.py`（新）：
  - prompt 含 `reachable_locations` / `previous_location` 与显式段落；
  - 假 intent 端点对相邻地点名可解析；无地点仍 `needs_clarification`。
- `tests/test_p3c_player.py`（新）：
  - enter/presence/leave 状态与钱包、presence；
  - 完整链路 + replay MATCH。
- 全部测试禁止真实网络请求；假端点进程内启动。

## 5. 验收与冻结

1. `PYTHONPATH=src .venv/bin/python -m pytest -q` 全过。
2. `python3 tools/framework_check.py` 全过。
3. 假端点 10d / 72d：记录 K1、拒绝率、守恒、replay、state/event hash。
4. 写 `reports/p3c-report.md`：四缺陷修复位置与测试名、clarification 前后比例、
   玩家链路 seq/哈希、未做项。
5. commit → `git tag -a delivery-p3c`。
6. `git worktree add /tmp/wsim-p3c delivery-p3c`，从 tag 干净重跑 pytest /
   framework / 10d / 72d，回填报告，清理 worktree。

## 6. 明确不做

- 不调用真实 Xing / 真实决策端点（0 次）；
- 不做完整玩家系统（8 人周）：不开放杀害、抢掠、强迫婚配；
- 不做多玩家会话管理、账号体系、玩家背包 UI；
- 不改 K1/K3 口径，不为达标调参。

# P3-D 真实 Xing 意图槽（2 个游戏日）

目标：把已实测的 Xing 网关（`http://127.0.0.1:3425/v1`，key `magpie`，
`openrouter/xingchen-agi/xing4.0-29b-a4b:free`）接进 `intent` 槽，只跑 2 天，
不跑 72 天真实调用。

## 1. 适配层（`src/wsim/agent/intent_model.py`）

1. 剥围栏：```json / ``` / 围栏前后带解释文字 / 首个 `{` 到末个 `}` 三级回退。
2. 键名归一：
   - 顶层中文（意图/类别/类型/动作）与英文（intent/category/type/action/verb）→ `category`；
   - 顶层中文（参数/字段/槽位）与英文（args/arguments/params/slots）→ `args`；
   - 字段中文（物品/数量/卖家/…）与英文（item/quantity/seller/…）→ 模板字段名；
   - 白名单外的键丢弃并记 `unknown_keys`，不进入参数。
3. 原文 grounding：
   - 字符串值必须出现在「用户原话 + 本轮上下文实体名」中；
   - 数字（含中文数量词）归一后必须回指用户原话里的数字词（“两斗”→ 2，单位斗）；
     上下文里的无关整数（tick/库存）不得为编造数量背书；
   - 找不到依据的字段按缺失处理，绝不填充：必填缺失 → `needs_clarification`，
     可选字段被拦则丢弃并记 `blocked`；
   - 归一结果记 `normalizations`（value/unit/source），source 保留原文片段。
4. 兼容：不传 `utterance` 的旧调用（P3-B 单测）grounding 关闭，语义不变。

## 2. 限额、重试与观测（`src/wsim/agent/provider.py`）

- intent 槽配置：`max_tokens=1500`、`timeout_s=60`、`max_retries=2`；
- 429 分类为 `http_429` 并重试，固定 25s 退避，最多 2 次；其余仍指数退避；
- 每次调用记 `usage` 与延迟；`summary()` 增 `intent_latency`（min/p50/p90/max）、
  `intent_client_stats`（calls/attempts/retries/http_429/errors）。
- `intent_unknown_keys`、`intent_grounding_blocked/by_field/cases`、
  `intent_normalizations` 进入 `summary()`，供报告与审计。

## 3. 录制回放

- `WSIM_INTENT_RECORD=<path>.jsonl` 时把每次意图返回按 `prompt_sha` 落盘；
- `WSIM_INTENT_REPLAY=<path>.jsonl` 时 intent 槽改用回放 transport，不联网；
  缺记录报 `replay_miss`，不伪造返回。

## 4. 验收

1. `tests/test_p3d_xing_adapter.py` 全过（围栏、键归一、grounding 拦截、
   “两斗”回指、429 25s×2、配置限额、录制回放）。
2. 真实 2 天：`--days 2 --seed 42 --npc 12 --enable-llm`，决策槽用假端点、
   意图槽用真实 Xing；报告调用数、成功率、schema 失败、grounding 拦截、429、
   clarification 比例、K1、拒绝率、守恒、replay、延迟分布。
3. 与 P3-C（全桩）并列，明确标注“真模型 vs 桩，不可直接比较”。
4. `reports/p3d-report.md`；commit → `git tag -a delivery-p3d`；干净 worktree 重跑。

## 5. 明确不做

- 不跑 72 天真实调用；真实 2 天足够。
- 不因真实模型表现调 K1/K3 口径。
