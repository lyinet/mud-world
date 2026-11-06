# 侠客行（xkx）承接方案：可拍板版

> 输入：`engine-feasibility.md`（引擎可行性）+ `content-inventory.md`（内容盘点）。
> 本文只做决策与计划，不含实现代码。
> 内容基线：`fluffos/xkx100` @ `c1b87db`（2026-10-14），5,683 房 / 12,611 出口边 / 87 区域 / 2,501 物品 / 2,385 NPC / 362 技能 / 31 门派 / 2,017 静态任务。
> 合规事实：5 个候选仓库全部无 LICENSE；`xkx2001` README 明示“仅供学习用”。

---

## 0. 执行摘要（给拍板用的 12 行）

1. **能做**：在现有事件溯源引擎上交付一个 195 房的核心循环切片——文字房间/出口、买卖/对话、门派师徒、任务、属性/技能成长、回合战斗、刷怪掉落，全流程确定性可回放。
2. **不做**：逐秒心跳、物品实例化/容器/耐久、PK、尸体持久化、全量 5,683 房一次上线。
3. **推荐切片**：杭州 + 嘉兴 + 石梁（195 房 / 38 物品 / 92 NPC / 3 商店 / 22 位师父模板 / 7 个主动攻击 NPC）。
4. **理由**：这是唯一同时具备“商店、门派入口、战斗、对话”的紧凑区域，且三区两跳连通；失败可回滚，不污染现有世界。
5. **战斗方案**：一次 `fight` 动作原子结算最多 20 回合，写 1 条事件 + 逐回合日志；世界 tick 仍为 10 分钟，战斗动作只耗 1 tick。
6. **确定性**：随机全部走 `PRNG(seed, seq, salt)`；新增字段默认空且条件序列化；无玩家旧基线必须逐字节不变。
7. **数据管线**：LPC→JSON 抽取器可重跑、幂等、可 diff；以已知覆盖率（房间 96.1% / 出口 97.8% / 物品 85% / NPC 90% / 对话 64%）为下限，缺口进 `diagnostics.jsonl`，不静默丢弃。
8. **切片工时**：计划 70 agent-round（≈35 人日）；按 98 AR 预算排期，3 个 agent 约 4–6 周（含返工与缓冲）。
9. **全量工时**：320–560 agent-round（≈160–280 人日，含内容 120–200 + 机制 40–80），3–4 个 agent 约 14–31 周。
10. **最大阻塞**：内容仓库无 LICENSE。内部原型可做；公开必须取得书面授权或重写文案。
11. **决策点**：范围切片、独立内容包、不公开、不做逐秒、预算 6 周 gate。
12. **最短可玩**：P0 壳 + P1 切片 + P2 战斗 ≈ 40 agent-round，之后玩家能 `look/go/ask/buy/fight/loot/score`。

---

## 1. 目标与非目标

### 1.1 一句话目标

**在现有 `world-sim` 引擎上，做出一个“玩家能真正走进去玩”的 xkx 核心循环切片：能看房间、走出口、问 NPC、买卖、拜师、接任务、练技能、打怪掉宝、死亡复活，并且整段可确定性回放。**

### 1.2 非目标（每条给替代方案）

| 不做 | 原因 | 替代方案 |
|---|---|---|
| 逐秒实时心跳 | 现有 1 tick = 10 游戏分钟；改成秒级要重写 LOD、任务截止、日结和全部 golden 基线 | 一次动作 = 一个 10 分钟时间片；战斗内部按回合结算，客户端逐行动画播放 |
| 物品实例化 / 容器 / 耐久 / 重量 | 现在物品是 `{item_id: qty}`，`_owner_stock` 只认 entity/business；改实例会波及全部背包/商店/任务 | 同质计数；容器后续用 `ground:<loc>` / `container:<id>` 持有者单独立项 |
| 玩家 PK / 偷窃 / 抢劫 | `PLAYER_VERBS` 明确排除 L3 伤害/抢掠；全局单写者会把 PvP 串行化 | 第一版只做 PvE；后续做“双方同意的决斗房” |
| 尸体持久化 | 无地面持有者；会引入实体生命周期与清理 | 击杀后掉落直接进击杀者背包 |
| 全量 5,683 房一次上线 | 内容 120–200 人日，抽取/校验/文案缺口会在第一版淹没机制验证 | 先 195 房切片，验证核心循环后再按区域扩容 |
| 362 技能 / 31 门派全量 | 技能树和门派传承是内容+平衡工程，不是引擎问题 | 第一版 8–12 技能、2–3 门派；保留扩展位 |
| 2,017 静态任务 1:1 | 任务文本和前置条件高度脚本化 | 20–30 个声明式模板 + 任务生成器 |
| 玩家间聊天/组队/交易 | 会话层没有玩家间消息模型 | 第一版只对 NPC `talk/ask`；后续在 API 层加频道 |
| 天气 / 饥渴 / 精细昼夜 | 只有 `stamina` 与 `tick/day` | 天气只做展示；饥渴并入 stamina |
| 图形 UI / 鼠标操作 | 目标是文字 MUD 操作感 | 纯文字壳：滚动日志 + 命令输入 |

