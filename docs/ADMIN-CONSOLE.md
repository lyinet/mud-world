# 管理控制台后端（wsim.admin）

独立进程的管理服务：改游戏参数、配置 provider（中转站地址）与 key、按能力位分配 provider、查看真实调用状态。
所有写操作鉴权 + 审计；配置落 `service.sqlite`；key 用 AES-256-GCM 加密，界面只显示掩码。

> 术语以代码为准：存储与 API 的单位是 **provider**（`id/base_url/model/...`）。
> 控制台前端把同一 `base_url` 的多个 provider 聚成一张"中转站"卡片，**只是展示分组**，
> 后端没有中转站聚合实体，也没有单独的"中转站表"。

- 服务入口：`python -m wsim.admin`
- 默认监听 `127.0.0.1:8031`（`XKX_ADMIN_BIND` / `XKX_ADMIN_PORT` 可改）
- **不挂在玩家端口**，不改 `src/wsim/api/**`

## 1. 启动

```bash
export PYTHONPATH=src
export XKX_ADMIN_TOKEN='<管理登录口令>'
export XKX_SECRET_KEY="$(python -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())')"
export XKX_ADMIN_DB="$PWD/runs/<run>/service.sqlite"
python -m wsim.admin --root . --run <run> --host 127.0.0.1 --port 8031
```

| 环境变量 | 作用 |
|---|---|
| `XKX_ADMIN_TOKEN` | 管理登录口令（`POST /api/admin/login`） |
| `XKX_SECRET_KEY` | 主密钥（32 字节 base64）。**未配置时保存 key 返回 503 `master_key_missing`** |
| `XKX_ADMIN_BIND` / `XKX_ADMIN_PORT` | 监听地址/端口，默认 `127.0.0.1:8031`（容器内为 `0.0.0.0:8031`，仅内网可达） |
| `XKX_ADMIN_ALLOW_IPS` | 允许的非回环来源 IP（逗号分隔）。不配则只允许本机 |
| `XKX_ADMIN_DB` / `XKX_SERVICE_DB` / `XKX_RUN` | 数据库定位（优先级 `--db` → `XKX_ADMIN_DB` → `XKX_SERVICE_DB` → `runs/<XKX_RUN>/service.sqlite`） |

> **玩家进程必须使用同一份 `XKX_SECRET_KEY` 与同一个数据库**，否则无法解密 DB 里的 key，
> 会按“DB 无配置”回退到环境变量。主密钥只存在于环境变量，不落盘、不进审计。

## 2. 配置模型

`GET /api/admin/settings` 返回：

```json
{
  "world": {"tick_minutes": 10, "ambient_speed": 1, "ambient_cpu": 70,
            "player_cap": 200, "starting_money": 300, "quest_floor_enabled": true},
  "world_state": {
    "ambient_speed": {"value": 1, "effective_value": 1.0, "applied": true,
                      "restart_required": true, "source": "ambient_status.json",
                      "observed_at": "2026-11-06T02:39:19+0800", "note": "..."},
    "tick_minutes": {"value": 10, "effective_value": null, "applied": false,
                     "restart_required": true, "source": "none", "note": "主服务当前无运行期读取点"}
  },
  "hot": {"ambient_speed": false, "global_enabled": true, "daily_budget_usd": true,
          "per_capability_daily_tokens": true, "providers": true, "routing": true, "...": "..."},
  "llm": {"global_enabled": true, "daily_budget_usd": 0.20,
          "per_capability_daily_tokens": {"dialogue": 0, "strategy": 0}},
  "providers": [{"id": "relay-a", "base_url": "https://...", "model": "...", "...": "..."}],
  "routing": {"dialogue": {"provider": "relay-a", "fallback": ["relay-b"]}, "...": "..."},
  "capabilities": {"dialogue": {...}, "...": "..."},
  "capabilities_status": {"dialogue": "active", "strategy": "unconfigured", "...": "..."},
  "capabilities_evidence": {"dialogue": "agent/dialogue_async.py::...", "...": "..."},
  "capabilities_last_check_at": "2026-11-05T18:41:03Z",
  "budget": {"day": "2026-11-06", "timezone": "UTC", "daily_budget_usd": 0.20,
             "used_usd": 0.0, "remaining_usd": 0.20, "denied": 0,
             "tokens": {"dialogue": {"used": 0, "limit": 0, "remaining": null}}},
  "version": 11,
  "env_bridge": "runs/<run>/admin_env.sh"
}
```

`PUT /api/admin/settings` 支持按段落部分更新（`{"world":{"ambient_speed":2}}`），
返回 `requires_restart`、`changed`、`world_state`、`settings`。

**字段边界（与后端契约一致）**：

