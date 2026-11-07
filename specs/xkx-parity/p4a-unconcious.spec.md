# P4a：昏迷状态（`<昏迷不醒>`）

> 状态：已获用户批准（“P4a 昏迷状态——做。范围就按你提的最小方案”）。本文件记录源码结论、精确改动与验证口径。
> 分支：`fix/xkx-room-parity` → `origin/xkx-parity`；不碰 master，不 force/rebase。

## 0. 结论先行

1. **出生态是活的，不是昏迷。** 黄龙洞 `dushe`、郭庄 `wolfdog` 的 LPC 都没有 `set("disable")`、没有
   `set_temp("noliving/unconcious")`、没有把 `qi/jing` 设成负数的初始化代码。
2. 对 NPC 生效的 `<昏迷不醒>` 只有一个来源：
   `feature/damage.lpc:105-144` 的 `unconcious()` → `disable_player(" <昏迷不醒>")`；
   `disable_player()` 只是 `set("disable_type", type)` + `disable_commands()`
   （`feature/command.lpc:127-133`）。
3. 触发 `unconcious()` 的唯一常规路径是 `inherit/char/char.lpc:57-78` 的 `heart_beat()`：
   `my["qi"] < 0 || my["jing"] < 0` → `unconcious()`；而 `my["eff_qi"] < 0 || my["eff_jing"] < 0`
   会先走 `die()`。也就是说，**这是战斗中的临时态**（被玩家/其他 NPC 打到负血但未到致死线），
   约 `random(100 - con) + 30` 秒后 `revive()` 自动恢复。
4. 因此参考采集里的 `毒蛇(Dushe) <昏迷不醒>`、`二只大狼狗(Wolf dog) <昏迷不醒>`
   **不是原版的初始状态，而是采集会话（逐房进入/探针在场）中被打出来的战斗快照**。
   玩家 `heart_beat` 在 `is_fighting()` 时会自动 `attack()`（同文件 94-96 行），
   而 `dushe.lpc:27-36` 的 `init()` 对每个交互玩家无条件 `kill_ob`（除非带雄黄），
   `wolfdog.lpc:29-37` 在 `random(kar+per) < 30` 时 `kill_ob`，两只都会主动进攻。
5. 按用户决定，P4a 仍**把该快照态显式化**：内容包给这两只 NPC 加 `initial_state` 字段，
   创世按字段建实体，`look` 名字后追加 `<昏迷不醒>`，`_present_view` 让“非 alive 但昏迷”的实体可见。
   这是“复现采集快照”的显式标注，不是把原版出生语义改写成昏迷；字段来源在上表已写明。

## 1. 源码依据（逐条可核）

| 文件 | 行 | 内容 |
|------|----|------|
| `tools/xkx_extract/_src/xkx100/d/hangzhou/npc/dushe.lpc` | 7-25 | `set_name("毒蛇", ({ "dushe" }))`、`set("max_ging", 80)`（注意不是 `max_jing`）、`set("max_qi", 80)`；无 disable/unconcious |
| 同上 | 27-36 | `init()`：交互玩家且无 `xionghuang` → `call_out("kill_ob", 1, me)` |
| `.../npc/wolfdog.lpc` | 5-27 | `set_name("大狼狗", ({ "wolf dog", "dog" }))`、`set("age", 4)`；无 max_qi/max_jing、无 disable/unconcious |
| 同上 | 29-37 | `init()`：`random(ob->query_kar() + ob->query_per()) < 30` → `kill_ob` |
| `.../feature/name.lpc` | 121-138 | 玩家分支看 `query_temp("noliving/unconcious")`；NPC 分支 `if (!living(this_object())) str += HIR + query("disable_type") + NOR;` |
| `.../feature/damage.lpc` | 105-144 | `unconcious()`：NPC 走 `disable_player(" <昏迷不醒>")`，随后 `set("jing",0)`、`set("qi",0)`、`call_out("revive", random(100-query("con"))+30)` |
| `.../feature/command.lpc` | 127-133 | `disable_player(type)` = `set("disable_type", type)` + `disable_commands()` |
| `.../inherit/char/char.lpc` | 57-78 | `heart_beat()`：`eff_qi/eff_jing < 0` → `die()`；否则 `qi/jing < 0` → `unconcious()` |
| `.../adm/daemons/chard.lpc` | `setup_char` | 非 user 的 NPC 出生 `qi = max_qi`、`eff_qi = max_qi` |
| `.../adm/daemons/race/snake.lpc` | 68-75 | `max_jing = age*5`（age 3 → 15）、`max_qi = age*8`（被 NPC 的 80 覆盖） |
| `.../adm/daemons/race/beast.lpc` | 69-79 | age 4 → `max_jing = 70`、`max_qi = 50` |

结论：两只 NPC 创世时 `qi/jing` 均为正，是 `living` 的；`<昏迷不醒>` 只能来自战斗伤害。

## 2. 精确改动（最小方案）

