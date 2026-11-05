# 管理控制台后端（admin console backend）规格

状态：已批准并实现中（提交 ①–③ 已 push；提交 ④–⑥ 按 2026-11-06 决策追加）
范围：只新增/改动下列文件，其余目录（`api/`、`actions/`、`sim/`、`ui/`、`content/`、`deploy/`）一律不碰。

允许改动：

- 新增 `src/wsim/admin/**`：`__init__.py`、`__main__.py`、`settings.py`、`secrets.py`、`providers.py`、`audit.py`、`server.py`
- 改 `src/wsim/agent/provider.py`（只做 provider 解析来源替换 + 快照缓存 + 真实调用结果回写，不改调用方签名）
- 新增/更新 `tests/test_admin_*.py`
- 新增 `docs/ADMIN-CONSOLE.md`
- 追加 `CHANGELOG.md`
- 运行期生成 `<root>/runs/<run>/admin_env.sh`（数据文件，不含任何密钥）

## 0. 决策记录（2026-11-06，按“最保守且可观测”定）

| # | 决策 | 理由 |
|---|---|---|
| D1 | `world.*` 不做假热更：GET 同时返回配置值 `world` 与运行观测 `world_state`，每键 `{value, effective_value, applied, restart_required, source, observed_at, note}` | 玩家 API 只在启动时读 `XKX_AMBIENT*`，`api/**` 禁改。写内存变量回 200 会骗前端，违反“不做假热更”。双值让“想改的”和“真在跑的”并列可见。 |
| D2 | `world_state` 落 `admin_runtime` 表（不进 `admin_settings.version`） | 运行态每秒都可能变；若混进 version 会让主服务快照每 5 秒重载配置，纯属浪费。配置与观测分表，语义也更干净。 |
| D3 | 玩家进程重启的判定：`ambient_status.json` 的 `pid` 变化 | 管理服务无法被玩家进程回调（禁改 `api/**`）；`pid` 是客观、可审计的“新进程已起来”证据。pid 变化后把 `speed`、`cpu.limit*100` 记为 `effective_value`，再与配置值比较得出 `applied`。 |
| D4 | 提供 `<run>/admin_env.sh`（`XKX_AMBIENT=1`、`XKX_AMBIENT_SPEED`、`XKX_AMBIENT_CPU`） | 不改 `api/**` 的前提下，让“重启后生效”可操作：重启前 `source` 该文件即可。文件只含世界参数，不含 key/token。 |
| D5 | 无运行期观测点的参数（`tick_minutes`、`player_cap`、`starting_money`、`quest_floor_enabled`）：`effective_value=null`、`applied=false`、`source="none"` | 主服务当前没有这些键的读取点，编不出“实际生效值”。如实标 null 比编一个数字更可信。 |
| D6 | 能力状态四态由真实调用写入 `admin_runtime`，启动不探活 | “配了 key 就 active”是假指标；探活会烧钱、触发限流。只信真实请求结果（含超时、4xx/5xx）。 |
| D7 | 能力状态写库按能力位节流：状态变化立即写，否则 ≥5 秒最多一次；任何写失败静默 | 主服务每次 LLM 调用不能被管理面拖慢或打断；5 秒粒度足够人看。 |
| D8 | 能力位状态与“谁在承担”同一份聚合：`capabilities`（详细）与 `capabilities_status`（扁平）都由 `capability_report()` 产出 | 避免两套真相。详细里给 `provider`、`model`、`fallback`、`last_served_by`。 |
| D9 | stub 能力不出现 provider 时也保留条目，`status="stub"`、`last_check_at=null`；路由未配 → `unconfigured` | 让控制台能显示“这个能力根本没接模型”，而不是伪装成未验证。 |
| D10 | 掩码统一 `前4****后4`；长度 < 9 全星号；长度/创建/最后使用时间可读，明文永不出 `secrets.py` | 满足“只回掩码 + 元数据”。长度需在进程内解密后计算，掩码在同一函数完成，返回对象里只有掩码。 |
| D11 | `admin_secrets` 增列 `created_at`、`last_used_at`；最后使用时间由真实调用成功回写，节流 60 秒 | 控制台要能看到“这个 key 是否还在用”。写库节流避免每次调用都写。 |
| D12 | 已 push 的 ①–③ 不改写历史；本轮追加 2 笔提交（`add4075` 参数契约+key 元数据+能力四态，随后文档+CHANGELOG） | 重写远端历史风险高；用户要求“各自 push”，追加提交同样满足可审计、可回滚。 |
| D13 | 世界确定性不受影响：管理面只改 provider 解析与遥测，不写事件、不影响随机数、不入哈希 | 三条红线之一（同 seed 同事件序列必须同结果）保持成立。 |
| D14 | admin 墙钟一律走 `src/wsim/admin/clock.py::now_utc()`（带 `# wallclock: audit-only`），`sim/`、`actions/`、`world/` 维持零容忍 | 审计/遥测允许墙钟，但必须单点收敛，且该值不得进入世界状态、事件序列、`content_hash`。 |

