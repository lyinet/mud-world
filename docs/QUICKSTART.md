# QUICKSTART — 一条命令跑起来

`world-sim` 是一个有真实玩家入口的 MUD 世界：引擎 `wsim.api`（默认 `8020`）+ 玩家前端
`ui/player`（默认 `8000`）。把包解开后，在 `world-sim/` 目录执行一条命令即可：

```bash
./start.sh
```

脚本会自动完成首次准备：

1. 检查 Python ≥ 3.10；
2. 没有 `.venv` 就创建，并按 `requirements.lock` 安装固定版本依赖；
3. 生成 2 日种子世界 `runs/quickstart-2d`（约 2 秒，只生成一次）；
4. 启动 `wsim.api` 与玩家前端，等两个健康检查都通过；
5. 打印前端地址；Ctrl-C 同时停止两个服务。

看到下面这段就说明起来了（端口、run 名可能不同）：

```text
[start] Python: Python 3.12.7 (.../.venv/bin/python)
种子世界: quickstart-2d · 2 游戏日 · 633 条事件 · NPC 自主行动 541 条
最近 8 条 NPC 自主行动（只读回放，不是你的实时会话）:
  [第2日 22:00] 严守一：去了集市
[demo] 端口: API=8020 前端=8000 均空闲
[demo] 健康检查: API 200 (GET /api/player/state)
[demo] 健康检查: 前端 200 (GET /api/health)
[demo] 健康检查: 前端已连上 API (mode=live, upstream_ok=true)
[demo] API:  http://127.0.0.1:8020  (run=runs/player-demo)
[demo] 前端: http://127.0.0.1:8000/
```

浏览器打开 <http://127.0.0.1:8000/>，填账号/角色名/身份，点“进入世界”。
若 8020 或 8000 被占用，换端口：

```bash
API_PORT=8120 PORT=8010 ./start.sh
```

## 1. 环境要求

- macOS（本次验证使用 macOS + Python 3.12.7），Linux 也可用；
- Python **3.10+**；
- 首次安装依赖需要网络。依赖只有 `PyYAML` / `pytest` 及其传递依赖，版本已固定：
  [requirements.txt](../requirements.txt) / [requirements.lock](../requirements.lock)。

不想自动装依赖时，也可以手动：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
./scripts/demo.sh     # 已准备好 .venv 时的等价启动命令
```

## 2. 三个可验证观察点

以下命令都打前端代理 `8000`，等价于直打引擎 `8020`。每个写动作推进 6 tick（1 小时）。

### ① 看到世界在跑：tick 在增长，地点/NPC 在线

```bash
curl -sS -X POST http://127.0.0.1:8000/api/player/enter \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","display_name":"初七","archetype":"捕快"}'
```

预期（节选，实测）：

```json
{"ok": true, "status": "executed", "tick": 0, "next_tick": 6, "event_seq": 6,
 "details": {"created": true,
             "endowment": {"granted": true, "money": 300,
                           "items": [{"name": "粗布（一匹）", "qty": 1},
                                     {"name": "炊饼", "qty": 3}]}},
 "data": {"location": "market", "wallet": 300}}
```

再读状态：

```bash
curl -sS 'http://127.0.0.1:8000/api/player/state?account_id=u_walk'
```

预期（节选）：

```json
{"ok": true, "tick": 6,
 "player": {"location": "market", "location_name": "集市",
            "wallet": {"money": 300}},
 "present_npcs": [{"id": "duan_wudou", "name": "段五斗"}, {"id": "jin_mancang", "name": "金满仓"}, "..."],
 "location": {"exits": [{"id": "academy", "name": "书院", "exists": true}, "..."]}}
