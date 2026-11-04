# Release Notes — delivery-p3j（对外交付）

- 代码 HEAD：`c999fb6`（`5635c26` 目标体量校准 → `bc7013c` 成长线/里程碑 → `c999fb6` 委托供给与节奏校准）
- 内容冻结：`content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`
  （31 NPC / 41 物品 / 9 地点 / 27 任务模板 / 4 门派）
- 前序：`delivery-p3i`（`d13ff53`）；p3f/p3g/p3h/p3i 的 tag 均不改写
- 包内测试：**321 passed, 8 skipped**（skip 为显式保留的旧基线）

## 1. 相对 p3i 的变化（逐条）

1. **目标体量校准**：长线目标 target 从 **600 文 / 0.6 / 40 点** 校准到 **142 文 / 0.033 / 12 点**，
   按 10 天有效产能 P60 的 6–8 成设定。规则臂 K1 **49.45% → 65.93%**，完成目标 **20 → 43**；
   攒钱/学手艺/结关系完成率 **3.8% / 0% / 0% → 30.8% / 31.2% / 50.0%**；
   空转目标 **38 → 23**、未创建目标 **22 → 15**；事件数 **3366 → 2707（−19.6%）**，
   减少的是空转事件，不是 NPC 变懒。
2. **里程碑链与维护义务**：一级完成后自动建二级（扩店 **250 → 分号 500**；
   技艺 **0.033 → 0.066 → 0.10**；关系 **12 → 24 → 36**）。新增维护成本：
   店铺月租 **30 文/家**、门派月费 **6 文/人**、技艺生疏 **−0.002**、关系衰减 **−1.0**。
   10 天内 **4 个 NPC 进二级、1 个进三级**；月租 **300 文**、月费 **96 文**实际扣账。
3. **玩家成长线**：声望 / 技能 / 资产三条各 **5 级**，带门槛与解锁；
   新增 **“今天能做什么”** 面板（“先做这个” + 可接委托 / 可学技能 / 可拜访 NPC / 可买产业）。
4. **委托供给与过期修复**：过期单自动下架，日界按模板补发，保证**每天至少 2 条可接**；
   拒绝原因可诊断（`quest_expired` / `reputation_too_low` / `too_many_quests`）；
   7 天压力测试 **14 接 / 14 交 / 0 拒绝**（逐日可接 8/7/7/7/7/7/7）。
5. **节奏调整**：技能 L2 门槛 **0.36 → 0.42**、资产 L2 **400 → 600**；
   第 1 天从三线 L2 变为**单线 L2**，技能 / 资产改为**第 4 天到 L2**。
6. **模型层结论（保留，不美化）**：K1 维度模型**零增益**；战略层实验 **FAIL**
   （PCR **22.73% < 28%**）；模型层机制实验**永久收工**。
   对话与好模板持平（非平手 **55.9%**，Wilson CI **44.1%–67.1%**）；
   模型仅用于 **spotlight** 与玩家主动深聊，**默认模板**。
7. **物价**：供需 + 库存驱动，单日 **±20%** 护栏；2 日 **15 件商品中 12 件有价差**。
8. **基线**：新增 `GOLDEN_MILESTONE`（**800 events**）与 `PLAYER_SUPPLY_GOLDEN`；
   旧基线（824 / 791 / 796 events）原样保留并显式 skip。
   **任何事件数 / 金额对比必须标注当前 `content_hash = 7ec670c1…`**。

## 2. 新增/更新文档

- 新增 `docs/RELEASE-NOTES-p3j.md`（本文件）。
- 重写 `docs/known-limitations.md`（p3j 口径，含 4 条新增限制）。
- 更新 `docs/QUICKSTART.md`、`deploy/README.md`：p3j、`content_hash=7ec670c1…`、321 passed / 8 skipped、
  成长线与“今天能做什么”面板说明。
- 新增 `docs/DELIVERY-TEST-p3j.md`：干净解包实测命令与输出。

## 3. 包内范围

- `src/`、`sim/`、`ui/`、`content/`、`config/`、`scripts/`、`tools/`、`tests/`、`specs/`、
  `fixtures/`、`plugins/`、`client/`、`docs/`、`reports/`；
- 含 `specs/a4-strategist`、`docs/CONTENT-GUIDE-p3i.md`、`reports/goal-lifecycle-audit.md`、
  `reports/verify-guard-quota-frozen-2026-11-04.md`、`tests/test_p3e_golden.py`、
  `tests/test_p4_quest_supply.py`；
- 不含 `.venv`、顶层 `runs/`、`.git`、`.sqlite`、真实 key；`runs/quickstart-2d` 首次启动生成。

## 4. 复现

```bash
python3 content/validate_content.py          # PASS，content_hash=7ec670c1…
python3 -m pytest                            # 321 passed, 8 skipped
./scripts/selfcheck.sh                       # 4/4 PASS
python3 tools/framework_check.py             # 6/6 PASS
python3 tools/replay.py --run reports/evidence/a3-model3-10d-31npc
```

事件数 / 金额 / 经济流量对比必须同时标注 `content_hash` 与当前 `state_final` / `event_final` 哈希，
跨快照不得直接相减。
