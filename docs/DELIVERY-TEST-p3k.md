# 交付实测 — delivery-p3k（最终版 · 干净解包 · 一条命令起服务）

- 实测日期：2026-11-04（Asia/Shanghai）
- 被测代码：`0ff9dc2`（含 W1 读路径鉴权 `133333e`/`c4eee95`、W4 代理 `e807cfc`/`0ff9dc2`、
  W5 `9c76cde`、W1 只读别名 `7250141`、W5 README 版本注入 `cf5407e`、W6 出包修复）
- 被测包：`xkx-world-sim-delivery-p3k.tar.gz` / `.zip`，由 `git archive HEAD` 的干净树构建
  （**不含**工作区未跟踪文件、`.venv`、`runs/`、`.git`、`.sqlite`）
- 机器：macOS，Python 3.12.7；全程不调用模型、不产生 API 费用
- 最终 tar/zip 的 SHA256 见 `dist/p3k-SHA256.txt`（该文件不入库；本文件随包，无法自指包哈希）

## 1. 构建

```bash
cd /Users/lyinet/projects/XKX/world-sim
rm -rf /tmp/xkx-p3k-build4 && mkdir -p /tmp/xkx-p3k-build4
git archive HEAD | tar -x -C /tmp/xkx-p3k-build4
cd /tmp/xkx-p3k-build4
python3 tools/build_dist.py --version p3k --out dist
```

观测：`514` 个文件；`tar.gz` = **5,910,011 B（5.64 MiB）**，
`zip` = **6,234,984 B（5.95 MiB）**；解包后约 **20 MiB**（首次 `./start.sh` 另建 `.venv` 约 27 MiB）。
清单 `dist/MANIFEST-delivery-p3k.txt` 共 514 行。

必需项逐条确认存在：

| 必需项 | 清单命中 |
| --- | --- |
| 启动脚本 `start.sh` | 1 |
| 依赖声明 `requirements.txt` / `requirements.lock` | 1 / 1 |
| `docs/QUICKSTART.md` | 1 |
| `docs/PLAYER-GUIDE.md` | 1 |
| `docs/AUTH-API-W1.md` | 1 |
| `SECURITY.md` | 1 |
| `CHANGELOG.md` | 1 |
| 前端 `ui/player/app.js` / `index.html` / `server.py` | 1 / 1 / 1 |
| 内容 `content/npcs.yaml` | 1 |
| base 人物卡 `content/personas/` | 30 |
| 扩展包 `content/packs/scaleup_w3/pack.yaml` | 1 |
| 扩展包人物卡 `content/packs/scaleup_w3/personas/` | 29 |
| 部署资产 `deploy/Dockerfile`、`docker-compose.yml`、`entrypoint.sh`、`healthcheck.py` | 均存在 |
| `docs/OPS-RUNBOOK.md`、`reports/w5/**` | 存在（W5 随包） |

反向校验：清单中 **0** 条匹配顶层 `world-sim/runs/`、`.git/`、`.venv/`、`*.sqlite`、
`.env`、`token.txt`。
`deploy/service.yaml` 按设计**不在包内**（原因见 `docs/RELEASE-NOTES-p3k.md` §1.6）。

## 2. 干净解包 + 一条命令起服务

```bash
rm -rf /tmp/xkx-p3k-final6 && mkdir -p /tmp/xkx-p3k-final6
cd /tmp/xkx-p3k-final6
tar -xzf /tmp/xkx-p3k-build4/dist/xkx-world-sim-delivery-p3k.tar.gz
cd world-sim
API_PORT=8565 PORT=8545 RUN_ID=p3k-final6 ./start.sh
```

`start.sh` 唯一依赖包内文件；不读取源码仓库，也不依赖原工作区 `runs/`、`reports/`。

实测时间线（2026-11-04）：

| 时刻 | 观测 |
| --- | --- |
| 22:54:19 | 解包完成（全新目录，无预置环境） |
| 22:54:2x | `[start] 首次运行：创建 .venv ...` |
| 22:54:3x | 种子世界生成完成：**2 游戏日 · 836 条事件 · NPC 自主行动 736 条** |
| 22:54:43 | `[demo] 健康检查: API 200 (GET /api/player/state)`；前端 `GET /api/health` 200 |
| 22:54:43 | API `{"ok": true, "auth_required": false, "auth_mode": "auto"}`；前端 `http://127.0.0.1:8545/` 就绪 |
| 22:54:56 | 首个业务端点响应（`/api/state` 200，见 §3） |

