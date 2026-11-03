# QUICKSTART — 十分钟跑起来

`world-sim` 是一个无头 AI NPC 世界模拟器。首发形态是**一条命令起两个服务**：

- `wsim.api`（默认 `8020`）：玩家真写 API，负责进入世界、移动、对话、接任务
- `ui/player/server.py`（默认 `8000`）：玩家前端，反向代理到 `8020`

## 0. 最短路径

```bash
cd world-sim
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/demo.sh
```

浏览器打开脚本打印的地址（默认 <http://127.0.0.1:8000/>）。
按 Ctrl-C 会把两个服务一起收掉并释放 8020 / 8000。

## 1. 环境要求

- Python **3.10+**（本次验证使用 3.12.7）
- macOS 自带 bash 3.2 即可，脚本不使用 bash 4+ 语法
- 依赖只有 `PyYAML` 和 `pytest`（见 `requirements.txt`）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

如果不想用 `.venv`，`scripts/demo.sh` 会自动回退到系统 `python3`，但需要自行安装依赖。

## 2. 启动两个服务

```bash
./scripts/demo.sh
```

脚本会依次：

1. 检查 Python 版本、`PyYAML`、`src/wsim/api/__main__.py`、`ui/player/server.py`，以及 8020 / 8000 是否空闲；
2. 处理 `runs/player-demo` 的 genesis 冲突（见下节）；
3. 启动 `PYTHONPATH=src python3 -m wsim.api --run player-demo --port 8020`，等 `GET /api/player/state?account_id=u_demo` 返回 `ok:true`；
4. 启动 `ui/player/server.py --upstream http://127.0.0.1:8020 --port 8000`，等 `GET /api/health` 返回 `200`；
5. 再确认 `/api/health` 的 `mode=live` 且 `upstream_ok=true`——前端没有偷偷降级成只读；
6. 打印两个地址；任一健康检查失败，会打印该服务日志最后 30 行并停掉另一个，不留半启动状态；
7. Ctrl-C 同时停止两个进程，并逐端口确认已释放。

自定义端口和 run：

```bash
API_PORT=8120 PORT=8010 ./scripts/demo.sh
RUN_ID=my-demo ./scripts/demo.sh
```

手动分别启动（等价于 demo 做的事）：

```bash
PYTHONPATH=src PYTHONUNBUFFERED=1 python3 -m wsim.api --root . --run player-demo --port 8020
PLAYER_API=http://127.0.0.1:8020 python3 ui/player/server.py \
  --upstream http://127.0.0.1:8020 --port 8000
```

### `runs/player-demo` 的 genesis 陷阱

`wsim.api` 每次启动都会对 run 目录做创世（genesis）。如果 `runs/player-demo/events.sqlite`
已经存在，再启动就是往旧世界继续追加，玩家会看到上次的存档，而不是一次干净的开局。

`demo.sh` 的处理是**自动换名**：检测到 `runs/player-demo/events.sqlite` 时，改用
`runs/player-demo-<时间戳>-<pid>`，旧数据原样保留，日志会打印：

```text
[demo] 检测到 runs/player-demo/events.sqlite；不静默追加，改用 player-demo-20261104-021533-30217（旧数据未动）
[demo] API run: runs/player-demo-20261104-021533-30217
```

想要固定某个已有存档，显式传 `RUN_ID`：

```bash
RUN_ID=player-demo-20261104-021533-30217 ./scripts/demo.sh
```

注意这个 `RUN_ID` 必须对应一个**已存在或可新建**的 run 目录；demo 不会清空任何 `runs/` 数据。

## 3. 十分钟走查

以下命令都打 `8020`。前端按钮走的是同一套接口，经过 `8000` 反代到 `8020`。
每个动作都会推进 6 tick，所以 `tick` 会一路增长。

### ① 进世界拿 300 文

```bash
curl -sS -X POST http://127.0.0.1:8020/api/player/enter \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","display_name":"陈捕快","archetype":"捕快"}'
```

预期：

```json
{"ok": true, "details": {"created": true, "endowment": {"money": 300, "items": {"粗布": 1, "炊饼": 3}}}}
```

重复进入同一账号会返回 `"ok": false, "reject_reason": "already_online"`，这属于正常。

### ② 看地点

```bash
curl -sS 'http://127.0.0.1:8020/api/player/state?account_id=u_walk'
```

预期（字段节选）：

