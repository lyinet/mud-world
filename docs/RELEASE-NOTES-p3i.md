# Release Notes — delivery-p3i（对外交付）

- 代码 HEAD：`64365f8`（`19c298b` 审计 → `5833eca` 护栏 v2 → `20dc739` 内容 → `64458bd` K1 收工 → `64365f8` 物价接线）
- 内容冻结：`content_hash = fac4ac31fec9db5720d829d79827b215f5d0aee217be5fcb57bfdf38165ef8dd`
- 前序：`delivery-p3h`（`c19585b`）；p3f/p3g/p3h 的 tag 均不改写
- 包内测试：**301 passed, 4 skipped**（skip 为显式保留的旧基线）

## 1. 相对 p3h 的变化（逐条）

1. **内容包扩充并冻结**：`content_hash = fac4ac31…`；**41 物品 / 31 NPC / 9 地点 /
   27 任务模板 / 关系边 106**。确定性 2 日 seed 世界事件 **633 → 838（+205，+32.4%）**。
2. **新增 10 件物品**：麻绳 **66 笔**、墨条 **9 笔**、小米 **2 笔**、艾条 **2 笔**成交，
   另 **38 次消耗**；`bamboo_basket` 承接 38 条柜台事件。新增 4 步引导链、6 类日常委托、
   3 条长线目标；**31/31 NPC 有关系边**（朋友 47 / 对手 16 / 债主债户各 13 / 师徒各 5 / 亲族 8）。
3. **物价接入运行时**：供需 + 库存驱动，单日 **±20% 护栏**；2 日 15 件商品中 **12 件有价差**，
   最高 **+12.5%**。事件数与调度不变（接线前后均 1224 events / K1 19/91 / 非法率 0.000817），
   但成交金额显式漂移，新增基线 **`GOLDEN_P3I_PRICE` = 791 events**
   （state `86871faf…`、event `ee7d0a90…`、sell 1145 / buy 842）。
4. **K1 线正式收工**：三次尝试均未超过规则臂；冻结快照下规则臂与模型臂**同为 56.04%（51/91）**；
   `49.45% → 56.04%` 的提升**归因于内容扩充，不是模型**；模型在该维度**零收益、纯成本**
   （438 calls / $3.03，并在第 7 天触发 $3.0 hard 中止）。护栏 v2 与类型配额**代码保留、默认关闭**。
5. **对话层结论不变**：R5 最终盲评模型 38 胜 / 模板 30 胜 / 平手 2，非平手 **55.9%**
   （Wilson CI **44.1%–67.1%**），与好模板持平；**默认走模板**，模型仅 spotlight / 玩家主动深聊。

## 2. 新增/更新文档

- 新增 `docs/CONTENT-GUIDE-p3i.md`：内容包逐项验证入口（新手链、日常、长线、物价、关系）。
- 新增 `reports/goal-lifecycle-audit.md`：目标生命周期审计（方向更正依据）。
- 新增 `reports/verify-guard-quota-frozen-2026-11-04.md`：冻结快照 K1/K3/K6/成本/护栏明细。
- 更新 `docs/known-limitations.md`：K1 三次背景、物价限制、模型零增益、快照 hash 纪律。
- 更新 `docs/QUICKSTART.md`、`deploy/README.md`：p3i 版本与 301 passed / 4 skipped。

## 3. 包内范围

- `src/`、`sim/`、`ui/`、`content/`、`config/`、`scripts/`、`tools/`、`tests/`、`specs/`、
  `fixtures/`、`plugins/`、`client/`、`docs/`、`reports/evidence/`；
- 含 `tests/test_p3e_golden.py`、`tests/test_p3i_pricing.py`、`reports/evidence/blind-eval-r4/`、
  两臂 `blocked/pruned/stale.jsonl`；
- 不含 `.venv`、顶层 `runs/`、`.git`、`.sqlite`、真实 key；`runs/quickstart-2d` 首次启动生成。

## 4. 复现

```bash
python3 content/validate_content.py          # PASS，content_hash=fac4ac31…
python3 -m pytest -q                          # 301 passed, 4 skipped
./scripts/selfcheck.sh                        # 4/4 PASS
python3 tools/replay.py --run reports/evidence/a3-model3-10d-31npc
```

价格/金额对比必须同时标注 `content_hash` 与当前 `state_final` / `event_final` 哈希，跨快照不得直接相减。