---

## 2. 范围建议：三个候选切片

### 2.1 切片对比（实测计数，来自本地 `fluffos-xkx100`）

| 切片 | 区域 | 房间 | 物品模板 | NPC 模板 | 房间 objects 槽 | 商店 | 门派数 | 可拜师 NPC | 主动攻击 NPC | 可问话 NPC | 连通性 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **A（推荐）** | 杭州 + 嘉兴 + 石梁 | **195** | **38** | **92** | 141 | **3** | **1**（红花会） | **22** | **7** | 16 | 杭州↔嘉兴 1 跳，嘉兴↔石梁 1 跳 |
| B | 扬州 + 黄河 + 山村 | 250 | 188 | 230 | 289 | 24 | 0 | 0 | 4 | 74 | 扬州↔黄河/山村各 1 跳 |
| C | 大理 + 无量山 + 天龙寺 | 254 | 52 | 102 | 260 | 3 | **1**（天龙寺） | 8 | 2 | 17 | 大理↔无量山/天龙寺各 1 跳 |
| 全量 | 87 区域 | 5,683 | 2,501 | 2,385 | — | — | 31 | — | — | — | 261 条跨区边 |

> 计数口径：房间= `inherit ROOM` 文件；物品=继承 ITEM/装备/食物等基类的文件；NPC=`inherit NPC` 文件；主动攻击=源码含 `aggressive`；可拜师=`inherit F_MASTER`；商店=含 `vendor_goods`；可问话=含 `set("inquiry")`。同一 NPC 模板可能被多个房间克隆，故“模板数”不是在线实例数。

### 2.2 为什么推荐 A（杭州+嘉兴+石梁）

1. **四要素齐全**：3 个商店（买卖闭环）、1 个门派（红花会 19 位师父 + 金蛇郎君/武林盟主 3 个拜师入口）、7 个主动攻击 NPC（战斗）、16 个可问话 NPC（对话）。
2. **连通性好**：杭州→嘉兴→石梁两跳；切片边界存在指向区外的出口，统一做 `closed` stub，保证切片内零悬空边。
3. **规模可控**：195 房 = 全量的 3.4%，但覆盖了门派/商店/战斗/对话四类代表内容；抽取器可以在一天内跑完并 diff。
4. **失败可回滚**：独立内容包 + 独立 `events.sqlite`，不碰现有 9 地点/31 NPC 世界。
5. **后续扩容自然**：P5 加苏州（+106 房）到 301 房，再按区域扩展。

**为什么不是 B**：扬州经济/对话最强（188 物品、24 商店、74 可问话），但 0 门派、4 个主动攻击 NPC，验证不了“拜师 + 战斗”。
**为什么不是 C**：大理/天龙寺门派最强（1 个门派 + 8 位师父模板），但只有 2 个主动攻击 NPC、3 个商店、52 物品，经济与战斗都太薄。
**为什么不一次搬全量**：全量 87 区域 / 2,385 NPC / 2,501 物品，抽取校验与文案缺口的返工量会掩盖机制问题；且 license 未解决，先做小切片可以把“可不可公开”与“好不好玩”分开决策。

### 2.3 全量 vs 切片：工时差

折算口径（全文统一）：

- **1 agent-round（AR）** = 1 个 agent 的 1 个半日工作单元（≈0.5 人日），含自测、诊断与文档。
- 1 个 agent 每周按 **6 个有效 AR** 计（5 个工作日 × 1.2 轮/日，扣除评审与返工）。
- **3 个并行 agent = 18 AR/周；4 个并行 agent = 24 AR/周。**

