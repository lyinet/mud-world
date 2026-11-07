# 招式序列与技能钩子接入战报 — 验收清单

- [x] `skills_actions.json` 22 技能 / 247 普通招式 / 126 个 default 系数全 0 已被测试遍历断言。
- [x] 选招按原版 `query_action()` 语义：`seq=max{i|level>action[i-1].lvl}`、`seq=random(seq)`、取 `action[seq]`。
- [x] 选招随机走 `PRNG(seed, seq, salt)`；有招式路径首笔取数 `(2,1)` 已锁进 `tests/test_combat_moves.py`。
- [x] 缺键 / `default` 系数一律取 0；战报遇到缺键不崩，JSON 无 `None/NaN`。
- [x] `force_hit` 接 6 个有 code 的内功；`martial_hit` 接 mantian-xing / yunlong-zhua。
- [x] `victim_force_hit` / `victim_parry_hit` / `weapon_hit` 保持 0，并在文档写明“源码整段被注释 / 无实现 / 由武器对象提供”。
- [x] 招式只影响表现与既有系数：B（系数归零）→ C（接入后）差异回合为 0，未新增伤害来源。
- [x] 真实 xkx 世界打两场（店小二 + 老虎），战报显示原版动作文案；有映射技能的李沅芷显示「白蛇吐信」。
- [x] 同 seed 伤害序列差异逐项归因到 `combatd.lpc:280-295` 的 `reset_action()/query_action()` 掷点平移。
- [x] 旧九地点世界哈希逐字节不变：state `009076f0…` / event `02d5f812…`，`test_xkx_determinism_legacy` 与 `test_p3e_golden` 原值通过。
- [x] 同 seed 两条真实老虎会话双跑：state `c22f4b14…`、event `0e4ef8d9…` 两次一致，战报文本逐行一致。
- [x] `pytest tests` 全绿：884 passed，18 skipped。
- [x] `combat-implementation.md` §3 / §5 / §7 / §12 已更新；`skills-actions.spec.md` 已补默认种族招式与 `race` 推断。
- [x] 显式路径 `git add`，未带入其他工作流文件。
- [x] 提交并推 `origin xkx-parity`（未推 `master`）。
