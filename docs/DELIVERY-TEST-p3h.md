# p3h 交付实测记录（2026-11-04）

- 代码基线：`451aed3`（证据/文档提交 `1c046fd`、`f22ad3d`、`205bfe4`、`2c096c3` 不改代码）
- 环境：macOS，Python 3.12.7
- 干净目录：`/tmp/xkx-p3h-clean/world-sim`（无原仓库 `.venv` / `runs`）
- 实测包：由 `2c096c3` 的 `git archive` 树构建；最终包在此基础上仅追加本文件，
  代码与数据完全一致。最终 SHA256 见交付回报。

## 1. 解包与首次启动

```bash
rm -rf /tmp/xkx-p3h-clean && mkdir -p /tmp/xkx-p3h-clean
tar -xzf /Users/lyinet/projects/XKX/world-sim/dist/xkx-world-sim-delivery-p3h.tar.gz \
  -C /tmp/xkx-p3h-clean
cd /tmp/xkx-p3h-clean/world-sim
API_PORT=8421 PORT=8411 RUN_ID=p3h-clean ./start.sh
```

实测输出（节选）：

```text
[start] 首次运行：创建 .venv ...
[start] 安装依赖 requirements.lock（首次可能需要几十秒）...
[start] Python: Python 3.12.7 (/tmp/xkx-p3h-clean/world-sim/.venv/bin/python)
[start] 生成 2 日种子世界（约 2 秒，只需一次）...
种子世界: quickstart-2d · 2 游戏日 · 633 条事件 · NPC 自主行动 541 条
[demo] 端口: API=8421 前端=8411 均空闲
[demo] 健康检查: API 200 (GET /api/player/state)
[demo] 健康检查: 前端 200 (GET /api/health)
[demo] 健康检查: 前端已连上 API (mode=live, upstream_ok=true)
[demo] 前端: http://127.0.0.1:8411/  (反向前端 -> http://127.0.0.1:8421)
```

```json
{"ok": true, "mode": "live", "readonly": false, "profile_version": 1,
 "upstream": "http://127.0.0.1:8421", "upstream_ok": true}
```

## 2. 玩家闭环

```bash
curl -sS -X POST http://127.0.0.1:8411/api/player/enter \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3h2","display_name":"初七","archetype":"捕快"}'
curl -sS 'http://127.0.0.1:8411/api/player/state?account_id=u_p3h2'
curl -sS -X POST http://127.0.0.1:8411/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3h2","verb":"move","args":{"to":"academy"}}'
curl -sS -X POST http://127.0.0.1:8411/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3h2","verb":"talk","args":{"target":"bai_lusheng","message":"你好，今日有什么新鲜事？"}}'
# 回集市接任务后读最终状态
curl -sS -X POST http://127.0.0.1:8411/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3h2","verb":"quest_accept","args":{"quest":"quest:errand_market_ledger:0"}}'
curl -sS 'http://127.0.0.1:8411/api/player/state?account_id=u_p3h2'
```

实测要点：

```text
enter  ok=true tick=0 next_tick=6 created=true wallet=300 inv={粗布:1, 炊饼:3}
state  ok=true tick=6 loc=集市 money=300 npcs=7
move   ok=true tick=6 next_tick=12 loc=academy
talk   ok=true tick=12 next_tick=18 reply_len=29
final  ok=true tick=30 loc=集市 accepted_by_me=1 active=[quest:errand_market_ledger:0]
```

## 3. 自检

```bash
./scripts/selfcheck.sh
PYTHONPATH=src .venv/bin/python -m pytest -q
```

实测：

```text
[PASS] 内容包校验 content/validate_content.py
[PASS] 单元测试 pytest（287 passed）
[PASS] 框架验收 tools/framework_check.py（6/6，FRAMEWORK CHECK: PASS）
[PASS] 玩家闭环（wsim.api + ui/player + HTTP 动作）
总结: selfcheck PASS（4 项全部通过）
```

> 首次实测曾发现 5 个对话回归测试硬编码 `runs/a2-dialogue-30/eval_raw.jsonl`，干净包内
> 因无 `runs/` 而失败（3 passed / 1 failed）。已在 `2c096c3` 修正为 `runs/` 优先、
> `reports/evidence/a2-dialogue-30/` 回退，包内 287 项全部通过。

## 4. 断线提示

```bash
curl -sS http://127.0.0.1:8411/api/health
# {"ok": true, "mode": "live", "upstream_ok": true, ...}
kill -TERM $(lsof -nP -iTCP:8421 -sTCP:LISTEN -t | head -1)
curl -sS http://127.0.0.1:8411/api/health
# {"ok": true, "mode": "degraded", "upstream_ok": false, ...}
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8411/
# 200
```

页面在 10 秒轮询内显示“上游不可达”；前端进程存活，不伪造世界状态。

## 5. 包内容核验

```bash
tar -tzf dist/xkx-world-sim-delivery-p3h.tar.gz | \
  grep -E 'world-sim/(\.venv|runs|\.git)/|\.env$|\.sqlite$'   # 无输出
tar -tzf dist/xkx-world-sim-delivery-p3h.tar.gz | \
  grep -E 'specs/|tests/test_p3e_golden.py|blind-eval-r4/|a3-model3-10d-31npc/(blocked|pruned|stale)\.jsonl'
```

结果：上述必含项全部命中；不含 `.venv`、`runs/`、`.git`、`.sqlite`、真实 key，
只有 `.env.example` 占位符。