```json
{"ok": true, "tick": 6,
 "player": {"location": "market", "location_name": "集市", "wallet": {"money": 300}},
 "present_npcs": [{"id": "bai_lusheng", "name": "白露生"}],
 "location": {"exits": [{"id": "academy", "name": "书院", "exists": true}]}}
```

### ③ 走到相邻地点

把 `to` 换成 `location.exits` 里 `exists:true` 的任意一项：

```bash
curl -sS -X POST http://127.0.0.1:8020/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","verb":"move","args":{"to":"academy"}}'
```

预期：

```json
{"ok": true, "details": {"from": "market", "to": "academy"}, "next_tick": 12}
```

再读一次状态，`player.location` 应该已经变成 `academy`。
不相邻的地点会返回 `"reject_reason": "not_adjacent"`。

### ④ 和在场 NPC 说话并看到回复

`target` 用上一步状态里 `present_npcs[0].id`；如果到达的地点没人，先走回 `market`：

```bash
curl -sS -X POST http://127.0.0.1:8020/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","verb":"talk","args":{"target":"bai_lusheng","message":"你好，今日集市可有什么新鲜事？"}}'
```

预期：

```json
{"ok": true, "details": {"target": "bai_lusheng", "reply": "……（一段非空中文回复）"}, "next_tick": 18}
```

对方不在同一地点返回 `"reject_reason": "not_together"`。

### ⑤ 接一张集市任务

先回到集市，再看 `quest_board.rows`，挑 `acceptable:true` 的一行：

```bash
curl -sS -X POST http://127.0.0.1:8020/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_walk","verb":"quest_accept","args":{"quest":"errand_market_ledger:0"}}'
```

预期：

```json
{"ok": true, "details": {"quest": {"id": "errand_market_ledger:0", "name": "捎账本到客栈", "reward_money": 10}}, "next_tick": 24}
```

任务 `id` 以实际 `quest_board` 为准；不在发布人所在地会返回 `"reject_reason": "not_at_giver"`，
已接满两个会返回 `"too_many_quests"`。

### ⑥ 刷新页面确认状态还在

刷新 <http://127.0.0.1:8000/>，或再取一次状态：

```bash
curl -sS 'http://127.0.0.1:8020/api/player/state?account_id=u_walk'
```

预期：

```json
{"ok": true, "tick": 24,
 "player": {"location": "market", "wallet": {"money": 300}},
 "quest_board": {"active": [{"id": "errand_market_ledger:0", "status": "accepted"}]}}
```

关键断言：位置还是 `market`、钱包还是 300、`active` 里能看到刚接的任务、`tick` 比上一步更大。
这些能同时成立，说明写入真的落盘了，而不是前端缓存。

## 4. 配置模型（可选）

模拟默认完全离线，不需要任何 key。要用真实模型，必须显式加 `--enable-llm`。

```bash
cp .env.example .env
# 编辑 .env，至少填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
set -a; . ./.env; set +a
.venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0 --enable-llm
```

环境变量含义：

| 变量 | 作用 |
|---|---|
| `LLM_BASE_URL` | OpenAI 兼容中转站地址，末尾可带 `/v1` |
| `LLM_API_KEY` | key，只放环境变量，不要提交 |
| `LLM_MODEL` | 两个槽位未单独指定时的兜底模型名 |
| `LLM_DECISION_MODEL` | 决策槽：目标选择、replan、夜间批处理 |
| `LLM_DIALOGUE_MODEL` | 对话槽：玩家在场对话、聚光档 NPC 发言 |

`config/llm.yaml` 里还有意图槽（`xing_intent`），对应 `XING_API_KEY` / `XING_INTENT_MODEL`；
如果只想用普通 OpenAI 兼容模型，把 `models.intent.type` 改成 `openai_chat`。

> 免费档延迟约 10 秒，建议只用于夜间批处理；实时对话体验会明显变慢。

## 5. 自检

```bash
./scripts/selfcheck.sh
```

四项串行检查：

1. `content/validate_content.py` 内容包校验
2. `pytest` 单元测试
3. `tools/framework_check.py` 六项框架验收
4. 玩家闭环：临时起 `wsim.api` + `ui/player`，跑 `scripts/player_smoke.py`，
   依次进入世界 / 移动 / 说话 / 接任务，并断言状态真的变化

`framework_check.py` 在临时副本里运行，玩家闭环用临时 run 目录，都不会写仓库的 `runs/`。
每项输出 `[PASS]` / `[FAIL]` 和末尾关键信息，最后给一行总结。
玩家闭环失败时会打印 `wsim.api` 和前端日志的最后 30 行。

