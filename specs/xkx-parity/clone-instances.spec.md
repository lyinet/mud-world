# 同模板多实例（clone）设计

状态：设计稿，等待用户决定；**未改 `src/**`、`content/**`、`config/**`**
范围：只解决对拍报告里"17 房同名 clone 数量不同"这一类，不顺带做房间重置/游走/刷新
依据：`reports/xkx-ref/room-parity.md` / `.json`、原版 mudlib 源码、驱动源码、现有复刻代码

## 0. 结论先行

建议做，但拆成 P1–P3 三个独立提交；P4（房间重置/刷新/游走）单独再议。

- 当前报告只把复刻侧算成"每模板 1 只"，实际运行时更差：模板的规范 home 只有一个房间，**其余摆放房间是 0 只**。例如 `shanlu3` 原版 3 个男孩、复刻 0 个；`dongxiangfang`/`xixiangfang`/`fang3`/`fang4` 的丫环、`tulu1` 的男孩、`suti4` 的乌鸦都是 0。
- 19 个模板、44 个额外实例即可补齐（附录 A）。这 19 个模板全是无关系、无店铺、无任务、无师徒的匿名杂兵，实体模型风险远低于一般 NPC。
- 做完 P1–P3 后，报告里 17 房数量差应全部消失，NPC 一致率预计 **175/195 → 192/195（89.74% → 98.46%）**；剩余 3 房是已有的 `<昏迷不醒>` 战斗状态 2 房 + `road1` 香火道人游走 1 房，不在本设计范围。
- 代价：xkx 世界自身 hash 必变（105 → 149 实体），旧九地点 hash 必须逐字节不变。两条都可在下面的确定性论证里分开记账。

## 1. 原版语义（先给证据，不猜）

### 1.1 房间按 `set("objects")` 的 count 创建

`tools/xkx_ref/run/mudlib/inherit/room/room.c`：

- `reset()` 读取 `query("objects")`，值为 `([ 模板路径: 数量 ])`（L24–37）。
- 数量为 1 走单实例分支；`>1` 时 `allocate(count)`，逐个 `make_inventory()`（L43–48、L59–72）。
- `make_inventory()` 每次 `new(file)` 后 `move(this_object())`（L15–22）。因此同一模板的克隆是**独立对象**，`move` 进入房间的顺序是 j=0..N-1。
- `reset()` 对已存在但不在本房的 NPC 调 `return_home()`；回不去（战斗/昏迷/无出口）就 `add("no_clean_up", 1)`（L50–58、L66–71）。
- 死亡/被 destruct 的槽位在下一次 `reset()` 时重新 `make_inventory()`（L60–64）。`setup()` 在房间加载时调用一次 `reset()`（L191–193）。

结论：原版的"数量"是每个房间各自的 `(模板, 房间) -> count`，不是全局"模板 -> count"。同一模板可以同时出现在多个房间（男孩 3 房、丫环 5 房），每个房间各自持有自己的克隆。

### 1.2 显示按 `id + short()` 分组，带数量前缀

`tools/xkx_ref/run/mudlib/cmds/std/look.c` 的 `combined()`（L624–657）：

- 以 `id + "/" + short()` 为 key 分组。
- `count != 1` 时输出 `chinese_number(count) + query("unit") + short`（L655）。
- `unit` 来自种族守护进程：人类 `位`、走兽/飞禽/蛇/虫 `只`、鱼 `条`
  （`adm/daemons/race/human.c:36`、`beast.c:57`、`bird.c:56`、`fish.c:52`、`snake.c:56` 等）。

所以原版在场列表是"四位绿营清兵"而不是四行"绿营清兵"。

### 1.3 `fight`/`kill` 只选一个目标，按名字+序号

