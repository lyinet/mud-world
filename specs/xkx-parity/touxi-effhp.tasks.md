# tasks：偷袭窗口 + `eff_hp` 初始化 + 高配验收

依赖 spec：
- `specs/xkx-parity/npc-aggression.spec.md` §“偷袭窗口与登记时机”
- `specs/xkx-parity/eff-qi.spec.md` §11

范围：只做下列两处代码修正；高配验收只归档证据，不改默认档。

## T1 偷袭窗口（已完成）

- [x] `src/wsim/world/state.py`：新增 `Entity.aggro_tick: int = 0` / `aggro_target: str = ""`；
      `to_dict()` 仅在 `aggro_tick` 非 0 时输出。
- [x] `src/wsim/sim/aggression.py`：规则表携带 `delay`（always=0，chance_kar_per/unless_item=1）。
- [x] `src/wsim/actions/aggression.py`：`trigger` 命中只写 `aggro_tick = intent.tick + delay`，
      不写 `combat_target`；新增 `mode="promote"` 转正（目标仍 alive 且同房间才登记）。
- [x] `src/wsim/sim/scheduler.py`：`_process_npc_aggression` 在 `tick >= aggro_tick` 时提交 `promote`。
- [x] `tests/test_xkx_npc_aggression.py` 增三例：wolfdog 掷中后第 1 tick ok / promote 后拒；
      laohu 进房后即拒；未触发 clone 始终可偷袭。
- [x] seed=42 复跑（`/tmp/touxi_window_after.py` → `/tmp/touxi_window_after.json`）：
      狼狗 canonical 同 tick `touxi=ok`、tick 1 转正后拒；老虎同 tick 即拒。
- [x] 旧世界 golden / 基线哈希全绿（`pytest -k "golden or baseline or hash"` 通过；
      全量 1128 passed / 18 skipped，基线 `ebf5d50`）。

## T2 `eff_hp` 初始化（已完成）

- [x] `src/wsim/world/effects.py` `create_entity`：写完 `max_hp/hp` 后
      `if ent.max_hp > 0: ent.eff_hp = int(ent.max_hp)`。
- [x] `src/wsim/world/effects.py` `entity_respawn`：数值覆盖完成后写 `eff_hp = max_hp`
      （`-2` 仅作为 `max_hp==0` 旧内容包的兜底保留）。
- [x] `tests/test_combat_eff_qi.py` 增三例：genesis 满值、respawn 覆盖后满值、
      `to_dict()` 不序列化 `-2`。
- [x] 全量 `pytest` + 旧世界基线哈希比对。

## T3 高配号验收（证据已归档）

- [x] 临时配置 `/tmp/xkx-highexp.yaml`：`combat_exp=500000`、
      `skill_levels={unarmed:500, dodge:500, parry:500}`（**非默认档**，默认 6400/40）。
- [x] seed=42，`ansuan 老虎 at 西下行` 459 轮：911 次出手 / 9 次命中 = **0.988%**；
      每刀 `44,52,46,45,60,46,65,49,40`（合计 447）；老虎 425→0 **unconcious**，
      玩家 100→18 alive。
- [x] 证据文件：`/tmp/tiger_highexp2.json`、`/tmp/tiger-highexp2/run/events.sqlite`。
- [ ] 可选：同 run 对 0 血老虎补一刀，确认 `eff_qi<0` 转入 dead（本轮范围外，未做）。
