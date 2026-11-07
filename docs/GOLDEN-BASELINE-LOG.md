# GOLDEN 基线台账

本台账登记 `tests/test_p3e_golden.py` 中每一组基线值的每一次变更。
审计门禁会逐条核对：**当前基线值必须能在本表查到对应记录；历史变更必须全部登记。**
缺记录即红，不设豁免；本表不改测试代码。

列说明：

- `提交`：变更发生的提交（短 sha）。
- `基线` / `字段`：`tests/test_p3e_golden.py` 中的常量名与字段名。
- `旧值` / `新值`：变更前后的值；首次登记写 `新增`。
- `理由来源`：`提交正文` 表示当时记录；`事后追溯` 表示提交正文未说明、由负责人事后补充。
- `复现证据`：当前实现可复现的写命令与 run；历史值已被后续提交取代的写“未复现”。

复现命令（当前 V6 两组基线）：

```bash
PYTHONPATH=src python tools/xkx_audit/__main__.py
```

- `GOLDEN_MILESTONE_V6`：run `same-a`（2 日、seed=42、fake LLM、dialogue 关）。
- `DIALOGUE_GOLDEN_MILESTONE_V6`：run `dialogue-on`（同上，dialogue 开）。

| 提交 | 基线 | 字段 | 旧值 | 新值 | 理由来源 | 理由 | 复现证据 |
|---|---|---|---|---|---|---|---|
| 60e8596560 | GOLDEN | state_final | 新增 | 1bd870c4c8270da6117721f67a67a38bbc2d34dae12386a0d16f0ce3abedd226 | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 60e8596560 | GOLDEN | event_final | 新增 | 4d4019291eaaeb6b58c5279dce7840efcaaa4622272223782bbb84ab778f35cf | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 60e8596560 | GOLDEN | events_total | 新增 | 824 | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 60e8596560 | GOLDEN | sell_in | 新增 | 849 | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 60e8596560 | GOLDEN | buy_out | 新增 | 795 | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 60e8596560 | GOLDEN | burned | 新增 | 113 | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 60e8596560 | GOLDEN | business_cash | 新增 | 12058 | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 60e8596560 | GOLDEN | precondition_blocked | 新增 | 2 | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 60e8596560 | GOLDEN | precondition_pruned | 新增 | 5 | 提交正文 | 提交正文：“新增 tests/test_p3e_golden.py：锁死 2 天 fake run 的事件序/state/流量；实测把调度改回全量会 FAIL”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| cc3a4752ae | DIALOGUE_GOLDEN | state_final | 新增 | a52d5a482f5708298fb3be5c863df8a777428716e67cd811644b674d16565b96 | 提交正文 | 提交正文：“A2 开启后 talk 台词进入 memory_add，事件数仍 824，但 event/state hash 显式漂移。新增 DIALOGUE_GOLDEN 锁定 fake LLM seed=7 的确定性结果”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| cc3a4752ae | DIALOGUE_GOLDEN | event_final | 新增 | 86946e7c66012dcd2c51ed65f1004752eae165742d050431e2d5fb3b80e1a6a0 | 提交正文 | 提交正文：“A2 开启后 talk 台词进入 memory_add，事件数仍 824，但 event/state hash 显式漂移。新增 DIALOGUE_GOLDEN 锁定 fake LLM seed=7 的确定性结果”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| cc3a4752ae | DIALOGUE_GOLDEN | events_total | 新增 | 824 | 提交正文 | 提交正文：“A2 开启后 talk 台词进入 memory_add，事件数仍 824，但 event/state hash 显式漂移。新增 DIALOGUE_GOLDEN 锁定 fake LLM seed=7 的确定性结果”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| cc3a4752ae | DIALOGUE_GOLDEN | dialogue_calls | 新增 | 65 | 提交正文 | 提交正文：“A2 开启后 talk 台词进入 memory_add，事件数仍 824，但 event/state hash 显式漂移。新增 DIALOGUE_GOLDEN 锁定 fake LLM seed=7 的确定性结果”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 98f0cdfee9 | GOLDEN | state_final | 1bd870c4c8270da6117721f67a67a38bbc2d34dae12386a0d16f0ce3abedd226 | 38bdacdaa2764374ac0b59a27eab5e84788b58ba84d7e210b6915384d5f220e5 | 提交正文 | 提交正文：“模板不再倾倒第三人称人格档案……用一句第一人称回应……同步 fake-run golden 的状态/事件哈希”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 98f0cdfee9 | GOLDEN | event_final | 4d4019291eaaeb6b58c5279dce7840efcaaa4622272223782bbb84ab778f35cf | 9f2547cc85a4f89476ac947085d1a5ce9f6bd3e35b80561baa9569a99f3b7766 | 提交正文 | 提交正文：“模板不再倾倒第三人称人格档案……用一句第一人称回应……同步 fake-run golden 的状态/事件哈希”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e2151e0d03 | GOLDEN | state_final | 38bdacdaa2764374ac0b59a27eab5e84788b58ba84d7e210b6915384d5f220e5 | 9d73cb2e87c52cfe9e8880ba704f957825288e95aaccdefc02725f98f08ed894 | 提交正文 | 提交正文：“模板文本进入 talk 记忆，state/event 哈希随句式分化变化；事件数 824 与经济流量不变，仅显式更新哈希并注明原因”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e2151e0d03 | GOLDEN | event_final | 9f2547cc85a4f89476ac947085d1a5ce9f6bd3e35b80561baa9569a99f3b7766 | 43e16d6b765561ed18889d533eb6d46e70192441d1ecb74e5208eb80d5c75263 | 提交正文 | 提交正文：“模板文本进入 talk 记忆，state/event 哈希随句式分化变化；事件数 824 与经济流量不变，仅显式更新哈希并注明原因”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 335a4ee015 | GOLDEN | precondition_pruned | 5 | 2 | 提交正文 | 提交正文：“同步更新 golden v3（仅 pruned 5→2，事件/状态哈希不变）”；原因是提交前二次位置校验把 trade 的 not_together 判为过期而非非法。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| d780dcff3c | DIALOGUE_GOLDEN | state_final | a52d5a482f5708298fb3be5c863df8a777428716e67cd811644b674d16565b96 | b980558ee70f752127185a93fc52eece0593f0daa23598ba6a2bcf625253987c | 事后追溯 | **事后追溯，非当时记录**（提交正文为空）：该提交接入 text_guard 与统一 system prompt，改变了 NPC 对话模板句文本，进 NPC memory 后导致状态/事件哈希漂移；当前基线值与实现一致，已由审计新鲜 run 逐字段复现。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| d780dcff3c | DIALOGUE_GOLDEN | event_final | 86946e7c66012dcd2c51ed65f1004752eae165742d050431e2d5fb3b80e1a6a0 | c04895352f525d7ecaf6aa4175f987aacea7e0d600fd3a7fd03d31513f7dbb8c | 事后追溯 | **事后追溯，非当时记录**（提交正文为空）：该提交接入 text_guard 与统一 system prompt，改变了 NPC 对话模板句文本，进 NPC memory 后导致状态/事件哈希漂移；当前基线值与实现一致，已由审计新鲜 run 逐字段复现。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | DIALOGUE_GOLDEN_P3I | state_final | 新增 | 9554ac3f2522da3dd31ed665266bfe2807dab2ab1283967a3bd7421ce4ee6f3d | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | DIALOGUE_GOLDEN_P3I | event_final | 新增 | cdc7a07c937751ff2df04810630f7b7d7472f93fd44626df57cde39d91d84406 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | DIALOGUE_GOLDEN_P3I | events_total | 新增 | 791 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | DIALOGUE_GOLDEN_P3I | dialogue_calls | 新增 | 63 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | state_final | 新增 | e5a986f79ffcc5903620caca8b914279e3e43488fc3b68070b385e07abe6c247 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | event_final | 新增 | 3e7afc830af4c85532e22cf2a80546e6181e0216bffba1a3e1d27e9d9dccd3e0 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | events_total | 新增 | 791 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | sell_in | 新增 | 1133 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | buy_out | 新增 | 852 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | burned | 新增 | 110 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | business_cash | 新增 | 11307 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | precondition_blocked | 新增 | 9 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 20dc739155 | GOLDEN_P3I | precondition_pruned | 新增 | 2 | 提交正文 | 提交正文：“golden 基线显式迁移到 791-event 世界（旧 824-event 基线保留并 skip 注明原因）”；原因是 P3I 扩充 10 件物品、27 个任务模板、关系度与物价模型。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | DIALOGUE_GOLDEN_P3I_PRICE | state_final | 新增 | 177904390adb8571ab6bb851872346cc17ab624bea6e9d80abd6d3e75bc006bf | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | DIALOGUE_GOLDEN_P3I_PRICE | event_final | 新增 | a82c516a44932e918fab98045014833bd578f955aa8e45d313e68f35f3e9640c | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | DIALOGUE_GOLDEN_P3I_PRICE | events_total | 新增 | 791 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | DIALOGUE_GOLDEN_P3I_PRICE | dialogue_calls | 新增 | 63 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | state_final | 新增 | 86871fafdf16f72d8b9ad045d00b07ceb17358367f500f51080f94c0deb86974 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | event_final | 新增 | ee7d0a90b5bdb6598f66d974f763e12764e506e5336f17b5f5cb43ad46df3e69 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | events_total | 新增 | 791 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | sell_in | 新增 | 1145 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | buy_out | 新增 | 842 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | burned | 新增 | 110 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | business_cash | 新增 | 11285 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | precondition_blocked | 新增 | 9 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 64365f859f | GOLDEN_P3I_PRICE | precondition_pruned | 新增 | 2 | 提交正文 | 提交正文：“同期新增 P3I 价格 golden 基线（791 events，旧基线保留并 skip）”；原因是供需物价模型接入运行时。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | DIALOGUE_GOLDEN_CALIB | state_final | 新增 | 139a879c30680314ddb93a026fc107ef9cb7609dbe264a47dadd205ed0bbf8c4 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | DIALOGUE_GOLDEN_CALIB | event_final | 新增 | bcd166d00d546c5f8c3aee69e661ca926680f3494cb3ee275f9194024717ae98 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | DIALOGUE_GOLDEN_CALIB | events_total | 新增 | 796 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | DIALOGUE_GOLDEN_CALIB | dialogue_calls | 新增 | 63 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | state_final | 新增 | 55ac3ed8bde6182714e0b9576880c04eaed4296e2a04a079fb60a92c2e537db8 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | event_final | 新增 | 344b501a5969cef1e518e36dd2b448c0f08145f8468fc621269c4c260f6fedb2 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | events_total | 新增 | 796 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | sell_in | 新增 | 971 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | buy_out | 新增 | 820 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | burned | 新增 | 127 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | business_cash | 新增 | 12184 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | precondition_blocked | 新增 | 11 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 5635c26c70 | GOLDEN_CALIB | precondition_pruned | 新增 | 2 | 提交正文 | 提交正文：“按单目标有效 10 天产能 P60 的 0.6-0.8 重设目标，并同步 10 日产能护栏与显式迁移的 2 天 golden 基线”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | DIALOGUE_GOLDEN_MILESTONE | state_final | 新增 | ec1b2d5a1564b2d30a751d7e91bbd55a9b1df36be47c9183709bd5aa291de88f | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | DIALOGUE_GOLDEN_MILESTONE | event_final | 新增 | 741f98f3ce37f1457ee6d95b519081167eb01c253fa09148a4cdd77b92ccec95 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | DIALOGUE_GOLDEN_MILESTONE | events_total | 新增 | 800 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | DIALOGUE_GOLDEN_MILESTONE | dialogue_calls | 新增 | 63 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | state_final | 新增 | a155665368313c1703ebcb5b1e4cdf5110a5890928918b23a4a6e725030428a0 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | event_final | 新增 | 61b1531a03bb79b7c6548b87fb8f5e161d9ffc511dd13e8276419d936e7ac664 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | events_total | 新增 | 800 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | sell_in | 新增 | 971 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | buy_out | 新增 | 820 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | burned | 新增 | 127 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | business_cash | 新增 | 12184 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | precondition_blocked | 新增 | 11 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| bc7013ce0e | GOLDEN_MILESTONE | precondition_pruned | 新增 | 2 | 提交正文 | 提交正文：“新 golden 基线 GOLDEN_MILESTONE（旧基线保留并显式 skip）”；原因是玩家成长线与 NPC 里程碑链/维护型目标落地。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | DIALOGUE_GOLDEN_MILESTONE_V2 | state_final | 新增 | ec1b2d5a1564b2d30a751d7e91bbd55a9b1df36be47c9183709bd5aa291de88f | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | DIALOGUE_GOLDEN_MILESTONE_V2 | event_final | 新增 | fa22d38584f600eec958cf2e65527e56fa51f8b47056112ec1597f710634a8d3 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | DIALOGUE_GOLDEN_MILESTONE_V2 | events_total | 新增 | 800 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | DIALOGUE_GOLDEN_MILESTONE_V2 | dialogue_calls | 新增 | 63 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | state_final | 新增 | a155665368313c1703ebcb5b1e4cdf5110a5890928918b23a4a6e725030428a0 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | event_final | 新增 | 72dd62c2923ebec867398bfba2f4cb304af8c59d66570bd3539b0991e93a24ce | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | events_total | 新增 | 800 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | sell_in | 新增 | 971 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | buy_out | 新增 | 820 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | burned | 新增 | 127 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | business_cash | 新增 | 12184 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | precondition_blocked | 新增 | 11 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 6189ad94fd | GOLDEN_MILESTONE_V2 | precondition_pruned | 新增 | 2 | 提交正文 | 提交正文：“goal_create 的 effect payload 增加了 secondary: bool，只影响 events.jsonl 序列化……仅 event hash 漂移；保留旧基线，新增并锁定 V2”。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | DIALOGUE_GOLDEN_MILESTONE_V3 | state_final | 新增 | 22d415077f26873dcb1fd26c1b94ac4efd3ba8b8035ce43ce8c0fefddcee01b9 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | DIALOGUE_GOLDEN_MILESTONE_V3 | event_final | 新增 | b7ded604509b24579eab66771c88f4bdfad9d049fc827994dbb6b07f6472049e | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | DIALOGUE_GOLDEN_MILESTONE_V3 | events_total | 新增 | 693 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | DIALOGUE_GOLDEN_MILESTONE_V3 | dialogue_calls | 新增 | 65 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | state_final | 新增 | a8aecf44c5dc845782eefda10789ac3a937424438720d56ef72cfeb29035efe4 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | event_final | 新增 | ba66bb4db27f974b2f81833399bc40ed312e1a4e752da38c83efa3c087a8833d | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | events_total | 新增 | 693 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | sell_in | 新增 | 967 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | buy_out | 新增 | 793 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | burned | 新增 | 81 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | business_cash | 新增 | 12599 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | precondition_blocked | 新增 | 8 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| e1d6d2bbcc | GOLDEN_MILESTONE_V3 | precondition_pruned | 新增 | 4 | 提交正文 | 提交正文：“800-event golden 保留原值为 skip，新增 v3 基线并显式记录漂移”；原因是委托公平轮转 + 接单提前改变事件序列。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | DIALOGUE_GOLDEN_MILESTONE_V4 | state_final | 新增 | 22d415077f26873dcb1fd26c1b94ac4efd3ba8b8035ce43ce8c0fefddcee01b9 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | DIALOGUE_GOLDEN_MILESTONE_V4 | event_final | 新增 | b7ded604509b24579eab66771c88f4bdfad9d049fc827994dbb6b07f6472049e | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | DIALOGUE_GOLDEN_MILESTONE_V4 | events_total | 新增 | 693 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | DIALOGUE_GOLDEN_MILESTONE_V4 | dialogue_calls | 新增 | 65 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | state_final | 新增 | a8aecf44c5dc845782eefda10789ac3a937424438720d56ef72cfeb29035efe4 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | event_final | 新增 | ba66bb4db27f974b2f81833399bc40ed312e1a4e752da38c83efa3c087a8833d | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | events_total | 新增 | 693 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | sell_in | 新增 | 967 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | buy_out | 新增 | 793 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | burned | 新增 | 81 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | business_cash | 新增 | 12599 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | precondition_blocked | 新增 | 8 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 240e5a8dc3 | GOLDEN_MILESTONE_V4 | precondition_pruned | 新增 | 4 | 提交正文 | 提交正文：“旧 V3 基线 skip，新建 V4 同值基线（693 events 零漂移）”；原因是玩家 buy/sell/consume/escort 打通与只读视图扩展。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | DIALOGUE_GOLDEN_MILESTONE_V5 | state_final | 新增 | 67b86167cc675e0ab4ead722c4b4b8077b42eb7a0683f63dd6f5943facf1b0fe | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | DIALOGUE_GOLDEN_MILESTONE_V5 | event_final | 新增 | 394e38a7247ac0bfc358167631243453eba92de26a166028d6a546087a1a04e1 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | DIALOGUE_GOLDEN_MILESTONE_V5 | events_total | 新增 | 692 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | DIALOGUE_GOLDEN_MILESTONE_V5 | dialogue_calls | 新增 | 65 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | state_final | 新增 | 0ee1f724df0e2cb8960f64408a3442185f96aa9c1857fe71091179567900cf2c | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | event_final | 新增 | 6a919efe6e0ac23d19eb0e75097be917df3dc85f41aca06b36b1923f5b65d2f4 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | events_total | 新增 | 692 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | sell_in | 新增 | 967 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | buy_out | 新增 | 785 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | burned | 新增 | 81 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | business_cash | 新增 | 12816 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | precondition_blocked | 新增 | 8 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 1468511991 | GOLDEN_MILESTONE_V5 | precondition_pruned | 新增 | 4 | 提交正文 | 提交正文：“基线 V4（W1 已入库）原值保留并 skip，新建 V5”；原因是开店经营闭环与独立日结事件。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 0fa9e02926 | DIALOGUE_GOLDEN_MILESTONE_V6 | state_final | 新增 | e4a6b5f7ee645331ac281a69891e0ff7f28bb220eb2f8668489b7e774b99a626 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 0fa9e02926 | DIALOGUE_GOLDEN_MILESTONE_V6 | event_final | 新增 | e98e395ad6647a264f9ec615c502f73a11a84d2a9cdbe8d7d2af80c5abf64367 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 0fa9e02926 | DIALOGUE_GOLDEN_MILESTONE_V6 | events_total | 新增 | 791 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `dialogue-on` 逐字段一致 |
| 0fa9e02926 | DIALOGUE_GOLDEN_MILESTONE_V6 | dialogue_calls | 新增 | 65 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | state_final | 新增 | 03b784697bf9de1c2db60d0925f11ebde4d3d956e2e7a188842e4c760915b8c7 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | event_final | 新增 | 3be92716396b88d086571e40968581806549240ecaf6a13ca2debd7c317c16e4 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 未复现：该历史值已被后续提交取代（当前实现不可回放旧哈希） |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | events_total | 新增 | 791 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | sell_in | 新增 | 584 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | buy_out | 新增 | 657 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | burned | 新增 | 72 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | business_cash | 新增 | 13058 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | precondition_blocked | 新增 | 5 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| 0fa9e02926 | GOLDEN_MILESTONE_V6 | precondition_pruned | 新增 | 2 | 提交正文 | 提交正文：“重建 V6 golden 基线（791 events），旧 V5 保留并 skip”；原因是 NPC 多跳寻路改为确定性最短路逐跳。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| aa885b1715 | DIALOGUE_GOLDEN_MILESTONE_V6 | state_final | e4a6b5f7ee645331ac281a69891e0ff7f28bb220eb2f8668489b7e774b99a626 | 6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55 | 提交正文 | 提交正文：“golden V6 哈希按对话文本漂移显式更新，原因写入 DIALOGUE_VARIETY_BASELINE_REASON”；原因是 NPC 对话同质化修复（轮次/人格入模板）。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `dialogue-on` 逐字段一致 |
| aa885b1715 | DIALOGUE_GOLDEN_MILESTONE_V6 | event_final | e98e395ad6647a264f9ec615c502f73a11a84d2a9cdbe8d7d2af80c5abf64367 | 0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49 | 提交正文 | 提交正文：“golden V6 哈希按对话文本漂移显式更新，原因写入 DIALOGUE_VARIETY_BASELINE_REASON”；原因是 NPC 对话同质化修复（轮次/人格入模板）。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `dialogue-on` 逐字段一致 |
| aa885b1715 | GOLDEN_MILESTONE_V6 | state_final | 03b784697bf9de1c2db60d0925f11ebde4d3d956e2e7a188842e4c760915b8c7 | 6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55 | 提交正文 | 提交正文：“golden V6 哈希按对话文本漂移显式更新，原因写入 DIALOGUE_VARIETY_BASELINE_REASON”；原因是 NPC 对话同质化修复（轮次/人格入模板）。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| aa885b1715 | GOLDEN_MILESTONE_V6 | event_final | 3be92716396b88d086571e40968581806549240ecaf6a13ca2debd7c317c16e4 | 0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49 | 提交正文 | 提交正文：“golden V6 哈希按对话文本漂移显式更新，原因写入 DIALOGUE_VARIETY_BASELINE_REASON”；原因是 NPC 对话同质化修复（轮次/人格入模板）。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `same-a` 逐字段一致 |
| 1e076da0e5 | DIALOGUE_GOLDEN_MILESTONE_V6 | dialogue_calls | 65 | 0 | 提交正文 | 提交正文：“golden V6 离线 dialogue_calls 65->0”；原因是模型台词统一到 dialogue_aside 单一路径，headless 不再挂 AsyncDialogueQueue。 | 已复现：`PYTHONPATH=src python tools/xkx_audit/__main__.py`，run `dialogue-on` 逐字段一致 |