结论：**首次全流程约 24 秒**（含建 venv、装依赖、生成 2 日种子、起 API + 前端）。

## 3. 端点观测（legacy 默认模式，端口 API=8565 / 前端=8545）

| # | 命令 | 观测 |
| --- | --- | --- |
| 1 | `curl -sS -m 5 -w '%{http_code} %{time_total}' 'http://127.0.0.1:8545/api/state?account_id=u_p3k7'` | **HTTP 200**，0.0167s |
| 2 | `curl -sS -m 5 -w '%{http_code} %{time_total}' 'http://127.0.0.1:8545/api/player/state?account_id=u_p3k7'` | **HTTP 200**，0.0017s |
| 3 | `cmp` 两份响应体 | **逐字节相同** |
| 4 | `curl -sS http://127.0.0.1:8545/api/health` | HTTP 200，`mode=live, upstream_ok=true` |
| 5 | `curl -sS -o /tmp/p6_index.html -w '%{http_code} %{size_download}' http://127.0.0.1:8545/` | HTTP 200，12,406 B，`<title>入世 · 世界门面</title>` |

> **口径**：`/api/state` 是 `/api/player/state` 的**只读别名**，两者共用同一 handler，
> 鉴权行为一致，响应逐字节相同。对客文档统一按此表述。

## 4. 玩家闭环（经前端反代，account=`u_p3k7`，角色名“十四”）

```bash
# 进入世界
curl -X POST http://127.0.0.1:8545/api/player/enter -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3k7","display_name":"十四","archetype":"捕快"}'
# 动作统一 POST /api/player/action
```

| 步骤 | 观测 |
| --- | --- |
| enter | `ok=true`，`tick=0`，created |
| state | `ok=true`，`tick=6`，地点 **集市**，钱包 **300 文**，在场 NPC 7 |
| quest_accept `quest:daily_market_inquiry:0` | `ok=true`，`tick=6`，`active_quests=[quest:daily_market_inquiry:0]` |
| move `academy` | `ok=true`，`tick=12`，地点 **书院** |
| talk `bai_lusheng` | `ok=true`，`tick=24`，对话成功（白露生回复） |
| move `market` | `ok=true`，`tick=30`，地点 **集市** |
| quest_deliver 同一任务 | `ok=true`，`tick=36`，`active_quests=[]` |
| 最终 state | `ok=true`，`tick=42`，地点 **集市**，钱包 **309 文**，`active_quests=[]`，关系 2 |

玩家面板 HTML、健康检查、匿名写入均正常；**一键试玩链路成立**。

## 5. 三路径安全契约（本轮重点）

### 5.1 legacy 匿名（`./start.sh` 默认，`auth_required=false`）

- 行为**完全不变**：匿名读写正常；
- `/api/state` 与 `/api/player/state` 均 200，响应逐字节相同（见 §3–§4）。

### 5.2 服务模式 API（`--auth on`，端口 8586，22:55:22 就绪）

```bash
PYTHONPATH=src .venv/bin/python -m wsim.api --root . --run p3k-sec3 --port 8586 --auth on
```

| 场景 | 观测 |
| --- | --- |
| 匿名 `GET /api/state?account_id=u_ming` | **200**，恰好 9 键：`activity/idle_seconds/name/ok/online/place/sect_name/server_tick/shop_name`；`wallet/items/skills/relations/active_quests/growth/today` **全部不存在** |
| 本人令牌 `GET /api/state` | **200**，完整状态，`wallet.money=300`，地点集市 |
| 令牌 + 他人账号 `GET /api/state?account_id=u_bob` | **403** `auth_account_mismatch`（tick 6） |
| 匿名 `GET /api/player/intents/i:deadbeef` | **401** `auth_missing_token`（tick 6） |

### 5.3 服务模式经 UI 代理（端口 8556，22:55:33 就绪）

```bash
.venv/bin/python ui/player/server.py --upstream http://127.0.0.1:8586 --port 8556 --host 127.0.0.1
```

| 场景 | 观测 |
| --- | --- |
| `/api/health` | **200**，`mode=live, readonly=false, upstream_ok=true` |
| 带令牌 `GET /api/player/state` | **200**，`wallet=300`，地点集市（代理已转发 `Authorization`） |
| 匿名 `GET /api/state?account_id=u_ming` | **200**，仅 9 键公共摘要 |
| 带令牌 `POST /api/player/action`（move academy） | **200**，`ok=true, tick=6`（写操作不再 401） |
| 带令牌跨账号 `GET /api/state?account_id=u_bob` | **403** 原样透传 |
| `GET /` | **200**（玩家面板） |

