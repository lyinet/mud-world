# p3g 交付实测记录（2026-11-04）

> **内部快照说明**：基于 `60e8596`，HEAD 之后已推进至 `193fc7a`，本包仅作内部快照，不作对外最终版。

- 基线提交：`60e8596`（对外首发）
- 环境：macOS，Python 3.12.7
- 干净目录：`/tmp/xkx-p3g-clean/world-sim`（无原仓库 `.venv` / `runs`）
- 包 SHA256：`839d89a3…`（zip）/ `56972a7a…`（tar.gz）；本文件是最后加入的文档，未进入该内部快照包

## 1. 解包与首次启动

```bash
rm -rf /tmp/xkx-p3g-clean && mkdir -p /tmp/xkx-p3g-clean
cd /tmp/xkx-p3g-clean
unzip -q /Users/lyinet/projects/XKX/world-sim/dist/xkx-world-sim-delivery-p3g.zip
cd world-sim
API_PORT=8820 PORT=8810 RUN_ID=p3g-clean ./start.sh
```

实测输出（节选）：

```text
[start] 首次运行：创建 .venv ...
[start] 安装依赖 requirements.lock（首次可能需要几十秒）...
[start] Python: Python 3.12.7 (/tmp/xkx-p3g-clean/world-sim/.venv/bin/python)
[start] 生成 2 日种子世界（约 2 秒，只需一次）...
种子世界: quickstart-2d · 2 游戏日 · 633 条事件 · NPC 自主行动 541 条
[demo] 端口: API=8820 前端=8810 均空闲
[demo] 健康检查: API 200 (GET /api/player/state)
[demo] 健康检查: 前端 200 (GET /api/health)
[demo] 健康检查: 前端已连上 API (mode=live, upstream_ok=true)
[demo] 前端: http://127.0.0.1:8810/
```

健康检查：

```json
{"ok": true, "mode": "live", "readonly": false, "upstream_ok": true}
```

## 2. 玩家闭环

```bash
curl -sS -X POST http://127.0.0.1:8810/api/player/enter \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3g","display_name":"初七","archetype":"捕快"}'
curl -sS 'http://127.0.0.1:8810/api/player/state?account_id=u_p3g'
curl -sS -X POST http://127.0.0.1:8810/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3g","verb":"move","args":{"to":"academy"}}'
curl -sS -X POST http://127.0.0.1:8810/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3g","verb":"talk","args":{"target":"bai_lusheng","message":"你好，今日有什么新鲜事？"}}'
# 回集市接任务（略去中间 move）
curl -sS -X POST http://127.0.0.1:8810/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3g","verb":"quest_accept","args":{"quest":"quest:errand_market_ledger:0"}}'
```

实测：

```text
enter {"ok": true, "tick": 0, "next_tick": 6, "wallet": 300, "items": [粗布×1, 炊饼×3]}
state {"tick": 6, "loc": "market", "money": 300, "npcs": 7}
move  {"ok": true, "tick": 6, "next_tick": 12, "loc": "academy"}
talk  {"ok": true, "tick": 12, "reply_len": 154}
final {"tick": 30, "loc": "market", "accepted": 1,
       "active": ["quest:errand_market_ledger:0"]}
```

## 3. 自检 / golden / replay

```bash
cd /tmp/xkx-p3g-clean/world-sim
./scripts/selfcheck.sh
PYTHONPATH=src .venv/bin/python -m pytest tests/test_p3e_golden.py -q
PYTHONPATH=src .venv/bin/python tools/replay.py \
  --run reports/evidence/runs/p3e-deepseek-real-2d-12npc-k6r2
```

实测：

```text
[PASS] 内容包校验 content/validate_content.py
[PASS] 单元测试 pytest（194 passed in 11.08s）
[PASS] 框架验收 tools/framework_check.py（6/6，FRAMEWORK CHECK: PASS）
[PASS] 玩家闭环（wsim.api + ui/player + HTTP 动作）
总结: selfcheck PASS（4 项全部通过）
golden: PASS（824 events / 事件序哈希 / state hash / 经济流量）
replay: MATCH（222 events，state=e607d200…）
```

## 4. 断线提示

单独起 API（8920）+ 前端（8910），进入 live 后只杀 API：

```bash
curl -sS http://127.0.0.1:8910/api/health
{"ok": true, "mode": "live", "upstream_ok": true}
kill -TERM $(lsof -nP -iTCP:8920 -sTCP:LISTEN -t | head -1)
curl -sS http://127.0.0.1:8910/api/health
{"ok": true, "mode": "degraded", "readonly": false,
 "upstream": "http://127.0.0.1:8920", "upstream_ok": false}
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8910/
200
```

浏览器自动化实测：页面正文显示“上游不可达”（PASS）。本次未保存截图，截图接口在此前报
viewport 不可用（BLOCKED）；手工浏览器走查步骤见 QUICKSTART §3.2。

## 5. 包内容核验

```bash
tar -tzf dist/xkx-world-sim-delivery-p3g.tar.gz | grep -E 'world-sim/(\.venv|runs|\.git)/|\.env$'
# 无输出
tar -tzf dist/xkx-world-sim-delivery-p3g.tar.gz | grep -E \
  'tests/test_p3e_golden.py|k6r2/(blocked|pruned).jsonl|RELEASE-NOTES-p3g|known-limitations'
# 全部命中
```

- 不含 `.venv`、`runs/`、`.git`、`.env`、真实 key；只有 `.env.example`（占位符）；
- 含 `tests/test_p3e_golden.py`；
- 含 fake / deepseek 两套 `blocked.jsonl`、`pruned.jsonl`；
- 含 `docs/RELEASE-NOTES-p3g.md`、`docs/known-limitations.md`、本文件。

## 6. 结论

干净解包、首次自动准备、单命令启动、进入/移动/对话/接任务、断线提示、194 passed、framework 6/6、
golden、replay MATCH 全部通过。对外 K6 口径、世界漂移、K1/dialogue/回放边界按
[known-limitations.md](known-limitations.md) 如实披露。
