# 交付实测 — delivery-p3l（活世界版 · 干净解包 · 三路径）

- 实测日期：2026-11-05（Asia/Shanghai）
- 被测代码：`d01ba9b`（= `102a312` + 复核清理 `11c3ac9` / `d01ba9b`；基线 `delivery-p3k` = `b23827a`）
- 被测包：由 `d01ba9b` 干净树 + 本文件所在文档提交构建
  （**不含**工作区未提交改动、`.venv`、`runs/`、`.git`、`.sqlite`）
- 机器：macOS，Python 3.12.7；全程零模型调用、零 API 费用
- 最终 tar/zip 的精确字节与 SHA256 见 `dist/p3l-SHA256.txt`（该文件不入库；
  本文件随包，无法自指包哈希）

## 1. 构建与包体

```bash
cd /Users/lyinet/projects/XKX/world-sim
git archive d01ba9b | tar -x -C /tmp/xkx-p3l-build
# 叠加文档提交中的 docs/RELEASE-NOTES-p3l.md、docs/DELIVERY-TEST-p3l.md、deploy/README.md
cd /tmp/xkx-p3l-build
python3 tools/build_dist.py --version p3l --out dist
```

| 项 | 结果 |
| --- | --- |
| 文件数 | **537** 个（清单 `dist/MANIFEST-delivery-p3l.txt`） |
| `tar.gz` | **8.27 MiB**（精确字节见 `dist/p3l-SHA256.txt`） |
| `zip` | **8.59 MiB**（精确字节见 `dist/p3l-SHA256.txt`） |
| 包根 `README.md` 首行 | `# XKX / world-sim 对外试玩包（p3l）`（由 `--version p3l` 注入） |
| `deploy/service.yaml` | 按设计**不在包内** |
| token 类文件 | **0**：解包后 `find . -name 'token*'` 为空 |
| 顶层 `runs/` / `.git/` / `.venv/` / `*.sqlite` | **0** |

> 候选包（`102a312`）曾含 `reports/w5/crash-drill/token-check.txt`，按复核结论**不可交付**；
> 本包从 `d01ba9b` 重打，已确认该文件不存在。风险说明见 `docs/RELEASE-NOTES-p3l.md` §1.6。

## 2. 三路径实测

### 2.1 legacy（`./start.sh` 默认，含世界自走）

```bash
cd /tmp/xkx-p3l-final2/world-sim
API_PORT=8572 PORT=8552 RUN_ID=p3l-final2 ./start.sh
```

| 时刻 | 观测 |
| --- | --- |
| 10:02:37 | 解包目录首次启动完成（**9s**，含建 venv、装依赖、2 日种子、起 API + 前端）：API 8572 / 前端 8552 就绪 |
| 10:02:37 | `/api/health`：`ok=true, mode=live, ambient=running, ambient_tick=0, ambient_speed=1.0, upstream_ok=true` |
| 10:02:37 | `GET /api/world/feed?limit=20`：HTTP 200，0.000775s；顶层 `ok/tick/limit/events`；`tick=0, n=0`（首个 tick 尚未落库） |
| 10:02:37 | `GET /api/state` HTTP 200；`GET /api/player/state` HTTP 200；`GET /` HTTP 200、12,997 B、`<title>入世 · 世界门面</title>` |
| 10:05:49 | 60 秒可见性见 §3.1 |

`XKX_AMBIENT=0` 关闭自走：在 `102a312` 候选包实测（端口 8571 / 8551，RUN_ID=`p3l-ambient-off`）：

| 项 | 结果 |
| --- | --- |
| `/api/health` | `ambient=stopped, ambient_tick=null, tick=0` |
| 等待 >20s 后 | tick 仍为 **0**，`/api/world/feed` 返回 `events=[]` |
| 结论 | 自走确实未启动，行为与旧版一致 |

> **证据口径**：`d01ba9b` 未改 ambient 启动逻辑；关闭态沿用候选包实测，**未在最终包复跑**。

### 2.2 service 模式（`--auth on` + UI 代理）

```bash
cd /tmp/xkx-p3l-final2/world-sim
PYTHONPATH=src XKX_AMBIENT=1 .venv/bin/python -m wsim.api \
  --root . --run p3l-svc-final --port 8589 --auth on
.venv/bin/python ui/player/server.py --upstream http://127.0.0.1:8589 --port 8559
```

API 直连（10:02:52 就绪，1s）：