## 1. 独立进程与存储

- 入口：`python -m wsim.admin`，参数 `--root`（默认 `.`）、`--run`（默认 `$XKX_RUN` 或 `player-demo`）、`--host`（默认 `$XKX_ADMIN_BIND` 或 `127.0.0.1`）、`--port`（默认 `$XKX_ADMIN_PORT` 或 `8031`）、`--db`（覆盖）。
- 数据库解析顺序（`settings.resolve_db_path`）：
  1. `--db`
  2. `XKX_ADMIN_DB`
  3. `XKX_SERVICE_DB`
  4. `<root>/runs/<XKX_RUN or player-demo>/service.sqlite`
- 玩家 API 不新增任何端点；管理服务不复用玩家端口。
- 环境变量总表：`XKX_SECRET_KEY`、`XKX_ADMIN_TOKEN`、`XKX_ADMIN_BIND`、`XKX_ADMIN_PORT`、`XKX_ADMIN_ALLOW_IPS`、`XKX_ADMIN_DB`、`XKX_SERVICE_DB`、`XKX_RUN`。

### 1.1 schema（幂等，`PRAGMA user_version=2`）

```sql
admin_settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
               updated_at REAL NOT NULL, updated_by TEXT NOT NULL DEFAULT '')
admin_secrets(id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, nonce BLOB NOT NULL,
              created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
              last_used_at REAL NOT NULL DEFAULT 0, updated_by TEXT NOT NULL DEFAULT '')
admin_audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, who TEXT NOT NULL,
            action TEXT NOT NULL, key TEXT NOT NULL, before_json TEXT NOT NULL DEFAULT '',
            after_json TEXT NOT NULL DEFAULT '', detail_json TEXT NOT NULL DEFAULT '')
admin_sessions(token_hash TEXT PRIMARY KEY, created_at REAL NOT NULL, expires_at REAL NOT NULL, who TEXT NOT NULL)
admin_login_failures(ip TEXT NOT NULL, failed_at REAL NOT NULL)
admin_runtime(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL)
```

- 迁移：`user_version=1` 的库补 `admin_secrets.created_at/last_used_at` 两列（`ALTER TABLE` 前查 `PRAGMA table_info`），并建 `admin_runtime`；重复调用不重复 ALTER。
- 全局版本号 = `MAX(admin_settings.version)`；每次配置写入把受影响行 `version` 设为 `旧全局版本 + 1`。
- 所有配置写操作在 `BEGIN IMMEDIATE` 事务内完成「改配置 + 写审计」。
- `admin_runtime` 不参与版本号：存 `world_state`（D2）与 `capability:<name>` 记录。

### 1.2 配置键与默认值

`admin_settings` 按顶层段落存 4 行：`world`、`llm`、`providers`、`routing`。
`GET /api/admin/settings` 返回合并后的完整对象，并追加**服务端计算**字段：

- `hot`：扁平 `{键: bool}`，键为 §1.4 表中的叶子名（`ambient_speed`、`providers`、`global_enabled`…）
- `hot_notes`：`{键: 说明}`
- `world_state`：每个 `world` 键的运行观测四元组（D1/D5），见 §1.4
- `capabilities` + `capabilities_status` + `capabilities_evidence`：见 §2
- `env_bridge`：`admin_env.sh` 路径

默认值：

```json
{
  "world": {"tick_minutes": 10, "ambient_speed": 1, "ambient_cpu": 70, "player_cap": 200,
            "starting_money": 300, "quest_floor_enabled": true},
  "llm": {"global_enabled": true, "daily_budget_usd": 0.20,
          "per_capability_daily_tokens": {"dialogue": 0, "strategy": 0}},
  "providers": [],
  "routing": {"dialogue": {"provider": "", "fallback": []},
              "strategy": {"provider": "", "fallback": []},
              "self_goal": {"provider": "", "fallback": []},
              "night_batch": {"provider": "", "fallback": []},
              "quest_gen": {"provider": "", "fallback": []},
              "persona_gen": {"provider": "", "fallback": []},
              "validation": {"provider": "", "fallback": []}}
}
```

