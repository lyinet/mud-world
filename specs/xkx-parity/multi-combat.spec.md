# 多人同屏战斗（multi-combat）设计 spec

- 状态：**已按评审意见修订；待最终批准后实现**
- 分支：`xkx-multi-combat`（基于 `origin/xkx-parity` 1cbbcb3）
- 目标：一个房间内多个玩家攻击同一只 NPC、以及一只 NPC 同时面对多个玩家时，敌人表与每回合目标判定正确、不互相覆盖；仍满足事件溯源 + 单写者 + 确定性。
- 非目标：不改伤害公式、不改 `tools/xkx_formula/`；不重命名/删除 `combat_target`；不改 `content/xkx/**`、`room_reset.py`、`combat_rules.py`、`respawn`；API `opponents` 本期不改。

---

## 0. 评审拍板结论（七条，逐条落实）

| # | 结论 | 落点 |
| --- | --- | --- |
| 1 | 被动 NPC 反击：**先取证再实现**。原版 `kill.lpc:82/98` 会双向 `kill_ob`，理论上还手；但必须先在 4004 用 `kill 店小二`×5 实测。原版不还手则照不还手实现。`trigger`（规则表主动开战）语义不变。 | §1.6、§4.1-A、任务 T1/T4 |
| 2 | `combat_contrib` 保留。语义 = **实际扣气血累计**，不是仇恨权重，**不参与选敌**。 | §3.3 |
| 3 | API `opponents` 本期不改。spec 记录待办与玩家可见后果：客户端只能看到当前主目标。 | §3.6 |
| 4 | `trigger` 在多敌下登记**全部**合法玩家，不挑一个。测试：两人同房触发 → 表长 2 → 多 tick 目标分布 75/25。 | §3.2、§3.4、§4.1-B |
| 5 | 合杀经验归属已查清：**只看最后一击**（`last_damage_from`），不是分摊、不是首个 `kill_ob`。大狼狗自定义 `die()` 跳过 `killer_reward`，杀它**两人都无经验**。 | §1.7 |
| 6 | 追杀名单 `killer`：**本期不做**。明确记录偏差：逃跑即彻底脱战；原版追、我们不追。 | §1.8 |
| 7 | 每回合只用一个 `select_opponent` 选出的 `victim`。引擎没有独立的门/守势/反击状态，唯一需要锁死的是“选敌骰最先、随后 simulate_fight 原有骰序、无交错”。`guarding` 原版是**角色级**临时标记，不是对手级。 | §3.4、§4.1-C |

---

## 1. 原版源码结论（先查后设计）

参考源码：`tools/xkx_ref/src/xkx100/`。以下行号以该目录为准。

### 1.1 敌人是数组还是单值？容量上限？追加与去重？

- **每个角色自己持有敌人数组**：`feature/attack.lpc:12` `nosave object *enemy;`，另有独立追杀名单 `feature/attack.lpc:13` `nosave string *killer;`。
- **没有硬容量上限**。`feature/attack.lpc:10` 的 `MAX_OPPONENT 4` **只用于选敌窗口**，不截断 append。
- **追加**：`feature/attack.lpc:35-67` `fight_ob()`；同房 `:40`，重复 `:64-65`（`member_array` 已存在则 return），否则 `enemy += ({ ob })`。
- **追杀追加**：`feature/attack.lpc:69-114` `kill_ob()`；`no_fight` `:77`，同房 `:78`，`killer` 去重 `:83-84`，最后 `fight_ob` `:113`。
- **结论**：数组；无上限；按同一对象去重；心跳最多选中前 4 个之一，其余仍在表里、可在自己心跳出手。

### 1.2 一个 NPC 面对多个玩家，每个心跳怎么挑这一个回合的对手？

- `inherit/char/char.lpc:96` 心跳调 `attack()`。
- `feature/attack.lpc:228-241` `attack()`：`:231` `clean_up_enemy()`，`:233` `select_opponent()`，`:236` `COMBAT_D->fight(this_object(), opponent)`。
- `feature/attack.lpc:132-149`：`:139` `which = random(MAX_OPPONENT)`；`:140` `return which < sizeof(enemy) ? enemy[which] : enemy[0]`。
- **不是第一个、不是按伤害仇恨，是 `random(4)` + 越界回退 `enemy[0]`**。2 个敌人时 `enemy[0]` 75%、`enemy[1]` 25%；≥4 个时前 4 个各 25%。