| 口径 | 内容侧 | 机制侧 | 合计 | AR | 3 agents | 4 agents |
|---|---|---|---:|---:|---:|---:|
| **切片 A（195 房）** | 8–14 人日 | 20–35 人日 | **28–49 人日** | **56–98 AR** | **3.1–5.4 周** | **2.3–4.1 周** |
| 全量 5,683 房 | 120–200 人日 | 40–80 人日 | **160–280 人日** | **320–560 AR** | **17.8–31.1 周** | **13.3–23.3 周** |

- 切片内容侧 8–14 人日 ≈ 全量内容的 4%–12%（房间 3.4%、物品 1.5%、NPC 3.9%，抽取器/校验的固定成本占比较高）。
- 切片机制侧 20–35 人日 = 全量机制的 50%–70%（战斗/属性/技能/刷新/死亡是“有或没有”的一次性投入；心跳/存档/装备/任务系统现有引擎已有）。
- 上表不含 license 谈判、文案重写、平衡调优与线上运维。
- **建议按 98 AR 排期（切片上限），即 3 agents 约 5.4 周，另留 1 周缓冲 = 6 周内可给老板演示。**

---

## 3. 阶段计划

### P0｜文字壳：先让人“看见这个世界”（2 agent × 2 轮 = 4 AR）

| 项 | 内容 |
|---|---|
| 玩家看见 | 滚动文字区、命令输入；`look` 输出房间名/长描述/出口/NPC；`i` 背包；`score` 体力/钱包/位置；`help` 基础命令 |
| 能做什么 | `look / n / s / e / w / up / down / i / score / help / ask` |
| 涉及模块 | `ui/player`（文字壳）；只读复用 `session._location_view` / `_present_view`；`move/talk` 走现有动作 |
| 验收（可执行） | ① `GET /api/player/state` 返回真实 `location/exits/services/wallet`；② `move` 后 `events.sqlite` 新增 1 条 `move` 事件；③ 9 个现有房间全部可达；④ `pytest -q tests/test_p3e_golden.py` 绿；⑤ 前端刷新后数据来自 API，不是硬编码 |
| 边界 | 不改后端接口契约；不改现有世界内容 |

### P1｜抽取器 + 195 房切片（3 agent × 6 轮 = 18 AR）

| 项 | 内容 |
|---|---|
| 玩家看见 | 195 个真实 xkx 房间、38 种物品、92 个 NPC、3 个商店；杭州/嘉兴/石梁地图连通；`ask` 有真实问答；`buy/sell` 用真实价格与库存 |
| 能做什么 | `look / go / ask 店小二 / buy 包子 / sell 野果 / i / score / map` |
| 涉及模块 | `tools/xkx_extract.py`、`content/packs/xkx-slice/**`、`content` 加载器（pack 选择）、只读视图补 `map` |
| 验收（可执行） | ① `python tools/xkx_extract.py --src /tmp/xkx-research/fluffos-xkx100 --areas hangzhou,jiaxing,shiliang --out content/packs/xkx-slice --strict` 退出码 0；② 重跑后 `manifest.content_hash` 一致（幂等）；③ `python tools/xkx_content_validate.py --pack xkx-slice` 报 `errors=0`；④ 可达房间 ≥190/195，不可达清单有 owner；悬空出口=0；⑤ 2 日 seed 世界跑两遍，state/event hash 完全一致；⑥ `pytest tests/test_xkx_extract.py tests/test_xkx_validate.py` 绿 |
| 边界 | 边界出口做成 `closed` stub；缺口文案用 placeholder + diagnostic，不静默丢 |

### P2｜战斗 v1：从“能走”到“能打”（3 agent × 6 轮 = 18 AR）

| 项 | 内容 |
|---|---|
| 玩家看见 | `score` 出现气血/内力；`fight 野狗` 逐行输出“你击中/对方闪避/对方反击”；怪死掉宝；玩家气血归零后复活在客栈；怪按刷新间隔重新出现 |
| 能做什么 | `fight <npc> / flee / loot / score / look` |
| 涉及模块 | `state.py`（`hp/max_hp/attrs`）、`effects.py`（`hp_add/entity_status/item_create/transfer`）、`actions/combat.py`、`player.py` 白名单、`scheduler`（`system_spawn`）、内容包怪物模板 |
| 验收（可执行） | ① 能击杀至少 1 种怪，`inventory` 多出掉落物；② HP 从满值降到 0 后触发死亡与复活；③ 同一 seed 两次 run 的 `state_final_hash` 与 `event_hash` 一致；④ `pytest tests/test_xkx_combat_determinism.py` 绿；⑤ `pytest tests/test_p3e_golden.py` 仍绿（旧世界逐字节不变）；⑥ 回放后 hash 与实时一致 |
| 边界 | 不做尸体、不做 PK、不做逐回合独立事件 |

