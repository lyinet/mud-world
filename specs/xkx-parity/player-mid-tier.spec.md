# 开局角色改为自洽的原版 MID 档（Spec）

## 0. 决策（已定，不再讨论）

把 `content/xkx` 世界的开局 endowment 从「`combat_exp=250` + `unarmed=40 / dodge=10 / parry=10`」
改成自洽的 **MID 档**：

- `combat_exp = 6400`
- `skill_levels = {"unarmed": 40, "dodge": 40, "parry": 40}`
- 空手、`max_hp=100`、六维沿用既有身份表。

### 依据（原版，不是编的角色）

1. `tools/xkx_ref/src/xkx100/cmds/skill/learn.lpc:102`
   `my_skill * my_skill * my_skill / 10 > (int)me->query("combat_exp")` 时拒绝继续学。
   `40^3 / 10 = 6400`，即 **6400 是技能 40 级的最低合法经验**；低于它，“40 级武艺”在原版
   数据上不自洽。
2. 同一门槛在 `cmds/skill/practice.c`、`cmds/skill/study.lpc` 复现；
   `tools/xkx_ref/measure_hit_rate.py:6` 的原版 MID 快照说明也写明
   “exp 6400，raw 技能 40/40/40，无兵器；只改存档数据，不改 LPC”。
3. 原版 MID 实测已在 `reports/xkx-ref/hit-rate.md` 落盘：
   - MID × 店小二：对方命中 `0/164 = 0.00%`
   - MID × 流氓：对方命中 `14/179 = 7.82%`
   - MID × 老虎：对方命中 `152/152 = 100.00%`
   这说明 `6400 + 40/40/40` 是原版可观察的合法档位，不是我们发明的数值。

### 产品口径（必须写进文档）

**开局档位是产品选择，不是原版新手档。** 原版新手档是 `combat_exp≈0`、技能近 0
（见 `reports/xkx-ref/hit-rate.md` 的 NEW 档），本仓库保留其原始实测数据，将来可做
“新手体验”对照；本次不改原版系数、不改 `tools/xkx_formula/**`。

## 1. 代码改动（精确到文件/函数）

### 1.1 `config/worlds/xkx.yaml`

在 `archetype` 附近新增：

```yaml
# 开局档位：产品选择，不是原版新手档。
# combat_exp=6400 是原版技能 40 级的最低经验（40^3/10，cmds/skill/learn.lpc:102）；
# unarmed/dodge/parry 同级 40，保证攻防两侧同一档位、数据自洽。
player_start:
  combat_exp: 6400
  skills:
    unarmed: 40
    dodge: 40
    parry: 40
```

### 1.2 `src/wsim/actions/player.py`

- `PLAYER_START_COMBAT_EXP = 6400`
- `PLAYER_START_SKILL_LEVELS = {"unarmed": 40, "dodge": 40, "parry": 40}`
- 更新第 35–47 行注释：说明这是 **MID 产品档位**，依据 `40^3/10`；不再保留
  “dodge/parry 只给 10 避免无敌”的旧理由（旧理由记录在 git 历史与
  `combat-implementation.md` §9.1 的“已废止”行）。
- `_player_vitals(archetype: str = "", start: dict | None = None) -> dict`
  - 默认取模块常量；
  - `start` 为 dict 时只接受合法整数覆盖：
    - `start["combat_exp"]` 为非负 `int` → 覆盖；
    - `start["skills"]` 为 dict → 逐键覆盖（键为 `str`，值为非负 `int`），未给出的技能保留默认；
  - 非法/缺省一律回落默认，不抛异常（配置是外部输入，属系统边界）。
- `player_genesis_effects(..., start: dict | None = None)`：
  `entity_data.update(_player_vitals(DEFAULT_ARCHETYPE, start))`。
- `PlayerEnterAction.apply`：
  `start = intent.args.get("player_start")`，创建/补血两处都传 `self._vitals(archetype, start)`。
- `PlayerEnterAction._vitals(self, archetype: str, start: dict | None)` 透传。

### 1.3 `src/wsim/api/session.py`

- 新增 `WorldSession._load_player_start(path) -> dict | None`，模式与
  `_load_npc_wander` 一致：缺文件/解析失败/非 dict → `None`。
- `__init__` 中 `self.player_start = self._load_player_start(self.world_config_path)`。
- `_args` 的 `action == "enter"` 分支增加 `"player_start": self.player_start`。
  这是 `perform("enter", …)` 的唯一汇聚点，HTTP 与文字壳都经过它，动作层不再散落配置读取。
- genesis 分支调用 `player_genesis_effects(..., start=self.player_start)`。

### 1.4 不改的东西（红线）

- `tools/xkx_formula/**` 零改动。
- 不改任何原版系数、伤害/命中/招架/经验压制公式。
- `tests/test_xkx_determinism_legacy.py` 的 `735 / 009076f0… / 02d5f812…` 必须原样通过。
- 旧九地点内容没有 `player_start`，`_load_player_start` 返回 `None`，`_player_vitals`
  行为与本次改动前一致（模块常量若被外部调用，语义仍为确定性默认）。