> **结论口径**：匿名 = 公共摘要；本人 = 令牌 + 完整状态；`--auth on` 必须配合带令牌的前端
> （W4 已修代理转发与探活）。

## 6. Docker（交付包自身 · 服务模式）

在**解包目录** `/tmp/xkx-p3k-final6/world-sim/deploy` 执行：

```bash
docker-compose up -d --build
```

观测时间线（2026-11-04）：

| 时刻 | 观测 |
| --- | --- |
| 22:56:38 | `Container xkx-world Created / Starting` |
| 22:56:42 | 健康检查第 1 次：`/api/service/info -> 200 ok`，`/api/health -> 200 ok`（PASS） |
| 22:56:48 | `docker inspect`：`running healthy` |
| 22:56:58 | 健康检查复检：两项均 PASS，`FailingStreak=0` |
| 22:57:08 | `docker-compose down`，容器与网络已移除 |

端点观测（容器 API 8020 / 前端 8030，`--auth on`）：

| 项 | 观测 |
| --- | --- |
| `/api/health` | **200**，`mode=live, readonly=false, upstream_ok=true` |
| `/api/state`（匿名，已进入玩家 `u_zhou`） | **200**，仅 9 键公共摘要 |
| `/api/state`（本人令牌） | **200**，`wallet.money=300` |
| 跨账号 `/api/state?account_id=u_other` + 本人令牌 | **403** `auth_account_mismatch` |
| 匿名 `/api/player/intents/i:x` | **401** `auth_missing_token` |
| `GET /` | **200**，12,406 B |

Docker 说明：本机使用**独立版 `docker-compose` 5.6.1（legacy builder）**，`docker compose`
v2 不可用；legacy builder 不读 `deploy/Dockerfile.dockerignore`，构建上下文偏大。
生产建议 **Docker Compose v2 / BuildKit**（`docker compose up -d --build`）。

容器与 `./start.sh` 的模式差异：

- `./start.sh` 默认 `--auth auto`，包内无 `deploy/service.yaml`，因此走 **legacy 匿名**；
- 容器 `entrypoint.sh` 显式 `--auth on`，**匿名只能看公共摘要，写入与完整状态必须带令牌**；
- 两者共用同一份 `ui/player` 前端，服务模式必须通过登录后的前端访问（代理已转发令牌）。

## 7. 包内自检

在解包目录 `/tmp/xkx-p3k-final6/world-sim` 执行：

```bash
./scripts/selfcheck.sh
```

| 项 | 结果 |
| --- | --- |
| 内容包校验 | **PASS**（NPC 30 人、初始货币 35,650 文、关系边 106 条） |
| 单元测试 pytest | **423 passed, 10 skipped**（120.69s） |
| 框架验收 `tools/framework_check.py` | **6/6 PASS**，`replay=MATCH`，守恒=0 |
| 玩家闭环（临时端口随机） | **PASS**（enter → 书院 → 对话 → 接任务 → 刷新状态仍在） |
| 总结 | **selfcheck PASS（4 项全部通过）** |

两种内容态哈希（包内计算，内容文件未改）：

| 状态 | `content_hash` | 规模 |
| --- | --- | --- |
| 关闭（默认） | `7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f` | 31 NPC / 30 人物卡 / 9 地点 / 41 物品 / 27 委托 |
| `scaleup_w3` | `3fd99946b44a7dac4c3ea041d114e28d84ef07e67aa90e40e209970c2c43534c` | 60 NPC / 59 人物卡 / 20 地点 / 70 物品 / 60 委托 |

## 8. 最终产物说明

本文件记录的是从 `0ff9dc2` 树构建并经三路径实测的交付包。文档提交后会在**同一运行文件树**上
再重建一次 `dist/` 产物，使包内包含本文件与 `docs/RELEASE-NOTES-p3k.md` 的最终正文；
`src/`、`content/`、`ui/`、`config/`、`scripts/`、`deploy/`、`start.sh` 逐字节不变，
因此上述实测结论即最终交付包的结论。最终体积与两个 SHA256 以 `dist/p3k-SHA256.txt` 为准。

## 9. 未覆盖 / 风险

