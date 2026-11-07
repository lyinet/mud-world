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
| `drops` | array | 死亡掉落，见 5.1；**没有掉落也写空表** |
| `drops_source` | `"lpc"` \| `"none"` | `lpc` = `die()` 中有真实掉物；`none` = 源码无掉落 |
| `drops_note` | string \| null | `drops_source="none"` 时必填，写明为什么没有 |
| `temp_apply` | array | `create()` 内 `set_temp` 的静态快照，见 5.2 |
| `origin` | `"slice"` \| `"external"` | |

`skills` 的键是技能 id，中文名在 `skills.json` 查。`map_skills` 表示“用哪个技能顶替基础技能”，不是等级。

### 5.1 `drops[]` — 死亡掉落

```json
{ "item": "/clone/medicine/vegetable/hugu",
  "qty": 1,
  "chance": 1.0,
  "chance_expr": null,
  "item_resolved": true,
  "landing": "ground",
  "source_landing": "corpse",
  "source": { "path": "d/hangzhou/npc/laohu.lpc", "line": 45 },
  "source_kind": "lpc" }
```

| 字段 | 含义 |
| --- | --- |
| `item` | 物品 id，必须在 `items.json` 中查得到（加载器对账，缺一即 error） |
| `qty` | 每次死亡掉落数量，整数；原版循环创建时不会默认成 1，会进诊断 |
| `chance` | `1.0` = 必掉；`<1` = 由源码 `random()` 折算；`null` = 无法静态折算 |
| `chance_expr` | 源码概率条件原文（如 `random(3)==0`）；`null` 表示原版没有概率判定 |
| `item_resolved` | 掉落物源文件是否存在；true 表示已并入 `items.json` |
| `landing` | 引擎落地方式，本项目统一 `"ground"` |
| `source_landing` | 原版实际放置位置：`corpse`（先入尸体）或 `ground`（直接落地），仅作溯源 |
| `source_kind` | `lpc`，表示从 `die()` 静态抽取 |

**概率口径**：`chance=null` 时加载器必须按 `chance_expr` 自行实现，不得把未知概率当必掉；`chance=1.0` 且 `chance_expr=null` 才是真正的必掉。

**尸体简化（重要）**：原版死亡会先 `make_corpse()` 生成尸体，把 `die()` 里创建的物品和 NPC 随身装备一起塞进尸体，玩家再翻尸体取物。本项目**不建模尸体**：

- `drops[]` 只记 `die()` 中显式创建的物品，`landing` 固定为 `ground`，引擎直接把物品放地面；
- NPC 随身装备**不进 `drops`**，由引擎在 NPC 死亡时按 `npcs.json.equipment` 落地，避免与 `drops` 双计；
- `source_landing="corpse"` 只用于溯源，加载器不要据此去创建尸体容器。

当前只有 3 个 NPC 有掉落（`laohu`、`wolfdog`、`fish`），均必掉；其余 102 个 NPC 为 `drops: []` + `drops_note`。

**装备对账**：`equipment[].ref` 必须能在 `items.json` 查到，抽取器对两侧做硬校验，缺一个即 `E_EQUIP_ITEM_NOT_IN_ITEMS`。装备物品与普通物品共用同一张 `items.json`：

- `ref` 已归一化：补前导 `/`、去掉 `.lpc`（原版有 3 处写法不规范），加载器按 `ref` 直接查表即可；
- `equipment[].item_resolved` 表示该物品源文件是否真实存在；false 时校验报 error；
- 装备物品缺 `set_weight()` 时按基类 `inherit/item/item.lpc` 的默认 `set("weight",100)` 写入，并带 `weight_source="default"` + `weight_reason`；
- 名称/价格/描述一律照原版；原版没有 `set("long")` 的，`desc=null`、`desc_source=null`，不补写；
- 原版没有 `set("value")` 的，`price=null`，不按 0 假装有价。

### 5.2 `temp_apply[]` — 临时属性（`set_temp`）

```json
{ "key": "apply/defense",
  "value": 30000,
  "value_expr": null,
  "known": true,
  "source_kind": "lpc",
  "source": { "path": "d/hangzhou/npc/laohu.lpc", "line": 29 } }
```

| 字段 | 含义 |
| --- | --- |
| `key` | 原版 `set_temp` 的键名，原样保留 |
| `value` | 整数字面量；非整数时为 `null` |
| `value_expr` | `value=null` 时的源码原式（如 `ma`、`"huo qingtong"`）；不折算 |
| `known` | 是否在战斗白名单内；`false` 表示抽取器不认识，原样保留并记 `W_TEMP_KEY_UNKNOWN` |
| `source_kind` | 固定 `lpc`，表示照源码静态搬运 |
| `source` | 源码文件与行号 |