### P3｜成长：属性 / 技能 / 装备（3 agent × 4 轮 = 12 AR）

| 项 | 内容 |
|---|---|
| 玩家看见 | `score` 八项属性（膂力/悟性/根骨/身法/耐力/灵性/定力/胆识）；`skills` 显示“基本剑法 12/100”；`practice` 消耗体力涨熟练度；换剑后打同一只怪伤害变化 |
| 能做什么 | `skills / practice / learn / wield / remove / score` |
| 涉及模块 | `attrs/skill_levels/skill_xp`、`effects`（`attr_add/skill_xp_add`）、`actions/skill.py`、`actions/equipment.py`、战斗公式、内容技能表 |
| 验收（可执行） | ① 8 项属性入库且可回放（`kar/per` 延后）；② 3 条技能线可从 0 练到 ≥10；③ 装备前后同怪伤害有可观测差异；④ `test_xkx_skill_growth.py` 绿；⑤ 属性/技能全部来自世界状态，前端不计算 |
| 边界 | 技能上限/门派限制先做 2–3 条线；知识类技能可延后 |

### P4｜门派、师徒、任务（3 agent × 3 轮 = 9 AR）

| 项 | 内容 |
|---|---|
| 玩家看见 | 能进红花会、拜师；师门任务面板；完成任务给贡献；`score` 显示门派/师父 |
| 能做什么 | `join 红花会 / apprentice <master> / quest list / quest accept / quest complete / score` |
| 涉及模块 | `Faction`、`sects.yaml`、`player_sect.py`、`quests.py`、`quest_accept` 前置、内容包 |
| 验收（可执行） | ① 至少 1 个门派可加入；② 至少 3 位师父可拜；③ 5 条师门任务可接可交；④ 贡献值可查且入库；⑤ `test_xkx_sect_quest.py` 绿 |
| 边界 | 门派技能传承先做 1–2 门；门派战/门规延后 |

### P5｜扩到苏州 + 压测（3 agent × 3 轮 = 9 AR）

| 项 | 内容 |
|---|---|
| 玩家看见 | 301 房、7 商店、22 位师父、7 个主动攻击 NPC；`map` 显示区域图；跨区移动不断线 |
| 能做什么 | `map / go` 跨区、跨区任务、跨区战斗 |
| 涉及模块 | 内容包 + 抽取器（加 suzhou）、读视图/价格缓存、压测脚本 |
| 验收（可执行） | ① 可达房间 ≥295/301，悬空出口=0，不可达清单有 owner；② 100 席读 P95 记录在案；③ 2 日事件量/唤醒量与基线表对齐；④ `clone()`/`state.hash()` 实测值写入报告；⑤ `seq_gap=0`；⑥ 全量测试绿 |
| 边界 | 千房间不在本阶段；是否继续扩由老板按数据拍板 |

**切片总计：4 + 18 + 18 + 12 + 9 + 9 = 70 AR ≈ 35 人日；3 agents ≈ 3.9 周，加缓冲 5–6 周。**

---

## 4. 数据管线：LPC → JSON

### 4.1 抽取器结构

```
tools/xkx_extract/
  index.py        # 稳定遍历、inherit 链解析
  parse.py        # set("long"/"exits"/"objects"/"inquiry") 等
  normalize.py    # 房间/物品/NPC/商店/对话 → 统一 JSON
  validate.py     # 规则校验
  emit.py         # canonical JSON 输出 + manifest
  diff.py         # 旧/新产物结构化 diff
```

- 输入固定为 `fluffos/xkx100 @ c1b87db`；仓库不提交该源码，只提交抽取产物与 manifest（license 决策见 §7）。
- 输出 `content/packs/xkx-slice/`：
  - `rooms.json`（id/name/long/exits/objects/flags）
  - `items.json`（id/name/desc/price/category/equip_slot）
  - `npcs.json`（id/name/desc/title/skills/attrs/inquiry/aggressive/vendor）
  - `shops.json`（stock/price/markup）
  - `quests.json`（模板/发布人/目标/奖励）
  - `manifest.json`（source commit、areas、per-file sha256、content_hash）
