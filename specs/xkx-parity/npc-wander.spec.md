# P4b：xkx NPC 游走方案（只设计，不写代码）

> 状态：待用户评审。默认关闭、由世界配置显式打开；不另造机制，复用 `LODScheduler` + `Writer` 单写者事件流。
> 分支：`fix/xkx-room-parity` → `origin/xkx-parity`。本文件不含实现。

## 0. 结论先行

1. 游走 = **在现有 LOD 唤醒点上，把原有 `move` intent 的生产者限定为“走向相邻房间”**。
   没有新的 tick 循环、没有新的随机源、没有新事件类型；位置变更仍由现有
   `move` action → `set_location` effect 落地，仍由 `Writer.submit()` 单写者写事件。
2. 每 tick 行动 NPC 数**不是固定值**：由 LOD 最小堆中 `tick <= 当前 tick` 的到期项决定，
   通常是 0..N（N 取决于 `interval_ticks` 与当前 NPC 数）。
3. 随机分两层：
   - **决策层**（走不走、走哪条出口）：`PRNG(seed, tick, f"npc_wander:{npc_id}")`，
     只依赖 tick 和 NPC id，不依赖全局事件顺序；
   - **落地层**（action 内结算）：沿用 `PRNG(seed, seq, f"action:{verb}")`，`seq = store.count() + 1`。
   `seq` 与事件序号 1:1；决策层绝不能用 seq（提交前还不知道 seq）。
4. xkx 世界哈希会漂移：默认关闭的基线记为 `world_hash ccbec47a… / event_hash 0505840d… / 1301 事件`
   （seed 20261014、2 日、当前内容包）；打开游走后按同一 seed/天数重记，并同时验证“同 seed 两次一致”。
5. `--reset` 只是删除 run 目录并从 tick=0、seq=1 重新创世；只要 seed/内容/配置/提交相同，
   首日与第二日都可逐字节复现（复现走 headless，不依赖墙钟）。
6. 旧九地点零变化：代码默认 `npc_wander.enabled = False`，旧世界配置不新增键；
   门控在 LOD/理由器入口，关闭时连一次 `advance_to` 都不额外触发。

## 1. 复用的现有机制（接入点）

| 环节 | 现有实现 | 接入方式 |
|------|----------|----------|
| 唤醒调度 | `src/wsim/sim/lod.py` `LODScheduler.advance_to()`（约 165-177 行）：最小堆弹出 `tick <= target` 的到期项，绝不全量遍历 | 原样复用；游走只改变 `_wake()` 里生成的 intent |
| 单 NPC 决策 | 同文件 `_wake()`（约 221-234 行）：递增 `wake_counts`，按 LOD 档位取 interval，调用 `reasoner.decide()`，并用 `PRNG(..., "lod_jitter:{npc}")` 算下一次 jitter | 在 `decide()` 结果上做“出口选择”，下一次唤醒仍由现有 jitter 决定 |
| tick 主循环 | `src/wsim/sim/scheduler.py` 主循环（约 609-685 行）：以 `writer.state.tick` 推进，处理日界/LOD/意图队列，逐个 `writer.submit()`，记录事件与 state hash | 原样复用；不新增循环 |
| 单写者 | `src/wsim/world/writer.py` `Writer.submit()`（约 88-159 行）：`seq = store.count() + 1`，校验前置条件，`PRNG(self.seed, seq, "action:{verb}")`，追加 `Event` 并更新 state | 游走 intent 必须经此提交，禁止直接改 `state` |
| 位置变更 | `src/wsim/actions/basic.py` `move`（约 89-103 行）→ `src/wsim/world/effects.py` `set_location`（约 408-414 行） | 复用；不新增 effect/verb |
| 随机 | `src/wsim/core/rng.py` `PRNG(seed, seq, salt)`：SHA256(`seed|seq|salt`) | 决策层用 tick 作为第二参数；落地层继续用 seq |

**明确不新增**：新的 scheduler、新的 timer、新的 `effect` 类型、新的 `verb`（沿用 `move`）、
`random`/`time` 等非种子化随机。

## 2. 每 tick 走几个、走哪一步

### 2.1 走几个

- `advance_to(t)` 只弹出 `wake_tick <= t` 的 NPC；每个到期 NPC 在一次唤醒里**最多产生 1 个 intent**。
- 每个 NPC 的下一次唤醒 = `t + interval + jitter`：
  - `interval` 由 LOD 档位决定（现有逻辑，见 `lod_tiers`）；
  - `jitter` 沿用现有 `PRNG(seed, <wake 计数或 tick>, f"lod_jitter:{npc}")`，不新增随机源。
- 因此“每 tick 走几个”= 该 tick 到期队列长度；配置 `interval_ticks` 后可用
  `149 个候选 / 平均 interval` 估算，但 spec 不承诺固定数，验收以实际 `lod.jsonl`/事件数记录。

### 2.2 走哪一步