- `tools/xkx_ref/run/mudlib/cmds/std/fight.c:17`：`present(arg, environment(me))` 取目标，取不到就报"你想攻击谁？"（L17–19）。
- `kill.c` 同样走 `present()`。
- 驱动 `present()` 的实现：`tools/xkx_ref/src/fluffos-v2019/src/vm/internal/simulate.cc:748` 的 `object_present2()`：
  - 遍历 `ob->contains`（`next_inv`），对每个对象调 `id(name)`（L774–786）。
  - 字符串末尾是数字且前面有空格时拆成"名字 + 序号"（L762–772）；`--count > 0` 跳过前 N-1 个（L787–789）。
  - 不写序号时 `count=0`，第一个匹配就返回（L784–790）。
  - 文档 `docs/efun/objects/present.md` 明确："foo 1" 是库存里的第一个，而**库存里的第一个是最后 move 进来的对象**（LIFO）。
- 因此 4 只青兵时：
  - `fight qing bing` → 最后创建的第 4 只（j=count-1）。
  - `fight qing bing 2` → 倒数第二只。
  - `fight qing bing 9` → `present()` 返回 0。
  - `fight 绿营清兵` → `id()` 不匹配中文 short，返回 0（原版按 `id()`，不是按显示名）。
  - 一次命令只打一只，没有群体攻击。

### 1.4 游走、死亡、刷新

- 游走：`inherit/char/npc.c` 的 `chat()`（L255–274）按 `chat_chance` 概率随机取 `chat_msg`；默认 `random_move()` 随机选一个出口 `go`（L277–284）。不是所有 NPC 都配了 `chat_msg`。
- 死亡：原版 `die()` 会 destruct 对象，房间 `query_temp("objects")` 里的槽位变空；之后只有该房间再次 `reset()`（通常是房间被 clean_up 后重新加载/重启）才会补回。
- 回 home：`return_home()`（L229–250）在战斗、昏迷、无出口且连续 5 次失败时拒绝回 home，并把房间标成 `no_clean_up`，避免房间被卸载导致 NPC 凭空消失。
- 复刻现状：xkx 世界 `ambient: 0`（`config/worlds/xkx.yaml`），没有 NPC 心跳/游走；没有 `spawns.yaml`；没有房间卸载/重置；NPC 死亡后 `status=dead` 永久留在 state 里但不再显示（`session.py:2230-2251` 过滤 `status == "alive"`）。这是**所有 NPC 的既有差异**，不是 clone 引入的。

## 2. 实体模型影响面

### 2.1 ID 方案

采用"规范实例 + 额外实例"两段式，避免改动现有引用：

| 实例 | id | 说明 |
|---|---|---|
| 规范实例（每个模板 1 个） | 保持模板路径，如 `/d/hangzhou/npc/qingbing` | 位置 = 现有 `placed_npcs[ref] = sorted(homes)[0]`，即 `xkx_content.py:294-298` 的 home |
| 额外实例 | `f"{ref}@{room}#{n}"`，n 从 1 开始 | room 为该房间 id；n 只在该房间内编号 |

为什么不是全局 `template#n`：同一模板分散在多个房间（附录 A），全局序号表达不了"每个房间各 N 只"；必须带房间维度。

为什么不把所有实例改成 `ref@room#n`：规范实例 id 被内容关系、任务、门派、玩家已保存状态、对拍/存档引用；全改会把所有既有 xkx 实体 hash 和引用一次打散。保留规范实例 id，只新增额外实例，改动面最小。

解析规则：只有包含 `@` 的 id 才是额外实例；`rid.rsplit("#", 1)` 取序号，再 `split("@", 1)` 取模板与房间。现有 id 不含 `@`/`#`（已核对内容包），不会误判。

唯一性：105 个规范实体 + 44 个额外实例 = 149 个实体 id；P1 用集合断言唯一。

### 2.2 Entity 字段与序列化

在 `src/wsim/world/state.py` 的 `Entity` 增加两个可选字段：

- `template: str = ""`：额外实例指向规范模板 id；规范实例留空。
- `instance: int = 0`：额外实例的房间内序号；规范实例为 0。

`Entity.to_dict()`（`state.py:87-160`）沿用现有"非默认才输出"的写法：只有 `template` 非空时才写 `template`/`instance`。这样规范实例（包括旧九地点全部实体）的序列化逐字节不变。

