# 多人同屏战斗（multi-combat）设计 spec

- 状态：**设计稿，待评审；本分支未改任何实现代码**
- 分支：`xkx-multi-combat`（基于 `origin/xkx-parity` 最新提交）
- 目标：一个房间内多个玩家攻击同一只 NPC，以及一只 NPC 同时面对多个玩家时，仇恨列表与每回合目标判定正确、不互相覆盖；仍满足事件溯源 + 单写者 + 确定性。
- 非目标：不做实时逐秒逐回合的战斗重构；不改伤害公式、不改 `tools/xkx_formula/`；不重命名/删除 `combat_target`；不改 `content/xkx/**`、`room_reset.py`、`respawn`。

---

## 1. 原版源码结论（先查后设计）

参考源码：`tools/xkx_ref/src/xkx100/`。以下行号以该目录为准。

### 1.1 敌人是数组还是单值？容量上限？追加与去重？

- **每个角色自己持有敌人数组**：`feature/attack.lpc:12` `nosave object *enemy;`，另有独立的追杀名单 `feature/attack.lpc:13` `nosave string *killer;`。
- **没有硬容量上限**。`feature/attack.lpc:10` 只有 `#define MAX_OPPONENT 4`，它**只用于选敌窗口**，不用于 append 截断。
- **追加**：`feature/attack.lpc:35-67` `fight_ob()`；同房校验在 `:40`，重复校验在 `:64-65`（`member_array` 已存在则 `return 0`），否则 `enemy += ({ ob })`。
- **追杀追加**：`feature/attack.lpc:69-114` `kill_ob()`；`no_fight` 在 `:77`，同房在 `:78`，已在 `killer` 中在 `:83-84` 去重，最后 `fight_ob(ob)`（`:113`）。
- **保护者连带入战**：`feature/attack.lpc:99-111`，目标有 `query_temp("guarded")` 时，把同房、活着、未在战斗的守卫 `enemy += guards`，并让守卫对攻击者 `kill_ob`。
- **结论**：数组；无上限；按“同一对象”去重；但受 `select_opponent` 的 `random(4)` 限制，**同时最多只有前 4 个敌人会被心跳选中出手**，其余仍留在表里、仍可在自己的心跳出手。

### 1.2 一个 NPC 面对多个玩家，每个心跳怎么挑这一个回合的对手？

- 心跳入口：`inherit/char/char.lpc:96` 调 `attack()`。
- `feature/attack.lpc:228-241` `attack()`：`:231` 先 `clean_up_enemy()`，`:233` `opponent = select_opponent()`，`:236` `COMBAT_D->fight(this_object(), opponent)`。
- 选敌实现：`feature/attack.lpc:132-149`：
  - `:139` `which = random(MAX_OPPONENT);`
  - `:140` `return which < sizeof(enemy) ? enemy[which] : enemy[0];`
- **不是第一个、不是按伤害仇恨，是 `random(4)` + 越界回退 `enemy[0]`**。因此 2 个敌人时概率不是 50/50，而是 **`enemy[0]` 75%、`enemy[1]` 25%**；≥4 个敌人时前 4 个各 25%。
- 本回合的 `victim` 就是这个被选中的 `opponent`，传给 `adm/daemons/combatd.lpc:728` `fight(me, victim)`；`fight()` 再调 `do_attack`（`:249` 起）。`victim` 忙/昏迷时走快攻（`combatd.lpc:742-746`），否则按勇气骰决定攻击或守势（`:763` 起），守势失败时 `victim` 会被 `fight_ob(me)` 拉入战斗（`:783-800`）。

### 1.3 玩家同时有多个敌人时，出手目标怎么定？

- 同一套 `select_opponent()`（`feature/attack.lpc:132-149`），不是 `query_enemy()[0]`。
- `query_enemy()`（`:17-19`）只负责返回整个数组给命令/界面；心跳出手仍用随机 4 选 1 + 越界回退首元素。
- `last_opponent` 只是临时记录（`attack.lpc:235`），供 `surrender.lpc:14` 等命令使用，不决定下次出手。

