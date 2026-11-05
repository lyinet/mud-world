# 委托板保供：根因、修复与 10 日验收

> 结论：委托板断供的根因不是 giver 没钱，而是**全局过期上限**在日界集中过期后把
> `system_quest_post` 的全部模板一起封停；随后存量 open 单继续过期，板面归零并形成
> 周期性死锁。修复在 `SystemQuestPostAction` 增加**每发布点每世界日 1 张保底单**
> （确定性轮转、优先采购/跑腿），giver 现金不足时由 `treasury:region` 走真实
> `transfer(reason=quest_reward_advance)` 垫付，禁止 `mint`。`content/**` 未改，
> `content_hash` 关闭态 `7ec670c1…` / 开启态 `3fd99946…` 不变。
>
> 证据 run：`runs/audit-r2`（修复前故障世界）、`runs/service-ambient-10d{-b}`（真实服务
> + ambient，seed 42，4x 配置）、`runs/fix-floor-30d`（30 天供给/需求平衡补充）、
> `runs/floor-player-proof`（玩家接单交付）、`runs/floor-advance-proof`（金库垫付）。

---

## 1. 根因与证据

### 1.1 每世界日、每发布点的新增单数（修复前 `audit-r2`）

统计口径：`events.sqlite` 中 `system_quest_post` 的 `quest_create` effect，按
`created_tick // 144` 归日，按 giver 当时所在地点归发布点。

| 世界日 | academy | escort_agency | ferry | inn | market | pharmacy | smithy | temple | 合计 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 3 | 3 | 5 | 8 | 2 | 2 | 4 | 27 |
| 2 | 0 | 1 | 5 | 1 | 6 | 1 | 1 | 1 | 16 |
| 3 | 0 | 2 | 5 | 3 | 2 | 4 | 0 | 3 | 19 |
| 4 | 0 | 0 | 3 | 2 | 7 | 1 | 0 | 3 | 16 |
| 5 | 0 | 0 | 3 | 0 | 2 | 2 | 0 | 4 | 11 |
| 6 | 0 | 1 | 3 | 2 | 5 | 4 | 1 | 4 | 20 |
| 7 | 0 | 0 | 2 | 0 | 1 | 0 | 1 | 0 | 4 |
| 8 | 0 | 0 | 3 | 2 | 5 | 4 | 1 | 3 | 18 |
| 9 | 1 | 0 | 0 | 2 | 4 | 4 | 0 | 3 | 14 |
| 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 11 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 14 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| 15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |

第 7 日起集市降到 1 单、药铺归零；**第 10 日起 8 个发布点全部 0 单**，与
`system_quest_post` 返回 `[]` 的现象一致（旧 run 在 tick 1705/1740/1741 均为 0 单）。

### 1.2 发布被抑制的具体原因（逐模板复算）

对每个 `system_quest_post` tick 的 27 个模板逐一复算前置条件，触发次数：

| 原因 | 触发次数 |
|---|---:|
| 全局过期上限（retained expired ≥ 20） | **324** |
| 在途 open/accepted（同模板已有单） | 191 |
| giver 现金不足（reward > 钱包） | 3 |

现金不足仅出现 3 次，且全部发生在第 24–26 日的 `zhuo_bufan`（钱包 0 文、reward 22 文）——
**不是第 7–15 日断供的原因**。第 7–15 日被抑制的模板中，giver 现金余额 268–4937 文，
reward 仅 8–36 文，没有任何一例因现金不足被抑制。

### 1.3 逐条被抑制的 giver（第 9 日 tick 1159，发布 0 单）