`create_entity` 效果（`src/wsim/world/effects.py:200-260`）增加可选的 `template`/`instance` 读取；旧事件没有这两个键，行为不变。

额外实例的静态数据从规范模板复制，**不按 clone id 重新计算**：

- `name`/`role`/`race`/`personality`/`skills`/`max_hp`/`hp`/`neili`/`max_neili`/`jiali`/`attrs`/`skill_levels`/`skill_xp`/`potential`/`combat_exp`/`temp_apply`/`equipment` 原样复制。
- 原因：`genesis.py:201` 的 `skill` 依赖 `_stable_index(nid, ...)`；用 clone id 重算会得到与规范实例不同的数值，与原版"同一模板每个 clone 各自 create，属性相同"不符。

账户/资产：

- 每个额外实例建 `wallet:{clone_id}`（`genesis.py:194,221`），金钱按模板 `assets.money` 复制。当前 19 个模板 money 均为 0，不会凭空增发。
- 若模板未来有 `assets.items`，必须按实例 `item_create(owner=clone_id)`，不能共享规范实例的 inventory。
- `equipment` 每个实例各自一份 list；死亡掉落时各自结算。

### 2.3 现有按 id 索引的地方：哪些踩空、哪些要加模板维度

| 位置 | 现状键 | 额外实例后的行为 | 处理 |
|---|---|---|---|
| `WorldState.entities` | 实体 id | 新 id 唯一，正常 | 无需改 |
| `relations`（`a|b|kind`，`state.py:280-302`） | 实例 id | 额外实例没有内容关系；玩家与某只 clone 的关系按实例存，符合原版"每个对象独立" | 不需要模板维度；禁止把模板关系复制给每只 |
| `memories` / `talk_ledger` | NPC id | 每只独立记忆，符合原版对象独立 | 不需要模板维度 |
| `sects` 内容 / `factions.members` | 唯一师父 NPC id | 19 个 clone 模板均非门派成员、非师父 | 无需改；若未来有门派模板被 clone，只允许规范实例入册 |
| `quests.target_npc` / `giver`（`state.py:372-395`） | 具体 NPC id | 现有 xkx 任务目标全是唯一 NPC，不指向 clone 模板（已核对） | 无需改；规则：接任务时把模板解析成一只存活实例并落成实例 id，不能存模板路径 |
| `ground`（`state.py:493`） | 地点 id | 与实体 id 无关；多只 clone 掉同名装备会按物品 id 堆叠 | 无需改；玩家可见为数量堆叠，原版 look 也按数量分组 |
| `loot` / 死亡掉落 | 实体 id + 地面 | 每只 clone 用自己的 `equipment` 掉落 | 无需改；不要共享 list |
| `master` / `disciple` / `spouse` / `disciples` | 实例 id | 玩家收某只 clone 为徒/结亲时绑定具体实例，符合原版 | 无需改；内容关系只挂规范实例 |
| `wallet:{id}` / accounts | 实体 id | 每只一个账户（当前金额 0） | 需要接受 state 变大 |
| `npc_meta`（`session.py:2244-2253`） | 内容模板 id | **额外实例直接查会踩空**：显示名退回基名、`is_master`/`sect` 为空、技能展示缺失 | **必须加模板维度**：`meta = npc_meta.get(ent.template or ent.id)` |
| `_npc_ids`（`session.py:522`） | `content.npcs` id | 额外实例不在集合里；若开 ambient，clone 不能作为 actor | P2 把实例 id 并入 `_npc_ids`；ambient 目前关闭 |
| `AmbientScheduler.npc_ids`（`scheduler.py:286-290`） | `content.npcs` | 同上，额外实例不会被调度 | 可选 P2 扩展；不影响本次对拍 |
| 店铺 `stock_by_room` / `placement` | 模板 id → 首个 home | 若店主模板被 clone，库存只挂规范实例 | 内容校验：店主/带 shop 的模板禁止 clone |
| 任务/关系内容指向 | 模板 id | 若某模板既是任务目标又被 clone，接单会指向规范实例 | 内容校验：任务目标/发放者模板禁止 clone |

