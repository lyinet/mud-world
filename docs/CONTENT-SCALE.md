# W3 内容扩容：scaleup_w3 可开关扩展包

## 结论先说

**内容量够玩 10 小时：PASS。**

10 天开启态种子世界里，33 张新委托模板有 24 张被实际接下，29 件新物品有 23 件发生成交，
29 个新 NPC 全部产生自主事件。严格使用率
`(24 + 23 + 29) / (33 + 29 + 29) = 76 / 91 = 83.52%`，高于 40% 门槛。

但这不是“内容更多了”的结论：有 9 张模板与 6 件物品在 10 天内没被调度到，
原因见下文“未覆盖项”，主因是引擎按 `quest id` 排序扫描导致长尾模板饿死，不是内容不足。

## 落地方式：默认关闭的可开关扩展包

- 新内容在 `content/packs/scaleup_w3/`，由 `pack.yaml` 声明目标量、开关与文件清单。
- 开关在 `content/packs/enabled.yaml`（默认 `enabled: []`），也支持环境变量
  `XKX_CONTENT_PACKS=scaleup_w3` 与校验器参数 `--pack scaleup_w3`。
- **默认关闭**：`content/manifest.yaml` 未改，扩展包不在 `additions/` 下，
  引擎默认加载路径完全不经过它。
- 开启态由 `tools/content_scale.py materialize` 把 base + additions + pack
  物化成一个独立 content 目录（带自己的 manifest），再交给现有 `load_content()`。
  这样不需要改 `src/wsim/**` 就能跑开启态种子世界。

复现：

```bash
cd world-sim
python content/validate_content.py --no-pack
python content/validate_content.py --pack scaleup_w3
python tools/content_scale.py determinism
python tools/content_scale.py run --days 2 --seed 42 --run-id w3-enabled-2d-60npc
python tools/content_scale.py run --days 10 --seed 42 --run-id w3-enabled-10d-60npc
python tools/content_scale.py audit --run-dir runs/w3-enabled-10d-60npc
```

## 关闭态验收（零污染）

| 项目 | 数值 |
|---|---|
| `content_hash` | `7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f` |
| NPC / 地点 / 物品 / 委托 | 31 / 9 / 41 / 27 |
| 2 日种子世界事件数 | 800 |
| state hash | `a155665368313c1703ebcb5b1e4cdf5110a5890928918b23a4a6e725030428a0` |
| event hash | `61b1531a03bb79b7c6548b87fb8f5e161d9ffc511dd13e8276419d936e7ac664` |
| pytest | 374 passed, 8 skipped |
| selfcheck | 4/4 PASS |
| 新增测试 | `tests/test_w3_pack.py` 4 passed |

关闭态 golden（`GOLDEN_MILESTONE`）由现有 `tests/test_p3e_golden.py` 断言通过，
本轮没有改任何旧基线值。

## 开启态验收

内容校验器 `python content/validate_content.py --pack scaleup_w3` **PASS**，包含四项新校验：

| 新校验 | 结果 |
|---|---|
| 无孤立 NPC | PASS（关系边 198，60 人全连通；29/29 新 NPC 有包内连边） |
| 无不可达地点 | PASS（20/20 从集市可达） |
| 无价格越界 | PASS（29/29 件新物品落在同品类锚点 ±20% 内） |
| 每张模板前置可满足 | PASS（60/60，含 33 张新模板） |

其他开启态数字：

| 项目 | 数值 |
|---|---|
| NPC / 地点 / 物品 / 委托 | 60 / 20 / 70 / 60 |
| 关系边 | 198（base 107 + 新增 91） |
| 新委托类别 | clear_path、teaching 各 3；8 类每类 ≥3 |
| 开启态 `content_hash` | `888e60a09357702afbee347d3602b7184239acc6bb8df5b40cf900550bcaa2aa` |
| 物化两次字节一致 | PASS，digest `fca834856ded332465db156836baac12548520950ccfa2cd873d63bcacd60b9f` |

### 双日开启态基线（另存，不覆盖关闭态）

产物：`runs/w3-enabled-2d-60npc/summary.json`。

| 项目 | 数值 |
|---|---|
| 事件数 | 1991 |
| state hash | `5b92b9d41091a760b9ff357a266f44e428e0fb9f89344a98629149b5b1623e6d` |
| event hash | `0c2b35eca0425bec95d411b0548c30ca463cfc08b1f652601bd99ec3b435e1d6` |
| 非法率 | 0.0502% |
| 完成目标 | wealth 5、mastery 0、bond 0、shop 4、faction 7 |
| 同 seed 重复跑 | 事件数/hash 完全一致 |

关闭态基线仍在 `tests/test_p3e_golden.py` 与既有 run 中，本包没有覆盖任何关闭态基线文件。

### 10 日开启态

产物：`runs/w3-enabled-10d-60npc/`。

| 项目 | 数值 |
|---|---|
| 事件数 | 6014 |
| state hash | `d3c45bbc2bfe75d6e8888c97e3745ff6fe81232882b71e914adc391ce634edcd` |
| event hash | `f66e214f05f929225fb81fdcd77324be83abfe7060ea011584940f640452621b` |
| 非法率 | 0.0166% |
| 完成目标 | wealth 25、mastery 12、bond 14、shop 22、faction 22 |