| 模板 | giver | 现金 | reward | 抑制原因 |
|---|---|---:|---:|---|
| commission_herb_sorting | han_xinglin | 476 | 14 | 在途 + 全局过期上限 |
| commission_stonework | shi_gandang | 367 | 12 | 在途 + 全局过期上限 |
| daily_escort_short | pei_wujiu | 1509 | 22 | 在途 + 全局过期上限 |
| daily_ferry_fish_money | ruan_xiaoluo | 640 | 10 | 在途 + 全局过期上限 |
| daily_ferry_message | qin_wangchuan | 351 | 10 | 全局过期上限 |
| daily_ferry_rope | ruan_xiaoluo | 640 | 16 | 在途 + 全局过期上限 |
| daily_inn_firewood | lu_jiujin | 343 | 12 | 在途 + 全局过期上限 |
| daily_inn_millet | wen_xiaoman | 348 | 18 | 在途 + 全局过期上限 |
| daily_market_inquiry | ruan_qingfu | 669 | 9 | 在途 + 全局过期上限 |
| daily_smithy_bellows | shen_yanzhou | 1066 | 15 | 在途 + 全局过期上限 |
| debt_duan_to_jin | jin_mancang | 4937 | 25 | 全局过期上限 |
| debt_lu_to_song | song_henian | 3167 | 8 | 全局过期上限 |
| debt_shi_to_lu | lu_jiujin | 343 | 8 | 全局过期上限 |
| errand_academy_notice | yan_shouyi | 1586 | 8 | 在途 + 全局过期上限 |
| errand_market_ledger | jin_mancang | 4937 | 10 | 全局过期上限 |
| errand_market_sweet | meng_jiuniang | 1309 | 9 | 全局过期上限 |
| errand_silk_sample | liu_sanniang | 850 | 8 | 在途 + 全局过期上限 |
| escort_iron_short | pei_wujiu | 1509 | 12 | 在途 + 全局过期上限 |
| escort_medicine_temple | zhuo_bufan | 268 | 22 | 在途 + 全局过期上限 |
| inquiry_academy_lecture | cao_banxian | 1173 | 8 | 在途 + 全局过期上限 |
| inquiry_luo_marriage | meng_jiuniang | 1309 | 8 | 在途 + 全局过期上限 |
| procure_charcoal | song_henian | 3167 | 36 | 在途 + 全局过期上限 |
| procure_mugwort | yan_shouyi | 1586 | 28 | 在途 + 全局过期上限 |
| tutorial_first_buy | su_hexiang | 813 | 14 | 在途 + 全局过期上限 |
| tutorial_first_sale | cao_banxian | 1173 | 14 | 在途 + 全局过期上限 |
| tutorial_meet_sanniang | jin_mancang | 4937 | 10 | 在途 + 全局过期上限 |
| tutorial_silk_sample | liu_sanniang | 850 | 12 | 在途 + 全局过期上限 |

合计 27 个模板全部被抑制，**无一条因现金不足**。

### 1.4 第 13 日 tick 1741（发布 0 单，全部命中全局上限）

同一批 giver 的现金 146–4851 文（`zhuo_bufan` 146、`lu_jiujin` 423、`shi_gandang` 293、
`song_henian` 3441、`jin_mancang` 4851 等），reward 8–36 文；27 个模板的抑制原因
**全部包含“全局过期上限”**，其中 0 个包含“在途”（在途单已在前一日批量过期）。
这说明封停不是临时性在途阻塞，而是全局计数持续 ≥20 造成的**整板封停**。

### 1.5 机制链条

1. 每个模板 1–2 天期限，NPC 不接单时在日界被 `quest_fail`，进入 `expired` 保留期（7 天）。
2. 大量未接单在日界集中过期 → `retained_expired` 很快 ≥ `expired_cap_global=20`。
3. `SystemQuestPostAction` 对**所有模板**命中 `sum(expired) >= 20` 直接 `continue`，
   常规发布 0 单；保留期内计数不下降，持续 7 天。
4. 板面存量 open 单继续过期 → 板面归零；7 天后 prune 掉旧记录，计数回落，短暂恢复后
   再次集中过期 → 周期性死锁（`runs/audit-ambient` 62 天 run 第 16–23 日 0 单、第 24 日
   prune 后一次补 24 单，即该循环）。

因此：**giver 现金、模板配额都不是根因；根因是健康度门槛（全局过期上限）误伤了供给端。**

---

## 2. 修复逻辑

改动仅 `src/wsim/actions/quests.py`：常规发布路径保持不变，新增 `_floor_posts` 保底路径。

### 2.1 规则（确定性、可解释）

- **发布点**：某个地点的 giver 存活，且模板 `require_reputation <= 0`（新手可接）。
- **触发条件**：该发布点当日**没有任何“无声望门槛的 open 单”**（含本 tick 常规路径
  刚创建的单）时，补 1 张。
- **候选与轮转**：候选按模板 id 排序，优先 `category in ("procurement", "errand")`；
  起点 `start = day % len(group)`，按 `group[start:] + group[:start]` 取第一张可发单。
  **纯整数取模，无随机数**，同 seed/day/state 必得同一模板。
- **门槛**：保底单跳过全局/模板过期上限与完成率门槛（这两项用于防刷屏，不能反噬供给），
  但仍要求 giver 存活、同模板无 open、`giver_item` 有货、非 repeatable 模板无历史记录。
- **全在途兜底**：若该点所有候选模板都已有 `accepted` 在途（正被送），允许 repeatable
  模板再开一张，避免“都在送、板面为空”；同一 tick 每点仍只补 1 张。
