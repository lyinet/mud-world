# 世界自走（后台 NPC 调度器）

> 目标：玩家不在场时世界也按虚拟时钟继续走。调度器默认纯规则、零 LLM。
> 从 W3 起 `./start.sh` **默认**开启；W4 起自走与玩家**同进程、同一份 `events.sqlite`、同一个 Writer**，
> 玩家在游戏里能看到 NPC 在同一世界里活着。`XKX_AMBIENT=0` 可显式关闭。

## 1. 架构边界

- 入口模块：[`src/wsim/sim/scheduler.py`](../src/wsim/sim/scheduler.py)
- 独立运行器：[`tools/ambient_run.py`](../tools/ambient_run.py)（离线/验收用，自带世界）
- 验收器：[`tools/ambient_accept.py`](../tools/ambient_accept.py)
- 复用既有引擎：`WorldState` / `EventStore` / `Writer` / `MockReasoner` / `LODScheduler`。
  调度器只回答「什么 tick 唤醒谁」，所有世界写入仍走 `Writer`，事件仍是唯一事实源。

### 单写者（W4 起）

- 服务模式不再起独立的 ambient 子进程。`WorldSession.start_ambient()` 把**本会话已有的**
  `Writer` / `EventStore` / `registry` 注入 `AmbientScheduler(writer=…, store=…, registry=…, lock=…)`，
  自走在后台线程里逐 tick 运行。
- 单写者靠**同一把 `WorldSession.lock`** 保证：每个 tick 的全部写入都在
  `with session.lock:` 内完成；玩家写路径（`perform` → `propose_intent` → `Writer.submit`）
  用的是同一把 `RLock`。因此两条路径串行、单事务、`seq` 连续，不存在两个 Writer 同时写。
- 共享模式下 `AmbientScheduler` **不再**创世、不再新建 `events.sqlite`、也不关闭共享 store
  （`_owns_store=False`）。`runs/<RUN_ID>/ambient/` 只放调度器自己的审计/进度/状态文件。
- 同一世界时钟：世界 tick 的唯一事实源是 `Writer.state.tick`。玩家动作仍按原语义推进 6 tick；
  自走只从当前世界 tick 往后推一格，玩家跨过的 tick **不补跑**（排队中的旧意图直接丢弃/归并到当前 tick），
  所以不存在双倍走时。

## 2. 首发默认启动（一条命令）

```bash
./start.sh                 # API + 前端 + 世界自走（默认 1x、CPU 上限 70%）
XKX_AMBIENT=0 ./start.sh   # 关闭自走；行为与未接入自走前一致
XKX_AMBIENT_CPU=70 ./start.sh
XKX_AMBIENT_SPEED=2 ./start.sh
```

- 启动顺序：API 先就绪；自走由 API 进程内启动（`XKX_AMBIENT=1` 注入），
  `demo.sh` 轮询 `/api/health` 的 `ambient=running` 确认，最多 4s，超时只告警、不阻塞前端。
- 进程日志：`runs/<RUN_ID>/logs/{api,ui}.log` + `runs/<RUN_ID>/ambient/ambient*.jsonl`。
- 退出：`Ctrl-C` / `TERM` 关闭 API 时 `WorldSession.close()` 先 `stop_ambient()` 并 join 线程，
  再关 sqlite；无僵尸、无半写。
- 自走线程异常/退出只影响自走本身，API/前端继续服务；状态与错误落在
  `ambient_status.json` / `ambient_error.json`。
- 可观测：`GET /api/health` 与 `GET /api/service/info` 的
  `ambient / ambient_tick / ambient_last_tick_at / ambient_reason / ambient_pid / ambient_speed`；
  原键（`ok/mode/readonly/profile_version/upstream_ok` 等）值语义不变。
  **`ambient_tick` 在 running/paused 时直接取世界 tick**，不再是隔壁世界的时钟。

### 玩家可见的世界事件流

`GET /api/world/feed?limit=20`（`limit` 1–50，默认 20）——公共只读，服务模式与 legacy 模式都匿名可读。
返回 `{"ok": true, "tick": <世界 tick>, "limit": n, "events": [...]}`，每条固定字段：

| 字段 | 含义 |
|---|---|
| `tick` | 事件发生时的世界 tick |
| `actor` | NPC 名（玩家 ID 永不出现） |
| `verb` | 动作（`system*` 与玩家事件已过滤） |
| `place` | 该 NPC 当前所在地名 |
| `summary` | 一句中文短句，≤40 字 |
| `important` | 是否里程碑类事件（婚配/开店/入派/押镖/交付等） |

`place` 取的是读取时的当前地点，用于「NPC 此刻在我附近」的判定；接口不返回钱包、背包、
任务、对话等任何玩家私有状态。


## 3. 启动 / 暂停 / 恢复 / 单步 / 倍率

程序接口（`AmbientScheduler`）：

