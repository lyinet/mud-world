# Tasks · delivery-p3e

## E1 玩家实体与动作
- [x] `src/wsim/world/state.py`：`PlayerProfile`、`WorldState.players`、clone/to_dict（空不写哈希）
- [x] `src/wsim/world/effects.py`：`player_upsert` / `player_presence`
- [x] `src/wsim/actions/player.py`：enter/heartbeat/leave/system_player_presence/system_tick/genesis helper/status
- [x] `src/wsim/actions/registry.py` 注册新动作，保留 `player_presence`
- [x] `src/wsim/actions/social.py` TalkAction：同地点、在线、去懒创建、spoofed 保留
- [x] `src/wsim/actions/social.py` apprentice/marry/recruit：离线守卫 + NPC→玩家关闭
- [x] `src/wsim/actions/commerce.py` TradeAction：离线守卫 + 公平闸门 + service_item
- [x] `src/wsim/actions/quests.py`：玩家并发任务上限 2
- [x] `player_endowment`：运行中新账号首次进入一次性发放（显式 mint + 独立事件 + `endowed` 幂等 + `endowment_total` + 200 席位/60000 文上限）

## E2 HTTP API
- [x] `src/wsim/api/session.py`：WorldSession + MCP 三段审批 + request_id 幂等 + system_tick
- [x] `src/wsim/api/server.py` + `__init__.py`：10 个端点，JSON 响应
- [x] `docs/player-api.md`：字段、示例、错误码表（字段冻结 v1，真实 JSON 已贴）
- [x] 契约 v1.1：首次进入发放、`details.endowment` 真实返回、`granted_at_genesis` 语义修正、`event_seq +2/+3` 说明

## E3 十分钟走查
- [x] `tools/playthrough.py`：HTTP 全流程 + BFS + 叙事 + 独立 replay
- [x] `runs/p3e-playthrough/{narrative.txt,report.json,events.sqlite}`

## LLM 追加项
- [x] `config/llm.yaml`：三槽 extra_body 候选 + decision/intent 最低档模型（deepseek-flash / v4.1-flash）
- [x] `src/wsim/agent/provider.py`：候选回退 + `thinking_disabled_by` + 生产默认真实 sleep
- [x] `src/wsim/agent/cost.py`：cost.jsonl 字段 + 槽汇总
- [x] 参数验证由 `tests/test_p3d_xing_adapter.py::test_reasoning_switches_are_per_slot` + `tests/test_provider.py` 覆盖
  （独立 `tools/thinking_probe.py` 未做，改用真实 2 天跑的 `thinking_disabled_by` 统计替代）

## P0 关闭项
- [x] P0-1 `tests/test_provider.py`：不注入 `sleep_fn` 的 429 测试（真实等待，默认 25s、测试 0.05s）
- [x] P0-2 `tests/test_p3e_grounding.py`：跨分句 / 人数 / 量词 / 俩 / 半 / 丢弃留痕 六用例
- [x] P0-4 `reports/evidence/` + `docs/evidence.md`；p3d 复现命令文件名修正；key 脱敏；README 轮换提示
- [x] P0-5 `tools/intent_coverage.py`：17 模板 × 2 条真实录制，按模板成功率 73.53%
- [x] 换模型 2 天真实跑：12 NPC / seed 42，与 Xing 并列（不可比），证据在 `reports/evidence/`

## 测试与冻结
- [x] `tests/test_p3e_player.py`、`tests/test_p3e_api.py`、`tests/test_p3e_preconditions.py`
- [x] `tests/test_p3e_endowment.py`：首次 300 / 二次不重发 / 双账号 / minted==总额 / replay 一致
- [x] 修订 `tests/test_talk_action.py` 的 enter 前置
- [x] 全量 pytest（冻结时 182 passed；复核后 193 passed）+ framework 6/6 + playthrough + replay MATCH
- [x] `reports/p3e-report.md`（真实叙事 + P0 关闭证据 + 换模型对照 + 未验证项）
- [x] commit → `git tag -a delivery-p3e` → 干净 worktree 重跑 MATCH → 回填

## P3-E 复核修复（2026-11-04，新 commit，不改写 tag）
- [x] P0-2 `_units_compatible`：人数单位与物品数量单位互斥；测试 15/15（含 `2人/3人/3位/3名` 反例）
- [x] P0-4 复现链：run 副本入库 `reports/evidence/runs/`；文档只承诺事件日志回放，整跑不承诺
- [x] K6 口径：同时输出已执行/严格/含剪枝三种口径，结论取严格口径；K6 收口后 fake **0.24% PASS**、deepseek **0.00% PASS**（含剪枝 0.85% / 0.90%）
- [x] K6 前置校验补全：`is_prunable` 位置类提案提交前丢弃、同 tick 社交优先调度、trade/quest/sell 校验覆盖；27 条旧 blocked 逐条复查见 `reports/evidence/p3e-report-stats.md` §4
- [x] framework 第 6 项改读严格 + 含剪枝口径，不再只读已执行口径打 PASS
- [x] P0-5 归因：`tools/p3e_report_stats.py` 脚本生成（6 unknown + 2 shop_type + 1 category_drift）
- [x] 换模型 2 天披露：thinking 遥测 5/19、dialogue 0 次、动作 99.3% 规则、K1 7.69% 已写入报告