总结：真正需要"模板维度"的只有 `npc_meta`（显示/身份）和校验/调度枚举；关系、记忆、师徒、掉落、任务实例绑定都应保持**实例维度**，不要做模板级聚合。

### 2.4 内容校验与失败策略

派生额外实例时逐模板检查，任一命中就不 clone，保留现状并写诊断（不静默）：

- `relations` 非空、`is_master` 为真、带店铺身份、是 `quests.json` 的 `giver` 或 `target`。
- 模板被摆放在 room 但 `count` 不是正整数（表达式/随机由现有逻辑跳过，不新增随机）。
- `home` 不在 195 切片内。

诊断格式沿用现有 `mismatches`，例如 `clone_not_supported:<ref>:<reason>`，写进 `content/xkx/diagnostics.jsonl` 或加载期告警。当前 19 个模板全部通过。

## 3. 战斗与掉落

### 3.1 每实例独立状态

- `Entity` 的 `hp/max_hp/equipment/status/skills/...` 本来就按实体存；额外实例有独立 id，战斗读写天然隔离。
- 复刻当前**没有** NPC 主动攻击/仇恨（原版 `init()` 里 `kill_ob` 未建模，2 房 `<昏迷不醒>` 就是证据）。因此"每实例独立仇恨"目前是 N/A；若以后补 aggro，用实例 id 做键即可，不需要模板维度。
- 死亡：`combat.py` 把目标 `status` 置 `dead`，按该实体的 `equipment` 掉落；额外实例各自掉落，不会共享。
- 原版死亡后对象 destruct，房间槽位空；复刻保留 dead 实体但不显示。两者玩家可见结果一致（消失），差别在状态体积和"以后能不能复活"。本设计不引入复活。

### 3.2 `fight 青兵` 有 4 只时怎么选

原版行为（第 1.3 节，源码已核）：

- `fight 青兵`/`fight qing bing`：`present()` 返回库存里的第一只 = **最后 move 进来的那只**（j=count-1）。
- `fight 青兵 2`：倒数第二只；序号超出返回 0，提示"你想攻击谁？"。
- 一次只打一只，不群体攻击。

复刻现状（`src/wsim/api/xkx_shell.py`）：

- `_cmd_fight`（L1010-1042）用 `_match_npc_prefix`（L906-925）在 `present_npcs` 里做"名字/ID 最长前缀"匹配，剩余部分被忽略（本来用于"问 店小二 有酒吗"）。
- `fight 青兵 2` 现在会命中第一只（把 `2` 当台词丢弃）；`fight 青兵2` 也会命中（原版应失败）。
- 没有任何序号/库存顺序概念。

需要改成：

1. `_present_view` 对同一模板/同一显示名的实例按 `instance` **降序**输出，规范实例 `instance=0` 排最后；等价于原版"最后 move 进来的在前"。
2. `_cmd_fight` 先按原版规则拆序号：只有"名字 + 空格 + 纯数字"才拆；`青兵2` 不拆。
3. 默认目标取该名字组的第一只（即最新那只）；`青兵 2` 取组内第 2 只；超出范围返回"你想攻击谁？"，不静默降级到第一只。
4. `fight` 以外的 `ask`/`give` 仍可保留"剩余部分当台词"的现有语义，不要为了 fight 改掉。
5. 原版只按 `id()` 匹配；复刻现在也接受显示名。这个差异已存在，本设计不扩大也不修复，只保证序号语义一致。

## 4. 调度与存活状态

### 4.1 与原版对齐到什么程度

