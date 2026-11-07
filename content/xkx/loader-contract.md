# xkx 内容包加载契约（schema_version 1）

本目录是 `d/hangzhou`、`d/jiaxing`、`d/shiliang` 三个区域的静态切片，由
`tools/xkx_extract` 从 xkx100 LPC 源码抽取。加载器只需读 JSON，不需要解析 LPC。

- 编码：UTF-8，无 BOM，换行 `\n`。
- 排序：所有数组按抽取顺序固定，`json.dumps` 使用 `sort_keys=True`；同一源码 commit、同一区域集合重跑，产物逐字节一致。
- 统配 id：全部是 mudlib 绝对路径、去掉 `.lpc` 扩展名，例如 `/d/hangzhou/baiti`。加载器可以直接把它当主键，不要重写。
- 时间戳：不存在。版本用手工维护的 `schema_version` / `rules_version`。

## 1. 公共字段

每条记录都有：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string | mudlib 绝对路径，主键 |
| `origin` | `"slice"` \| `"boundary"` \| `"external"` | 见下 |
| `source` | object | `{"path": "d/hangzhou/baiti.lpc", "line": 6}`；指向原版源码位置，仅供追溯 |

`origin` 取值：

- `slice`：区域目录内的真实内容，计入 `source` / `total` 覆盖率。
- `boundary`：区外目标房间的替身（boundary stub）。玩家可以从切片走进来，再走回去，但不能继续往区外走。
- `external`：切片内房间或商店引用了区外的物品/NPC，为保证加载不断链而一并收录。不计覆盖率。

### `desc` / `desc_source`

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `desc` | string \| null | 玩家可见描述（房间 `long`、物品/NPC `long`），已去 ANSI 颜色与首尾空白 |
| `desc_source` | `"lpc"` \| `"authored"` \| `null` | 描述来源 |

- `lpc`：原版 LPC 源码里真实存在，计入 `coverage.source`。
- `authored`：原版没有 `long`，由本项目按原版器物/身份常识自撰；**只计入 `coverage.total`，绝不计入 `coverage.source`**。11 条全部列在 `manifest.json` 的 `authored_descriptions`。
- `null`：缺失。切片内若出现，`total` 覆盖不足 100%，抽取器直接报 error。

加载器约束：`desc` 为 null 时不要显示“（这里什么都没有）”，也不要用 id 顶替；这属于内容缺陷，应由抽取器拦住。

## 2. `rooms.json`

201 条 = 195 条 `slice` + 6 条 `boundary`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 房间主键 |
| `name` | string \| null | 房间短名，对应原版 `set("short")`；null 表示源码没写 |
| `desc` | string \| null | 房间长描述，对应 `set("long")` |
| `desc_source` | `"lpc"` \| `null` | 房间没有自撰描述 |
| `kind` | `"room"` \| `"boundary_stub"` | 与 `origin` 对应 |
| `flags` | object | 原版房间标记，**值是原始值，不是统一布尔**：`outdoors` 常见为区域名（如 `"xihu"`）、`no_clean_up` 可能为 `0` |
| `coord` | object | `{"x": 4130, "y": -1450, "z": 0}`，可能为空 `{}`；用于地图定位，不参与寻路 |
| `objects` | array | 房间初始物件，见下 |
| `source` | object | 源码位置 |

`flags` 目前出现过的键：`outdoors`、`no_clean_up`、`no_fight`、`no_beg`、`no_steal`、`valid_startroom`、`owner`、`shop_type`。加载器对未知键应忽略而不是报错。

### `objects[]`

```json
{ "ref": "/d/hangzhou/npc/tiao-fu", "kind": "npc", "count": 1, "in_slice": true }
{ "ref": "/clone/medicine/vegetable/baiwei", "kind": "item", "count": null,
  "count_expr": "random(2)", "in_slice": false, "origin": "external" }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ref` | string | 要实例化的物品或 NPC id |
| `kind` | `"item"` \| `"npc"` | 引用类型 |
| `count` | int \| null | 原版写死的数量；null 表示数量是表达式 |
| `count_expr` | string | 仅 `count=null` 时出现，原样保留（如 `random(2)`），加载器可当装饰或按需实现 |
| `in_slice` | bool | id 是否在本内容包内有完整定义 |
| `origin` | `"external"` | 仅 `in_slice=false` 时出现 |

加载顺序：先读 `items.json` / `npcs.json` 建索引，再按 `objects` 实例化。

## 3. `exits.json`