### 1.3 玩家同时有多个敌人时，出手目标怎么定？

- 同一套 `select_opponent()`（`attack.lpc:132-149`），不是 `query_enemy()[0]`。
- `query_enemy()`（`:17-19`）只返回整表给命令/界面；心跳出手仍随机 4 选 1 + 越界回退首元素。
- `last_opponent`（`:235`）只供 `surrender.lpc:14` 等命令，不决定下次出手。

### 1.4 队友/同队与 `remove_all_enemy`

- `feature/team.lpc` 无同队共享仇恨；唯一相关是 `team.lpc:19-24` 跟随换房清**自己**敌人表。
- `remove_all_enemy()`：`attack.lpc:171-181`，遍历自己 `enemy` 逐个 `敌人->remove_enemy(this_object())` 后清空；**`killer` 不清**。
- `remove_enemy()`：`attack.lpc:152-158`，自己正在追杀该 id 时 `return 0` 拒绝移除。
- `remove_all_killer()`：`attack.lpc:184-193`。
- A 逃跑不会让正在打 B 的 NPC 停手；A 在 NPC 下次 `clean_up_enemy` 时因不同房被摘掉。

### 1.5 清理时机

- 每次心跳出手前：`attack.lpc:231` → `clean_up_enemy()`（`:116-128`）：对象消失、不同房、非 living（除非仍在 `killer`）摘除。
- 换房/逃跑成功：`feature/move.lpc:85`、`cmds/std/go.lpc:321` 调 `remove_all_enemy()`。
- 昏迷/死亡：`feature/damage.lpc:121`；死亡 `:210-211` 清敌人、`:291` `remove_all_killer`、`:292` 同房所有 `remove_killer`。
- 主动停手：`cmds/std/halt.lpc:6`、`surrender.lpc:21`。

### 1.6 被动 NPC 是否会还手（4004 实测已确认）

- `cmds/std/kill.lpc:82` 玩家 `me->kill_ob(obj)`；`:89-98` 对非玩家目标 `obj->kill_ob(me)`。
- `kill_ob` → `fight_ob`（`attack.lpc:69-114/35-67`）把对方加入 `enemy` 并 `set_heart_beat(1)`（`attack.lpc:38`）。
- 因此**理论上**店小二被 `kill` 后会在下一个心跳 `attack()`；已在 4004 实测 5 场，**5/5 全部还手，且 5/5 把测试玩家打死**：
  - 每场 `kill 店小二` 后立即出现 `看起来店小二想杀死你！`，随后 `店小二提起拳头/往...抓/踢...` 主动出手；
  - 还手回合：第 1 场约 6 次、第 2 场 3 次、第 3 场 4 次、第 4 场 2 次、第 5 场 3 次 NPC 出手（每次均伴随 `你...造成一处瘀伤/瘀青/血痕`）；
  - 玩家气血全部下降并 5/5 死亡（`你的眼前一黑...` → 鬼门关）。
  - 结论：`attitude=friendly` 不阻止还手。`kill` 命令走 `kill.lpc:82/98` 的 `obj->kill_ob(me)`，`kill_ob` → `fight_ob` 直接入 `enemy` 并 `set_heart_beat(1)`，不经过 `auto_fight` 的 `looking_for_trouble`/`is_fighting` 门槛。**实现被动反击**（`PASSIVE_RETALIATION = True`），不再按理论放宽。
- 引擎实现点：multi 开时玩家 `FightAction` 把攻击者登记进防守 NPC 的 `combat_enemies`；之后该 NPC 心跳即按 §3.4 还手。`trigger` 的规则判定不变。

### 1.7 合杀经验/技能成长归属（决定性证据）

- 伤害来源只记“最后一个伤害来源”：`feature/damage.lpc:22`（`receive_damage`）与 `:48`（`receive_wound`）的 `set_temp("last_damage_from", reason)`。
- 死亡：`feature/damage.lpc:230` 取 `killer = query_temp("last_damage_from")`，`:253` `COMBAT_D->killer_reward(killer, this_object())`。昏迷：`:115-116` 用同一来源 `winner_reward`。
- `adm/daemons/combatd.lpc:971-1183 killer_reward`：
  - `:978` `killer->killed_enemy(victim)`；
  - 玩家死亡惩罚 `:1011-1024`；
  - 非玩家（NPC）经验/潜能/阅历只在任务匹配时发放（`:1119-1183`，早退 `:1120`、`:1137`），**普通怪没有击杀经验**；
  - 经验入账 `:1152-1154`，潜能 `:1159-1164`，阅历 `:1165-1167`。
