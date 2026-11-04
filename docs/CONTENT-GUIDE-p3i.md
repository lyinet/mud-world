# CONTENT-GUIDE p3i：内容扩充与玩家验证清单

> 适用版本：`content_hash = fac4ac31fec9db5720d829d79827b215f5d0aee217be5fcb57bfdf38165ef8dd`
> （41 物品 / 31 NPC / 9 地点 / 27 任务模板）。本文件与 `QUICKSTART.md`、`known-limitations.md`、
> `RELEASE-NOTES-p3h.md` 互不覆盖；后三份属于打包会话，本文件是 P3I 内容包的验证入口。

## 0. 结论：试玩深度是否够

**够玩，且有可追的目标；但“物价波动”只做到内容层规格，引擎未接入，运行时价格不变。**

- 有东西可做：4 步新手链 + 8 条循环日常（买卖 / 跑腿 / 打听 / 护送）+ 1 张新讨债单，
  2 日 seed 世界里 NPC 自主接下 6 类日常、27 次接单、23 次交付。
- 有目标可追：3 条跨多日长线弧（柳三娘盘铺、段五斗盘铺、韩杏林学手艺），
  2 日实测已出现 1 次 100% 里程碑和 3 次技能里程碑。
- 有变化可看：31/31 NPC 关系度 > 0（平均 3.45 条），新增 10 件物品中 6 件在 2 日内
  产生 79 笔成交、38 次消耗，`bamboo_basket` 作为集市柜台承接 38 条事件。
- **缺口**：`Content.item_price()` 只读 `base_price × price_markup`，不读供需；
  因此下表物价曲线是“可复现的内容规格 + 校验器保证”，玩家在游戏里看到的价格仍是静态的。
  接入需要改 `src/wsim/**`，按本次纪律未做；详见第 4 节。

## 1. 本次改了哪些文件

| 文件 | 改动 |
| --- | --- |
| `content/items.yaml` | 追加 10 件物品（31 → 41） |
| `content/locations.yaml` | 集市/铁匠铺/客栈/药铺/书院/河渡追加新商品服务 |
| `content/npcs.yaml` | 家庭/师徒/债主/竞争关系 5 组；每条关系写明“谁对谁、方向、金额” |
| `content/quests.yaml` | 4 步引导链 + 8 条日常 + 1 张讨债单（14 → 27 模板） |
| `content/manifest.yaml` | 声明 `additions/economy_p3i.yaml`（type=economy） |
| `content/additions/economy_p3i.yaml` | 新物品产地/用途、3 条长线弧、确定性物价模型 |
| `content/validate_content.py` | 物品数量上限、任务模板数、P3I additions 专项校验 |
| `tests/test_p2b_regression.py` | 真实内容物品数 31 → 41 |
| `tests/test_p3e_golden.py` | 旧 824-event 基线保留并 skip；新增 P3I 791-event 基线 |

未改 `src/wsim/**`、`sim/run_headless.py`（另一条会话正在改），未改打包会话的三份文档。

## 2. 玩家怎么验证每一项（逐项：操作 → 预期）

先按 `docs/QUICKSTART.md` 的 `./start.sh` 起服（前端 `http://127.0.0.1:8000/`），
或用 curl 直打前端代理。写动作每次推进 6 tick（1 小时）。

### 2.1 新手引导任务链（4 步）

| 步骤 | 操作（一句话） | 预期看到 |
| --- | --- | --- |
| 1/4 | 进入世界后停在集市，`GET /api/player/state` 看 `quest_board.rows`，接 `quest:tutorial_meet_sanniang:0` | 任务名“【引导1/4】到集市绣摊认识柳三娘”，报酬 10 文；接后 `accepted_by_me` +1 |
| 2/4 | 在集市与柳三娘交谈并 `POST /api/player/quest/deliver`，再读任务板 | 出现“【引导2/4】把绣样送到药铺苏掌柜手上”；钱包 +10 文、柳三娘关系 +3 |
| 3/4 | 到药铺接 `quest:tutorial_first_buy:0`，垫 28 文买 2 束艾条再交付 | 任务名含“垫付28文买两束艾条”；钱包先 -28、交付后 +14 报酬（净 +14 含余货） |
| 4/4 | 到城隍庙接 `quest:tutorial_first_sale:0`，把 1 束艾条卖给曹半仙 | 任务名“【引导4/4】第一笔卖货”；钱包 +8 卖货款 +14 报酬，完成第一次“买入→卖出”闭环 |