- `model` 只存在于 `providers[*]`；`routing.<capability>` 只有 `provider` 与 `fallback`，
  **没有独立的 model 字段**（换模型就是换 provider 条目）。
- provider 契约**没有 `auth_mode`**：主服务固定发
  `Authorization: Bearer <key>`，不支持按条目切换鉴权方式。
- `routing_view`（`GET /api/admin/providers`）里的 `model` 来自该能力位当前 provider 的
  `model`，并附 `last_served_by`（真实调用回写），不是 routing 配置里的字段。

## 3. 生效契约（不做假热更）

| 键 | 生效方式 | 说明 |
|---|---|---|
| `llm.global_enabled`（killswitch） | hot | 主服务每次调用前查快照，≤5 秒生效 |
| `providers[*]` / `routing[*]` | hot | 同上；换 provider/model/key 无需重启 |
| `world.ambient_speed` / `world.ambient_cpu` | restart_required | 只在玩家 API 启动时读 `XKX_AMBIENT*` |
| `world.tick_minutes` / `player_cap` / `starting_money` / `quest_floor_enabled` | restart_required | 主服务当前无运行期读取点 |
| `llm.daily_budget_usd` / `per_capability_daily_tokens` | hot | 主服务在**每次真实调用前**查快照并拦截，≤5 秒生效；计数落 `admin_usage` |

### 世界参数的“重启后生效”流程

1. 控制台写入新值 → `world_state.<key> = {value: 新值, effective_value: 当前运行值, applied: false}`。
2. 生成/更新 `<run>/admin_env.sh`（只含世界参数，无密钥）：
   ```bash
   source runs/<run>/admin_env.sh
   python -m wsim.api --root . --run <run> ...
   ```
3. 玩家 API 重启后 `ambient_status.json` 的 `pid`/`speed`/`cpu.limit` 变化；
   管理服务观测到新值后 `effective_value` 更新、`applied=true`。

> 写入后立即读取，`effective_value` 仍是旧值、`applied=false`——这是刻意行为，不是失败。
> `source` 环境桥并重启玩家进程后才置 `applied=true`。

## 4. key 与 provider（前端展示为“中转站”）

- 存储：`admin_secrets(id, ciphertext, nonce, created_at, updated_at, last_used_at, updated_by)`，
  AES-256-GCM（优先 `cryptography`，缺失时内置实现，NIST 向量已测）。
- 读取：`GET /api/admin/providers` 只返回
  `key_masked`（`前4****后4`）、`key_length`、`key_created_at`、`key_updated_at`、`key_last_used_at`；
  **任何响应、日志、审计都不出现明文**。
- 轮换：`PUT /api/admin/providers/{id}` 带 `api_key` 时写密文并**递增 settings 版本**，
  主服务快照随之重载，新调用使用新 key。
- 主密钥缺失：`secrets` 写入返回 503 `master_key_missing`，provider 只能来自环境变量。

## 5. 能力位与状态（只能由真实调用驱动）

能力位：`dialogue` / `strategy` / `self_goal` / `night_batch` / `quest_gen` / `persona_gen` / `validation`，
每个可独立配置 provider 与 fallback 链。

| 状态 | 含义 |
|---|---|
| `stub` | 明确走本地确定性实现 |
| `unconfigured` | 该能力位没有配置 provider |
| `unverified` | 已配置，但从未有真实调用记录 |
| `active` | 最近一次真实调用成功（附 `last_check_at`、`latency_ms`） |
| `degraded` | 最近一次真实调用失败（附 `error`/HTTP 状态、`last_check_at`） |

按代码真实调用点：

| capability | 真实调用点 | 当前判定 |
|---|---|---|
| dialogue | `agent/dialogue_async.py::AsyncDialogueQueue`（`WSIM_DIALOGUE=1` 挂到 `talk`） | 可 active/degraded/unverified |
| strategy | `agent/strategy_client.py::StrategyClient`（`--strategy` 且非 fake） | 可 active/degraded/unverified |
| night_batch | `sim/headless.py::run_night_batch → LLMReasoner.decide_batch`（decision 槽） | 可 active/degraded/unverified |
| self_goal | `sim/self_goal.py` 确定性规则 | stub |
| quest_gen | `content/` + `actions/quests.py` 规则生成 | stub |
| persona_gen | `content/personas/*.md` 静态文本 | stub |
| validation | `agent/prompt.py` / `agent/intent_model.py` 确定性校验 | stub |

- 启动**不主动探活**；状态初始为 `unverified`，`last_check_at=null`。
- 主服务每次真实调用结束后回写 `{status, last_check_at, latency_ms, error, http_status, provider, model, fallback_used}`；
  状态变化立即写、否则 ≥5 秒最多一次，写失败静默（不拖慢主服务）。
