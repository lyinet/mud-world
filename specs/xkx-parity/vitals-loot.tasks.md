# 任务清单：vitals-loot

对应规格：`specs/xkx-parity/vitals-loot.spec.md`

## T1 玩家创世带气血与属性
- 文件：`src/wsim/actions/player.py`
- 改动：`ARCHETYPE_ATTRS`、`PLAYER_MAX_HP=100`、`PLAYER_BASE_VITALS`；`player_genesis_effects()` 与 `PlayerEnterAction.apply()` 两处 `create_entity` 带 `max_hp/hp/attrs`
- 验收：新角色 `max_hp==hp==100`，`attrs` 六项齐全；`fight` 不再因缺血拒绝
- 测试：`tests/test_combat_fight.py` 增加“玩家创世有血”断言

## T2 NPC 创世落 max_hp
- 文件：新增 `src/wsim/sim/vitals.py`；改 `src/wsim/sim/genesis.py`
- 改动：`resolve_npc_vitals(npc)`；`max_hp = max_qi` 优先，缺失按 500/300/200 三档，`max(1, ...)`；带 `attrs/combat_exp/skill_levels`
- 门控：仅当 NPC dict 含 `vitals/attrs/skills` 键时生效
- 验收：xkx 92 NPC 全部 `max_hp>0`；4 个缺 `max_qi` 的为 200/500/500/300；legacy 实体不含新键
- 测试：`tests/test_combat_genesis_vitals.py`（在 `tests/test_combat*` 范围内）

## T3 地面掉落状态 + Effect
- 文件：`src/wsim/world/state.py`、`src/wsim/world/effects.py`
- 改动：`WorldState.ground`（非空才序列化）；`loot_create` 落地面；新增 `loot_take`；`EFFECT_OPS` 增项
- 验收：地面/背包数量守恒；旧世界 `to_dict()` 不含 `ground`

## T4 `loot` / `get` 动作
- 文件：新增 `src/wsim/actions/loot.py`；改 `src/wsim/actions/registry.py`（aliases 支持 + 注册）；改 `src/wsim/actions/player.py`（`PLAYER_VERBS`）
- 改动：`LootAction`（verb `loot`，aliases `("get",)`），按 item id 排序取物；`loot_not_found` / `not_here` / `bad_qty` 原因码
- 验收：取走地面全量或指定物品；空地面给 `loot_not_found`；同 seed 顺序确定

## T5 战斗掉落改落地面
- 文件：`src/wsim/actions/combat.py`
- 改动：`loot_create` 带 `location/weight`，不再直塞攻击者背包
- 验收：`rounds[]` 战报不变；击杀后地面有掉落

## T6 测试与基线
- `tests/test_combat_loot.py`：取物成功 / 空地面原因码 / 同 seed 确定性 / 重量数量一致
- `tests/test_combat_fight.py`：断言改为地面掉落
- `tests/test_xkx_determinism_combat.py`：显式重采并注释原因
- 复跑：`test_xkx_determinism_legacy.py`、`test_p3e_golden.py` 必须逐字节不变

## T7 真实 xkx 世界一战
- 用 `load_xkx_content("content/xkx")`（只调用）在 `/tmp` 脚本里创世 + 玩家 + `fight` + `loot`
- 产物：事件 effects、`rounds[]`、双方血量、掉落/捡拾、`sum(hp_add.delta)==state 增量`
- 不写入仓库（脚本放 `/tmp`）

## T8 文档与提交
- 更新 `specs/xkx-parity/combat-implementation.md`：数值表（原版 vs 默认）、NPC 默认规则、地面掉落与 `get` 语义、重采记录
- 显式路径 `git add`；中文 commit；`git fetch` 后快进 push，冲突即停

## T9 地面来源分桶（世界层）
- 文件：`src/wsim/world/state.py`、`src/wsim/world/effects.py`
- 改动：`WorldState.ground_sources`（非空才序列化）；`loot_create` 读 `source/source_entity/source_tick`
  并合并同 key 条目；`loot_take` 支持定向 `source` 扣减，未指定时先扣未归因部分
- 验收：`ground_sources` 为空时 `to_dict()` 无该键；归因总量不超过聚合；旧事件回放不产生来源桶

## T10 动作语义分离
- 文件：`src/wsim/actions/loot.py`、`src/wsim/actions/combat.py`
- 改动：`LootAction` 按 `scope`（`kill`/`all`）取物；`kill` 只取最近击杀条目，无来源报 `loot_no_corpse`；
  `_drop_effects` 两处 NPC 死亡传 `source="kill"`；不动 `combat_rules.py`
- 验收：击杀掉落可 `loot`；地面既有物品不被 `loot` 带走；`get all` 仍清地面

## T11 文字壳命令口径
- 文件：`src/wsim/api/xkx_shell.py`
- 改动：按首词区分 `loot`/`get`；`get all` 清地面；`get <物品> [数量]` 透传 qty；裸 `get` 给用法；
  `loot_no_corpse` 提示「这里没有刚死的东西可搜刮。」；帮助文本同步
- 验收：`loot` 无击杀不静默；`get` 不再误触全捡

## T12 测试与副本验证
- 文件：`tests/test_combat_loot.py`、`tests/test_combat_effects.py`、`tests/test_combat_death_equipment.py`、
  `tests/test_equipment_actions.py`、`tests/test_xkx_api.py`
- 验收：T9–T11 全项覆盖；单测通过；在 `/tmp/xkx-loot-verify/` 的 events.sqlite 副本上起 8051/8052
  验证，不读写 `runs/xkx-world`；三段提交并 push `fix/xkx-loot-semantics`

