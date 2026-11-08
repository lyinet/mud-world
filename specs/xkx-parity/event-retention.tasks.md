# 事件表增长治理 — 任务拆分

关联规格：`specs/xkx-parity/event-retention.spec.md`
分支：`feat/xkx-event-retention`（基点 `origin/xkx-parity@70ab046`）

## 提交纪律

- 显式 `git add <path>`，禁止 `git add -A`；不触碰工作区里其他人的未提交改动。
- 每段完成即 `commit + push`，commit message 用中文写清“做了什么/为什么”。
- 不使用 force push；不向 `master` 提交。
- 每段合入前必须跑对应验证；全量 pytest 在最后一段跑。

## T0 规格（本次先交付）

- [x] 写 `event-retention.spec.md`（问题、边界、格式、恢复、VACUUM、验收）
- [x] 写本任务清单
- [x] 里程碑：spec 完成，先回报一次

## T1 存储层最小增量

- [x] `EventStore.count()` 改 `MAX(seq)`（空表 0），保证裁剪后 seq 单调
- [x] 新增 `max_seq()` / `min_seq()` / `iter_range(start, end)`（流式，不全量入内存）
- [x] 新增 `delete_through(seq)`（单事务、返回删除行数）与 `checkpoint_truncate()`
- [x] 不新增列/不改索引；关闭态逻辑内容不变
- [x] 验证：`test_xkx_determinism_legacy` 跑两次；既有 `test_atomicity` / `test_w1_persistence`

## T2 归档写盘

- [x] `retention.py`：`RetentionConfig` / env / CLI 参数解析
- [x] 段文件 gzip JSONL（mtime=0、压缩级别 6、每行 canonical dumps、seq 升序）
- [x] 段粒度为 seq 连续前缀，日界优先、单段 ≤ 20000 条
- [x] `manifest.json` 原子写、sha256、字节数、day 范围、幂等重跑
- [x] `--dry-run`：只算 cutoff / S / 段划分，不写盘
- [x] 验证：重复归档同一前缀不产生重复段；sha256 对得上

## T3 裁剪 + 快照 + 恢复

- [x] `cutoff_tick` 与 `S` 计算（只裁完整日、只裁前缀、空表/全表安全阀）
- [x] 回放到 S 生成快照（`WorldState` + `player_ticks` + 校验字段），落盘后读回复核
- [x] 归档完整且快照复核通过后，才 `DELETE seq <= S` + `checkpoint(TRUNCATE)`
- [x] 写 `meta.event_pruned_through_seq` / `event_retention_version` / `event_archive_last_day`
- [x] `retention_journal.json` 阶段机 + `recover()`，三阶段失败注入可重跑
- [x] `--vacuum`（独占、服务停止）、`--verify`（快照+尾段重建比对）
- [x] 验证：中断恢复、裁剪边界、seq 继续单调

## T4 重建路径接入

- [x] `api/persistence.replay_into` 支持：快照优先 → 归档+尾段 → 未裁剪全量回放
- [x] 快照校验失败时自动退回归档重建；归档也缺失时明确报错，不静默半重建
- [x] `WorldSession` 传入 `run_dir` / `content_hash`（仅此最小改动）
- [x] 验证：裁剪后重启 `WorldSession` 的 state hash 与裁剪前一致

## T5 测试与实测

- [x] `tests/test_event_retention.py`：关闭态字节一致、归档重建一致、中断恢复、裁剪边界
- [x] 关闭态两次跑 `test_xkx_determinism_legacy`，记录哈希字面量
- [x] 全量 `pytest` 全绿；如有基线变化单独上报，不静默接受
- [x] 合成数据造量（≈10 万条或 15 分钟级别，不做长时间压测）：
      记录 db 体积、归档体积、裁剪耗时、`--verify` 结果
- [x] 最终收尾报告：设计取舍、实测数字、未完成项

## 明确不做（本次）

- 在线自动触发（cron/低峰窗口执行 CLI；不挂进 tick 循环）
- 改 `events` 表结构、改 `src/wsim/sim/**`、改 Effect/Intent 契约
- 历史全量审计接口改造（裁后只保留尾段，全史读归档）
