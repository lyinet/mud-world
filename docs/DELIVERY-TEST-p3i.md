# p3i 交付实测记录（2026-11-04）

- 代码基线：`64365f8`；文档提交 `79caed3`
- 内容冻结：`content_hash = fac4ac31fec9db5720d829d79827b215f5d0aee217be5fcb57bfdf38165ef8dd`
- 环境：macOS，Python 3.12.7
- 干净目录：`/tmp/xkx-p3i-clean/world-sim`（无原仓库 `.venv` / `runs`）
- 实测包由 `79caed3` 的 `git archive` 树构建；最终包仅追加本文件，代码与数据一致。
  SHA256 见交付回报。

## 1. 解包与首次启动

```bash
rm -rf /tmp/xkx-p3i-clean && mkdir -p /tmp/xkx-p3i-clean
tar -xzf /Users/lyinet/projects/XKX/world-sim/dist/xkx-world-sim-delivery-p3i.tar.gz \
  -C /tmp/xkx-p3i-clean
cd /tmp/xkx-p3i-clean/world-sim
API_PORT=8422 PORT=8412 RUN_ID=p3i-clean ./start.sh
```

实测：

```text
[start] 首次运行：创建 .venv ...
[start] 安装依赖 requirements.lock（首次可能需要几十秒）...
[start] Python: Python 3.12.7 (.../world-sim/.venv/bin/python)
[start] 生成 2 日种子世界（约 2 秒，只需一次）...
种子世界: quickstart-2d · 2 游戏日 · 838 条事件 · NPC 自主行动 742 条
[demo] 健康检查: API 200 (GET /api/player/state)
[demo] 健康检查: 前端 200 (GET /api/health)
[demo] 健康检查: 前端已连上 API (mode=live, upstream_ok=true)
[demo] 前端: http://127.0.0.1:8412/  (反向前端 -> http://127.0.0.1:8422)
```

```json
{"ok": true, "mode": "live", "readonly": false, "profile_version": 1,
 "upstream": "http://127.0.0.1:8422", "upstream_ok": true}
```

## 2. 玩家闭环

```bash
curl -sS -X POST http://127.0.0.1:8412/api/player/enter \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3i","display_name":"初七","archetype":"捕快"}'
curl -sS 'http://127.0.0.1:8412/api/player/state?account_id=u_p3i'
curl -sS -X POST http://127.0.0.1:8412/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3i","verb":"move","args":{"to":"academy"}}'
curl -sS -X POST http://127.0.0.1:8412/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3i","verb":"talk","args":{"target":"bai_lusheng","message":"你好，今日有什么新鲜事？"}}'
# 回集市接任务
curl -sS -X POST http://127.0.0.1:8412/api/player/action \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3i","verb":"quest_accept","args":{"quest":"quest:errand_market_ledger:0"}}'
curl -sS 'http://127.0.0.1:8412/api/player/state?account_id=u_p3i'
```

实测要点：

```text
enter  ok/executed tick=0 next_tick=6 created=true wallet=300 inv={粗布:1, 炊饼:3}
state  ok=true tick=6 loc=集市 money=300 npcs=7
move   ok/executed tick=6 next_tick=12 loc=academy
talk   ok/executed tick=12 next_tick=18 reply_len=29
       reply=街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。
quest  ok/executed tick=24 next_tick=30
final  ok=true tick=30 loc=集市 accepted_by_me=1
       active=[quest:errand_market_ledger:0]
```

## 3. 自检

```bash
./scripts/selfcheck.sh
```

实测：

```text
[PASS] 内容包校验 content/validate_content.py
[PASS] 单元测试 pytest（301 passed, 4 skipped in 16.64s）
[PASS] 框架验收 tools/framework_check.py（6/6，FRAMEWORK CHECK: PASS）
[PASS] 玩家闭环（wsim.api + ui/player + HTTP 动作）
总结: selfcheck PASS（4 项全部通过）
```

4 条 skip 是显式保留的旧 824-event / 旧 P3I 基线（见 `tests/test_p3e_golden.py` 文件头），
不是删测试凑绿。

## 4. 断线提示

```bash
curl -sS http://127.0.0.1:8412/api/health
# {"ok": true, "mode": "live", "upstream_ok": true, ...}
kill -TERM $(lsof -nP -iTCP:8422 -sTCP:LISTEN -t | head -1)
curl -sS http://127.0.0.1:8412/api/health
# {"ok": true, "mode": "degraded", "readonly": false, "profile_version": 1,
#  "upstream": "http://127.0.0.1:8422", "upstream_ok": false}
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8412/
# 200
```

页面在 10 秒轮询内显示“上游不可达”；前端进程存活，不伪造世界状态。

## 5. 包内容核验

```bash
tar -tzf dist/xkx-world-sim-delivery-p3i.tar.gz | \
  grep -E 'world-sim/(\.venv|runs|\.git)/|\.env$|\.sqlite$'   # 无输出
tar -tzf dist/xkx-world-sim-delivery-p3i.tar.gz | \
  grep -E 'docs/CONTENT-GUIDE-p3i.md|reports/goal-lifecycle-audit.md|verify-guard-quota-frozen|tests/test_p3i_pricing.py'
```

结果：必含文档与测试全部命中；含 `specs/`、`tests/test_p3e_golden.py`、
`reports/evidence/blind-eval-r4/`、两臂 `blocked/pruned/stale.jsonl`；
不含 `.venv`、顶层 `runs/`、`.git`、`.sqlite`、真实 key。
