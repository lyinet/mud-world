# Spec · xkx 冒烟四缺陷修复（wimpy / 门派字段 / 大狼狗掉落 / 铁棍战报）

> 分支：`xkx-parity`。顺序：A（缺陷 1、2）先做完先报一次；B（缺陷 3、4）做完再报一次。
> 红线：`tools/xkx_formula/**` 零改动；不改原版系数；显式 `git add`；不 force；旧九地点三项哈希逐字节不变；全量 pytest 全绿。
> 用户明确纠正：**不新增原版没有的 `flee` 命令**；按原版实现 `wimpy` 自动逃跑语义。

## 0. 源码事实（已核实，直接采用）

### 0.1 原版没有玩家级 `flee` / `escape` 命令

- `cmds/std/`、`cmds/usr/` 下不存在 `flee.lpc` / `escape.lpc`。
- `do_flee` 的唯一定义在 [`cmds/std/go.lpc:336-347`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/cmds/std/go.lpc#L336-L347)：
  ```lpc
  exits = environment(me)->query("exits");
  if (!mapp(exits) || !sizeof(exits)) return;
  directions = keys(exits);
  tell_object(me, "看来该找机会逃跑了...\n");
  main(me, directions[random(sizeof(directions))]);
  ```
  即选一个随机出口，走普通 `go`。
- `do_flee` 的调用方只有：
  - [`inherit/char/char.lpc:86-92`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/inherit/char/char.lpc#L86-L92)：心跳里 `wimpy` 触发；
  - 物品效果 [`clone/misc/yanwu.lpc:52`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/clone/misc/yanwu.lpc#L52)、[`d/emei/obj/pilidan.lpc:41`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/d/emei/obj/pilidan.lpc#L41)。
- 玩家可观察入口只有 `wimpy` 命令（见 0.2）；物品特殊效果不在本轮范围。本轮**不实现** `flee` 命令、不保留壳层别名、不加配置开关。

### 0.2 `wimpy` 设置（原版命令）

- [`cmds/usr/wimpy.lpc:7-20`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/cmds/usr/wimpy.lpc#L7-L20)：
  ```lpc
  if (!arg) { write("你现在的当「气」低於 " + (int)me->query("env/wimpy") + "% 时就会尝试逃跑。\n"); return 1; }
  if (sscanf(arg, "%d", wmp) != 1 || wmp < 0 || wmp > 80) return help(me);
  me->set("env/wimpy", wmp);
  ```
- 帮助文本：`wimpy [<逃跑时「气」的百分比>]`，0..80。
- 原版也可用 `set wimpy <int>`（[`cmds/usr/set.lpc:12-26`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/cmds/usr/set.lpc#L12-L26) 白名单含 `wimpy`）；本文字壳没有通用 `set`，本轮实现原版专用命令 `wimpy`，不新增别的设置入口。

### 0.3 自动逃跑（心跳）

- [`inherit/char/char.lpc:80-97`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/inherit/char/char.lpc#L80-L97)：
  ```lpc
  if (is_busy()) { continue_action(); return; }
  if (is_fighting() && wimpy_ratio > 0 &&
      (my["qi"] * 100 / my["max_qi"] <= wimpy_ratio ||
        my["jing"] * 100 / my["max_jing"] <= wimpy_ratio))
    GO_CMD->do_flee(this_object());
  if (living(...)) attack();
  ```
  顺序是：先按气血/精神比例判断、尝试 `do_flee`，再攻击。
- 战斗中逃跑判定在 [`cmds/std/go.lpc:60-79`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/cmds/std/go.lpc#L60-L79)：
  ```lpc
  if (random(me->query("dex")) <= random(f_obs[0]->query("dex"))) {
    me->start_busy(1);
    ... "想走？可没那么容易！"
    return notify_fail("你逃跑失败。\n");
  }
  ```
  **成功 = 玩家 dex 骰 > 敌人 dex 骰；失败 = 小于等于**，失败原地下一次心跳再试。
- 成功移动：[`cmds/std/go.lpc:320-330`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/cmds/std/go.lpc#L320-L330) `move` 成功后 `remove_all_enemy()`；随后 `follow_me`。追击条件 [`feature/team.lpc:26-38`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/team.lpc#L26-L38)：只有 `leader` 或 `pursuer && is_killing`。老虎/毒蛇/大狼狗均未设 `pursuer` → **不追**。

### 0.4 拜师与 `score`

- `bai` 写 `create_faction` + `faction_join` → `Entity.faction`([`src/wsim/actions/growth.py:168`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/growth.py#L168))。
- 私有快照 `_sect_view` 从 `ent.faction` 反查，返回两层 `{"in_sect": true, "sect": {"name": ..., "role": ...}}`（[`src/wsim/api/session.py:1733-1792`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/session.py#L1733-L1792)）。
- `score` 却按一层读 `state["sect"].get("name")`（[`src/wsim/api/xkx_shell.py:1900-1905`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/xkx_shell.py#L1900-L1905)）→ 永远空。**同一事实源，读法错层**。

### 0.5 大狼狗掉落

- 原版 [`d/hangzhou/npc/wolfdog.lpc:38-43`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/d/hangzhou/npc/wolfdog.lpc#L38-L43)：`die()` 里 `new(goupi)` 并 `move(environment(...))`，**确实掉狗皮**。
- 内容包已有真实数据 `content/xkx/npcs.json:8298-8313`：`drops = [{item:/d/hangzhou/npc/obj/goupi, qty:1, chance:1.0, landing:"ground", source.line:41}]`。
- 引擎从 `genesis` → `Entity` → `_drop_counts` 全链路都没消费 `drops`（`src/wsim` 中只有 `combat.py` 掉落逻辑读 `inventory`/`equipped`/`equipment`）。**有数据、没被消费；不需要编数据。**

### 0.6 铁棍战报仍拳脚

- 原版 [`feature/attack.lpc:215-224`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L215-L224)：有映射技能用技能 `query_action`；**没有映射技能时 `set("actions", ob->query("actions", 1))`，由武器对象提供招式**，不回落到拳脚。
- 铁棍继承 [`inherit/weapon/staff.lpc:20-21`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/inherit/weapon/staff.lpc#L20-L21)：`verbs = ({ "bash", "crush", "slam" })`，`query_action` 见 [`adm/daemons/weapond.lpc:88-100`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/adm/daemons/weapond.lpc#L88-L100)，文本 `weapond.lpc:66-80`。
- 内容包 `skills_actions.json` 的 `staff.actions` 为空是**正确**的：基本功 `kungfu/skill/staff.lpc` 本来没有 `query_action`。当前引擎 `combat_rules.py:403-414` 在无映射技能时直接走种族默认招式，漏了“武器自带 actions”层。
- 玩家 `staff` 等级 0 不是原因：原版无等级门槛，0 级照样用武器 verbs。

### 0.7 核查：玩家逃跑时老虎被其他玩家攻击（同队 / 非同队）

> 用户要求本轮**只核查并写进 spec，不实现**。以下是原版源码事实。

- 每个角色的敌人表是自己的 `nosave object *enemy`（[`feature/attack.lpc:9`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L9)）；`fight_ob` / `kill_ob` 只把对方加进**自己**的 `enemy`（[`feature/attack.lpc:33-45`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L33-L45)、[`feature/attack.lpc:47-114`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L47-L114)）。
- 逃跑成功的清理：[`cmds/std/go.lpc:320-323`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/cmds/std/go.lpc#L320-L323) 在 `move` 成功后调用 `me->remove_all_enemy()`；
  [`feature/attack.lpc:171-181`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L171-L181) 会遍历玩家自己的 `enemy`，逐个调用 `敌人->remove_enemy(玩家)`，再清空玩家自己的 `enemy`。
- `remove_enemy` 对“正在追杀该 id”的敌人返回 0（不移除，[`feature/attack.lpc:152-158`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L152-L158)）。但 NPC 每次心跳攻击前先 `clean_up_enemy()`，把**不在同一房间**的敌人清掉（[`feature/attack.lpc:116-130`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L116-L130)、[`feature/attack.lpc:228-241`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L228-L241)）；随后 `select_opponent()` 只从本房敌人里随机 4 选 1（[`feature/attack.lpc:132-140`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L132-L140)）。
- 结论：
  1. **老虎不会追逃跑的 A**。A 换房后即使仍残留在老虎 `enemy` 表（`is_killing` 分支），下一次心跳 `clean_up_enemy` 因不同房清掉；老虎没有 `pursuer`，不主动 move 追人。
  2. **老虎被 B 打时，A 逃跑不会让老虎停手**。老虎 `enemy` 里仍有 B（B 是同队或非同队都一样），继续按 `select_opponent` 打本房的 B。
  3. **同队不改变敌人表的成立条件**。`kill_ob` 只有 `guarded`（被保护者）会把守卫加入战斗（[`feature/attack.lpc:82-112`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/feature/attack.lpc#L82-L112)）；普通队友不会仅因同队自动进 `enemy`。因此“B 与 A 同队”与否，不改变老虎继续打 B 的事实，也不改变 A 是否被追。
- 本轮引擎是单 `combat_target` 模型，没有多敌表；实现上述多人同屏行为需要新增敌人列表与 `clean_up_enemy/select_opponent` 选敌规则。**本轮不实现**，只记录；未来若做，按本节源码顺序建模。

## 1. 缺陷 1：`wimpy` 自动逃跑（不新增 `flee`）

### 1.1 数据与命令

- [`src/wsim/world/state.py`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/state.py#L95-L100)：`Entity` 增 `wimpy: int = 0`（原版 `env/wimpy` 的落地）；`to_dict()` 仅非 0 时写 `wimpy`，旧世界无键。
- [`src/wsim/world/effects.py`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/world/effects.py#L48-L134)：`EFFECT_OPS` 增 `"wimpy_log"`（只读日志）；`_ENTITY_FIELDS` 增 `"wimpy"`；`create_entity` 接受 `wimpy`。
- 新增 [`src/wsim/actions/wimpy.py`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/wimpy.py)：`WimpyAction(verb="wimpy")`
  - 无参数：返回 `info` 行 `你现在的当「气」低于 {n}% 时就会尝试逃跑。`（0 时为 `0%`，即关闭）。
  - 参数：只接受 `0..80` 整数；越界/非整数返回原版帮助文本，不写状态。
  - 合法：`set_field(entity=玩家, field="wimpy", value=n)` + `wimpy_log{action:"set", value:n, previous:old}`。
- `src/wsim/actions/registry.py` 注册 `WimpyAction`；`src/wsim/api/session.py` 的 `WRITE_ACTIONS`/`ACTION_ALIASES` 加 `"wimpy": "wimpy"`；`src/wsim/api/xkx_shell.py` 的 `VERB_ALIASES` 加 `"wimpy": "wimpy"`，`execute` 分派 `_cmd_wimpy` 渲染状态/帮助/成功三态。

### 1.2 自动逃跑落点：NPC 心跳 `continue`

在 [`src/wsim/actions/aggression.py:84-175`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/actions/aggression.py#L84-L175) 的 `continue` 里，结算伤害**之前**插入 `_wimpy_escape(state, npc, target, rng, intent)`（对应 `char.lpc` 心跳顺序：先逃跑、再攻击）：

1. 仅当 `target` 是玩家、`target.wimpy > 0`、`target.max_hp > 0`、`target.hp * 100 <= target.wimpy * target.max_hp` 时触发。原版同时看 `jing`，本引擎只持久化气血；本轮只判定气血，报告注明这一条偏差。
2. 取数顺序锁定（salt 仍 `action:npc_aggression`，同一事件），**与原版 `do_flee` → `main()` 一致：先选方向、再掷 dex**：
   - `exit_index = rng.below(len(sorted_exits))`（按地点 id 排序锁顺序）；
   - `roll_player = rng.below(target.dex)`（`attrs["dex"]`，缺失 0）；
   - `roll_enemy = rng.below(npc.dex)`；
   - 失败条件 `roll_player <= roll_enemy`。
   - `wimpy_log.roll_order = ["exit_index","roll_target","roll_npc"]`，两个 dex 骰与方向骰的先后在事件里显式可读。
   - **失败也消费方向随机数**（`exit_index` 已掷出，只是不移动），与原版
     [`cmds/std/go.lpc:336-347`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/cmds/std/go.lpc#L336-L347)
     先 `directions[random(sizeof(directions))]`、再进
     [`cmds/std/go.lpc:60-79`](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_extract/_src/xkx100/cmds/std/go.lpc#L60-L79)
     掷 dex 的顺序一致。
3. **失败**：产 `wimpy_log{result:"failed", round, exit_index, attempted_exit, rolls}`；不移动、不清交战；本回合继续 `combat_effects`（原版玩家 busy 1 拍，NPC 照常攻击）。
4. **成功**：按已掷出的 `exit_index` 取出口：
   - `set_location(entity=玩家, value=出口)`；
   - 复用 `_reset(npc)` 清 `combat_target/combat_tick/combat_round`（等价 `remove_all_enemy`）；
   - 产 `wimpy_log{result:"success", from/to, rolls}` 与 `aggression_log{result:"escaped", reason:"wimpy"}`；
   - **跳过**本回合 `combat_effects`（玩家已离开，NPC 不再打中）。
   - 不产追击（三名 NPC 无 `pursuer`）。
5. 当前房间无出口 → `wimpy_log{result:"no_exit", roll_order:[]}`，**不掷点**，继续本回合战斗（原版 `do_flee` 无出口直接 return，也逃不掉）。
6. `wimpy == 0` 时**不取任何随机数**，既有主动攻击/战斗的 PRNG 序列逐字节不变（保护现有确定性测试与旧世界）。

### 1.3 边界

- 玩家已死亡/鬼门关：不触发（先走 continue 的 target 存活/同地点检查）。
- 同一 tick 同一 NPC 只判一次；失败下一 tick 再试（对应心跳）。
- 成功清空该 NPC 交战，其它 NPC 若仍在打，下一次各自 tick 继续判定；成功只对当前攻击者执行 `remove_all_enemy` 等价清理（原版 `remove_all_enemy` 清全部，本引擎把所有 `combat_target == 玩家` 的 NPC 一并 reset，保持语义一致）。
- 旧九地点：无 `wimpy` 字段、无 `wimpy_log` 事件、无随机数变化。

## 2. 缺陷 2：`score` 门派读法

只改 [`src/wsim/api/xkx_shell.py:1900-1905`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/xkx_shell.py#L1900-L1905)：

```python
sect_view = state.get("sect") or {}
sect = sect_view.get("sect") if isinstance(sect_view.get("sect"), dict) else sect_view
sect_name = str(sect.get("name") or "")
```

- `in_sect == false` / `sect is None` → `门派：尚未拜入门派。`
- 有门派 → `门派：{name}（{role_label}）`，`role=="leader" → 门主`，否则 `弟子`。
- 事实源仍是 `Entity.faction`；不改 `bai`、不写状态。

## 3. 缺陷 3：消费内容包 `drops`

- `Entity` 增 `drops: dict[str,int]`；`to_dict()` 仅非空输出排序字典（旧世界无键，哈希不变）。
- `effects.py` `create_entity` 接受并规范化 `drops`。
- `sim/genesis.py` 只汇总 `chance == 1.0 && item_resolved && landing == "ground"` 的条目；其余跳过（不猜概率、不编数据）。
- `actions/combat.py` `_drop_counts` 增 `content_drops`，与背包/装备合并时同 id 取 `max`（避免与 `equipment` 去重逻辑双计）；NPC 死亡两条路径都传 `getattr(ent, "drops", {})`。
- 验收：杀大狼狗 → `loot_create(goupi)` → `look` 地上有狗皮 → `loot 狗皮`；`drops: []` 的安健刚仍不掉。

## 4. 缺陷 4：武器自带招式

- [`src/wsim/sim/skill_actions.py`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/skill_actions.py)：
  - `WEAPON_VERBS`：从 `inherit/weapon/*.lpc` 的 `set("verbs", ...)` 抽取（`staff/club/stick → ("bash","crush","slam")`，其余按源码实际）。
  - `WEAPON_ACTION_TEXT`：照 `weapond.lpc:59-80` 的 `action`/`damage_type`；未知 verb 回落 `hit`（`weapond.lpc:93`）。
  - `select_weapon_action(weapon_type, prng)`：`verbs[prng.below(len(verbs))]`；不看技能等级。
- [`src/wsim/sim/combat_rules.py:403-425`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/sim/combat_rules.py#L403-L425)：无映射技能且持武器 → `selection="weapon"`；有映射仍 `selection="skill"`；空手才走种族 `default`。
- 事件 entry 仅在持武器时增加 `"weapon": str(src.weapon)`（空手事件不变）。
- [`src/wsim/api/xkx_shell.py:1524-1534`](file:///Users/lyinet/projects/XKX/world-sim/src/wsim/api/xkx_shell.py#L1524-L1534)：`_render_action_text` 把 `$w/$W` 映射为武器显示名（`_item_name(act["weapon"])`），不再留空。
- 验收：`wield 铁棍` 后战报出现“挥舞/高举/手握……砸”，无“挥拳/踢”。

## 5. 测试与真实验收

### 5.1 自动化测试

- `tests/test_wimpy.py`：命令无参/越界/0..80；`to_dict` 空 `wimpy` 不输出；自动逃跑成功（移动 + 清交战 + `wimpy_log`）与失败（原地 + 本回合照打）；`roll_order == ["exit_index","roll_target","roll_npc"]`；无出口不掷点；`wimpy=0` 不取随机数（与既有 aggression 事件一致）；同 seed 双跑一致。
- `tests/test_score_sect.py`：嵌套视图显示 `红花会（弟子）`；`in_sect=false` 显示未拜入。
- `tests/test_combat_death_equipment.py`：内容 `drops` 死亡后落地、`loot` 可取；`drops` 与 `equipment` 同 id 取 max；`to_dict` 空 `drops` 不输出；创世读到大狼狗 `drops`。
- `tests/test_skill_actions.py`：`staff` 三个 verbs 且只掷一次；空武器类型不掷点；未知类型回落基类。
- `tests/test_combat_moves.py`：无映射技能持铁棍 → `selection="weapon"`；`staff` 等级 0 仍用武器动作。
- 既有 `tests/test_xkx_determinism_legacy.py` 断言不改。

### 5.2 真实响应（独立端口 8053 / 8057，固定 seed）

- 用 `/tmp/xkx-smoke.yaml`（复制 `config/worlds/xkx.yaml`，仅改 `run_id: xkx-smoke` / `api_port: 8053` / `ui_port: 8054`，固定 seed）与 `/tmp/xkx-wimpy.yaml`（独立 fresh run，`api_port: 8057`，spawn `/d/hangzhou/yuhuangsd`，ambient 1、npc_wander 关），**不得 reset `xkx-world`**。
- 缺陷 1：
  - `wimpy`（无参）与 `wimpy 80` 的真实响应；
  - 进入 `/d/hangzhou/shanlu6` 不输入 `fight`，贴出每回合掉血与 `wimpy_log` 的失败/成功原始事件；
  - 成功后 `look` 显示已换房间、NPC 不再打；至少给出一次失败和一次成功的真实记录（可重复进入，固定 seed，不改公式）；
  - 战斗结束后 `wimpy` 不再触发（无交战）。
- 缺陷 2：`bai 安健刚` 前后各一次真实 `score`。
- 缺陷 3：`/d/hangzhou/guozhuang` 杀大狼狗 → `look` → `loot 狗皮` 原始输出。
- 缺陷 4：`/d/hangzhou/tiejiangpu`（铁匠铺；`smith` 的店铺在房间 business 上，NPC 本人可能已自主移动）买 `铁棍` → `wield 铁棍` → `fight`，贴战报行与 `selection`。
- 每次报告附旧九地点三项哈希、`git diff --stat tools/xkx_formula`（必须为空）、全量 pytest。

### 5.3 随机顺序修正对 `content_hash` / `--reset` 的影响

- **`content_hash` 不变**。它只由内容包 `content/xkx/**` 计算（启动日志 `ambient_boot ... content=56417288eb1a`）；本次只改引擎代码与事件字段，没有改任何内容包文件。
- **旧事件重放不受影响**。事件库记录的是已经发生的效果，不记录随机流；旧 `xkx-smoke` 可直接继续重放，不需要 `--reset`。
- **未来事件序列会变**。同一 seed 下，wimpy 现在先掷方向、失败也掷方向，随机流消耗与旧实现不同；旧 run 里“下一条”wimpy 事件与旧证据不再逐字节对应。若要逐字节复现旧顺序的旧证据，只能用旧代码或旧事件库；新验收统一用 fresh run（`xkx-wimpy`）。
- **旧九地点哈希不变**。`test_xkx_determinism_legacy.py` 的世界没有玩家、没有 `wimpy` 字段，不进入 `_wimpy_escape`，随机流与三项哈希保持基线。

## 6. 提交纪律

- 只在 `xkx-parity`；显式 `git add` 本次文件；绝不 add 预存脏文件（`reports/xkx-audit/*`、`specs/xkx-parity/mud-client.tasks.md`、`reports/soak/*`、`tools/xkx_formula/calibration/*.json`）。
- 不 force；推 `xkx-parity`。