## 原地改写清单（审计首轮查出的 7 处）

| 提交 | 基线 | 字段 | 判定 |
|---|---|---|---|
| `98f0cdfee9` | `GOLDEN` | `state_final` / `event_final` | 有提交正文理由，已登记 |
| `e2151e0d03` | `GOLDEN` | `state_final` / `event_final` | 有提交正文理由，已登记 |
| `335a4ee015` | `GOLDEN` | `precondition_pruned` | 有提交正文理由，已登记 |
| `d780dcff3c` | `DIALOGUE_GOLDEN` | `state_final` / `event_final` | **事后追溯，非当时记录；已登记** |
| `aa885b1715` | `DIALOGUE_GOLDEN_MILESTONE_V6` | `state_final` / `event_final` | 有提交正文理由，已登记 |
| `aa885b1715` | `GOLDEN_MILESTONE_V6` | `state_final` / `event_final` | 有提交正文理由，已登记 |
| `1e076da0e5` | `DIALOGUE_GOLDEN_MILESTONE_V6` | `dialogue_calls` | 有提交正文理由，已登记 |

## 当前基线复现结论

- `GOLDEN_MILESTONE_V6`：审计新鲜 run `same-a` 逐字段一致（791 events）。
- `DIALOGUE_GOLDEN_MILESTONE_V6`：审计新鲜 run `dialogue-on` 逐字段一致（791 events，dialogue_calls=0）。
- 当前基线值与实现一致；历史台账缺口已由本表补齐，不改变任何测试常量。

