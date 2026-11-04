# Release Notes — delivery-p3h（对外首发）

- 基线提交：`451aed3`（接线 `d780dcf` + r4 盲评包 `451aed3`）
- 前序：`456c093`（r3b）、`8deb619`（模板与 R3 输入）、`335a4ee`（A3 收尾）
- 证据与文档提交：`1c046fd` 及本版文档提交
- 对外口径以 `delivery-p3h` 为准；`delivery-p3f` / `delivery-p3g` 不再对外

## 1. 本版做了什么

### 对话

- `text_guard` 接入与统一 system prompt（`d780dcf`）：方括号主题词标签、Markdown、括号动作、
  第三人称旁白两侧均为 0；角色口号去重。
- 诉求路由收细（`456c093`）：把“问旧事/问路/借物/赊账”等从粗分类拆开，r3b 重建后用于 r4 盲评；
  仍保留两条单关键词误判（见 known-limitations §3）。
- 对话默认走模板（零成本）；模型只用于 spotlight NPC 或玩家主动深聊。

### 自主目标（A3）

- 模型目标选择权、动作留存、`not_together` 丢弃/改选、预算硬闸（`f1010a0`、`9242c1e`）。
- 竞态 trade 记 `stale`、intent 归零、护栏收口（`335a4ee`）：
  - intent 调用 **0**（`by_slot={"decision"}`）；
  - 53 条 trade 竞态（35 llm + 18 planner）全部 stale，不计非法；
  - K6 严格 1.142%、含剪枝 1.755%，均 <2% PASS，已执行非法 0。
- K1 未达规则臂：M 41/91=45.05% vs R 45/91=49.45%（-4.40pp）。停止规则生效：
  “模型带来行为多样性，但不提高目标完成率”，不再追 +5pp。
- K3 转 diagnostic_only：verb 17 vs 16、entropy 2.7231 vs 2.5842、pairwise JS 0.1448 vs 0.1087；
  对象层 entropy 5.0185 vs 5.0478（略低，如实保留）。不设 pass/fail。

### 盲评

- R5（70 对）：模型 38 胜 / 模板 30 胜 / 平手 2，非平手胜率 55.9%，Wilson CI [44.1%, 67.1%]。
  结论“模板已接近模型水平”。模型 70/70 对更长（49.0 vs 31.0 字），17 条“更长但更空”全在模型侧；
  模型侧 55/70 条命中套路化动作/口号 87 次（案板 39、擦手 19、“要肥要瘦/趁早”8 等）。
- 证据：`reports/evidence/blind-eval-r2/result*.md`、`blind-eval-r3/result-r4.md`、
  `blind-eval-r4/result-r5.md`、`blind-eval-result.md`。

### 复现与成本

- 整 run 回放 `c4fc017c…`、`9b921eb1…` 均 **MATCH**，回放成本 **0**。
- A3 跨轮累计约 **$5.80**；对话生成累计约 **$0.33**（R3 $0.110151 / 上限 $0.30，
  R4 $0.220709 / 上限 $0.25，均未超限）。
- A3 单轮硬上限 $3.0、soft $2.5：第二轮 $2.657972 触发 soft（$2.5015 打印），未触 hard；
  第三轮 $2.466982 未触 soft；跨轮预算不合并，累计 $5.796 超过单轮上限（规则如此）。

## 2. p3h 与 p3g 的差异（逐条）

1. **基线**：p3g `60e8596` → p3h `451aed3`。
2. **对话质量工程**：新增 `text_guard`、统一 system prompt、诉求路由收细；p3g 无。
3. **盲评样本**：p3g 只有 r2 30 对；p3h 扩到 70 对并有 R5 最终盲评（r2/r3/r3b/r4 四轮）。
4. **自主目标指标**：p3g 对外 K1 7.69%（2 天/12 NPC、p3e run）；p3h 改为 10 天/31 NPC 的
   A3 M/R 对照，45.05% vs 49.45%，并明确判定 FAIL。
5. **K3**：p3g 未作为对外指标；p3h 首次给出双层 K3，且标为 diagnostic_only。
6. **K6 口径**：p3g 对外 0.85% / 0.90%（2 天 run）；p3h 为 1.142%（严格）/ 1.755%（含剪枝），
   trade 竞态 53 条改记 stale。
7. **成本披露**：p3g 无跨轮累计；p3h 披露 A3 $5.80、对话 $0.33 与预算/超限事实。
8. **复现证据**：p3h 新增两臂 `events.jsonl`、`blocked/pruned/stale.jsonl`、`k3_details.json`。
9. **包内容**：p3h 首次把 `specs/` 打入包；新增 `docs/RELEASE-NOTES-p3h.md`。
10. **测试数**：p3g 194 passed → p3h 287 passed（golden 基线 v3 归类修正）。
11. **tag**：新增 `delivery-p3h`，不改写 `delivery-p3f` / `delivery-p3g`。
12. **包内可跑**：5 个依赖 `runs/a2-dialogue-30` 的对话回归测试改为 `runs/` 优先、
    `reports/evidence/a2-dialogue-30/` 回退；包内不含 `runs/` 时仍能跑满 287 项。

## 3. 包里有什么

- `src/`、`sim/`、`ui/`、`content/`、`config/`、`scripts/`、`tools/`、`tests/`、`fixtures/`、
  `plugins/`、`client/`、`specs/`、`docs/`、`reports/evidence/`；
- `tests/test_p3e_golden.py`、`reports/evidence/blind-eval-r4/`、
  两臂 `blocked.jsonl` / `pruned.jsonl` / `stale.jsonl`；
- 不含 `.venv`、`runs/`、`.git`、真实 key；`runs/quickstart-2d` 在首次 `./start.sh` 生成。
