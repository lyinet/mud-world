# XKX Parity：疗伤与伤势恢复手段（规格）

- 状态：**规格冻结，未实现**（本文不含代码改动）
- 日期：2026-11-09
- 基线分支：`origin/xkx-parity@3d06896`（+ 已合入的 jing 五段：`feat/xkx-jing` 后续单独合入前，本规格按当前 HEAD 取证）
- 原版参考树：`world-sim/tools/xkx_ref/src/xkx100/`
- 相关既有规格：`specs/xkx-parity/jing-eff-jing.spec.md`、`specs/xkx-parity/eff-qi.spec.md`

---

## 1. 结论摘要（六条）

1. **内容包没有任何疗伤物品**。`content/xkx/items.json` 共 85 条：`armor` 29 / `weapon` 27 / `misc` 29，`category=medicine` **0 条**；`apply` 键只有 `apply/armor`、`apply/damage`、`apply/personality`，**无任何 `heal`/`cure`/`wound`/`eff_qi` 效果字段**。四味药草（`baiwei`/`baizhi`/`heye`/`hugu`）是 `misc`、`apply={}`、`food_supply=null`。
2. **内容包有 6 个可落地的疗伤 `exert`**：基础 `force` 的 `recover`（qi）与 `regenerate`（jing），以及 `bibo-shengong`/`hunyuan-yiqi`/`taiji-shengong`/`yunlong-shengong` 的 `heal`（自疗 qi）、`hunyuan-yiqi`/`huntian-qigong` 的 `lifeheal`（他疗 qi）。公式、门槛、内力消耗、busy 已逐行取证（§4）。
3. **没有 `heal`/`cure`/`apply`/`medicate` 指令**。`cmds/std/` 只有 `eat.lpc`、`drink.lpc`；疗伤唯一入口是 `cmds/skill/exert.lpc`（`exert|yun <功能>`），调度在 L36/L41。
4. **两条恢复通道语义不同，我们的现有原语都不等价**：`receive_heal` 把**当前值**抬到 `eff_*` 为上限、返回入参 `heal`；`receive_curing` 把 **`eff_*`** 抬到 `max_*` 为上限、返回**实际增量**。我们的 `hp_add` 只封 `max_hp`、**不封 `eff_hp`**，因此**不等价** `receive_heal`；`heal_apply` 是 `heal_up` 的原子快照，不是通用恢复原语。
5. **玩家侧现在六条路径全部走不通**：玩家 `max_neili=0`（`xkx.yaml` 的 `player_start` 只给 `unarmed/dodge/parry: 40`）、内容 `skills.json` 的 48 条只有 7 个键（无 `perform` 数据，`heal`/`lifeheal` 的玩家闸门 `perform/heal` 无法满足）、物品层无药。**要让玩家真能疗伤，必须补内容或另开内力（dazuo）规格**——见 §6.5 的三个选项，需拍板。
6. **落地方案**：新增独立开关 `combat.healing`（默认 `false`，依赖 `combat.eff_qi`，缺失时 `warnings.warn` 并降级）+ `Entity.busy_until` + 7 个 effect 原语（`qi_heal/qi_cure/jing_heal/jing_cure/neili_add/neili_set/busy_set`）+ `ExertAction` + `heal_up` 补 `max_neili//10` 项。关闭态逐字节不变；**打开后旧九地点基线也应保持 `735 / 009076f0… / 02d5f812… / 11442 / 6 / 3`**（该 run 无战斗，且本阶段不给 AI 加自疗决策），变了即缺陷。

---

## 2. 原版恢复原语（`feature/damage.lpc`）

### 2.1 `receive_heal(type, heal)` —— L71-84

```
val = query(type) + heal;
if (val > query("eff_" + type)) set(type, eff_type);
else set(type, val);
return heal;                      // 返回入参，不是实际增量
```

- 只动 **当前值**（`qi`/`jing`），`eff_*` 不变。
- 上限是 `eff_*`，不是 `max_*`。
- **边界**：`eff_* = -1`（已死）时会把 `qi/jing` 写成 `-1`，即"疗伤救不回已死"。
- 无下限钳制、无 `type` 之外的校验（仅 `heal<0` 报错）。

### 2.2 `receive_curing(type, heal)` —— L86-103

```
val = query("eff_" + type); max = query("max_" + type);
if (val + heal > max) { set("eff_" + type, max); return max - val; }
else { set("eff_" + type, val + heal); return heal; }
```