## 10 天新内容使用率

严格口径：新模板被接 + 新物品成交 + 新 NPC 有自主事件，三项去重后占
`33 + 29 + 29` 的比例。

| 维度 | 覆盖 / 总量 | 覆盖率 |
|---|---|---|
| 新委托模板被接 | 24 / 33 | 72.7% |
| 新物品有成交 | 23 / 29 | 79.3% |
| 新 NPC 有自主事件 | 29 / 29 | 100% |
| **严格合计** | **76 / 91** | **83.52% → PASS** |

### 模板使用明细

| 类别 | 模板数 | 被接数 | 被接模板（次数） |
|---|---|---|---|
| errand | 5 | 5 | bean_curd 4、dye_pouch 4、opera_cake 4、night_watch_report 3、river_shrimp 3 |
| inquiry | 4 | 4 | herb_box 3、bone_kit 2、salt_lamp 2、clay_figure 1 |
| procurement | 7 | 3 | kiln_yam 1、dried_ginger 1、herb_salve 1 |
| escort | 4 | 2 | dye_vat 2、fishing_spear 1 |
| debt_collection | 3 | 3 | cang_luo 1、hu_chen 1、xiu_cai 1 |
| clear_path | 3 | 3 | book_wood 3、kiln_timber 3、river_net 1 |
| teaching | 3 | 0 | — |
| commission | 4 | 4 | granary_pickle 4、herb_pick 4、pottery_clay 3、dye_stir 1 |

### 物品成交明细

有成交 23 件：clay 39、cool_tea 41、kiln_timber 35、raw_hemp 30、indigo_dye 17、
kiln_yam 9、river_shrimp 8、dried_ginger 6、engrave_chisel 5、bean_curd 4、pear_cake 4、
salt_block 4、chaihu 3、embroidered_pouch 3、pickled_radish 3、clay_figure 2、
fish_net_rope 2、potter_wheel 2、salted_fish 2、bone_set_kit 1、dye_vat 1、fishing_spear 1、
herb_salve 1。

### 新 NPC 自主事件

29/29 全部有事件，事件量 56（deng_xiaoqi）到 163（geng_laoge），
说明 29 人都接入了移动、买卖、任务或社交循环，没有静态摆件。

## 未覆盖项与原因

9 张未接模板：

- `procure_salted_fish`、`procure_wheat_flour`、`procure_salt_block`、`procure_raw_hemp`
- `escort_engrave_chisel`、`escort_potter_wheel`
- `teach_book_copy`、`teach_dye_cool_tea`、`teach_opera_cake`

6 件无成交物品：

- `wheat_flour`、`salt_scale`、`wood_board`
- `rare_herb_box`（只被打听模板引用，打听是零金额信息交接，不产生物品成交）
- `service_copying`、`service_night_watch`（服务类物品，当前经济只对实体货物记账）

原因不是“库存不足”：例如 `salted_fish` / `wheat_flour` / `salt_block` / `raw_hemp`
在 10 日结束时商业库存仍有 ≥1980 件。主因是引擎 `_quest_feasible` 的开放任务扫描：

- `src/wsim/sim/intent.py` 的 `open_quests` 按 `q.id` 排序，所有模板同一 tick 发布，
  因此 `procure_*`、`teach_*` 这类字典序靠后的模板长期排在队尾；
- 10 天全服只发生 106 次接单，平均每模板 1.7 次，长尾模板被结构性饿死；
- 护送模板还叠加路线/角色资格过滤，尾部更容易拿不到。

建议整合波改引擎（本轮未改 `src/wsim/**`）：给开放任务做按模板公平抽样或每日配额，
而不是纯字典序扫描；这样不增加内容也能让 9 张长尾模板进入流通。

## 需要整合波的引擎改动点

1. **扩展包开关进入引擎加载路径**：`load_content` / `load_content_packages` 目前不读
   `content/packs/enabled.yaml` 或 `XKX_CONTENT_PACKS`。现由
   `tools/content_scale.py materialize` 物化合并目录绕过；若要引擎原生支持，
   需在 content 加载层加 pack 解析（base + additions + pack 深合并，quests/sects/personas
   直接读物化文件）。
2. **任务公平调度**：`src/wsim/sim/intent.py` 的开放任务按 id 排序扫描，
   长尾模板 10 天零接单。建议改公平轮转/配额。
3. **新 NPC persona 卡**：29 个新 NPC 没有 persona（base 只有 30 张）。
   开启态可跑、不会崩，但对话层对新 NPC 只能走通用回退；若要对话质量一致，
   需补 29 张卡或给 prompt 层加显式回退文案。
4. `clear_path` / `teaching` 两类复用 `work` 动词，引擎不读 `category`，无需改引擎。
5. 新地点出口由引擎 `_repair_exits` 自动补反向边，物化时也已补全，无需改引擎。

## 提交边界

本轮只改：

- `content/packs/`（扩展包与开关）
- `content/validate_content.py`
- `tools/content_scale.py`
- `tests/test_w3_pack.py`
- `docs/CONTENT-SCALE.md`

未改 `src/wsim/**`、`ui/player/**`、`docs/QUICKSTART.md`、`docs/known-limitations.md`、
`docs/RELEASE-NOTES-p3i.md`、`dist/`，未动任何 tag。
