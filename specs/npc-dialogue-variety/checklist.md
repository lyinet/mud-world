# Checklist · NPC 对话去同质化

- [x] 修复前证据落盘：`reports/npc-dialogue-variety-before.json`（5 NPC × 3 轮完整请求/响应）
- [x] `capabilities.dialogue=unconfigured`、`llm_calls=0`、无 `dialogue.jsonl` 已记录
- [x] `TalkAction` 传入 `turn/last_message/last_reply`；`turn` 由 memory 推导且不新增 `talk_ledger` 字段
- [x] `compose_talk` 的人设台词参与全部意图并按轮次轮换，同文有变体兜底
- [x] `DIALOGUE_SYSTEM_PROMPT` 要求延续上一轮且体现人格差异
- [x] `dialogue` 能力位双路可工作：管理台 provider 注册表优先、env 回退（单测覆盖）
- [x] 红线：状态/事件只写模板；模型文本仅进 `dialogue.jsonl` 与本次 HTTP `details.reply`
- [x] `tests/test_dialogue_variety.py` 与既有对话测试全过
- [x] golden V6 哈希按对话文本漂移显式更新，原因写入测试常量，旧 V3/V4/V5 未改
- [x] 修复后指标：unique 15/15、同 NPC 相邻相同 0、跨 NPC 相同 0、LCS ≤2
- [x] `docs/DIALOGUE-QUALITY.md` 记录台词多样性回归指标与阈值
- [x] `CHANGELOG.md` 条目更新，未触碰 `ui/**`、`src/wsim/api/session.py`