- 只动 **`eff_*`**（伤势天花板），`qi/jing` 不变。
- 返回**实际增量**（被 `max_*` 截断时返回余量）。
- 这是"真正把伤治好"的通道；`receive_heal` 只是"把当前气补到伤势允许的上限"。

### 2.3 `heal_up()` —— L323-400

| 行 | 行为 |
|---|---|
| L349-350 | `water -= 1`、`food -= 1`（每次心跳各消耗 1） |
| L352-358 | `water<1` / `food<1` 时玩家与宠物**不恢复** |
| L360-366 | `jing += con/3 + max_jingli/10`；封 `eff_jing` 后 `eff_jing++`（< `max_jing` 时） |
| L368-374 | `qi += con/3 + max_neili/10`；封 `eff_qi` 后 `eff_qi++`（< `max_qi` 时） |
| L376-380 | `jingli` 恢复（`taoism/20`） |
| L382-389 | `neili` 恢复（`force/2 + sta/3`） |
| L391-398 | `tili` 恢复（`parry/2 + sta/3`） |

- **关键**：`heal_up` **不调用** `receive_heal`/`receive_curing`，它直接写字段并手动 `eff_*++`。所以"被动恢复"和"主动疗伤"是两套语义，不能合并成一个原语。
- 我们的 `SystemHealAction`（`src/wsim/actions/system.py:510-622`）只实现了 `con//3`，**漏了 `max_neili//10`**（`max_jingli` 已按 0 处理），且**无 water/food 门**。

### 2.4 两个原语的调用者（全树 `grep` 实证）

`receive_heal(` —— 共 **4 个文件**：
- `kungfu/skill/force/recover.lpc:28`（qi）
- `kungfu/skill/force/regenerate.lpc:28`（jing）
- `d/yixing/skills/lifeheal2.lpc`（门派技能，内容无此技能）
- `feature/damage.lpc`（定义本身）

`receive_curing(` —— 共 **70 个文件**，分四类：
1. 内功 exert：`kungfu/skill/<内功>/{heal,lifeheal,jingheal,wakeup}.lpc`（约 40 门，含内容包没有的门派）
2. 药：`clone/medicine/nostrum/*.lpc`（`baicaodan/gaoyao/jinchuang-yao/jinchuang/sanhuangwan/tianwangdan/yangjing`）、`clone/medicine/vegetable/*.lpc`（`dongchongcao/huangqi/laoshanshen/renshen/xuelian`）、`inherit/medicine/pill.lpc`
3. 地点药：`d/{emei,jiangling,kunlun,lingjiu,yanziwu}/obj/*.lpc`（`caoyao/tianxiang-gao/zhongyao/danggui/hongshen/jinchai/tianqi/baiyunwan/heiyugao`）
4. 条件/救场：`kungfu/condition/{bandaged,jing_curing}.lpc`、`feature/damage.lpc:177-190`（鬼魂/擂台各 `receive_curing("qi|jing", 10)`）、`cmds/wiz/wake.lpc`

---

## 3. 物品取证（`content/xkx/items.json`）

| 项 | 值 |
|---|---|
| 总条数 | 85 |
| 分类 | `armor` 29、`weapon` 27、`misc` 29，**`medicine` 0** |
| 全部 `apply` 键 | `apply/armor`、`apply/damage`、`apply/personality` |
| 含 `heal`/`cure`/`wound`/`recover` 字段 | **0** |
| 带 `food_supply` | 12 条（如 `/d/hangzhou/npc/obj/baozi`:20、`/d/hangzhou/npc/obj/chicken`:100） |

四味药草（`category=misc`、`apply={}`、`food_supply=null`）：

| id | 名称 | price | 源 |
|---|---|---|---|
| `/clone/medicine/vegetable/baiwei` | 白薇 | 1200 | `clone/medicine/vegetable/baiwei.lpc:12` |
| `/clone/medicine/vegetable/baizhi` | 白芷 | 900 | `.../baizhi.lpc:12` |
| `/clone/medicine/vegetable/heye` | 荷叶 | 5 | `.../heye.lpc:12` |
| `/clone/medicine/vegetable/hugu` | 虎骨 | 30000 | `.../hugu.lpc:12` |