- **可审计**：保底单 `quest_create` 带 `floor: true` 与 `advance_money`，可与常规单区分。

### 2.2 金库垫付（不造钱）

- giver 钱包不足 reward 时，差额 `advance = reward - balance` 由 `treasury:region`
  通过真实 `transfer` 支付：
  `src=treasury:region, dst=wallet:<giver>, reason=quest_reward_advance, quest, template, day`。
- 金库余额不足以覆盖差额时，**放弃该点当日保底**，绝不 `mint`、不记欠账。
- 因此垫付全部可追溯到 `transfer` 事件，`accounts_sum = minted − burned` 恒成立。

### 2.3 过期参数为什么不动

`content/**` 数值冻结，不改 `deadline_days` / `expired_cap_*`。原因是只调参数无法证明稳态：
按当前 2 天期限与每日集中过期，保留期 7 天内同一模板最多可积累 4 张过期记录，仍高于
模板上限 3；把全局上限调高又会堆满板面。改为“**每点每日保底 1 张**”后，不需要依赖
过期阈值：当天补的单期限 ≥1 天，即使无人接，板面最小可接数由保底规则本身保证 ≥1；
若被接走，次日仍会补。即供给下界由发布规则而非过期窗口决定。

---

## 3. 10 个世界日验收（真实服务 + ambient，seed 42）

### 3.1 环境

- `runs/service-ambient-10d` / `runs/service-ambient-10d-b`
  - 真实 `wsim.api` HTTP 服务进程 + `WorldSession` + 同进程 `AmbientScheduler`
  - `seed=42`、`tick_step=6`、ambient **speed 4.0（4x 配置）**
  - 以 `start_ambient` → `pause` → 同步 `step(144)` × 10 推进到 tick 1440；
    不使用墙钟等待（真实 4x 墙钟下 6 秒/tick，10 天需约 36 分钟），但事件流仍由真实
    `AmbientScheduler`/`Writer` 产生，玩家 API 走真实 HTTP。
- `content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`。

### 3.2 逐日数据

| 世界日 | 新增（常规+保底） | 接单 | 完成 | 过期 | 板面 open min/med/max | 可接 min/med/max | 保底 | 垫付 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 27 (27+0) | 25 | 22 | 0 | 0/14/27 | 0/13/26 | 0 | 0 |
| 2 | 20 (19+1) | 17 | 16 | 0 | 2/15/22 | 2/14/21 | 1 | 0 |
| 3 | 16 (16+0) | 12 | 11 | 0 | 5/11/21 | 5/11/20 | 0 | 0 |
| 4 | 14 (13+1) | 11 | 10 | 2 | 7/14/21 | 7/13/20 | 1 | 0 |
| 5 | 14 (13+1) | 13 | 14 | 2 | 8/14/22 | 7/13/21 | 1 | 0 |
| 6 | 18 (17+1) | 14 | 13 | 3 | 6/15/24 | 5/14/23 | 1 | 0 |
| 7 | 18 (18+0) | 11 | 9 | 5 | 5/19/23 | 5/19/22 | 0 | 0 |
| 8 | 10 (10+0) | 11 | 12 | 1 | 10/15/21 | 10/15/20 | 0 | 0 |
| 9 | 16 (16+0) | 8 | 6 | 4 | 6/20/22 | 6/19/21 | 0 | 0 |
| 10 | 3 (0+3) | 3 | 2 | 5 | 9/10/14 | 8/9/13 | 3 | 0 |

合计：新增 156、接单 125（80.1%）、完成 115（对当日接单 92.0%）、过期 22。
完成数可含前一日接单的跨日交付，故“完成/接单”仅作流量参考。

### 3.3 每日日界各发布点“可接单”数（第 7–10 日）

| 世界日 | academy | escort_agency | ferry | inn | market | pharmacy | smithy | temple | 最小 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 | 2 | 1 | 2 | 3 | 7 | 1 | 3 | 3 | **1** |
| 8 | 2 | 2 | 1 | 3 | 8 | 0* | 1 | 3 | **1** |
| 9 | 2 | 2 | 2 | 3 | 7 | 1 | 1 | 3 | **1** |
| 10 | 1 | 1 | 1 | 1 | 4 | 1 | 1 | 1 | **1** |

\* 第 8 日 `su_hexiang` 不在药铺，药铺当天没有 giver，因此不在“发布点”集合内；
不是“有 giver 但发不出单”。第 7/9/10 日药铺均有 ≥1 张可接单。
集市（market）7/8/9/10 日分别为 7/8/7/4 张，河渡（ferry）2/1/2/1 张，均 ≥1。
**结论：第 7–10 日每个有 giver 的发布点每天都有 ≥1 张可接单，满足保底要求。**

