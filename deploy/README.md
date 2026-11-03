# XKX / world-sim 对外首发试玩包（p3g）

本版基于 `60e8596`；`delivery-p3f` 仅内部里程碑，不作对外最终版。

这是一个可离线运行的 MUD 世界：Python 引擎 `wsim.api` + 浏览器前端 `ui/player`。
无需模型 key、数据库或 GPU；首次运行自动建 venv、装固定依赖、生成种子世界。

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

## 三个观察点

1. **世界在跑**：顶部时钟每个动作推进 1 小时（6 tick）；`tick` 从 0 → 6 → 12。
2. **NPC 自主行动**：启动时会生成 2 日种子世界，终端已打印最近 8 条 NPC 行动；完整版：
   ```bash
   .venv/bin/python scripts/world_feed.py --run runs/quickstart-2d --lines 12
   ```
   这是只读回放，专门用来看 NPC 的移动/干活/买卖；实时会话是玩家动作驱动的（见已知限制）。
3. **自己的动作生效**：点“书院”出门 → 地点变化；点 NPC 说话 → 出现回应；回集市接任务 →
   “我进行中”从 0 变 1；刷新页面后位置/钱包/任务都还在。

## 文档

- [快速开始（含每一步预期输出、断线验证）](docs/QUICKSTART.md)
- [已知限制（K1 7.69% FAIL、dialogue 0 调用、只保证事件回放、K6 对外含剪枝 0.85%/0.90%）](docs/known-limitations.md)
- [p3g Release Notes（golden 基线、816→824 漂移、K6 口径）](docs/RELEASE-NOTES-p3g.md)
- [项目说明（仓库原有 README）](README-PROJECT.md)

## 自动自检

```bash
./scripts/selfcheck.sh
```

本机实测：`pytest 194 passed`，内容包 / 单元测试 / 框架验收 / 玩家闭环 4 项全 PASS；framework 6/6。

## 包内范围

- `src/`、`sim/`、`ui/`、`content/`、`config/`、`scripts/`、`tools/`、`tests/`、`fixtures/`、`plugins/`、`client/`；
- `docs/` 与 `reports/evidence/`（指标证据、可回放 run）；
- 不含 `.venv`、`runs/`、`.git`、任何真实 key。`runs/quickstart-2d` 会在首次 `./start.sh` 时生成。

## 不要做什么

- 不要把 8020/8000 直接暴露公网：没有鉴权，`account_id` 只是字符串；
- 不要把“实时会话”当成 NPC 会自己持续行动的世界：后台没有 NPC 调度器；
- 不要用 72 天无玩家 run 的 75.56% K1 替代首发披露的 7.69%。