1. **内容包**
   `content/xkx/npcs.json` 的两条记录各加一个字段（保持文件字母序）：
   ```json
   "initial_state": "unconcious"
   ```
   位置：`"id": "/d/hangzhou/npc/dushe"` 之后、`"is_master"` 之前；`wolfdog` 同理。
   不新增文件、不改生成器 `tools/xkx_extract/emit.py`（该字段是人工标注，重新抽取时需保留，已在报告注明）。

2. **实体 schema / 事件**
   - `src/wsim/world/state.py`：`Entity.status` 注释扩展为 `alive / unconcious / dead / reincarnated`（字段本身不变）。
   - `src/wsim/world/effects.py` 的 `create_entity`：新增可选键 `status` 解析，
     仅接受 `alive` / `unconcious`（其余抛 `EffectError`）。旧事件不带该键 → 默认 `alive`，
     `to_dict()` 输出不变，旧九地点哈希不变。

3. **创世**
   `src/wsim/sim/genesis.py` 规范 NPC 的 `entity_data` 构造处：
   ```python
   initial = str(npc.get("initial_state") or "")
   if initial and initial != "alive":
       entity_data["status"] = initial
   ```
   4a1 的同模板实例用 `copy.deepcopy(base)`，因此郭庄第二只大狼狗自动继承同一状态，
   不额外引入随机或调度。

4. **可见性**
   `src/wsim/api/session.py` `_present_view`：
   - 过滤条件 `ent.status != "alive"` → `ent.status not in ("alive", "unconcious")`；
   - 每行新增 `"status": ent.status`（`present_npcs` 字段集**扩展**，不是放宽）。

5. **显示**
   `src/wsim/api/xkx_shell.py` `_npc_present_text`：
   - 分组键由 `(template, name)` 改为 `(template, name, status)`（原版 `combined()` 按 `id + "/" + short()` 分组，
     `short()` 已含 `disable_type`，所以昏迷/清醒不能合组）；
   - 单只输出 `名字` + ` <昏迷不醒>`（当 `status == "unconcious"`）；
   - 多只输出 `中文数字 + 单位 + 名字` + ` <昏迷不醒>`。

6. **对拍工具**
   `tools/xkx_ref/parity_rooms.py` `replica_field`：NPC 的 `display_name` 若对应内容记录
   `initial_state == "unconcious"`，追加 ` <昏迷不醒>`，与 `parse_look` 的状态后缀对齐。
   归一化规则 6 条**不变**。

7. **测试**
   - `tests/test_p3e_api.py`：`present_npcs` 精确字段集追加 `"status"`（扩展，仍精确）。
   - `tests/test_xkx_clone_instances.py` 增加：
     - `test_genesis_marks_reference_unconcious_npcs`：黄龙洞 `毒蛇`、郭庄两只 `大狼狗` 的 `status == "unconcious"`；其余实体仍为 `alive`。
     - `test_look_appends_unconcious_tag_to_snake_and_wolfdogs`：两房 `look` 文本分别含有 `毒蛇 <昏迷不醒>`、`二只大狼狗 <昏迷不醒>`。
8. **基线台账**
   `docs/GOLDEN-BASELINE-LOG.md` 追加一行：`content_hash` / `xkx npc status` 变化、旧值、新值、原因、复现命令。

## 3. 验证（必须真实执行并贴原文）

1. 单元/回归：
   ```bash
   cd /private/tmp/xkx-parity-fix
   PYTHONPATH=src python -m pytest tests/test_xkx_clone_instances.py tests/test_xkx_api.py tests/test_p3e_api.py -q
   ```
2. 旧九地点三项哈希（改动前/后各一次）：
   ```bash
   PYTHONPATH=src python - <<'PY'
   # 复现 docs/GOLDEN-BASELINE-LOG.md 里无 xkx 的 legacy 世界；
   # 贴 state_hash / event_hash / events / business_cash / blocked / pruned
   PY
   ```
   期望：`735` 事件、`state 009076f0…`、`event 02d5f812…`、`business_cash 11442`、`blocked 6`、`pruned 3` 全部不变。
3. 真实世界 `look`：以 `spawn` 指向 `/d/hangzhou/huanglongdong` 与 `/d/hangzhou/guozhuang`
   各启动一次（端口避开 8011/8041），贴原始 `look` 响应。
4. 对拍：
   ```bash
   python tools/xkx_ref/parity_rooms.py --all --reparse
   ```
   贴 `npcs` 一致率/差异房变化；`items` 不变。

## 4. 明确不做 / 口径

- 不引入定时唤醒、不碰 `src/wsim/sim/scheduler.py`、不引入 `random`。
- 昏迷只是创世初始态；运行期战斗不会把实体改成 `unconcious`（原版会，但那是 P4b/P5 范围）。
- `initial_state` 是“参考快照复现”标注，不是原版出生语义；报告中会与“原版本身随机/时序”类差异分开说明。
- 旧九地点完全没有 `initial_state` 键，创世事件逐字节不变。
