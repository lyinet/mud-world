# world-sim · 九地点世界

一个事件溯源、可确定性回放的文字世界：Python 模拟内核 + 会自主行动的 NPC + 浏览器前端。
不需要模型 key、GPU 或外部数据库，首次运行会在本地生成种子世界。

| 项目 | 值 |
|---|---|
| 分支 | `master`（`df2b37c`） |
| 世界内容 | 9 个地点 / 31 位 NPC |
| 测试 | 687 项（669 passed / 18 skipped / 0 failed） |
| Python | 3.10+；核对环境 3.12.7 |
| 许可 | 仓库无 LICENSE 文件，见文末 |

> 另有一个 `xkx-parity` 分支在做侠客行复刻，进行中；那是独立内容包和独立端口，不在本 README 范围。

---

## 1. 这是什么

- **世界状态不是内存里的一份对象，而是一串事件。** `events.sqlite` 只追加，`WorldState` 是可随时重建的投影。
- **写入是单写者串行。** 玩家动作、NPC 自走、后台任务都进同一个 Writer，按 tick 提交，避免并发写冲突。
- **随机数是确定的。** 引擎只用 SplitMix64，不用 Python `random`；同一个种子、同一串输入，回放出同一个世界。
- **NPC 会自己行动。** 修炼、开店、接委托、结婚都由世界自走推进，不依赖玩家在线。
- **模型只负责台词。** 模型生成的对话写入 `dialogue_aside.jsonl`，不进入事件序列，也不改世界哈希。

一句话：它先是一个可审计、可回放的世界模拟内核，然后才是一个能玩的文字前端。

## 2. 现在能玩什么

下面每行都来自 **2026-11-07 本次实跑**，不是旧报告摘抄。核对环境：
`./start.sh` 起在 `127.0.0.1:18052`（API）与 `127.0.0.1:18051`（前端），账号 `u_readme_2611`。

| 动作 | 本次命令 | 本次真实输出 | 时间 |
|---|---|---|---|
| 注册 | `POST /api/auth/register {"username":"u_readme_2611","password":"readme-pass-2611"}` | `{"account_id":"u_readme_2611","created":true,"ok":true}` | 16:00:47 |
| 登录 | `POST /api/auth/login {"username":"u_readme_2611","password":"readme-pass-2611"}` | `{"ok":true,"account_id":"u_readme_2611","username":"u_readme_2611"}` | 16:00:47 |
| 进入 | `POST /api/player/enter {"account_id":"u_readme_2611","display_name":"README游侠2611"}` | `granted=true, money=300, items=[粗布（一匹）×1, 炊饼×3]` | 16:00:47 |
| 看 | `GET /api/player/state?account_id=u_readme_2611` | 地点`集市`；出口`书院/客栈/药铺/铁匠铺/茶棚`；在场 10 人；钱包 300 文 | 16:00:47 |
| 交谈 | `POST /api/player/action {"verb":"talk","args":{"target":"liu_sanniang","message":"最近有什么新鲜事"}}` | `街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。阿是这话，你也听得的？` | 16:00:58 |
| 买 | `POST /api/player/action {"verb":"buy","args":{"item":"flatbread","qty":1}}` | `ok=true, status=executed`；随后 state 复核钱包 `297` 文 | 16:00:47 |
| 接任务 | `POST /api/player/action {"verb":"quest_accept","args":{"quest":"quest:daily_smithy_bellows:149"}}` | `ok=true, status=executed`；任务`【中】铁匠铺拉风箱`进入已接列表 | 16:00:58 |
| 走 | `POST /api/player/action {"verb":"move","args":{"to":"smithy"}}` | `ok=true, status=executed`；复核地点变为`铁匠铺` | 16:00:47 |

同一份 `player-api.md` 里还有押镖、门派、婚姻、收徒、产业等动作。它们在当前版本可用，但本次没有逐项演示，因此不在此表列“已完成”。

前端就是这些动作的可视化壳：左栏地图、中栏世界动态、右栏“今天能做什么”。直接调 API 和点页面走的是同一套动作与同一个事件日志。

## 3. 快速开始

### 3.1 本地一条命令

要求：Python 3.10 以上。首次运行脚本会创建 `.venv`、装锁定依赖、生成种子世界。

```bash
cd world-sim
./start.sh
```

成功后终端会打印世界统计、访问地址和自走状态。关键行类似：