> `content/items.yaml`（另一内容根，非 xkx 世界）虽有 `wound_powder`（金疮药）/`ginseng`/`plaster` 等 `category: medicine` 条目，但**只有 `base_price`、无任何效果字段**；且 `xkx.yaml` 的 `content_root_candidates` 首选 `content/xkx`，这些条目不会进入 xkx 世界。

**原版药品效果（供将来补内容时对齐，本阶段不实现）**：

| 文件 | 行为 |
|---|---|
| `clone/medicine/nostrum/jinchuang.lpc:28-46` | 非 busy、`eff_qi < max_qi` → `receive_curing("qi", 50)`、`start_busy(2)`、`destruct` |
| `clone/medicine/nostrum/sanhuangwan.lpc:23-42` | `receive_curing("jing", 50)` + `receive_curing("qi", 100)` |
| `inherit/medicine/pill.lpc:15-21` | 通用丹：`receive_curing("qi", 50)` |

---

## 4. 技能/指令取证

### 4.1 唯一入口：`cmds/skill/exert.lpc`

| 行 | 行为 |
|---|---|
| L9-23 | `main(me, arg)`；busy 拒绝；无 `arg` 拒绝 |
| L28 | 取 `query_skill_mapped("force")`，**必须先 `enable` 内功** |
| L36 | `SKILL_D(force)->exert_function(me, arg)`（所选内功优先） |
| L41 | 回退 `SKILL_D("force")->exert_function(me, arg)`（基础内功通用功能） |
| L55-56 | 帮助：`exert \| yun <功能名称> [<对象>]` |

`kungfu/skill/force.lpc:15` 的 `exert_function_file(func)` 把功能名映射到 `kungfu/skill/force/<func>.lpc`，因此 `exert recover|regenerate|revive` 落到基础内功目录。

### 4.2 基础内功（无 perform 闸门，全员可用）

| 功能 | 文件 | 门槛 | 消耗 | 效果 |
|---|---|---|---|---|
| `recover` | `force/recover.lpc:7-33` | `neili>=20`；`q=eff_qi-qi`，`q<10` 拒绝 | `n=100*q/force_level`（最少 20；`n>neili` 时 `q=q*neili/n`、`n=neili`），`neili-=n` | `receive_heal("qi", q)`；战斗中 `start_busy(1)` |
| `regenerate` | `force/regenerate.lpc:7-33` | 同上，`j=eff_jing-jing` | 同上 | `receive_heal("jing", j)`；战斗中 `start_busy(1)` |
| `revive` | `force/revive.lpc:7-33` | `neili>=20`；`max_tili-tili>=10` | `n=max(force_level,20)` 上限 neili | `tili += n/10`（**体力，不是气血**） |

### 4.3 内容包内的内功疗伤（6 个文件 / 5 门内功）

自疗 `heal`（`bibo-shengong`/`hunyuan-yiqi`/`taiji-shengong`/`yunlong-shengong` 四门公式完全一致）：

| 条件 | 值 |
|---|---|
| perform 闸门 | `userp(me) && !wizardp && !query("perform/heal") && !query("can_perform/<skill>/heal")` → 拒绝（L8-11；**NPC 因 `userp` 为假而绕过**） |
| 状态 | 非战斗 L13、非 busy L16 |
| 技能 | `<skill> >= 20`（L19） |
| 内力 | `neili >= 50`（L22） |
| 伤势 | `eff_qi >= max_qi/2`（L28；L25 的"未受伤"判断被注释） |
| 效果 | `receive_curing("qi", 10 + force_level/5)`（L34/36/34/33） |
| 消耗 | `neili -= 50`、`jiali=0`、`start_busy(1)` |

他疗 `lifeheal`：

| 技能 | 门槛 | 效果 | 消耗 |
|---|---|---|---|
| `hunyuan-yiqi`（`lifeheal.lpc:28-55`） | 双方非战斗（L19）、双方非 busy（L22/L25）、`skill>=20`（L28）、`max_neili>=300`（L31）、`neili>=150`（L34）、目标 `eff_qi >= max_qi/5`（L40） | `receive_curing("qi", 10+force/3)` + `add("qi", 10+force/3)` 封 `eff_qi`（L49-52） | `neili -= 100`、`busy(1)`、目标 `busy(2)` |
| `huntian-qigong`（`lifeheal.lpc:29-57`） | 同上，`skill>=20` / `max_neili>=300` / `neili>=150` / 目标 `eff_qi>=max_qi/5` | `receive_curing("qi", 10+force/3)` + `add("qi", 10+force/4)`（L50-53） | `neili -= 150`、`busy(1)`、目标 `busy(2)` |

