# P5：房间重置 / NPC 重生（已实现）

> 状态：**已实现**。默认关闭，仅 `config/worlds/xkx.yaml` 的 `room_reset.enabled: true` 打开。
> 关闭态旧九地点三项哈希逐字节不变；打开态在“无死亡/无缺失”的健康世界零事件、哈希与关闭态一致。
> 分支：`fix/xkx-room-parity` → `origin/xkx-parity`。

## 0. 结论先行

1. 原版 reset 是**驱动级对象扫描**，不是房间脚本定时器：`look_for_objects_to_swap()`
   每 5 分钟注册一次，命中 `O_WILL_RESET && gametick >= next_reset` 的对象就
   `reset_object()`，由 `room.lpc::reset()` 补齐 `set("objects")` 槽位。
2. 每隔 `time to reset : 900` 秒（且默认 `randomized resets` 开启 → 450..899 秒），
   叠加 5 分钟扫描后，**实际间隔 10..20 分钟**。我们用 tick 级 10/20 分钟交替近似。
3. 被杀 NPC：`die()` 里 `destruct()`，对象消失；下一次 reset 槽位 `objectp()` 为假 →
   `make_inventory()` 重新 `clone()`，**满血、全新对象**。
4. 被捡走的物品：槽位里仍持有对象指针，`objectp()` 为真 → **不会复制**；只有对象被
   `destruct`/消耗后才会补。被移动的 living NPC：`return_home()` 拉回。
5. 商店：原版 `F_DEALER::do_buy()` 每件 `new(ob_file)`，**没有库存字段、没有补货周期**；
   我们内容包的 `business_stock: 30`（“存27”的来源）是引擎近似，因此 reset 必须把
   NPC 货架补回 30，否则会把“原版无限”误实现成“买空后永久缺货”。
6. 审计：`entity_respawn` 事件携带上一轮 `death_tick`/`death_location`，实体上同时保留
   `last_death_tick`/`last_death_location`；不因重生擦掉死亡线索。

## 1. 原版源码结论

| 命题 | 源码位置 | 结论 |
|------|----------|------|
| reset 扫描周期 | `tools/xkx_ref/src/fluffos-v2019/src/backend.cc` L262-267 | `look_for_objects_to_swap()` 每 5 分钟注册一次 |
| reset 触发条件 | 同上 L298-306 | `O_WILL_RESET && gametick >= next_reset && !O_RESET_STATE` |
| 单次 reset | `.../vm/internal/base/object.cc` L1936-1944 | `set_nextreset()` 后调用 `reset_object(this)` |
| 随机间隔 | 同上 L1926-1933；`.../base/internal/rc.cc` L79 | `time to reset` 默认 900；randomized 默认开启 → 450..899 秒 |
| 房间槽位语义 | `tools/xkx_ref/src/xkx100/inherit/room/room.lpc` L24-76 | `count==1` 单槽；`count>1` 数组槽；`objectp()` 为假才 `make_inventory()` |
| 房间对象不在本房 | 同上 L36-47 | living → `return_home(this_object())`；失败 `no_clean_up=1` |
| 死亡销毁 | `tools/xkx_ref/src/xkx100/feature/damage.lpc` L304-308 | 非玩家 `die()` 末尾 `destruct()` |
| return_home 拒绝条件 | `tools/xkx_ref/src/xkx100/inherit/char/npc.lpc` L229-250 | 非 living / `noliving` / `is_fighting()` 不拉 |
| 昏迷即 noliving | `tools/xkx_ref/src/xkx100/feature/damage.lpc` L134 | `set_temp("noliving/unconcious", 1)` |
| 无 reset 例外 | 全树检索 `no_reset` | 无该标记；店主/任务 NPC 也无例外 |
| 唯一 reset 覆写 | `tools/xkx_ref/src/xkx100/d/shiliang/huli.lpc` L61-64 | 只在虎力房覆写，语义仍是补槽位 |
| 任务强制 reset | `tools/xkx_ref/src/xkx100/adm/daemons/questd.lpc` L85-91 | 任务对象 `obj0->reset()`，不改房间周期 |

## 2. 商店补货：原版不补，因为没有库存

`tools/xkx_ref/src/xkx100/feature/dealer.lpc::do_buy()`：