## xkx 世界基线（不是 `tests/test_p3e_golden.py` 常量）

xkx 内容包的 `content_hash` 与实体数不写进旧世界 golden 常量（旧九地点与 xkx 是两套
内容包），但同样需要可追溯：任何一次漂移都要在本表登记。旧九地点三项（735 events /
`009076f0346352e3…` / `02d5f812c4278333…`，business_cash 11442、blocked 6、pruned 3）
在下列变更后均**未变**，因此不产生旧世界基线记录。

| 提交 | 基线 | 字段 | 旧值 | 新值 | 理由来源 | 理由 | 复现证据 |
|---|---|---|---|---|---|---|---|
| d13eb30 | XKX_CONTENT | content_hash | c5df62d794eb53484e3c85dbe37b0147bdcad94e1af64d7299aaf4e31bc004de | 2b52983fb8f04210947d39f46bd89156c6e2805f3653faae5c9fc899f26d35e5 | 提交正文 | 提交正文：“原版房间 reset() 按 (房间, 模板) -> count 逐个 new()；P1 派生了 44 个额外槽位，本次真正落到世界里”；`npc_instances` 非空后进入 `Content.content_hash` payload，因此 xkx 世界 content_hash 必须变，旧 run 启动即 `content_hash_mismatch`，需 `--reset`。 | 已复现：`PYTHONPATH=src python -c "from wsim.api.xkx_content import load_xkx_content; c=load_xkx_content('content/xkx'); print(c.content_hash)"`；把 `npc_instances` 临时置空可复现旧值 |
| d13eb30 | XKX_WORLD | npc_entities | 105 | 149 | 提交正文 | 同上：105 个规范 NPC + 44 个同模板实例；world feed 白名单与 `_present_view` 的 `npc_meta` 回查同步按模板维度。 | 已复现：`tests/test_xkx_clone_instances.py::test_genesis_creates_all_instances_with_template_stats` |
| d13eb30 | XKX_WORLD | clone_instances | 新增 | 44 | 提交正文 | 同上：19 个模板、44 个额外槽位，全部落在原版 `objects` 声明 count 的解内，顺序按 (模板, 房间, 槽位) 固定。 | 已复现：`tests/test_xkx_clone_instances.py::test_instances_are_room_scoped_and_unique` |
| 6b798ac | XKX_CONTENT | content_hash | 2b52983fb8f04210947d39f46bd89156c6e2805f3653faae5c9fc899f26d35e5 | 9fc1d247a5840ebc8555f2e5cada284a79b5366a1782f91ed34e4af83c9d62a5 | 提交正文 | P4a 给 `npcs.json` 的 dushe/wolfdog 加了 `initial_state: unconcious`；`Content.content_hash` 的 `npcs` payload 因内容字段变化而漂移，旧 run 启动即 `content_hash_mismatch`，需 `--reset`。 | 已复现：`PYTHONPATH=src python -c "from wsim.api.xkx_content import load_xkx_content; print(load_xkx_content('content/xkx').content_hash)"` |
| 6b798ac | XKX_WORLD | npc_status_unconcious | 0 | 3 | 提交正文 | P4a：黄龙洞 `毒蛇` 1 只 + 郭庄 `大狼狗` 2 只（规范实例 + 同模板额外实例）以 `status=unconcious` 创世；其余 NPC 仍 `alive`。 | 已复现：`tests/test_xkx_clone_instances.py::test_genesis_marks_reference_unconcious_npcs` |

P4a 后旧九地点三项仍逐字节不变：`735` 事件、`state_final 009076f0346352e3…`、
`event_final 02d5f812c4278333…`、`business_cash 11442`、`blocked 6`、`pruned 3`
（`tests/test_xkx_determinism_legacy.py` 通过；旧内容包没有 `initial_state` 键，
`create_entity` 事件不带 `status`）。