427 条，全部带方向名。一条记录 = 一个单向出口，不在房间内嵌。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `from` | string | 起点房间 id |
| `dir` | string | 方向名，原版原样：`north`/`south`/`east`/`west`/`up`/`down`/`northeast`/`northwest`/`southeast`/`southwest`/`northup`/…/`enter`/`out` |
| `to` | string | 终点房间 id，保证存在于 `rooms.json` |
| `line` | int | 源码行号 |
| `status` | `"open"` \| `"closed"` | `closed` 仅出现在指向 boundary stub 且无回程的出口（本切片没有这种情况） |
| `traversable` | bool | 与 `status` 一致，加载器用它判断能否移动 |
| `boundary` | bool | 仅当该出口通向 `kind=boundary_stub` 的房间时出现 |
| `from_kind` | `"boundary_stub"` | 仅当起点是 boundary stub 时出现 |

规则：

- 同一对 `(from, dir)` 在一个房间内唯一；原版后写的覆盖先写的。
- 出口是**单向事实**，不要自动补回程。原版有 9 条单行道（见 `diagnostics.jsonl` 的 `W_ASYMMETRIC_EXIT`），这是设计如此，不是数据 bug。
- 方向名不做翻译映射；中文展示由引擎侧负责。

## 4. `items.json`

51 条 = 39 条 `slice` + 12 条 `external`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 物品主键 |
| `name` | string \| null | 中文名，来自 `set_name` |
| `desc` | string \| null | 物品长描述 |
| `desc_source` | `"lpc"` \| `"authored"` \| `null` | 见公共字段 |
| `price` | int \| null | 原版 `set("value")`，null 表示源码没有或不是整数字面量 |
| `price_expr` | string \| null | `price` 为 null 且源码有 `value` 时的原始表达式文本 |
| `unit` | string \| null | 量词，如 `把`、`件` |
| `material` | string \| null | 材质，如 `cloth`、`steel`、`iron` |
| `weight` | int \| null | 原版 `set_weight` |
| `category` | string | `misc` / `armor` / `weapon` / `food` / `liquid` / `medicine` / `money` / `equip` |
| `equip_slot` | string \| null | 装备槽位；非装备为 null |
| `food_supply` | int \| null | 食物/饮水补充值 |
| `origin` | `"slice"` \| `"external"` | |

`equip_slot` 目前出现过的值：`cloth`、`head`、`neck`、`armor`、`surcoat`、`waist`、`wrists`、`shield`、`finger`、`hands`、`boots`、`sword`、`blade`、`mace`、`staff`、`whip`、`axe`、`hammer`、`dagger`、`fork`、`throwing`、`club`。加载器按槽位做互斥即可，不需要认识具体武器类型。

注意：`/d/changan/obj/chairs` 原版没有 `set_name`，`name` 为 null；加载器需要用 id 或描述首句兜底展示，不要崩溃。

## 5. `npcs.json`

105 条 = 92 条 `slice` + 13 条 `external`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | NPC 主键 |
| `name` | string \| null | 中文名，来自 `set_name` |
| `aliases` | string[] | 英文别名（`id` 的第二参数） |
| `title` | string \| null | 头衔，如门派称谓 |
| `nickname` | string \| null | 外号 |
| `desc` | string \| null | 人物长描述 |
| `desc_source` | `"lpc"` \| `"authored"` \| `null` | 见公共字段 |
| `gender` | string \| null | 原版原样：`男性`、`女性`、`雄性`、`无性` 等 |
| `age` | int \| null | 年龄 |
| `class` | string \| null | 职业模板：`fighter`、`scholar`、`bonze`、`swordsman`、`taoist` 等 |
| `attitude` | string \| null | 原版原样：`friendly`、`peaceful`、`heroism`、`aggressive`、`peace` 等 |
| `aggressive` | bool | **主动攻击标志**，仅当 `attitude == "aggressive"` 为 true；切片内共 7 个 |
| `attrs` | object | `str`/`int`/`con`/`dex`/`per`/`kar`，缺省键不出现 |
| `vitals` | object | `max_qi`/`max_jing`/`max_neili`/`neili`/`jiali`/`combat_exp`，缺省键不出现 |
| `skills` | object | 技能 id → 等级（`set_skill`） |
| `map_skills` | object | 基础技能 → 映射到的技能 id（`map_skill`），如 `{"unarmed": "jingang-quan"}` |
| `prepare_skills` | string[] | 已准备技能 id（`prepare_skill`） |
| `equipment` | array | `[{"ref": "...", "slot": "wear"｜"wield"｜null}]`，slot 取原版调用方法名 |
| `is_master` | bool | 是否 `inherit F_MASTER` |
| `sect` | string \| null | 门派目录名（如 `honghua`），仅 `is_master=true` 时有值 |
| `vendor` | bool | 是否有 `set("vendor_goods")`，即能否开店 |
| `origin` | `"slice"` \| `"external"` | |