```lpc
ob = new(ob_file);          // 每买一件现做一件
ob->set_amount(i);
ob->move(this_player());
```

- 货架商品没有库存计数、没有 `reset()` 补货；玩家**买不空**。
- 只有玩家寄售（`from == "pawn"`）受实际对象数量限制。
- 复刻沿用引擎的 `business.stock`，xkx 内容 `business_stock = 30`（老板娘界面“存27”）。
  因此 reset 时把 NPC 商业体补回 30；玩家自营店铺（`owner` 非空）仍走付费 `restock`。
- **残留偏差**：原版无上限，复刻在两次 reset 之间最多只能买 30 件；买空窗口最长一个
  reset 周期（10 或 20 分钟），随后自动补回。spec 明确记录，不假装等同于无限。

## 3. 周期偏差（必须写清）

- 原版：真实秒级随机 450..899 秒 + 5 分钟扫描 → 10..20 分钟，逐对象独立。
- 复刻：tick 固定 10 分钟，`(tick - phase) % 3 != 2` → **10/20 分钟交替**，均值同为 15 分钟；
  `phase = PRNG(seed, 0, "room_reset_phase:<room>") % 3`，逐房间独立。
- 为什么不能照抄墙钟：整套引擎必须同 seed 逐字节回放，墙钟/秒级随机会让两次运行分叉；
  因此只能把“真实秒”折算成确定性的 tick 相位。
- 玩家可感知后果：
  1. 一只原本 8 分钟就能刷新的怪，最快要等到第 10 分钟的 tick 才可能回来；
  2. 同一 tick 到期的房间一起结算，不是按秒错开；
  3. 商店买空窗口按 tick 对齐（最长 10 或 20 分钟），不是原版的“永不买空”。

## 4. 实现

新增 `src/wsim/sim/room_reset.py`：

- `RoomResetPolicy(content, seed, enabled=False)`；默认关闭。
- `due(room, tick)`：`(tick - phase) % 3 != 2`，`tick <= 0` 不结算。
- `effects(state, tick)`，按 `(room, kind, ref)` 稳定排序：
  - 槽位 NPC `status == "dead"` → `entity_respawn`（创世基线：hp/max_hp/attrs/
    skill_levels/inventory/equipment/temp_apply/race…）；
  - 槽位物品：房内地面已有 → 跳过；全服任何背包/装备/其他地面持有 → 跳过（原版对象仍在）；
    否则 `loot_create(reason="room_reset")`；
  - 槽位 NPC `alive` 且不在本房：原版 `return_home()`；复刻**只拉 `goal_id` 为空的空闲 NPC**。
    原因见下方“已知偏差”；无条件拉回会让 tick 级多步寻路的 NPC 永远到不了目的地
    （实测当日 `move 335 → 6`、交易/工作归零，整张世界停摆）。
  - NPC 商业体（`owner` 为空）把服务商品补回 `business_stock`：`item_create(reason="vendor_restock")`。
- `has_work()` 为空则不提交事件；健康世界 reset 完全空转。
- `SystemRoomResetAction`（`verb="system_room_reset"`）只调用 `policy.effects()`，不直接改 state；
  所有变更仍由 `Writer.submit()` 写事件、由 `effects.py` 落地。

接线：

- `config/worlds/xkx.yaml` 新增 `room_reset.enabled: true`；其他世界配置不加键。
- `RunConfig.room_reset` 默认 `False`；`WorldSession._load_room_reset()` 默认 `False`。
- `build_registry(..., room_reset=...)` 仅在 `enabled` 时注册动作。

审计：

- `Entity.last_death_tick/last_death_location`（非 0 才进 `to_dict()`，旧世界哈希不变）。
- `entity_respawn` 事件 data 同时带 `death_tick`/`death_location`（死亡发生时刻/地点）。

顺带补齐战利品链路（验收闭环必需）：

- 原版 `wolfdog.lpc::die()` / `laohu.lpc::die()` 里 `new()` 狗皮/虎骨；内容包以
  `npcs[].drops` 记录。此前复刻只掉背包/装备，`drops` 未接通。
- `FightAction(content)` 增加 `content_drops()`：只认 `landing=="ground"`、`qty` 为正整数、
  `chance` 为 0..1；`chance>=1` 不消费随机流，旧内容无 `drops` 时行为逐字节不变。

## 5. 验收（事件库原始行）