> 离线复算证据（`runs/p3i-audit2`）：4 步在 tick 79 / 80 / 85 / 86 被 NPC 自主接下
> （`tutorial_first_buy`@ruan_qingfu、`tutorial_first_sale`@yan_shouyi、
> `tutorial_meet_sanniang`@luo_qingxu、`tutorial_silk_sample`@meng_jiuniang），
> 第 2 日在 tick 188 / 190 复现。

### 2.2 日常委托（8 条，循环）

| 验证点 | 操作 | 预期 |
| --- | --- | --- |
| 刷新规则 | 交付一张日常单后，把世界推进到第 2 日开市（tick 144），再读任务板 | 同模板重新出现（`repeatable: true`，每模板同一时刻只留 1 张 open/accepted） |
| 【轻】跑腿 | 接 `daily_ferry_message` 或 `daily_market_inquiry` | 报酬 8–12 文、期限 2 日；跑一趟即可交付 |
| 【中】买卖 | 接 `daily_inn_millet`（垫 22 文）或 `daily_ferry_rope`（垫 18 文） | 需要先垫钱买货再交付；报酬 16–18 文 |
| 【重】护送 | 接 `daily_escort_short`（镖路 铁匠铺→集市，货值 160，镖银 140） | 期限 3 日；交付后有镖银与 22 文报酬，NPC 关系 +2 |
| NPC 自主接单 | `scripts/world_feed.py --run runs/p3i-audit2 --lines 30` | 能看到 NPC 自己接下 `daily_*` 并交付 |

> 实测：6 类日常被 NPC 自主接下（`daily_ferry_fish_money`@tick 8、`daily_ferry_message`@11、
> `daily_escort_short`@12、`daily_ferry_rope`@17、`daily_inn_millet`@27、`daily_smithy_bellows`@34），
> 共 27 次接单 / 23 次交付。

### 2.3 长期目标（3 条跨日弧）

| 弧 | 操作 | 预期 |
| --- | --- | --- |
| 柳三娘·盘铺 | 集市点柳三娘，看详情里的目标与进度条 | 目标“盘铺子（build_shop）”进度从 0% 走向 100%；实测 tick 17 到 100% 并出现 `open_shop` |
| 段五斗·菜摊盘铺 | 同上，看段五斗 | 实测 tick 68 到 100%，`open_shop` 事件 |
| 韩杏林·学手艺 | 药铺点韩杏林，看 `advance_skill` 进度 | 里程碑 3% / 15% / 40%；实测 tick 81→0.83%、149→1.67%、150→2.50%（按目标 target 折算 3% 门槛已过） |

> 长线弧不新增 NPC 目标记录（NPC 目标已是引擎认可的 3 条上限），而是把现有 goal
> 编排成可追的里程碑阈值，写在 `additions/economy_p3i.yaml` 的 `long_term_arcs` 里。

### 2.4 NPC 关系与家族

| 验证点 | 操作 | 预期 |
| --- | --- | --- |
| 关系可见 | 点任意 NPC 详情或读 `state` 里的 `relations` | 每条关系有方向与数值：温小满—罗轻絮 family 30、韩杏林—蒋听雨 family 20、秦望川→阮小螺 master 45 / 反向 disciple 45、石敢当→陆九斤 debtor -15（欠 50 文）/ 陆九斤→石敢当 creditor 15（债权 50 文）、沈砚舟—邢破军 rival -25 |
| 讨债单归属 | 接 `debt_shi_to_lu`（发布人陆九斤，目标石敢当，铁匠铺） | 讨回 50 文后按 `settlement: remit_to_giver` 交回陆九斤，讨债人只拿 8 文 |
| 覆盖度 | 用 `content/validate_content.py` 或第 6 节脚本 | 31/31 NPC 关系度 > 0，平均 3.45，无孤立点 |

