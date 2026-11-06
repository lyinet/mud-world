# 长跑三项指标转 PASS 修复规格（2026-11-06）

## 已批准的硬口径（2026-11-06）

1. **判定不许动**：三项阈值与采样窗口保持原样，只改代码。任何实质上放宽判据的改动 = 该项 FAIL。
2. **基线不许自己改**：修复若改变无玩家 2 日/10 日 `state_final`/`event_final`，先停手报告，
   由用户决定是否升级基线。
3. **清理必须可复现**：影响可观测状态的清理只走确定性事件（如 `quest_prune` Effect）；
   纯派生内存（令牌/提案队列）按世界 tick/序号淘汰，不读墙钟，不进 `state_hash`。
4. **执行顺序**：先保证 soak 能长时间不超时跑完 → 再收无界增长 → 最后优化深拷贝与排序。

## 目标

用同一套 `tools/soak_test.py`（50 玩家 / think-time 2s / ≥2h）复跑，使下列三项真实转 PASS，
不得修改阈值、不得修改采样口径：

- `rss_growth_lt_5pct_per_hour`
- `p95_drift_read_lt_1_3`
- `p95_drift_write_lt_1_3`

## 基线事实（已实测，作为对比基准）

基线报告：`/tmp/wsim-baseline/reports/soak/soak-20261106-041244/summary.json`
（独立 worktree，提交 `9b1ec2c`，7200s，50 玩家/2s）

| 指标 | 基线值 |
| --- | --- |
| RSS `growth_pct_per_hour` | 80.843 |
| RSS `slope_mb_per_hour` | 539 |
| RSS `warm_mean_mb` / `peak_sampled_mb` | 666.79 / 1258.95 |
| read p95 首/末 10 分钟 | 199.83ms → 2715.88ms（ratio 13.591） |
| write p95 首/末 10 分钟 | 222.70ms → 1121.62ms（ratio 5.036） |
| state.quests | 276 → 965 |
| gc_objects | 297,683 → 2,583,679 |
| allocatedblocks | 1.44M → 12.2M |
| events seq | 1..265,643 连续 |

基线探针（`_probe/gc_probe.jsonl`，23 行）按类型统计：

- `_AdminToken` 6,833 → **129,001**（与写入次数同阶）
- `Intent` 13,668 → **258,032**（≈ tokens + outbox 各一份）
- `wsim.core.models.Event` 13,624 → **258,188**
- `AuditRecord` 4,096 → **94,208**（按 4096 整步增长）
- `Quest` 760 → 27,399

## 根因判定（按证据逐条）

1. **`gateway.tokens` 无界**：`admin.request` 每次生成一条 `_AdminToken`，从不删除；
   每条 token 持有完整 `Intent` → 129k 个 Intent 常驻。
2. **`gateway.outbox` 无界且从不移除已投递项**：每次 propose 追加一条，`admin.apply`
   成功后不删除 → 另外 129k 个 Intent 常驻。
3. **`writer._seen`（Event 常驻）**：`9b1ec2c` 基线尚无上限，故探针看到 258k Event；
   HEAD 前的提交 `116231a` 已加 `seen_max=20000` 上限，该热点当前已被封顶，2h 内预期
   不超过 2 万条 Event。同理 `session.requests/intents`（4096）与 `mcp.audit.records`
   （4096 + 文件轮转）也已在 `116231a` 封顶，无需再改。
   复现验证：进程内 ambient 压测后 `gc.get_objects()` 中 `wsim.core.models.Event`
   唯一持有者是 `writer._seen`，无第三处常驻容器。
4. **completed 委托永不清除**：`actions/quests.py` 的 `SystemQuestFailAction` 只 prune
   expired，completed 永久留在 `state.quests` → 276→965，并使每次 `sorted(state.quests)`
   和 `state.clone()` 的成本随运行时长线性上升（这同时解释了 write p95 漂移）。
5. **写路径全量克隆**：`world/writer.py:125` 每个 intent 一次 `WorldState.clone()`
   （pickle 往返，O(world size)）。这是**必要的原子性**语义（先 clone 再 apply，
   失败时旧投影不受影响），不能简单删除。可安全优化的只有两处：
   - `effects` 为空时（例如只写 `schedule_remove` 之外的空 effect intent）直接复用当前
     投影，不克隆；
   - 世界规模由第 4 条封顶后，克隆成本不再随时间上升。
   `_sorted_quests()` 读路径缓存已在 `9c42736` 提交、HEAD 生效（4 个旧的
   `sorted(state.quests)` 调用点全部改走它），本次不改 `session.py`。
6. **`quests.py:382` 的 `sorted(state.quests)`**：属于动作 apply 内部（确定性路径），
   不能改成缓存；改为由第 4 条把 `len(state.quests)` 封顶来消除增长。