### 3.4 守恒

- `accounts_sum 45691`、`minted 47450`、`burned 1759`、`minted - burned 45691`，**delta = 0**。
- `treasury:region` 期末 8074；本 10 天未出现 giver 现金不足，`quest_reward_advance` 0 笔。
- 垫付另以上一节构造场景验证（见 §4）。

### 3.5 确定性

- 同 seed（42）两次真实服务 run：3851 events、max_tick 1440，
  `EventStore.event_hash = 655bd929e6fd28b4769f173901e731b07f8e731f3bda1761aedc00a528f8e396`
  两次完全一致。
- headless 10 天 `runs/fix-floor-10d-a/-b` 亦一致：
  `a586e3f7a0ce997844200f7263692a1ba9ed708c9987a79d00616afb891dc74c`，3968 events。

---

## 4. 玩家侧实测（原本 0 单的世界日接单并完成）

`runs/floor-player-proof`：把 `audit-r2`（修复前在第 12 日仍 0 单）裁到 tick 1727
（板面 0 单），启动真实 HTTP API 并 `enter` 跨日界 tick 1728（第 12 日）：

```
4035 @1728 system_tick day_boundary=true
     quest_create quest:daily_ferry_fish_money:1728      floor=true
     quest_create quest:escort_medicine_temple:1728      floor=true
     quest_create quest:errand_silk_sample:1728          floor=true
     quest_create quest:daily_inn_millet:1728           floor=true
     quest_create quest:daily_ferry_message:1728        floor=true
4036 @1728 quest_accept  {"quest":"quest:errand_silk_sample:1728"}
     → 钱包 300
4038 @1734 move         {"to":"pharmacy"}                → 到苏合香处
4040 @1740 trade        {"target":"su_hexiang"}          → 交易动作完成
4042 @1746 quest_deliver {"quest":"quest:errand_silk_sample:1728"}
     transfer 8 文 src=wallet:liu_sanniang dst=wallet:player:u_demo reason=quest_reward
     quest_complete
     → 钱包 308
```

事件链：`quest_accept`(4036) → `move`(4038) → `trade`(4040) → `quest_deliver`+`quest_complete`(4042)，
钱包 300 → 308（+8 文），报酬由 giver 柳三娘钱包真实支付。

---

## 5. 金库垫付场景与对账

`runs/floor-advance-proof`：以 `audit-r2` 裁到 tick 143，追加 `proof_churn_board` 把
所有 giver 钱包 burn 到 0、把板面 open 单全部 `quest_fail`（构造“现金不足 + 空板”），
再 `enter` 跨日界到 tick 149。7 个发布点各补 1 张保底单，逐笔垫付：

| quest | giver | 垫付（文） |
|---|---|---:|
| quest:escort_iron_short:149 | pei_wujiu | 12 |
| quest:daily_ferry_message:149 | qin_wangchuan | 10 |
| quest:daily_inn_millet:149 | wen_xiaoman | 18 |
| quest:errand_silk_sample:149 | liu_sanniang | 8 |
| quest:tutorial_first_buy:149 | su_hexiang | 14 |
| quest:daily_smithy_bellows:149 | shen_yanzhou | 15 |
| quest:procure_mugwort:149 | yan_shouyi | 28 |

每笔均为 `transfer(src=treasury:region, dst=wallet:<giver>, reason=quest_reward_advance,
quest=..., template=..., day=1)`，`quest_create.advance_money` 与之一一对应，合计 105 文。
对账：`accounts_sum = minted − burned`（`delta=0`），无 `mint`、无负余额；金库不足时该点
当日不补单（单测 `test_floor_skips_when_treasury_cannot_cover` 覆盖）。

---

## 6. 供给/需求平衡（30 天补充，`runs/fix-floor-30d`）