```text
[start] Python: Python 3.12.7 (<repo>/.venv/bin/python)

种子世界: quickstart-2d · 2 游戏日 · 735 条事件 · NPC 自主行动 637 条
[start] 启动 API + 玩家前端 + 世界自走（XKX_AMBIENT=1，0 可关闭）...
[demo] 启动 wsim.api -> http://127.0.0.1:8020
[demo] 健康检查: API 200 (GET /api/player/state)
[demo] 启动玩家前端（反代模式）-> http://127.0.0.1:8000 (upstream http://127.0.0.1:8020)
```

打开 <http://127.0.0.1:8000/>。端口被占用时换一组：

```bash
API_PORT=8120 PORT=8010 ./start.sh
```

按 `Ctrl-C` 会同时停掉 API、前端和世界自走。日志在 `runs/<RUN_ID>/logs/{api,ui,ambient}.log`。
不想要 NPC 自走：`XKX_AMBIENT=0 ./start.sh`。

**首次进入没有默认口令。** 在页面注册，或按 [docs/AUTH-API-W1.md](docs/AUTH-API-W1.md) 调注册/登录接口。
新账号第一次 `enter` 固定发放：300 文、炊饼×3、粗布×1。

本次实测启动耗时（已有 `.venv`、种子世界已存在，端口 18032/18031）：
API 首次 `/api/health` 返回 `ok=true` 用 **0.70s**，前端首页可访问用 **0.92s**。
首次冷启动还要建 venv 和生成种子，耗时不等于这两个数。

### 3.2 Docker Compose

```bash
cd world-sim
./deploy/gen_selfsigned_cert.sh          # 生成 deploy/certs/xkx.crt / xkx.key
docker-compose -f deploy/docker-compose.yml up -d --build
docker-compose -f deploy/docker-compose.yml ps
```

默认端口：

- 世界 API：`127.0.0.1:8020`
- 玩家前端：`127.0.0.1:8030`
- nginx：`80` / `443`（TLS 入口）

容器服务为 `world` / `admin` / `backup` / `proxy`。停：

```bash
docker-compose -f deploy/docker-compose.yml down
```

本机核对说明：Docker 29.9.2，只有独立版 `docker-compose`（5.6.1），没有 `docker compose` 子命令；
本次只跑了 `docker-compose config` 校验配置，**没有实际启动容器**。生产环境按
[docs/OPS-RUNBOOK.md](docs/OPS-RUNBOOK.md) 使用 Compose v2。

### 3.3 服务模式（鉴权开启）

```bash
cd world-sim
RUN=service PORT=8020 ./deploy/run_service.sh
```

输出：

```text
[run_service] root=<repo> run=service listen=http://127.0.0.1:8020 auth=auto
```

`deploy/service.yaml` 存在时 `auth=auto` 会开启鉴权，流程仍是注册 → 登录 → 带 Bearer token 进入。
账号库和存档都在 `runs/service/`。

### 端口一览

| 进程 | 默认地址 |
|---|---|
| `./start.sh` 世界 API | `127.0.0.1:8020` |
| `./start.sh` 玩家前端 | `127.0.0.1:8000` |
| Docker 世界 API | `127.0.0.1:8020` |
| Docker 玩家前端 | `127.0.0.1:8030` |
| Docker nginx | `:80` / `:443` |
| `deploy/run_service.sh` | `127.0.0.1:8020` |
| 管理台后端 | `127.0.0.1:8032` |
| 管理台页面 | `127.0.0.1:8039` |

## 4. 目录结构

```text
world-sim/
├── start.sh                 # 本地一键启动：API + 前端 + 世界自走
├── src/wsim/
│   ├── core/                # 事件、tick、确定性 RNG、哈希
│   ├── world/               # WorldState、Writer、回放
│   ├── actions/             # 动作与前置条件
│   ├── sim/                 # NPC 目标、日程、自走
│   ├── api/                 # 玩家 API、鉴权
│   ├── admin/               # 管理台后端
│   └── agent/               # 模型调用与对话旁路
├── content/                 # 九地点内容（YAML）
│   ├── locations.yaml
│   ├── npcs.yaml
│   ├── items.yaml
│   ├── verbs.yaml
│   └── additions/           # 增量内容（茶棚等）
├── ui/player/               # 玩家前端
├── ui/admin/                # 管理台页面
├── deploy/                  # Docker、nginx、服务启动、证书、备份
├── docs/                    # 玩家 API、管理台、运维、容量、soak
├── scripts/                 # demo、selfcheck、内容校验
├── tests/                   # pytest
└── runs/                    # 运行时存档与日志（不入库）
```

## 5. 核心设计

### 5.1 事件日志是唯一事实源

```text
玩家动作 / NPC 自走 / 后台任务
        ↓
    单 Writer，按 tick 串行
        ↓
events.sqlite（append-only）
        ↓ 回放
WorldState（内存投影，可随时重建）
```

