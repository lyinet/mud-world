# tasks：长跑三项指标转 PASS

- [x] T0 口径确认：不改 `tools/soak_test.py` 的阈值与采样窗口，不改任何基线值；
      清理只走确定性事件/纯派生状态（按世界 tick 或序号），不读墙钟。
- [ ] T1 建立绿基线：`pytest tests/test_p3e_golden.py tests/test_reproducibility.py -q`
      在 HEAD 工作树上全部通过，记录结果。
- [ ] T2 `src/wsim/mcp/gateway.py`：outbox 改为“只清已投递 + 满则 `outbox_full` 背压”；
      tokens 改为“TTL 过期 + 满则 `token_capacity_exceeded`”，不再 FIFO 静默淘汰。
      同步 `tests/test_mcp.py`（如断言旧丢弃行为则改为断言拒绝码）。
- [ ] T3 跑 T1 的两组确定性测试（gateway 改动不得触碰 sim/actions/world）。
- [ ] T4 `src/wsim/actions/quests.py`：复核 completed 保留窗口实现（只清 completed、
      `if state.players` 门控、按 512 批 `quest_prune`）；补/改 `tests/test_quest_health.py`。
- [ ] T5 跑 T1 的两组确定性测试 + `tests/test_quest_health.py`。
- [ ] T6 `src/wsim/world/writer.py`：`effects` 为空时跳过 `clone()`；其余不动。
- [ ] T7 跑 T1 的两组确定性测试（clone 路径改动是最敏感的一步，必须逐字节一致）。
- [ ] T8 提交 1/2/3（显式 pathspec，每次带 CHANGELOG），push ff；
      提交后 `git show <commit> -- src/wsim/api/session.py | grep -ci recent_rows`
      应为 0，并贴 `git status --short` 证明未吞他人改动。
- [ ] T9 2h soak 复跑（50 玩家/2s，`--strict`），贴 `summary.json` 路径与全部 check 行；
      不达标如实报告并给出下一个热点。