### 1.4 队友/同队与 `remove_all_enemy` 在多敌下的行为

- `feature/team.lpc` **没有“同队共享仇恨/自动围攻”逻辑**。唯一与战斗相关的是 `team.lpc:19-24` `follow_path()` 里 `remove_all_enemy()`，即跟随换房时清空**自己**的敌人表。
- 同队不会自动进 `enemy`；只有 1.1 的 `guarded` 保护机制会拉守卫入战（`attack.lpc:99-111`）。
- `remove_all_enemy()`：`attack.lpc:171-181`，遍历**自己**的 `enemy`，逐个调用 `敌人->remove_enemy(this_object())`，然后清空自己的 `enemy`；**`killer` 不清**。
- `remove_enemy()`：`attack.lpc:152-158`，若自己正在追杀该 id（`is_killing`）则 `return 0` 拒绝移除；否则从 `enemy` 摘除。
- `remove_all_killer()`：`attack.lpc:184-193`，先对每个敌人 `remove_killer(this_object())` 再清空 `killer`。
- 因此 A 逃跑不会让“正在打 B 的 NPC”停手：NPC 的 `enemy` 里仍有 B，按 1.2 继续选人；A 只是在 NPC 下次 `clean_up_enemy` 时因不同房被摘掉。

### 1.5 敌人列表的清理时机

- **每次心跳出手前**：`attack.lpc:231` → `clean_up_enemy()`（`:116-128`）：对象消失、不同房、非 living（除非仍在 `killer`）都摘除。
- **换房/逃跑成功**：`feature/move.lpc:85`、`cmds/std/go.lpc:321` 调 `me->remove_all_enemy()`。
- **昏迷/死亡**：`feature/damage.lpc:121`（unconcious）`remove_all_enemy()`；死亡后 `damage.lpc:210-211` 清敌人、`:291` `remove_all_killer`、`:292` 让同房所有人 `remove_killer(this_object())`。
- **主动停手**：`cmds/std/halt.lpc:6`、`cmds/std/surrender.lpc:21`。
- **跟随换房**：`feature/team.lpc:21`。
- `remove_enemy()` 不会因为“正在追杀”而移除；真正强制清空走 `remove_all_*` 或下一次 `clean_up_enemy`。

---

## 2. 当前引擎缺陷（核查结论）