- CLI 约定：
  ```bash
  python tools/xkx_extract.py \
    --src /tmp/xkx-research/fluffos-xkx100 \
    --areas hangzhou,jiaxing,shiliang \
    --out content/packs/xkx-slice \
    --report reports/xkx-extract/$(date +%Y%m%d-%H%M%S) \
    --strict
  ```

### 4.2 幂等、可重跑、可 diff

| 要求 | 实现 |
|---|---|
| 幂等 | 遍历按路径排序；JSON `sort_keys=True`、固定分隔符；产物不含时间戳；重复跑两次 `content_hash` 必须相同 |
| 可重跑 | `--verify` 模式：重抽到临时目录并逐文件 sha256 比对；任何差异非零退出 |
| 可 diff | `diff.py` 输出 `added/removed/changed` 三类，按 id 排序；`approved_changes.yaml` 白名单内的变化才允许通过 |
| 可追溯 | `manifest.json` 记录 source commit、区域列表、每文件 sha256、规则版本 |
| 失败可读 | `diagnostics.jsonl` 每行含 `path/line/rule/severity/snippet`；`summary.json` 按规则计数；每规则抽 20 条样本到 `samples/` |

### 4.3 解析覆盖率目标

已知基线（全仓）：房间长描述 5,464/5,683 = **96.1%**；有出口 5,560/5,683 = **97.8%**；物品描述 85%；NPC 描述 90%；对话文本 1,776/2,784 = **64%**（其余是函数，不可静态搬）。

**目标不是 100%，而是“不静默丢失”**：

- 房间：`long` 覆盖率 ≥ 96.1%，出口 ≥ 97.8%；切片不得低于全仓比例。
- 物品/NPC：≥ 85% / 90%；不足的条目保留 id 与占位描述，并写 `W_DESC_MISSING`。
- 对话：字符串主题 100% 搬；函数主题转成 `fallback` 文本 + `I_DIALOGUE_FUNC`，不丢主题键。
- 每个切片发布门槛：`errors=0`，`warnings` 有清单且有 owner。

### 4.4 校验规则

| 规则 | 级别 | 处理 |
|---|---|---|
| `E_DANGLING_EXIT` 出口指向不存在房间 | error | 阻断发布；补目标或做 `closed` 边界 |
| `E_DUP_ROOM_ID` 重复 id（路径/id 冲突） | error | 阻断；人工改名 |
| `E_MISSING_ITEM_REF` objects 引用不存在物品 | error | 阻断；补模板或删声明 |
| `E_MISSING_NPC_REF` objects 引用不存在 NPC | error | 阻断 |
| `E_SHOP_STOCK_MISSING` 商店库存物品不存在 | error | 阻断 |
| `E_QUEST_GIVER_MISSING` 任务发布人不存在 | error | 阻断 |
| `E_ENCODING` 非 UTF-8 / 乱码 | error | 阻断 |
| `W_NO_LONG` 无长描述 | warn | 占位 + 诊断 |
| `W_NO_EXIT` 孤立房间 | warn | 标记，人工确认 |
| `W_ASYMMETRIC_EXIT` 单向出口 | warn | 保留单向语义，不自动补反向 |
| `W_BOUNDARY_EXIT` 出口指向切片外 | warn | 生成 `closed` stub |
| `I_DIALOGUE_FUNC` 对话是函数 | info | 转 fallback 文本 |
| `I_PRICE_ZERO` 价格为 0 或缺失 | info | 用品类默认价，记录 |

### 4.5 失败样本报告格式

```json
{"path":"d/hangzhou/honghua/yu.lpc","line":42,"rule":"W_NO_LONG","severity":"warn",
 "snippet":"set(\"short\",\"红花会堂主\");","owner":"content-p1"}
```

- 每次抽取产出 `summary.json`：`files_scanned / rooms / items / npcs / errors / warnings / content_hash`。
- CI 只允许 `errors=0`；warnings 数量与上版对比，增长超过 10% 需要说明。

---

## 5. 战斗与属性设计

### 5.1 属性表（来自 `feature/attribute.lpc:9-99`）

