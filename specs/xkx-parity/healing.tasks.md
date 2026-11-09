# XKX Parity：疗伤与伤势恢复手段（任务清单）

- 规格：`specs/xkx-parity/healing.spec.md`
- 分支：`feat/xkx-healing`（从 `origin/xkx-parity@40121eb` 拉；不合主干）
- 已批准范围：**方案 B 收窄**——只做原语、基础内功 `recover`/`regenerate`、`player_start` 数据、开关。门派自疗 4 门与药品不做。

---

## 段 1：原语 + `busy_until` + `heal_up` 补齐（关闭态零变更）

- [x] `Entity.busy_until: int = 0`；`to_dict()` 仅非零输出（`world/state.py`）。
- [x] 7 个原语：`qi_heal` / `qi_cure` / `jing_heal` / `jing_cure` / `neili_add` / `neili_set` / `busy_set`（`world/effects.py`）。
- [x] `heal_up` 的 qi 分支补 `max_neili//10`，仅 `combat.healing` 打开时生效；jing 分支不掺 `max_neili`（`actions/system.py`）。
- [x] `combat.healing` 开关：默认 false，依赖 `combat.eff_qi`（缺失 `warnings.warn` 并降级）；`session.py` + `registry.py` 接线。
- [x] 单测锁死两条语义：`receive_heal` 封当前值、返回入参；`receive_curing` 封有效值、返回实际增量（`tests/test_healing.py`）。
- [x] 验收：`pytest` 1168 passed / 18 skipped；`grep -rn "FAKE" src/ tests/` 无输出。

## 段 2：`ExertAction` + `recover` / `regenerate`

- [ ] `ExertAction`（verb `exert`，别名 `yun`）：开关、busy、alive、未知功能拒绝。
- [ ] `recover`：`neili>=20`、`q=eff_qi-qi>=10`、`n=100*q//force_level`（下限 20，`n>neili` 时按比例缩 q）、`neili_add(-n)`、`qi_heal(q)`、战斗中 `busy_set(1)`。
- [ ] `regenerate`：jing 同构。
- [ ] `force_level`：`skill_levels["force"]` 优先，回退 `int(skills["force"]*100)`；`<=0` 拒绝。
- [ ] 明确不做：`heal` / `lifeheal` / 门派自疗 / 药品 / `force/revive`。

## 段 3：`player_start` 数据（单独提交）

- [ ] `config/worlds/xkx.yaml` 的 `player_start` 加 `force: 40`、`max_neili: 200`。
- [ ] **不许改 `content/xkx/**`**；提交前记录 `content_hash`，提交后必须仍是原值。
- [ ] 验证新建玩家：`max_neili=200`、`cps/cor` 仍是 23/24。

## 段 4：打开开关并验收（单独提交）

- [ ] `combat.healing: true`（单独提交）。
- [ ] 跑 H1–H16，报原始值（`/tmp/healing_acceptance.py`，`seed=42`）。
- [ ] 旧九地点六项必须仍是 `735 / 009076f0…780ce9 / 02d5f812…078ae / 11442 / 6 / 3`；变了停手上报。
- [ ] `pytest` 全绿；`grep -rn "FAKE" src/ tests/` 无残留。

---

## 不做（详见 spec §8）

药品、`xiantian-qigong`/`shenzhao-jing` 等 30+ 门派内功、门派自疗 4 门、`heal/cure/apply/medicate` 指令、条件类恢复、`force/revive|transfer|bipin`、`water/food` 饥渴门、`jingli/tili`、`dazuo` 内力子系统、NPC 自动疗伤 AI。