战斗白名单：`apply/attack`、`apply/defense`、`apply/armor`、`apply/dodge`、`apply/parry`、`apply/damage`。白名单外的键（如 `armor`、`qingshu`、`shoupa`、`is_riding`、`is_rided_by`）不丢弃、不猜测，`known=false` 供引擎侧确认。

**只取 `create()` 作用域**：原版同名对象在运行期（`init()`、任务函数等）也会 `set_temp` 改状态，那些不是创世属性，抽取器按函数体行号排除。

**与原版的差异（重要）**：原版 `set_temp` 是运行期临时加成，`reset()` 或对象重建后会按 `create()` 重新设置；本项目按**创世即带**处理——加载器在创建 NPC 时一次性应用这些加成，不做运行期重算。对静态战斗数值（`apply/attack`、`apply/defense` 等）两者等价；若将来引入“加成被临时修改”的机制，需要单独建模，不能假设这里会随 `reset` 刷新。

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

## 9. `skills_actions.json` — 招式序列表

48 条，与 `skills.json` 一一对应；一条 = 一个技能的全部招式定义。即使该技能没有 `action` 数组（轻功/内功/知识），也保留一行，`action_count=0`、`actions=[]`，让引擎能按 id 直接查空表。

**字段来源标记三分**（每处数值都要带，不允许裸数字）：

| 值 | 含义 |
| --- | --- |
| `lpc` | 原版 LPC 里直接写死的字面量，原样搬运 |
| `derived` | 原版只给了公式/结构，数值是**我们按源码公式解释出来的**（如线性插值） |
| `default` | 原版缺键，按 LPC mapping 语义取默认 `0`；缺键本身记入 `coefficient_gaps` 并进 `diagnostics.jsonl` |
| `missing` | 文案缺失（仅 `name_source` 使用），没有可搬运原文 |

`lpc` 与 `derived` 必须分开读：将来数值对不上时，前者是抽取错误，后者是解释口径错误。

### 9.1 技能级字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 技能 id，与 `skills.json` 主键一致 |
| `name` | string \| null | 中文名 |
| `martialtype` | string | 原版 `martialtype()` |
| `enable` | string[] | 可启用基础技能，决定可用武器/招式类型 |
| `action_count` | int | `actions` 条数 |
| `coefficient_model` | object \| null | 系数来源模型，见 9.2 |
| `requirements` | object | 学习前置，见 9.3 |
| `effects` | array | perform 附加效果，见 9.4 |
| `actions` | array | 普通招式，见 9.5 |
| `special_actions` | array | 特殊分支招式，见 9.6 |
| `coefficient_gaps` | string[] | 源码缺键的系数名；对应 `default`，引擎按 0 |
| `source` | object | 源码位置 |

### 9.2 `coefficient_model`

```json
{ "mode": "linear_seq", "formula": "e1 + (e2 - e1) * seq / ttl",
  "params": { "force": {"e1": 140, "e2": 240}, ... },
  "default_return": { "damage": 0, "dodge": null, "force": null, "parry": null } }
```

- `mode="linear_seq"`：原版按 `seq = random(可选招式数)` 在首末系数之间线性插值；`seq` 取 0..ttl-1，LPC 整除向零截断。`params[field]` 是 `{e1,e2}` 首末值。`formula` 是**我们的解释式**，不是原版字符串。
- `mode="embedded"`：系数直接写在每个 action 条目里，没有统一公式；`params` 为空。
- `mode=null`：无 `action` 数组。
- `default_return[field]`：`null` 表示原版函数该分支不返回此键（引擎不取值）；`0` 表示原版 mapping 缺键，LPC 取 0。

四个系数的引擎语义（对应 `ACTION_SEQUENCE`）：

| 键 | 作用 |
| --- | --- |
| `dodge` | 修正受害者闪避 `dp` 的百分比：`dp = (100 + dodge) * dp / 100` |
| `parry` | 修正受害者招架 `pp` 的百分比：`pp = (100 + parry) * pp / 100` |
| `force` | 命中后 `damage_bonus += force * damage_bonus / 200` |
| `damage` | 兵器基础伤害 `damage += damage * damage / 200`；拳脚招式没有这一项（缺键即 0） |

### 9.3 `requirements`

```json
{ "weapon_skill_type": null,
  "learn": { "parsed": true,
             "skills": [{"skill": "yunlong-shengong", "min_level": 50}],
             "max_neili": 250 },
  "source_kind": "lpc" }
```

