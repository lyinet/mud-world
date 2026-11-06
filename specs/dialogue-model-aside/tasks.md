# 对话模型旁路 Tasks

## Phase 1 · 旁路存储与运行时

- [ ] 1.1 新增 `src/wsim/agent/dialogue_aside.py`：`DialogueAside`（JSONL 追加、启动加载索引、`get/put/count/path`、损坏行跳过、线程锁）。
- [ ] 1.2 改造 `src/wsim/agent/dialogue_runtime.py`：`enabled()` 改读 `XKX_DIALOGUE_MODEL`；`build_queue` 构造预算与遥测并 `defer_calls=True`；`attach_if_enabled` 返回队列；`remember` 带模型名；`overlay_reply` 写旁路并覆盖 `details.reply`。
- [ ] 1.3 清理 `src/wsim/actions/registry.py:147-153` 的 `WSIM_DIALOGUE` 自动挂载。

## Phase 2 · 动作与会话接线

- [ ] 2.1 `src/wsim/agent/dialogue_async.py`：构造函数新增 `defer_calls: bool = False`。
- [ ] 2.2 `src/wsim/actions/social.py`：`defer_calls=True` 时只暂存对话上下文（上限 256，配偶覆盖不暂存），不发起模型调用；模板 `memory_add` 不变。
- [ ] 2.3 `src/wsim/api/session.py`：`__init__` 创建 `DialogueAside` 并 `attach_if_enabled`；新增 `finalize_dialogue()`（提交后调用、锁外、异常静默）；`messages()` 按 `(event_seq,npc)` 覆盖文本并返回 `dialogue_source`。
- [ ] 2.4 `src/wsim/api/server.py`：action 路由改调 `session.finalize_dialogue()`；`/api/health` 增加 `llm` 块；`/metrics` 增加对话计数。
- [ ] 2.5 全局搜索确认没有第三处读取 `talk:npc` 文本（`player_state`/`status`/`world_feed`）。

## Phase 3 · 测试

- [ ] 3.1 新增 `tests/test_dialogue_aside.py`：
  - 同 seed 两个 `WorldSession` 跑同一 `talk` 脚本，`state_final`/`event_final` 完全一致；
  - `events` 中 `talk:npc` 为模板文本，旁路文本不出现在事件里；
  - 删除旁路后新会话读回模板。
- [ ] 3.2 跑 `pytest -q tests/test_p3e_golden.py`（当前启用基线）。
- [ ] 3.3 跑 `pytest -q tests/test_talk_action.py tests/test_dialogue_intents.py tests/test_dialogue_routing.py tests/test_dialogue_guard_integration.py`。
- [ ] 3.4 若既有 golden 因“删掉 registry 自动挂载 / defer”漂移，先停下报告，不静默改基线。

## Phase 4 · 现网验收（API 8031，保持服务）

- [ ] 4.1 无玩家 2 日 golden：`XKX_DIALOGUE_MODEL=0` 与 `=1` 各跑一次，贴两组 `state_final`/`event_final`/`sha256sum events.jsonl`。
- [ ] 4.2 有玩家同 seed 两次：贴两次 `state_final`/`event_final`，证明模型文本未进世界。
- [ ] 4.3 真实模型：开启 `XKX_DIALOGUE_MODEL=1` 重启 API，脚本化 `talk`，`head -3 dialogue_aside.jsonl` 贴 3 条原文。
- [ ] 4.4 `GET /api/health` 贴 `llm.calls`/`by_slot`/`decision_calls`；确认 `runs/<run>/dialogue.jsonl` 只有对话记录。
- [ ] 4.5 删 `dialogue_aside.jsonl` 重启，`talk` 后 `GET /api/player/messages` 贴模板文本与 `dialogue_source=template`。
- [ ] 4.6 失败注入（临时错误 base_url 或错误 key）：确认动作仍成功、回复退回模板、无 5xx、tick 正常。

## Phase 5 · 文档与提交

- [ ] 5.1 `docs/OPS-RUNBOOK.md` 写“为什么模型台词不能进账本 / 开启 / 关闭 / 清空旁路 / 失败语义 / 排查”。
- [ ] 5.2 `CHANGELOG.md` 新增条目。
- [ ] 5.3 提交（显式 pathspec，仅本特性文件）：`feat(dialogue): 模型台词走旁路，账本只记模板`。
- [ ] 5.4 给老板一句话结论：能否看出 NPC 说话不同、在哪看。

## 完成判定

- 六项验收全部有真实命令与输出片段；
- 世界哈希在开启/关闭、两次同 seed 下完全一致；
- 旁路可删、可关、失败静默；
- 前端零改动即可看到模型台词差异。
