# 管理控制台后端任务清单

批准后按序执行；每笔提交只用显式 pathspec，禁止 `git add -A`（工作区有其他会话的未提交改动）。

## 提交 ①：存储层（settings + secrets + audit）

- [x] `src/wsim/admin/__init__.py`：版本号、导出。
- [x] `src/wsim/admin/settings.py`
  - `resolve_db_path(root=None, run=None, explicit=None) -> Path`
  - `init_schema(path) -> None`（`PRAGMA user_version=1`，幂等）
  - `DEFAULT_SETTINGS`、`HOT_KEYS`
  - `SettingsStore`：`get_all()` / `get_section()` / `update(patch, who)` / `version()` / `providers()` / `routing()` / `global_enabled()`
  - 版本递增与 `BEGIN IMMEDIATE` 事务
- [x] `src/wsim/admin/audit.py`：`append(conn, who, action, key, before, after, detail)`、`list_records(limit)`、`MASK_KEYS`
- [x] `src/wsim/admin/secrets.py`
  - `master_key()`（base64 → 32B，缺失/错长抛 `MasterKeyMissing`）
  - `encrypt(plain) -> (ciphertext, nonce)` / `decrypt(ct, nonce)`
  - `put_secret(id, plain, who)` / `get_secret(id)` / `delete_secret(id)` / `has_secret(id)`
  - `mask_secret(value)`
- [x] `tests/test_admin_store.py`（含 grep 日志/审计 0 明文、未配主密钥 503 语义）
- [x] 提交 ① 并 push

## 提交 ②：providers + 路由 + provider.py 接线

- [x] `src/wsim/admin/providers.py`
  - `CAPABILITIES`、`SLOT_CAPABILITY`、`capabilities_status()/evidence()`
  - `SnapshotCache`（`get(max_age=5.0)`、`invalidate()`；查库失败保留旧快照）
  - `resolve(capability) -> ResolvedProvider | None`：DB→环境变量→`NotConfigured`
  - `ResolvedProvider.to_provider_config(slot, fallback)`（不改 `ProviderConfig` 必填字段）
  - `test_provider(id)`：`max_tokens=1` 最小请求，返回 `ok/latency_ms/model/error`
- [x] `src/wsim/agent/provider.py`
  - 新增 `_admin_config_for_slot(slot)` 与 `ProviderClient._current_config()`
  - `_run`/`complete` 起始处解析；`llm_killswitch`、`llm_not_configured` 错误码
  - 不改构造签名与既有行为（空 DB 时等价于现状）
- [x] `tests/test_admin_providers.py`（优先级、节流、killswitch、缺 key 回退、429 fallback）
- [x] 提交 ② 并 push

## 提交 ③：管理 HTTP API

- [x] `src/wsim/admin/server.py`
  - 路由表 §3；`ThreadingHTTPServer` + 请求体上限/超时
  - 登录限流（`admin_login_failures`）、会话 sha256、24h、IP 白名单
  - `overview`（读 `events.sqlite` 只读 + `ambient_status.json` + `backups/` + `service.sqlite` 大小）
  - providers CRUD / test / audit / killswitch / settings PUT（深合并 + `requires_restart`）
  - 所有写接口鉴权 + 审计
- [x] `src/wsim/admin/__main__.py`：`python -m wsim.admin` 启动、信号关闭、JSON 启动行
- [x] `tests/test_admin_server.py`（本地 mock 中转站线程；成功/坏 base_url/429 fallback/换 key/审计/killswitch/重启持久化）
- [x] 提交 ③ 并 push

## 提交 ④（追加）：参数生效契约 + key 元数据 + 能力位四态

> 实际实现把原计划的 ④⑤ 合并为一笔 `add4075`：server.py 的 `world_state` 与
> `capabilities` 同源依赖 providers 的四态聚合，拆开会让单笔提交自测不绿。

- [x] `src/wsim/admin/settings.py`
  - schema v2：`admin_secrets` 增 `created_at/last_used_at`；新增 `admin_runtime` 表；v1→v2 幂等迁移
  - `RuntimeStore`（`get/set` key→json，不参与 version）
  - `default_world_state()` + `WORLD_KEYS` / `OBSERVED_WORLD_KEYS`
  - `SettingsStore.bump_version()`（key 轮换时让主服务快照失效）
- [x] `src/wsim/admin/secrets.py`
  - 掩码改 `前4****后4`；`meta(secret_id)` 返回 `{has, masked, length, created_at, updated_at, last_used_at}`
  - `put` 保留 created_at、更新 updated_at；`touch(id, at)` 更新 last_used_at
- [x] `src/wsim/admin/server.py`
  - `GET/PUT /api/admin/settings` 返回 `world_state`（`value/effective_value/applied/restart_required/source/observed_at/note`）
  - `reconcile_world_state()`：读 `ambient_status.json` 的 `pid/speed/cpu.limit`
  - `admin_env.sh` 原子生成；`env_bridge` 字段
  - `providers` 列表附 key 元数据；`routing_view` 与 `capabilities` 同源
- [x] `src/wsim/admin/providers.py`
  - `record_result()`：按槽位映射 capability，写 `admin_runtime`（状态变化立即写，否则 ≥5s 节流；失败静默）
  - `capability_report()`：stub / unconfigured / unverified / active / degraded + `last_check_at/latency_ms/error/provider/model/fallback/last_served_by`
  - `capabilities_status()` 与 `capability_report()` 同源；删除静态 `CAPABILITY_STATUS`
- [x] `src/wsim/agent/provider.py`
  - `_run` 成功/最终失败出口回写 `record_result`；不改签名、不写事件
- [x] `tests/test_admin_store.py` / `test_admin_providers.py` / `test_admin_server.py` 覆盖新契约
- [x] `src/wsim/admin/clock.py::now_utc()`：admin 墙钟唯一入口（lint 转绿）
- [x] 提交 ④ 并 push（`add4075`）

## 提交 ⑤：文档 + 验收

- [x] `docs/ADMIN-CONSOLE.md`：启动方式、环境变量、API 示例、生效契约（`applied/effective_value`）、能力四态、诚实声明、安全注意
- [x] `CHANGELOG.md` 追加条目
- [x] 两进程真实验收 1–8；缺失项如实标注
- [x] `pytest` 全量（642 collected，退出码 0，17 skipped）
- [x] 提交 ⑤ 并 push（`97b0ad6`，`d8c644c..97b0ad6`）
