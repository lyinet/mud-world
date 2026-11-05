# 任务拆分（player-marriage）

按提交顺序，每笔单独 pathspec + push。

## 提交 1：规格
- [x] `specs/player-marriage/spec.md`
- [x] `specs/player-marriage/tasks.md`
- [x] `specs/player-marriage/evidence/baseline.json`（实现前无玩家世界哈希基线）

## 提交 2：实现
- [x] `world/state.py`：`WorldState.marriages`（仅非空进哈希）+ clone/to_dict
- [x] `world/effects.py`：`marriage_meta` 原子 op；`EFFECT_OPS` 注册
- [x] `actions/marriage.py`：决策函数 + 5 个事件动作 + `spouse_time`
- [x] `actions/registry.py`：注册新动作
- [x] `sim/intent.py`：`MockReasoner.decide` 入口每日 `spouse_time`（不依赖内容目标，
      内容目标耗尽时仍生效；`Planner.plan` 不再排期，避免重复）
- [x] `actions/social.py`：`TalkAction` 配偶专属台词
- [x] `api/session.py`：白名单/别名/`_args`/前置校验/多事件链/`marriage` 视图/原因码
- [x] `docs/player-api.md`：契约与原因码
- [x] `CHANGELOG.md`：逐条记录

## 提交 3：测试
- [x] `tests/test_player_marriage.py`：决策边界、事件链、守恒、重婚/和离、哈希确定性
- [x] 旧基线测试保持通过（不修改旧期望值）

## 提交 4：文档与验收证据
- [x] `specs/player-marriage/evidence/`：真实服务 + ambient 全链路、拒绝样例、对账、哈希、pytest/selfcheck 输出
- [x] `CHANGELOG.md` 补验收结果

## 验证命令
- `python3 -m pytest`
- `./scripts/selfcheck.sh`
- 无玩家基线：`python3 sim/run_headless.py --days 2 --seed 42 --npc 12 --out <tmp> --run-id before`
- 婚姻场景确定性：同一脚本跑两次比较 `world_hash.txt` / `event_hash.txt`