关系如何影响行为（内容层事实，非猜测）：

- **对话路由**：`talk`/gossip 取关系对象作为话题来源（social 动作按 `state.relations` 选人），
  玩家面板 `relations` 直接展示亲密度与债务；关系值还用于入派门槛
  `economy.sect.min_relation_value = 30`，以及 `build_relation` 目标的进度判定。
- **任务归属**：任务发布人/目标由 `quests.yaml` 的 `giver`/`target_npc` 固定，关系本身不会
  凭空生成任务；P3I 的做法是把关系“落成内容”——5 组关系里的债务对生成 1 张讨债单，
  师徒对承接拜师/学徒台词，家庭对进入家族闲聊话题。

### 2.5 物价波动（**半成品，如实标注**）

| 验证点 | 操作 | 预期 |
| --- | --- | --- |
| 内容层可复现 | 跑 `python3 content/validate_content.py` | 打印 5 类 15 件商品的两日价格；同输入连算两次结果一致，无随机项 |
| 运行时是否真的变价 | 在集市/药铺查同一商品两天的成交价 | **不变**：引擎只用 `base_price × price_markup`，未接入 `price_model` |

两日确定价（`price = round(base_price * (1 + elasticity * (demand - supply) / (demand + supply)))`）：

| 商品 | 类别 | 第1日 → 第2日 | 幅度 |
| --- | --- | --- | --- |
| wound_powder | medicine | 24 → 27 | +12.5% |
| millet | food | 11 → 12 | +9.1% |
| rice | food | 12 → 13 | +8.3% |
| bamboo_basket | material | 14 → 15 | +7.1% |
| moxa_roll | medicine | 14 → 15 | +7.1% |
| iron_ingot / sickle / hammer / silk / jade_pendant / cleaver / pearl_earring | 各 | 约 +3.8% ~ +6.8% | — |
| flatbread / hemp_rope / mugwort | food/material/medicine | 不变 | 0.0% |

覆盖 5 类（food / material / tool / medicine / luxury）、2 日、15 件商品；最大两日幅度 **+12.5%**。
驱动规则是“进货收紧 + 赶集/秋收/换季需求上升”，全部由 YAML 里的 `supply_by_day` /
`demand_by_day` 显式给出，无 `random/rng/noise/variance` 字段。

### 2.6 可用物品（10 件）

| 商品 | 价格 | 产地 | 用途 | 2 日实测 |
| --- | --- | --- | --- | --- |
| rice_cake 米糕 | 2 | 客栈 | 早点充饥 | 消耗 31 次 |
| lotus_root 莲藕 | 6 | 河渡 | 水产/素菜 | 消耗 7 次 |
| river_fish 河鱼 | 9 | 河渡 | 食材 | 货架可买，NPC 0 笔 |
| millet 小米 | 11 | 集市 | 主粮；`daily_inn_millet` 目标物 | 买入 2 笔 |
| hemp_rope 麻绳 | 9 | 集市 | 捆货/渔网；引导链垫付商品 | 买 50 / 卖 15 / 换货 1 |
| bamboo_basket 竹篮 | 14 | 集市 | 装货容器；集市柜台 business | 承接 38 条交易事件 |
| ink_stick 墨锭 | 22 | 书院 | 抄书耗材 | 买 8 / 卖 1 |
| sickle 镰刀 | 46 | 铁匠铺 | 农具；铁匠铺新货 | 2 次做工场景 |
| moxa_roll 艾条 | 14 | 药铺 | 灸疗；引导链 3/4 步商品 | 买入 2 笔 |
| pearl_earring 珠耳坠 | 320 | 集市 | 首饰/嫁娶 | 货架可买，NPC 0 笔 |

合计：6 件产生 79 笔成交（hemp_rope 66、ink_stick 9、millet 2、moxa_roll 2），
2 件产生 38 次消耗（rice_cake 31、lotus_root 7），`bamboo_basket` 38 条柜台事件，
`sickle` 2 次做工；`river_fish`、`pearl_earring` 2 日内只有货架、无 NPC 成交（如实记录）。