### 4.4 内容缺口（决定边界）

- `content/xkx/skills.json` 共 **48** 条，键只有 `enable/id/martialtype/name/source/type/used_by`。**没有** `xiantian-qigong`、`shenzhao-jing`、`jiuyang-shengong`、`yijinjing`；**没有 `force` 以外的 `perform`/`can_perform` 数据**。
- `content/xkx/skills_actions.json`、`skills_stats.json` 对 `heal/lifeheal/jingheal/wakeup/regenerate/recover/cure` 的命中数均为 **0**（这两份文件只收录攻击招式与命中钩子，不含 exert 功能）。
- 因此：**30+ 门其他门派 `heal/lifeheal/jingheal/wakeup` 全部"内容未提供"**；`xiantian-qigong`、`shenzhao-jing` 的救醒/他疗也不可用。
- 玩家 `neili` 依赖：`cmds/skill/exercise.lpc`（`exercise|dazuo`）L57-100 提供内力增长（`neili += 1 + force/10`，耗 qi；蓄到 `max_neili*2` 才突破 `max_neili`，上限 `force*10`），**本阶段不实现**。

---

## 5. 现状对照：我们 vs 原版

| 我们 | 位置 | 原版等价物 | 是否等价 |
|---|---|---|---|
| `hp_add` | `world/effects.py:564-585` | `receive_heal("qi", n)` | **否**：`hp_add` 封 `max_hp`（或 `floor=-1`），**不封 `eff_hp`**；`receive_heal` 封 `eff_qi` |
| `hp_set` | 同上 | `set("qi", v)` | 等价（但同样不封 eff） |
| `eff_hp_add` | `effects.py:587-598` | `receive_curing("qi", n)` 的写入部分 | **部分**：钳制区间 `[-1, max_hp]` 一致；但 `receive_curing` 需**返回实际增量**，`eff_hp_add` 无返回值 |
| `eff_hp_set` | 同上 | `set("eff_qi", v)` | 等价 |
| `jing_add` / `eff_jing_add` | `effects.py:600-631` | `receive_heal`/`receive_curing`("jing") | 同 qi 组：均不封 eff / 不返回增量 |
| `heal_apply` | `effects.py:633-647` | `heal_up` 的落值快照 | 仅等价"被动心跳"，**不是**通用恢复原语 |
| `SystemHealAction` | `actions/system.py:510-622` | `heal_up`（damage.lpc:323-400） | **缺** `max_neili//10` 项（`max_jingli` 按 0 已对齐）；**无** water/food 门 |
| `PlayerRestAction` sleep | `actions/player.py:637+` | `cmds/std/sleep.lpc:196-213` | 一致：只把 `qi/jing` set 到 `eff_*`，**不抬 eff** |
| `use_item` | `actions/equipment.py:345-385` | 药品 `do_eat` | **否**：只加 `stamina`，无 `receive_curing` |
| `ConsumeAction` | `actions/basic.py:257-307` | `eat/drink` | **否**：只加 `stamina` |
| `neili/max_neili` | `world/state.py:104-105` | `neili/max_neili` | 字段有、**无任何写原语**（只有 `create_entity/vitals_set` 落值） |
| `busy` | — | `start_busy(n)` | **不存在**（只有 `last_action_tick`） |
| `jingli/max_jingli/tili/max_tili/water/food` | — | damage.lpc:349-398 | **不建模** |

---

## 6. 落地方案

### 6.1 开关：`combat.healing`（独立开关，默认关闭）

- 位置：`config/worlds/xkx.yaml` 的 `combat:` 段，紧跟 `jing`。
- 语义：`healing=true` 依赖 `combat.eff_qi`；未开 `eff_qi` 时 `warnings.warn(...)` 并降级为 `False`（与 `jing` 的既有做法一致，`api/session.py:585-588`）。
- **是否需要独立开关：需要**。理由：本项新增 7 个 effect op、1 个 verb、1 条 `heal_up` 公式变更；独立开关让"关闭态零字段/零事件"可被单测锁死，也允许疗效验收与代码合并解耦（沿用 jing 五段的做法）。
- 加载：`session.py` 增加 `_load_healing(path)`（照 `_load_jing` 写），`build_registry(..., healing=...)`（`actions/registry.py:46`）。