- 候选动作只有一个：`move` 到**当前房间的一个相邻房间**（`exits.json` 的出口，排除 closed）。
- 出口选择：`PRNG(cfg.seed, tick, f"npc_wander:{npc_id}")` 取模；同一 tick 同一 NPC 的结果与
  其他 NPC 是否行动无关。
- 一行一步：一次唤醒只走一间，不做寻路、不做跨区域传送、不追玩家。
- 资格过滤（决定谁进入候选集，不引入随机）：
  - `status == "alive"`；
  - 当前房间至少 1 个可用出口；
  - 排除 `is_master` / 店铺 NPC / 被任务引用的 NPC（与 P1 的 clone 禁用名单同一套）；
  - 参考快照 `unconcious` 的毒蛇/大狼狗不行动（P4a 状态与游走解耦）。
- 如果 `move` 前置条件失败（体力、出口消失等），事件按现有路径记 `outcome != ok`，
  不重试、不补偿，保证可复现。

## 3. 随机与 seq 的对应关系

### 3.1 两个 PRNG 调用点

1. **决策层**（`_wake()` 内、调用 `writer.submit()` 之前）：
   ```text
   r = PRNG(seed, tick, f"npc_wander:{npc_id}")   # 选择出口/是否走
   ```
   为什么用 `tick` 而不是 `seq`：intent 尚未提交，全局 seq 未知；用 tick + per-NPC salt
   可以保证“同 seed、同 tick、同 NPC”得到同一决策，不受同 tick 其他 NPC 提交顺序影响。
2. **落地层**（`Writer.submit()` 内，现有）：
   ```text
   seq = store.count() + 1
   r = PRNG(seed, seq, f"action:{intent.verb}")   # move action 内部若需随机，用这一路
   ```
   `seq` 是全局单调事件号；每个提交的 intent 恰好产生 1 个事件，所以 **seq ↔ 事件序号 1:1**。

### 3.2 不变式

- 同一 `(seed, seq, salt)` → 同一随机流（`rng.py` 的 SHA256 保证）。
- 决策随机**不得**使用 `seq`；落地随机**不得**使用 `tick` 作为替代（否则事件顺序一变就不可复现）。
- 同 tick 多个到期 NPC 的提交顺序由 LOD 堆的 `(wake_tick, 入堆序号)` 决定；入堆序号来自确定性的
  `_wake()` 调度，因此可复现。**这是实现时必须保持的隐藏契约**（风险点 R1）。
- 开启游走后，事件总数与 `event_hash` 必然变化；这是新增行为，不是随机源不稳定。

## 4. 开关与世界配置

- 代码默认：`npc_wander.enabled = False`；世界配置没有该键时行为与今天完全一致。
- `config/worlds/xkx.yaml` 显式打开（示例，评审后落地）：
  ```yaml
  npc_wander:
    enabled: true
    interval_ticks: [30, 90]   # 仅用于 LOD interval，不含 jitter
    step_limit: 1              # 每次唤醒只走一间
  ```
- **旧九地点世界配置不新增任何键**；门控位置：
  - 启动时读取 `npc_wander` → 只有 `enabled: true` 才把 wander 决策挂到 `_wake()`；
  - 关闭时 `_wake()` 走原分支，不产生任何新 intent，不额外推进 tick。
- 不要求把 `ambient` 从 0 改成 1：wander 自己需要有到期唤醒；实现上用独立门控启动 LOD
  （或若 `ambient` 已开则只叠加 move 过滤器），避免顺带打开其他 ambient 行为。
- 风险：若复用 `MockReasoner._legacy()`（它会按概率生成 `move`/修炼等 intent），必须只保留
  `move` 且出口受限；其他 verb 一律丢弃，否则会改变既有 xkx 行为。

## 5. 哈希漂移的记账与“同 seed 两次一致”

### 5.1 记账口径

- **内容哈希**：游走开关在 `config/worlds/*.yaml`，不进 `Content.content_hash`；
  只要内容包不变，`content_hash` 不变，不需要 `--reset`。
- **世界/事件哈希**：开启游走后，`world_hash.txt` / `event_hash.txt` 必然漂移；
  每次修改游走策略（间隔、出口选择、过滤名单）都按 `docs/GOLDEN-BASELINE-LOG.md` 的 8 列表格新增一行：
  `提交 | XKX_WORLD | npc_wander | 旧值 | 新值 | 理由来源 | 理由 | 复现证据`。
- **基线分两套**：
  - `wander=off`：保持当前基线 `world_hash ccbec47a461356aa8e09321382cbf11dd8a79abef4ac14b7aefe37194ee634b0`、
    `event_hash 0505840d31d3b78a5e6c6c24d565c0127587f31235ca5b6fd68eff513b2b42b1`、1301 事件；
  - `wander=on`：实现提交后按同 seed/天数重记，并在台账写明“旧值 = off 基线 / 新值 = on 基线”。

### 5.2 可执行复现命令（同 seed 两次一致）

已在当前提交（`61c23ca`，wander 尚未实现）实测通过，可直接运行：