## 3. 验收数字（本机实测，2026-11-04）

| 项目 | 数字 |
| --- | --- |
| 内容包校验 | PASS（硬性检查全过，6 条单向 friend 属允许张力） |
| `pytest` | **294 passed, 2 skipped**（2 条 skip = 被显式保留的旧 824-event 基线） |
| `./scripts/selfcheck.sh` | **PASS 4/4**（校验 / pytest / 框架验收 / 玩家闭环） |
| 2 日 seed 世界（`runs/p3i-audit2`，seed=42，31 NPC，带玩家） | 1224 事件，非法率 0.000817，k1 19/91 |
| 任务链触发 | tick 79 / 80 / 85 / 86（NPC 自主接单）；第 2 日 188 / 190 复现 |
| 日常委托自主接下 | 6 类（≥3 要求），27 次接单 / 23 次交付 |
| 长线里程碑 | 柳三娘 build_shop 100%@17；段五斗 100%@68；韩杏林 advance_skill 0.83%@81 → 2.50%@150 |
| 关系度 > 0 | 31/31 = **100%**，平均 3.45 条，107 条边 |
| 物价两日幅度 | 引擎运行时 **0.0%**；内容模型 **+12.5% 最大**（15 件 / 5 类） |
| 新物品成交 | 79 笔成交 + 38 次消耗 + 38 条柜台事件 |

## 4. 对确定性 seed 世界的影响（下游需要重新基线）

同一引擎、同一 seed=42、2 日、31 NPC、无玩家，只换内容包：

| 指标 | 旧内容 `085702…` | 新内容 `fac4ac…` | 差异 |
| --- | --- | --- | --- |
| 事件总数 | 633 | 838 | **+205（+32.4%）** |
| K1 目标推进 | 13/91 = 14.29% | 15/91 = 16.48% | **+2 个目标、+2.19pp**（跨 content_hash，按 `non_comparable` 标注） |
| K3（diagnostic_only） | entropy 3.537 bits / 有效类别 11.61 | entropy 3.245 bits / 有效类别 9.48 | 行为更集中（任务/干活占比上升），非 pass/fail |
| golden 2 日假端点 run | 824 events（旧基线） | **791 events** | 旧基线不可复现，已新建 P3I 基线 |
| 非法率 | 0.0（对照 run） | 0.000817 | 1 条被 `effect_invalid` 拒绝（物品不足） |

带玩家入口的 2 日 run 为 1224 事件；`runs/p3i-audit`（`e113f6…`）与 `runs/p3i-audit2`（`fac4ac…`）
事件数、K1、state/event 哈希完全相同——最后一步内容定稿（关系朝向、目标回退）不改变世界走向。

## 5. golden 基线迁移（非静默改旧值）

- 旧 `GOLDEN`（824 events）与 `DIALOGUE_GOLDEN` 原值保留，测试显式 `skip` 并注明
  “P3I 内容扩充后旧世界不可复现”。
- 新基线 `GOLDEN_P3I = 791 events`（state `e5a986f7…`、event `3e7afc83…`、
  sell 1133 / buy 852 / burned 110 / business 11307 / blocked 9 / pruned 2）、
  `DIALOGUE_GOLDEN_P3I = 791 events`（calls 63）。
- 迁移原因：新增 10 件物品与新地点服务改变 genesis 商业体与买卖路径；
  采集 v1（792 events）后关系边与债务朝向尚未定稿，v2 为重采样，
  v1 数值留在 `tests/test_p3e_golden.py` 注释里作为漂移记录。

## 6. 复跑命令

```bash
python3 content/validate_content.py                      # 内容校验（含 P3I 专项）
python3 -m pytest -q                                     # 294 passed, 2 skipped
./scripts/selfcheck.sh                                   # 4/4 PASS
python3 sim/run_headless.py --days 2 --seed 42 --npc 0 \
  --with-players 1 --dump-state --out runs --run-id p3i-recheck
python3 scripts/world_feed.py --run runs/p3i-recheck --lines 30
```
