# 世界审计器 · 最小可玩界面（ui/）

只用 Python 标准库（`http.server` + `ThreadingHTTPServer`），不联网、不引 CDN、不装第三方包。
前端是单文件 `ui/index.html`（内联 CSS/JS，中文界面）。

## 启动

在 `world-sim/` 目录下：

```bash
python3 ui/server.py --run runs/p2b-72d --port 8000
```

然后打开 <http://127.0.0.1:8000/>。

- `--run` 默认为 `runs/p2b-72d`，可指向任意同结构的 run 目录（相对路径按 `world-sim/` 解析）。
- 如果 run 目录不存在 / 缺 `summary.json`、`events.jsonl`、`lod.json` / JSON 解析失败，服务仍会启动，页面显示友好错误卡片，不会白屏。
- 停止：`Ctrl+C`。

## 边界（硬约束）

- **读**：世界状态只通过 `src/wsim/mcp/gateway.py` 的 `world.read_snapshot` 获取。
- **写**：对话只通过 `world.propose_intent` 进入 `gateway.outbox`，不绑定 Writer，`state_unchanged=true`。
- **不改**：不修改 `src/`、`content/`、`tests/`；不写 run 目录；不直接打开 `events.sqlite`。
- `events.jsonl` / `summary.json` / `lod.json` / `decisions.jsonl` 只做只读解析，用于离线视图和时间轴。
- 世界状态由 `events.jsonl` 的 Effect 回放重建（只读投影），启动时会与 `world_hash.txt` 比对，页面显示是否一致。

## 四项功能与数据来源

| 功能 | 数据来源 | 说明 |
|---|---|---|
| 1. 世界总览 | `summary.json`（run/seed/days/hashes）+ `lod.json`（档位）+ `events.jsonl` 回放后的 `state`（经 gateway 只读快照）+ 地点 YAML 只读解析 | 虚拟时间、9 个地点、31 个 NPC 的姓名/职业/所在地/当前目标/LOD 档位 |
| 2. NPC 详情 | gateway 快照（实体/目标/关系/债务/账户）+ `lod.json`（档位、唤醒次数）+ `events.jsonl`（最近 10 条、当天日程）+ `decisions.jsonl`（最近计划）+ `content/personas/<id>.md`（台词） | 目标进度、关系含债务、最近 10 条事件、当天日程、LOD、人设卡 |
| 3. 对话 | `MockReasoner`（`src/wsim/sim/intent.py` 的规则桩）生成回应 + `MCPGateway.world.propose_intent` 提交提案 + gateway 审计日志 | 页面显示“这是提案”，以及已提交/被拒原因、intent_id、审计条目；响应在本地完成，实测远低于 1 秒 |
| 4. 事件时间轴 | `events.jsonl` 只读解析（按 `day` 分组，可按 NPC / 地点筛选） | 显示事件类型、时间、地点、金额变化（钱包口径）、ok/rejected |

## 接口

- `GET /api/health`：run 是否可用。
- `GET /api/overview`：世界总览。
- `GET /api/npc/<id>`：NPC 详情。
- `GET /api/timeline?day=72&npc=han_xinglin&location=pharmacy&limit=500`：事件时间轴。
- `POST /api/dialogue` body `{"npc":"han_xinglin","message":"你今天忙什么？"}`：MockReasoner 回应 + propose_intent 提案 + 审计条目。

## 哪些是桩

- **对话模型是桩**：`MockReasoner` 是确定性规则桩，不是真实 LLM。本版无网络、无模型客户端。
- **`talk` 动作是桩**：内容包里没有 `talk` 执行器，提案只进入 outbox，不会被 Writer 执行，也不会改变世界。
- **MCP 是本地 mock**：直接构造 `MCPGateway`，没有接真实 MCP transport；`admin_apply` 不在本 UI 使用。
- **离线快照**：界面读的是冻结提交 `42f4914` 附近生成的 `runs/p2b-72d`，不是实时世界；“当天日程”是第 72 天事件回放，不是未来计划表。
- 地点解析用极简只读 YAML 解析（只取 id/name/exits/services），不引入 PyYAML。

## curl 验证示例

```bash
curl -s http://127.0.0.1:8000/api/overview | python3 -m json.tool | head -40
curl -s http://127.0.0.1:8000/api/npc/han_xinglin | python3 -m json.tool | head -60
curl -s -X POST http://127.0.0.1:8000/api/dialogue \
  -H 'Content-Type: application/json' \
  -d '{"npc":"han_xinglin","message":"你今天忙什么？"}' | python3 -m json.tool
curl -s 'http://127.0.0.1:8000/api/timeline?day=72&npc=han_xinglin' | python3 -m json.tool | head -60
```
