# p3j 交付实测记录（2026-11-04）

- 代码基线：`c999fb6`；文档提交 `edb3560`
- 内容冻结：`content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`
  （31 NPC / 41 物品 / 9 地点 / 27 任务模板）
- 环境：macOS，Python 3.12.7
- 干净目录：`/tmp/xkx-p3j-clean/world-sim`（无原仓库 `.venv` / `runs`）
- 实测包由 `git archive HEAD`（`edb3560` 树）构建；最终包仅追加本文件，代码与数据一致。
  SHA256 见交付回报。

## 1. 解包与首次启动

```bash
rm -rf /tmp/xkx-p3j-clean && mkdir -p /tmp/xkx-p3j-clean
tar -xzf dist/xkx-world-sim-delivery-p3j.tar.gz -C /tmp/xkx-p3j-clean
cd /tmp/xkx-p3j-clean/world-sim
API_PORT=8422 PORT=8412 RUN_ID=p3j-clean ./start.sh
```

实测：

```text
[start] 首次运行：创建 .venv ...
[start] 安装依赖 requirements.lock ...
[start] Python: Python 3.12.7 (.../world-sim/.venv/bin/python)
种子世界: quickstart-2d · 2 游戏日 · 836 条事件 · NPC 自主行动 736 条
[demo] 端口: API=8422 前端=8412 均空闲
[demo] 健康检查: API 200 (GET /api/player/state)
[demo] 健康检查: 前端 200 (GET /api/health)
[demo] 健康检查: 前端已连上 API (mode=live, upstream_ok=true)
[demo] 前端: http://127.0.0.1:8412/
```

```json
{"ok": true, "mode": "live", "readonly": false, "profile_version": 1,
 "upstream": "http://127.0.0.1:8422", "upstream_ok": true}
```

## 2. 玩家闭环（含成长面板 / 接单交付 / 开摊 / 账本）

```bash
curl -sS -X POST http://127.0.0.1:8412/api/player/enter \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","display_name":"初七","archetype":"捕快"}'
curl -sS 'http://127.0.0.1:8412/api/player/state?account_id=u_p3j'
# 接单 → 移动 → 对话 → 交付 → 回集市开摊
curl -sS -X POST http://127.0.0.1:8412/api/player/action -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","verb":"quest_accept","args":{"quest":"quest:errand_market_ledger:0"}}'
curl -sS -X POST http://127.0.0.1:8412/api/player/action -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","verb":"move","args":{"to":"academy"}}'
curl -sS -X POST http://127.0.0.1:8412/api/player/action -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","verb":"talk","args":{"target":"bai_lusheng","message":"你好，今日有什么新鲜事？"}}'
curl -sS -X POST http://127.0.0.1:8412/api/player/action -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","verb":"move","args":{"to":"market"}}'
curl -sS -X POST http://127.0.0.1:8412/api/player/action -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","verb":"move","args":{"to":"inn"}}'
curl -sS -X POST http://127.0.0.1:8412/api/player/action -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","verb":"quest_deliver","args":{"quest":"quest:errand_market_ledger:0"}}'
curl -sS -X POST http://127.0.0.1:8412/api/player/action -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","verb":"move","args":{"to":"market"}}'
curl -sS -X POST http://127.0.0.1:8412/api/player/action -H 'Content-Type: application/json' \
  -d '{"account_id":"u_p3j","verb":"open_shop","args":{"at":"market","shop_type":"stall","name":"初七的摊子"}}'
```

实测要点：

```text
enter   ok/executed tick=0 next_tick=6 created=true wallet=300
        growth: assets=囊中羞涩 L1(next 600) / reputation=无名之辈 L1(next 3) / skill=生手 L1(next 0.42)
        today.next_action=先接委托：【轻】打听客栈酒价
        today.quests=3 条；today.business=stall 300 文（买得起）/ storefront 1200 / branch 3000
state   tick=6 loc=集市 money=300；quest_board acceptable_here=7
accept  ok/executed tick=6 next_tick=12 active=[quest:errand_market_ledger:0]
move    ok/executed tick=12 loc=academy
talk    ok/executed tick=18 next_tick=24 reply_len=29
        reply=街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。
move    ok tick=24 loc=market；tick=30 loc=inn
deliver ok/executed tick=36 next_tick=42 money=310 active=[]
        details.relation=friend affection=10 trust=45 target=jin_mancang
move    ok tick=42 loc=market money=310
shop    ok/executed tick=48 money=10（开摊支出 300 文）
final   ok=true tick=54 loc=集市 money=10
        growth: reputation=小有名气 L3(next 40)；skill=生手 L1(next 0.42)；assets L1(next 600)
        today.next_action=先接委托：【轻】打听客栈酒价；today.quests 3 条
```

