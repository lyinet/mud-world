# XKX / world-sim 对外试玩包（p3l）

本版在 `delivery-p3k` 冻结基线 `b23827a` 上叠加 p3l 活世界增量（该哈希是**历史基线**，
等于 `delivery-p3k^{commit}`，不等于当前 HEAD；当前交付由新的 `delivery-*` tag 冻结，见
[docs/RELEASE-ROLLBACK.md](docs/RELEASE-ROLLBACK.md)），内容冻结
`content_hash = 7ec670c1…`（关闭态）；`delivery-p3f`–`p3k` 为前序交付，tag 均不改写。

这是一个可离线运行的 MUD 世界：Python 引擎 `wsim.api` + 浏览器前端 `ui/player`
+ 默认开启的后台世界自走（ambient）。
无需模型 key、GPU 或外部数据库；存档与账号用本地 sqlite（`events.sqlite` / `service.sqlite`），
首次运行自动建 venv、装固定依赖、生成种子世界。

## 一条命令起来

```bash
./start.sh
```

然后打开 <http://127.0.0.1:8000/> 进入游戏。端口占用时：

```bash
API_PORT=8120 PORT=8010 ./start.sh
```

脚本失败会给出可读错误（缺 Python、venv 创建失败、pip 失败、端口占用、种子生成失败），
不会只甩一段栈回溯。按 Ctrl-C 同时停掉引擎、前端与自走；日志在
`runs/<RUN_ID>/logs/{api,ui,ambient}.log`。默认自走可用 `XKX_AMBIENT=0 ./start.sh` 关闭，
CPU 上限用 `XKX_AMBIENT_CPU=70`（百分比）调整。

## 第一次进入

账号任意（如 `u_walk`），角色名 2–12 字且不要与 NPC 重名（如 `初七`），身份选“捕快”。
进入后应看到钱包 300 文、炊饼×3、粗布×1，地点“集市”。

右侧面板会显示：

- **“今天能做什么”**：先做这个 + 可接委托 / 可学技能 / 可拜访 NPC / 可买产业；
- **三条成长线**：声望 / 技能 / 资产，各 5 级，带下一级门槛与解锁。

## 三个观察点

1. **世界在跑**：顶部时钟每个动作推进 1 小时（6 tick）；`tick` 从 0 → 6 → 12。
2. **NPC 自主行动**：`./start.sh` 默认常驻同进程世界自走，NPC 会自己修炼、开店、结婚；
   `/api/health` 的 `ambient`/`ambient_tick` 显示自走是否在推进，且 `ambient_tick` 就是当前世界 tick。
   自走与玩家共用同一份 `events.sqlite`、同一个 Writer 和同一把锁，整 tick 串行提交；
   `runs/<RUN_ID>/ambient/` 只保留审计与状态文件（不再另起独立世界）。
   中栏「世界动态」每 8 秒匿名刷新 `GET /api/world/feed?limit=20`，失败时静默显示「世界暂时安静」。
   想看一段更长的 NPC 行动回放，可另生成一个 jsonl 种子世界（只读，不影响当前存档）：
   ```bash
   .venv/bin/python sim/run_headless.py --days 10 --seed 42 --run-id npc-feed
   .venv/bin/python scripts/world_feed.py --run runs/npc-feed --lines 12
   ```
   这是只读回放，专门用来看 NPC 的移动/干活/买卖；实时会话是玩家动作驱动的（见已知限制）。
3. **自己的动作生效**：点“书院”出门 → 地点变化；点 NPC 说话 → 出现回应；
   回集市接任务并交付 → 钱包增加；也可开摊或入会；刷新页面后位置/钱包/任务都还在。

## 滚动重启（长跑容量）

`deploy/rolling_restart.py` 在宿主机巡检容器，三项阈值任一连续命中才重启：

- **内存主判据是当前 RSS**：`docker exec` 进容器，按 cmdline 含 `wsim.api` 定位 API
  进程（容器 PID 1 是 `deploy/entrypoint.sh`，不是 API），读 `/proc/<pid>/status`
  的 `VmRSS`。连续 3 个采样窗严格单调上涨且斜率 > 0.5 MB/min（约 800MB 时
  3.75%/h，低于验收线 5%/h）才重启；绝对上限 800MB 同样要求连续 3 窗。
  `VmHWM`（峰值，等价 `ru_maxrss`）只写日志，不参与决策——峰值只增不减，
  一次备份/GC 尖峰会永久超阈值，无法区分尖峰与泄漏。
- 读 P95：取 `/api/metrics` 直方图两次采样的增量，连续 3 窗 > 500ms 才重启。
  容器内 API 绑 `0.0.0.0`，`/api/metrics` 默认按“非回环”处理返回 404；需要
  `XKX_METRICS_TOKEN=<token>` 注入容器，并用 `--metrics-token <token>`（或同名
  环境变量）传给脚本。取不到时脚本只告警一次、停用 P95 判据，RSS/CPU 仍生效。
- CPU：`docker stats` 进程 CPU% 连续 2 窗 > 2× 基线才重启。

