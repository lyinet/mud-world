# Release Notes — delivery-p3k（最终首发包）

- 代码基线：`0ff9dc2`（W1 读路径鉴权 `133333e`/`c4eee95`、W4 代理 `e807cfc`/`0ff9dc2`；
  含 W5 `9c76cde`）；打包脚本：`11099d9`、`53de927`、`e317223`、`cf5407e`
- 文档提交：本文件与 `docs/DELIVERY-TEST-p3k.md` 随包提交，提交哈希以交付回报为准
- 前序：`delivery-p3j`（`c999fb6`）；`delivery-p3f`–`p3j` 的 tag 均不改写
- 内容冻结（关闭态，默认）：
  `content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`
  （31 NPC / 41 物品 / 9 地点 / 27 任务模板 / 30 张人物卡）
- 内容冻结（开启扩展包 `scaleup_w3`）：
  `content_hash = 3fd99946b44a7dac4c3ea041d114e28d84ef07e67aa90e40e209970c2c43534c`
  （60 NPC / 70 物品 / 20 地点 / 60 委托 / 59 张人物卡）；**默认关闭**
- 包内测试：**423 passed, 10 skipped**（见 `docs/DELIVERY-TEST-p3k.md`）

## 1. 相对 p3j 的变化（逐条）

1. **W1 账号 / 存档 / 鉴权 / 在线态**
   - 账号与令牌：PBKDF2 口令哈希 + sqlite，口令不落明文；`/api/auth/register|login|refresh|logout|me`
     契约固定（`88fdbfb`、`52839ad`）。
   - 存档恢复：事件日志回放恢复玩家存档，重启不丢档（`ac40a9c`）。
   - 内容闸门：`content_hash` 不匹配时拒绝启动，退出码 3（`23e1d05`）。
   - 断线重连：60 秒宽限、进程重启后可恢复（`73d8cc4`、`e1cf030`、`c23fc1a`）。
   - 容量与隔离：单区域 201 席返回 429 `player_cap_reached`；双客户端 20 轮并发隔离回归
     （互见、0 串档、同 NPC 关系不脏写）（`5cefd47`、`8a365b8`）。
   - 写路径与在线态：写路径去掉 `read_all`，新增 `src/wsim/api/event_tail.py`；
     在线三态按玩家 wall-clock 判定（active ≤30s / idle ≤60s / offline >60s）（`500369e`）。
   - 只读别名：新增 `GET /api/state`，与 `GET /api/player/state` 共用同一 handler，
     鉴权行为一致（`7250141`）。
   - **读路径鉴权（安全修复，`133333e` + `c4eee95`）**：
     - 服务模式带令牌：只能读本人完整状态；`account_id` 不一致返回 403
       `auth_account_mismatch`。
     - 服务模式匿名：`/api/state`、`/api/player/state` 返回 200，但只给公共摘要白名单
       （`ok/name/place/activity/idle_seconds/online/sect_name/shop_name/server_tick`），
       **不含** `wallet/items/skills/relations/active_quests/growth/today`。
     - `/api/player/intents` 必须带令牌，匿名 401 `auth_missing_token`。
     - legacy 模式（`auth_required=false`，`./start.sh` 默认）行为**完全不变**。
2. **W2 世界自走调度器（独立于 HTTP，默认纯规则、零 LLM）**
   - `src/wsim/sim/scheduler.py`、`tools/ambient_run.py`、`tools/ambient_accept.py`、`docs/AMBIENT.md`
     （随 `466c807` 入库）。
   - **`start.sh` 不启用该调度器**：它只在显式运行 `tools/ambient_run.py` 时工作，
     避免试玩包默认多跑一个后台循环。
3. **W3 可开关内容扩展包 `scaleup_w3`**
   - 引擎原生加载（`content/packs/`），关闭态 hash 保持 `7ec670c1…` 不变（`466c807`、`6e366d5`）。
   - 开启后 60 NPC / 20 地点 / 70 物品 / 60 委托 / 59 张人物卡，hash `3fd99946…`；
     开关方式：`content/packs/enabled.yaml` 的 `enabled` 列表，或环境变量
     `XKX_CONTENT_PACKS=scaleup_w3`。
   - 修复：开启态 29 个新 NPC 不再空卡（adapter 改走 `load_content`，`fd92c13`）；
     地点名从已加载内容动态取，新地点不再回退“集市”（`e89c268`）。
4. **W4 玩家前端**
   - 新玩家 4 步引导、失败可诊断的原因码中文映射、移动端单列（≤768px，重点 375×812）、
     键盘可达、登录/注册与令牌单飞轮换（`a274a9f`、`cc12453`）。
   - 鉴权接口按 W1 真实 `/api/auth/*` 对齐；legacy 匿名模式仍可玩。
   - **服务模式代理修复**：`ui/player/server.py` 正向转发 `Authorization`（GET/POST 均透传），
     401/403 原样保留；前端登录态带令牌取数、403 自动重取、启动先载令牌（`e807cfc`）。
   - **探活修复**：`probe_upstream` 改走 `/api/service/info`，服务模式下不再因匿名公共摘要
     缺 `profile_version` 而误降级 readonly（`0ff9dc2`）；`start.sh` 就绪探针同步调整。
     结论：**`--auth on` 必须配合带令牌的前端**；W4 已修好代理，浏览器登录后可正常读写。