| 场景 | 观测 |
| --- | --- |
| 注册 / 登录 / enter | 200 / 200 / 200（`created=true`, `tick=1`） |
| 匿名 `GET /api/state?account_id=u_p3l_final` | **200**，恰好 9 键：`activity/idle_seconds/name/ok/online/place/sect_name/server_tick/shop_name`；`wallet/items/relations` 均不存在 |
| 本人令牌 `GET /api/state` | **200**，完整状态（`player.wallet.money=300`、地点集市、`tick=7`） |
| 令牌 + 他人账号 | **403** `auth_account_mismatch` |
| 匿名 `GET /api/player/intents/i:deadbeef` | **401** `auth_missing_token` |
| 令牌 `POST /api/player/action`（move→academy） | **200** `ok=true, status=executed`（候选包同路径已验，最终包同代码） |
| 匿名 `GET /api/world/feed?limit=20` | **200**，顶层 `ok/tick/limit/events`，事件 6 字段、升序；`tick` 与 health `ambient_tick` 同一时钟 |

UI 代理（8559，1s 就绪）：

| 场景 | 观测 |
| --- | --- |
| `/api/health` | **200**，`mode=live`，`ambient_tick` 与 API 一致 |
| 匿名 `/api/state?account_id=u_p3l_final` | **200**，仅 9 键公共摘要 |
| 带令牌 `/api/player/state` | **200**，完整状态（集市 / 300 文） |
| 带令牌跨账号 `/api/state?account_id=u_other` | **403** 原样透传 |
| `/api/world/feed?limit=20` | **200**，20 条真实 NPC 事件（`tick=41`） |
| `GET /` | **200**，12,997 B |

### 2.3 Docker（从最终交付包自身 `deploy/` 起容器）

```bash
cd /tmp/xkx-p3l-final2/world-sim/deploy
docker-compose up -d --build
```

| 时刻 | 观测 |
| --- | --- |
| 10:04:00 | 开始构建（`python:3.12-slim`、17 步、成功打 tag `xkx/world-sim:w5`；legacy builder 上下文约 18.5 MB） |
| 10:04:0x | `Container xkx-world Started`；**4s** 内 `healthy` |
| inspect | `running healthy`，`FailingStreak=0` |
| `/api/health` | **200**，`mode=live`，`ambient=stopped`（默认），`tick=27` |
| `/api/world/feed?limit=20` | **200**（默认关闭自走，返回历史窗口） |
| `/api/state` / `GET /` | **200** / **200**（12,997 B） |

显式开启容器内自走（临时 override 注入 `XKX_AMBIENT=1`，不改包内文件）：

| 项 | 结果 |
| --- | --- |
| 健康 | `healthy`（**4s** 内），`failing=0` |
| `/api/health` | **200**，`mode=live, ambient=running, ambient_tick=27` |
| `/api/world/feed?limit=20` | **200**，返回真实 NPC 事件（曹半仙 `move/quest_accept/work`、石敢当 `move`） |
| 鉴权矩阵 | 匿名 9 键摘要、令牌完整状态、跨账号 **403**、匿名 intents **401**、令牌 move **200 executed** |
| `GET /` | **200** |

Docker 说明：本机为独立版 `docker-compose` 5.6.1（legacy builder，不读
`deploy/Dockerfile.dockerignore`，构建上下文偏大）；生产建议 Compose v2 / BuildKit。
**容器默认不自走**是当前包的已知边界，需要活世界容器时显式设 `XKX_AMBIENT=1`。

## 3. 世界动态 60 秒可见性

### 3.1 legacy（`p3l-final2`，10:04–10:05）

| 时点 | health `ambient_tick` | feed `tick` | 事件数 | 窗口内最大 tick |
| --- | --- | --- | --- | --- |
| T0（10:04:49） | 21 | 21 | 20（limit 满窗） | 21 |
| T60（10:05:49） | **31** | **31** | **20**（limit 满窗） | **31** |

- `same_clock=true`；60 秒窗口内 20 条全部是 tick > T0 的**新增** NPC 事件；
  样例：`{"tick":31,"actor":"柳三娘","verb":"move","place":"客栈","summary":"动身前往客栈","important":false}`

### 3.2 service（`p3l-svc-final`，10:02:53–10:03:53）

| 时点 | health `ambient_tick` | feed `tick` | 事件数 | 窗口内最大 tick |
| --- | --- | --- | --- | --- |
| T0（10:02:53） | 7 | 7 | 2 | 7 |
| T60（10:03:53） | **16** | **16** | **14** | **14** |

- `same_clock=true`；窗口内新增 **12** 条真实 NPC 事件；
  样例：`{"actor":"石敢当","important":false,"place":"集市","summary":"石敢当在集市活动","tick":14,"verb":"debt_settle"}`

### 3.3 UI 面板

- 中栏默认可见、固定高度独立滚动、8 秒独立节流、匿名请求、失败静默「世界暂时安静」、
  动词中文映射：代码核对 `ui/player/index.html`、`ui/player/style.css`、`ui/player/app.js`。
