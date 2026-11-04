# delivery-p3h 交付规范

## 目标

从冻结代码 `451aed3` 出对外首发包 `delivery-p3h`，只允许追加证据与文档提交，不改引擎行为。

## 冻结基线

- 代码 HEAD：`451aed3`（接线 `d780dcf` + r4 盲评包 `451aed3`；前有 `456c093` r3b、`8deb619` 模板、`335a4ee` A3 收尾）。
- 可追加提交：`reports/evidence/**`、`docs/**`、`deploy/README.md`、`tools/build_dist.py`（仅补 `specs` 目录）。
- 禁止：改 `src/`、`sim/`、`ui/`、`content/`、`config/`、`tests/`。

## 交付内容

1. 证据入库
   - `reports/evidence/blind-eval-r2/result.md`、`result-r3.md`
   - `reports/evidence/blind-eval-r3/result-r4.md`
   - `reports/evidence/blind-eval-r4/result-r5.md`
   - `reports/evidence/blind-eval-result.md`
   - `reports/evidence/a3-model3-10d-31npc/`、`reports/evidence/a3-rule3-10d-31npc/`：
     `blocked.jsonl`、`pruned.jsonl`、`stale.jsonl`、`events.jsonl`、`event_hash.txt`、
     `world_hash.txt`、`summary.json`、`k3_details.json`
   - `dist/` 不入库。
2. 文档
   - 重写 `docs/known-limitations.md`（p3h 口径）。
   - 新增 `docs/RELEASE-NOTES-p3h.md`。
   - 新增 `docs/DELIVERY-TEST-p3h.md`（实测命令与输出）。
   - 更新 `deploy/README.md`、`docs/QUICKSTART.md` 的版本与测试数（287 passed）。
3. 打包
   - `tools/build_dist.py` 的 `TOP_DIRS` 增加 `specs`。
   - `git archive HEAD` 到临时目录，在该干净树内构建 `dist/xkx-world-sim-delivery-p3h.{tar.gz,zip}` 与 MANIFEST。

## 必须披露的结论（原文口径）

- K1：M 41/91=45.05%，R 45/91=49.45%，低 4.40pp；“模型带来行为多样性但不提高目标完成率”。
- K3：verb 17 vs 16、entropy 2.7231 vs 2.5842、JS 0.1448 vs 0.1087；对象层 entropy 5.0185 vs 5.0478；diagnostic_only，不设 pass/fail。
- K6：严格 1.142%、含剪枝 1.755%；trade 竞态 53 条全部 stale。
- R5：模型 38 胜 / 模板 30 胜 / 平手 2，非平手 55.9%，Wilson CI [44.1%, 67.1%]；“模板已接近模型水平”；70/70 更长（49.0 vs 31.0 字）；17 条更长但更空全在模型侧；模型 55/70 命中套路化 87 次（案板 39、擦手 19、要肥要瘦/趁早 8）。
- 产品默认：对话默认模板（零成本），模型仅 spotlight 或玩家主动深聊。
- 复现：`c4fc017c…`、`9b921eb1…` 回放 MATCH，成本 0；模板两条单关键词误判。
- 成本：A3 跨轮约 $5.80；对话生成累计约 $0.33；预算上限与实际值如实列出。

## 验收

- 干净解包：`./start.sh` 起双服务；`scripts/selfcheck.sh` 4/4 PASS；玩家闭环 enter/state/move/talk/quest/final；断线后 `/api/health` 为 `degraded`。
- 包内含：`specs/`、`tests/test_p3e_golden.py`、`reports/evidence/blind-eval-r4/`、两臂 `blocked/pruned/stale.jsonl`。
- 包不含：`.venv`、`runs/`、`.git`、真实 key。
- tag `delivery-p3h` 指向最终提交；不改写 `delivery-p3f`/`delivery-p3g`。