### 6.2 `Entity` 增量（`world/state.py`）

| 字段 | 处理 |
|---|---|
| `busy_until: int = 0` | **新增**；`to_dict()` 仅 `!= 0` 时输出；旧档缺省 0 = 不忙 |
| `neili` / `max_neili` | 复用（L104-105），不新增 |
| `jingli` / `max_jingli` | 不新增；`max_jingli ≡ 0`（延续 jing 规格的"未建模"声明） |
| `tili` / `max_tili` | 不新增（`stamina` 是另一套模型，不做映射） |
| `water` / `food` | 不新增（见 §7） |
| `heal_credit` / `heal_cd` | 复用 |

### 6.3 `effects.py` 新增原语（7 个）

| op | 语义 | 边界 |
|---|---|---|
| `qi_heal` | `qi = min(qi+delta, eff_cur)`；`data["healed"]=delta` | `eff_cur = eff_hp>= -1 ? eff_hp : max_hp`；`eff=-1` 时结果 `qi=-1`（复刻原版"救不回已死"） |
| `qi_cure` | `eff_hp = min(eff_cur+delta, max_hp)`；`data["healed"]=实际增量` | 下限 `-1` |
| `jing_heal` | `jing = min(jing+delta, eff_jing_cur)` | 需 `max_jing>0` |
| `jing_cure` | `eff_jing = min(eff_jing_cur+delta, max_jing)`；写回实际增量 | 需 `max_jing>0` |
| `neili_add` | `neili = clamp(neili+delta, 0, max(max_neili, neili))` | 原版 `add("neili",-n)` 可到 0；不新增字段 |
| `neili_set` | 直接落值（`0..max_neili`） | 供 dazuo 将来复用 |
| `busy_set` | `busy_until = max(busy_until, tick + rounds)` | 仅非零序列化 |

> `healed` 用实际增量写回事件，保证回放对账（与 `hp_add` 把钳制后的 `delta` 写回同一原则）。

### 6.4 动作层：`ExertAction`（verb `exert`，别名 `yun`）

`actions/skills.py`（新文件）或 `actions/combat.py` 内新增；注册于 `build_registry`。

```
preconditions:
  - ent.status == alive
  - healing 开关打开（否则 not_available）
  - func = args["func"]；target = args.get("target") 或 self
  - busy_until > tick → "busy"
  - 按功能表逐条校验（见下）
apply:
  - neili_add / qi_heal / qi_cure / busy_set 组合
  - 目标必须同地点、alive（lifeheal）
```

功能表（**本阶段只实现 `recover` / `regenerate`**；门派自疗/他疗按 2026-11-09 收窄指令不做，见 §8）：

| `func` | 判定 | 效果 |
|---|---|---|
| `recover` | `neili>=20`；`q = eff_qi - qi`；`q>=10` | `n = 100*q // force_level`，`n<20 → 20`；`n>neili → q = q*neili//n, n=neili`；`neili_add(-n)`、`qi_heal(q)`；战斗中 `busy_set(1)` |
| `regenerate` | 同构，用 jing/eff_jing | `jing_heal(j)` |

- `force_level(ent)`：优先 `ent.skill_levels.get("force")`，回退 `int(ent.skills.get("force", 0.0) * 100)`（`world/state.py:59` 的既有回退约定）。
- `force_level<=0` → `not_qualified`（原版 `100*q/force` 会除零；玩家只有 40 级 `force` 时正常）。
- 未知功能（`heal`、`lifeheal`、`roar`、`transfer`、`bipin`、未列入的内功）→ `no_such_function`，不猜效果。

### 6.5 玩家可玩性缺口（必须拍板）

现状：玩家 `max_neili=0`、无 `force` 技能、无 `perform` 标记、内容无药 → 上面 6 个功能对玩家全部 `not_qualified`。三个选项：

| 选项 | 内容/代码 | 影响 |
|---|---|---|
| **A（最小）** | 只落 §6.3/§6.4 + `heal_up` 公式；不改内容 | 玩家仍无法主动疗伤；仅 NPC 可用（无 AI 调用）→ **没有玩家可见收益** |
| **B（已批准，2026-11-09）** | A + 内容补丁：`xkx.yaml` 的 `player_start` 加 `force: 40`、`max_neili: 200`（不改 `content/xkx/**`） | 玩家可用 `exert recover`（q≥10 时一次回 `min(q, 内力)`）；**xkx 世界基线会变**（预期，需重录）；旧九地点不受影响 |
| **C（最大）** | A + 独立 `dazuo/内力` 子系统（`cmds/skill/exercise.lpc` 语义） | 玩家自 0 起修内力，最贴近原版；工作量另立规格 |