```bash
cd /private/tmp/xkx-parity-fix
PYTHONPATH=src python - <<'PY'
from pathlib import Path
from wsim.api.xkx_content import load_xkx_content
from wsim.sim.headless import RunConfig, run_headless

content = load_xkx_content(Path("content/xkx"))
for tag in ("a", "b"):
    run_headless(
        Path("."),
        RunConfig(seed=20261014, days=2, out_root=Path("/tmp/xkx-wander"), run_id=f"probe-{tag}"),
        content=content,
    )
PY
diff /tmp/xkx-wander/probe-a/world_hash.txt /tmp/xkx-wander/probe-b/world_hash.txt
diff /tmp/xkx-wander/probe-a/event_hash.txt /tmp/xkx-wander/probe-b/event_hash.txt
```

当前实测输出：
```text
world_hash  ccbec47a461356aa8e09321382cbf11dd8a79abef4ac14b7aefe37194ee634b0
event_hash  0505840d31d3b78a5e6c6c24d565c0127587f31235ca5b6fd68eff513b2b42b1
events      1301（rejected 4，precondition_blocked 148）
```

实现游走后，把 `npc_wander.enabled=true` 作为 `RunConfig`/世界配置传入，**同一条命令**应得到
两个相同的新哈希；验收同时比较 `daily.jsonl` 的每日 `state_hash`，确保不是只在终点偶然相等。

注意：`run_headless` 是确定性验证的入口；实时 `start_world.sh` 受墙钟调度影响，
只用于演示 `look`/行为，不用来断言哈希相等。

## 6. `--reset` 后首日与第二日的可复现性

- `--reset`（`scripts/start_world.sh:26/36`）只删除该世界 run 目录；重新启动后：
  - `tick = 0`，`store.count() = 0`，创世事件 `seq = 1`；
  - 内容哈希必须匹配，否则 `content_hash_mismatch`（`src/wsim/api/persistence.py:16-34`）。
- 复现保证来自：seed 固定 + 确定性 LOD 入堆 + 决策用 `(seed, tick, salt)` + 落地用 `(seed, seq, salt)`；
  与墙钟、进程启动顺序无关（以上均在 headless 中验证）。
- 验收命令（实现后执行）：
  1. 首日：`days=1` 跑 a/b 两次，`diff` `world_hash.txt` / `event_hash.txt`；
  2. 第二日：`days=2` 跑 a/b 两次，同样 `diff`；
  3. 交叉：`days=2` 的 `daily.jsonl` 第 1 日 `state_hash` 必须等于 `days=1` 的 `world_hash.txt`。
- 若第 3 步不等，说明日界系统事件或 LOD jitter 依赖了非确定输入，视为阻塞，不放宽断言。

## 7. 验收清单（实现提交时执行）

- [ ] 默认关闭：不传 `npc_wander` 时，`tests/test_xkx_determinism_legacy.py` 与
      `tests/test_p3e_golden.py` 全绿，旧九地点 `735` 事件、`state 009076f0…`、`event 02d5f812…` 不变。
- [ ] xkx 打开后：`look` 能看到 NPC 换房（真实世界端口避开 8011/8041）。
- [ ] 同 seed 两次一致（第 5.2 节命令 + 第 6 节三日交叉）。
- [ ] 无新 verb/effect：`events.jsonl` 中游走事件 verb 仍为 `move`。
- [ ] `Writer` 是唯一写入口：`git diff` 中除 intent 生产与配置外，没有直接改 `state` 的代码。

## 8. 风险与不做也可以的理由

- **R1 堆顺序**：同 tick 多个到期项的提交顺序若依赖 dict/set 迭代，会破坏可复现；
  实现时必须显式按 `(wake_tick, npc_id)` 稳定排序。
- **R2 ambient 副作用**：若直接复用 `MockReasoner._legacy()` 且不限制 verb，会顺带开启修炼/其他行为，
  必须只保留 `move`。
- **R3 LOD 档位漂移**：现有 `lod_tiers` 可能按活跃度改变 interval；游走会改变“活跃度”，
  形成反馈环。验收时固定 `lod_tiers` 或把 wander NPC 固定在同一档。
- **R4 任务/店主**：被任务引用或 `is_master` 的 NPC 不允许游走，否则任务目标会漂移；过滤名单与 P1 同一套。
- **不做也可以**：当前 195 房对拍只剩 `road1` 香火道人 1 房差异，游走收益主要是“世界更活”；
  如果只追求对拍数字，可以不做。但用户已明确要方案，故按上述最小接入设计。

## 9. 建议的独立提交拆分（实现阶段）

1. **W1 开关与配置读取**：`npc_wander` 默认关闭、xkx.yaml 显式打开；旧世界零变化；只加测试。
2. **W2 决策层**：`_wake()` 里用 tick+salt 选相邻出口，生成 `move` intent；资格过滤。
3. **W3 可复现验收与台账**：同 seed 两次、日界交叉、GOLDEN-BASELINE-LOG 新增 on 基线。
