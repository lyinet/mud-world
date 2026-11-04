# 交付实测 — delivery-p3k（干净解包 · 一条命令起服务）

- 实测日期：2026-11-04（Asia/Shanghai）
- 被测代码：`e317223`（含 W5 `9c76cde`、W5 交付边界文档 `d9c5a13`、W6 出包修复）
- 被测包：`xkx-world-sim-delivery-p3k.tar.gz` / `.zip`，由 `git archive HEAD` 的干净树构建
  （**不含**工作区未跟踪文件、`.venv`、`runs/`、`.git`、`.sqlite`）
- 机器：macOS，Python 3.12.7；全程不调用模型、不产生 API 费用

## 1. 构建

```bash
cd /Users/lyinet/projects/XKX/world-sim
rm -rf /tmp/xkx-p3k-build && mkdir -p /tmp/xkx-p3k-build
git archive HEAD | tar -x -C /tmp/xkx-p3k-build
cd /tmp/xkx-p3k-build
python3 tools/build_dist.py --version p3k --out dist
```

观测：`487` 个文件；`tar.gz` = **2,363,744 B（2.25 MiB）**，
`zip` = **2,682,998 B（2.56 MiB）**；解包后约 **20 MiB**（首次 `./start.sh` 另建 `.venv` 约 27 MiB）。

```text
SHA256  xkx-world-sim-delivery-p3k.tar.gz  7f2a654f4a92170e009ee6e603668a07c9ffcd0c0b946c74907eb5072fa97e55
SHA256  xkx-world-sim-delivery-p3k.zip     509db2c03098361930682fa72ae71a2b47c5361c12846f63d057924793ef91db
```

清单校验（`dist/MANIFEST-delivery-p3k.txt`，共 487 行）逐条确认存在：

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

反向校验：清单中 **0** 条匹配 `.venv/`、顶层 `runs/`、`.git/`、`*.sqlite`、`.env`、`token.txt`。
`deploy/service.yaml` 按设计**不在包内**（原因见 `docs/RELEASE-NOTES-p3k.md` §1.6）。

## 2. 干净解包 + 一条命令起服务

```bash
rm -rf /tmp/xkx-p3k-final2 && mkdir -p /tmp/xkx-p3k-final2
cd /tmp/xkx-p3k-final2
tar -xzf /tmp/xkx-p3k-build/dist/xkx-world-sim-delivery-p3k.tar.gz
cd world-sim
API_PORT=8562 PORT=8542 RUN_ID=p3k-final ./start.sh
```

`start.sh` 唯一依赖包内文件；不读取源码仓库，也不依赖原工作区 `runs/`、`reports/`。

实测时间线（2026-11-04）：

| 时刻 | 观测 |
| --- | --- |
| 21:48:00 | `[start] 首次运行：创建 .venv ...`（全新目录，无预置环境） |
| 21:48:11 | 种子世界生成完成：**2 游戏日 · 836 条事件 · NPC 自主行动 736 条** |
| 21:48:20 | `[demo] 健康检查: API 200 (GET /api/player/state)` |
| 21:48:20 | `[demo] 健康检查: 前端 200 (GET /api/health)`，`mode=live, upstream_ok=true` |
| 21:48:20 | API `{"ok": true, "listen": "http://127.0.0.1:8562", "auth_required": false, "auth_mode": "auto"}` |
| 21:48:20 | 前端 `http://127.0.0.1:8542/` 就绪（反代到 API） |

结论：**首次全流程约 20 秒**（含建 venv、装依赖、生成 2 日种子、起 API + 前端）。

## 3. 端点观测（逐条）

| # | 命令 | 观测 |
| --- | --- | --- |
| 1 | `curl -sS -m 5 -w '\nHTTP %{http_code} time=%{time_total}s\n' http://127.0.0.1:8562/api/state` | **HTTP 404**，`{"ok": false, "reject_reason": "unknown_action"}`，0.0018s |
| 2 | `curl -sS 'http://127.0.0.1:8562/api/player/state?account_id=u_new'` | **HTTP 200**，`{"ok": false, "reject_reason": "player_not_entered"}`，0.0009s |
| 3 | `curl -sS http://127.0.0.1:8542/api/health` | HTTP 200 |
| 4 | `curl -sS -o /tmp/index.html -w '%{http_code} %{size_download}' http://127.0.0.1:8542/` | HTTP 200，返回玩家面板 HTML（`<title>` 为 `入世 · 世界门面`） |

> **口径必须写清**：服务没有字面 `/api/state` 路由，只有 `GET /api/player/state`。
> 压测工具输出标签写的是 `/api/state`，实际请求的是 `/api/player/state`。
> 这是文档口径差异，不是服务不可用；对客文档统一用 `/api/player/state`。

## 4. 玩家闭环（经前端反代，account=`u_p3k4`，角色名“初三”）

```bash
# 进入世界
curl -X POST http://127.0.0.1:8542/api/player/enter -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3k4","display_name":"初三","archetype":"捕快"}'
# 动作统一 POST /api/player/action
```