> 本规格默认 **B**，但 B 的内容补丁属"内容生产"，需单独批准后作为独立提交，不与代码混合。

### 6.6 `heal_up` 公式对齐

`SystemHealAction`（`actions/system.py:596`）改为：`qi += con//3 + max_neili//10`；jing 分支保持 `con//3`（原版是 `max_jingli//10`，`max_jingli ≡ 0`，不掺 `max_neili`）。留在 `combat.healing` 开关内，关闭态公式不变。

### 6.7 旧档兼容

- `busy_until=0` 缺省 → 旧档"从不忙"；`to_dict()` 不输出。
- `neili=0`/`max_neili=0` 缺省 → `recover` 自然拒绝，不产生事件。
- 新开关缺省 `false` → 零新字段、零新事件、零额外骰。
- `heal_apply` 的 `jing/eff_jing` 写回沿用"事件显式带键才写"的既有规则（`effects.py:643-646`）。
- `qi_heal` 的 `eff_hp=-2`（未初始化）按 `max_hp` 处理，与 `healing_ceiling` 一致。

---

## 7. 验收口径

### 7.1 单元级场景（`Writer(state, store, reg, seed=42)`，确定性，与墙钟无关）

| # | 场景 | 输入 | 期望原始值 |
|---|---|---|---|
| H1 | `qi_heal` 封 eff | max=100, eff=80, qi=50, delta=50 | `qi=80`，`healed=50` |
| H2 | `qi_heal` 已死 | eff=-1, qi=10, delta=50 | `qi=-1`（原版行为，不救活） |
| H3 | `qi_cure` 截断 | max=100, eff=80, delta=50 | `eff_hp=100`，`healed=20` |
| H4 | `qi_cure` 不截断 | max=100, eff=30, delta=50 | `eff_hp=80`，`healed=50` |
| H5 | `recover` 正常 | force=50, neili=100, max_qi=100, eff=80, qi=50 | q=30, n=60 → `neili=40`, `qi=80` |
| H6 | `recover` 内力不足 | force=50, neili=30, eff=80, qi=20 | q=60, n=120 → q=15, n=30 → `neili=0`, `qi=35` |
| H7 | `recover` 无需疗伤 | q=9 | 拒绝 `not_injured`，零效果 |
| H8 | `recover` 内力门槛 | neili=19 | 拒绝 `insufficient_neili` |
| H9 | `recover` n 下限 | force=200, eff=80, qi=60 → q=20, n=10→20 | `neili -= 20`, `qi=80` |
| H10 | `regenerate` | force=50, neili=100, jing=50, eff_jing=80 | `jing=80`, `neili=40` |
| H11 | `heal` 门槛 | hunyuan-yiqi=20, force=50, neili=100, eff=40/max=100 | 拒绝 `too_injured`（< max/2） |
| H12 | `heal` 正常 | eff=50/max=100, neili=100 | `qi_cure +20` → eff=70；`neili=50`；`busy_until=tick+1` |
| H13 | `lifeheal` | hunyuan, max_neili=300, neili=200, force=50, target eff=50/max=100 | curing 26 → eff=76；`qi_heal 26` → qi=76；`neili=100`；目标 `busy_until=tick+2` |
| H14 | `heal_up` 公式 | con=20, max_neili=500, qi=10, eff=80, max=100 | 单次触发 `qi=66`（6+50） |
| H15 | `heal_up` 无内力 | con=20, max_neili=0, qi=10, eff=80 | `qi=16`（6+0） |
| H16 | 关闭态 | `healing=false` | 无 `busy_until` 键、无新事件、`to_dict()` 与旧档逐字节一致 |

### 7.2 世界级基线

- 脚本：`/tmp/legacy_twice.py`（`run_headless(days=2, seed=42, npc_limit=0)` 连跑两次）。
- **旧九地点必须不变**：`735 / 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9 / 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae / 11442 / 6 / 3`。
- 判定理由：该 run 无战斗（jing 开关打开后事件数仍为 735 即证据），且本阶段**不给 AI 加 `exert` 决策**，所以即便 `healing=true` 也不应产生任何疗伤事件。**若开关打开后基线变化 → 停止并上报，不许改基线。**
- 若采纳 §6.5 选项 B：xkx 世界（`player_start` 变更）会出新哈希，需按新值重录并单独报告，旧九地点仍按上式校验。