- 本轮 Docker 已从包自身实跑（服务模式，healthy）；legacy builder 的上下文偏大属已知限制。
- `/api/state` 字面路由已存在，为 `/api/player/state` 的只读别名（见 §3 口径）。
- 容量边界：单区 **150 席**对外；200 席可连入但 P95 不达标，需多区（详见
  `docs/RELEASE-NOTES-p3k.md` §2）。
- `reports/w4-ui/` 浏览器证据不入包；UI 行为以 `docs/PLAYER-GUIDE.md` 与代码为准。
- 72 天长周期、多人长跑仍未验证；委托补发只在玩家会话作用域生效。

## 10. v2 重打（`delivery-p3k` tag 之后的溯源修正）

> 本节为追加记录；§1–§9 的 v1 实测记录**原样保留**，两者差异有溯源价值。

**为何重打**：v1 包内 `README.md`（由 `deploy/README.md` 生成）第 3 行写「本版基于 `9c76cde`」，
实际交付基线是 `b23827a`。W5 已提交 `b6cfb89` 更正该行，并同步 README 内过时内容与测试计数
（`423 passed, 10 skipped`）。`delivery-p3k` tag 仍指向 `b23827a`，不动。

**新旧产物对应**：

| 版本 | 构建来源 | tar.gz SHA256 | zip SHA256 |
| --- | --- | --- | --- |
| v1（tag 时产物） | `b23827a` | `a7ef84ea8c457cd820dc6913e9e06ff0934d86d119afe3c6f316b6aa6564fe1d` | `2dac83d9e9106ceaa60b4329609e7956835fec706128d44380993b2b2ed6e01c` |
| v2（本次重打） | `b6cfb89` + 本文件所在文档提交 | 见 `dist/p3k-SHA256.txt` | 见 `dist/p3k-SHA256.txt` |

v2 的最终哈希、实际构建 HEAD SHA 与构建时间写在 `dist/p3k-SHA256.txt`（该文件不入库；
本文件随包，无法自指包哈希）。

**v2 冒烟（全新目录解包）**：

```bash
rm -rf /tmp/xkx-p3k-final8 && mkdir -p /tmp/xkx-p3k-final8
cd /tmp/xkx-p3k-final8 && tar -xzf dist/xkx-world-sim-delivery-p3k.tar.gz
cd world-sim
API_PORT=8567 PORT=8547 RUN_ID=p3k-final8 ./start.sh
```

| 检查 | 结果 |
| --- | --- |
| 包内 `README.md` 首行 | `# XKX / world-sim 对外试玩包（p3k）` |
| 包内 `README.md` 第 3 行 | 「本版基于 `b23827a`」 |
| `GET /api/state` | HTTP 200 |
| `GET /` | HTTP 200（玩家面板） |

结论：v2 仅修正 README 溯源与文档计数，运行文件与 v1 一致；三路径全量结论沿用 §3–§6。

## 11. 真服务端到端（tag 之后补验，2026-11-05）

**为什么补**：§3–§6 的服务模式证据是 `curl` 直连 API + 若干端点抽查，UI 侧此前只有
`tools/ui_check_proxy.py` 的 **mock 上游**。真 API（`--auth on` + 真 `service.sqlite`）→
真 `ui/player/server.py` 代理 → 真浏览器这条链路，从注册、令牌落盘、refresh 轮换、
中途 401、拒绝原因码到玩家闭环，没有一次性跑通过。

**版本与代码口径**

| 项 | 值 |
| --- | --- |
| 冻结 tag | `delivery-p3k` = `b23827a16bba6fa1527f58aaea44f8539a8e9301`（未移动） |
| 实测树 | `HEAD` = `2a1a57ba2fbbb5dfad5ee2929917054c03bfccdf`（含 tag 之后的 `e807cfc`/`0ff9dc2` 两笔 W4 修复） |
| 实测树来源 | `git archive HEAD` 快照（`/tmp/w4-e2e-src`），**不含**工作区未提交改动 |
| 服务 run / legacy run | `w4-e2e-1105074250` / `w4-e2e-legacy-1105074250` |
| 账号 | `w4e2e_1105074250` |
| 端口 | API `58233`（`--auth on`），代理 `58234`；legacy API/代理为另取的随机空闲端口 |
| 模型调用 | 0（全程本地） |

> 说明：W3 会话正在改 `src/wsim/sim/intent.py`（未提交）。为保证结论对应交付树而非
> 半成品，API 与代理都从 `git archive HEAD` 的快照启动，避免外人未落地改动污染证据。