- `weapon_skill_type`：从 `enable` 推导出的兵器类型（`sword`/`blade`/…）；null 表示拳脚/内功类，不要求武器。
- `learn.parsed`：能否静态解析 `valid_learn`。`false` 时 `learn` 里只保留能读到的字段，读不到的进 `diagnostics.jsonl`，引擎不得自行假设等级。
- `learn.skills[]`：前置技能与最低等级；`learn.max_neili` 等属性键若出现即为源码要求。

### 9.4 `effects[]` — perform 附加效果

```json
{ "name": "刺腕", "perform": "ciwan", "flag": "bixie/ciwan",
  "effect": "victim_cannot_parry", "effect_source": "derived",
  "meaning": "命中后目标无法招架",
  "duration": { "base": 6, "random": 6 },
  "requirements": { "parsed": true, "weapon_skill_type": "sword",
                    "force_skill": "kuihua-xinfa", "max_neili": 600, "neili": 400,
                    "skills": [{"skill": "bixie-jian", "min_level": 120}] },
  "requirements_source": "lpc" }
```

- `flag`：原版 `set_temp("…",1)` 的键。引擎命中后据此跳过受害者的闪避/招架判定。
- `effect` / `effect_source`：`victim_cannot_dodge`、`victim_cannot_parry` 是我们的解释名，故标 `derived`。
- `duration`：原版 `call_out(..., base + random(random))`；`random=0` 表示固定时长。
- 当前只有 `bixie-jian` 的 `cizu`/`ciwan` 两条；没有 perform 效果就是空数组。

### 9.5 `actions[]`

```json
{ "index": 0, "name": "草木皆兵", "name_source": "lpc",
  "skill_level_required": 0, "skill_level_source": "lpc",
  "coefficients": { "dodge": -45, "parry": 15, "force": 140, "damage": 0 },
  "coefficient_source": { "dodge": "derived", "parry": "derived",
                           "force": "derived", "damage": "default" },
  "text": "$N一式「草木皆兵」…", "text_expr": null, "text_source": "lpc",
  "damage_types": [], "source": {"path": "kungfu/skill/yunlong-shou.lpc", "line": 10} }
```

- `name`：招式名，取自 `skill_name`。
- `skill_level_required`：原版 `lvl`，低于此等级不会入选。
- `text` 是可直接展示的文案；`text_expr` 是含 `order[random(13)]` 等动态片段的原版表达式（`text_source="derived"`），引擎想要逐字复刻时用它，否则用 `text`。
- `damage_types`：原版 `damage_type`，如 `["割伤"]`；空数组表示未限定。

### 9.6 `special_actions[]`

```json
{ "condition": "me->query_temp(\"double_attack\")", "condition_source": "lpc",
  "coefficients": { "dodge": 20, "parry": 20,
                     "force": {"min": 350, "max": 449},
                     "damage": {"min": 100, "max": 149} },
  "damage_types": ["割伤"],
  "source": {"path": "kungfu/skill/taiji-jian.lpc", "line": 247} }
```

- 原版 `query_action` 里 `if` 分支直接返回的招式，不走线性插值。
- `coefficients` 的值为 int 或 `{min,max}`；后者原版是随机区间，引擎取该区间内随机整数。
- `condition` 是原版条件表达式原文，`condition_source="lpc"`；引擎可选择实现或忽略，但不得改写成别的条件。

## 10. `skills_stats.json` — 技能属性表