| 能力 | 接口 |
|---|---|
| 启动 | `start(minutes=None, pause_at_tick=None, resume_after_s=None)` |
| 暂停 | `pause()` / `resume()` |
| 单步 | `step(n=1)`（暂停状态下也可用，推进后仍保持原状态） |
| 倍率 | `set_speed(1.0 / 2.0 / 4.0)` |
| 停止 | `stop()` |
| 固定 tick 快跑 | `run_ticks(n)`（不配速，测试/验收用） |
| 玩家进出 | `enter_player(...)` / `leave_player(...)` |

命令行：

```bash
# 1x 真实跑 30 分钟（≈300 ticks）
python tools/ambient_run.py --run-id ambient-1x-30m --minutes 30 --speed 1

# 4x 真实跑 30 分钟（≈1200 ticks）
python tools/ambient_run.py --run-id ambient-4x-30m --minutes 30 --speed 4

# 第 40 tick 暂停 5 分钟后自动恢复
python tools/ambient_run.py --run-id ambient-pause --minutes 10 --speed 4 \
    --pause-at-tick 40 --resume-after 300

# 快跑 300 ticks，第 150 tick 玩家进入、第 210 tick 退出
python tools/ambient_run.py --run-id ambient-player --ticks 300 \
    --enter-at-tick 150 --leave-at-tick 210

# 运行时通过控制文件动态操作（每次读取后即删除）
echo '{"command":"pause"}' > /tmp/ambient-ctl.json
python tools/ambient_run.py --run-id ambient-live --minutes 10 --control-file /tmp/ambient-ctl.json
# 支持：pause / resume / step(ticks) / speed(speed) / stop
```

配速定义：`1x = 10 ticks/真实分钟`（1 tick = 10 游戏分钟，即 6 秒/tick）；
2x / 4x 线性加速，30 分钟真实时间分别约 600 / 1200 ticks。

## 4. 成本模型

- 默认 `llm_enabled=False`：spotlight 也只走 `MockReasoner`，**0 次 LLM 调用、0 成本**。
- 接口允许注入模型工厂（`AmbientReasoner(rule, cost, model_factory)`），但默认不接线；
  W2 不加载任何 HTTP/模型客户端。
- `llm_max_calls` 为一次运行的硬上限：达到后 spotlight 自动降级回规则，
  `cost.degraded_to_rules` 与 `last_denied_reason` 记账。
- 规则模式不把「关闭 LLM」算作降级：`degraded_to_rules` 保持 0。

## 5. 玩家进出不破坏世界

- 玩家进入/退出只追加 `player_enter` / `player_leave` 事件，不重建世界、不回拨 tick。
- `ambient_progress.jsonl` 逐 tick 记录：
  `tick / seq / state_hash / event_chain / chain`，其中 `chain` 由前一条 `chain`
  与当前 tick 的状态/事件链决定。
- 玩家进入只会在当前 tick 之后追加事件；进入前的 progress 前缀逐字节不变，
  验收器会重算整条链并检查 tick 严格连续，回滚即 FAIL。
- 玩家所在位置的 NPC 会被临时升到 spotlight（`player_nearby`），日结时恢复正常调度。

## 6. 可观测

输出目录 `runs/<RUN_ID>/ambient/`：

| 文件 | 内容 |
|---|---|
| `event.sqlite` | append-only 事件源（`events.sqlite`） |
| `ambient.jsonl` | 逐事件审计：`seq/tick/actor/verb/outcome/source/reason/args/canonical/event_hash/event_chain` |
| `ambient_progress.jsonl` | 逐 tick 推进链：`state_hash/event_chain/chain` |
| `ambient_summaries.jsonl` | 每 6 ticks（=1 游戏小时）摘要：tick、事件数、NPC 数、动词数、LLM 成本、真实耗时、CPU 比 |
| `ambient_control.jsonl` | 启动/暂停/恢复/单步/倍率/停止记录（含虚拟 tick 与真实耗时） |
| `ambient.log` | 人类可读日志（启动、小时摘要、玩家进出、错误） |
| `ambient_summary.json` | 最终汇总：ticks、NPC 事件、动词、hash、成本、CPU 护栏、LOD 分布 |
| `ambient_error.json` | 任异常写此文件（含 traceback）后向上抛出，不静默 |
| `ambient_status.json` | 原子写状态：`running/paused/stopped`、`tick`、`last_tick_at`、CPU 计数；供 health 只读 |

## 7. 护栏

CPU 护栏（默认阈值 **70%**，`XKX_AMBIENT_CPU` 可调）分三档实际动作：

1. **降速**：0.25s 采样窗口内 `raw = cpu / work_wall > 70%` 时补睡
   `cpu/limit - wall`，把窗口平均拉回阈值；计数 `cpu_guard_sleeps/slept_s`。
2. **暂停**：超限持续累计 ≥2s（`cpu_pause_after_s`）时暂停自走
   （`state=paused`、`reason=cpu_ratio_over_limit`），不再拖慢 API/前端；
   暂停期间每 0.05s 采样，进程近乎空闲。
3. **恢复**：暂停期间比例 ≤35%（`cpu_resume_below_ratio`）持续 ≥2s
   （`cpu_resume_after_s`）后自动恢复 `state=running`，倍率回到原设定。