| 属性 | xkx 字段 | 作用 | 第一版 |
|---|---|---|---|
| 膂力 | `str` | 出手重；伤害加成 | ✅ |
| 悟性 | `int` | 学习/读书速度 | ✅（成长用） |
| 根骨 | `con` | 气血上限/恢复 | ✅ |
| 身法 | `dex` | 闪避 | ✅ |
| 耐力 | `sta` | 内力恢复 | ✅ |
| 灵性 | `spi` | 可学武功数量/速度 | 🟡 延后到 P3 |
| 福缘 | `kar` | 机缘/掉落 | 🟡 延后 |
| 容貌 | `per` | 对定力低者震慑 | ❌ 第一版不做 |
| 定力 | `cps` | 减少破绽/被反击 | ✅（战斗修正） |
| 胆识 | `cor` | 提高出手成功率 | ✅（战斗修正） |

派生值：`combat_exp`、`potential`、`learned_points`、`qi`（气血）、`jing`（精气）、`neili`（内力）、`jiali`（加力）。
第一版 `Entity` 新增：`attrs: dict[str,int]`、`hp/max_hp`、`combat_exp`、`skill_levels/skill_xp`（新字段）；旧 `skills: dict[str,float]` 继续服务现有 NPC，不做破坏性迁移。

### 5.2 回合模型（选定方案）

**选定：一次 `fight` 动作原子结算，最多 20 回合，写 1 条事件。**

理由：

1. 世界 1 tick = 10 分钟。若每回合 1 tick，20 回合 = 200 分钟，荒谬；若每回合 1 条事件但不推进 tick，则同一 tick 内 20 条事件会放大 20 倍 `clone()` 与写锁。
2. 一次动作 1 条事件 = 1 次 `clone()` + 1 次 append，写锁占用最小；事件内 `rounds[]` 保存逐回合日志，客户端按 `seq` 播放，观感仍是“逐回合”。
3. `replay_into()` 只重放 Effect，不重跑战斗；`rounds[]` 作为 Effect 载荷可完整还原战报。
4. 战斗动作耗 **1 tick**（不是默认 6 tick）：一场遭遇战占一个 10 分钟时间片，既防止“打一场跳一小时”，也防止无限刷战斗卡时间。实现上需要给 `session._advance()` 增加“按动词覆盖 `tick_step`”的分支（`fight=1`），这是对核心的一处小改，必须单独评审。
5. 后续若要“真逐回合”，可升级为同 tick 多事件（`A-round-events`），但那是 P2 之后的选项，不是第一版。

回合循环（伪代码）：

```
for round in 1..20:
  attacker = 先手方（玩家或怪，按 dex+速度，平手按 intent 提交顺序）
  A 出手 → 判定闪避 → 判定招架 → 计算伤害 → 扣 hp
  若目标 hp<=0：结算死亡/掉落，break
  目标反击（若存活）→ 同上
  每回合写一条 combat_log 行
```

### 5.3 公式来源与整数化翻译

来源：`adm/daemons/combatd.lpc`
- `skill_power()`：197–242 行
- 命中/闪避：328–354 行
- 招架：387–406 行
- 伤害：436–558 行
- 破防/受伤：560–591 行

翻译规则（全部整数运算，避免浮点累积差异）：

```
level = skill_level(attack_skill) + apply_attack      # 上限 800
power = (level // 6) * level * level
exp   = combat_exp
ap    = clamp((power + exp) // 80 * (str + str_base), 1, 200000000)
dp    = clamp((power_d + exp) // 80 * (dex + dex_base), 1, 200000000)
# 闪避：random(ap+dp) < dp
if prng.below(ap + dp) < dp: dodge
# 招架：random(ap+pp) < pp
if prng.below(ap + pp) < pp: parry
damage = weapon_damage or unarmed_damage
if damage > 300: damage = 300 + prng.below(damage - 300)
damage = (damage + prng.below(damage)) // 2
damage -= armor_roll
damage += str_bonus_roll
while prng.below(defense_exp) > my_exp: damage -= damage // 3; defense_exp //= 2
damage = damage * pp_int // 1000   # pp_int 1000..1500
```

- `prng.below(n)` 等价 LPC `random(n)`；禁止 `random()` 无参、禁止 `time()`。
- `power_point` 用千分位整数（1000..1500），最后 `damage = damage * pp_int // 1000`。
- 所有 HP/伤害/经验/掉落数量为整数。

### 5.4 随机与回放

- 随机源：`Writer.submit()` 已按 `(seed, seq, salt="action:{verb}")` 建流（`core/rng.py`）。
- 战斗内顺序取数：`salt="action:fight"`，每回合按固定顺序取 `dodge → parry → damage → armor → exp → wound`；取数顺序写在测试里锁死。
- 禁止在 `Action.apply()` 之外取随机；禁止用实体 id 哈希做隐式随机（会随扩容漂移）。
- 掉落：`loot_table` 的权重与数量全部从同一 PRNG 流取；记录 `roll` 值到事件 Effect，便于审计。
- 回放：`replay_into()` 不重跑动作，只重放 Effect；因此“回放一致”不依赖 PRNG 重算。