| 行为 | 原版 | 本设计 P1–P3 | 是否新增差异 |
|---|---|---|---|
| 初始投放 | 每房 `reset()` 按 count 创建 | 创世按 `(模板, 房间, n)` 创建，位置=home | 否 |
| 游走 | `chat_chance` + `random_move` | 不实现；xkx ambient 关闭 | 否（所有 NPC 既有差异） |
| 死亡后再刷 | 房间重置/重启时补槽位 | 不实现；dead 永久不显示 | 否（所有 NPC 既有差异） |
| 回 home | `return_home()`，战斗/昏迷时拒绝 | 不实现；实例始终在 home | 否 |
| 单写者 + tick | 驱动心跳/房间 reset | 全部在 tick 0 的 genesis 单事件里完成；无并发写 | 否 |

结论：clone 只补"初始投放数量"，不夹带游走/刷新。P4 若要做，应按原版语义单独设计"房间重置 tick"，不能塞进本次提交。

### 4.2 与现有 NPC 调度器的兼容性

- xkx 世界 `ambient: 0`，`AmbientScheduler` 不启动，clone 不会被调度器改写。
- 若将来打开 ambient：
  - `session.py:522` 的 `_npc_ids` 和 `scheduler.py:286-290` 的 `npc_ids` 目前只取 `content.npcs`；需要把 `content.npc_instances` 的 id 也纳入，否则 clone 永远不动、也不会被 reasoner 选中。
  - 每个 clone 作为独立 actor 发 Intent，走同一个 Writer；不存在共享对象状态。数量从 105 涨到 149，调度成本线性增加但仍是单写者。
  - reasoner 的 persona/角色数据按 `template` 取，不按实例复制一套人设。
- 若不打开 ambient，clone 就是静态摆放，和现在所有 xkx NPC 一样。

## 5. 确定性

### 5.1 新增实例不改随机取数顺序

- 创世不调用任何 `random()`；额外实例的生成只遍历排序后的 `(ref, room, n)`，顺序固定。
- 不引入 UUID、时间戳、墙钟；id 由模板/房间/序号字符串拼成。
- 状态字典按 key 排序后 hash（`state.py` 的 `to_dict` + `WorldState.to_dict`），新增实体不会改变既有 key 的排序位置。
- PRNG 是 `PRNG(seed, seq, salt)`，`seq` 是事件序号。创世仍是一个事件（`build_genesis` 返回一个 bootstrap 事件），增加 effect 不会增加事件数，也不会移动后续事件序号；创建 clone 本身不消耗随机数。因此后续战斗的随机流不会因为"多了 44 个实体"而整体平移。
- 禁止：把 clone 拆成多个创世事件、按 set/dict 不稳定顺序生成、用实例 id 重新做 `_stable_index` 数值计算。

### 5.2 旧九地点 hash 为什么可以保持不变

不是断言，是五条路径都不触碰：

1. 旧九地点内容（`content/npcs.yaml` / `locations.yaml`）没有房间 `objects` NPC 条目；`genesis.py:270-272` 的注释也说明 legacy locations 没有 `objects` 键。`content.npc_instances` 为空，新增循环零 effect。
2. 现有规范 NPC 创建循环（`genesis.py:188-268`）不改；旧世界 0 个额外实例，事件 payload 不变。
3. `Entity.to_dict()` 的 `template`/`instance` 只在 `template` 非空时输出；旧世界所有实体 `template=""`，不会多键。
4. 新增代码路径不调用随机/墙钟；不新增 account、不新增 equipment。
5. `Content.content_hash` 只在 `npc_instances` 非空时才把该键放进 payload（现有 `xkx_quests` 同款做法，`core/content.py:78-95`）；legacy 为空，content hash 不变。

因此 bootstrap 事件 payload、事件总数（735）、state hash 都应逐字节不变。每一步提交后都重跑 `tests/test_xkx_determinism_legacy.py`，并贴：

```
events 735
state_final 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9
event_final 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae
```

### 5.3 xkx 世界自身 hash 怎么记账