| 世界日 | 新增（常规+保底） | 接单 | 完成 | 过期 | 板面 open min/med/max | 保底 |
|---:|---:|---:|---:|---:|---:|---:|
| 11 | 3 (0+3) | 3 | 2 | 2 | 5/8/10 | 3 |
| 12 | 4 (0+4) | 3 | 4 | 5 | 1/7/9 | 4 |
| 13 | 6 (0+6) | 5 | 5 | 0 | 1/7/7 | 6 |
| 14 | 5 (0+5) | 5 | 5 | 1 | 1/5/7 | 5 |
| 15 | 6 (0+6) | 3 | 2 | 1 | 1/6/7 | 6 |
| 16 | 19 (19+0) | 1 | 2 | 0 | 3/22/22 | 0 |
| 17 | 2 (2+0) | 3 | 2 | 3 | 17/21/23 | 0 |
| 18 | 6 (6+0) | 4 | 4 | 16 | 3/23/23 | 0 |
| 19 | 4 (0+4) | 4 | 5 | 0 | 3/6/7 | 4 |
| 20 | 4 (0+4) | 2 | 2 | 2 | 3/7/7 | 4 |
| 21 | 4 (0+4) | 2 | 2 | 1 | 3/7/7 | 4 |
| 22 | 3 (0+3) | 3 | 3 | 2 | 2/6.5/7 | 3 |
| 23 | 5 (0+5) | 2 | 2 | 1 | 2/7/7 | 5 |
| 24 | 3 (0+3) | 3 | 3 | 1 | 3/6/7 | 3 |
| 25 | 4 (0+4) | 3 | 3 | 3 | 1/5/7 | 4 |
| 26 | 6 (0+6) | 6 | 6 | 0 | 1/5/7 | 6 |
| 27 | 22 (21+1) | 3 | 3 | 0 | 1/23/23 | 1 |
| 28 | 3 (3+0) | 4 | 4 | 1 | 18/22/23 | 0 |
| 29 | 5 (5+0) | 4 | 4 | 17 | 2/22/23 | 0 |
| 30 | 5 (0+5) | 2 | 1 | 0 | 2/7/7 | 5 |

- 第 11–15 日常规路径被全局上限封停时，保底每日 3–6 单，板面最小 1 单，**不再归零**。
- 第 16 日 prune 后常规恢复 19 单（板面 max 22），第 19/27/30 日保底再次按需激活；
  保底单只在“该点当天没有可接单”时补 1 张，没有出现“每点每天堆满”的情况。
- 30 天中 `advance` 仅在第 27 日（3 文）与第 30 日（12 文）触发，说明垫付是边缘兜底，
  不是常态补贴。

---

## 7. 契约与基线

- `content/**` 未改；关闭态 `content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`，
  开启态 `3fd99946b44a7dac4c3ea041d114e28d84ef07e67aa90e40e209970c2c43534c`（见 `docs/DELIVERY-TEST-p3l.md`）。
- 2 天 golden 世界（seed 42）不触发保底：`tests/test_p3e_golden.py` 实跑
  `ssssssssssssssss..`，V5 两条通过、692 events 不变，因此**不新建基线**；
  旧基线（GOLDEN / P3I / CALIB / MILESTONE / V2 / V3 / V4）原值与 skip 保留。
- 测试：
  - 新增 `tests/test_quest_floor.py` 6 条：垫付 + 守恒、金库不足不造钱、确定性轮转、
    非优先类别兜底、全部在途时再开一张、已有可接单不堆单。
  - `tests/test_quest_health.py`：旧“全局上限 → 0 单”契约改为
    “常规 0 单但每个发布点仍有 1 张保底”的断言。
- 全量 `pytest`：**480 passed, 16 skipped**；`./scripts/selfcheck.sh`：**4/4 PASS**。

---

## 8. 未覆盖项与限制

1. **真实墙钟 4x 未跑满 10 天**：10 天验收在真实服务进程内用 `pause + step` 同步推进
   （ambient 配置为 4x）；事件/调度/Writer 相同，但没有等待 36 分钟墙钟。1x 未跑。
2. **第 8 日药铺为 0**：当天 `su_hexiang` 不在药铺，不构成“有 giver 的发布点”；
   若按“地点必须有板”而非“有 giver 才有板”定义，则药铺在该日仍为空。
3. **金库垫付未在 10 天真实服务 run 中出现**：该 run 无 giver 现金不足，垫付以
   `floor-advance-proof` 构造场景 + 单测验证；真实长跑中垫付仅在 30 天 headless
   run 的第 27/30 日出现。
4. **保底单不区分玩家/NPC 接单**：NPC 也会接保底单；本修复只保证“可接”，不强制
   玩家优先。保底单可能被 NPC 当日接走，玩家看到的是当天仍在板面的剩余量。
5. **声望门槛模板不参与保底**：`require_reputation > 0` 的模板即使 giver 在场也不补，
   避免新手看到不可接单；这些板面在玩家声望足够前仍可能为空。
6. 未改 `ui/player/**`、`api/session.py`、`actions/escort.py`、`actions/player.py`、
   `sim/scheduler.py`、`sim/intent.py`。