```

判定：进入后 `tick` 从 0 涨到 6，之后每个动作再 +6；`present_npcs` 非空；`wallet.money=300`。
浏览器里看顶部时钟（第 1 日 01:00 → 02:00）和左侧“在线”。

### ② 看到 NPC 自主行动：2 日种子世界的行动流

实时会话是按玩家动作推进的；NPC 连续自主行动由随包生成的 2 日种子世界展示。
在仓库根目录执行：

```bash
.venv/bin/python scripts/world_feed.py --run runs/quickstart-2d --lines 12
```

实测输出（seed=42）：

```text
种子世界: quickstart-2d · 2 游戏日 · 633 条事件 · NPC 自主行动 541 条
最近 12 条 NPC 自主行动（只读回放，不是你的实时会话）:
  [第2日 20:20] 罗轻絮：还债
  [第2日 21:30] 严守一：在城隍庙干活
  [第2日 21:40] 严守一：去了客栈
  [第2日 21:50] 严守一：消耗了阳春面 ×1
  [第2日 22:00] 严守一：去了集市
  [第2日 22:00] 阿茗：在茶棚干活
  [第2日 22:10] 严守一：买入 粗布（一匹） ×1
  [第2日 22:10] 阿茗：消耗了粗茶（一碗） ×1
  [第2日 22:20] 阿茗：去了集市
  [第2日 22:30] 阿茗：卖出 粗茶（一碗） ×1
  [第2日 23:40] 谢停云：在书院干活
  [第2日 23:50] 谢停云：去了集市
```

判定：能读到 NPC 的移动/干活/买卖/消耗事件，且事件数、NPC 行动数均非 0。
浏览器里对应的是在场 NPC 列表与地点变化；NPC 自主行动的历史在种子 run 里，不在实时会话里同步播放
（见 [known-limitations.md](known-limitations.md)）。

### ③ 看到自己的动作生效：移动、对话、接任务

移动到书院（`to` 取 `location.exits` 里 `exists:true` 的 id）：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","verb":"move","args":{"to":"academy"}}'
```

实测：

```json
{"ok": true, "status": "executed", "tick": 6, "next_tick": 12, "event_seq": 9,
 "data": {"location": "academy"}}
```

和在场 NPC 说话（`target` 用状态里的 `present_npcs[0].id`，书院实测 `bai_lusheng`）：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","verb":"talk","args":{"target":"bai_lusheng","message":"你好，今日有什么新鲜事？"}}'
```

实测 `details.reply` 非空（一段中文），并能在 `/messages` 取到同一条：

```bash
curl -sS 'http://127.0.0.1:8000/api/player/messages?account_id=u_walk&since_tick=11'
```

接任务前先回集市，读 `quest_board.rows` 里 `acceptable:true` 的 `id`：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","verb":"move","args":{"to":"market"}}'

curl -sS 'http://127.0.0.1:8000/api/player/state?account_id=u_walk'
```

实测可接任务包含 `quest:errand_market_ledger:0`，接取：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","verb":"quest_accept","args":{"quest":"quest:errand_market_ledger:0"}}'
```

预期状态：`quest_board.counts.accepted_by_me` 从 0 变 1，`quest_board.active` 出现该 id，
`tick` 继续增长。刷新浏览器页面，位置、钱包、已接任务都还在。

判定三件事同时成立：地点变了（`market → academy`）、`tick` 变大、接任务后 `accepted_by_me` 真的增加。

## 3. 可玩入口自检

### 3.1 自动自检

```bash
./scripts/selfcheck.sh
```

四项串行：内容包校验、`pytest`、框架验收、玩家闭环（临时起 API + 前端，跑进入/移动/对话/接任务）。
本机实测（2026-11-04，`delivery-p3i`）：

```text
301 passed, 4 skipped
[PASS] 内容包校验 content/validate_content.py
[PASS] 单元测试 pytest
[PASS] 框架验收 tools/framework_check.py（临时副本，不写 runs/）
[PASS] 玩家闭环（wsim.api + ui/player + HTTP 动作）
总结: selfcheck PASS（4 项全部通过）
```

### 3.2 浏览器手工走查（无截图能力时照此执行）

1. 打开 <http://127.0.0.1:8000/>，顶栏应显示 `可写 · 真 API · http://127.0.0.1:8020`；
2. 输入账号 `u_walk`、角色名 `初七`、选“捕快”，点“进入世界”，右侧出现“300 文 / 炊饼×3 / 粗布×1”；
3. 点“书院”出口，左侧地点变“书院”，顶部时钟前进；
4. 点在场 NPC“白露生”，输入一句话发送，出现“世界回应”；
5. 回到集市，任务板出现可接任务，接一张后计数 `我进行中` 从 0 变 1；
6. 刷新页面，位置/钱包/任务仍保留。