`skills` 的键是技能 id，中文名在 `skills.json` 查。`map_skills` 表示“用哪个技能顶替基础技能”，不是等级。

## 6. `dialogue.json`

67 条 = 40 条切片内 + 27 条来自区外 NPC。一条记录 = 一个可问的主题（`set("inquiry")` 的键）。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `topic` | string | 主题键，玩家输入的问题词（中文），如 `婚约`、`自宫` |
| `npc` | string | 提供该主题的 NPC id |
| `kind` | `"string"` \| `"fallback"` | 答案类型 |
| `text` | string \| null | `kind=string` 时的答案正文 |
| `func` | string \| null | `kind=fallback` 时的原版函数名/表达式文本，静态不可搬运 |
| `source` | object | 源码位置 |

加载器约束：

- `kind=string`：直接显示 `text`。
- `kind=fallback`：原版答案是函数（涉及状态、任务、条件），本内容包只保留主题键；加载器给一句通用占位或走引擎自己的处理器，**不要把它当成有答案**，也不要伪造文本。
- 同一 NPC 多个主题共用同一答案文本是正常的（原版一个函数/字符串挂多个键）。

## 7. `shops.json`

3 条，每条对应一个 `vendor=true` 的 NPC。

```json
{
  "id": "/d/hangzhou/npc/smith",
  "npc": "/d/hangzhou/npc/smith",
  "stock": [
    {"item": "/d/city/obj/changjian", "name": "长剑", "price": 1500, "origin": "external"}
  ],
  "source": {"path": "d/hangzhou/npc/smith.lpc", "line": 7}
}
```

`stock[].item` 一定能在 `items.json` 找到；`price` 可能与物品自身 `price` 相同，库存条目以自身为准。售卖/回购逻辑不在内容包内。

## 8. `skills.json` / `sects.json`

`skills.json`：48 条，只收切片内 NPC 声明或映射到的技能。

| 字段 | 说明 |
| --- | --- |
| `id` | 技能 id，如 `anying-fuxiang` |
| `name` | 中文名，如 `暗影浮香` |
| `type` | 原版 `type()`，如 `martial` |
| `martialtype` | 原版 `martialtype()`，如 `dodge` |
| `enable` | 可启用的基础技能列表 |
| `used_by` | 使用该技能的 NPC id 列表 |

`sects.json`：1 条（红花会）。

| 字段 | 说明 |
| --- | --- |
| `id` | 目录名 `honghua` |
| `name` | 中文名 `红花会` |
| `area` | `hangzhou` |
| `masters` | 门派 NPC id 列表（19 人） |
| `master_count` | 人数 |

加载器若要按门派拜师，用 `sects.masters` + `npcs.is_master`；不要按 `children()` 猜。

## 9. `diagnostics.jsonl` / `summary.json` / `manifest.json`

- `diagnostics.jsonl`：一行一条诊断，字段 `rule`、`severity`（`error`/`warning`/`info`）、`path`、`line`、`message`，部分带 `id`/`target`/`metric`/`ref`。error 阻断发布。
- `summary.json`：`counts`、`coverage`、`errors`、`warnings`、`infos`、`by_rule`。
- `manifest.json`：源 commit、区域、`counts`、`coverage`、`field_semantics`（本文件字段口径）、`authored_descriptions`（11 条自撰文案）、`coverage_semantics`、每个文件的 sha256/字节数、`content_hash`。

### 覆盖率两本账

```json
"coverage": {
  "source": { "rooms_desc": {...}, "room_exits": {...}, "items_desc": {...},
              "npcs_desc": {...}, "dialogue_text": {...} },
  "total":  { "rooms_desc": {...}, "items_desc": {...}, "npcs_desc": {...} }
}
```

每个指标是 `{"hit", "total", "ratio", "threshold"}`。

- `source`：只统计 `desc_source="lpc"`，衡量对原版忠实度。当前：房间 195/195=100%，出口 191/195=98.0%，物品 32/39=82.0%，NPC 88/92=95.7%，对话 28/40=70.0%。
- `total`：含 `authored`，衡量玩家可见描述完整性，必须 100%。当前三项全 100%。

命令行：`--strict-source` 与 `--strict-total` 独立生效，任一可单独让进程失败；`--verify` 重抽并逐文件比对。

## 10. 本内容包不承诺的内容

- 技能效果、战斗公式、伤害/命中、经验成长：属于引擎，不在 JSON 里。
- 任务、剧情线、条件触发器、函数型对话的实际行为。
- 商店议价、库存刷新、门派拜师流程。
- 区外区域的地图与内容：只提供 6 个 boundary stub 和 25 条区外引用，详见 `boundary-and-external-refs.md`。