### 7.3 每段纪律（沿用 jing）

`pytest` 全绿；`grep -rn "FAKE" src/ tests/` 无残留；段 1–3 不改 `xkx.yaml`；开关只在最后一段单独提交打开。

---

## 8. 明确不做（内容未提供 / 本阶段不实现）

| 类别 | 清单 | 原因 |
|---|---|---|
| 门派自疗/他疗 | `bibo-shengong`/`hunyuan-yiqi`/`taiji-shengong`/`yunlong-shengong` 的 `heal`，`hunyuan-yiqi`/`huntian-qigong` 的 `lifeheal` | 公式已取证（§4.3），但 2026-11-09 收窄指令明确"与本阶段无关，不要实现"；留待后续 |
| 内功疗伤 | `xiantian-qigong/{heal,lifeheal}`、`shenzhao-jing/{heal,wakeup,jingheal}`、`king-of-dragon/wakeup`、`jiuyang-shengong/lifeheal`、`yijinjing/heal`、`linji-zhuang/lifeheal`、`guiyuan-tunafa`、`bahuang-gong`、`beiming-shengong`、`dulong-dafa`、`hamagong`、`honghua-shengong`、`huiyan-xinfa`、`kuihua-xinfa`、`kurong-changong`、`longxiang`、`panshi-shengong`、`qingming-xuangong`、`shenyuan-gong`、`wudu-shengong`、`xiaowuxiang`、`xuantian-wuji`、`yunv-xinfa`、`zhanzhuang-gong`、`zixia-shengong`、`baiyun-xinfa`、`bingxue-xinfa`、`hanbing-zhenqi` | 这些技能 id 不在 `content/xkx/skills.json` 的 48 条内；不按名字猜公式 |
| 门派/地点技能 | `d/yixing/skills/lifeheal2.lpc`、`d/yixing/npc/cl_bei.lpc` 等 | 内容无对应技能/ NPC 行为 |
| 药品 | `clone/medicine/nostrum/*`（7）、`clone/medicine/vegetable/*`（5）、`inherit/medicine/pill`、`d/{emei,jiangling,kunlun,lingjiu,yanziwu}/obj/*`（8） | `items.json` 0 条 medicine、0 条 heal 字段；不补造物品 |
| 指令 | `heal` / `cure` / `apply` / `medicate` | 原版 `cmds/std/` 不存在这些文件 |
| 条件类恢复 | `kungfu/condition/{bandaged,jing_curing,drunk}.lpc` | 我们的引擎无 condition 模型 |
| 体力通道 | `force/revive`（tili）、`force/transfer`（传内力）、`force/bipin`（比拼内力） | `tili` 未建模；传/拼内力与疗伤无关 |
| 被动恢复细节 | `water`/`food` 消耗与饥渴门（damage.lpc:349-358）、`jingli`、`neili`/`tili` 心跳恢复（L376-398） | 引擎无这些字段；饥渴属另一子系统 |
| 内力获取 | `exercise|dazuo`（`cmds/skill/exercise.lpc`）、`enable` | 独立子系统（§6.5 选项 C），需另立规格 |
| AI 行为 | NPC 受伤后自动 `exert recover/heal` | 会改变世界事件流与基线；本阶段只落原语与显式指令 |

---

## 9. 风险与回滚

- **风险 1**：`qi_heal` 在 `eff=-1` 时把 `qi` 写成 `-1`，若上层未接死亡判定会出现"负血存活"。缓解：只在 `healing=true` 下由 `ExertAction` 发出，并在 `entity_status` 前不越过既有死亡判定。
- **风险 2**：`neili` 是 44 个 NPC 的既有字段，`neili_add` 写 `0` 会进 `to_dict()`（`state.py:227-230` 只在非零时输出）。缓解：`neili_add` 只在开关内发事件；关闭态无事件。
- **风险 3**：内容补丁（选项 B）会改 xkx 世界基线。缓解：内容补丁独立提交、独立报告哈希。
- **回滚**：撤 `combat.healing` 键 → 零事件；回滚代码 → `busy_until` 缺省 0，旧档可读。