**当前实测的 FAIL（内容包正在迭代，不代表启动脚本有问题）**：

- `validate_content.py`：persona 卡结构 / token 区间检查，或动词数量检查。
  校验脚本与 `content/` 的口径可能暂时不同步（例如内容包已扩到 19 个动词，
  脚本按 18 个校验；30 张 persona 卡缺标题或 token 不足）。这是内容仓库的编辑状态，
  不是 demo/selfcheck 脚本的故障。
- `framework_check.py` 第 1 项（停摆门禁）：拒绝率上限 2%。
  同一命令在本机不同时点实测过 1.55%（PASS）与 2.43%（FAIL），差异来自内容包改动；
  内容稳定后复跑即可。

这两项都允许 FAIL；`pytest`、其余 5/6 框架检查与玩家闭环仍应通过。

## 6. 常见错误排查

### 端口被占用

```text
[demo] 错误: 端口 8020 已被占用。换端口: API_PORT=8120 PORT=8010 scripts/demo.sh；查看占用: lsof -nP -iTCP:8020 -sTCP:LISTEN
```

两个端口都要查：

```bash
lsof -nP -iTCP:8020 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
API_PORT=8120 PORT=8010 ./scripts/demo.sh
```

### 前端显示只读 / 连不上 API

```text
[ui] [player-ui] 上游不可达：http://127.0.0.1:8020，已自动降级为只读模式。
```

说明 `8000` 的 `--upstream` 指错了，或 `8020` 没起来。手动启动时保持一致：

```bash
python3 ui/player/server.py --upstream http://127.0.0.1:8020 --port 8000
```

`demo.sh` 会在 `/api/health` 里检查 `mode=live` 与 `upstream_ok=true`，
不满足就直接失败并打印前端日志，不会让你以为在写世界。

### 启动后看到旧存档

`runs/player-demo/events.sqlite` 已存在，demo 已自动换名。日志会写：

```text
[demo] 检测到 runs/player-demo/events.sqlite；不静默追加，改用 player-demo-<时间戳>-<pid>（旧数据未动）
```

如要回到指定存档，用 `RUN_ID=runs 里的目录名 ./scripts/demo.sh`。

### 生成 10 天只读 run

`runs/` 默认被 `.gitignore` 忽略，clone 后可能是空的。只在 `ui/player` 还没有反代能力时
demo 才会走只读兜底，并自动生成 `runs/quickstart-10d`。手动生成：

```bash
.venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id quickstart-10d
```

可用 run 目录必须包含：

```text
summary.json
events.jsonl
lod.json
events.sqlite   # 可选；没有时界面只能只读浏览，对话无法持久化
```

不要跑 72 天全量模拟做首次体验，耗时明显更长。

### 模型返回 401

- key 没有导出到当前 shell：`echo ${LLM_API_KEY:+key 已设置}`
- `LLM_BASE_URL` 少了 `/v1` 或指向了错误网关
- key 已过期/额度用尽。先用中转站的 `/models` 接口确认 key 可用。

### 模型返回 429

- 免费档限流：降低并发，或只把模型用于夜间批处理
- `config/llm.yaml` 的 `provider.max_retries` / `backoff_base_s` 会重试 timeout、网络错误和 5xx；
  4xx 不会重试，429 需要等额度恢复或换 key
- 预算护栏也会触发降级：决策槽回退规则调度，对话槽返回兜底话术，并写 `system_budget` 审计事件

### 界面显示 hash_match=False

run 是在内容包变更前生成的，事件回放后的世界哈希与 `world_hash.txt` 不一致。
不影响浏览和对话，但若要看自洽数据，用当前内容重跑一次 10 天：

```bash
.venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id fresh-10d
RUN_ID=fresh-10d ./scripts/demo.sh
```

### 中文乱码

```bash
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
export PYTHONIOENCODING=utf-8
```

界面和日志都按 UTF-8 输出；如果仍乱码，检查终端字体和 locale。

## 7. 这个包不包含什么

- 不包含真实模型服务或 API key，`.env.example` 只有占位符
- 不包含 72 天全量 run；`runs/` 默认被 `.gitignore` 忽略，clone 后由 demo 按需生成
- 不包含依赖之外的 Python 包；GPU、数据库、向量库都不是必需项
- `reports/` 里的历史验收记录会随仓库一起 clone，但只是参考材料，不是启动必需