判定：面板“今天能做什么”可见、三条成长线可见、接 1 单并交付 1 单、开摊成功、
账本资金变化 `300 → 310（任务 +10）→ 10（开摊 −300）`。

## 3. 包内测试

```bash
cd /tmp/xkx-p3j-clean/world-sim
.venv/bin/python -m pytest          # 321 passed, 8 skipped in 17.32s
./scripts/selfcheck.sh              # 4/4 PASS
.venv/bin/python tools/framework_check.py   # 6/6 PASS
```

实测：

```text
321 passed, 8 skipped
[PASS] 内容包校验 content/validate_content.py
[PASS] 单元测试 pytest（321 passed, 8 skipped）
[PASS] 框架验收 tools/framework_check.py（6/6，FRAMEWORK CHECK: PASS）
[PASS] 玩家闭环（wsim.api + ui/player + HTTP 动作）
总结: selfcheck PASS（4 项全部通过）

[1/6] 新增内容不改码 + 停摆门禁 ... PASS
[2/6] 新增插件不改码 + 完整 state 隔离 ... PASS
[3/6] MCP 身份映射与越权拒绝 ... PASS
[4/6] LOD 事件驱动与外部计数 ... PASS
[5/6] 守恒 / 债务独立重算 ... PASS
[6/6] 假端点端到端（127.0.0.1） ... PASS
FRAMEWORK CHECK: PASS
```

8 条 skip 是显式保留的旧基线（824 / 791 / 796 events），不是删测试凑绿。

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
tar -tzf dist/xkx-world-sim-delivery-p3j.tar.gz | \
  grep -E 'world-sim/(\.venv|runs|\.git)/|\.env$|\.sqlite$'   # 无输出
tar -tzf dist/xkx-world-sim-delivery-p3j.tar.gz | \
  grep -E 'specs/a4-strategist|docs/CONTENT-GUIDE-p3i.md|reports/goal-lifecycle-audit.md|reports/verify-guard-quota-frozen-2026-11-04.md'
```

结果：必含文档与 specs 全部命中；含 `specs/`（含 `a4-strategist`、`delivery-p3j`）、
`docs/CONTENT-GUIDE-p3i.md`、`reports/goal-lifecycle-audit.md`、
`reports/verify-guard-quota-frozen-2026-11-04.md`、`tests/test_p3e_golden.py`（`GOLDEN_MILESTONE`）、
`tests/test_p4_quest_supply.py`（`PLAYER_SUPPLY_GOLDEN`）、`reports/evidence/blind-eval-r4/`；
不含 `.venv`、顶层 `runs/`、`.git`、`.sqlite`、真实 key。

## 6. W4 客户端体验验收证据（`reports/w4-ui/`）

本目录证据由 `python3 tools/ui_check.py` 在 mock 服务上生成（不写世界），
`reports/w4-ui/checks.json` 是脚本直接写出的 66 条断言结果（**66 checks / 0 failed**），不是手工粘贴。
复现：

```bash
python3 tools/ui_check.py          # 生成 screenshots + checks.json + keyboard.txt
```

| 文件 | 对应验收项 |
| --- | --- |
| `checks.json` | 全部 66 条断言的机器可读结果（原因码、桌面、移动、键盘、回归、鉴权） |
| `desktop-step1.png` … `desktop-step4.png` | 桌面四步引导：① 先做这个 → ② 接委托 → ③ 移动 → ④ 交付，逐步高亮 |
| `desktop-complete.png` | ④ 交付后资金 300→312 文、引导完成 |
| `desktop-reload-no-guide.png` | 引导完成/跳过后刷新不再出现（localStorage 持久化） |
| `mobile-step1.png` … `mobile-step4.png` | 375×812 下四步各自目标在视口内、未被提示遮挡 |
| `mobile-complete.png` | 移动端主流程完成（资金 312 文，四组面板与三条成长线仍在） |
| `mobile-summary.json` | 移动端终态摘要（`scrollWidth=375`，无横向滚动） |
| `keyboard.txt` / `keyboard-complete.png` | Tab 走完主流程、Enter 触发、3px 可见焦点 |
| `reasons-history.png` | 原因码中文文案与右下角最近 3 条错误历史 |
| `regression-healthy.png` | 健康模式下断线提示条不显示 |
| `regression-degraded.png` | 降级模式显示「上游不可达」 |
| `auth-required.png` | 未登录时登录/注册入口与「需要重新登录」提示 |
| `auth-401-mid-session.png` | 写操作 401 后重登入口出现、世界状态保留 |

鉴权部分按 W1 真实 `/api/auth/*` 契约用 mock 复验，真实服务端到端联调留到整合波。