### 5.5 事件量与写锁影响

| 方案 | 每场事件 | 每场 clone/append | 20 回合战斗事件体积（估） | 100 场/日磁盘（估） |
|---|---:|---:|---:|---:|
| **A-atomic（选）** | 1 | 1 | 2–6 KB | 0.2–0.6 MB |
| A-round-events | 20 | 20 | 40–120 KB | 4–12 MB |
| B-live 分叉 | 20+ | 20+ | 40–120 KB | 4–12 MB + 调度 |

结论：A-atomic 把写锁占用压到 1/20，事件量压到 1/20；代价是客户端要从 `rounds[]` 播放，不是服务端逐条推。

### 5.6 HP 归零 / 死亡 / 复活 Effect 原语

新增 Effect（全部走 `EFFECT_OPS` 白名单）：

| Effect | 语义 |
|---|---|
| `hp_add {entity_id, delta}` | 增减气血，clamp 到 `[0, max_hp]` |
| `hp_set {entity_id, value}` | 仅创世/复活用 |
| `entity_status {entity_id, status}` | `alive/dead` |
| `loot_create {entity_id, item_id, qty}` | 掉落入包 |
| `schedule_add {id, tick, action}` | 刷新/复活定时 |
| `player_presence {player_id, presence}` | 死亡进 custody，复活回 active |
| `skill_xp_add {entity_id, skill, delta}` | 战斗/练功成长 |
| `attr_add {entity_id, attr, delta}` | 属性成长（受上限约束） |

死亡流程：

1. `hp_add` 到 0 → `entity_status=dead`。
2. 玩家：`presence=custody`，记录 `death_tick/location`。
3. 怪物：`loot_create` 掉落（无尸体）；`schedule_add` 在 `respawn_ticks` 后由 `system_spawn` 重建。
4. 玩家复活：`hp_set=max_hp*0.5`、`entity_status=alive`、`presence=active`，位置回客栈/绑定点。
5. 全流程 1 条事件（或 2 条：死亡 + 复活由系统 tick 触发），都可回放。

---

## 6. 确定性红线

### 6.1 红线是否还成立

**成立，但必须按“内容包作用域”理解。**

- 现有 `tests/test_p3e_golden.py` 锁的是**旧内容包**的无玩家世界：同 seed 的 `events_total / state_final_hash / event_hash` 逐字节一致。
- 新增 xkx 切片是**新内容包、新 `content_hash`、新基线**；它不会让旧基线漂移，只要：
  1. 新字段默认空，且 `WorldState.to_dict()` 条件输出（沿用 `presence/equipped` 模式）。
  2. 新系统动作（`system_spawn`）在没有 `spawns` 时**不产生任何事件**；旧包 `spawns` 为空。
  3. 战斗/刷新模块禁止 `time()`、`random()`、进程内缓存。
  4. 新内容包启动时使用独立 `events.sqlite`，不与旧世界混档。

### 6.2 怎么测（必须绿）

| 测试 | 内容 | 失败即阻断 |
|---|---|---|
| `tests/test_p3e_golden.py`（既有） | 旧包 2 日 seed 世界，事件数/state hash/event hash | 是 |
| `tests/test_xkx_legacy_hash.py`（新增） | 旧包在启用新代码后跑 2 日，`state.hash()`/`full_hash()` 与提交前快照逐字节相等 | 是 |
| `tests/test_xkx_new_baseline.py`（新增） | 新包同 seed 跑两遍，`events_total`/`state hash`/`event hash` 完全一致 | 是 |
| `tests/test_xkx_replay_equivalence.py`（新增） | 实时跑 N 日后 `replay_into()`，state hash 与实时一致 | 是 |
| `tests/test_xkx_no_hidden_random.py`（新增） | `combat/spawn` 模块导入时禁用 `random/time`，跑 1 日不报错 | 是 |
| `tests/test_xkx_spawn_noop.py`（新增） | `spawns` 为空时 `system_spawn` 不写事件 | 是 |
| `tests/test_xkx_combat_determinism.py`（新增） | 固定 seed/固定双方属性，重复 100 场战斗逐场伤害序列一致 | 是 |

