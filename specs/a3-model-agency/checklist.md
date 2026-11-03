# Checklist · A3

- [x] `sim/intent.py` 合法模型目标直接生效，urgent 仍由规则抢占
- [x] `actions/system.py` 的 `goal_source` 映射使 model/content 目标进入 K1 分母
- [x] `agent/provider.py` 模型动作按 `(verb,target)` 覆盖规则重复项，并保留 retime/retarget 路径
- [x] `agent/prompt.py` 决策 prompt 收窄为 6 个具体社交/目标动作，且可直接产出 verb/target
- [x] `sim/headless.py` 落盘 `model_goal_chains.jsonl` 与 summary `model_agency`
- [x] `pytest -q` 212 全过，golden 无漂移，未改旧基线
- [x] R 臂先跑（零成本），M 臂后跑，同 seed/天数/NPC 数
- [ ] 目标相关动作 ≥155：实测 83，FAIL
- [ ] K1 相对 R ≥ +5pp：实测 −7.69pp，FAIL
- [ ] K6 严格与含剪枝全 pass：含剪枝 `illegal_proposal_rate=3.79%`，FAIL
- [x] 模型事件占比 ≥5%：实测 8.12%，PASS
- [x] `llm → goal_progress` 归因链 ≥10：实测 83，PASS
- [ ] replay 整 run MATCH：决策槽无回放 transport，未验证
- [ ] 成本 ≤$2.0 且在 $1.75 停下：实测 $3.138，FAIL
- [x] 盲评包（隐藏标签）已落盘：`reports/a2-dialogue-eval/blind_pairs.jsonl`（30 对）
- [x] 未触碰 README/QUICKSTART/DELIVERY-TEST/known-limitations，未改 tag