- `Entity` 只有单值 `combat_target`：[`src/wsim/world/state.py:99-103`](../../src/wsim/world/state.py#L99-L103)。没有敌人数组、没有每目标伤害账本。
- `NpcAggressionAction` 的 `trigger` 在 `combat_target` 非空时直接 `already_fighting`（[`src/wsim/actions/aggression.py:152-153`](../../src/wsim/actions/aggression.py#L152-L153)），所以一个 NPC 只能记住一个目标。
- 玩家 `FightAction` **完全不登记** NPC 的敌人表（[`src/wsim/actions/combat.py:298-338`](../../src/wsim/actions/combat.py#L298-L338)）：第二个玩家攻击同一只怪时，NPC 侧没有任何记录，NPC 心跳不会反击第二名玩家；`_combat_view` 也只显示一个对手。
- 心跳调度只按 `combat_target` 单目标推进（[`src/wsim/sim/scheduler.py:710-750`](../../src/wsim/sim/scheduler.py#L710-L750)）。
- 玩家 wimpy 逃跑时遍历所有 NPC 并整只 `_reset`（[`src/wsim/actions/aggression.py:86-89`](../../src/wsim/actions/aggression.py#L86-L89)），多目标时会误伤“正在打别人的 NPC”。
- 诚实说明：当前代码里**字面意义的“后进场玩家覆盖前者 combat_target”**并不发生在 `trigger` 路径（该路径本来就拒绝第二次登记）；真实缺陷是“单值字段无法表达多目标 + 第二人攻击后 NPC 不认账”。本设计修的是后者并让前者在结构上不可能发生。

---

## 3. 设计

### 3.1 总原则

1. 多目标一律是 `Effect` 可表达的状态变更；不在引擎内存里另建临时敌人表。
2. 单写者：所有追加/摘除/清空走 `set_field`，随事件持久化、可回放。
3. 确定性：唯一的随机是心跳选敌；走 `PRNG(seed, seq, salt="action:npc_aggression")`，取数顺序写死（见 3.4）。
4. 兼容：`combat_target` 保留，作为“最近一次选中的对手/主目标”；旧读者（`npc_aggression`、wimpy、移动阻断、API 视图）不改契约。
5. 默认关闭；旧九地点世界零事件、零字段、零哈希变化。

### 3.2 新增状态（只做增量）

在 `Entity` 增加两个字段（位置紧邻 `combat_target` 块）：

| 字段 | 类型 | 语义 | 序列化 |
| --- | --- | --- | --- |
| `combat_enemies` | `list[str]` | 本实体的敌人表，**登记顺序**保存，按 id 去重；对应原版 `enemy` | 仅非空时写入 `to_dict()` |
| `combat_contrib` | `dict[str,int]` | 该 NPC 实际受到的伤害，按攻击者累计；供验收与未来击杀归属 | 仅非空时写入，按 key 排序 |

- `combat_target`、`combat_tick`、`combat_round` 保持原名与原语义。`combat_enemies` 非空时约定 `combat_target ==` 最近一次选中的目标；为空时 `combat_target == ""`。
- `combat_contrib` 只统计“真正扣掉的气血”：`max(0, hp_before - max(0, hp_after))`，不含 dodge/parry/overkill。
- 旧存档迁移：读取时 `effective_enemies(ent) = dedup(ent.combat_enemies or ([ent.combat_target] if ent.combat_target else []))`；不写假 genesis 事件，下一次真实写入时自然落盘。

### 3.3 录入与摘除（Effect 落点）

- 玩家 `fight` 打 NPC（multi 开）：
  - 开打前追加 `set_field combat_enemies = old + [attacker]`（已存在则不写）；
  - 若 `combat_target` 为空，同时写 `combat_target = attacker`、`combat_tick = intent.tick`、`combat_round = 0`；
  - 对应原版 `kill.lpc` 的双向登记（玩家 `kill_ob(NPC)` + NPC `kill_ob(玩家)`）。
- NPC `trigger`（multi 开）：仍要求规则命中、目标合法；`combat_enemies` 为空时才允许，等价原版 `init()` 中 `is_fighting()` 早退（`attack.lpc:253`）。**第二个玩家进房不会自动被拉战**，必须自己 `fight`。
- multi 开时 `continue` 放宽 NPC 门槛：只要是非玩家、`max_hp>0`、敌人表非空，即可心跳反击；不再要求一定命中 `AGGRESSION_SPECS`（对齐 `kill.lpc:82-84`：被玩家攻击的 NPC 会反击）。`trigger` 仍只对规则表内 NPC 生效。
- 摘除只摘“这一个”：
  - 目标死亡/离线/换房/实体消失 → 从表中移除该 id；表空才清 `combat_target/tick/round`；
  - NPC 死亡 → 整表清空（并保留现有死亡掉落/战报顺序）；
  - 玩家 wimpy 逃跑 → 从**所有** NPC 的表中只移除该玩家；不再整只 `_reset`。
- 换房不立即写事件（与原版一致）：NPC 下一次心跳 `clean_up_enemy` 等价的 prune 时摘掉。
- `remove_all_enemy` 的等价：仅在本方主动收兵/NPC 死亡/表被 prune 空时整表清空。

### 3.4 心跳选敌与骰序（multi 开）

`_process_npc_aggression` 每 tick 对每个已交战 NPC 仍只发**一条** `continue`：

- `args.targets` = `effective_enemies(npc)` 按登记顺序过滤（活着、在线、同房），`args.target` = `targets[0]`（兼容旧字段）。
- `NpcAggressionAction.continue` 的取数顺序固定为：

| 顺序 | 骰 | 说明 |
| --- | --- | --- |
| 1 | `opp_index = rng.randint(0, MAX_OPPONENT-1)`，`MAX_OPPONENT=4` | 完全复刻 `attack.lpc:139 random(4)` |
| 2 | `chosen = targets[opp_index] if opp_index < len(targets) else targets[0]` | 复刻 `attack.lpc:140` 越界回退 |
| 3 | 先 prune 失效目标，再按上面规则选；prune 不额外消耗骰 | prune 结果写入日志 |
| 4 | wimpy：`exit_index` → `roll_target` → `roll_npc` | 沿用现有 `_wimpy_escape` 顺序 |
| 5 | 若逃跑成功：不跑战斗骰，直接返回 | 与原版心跳“先逃跑再攻击”一致 |
| 6 | `simulate_fight(... max_rounds=1)` 内部骰序不变 | 一次心跳一回合 |

- `aggression_log` 增写：`targets`、`opponent_index`、`opponent_roll`、`pruned`。
- 选中的 `chosen` 写回 `combat_target`；`combat_round += 1`（NPC 级回合，不按目标分开）。
- **不按伤害仇恨加权**：原版没有仇恨权重，`combat_contrib` 只做归属审计，不参与选敌。

### 3.5 伤害归属

- `combat_effects` 在结果 trace 上累计：对每个 `result=="hit"` 的 action，`target==NPC.id` 时累加到 `combat_contrib[actor]`（实际扣气血）。
- 玩家 fight 与 NPC 心跳都走同一函数，因此双方贡献都记在被打的 NPC 上；一次事件内最多一个 `set_field combat_contrib`。
- 不新增 `combat_last_attacker`；最后一击由事件顺序 + `rounds` 可审计，避免冗余字段。

### 3.6 开关、迁移、回滚

- 开关：环境变量 `XKX_MULTI_COMBAT`，取值 `1/true/on` 开，缺省关；**进程启动时读一次**，运行中改环境无效。
- **不在 `config/worlds/default.yaml` 增键**；不改任何默认世界配置。
- 关：所有分支保持现状——不写 `combat_enemies/combat_contrib`，不追加登记，选敌不额外取骰，`_reset` 行为不变；旧九地点事件数与三项哈希必须逐字节不变。
- 开：按本 spec 生效。
- 回滚：取消环境变量并重启即可；`combat_target` 始终是“最近/主目标”，旧逻辑可直接接管；`combat_enemies/combat_contrib` 残留但不参与关闭态逻辑。
- multi 开且只有 1 个敌人时，仍消耗一次 `random(4)`（复刻原版），因此 multi-on 的既有战斗确定性基线会前进一位；**只允许新增 multi-on 基线，不允许修改旧基线**。

### 3.7 API 契约变更清单（只列，不动 `src/wsim/api/`）

按协作约束，需要改 API 时先批准再动：

1. `_combat_view(pid)`（`src/wsim/api/session.py`）：
   - 保留 `opponent` / `opponent_name`（= `combat_target`）不变；
   - 新增 `opponents: [{id,name}]`，从所有 NPC 的 `effective_enemies` 中反查包含该玩家的项；
   - 新增 `engaged_count`。
2. `npc_aggression` 心跳事件的 `targets` 透出（如果公共世界流需要），否则仅留在事件里供回放。
3. 不加新写入口；`fight` 的 `max_rounds` 演练参数沿用现有 args，HTTP 验收通过该参数逐回合推进。

### 3.8 文件范围

计划改动（实现阶段）：

- `src/wsim/world/state.py`：两个字段 + `to_dict()` 非空写出（紧邻现有 `combat_target` 块，纯增量）。
- `src/wsim/world/effects.py`：`_ENTITY_FIELDS` 增两字段 + `set_field` 对这两个字段做形状校验（纯增量）。
- `src/wsim/sim/aggression.py`：`MAX_OPPONENT=4`、`effective_enemies()`、开关读取（小工具）。
- `src/wsim/actions/aggression.py`：trigger 追加、continue 选敌/prune、wimpy 只摘一个、reset 全清。
- `src/wsim/actions/combat.py`：fight 登记 + `combat_contrib`。
- `src/wsim/sim/scheduler.py`：每 tick 传 `targets`（单意图不变）。
- `tests/test_xkx_multi_combat.py`（新增）；必要时加 multi-on 确定性基线测试。

**不碰**：`content/xkx/**`、`src/wsim/sim/room_reset.py`、`respawn` 相关、`src/wsim/sim/combat_rules.py`、`tools/xkx_formula/**`、`src/wsim/api/**`（除 3.7 经批准）。

与并发会话冲突处理：`state.py` / `effects.py` 只加不删、hunk 紧贴 `combat_target` 块；实现前先 `git pull --rebase origin xkx-parity`，冲突则停下上报，不覆盖他人未提交改动。

---

## 4. 验收方案

### 4.1 单元测试（新增 `tests/test_xkx_multi_combat.py`）

1. 两玩家对同一 NPC 发 `fight` → NPC `combat_enemies` 含两人、顺序=登记顺序、去重；`combat_target` 保持主目标。
2. `select_opponent` 映射表：`targets` 长度 1/2/3/4/5 × `opp_index` 0..3，逐一断言与 `attack.lpc:139-140` 一致（2 人时首目标 75%）。
3. prune 只摘失效目标：A 换房后 B 仍在表内，`combat_target` 不丢。
4. wimpy：A 逃跑只从 NPC 表里移除 A，B 与该 NPC 的战斗继续。
5. `combat_contrib`：多轮交战后按攻击者求和正确，overkill 不计。
6. 关闭态：同一场景不产生 `combat_enemies/combat_contrib` 事件/字段，事件数不变。
7. multi-on 同 seed 双跑：`state_final` / `event_final` 一致。

### 4.2 旧世界三项哈希（红线）

```
python -m pytest tests/test_xkx_determinism_legacy.py -q
# events 735
# state_final 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9
# event_final 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae
```

基线已在 `xkx-multi-combat` 工作树复跑通过。实现后必须仍是这三项。

### 4.3 真实 HTTP 双玩家

前提：只重启玩家 API（8031），启动前 `XKX_MULTI_COMBAT=1`；UI/管理台不动。

1. 两个账号 A/B `POST /api/player/enter`，进入同一房间（大狼狗所在 `d/hangzhou` 房间）。
2. A `POST /api/player/action` `{"action":"fight","args":{"target":"...wolfdog","max_rounds":2}}`；B 对同一目标同样操作。
3. 逐回合证据：
   - 两次 `fight` 的 `combat_log.rounds` 都出现 `actor=player:A|player:B`、`target=wolfdog`，且 `combat_contrib` 事件按攻击者分别累加；
   - `npc_aggression continue` 事件 `targets` 同时含 A/B，`opponent_index` 逐 tick 可读；NPC 的反击目标不再固定为最后一人；
   - wolfdog 的共享 `hp` 单调下降，没有因第二人登记而重置。
4. 击杀后：A/B 各自 `look`/`state` 都能看到地面狗皮，各自 `loot` 成功（共享地面，先到先得，符合原版 `wolfdog.lpc:38-43` + 单房间尸体语义）。
5. 同 seed 双跑比对；全量 `python -m pytest -q` 全绿。
6. 公式库零改动证据：`git diff origin/xkx-parity... -- tools/xkx_formula` 为空。

### 4.4 提交纪律

- 显式 `git add` 指定文件；push 前 `git pull --rebase`；不 force；中文提交说明；冲突停下上报。

---

## 5. 风险与开放问题（请评审拍板）

1. **被动 NPC 反击范围**：multi 开时 `continue` 取消“必须在 `AGGRESSION_SPECS`”的限制，让被玩家攻击的普通 NPC 也反击（对齐 `kill.lpc:82-84`）。若不想扩大行为面，可改为“只登记规则表内 NPC”，但那样非主动 NPC 仍不会还手。
2. **`combat_contrib` 是否保留**：目前只有验收/审计消费；若评审认为状态膨胀，可砍掉，改为从 `combat_log.rounds` 事后统计。
3. **API `opponents`**：不改也能验收（事件里已有）；但玩家界面只能看到主目标。是否本期改由评审决定。
4. **单敌仍消耗 `random(4)`**：复刻原版，但会让 multi-on 的既有 xkx 战斗基线与关闭态不同；接受以“新增 multi-on 基线”方式处理。