- `routing_view` 与 `capabilities` 同源：每个能力位可直接看到
  `provider`、`model`、`fallback`、`last_served_by`。

## 6. 预算与额度（真实调用前拦截）

### 6.1 口径

- **计费窗口：自然日（UTC，写死）**。费用按真实时间结算，所以不按世界日；
  `day` 固定为 UTC `YYYY-MM-DD`，跨 UTC 零点自动开新账，不做手动重置。
- **确定性边界**：该自然日只由 `clock.py::now_utc()` 推导，仅写入 `admin_usage.day` 与审计时间；
  **不进入世界状态、事件序列或 `content_hash`**。预算拒绝会让本次调用不发出，
  但拒绝本身不会把墙钟写入仿真数据。
- 回放（`ProviderClient.replay=True`）不检查、不记账：回放零网络、必须可复现。

### 6.2 语义

| 键 | 含义 | 0 值 |
|---|---|---|
| `llm.daily_budget_usd` | 当日（UTC）全部能力位累计花费上限（USD） | `≤0` 不限 |
| `llm.per_capability_daily_tokens.<cap>` | 该能力位当日 token 上限（输入+输出） | `≤0` 不限 |

- 调用前检查：**已用 ≥ 上限**即拒绝，不发出任何网络请求。
  - 当日花费超限 → `budget_exhausted`
  - 能力位 token 超限 → `capability_token_limit`
- 降级链照旧：被拒的 provider 跳过，继续下一个；全部被拒则本次调用以拒绝原因失败，
  `LLMReasoner` 按既有逻辑降级到 deterministic/mock，不中断 tick。
- 记账：每次真实 provider 调用的最终 `usage`（`prompt_tokens`/`completion_tokens`）累加；
  上游没回 `usage` 时按 4 字符≈1 token 估算。单价固定
  `$0.0015/1K input`、`$0.006/1K output`（与 `agent/cost.py` 默认一致），属于估算口径。
- 持久化：`admin_usage(day, capability, provider_id, calls, tokens_in, tokens_out, cost_usd, denied, updated_at)`，
  重启不丢当日已用额度。
- 审计：同一自然日同一能力位/provider/拒绝原因的**首次**拒绝写 `admin_audit`，
  `action=llm.budget_denied`，`detail` 含
  `day/timezone/capability/slot/provider_id/reason/used_usd/limit_usd/used_tokens/token_limit`；
  后续拒绝计入 `admin_usage.denied`，不重复刷审计。
- 失败放行：管理库读/写异常时放行且不阻断 tick，`GET /api/admin/budget` 的 `error` 字段暴露读取异常。

### 6.3 查询

- `GET /api/admin/budget`：`day`、`timezone`、`daily_budget_usd`、`used_usd`、`remaining_usd`、
  `tokens.{capability}.{used,limit,remaining}`、`by_provider`、`calls`、`denied`。
- `GET /api/admin/settings` 的 `budget` 字段同源；`capabilities.<cap>.budget` 也内联该项。
- `GET /api/admin/overview` 的 `budget` 为精简版（day/timezone/上限/已用/剩余/拒绝数）。

## 7. HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/admin/login` | `{token}` → 24h 会话；失败 10 次/5 分钟锁 15 分钟 |
| GET | `/api/admin/health` | 管理服务自身健康（无需鉴权） |
| GET | `/api/admin/overview` | 世界 tick、在线人数、事件总数、库体积、LLM 调用/花费、预算摘要、最近备份 |
| GET | `/api/admin/budget` | 今日已用 / 上限 / 剩余（自然日 UTC），含各能力位 token 与 provider 明细 |
| GET / PUT | `/api/admin/settings` | 见 §2/§3 |
| GET | `/api/admin/providers` | 列表（掩码 key + 元数据）+ `routing_view` |
| PUT / DELETE | `/api/admin/providers/{id}` | 新建/更新/删除；带 `api_key` 时加密入库 |
| POST | `/api/admin/providers/{id}/test` | 一次 `max_tokens=1` 真实请求，返回 `ok/latency_ms/model/error`；不返回 key，不改能力状态 |
| GET | `/api/admin/audit?limit=100` | 变更记录，值掩码 |
| POST | `/api/admin/llm/killswitch` | `{enabled}`；打开后新调用直接 `llm_killswitch` |

鉴权：`Authorization: Bearer <session>`；仅存 sha256。非本机来源需在 `XKX_ADMIN_ALLOW_IPS`，否则 403 `admin_ip_not_allowed`。

`GET /api/admin/audit` 的 `action` 取值为代码里真实写入的集合，不存在其他拼写：