环境：`seed=20261014`，真实 `content/xkx`，`EventStore` 落盘于 `/tmp/xkx-p5-accept/`，
验收角色 `player:accept` 为引擎级合成角色（不是客户端会话）。

### 5.1 郭庄大狼狗完整闭环

| 步骤 | 事件（seq / tick / verb / outcome） | 关键 effects |
|------|------------------------------------|--------------|
| 杀狗 | `2 / 0 / fight / ok` | `hp_add -200`、`entity_status dead`、`loot_create /d/hangzhou/npc/obj/goupi qty=1 reason=fight` |
| 捡皮 | `3 / 0 / loot / ok` | `loot_take goupi → player:accept qty=1 weight=5000` |
| reset | `4 / 1 / system_room_reset / ok` | `entity_respawn`：`hp=200 max_hp=200`、`death_tick=0 death_location=/d/hangzhou/guozhuang`、`attrs{str26 con24 dex7 int8 kar11}`、`temp_apply{armor2 attack15 damage6}` |
| 再杀 | `5 / 1 / fight / ok` | 再次 `entity_status dead` + `loot_create goupi qty=1 reason=fight` |

原始行（节选，完整见 `/tmp/p5_accept.out`）：

```json
{"seq":2,"tick":0,"actor":"player:accept","verb":"fight","outcome":"ok","args":{"target":"/d/hangzhou/npc/wolfdog"},"effects":[{"op":"hp_add","data":{"entity":"/d/hangzhou/npc/wolfdog","delta":-200,"reason":"fight"}},{"op":"entity_status","data":{"entity":"/d/hangzhou/npc/wolfdog","status":"dead","location":"/d/hangzhou/guozhuang","tick":0}},{"op":"loot_create","data":{"item":"/d/hangzhou/npc/obj/goupi","location":"/d/hangzhou/guozhuang","qty":1,"reason":"fight"}}]}
{"seq":3,"tick":0,"actor":"player:accept","verb":"loot","outcome":"ok","args":{"item":"/d/hangzhou/npc/obj/goupi"},"effects":[{"op":"loot_take","data":{"entity":"player:accept","item":"/d/hangzhou/npc/obj/goupi","location":"/d/hangzhou/guozhuang","qty":1,"weight":5000}}]}
{"seq":4,"tick":1,"actor":"system","verb":"system_room_reset","outcome":"ok","args":{"tick":1},"effects":[{"op":"entity_respawn","data":{"entity":"/d/hangzhou/npc/wolfdog","location":"/d/hangzhou/guozhuang","hp":200,"max_hp":200,"attrs":{"con":24,"dex":7,"int":8,"kar":11,"str":26},"combat_exp":1000,"death_tick":0,"death_location":"/d/hangzhou/guozhuang","equipment":[],"equipped":{},"equipped_apply":{},"inventory":{},"max_stamina":100.0,"stamina":100.0,"race":"走兽","skill_levels":{},"skill_xp":{},"temp_apply":{"apply/armor":2,"apply/attack":15,"apply/damage":6},"weapon_type":""}}]}
{"seq":5,"tick":1,"actor":"player:accept","verb":"fight","outcome":"ok","args":{"target":"/d/hangzhou/npc/wolfdog"},"effects":[{"op":"hp_add","data":{"entity":"/d/hangzhou/npc/wolfdog","delta":-200,"reason":"fight"}},{"op":"entity_status","data":{"entity":"/d/hangzhou/npc/wolfdog","status":"dead","location":"/d/hangzhou/guozhuang","tick":1}},{"op":"loot_create","data":{"item":"/d/hangzhou/npc/obj/goupi","location":"/d/hangzhou/guozhuang","qty":1,"reason":"fight"}}]}
```

结果：第一次狗皮被捡走后 `ground[goupi]=0`；reset 后 `ground[goupi]=0`（**不复制**）；
再杀后 `ground[goupi]=1`，玩家背包仍有第一张 → 共 2 张。

**关于“狗皮重新出现在地面”**：原版不会发生，也不应发生。`room.lpc::reset()` 只在
`objectp(槽位对象)` 为假时 `make_inventory()`；狗皮不在 `set("objects")` 里，且被捡走后
对象仍存在于玩家背包，槽位指针有效 → reset 不可能再生成第二张。`guozhuang.lpc` 的
`objects` 只有 `wolfdog:2`。因此验收口径是：