## 2. 文档改动

`specs/xkx-parity/combat-implementation.md` §9.1：

1. 默认值表更新为 `combat_exp=6400`、`unarmed/dodge/parry=40/40/40`，来源列写
   `40^3/10` 原版门槛。
2. 明确一句：
   > **开局档位是产品选择，不是原版新手档。** 原版新手档（技能近 0、`combat_exp≈0`）
   > 的实测数据保留在 `reports/xkx-ref/hit-rate.md` 的 NEW 档，将来可做“新手体验”对照。
3. 保留旧行并标注“已废止（2026-11-07 前：250 + 40/10/10）”，不静默改数。
4. 新增 `§9.1.1 MID 档复测（seed 1–40）`：贴本次 6 个对手的表与口径。

## 3. 测试改动与验证

### 3.1 必须更新

`tests/test_combat_vitals.py`：

- `test_player_genesis_has_hp_and_attrs`：断言 `combat_exp == 6400`、
  `skill_levels == {"unarmed":40,"dodge":40,"parry":40}`。
- `test_player_enter_assigns_archetype_vitals_and_backfills_old_entity`：
  同样改为 MID；新增一个显式 `player_start` 覆盖用例：
  `player_start={"combat_exp": 1234, "skills": {"unarmed": 7}}`
  → `combat_exp=1234`，`unarmed=7`，`dodge/parry` 保持 40。

### 3.2 回归

```bash
cd <worktree>
PYTHONPATH=src python -m pytest -o addopts="" -q
```

必须全绿；旧世界三项哈希必须仍是
`735 / 009076f0f0346352e328d8c158a3017a3e26fbc0b2c59ef7ca20c8fc3d1f780ce9 / 02d5f812c427833387e592ad9321ebd72a9c5c719825b958f53af83d795078ae`。

若 `tests/test_xkx_determinism_combat.py` 漂移：该测试用显式 genesis 实体、不读玩家创世，
预期不漂移；若漂移，先查是否误把 `player_start` 注入了 genesis，再决定是否显式迁移基线。

## 4. 重测（真实对局，非单测）

### 4.1 对手与取样

- 对手：店小二、流氓、小沙弥、刀客、绿营清兵、老虎。
- seed：1–40（老虎取 1–10；若结果稳定，报告中说明取样减少）。
- 每个 `(对手, seed)` 独立 `WorldSession`，`spawn` 取该 NPC 所在房间，
  `content_root=content/xkx`，`world_config=config/worlds/xkx.yaml`，`endow=False`。
- 只走真实动作：`perform(account, "enter", …)` → `perform(account, "fight", {"target": npc_id})`。
  不注入属性、不手工构造 Entity。

### 4.2 指标口径

- 胜/负/平：`combat["winner"]`，`attacker`=玩家胜，`defender`=玩家负，空串=平。
- 平均回合数：`combat["rounds_used"]` 的算术平均（注明是否只统计胜场）。
- 玩家剩余气血中位数：`combat["hp"][player_id]` 的中位数。
- 对方命中率：
  `count(NPC 出手 result=="hit") / count(NPC 出手)`；
  NPC 出手 = `rounds[].actions[]` 中 `actor == npc_id` 且 `result in {hit,dodge,parry}`。
  若 NPC 一场都没出手（被先手秒杀），记为 `-` 并单列。

### 4.3 双跑一致

至少取 2 场（例如 店小二 seed=1、流氓 seed=2）：

- 两个独立 `run_dir`、同 seed、同 spawn、同对手；
- 比较 `sha256(events.sqlite)`；
- 比较战斗事件里的 `combat_hash`（若代码中不存在该字段，则用 `EventStore.event_hash()`，
  并在报告中说明口径）。

### 4.4 报告表

最终报告必须包含：

| 对手 | seed 数 | 胜/负/平 | 平均回合 | 玩家剩余气血中位数 | 对方命中率 |
| --- | --- | --- | --- | --- | --- |

并附“改前（250 经验）全败”的横向对照行/说明。

## 5. 交付

- 独立 worktree：`git worktree add /tmp/xkx-mid-wt -b fix/xkx-mid-tier origin/xkx-parity`
- 显式 `git add` 改动文件（配置、`player.py`、`session.py`、测试、文档、本 spec/tasks）。
- 提交后 `git fetch` + 必要时 `git rebase origin/xkx-parity`。
- `git push origin HEAD:xkx-parity`（**非 force**）。
- 最终只报一次，附上表、双跑哈希、旧九地点三项哈希、公式零改动声明。

## 6. 边界

- 若 MID 档出现“打谁都稳赢、对方命中率 0”，**不自己加难度**。把数字贴出来，由用户决定。
  原版 MID 对店小二本来就是 0%（`reports/xkx-ref/hit-rate.md`），因此 0% 本身不是缺陷，
  但必须与原版对照后报告。
