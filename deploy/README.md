# XKX / world-sim 对外试玩包（p3k）

本版基于 `9c76cde`（W5 收口；打包修复 `e317223`），内容冻结
`content_hash = 7ec670c1…`（关闭态）；`delivery-p3f`–`p3j` 为前序交付，tag 均不改写。

这是一个可离线运行的 MUD 世界：Python 引擎 `wsim.api` + 浏览器前端 `ui/player`。
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
不会只甩一段栈回溯。按 Ctrl-C 同时停掉引擎和前端。

## 第一次进入

账号任意（如 `u_walk`），角色名 2–12 字且不要与 NPC 重名（如 `初七`），身份选“捕快”。
进入后应看到钱包 300 文、炊饼×3、粗布×1，地点“集市”。

右侧面板会显示：

- **“今天能做什么”**：先做这个 + 可接委托 / 可学技能 / 可拜访 NPC / 可买产业；
- **三条成长线**：声望 / 技能 / 资产，各 5 级，带下一级门槛与解锁。

## 三个观察点

1. **世界在跑**：顶部时钟每个动作推进 1 小时（6 tick）；`tick` 从 0 → 6 → 12。
2. **NPC 自主行动**：启动时会生成 2 日种子世界，终端已打印最近 8 条 NPC 行动；完整版：
   ```bash
   .venv/bin/python scripts/world_feed.py --run runs/quickstart-2d --lines 12
   ```
   这是只读回放，专门用来看 NPC 的移动/干活/买卖；实时会话是玩家动作驱动的（见已知限制）。
3. **自己的动作生效**：点“书院”出门 → 地点变化；点 NPC 说话 → 出现回应；
   回集市接任务并交付 → 钱包增加；也可开摊或入会；刷新页面后位置/钱包/任务都还在。

## 文档

- [快速开始（含每一步预期输出、断线验证）](docs/QUICKSTART.md)
- [已知限制（目标校准、模型 FAIL、维护义务、委托补发范围、节奏单人实测）](docs/known-limitations.md)
- [p3k Release Notes（服务化、自走调度器、压测与部署、首发容量边界）](docs/RELEASE-NOTES-p3k.md)
- [p3k 交付实测（干净解包、一条命令起服务、测试计数）](docs/DELIVERY-TEST-p3k.md)
- [内容包验证清单（新手链、日常、长线、物价、关系；p3i 沿用）](docs/CONTENT-GUIDE-p3i.md)
- [项目说明（仓库原有 README）](README-PROJECT.md)

## 自动自检

```bash
./scripts/selfcheck.sh
```

本机实测：`pytest 414 passed, 10 skipped`，内容包 / 单元测试 / 框架验收 / 玩家闭环 4 项全 PASS；
framework 6/6。10 条 skip 是显式保留的旧基线，不是删测试。

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
- 不要把“实时会话”当成 NPC 会自己持续行动的世界：`start.sh` 默认不启用调度器，
  需要时显式运行 `tools/ambient_run.py`（见 `docs/AMBIENT.md`）；
- 不要把本版 K1 的 `49.45% → 65.93%` 当成模型贡献：它归因于目标体量与内容校准，模型在该维度零增益；
- 不要把模型的两次机制实验当成“仍在优化”：每 tick 选择器与每 3 日战略层均 FAIL，已永久收工；
- 不要把 R5 的 55.9% 非平手胜率当作“模型明显更好”：与好模板持平，对话默认仍走模板；
- 目标 target 按 10 天窗口校准，72 天是否仍成立未验证；委托补发只在玩家会话作用域生效；
- 物价只按日与库存变化、单日最多 ±20%；比较事件数/金额时必须同时标注
  `content_hash = 7ec670c1…` 与 `state_final` / `event_final` 哈希。