`PUT /api/admin/settings` 为深合并（按段落、按 provider id），**入参仍用标量值**（`{"world": {"ambient_speed": 2}}`），返回：

```json
{"ok": true, "version": 7,
 "requires_restart": ["ambient_speed"],
 "changed": ["ambient_speed"],
 "world_state": {"ambient_speed": {"value": 2, "effective_value": 1.0, "applied": false,
                                   "restart_required": true, "source": "ambient_status.json",
                                   "observed_at": "...", "note": "重启玩家 API 后生效"}},
 "settings": { ... 同 GET ... }}
```

### 1.3 加密与掩码

- `secrets.py`：AES-256-GCM，主密钥 `XKX_SECRET_KEY`（32 字节 base64，可用 `base64.b64encode(os.urandom(32))` 生成）。
- 未配置/长度不对 → 任何需要读写 secret 的接口返回 `503 {"ok": false, "code": "master_key_missing"}`；provider 只能来自环境变量；**不存在明文回退，明文不落盘**。
- 掩码 `mask_secret(v)`：`前4****后4`；长度 < 9 时全星号（如 `sk-****abcd` 语义）。
- `GET /api/admin/providers` 每行附：`has_key`、`key_masked`、`key_length`、`key_created_at`、`key_updated_at`、`key_last_used_at`。这些字段由 `secrets.meta()` 进程内解密计算，**响应里没有明文**。
- provider 列表、审计、日志、错误响应一律只用掩码。

### 1.4 生效契约：`hot` 与 `restart_required`

| 键 | 生效方式 | 依据（代码现状） |
|---|---|---|
| `llm.global_enabled`（killswitch） | hot | `provider.py` 每次调用前查快照，≤5s 生效 |
| `providers[*]` / `routing[*]` | hot | 同上，换 provider/model/key |
| `world.ambient_speed` | restart_required | 只在 `WorldSession.start_ambient_from_env` 启动时读 `XKX_AMBIENT_SPEED`；`api/**` 禁改 |
| `world.ambient_cpu` | restart_required | 同上（`XKX_AMBIENT_CPU`） |
| `world.tick_minutes` / `player_cap` / `starting_money` / `quest_floor_enabled` | restart_required | 代码中无运行期读取点 |
| `llm.daily_budget_usd` / `per_capability_daily_tokens` | restart_required | 预算实际由 `sim/headless.py` 的 `BudgetGuard` 读 `config/llm.yaml`；接 admin 需改 `sim/**`，本轮禁改 |

**双值契约**

- 写入 `world.*` 后：配置值落 `admin_settings.world`；`admin_runtime.world_state` 记 `{value, effective_value, applied, observed_at, source}`。
- `applied` 只有两种来源：`value == effective_value`（已生效），或检测到玩家进程重启后重新对齐；**绝不因为“写成功”就置 true**。
- `effective_value` 观测：
  - `ambient_speed` ← `ambient_status.json.speed`
  - `ambient_cpu` ← `ambient_status.json.cpu.limit * 100`
  - 其余 world 键 ← `null` + `source="none"`（D5）
- `AdminApp.reconcile_world_state()`：读 `ambient_status.json` 的 `pid`；与存储的 `applied_pid` 不同 → 视为玩家进程重启，刷新 `effective_value` 并重算 `applied`；pid 相同则只刷新实时观测值。管理服务启动时先跑一次。
- `admin_env.sh`：任何 `world` 写入后原子生成，内容仅：
  ```
  export XKX_AMBIENT=1
  export XKX_AMBIENT_SPEED=<config>
  export XKX_AMBIENT_CPU=<config>
  ```
  文件不存在时才创建；不含 key/token。
- 验收口径（不伪造）：写入后立即读 `effective_value` 仍是旧值、`applied=false`；`source admin_env.sh` 重启玩家 API 后，管理服务观测到新 pid，`effective_value` 变新值、`applied=true`。

## 2. capabilities_status（四态，只能由真实调用驱动）

状态定义（`providers.capability_report()` 唯一产出）：

