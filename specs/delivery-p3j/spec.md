# delivery-p3j 交付规范

## 目标

从冻结代码 `c999fb6` 出对外交付包 `delivery-p3j`，只允许追加/更新交付文档与打包脚本，不改引擎行为；
`delivery-p3f` / `p3g` / `p3h` / `p3i` 的 tag 一律不改写。`dist/` 不入库。

## 冻结基线（已核对）

- 代码 HEAD：`c999fb6`（`fix(p4): 在线玩家委托供给/过期下架与节奏门槛校准`）；
  前序 `bc7013c`（成长线/里程碑）、`5635c26`（目标体量校准）、`d3fd35c`/`ff56f92`/`1c962df`（A4 战略层）。
- 内容冻结：`content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`
  （31 NPC / 41 物品 / 9 地点 / 27 任务模板 / 4 门派；与 p3i `fac4ac31…` 不同，事件/金额对比必须标注）。
- 工作树：仅 `dist/` 下历史产物未跟踪；无源码改动。
- 仓库内实测：`pytest` **321 passed, 8 skipped**；`selfcheck.sh` 4/4 PASS；framework 6/6 PASS。

## 交付内容

1. 文档
   - 新增 `docs/RELEASE-NOTES-p3j.md`：逐条写目标体量校准、里程碑链与维护义务、玩家成长线、
     委托供给与过期修复、节奏调整、模型层结论、物价、基线。
   - 重写 `docs/known-limitations.md` 到 p3j 口径，新增 4 条限制（两次机制实验 FAIL、target 按 10 天校准、
     委托补发仅玩家会话、节奏仅单人实测）。
   - 更新 `docs/QUICKSTART.md`、`deploy/README.md`：p3j 版本、321 passed / 8 skipped、
     `content_hash=7ec670c1…`、成长线/面板说明。
   - 新增 `docs/DELIVERY-TEST-p3j.md`：干净解包实测命令与输出。
2. 证据
   - 确认包内含 `specs/`（含 `a4-strategist`）、`docs/CONTENT-GUIDE-p3i.md`、
     `reports/goal-lifecycle-audit.md`、`reports/verify-guard-quota-frozen-2026-11-04.md`、
     `tests/test_p3e_golden.py`（`GOLDEN_MILESTONE`）与 `tests/test_p4_quest_supply.py`（`PLAYER_SUPPLY_GOLDEN`）。
3. 打包
   - 使用现有 `tools/build_dist.py --version p3j`（`TOP_DIRS` 已含 `specs`）。
   - `git archive HEAD` → `/tmp/xkx-p3j-src`，在干净树内构建 tar.gz/zip/MANIFEST，复制回 `dist/` 并算 SHA256。

## 必须披露的结论（release notes 原文口径）

1. 目标体量：target 600→142 文 / 0.6→0.033 / 40→12 点，按 10 天有效产能 P60 的 6–8 成；
   规则臂 K1 49.45%→65.93%，完成目标 20→43；攒钱/学手艺/结关系完成率
   3.8%/0%/0%→30.8%/31.2%/50.0%；空转 38→23、未创建 22→15；事件 3366→2707（−19.6%，空转事件消失）。
2. 里程碑链：扩店 250→分号 500；技艺 0.033→0.066→0.10；关系 12→24→36；月租 30 文/家、
   月费 6 文/人、技艺生疏 −0.002、关系衰减 −1.0；10 天 4 人进二级、1 人进三级；月租 300、月费 96 实扣。
3. 玩家成长线：声望/技能/资产各 5 级，带门槛与解锁；“今天能做什么”面板（先做这个 + 可接委托/可学技能/可拜访/可买产业）。
4. 委托：过期单自动下架、日界按模板补发、每天至少 2 条可接；拒绝原因可诊断
   （`quest_expired` / `reputation_too_low` / `too_many_quests`）；7 天 14 接 14 交 0 拒绝。
5. 节奏：技能 L2 门槛 0.36→0.42、资产 L2 400→600；第 1 天从三线 L2 变为单线 L2，技能/资产第 4 天 L2。
6. 模型层：K1 维度零增益；战略层实验 FAIL（PCR 22.73% < 28%）；机制实验永久收工；
   对话与好模板持平（55.9%，CI 44.1–67.1）；模型仅用于 spotlight 与主动深聊，默认模板。
7. 物价：供需+库存驱动、单日 ±20%；2 日 15 件中 12 件有价差。
8. 基线：新增 `GOLDEN_MILESTONE`（800 events）、`PLAYER_SUPPLY_GOLDEN`；旧基线保留并 skip；
   事件/金额对比必须标注当前 `content_hash=7ec670c1…`。

## 新增 known-limitations

- 模型层两次机制实验（每 tick 选择器、每 3 日战略层）均 FAIL，K1 维度零增益，仅保留对话/spotlight 价值。
- 目标 target 按 10 天窗口校准，72 天长周期是否仍成立未验证。
- 委托供给只在玩家会话作用域修复，NPC 侧不发补发。
- 节奏调整只基于单人实测路径，未经多人/长时间验证。

## 验收

- 干净目录解包 → `./start.sh` 一条命令起双服务（API + 前端）。
- 实测玩家闭环并贴输出，必须包含：面板显示“今天能做什么”、三条成长线可见、至少接 1 单并交付 1 单、
  开摊或入会之一成功、账本出现资金变化。
- 包内 `pytest` 全绿并贴计数（预期 321 passed, 8 skipped 或更多）；selfcheck 4/4；framework 6/6。
- 包内不得含 `.venv`、顶层 `runs/`、`.git`、`.sqlite`、真实 key；必须含
  `specs/`（含 a4-strategist）、`docs/CONTENT-GUIDE-p3i.md`、`reports/goal-lifecycle-audit.md`、
  `reports/verify-guard-quota-frozen-2026-11-04.md`。
- 打 annotated tag `delivery-p3j`，给 zip/tar.gz SHA256 与 MANIFEST。
- 若包内容与 HEAD 不一致或测试失败：修打包脚本/取证路径，不删测试凑绿，并在结论说明。

## 纪律

- 只 `git add` 自有文档与打包相关文件，禁止 `git add -A`；`dist/` 不入库；
- tag 不改写 p3f/p3g/p3h/p3i。