- 预期变化：实体 105 → 149；新增 44 个 `wallet:` 账户；额外实例带 `equipment`/`template`/`instance` 键。state hash 与 genesis event hash 都会变，事件总数不变。
- `Content.npc_instances` 非空时纳入 `content_hash`。这样旧 xkx run 目录会明确报 `content_hash_mismatch`，必须 `./scripts/start_world.sh --world xkx --reset`，**避免旧事件回放后静默缺少 clone**。legacy 内容不受影响。
- 没有现存的 xkx 世界 hash 测试（只有旧九地点测试固定了 hash），所以不会误伤测试。建议实现验收后新增一条 xkx 基线（state/event hash、事件数）写进 `reports/xkx-ref/room-parity.md` 头部或 `reports/xkx-ref/world-hash.md`，并在 P3 后加一个可选的 `test_xkx_world_hash.py` 防漂移；不要用旧 xkx hash 做断言。
- 对拍工具 `parity_rooms.py` 的复刻侧 `replica_field` 目前把 NPC `cnt` 强制为 1（记录为"已知架构差异"）。P2 之后必须改回 `cnt = count`，否则新增了实例但报告仍按 1 算。这个改动只在实例真实存在后做，不能提前放宽。

## 6. 工作量与风险拆分

每一步独立可提交、可单独验证；不要求一次做完。

### P0：先把基线测试修正为绿（与 clone 无关）

当前 HEAD 上 `tests/test_xkx_api.py` 已有两条**既有失败**，不是本设计引入：

- `test_help_lists_all_required_commands`：断言 `"92 位 NPC"`（L55），但 help 实际由 `content.npcs` 生成，现为 105。
- `test_content_counts_match_pack_exactly`：断言 `len(content.npcs)==92`（L61）、`npcs_in_slice==92`（L65）、`masters==22`（L67）；实测加载器返回 `len(content.npcs)=105`、`npcs_in_slice=105`、`npcs_total=105`、`masters=24`（`rooms_in_slice=195`、`shops=3`、`exits_total=427` 仍匹配）。

根因：前序提交 `a3f3cf8` 把切片外但被摆放的 NPC（马厩的马夫/马）纳入内容包后，没有同步 92/22 的旧断言和 help 文案。P0 只改测试期望值与 help 文案（若文案本身正确则只改测试），**不动世界逻辑、不动创世 payload、不动 hash**。P0 合并后 `tests/test_xkx_api.py` 变绿，后面 P1–P3 的任何失败才能归因到本设计。

### P1：内容/实体 schema（无行为变化）

改动：

- `core/content.py`：新增 `npc_instances: list[dict]`，非空时纳入 `content_hash`。
- `api/xkx_content.py`：从 `placement` + `rooms.json` 的 `objects` 派生额外实例；排序、校验、诊断。
- `world/state.py`：`Entity.template` / `Entity.instance`，条件序列化。
- `world/effects.py`：`create_entity` 可选读取 `template`/`instance`。

验证：

- 新单测：44 个额外实例、id 唯一、没有关系/店铺/师徒/任务引用。
- `test_content_counts_match_pack_exactly` 在 P0 修正后的口径上仍通过：`len(content.npcs)==105`、`npcs_in_slice==105`、`npcs_total==105`、`masters==24`（实例不影响模板统计，`npc_instances` 是独立列表）。
- 旧九地点 hash 三项不变；xkx 行为不变（还没有创建实例）。

风险：低。

### P2：创世创建实例（最可能破坏既有测试）

改动：

- `sim/genesis.py`：在规范 NPC 循环之后、地面物品之前，按排序好的实例追加 `create_account` / `create_entity` / `mint`（仅 money>0）；复制 `equipment`、`temp_apply`、`attrs`、`vitals`、`skills`。
- `api/session.py`：`_npc_ids` 并入实例 id；`npc_limit` 口径确认（xkx 为 0）。
- `tools/xkx_ref/parity_rooms.py`：`replica_field` 的 NPC `cnt` 改回 `count`。

验证：

- `./scripts/start_world.sh --world xkx --port 8071 --reset`；`state.entities` 149 个；`road12` 有 4 只青兵实体（未分组时显示 4 行）。
- `parity_rooms.py --all --reparse`：17 房数量差消失，NPC 一致率 175/195 → 192/195。
- 旧九地点 hash 三项不变；记录新的 xkx state/event hash 与事件数。
- 重点回归：`tests/test_xkx_api.py`（look 快照/在场列表）、`tests/test_xkx_growth.py`（内容统计/师徒）、`tests/test_combat_death_equipment.py`（死亡掉落）。