### 6.3 操作纪律

- 新字段一律 `field: {default}` 且 `to_dict` 里“非空才写”；禁止“先加字段再补条件”。
- 每次改 `state/effects` 必须先跑旧 golden，再跑新基线。
- PR 描述必须贴：旧包 `events_total` 与 `state_final_hash` 前后对比；新包两次 run 的 hash 对比。
- 禁止在 `Action.apply()` 里读墙钟或调用 `random`/`uuid`；ID 必须由 `(spawn_id, generation)` 或事件序号派生。

---

## 7. 决策点（请老板拍板）

| # | 决策 | 选项 | 我的建议 | 截止 |
|---|---|---|---|---|
| 1 | 范围 | 全量 / 切片 | **切片 A（195 房）**；全量作为 P5 后的 gate | 立项前 |
| 2 | 与现有世界关系 | 混合 / 独立包 | **独立内容包 + 独立 events.sqlite**；不混档 | 立项前 |
| 3 | License / 公开 | 公开 / 内部 / 重写 | **内部原型**；公开前必须取得书面授权或重写文案 | 立项前 |
| 4 | 逐秒心跳 | 做 / 不做 | **不做**；战斗原子结算 + 客户端播放 | 立项前 |
| 5 | 战斗粒度 | 原子 1 事件 / 每回合 1 事件 | **原子 1 事件**（20 回合上限） | P2 前 |
| 6 | 物品实例化 | 做 / 不做 | **不做**；同质计数；容器单独立项 | P1 前 |
| 7 | PK / 偷抢 | 做 / 不做 | **不做**；后续做同意决斗 | P2 前 |
| 8 | 尸体持久化 | 做 / 不做 | **不做**；击杀直接入包 | P2 前 |
| 9 | 技能规模 | 362 / 8–12 | **8–12 条**；按门派扩展 | P3 前 |
| 10 | 门派规模 | 31 / 1–3 | **1 个（红花会）**；P4 验证后再加 1–2 个 | P4 前 |
| 11 | 任务规模 | 2,017 / 20–30 | **20–30 模板 + 生成器** | P4 前 |
| 12 | 预算上限 | 6 周 / 更长 | **6 周切片 gate**；P5 后再批全量 | 立项前 |
| 13 | LLM 用法 | 全量 / 规则优先 | **规则优先**；LLM 只做 spotlight 风味文本 | P1 前 |
| 14 | 千房间 | 现在 / 以后 | **以后**；P5 用实测数据再定 | P5 后 |

---

## 8. 风险与开放问题

1. **License 是硬阻塞**：无 LICENSE 不等于“可以随便用”；公开分发需要授权或重写。内部原型不受影响，但上线前必须解决。
2. **抽取覆盖率不是 100%**：房间 96.1%/出口 97.8% 是已知上限；剩余缺口要么人工补，要么接受占位。**不能用“优化中”掩盖缺口数量。**
3. **战斗平衡未验证**：公式翻译只保证“形状一致”，不保证手感；P2 必须用 100 场固定 seed 战斗做伤害分布对比，再调参。
4. **事件量/磁盘**：原子战斗可控；若后续改成逐回合事件，必须重跑 `docs/CAPACITY.md` 的口径。
5. **内容与引擎耦合**：`content_hash` 变化会改变新基线；旧世界必须保持独立，禁止在同一 DB 上换包。
6. **文本风格**：xkx 文案有时代痕迹；第一版保留原文，是否润色由产品决定，不在技术范围。

---

## 9. 证据索引

| 主题 | 位置 |
|---|---|
| 引擎可行性 | `specs/xkx-parity/engine-feasibility.md` |
| 内容盘点 | `specs/xkx-parity/content-inventory.md` |
| 房间/出口统计 | 本地 `fluffos-xkx100`：5,683 / 12,611 / 87 |
| 属性定义 | `feature/attribute.lpc:9-99` |
| 战斗公式 | `adm/daemons/combatd.lpc:197-242, 328-354, 387-406, 436-558, 560-591` |
| 技能成长 | `feature/skill.lpc:172-204` |
| 心跳 | `inherit/char/char.lpc:47-135` |
| 刷新配置 | `config.ini:71,76,149-150` |
| 商店 | `inherit/room/shop.lpc:65-88` |
| 对话 | `cmds/std/ask.lpc:87` |
| 移动 | `cmds/std/go.lpc:96,342` |
