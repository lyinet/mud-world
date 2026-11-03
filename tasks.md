# Tasks · delivery-p2.2（P2.1 任务保留）

## P2.2 整改

### P1 对话持久化
- [x] `ui/adapter.py`：直接把 run 的 `events.sqlite` 交给 `Writer`，对话 effect 追加到持久 store。
- [x] 对话写入后同步 `world_hash.txt` / `event_hash.txt` / `summary.json`，刷新与 replay 可重建。
- [x] `src/wsim/agent/dialogue.py`：按 `turn` + `last_reply` 轮换台词与引导句，连续 3 轮不重复。
- [x] `tests/test_talk_action.py`：3 轮不重复、跨 3 日累积（affection 24 / trust 64）、关库重开仍在。
- 验证：`python3 -m pytest -q tests/test_talk_action.py`；`tools/replay.py --run /tmp/p22v-ui` → MATCH。

### P2 `open_shop` 门槛
- [x] `content/economy.yaml`：`shop_opening.expected_wage_days: 3` + `expected_staff`。
- [x] `src/wsim/actions/common.py`：`treasury_open_shop_buffer` 只按店铺岗位工资计算。
- [x] `business.py` / `intent.py` 同步新缓冲；`tests/test_treasury_revenue.py` 更新断言。
- 验证：72d `open_shop_postponed=0`，开店 11 次。

### P3 UI 任务指引
- [x] `/api/quests` + 任务板：发布人地点 / 同行 / 可接 / 提示。
- [x] `_event_hint`：拒绝事件输出可执行一句话。
- [x] NPC 详情显示当前地点与最近动作。

### P4 文档
- [x] `reports/delivery-p2.2.md`：四项改动、前后数字、K1 `non_comparable`、税差舍入、失败数口径。
- [x] `reports/delivery-p2.1.md` 顶部加 P2.2 更正指针；旧两份报告保留。

### P5 冻结
- [x] commit 代码 → `git tag -a delivery-p2.2`（附注 commit + content_hash）。
- [x] 从 tag 干净重跑 10d / 72d，replay MATCH，framework 5/5，pytest 89 passed。
- [x] 回填 `reports/delivery-p2.2.md` 并提交文档。

---

## P2.1 任务（历史）

## T1 状态与 Effect 底座
- [x] `src/wsim/world/state.py`：加 `talk_ledger`、`quest_stats`、`quests_expired_total`；
      同步 `clone()`、`to_dict()`。
- [x] `src/wsim/world/effects.py`：
      - `EFFECT_OPS` 加 `talk_ledger`、`quest_prune`；
      - `memory_add` 支持可选 `source`；
      - `talk_ledger` 覆盖写入；
      - `quest_prune` 只删 terminal 状态，保留 stats；
      - `quest_create/complete/fail` 维护 `quest_stats` 与 `quests_expired_total`。
- 验证：`python3 -m pytest -q tests/test_replay.py tests/test_effects.py`（如文件名存在）。

## T2 `talk` 执行器
- [x] 新建 `src/wsim/agent/dialogue.py`：`PLAYER_ID`、`extract_persona_voice`、`compose_talk`。
- [x] `src/wsim/actions/social.py`：新增 `TalkAction`（preconditions + apply + 上限 clamp）。
- [x] `src/wsim/actions/registry.py`：注册 `TalkAction`。
- [x] 新建 `tests/test_talk_action.py`（台词消费、记忆、±2/±8 上限、守恒 0、审批链）。
- 验证：`python3 -m pytest -q tests/test_talk_action.py`。

## T3 任务板健康度
- [x] `content/economy.yaml`：`quests` 增加 5 个新阈值。
- [x] `src/wsim/actions/quests.py`：
      - `SystemQuestPostAction` 加资金/物品/完成率/单模板过期/全局过期五道预检；
      - `SystemQuestFailAction` 追加 `quest_prune`。
- [x] `src/wsim/sim/headless.py`：`quests.health`（active/expired_on_board/expired_total/
      by_template + completion_rate）。
- [x] 新建 `tests/test_quest_health.py`（5 条规则）。
- 验证：`python3 -m pytest -q tests/test_quest_health.py`。

## T4 金库收入侧杠杆
- [x] `src/wsim/actions/common.py`：`tax_split_effects`、`daily_wage_bill`、
      `treasury_open_shop_buffer`。
- [x] `content/economy.yaml`：`taxes.transaction_treasury_share: 0.5`。
- [x] `src/wsim/actions/commerce.py`：Buy/Sell/Trade 三处税改分流。
- [x] `src/wsim/actions/business.py`：`OpenShopAction.preconditions` 加 `treasury_buffer`。
- [x] `src/wsim/sim/intent.py`：Planner `postponed_open_shop` + build_shop 延后；
      MockReasoner 汇总计数。
- [x] `src/wsim/sim/headless.py`：treasury `tax_to_treasury`/`tax_burned`/
      `open_shop_postponed`，intent_audit 同步。
- [x] 新建 `tests/test_treasury_revenue.py`。
- 验证：`python3 -m pytest -q tests/test_treasury_revenue.py`。

## T5 UI 落地
- [x] `ui/adapter.py`：session writer（复制 events.sqlite）+ 三身份 gateway；
      `dialogue()` 走 propose → request → approve → apply，返回 landed/seq/memory/relation。
- [x] `ui/index.html`：文案改「已落地」，展示 seq/outcome/记忆/关系变化。
- [x] 手工验证：`python3 ui/server.py --run runs/delivery-72d --port 8000`，
      一次对话后返回 `landed=true` 且 `seq` 非空。
- 验证：`python3 -m pytest -q` 后启动 UI 抽查。

## T6 文档纠错
- [x] 复制 `reports/delivery-p2.md` → `reports/delivery-p2.1.md`。
- [x] 修正 180 章节、931/754/177 拆分、护栏归因。
- [x] 新增 talk / 任务板 / 金库三节改法与前后数字。

## T7 重新冻结
- [x] commit 全部改动。
- [x] 计算新 `content_hash`，`git tag -a delivery-p2.1`（附注 commit + hash）。
- [x] `git worktree add /tmp/wsim-p21b delivery-p2.1`，跑 10d/72d、replay、framework_check、
      pytest。
- [x] 回填 `reports/delivery-p2.1.md`：tag/commit/content_hash、10d/72d 哈希、
      对话落地数、记忆条数、关系变化、过期单数、拒绝率、金库期末、税回流、
      开店延后次数、未达标项。
- [x] 清理临时 worktree；确认 `git status --short` 干净。
