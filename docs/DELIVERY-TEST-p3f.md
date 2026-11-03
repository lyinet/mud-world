# p3f 交付实测记录（2026-11-04）

环境：macOS，Python 3.12.7；仓库 `f100693` 之上的交付改动；干净临时目录
`/tmp/xkx-delivery-clean/world-sim`，**不使用**原仓库的 `.venv` / `runs`。

## 1. 解包

```bash
rm -rf /tmp/xkx-delivery-clean && mkdir -p /tmp/xkx-delivery-clean
cd /tmp/xkx-delivery-clean
unzip -q /Users/lyinet/projects/XKX/world-sim/dist/xkx-world-sim-delivery-p3f.zip
ls -l world-sim/start.sh world-sim/README.md
```

实测：

```text
-rw-r--r--@ 1 lyinet  wheel   2591 world-sim/README.md
-rwxr-xr-x@ 1 lyinet  wheel   3033 world-sim/start.sh
```

## 2. 一条命令启动（首次，自动建 venv + 装依赖 + 造种子世界）

```bash
cd /tmp/xkx-delivery-clean/world-sim
API_PORT=8620 PORT=8610 RUN_ID=clean-test ./start.sh
```

实测输出（节选）：

```text
[start] 首次运行：创建 .venv ...
[start] 安装依赖 requirements.lock（首次可能需要几十秒）...
[start] Python: Python 3.12.7 (/tmp/xkx-delivery-clean/world-sim/.venv/bin/python)
[start] 生成 2 日种子世界（约 2 秒，只需一次）...
种子世界: quickstart-2d · 2 游戏日 · 633 条事件 · NPC 自主行动 541 条
[start] 启动 API + 玩家前端 ...
[demo] 健康检查: API 200 (GET /api/player/state)
[demo] 健康检查: 前端 200 (GET /api/health)
[demo] 健康检查: 前端已连上 API (mode=live, upstream_ok=true)
[demo] 前端: http://127.0.0.1:8610/  (反向前端 -> http://127.0.0.1:8620)
```

```bash
curl -sS http://127.0.0.1:8610/api/health
```

```json
{"ok": true, "mode": "live", "readonly": false, "profile_version": 1,
 "upstream": "http://127.0.0.1:8620", "upstream_ok": true}
```

## 3. 玩家闭环（前端代理）

```bash
# 进入
curl -sS -X POST http://127.0.0.1:8610/api/player/enter \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_clean","display_name":"初七","archetype":"捕快"}'
# 状态
curl -sS 'http://127.0.0.1:8610/api/player/state?account_id=u_clean'
# 移动
curl -sS -X POST http://127.0.0.1:8610/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_clean","verb":"move","args":{"to":"academy"}}'
# 对话
curl -sS -X POST http://127.0.0.1:8610/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_clean","verb":"talk","args":{"target":"bai_lusheng","message":"你好，今日有什么新鲜事？"}}'
# 回集市接任务
curl -sS -X POST http://127.0.0.1:8610/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_clean","verb":"quest_accept","args":{"quest":"quest:errand_market_ledger:0"}}'
```

实测结果：

```text
enter: ok=true tick=0 next_tick=6 created=true wallet=300 items=[粗布×1, 炊饼×3]
state: tick=6 loc=market money=300 npcs=[段五斗, 金满仓, 柳三娘, 罗轻絮, 孟九娘, 阮青蚨, 邢破军]
move:  ok=true tick=6 next_tick=12 loc=academy
talk:  ok=true tick=12 reply_len=154 reply_head=白露生（学究）听了你的话，先引经据典...
quest: ok=true tick=24 next_tick=30
final: tick=30 loc=market accepted_by_me=1 active=[quest:errand_market_ledger:0]
```

## 4. 断线提示

单独起 API（8720）+ 前端（8710），进入 live 后只杀 API：

```bash
curl -sS http://127.0.0.1:8710/api/health   # kill 前
{"ok": true, "mode": "live", "upstream_ok": true}
kill -TERM $(lsof -nP -iTCP:8720 -sTCP:LISTEN -t | head -1)
curl -sS http://127.0.0.1:8710/api/health   # kill 后
{"ok": true, "mode": "degraded", "readonly": false,
 "upstream": "http://127.0.0.1:8720", "upstream_ok": false}
```

前端进程仍存活（`GET /` 返回 200），页面提示条包含“上游不可达”。
浏览器自动化确认了该文本；**截图未保存**：本次 browser_use 截图接口报 viewport 不可用
（BLOCKED），因此不给截图路径，改用手工走查步骤（QUICKSTART §3.2）。

## 5. 自动自检（干净解包内）

```bash
cd /tmp/xkx-delivery-clean/world-sim && ./scripts/selfcheck.sh
```

实测：

```text
[PASS] 内容包校验 content/validate_content.py
[PASS] 单元测试 pytest（193 passed in 8.71s）
[PASS] 框架验收 tools/framework_check.py（6/6，FRAMEWORK CHECK: PASS）
[PASS] 玩家闭环（wsim.api + ui/player + HTTP 动作）
总结: selfcheck PASS（4 项全部通过）
```

## 6. 结论

干净解包、首次自动准备、单命令启动、进入/移动/对话/接任务、断线提示、4 项自检均通过。
未通过/未验证项严格按 [known-limitations.md](known-limitations.md) 披露，不用“优化”措辞。
