# Release Notes — delivery-p3l（活世界版）

- 前序冻结基线：`delivery-p3k` = `b23827a`（tag 不改写）；`delivery-p3f`–`p3k` 的 tag 均不改写
- 本版来源：`102a312`（世界动态中文映射与真后端复验）+ 独立复核提交
  `11c3ac9`（删除 `reports/w5/crash-drill/token-check.txt` 并补凭证类 ignore）、
  `d01ba9b`（CHANGELOG 记录该清理）
- 最终构建 HEAD 与构建时间写在 `dist/p3l-SHA256.txt`；该文件不入库，本文件随包，无法自指包哈希
- 内容冻结不变：关闭态 `content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`
  （31 NPC / 41 物品 / 9 地点 / 27 任务模板 / 30 张人物卡）；`scaleup_w3` 开启态
  `3fd99946b44a7dac4c3ea041d114e28d84ef07e67aa90e40e209970c2c43534c`（默认关闭）
- 包内测试：**439 passed, 12 skipped**（干净解包，见 `docs/DELIVERY-TEST-p3l.md`）
- 全程零模型调用、零 API 费用

## 1. 相对 p3k 的变化（逐条）

### 1.1 世界默认自走

- `./start.sh` 一条命令即起 **API + 前端 + 后台世界**；默认 `XKX_AMBIENT=1`，
  `XKX_AMBIENT=0` 关闭，行为与此前一致（不启动自走线程、无 `runs/<RUN_ID>/ambient/` 新文件）。
- 自走由 API 进程内启动（后台线程），不再是独立子进程；`Ctrl-C` 时先停自走线程再关 sqlite。
- `/api/health` 与 `/api/service/info` 暴露
  `ambient / ambient_tick / ambient_last_tick_at / ambient_reason / ambient_pid / ambient_speed`；
  running/paused 时 `ambient_tick` 直接取**当前世界 tick**，不是隔壁世界的时钟。
- **CPU 护栏**阈值默认 **70%**（`XKX_AMBIENT_CPU` 可调），三档动作：降速（补睡拉回阈值）→
  暂停（持续超限 ≥2s）→ 自动恢复（回落到 35% 以下持续 ≥2s）。
  实测：注入忙等负载、4x 配速，第 2 tick / 3.68s 暂停，**2.24s 后自动恢复**，
  `cpu_pauses=1 / cpu_resumes=1`（证据见 `docs/AMBIENT.md`）。

### 1.2 自走与玩家是同一个世界

- 同一份 `runs/<RUN_ID>/events.sqlite`、同一个 `Writer`、同一把 `WorldSession.lock`（`RLock`）；
  每个自走 tick 的全部写入与玩家写路径（`propose_intent → Writer.submit`）串行、单事务、`seq` 连续，
  不存在第二个 Writer。
- 共享模式下自走不再创世、不再新建/关闭事件库；`runs/<RUN_ID>/ambient/` 只保留审计、
  进度、状态与错误文件（`ambient*.jsonl` / `ambient_status.json` 等）。
- 同一世界时钟：玩家动作仍推进 6 tick，自走只从当前 tick 往后推一格，玩家跨过的 tick 不补跑，
  日界结算按“日变更”只触发一次，不存在双倍走时。
- 在线/离线对照（300 tick）：离线 NPC 自主事件 **891**，玩家在线 **970**（108.9%，
  验收门槛 ≥80%）；玩家 `cultivate` 动作 +6 tick 后，12s 自走仅 +1 tick。

### 1.3 新增匿名只读 `GET /api/world/feed?limit=20`

- `limit` 范围 1–50，默认 20；legacy 与服务模式均**匿名可读**，不需要令牌。
- 顶层固定 `{ok, tick, limit, events}`；`tick` 与 `/api/health` 的 `ambient_tick` 同一时钟。
- 每条事件固定 6 字段：`tick / actor / verb / place / summary / important`；`events` 按 `tick` 升序。
- `actor` 只出现 NPC 名，玩家事件与 `system_*` 已过滤；`place` 是读取时 NPC 所在地名；
  `important` 标记婚配/开店/入派/押镖/交付等里程碑。
- 不返回钱包、背包、任务、对话等任何玩家私有状态。样例：

  ```json
  {"tick": 13, "actor": "石敢当", "verb": "buy", "place": "集市",
   "summary": "在集市做买卖", "important": false}
  ```

### 1.4 UI「世界动态」面板

- 中栏默认可见（不参与折叠），固定高度 168px、独立 `overflow-y: auto`，
  不挤压主线操作；移动端高度 148px。
- 每 **8 秒**独立节流轮询 `GET /api/world/feed?limit=20`，**匿名请求**（故意不带令牌，
  与登录/刷新链路解耦），失败不弹红字、不写错误历史、不动主状态，静默显示「世界暂时安静」。
- 后端内部动词在 `ui/player/app.js` 的 `FEED_VERB_TEXT` 集中映射为中文
  （`move→赶路`、`work→上工`、`quest_accept→接单`、`quest_deliver→交单` 等），
  未知动词兜底「有事发生」；摘要若混入 `[a-z_]+` 也逐词替换，渲染文本不含内部英文动词。
- 证据：`reports/w4-feed/feed-checks.json`（默认可见、5 条渲染、tick 倒序、文本无 `[a-z_]+`）；
  `tools/ui_check.py` 86 checks / 0 failed；`tools/ui_check_real_feed.py` 12 checks / 0 failed（真后端）。

### 1.5 委托公平轮转

- 开放委托按模板 `last_served_tick` 最久未服务优先轮转（平局按 `quest id` 稳定排序），
  接单/执行步骤移到当天上工之前，避免 2 天期限模板因发布人走开而被静默拒绝。
