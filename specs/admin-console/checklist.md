# 管理控制台后端验收清单

## 契约与存储

- [x] `GET /api/admin/settings` 的 `world_state` 每键含 `value/effective_value/applied/restart_required/source/observed_at/note`
- [x] 写 `ambient_speed` 后立即读：`value` 为新值、`effective_value` 仍为旧值、`applied=false、restart_required=true`
- [x] 重启玩家 API（`source <run>/admin_env.sh`）后：管理服务观测到新 `pid`，`effective_value` 变新值、`applied=true`
- [x] 无运行期观测点的 world 键：`effective_value=null`、`applied=false`、`source="none"`（不编造）
- [x] `hot` 由服务端计算，`llm.global_enabled`/`providers`/`routing` 为 true，world 键为 false
- [x] schema v2 幂等：v1 库升级补 `admin_secrets.created_at/last_used_at` 与 `admin_runtime`，重复 init 不报错

## 加密与密钥

- [x] 未配置 `XKX_SECRET_KEY` 时保存 key 返回 503 `master_key_missing`，且库中无明文
- [x] key 以 AES-256-GCM 存 `admin_secrets`（密文+nonce），无明文备份/日志/审计
- [x] `GET /api/admin/providers` 只回 `key_masked`（`前4****后4`）+ `key_length` + `key_created_at/key_updated_at/key_last_used_at`
- [x] 真实调用成功后 `key_last_used_at` 更新；改 key 后 mock 侧只看到新 key
- [x] 明文 key 写入后 grep 全部日志/审计文件 0 命中

## 能力位与路由

- [x] `capabilities` 四态：stub（self_goal/quest_gen/persona_gen/validation）、unconfigured（无路由）、unverified（配了但从未真实调用）、active/degraded（有真实调用记录）
- [x] 启动不探活：状态初始为 `unverified`，`last_check_at=null`
- [x] `active/degraded` 带 `last_check_at`、`latency_ms`，失败带 `error`/HTTP 状态
- [x] 每个能力位可查“当前由哪个 provider 的哪个 model 承担”（`provider/model/fallback/last_served_by`），与 `capabilities_status` 同源
- [x] 429 降级到第二 provider 后成功：能力位 `active` 且 `last_served_by.fallback_used=true`

## 管理与安全

- [x] 登录/401/限流（10 次/5 分钟锁 15 分钟）/非本机 403 `admin_ip_not_allowed`
- [x] 所有写接口鉴权 + 审计；审计中敏感值为掩码
- [x] killswitch 打开后新调用返回 `llm_killswitch`，调用计数不增长
- [x] 重启管理服务后配置与 `world_state` 仍在

## 交付

- [x] 两进程 + mock 中转站真实验收 1–8 输出已贴报告
- [x] `pytest` 全量通过：642 collected，退出码 0（17 skipped）
- [x] 提交 `add4075` 已 push；文档、CHANGELOG、spec 随后提交
