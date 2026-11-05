# 玩家婚姻闭环规格（player-marriage）

- 状态：实现中（本规格先于代码提交）
- 基线：`master` @ `f1f15a7`（其上含 `1709524`）
- 约束：确定性、零模型调用、事件溯源、货币守恒、无玩家世界哈希逐字节不变
- 关键路径：`actions/marriage.py`、`api/session.py`、`world/effects.py`、`world/state.py`、`sim/intent.py`

## 1. 目标

让玩家能对同地点 NPC 求婚、由确定性规则裁决、以事件落库成婚，并在婚后产生可观测的
日常行为（配偶每天到玩家身边问候/带饭/提醒），最终支持和离。NPC-NPC 旧婚姻路径
（`MarryAction`）不改语义、不改数值。

## 2. 状态机

婚姻元数据只保存在 `WorldState.marriages`（键为排序后的 `a|b`），只在“至少一方是
玩家”的婚姻/求婚中出现；没有玩家参与的世界不会产生该键，因此 `state.hash()` 不变。

```
single ──propose(玩家)──> proposed(decision=accept|courtship)
   │                              │
   │                              ├─ decision=courtship ──> courtship（3 世界日有效，可再次求婚）
   │                              ├─ decision=reject ────> rejected（不改变 spouse，终态）
   │                              └─ decision=accept ──> accepted ──marriage──> married
   │
   └─ married ──marriage_end（同地点）──> ended（双方 spouse=None，关系 status=ended）
```

- `single`：`entity.spouse is None` 且没有进行中的求婚/求处。
- `proposed` / `accepted`：同一 API 调用内的过渡态，调用返回前必然进入 `married` 或 `rejected`。
- `courtship`（“再处处看”）：保存 `since_tick` 与 `expires_tick = since_tick + 3*TICKS_PER_DAY`；
  到期后再次求婚按新状态重新裁决。
- `married`：`entity.spouse` 双向写入，`marriages` 记录 `since_tick`、聘礼/信物。
- `ended`：双方 `spouse` 清空，`marriage` 关系 `status=ended`；不返还聘礼（共同财产不在本轮范围）。

不允许状态：一方有 `spouse` 而另一方没有；`married` 但无 active marriage 关系。整条链路
在任何单个事件内都是原子的；多事件链一旦前置校验失败，不写入任何婚姻状态（只写一条
reject 事件，见 §6）。

## 3. 事件（事件日志中的 verb）

| 事件 | actor | 触发 | 关键 effects |
|---|---|---|---|
| `marriage_propose` | 玩家 | API `marriage_propose` | `marriage_meta(status=proposed/courtship/rejected)` + 双方 `memory_add` + `audit_log`（decision/reason） |
| `marriage_accept` | 目标 NPC | 决策 accept 后同一 API 调用 | `marriage_meta(status=accepted)` + NPC 记忆 |
| `marriage_reject` | 目标 NPC | 决策 reject 后同一 API 调用 | 拒绝事件（`outcome=rejected`，`reason=rejected:<code>`）；不改 spouse |
| `marriage` | `system`（args 内含双方） | 决策 accept 后同一 API 调用 | `marry` 双向 spouse/关系、`marriage_meta(status=married, since_tick)`、聘礼 `transfer` / 信物 `item_move`、双方记忆 |
| `marriage_end` | 玩家 | API `marriage_end` | 双方 `spouse=null`、两条 marriage 关系 `status=ended`、`marriage_meta(status=ended)`、双方记忆 |

一次成功求婚的 API 写事件链：`marriage_propose → marriage_accept → marriage → system_tick`
（`event_seq` 连续 +4）。拒绝：`marriage_propose → marriage_reject → system_tick`（+3）。
求处（中间态）：`marriage_propose → system_tick`（+2）。和离：`marriage_end → system_tick`（+2）。

## 4. 决策规则（零模型、可解释）

输入：`affection = max(所有关系类型双向好感)`，`trust = max(所有关系类型双向信任)`，
双方 `spouse`、active `marriage` 关系、地点、背包/钱包、内容包物品表。

按顺序裁决：

1. `target == actor` → `self_propose`
2. 目标不是同地点活着的 NPC → `not_together` / `unknown_target`
3. 玩家已有配偶（`spouse` 或 active marriage） → `already_married`
4. 目标已有配偶或 active marriage → `target_in_couple`
5. 指定信物但背包不足/物品不存在 → `gift_missing`
6. 指定聘礼钱且钱包不足 → `not_enough_money`
7. `trust < 20` → `trust_too_low`
8. `affection < 40` → `bond_too_low`
9. `affection >= 60 且 trust >= 30` → **accept**
10. `40 <= affection <= 55`（好感缺口 5–20）且 `trust >= 30` → **courtship**
11. `affection >= 60` 且 `20 <= trust < 30` → **courtship**
12. 其余（好感 56–59 的 1–4 点小缺口） → `bond_too_low`