| 状态 | 含义 |
|---|---|
| `stub` | 明确走本地确定性实现，无模型调用 |
| `unconfigured` | 该能力位没有配置 provider |
| `unverified` | 已配置，但从未有真实调用记录 |
| `active` | 最近一次真实调用成功（附 `last_check_at`、`latency_ms`） |
| `degraded` | 最近一次真实调用失败（附 `error`/HTTP 状态、`last_check_at`） |

按代码真实调用点判定（与 D6/D8 同源）：

| capability | 基础判定 | 依据 |
|---|---|---|
| dialogue | 可 `active/degraded/unverified` | `agent/dialogue_async.py::AsyncDialogueQueue` 挂到 `talk`（`WSIM_DIALOGUE=1`） |
| strategy | 可 `active/degraded/unverified` | `agent/strategy_client.py::StrategyClient`（`--strategy` 且非 fake） |
| night_batch | 可 `active/degraded/unverified` | `sim/headless.py::run_night_batch → LLMReasoner.decide_batch`（decision 槽） |
| self_goal | `stub` | `sim/self_goal.py` 确定性规则台账 |
| quest_gen | `stub` | `content/` + `actions/quests.py` 规则生成 |
| persona_gen | `stub` | `content/personas/*.md` 静态文本 |
| validation | `stub` | `agent/prompt.py` / `agent/intent_model.py` 确定性 schema 校验 |

**记录来源**：`provider.py` 每次真实调用结束后调用 `providers.record_result(...)`（成功/失败、耗时、provider、model、fallback、HTTP 状态），写 `admin_runtime` 的 `capability:<name>` 行；未真实调用的能力永远 `unverified`。启动不探活，`last_check_at` 初始为 `null`。

**响应形态**：`capabilities` 每项

```json
{"status": "active", "last_check_at": "...", "latency_ms": 123.4, "error": null,
 "provider": "relay-a", "model": "gpt-x", "fallback": ["relay-b"],
 "last_served_by": {"provider": "relay-a", "model": "gpt-x", "fallback_used": false},
 "source": "real_call", "evidence": "agent/..."}
```

顶层 `capabilities_status` 保留扁平 `{capability: status}` 以兼容；`capabilities_evidence` 给出证据。`GET /api/admin/settings` 与 `/api/admin/providers` 的 `routing_view` 读同一份聚合。

槽位→capability 映射（`providers.SLOT_CAPABILITY`）：`dialogue→dialogue`、`strategy→strategy`、`decision→night_batch`；`intent` 不纳入管理面（保持现有 env/config 解析，文档标注）。

## 3. 管理 HTTP API（`src/wsim/admin/server.py`）