5. **W5 压测与容器化部署**
   - 见第 2 节；`deploy/**`、`docs/OPS-RUNBOOK.md`、`reports/w5/**`、`tools/load_test.py`
     随 `9c76cde` 入库，p3k 包按需纳入 `deploy/` 资产。
6. **W6 出包修复**
   - 包内补入根目录 `SECURITY.md`、`CHANGELOG.md`（`11099d9`）。
   - 包内纳入 `deploy/` 容器化资产（`53de927`）。
   - **排除 `deploy/service.yaml`**（`e317223`）：该文件是 W1 `--auth auto` 的强制 Bearer 标记，
     随包会把 `./start.sh` 的匿名试玩变成必须登录，断掉“解包一条命令起服务”的复现链；
     容器由 `entrypoint.sh` 显式 `--auth on`，不依赖该文件。

## 2. 压测与部署（W5 同口径数字）

口径：**50 并发 × 600s、think=2s、ramp=10s、全端点、全新空日志**。
下表 `/api/state` 为只读别名，等价于 `/api/player/state`（同一 handler、鉴权行为一致）。

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| `/api/state` P95 | 3,700ms | **29.3ms** |
| 全端点 P95 | 3,750ms | **39.7ms** |
| `player_offline` 拒绝数 | 4,727 | **0** |
| 请求数 | 16,317 | 29,420 |
| 错误率 / 5xx / 429 | —— | **0% / 0 / 0** |

写吞吐（同一 17k 事件日志、单线程 200 写）：**4.28 → 95.8 次/s（22.4 倍）**，
P50 **234.8ms → 9.9ms**。

不变量：钱包 delta=0、`seq 1..29,423` 连续、无半写；`kill -9` 演练 **8/8 PASS**。

容量实测：

- **150 席 60s**：99.8 req/s、`/api/state` P95 **156ms**、CPU **78.5%**；
- **199 席**：`/api/state` P95 **1,762ms**、CPU **89.7%**；
- 单进程单区、300ms 口径上限约 **150–165 席**；500 席需 **3–4 区**，
  每区 **4 核 / 2GB**。

> **容量边界（必须按此对客）**：首发按**单区 150 席**对外。200 席功能上可连入
> （第 201 席返回 429 `player_cap_reached`），但 **P95 不达标**，需多区才能兑现。
> **不得写成“200 席达标”。**

Docker 说明：本机验证使用**独立版 `docker-compose`**（legacy builder，不读
`deploy/Dockerfile.dockerignore`，构建上下文偏大）；生产建议 **Docker Compose v2 / BuildKit**
（`docker compose up -d --build`）。

## 3. 包内范围

- `src/`、`sim/`、`ui/`、`content/`、`config/`、`scripts/`、`tools/`、`tests/`、`specs/`、
  `fixtures/`、`plugins/`、`client/`、`docs/`、`reports/`、`deploy/`；
- 根目录：`start.sh`、`requirements.txt`、`requirements.lock`、`pyproject.toml`、`.env.example`、
  `SECURITY.md`、`CHANGELOG.md`、`ARCHITECTURE.md`、`spec.md`、`tasks.md`；
- 内容：base 30 张人物卡 + `scaleup_w3` 29 张人物卡（共 59 张）；
- `deploy/`：Dockerfile、docker-compose、entrypoint、healthcheck、wait_for、crash_drill 等；
  **不含 `service.yaml`**（原因见 §1.6）；
- 不含 `.venv`、顶层 `runs/`、`.git`、`.sqlite`、任何真实 key；`runs/quickstart-2d`
  在首次 `./start.sh` 时生成。

## 4. 复现

```bash
python3 content/validate_content.py          # PASS，content_hash=7ec670c1…
python3 -m pytest                            # 423 passed, 10 skipped
./scripts/selfcheck.sh                       # 4/4 PASS
XKX_CONTENT_PACKS=scaleup_w3 \
  python3 content/validate_content.py        # PASS，content_hash=3fd99946…
```

事件数 / 金额 / 经济流量对比必须同时标注 `content_hash` 与当时的 `state_final` / `event_final`
哈希，跨快照不得直接相减。

## 5. 未覆盖项与风险

- **`/api/state` 为只读别名，等价于 `/api/player/state`**：两者共用同一 handler，
  鉴权行为一致；压测报告中的 `/api/state` 即此别名。
- **W4 浏览器证据目录不在包内**：p3k 包只纳入运行所需文件；`reports/w4-ui/` 的证据以仓库为准，
  UI 行为以 `docs/PLAYER-GUIDE.md`、本文件与代码为准。
- **Docker 已在本轮从解包后的 `deploy/` 实际起容器**（服务模式）：容器 healthy、
  `/api/health` 200 `mode=live`、`/api/state` 200（匿名公共摘要）；时间线与命令见
  `docs/DELIVERY-TEST-p3k.md` §6。
- **容量**：见 §2 的 150 席边界；200 席不是达标口径。
- **模型结论不变（保留，不美化）**：K1 维度模型零增益；战略层实验 FAIL；
  对话默认模板，第三方盲评与好模板持平（非平手 55.9%，Wilson CI 44.1%–67.1%）。
- **72 天长周期、多人长跑仍未验证**；委托补发只在玩家会话作用域生效。
