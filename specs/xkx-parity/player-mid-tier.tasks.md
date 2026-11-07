# 开局 MID 档：任务清单

- [x] T1 建独立 worktree（`origin/xkx-parity` → `/tmp/xkx-mid-wt`，分支 `fix/xkx-mid-tier`）。
- [x] T2 `config/worlds/xkx.yaml` 新增 `player_start`（6400 / 40·40·40）。
- [x] T3 `src/wsim/actions/player.py`：常量改 MID；`_player_vitals(archetype, start)`；
      `player_genesis_effects(..., start)`；`PlayerEnterAction` 透传 `player_start`。
- [x] T4 `src/wsim/api/session.py`：加载 `player_start`，注入 `enter` payload 与 genesis。
- [x] T5 更新 `tests/test_combat_vitals.py`（MID 断言 + 显式覆盖用例）。
- [x] T6 全量测试：`PYTHONPATH=src python -m pytest -o addopts="" -q`；记录旧九地点三项哈希。
- [x] T7 重测 6 对手 × seed 1–40（老虎 1–10），产出胜/负/平、平均回合、
      剩余气血中位数、对方命中率；附改前（250 经验）对照。
- [x] T8 同 seed 双跑一致：2 场 `file_sha256` + `combat_hash`（或 `event_hash`）。
- [x] T9 更新 `specs/xkx-parity/combat-implementation.md` §9.1 + §9.1.1。
- [x] T10 显式 `git add`、提交、`git fetch`/必要时 rebase、`git push origin HEAD:xkx-parity`（非 force）。
- [x] T11 最终报告一次（表 + 双跑 + 旧哈希 + 公式零改动）。
