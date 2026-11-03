# Action 插件

每个插件目录包含：

- `manifest.yaml`：`id`、`verb`、`version`、`entry`、`args` schema、`preconditions`、`effects`
- `action.py`：导出纯函数 `apply(state, args) -> list[Effect]`

## 硬约束

1. `apply` 只读 state，返回 `Effect[]`；适配器会在调用前后比较 state 哈希，变更即整笔拒绝。
2. `effects` 必须同时出现在 manifest 和引擎 `EFFECT_OPS` 中；未知原语整笔回滚。
3. 插件禁止使用 `mint`。增发货币只能由 `sim/genesis.py` 和系统动作控制。
4. 插件不能写事件日志、不能绕过 Writer、不能持有全局可变状态、不能读墙钟/全局随机。

## 内核不可扩展的部分

- 单写者 `Writer`、一 Intent 一事务、失败整笔回滚
- append-only 事件日志与 canonical 哈希
- Effect 原语语义、守恒校验、账户模型
- 虚拟时钟与 `PRNG(seed, seq, salt)`
- MCP 三级权限、审计与双人确认