- **最后一击独占**：没有“所有登记者分摊”，也没有“首个 `kill_ob` 优先”。`combat_contrib` 与经验归属无关，仅审计。
- **系统性规则（不是狼狗个例）**：普通 NPC 本来就没有击杀经验；`killer_reward` 只对**任务目标**发奖励。157 个自定义 `die()` NPC 的逐项矩阵见 [kill-reward-matrix.md](./kill-reward-matrix.md)：其中 130 个不调 `::die()`、27 个调 `::die()` 进入 `killer_reward`、1 个直接调 `killer_reward`（`d/huashan/npc/lin-pingzhi.lpc` 掉 `bixie_book`）；无论哪类，普通怪的击杀经验都是 **0**。
- 大狼狗 `d/city/npc/wolfdog.lpc:41-47`、杭州大狼狗 `d/hangzhou/npc/wolfdog.lpc` **自定义 `die()` 且不调 `::die()`**：直接 destruct、掉狗皮，不走 `killer_reward`。**杀大狼狗两人均无击杀经验**。老虎 `d/hangzhou/npc/laohu.lpc:44-51` 同理（掉 `hugu` 到尸体），无击杀经验。
- **复刻侧对齐结论**：`src/wsim/actions/combat.py:254-258` 的 `combat_exp_add` 全部来自 `simulate_fight` 的逐招增量；死亡分支（`:148-220`）没有任何击杀奖励；`content/xkx/npcs.json` 无 `kill_reward` 字段。**复刻侧本来就没有“击杀即给经验”的实现，不存在“现在是给了”需要关闭的发放**；本次只新增回归测试锁死“死亡时不得新增额外 `combat_exp_add`”。若未来加入任务击杀奖励，必须按 `killer = last_damage_from` 的单人归属，不得分摊。
- 逐招成长仍按本次交手的攻/守双方各自结算（`combatd.lpc:361-372`、`:417-423`、`:579-587`），与最后一击无关。
- 引擎现状 `combat_rules.simulate_fight` 已按交手双方累计 exp；本 spec 不新增“击杀奖励”，与 wolfdog 原版一致。不新增 `combat_last_attacker`。

### 1.8 `killer`（追杀名单）：本期不做，记录偏差

- 原版 `remove_all_enemy` 不清 `killer`（`attack.lpc:171-181`）；`remove_enemy` 在 `is_killing` 时拒绝移除（`:152-158`）。
- 原版 `init()` 对 `userp(ob) && is_killing(ob->id)` 走 `auto_fight(..., "hatred")`（`attack.lpc:256-259`），所以逃跑后再进房仍会被主动追杀。
- **决策：本期不实现 `killer`/vendetta 状态。**
  - 与 `vendetta` 的关系：不新增字段、不读写 `vendetta`；现有 `combat_enemies` 只表达“当前交战”。
  - 清理时机：玩家逃跑成功即从所有 NPC 的 `combat_enemies` 移除该玩家；目标换房/死亡/离线由 prune 移除。
  - 玩家感知：**逃跑即彻底脱战**，再进房不会被同一只 NPC 自动追杀。
  - 这是与原版的**已知偏差**（原版追、我们不追），写入 changelog/验收报告，不藏。

### 1.9 `init()` 的“全部登记”语义与边界

- 通用 `feature/attack.lpc:245-273 init()`：只处理 `this_player()`，且 `:253` `is_fighting()` 早退；`combatd.lpc:815` `looking_for_trouble` 去重，因此**通用 aggressive NPC 严格原版只登记首个触发者**。
- **大狼狗** `d/hangzhou/npc/wolfdog.lpc:31-40`、**毒蛇** `d/hangzhou/npc/dushe.lpc:29-35` 覆写 `init()`，`call_out("kill_ob", 1, ob)` **不检查 `is_fighting()`**：NPC 进房/重载时驱动会对房内每个 living 各调一次 `init()`，因此可以登记多人。
- `d/hangzhou/npc/qianlong.lpc:29-38` 显式遍历 `all_inventory(environment(me))` 逐个 `kill_ob`。
- **决策（按评审第 4 条）**：multi 开时，`trigger` 一律登记全部合法玩家（活着、在线、同房、非自己、未在表中），不再“挑一个”。对通用 aggressive NPC 这是相对原版的**行为放宽**，只影响 multi-on，且已记录。
- `trigger` 的规则判定（`aggressive`/`unless_item`/`chance_kar_per`）**不变**；放宽只针对“登记人数”。