**复现命令**

```bash
cd /Users/lyinet/projects/XKX/world-sim
rm -rf /tmp/w4-e2e-src && mkdir -p /tmp/w4-e2e-src
git archive HEAD | tar -x -C /tmp/w4-e2e-src
python3 tools/e2e_service_check.py --root /tmp/w4-e2e-src
# 输出：reports/w4-e2e/e2e-service.json + 5 张截图；退出码 0 表示 22 项全过
```

脚本自行拉起两组真实服务（服务模式 `--auth on`、legacy 模式 `--auth off`），各配一个
真实 `ui/player/server.py` 代理，再用 Playwright（headless Chromium）走真实页面；
`runs/` 下的一次性 run 目录即真实 sqlite 落盘位置（快照目录内）。

**逐项结果（22 checks，0 failed）**

| # | 验收项 | 结果 | 观测 |
| --- | --- | --- | --- |
| 1 | 注册 → 自动登录 → 令牌落盘 | **PASS** | UI 注册触发 `POST /api/auth/register` 1 次、`POST /api/auth/login` 1 次；`localStorage.ws_auth_v1` 含 `access_token`/`username`/`expires_at`（无口令） |
| 1b | 刷新后仍登录且读完整状态 | **PASS** | `reload()` 后 `hasAccess=true`、进入页关闭；状态含 `wallet.money=300`、`items=2`、`skills=1`、`relations`（22 个私有键，非 9 键公共摘要） |
| 2 | refresh 轮换 + 单飞 | **PASS** | 并发 2 个伪令牌 401：`refresh` 仅 **1** 次，两个读都成功；旧令牌 `/api/auth/me` **401**、新令牌 **200**；`hasAccess=true` 且 `authNotice` 隐藏（失败一侧未误清） |
| 3 | 中途 401 不吞世界状态 | **PASS** | 刷新重放前后 `tick=72`、`pharmacy`、`312 文`、`active=[quest:errand_market_ledger:0]` 完全一致；错误历史 **0 → 0** |
| 3b | 令牌被服务端轮换走后 | **PASS** | `/auth/me` 401；UI 钱包仍显示 `312 文`、地点仍 `药铺`、`hasAccess=false`、重新登录提示可见；仅 1 条 auth_required 记录 |
| 4 | legacy（`--auth off`）判定 | **PASS** | UI `authState().mode=legacy`；经真代理 `POST /api/auth/register|login|refresh` 全部 **503 `auth_disabled`** |
| 5 | 原因码端到端 | **PASS** | UI 错误浮层文案与原因码：`not_at_giver`（药铺接集市单）、`not_at_target`（回药铺交付客栈单）、`player_offline`（leave 后移动）、`auth_account_mismatch`（API 真 403 `code=auth_account_mismatch`，前端用令牌身份静默重取，不写红字） |
| 6 | service 模式玩家闭环 | **PASS** | 注册新号 → 进入世界 → 对话（真 NPC 回复、关系 `trust 40.5`）→ 接 `quest:tutorial_silk_sample:0` → 集市→药铺 → 交付 → 钱包 **300 → 312 文**，任务离开 active |

**截图证据**（均在 `reports/w4-e2e/`）

| 文件 | 内容 |
| --- | --- |
| `service-reload-state.png` | 刷新后仍登录、完整状态（钱包/背包/技能/关系） |
| `service-loop-delivered.png` | 交付后钱包 312 文 |
| `service-reason-codes.png` | 错误浮层展开，三条真实拒绝文案 + `原因码：not_at_target/player_offline/not_at_giver` |
| `service-mid-401-preserved.png` | 中途 401 刷新重放后：312 文、药铺、无错误红字 |
| `legacy-auth-disabled.png` | legacy 模式页面（登录/注册不可用） |

**产品缺陷**：本轮 **0 个**。仅有两处**测试假设**需要修正（不是产品问题）：

- `quest_deliver` 的定位前置是 `at_target or at_giver`（`src/wsim/actions/quests.py` 第 153–156 行），
  在集市（委托人所在地）交付账本单是**合法**的，所以 `not_at_target` 必须在集市之外触发；
- 证据脚本的 `read_state` 里 `st.location` 是对象（带 `id/name/exits`），不是字符串。

**未覆盖**：72 天长周期、多玩家并发压测、浏览器真机（仅 headless Chromium）；
playwright 请求日志只记录 `method/url`，不含 `Authorization` 头。