触发/恢复均写 `ambient_control.jsonl`、`ambient.log` 与摘要
（`cpu_pauses/cpu_resumes`）；状态文件 `ambient_status.json` 同步更新。

- 异常：控制文件解析、tick 推进、模型调用抛错时写 `ambient_error.json`，
  记录 `phase/tick/traceback`，并以非零码退出；不会吞异常继续跑。
- 系统动作被拒（`outcome=rejected`）会写日志并在 `blocked` 计数中可见。

## 8. 验收

```bash
python tools/ambient_accept.py \
  --run-1x runs/w2/ambient-1x-30m \
  --run-4x runs/w2/ambient-4x-30m \
  --det-a runs/w2/det-a --det-b runs/w2/det-b \
  --player runs/w2/ambient-player \
  --pause runs/w2/ambient-pause
```

覆盖：

1. 1x 30 分钟：≥300 ticks、≥12 条 NPC 自主事件、≥4 个 NPC 有动作、≥3 种动词、0 LLM；
2. 同 seed 两次空场：state/event/progress hash 完全一致；
3. 玩家在 ≥100 tick 后进入：progress 前缀不变、tick 连续、hash 链可重算、0 回滚；
4. 4x：tick 数为 1x 的 3–5 倍，事件密度/100 tick 偏差 ≤30%；
5. 暂停 5 分钟：控制日志中 pause/resume 同一 tick，恢复后从下一 tick 继续；
6. 全部 run 的 audit/progress 链可重算、audit 与事件总数一致、小时摘要字段完整。

### 实测（本轮验收）

| 项 | 1x（30 分钟） | 4x（30 分钟） |
|---|---|---|
| ticks | 300 | 1200 |
| NPC 自主事件 | 953 | 3274 |
| 有动作 NPC | 27/31 | 31/31 |
| 动词种类 | 15 | 17 |
| LLM 调用 | 0 | 0 |
| 事件密度（全部事件/100 tick） | 365.3 | 311.8（偏差 14.7%） |
| 进程 CPU / 墙钟 | 0.36% | 2.05% |
| CPU 护栏补睡 | 12 次 / 0.206s | 97 次 / 2.11s |
| 游戏小时摘要 | 50 条 | 200 条 |

补充实测：

- 同 seed 两次 300 tick 空场：state hash、event hash、event chain、progress chain
  完全一致，`ambient.jsonl` 与 `ambient_progress.jsonl` 逐字节一致。
- 玩家第 149 tick 进入、第 209 tick 退出：progress 0..299 连续，进入前 150 条记录
  前缀逐字节不变，进入事件 seq 追加在推进记录之后，0 回滚。
- 暂停：第 40 tick 暂停，300.03s 后同一 tick 恢复；恢复后从 41 继续，共 200 ticks。
- `ambient_accept.py` 41 项检查全部 PASS；全量 `pytest` 401 passed / 10 skipped；
  `selfcheck` 4/4 PASS。

### W3 默认开启 / CPU 护栏实测

| 项 | 结果 |
|---|---|
| 300 ticks（seed 42，快跑验收） | 1119 事件 / 966 NPC 自主事件 |
| 活跃 NPC | 28/31 |
| 动词种类 | 16 |
| LLM 调用 | 0 |
| 玩家第 149 tick 进入、209 退出 | progress 0..299 连续；0..149 与无玩家对照逐字节一致；0 回滚 |
| 1x 稳态（3 分钟，30 ticks） | 进程 CPU 0.19%，护栏补睡 0 次 |
| 注入忙等负载（70% 阈值，4x） | 补睡 3 次/0.496s → 第 2 tick、3.68s 暂停 → 2.24s 后恢复；cpu_pauses=1 / cpu_resumes=1 |
| `./start.sh` 默认 | `/api/health` `ambient=running`，7s 内 tick 0→1；UI health 同步 |
| `XKX_AMBIENT=0` | `ambient=stopped`、无 ambient 目录/进程 |

干净 worktree 复验：pytest **429 passed / 10 skipped / 0 failed**，selfcheck **4/4 PASS**。

CPU 护栏注入验证：本轮用一次性 harness 在每个 tick 内忙等 0.45s（4x 配速），
在 70% 真实阈值下观测到上述 throttle → pause → resume 全链路；harness 未入库。
日常 1x 空场不会触发护栏（进程占用约 0.2%）。

## 9. 已知边界

- 自走与 API 是两个独立世界：ambient 写 `runs/<RUN_ID>/ambient/events.sqlite`，
  玩家存档写 `runs/<RUN_ID>/events.sqlite`。本轮不做双写合并，避免破坏单写者；
  玩家可观测入口是 `/api/health` 的 `ambient_tick`。
- 不做重启续跑：进程退出后新起一次 run 是新的世界；磁盘上的 `events.sqlite`
  与 progress 可回放，但调度器未实现从快照恢复。
- 模型工厂只是接口，仓库内没有默认模型客户端，避免引入网络依赖与成本。
- `ui/player` 仅在 `/api/health` 追加 ambient 字段，不改现有键与 live/readonly 判定。
