# 任务拆分（player-marriage）

按提交顺序，每笔单独 pathspec + push。

## 提交 1：规格
- [x] `specs/player-marriage/spec.md`
- [x] `specs/player-marriage/tasks.md`
- [x] `specs/player-marriage/evidence/baseline.json`（实现前无玩家世界哈希基线）

## 提交 2：实现
- [ ] `world/state.py`：`WorldState.marriages`（仅非空进哈希）+ clone/to_dict
- [ ] `world/effects.py`：`marriage_meta` 原子 op；`EFFECT_OPS` 注册
- [ ] `actions/marriage.py`：决策函数 + 5 个事件动作 + `spouse_time`
- [ ] `actions/registry.py`：注册新动作
- [ ] `sim/intent.py`：玩家配偶每日 `spouse_time` 日程
- [ ] `actions/social.py`：`TalkAction` 配偶专属台词
- [ ] `api/session.py`：白名单/别名/`_args`/前置校验/多事件链/`marriage` 视图/原因码
- [ ] `docs/player-api.md`：契约与原因码
- [ ] `CHANGELOG.md`：逐条记录

## 提交 3：测试
- [ ] `tests/test_player_marriage.py`：决策边界、事件链、守恒、重婚/和离、哈希确定性
- [ ] 旧基线测试保持通过（不修改旧期望值）

## 提交 4：文档与验收证据
- [ ] `specs/player-marriage/evidence/`：真实服务 + ambient 全链路、拒绝样例、对账、哈希、pytest/selfcheck 输出
- [ ] `CHANGELOG.md` 补验收结果

## 验证命令
- `python3 -m pytest`
- `./scripts/selfcheck.sh`
- 无玩家基线：`python3 sim/run_headless.py --days 2 --seed 42 --npc 12 --out <tmp> --run-id before`
- 婚姻场景确定性：同一脚本跑两次比较 `world_hash.txt` / `event_hash.txt`