鉴权：`Authorization: Bearer <session>`；会话有效 24h，库中只存 `sha256(token)`。
登录：`POST /api/admin/login {"token": "..."}` 对比 `XKX_ADMIN_TOKEN`（`hmac.compare_digest`）。失败计数按 IP：5 分钟内 ≥10 次 → 锁 15 分钟，返回 `429 {"code":"too_many_attempts"}`。
IP 白名单：回环地址恒允许；非回环必须命中 `XKX_ADMIN_ALLOW_IPS`（逗号分隔，精确匹配），否则 `403 {"code":"admin_ip_not_allowed"}`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/admin/login` | 返回 `{ok, token, expires_at}` |
| GET | `/api/admin/overview` | `health`、`world.tick`、`online_players`、`events_total`、`db_bytes`、`llm.calls`、`llm.cost_usd`、`backup.last_at`、`runtime`（含 `world_state` 摘要） |
| GET | `/api/admin/settings` | §1.2 完整对象（含 `world_state`、`capabilities`） |
| PUT | `/api/admin/settings` | 深合并 + 审计，返回 `requires_restart`、`world_state` |
| GET | `/api/admin/providers` | provider 列表 + 掩码元数据 + `routing_view`（能力位→provider/model/fallback） |
| PUT | `/api/admin/providers/{id}` | 写 provider 行；带 `api_key` 时写 secrets；无主密钥 → 503 |
| DELETE | `/api/admin/providers/{id}` | 删除 provider + secret，并清空 routing 中引用 |
| POST | `/api/admin/providers/{id}/test` | 真实一次 `max_tokens=1` 请求，返回 `{ok, latency_ms, model, error}`，绝不返回 key；不改能力位状态 |
| GET | `/api/admin/audit?limit=100` | `who/when/action/key/before/after`，值掩码 |
| POST | `/api/admin/llm/killswitch` | `{enabled}` → 写 `llm.global_enabled`，审计 |
| GET | `/api/admin/health` | 管理服务自身健康（不需要鉴权，返回版本/uptime/厂商数，不含配置值） |

所有写接口：401 未鉴权；403 IP；写成功必写审计。

## 4. 主服务接线（`src/wsim/agent/provider.py`）

- `ProviderClient._config_chain()`：槽位映射到 capability 后调用 `wsim.admin.providers.chain_for_slot(slot)`；异常/无配置返回 `[self.config]`（保持原 env 解析），killswitch 返回阻断码。
- 快照缓存（`providers.py`）：进程内单例，记录 `(version, settings, checked_at_monotonic)`；`get(max_age=5.0)` 在 `now - checked_at >= 5` 时才查一次 `MAX(version)`（只读、`timeout=1`）；查库失败沿用旧快照，不抛错、不阻塞调用。
- 命中 DB 配置时：`base_url/model/timeout_s/params(max_tokens,temperature)/thinking` 来自 DB；key 来自 `secrets.decrypt(id)`；缺失 key 时回退 env 配置；env 也没有 → `ProviderUnavailable("llm_not_configured")`。
- killswitch：快照里 `llm.global_enabled == false` → `ProviderUnavailable("llm_killswitch")`，`_run` 直接返回错误（不发 HTTP）。
- 真实调用结果回写：`_run` 的成功/最终失败出口调用 `admin_providers.record_result(capability, provider_id, model, ok, latency_ms, error, http_status, fallback_used)`；该函数吞掉所有异常并节流（D7/D11）。
- 未命中 DB（空 providers / 无 routing）→ 完全沿用现有 `ProviderConfig`，行为与现在一致。
- DB 不可用（文件不存在/表不存在）→ 静默沿用 env。
- 不改 `ProviderClient.__init__` / `complete` 签名；不改 `sim/**` 调用点。

## 5. 测试与验收

单测：
- `tests/test_admin_store.py`：schema 幂等（含 v1→v2 迁移）、版本递增、深合并、审计掩码、密文无明文、`master_key_missing`、secret 元数据、`world_state` 持久化、grep 日志/审计 0 命中、重开库配置仍在。
- `tests/test_admin_providers.py`：resolve 优先级 DB→env→异常、快照 5s 节流、刷新、killswitch 拒调用、缺 key 回退、429→fallback、`record_result` 四态、节流、stub/unconfigured。
- `tests/test_admin_server.py`：登录/401/限流锁定/IP 403、providers CRUD 掩码元数据、`test` 打本地 mock 中转站（成功/坏 base_url 失败）、429→fallback→成功、改 key 后 mock 收到新 key、audit 列表、killswitch、重启后配置仍在、写 `world.*` 后 `applied=false`/`effective_value` 仍旧、pid 变化后 `applied=true`。

两进程验收（写进报告，不新增仓库脚本）：玩家 API `:8022` + 管理服务 `:8031` + 本地 mock 中转站 `:9911`。

1. 登录 → 改 `ambient_speed` → 立即读 `effective_value` 仍旧、`applied=false`；`source admin_env.sh` 重启玩家 API → `applied=true`（贴 health 前后）。
2. mock `test` 成功给延迟；改坏 base_url → `test` 失败给错误；指定 `dialogue` 用该 provider，触发一次真实 dialogue 调用，mock 侧记录 + 主服务计数证明请求到达。
3. 降级链：mock 第一次 429，第二个 provider 成功，断言最终成功且 `fallback_used=true` 被记录。
4. 改 key → mock 侧看到新 key；grep 全日志/审计 0 明文。
5. 未配 `XKX_SECRET_KEY`：保存 key 返回 503 `master_key_missing`。
6. killswitch 打开后 LLM 调用被拒（`llm_killswitch`，计数不变）。
7. 审计列表能查到每次变更，值为掩码。
8. 重启管理服务配置仍在；`pytest` 全量通过并贴干净 HEAD 计数。

## 6. 未纳入本轮（如实声明）

- `world.*` 的运行时热改（需改 `api/session.py` 或 `sim/scheduler.py`，本轮禁改）；本轮用 `admin_env.sh` + 重启 + pid 观测兑现 `applied`。
- admin 的 `daily_budget_usd` / `per_capability_daily_tokens` 强制拦截（需改 `sim/headless.py`）。
- `intent` 槽与 `decision` 实时槽的独立路由（本轮只映射到 `night_batch`）。
- 前端 UI（本轮只交付后端）。