## 变更清单（精确到文件与函数）

### A. `src/wsim/mcp/gateway.py`

- `MCPGateway.__init__`：保留 TTL/上限参数（`XKX_MCP_TOKEN_TTL_TICKS` 默认 144 tick
  = 1 世界日；`XKX_MCP_TOKEN_MAX` 默认 4096；`XKX_MCP_OUTBOX_MAX` 默认 4096）。
  删除 `XKX_MCP_OUTBOX_TTL_TICKS` 的“丢弃未投递”语义。
- `admin.request`：达到 `token_max` 时**拒绝新令牌**（`GatewayError("token_capacity_exceeded")`），
  不再 `pop(next(iter(...)))` 淘汰仍有效的旧令牌 —— 保证令牌语义不被静默改变。
- `admin.approve/apply`：语义不变（`unknown_token` / `token_used` / `second_person_required`
  / `apply_by_approver_only` 的判定顺序保持，重连/重试拿到的是正确的拒绝码）。
- `_prune_tokens(now_tick)`：只删 `now_tick - rec.tick > ttl` 的过期项；过期判定只看世界 tick。
- `propose_intent`：**只允许“已投递即清理”**。删除按 TTL 淘汰未投递项、删除按上限
  静默截断队首；达到 `outbox_max` 时返回 `GatewayError("outbox_full")`（确定性背压）。
- `_drop_delivered` 保持：apply 成功按内容键精确移除对应提案，找不到则不动其他条目。

### B. `src/wsim/actions/quests.py`

保留工作树现有实现并复核：

- `SystemQuestFailAction.apply` 末尾新增 completed 保留窗口：`completed_retention_days`
  默认 7 世界日（取 `content.economy["quests"]`），`completed_max` 默认 1000；按
  `(completed_tick, qid)` 排序取前缀，512 条一批 → `quest_prune` Effect。
- 只清 `status == "completed"`；`open/accepted/expired` 一律不动。
- 门控 `if state.players:`：无玩家世界的 `state_hash` 与旧基线逐字节一致（确定性红线）。
- 清理动作本身是纯函数 Effect，同 seed 同输入同结果。

### C. `src/wsim/world/writer.py`

- `submit()`：当 `effects` 为空时跳过 `state.clone()`，直接 `self.state.tick = intent.tick`
  并提交事件（值语义与克隆后 apply 空效果完全相同）。
- 其余路径不动；不做 CoW/分区克隆重构。

### D. 不纳入本次提交的内容

- `src/wsim/api/session.py` 的 `recent_rows` 改动属于另一条线（未提交），本次**一行不碰**，
  也不提交它。
- `src/wsim/world/store.py` 的分库归档 + `tests/test_event_store_sharding.py` 属于上一会话
  的独立改进，2h soak 内不会触发切库（阈值 5 GiB / 550 万条）。本规格决定：**本次不提交**，
  留在工作树，待独立评审；若最终决定提交，单独一个 commit，且必须先过确定性测试。
- `reports/w4-*` 等他人未提交改动一律不提交。

## 确定性红线与验收步骤

每步改动后按顺序执行（任一失败即停手修复，不得跳过、不得 `|| true`）：

```bash
cd /Users/lyinet/projects/XKX/world-sim
.venv/bin/python -m pytest tests/test_p3e_golden.py tests/test_reproducibility.py -q
.venv/bin/python -m pytest tests/test_mcp.py tests/test_quest_health.py -q
```

判定标准：无玩家 2 日/10 日 `state_final` / `event_final` 逐字节一致（测试内置）。
若某处修复必然改变基线，**停手报告，由用户决定是否升级基线**；不得自行改基线值。

## 2 小时复跑验收

```bash
.venv/bin/python tools/soak_test.py --duration 7200 --players 50 --think-time 2 --strict
```

交付：`reports/soak/<run>/summary.json` 路径 + 全部 10 条 `[soak] check ...` 行。
若仍 FAIL，如实报“未达标”，并给出下一个热点（附探针数据），不美化。

## 提交计划（每条显式 pathspec + CHANGELOG + push ff）

1. `fix(mcp): 令牌/提案队列加上限与确定性清理，未投递提案不丢` —
   pathspec：`src/wsim/mcp/gateway.py` + 对应测试（若新增）。
2. `fix(quests): completed 委托按 7 世界日/1000 条保留窗口清理` —
   pathspec：`src/wsim/actions/quests.py tests/test_quest_health.py`。
3. `perf(writer): 空效果路径跳过全量克隆` —
   pathspec：`src/wsim/world/writer.py`。
4. CHANGELOG 单独提交（`docs/CHANGELOG.md`），回填上述哈希。

push 使用 fast-forward（不 force、不动 tag）。