`events.sqlite` 只追加，不原地改。要查“为什么现在是这个状态”，回放到对应 seq 即可。

### 5.2 单写者

所有写入走同一个 Writer。玩家请求不是直接改内存，而是生成意图、过前置条件、提交事件。
这样同一 tick 内的并发请求也有确定顺序。

### 5.3 确定性随机

引擎只用 SplitMix64。同一条事件序列、同一个种子，重复回放得到同一状态。
这也是测试能对世界哈希做逐字节比较的前提。

### 5.4 三级哈希

- `content_hash`：内容包哈希
- `event_final`：事件序列哈希
- `state_final`：回放后的世界状态哈希

本次核对的 2 日种子世界（`runs/quickstart-2d`，seed=42，735 事件）：

```text
content_hash  5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4
event_final   02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae
state_final   009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9
```

**无玩家时，世界哈希逐字节不变。** 同样的事件序列回放两次，`event_final` 与 `state_final` 相同。

### 5.5 模型台词为什么不改世界哈希

模型输出只做一件事：生成台词。台词写入 `dialogue_aside.jsonl`，与 `events.sqlite` 分离；
它不进入 `WorldState`，不参与 `content_hash` / `event_final` / `state_final` 计算。

所以换模型、换中转站、改 prompt，只会改变玩家看到的句子，不会改变世界的确定性结果。

## 6. 管理控制台

管理台是独立进程，不跟玩家 API 共用端口。本机实际使用后端 `8032`、页面 `8039`：

```bash
cd world-sim

export PYTHONPATH=src
export XKX_ADMIN_TOKEN='<管理登录口令>'
export XKX_SECRET_KEY="$(python3 -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())')"
export XKX_ADMIN_DB="$PWD/runs/<RUN>/service.sqlite"

# 后端
.venv/bin/python -m wsim.admin --root . --run <RUN> --host 127.0.0.1 --port 8032

# 页面（同源代理，避免 CORS）
.venv/bin/python ui/admin/server.py --port 8039 --upstream http://127.0.0.1:8032
```

打开 <http://127.0.0.1:8039/>，用 `XKX_ADMIN_TOKEN` 登录。

代码里的 `--port` 默认值是 `8031`；本机把玩家 API 放在 8031，所以管理台改用 8032，页面代理到 8039。
两处都实跑过：后端 `GET /api/admin/*` 需要登录后的 Bearer token，未登录直接 401。

能改什么：

- 世界参数与运行开关
- 模型 provider / 中转站地址
- API key（AES-256-GCM 加密后落库，明文不写日志）
- 按能力位选择模型
- 预算与调用审计

能力位共 7 个：`dialogue`、`strategy`、`self_goal`、`night_batch`、`quest_gen`、`persona_gen`、`validation`。
当前代码里真实接上调用点的只有 `dialogue`、`strategy`、`night_batch`；其余 4 个是配置位，不要当成已生效能力。

详见 [docs/ADMIN-CONSOLE.md](docs/ADMIN-CONSOLE.md)。

## 7. 部署与运维

完整步骤在 [docs/OPS-RUNBOOK.md](docs/OPS-RUNBOOK.md)，这里只列入口。

**TLS**：自签证书用 `./deploy/gen_selfsigned_cert.sh`；正式证书走 ACME，替换后
`docker-compose -f deploy/docker-compose.yml restart proxy`（nginx 启动时读证书，不热加载）。

**备份**：用 sqlite 在线备份 API，不要 `cp`。

```bash
.venv/bin/python tools/backup_snapshot.py --root . --run service
sh tools/backup_offsite.sh --backup-dir backups
```

备份 manifest 带 `sha256`、事件 `seq`、`content_hash`，可直接用于异机恢复核对。

**探活**：

```bash
curl -s http://127.0.0.1:8020/api/health
curl -s http://127.0.0.1:8020/healthz
```

**限流与登录锁定**：登录失败按 900s / 3600s / 86400s 退避，锁定期间返回 429。

**回滚**：按 [docs/RELEASE-ROLLBACK.md](docs/RELEASE-ROLLBACK.md) 回到上一个 `delivery-*` tag，
再跑一次备份校验。不要直接改 `events.sqlite`。

## 8. 测试与门禁

```bash
cd world-sim
PYTHONPATH=src .venv/bin/python -m pytest -q
bash scripts/selfcheck.sh
```

本次结果（2026-11-07 15:20 起跑）：

```text
pytest:  687 tests, 669 passed, 18 skipped, 0 failed, 0 errors, 142.16s
selfcheck: 4/4 PASS
```