说明：缺口 5–20 才是“再处处看”；缺口 1–4 仍按门槛不足拒绝，边界明确可复现。
`courtship` 不消耗聘礼/信物；下一次求婚仍走同一裁决（门槛达标即 accept）。

裁决依据（`affection`、`trust`、`decision`、`reason_code`、`thresholds`）写入
`marriage_propose` 事件的 `audit_log` 与 `marriage_meta`，便于审计复现。

## 5. 聘礼与守恒

- 聘礼钱：`transfer(wallet:<player> → wallet:<npc>, amount=gift_money, reason="marriage_dowry")`，
  仅 `marriage` 事件内发生；不使用 `mint`，不收交易税。
- 信物：`item_move(src=player, dst=npc, item, qty)`，仅 `marriage` 事件内发生。
- 拒绝/求处不转账、不移动物品。
- 和离不退款、不铸币；共同财产不做本轮范围，写入 `docs/player-api.md`。

## 6. 前置校验与回滚

- 只读前置校验（`_precheck_player_action`）覆盖 `not_together/already_married/
  target_in_couple/gift_missing/not_enough_money/self_propose/spouse_not_here`；
  命中时只写一条 `rejected:code` 事件，不产生任何婚姻/资产状态。
- 决策类原因（`bond_too_low/trust_too_low`）在 `marriage_propose` 内裁决，随后写
  `marriage_reject`（`outcome=rejected`）；`spouse` 仍为空，聘礼未动。
- 任何单个 intent 由 `Writer` 在 clone 上原子应用；非法 effect 整笔 reject，不写半截状态。
- 多事件链在同一 `WorldSession.lock` 内顺序提交；若中段失败，状态停在显式中间态
  （`proposed`，未成婚、未收礼），不是“半对夫妻”。

## 7. 婚后可观测行为

1. **日程**：`Planner.plan` 对“配偶是玩家”的 NPC，每个世界日追加一次
   `move(配偶所在地) → spouse_time(target=配偶)`。仅在 `entity.spouse.startswith("player:")`
   时生效，NPC-NPC 婚姻与无玩家世界完全不走该分支。
2. **spouse_time**：NPC 与玩家必须同地点、双方互为配偶。
   - 若 NPC 背包里有 food/drink 物品，`item_move(npc → player, qty=1)` 并写“带饭”记忆；
   - 否则按世界日轮换取日常小事（提醒缴租/收拾家/晚饭等候），写双方记忆；
   - `marriage_meta` 更新 `last_daily_tick`、`daily_note`；婚姻关系每天 +1 好感/+1 信任。
3. **talk**：玩家 target 是配偶时，`TalkAction` 使用配偶专属确定性台词（按世界日轮换），
   覆盖模板/模型回复；`details.reply` 可直接看到。
4. **状态**：`GET /api/player/state` 的 `marriage` 暴露
   `{status, spouse_id, spouse_name, since_tick, last_daily_tick, daily_note}`。

## 8. API / UI 契约

- 白名单新增动词：`marriage_propose`、`marriage_end`（内部 verb 同名）；
  `marriage_accept`/`marriage_reject`/`marriage` 为服务端 NPC/系统事件，不对客户端开放。
- `marriage_propose` args：`target`（NPC id，必填）、`gift_money`（整数，≥0，可选）、
  `gift_item`（物品 id，可选）、`gift_qty`（整数，默认 1，1–20）。
- `marriage_end` args：无（配偶由 `spouse` 推导）。
- 响应：成功路径返回 `marriage` 快照与 `details.decision`（accept/courtship）；
  拒绝返回 `ok=false` + `reject_reason`（中文映射见 `docs/player-api.md`）。
- UI 本轮不做，后端契约稳定即可。

## 9. 验收项

1. 真实服务 + ambient 全链路：提高好感 → 求婚（含聘礼）→ 接受 → 结婚 → 婚后 3 个世界日
   同地点日常互动 → 和离；逐条 tick/verb/结果/钱包变化。
2. 拒绝样例各一条：`already_married`、`bond_too_low`、`not_enough_money`、`gift_missing`。
3. 对账：`accounts_sum = minted − burned`、`delta=0`、seq 连续、无半写。
4. 无玩家世界 state/event 哈希逐字节不变（基线见 `evidence/baseline.json`）；
   同一 marriage 场景同 seed 两次运行 state/event 哈希一致。
5. 全量 pytest 计数与 `./scripts/selfcheck.sh` 4/4。

## 10. 非范围

- 玩家-玩家婚姻、NPC 主动向玩家求婚、婚礼宴席、共同财产/嫁妆分割、离婚冷静期。
- UI 面板（另一路在改 `ui/player/**`）。
- 修改 `content/**` 数值或 `sim/scheduler.py`。
