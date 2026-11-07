# Tasks · xkx 冒烟四缺陷修复

## 阶段 A：缺陷 1（wimpy 自动逃跑）与缺陷 2（score 门派）——先报一次

- [x] A0 确认不新增 `flee` 命令：不加 `flee` 别名/壳层入口/配置开关
- [x] A1 `Entity` 增 `wimpy: int = 0`；`to_dict()` 仅在非 0 时输出
- [x] A2 `effects.py`：`EFFECT_OPS` 增 `wimpy_log`；`_ENTITY_FIELDS` 增 `wimpy`；`create_entity` 接受
- [x] A3 新增 `src/wsim/actions/wimpy.py`：`WimpyAction`
  - [x] 无参：`你现在的当「气」低于 {n}% 时就会尝试逃跑。`
  - [x] 参数仅 0..80；越界/非整数返回原版帮助，不写状态
  - [x] 合法：`set_field(wimpy)` + `wimpy_log`
- [x] A4 `actions/registry.py` 注册；`session.py` `WRITE_ACTIONS`/`ACTION_ALIASES` 加 `wimpy`
- [x] A5 `xkx_shell.py`：`VERB_ALIASES` 加 `wimpy`；`execute` 分派 `_cmd_wimpy`（状态/帮助/成功三态）
- [x] A6 `actions/aggression.py` `continue`：伤害前插 `_wimpy_escape`
  - [x] 触发条件：玩家 `wimpy>0` 且 `hp*100 <= wimpy*max_hp`
  - [x] PRNG 顺序（对齐原版 `do_flee`→`main()`）：`below(len(sorted_exits))` 先选方向，再 `below(dex_player)`，再 `below(dex_npc)`；失败也消费方向骰
  - [x] 失败：`wimpy_log failed`（含 `exit_index/attempted_exit/roll_order`），本回合照常战斗
  - [x] 成功：按已掷 `exit_index` 取出口；`set_location` + 清全部 `combat_target==玩家` 的 NPC + `wimpy_log success`；跳过本回合伤害
  - [x] 无出口：`wimpy_log no_exit`，照常战斗
  - [x] `wimpy==0`：不取任何随机数，既有序列不变
- [x] A7 修 `_score_lines`：解包 `state["sect"]["sect"]`，role → 门主/弟子；`in_sect=false` 仍显示未拜入
- [x] A8 新增 `tests/test_wimpy.py`（命令/成功/失败/无出口/0 不取数/同 seed 双跑）
- [x] A9 新增 `tests/test_score_sect.py`
- [x] A10 跑相关测试 + 全量 pytest；跑旧九地点三项哈希
- [x] A11 独立端口 8053/8054 真实 run：`wimpy` 无参/设 80；进老虎房不 `fight`，贴失败与成功真实事件；`bai 安健刚` 前后 `score`
- [x] A12 显式 `git add` A 阶段文件，提交；不 force
- [x] A13 向用户报告第一批（源码结论、真实响应、哈希、pytest）

## 阶段 B：缺陷 3（大狼狗掉落）与缺陷 4（铁棍战报）

- [x] B1 `Entity` 增 `drops: dict[str,int]`；`to_dict()` 仅非空输出
- [x] B2 `effects.py` `create_entity` 接受并规范化 `drops`
- [x] B3 `sim/genesis.py` 汇总 `chance==1.0 && item_resolved && landing=="ground"`
- [x] B4 `actions/combat.py` `_drop_counts` 增 `content_drops`，同 id 取 max；两条 NPC 死亡路径都传
- [x] B5 `sim/skill_actions.py`：`WEAPON_VERBS`（源码 `inherit/weapon/*.lpc`）、`WEAPON_ACTION_TEXT`（`weapond.lpc:59-80`）、`select_weapon_action`
- [x] B6 `sim/combat_rules.py`：无映射技能 + 持武器 → `selection="weapon"`；entry 仅在持武器时写 `weapon`
- [x] B7 `api/xkx_shell.py`：`$w/$W` 渲染为武器显示名（不暴露 item id）
- [x] B8 在 `tests/test_combat_death_equipment.py`、`tests/test_skill_actions.py`、`tests/test_combat_moves.py` 补测试
- [x] B9 真实 run：`guozhuang` 杀两只大狼狗 → 地上狗皮 → `loot`；铁匠铺买 `铁棍` → `wield` → `fight` 贴战报
- [ ] B10 全量 pytest + 旧九地点三项哈希 + `git diff --stat tools/xkx_formula`
- [ ] B11 显式 `git add` B 阶段文件，提交；不 force；推 `xkx-parity`
- [ ] B12 向用户报告第二批（真实响应、哈希、pytest、公式库 diff）

## 阶段 C：额外核查（只写 spec，不实现）

- [x] C1 核查原版“玩家逃跑时老虎被其他玩家攻击（同队/非同队）”行为，写入 spec 0.7
- [x] C2 注明随机顺序修正不影响 `content_hash`，但会改变未来事件序列；旧事件重放无需 `--reset`
