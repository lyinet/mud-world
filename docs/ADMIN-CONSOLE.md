# 管理控制台后端（wsim.admin）

独立进程的管理服务：改游戏参数、配置 API 中转站与 key、按能力位分配模型、查看真实调用状态。
所有写操作鉴权 + 审计；配置落 `service.sqlite`；key 用 AES-256-GCM 加密，界面只显示掩码。

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
| `XKX_ADMIN_BIND` / `XKX_ADMIN_PORT` | 监听地址/端口，默认 `127.0.0.1:8031` |
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
  "hot": {"ambient_speed": false, "global_enabled": true, "providers": true, "routing": true, "...": "..."},
  "llm": {"global_enabled": true, "daily_budget_usd": 0.20,
          "per_capability_daily_tokens": {"dialogue": 0, "strategy": 0}},
  "providers": [{"id": "relay-a", "base_url": "https://...", "model": "...", "...": "..."}],
  "routing": {"dialogue": {"provider": "relay-a", "fallback": ["relay-b"]}, "...": "..."},
  "capabilities": {"dialogue": {...}, "...": "..."},
  "capabilities_status": {"dialogue": "active", "strategy": "unconfigured", "...": "..."},
  "capabilities_evidence": {"dialogue": "agent/dialogue_async.py::...", "...": "..."},
  "capabilities_last_check_at": "2026-11-05T18:41:03Z",
  "version": 11,
  "env_bridge": "runs/<run>/admin_env.sh"
}
```

`PUT /api/admin/settings` 支持按段落部分更新（`{"world":{"ambient_speed":2}}`），
返回 `requires_restart`、`changed`、`world_state`、`settings`。

## 3. 生效契约（不做假热更）

| 键 | 生效方式 | 说明 |
|---|---|---|
| `llm.global_enabled`（killswitch） | hot | 主服务每次调用前查快照，≤5 秒生效 |
| `providers[*]` / `routing[*]` | hot | 同上；换 provider/model/key 无需重启 |
| `world.ambient_speed` / `world.ambient_cpu` | restart_required | 只在玩家 API 启动时读 `XKX_AMBIENT*` |
| `world.tick_minutes` / `player_cap` / `starting_money` / `quest_floor_enabled` | restart_required | 主服务当前无运行期读取点 |
| `llm.daily_budget_usd` / `per_capability_daily_tokens` | restart_required | 预算实际由 `sim/headless.py` 读 `config/llm.yaml`，本轮未接线 |

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

## 4. key 与中转站

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

## 6. HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/admin/login` | `{token}` → 24h 会话；失败 10 次/5 分钟锁 15 分钟 |
| GET | `/api/admin/health` | 管理服务自身健康（无需鉴权） |
| GET | `/api/admin/overview` | 世界 tick、在线人数、事件总数、库体积、LLM 调用/花费、最近备份 |
| GET / PUT | `/api/admin/settings` | 见 §2/§3 |
| GET | `/api/admin/providers` | 列表（掩码 key + 元数据）+ `routing_view` |
| PUT / DELETE | `/api/admin/providers/{id}` | 新建/更新/删除；带 `api_key` 时加密入库 |
| POST | `/api/admin/providers/{id}/test` | 一次 `max_tokens=1` 真实请求，返回 `ok/latency_ms/model/error`；不返回 key，不改能力状态 |
| GET | `/api/admin/audit?limit=100` | 变更记录，值掩码 |
| POST | `/api/admin/llm/killswitch` | `{enabled}`；打开后新调用直接 `llm_killswitch` |

鉴权：`Authorization: Bearer <session>`；仅存 sha256。非本机来源需在 `XKX_ADMIN_ALLOW_IPS`，否则 403 `admin_ip_not_allowed`。

## 7. 时间源纪律

- 管理面墙钟统一走 `src/wsim/admin/clock.py::now_utc()`（审计/遥测专用）。
- 该值不得进入世界状态、事件序列或 `content_hash`；影响仿真的参数只在 tick 边界从持久化配置读取。
- `sim/`、`actions/`、`world/` 仍为确定性零容忍，不在此列。

## 8. 本轮未达成（如实声明）

- `ambient_speed` 等世界参数无法热改：玩家 API 只在启动时读取，`api/**` 本轮禁改；用环境桥 + 重启 + `pid` 观测兑现 `applied`，不做假热更。
- `llm.daily_budget_usd` / `per_capability_daily_tokens` 未强制拦截（需改 `sim/headless.py`）。
- `intent` 槽仍走原有 env/config；管理面只映射 `dialogue`、`strategy`、`decision→night_batch`。
- 无前端 UI；本轮只交付后端。

## 9. 2026-11-06 两进程验收摘要

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