### 3.3 断线提示

在浏览器已进入世界后，把 `wsim.api` 停掉（只停引擎进程，不要停前端）。10 秒内：

- 顶栏变为 `可写 · 真 API · http://127.0.0.1:8020（不可达）`；
- 页面提示条出现“上游不可达 · 真 API 已断开”；
- 先弹一次“真 API 已断开”的 toast。

无浏览器时可直接验证前端健康检查（找一个已停掉的端口，如 `8999`）：

```bash
python3 ui/player/server.py --upstream http://127.0.0.1:8999 --port 8011 &
curl -sS http://127.0.0.1:8011/api/health
```

预期：

```json
{"ok": true, "mode": "degraded", "readonly": false,
 "upstream": "http://127.0.0.1:8999", "upstream_ok": false}
```

> 启动时就不可达会直接进入“只读模式”；运行中掉线则显示“上游不可达”。两者都不会伪造世界状态。

## 4. 常见错误

### 端口被占用

```text
[demo] 错误: 端口 8020 已被占用。换端口: API_PORT=8120 PORT=8010 scripts/demo.sh；查看占用: lsof -nP -iTCP:8020 -sTCP:LISTEN
```

```bash
lsof -nP -iTCP:8020 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
API_PORT=8120 PORT=8010 ./start.sh
```

### 依赖安装失败

脚本会打印 pip 输出末尾 25 行，不会只给栈回溯。常见原因是网络/代理；重试：

```bash
.venv/bin/pip install -r requirements.lock
```

### 看到旧存档 / 世界被追加

`wsim.api` 每次启动都是新创世，不 replay `events.sqlite`。`demo.sh` 检测到
`runs/player-demo/events.sqlite` 已存在会自动换名为 `player-demo-<时间戳>`：

```text
[demo] 检测到 runs/player-demo/events.sqlite；不静默追加，改用 player-demo-20261104-021533-30217（旧数据未动）
```

要固定某个存档，显式 `RUN_ID=<runs 里的目录名> ./start.sh`。

### 模型相关的 401 / 429

播放默认完全离线，不需要任何 key。只有显式 `--enable-llm` 才调用真实模型；
key 只从环境变量读取（见 [.env.example](../.env.example)）。401 检查 key/`LLM_BASE_URL`，
429 是限流，降低并发或等额度恢复。

## 5. 已知限制

首发不美化任何未达标项，完整清单与证据见 [known-limitations.md](known-limitations.md)：

- K1 三次尝试均未超过规则臂；冻结快照 `fac4ac31…` 下规则臂与模型臂**同为 56.04%（51/91）**，
  但 M 臂第 7 天触发 $3.0 hard 中止，整轮判 **FAIL**；`49.45%→56.04%` 归因内容而非模型；
- K3 行为多样性为 **diagnostic_only**，不设 pass/fail，也不能当作目标完成率证据；
- 对话默认走**模板**（零成本）；模型仅 spotlight NPC / 玩家主动深聊；R5 盲评结论为
  “模板已接近模型水平”（非平手 55.9%，CI 44.1%–67.1%）；
- 物价只按日与库存变化，不按小时/事件驱动；单日最多 ±20%；价格会漂移，
  任何事件数/金额对比都必须标注当前快照 `content_hash`；
- 只保证**事件日志回放**（`MATCH`，回放成本 0），不保证整跑复现；
- K6 冻结快照口径 **0.0782%/0.0538%（严格）、1.6940%/1.9370%（含剪枝）**，均 <2% PASS。

## 6. 包里不含什么

- 不含任何真实模型 key；`.env.example` 只有占位符；
- 不含 72 天全量 run；`runs/` 不入包，种子世界在首次运行时自动生成；
- 不需要 GPU、数据库或向量库。