| 步骤 | 观测 |
| --- | --- |
| enter | `ok=true`，`created=true`，`tick=120`，钱包 **300 文** |
| state | `ok=true`，地点 **集市**，在场 NPC 7，可接任务 8 |
| quest_accept `quest:daily_market_inquiry:0` | `ok=true`，`tick=126`，`active_quests=[quest:daily_market_inquiry:0]` |
| move `academy` | `ok=true`，`tick=132`，地点 **书院** |
| talk `bai_lusheng` | `ok=true`，`tick=138`，回复 29 字 |
| move `market` → `inn` | `ok=true`，`tick=150`，地点 **客栈** |
| quest_deliver 同一任务 | `ok=true`，`tick=156` |
| move `market` → `open_shop`（摊） | `ok=true`，`tick=168` |
| 最终 state | `ok=true`，`tick=174`，地点 **集市**，钱包 **9 文**，`active_quests=[]` |

玩家面板 HTML、健康检查、匿名写入均正常；**一键试玩链路成立**。

## 5. 扩展包开关（默认关闭）

```bash
XKX_CONTENT_PACKS=scaleup_w3 PYTHONPATH=src \
  .venv/bin/python -m wsim.api --root . --run p3k-pack --port 8572
```

观测（account=`u_p3kpack`）：

- 进入成功（`ok=true`，`tick=0`，钱包 300 文，地点集市）；
- 集市出口 **13** 个（关闭态 8 个），包含 `salt_depot`、`dye_works`、`book_shop` 等新地点；
- 在场 NPC 7 人，含扩展包新 NPC `xing_pojun`；
- 任务板 `open=59`（关闭态 26）；
- 内容统计：**60 NPC / 59 人物卡 / 20 地点 / 70 物品 / 60 委托**。

两种内容态哈希（包内计算，未改内容）：

| 状态 | `content_hash` | 规模 |
| --- | --- | --- |
| 关闭（默认） | `7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f` | 31 NPC / 30 人物卡 / 9 地点 / 41 物品 / 27 委托 |
| `scaleup_w3` | `3fd99946b44a7dac4c3ea041d114e28d84ef07e67aa90e40e209970c2c43534c` | 60 NPC / 59 人物卡 / 20 地点 / 70 物品 / 60 委托 |

## 6. 包内自检

在解包目录 `/tmp/xkx-p3k-final2/world-sim` 执行：

```bash
./scripts/selfcheck.sh
```

实测（21:52 完成）：

| 项 | 结果 |
| --- | --- |
| 内容包校验 | **PASS**（NPC 30 人、初始货币 35,650 文、关系边 106 条） |
| 单元测试 pytest | **414 passed, 10 skipped**（115.27s） |
| 框架验收 `tools/framework_check.py`（临时副本） | **6/6 PASS**；source=llm 落地 63/63，`replay=MATCH`，守恒=0 |
| 玩家闭环（临时端口随机） | **PASS**（enter → 书院 → 对话 → 接任务 → 刷新状态仍在） |
| 总结 | **selfcheck PASS（4 项全部通过）** |

## 7. 最终包与实测包的差异

最终 `p3k` 由文档提交后的同一 HEAD 重建。相对本实测包（`e317223` 树）**只多两个新文档并更新
`QUICKSTART.md` 的版本数字**：

- 新增 `docs/DELIVERY-TEST-p3k.md`（本文件）
- 新增 `docs/RELEASE-NOTES-p3k.md`
- 更新 `docs/QUICKSTART.md`（p3j 旧数字 → p3k 实测数字）

`src/`、`content/`、`ui/`、`scripts/`、`tools/`、`deploy/` 等运行文件逐字节相同
（`diff -rq` 只列出上述 3 个 docs 差异，清单逐文件 SHA256 一致）；因此第 2–6 节的实测结论
对最终包同样成立。

最终包（489 文件，约 2.26 MiB）再次干净解包到 `/tmp/xkx-p3k-deliver` 并用同一命令复核：

| 复核项 | 最终包观测（21:54，端口 8562/8542） |
| --- | --- |
| 首次启动 | 21:54:23 建 `.venv`，21:54:38 API + 前端就绪（约 15s） |
| `GET /api/service/info` | `{"ok": true, "auth_required": false}` |
| `GET /api/state` | HTTP 404 `unknown_action`（0.0028s，口径见 §3） |
| `GET /api/player/state?account_id=u_new` | HTTP 200 `player_not_entered`（0.0009s） |
| `GET /` | HTTP 200，12,406 B，`<title>入世 · 世界门面</title>` |
| `POST enter`（`u_p3kd` / “初十”） | `ok=true`，`tick=0`，钱包 300 文 |
| `GET /api/player/state` | `ok=true`，`tick=6`，集市，在场 NPC 7，`open_quests=26` |

## 8. 未覆盖 / 风险

- 未在本次解包中重复 `docker compose` 起容器；容器路径沿用 W5 `9c76cde` 的
  `docs/OPS-RUNBOOK.md` 与 `reports/w5/**`。本机 Docker 验证使用独立版 `docker-compose`
  （legacy builder，不读 per-Dockerfile ignore）；生产建议 Compose v2 / BuildKit。
- 包根 `README.md` 仍标 p3j（由 `deploy/README.md` 生成，`deploy/**` 属 W5 所有权，本轮未改）；
  以 `docs/QUICKSTART.md`、本文件与 `docs/RELEASE-NOTES-p3k.md` 为准。
- `/api/state` 字面 404，真实端点为 `/api/player/state`（见 §3）。
- 容量边界：单区 **150 席**对外；200 席可连入但 P95 不达标，需多区（详见 RELEASE-NOTES §2）。
- `reports/w4-ui/` 未入库，p3k 包内不含 W4 浏览器证据。