风险：**最高**。原因是它第一次改变 xkx 创世 payload：任何断言 xkx 实体数量、特定房间在场列表、xkx content hash 或 bootstrap 事件的测试都可能失败；对拍报告也必须重跑。旧九地点理论上不受影响，但仍需每步贴 hash。

注意基线：当前 `tests/test_xkx_api.py` 的两条失败（见 P0）会掩盖 P2 的真实影响。**P0 未合入前，P2 的任何红灯都无法归因**；P2 开工前必须先确认 P0 后该文件全绿，并保存一份 P0 后的测试结果作为对照。

### P3：显示分组与目标序号

改动：

- `api/session.py`：`_present_view` 用 `meta = npc_meta.get(ent.template or ent.id)`；同模板/同名实例按 `instance` 降序。
- `api/xkx_shell.py`：`look` 的"在场人物"按 `(template, display_name)` 分组，`count != 1` 时输出 `中文数字 + 种族单位 + 名字`；单位按 `race` 映射（人 `位`、走兽/飞禽/蛇/虫 `只`、鱼 `条`，未知按人 `位`）。
- `api/xkx_shell.py`：`_cmd_fight` 支持"名字 + 空格 + 序号"，默认取组内最新一只，越界报"你想攻击谁？"；`青兵2` 不拆序号。

验证：

- 真实 look：`road12` → `在场人物：四位绿营清兵、杭州提督 李可秀。`（顺序以实例序为准）
- `fight qing bing` 打最新一只；`fight qing bing 2` 打倒数第二只；`fight qing bing 9` 报无目标；杀一只后数字变三位，逐个减少。
- 对拍 NPC 一致率保持 192/195（显示归一化会去掉数量前缀，但运行时数量已真实）。
- 旧九地点 hash 不变（纯显示/命令层）。

风险：中。会改 `look`/`fight` 文本，可能影响 shell/UI 快照测试；不改世界状态 hash。

### P4：房间重置/刷新/游走（本次不做）

内容：房间 clean_up/重置、死亡补槽、`return_home`、`chat_chance` 游走。复杂度和风险都高于 clone 本身，且对本次"数量"一致率没有增量。建议单独立项；若要接，必须复用系统事件/单写者，不能引入墙钟。

## 7. 不做也可以的理由与替代方案

### 7.1 只做显示层数量标注（不推荐）

做法：不改实体，`look` 按内容 `count` 显示"四位绿营清兵"。

玩家能立刻看出的差异：

- `fight 青兵` 只能打到唯一实体；`fight 青兵 2` 永远没有第二个目标。
- 杀死这唯一实体后，四只"同时消失"；原版只死一只。
- 掉落只有 1 份装备，原版 4 份；玩家捡完就露馅。
- 任务/关系/记忆都只指向同一对象，原版每只独立。

结论：这只让报告数字变好，玩家行为一眼可破，属于为凑一致率造假，明确不做。

### 7.2 真正不做 clone 的理由（不成立）

原本担心的是关系/门派/任务/店主牵连；实测 19 个模板全部是匿名杂兵（附录 A），没有这些引用，44 个实例也不产生有意义的经济增量（money=0，只有随身装备复制，原版同样复制）。因此"投入产出不值"不成立；真正的成本在确定性创世和显示/目标序号，不在领域逻辑。

## 附录 A：需要补齐的 19 个模板（44 个额外实例）

规范实例 home = `sorted(homes)[0]`；"需新增"已扣掉规范实例所在房间的 1 只。