- **浏览器渲染证据沿用 `reports/w4-feed/feed-checks.json` + 截图，本轮未复跑浏览器**；
  p3l 未改动 feed 渲染逻辑（仅复核清理了报告文件），静态核对与证据一致。

## 4. 并发 P95

口径：`tools/load_test.py`，50 玩家并发 × 30s，`think=2s`，全新空日志，0 模型调用。

| 实例 | 请求 | 错误 / 5xx / 429 | 全端点 P95 | `/api/state` P95 | 不变量 |
| --- | --- | --- | --- | --- | --- |
| service 空日志（8588） | 1,428 | 0 / 0 / 0 | 32.644ms | **10.776ms** | PASS |
| service + ambient（8587） | 1,446 | 0 / 0 / 0 | 31.453ms | **10.517ms** | PASS |

- 两个实例均 `<300ms` 门槛，`seq` 连续、钱包 delta=0、`integrity=ok`、无半写。
- 验收口径（更密集请求、50 并发 × 30s）：**11,542 请求 / 0 error / 0 5xx**，
  `GET /api/state` **P95 22.579ms**（`CHANGELOG.md`，对照 29.3ms 未劣化）。
  两组数字条件不同，均保留，不互相替代。

## 5. 崩溃恢复 8/8（本轮**重跑**，不是沿用）

```bash
cd /tmp/xkx-p3l-final/world-sim
OUT=/tmp/p3l-crash-drill RUN=p3l-crash-drill XKX_AMBIENT=1 deploy/crash_drill.sh
```

20 并发压测中途 `kill -9`，重启后校验；本轮在 **ambient 与玩家同进程**条件下重跑：

| 检查 | 结果 |
| --- | --- |
| 无重复创世 | PASS（genesis=1） |
| 事件数单调 | PASS（6 → 446（kill 瞬间）→ 1238（重启后）） |
| seq 连续 | PASS |
| sqlite integrity | PASS（`ok`） |
| 钱包守恒 | PASS（delta=0） |
| 无半写 | PASS（`half_write_rows=[]`） |
| 旧令牌仍有效 | PASS（重启后 `/api/auth/me` 200） |
| 内容 hash 篡改退出码 | PASS（**3**） |

结论：**8/8 PASS**。本轮在 `102a312` 候选包（ambient 与玩家同进程）**重跑**；
`d01ba9b` 未改运行时与崩溃路径，**最终包沿用本次重跑，未再复跑**。p3k 的 8/8 证据未沿用。

## 6. 包内自检

在解包目录执行 `./scripts/selfcheck.sh`：

| 项 | 结果 |
| --- | --- |
| 内容包校验 | PASS（NPC 30 人、初始货币 35,650 文、关系边 106 条） |
| 单元测试 pytest | **439 passed, 12 skipped**（129.75s） |
| 框架验收 | **6/6 PASS**，`replay=MATCH`，守恒=0 |
| 玩家闭环 | PASS（enter → 移动 → 对话 → 接任务 → 刷新状态仍在） |
| 总结 | **selfcheck PASS（4/4）** |

内容态哈希（内容文件未改）：

| 状态 | `content_hash` | 规模 |
| --- | --- | --- |
| 关闭（默认） | `7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f` | 31 NPC / 41 物品 / 9 地点 / 27 委托 / 30 人物卡 |
| `scaleup_w3` | `3fd99946b44a7dac4c3ea041d114e28d84ef07e67aa90e40e209970c2c43534c` | 60 NPC / 70 物品 / 20 地点 / 60 委托 / 59 人物卡 |

## 7. 未覆盖 / 风险

- **Docker 默认不自走**：需要显式 `XKX_AMBIENT=1`；本轮两种方式都已实测。
- **UI 浏览器证据沿用 `reports/w4-feed/`，未复跑浏览器**；feed 契约与 60 秒可见性已用真 API 复测。
- **容量边界不变**：单区 150 席对外；200 席可连入但 P95 不达标；扩容方案未实现。
- **4 件物品零成交未修**：见 `docs/known-limitations.md` §12。
- 72 天长周期、多人长跑仍未验证；委托补发只在玩家会话作用域生效。

## 8. 最终产物

本文件与 `docs/RELEASE-NOTES-p3l.md` 随文档提交进入包内；`src/`、`content/`、`ui/`、
`config/`、`scripts/`、`deploy/`（除 `README.md`）、`start.sh` 与 `d01ba9b` 逐字节一致。
最终包体积与两个 SHA256 以 `dist/p3l-SHA256.txt` 为准；tag `delivery-p3l` 指向 `d01ba9b`。