脚本每次采样打印一行 JSON，其中 `api_pid` 是被采样的 API 进程号（该容器里 PID 1
是 entrypoint，API 通常是 8 之类的子进程），`peak_rss_mb`（=`VmHWM`）仅供记录。

CPU 基线是本机采样值，**不入库**（已加入 `.gitignore`）：

```bash
python3 deploy/rolling_restart.py --record-baseline   # 正常负载下采样一次
cat deploy/.cpu_baseline                              # 例如 42.00
```

基线漂移判断：正常时段连续 3 次采样相对基线超过 ±30%，说明世界配速/玩家量变了，
应重录基线；不要拿旧基线做重启判据。

演练（只检查不动作，会打印触发原因并以非零退出）：

```bash
python3 deploy/rolling_restart.py --once --dry-run --rss-limit-mb 50 --rss-streak 1
# 输出：{"...","rss_mb":52,"rss_abs_streak":1,"trigger":true,
#        "reasons":["rss=52MB>=50MB for 1 windows"]}
#       [rolling-restart] DRY-RUN: 命中阈值，未执行重启   → 退出码 1
```

真实重启（走 `compose stop` → `stop_grace_period: 45s` → `up -d`，并等待 `/api/health`）：

```bash
python3 deploy/rolling_restart.py --loop --interval 60
# 小库空载演练的实际输出（隔离栈）：
# [rolling-restart] restart service=world: rss=36MB>=30MB for 1 windows
# [rolling-restart] post-restart {"restart":1,"stop_s":0.41,"health_200":true,
#   "health_ready_s":1.01,"seq_gap":0,"events_ok":true,"integrity":"ok",
#   "state_match":true}
```

`stop_grace_period=45s` 的依据：50 并发 2 小时 soak 实测 SIGTERM→退出 **31.24s**
（keep-alive 连接收尾 + WAL checkpoint），容器默认 10s 会直接 SIGKILL；45s =
31.24s × 1.44 余量。

## 文档

- [快速开始（含每一步预期输出、断线验证）](docs/QUICKSTART.md)
- [已知限制（目标校准、模型 FAIL、维护义务、委托补发范围、节奏单人实测）](docs/known-limitations.md)
- [p3l Release Notes（世界默认自走、同一世界 feed、公平轮转、容量边界）](docs/RELEASE-NOTES-p3l.md)
- [p3l 交付实测（三路径、60 秒可见性、P95、崩溃恢复）](docs/DELIVERY-TEST-p3l.md)
- [p3k Release Notes（历史基线：服务化、压测与部署）](docs/RELEASE-NOTES-p3k.md)
- [内容包验证清单（新手链、日常、长线、物价、关系；p3i 沿用）](docs/CONTENT-GUIDE-p3i.md)
- [项目说明（仓库原有 README）](README-PROJECT.md)

## 自动自检

```bash
./scripts/selfcheck.sh
```

本机实测：`pytest` **439 passed, 12 skipped**（干净解包），
内容包 / 单元测试 / 框架验收 / 玩家闭环 4 项全 PASS；framework 6/6。
skip 是显式保留的旧基线，不是删测试。

## 包内范围

- `src/`、`sim/`、`ui/`、`content/`、`config/`、`scripts/`、`tools/`、`tests/`、`specs/`、`fixtures/`、`plugins/`、`client/`；
- `docs/` 与 `reports/`（指标证据、可回放 run）；
- 含 `specs/a4-strategist`、`docs/CONTENT-GUIDE-p3i.md`、`reports/goal-lifecycle-audit.md`、
  `reports/verify-guard-quota-frozen-2026-11-04.md`；
- 不含 `.venv`、顶层 `runs/`、`.git`、`.sqlite`、任何真实 key。`runs/quickstart-2d` 会在首次
  `./start.sh` 时生成。

## 不要做什么

- 不要把 8020/8000 直接暴露公网：`start.sh` 默认只绑 `127.0.0.1`；API 虽已对写端点启用
  Bearer 鉴权（PBKDF2 账号），但仍是明文 HTTP、无 TLS/限流，且 `account_id` 只是字符串，
  不能当身份证明；
- 不要为自走再起第二个 Writer：p3l 起自走已并入 API 进程，与玩家共用
  `runs/<RUN_ID>/events.sqlite`、同一个 Writer 和同一把锁；关闭用 `XKX_AMBIENT=0`，
  不要另起独立的 ambient 子进程；
- 不要把本版 K1 的 `49.45% → 65.93%` 当成模型贡献：它归因于目标体量与内容校准，模型在该维度零增益；
- 不要把模型的两次机制实验当成“仍在优化”：每 tick 选择器与每 3 日战略层均 FAIL，已永久收工；
- 不要把 R5 的 55.9% 非平手胜率当作“模型明显更好”：与好模板持平，对话默认仍走模板；
- 目标 target 按 10 天窗口校准，72 天是否仍成立未验证；委托补发只在玩家会话作用域生效；
- 物价只按日与库存变化、单日最多 ±20%；比较事件数/金额时必须同时标注
  `content_hash = 7ec670c1…` 与 `state_final` / `event_final` 哈希。