| 模板 | 显示名 | 规范 home | 原版摆放（房×count） | 需新增（房×n） | 新增 |
|---|---|---|---|---|---|
| `boy` | 男孩 | maojiabu | maojiabu×1; shanlu3×3; tulu1×2 | shanlu3×3; tulu1×2 | 5 |
| `caichanu` | 采茶女 | shanlu5 | shanlu5×2 | shanlu5×1 | 1 |
| `girl` | 女孩 | qingbomen | qingbomen×2 | qingbomen×1 | 1 |
| `guest` | 进香客 | fajingsi | fajingsi×1; fajinsi×1; faxisi×2 | fajinsi×1; faxisi×2 | 3 |
| `honghua1` | 红花会众 | dalu1 | dalu1×2; road5×1; yuhuangsj×2 | dalu1×1; road5×1; yuhuangsj×2 | 4 |
| `honghua2` | 绿营会众 | jujingyuan | jujingyuan×1; shanlu2×2; yuelang×2 | shanlu2×2; yuelang×2 | 4 |
| `jiading` | 家丁 | liuzhuang | liuzhuang×2 | liuzhuang×1 | 1 |
| `jian-ke` | 剑客 | jujingyuan | jujingyuan×1; qiantang×1 | qiantang×1 | 1 |
| `liumang` | 流氓 | road6 | road6×4 | road6×3 | 3 |
| `maque` | 麻雀 | liulangqiao | liulangqiao×2; suti4×2 | liulangqiao×1; suti4×2 | 3 |
| `qingbing` | 绿营清兵 | road12 | road12×4 | road12×3 | 3 |
| `seng` | 老和尚 | faxisi | faxisi×1; lingyinsi×1 | lingyinsi×1 | 1 |
| `shami` | 小沙弥 | fajingsi | fajingsi×1; fajinsi×1 | fajinsi×1 | 1 |
| `tiao-fu` | 挑夫 | dalu1 | dalu1×1; road17×1; road5×1 | road17×1; road5×1 | 2 |
| `wolfdog` | 大狼狗 | guozhuang | guozhuang×2 | guozhuang×1 | 1 |
| `wuya`（杭州） | 乌鸦 | liulangqiao | liulangqiao×1; suti4×1 | suti4×1 | 1 |
| `wuya`（嘉兴） | 乌鸦 | tieqiang | tieqiang×4 | tieqiang×3 | 3 |
| `fish` | 噬人鱼 | huli | huli×2 | huli×1 | 1 |
| `yahuan` | 丫环 | dating | dating×2; dongxiangfang×1; fang3×1; fang4×1; xixiangfang×1 | dating×1; dongxiangfang×1; fang3×1; fang4×1; xixiangfang×1 | 5 |
| **合计** | | | | | **44** |

## 附录 B：验收清单

- [ ] P0 后 `tests/test_xkx_api.py` 全绿，基线口径为 `len(content.npcs)==105`、`npcs_in_slice==105`、`npcs_total==105`、`masters==24`。
- [ ] 额外实例 44 个、id 唯一、顺序稳定；模板统计仍 105/105、masters 24。
- [ ] 17 个数量差房间全部匹配；NPC 对拍 192/195，剩余 3 房是既有 `<昏迷不醒>`×2 + `road1` 游走×1。
- [ ] 真实 look：`road12` 显示"四位绿营清兵"；`shanlu3` 三位男孩；`dongxiangfang`/`xixiangfang` 有丫环。
- [ ] `fight qing bing` 打最新一只；`qing bing 2` 打倒数第二只；`qing bing 9` 无目标。
- [ ] 旧九地点 hash 三项逐字节不变（每次提交贴出）。
- [ ] xkx 新 hash 记录在案；`--reset` 后 content_hash 一致。
- [ ] 未改公式/数值；未新增墙钟/未种子化随机；归一化仍只有 N1–N6。

## 附录 C：非目标

- 不实现房间重置、死亡刷新、NPC 游走、`return_home`。
- 不修 2 房 `<昏迷不醒>`（原版运行时战斗状态）。
- 不修 `road1` 香火道人游走（原版 `random_move`）。
- 不把中文 short 加入 `id()` 匹配（原版只认 id；现有复刻的宽松匹配保持现状）。
- 不改 `content/xkx/*.json` 的既有字段；实例列表由加载器派生。