48 条，一行 = 一个技能。与 `skills_actions.json` 按 `id` 一一对应（抽取器对两侧 id 集合做双向校验，缺一即 error）。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` / `name` | string | 同 `skills.json` |
| `type` | string | 原版 `type()` |
| `martialtype` | string | 原版 `martialtype()` |
| `enable` | string[] | 原版 `valid_enable` |
| `category` | `"force"` \| `"dodge"` \| `"weapon"` \| `"unarmed"` \| `"action"` \| `"knowledge"` | 引擎技能类别 |
| `category_zh` | string | 中文类别：内功/轻功/兵器/拳脚/招式/知识 |
| `category_source` / `category_basis` | string | `derived` + 推导依据（`martialtype=` / `enable=` / 基础技能 id） |
| `action_count` | int | 该技能的普通招式条数 |
| `attack` | object | `{"damage":{"min","max"},"force":{"min","max"},"source_kind","reason"}` |
| `defense` | object | `{"dodge":{"min","max"},"parry":{"min","max"},"source_kind","reason"}` |
| `apply` | object | 原版 `add_temp("apply/*")` 加成，如 `{"attack": 10}` |
| `apply_source` / `apply_reason` | string | `lpc` 或 `default`（空表 + 原因） |
| `map_skills` | object | `{"mapped_from":[],"mapped_to":[],"source_kind":"derived","basis"}` |
| `max_level` | object | `{"value":800,"source_kind":"default","reason"}` |
| `curve` | object | 经验曲线参数，见 10.1 |
| `hooks` | object | 五个战斗钩子，见 10.2 |
| `gaps` | array | 该行的缺口摘要（如招式系数缺键） |
| `source` | object | 源码位置 |

- `attack` / `defense`：从 `actions` 系数的首末范围取 `min/max`；无招式序列的技能显式 `0/0` + `reason`，不是留空。
- `map_skills.mapped_from`：哪些基础技能映射到本技能（如 `["unarmed"]`）；`mapped_to`：本技能作为基础技能时被哪些技能映射。由 `npcs.json` 的 `map_skill` 反推，故标 `derived`。
- `max_level`：原版技能 LPC 不定义上限，统一沿用引擎 `SKILL_MAX=800`，标 `default` 并写原因。

### 10.1 `curve`

```json
{ "learn_bonus":   { "value": 15, "source_kind": "lpc" },
  "practice_bonus":{ "value": 15, "source_kind": "lpc" },
  "power_point":   { "kind": "constant", "value": 1.3, "expr": null, "source_kind": "lpc" },
  "source_kind": "lpc" }
```

- `learn_bonus` / `practice_bonus`：原版 `return 整数字面量`；读不到时显式 `value=0, source_kind="default", reason`。
- `power_point`：轻功威力系数，`skill_power` 防御计算时乘到等级上并夹在 `[1.0, 1.5]`。
  - `kind="constant"`：`value` 是字面量。
  - `kind="rules"`：`rules[]` 依 `condition` 命中，未命中取 `default`；`default=null` 时用 `default_expr`（原版表达式，静态不求值）。
  - `kind="expression"`：只有 `expr`，没有常量——引擎必须实现该表达式，**不许自行给默认系数**。
- 读不到的项一律带 `reason`；`validate` 会把“default 但没原因”判成 error。

### 10.2 `hooks` — 五个战斗钩子

键固定为 `force_hit`、`victim_force_hit`、`martial_hit`、`victim_parry_hit`、`weapon_hit`，一行里五项必须齐全。

```json
"force_hit": { "implemented": true,
               "owner": "inherit/skill/force.lpc hit_ob",
               "owner_line": "继承 FORCE 的 hit_ob",
               "value": null, "value_kind": "code", "expr": "int damage;\n…",
               "source_kind": "lpc",
               "reason": "钩子函数存在；值由源码表达式实时计算，静态不折算常量" }
```

| 字段 | 含义 |
| --- | --- |
| `implemented` | 源码里是否存在可执行的钩子函数 |
| `owner` | 函数来自哪里：技能自身 `<self>.lpc` 或继承的 `inherit/skill/force.lpc` |
| `value_kind` | `code` 或 `default` |
| `value` | `code` 时为 null（运行时求值）；`default` 时为 0 |
| `expr` | `code` 时为去掉注释的函数体原文，引擎照此实现 |
| `reason` | `default` 必须写清为什么是 0（缺函数 / 被注释 / 武器对象提供） |

当前实现状态：6 个内功的 `force_hit` 继承 `inherit/skill/force.lpc`（`value_kind=code`）；`mantian-xing`、`yunlong-zhua` 自身定义 `hit_ob`，故 `martial_hit` 为 `code`；`victim_force_hit` 因原版 `be_hit_ob` 整段被注释，全部 `default=0`；`weapon_hit` 由武器对象提供，技能表不可得，全部 `default=0`。

加载器约束：`value_kind=default` 的钩子不要当成“已实现但效果为 0”，它是**明确占位**；`code` 的钩子不能只读 `value`，必须执行 `expr` 或引擎内等价实现。

## 11. `diagnostics.jsonl` / `summary.json` / `manifest.json`

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

## 12. 本内容包不承诺的内容

- 战斗公式的具体求解、技能升级/经验结算流程：属于引擎。本包只提供招式序列、系数、属性与钩子出处；凡引擎侧读不到的，表里已显式标 `default` 并写明原因，未标的一律有源码依据。
- 任务、剧情线、条件触发器、函数型对话的实际行为。
- 商店议价、库存刷新、门派拜师流程。
- 区外区域的地图与内容：只提供 6 个 boundary stub 和 25 条区外引用，详见 `boundary-and-external-refs.md`。