- 狗皮不会凭空复制（上面 `ground_after_reset=0`）；
- 狗重生后再杀会再掉一张（上面 `seq=5`）；
- “被消耗/销毁的**房间声明物品**会补回”由 `tests/test_xkx_room_reset.py::test_taken_item_is_not_duplicated_but_missing_item_is_replenished`
  覆盖（合成房间；xkx 真实房间没有可消耗的声明物品）。

### 5.2 老虎（重生的那一刻）

```json
{"seq":2,"tick":0,"actor":"player:accept","verb":"fight","outcome":"ok","args":{"target":"/d/hangzhou/npc/laohu"},"effects":[{"op":"entity_status","data":{"entity":"/d/hangzhou/npc/laohu","status":"dead","location":"/d/hangzhou/shanlu6","tick":0}},{"op":"loot_create","data":{"item":"/clone/medicine/vegetable/hugu","location":"/d/hangzhou/shanlu6","qty":1,"reason":"fight"}}]}
{"seq":3,"tick":2,"actor":"system","verb":"system_room_reset","outcome":"ok","args":{"tick":2},"effects":[{"op":"entity_respawn","data":{"entity":"/d/hangzhou/npc/laohu","location":"/d/hangzhou/shanlu6","hp":200,"max_hp":200,"attrs":{"con":5,"dex":5,"int":10,"kar":7,"str":7},"combat_exp":30000,"death_tick":0,"death_location":"/d/hangzhou/shanlu6","max_neili":800,"neili":800,"race":"走兽","temp_apply":{"apply/armor":1000,"apply/attack":20000,"apply/defense":30000}}}]}
```

- **满血**：`hp == max_hp == 200`（由 `resolve_npc_vitals` 的基线决定；原版为 `new()` 后
  的满值）。
- **是否仍主动攻击**：**不会**。原版 `feature/attack.lpc::init()` 对 `attitude=="aggressive"`
  调 `COMBAT_D->auto_fight`；复刻没有实现 `init()`/`auto_fight`（`grep -rn "auto_fight|kill_ob"
  src/wsim` 为空）。这是 P5 之前就存在的缺口，不是 reset 引入的；reset 只恢复 Vitals，
  不会改变攻击能力。若要补，需单独立项实现进入房间时的自动战斗。

## 6. 确定性、哈希与回归

- 关闭态：`tests/test_xkx_determinism_legacy.py` 全绿，旧九地点
  `735` 事件、`state_final 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9`、
  `event_final 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae` 不变。
- 打开态、健康世界（无死亡/无缺失）：零 `system_room_reset` 事件，哈希与关闭态完全一致
  （`tests/test_xkx_room_reset.py::test_enabled_reset_is_reproducible_and_noop_on_healthy_world`）。
- 同 seed 两次打开态一致（同一测试内 `reset-on-a == reset-on-b`）。
- 实体数：重生复用原实体 id，不新增实体（测试 `len(state.entities)` 不变）。
- `content_hash`：P5 未改 `content/xkx/**`，`adc6c474…` 前后一致，无需 `--reset`。
  （最后一次内容侧变更是远端合并 `8f1b2dd`，与本轮无关。）
- 全量 `python3 -m pytest -q` 通过。

## 7. 已知偏差（明确不做/近似）

1. `return_home` 只对无进行中目标的空闲 NPC 生效。原版会拉所有非战斗 living NPC；
   复刻的 NPC 是 tick 级多步寻路，若每 1–2 tick 无条件拉回，NPC 永远到不了目的地，
   整张世界的移动/交易/工作会停摆（实测 `move 335→6`）。因此保留“空闲游走者归位”，
   放弃“打断任务中的 NPC”。这是刻意的可玩性/可复现性取舍，写进本 spec。
2. 周期粒度 10 分钟（见第 3 节）。
3. 商店货架 30 件上限（见第 2 节）。
4. 老虎/大狼狗等 `init()` 主动攻击未实现（见 5.2）。
5. 不建模房间 unload / `clean_up`；`no_clean_up` 语义不影响事件流，故省略。
6. 战利品统一落地为地面物品（`landing=ground`）；原版老虎的虎骨先放尸体再随尸体落地，
   尸体对象未建模。