| action | 触发 |
|---|---|
| `settings.update` | `PUT /api/admin/settings`（参数/路由写入） |
| `provider.put` | `PUT /api/admin/providers/{id}`（新建或改 provider 元数据） |
| `provider.key_rotate` | 同上且带 `api_key` 时额外一条（版本递增、主服务重载） |
| `secret.put` | 写入/轮换 provider 密钥（值为掩码） |
| `provider.delete` | `DELETE /api/admin/providers/{id}` |
| `llm.killswitch` | `POST /api/admin/llm/killswitch` |
| `llm.budget_denied` | 真实调用前被预算闸拒绝（同键首次，见 §6.2） |

## 8. 时间源纪律

- 管理面墙钟统一走 `src/wsim/admin/clock.py::now_utc()`（审计/遥测专用）。
- 该值不得进入世界状态、事件序列或 `content_hash`；影响仿真的参数只在 tick 边界从持久化配置读取。
- `sim/`、`actions/`、`world/` 仍为确定性零容忍，不在此列。

## 9. 未实现 / 边界（如实声明）

- `ambient_speed` 等世界参数无法热改：玩家 API 只在启动时读取，`api/**` 禁改；
  用环境桥 + 重启 + `pid` 观测兑现 `applied`，不做假热更。
- `intent` 槽仍走原有 env/config；管理面只映射 `dialogue`、`strategy`、`decision→night_batch` 三个能力位。
- 预算闸的已知边界：
  - `POST /api/admin/providers/{id}/test`（`max_tokens=1`）属于连通性探测，**不检查、不记账**。
  - 环境变量兜底的 provider 在 `admin_usage.provider_id` 中记为 `env`。
  - 检查是“先查后花”，并发批量同时通过检查时可能小幅超限；下次调用即被拒绝。
  - 单价为固定估算值，不是上游真实账单；以 `usage` 为准，缺失时按字符估算。
  - 只在 `dialogue` / `strategy` / `night_batch` 三个真实调用点生效，stub 能力位不受影响。
- 前端静态页在 `ui/admin/`，由反代 `location /admin/` 直接提供（`alias /usr/share/nginx/admin/`）；
  容器化起用方式见 [docs/OPS-RUNBOOK.md](OPS-RUNBOOK.md) §1.5。前端只消费本文档列出的真实端点。

## 10. 2026-11-06 两进程验收摘要

- 玩家 API `:8022`（run `admin-accept`）+ 管理服务 `:8031` + mock 中转站 `:9911`。
- 改 `ambient_speed 1→2`：写入后 `effective_value=1.0`、`applied=false`；`source admin_env.sh` 重启玩家进程后
  `pid 3281→3541`、`speed 2.0`、`applied=true`。
- mock `test`：成功 `latency_ms=29.3`；改坏 base_url 后 `ok=false, error=network`。
- 真实 dialogue 调用打到 mock（`max_tokens=8`、`model=mock-model-a`）；降级链 429→relay-b 成功，
  `stats.fallbacks=1`、`last_served_by.provider=relay-b`。
- 轮换 key 后 mock 收到 `Bearer sk-rotated-ccc333`；日志/审计/DB 字节 grep 明文 0 命中。
- 无主密钥实例保存 key → 503 `master_key_missing`。
- killswitch 后调用 `llm_killswitch`，mock 行数不变。
- 重启管理服务后 `version=11`、providers/routing/world_state/`dialogue=active` 全部保留。
- 2026-11-06 预算闸真实验收（run `admin-budget-accept`，管理 `:8031` + 玩家 `:8034` + mock `:9911`，
  对话走 `AsyncDialogueQueue → ProviderClient`，与 `sim/headless.py` 同一链路；UTC 自然日 `2026-11-05`）：
  - `daily_budget_usd=0.2`：首次真实 dialogue 成功，mock 1 次，`used_usd=0.00024`、`used_tokens=130`。
  - 调到 `0.000001`：调用被拒 `last_error=budget_exhausted`，mock 仍 1 次（未发请求），
    降级链 `fallbacks=1`、`errors_by_kind.budget_exhausted=2`（relay-a/relay-b 都被拒），tick 未崩。
  - `per_capability_daily_tokens.dialogue=100`（已用 130）：拒绝 `capability_token_limit`，`fallbacks=2`，mock 仍 1 次。
  - 调回 `daily=0.2`、token 上限 `0`：恢复成功，mock 1→2，`used_usd=0.00048`、`used_tokens=260`。
  - 审计 4 条 `llm.budget_denied`（relay-a/b × 两种原因），`detail` 含
    `day/timezone/slot/provider_id/reason/used_usd/limit_usd/used_tokens/token_limit`；
    run 目录（含 `service.sqlite`）grep key 明文 0 命中。
  - 重启管理服务后 `used_usd=0.00048`、`used_tokens=260`、`denied=4` 仍在（`admin_usage` 落盘）。
  - 全量 `pytest`：653 collected（635 passed / 18 skipped），退出码 0。