---

## 2. 当前引擎缺陷（核查结论）

- `Entity` 只有单值 `combat_target`：[`src/wsim/world/state.py:99-110`](../../src/wsim/world/state.py#L99-L110)。无敌人数组、无每目标伤害账本。
- `trigger` 在 `combat_target` 非空时直接 `already_fighting`（[`src/wsim/actions/aggression.py:152-153`](../../src/wsim/actions/aggression.py#L152-L153)），NPC 只能记一个目标。
- 玩家 `FightAction` **完全不登记** NPC 敌人表（[`src/wsim/actions/combat.py:298-338`](../../src/wsim/actions/combat.py#L298-L338)）；第二人打同一只怪时 NPC 侧无记录。
- 心跳只按 `combat_target` 单目标推进（[`src/wsim/sim/scheduler.py:710-750`](../../src/wsim/sim/scheduler.py#L710-L750)）。
- 玩家 wimpy 逃跑遍历所有 NPC 整只 `_reset`（[`src/wsim/actions/aggression.py:86-89`](../../src/wsim/actions/aggression.py#L86-L89)），多目标时误伤“正在打别人的 NPC”。
- 诚实说明：字面意义的“后进场覆盖前者”当前不发生在 `trigger`（该路径拒绝第二次登记）；真实缺陷是单值字段无法表达多目标、第二人攻击后 NPC 不认账。

---

## 3. 设计

### 3.1 总原则

1. 多目标一律是 `Effect` 可表达的状态变更；不在引擎内存另建临时敌人表。
2. 单写者：追加/摘除/清空全部走 `set_field`，随事件持久化、可回放。
3. 确定性：唯一新增随机是心跳选敌，走现有 `PRNG(seed, seq, salt="action:npc_aggression")`；取数顺序写死（§3.4）。
4. 兼容：`combat_target` 保留为“最近一次选中的主目标”；旧读者不改契约。
5. 默认关闭；旧九地点世界零事件、零字段、零额外骰、三项哈希不变。

### 3.2 新增状态（纯增量）

| 字段 | 类型 | 语义 | 序列化 |
| --- | --- | --- | --- |
| `combat_enemies` | `list[str]` | 本实体敌人表，登记顺序、按 id 去重；对应原版 `enemy` | 仅非空写入 `to_dict()` |
| `combat_contrib` | `dict[str,int]` | 该实体**实际被扣气血**按攻击者累计；审计用，不是仇恨权重 | 仅非空写入，key 排序 |

- `combat_target/tick/round` 原名原语义。`combat_enemies` 非空时约定 `combat_target ==` 最近一次选中的目标；为空时 `combat_target == ""`。
- `combat_contrib` 只累计 `max(0, hp_before - max(0, hp_after))`，不含 dodge/parry/overkill。
- 旧存档迁移：读取用 `effective_enemies(ent) = dedup(combat_enemies or ([combat_target] if combat_target else []))`；不写假 genesis 事件。
- **`combat_contrib` 语义边界（硬约束）**：只回答“谁实际打掉了多少血”；**不参与 `select_opponent`**，不得作为仇恨/威胁值使用。原版选敌不看伤害（`attack.lpc:139-140`）。

### 3.3 录入与摘除（Effect 落点）

- 玩家 `fight` 打 NPC（multi 开 + §1.6 取证支持反击）：
  - `set_field combat_enemies = old + [attacker]`（已存在不写）；
  - `combat_target` 为空时同时写 `combat_target=attacker`、`combat_tick=intent.tick`、`combat_round=0`；
  - 对应 `kill.lpc:82/98` 的双向登记。
  - 若 4004 实测“原版不还手”，则**不写**这条登记，仅保留 NPC 主动 `trigger` 路径。
- NPC `trigger`（multi 开）：规则命中后，登记**所有**合法玩家（§1.9）；`combat_target` 为空时写主目标三件套。
- `continue`（multi 开）：只要非玩家、`max_hp>0`、敌人表非空即可心跳反击；不再要求命中 `AGGRESSION_SPECS`（`kill.lpc:82-84`）。`trigger` 仍只对规则表内 NPC 生效。
- 摘除只摘“这一个”：目标死亡/离线/换房/消失 → 移除该 id；表空才清三件套；NPC 死亡整表清空；玩家 wimpy 逃跑 → 从**所有** NPC 表中只移除该玩家。
- 换房不立即写事件：NPC 下一次心跳 prune 时摘掉。
- `remove_all_enemy` 等价：仅本方主动收兵 / NPC 死亡 / 表被 prune 空时整表清空。

### 3.4 心跳选敌、骰序与单 victim

`_process_npc_aggression` 每 tick 对每个已交战 NPC 仍只发**一条** `continue`：

- `args.targets` = `effective_enemies(npc)` 按登记顺序（过滤活着/在线/同房后），`args.target = targets[0]` 兼容旧字段。
- `NpcAggressionAction.continue` 固定顺序：

| 顺序 | 动作/骰 | 说明 |
| --- | --- | --- |
| 1 | prune 失效目标（不耗骰） | 等价 `attack.lpc:231 clean_up_enemy` |
| 2 | `opp_index = rng.randint(0, MAX_OPPONENT-1)` | 复刻 `attack.lpc:139 random(4)` |
| 3 | `chosen = targets[opp_index] if opp_index < len(targets) else targets[0]` | 复刻 `:140` 越界回退 |
| 4 | wimpy：`exit_index` → `roll_target` → `roll_npc` | 沿用现有 `_wimpy_escape`，只对 `chosen` |
| 5 | 逃跑成功则不跑战斗骰，直接返回 | 与原版“先逃跑再攻击”一致 |
| 6 | `simulate_fight(npc, chosen, max_rounds=1)`，内部骰序不变 | 本回合唯一 victim |

- **单 victim 锁死**：本 tick 的门槛/文案/命中/闪避/招架/伤害/反击全部由 `combat_effects(state, npc, chosen, ...)` 的同一个 `defender` 承担；不存在“门用一个、反击用另一个”。引擎 `simulate_fight` 是双边 duel 结算（`combat_rules.py:560-566`），天然只有一个 `victim`。
- **原版 `guarding` 是角色级**：`combatd.lpc:744/764/783-800` 的 `query_temp("guarding")` 存在角色上，不按对手分开。引擎**没有** `guarding` 状态（`combat_rules.py` 无该字段），本 spec 不新增；若未来加，必须保持角色级并只作用于 `chosen`。
- `aggression_log` 增写 `targets`、`opponent_index`、`opponent_roll`、`pruned`。
- `chosen` 写回 `combat_target`；`combat_round += 1`（NPC 级回合，不按目标分）。
- **不按伤害加权**：原版无仇恨权重。

### 3.5 伤害归属

- `combat_effects` 产出 `hp_add` 后，multi 开且被打的是非玩家：`set_field combat_contrib = old + {attacker: max(0, hp_before-max(0,hp_after))}`；一次事件最多一条。
- 玩家 fight 与 NPC 心跳共用同一函数，双方贡献都记在被打 NPC 上。
- 不新增 `combat_last_attacker`：最后一击由事件顺序 + `combat_log.rounds` 可审计。

### 3.6 开关、迁移、回滚、API 待办

- 开关：环境变量 `XKX_MULTI_COMBAT`（`1/true/on/yes` 开），**进程启动读一次**；运行中改环境无效。**不在 `config/worlds/default.yaml` 增键**。
- 关：不写 `combat_enemies/combat_contrib`，不追加登记，不额外取骰，`_reset` 三字段行为不变；旧九地点事件数与三项哈希逐字节不变。
- 开：按本 spec 生效。单敌也消耗一次 `random(4)`（复刻原版）；只新增 multi-on 基线，不改旧基线。
- 回滚：取消环境变量重启；`combat_target` 始终是主目标，旧逻辑可直接接管；残留字段不参与关闭态。
- **API 待办（本期不改，必须记录）**：
  1. `_combat_view(pid)` 仍只返回 `combat_target` 对应的 `opponent`；**玩家可见后果：被两人围殴的 NPC，每个客户端只看到“当前主目标”，看不到还有谁在打**。
  2. 后续里程碑新增 `opponents`/`engaged_count`，并在 spec 的验收里补 UI 证据。
  3. `npc_aggression` 事件的 `targets` 本期只留在事件里供回放，不透出公共世界流。

### 3.7 文件范围（实现阶段）

- `src/wsim/world/state.py`：两字段 + `to_dict()` 非空写出（紧贴 `combat_target` 块）。
- `src/wsim/world/effects.py`：`_ENTITY_FIELDS` 增两字段 + `set_field` 形状校验；`entity_respawn` 增清两字段。
- `src/wsim/sim/aggression.py`：`MAX_OPPONENT=4`、`multi_combat_enabled()`、`effective_enemies()`、`select_index()`。
- `src/wsim/actions/aggression.py`：trigger 全量登记、continue 选敌/prune、wimpy 只摘一个、reset 全清。
- `src/wsim/actions/combat.py`：fight 登记（受 §1.6 结论控制）+ `combat_contrib`。
- `src/wsim/sim/scheduler.py`：每 tick 传 `targets`（单意图不变）。
- `src/wsim/api/session.py`：**仅** `_trigger_npc_aggression` 的 `already_fighting` 判定改为“目标是否已在敌人表”；不改响应契约。
- `tests/test_xkx_multi_combat.py`（新增）。

**不碰**：`content/xkx/**`、`src/wsim/sim/room_reset.py`、`respawn`、`src/wsim/sim/combat_rules.py`、`tools/xkx_formula/**`、API 响应体。

---

## 4. 验收方案

### 4.1 单元测试（`tests/test_xkx_multi_combat.py`）

- **A. 被动还手取证开关**：一个 `PASSIVE_RETALIATION` 常量由 4004 结果决定；测试断言当前取值与证据一致（原版还手 → fight 后 NPC 表含攻击者；不还手 → 表为空）。
- **B. trigger 全量登记 + 选敌分布**：
  - 两玩家同房 + always 规则 → `combat_enemies` 长度 2、顺序=登记顺序、去重；
  - **≥200 tick**（实现取 400 tick）采样，输出实际计数：`targets[0]` 期望 75%（300/400）、`targets[1]` 期望 25%（100/400），容差 ±7 个百分点（即 272-328 / 72-128）；
  - **三人以上**再跑一组：4 人时 `random(4)` 命中 0/1/2/3 各 25%；5 人时 `random(4)` 只有 0..3 四个面、不会越界，因此**只有前 4 个进入选敌窗口、index 4 永不被选中**，前 4 各 25%（不是“第 5 个回退到 index 0”）；真正发生回退的是敌人少于 4 个时（roll >= count → `targets[0]`）；
  - 每组都记录实际计数与断言，不只跑一回合。
- **C. 骰序锁死（`RecordingPRNG`）**：一 tick 的 draw 序列 = `select(0..3)` → `simulate_fight` 原有序列；对照关闭态，开启态只在最前多一笔 `random(4)`，其余逐笔一致；无其他新增骰。
- **D. 单 victim**：构造两目标，断言本 tick 的 `combat_log` 只有 `chosen` 的 target，且 `combat_contrib` 只累加 `chosen` 相关伤害；另一目标 hp 不变。
- **E. prune / wimpy**：A 换房只摘 A、B 仍在；A wimpy 成功只移除 A，B 与 NPC 战斗继续。
- **F. `combat_contrib`**：多轮后按攻击者求和正确，overkill 不计，且**不影响**选敌分布。
- **G. 关闭态逐字节一致**：同一 fight 场景在父提交 `1cbbcb3` 与实现分支各跑一次（关闭 `XKX_MULTI_COMBAT`），事件 JSON 序列逐字节 diff 为空；并断言无 `combat_enemies/combat_contrib` 字段/事件、draws 数不变。
- **H. multi-on 同 seed 双跑**：`state_final`/`event_final` 一致。

### 4.2 旧世界三项哈希（红线）

```
python -m pytest tests/test_xkx_determinism_legacy.py -q
# events 735
# state_final 009076f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9
# event_final 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae
```

基线已在 `xkx-multi-combat` 工作树复跑通过。实现后必须仍是这三项。

### 4.3 4004 被动还手实测结果（已完成，T1）

- 实例：`tools/xkx_ref` 参考 lib，端口 4004；账号 `xkxref`（角色「测试侠客」）；地点醉仙楼 `d/city/zuixianlou.lpc`；每场 `kill xiao er` 后观察 12 秒。
- 5/5 场全部还手；5/5 场玩家被打死。逐场记录：

| 场次 | 还手 | NPC 出手次数 | 玩家是否掉血 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 是 | 6 | 是（头顶/两肋/右手/颈部/后心…） | 死亡 |
| 2 | 是 | 3 | 是（颈部/右臂/两肋） | 死亡 |
| 3 | 是 | 4 | 是（后心/左臂/小腹） | 死亡 |
| 4 | 是 | 2 | 是（两肋/右肩） | 死亡 |
| 5 | 是 | 3 | 是（左腿/左耳；右手被挡开） | 死亡 |

- 关键日志：每场均出现 `你对着店小二喝道...` → `看起来店小二想杀死你！` → `店小二提起拳头往...` / `店小二往...抓/踢` → `你...造成一处瘀伤/瘀青/血痕` → `你的眼前一黑...` → 鬼门关。
- 结论：`PASSIVE_RETALIATION = True`。`enemy` 非空即可心跳 `attack()`，与 `attitude` 无关；`kill_ob` 入口不走 `auto_fight`，因此没有 `is_fighting`/`looking_for_trouble` 的早退问题。

### 4.4 真实 HTTP 双玩家（8031）

前提：独立实例 8031，启动前 `XKX_MULTI_COMBAT=1`；**不动 8011 老板档**。

1. A/B 两账号 `POST /api/player/enter` 进同一大狼狗房间。
2. A/B 分别 `fight` 同一只 wolfdog（可用 `max_rounds` 逐回合推进）。
3. 证据：
   - 两份 `combat_log.rounds` 均含 `actor=player:A|player:B`、`target=wolfdog`；
   - NPC 侧 `npc_aggression continue` 事件 `targets` 含两人，`opponent_index` 逐 tick 可读；
   - 同一实例存活期间 `hp` 单调下降；死亡后 `room_reset` 重生是**新实例**，不得把重生后的满血误读成旧实例回血；
   - `combat_contrib` 事件按攻击者累加。
4. 合杀后：**贴一行“经验归属”实测**——A/B `combat_exp` 前后不变（wolfdog 无击杀经验），符合 §1.7。
5. 击杀两具狼狗尸体各掉 1 张狗皮：A 从第一具 `loot` 成功、B 从第二具 `loot` 成功，证明两人都能从共享地面取物（单具只有 1 张，先到先得）。
6. 同 seed 双跑一致；全量 `python -m pytest -q` 全绿。
7. 公式库零改动：`git diff origin/xkx-parity... -- tools/xkx_formula` 为空。

### 4.5 提交纪律

- 显式 `git add` 指定文件；push 前 `git pull --rebase`；**不 force**；中文提交说明；冲突停下上报。

---

## 5. 已知偏差（必须随交付报告）

1. **逃跑不追杀（玩家可感知）**：原版逃跑只摘 `enemy`、`killer` 保留（`attack.lpc:152-158/171-181`），再进房会被 `userp(ob) && is_killing(ob->id)` 的 `hatred` 自动开战（`attack.lpc:256-259`）；**我们逃跑即彻底脱战，再进同一房间不会被该 NPC 主动追杀**，也不会在路途中被追。玩家会明显感到“比原版更容易甩掉敌人”。本期不做 `killer`/vendetta，后续若做需新增独立 `vendetta` 字段与清理时机，不与 `combat_enemies` 混用。
2. **通用 aggressive NPC 的 trigger 放宽**：原版 `init()` 在 `is_fighting` 时早退，只登记首个；multi 开时我们登记全部合法玩家（大狼狗/毒蛇本来就是多登记，通用 NPC 属放宽）。
3. **玩家侧敌人表不建模**：原版玩家也有 `enemy` 数组；引擎玩家出手是显式 `fight`，不对玩家做心跳自动选敌。
4. **无 courage 门/守势/反击状态**：引擎 `combat_rules` 是双边 duel，不实现 `combatd.lpc:763-800` 的门与 `guarding`；多敌下只需保证单一 `chosen`，不伪造这些状态。
5. **`combat_contrib` 不是仇恨**：不得用于选敌，只做伤害审计。
6. **API 只显示主目标**：客户端看不到其余围攻者，待后续里程碑。