- 10 天 seed 42、`scaleup_w3` 开启态：接单 **106→181**，distinct 模板 **44→60**
  （**60 张模板 10 天内全部至少被接 1 次**），零接单 **16→0**（新增 33 张里 **9→0**），
  零成交物品 **6→4**，`illegal_rate` **0.0**；state/event `d92b4579…` / `1cb069d7…`，两次重跑哈希一致。
- 关闭态：K1 **62/91 = 68.13%**（护栏 63.13%，未改阈值），事件 2934→2948；
  state/event `331c2e1c…` / `5a122bec…`，两次重跑哈希一致。

### 1.6 凭证清理（安全）

- `reports/w5/crash-drill/token-check.txt` 曾在 `9c76cde`（W5 容器化提交）入库，
  内容是一次性演练会话的 `session_id`、账号标识与过期时间（**不是 Bearer 令牌本体**）。
- 该文件已由 `11c3ac9` 从 HEAD 删除，并在 `.gitignore` 增加报告凭证类忽略；
  `SECURITY.md` §5.6 留档处置。**历史未改写**，旧提交中仍可检索，公开前仍需按
  `SECURITY.md` §3 做历史清理。
- p3l 包内**不含任何 token 类文件**；解包后 `find . -name 'token*'` 为空（见
  `docs/DELIVERY-TEST-p3l.md`）。

### 1.7 性能

- 验收口径（50 并发 × 30s）：**11,542 请求 / 0 error / 0 5xx**，
  `GET /api/state` P95 **22.579ms**（此前 29.3ms，未劣化）；同一 run 11,504 事件
  `seq 1..11504` 连续且去重、钱包 delta 0、半写 0。
- 本包复测（50 玩家 × 30s、think=2s、空日志）：两个实例分别
  `/api/state` P95 **10.776ms**（ambient 关闭）与 **10.517ms**（ambient 开启），
  0 error / 0 5xx / 0 429，不变量 PASS；均远低于 300ms 门槛。
  复测条件与验收口径请求密度不同，两组数字都保留，不互相替代。

## 2. 包内范围

- `src/`、`sim/`、`ui/`、`content/`、`config/`、`scripts/`、`tools/`、`tests/`、`specs/`、
  `fixtures/`、`plugins/`、`client/`、`docs/`、`reports/`、`deploy/`；
- 根目录 `start.sh`、依赖声明、`SECURITY.md`、`CHANGELOG.md`、`ARCHITECTURE.md`、
  `spec.md`、`tasks.md`；
- `deploy/`：Dockerfile、docker-compose、entrypoint、healthcheck、wait_for、crash_drill；
  **不含 `deploy/service.yaml`**（避免把 `./start.sh` 的匿名试玩变成必须登录）；
- 不含 `.venv`、顶层 `runs/`、`.git`、`.sqlite`、任何真实 key；
  `runs/quickstart-2d` 在首次 `./start.sh` 时生成；
- 独立复核清理后，包内不再有 `reports/w5/crash-drill/token-check.txt`

## 3. 复现

```bash
python3 content/validate_content.py          # PASS，关闭态
python3 -m pytest                            # 439 passed, 12 skipped
./scripts/selfcheck.sh                       # 4/4 PASS
XKX_CONTENT_PACKS=scaleup_w3 \
  python3 content/validate_content.py        # PASS，开启态
./start.sh                                   # API + 前端 + 世界自走
XKX_AMBIENT=0 ./start.sh                     # 关闭自走
```

事件数 / 金额 / 经济流量对比必须同时标注 `content_hash` 与 `state_final` / `event_final` 哈希，
跨快照不得直接相减。

## 4. 未做项与风险（按实际口径）

1. **4 件物品零成交**：`salt_lamp`、`salt_scale`、`rare_herb_box`、`service_night_watch`。
   本轮只做定位与建议，**一件不改**；根因（可达性闸门、luxury 现金门槛、`service` 被消费倾向排除）
   与最小修法见 `docs/known-limitations.md` §12。
2. **单区 150 席**：首发按单区 **150 席**对外；200 席功能上可连入（第 201 席返回 429
   `player_cap_reached`），但 P95 不达标。**不得写成“200 席达标”。**
3. **扩容方案未实现**：多区、每区 4 核 / 2GB 仍是方案，代码未落地。
4. **Docker 容器默认自走（tag 后修正）**：`delivery-p3l` tag 时 `deploy/entrypoint.sh`
   未注入 `XKX_AMBIENT`，容器默认 `ambient=stopped`；**tag 之后**已修正为默认开启，
   与 `./start.sh` 一致（`XKX_AMBIENT_CPU=70`、`XKX_AMBIENT_SPEED=1` 一并透传），
   `XKX_AMBIENT=0` 仍可显式关闭。实测：无任何环境变量 `up -d` → `ambient=running`、
   `ambient_tick` 推进、`/api/world/feed` 有真实 NPC 事件；`XKX_AMBIENT=0` → `ambient=stopped`。
   **tag 未移动**，交付包需按 tag 之后的修正提交重打。
5. **Docker 构建器**：本机验证使用独立版 `docker-compose` 5.6.1（legacy builder，
   不读 `deploy/Dockerfile.dockerignore`，构建上下文偏大）；生产建议 Compose v2 / BuildKit。
6. **72 天长周期、多人长跑仍未验证**；委托补发只在玩家会话作用域生效。
7. **模型结论不变（保留，不美化）**：K1 维度模型零增益；战略层实验 FAIL；
   对话默认模板，第三方盲评与好模板持平（非平手 55.9%，Wilson CI 44.1%–67.1%）。