`selfcheck` 覆盖：种子/回放一致性、API 健康、玩家前端、玩家闭环。

CI 在 `.github/workflows/ci.yml`，2 个 job：

- pytest + 内容校验 + 2 日冒烟
- selfcheck

触发条件：push 到 `master`、针对 `master` 的 PR。

## 9. 分支与发布状态

| 名称 | 当前值 |
|---|---|
| `master` | `df2b37c`（与 `origin/master` 一致） |
| 复位前保险 tag | `master-before-reset-20261107` → `121fb37` |
| 复位基线 tag | `pre-xkx-20261107` → `df2b37c` |
| 发布 tag | `delivery-p3l` |

## 10. 已知限制

**容量**

- 100 席是安全线；150 席时写请求 P95 到 958ms；500 席需要分区，当前单实例不支持。
- 单区域玩家席位硬上限 200。这是防刷上限，不是性能目标。

**长跑内存：旧结论已纠正**

- 旧报告曾把 RSS 线性增长判为 “Event/AuditRecord 无界泄漏”。这是**曾误判、已纠正**，原结论作废。
- 复核发现多出来的 Event/AuditRecord 由**长跑探针自己持有**：`gc.get_objects()` 返回的列表被探针 frame 的局部变量引用，gen2 不回收。证据见 `reports/soak/ownerprobe-20261107/_probe/owners.json`。
- 产品侧缓存本来就是有界的：幂等表 2 万条、审计队列 4096 条。
- 最小修复（幂等表只存 `seq`，命中时按 seq 回读，不再存整个 Event）在 `fix/health-no-inline-integrity` 分支，**尚未合入 `master`**；该分支回归 668 项通过。
- 20 分钟定向复测仍有两项未过：RSS 5%/h 门槛（实测 88.0%/h，但线性拟合 `r²=0.218`，不支持恒定线性泄漏）、写 P95 漂移 ≤1.30（实测 1.32）。
- **仍未关闭**：写延迟尖峰 `write.max = 1277ms`。不要把它写成已修复。
- 复测数据：`reports/soak/hotspot-r2c-20261107/summary.json`；新一轮 `hotspot-r2d-20261107` 结果仍在核对。

**其它**

- 本机没有 Docker Compose v2，本次 Docker 路径只做配置校验，未启动容器。
- 仓库根目录没有 `LICENSE` 文件。

## 11. 许可与数据须知

- 本仓库没有 `LICENSE` 文件。对外使用前需要补授权，当前按“内部研究与评估”对待。
- 仓库内不含 xkx 的参照内容。`xkx-parity` 分支的侠客行内容源自 `fluffos/xkx100`，没有授权，仅供内部研究与公式比对，不做商业运营。
- `reports/` 下的历史报告可能包含已脱敏的测试数据；引用前先核对当前分支。

---

## 附录：数字核对表

核对时间：2026-11-07 15:20–16:01（Asia/Shanghai）。除特别注明外，数字均来自本机现场命令。

| 数字 | 值 | 核对方式 |
|---|---|---|
| `master` HEAD | `df2b37c` | `git rev-parse HEAD origin/master` |
| 地点数 | 9 | `grep -c '^  - id:' content/locations.yaml content/additions/locations.yaml`（8 + 1） |
| NPC 数 | 31 | `content/npcs.yaml` 30 + `content/additions/npcs.yaml` 1 |
| 2 日种子事件数 | 735 | `runs/quickstart-2d/summary.json` |
| `content_hash` | `5267a8a8…` | 同上 |
| `event_final` | `02d5f812…` | `runs/quickstart-2d/event_hash.txt` |
| `state_final` | `009076f0…` | `runs/quickstart-2d/world_hash.txt` |
| pytest | 687 / 669 passed / 18 skipped | `--junitxml`，`time=142.160` |
| selfcheck | 4/4 PASS | `scripts/selfcheck.sh` 输出 |
| Python | 3.12.7 | `.venv/bin/python -V` |
| 包版本 | 0.1.0 | `pyproject.toml` |
| 启动耗时 | API 0.70s / 前端 0.92s | 已有 venv + 种子，端口 18032/18031 |
| 本地端口 | 8020 / 8000 | `start.sh` 默认值 |
| Docker 端口 | 8020 / 8030 / 80 / 443 | `deploy/docker-compose.yml` |
| 管理台端口 | 8032 / 8039 | `ps -o command` 实进程 |
| 容量安全线 | 100 席 | `docs/CAPACITY.md` |
| 150 席写 P95 | 958ms | `docs/CAPACITY.md` |
| 写延迟尖峰 | `write.max 1277ms` | `hotspot-r2c-20261107/summary.json` |
