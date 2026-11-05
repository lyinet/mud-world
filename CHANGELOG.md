# CHANGELOG

> 制度：每次改动都必须 commit 并推送到 `origin`，且在本文件留一条记录。版本按时间倒序。
> 每条必须写清：**改了什么、为什么改、验证方式、commit 短哈希**。
> 数字只允许取自 `git log`、`docs/RELEASE-NOTES-*.md`、`docs/DELIVERY-TEST-*.md`、`reports/`；
> 查不到的一律写“未记录”，不得估算或外推。
> 跨版本比较事件数 / 金额，必须同时标注当时的 `content_hash` 与 `state_final` / `event_final` 哈希。
> 提交流程与 tag 规则见 [docs/GIT-WORKFLOW.md](docs/GIT-WORKFLOW.md)。

---

## [功能] 2026-11-06 — 管理控制台后端（`src/wsim/admin`）

- **改了什么**
  - 新增独立管理服务 `python -m wsim.admin`（默认 `127.0.0.1:8031`，不挂玩家端口）：
    `POST /api/admin/login`（24h 会话、失败 10 次/5 分钟锁 15 分钟）、
    `GET /api/admin/overview`、`GET/PUT /api/admin/settings`、
    `GET /api/admin/providers` + `PUT/DELETE /api/admin/providers/{id}`、
    `POST /api/admin/providers/{id}/test`（`max_tokens=1`）、
    `GET /api/admin/audit`、`POST /api/admin/llm/killswitch`。
  - 配置落 `service.sqlite`：`admin_settings` / `admin_secrets` / `admin_audit` /
    `admin_sessions` / `admin_login_failures` / `admin_runtime`（schema v2，幂等迁移）。
  - key 用 AES-256-GCM 加密（优先 `cryptography`，缺失时内置实现，NIST 向量已测）；
    主密钥仅来自 `XKX_SECRET_KEY`，未配置时保存 key 返回 503 `master_key_missing`；
    响应/日志/审计只出现 `前4****后4` 掩码与长度/创建/最后使用时间。
  - `world` 参数不做假热更：`world_state` 返回 `value/effective_value/applied/restart_required`；
    重启玩家进程（`ambient_status.json` 的 `pid` 变化）并观测到新值后才置 `applied=true`；
    生成 `<run>/admin_env.sh` 环境桥。`hot` 只覆盖 `llm.global_enabled`/`providers`/`routing`。
  - `capabilities_status` 四态（`stub`/`unconfigured`/`unverified`/`active`/`degraded`）
    只由真实调用回写，启动不探活；与 `routing_view`（provider/model/fallback/last_served_by）同源。
  - `agent/provider.py` 按槽位从 DB 快照解析 provider（5 秒节流），DB → 环境变量 → `llm_not_configured`；
    不改调用方签名；真实调用结果回写能力状态；killswitch 直接拒绝。
  - 管理面墙钟收敛到 `src/wsim/admin/clock.py::now_utc()`（审计/遥测专用），
    不进入世界状态、事件序列与 `content_hash`。
- **为什么改**：上线需要一个可审计、可回滚、带总闸的运营入口，且 key 不能明文落盘；
  同时不能把“写进内存变量并回 200”伪装成热更新。
- **验证方式**
  - 两进程 + 本地 mock 中转站真实验收：改 `ambient_speed 1→2` 后 `applied=false`、
    `effective_value=1.0`；`source admin_env.sh` 重启玩家进程后 `pid 3281→3541`、`applied=true`。
    mock `test` 成功（`latency_ms=29.3`）与坏 `base_url` 失败；真实 dialogue 调用到达 mock；
    429 → 第二 provider 成功（`stats.fallbacks=1`）；轮换 key 后 mock 只收到新 key；
    日志/审计/DB 字节 grep 明文 0 命中；无主密钥 503；killswitch 后 mock 调用数不变；
    重启管理服务后配置与能力状态仍在。
  - `PYTHONPATH=src .venv/bin/python -m pytest -q`：642 项收集，全量退出码 0（17 skipped）。
- **commit**：`add4075`

---

## [功能] 2026-11-05 — 玩家经营五动作 + 盈利性验证（动作集 `3d0ceab`）

- **改了什么**
  - `actions/business.py`：新增 `restock` / `set_price` / `dismiss` / `withdraw` /
    `shop_report`。`restock` 从区域公账（批发价 = 基准 60%）或同地点他店（当店价）
    真实 `transfer` 进货，`item_create/item_move(reason=restock)`，返回单价/总价/
    库存前后；`set_price` 限基准价 ±20%，`price<=0` 恢复基准；`dismiss` 解除契约、
    清空未来工资记录但**保留已产生的欠薪债务**；`withdraw` 店现→钱包、不得超店现；
    `shop_report` 只读返回最近 7 日 `{revenue,restock,wage,tax,net_profit,tx,customers}`。
  - 日结行新增 `tx/customers`；雇工产出按技能（技能 ≥0.5 产出 2 件）；
    新增 `shop_attraction`（stall 0.32 / storefront 0.45 / branch 0.55，
    每名在店出勤雇工 +0.12，封顶 0.80）。
  - `actions/common.py`：`shop_price()` 与按当店价的 `selling_shops`（先筛最低价卖家，
    同价再按稳定散列轮转）；`actions/commerce.py` 买入按当店价成交。
  - `sim/intent.py`：专程进店概率按档位/在店雇工；NPC 每日两篮（口粮 + 耐用品），
    仅在检测到玩家店时启用；无玩家世界走旧分支。
  - `world/state.py`：`Business.price_overrides/day_history`（非空才序列化）+
    运行期 `day_customers`；`world/effects.py` 新增 `shop_dismiss/shop_price/
    shop_history/shop_report` 与日结重置。
  - `api/session.py`：仅新增 5 个动作的白名单 / 别名 / `_args` 与 `shop_report` 的
    `details.report`+`details.days` 透传（W4 的 UI 读 `details.days`），
    `withdraw` 兼容 UI 的 `amount` 键；**不改任何既有视图结构**（W1 `8ff784e`）。
  - `docs/shop-operations.md` 新增第 11–12 节（五动作语义/拒绝码/真实服务样例、
    定价与买主比价）；新增 `docs/shop-profitability.md`（10 日流水、三档盈亏平衡表、
    NPC 侧对照、门禁与守恒）。
- **为什么改**
  旧账下分号 3 雇工日固定 70 文、客流 5–10 人/日，结构性亏损，开店等于交税；
  且玩家没有补货/定价/辞退/提现/看账手段，只能干等。
- **验证**
  - `pytest`：573 passed, 18 skipped；1 项上游既有失败 `test_determinism_lint`
    （`177749d` 的 `wsim/admin/audit.py`、`secrets.py` 使用 `time.time`，与本轮无关，
    已在上游基线 `177749d` 复现）。本轮的 `tests/test_shop_operations.py` 27 项全过，
    覆盖五动作成功/拒绝、同地点两家店买便宜的、7 日报告、技能产出、API 白名单。
  - 无玩家 2 日 golden（seed42）：基线 `21f107a` 与当前 `state_final
    009076f0…` / `event_final 02d5f812…` 逐字节一致（`state_final.json`
    sha256 `91605d57…`、`events.jsonl` sha256 `1958e73b…`，两份 735 行）。
  - 无玩家 10 日（seed42，`content_hash 7ec670c1…`）：`events 3084`、
    `state_final 68b05948…`、`event_final 73026568…`、K1 `62/91 = 68.13%`，
    与基线逐项一致（旧基线不 skip、无新基线）。
  - 真实服务（ambient，分号 3000 文 + 3 雇工，+20% 定价）连续 10 日：
    营收 2134 / 进货 385 / 工资 272 / 税 100 / 净利 **+1377**；day4/5/6
    连续为正（+130/+255/+466），满足硬条件。
  - 同 seed 玩家店世界两次运行 `events 3090` 与 `state_final/event_final/K1`
    完全一致；真实服务事件回放 `conservation delta=0`
    （`sum(accounts)=50128 = minted 51350 − burned 1222`）。
  - 玩家店成交占比 3.97%（24/604，<10%）；NPC 侧合计 547→580（+6.0%）、
    NPC `buy` 114→177（+55.3%）、NPC 店零售营收 2375→4036（+69.9%），
    属新增客流而非转移。
- **本笔 commit**：动作集 `3d0ceab`（已推送 `origin/shop-operations`）；
  盈利验证文档与 CHANGELOG 为本笔。

---


## [W4 店铺经营 UI] 2026-11-06 — 进货 / 定价 / 辞退 / 提现 / 7 日账本（mock 验收）

- **改了什么**
  - `ui/player/index.html`：经营页签新增进货列表、定价列表、提现区与 7 日账本；
    账本表头为世界日 / 营收 / 进货 / 工资 / 税 / 净利 / 笔数 / 说明，底部「7 日合计」；
    雇工行新增「辞退」按钮与二次确认弹窗（写明欠薪仍计）。
  - `ui/player/app.js`：接入 `restock {item,qty}`、`set_price {item,price}`、
    `dismiss {target}`、`withdraw {amount}`、`shop_report {}`；无店/非店主禁用，
    动作在途按钮禁用并显示「处理中…」，成功提示带具体数字；新增原因码中文映射
    `price_out_of_range` / `insufficient_shop_cash` / `target_not_employed` /
    `bad_arg:price` / `bad_arg:amount` 并修正 `not_shop_owner` 文案。
    账本负净利原因由该行 `revenue/wage/restock` 推导，不写死；`shop_report` 为空显示空态。
  - `ui/player/style.css`：账本表格、正负净利着色、横向滚动与粘性首列、44px 控件、
    375×812 断点。
  - `tools/ui_check.py`：mock `shop` 增加 `cash/employees/stock[]/report`，实现五个动作与
    拒绝码；新增 `--only shopops`（16 项断言）与 `/__check/shop_report`、`/__check/slow`、
    `/__check/verb_stats` 测试路由。
  - `docs/PLAYER-GUIDE.md` §7.1 新增店铺经营玩家说明；`docs/w4-shopops-spec.md`、
    `docs/w4-shopops-tasks.md` 记录本轮规格/任务。
- **为什么改**：经营页签此前只有开店、库存件数和雇工名单，店主无法进货、调价、辞退、
  提现，也看不到日结盈亏；补齐后店主可在 UI 完成日常经营并诊断亏损原因。
- **验证方式**
  - `python3 tools/ui_check.py --only shopops`：16 checks，0 failed。
  - `python3 tools/ui_check.py`：151 checks，0 failed。
  - `python3 tools/ui_check_proxy.py`：12 checks，0 failed。
  - `python3 -m pytest -q`：exit 0。
  - 证据：`reports/w4-shopops/`（`mock-shopops*.png`、`mock-shopops.json`）。
- **后端未就绪**：当前 master 无这五个动作；本轮只做 UI + mock，真服务脚本
  `tools/ui_check_real_shopops.py` 将显式探测并标 `blocked_backend_not_ready`，
  不用 mock 顶替真服务证据。
- **commit**：`857172b`。

---

## [W4 店铺经营真服务验收] 2026-11-06 — 能力探测记录 + `blocked_backend_not_ready`

- **改了什么**
  - 新增 `tools/ui_check_real_shopops.py`：真 `wsim.api`（`--auth off` + ambient 4x）+ 真代理，
    浏览器开摊 → 进货 → 改价 → 等日结 → 账本 → 提现 → 辞退；每步截图，页面 hook fetch
    保存真实请求/响应到 `reports/w4-shopops/real-shopops.json`。
  - 进入后先对 `restock/set_price/dismiss/withdraw/shop_report` 发最小探测请求；
    命中 `unknown_action` / `unknown_verb` / `not_implemented_for_player` / HTTP 404
    即写 `blocked_backend_not_ready` 并退出 0，**不跑 mock、不伪造成功**。
  - 账本为空（开店不足一日）时如实显示空态并截图，记录 `ledger_empty: true`，不延长等待凑数据。
- **为什么改**：UI 与 mock 已就绪，但五个后端动作当前不在 master；需要一条可重复的真服务
  验收路径，并在后端未就绪时给出可审计的阻断结论，而不是假成功。
- **验证方式**
  - `python3 tools/ui_check_real_shopops.py --settle-timeout 20`：exit 0，
    2 checks / 0 failed，输出 `BLOCKED`，五个动作探测均 `unknown_action`。
  - 证据：`reports/w4-shopops/`（`real-0-business.png`、`real-0-blocked.png`、
    `real-7-mobile.png`、`real-shopops.json`）。
  - 全量 UI/mock 验收见上一条（`ui_check.py` 151/0、`--only shopops` 16/0、proxy 12/0）。
- **commit**：本条目所在提交。

---

## [部署 门 3] 2026-11-06 — TLS 终止与反向代理（nginx），后端只留内网

- **改了什么**
  - 新增 `deploy/nginx/xkx.conf`：`80 → 308 https://…`（保留方法与请求体）、
    `443` TLS 终止（TLSv1.2/1.3）、`X-Real-IP` / `X-Forwarded-For` /
    `X-Forwarded-Proto` / `X-Forwarded-Host` 透传、`client_max_body_size 8k`
    （与后端 `_MAX_BODY_BYTES=8*1024` 一致）、`/healthz` 明文放行不记日志、
    `/api/health` 单独放行、`server_tokens off`、读超时 30s 对齐后端。
  - 新增 `deploy/gen_selfsigned_cert.sh`：生成自签证书（SAN `localhost` /
    `127.0.0.1` / `::1`），仅用于演练；生产同名替换运维签发证书。
  - `deploy/docker-compose.yml`：新增 `proxy` 服务（`nginx:1.27-alpine`，
    `depends_on world healthy`，80/443 可 `PUBLIC_HTTP_PORT`/`PUBLIC_HTTPS_PORT` 覆盖）；
    `world` 增加 `expose 8020/8030`，宿主发布仍仅 `127.0.0.1`，公网只能经 proxy。
  - `.gitignore`：忽略 `deploy/nginx/certs/`（证书与私钥不入库）。
  - `docs/OPS-RUNBOOK.md`：新增 §1.4「对外入口与 TLS」，含部署形态表、选 nginx 的
    理由、起用步骤、路由/安全策略表、自签握手与 `/api/state` 200、308 跳转、
    XFF 回显上游实测、8KB 上限对齐实测、限流粒度说明与故障排查表。
- **为什么改**：门 3 要求公网入口具备 TLS 终止、强制跳转、来源头透传与和后端一致的
  请求体限制；同时后端不得直接暴露公网。选 nginx 是因为需要精确的 8KB 上限与
  探活放行语义，且证书来自自有 CA（无需 ACME）。
- **验证方式（自签，colima 29.5.2）**
  - `openssl s_client` 握手成功（`subject=CN=localhost`，SAN 含 `127.0.0.1`）。
  - `curl -sk https://127.0.0.1/api/state?account_id=tlsgate3 -H "Authorization: Bearer …"`
    → `CODE=200`、`ok=true`、`location=market`（注册/登录/进入世界均走 HTTPS）。
  - `curl http://127.0.0.1/...` → `308` + `Location: https://…`（POST 同样 308）。
  - `X-Forwarded-For/Proto/Host/X-Real-IP` 在回显上游容器里逐项确认到达。
  - 9KB 请求：proxy 413、直连后端 413（同为 8KB 上限）；7KB 到达后端（401）。
  - `netstat`：`*.80/*.443` 对外，`127.0.0.1.8020/.8030` 仅回环；局域网 IP 访问 8020 被拒。
- **未做**：未改 `src/wsim/api/**`（后端不信任 XFF 导致的限流粒度为文档说明，非本门改动）；
  未回退 `deploy/Dockerfile` 的 version 元数据。
- **commit**：`（见本提交）`。

---

## [P0 数据安全] 2026-11-06 — 异机/异地备份副本（`tools/backup_offsite.sh`）

- **改了什么**
  - 新增 `tools/backup_offsite.sh`：把最新一份自生成备份推送到 `local` 目录 /
    `rsync` over ssh / `rclone` remote，三选一由 `XKX_OFFSITE_MODE` 决定；
    目标由 `XKX_OFFSITE_TARGET` 给出。
    - **推送后必须在远端取 sha256 与 size**，与本地 `manifest.json` 的
      `databases.<name>.sha256/size_bytes` 逐项比对；远端缺失/读取失败/不一致 →
      退出码 1，输出 JSON 里带 `stage` 与逐文件 `remote_sha256`/`remote_size_bytes`。
    - 无远端配置时输出 `offsite=disabled`（不假装成功）；`XKX_OFFSITE_REQUIRED=1`
      （生产语义）缺失配置 → 退出码 3。
    - 新增 `--verify-only`：不推送、只复核远端已有副本（运维定期核对 / 篡改检测）。
  - `scripts/backup_loop.sh`：每轮备份成功后调用 offsite；失败写
    `last_backup_status.json`（`stage="offsite"` + `exit_code` + `error`）并 `exit 1`。
    同时修复失败分支里的真实 bug：`$OSRC：`（全角标点紧跟变量）在 macOS `/bin/sh`
    （bash 3.2）下会被当成变量名的一部分，触发 `unbound variable` 并吞掉退出码；
    已改为 `${OSRC}`（`$rc`、`$SOURCE` 同类位置一并加固）。
  - `deploy/Dockerfile`：安装 `rsync` 与 `openssh-client`，让默认镜像具备
    rsync over ssh 的异地推送能力（`rclone` 仍按需在派生镜像中安装）。
  - `docs/BACKUP-RESTORE.md`：新增 §5.3（offsite 配置/失败语义/实测输出/容器注意点）、
    §7-F（默认部署实测 + 只读卷 Exited(1) + 源库截断 exit 1 + healthcheck 判 unhealthy）、
    §9.1/§9.2（offsite 已实现、恢复时该用哪份产物）、§10 故障行。
- **为什么改**：此前"备份与主库同盘"只是文档里的"风险接受"，主机或磁盘损坏会同时
  丢主库与备份；且没有远端校验的推送等于不知道副本是否可用。
- **验证方式（本地另一目录模拟异地）**
  - 推送 + 远端校验：`{"ok": true, "offsite": "enabled", … "verified": true}`，`EXIT=0`。
  - 远端截断到 4096 字节：`stage=verify`、`sha256 mismatch remote=310bbc65… manifest=d65cf12d…`，`EXIT=1`。
  - 远端文件删除：`remote_read_failed (remote file missing: …)`，`EXIT=1`。
  - loop 接入：目标目录只读 → `LOOP_EXIT=1`，状态文件 `stage=offsite/exit_code=1`；
    `XKX_OFFSITE_REQUIRED=1` 且未配置 → `exit_code=3`，同样 `LOOP_EXIT=1`。
  - 容器内：`docker run … XKX_OFFSITE_MODE=local` 推送成功 `CTR_EXIT=0`；镜像内
    `rsync 3.5.0` / `OpenSSH_10.0p2` 可用。
- **未做**：未改 `src/wsim/api/**`、`actions/**`、`sim/**`、`ui/**`；
  未回退 `deploy/Dockerfile` 的 version 元数据。
- **commit**：`（见本提交）`。

---

## [P0 数据安全] 2026-11-06 — 备份默认生效、失败不再静默、状态可观测

- **改了什么**
  - `deploy/docker-compose.yml`：`backup` 服务去掉 `profiles: ["backup"]`，成为默认服务
    ——不带任何 profile 的 `docker compose up` 就会跑备份；命令改为
    `exec sh /app/scripts/backup_loop.sh`，删除 `|| true` 与 `while true` 内联循环。
  - `scripts/backup_loop.sh`：重写失败语义。单轮失败按 `BK_RETRIES`（默认 2）重试，
    仍失败则写 `last_backup_status.json`（`ok=false` + `stage` + `exit_code` + `error`）
    并 `exit 1`；成功也写状态文件（含 `backup_dir`、events seq、各文件 sha256/size、
    offsite 结果）。启动时先等源库出现（`BK_STARTUP_WAIT`，默认 300s），避免把
    world 建库竞态记成失败。
  - 新增 `deploy/backup_healthcheck.py`：读状态文件判健康——缺失 / `ok=false` /
    过期（`BK_INTERVAL × BK_STALE_FACTOR + 600s`）→ unhealthy；替换镜像继承的
    API 健康检查。`backup` 服务 `restart: "no"`：失败后保持 `Exited(非零)` 等人工介入，
    不在坏盘上无限重启。
- **为什么改**：默认部署里备份是 profile（默认不跑），且 `|| true` 会把失败吞掉，
  容器仍 `Up`。等于"有备份能力、默认不跑、跑失败也不报警"，不满足 P0。
- **验收（实测）**
  - 不带 profile：`docker-compose up -d` 后 `xkx-world healthy` / `xkx-backup healthy`，
    备份卷出现 `20261105T181308Z`（`duration_ms=31526` 含首次启动重试；
    第二次重建后 `duration_ms=683`），`last_backup_status.json` 为
    `{"ok": true, "stage": "ok", "offsite": {"offsite": "disabled"}, "events": {"seq": {"count": 70, ...}}}`。
  - 只读备份卷：容器 `Exited (1)`、`exit_code=1`，日志
    `OSError: [Errno 30] Read-only file system: '/app/backups/status.1.tmp'` +
    `备份连续失败 1 次，退出码 2`。
  - 源库截断：重试 3 次后 `Exited (1)`，日志
    `backup 失败 exit=1 ... DatabaseError: database disk image is malformed`，
    状态文件 `{"ok": false, "stage": "backup", "exit_code": 1, "error": "... malformed"}`。
  - 恢复该源库后 `restore_verify` 15/15、两容器 `healthy`。
- **未做**：未改 `src/wsim/api/**`、`actions/**`、`sim/**`、`ui/**`。
- **commit**：`（见本提交）`。

---

## [P0 数据安全] 2026-11-06 — 容器内备份/恢复演练 + RPO/RTO 与同盘处置

- **改了什么**
  - `docs/BACKUP-RESTORE.md`：新增 §7-E「容器内备份与恢复演练」，含 `docker ps`、
    `docker inspect` 挂载、备份目录 `ls -la`、恢复前后 `seq`/`integrity_check` 与
    失败退出码的原始输出；§0 结论速览拆出宿主机/容器两套实测数字；§8 RPO/RTO 补
    容器口径并写明「RPO 最坏 = 6 小时 + 单次备份耗时」；§9.1 明确「备份与主库同盘」
    的处置（风险接受 + 上线前必须有异机/异地副本，附 `rsync`/`rclone` 最小做法与验收）；
    §6 恢复步骤补容器内 `docker exec ... restore_verify.py`；§10 补两条容器故障行。
  - `deploy/docker-compose.yml`（单独一笔 `7b8d51b`）：`backup` 服务加
    `entrypoint: ["/bin/sh","-c"]` 与 `healthcheck.disable: true`。
- **为什么改**：此前只做了 `docker-compose config` 静态渲染，属配置校验不是验收。
  运行时演练暴露两个真实缺陷：(a) compose `command` 不覆盖镜像 ENTRYPOINT，
  备份容器实际又起了一套 API+UI，与 `world` 双写同一 `events.sqlite`
  （实测 `UNIQUE constraint failed: events.seq`）；(b) 备份容器继承镜像 HEALTHCHECK
  （查 8020/8030）必然 unhealthy。
- **容器演练（colima 29.5.2 / aarch64，镜像 `xkx/world-sim:w5`，run `p0ctr`）**
  - 世界与备份服务同时在线（修复后）：`xkx-world Up (healthy)`、`xkx-backup Up`；
    `xkx-backup` 挂 `xkx-w5-data -> /app/runs`、`xkx-w5-backups -> /app/backups`，
    备份文件 `stat` 设备号来自卷（非容器可写层）。
  - 容器内备份：`duration_ms=1818.409`、`seq {min:1,max:445,count:445,contiguous:true}`、
    `events.sqlite sha256 c8d4bf78… size 409600`、`backup_api_ms=307.302`。
  - 容器内截断 `events.sqlite`（430080 → 4096 字节）后起服：`Exited (1)`、
    `exit_code=1`、日志 `database disk image is malformed` +
    `[entrypoint] API 未在 30s 内就绪，退出`（`restart: unless-stopped` 下会重启循环，
    `RestartCount` 0→1→2→3）。
  - `docker exec xkx-backup ... restore_verify.py --apply-dir /app/runs/p0ctr`：
    退出码 0、15/15 PASS；起服后 `healthy`、`/api/state` **200**、
    `{"count":461,"min":1,"max":461,"distinct":461,"integrity":"ok"}`（恢复点 445，
    之后 ambient 续写）；玩家持久字段（地点/钱包/初始资产/体力）与备份前一致。
  - RTO（容器，停服→服务可用）：`stop 1135ms + restore/verify/apply 605ms +
    start→available 1435ms = 3174ms`；`restore_verify` 纯校验 624ms。
  - RPO：容器备份 1.818s → 最坏 **6h00m02s**；宿主机 29MB 口径维持 **6h00m07s**。
- **备份文件本身被截断（区别于主库截断）**：`head -c 4096` 截断备份里的
  `events.sqlite` 后跑 `restore_verify`，`ok=false`、`checks_passed=4/8`、
  `failure_reason=events.sqlite_sha256; events.sqlite_size; events.sqlite_integrity;
  events_queryable`，**`EXIT=2`**。
- **已知限制**：容器内数字对应 445 事件 / 400KB 的小体量，线上体量以宿主机 29MB 那组
  为准；备份与主库仍同盘（同机同盘），只做了卷级逻辑隔离，异机副本是上线前置项；
  本机无 buildx，镜像用 `DOCKER_BUILDKIT=0 docker build` 构建。
- **未做**：未改 `src/wsim/api/**`、`actions/**`、`sim/**`、`ui/**`；未回退
  `deploy/Dockerfile` 的 version 元数据（属另一会话，保留原样）。
- **commit**：`7b8d51b`（compose 修复）、`（本提交）`（文档与 CHANGELOG）。

---

## [CI] 2026-11-06 — 并发组按 SHA 隔离，避免并行任务互相取消

- **改了什么**：`.github/workflows/ci.yml` 的 `concurrency.group` 加入 `${{ github.sha }}`，
  并去掉 `cancel-in-progress: true`。
- **为什么改**：本仓库有多个并行任务连续 push master，原先按 `ref` 分组会把前一次 run
  直接取消；实测 `3423aa6`、`d398bf3` 的 run 均被后一次 push 取消，门禁结果不完整。
- **验证方式**：本次 push 的 run 不再被后续 push 取消，两个 job 完整跑完；
  run URL / 耗时 / passed-skipped-failed 见回报。
- **commit**：`（见本提交）`

---

## [文档] 2026-11-06 — 发布/回滚手册与提交规范

- **改了什么**：
  - 新增 `docs/RELEASE-ROLLBACK.md`：发布步骤、回滚步骤（compose 的 `image` 指回上一版 tag）、
    数据兼容性检查清单（`events.sqlite` schema、`content_hash`、令牌表），以及
    “哪些改动需要回滚数据库”的判定表；附 2026-11-06 同卷回滚实测命令与输出。
  - 新增 `docs/COMMIT-CONVENTION.md`：每次提交必须一条 CHANGELOG 条目、显式 pathspec、
    禁止 `git add -A` / `git add .`、tag 只增不改、提交前检查表与 CHANGELOG 模板。
- **为什么改**：把“发布可退回上一版”从口头约定变成可执行步骤与判定表；把变更记录纪律前置到提交时，
  避免多任务并行时用宽 pathspec 把无关文件卷进提交（本轮已实际发生一次，见 commit `0e1d685`）。
- **验证方式**：按手册命令完成同卷回滚实测，7/7 PASS（启动、`/api/state` 200、事件可读、
  `seq` 连续、旧令牌有效、`content_hash` 不变、回滚后继续写），输出见
  [docs/RELEASE-ROLLBACK.md](docs/RELEASE-ROLLBACK.md) 第 6 节；文档内命令与
  `tools/build_image.sh` 实际输出一致。
- **commit**：`（见本提交）`

---

## [安全 P0] 2026-11-06 — `/api/health` 改为真实健康检查（200/503）

- **改了什么**
  - 新增 `src/wsim/api/health.py`：`HealthProbe` 每次请求真实检查并返回快照。
  - `src/wsim/api/server.py`：`/api/health` 改走探针，返回 `200`/`503`；保留既有键
    `ok/mode/readonly/profile_version/upstream_ok`，新增
    `storage:{events_ok, seq_gap, disk_free_mb, disk_free_pct, last_commit_age_s, integrity}`
    与 `world:{run, tick, new_world}`。
  - 判定：`EventStore.count()`（COUNT(*) 缓存）与 `EventTail.last_seq`（MAX(seq) 缓存）
    不一致 → `seq_gap>0`、`events_ok=false`（O(1)，不 `read_all`）；`integrity_check`
    60 秒缓存；磁盘剩余 <5% 或写者 60 秒无提交 → 503。阈值可由
    `XKX_DISK_MIN_FREE_PCT` / `XKX_WRITER_IDLE_MAX_S` / `XKX_INTEGRITY_CACHE_S` 覆盖，
    `create_server` 也可显式注入（测试用）。
  - `docs/OPS-RUNBOOK.md` §10.2：判定口径与响应样例。
  - `tests/test_w1_health_real.py`：正常 200、磁盘阈值 503、seq 断洞 503、写者停滞 503。
- **为什么改**：原 `/api/health` 硬编码 `ok:true`，库丢了/写者卡死也报健康，探活与
  监控形同虚设。
- **验证方式**（原样输出）
  - 正常：`(200, {'ok': True, 'storage': {'events_ok': True, 'seq_gap': 0,
    'disk_free_mb': 1155289, 'disk_free_pct': 60.56, 'last_commit_age_s': 0.0,
    'integrity': 'ok'}, 'world': {'new_world': True, 'run': 'hok', 'tick': 0}, ...})`
  - 磁盘阈值（mock 100%）：`(503, {'ok': False, 'storage': {'disk_free_pct': 60.56, ...}})`。
  - seq 断洞（把末条事件 seq 抬高 5）：
    `(503, {'ok': False, 'storage': {'events_ok': False, 'seq_gap': 5, 'integrity': 'ok', ...}, 'world': {'new_world': False, 'run': 'hgap', 'tick': 0}})`。
  - `python -m pytest tests/test_w1_health_real.py -q` → 4 passed。
- **commit**：`（见本提交）`

---

## [安全 P0] 2026-11-06 — 丢库 fail-closed：有账号但事件库缺失/损坏时拒绝启动

- **改了什么**
  - 新增 `src/wsim/api/world_guard.py`：启动前只读检查 `service.sqlite` 账号数
    （`users` 表）与 `events.sqlite` 状态（是否存在、事件数、`PRAGMA integrity_check`）。
  - `src/wsim/api/__main__.py`：在创建 `WorldSession` **之前**执行守卫；失败时向 stderr
    打单行 JSON 并以退出码退出：丢库 **4 `events_missing`**、损坏 **5 `events_corrupt`**
    （内容包哈希不符仍是 3）。新增 `--allow-new-world`：只放行“丢库开新世界”，
    不放行损坏库。
  - 首次创世写 `runs/<run>/world_id.json` 世界身份标记（run_id / content_hash / seed /
    created_at）；启动日志新增 `"world": "new" | "existing"` 与 `allow_new_world`。
  - `docs/OPS-RUNBOOK.md` 新增第 10 节（启动矩阵、拒绝日志样例、`--allow-new-world` 边界）。
  - `tests/test_w1_world_guard.py`：四场景（全新目录、有账号无库、有账号损坏库、显式覆盖）。
- **为什么改**：卷挂错或误删 `events.sqlite` 时服务会静默重建创世并返回 `ok:true`，
  等于清档且无人察觉。
- **验证方式**（原样输出）
  - ① 全新空目录：`startup: {"ok": true, ..., "world": "new", "allow_new_world": false}`，
    `world_id.json: {"content_hash": "7ec670c1...", "run_id": "fresh", "seed": 42}`。
  - ② 有账号 + 无事件库：`exit: 4`，
    `stderr: {"accounts": 1, "code": "events_missing", "exit_code": 4, "ok": false, "reason": "检测到已有账号数据但事件库缺失，疑似卷挂载错误；如需开新世界请显式 --allow-new-world", ...}`。
  - ③ 有账号 + 损坏事件库：`exit: 5`，
    `stderr: {"code": "events_corrupt", "exit_code": 5, "reason": "事件库 integrity_check 失败（error: file is not a database）；拒绝启动，请从备份恢复 events.sqlite。"}`。
  - ④ 显式 `--allow-new-world`：`startup: {..., "world": "new", "allow_new_world": true}`；
    `/api/health` → `{"ok": true, "world": {"new_world": true, "run": "renew", "tick": 0}, ...}`。
- **commit**：`（见本提交）`

---

## [安全 P0/P1] 2026-11-06 — 请求体/头部限制、超时、CORS 同源默认 + 依赖漏洞扫描

- **改了什么**
  - `src/wsim/api/server.py`：
    - `Content-Length` 上限 8KB，超限 **413 `payload_too_large`**
      （`XKX_MAX_BODY_BYTES` 可覆盖）；
    - 请求头总字节数上限 8KB，超限 **431 `header_too_large`**
      （`XKX_MAX_HEADER_BYTES` 可覆盖）；
    - 连接读取超时（默认 30s，`XKX_REQUEST_TIMEOUT` 可覆盖），超时 **408 `request_timeout`**；
    - CORS 默认**不出 CORS 头**（只允许同源）；`XKX_CORS_ORIGIN` 配置后下发
      `Access-Control-Allow-Origin/Methods/Headers` 与 `Vary: Origin`，并支持 `OPTIONS` 预检（204）。
  - `tests/test_w1_http_hardening.py`：413、431、CORS 默认/覆盖、408 五项。
  - `reports/security/audit-2026-11-06.json`：`pip-audit -r requirements.txt` 原始报告。
  - `SECURITY.md` 新增第 6 节“依赖漏洞扫描（每次上线前）”：工具、命令、结论与不可修项处置口径。
- **为什么改**：上线前 P0/P1：无限请求体可撑爆内存；跨源默认放开等于把鉴权端点暴露给任意网页；
  依赖漏洞需要可追溯的扫描基线。
- **验证方式**
  - `python -m pytest tests/test_w1_http_hardening.py -q` → 5 passed。
  - `python3 -m pip_audit -r requirements.txt -o reports/security/audit-2026-11-06.json --format json`
    → `No known vulnerabilities found`（扫描 `pyyaml 6.0.3`、`pytest 9.2.0`、`pluggy 1.7.0`、
    `iniconfig 2.3.1`、`pygments 2.21.0`）。可修项与不可修项均为 0。
- **commit**：`（见本提交）`

---

## [安全 P0] 2026-11-06 — 登录限流防爆破 + 令牌吊销 + 审计

- **改了什么**
  - `src/wsim/api/auth.py`：新增 `login_failures` / `login_locks` / `audit_log` 表；
    `login()` 增加 `ip` 参数，按 `(ip, account)` 统计失败：5 分钟窗口 5 次失败后按
    15 分钟 → 1 小时 → 24 小时指数退避；锁定期内（含正确口令）返回 `too_many_attempts`；
    锁定判定在 PBKDF2 之前，成功登录清零失败与退避级别。
  - 新增 `clear_login_locks()`（运维解锁，可按账号/IP 过滤）与
    `revoke_sessions()`（按账号或全部吊销，`status=revoked`）；
    `resolve_token` / `refresh` / `reconnect` / `disconnect` 对 `revoked` 一律
    `auth_invalid_token`（不是 `auth_expired_token`）。
  - 吊销与解锁写 `service.sqlite` 的 `audit_log`（同一事务提交，随库备份，无需日志轮转）。
  - `src/wsim/api/server.py`：`_AUTH_STATUS` 增 `too_many_attempts: 429`；
    登录传 TCP 对端 IP（不信任 `X-Forwarded-For`）；429 响应带 `Retry-After` 头与
    `retry_after` 字段。
  - 新增 `tools/unlock_account.py`（`--account` / `--ip` / `--all`）与
    `tools/revoke_sessions.py`（`--account` / `--all`）。
  - `docs/AUTH-API-W1.md` 增第 6/7 节（限流规则、解锁与吊销用法、审计选址理由）。
- **为什么改**：上线前 P0：无登录限流等于开放爆破；无吊销能力意味着令牌泄露/离职
  账号无法即时止损。
- **验证方式**（`python -m pytest tests/test_w1_auth_hardening.py tests/test_w1_auth.py
  tests/test_w1_http_auth.py tests/test_w1_capacity.py -q` → 25 passed）
  - 50 次错误口令（真实服务 + 真 service.sqlite）：
    `first4=[(1,401),..]`、`5th=(5,429,'too_many_attempts','900')`、
    `50th=(50,429,...)`、`statuses={401:4,429:46}`、`retry_after_values=['900']`；
    锁定期内正确口令 `429 retry_after=900`。
  - 换 IP：`ipA correct too_many_attempts` / `ipB correct True`。
  - 运维解锁：`unlock: 0 unlocked scope=u_demo locks=1` → `login-after-unlock: 200 True`。
  - 吊销：`revoke: 0 ... sessions=1` → `me-after-revoke: 401 auth_invalid_token`、
    `write-after-revoke: 401 auth_invalid_token`；
    `audit_log: [('login_lock','u_demo','level=1 backoff=900s'),
    ('unlock_account','u_demo','locks=1'), ('revoke_sessions','u_demo','revoked=1 scope=u_demo')]`。
- **commit**：`（见本提交）`

---

## [安全 P0/P1] 2026-11-06 — HTTP 指标端点 `GET /api/metrics`（Prometheus 文本）

- **改了什么**
  - 新增 `src/wsim/api/metrics.py`：进程内 `HttpMetrics` 累加器与 Prometheus 0.0.4 渲染。
    counter：`xkx_http_requests_total{path,code}`、`xkx_http_rejections_total{code}`、
    `xkx_events_total`、`xkx_ambient_paused_total`、`xkx_llm_calls_total`；
    gauge：`xkx_world_tick`、`xkx_players_online`、`xkx_db_bytes{file}`；
    histogram：`xkx_http_request_duration_seconds{path}`，分桶
    `0.005/0.01/0.025/0.05/0.1/0.25/0.5/1.0/+Inf`。
  - `src/wsim/api/server.py`：`do_GET`/`do_POST` 包一层 `perf_counter` 计时并在出口记账；
    `_send` 对 `ok=false` 响应采集业务原因码；新增 `_handle_metrics` 与 `_send_text`；
    `create_server` 新增 `metrics_public` / `metrics_token`（默认按绑定地址推断：
    回环匿名可抓，非回环无 `XKX_METRICS_TOKEN` 一律 404）。
  - `docs/HTTP-METRICS.md`：指标清单、鉴权矩阵、Prometheus 抓取配置与实测数据。
  - 采集不进入写事务、不持有会话锁；唯一锁只保护内存 dict，临界区无 IO。
- **为什么改**：上线前 P0/P1 需要可观测性；没有指标就无法做容量与故障判断。纯新增端点，
  不改任何既有响应。
- **验证方式**
  - `python -m pytest`：`536 passed, 18 skipped`（全量，含本笔新增
    `tests/test_w1_metrics.py` 6 项：Prometheus 行格式正则校验、未授权 404、
    token 闸门、public 忽略 token、路径归一化、直方图累积桶）。
  - `/api/metrics` 自身延迟（顺序 300 次）：
    `{"phase": "sequential", "n": 300, "p50_ms": 0.405, "p95_ms": 1.395, "p99_ms": 2.801, "max_ms": 3.571}`
    → P95 = 1.395ms（要求 < 10ms）。
  - 50 并发 10s 抓 `/api/metrics`：`requests=25921, rps=2161.1, errors=21, p50=2.545ms`
    （并发排队 P95 55.3ms，与 `/api/state` 同口径压测同量级，非端点自身成本）。
  - `/api/state` 50 并发 30s，同口径前后对照：
    基线 HEAD `p95=43.09ms, requests=?, errors=25`；加指标后两次
    `p95=52.91ms (78686 req, 2517.9 rps, errors=31)` 与
    `p95=37.45ms (98615 req, 3142.5 rps, errors=13)`，落在基线同一波动区间内。
- **commit**：`（见本提交）`

---

## [部署] 2026-11-06 — 镜像版本化（label + /app/VERSION）与双 tag 构建

- **改了什么**：
  - `deploy/Dockerfile` 接受 `--build-arg XKX_VERSION` / `XKX_REVISION`，写入
    `org.opencontainers.image.version` / `revision` 两个 label 与 `/app/VERSION` 文件
    （`/api/health` 未改，避免动 API 逻辑）。
  - 新增 `tools/build_image.sh`：版本默认取 `git describe --tags --always`，一次构建
    `xkx-world-sim:<version>` 与 `xkx-world-sim:latest`，输出 version / revision /
    image_id / digest / `/app/VERSION`。
- **为什么改**：发布与回滚必须能精确指向某一镜像，且容器内外都能零依赖确认版本。
- **验证方式**：`tools/build_image.sh rb-v1` / `rb-v0` 构建成功，label 与 `/app/VERSION`
  一致；同卷回滚实测（当前版 `6c84cb0` → 上一版 `af4b64c`）7/7 PASS，完整命令与输出见
  [docs/RELEASE-ROLLBACK.md](docs/RELEASE-ROLLBACK.md) 第 6 节。
  说明：Dockerfile 的版本注入部分因并行任务提交 `0e1d685` 的宽 pathspec 提前进入 master，
  本提交补齐构建脚本；两处内容一致。
- **commit**：`（见本提交）`

---

## [CI] 2026-11-06 — GitHub Actions 门禁：pytest / selfcheck / 内容校验 / 开启态 2 日冒烟

- **改了什么**：新增 `.github/workflows/ci.yml`，在 push 到 `master` 与 `pull_request` 时并行跑两个 job：
  - `tests`：Python 3.11 + `PYTHONPATH=src`，`python -m pytest -q`（step 超时 15 分钟）；
    关闭态与 `XKX_CONTENT_PACKS=scaleup_w3` 开启态各跑一次 `content/validate_content.py`；
    再用 `sim/run_headless.py --days 2 --seed 42 --npc 0` 跑开启态 2 日无头冒烟（不启用 LLM）。
  - `selfcheck`：跑 `./scripts/selfcheck.sh`（内容校验 + pytest + framework + 玩家闭环）。
  - pip 依赖用 `actions/setup-python` 的 pip 缓存（按 `requirements.txt` / `requirements.lock` 命中）；
    失败时把 `reports/`（含 `reports/ci/*.log`）上传为 artifact，保留 7 天。
- **为什么改**：上线前 P1：任何一次 push 都必须自动验证，不能只靠本地手动跑；
  CI 与本地用同一套离线用例，LLM 测试走 `tools/fake_llm_server.py`，不访问外网、零模型调用。
- **验证方式**：本地 `python -m pytest -q`（536 passed / 18 skipped，工作区含并行任务改动）；
  `./scripts/selfcheck.sh` 4/4 PASS（内容校验 / pytest / framework 6/6 / 玩家闭环）；
  `content/validate_content.py` 关闭态与 `scaleup_w3` 开启态均 PASS；
  开启态 2 日冒烟 `events=1612`、`npc=60`、`content_hash=3fd99946…`、`illegal_rate=0.0`；
  GitHub Actions 首跑由本次 push 触发，run URL 与耗时见提交回报。
- **commit**：`（见本提交）`

---

## [P0 数据安全] 2026-11-06 — 在线备份 / 恢复校验 / 轮转 / 定时

- **改了什么**
  - 新增 `tools/backup_snapshot.py`：`sqlite3.Connection.backup`（分页 1024 pages、
    `sleep=0`、源连接 `PRAGMA query_only=1`、目标 `journal_mode=DELETE`）在线备份
    `events.sqlite` 与 `service.sqlite` 到 `backups/<UTC 时间戳>/`，写 `manifest.json`：
    每个文件 `sha256` / `size_bytes` / `integrity_check`、events `seq` 的
    `min/max/count/distinct/holes/contiguous`、`content_hash`、重放得到的
    `state_hash` / `event_hash`、`created_at`、`source_paths`、`backup_api_ms/steps`。
    备份目录 `700`、文件 `600`。
  - 新增 `tools/restore_verify.py`：恢复到临时目录后逐项校验（文件存在 / sha256 /
    大小 / `integrity_check=ok` / seq 连续且 min-max-count 与 manifest 一致 /
    `state_hash` / `event_hash` / 备份 `content_hash` 与 manifest 一致 /
    与当前 `content/` 一致），**任一失败退出码 `2`**；可选 `--apply-dir` 在停服后装回，
    装回前清除目标端陈旧 `-wal`/`-shm`。
  - 新增 `tools/backup_impact.py`：20 并发读 + 10 并发写对照压测（`--auth on/off`、
    `--duration`、`--tag`），用于实测备份对写路径的影响。
  - 新增 `scripts/backup_loop.sh`：默认 `BK_INTERVAL=21600`（6h）、启动先备份一次、
    `TERM/INT` 可中断；`BK_INTERVAL=10` 实测 5 份目录间隔 11/11/10/10s。
  - 轮转（常量写在 `backup_snapshot.py`）：保留 `KEEP_ROLLING_HOURS=48` 内全部 +
    `KEEP_DAILY_DAYS=7` 个 UTC 日每日最新 1 份；只识别 manifest 里
    `generator=tools/backup_snapshot.py` 的自生成目录，目标必须是 `backups/` 的直接子目录，
    **拒绝带符号链接的目标**，删除失败记 `prune.skipped` 不中断备份。
  - `deploy/docker-compose.yml` 新增 `profiles: ["backup"]` 的 `backup` 服务
    （复用 `xkx/world-sim:w5`，循环调 `backup_snapshot.py`）与命名卷
    `xkx-w5-backups`；`deploy/Dockerfile` 加 `COPY tools /app/tools` 并预建 `/app/backups`；
    `.gitignore` 加 `backups/`。
  - 新增 `docs/BACKUP-RESTORE.md`；`docs/OPS-RUNBOOK.md` §3/§4 改写为工具口径，
    新增 §9「P0 备份与恢复（工具化，实测）」。
- **为什么改**：`events.sqlite` 是唯一事实源（事件日志 + `meta.content_hash`），
  此前没有任何备份手段；`cp` 在 WAL 模式下会漏 `-wal`/`-shm`，必须用在线备份 API。
  调度选 compose profile 而非 entrypoint 开关：entrypoint 在 API 进程内做定时器会与
  `WorldSession` 抢锁、且停 API 就停备份；独立 profile 容器与主服务生命周期解耦、默认不启动。
  理由与对比写在 `docs/BACKUP-RESTORE.md` §5.2。
- **实测（原始输出见 `docs/BACKUP-RESTORE.md` §7–§8）**
  - 备份耗时 **2.46–6.29s**（29MB / 29.4k 事件），RPO ≈ **6h00m07s**；演练 B 实际丢失
    52s / 20 条事件（seq 29439–29458）。
  - RTO **6.510s**（restore+verify 5.002s + API 启动到 `/api/state` 200 为 1.505s）。
  - Drill A：备份 `seq max=29438` → 移走 `events.sqlite` → `restore_verify` 15/15 通过 →
    起服 `/api/state` **200**、玩家状态子集逐字段一致、seq 连续。
  - Drill B：`head -c 4096` 截断 → `sqlite3` 退出码 **11**、API 退出码 **1**
    （`store.py:43 database disk image is malformed`）→ 恢复 15/15 → `/api/health ok=true`。
  - Drill C：恢复后新增 20 个动作，`count=distinct=max=32600`，seq 32581–32600 接续无重复。
  - Drill D：基线/备份 4 轮交替对照，写 P95 基线中位 **376.7ms**、备份期间 **319.2ms**
    （备份组不高于基线）；存储层探针：备份 6.289s 期间 310 次写，**最大写延迟 31.252ms**
    （另一轮 8.226ms），远低于 200ms 门槛。
- **已知限制**（详见文档）：备份与主库同盘（丢盘两者一起丢，需要异地/对象存储副本）；
  备份未加密（含账号 PBKDF2 哈希与令牌，目录 700、文件 600）；`state_hash` 重放成本随
  事件数线性增长，会拉长备份与 RPO；`content/` 升级后旧备份无法直接启动（退出码 3，属预期）；
  只做了 `--profile backup config` 渲染校验，**未做容器运行时演练**。
- **未做**：未改 `src/wsim/api/**`、`actions/**`、`sim/**`、`ui/**`。
- **commit**：`0e1d685`（工具与脚本）、`（本提交）`（文档与 CHANGELOG）。

---

## [功能] 2026-11-06 — 玩家门派 P1：立派 / 收徒 / 日结 / 金库（动作层，未接线）

- **改了什么**
  - 新增 `specs/player-sect/`：`spec.md`（门槛、属性、收徒、治理、钱流、原因码、验收链、P1/P2）、
    `tasks.md`（实现与接线清单）、`checklist.md`。
  - 新增 `src/wsim/actions/player_sect.py`：`PlayerFoundSectAction`、`PlayerRecruitDiscipleAction`、
    `PlayerExpelDiscipleAction`、`PlayerPayDisciplesAction`、`PlayerSectDepositAction`、
    `PlayerSectWithdrawAction`；纯 `preconditions` + `effects`，复用 `world/effects.py` 原语。
  - 新增 `tests/test_player_sect_actions.py`（24 条）。
  - `docs/player-api.md` 新增 §13「未接线：门派动作设计」，并在 §11 记录 `1.2`。
- **关键数值**：立派 800 文（注册费 200 → 官仓 + 启动金 600 → 门派金库）、声望 ≥ 20、单人立派；
  收徒好感/信任 ≥ 30、目标技能 ≥ 0.2、掌门技能 ≥ 0.4；草创期弟子上限 3；
  会费 5 文/日/徒、俸禄 10 文/日/徒、欠饷 ≥ 30 文当日离派；提取单次 ≤ 500、留 100 保底。
- **为什么改**：NPC 的 2500 文 / 声望 40 / 5 名弟子依赖未对玩家开放的 `apprentice`，单人玩家
  无法在立派前凑齐 5 人；玩家初始 300 文、日净结余约 30–40 文，2500 文会把立派推到玩法末期。
  玩家口径改为 800 文 + 单人立派，并保留“先立派、再收徒”的正向循环。
- **钱流纪律**：所有金额走真实 `transfer` / `debt_*`；P1 不 `mint`、不 `burn`
  （注册费改真实转入 `treasury:region`）；欠付写 `unpaid_dues` / `unpaid_stipend` 债务，离派不销债。
- **验证方式**：`python -m pytest tests/test_player_sect_actions.py -q` → 24 passed；
  `python -m pytest -q` → 全量 554 条收集、全部通过（18 skip）。
  事件链单测用 `Writer + EventStore` 跑出 `found_sect → recruit_disciple ×2 → pay_disciples ×3`，
  第 2 日日结出现 `faction=null` / `master=null` / 双向关系 `ended` / 记忆「欠饷30文离派」，
  且整链 `minted_total`、`burned_total` 不变、`conservation_error == 0`。
- **未做**：未改 `actions/registry.py`、`actions/player.py`、`api/session.py`、`content/**`；
  API 仍拒绝调用，接线点见 `specs/player-sect/tasks.md`。
- **commit**：`（见本提交）`（spec / 动作层+测试 / 文档各自成笔）。

---

## [安全] 2026-11-06 — 历史凭证口径修正：已知风险 + 触发条件

- **改了什么**：`SECURITY.md` §1 由“必须吊销/轮换、禁止复用、吊销前不得公开”改为
  “已知风险 + 风险接受 + 触发条件”：
  - 事实：命中于 `2fc3373`（`spec.md`）与 `b0f6dbf`（`reports/p3d-report.md`）；
    当前 HEAD 无该凭证；仓库为 **PRIVATE**；历史未改写（改写会移动全部 15 个已发布 tag）。
  - 现状：签发方尚未轮换，持有人本机持续使用该 key，**风险由持有人接受**。
  - 触发条件：仓库转为公开、新增协作者、或任何 CI/外部服务获授权拉取该仓库时，
    **必须在放行前完成轮换**，并把完成时间补记在 SECURITY.md §1。
  - §2–§4 同步改写口径；§5“凭证纪律”的禁止落盘 / 摘要 / `.gitignore` 规则不变，
    仅第 4 款“已入库的…先吊销”改为同一口径（不要求轮换，触发条件成立时才轮换）。
  - 同口径同步到 `docs/GIT-WORKFLOW.md` §8：去掉“发现泄漏先吊销/轮换”的硬性要求，
    保留“禁止提交凭据”与 `.gitignore` 规则；tag 口径按实测修正为 **16 个且均已推送**
    （15 个 `delivery-*` + `p2b-baseline`，`origin` 与本地同名 tag 指向同一对象）；
    轮换仅在转公开 / 新增协作者 / CI 或外部服务获授权拉取时成为放行前置。
- **为什么改**：该 key 是持有人本机日常网关 key，仓库私有且仅本人可读；轮换会直接打断工作，
  当前收益接近零，故由持有人接受风险，仅保留触发条件作为硬闸门。
- **验证方式**：`git diff` 通读无 key 原文（只写提交哈希与文件名）；
  `SECURITY.md`、`CHANGELOG.md`、`docs/GIT-WORKFLOW.md` 三处口径逐条比对一致
  （均为“已知风险 + 风险接受 + 触发条件”，无“必须吊销”的相反表述）。
- **commit**：`337446b`（`SECURITY.md` + `CHANGELOG.md`）、`0258f15`（`docs/GIT-WORKFLOW.md`）。

---

## [修复] 2026-11-05 — NPC 进玩家店消费：营收恒 0 的机制修复

- **改了什么**
  - `actions/common.py`：新增 `is_player_business` / `stable_slot`（FNV-1a 32 位，纯函数）/
    `player_market_active` / `business_order` / `selling_shops`；`business_selling` 改为
    “同货卖家轮转”，`business_with_cash` 直接轮转店铺。轮转键为
    `(地点, 物品, 买主, 世界日)`，无 `random`、无墙钟；该地点无玩家店时返回原顺序。
  - `actions/basic.py` `ConsumeAction`、`actions/commerce.py` `BuyAction` / `SellAction`：
    卖家/买家解析传 `salt=intent.actor` 与 `day_of(intent.tick)`，玩家店与 NPC 店同权。
  - `sim/intent.py` `_propensity_buy`：玩家市场开启后按“同货卖家”决定买谁的，
    食物可作口粮购买（与午饭 `consume` 分开记账），库存门槛降为 1 件；
    新增 `_player_shop_trip`：午饭后在 ≤2 跳的玩家店按 `(npc, 世界日)` 稳定散列
    （约 32% 的日子）专程进店买一件。
  - 无玩家店的世界完全走旧分支（顺序、门槛、排除口粮都不变）。
- **为什么改**
  - 旧 `state.business_at()` 按 id 升序，`biz:*` 恒排在 `shop:player:*` 前；同 item 全店同价，
    平局时 `business_selling` / `cheapest_food` / `_propensity_buy` 总选 NPC 店 →
    玩家店营收恒 0；`_propensity_buy` 还整体排除 food 且要求 `qty>=3`，摊位无货可卖。
- **验证方式**
  - `tests/test_player_shop_customers.py`（7 例）：轮转覆盖玩家店且 NPC 店仍占首位、
    同货份额分配、最后一件可卖、口粮可买、专程 move+buy 同 tick、NPC 卖货给玩家店、
    无玩家店选取与旧算法一致。
  - 10 日 seed42（无玩家店）对照 `2bd5c16`：`events 3084`、`state_final 68b05948…`、
    `event_final 73026568…`、`content_hash 7ec670c1…`、K1 `62/91=68.13%` 全部一致。
  - 10 日 seed42（集市注入玩家店面）：`buy` 156（NPC 141 / 玩家 12）、`consume` 318（NPC 313 / 玩家 5）、
    `sell` 123（NPC 118 / 玩家 5）；NPC 店合计 572 笔（基线 547 笔），未被挤死；
    玩家店 10 日营收 65 文（day0 30、day1 30、day5 5）。
  - 同 seed 玩家店世界两跑：`events 3257`、`state_final 6af9fb93…`、`event_final ad9f5287…` 一致。
  - 真实服务 3 日（ambient 4x，集市摊位 + 雇 1 人，`llm_calls=0`）：
    day3 营收 23 / 7 笔、day4 营收 8 / 2 笔、day5 营收 5 / 1 笔，日均 >0；
    买主含 liu_sanniang / wei_changgeng / gu_qingya / lu_xinghua / shi_gandang / duan_wudou，
    其中 flatbread 既有 `consume`（午饭）也有 `buy`（添置口粮）。
  - 全量 `pytest`：524 collected / 18 skipped / 0 failed（旧 V3/V4/V5 golden 保持 skip，
    V6 无玩家店世界未漂移）。
- **commit**：`（见本提交）`（`content_hash` 关闭态 `7ec670c1…`，开 `scaleup_w3` 未改）

---

## [功能] 2026-11-05 — 玩家婚姻 UI：求婚入口、配偶栏、和离与配偶对话标记

- **改了什么**
  - `ui/player/index.html` / `app.js` / `style.css`：商业面板新增「婚姻」页签（六页签）；列当前地点可求婚 NPC，
    显示好感、信任、婚姻状态与同地点；表单可选聘礼金额与信物（信物下拉只列背包真实持有物品），提交
    `marriage_propose {target,gift_money,gift_item,gift_qty}`。
  - 裁决呈现：接受显示「与 X 成婚，tick N」并刷新 `state.marriage`；「再处处看」不扣聘礼/信物；
    婉拒按原因码映射成中文（`bond_too_low`/`trust_too_low`/`gift_missing`/`not_enough_money`/`already_married`/
    `target_in_couple`/`not_together`/`self_propose`/`spouse_not_here` 等），不显示英文 code。
  - 配偶常驻区：`status=married` 时固定显示「配偶：姓名 · 成婚 tick N」，并显示当日陪伴口径
    （优先 `last_daily_tick/daily_note`；缺失时按 `nearby` 同地点近似并在块内注明）。
  - 和离：二次确认弹层写明「聘礼不退还、共同财产不分割」，提交 `marriage_end {}`，成功回单身并隐藏配偶块。
  - 配偶对话：NPC 列表、对话目标、执行结果与回应均标「（配偶）」并高亮；服务端 `/messages` 的 `from`
    是 NPC 实体 id，回复展示前补一层姓名解析，避免界面出现 `liu_sanniang` 这类内部 id。
  - 占位收窄：未开放页只保留「立派 / 收徒」；`REASON_TEXT` 增加 `no_payer_funds`、
    `cannot_rest_while_escorting`、`cannot_rest_now`、`bad_arg:hours`；「休息」按钮先做占位（提示体力恢复中、不发请求）。
  - 文档：`docs/PLAYER-GUIDE.md` 新增「如何求婚 / 如何和离」（门槛、聘礼与信物规则、和离不退款、口径与限制）。
- **为什么改**：后端婚姻闭环已上线，前端仍把结婚放在「未开放」占位；玩家没有求婚入口，已婚状态与配偶日常也看不见。
- **验证**
  - `python3 tools/ui_check.py`：131 checks / 0 failed；其中 `--only marriage` 19 checks / 0 failed
    （证据 `reports/w4-marriage/mock-marriage.json` 与 mock 截图）。
  - `python3 tools/ui_check_proxy.py`：12 checks / 0 failed。
  - 真服务（`--auth off` + `XKX_AMBIENT=1`、`XKX_AMBIENT_SPEED=4`）：`tools/ui_check_real_marriage.py`
    走 UI 真实 talk 37 次跨 8 个世界日把好感提到 60/信任 77 → 求婚 accept（成婚 tick 1023）→ 配偶栏 →
    配偶对话标记 → 和离（tick 1035）；15 checks / 0 failed，截图与每步请求/响应在
    `reports/w4-marriage/real-marriage.json` 与 `real-*.png`。
  - `python3 -m pytest -o addopts="" -q`：499 passed / 18 skipped / 0 failed。
- **commit**：`（见本提交）`

---

## [功能] 2026-11-05 — 玩家店铺与在场 NPC 视图补齐

- **改了什么**
  - `GET /api/player/state` 顶层 `shop` 保留既有键，新增 `employees[]`（`name/position/wage/present/unpaid_wage`）、
    `cash`、`stock_count`（件数合计，排除 `escrow:` 托管键与 `qty<=0`）、`stock[]`
    （`item/name/qty/sell_price`）、`today_revenue`、`today_wage`、`today_tax`、`tax_arrears`、
    `last_settle_tick`（`tick - tick % 144`）。玩家未开店时形状稳定：空数组与 0。
  - `present_npcs[]`（以及 `nearby` 别名）新增 `skills`（最高技能及等级）、`employed_by`
    （`employed_at` 优先，否则 `business`）、`bond`（`affection_between`）、`trust`
    （双向 active 关系最大 `trust`，无关系给 0）。
  - `api/session.py`：派生计算放在 `_shop_view` / `_present_view`；`_present_view` 纳入既有
    按 tick 的 `_view_cache`（缓存由三元组扩为四元组），同一 tick 重复读不再重复构造。
  - 文档：`docs/player-api.md` 新增 §3.2 字段表；契约只增不改，`profile_version` 仍为 1。
- **为什么改**：UI 商业/经营面板拿不到雇工名单、岗位、日薪、到店与欠薪，也看不到自己的
  现金、可售件数与当日经营数字；在场 NPC 面板缺技能、受雇归属与好感/信任。
- **验证**
  - 新增 4 条 `tests/test_player_commerce.py`：店铺字段与件数口径、未开店空形状、
    在场 NPC 技能/受雇/关系、同 tick 缓存复用。
  - 全量 `python -m pytest -o addopts="" -q`：499 passed / 18 skipped / 0 failed。
  - P95（同一脚本 `/tmp/p95_compare.py`，`--auth off`、`XKX_AMBIENT=0`，端口 8140，
    同一基线 `f150ade`（临时 worktree）run `p95_v3_base_clean` → 本提交 run `p95_v3_after_final`；
    顺序 100 次、50 并发 250 次）：
    顺序 `/api/player/state` 0.92 ms → 1.02 ms；顺序 `/api/state` 0.31 ms → 0.40 ms；
    50 并发 `/api/player/state` 123.64 ms → 123.84 ms；50 并发 `/api/state` 31.87 ms → 35.80 ms
    （并发世界态绝对值受机器负载影响较大，多次运行波动明显）。
- **commit**：`8ff784e`

---

## [修复] 2026-11-05 — 店铺日结按实际出勤（缺勤 0 工资 0 产出）

- **改了什么**
  - `actions/business.py`：`build_shop_settlements` 新增出勤判定——结算日
    `[day*144, (day+1)*144)` 内该雇工有带 `employer=shop:player:*` 的 `work` 事件（且记忆带
    “工资本日由日结发放”标记）记 `present`，否则记 `absent`；`wage=0`、`output_qty=0`、
    不产生欠薪债，行内带 `absent`/`present_count`。
  - `shop_settlement_effects`：缺勤行写 `system_shop_settle` 明细
    “某雇工当日未到店，工资0文、产出0件”，并给店主、雇工各写一条记忆后跳过计薪/产出。
  - 新增 `settled_day_of(tick)`：headless 在当日最后一 tick、shared/ambient 在新日首个 tick
    结算，统一推断被结算的世界日，两条路径判定一致。
  - `actions/system.py`：日界把 `intent.tick` 传给结算，保证按真实结算日判定出勤。
  - 测试：`tests/test_shop_operations.py` 新增缺勤用例（1 出勤 1 缺勤 → 只发 1 份工资、
    产出 1 件、无缺勤者债务、店主记忆含“当日未到店”）。
- **为什么改**：旧日结不看雇工是否到店，“人没来、工资照发、产出照给”，账与事实不符。
- **验证方式**
  - 真实 `wsim.api` + ambient 3 世界日（run `live-attend`）：每天都有
    `employer=shop:player:u_live` 的出工事件（day0 85/119、day1 194/276、day2 323/357/398），
    日结 tick 153/289/432 按出勤计薪（18 / 13+欠 5 / 18），产出各 1 件。
  - 缺勤场景（run `live-absent`，日界前签约、雇工当日未到店）：日结 tick 154 明细
    “严守一当日未到店，工资0文、产出0件”+ 雇工记忆，`wage=[]`、`output=[]`、`debt=[]`；
    day1 出工后正常发 18；day2 现金不足发 11、欠薪债 7（欠薪语义不变）。
  - 欠薪债语义不变：只有出勤且现金不足才 `debt_create/increase`。
  - `content_hash` 关闭 `7ec670c1…` / 开启 `3fd99946…` 未变；`llm_calls=0`。

## [修复] 2026-11-05 — NPC 多跳寻路（最短路逐跳 + 移动/动作同 tick）

- **改了什么**
  - `sim/intent.py`：`Planner._move` 改为按 `path_between`（地点邻接图的确定性 BFS）产出整条
    最短路，按 tick 逐跳发 `move`，每跳 `args={"to": 本跳落点, "dest": 最终目的地}`；返回
    `cursor - 1`，让最后一个移动与紧随的 `work`/`consume`/买卖同 tick、先移动后动作。
    跨日段上工前补一次同 tick 通勤 `move(dest=工位)`（已在工位即 0 步空操作）。
  - `actions/basic.py`：`MoveAction` 按 `dest` 从**实际所在地**重算剩余最短路一次补足；
    已到终点时的“原地一跳”/陈旧一跳按 0 步成功（不扣体力、不写记忆），不再报 `not_adjacent`。
  - `sim/intent.py` 给每次决策产出的 `move` 打独立 `intent_id`：玩家动作一次推进 6 tick 后，
    旧计划的同形 move 会被 `Writer` 按 `(actor,tick,verb,args,reason)` 去重吞掉，
    导致“新计划该走却没走、紧随的 work 落空”；不同计划各自生效，同一计划重复提交仍幂等。
  - 测试：新增 `tests/test_move_pathfinding.py`（6 条：路径确定性、漂移自愈、无 dest 仍拒绝、
    陈旧一跳 0 步、原地一跳 0 步、末跳与 work 同 tick）；`tests/test_p3e_golden.py` 新增
    V6 基线（791 events / `03b78469…` / `3be92716…`），旧 V5 基线原样保留并 skip。
- **为什么改**：NPC 移动只支持相邻一跳，跨地点行程（客栈→药铺需经集市）全部
  `move:not_adjacent` 失败，计划落空、看起来在动其实原地空转；`work` 随之
  `not_at_workplace`。这是全世界所有 NPC 的全局行为 bug。
- **验证方式**
  - 10 世界日 headless（seed 42、真实 content、`content_hash 7ec670c1`）对照干净 master：
    事件 2822→3084；`blocked` 46→21、`pruned` 11→4；`move:not_adjacent` 1→**0**、
    `work:not_at_workplace` 3→**0**、`work:insufficient_stamina` 4→**0**；
    `consume:item_not_available` 16→5；NPC 覆盖 31→31；K1 62/91 = 68.13%（护栏 63.13%，
    未破）；到达分布 ferry 119→130、escort_agency 62→69、academy 143→147，
    `ferry`/`escort_agency`/`pharmacy`/`smithy` 出现此前 10 日从未到过的 NPC。
  - 同 seed 两次 2 天 golden：`state_final`/`event_final` 逐字节一致。
  - 真实 `wsim.api` + ambient 3 世界日：`move:not_adjacent` = 0、`work:not_at_workplace` = 0；
    雇工每日出工（tick 85/119、194/276、323/357/398）。
  - `content_hash` 关闭 `7ec670c1…` / 开启 `3fd99946…` 未变；`llm_calls=0`。

---

## [功能] 2026-11-05 — 玩家婚姻闭环：求婚、确定性裁决、婚后日常、和离

- **改了什么**
  - 新增 `actions/marriage.py`：`marriage_propose / marriage_accept / marriage_reject /
    marriage / marriage_end / spouse_time` 六个动作；NPC 裁决为纯规则（好感度/信任/
    配偶/地点/信物/聘礼），零模型调用。接受阈值 60/30；缺口 5–20 进入 3 世界日
    `courtship`；低于门槛按 `bond_too_low` / `trust_too_low` 拒绝。
  - `world/state.py` 新增 `WorldState.marriages`，只在至少一方是玩家时写入，空置不进哈希。
  - `world/effects.py` 新增 `marriage_meta` 原子 effect；`marriage` 仍复用既有 `marry`。
  - `api/session.py` 白名单/别名/`_args`/只读前置校验/`marriage` 快照；成功求婚由服务端
    自动追加 `marriage_accept → marriage`，事件链 seq 连号；拒绝写 `marriage_reject`。
  - `sim/intent.py`：婚后日常放在 `MockReasoner.decide` 入口（不是 `Planner.plan`）。
    内容目标耗尽后 `choose` 返回 None、`plan` 不会被调用，挂在 plan 里的日常永远不触发；
    现在每个世界日对“配偶是玩家”的 NPC 至少一次先 move 到玩家所在地点再 `spouse_time`，
    同 tick 内按队列顺序执行；NPC-NPC 婚姻与无玩家世界不走该分支。
  - `actions/social.py`：`TalkAction` 对配偶返回确定性专属台词（按世界日轮换）。
  - `audit/report.py` 将婚姻相关内核动词登记为 `ENGINE_ONLY_ALLOWED`，不改内容包。
  - 文档：`docs/player-api.md` 新增婚姻动作、状态字段、原因码、多事件 seq 说明；
    `specs/player-marriage/` 含规格、任务与验收证据。
  - 明确非范围：共同财产/聘礼分割；和离不退款、不铸币。
- **为什么改**：NPC 早能结婚，玩家 `marry` 恒返回 `not_implemented_for_player`；
  “世界活着”必须有玩家可参与的婚姻闭环，且婚后要能看到可观测的日常行为变化。
- **验证**
  - 无玩家 headless 基线（seed 42 / 2 日 / 12 NPC）：state_final
    `91ca6236…9d6d`、event_final `eb139d4f…0181`，与实现前逐字节一致（279 事件，
    `specs/player-marriage/evidence/hashes.json`）。
  - 新测试 7 条：裁决边界、完整事件链、聘礼守恒、拒绝原因、求处中间态、
    “无内容目标仍每日 spouse_time”、同 seed 两次 state/event 哈希一致。
  - 全量 `python -m pytest -o addopts="" -q`：488 passed / 16 skipped / 0 failed；
    `./scripts/selfcheck.sh` 4/4 PASS（见 `evidence/pytest.txt`、`evidence/selfcheck.txt`）。
  - 真实服务（`wsim.api --auth off`，run `pm-accept-1`）+ ambient 4x：
    拒绝样例 tick 11/11/17（`not_enough_money` / `gift_missing` / `bond_too_low`）；
    求婚 tick 1026 成功（聘礼 50 文 + 炊饼 1，钱包 300→250）；
    婚后第 15/16/17 世界日 `spouse_time` tick 2204/2312/2479（首日带炊饼，
    `seq` 4332/4388/4473）；重婚拒绝 tick 2485 `already_married`（钱包 240 不变）；
    和离 tick 2485（双方 spouse 清空、关系 ended，钱包 240 不变）；
    对账 `accounts_sum 46059 = minted 47750 − burned 1691`、`delta=0`，seq 1..4478 连续
    （`evidence/acceptance.json`）。
- **commit**：player-marriage 系列提交（spec / 实现修正 / 测试 / 文档与证据各自成笔）。

## [功能] 2026-11-05 — 玩家 `rest`：按小时恢复体力

- **改了什么**
  - `actions/player.py` 新增 `PlayerRestAction`（`rest`）：`hours` 1-8（默认 4，非法 `bad_arg:hours`）；
    体力按地点分级恢复——客栈（`service_room`）2.0/tick、住处 1.5/tick、镇内 0.5/tick、野外 0.25/tick；
    动作推进 `hours × 6` tick，写 `stamina`（`reason=rest`）与 `last_action_tick`。
  - 前置：押镖行程中 `cannot_rest_while_escorting`；休息窗口会跨过已接委托截止 tick
    返回 `cannot_rest_now`。`api/session.py` 放行动词、解析 `hours`、`_advance(step)` 支持按小时推进，
    并补三条原因码文案。
- **为什么改**：审计中 `insufficient_stamina` 出现 38 次且没有恢复手段，是硬死胡同。
- **验证方式**：`tests/test_player_commerce.py` 新增 4 条（客栈恢复与 tick、野外减半、
  非法 hours/押镖中拒绝、委托截止打断）；真实服务 `rest` 证据见本轮回报。
- **commit**：`7bf9702`

---

## [修复] 2026-11-05 — 押镖经济：必须带货 + 委托方真实付款 + 净赚门槛

- **改了什么**
  - `actions/escort.py`：玩家 `escort` 必须带真实货物，空货一律 `item_missing`（precheck 先拒，
    只写 reject 事件，treasury 零支出）；运费从**目的地收货 NPC 的真实钱包**垫付
    （`payer_total = fee + int(fee × agency_cut)`，开单即转入运费托管账户
    `escrow:<镖单id>:fee:<payer>`），找不到付得起的委托方返回 `no_payer_funds`，不垫付、不 mint。
  - 运费门槛 `fee ≥ max(20, ceil(1.15 × cargo_value))` 且 `fee − cargo_value − tax > 0`
    （`cargo_value = item_price × qty`，buy 口径），否则 `fee_too_low`。
  - 结算：成功时托管运费 → 玩家 `fee`、→ 镖局 `agency_cut`，货物 `escort_delivered`
    （托管离场 + 入委托方背包）；失败时 `escort_cargo_return` 原量退货、
    `escort_fee_refund` 运费原额退回委托方（玩家不收款）。`escort_settle` 事件带
    `payer / cargo_value / fee / tax / gross / net_profit / agency_cut`，`net_profit = fee − cargo_value − tax`。
  - `api/session.py`：`_precheck_player_action` 增加空货、门槛、净收益与委托方资金校验；
    新增 `no_payer_funds` 原因码文案。NPC/公账旧订单事件体逐字节不变。
- **为什么改**：第二轮审计实测空货单 tick 1309→1457 +91 文未扣货（等于印钞），
  4 粗布（货值 100）只 +89（净亏 11），且运费由 `treasury:region` 兜底。
- **验证方式**：`tests/test_player_commerce.py` 新增/更新 5 条（空货拒、低运费拒、
  无付款人拒、成功结算三方钱包、3 连单 treasury 净流入）；真实服务验收证据见本轮回报。
- **commit**：`b7c6cfb`

---


## [功能] 2026-11-05 — 玩家商业与经营面板（买/卖/现吃/开店/雇人/押镖）

- **改了什么**
  - `ui/player/` 新增「商业与经营」区块（`#commerceBlock`，顶栏「商业」直达），五个页签：
    商店（可买/可卖/现吃）、经营（摊/店面/分号三档与自有店铺）、雇人、押镖、未开放。
  - 商店：可买行显示名称/单价/含税总价/库存/`buy_limit`，可卖行显示 `sell_price` 与持有数；
    数量为原生数字输入（1-20，受 `buy_limit`/库存/现金约束）；`consume` 一键买一份现吃并显示
    `体力 a → b（±d）· 钱包 c → d 文（±e）`。
  - 经营：三档报价按 `content/economy.yaml` 的 `rent + 初始进货`（345 / 1472 / 3450），资金不足
    按钮禁用并写清缺口；开店后显示店内现金、库存件数、雇工列表（岗位/日薪/到店）。
  - 雇人：列当前地点在场 NPC（好感/技能/受雇），日薪默认岗位市价；服务端拒绝码
    （`relation_too_low`/`skill_too_low`/`wage_too_low`/`wage_too_high`/`shop_full`/
    `target_not_at_shop`/`unknown_position`/`self_recruit`）全部映射中文。
  - 押镖：终点只取服务端 `exits`（相邻校验），货物只列背包真实持有；实时给出预计到手
    （按服务端口径 `fee - int(fee × 30%)`）与到货 tick（`tick + 144`）。
  - 原因码：补齐 `item_not_available`/`not_here`/`shop_closed`/`not_a_merchant`/
    `not_enough_money`/`item_missing`/`not_implemented_for_player`/`bad_arg:cargo_qty` 等；
    未知码兜底不再显示原始 code，错误复查面板也不再露出 code。
  - 未开放玩法（`marry`/`found_sect`/`apprentice`）统一显示「该玩法尚未开放」，结婚入口位置已留出。
  - 刷新：主状态轮询 10000ms → 8000ms；输入聚焦或存在未保存数字时暂缓重建商业列表，失焦补渲染。
  - `tools/ui_check.py` 新增 `commerce` 组（mock 桌面/移动/键盘/8 秒刷新/输入保护/原因码）；
  - `tools/ui_check_real_commerce.py`：真 API（`--auth off` + ambient）+ 真代理跑
    买→吃→卖→押镖→开店→雇人，页面内 hook `fetch` 记录真实请求/响应。
  - 文档：`docs/PLAYER-GUIDE.md` 新增商业面板说明与原因码映射。
- **为什么改**：后台已开闸 `buy/sell/consume/escort/recruit/open_shop`，但玩家在界面上点不到，
  等于没开；本次只补界面与验收，不改服务端。
- **验证方式**
  - `python3 tools/ui_check.py`：112 项全过、0 失败（含新增商业组与既有 86 项）。
  - `python3 tools/ui_check_proxy.py`：12 项全过、无回归。
  - `python3 tools/ui_check_real_commerce.py`：真服务逐动作截图与请求/响应存
    `reports/w4-commerce/`。
  - `python3 -m pytest`：479 passed / 16 skipped（当次工作区全量，含并行分支的婚姻用例）。
- **commit**：见本次提交（仅 `ui/player/**`、`tools/ui_check*.py`、`reports/w4-commerce/**`、
  `docs/PLAYER-GUIDE.md`、`CHANGELOG.md`）。

## [修复] 2026-11-05 — 委托板保供：每发布点每日至少 1 张可接单 + 金库垫付

- **改了什么**
  - `actions/quests.py::SystemQuestPostAction` 在常规发布之外新增 `_floor_posts`：每个
    “发布点”（有存活 giver 且模板无声望门槛的地点）当天没有可接单时补 1 张；候选按模板 id
    排序、优先 `procurement`/`errand`，起点 `day % len(group)` 确定性轮转，无随机数；保底单
    跳过全局/模板过期上限与完成率门槛，仍要求 giver 存活、同模板无 open、`giver_item` 有货。
    该点候选全部 `accepted` 在途时，允许 repeatable 模板再开一张。
  - giver 现金不足 reward 时，差额由 `treasury:region` 走真实
    `transfer(reason=quest_reward_advance, quest, template, day)` 垫付；金库不足则放弃该点
    当日保底，**不 `mint`、不记欠账**。保底单 `quest_create` 带 `floor: true` 与
    `advance_money`，可与常规单区分。
  - `content/**` 数值未改（`deadline_days` / `expired_cap_*` 均不动），不依赖调过期参数。
- **为什么改**：修复前 `runs/audit-r2` 从第 10 世界日起 8 个发布点新增单全部为 0，
  `system_quest_post` 返回 `[]`；逐模板复算显示全局过期上限触发 324 次、在途 191 次、
  现金不足仅 3 次（且发生在第 24–26 日），第 7–15 日被抑制的 giver 现金 268–4937 文、
  reward 8–36 文，**没有一例因现金不足**。根因是大量未接单在日界集中过期，使
  `retained_expired ≥ expired_cap_global=20` 封停全部模板；存量 open 单继续过期后板面归零，
  保留期结束后短暂恢复再进入周期性死锁（`runs/audit-ambient` 第 16–23 日 0 单）。
- **验证方式**
  - 真实服务 + ambient（seed 42，4x 配置，`runs/service-ambient-10d`，tick 1440）连续 10 世界日：
    新增 156、接单 125、完成 115、过期 22；第 7–10 日日界每个有 giver 的发布点最小可接单均为 1
    （集市 7/8/7/4、河渡 2/1/2/1、药铺 1/—/1/1；第 8 日药铺无 giver 在店，不属发布点）。
    守恒 `accounts_sum 45691 = minted 47450 − burned 1759`，delta=0。
  - 同 seed 两次真实服务 run：3851 events，
    `event_hash 655bd929…` 两次一致；headless `fix-floor-10d-a/-b` 亦一致（`a586e3f7…`，3968 events）。
  - 玩家侧（`runs/floor-player-proof`，原第 12 日 0 单）：跨日界补 5 张保底单后接
    `quest:errand_silk_sample:1728` → move → trade → `quest_deliver`，钱包 300→308。
  - 垫付（`runs/floor-advance-proof`）：清空 7 个 giver 钱包后每点补 1 单，逐笔
    `transfer` 合计 105 文，`accounts_sum = minted − burned` 对账 delta=0。
  - 供给/需求平衡 30 天：第 11–15 日保底 3–6 单/日、板面最小 1；第 16 日 prune 后常规恢复
    19 单，未见板面堆满无人接。
  - `tests/test_quest_floor.py` 新增 6 条；`tests/test_quest_health.py` 旧“全局上限→0 单”
    契约改为“每发布点仍有 1 张保底”。
  - 全量 `pytest`：480 passed / 16 skipped；`./scripts/selfcheck.sh` 4/4 PASS；
    `tests/test_p3e_golden.py` V5（692 events）保持通过，旧基线原值保留 skip。
  - `content/**` 未改：`content_hash` 关闭态 `7ec670c1…`、开启态 `3fd99946…` 不变。
- **commit**：见本条对应的保底供给提交（实现 / 测试 / 文档各自成笔）。

## [修复] 2026-11-05 — 玩家 API 放行 recruit：开店闭环在正式 HEAD 可用

- **改了什么**
  - `actions/player.py` 的 `PLAYER_VERBS` 与 `api/session.py` 的 `WRITE_ACTIONS`、
    `ACTION_ALIASES` 都加入 `recruit`；`_args` 透传 `target/shop/position/wage`，
    空串键不透传（`arg_str(required=False)` 对空串仍判 `bad_arg:shop`）。
  - 语义直接复用既有 `RecruitAction`：带 `shop/position/wage` 走店铺雇佣，
    不带时保持原门派招揽；没有另造接口或参数。
  - 提速补丁（`_price_cache` / `_view_cache`）已在 `240e5a8` 入库，不是临时 hack：
    按 `tick` 失效，避免 `player_state` 每次重复 35 次全城扫价；本轮不重复合入。
- **验证**
  - 正式仓库 HEAD + 本提交的 HTTP 端到端（ambient 4x、seed 42）：temple 开 `stall`
    （tick 486）→ `recruit`（`yan_shouyi`/庙祝/18）一次成功（tick 2904）→ 过 3 个世界日
    （tick 3024/3170/3317）三次 `system_shop_settle`，每天
    `transfer(18, cash:shop:player:u_demo → wallet:yan_shouyi)`，合计 54 文、欠薪 0。
  - 同一实例负样例：未经营人情 → `relation_too_low`；日薪 1 文 → `wage_too_low`。
  - 对账：`accounts_sum 47241 = minted 47450 − burned 209`，`delta=0`，`escrow_total=0`。
  - 新增 1 条测试（白名单/别名/参数透传 + 空 shop 不透传）；全量 pytest
    483 收集 / 467 passed / 16 skipped；selfcheck 4/4。
- **commit**：见本次提交（仅 recruit 放行 + 测试 + 文档）。

## [修复] 2026-11-05 — work 事件补 employer 归因：玩家店铺出工可审计

- **改了什么**
  - `sim/intent.py` 为玩家店铺生成的 `work` 意图附带 `employer=shop:player:<account>`，
    覆盖常规整班、`found_sect` 加班、同地点短工三处；事件里能直接看出“为谁工作”，
    不再只靠地点猜测（雇工原 workplace 与玩家店铺可能同处一地）。
  - NPC 雇主的 `work` 参数保持 `{at, hours}`，无玩家雇佣的世界不受影响。
- **验证**
  - 地点级证据（`shop:player:u1` @ market，原 workplace=temple）：tick 32
    `work at=market employer=shop:player:u1 outcome=ok`，雇工 apply 后 `location=market`；
    day0 行程 tick 31 `move market` → tick 32 `work` → tick 36 `move temple`。
  - 玩家同地点 `state` 的 `nearby` 含 `yan_shouyi`；`talk` 原始事件 `outcome=ok`
    （非 `not_together`），见 `docs/shop-operations.md` 7.3。
  - 新增 1 条测试（NPC 雇主 `work` 参数仍为 `{at, hours}`），雇工测试改为断言每次
    出工都带 `employer`；无玩家雇佣的 V5 基线继续通过（`0ee1f724…` / `6a919efe…`）。

## [修复] 2026-11-05 — 玩家店铺雇工进入日程：契约即出工，欠薪仍到店

- **改了什么**
  - `sim/intent.py` 的常规日程 `work` 分支按雇主类型区分资金门槛：
    - NPC 商业体（`owner` 为空）沿用原规则 `余额 ≥ 日薪 + employer_reserve`，不变；
    - 玩家店铺（`owner=player:<account>`）契约成立即出工，偿付判断只看
      **玩家钱包余额 + 店铺当日营收**，不套 2000 文储备；钱不够仍在计划里注明
      “店主现金紧，工资走欠薪”，日结由 `system_shop_settle` 记店铺欠雇工的债。
  - 玩家雇主的日薪优先取契约 `employee_wages`（不再用岗位市场价）；`found_sect`
    加班路径同步使用该口径。
  - 头号动机：此前 `yan_shouyi` 签约后 `employed_at=shop:player:<account>`，
    但日程因 NPC 储备规则不再去庙里、也不去玩家店，实际“失业在家”。
- **验证**
  - 新增 `tests/test_intent_player_employment.py` 8 条：玩家店日程出现
    `move market → work at market`（店现 0 / 钱包低于储备仍出工）、欠薪文案、
    契约日薪优先、NPC 雇主储备规则不变、同地点 `talk` 前置、3 天自走工资与
    同 seed 两次 state/event 哈希一致。
  - 无玩家雇佣的世界：`tests/test_p3e_golden.py` V5 原值继续通过（state/event 哈希
    逐字节不变），因此不新增/不改旧基线。
  - 真实仓库代码自走 3 日验收记录见 `docs/shop-operations.md` 7.1；HTTP 端到端
    （开店 → 雇 1 人 → 3 个世界日，`recruit` 经 HTTP 签约）见 7.2。7.2 使用的是
    本仓库工作树（HEAD `1709524` + 本轮 `intent.py` + W1 尚未入库的 `recruit`
    放行）；`origin/master` 合并后可用同一脚本复跑。
- **commit**：见本次提交（与 CHANGELOG 同一 pathspec 提交）。

---

## [修复] 2026-11-05 — 押镖货物托管：发单即扣货、送达离场、失败原量退回、托管进对账

- **改了什么**
  - `EscortAction` 受理带 `cargo_item/cargo_qty` 的镖单时，立即 `item_consume` 玩家背包并
    `item_create` 到镖局库存的命名托管桶 `escrow:<镖单id>:<item>`；两条效果与镖单同事务，
    带 `reason=escort_cargo_escrow` / `order` / `escrow_account` / `movement=player->escrow`。
  - 受边界约束不改 `world/state.py`/`world/effects.py`：托管件数落在 `business.stock`，
    结算时按 `escrow:<镖单id>:` 前缀扫描。`escrow:` 键不是 `content.items` 中的真实物品，
    已逐条核对店铺买卖、NPC 采购、food/cheapest_food、`_shop_view`/`_location_view`，
    不会被当成商品买走。
  - `SystemEscortArriveAction`：送达 → `item_consume(reason=escort_cargo_deliver)`，
    有货损风险事件时先按比例 `escort_cargo_loss`；按既有模型离场（收货方入库/销毁），
    不向任何店铺注入库存。失败/超时/承运人离线但实体存在 →
    `item_consume + item_create` 原量退回背包（`escort_cargo_return`）；仅当实体缺失才
    `escort_cargo_orphaned`。无 cargo 的 NPC/旧 intent 镖单事件序列逐字节不变。
  - `EscortAction.preconditions` 增加 `item_not_available` / `item_missing` 与数量校验；
    同一批货连续发第二单直接拒绝。
  - `/api/player/state` 新增 `escrow[]` / `escrow_total` / `reconciliation`
    （`accounts_sum`、`minted_total`、`burned_total`、`net_issued`、`delta`、
    `escrow_accounts`、`escrow_items`），托管不再是隐形口袋。
  - `docs/player-api.md` 押镖段同步改写。
- **为什么**：消费开闸验收发现押镖只把 cargo 写进事件、不扣背包，同一批货可反复开单拿镖银，
  属于经济复制而非延迟结算。
- **验证方式**
  - `tests/test_player_commerce.py` 新增/加强 3 条：成功全流程（背包 7→1、托管 6 件、
    送达托管归零、背包不再有货、钱包 +89=91−2）、失败退回（7→1→7、钱包不变、托管归零）、
    连续两单第二单 `item_missing`；另加押镖中 `leave` 不蒸发不双份。
  - 干净 HEAD 实测证据 `/tmp/escort_escrow_evidence.json`：成功/失败/重复/离线四场景的
    事件序列（`item_consume+item_create(escort_cargo_escrow)` →
    `item_consume(escort_cargo_deliver)` 或 `item_consume+item_create(escort_cargo_return)`）、
    背包与钱包快照、`conservation_error=0`、`escrow_total=0`。
  - 50 并发 `buy` 前置拒绝 P95 **8.84ms**（上限 50ms）；`player_state` P95 94.84ms
    （同口径干净 HEAD 基线 123.26ms 未劣化）。
  - 干净 HEAD 全量 pytest：**473 tests / 457 passed / 16 skipped**；`scripts/selfcheck.sh`
    **4/4 PASS**。
  - 基线：NPC-only golden 不含玩家 cargo 镖单，事件构成不变，V4 数值不改。
- **commit**：见本条对应的押镖托管提交。

## [功能] 2026-11-05 — 开店经营闭环：初始库存、店铺雇佣、独立日结、自雇不记债

- **改了什么**
  - `open_shop` 按档位写入初始库存（stall 13 / storefront 30 / branch 50 件，只取内容包真实物品），
    并按既有 `sell_price` 向 `treasury:region` 进货：钱包 → 公账 `transfer(reason=shop_stock)`，
    库存 `item_create(reason=initial_stock)`；铺面费仍付给所在地商业体公账（`reason=shop_rent`）。
    前置要求钱包覆盖“铺面费 + 进货成本”，无赠送、无 `mint`。
  - `recruit` 增加店铺雇佣语义（带 `shop`/`position`/`wage`）：岗位须在 `economy.wages_daily`，
    日薪为市场价 0.8–1.5 倍，双向好感 ≥ 6、最高技能 ≥ 0.15，按档位限 1/2/3 人；
    失败返回 `relation_too_low` / `skill_too_low` / `wage_too_low|too_high` / `shop_full` /
    `target_not_at_shop` 等可读原因码，拒绝事件照常落库。成功写 `shop_hire`（岗位、日薪、
    `employed_at`）与双方记忆。
  - 新增独立日界事件 `system_shop_settle`（不并入 `system_debts`）：先付工资，现金不足记
    “店铺欠雇工”的债；再按 `月租/30` 缴日摊税，不足部分以运行期 `tax_arrears` 结转次日；
    净利 = 营收 − 进货 − 工资 − 税；每个雇工每日产出 1 件档位库存品；店主与雇工各写一条可读记忆，
    亏损追加“亏损，店主需补足”。
  - `transfer` 效果按 `reason=buy|consume` / `sell` 累计玩家店铺当日营收/进货（运行期计数器，
    不进 `to_dict()`）；`shop_day_close` 清零并结转欠税。
  - 修自雇零薪：在自家店 `work` 现在 `received=0` 且**不生成任何债务**（不再出现“自己欠自己”），
    只扣体力/涨技能并写“店主自雇，工资记0文（店主收益走分红）”。
  - `sim/scheduler.py::_day_end` 在 `system_maintenance` 之后加条件守卫：仅当存在 `player:` 所有者
    的店铺才 emit `system_shop_settle`；`headless.py` 用同一判断。无店世界事件序列逐字节不变。
- **为什么**：可玩性审计实测两处真 bug —— `open_shop` 扣 300/1200/3000 文换来无库存、无员工、
  无日结的空壳；在自家店 `work` 应得工资却实收 0，并生成“自己欠自己”的工资债。
- **验证方式**
  - 新增 `tests/test_shop_operations.py` 7 条：初始库存资金流向与 `minted` 不变；recruit 前置/原因码/
    契约；自雇 0 工资无自我债；日结工资/税/产出/亏损记忆/计数清零/欠税结转；欠薪债未结清累加、
    结清后用带 tick 新 id；无店日界事件整表哈希与改动前逐字节一致；有店 3 个世界日每日恰好一条
    settle 且位于维护类之后、seq 连续。
  - 真实服务（`wsim.api` + ambient，seed 42，content_hash `7ec670c1…`，run `shop-demo-v7`）跑
    day2–4：营收 65/59/210，工资每日 18（实付 18、欠 0），税每日 10 实付，净利 +37/+31/+182，
    产出每日 1 件，日末库存 39/35/28 件，日末店铺现金 45/76/258。
  - 不变量：3,203 事件 seq 1..3203 连续；`conservation_error=0`；
    `minted_total 53150 − burned_total 1175 = accounts_sum 51975`；玩家钱包 2550
    = 6000 − 3000 铺面费 − 450 进货。
  - 基线：旧 V3 期望值保留并 skip；W1 在 HEAD `48b512f` 入库的 V4 数值原样保留、两条测试 skip；
    新建 V5（693→692 events，buy 793→785，business_cash 12599→12816，sell 967 / burned 81 /
    blocked 8 / pruned 4 不变，state/event 哈希显式变化）。
  - 计数：本提交全量 pytest **454 passed / 16 skipped**（新增 `tests/test_shop_operations.py` 7 条 +
    golden V5 2 条通过；W1 的 V4 两条转为 skip，其数值未改）。
- **commit**：见本次提交（与 CHANGELOG 同一 pathspec 提交）。

---

## [功能] 2026-11-05 — 玩家消费类动作开闸：buy/sell/consume/escort 与价格可见

- **改了什么**：`api/session.py` 的玩家动词白名单/别名放行 `buy`、`sell`、`consume`、`escort`，
  `_args` 补齐 `item`、`qty`、`from`/`to`、`with_npc`、`route`/`route_id`、`cargo`/`cargo_qty`/
  `cargo_value`、`fee`，语义全部复用既有 `BuyAction`/`SellAction`/`ConsumeAction`/`EscortAction`。
  前置失败改成可读原因码：`not_enough_money`、`item_not_available`、`item_missing`、`not_here`、
  `shop_closed`、`not_a_merchant`；NPC 专属 `marry`/`found_sect`/`apprentice` 统一返回
  `not_implemented_for_player`（前端可渲染“这个玩法还没开放”）。`state` 只增不改地新增
  `location.services_detail[]`（`price`/`sell_price`/`stock`/`buy_limit`/`sell_limit`）、顶层 `shop`
  （`buy`/`sell` 两侧价与上限）、`player.wallet.items[].price/sell_price`、`escort_orders[]`；
  既有 `services`（item id 数组）与其它键语义不动。押镖 `cargo` 必须是玩家背包里真实持有的物品
  （不足给 `item_missing`），`escort_order_create` 记 `cargo_item/cargo_qty`，`cargo_value` 由
  `item_price*qty` 折算；到期由 API 提交 `system_escort_arrive`，写 `escort_settle` 与镖银 transfer。
  价格与钱包/地点/店铺视图按 tick 缓存，避免并发读重复扫全城供需（见下方 P95）。
- **为什么**：可玩性审计实测玩家 `buy`/`sell`/`consume`/`escort` 全是 `unknown_action`，
  商品价格表存在但玩家不可达，镖师也只能靠“押镖类委托”交单；UI 也拿不到价格。
- **验证方式**
  - 真服务（`wsim.api --auth off`，`XKX_AMBIENT=0`，seed 42）：`buy flatbread` 300→297、
    背包 3→4；`consume` 297→294、体力 80→100、事件含 `item_consume`；`sell` 294→296、背包 3；
    `buy raw_silk` 296→235、`burn transaction_tax=1`。
  - 负面路径实测：`not_enough_money`/`item_not_available`/`item_missing`/`not_here`/
    `not_a_merchant`/`not_implemented_for_player`，押镖空头货物也回 `item_missing`。
  - 押镖闭环：`route_market_short`、`cargo=coarse_cloth×6`（先在集市买入，值 150）、`fee=130`；
    镖单 `active→done`，tick 216 到达集市，钱包 81→170（`transfer escort_fee=91` 自
    `treasury:region`，再扣 1+1 交易税）；结算事件含 `escort_settle`。
  - 采购委托：4 单初始（`tutorial_first_buy`/`procure_mugwort`/`daily_inn_millet`/
    `daily_ferry_rope`）各自新世界实测 accept→buy→deliver 全成功；同世界连续完成 4 单后推进一天，
    声望门槛满足，`procure_charcoal` 出现并 accept→buy→deliver 成功（**未改任务数值**）。
  - 不变量：回放 643 事件 `seq` 1..643 连续；`conservation_error=0`，
    `accounts_sum 47746 = minted 47750 - burned 4`；每个买卖事件的钱/货方向与快照逐条一致。
  - 50 客户端并发（50 线程 ×250 请求）：`GET /api/state` P95 **6.43ms**（干净 HEAD 6.52ms，
    对照历史 22.6ms 未劣化）；`GET /api/player/state` P95 **91.69ms**（干净 HEAD 123.26ms）；
    `talk` 写 P95 **206.48ms**（干净 HEAD 211.35ms）；`buy` 前置拒绝 P95 **32.28ms**
    （干净 HEAD 9.88ms：现在会写可审计的 reject 事件，吞吐 2573 rps，非读写路径劣化）。
  - 基线：旧 V3 期望值原样保留并 `skip`，新建 V4；本改动只动 API/只读 state，2 天 headless
    世界不执行玩家 API 动作，实跑与 V3 逐项相同：**693 events / sell 967 / buy 793 / burned 81 /
    business_cash 12599 / blocked 8 / pruned 4**，`state_final a8aecf44…` / `event_final ba66bb4d…`
    不变（`content_hash 7ec670c1…`）；V4 是显式迁移基线，没有改旧数字。
  - 计数：干净 HEAD `d70999c` + 本提交 pytest **447 passed / 14 skipped**；`scripts/selfcheck.sh`
    **4/4 PASS**。
- **commit**：`240e5a8`。

---

## [修复] 2026-11-05 — 容器默认开启世界自走，与 `./start.sh` 对齐

- **改了什么**：`deploy/entrypoint.sh` 与 `deploy/docker-compose.yml` 原先没有注入
  `XKX_AMBIENT`，容器默认 `ambient=stopped`；现在默认 `XKX_AMBIENT=1`、
  `XKX_AMBIENT_CPU=70`、`XKX_AMBIENT_SPEED=1`，与 `./start.sh` 一致，
  `XKX_AMBIENT=0` 仍可显式关闭。`docs/OPS-RUNBOOK.md` §1.2 与
  `docs/RELEASE-NOTES-p3l.md` §4 同步改为「容器默认自走，可 0 关闭」。
- **为什么**：对外承诺是“一条命令就是活世界”，容器默认静止与之矛盾。
  此修正发生在 `delivery-p3l` tag（`d01ba9b`）**之后**，tag 未移动，交付包需按本提交重打。
- **验证方式**：全新卷、无任何环境变量 `docker-compose up -d --no-build` →
  容器 `Up (healthy)`，`/api/health` 返回
  `ambient=running / ambient_tick=1 / tick=1 / ambient_speed=1.0`，10 秒后
  `ambient_tick=3`（推进）；`/api/world/feed?limit=8` 返回 6 条真实 NPC 事件
  （`曹半仙` move/quest_accept/work、`石敢当` move/quest_accept）。
  全新卷 `XKX_AMBIENT=0` → `ambient=stopped / ambient_tick=null /
  ambient_reason=status_file_missing / tick=0`，`feed` 为 `{"events":[],"tick":0}`；
  同一容器内 register/login/enter/`/api/player/state` 全部 200 且 `ok=true`
  （初始资产 300 文，tick 6→12），玩家功能不受影响。
- **commit**：`dcbcea5`

---

## [功能] 2026-11-05 — 世界接线：自走与玩家同一个世界、同一本账

- **改了什么**：取消自走独立子进程与独立 `runs/<RUN_ID>/ambient/events.sqlite`。
  `WorldSession.start_ambient()` 把本会话已有的 `Writer` / `EventStore` / `registry` 注入
  `AmbientScheduler(writer=…, store=…, registry=…, lock=…)`，由 `WorldSession.lock`
  （同一把 `RLock`）串行化玩家写路径与每个自走 tick：单 Writer、单事务、`seq` 连续。
  共享模式不再创世、不再新建/关闭事件库；`runs/<RUN_ID>/ambient/` 只保留调度器审计与状态文件。
  自走只从 `Writer.state.tick` 往后推一格；玩家动作仍推进 6 tick，跨过的 tick 不补跑，
  排队中的旧意图直接丢弃或归并到当前 tick，日界结算改由“日变更”触发一次，避免双倍走时。
  新增匿名只读 `GET /api/world/feed?limit=20`（服务/legacy 模式均可），固定字段
  `tick/actor/verb/place/summary/important`，过滤 `player:` 与 `system_*`，不返回任何玩家私有状态。
  `/api/health` 的 `ambient_tick` 在 running/paused 时直接取同一世界 tick。
  `start.sh` 仍默认开启；`XKX_AMBIENT=0` 不启动线程，行为与旧版一致。
- **为什么**：上一波自走写的是隔壁世界，玩家在游戏里看不到 NPC 在动，
  health 里的 `ambient_tick` 是另一个世界的时钟——等于没活。
- **验证方式**：真服务 `XKX_AMBIENT=1`，玩家 `enter` 后 60s（tick 1→18），
  `/api/world/feed?limit=20` 返回 14 条，其中 tick≥9 的 12 条；
  玩家所在地 `集市` 有 8 条（含 `石敢当 work/sell/buy`），另有 `城隍庙` 的
  `曹半仙 join_sect`（important）。时钟一致性：玩家 `cultivate` 动作 +6 tick，
  随后 12s 自走 +1 tick（无双倍）。300 tick 对照：离线 NPC 自主事件 891，
  在线 970（108.9%，≥80% 门槛）。50 并发压测 30s：11,542 请求 / 0 error / 0 5xx，
  `GET /api/state` **P95 22.579ms**（对照 29.3ms，未劣化）；同一 run 11,504 事件
  `seq` 1..11504 连续且去重，钱包 delta 0，半写 0。headless 确定性基线不变：
  同 seed 两次 `state_final`/`event_final` 哈希一致（693 events，P4 v3 golden）。
  `pytest` **439 passed / 12 skipped / 0 failed**；`scripts/selfcheck.sh` **4/4 PASS**。
- **commit**：`e9f8047`（scheduler）、`1f2e64f`（api）、`53f62d9`（start.sh）。

---

## [功能] 2026-11-05 — 世界动态中文动作映射 + 服务模式真后端复验

- **改了什么**
  - `ui/player/app.js` 集中 `FEED_VERB_TEXT`：`move→赶路`、`work→上工`、
    `quest_accept→接单`、`quest_deliver→交单`、`trade→买卖`、`talk→交谈`、
    `cultivate→修炼`、`open_shop→开店`、`join_sect→入门`、`escort→押镖` 等；
    未知动词一律「有事发生」。摘要缺省时用中文动作兜底，摘要里混入
    `[a-z_]+` 内部动词时逐词替换，玩家侧不再出现 `move` / `quest_accept` 之类原文。
  - `tools/ui_check.py` 新增 4 条断言：空摘要按 verb 映射、未知动词兜底且不漏英文、
    摘要混入内部动词同样替换、整份渲染文本不含 `[a-z_]+`；mock feed 事件改用
    真服务同款内部动词（`work/trade/talk/move/cultivate`）。
  - 新增 `tools/ui_check_real_feed.py`：真 `wsim.api --auth on` + 真
    `service.sqlite` + `XKX_AMBIENT=1`（2x）自走 + 真 `ui/player/server.py` 代理，
    浏览器走真代理注册/登录/进入，60 秒窗口逐秒累积玩家实际看到的事件。
- **为什么**：后端 `verb` 是内部英文动词，契约要求玩家视角不得出现；
  此前的服务模式截图是「真代理 + mock 上游」，不算端到端。
- **验证方式**
  - `python3 tools/ui_check.py`：**86 checks / 0 failed**（feed 17 条全过）。
  - `python3 tools/ui_check_proxy.py`：**12 checks / 0 failed**（mock 上游回归）。
  - `python3 tools/ui_check_real_feed.py`：**12 checks / 0 failed**（真后端）：
    匿名/登录态 `/api/world/feed` 均 200，顶层 `{ok,tick,limit,events}`、每项 6 公共字段、
    正文无 `wallet/items/skills/...`；feed `tick=29` 与 `/api/health.ambient_tick=29` 一致；
    60 秒内 UI 出现 20 条、窗口累计 46 条真实 NPC 事件（演员 严守一/蒋听雨/金满仓/陆九斤，
    非 mock 名单），当前/相邻地点命中「药铺」；feed 请求 7 次、间隔稳定 8.0s、全部匿名；
    刷新后仍 20 条；`events.sqlite` 内 NPC 事件 43 条。
  - 干净 HEAD（`c2014a8` 的 `git archive`）：pytest **439 passed / 12 skipped**；
    `scripts/selfcheck.sh` **4/4 PASS**。
  - 证据 `reports/w4-feed/feed-service-real.png`、`feed-service-real-reload.png`、
    `real-feed-service.json`（原始匿名/登录态 JSON + 请求节流记录）。
- **与 mock 版本的真实差异**（UI 已兼容，非缺陷）
  - 真后端 `events` 按 tick **升序**返回，UI 侧统一倒序；mock 直接倒序。
  - 真后端 `verb` 是内部动词、`summary` 是中文短语；mock 事件同样改成内部动词回归，
    摘要为空时才展示映射后的中文动作。
  - 真后端一屏 20 条、`important` 数量随自走变化；mock 固定 5 条、2 条 important。
  - 真后端 feed 与 health 的 `ambient_tick` 同一世界；mock 无自走。
- **commit**：`e5eea9c`。

---

## [功能] 2026-11-05 — 玩家侧「世界动态」feed

- **改了什么**：`ui/player` 中栏新增默认可见的「世界动态」区块，读
  `GET /api/world/feed?limit=20`：按 tick 倒序最多 20 条，行格式
  `· [tick] actor 在 place：summary`，`important=true` 金色强调；固定高度 168px
  （移动端 148px）独立滚动，用户下滚阅读时插入新条目会补偿高度不跳动，停在顶部时跟随最新。
  刷新与主状态解耦：每 8 秒单独节流、匿名请求，不与登录/单飞刷新互相打断；
  失败/降级只显示「世界暂时安静」，不弹红字、不写错误历史、不动主状态。
  玩家当前或相邻地点的 `important` 事件复用现有 toast + 操作记录（`world_important` 原因码，
  按 tick+summary 去重），不新造弹层。`ui/player/server.py` 只读降级态新增
  `/api/world/feed` 本地合成公共事件（过滤 `player:` 与 `system_*`），live 模式透传上游。
  `tools/ui_check.py` 新增 13 条 feed 检查 + 3 条移动端检查；`ui_check_proxy` mock 上游
  补 `/api/world/feed` 并加 2 条服务模式断言。
- **为什么**：世界自走已默认开启，但玩家侧看不到 NPC 在动；需要把公共世界事件
  呈现给玩家，同时不挤压主线操作、不被底部安全区遮挡。
- **验证方式**：`python3 tools/ui_check.py` **82 checks / 0 failed**；
  `python3 tools/ui_check_proxy.py` **12 checks / 0 failed**；真 `wsim.api --auth off`
  + 真 `ui/player/server.py` 代理 + `XKX_AMBIENT_SPEED=4` 实测 feed 200 与真实事件截图；
  真 `--auth on` API 匿名 feed 200。工作区（含其他会话未提交改动）pytest
  **439 passed / 12 skipped**；`scripts/selfcheck.sh` **4/4 PASS**。
  证据 `reports/w4-feed/`：5 张截图 + `feed-checks.json`（16 项 feed 检查，匿名头全空）
  + `real-api-feed.json`。
- **与契约的实测差异**（UI 已兼容）：响应是 `{ok,tick,limit,events:[...]}`；
  `events` 实际按 tick **升序**返回；`verb` 是内部动词（`move`/`quest_accept`）而非中文动作名；
  `summary` 是短语（如「动身前往药铺」）而非契约示例里的完整句子。
- **commit**：`8f1ed22`；本条为回填（`docs(changelog)`）。

---

## [文档] 2026-11-05 — 内容层 4 件零成交物品种类定位

- **改了什么**：`docs/known-limitations.md` 追加 §12「内容层零成交物品（已定位、未修）」，
  逐件记录 `salt_lamp` / `salt_scale` / `rare_herb_box` / `service_night_watch` 的
  类目与价格、具体判定条件（`_propensity_buy()` 只看当前所在地、库存 ≥3、
  排除 `food/drink/service/meal`、`price×3 ≤ cash`、`cash ≥ 120`）、
  引用委托为何不成交（`inquiry_*` 与 `errand_night_watch_report` 的
  `offer/want` 为空、`money_delta=0`；`debt_hu_chen` 只搬钱），以及最小修法建议；
  并补口径说明：`inquiry_*` 为零价值问价、永远不会产生成交事件，
  “被任务引用”不等于“活跃”，覆盖应把 `quest_refs` 单独计入。
- **为什么**：委托公平轮转后零成交物品 6→4，剩余 4 件的根因需要留档，
  避免后续把“被引用”误读成“已盘活”，也避免重复排查。
- **验证方式**：纯文档，零代码改动，未触碰 `content/**`；`content_hash` 未变
  （关闭态 `7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`）。
  按任务要求未跑全量 pytest。
- **本轮不做**：任何修法都要动内容定义或经济判定，会让 `content_hash` 与
  V2/V3 golden（经济流量、`state_final`）再漂一次，冻结期内不接受。
- **commit**：`730c82b`；本条为回填（`docs(changelog)`）。

---

## [功能] 2026-11-05 — 世界自走成为首发默认体验

- **改了什么**：`./start.sh` 默认同时拉起 API、前端与后台自走（`XKX_AMBIENT=0` 关闭，
  关闭态与旧版一致）；ambient 日志落 `runs/<RUN_ID>/logs/ambient.log`，API/前端各自落文件；
  API 就绪后 ambient 后台独立启动，退出时先收 ambient 并 `wait` 收割；旧自走目录自动归档。
  CPU 护栏改为 70% 阈值三档动作：补睡降速 → 持续超限暂停 → 低于恢复线自动恢复；
  新增 `ambient_status.json`、`parent_pid` 看护、`cpu_pauses/cpu_resumes`。
  `/api/health`、`/api/service/info`、`ui/player` `/api/health` 只**追加** ambient 字段，
  `ok/mode/readonly/profile_version/upstream_ok` 不变。
- **为什么**：此前默认不启用调度器，玩家解包起服务后世界静止，与产品核心设定相反；
  同时避免自走过载时拖死服务端、进程退出后残留僵尸。
- **验证**：干净 worktree（HEAD=`fea1d8f`）全量 pytest **429 passed / 10 skipped / 0 failed**；
  `scripts/selfcheck.sh` **4/4 PASS**；`./start.sh` 一条命令后 `/api/health`
  `ambient=running` 且 7s 内 `ambient_tick 0→1`；`XKX_AMBIENT=0` 为 `stopped` 且无 ambient 进程；
  300 ticks（seed 42）：1119 事件 / 966 NPC 自主事件 / 28 个活跃 NPC / 16 种动词 / 0 LLM；
  玩家第 149 tick 进入、209 退出：progress 0..299 连续，0..149 与无玩家对照逐字节一致，0 回滚；
  1x 3 分钟进程 CPU 0.19%，注入忙等负载后 70% 阈值实测 3.68s 暂停、2.24s 自动恢复。
- **commit**：`aee9bbe`（调度器/护栏）、`90e4404`（health）、`fea1d8f`（start.sh）。

---

## [模拟] 2026-11-05 — 委托公平轮转，10 天零接单模板 16→0

- **问题**：`Planner._quest_step` 按 `q.id` 字典序扫开放委托，`procure_*` /
  `teach_*` 等前缀靠后的模板永远排队尾；10 天 seed42 开启态有 16 张模板零接单
  （其中新增的 33 张里有 9 张饿死），6 件物品零成交。另有 2 天期限模板的
  `not_at_giver` 静默拒绝：接单步骤在上工/午饭之后，发起人已走开。
- **修复**：开放委托按模板 `last_served_tick` 最久未服务优先轮转（平局按
  `quest id` 稳定排序，不依赖 dict 顺序或墙钟）；接单/执行步骤移到当天上工之前。
- **验证**（10 天 seed42，`content_hash` 开启态 `3fd99946b44a…`、关闭态
  `7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`）：
  - 开启态：接单 106→181，distinct 模板 44→60，零接单 16→0（新增 33 张里 9→0），
    零成交物品 6→4；state/event `d92b4579…` / `1cb069d7…`，illegal 0.0；
    两次重跑哈希一致。
  - 关闭态：K1 **62/91 = 68.13%**（护栏 63.13%，未改阈值），事件 2934→2948；
    state/event `331c2e1c…` / `5a122bec…`；两次重跑哈希一致。
- **基线**：旧 800-event golden（V2）原值保留并显式 skip；新增 V3 基线
  （693 events，state `a8aecf44…` / event `ba66bb4d…`），漂移原因见
  `tests/test_p3e_golden.py` 文件头：任务顺序前移导致 NPC 一日行动顺序变化，
  sell 971→967、buy 820→793、burned 127→81、business_cash 12184→12599。
- **验证计数**（干净 HEAD `fea1d8f` + 本提交）：pytest **429 passed / 12 skipped**；
  `scripts/selfcheck.sh` **4/4 PASS**。
- **commit**：`e1d6d2b`。

---

## [安全] 2026-11-04 — 读端点越权读取已修复

- **问题**：服务模式下读端点匿名且接受任意 `account_id`，读路径不解析令牌，
  存在读端点越权读取（可见面过宽）。
- **修复**：匿名可见面收窄为公共摘要（白名单 `ok`/`name`/`place`/`activity`/
  `idle_seconds`/`online`/`sect_name`/`shop_name`/`server_tick`）；带令牌强制
  `account = 令牌账号`，不一致 403 `auth_account_mismatch`；`messages` 匿名返回空列表；
  `quests` 匿名只给公共开放任务；`intents` 服务模式强制令牌（匿名 401
  `auth_missing_token`）。legacy 模式（`--auth off`）行为不变。
- **契约**：`docs/AUTH-API-W1.md` §3「读端点的可见面（服务模式）」。
- **验证**：全量 pytest **422 passed / 10 skipped / 0 failed**；`scripts/selfcheck.sh`
  **4/4 PASS**；匿名 `state` 响应体逐键等于白名单，且 grep 不到
  `wallet|items|skills|relations|active_quests|growth|today`。
- **commit**：`133333e`。

---

## [安全] 2026-11-05 — 移除已入库的崩溃演练 session 证据

- **事件**：`reports/w5/crash-drill/token-check.txt` 于 `9c76cde` 入库，内容为
  `/api/auth/me` 响应，含一次性 `session_id`（`s:e1cb5106…`）、账号名与 `account_id`；
  **不含 Bearer 本体**。
- **处置**：`11c3ac9` 从 HEAD `git rm` 该文件并删除磁盘副本；`.gitignore` 补
  `reports/**/token*.txt`、`reports/**/*-check.txt`（不忽略整个 `reports/**`，保留交付证据）；
  `SECURITY.md` 第 5 节第 6 条记录来龙去脉。
- **历史状态**：**未改写历史**，该文件在 `9c76cde` 及后续历史中仍可检出；`session_id`
  为一次性会话标识，压测后已 `DELETE FROM sessions`，不可用于重新认证。
- **扫描**：HEAD 中凭证型文件已归零；`tools/e2e_service_check.py` 的
  `Bearer e2e-stale-token` 是 401 测试字面量，`reports/w4-ui/checks.json` 的
  `tok_ui_user_1` / `s:1` 是 UI mock，均非真实凭证。

---

## [安全] 2026-11-04 — W5 崩溃演练明文令牌清理

- **事件**：`deploy/crash_drill.sh` 曾把用 `drill-secret` 登录得到的完整 Bearer token
  写入 `reports/w5/crash-drill/token.txt`（43 字节，**未入库、未推送**）。
- **处置**：删除该文件；源脚本改为只在进程内存/局部变量持有令牌，落盘仅
  `token-digest.txt`（sha256 + 末 4 位）；`/api/auth/me` 证据改为
  `status/ok/expires_at`（不再写 session_id）；演练结束执行
  `DELETE FROM sessions`，令牌即时失效，不依赖 TTL。
- **防复发**：`.gitignore` 增加 `**/token.txt`、`**/*.token`、`reports/**/token*`。
- **验证**：吊销前旧令牌 `/api/auth/me` HTTP 200；`DELETE FROM sessions` 后同一令牌
  HTTP 401 `auth_invalid_token`；`bash -n deploy/crash_drill.sh` 通过；新产物无完整令牌。
- **TTL 说明**：该令牌 `expires_at=1793885305`（2026-11-05 13:28 UTC，约 24h），
  与 60 秒断线重连宽限期无关——宽限期只影响 `disconnected` 判定，不延长令牌寿命。
- **commit**：`41ae2b1`（本次安全提交）。

---

## [记账] 2026-11-04 — `466c807` 混装提交审计（W2 + W3/W3b + content）

- **commit**：`466c807`（`feat(content): 扩展包改为引擎原生加载`，提交时间 2026-11-04 20:53）。
- **审计结果**：该提交实际包含 **9 个文件**，与“误吞 W2 + content + W3 + W4 + W5”的描述**不完全一致**：
  本次审计**未在该提交中发现 W1 / W4 / W5 / 打包文档 / agent 自提案文件**。
- **文件归属**
  | 文件 | 归属 |
  | --- | --- |
  | `docs/AMBIENT.md` | W2 调度器（自走运行说明） |
  | `src/wsim/sim/scheduler.py` | W2 调度器 |
  | `tests/test_w2_scheduler.py` | W2 调度器 |
  | `tools/ambient_accept.py` | W2 调度器（验收器） |
  | `tools/ambient_run.py` | W2 调度器（运行器） |
  | `src/wsim/core/content.py` | W3 内容（原生包加载） |
  | `src/wsim/core/content_manifest.py` | W3 内容（包清单/校验） |
  | `tools/content_scale.py` | W3 内容（扩容工具） |
  | `tests/test_w3b_native_packs.py` | W3b 人物卡（原生加载测试） |
- **为什么会出现混装**：提交信息只描述“内容包原生加载”，却同时带入 W2 调度器全套文件（`scheduler.py`
  942 行、ambient 工具与文档）。从文件构成判断，是多个并行会话共享同一工作区 / index 时，提交未按
  pathspec 限定范围，把另一条线已就绪的改动一起打包（**推断**，非提交者声明）。
- **影响**：`466c807` 无法按“一条线一个 commit”回溯；但 9 个文件均已入库，内容没有丢失。
- **处置**：不改写历史（amend / rebase / filter / 强推一律不做）；以本条文件归属表补足追溯粒度，
  后续提交须用 `git commit -- <paths>` 限定范围（见 `docs/GIT-WORKFLOW.md` §2）。
- **验证**：干净 HEAD（`d7f3707` 的 `git archive`）上 `pytest` **385 passed, 10 skipped**、
  `selfcheck.sh` **4/4 PASS**，说明混装内容与当前基线兼容。

---

## [未发布] 2026-11-04 — W1 引擎变更（账号 / 存档 / 内容 hash 闸门）

- **改了什么**
  - `88fdbfb` 账号与令牌存储：PBKDF2 口令哈希 + sqlite，口令不落明文（`src/wsim/api/auth.py`、
    `tests/test_w1_auth.py`）。
  - `ac40a9c` 事件日志回放恢复玩家存档，重启不丢档（`src/wsim/api/persistence.py`、
    `src/wsim/api/session.py`、`tests/test_w1_persistence.py`）。
  - `23e1d05` 内容 hash 不匹配时拒绝启动：退出码 3 + 契约日志（`src/wsim/api/__main__.py`、
    `tests/test_w1_hash_mismatch.py`）。
- **为什么改**：玩家账号与存档在重启后丢失；口令需要不可逆存储；内容包与代码版本不一致时静默启动会污染世界。
- **验证方式**：三个提交均附新增测试文件，但提交信息**未记录**实跑结果 → **未记录**。
- **commit**：`88fdbfb`、`ac40a9c`、`23e1d05`。
- **备注**：以上提交由并行工作流完成，本条目按仓库制度补记，未改动其代码。

---

## 2026-11-04 — chore(git): 建立 GitHub 推送链路与变更记录制度

- **改了什么**
  - 为本地仓库添加 `origin = https://github.com/lyinet/mud-world`，推送 `master` 全量历史与全部已有 tag。
  - 新增本文件 `CHANGELOG.md`：按版本倒序补记 p3f–p3j 里程碑。
  - 新增 `docs/GIT-WORKFLOW.md`：提交规范、推送时机、tag 规则、不入库清单、本地钩子说明。
  - 安装本地 `.git/hooks/post-commit`：提交后在后台 `git push origin HEAD`，失败只写 `.git/push.log`，不阻塞提交。
- **为什么改**：用户硬规则要求“每次修改都推送、每次都详细留痕”，且远程仓库刚建立、此前无任何 commit 对应。
- **验证方式**：`git remote -v` 含 origin；`git push -u origin master` 与 `git push origin --tags` 成功；
  `git ls-remote origin` 的 `refs/heads/master` 与本地 `HEAD` 一致；14 个 tag 全部出现在远程。
- **commit**：`f12424f`（制度与两份文档）；`docs(changelog):` 补记提交回填本条哈希与钩子安装命令修正。

---

## 2026-11-04 — delivery-p3j（tag `delivery-p3j` → `184859d`）

- **改了什么**
  - `5635c26` 目标体量校准：长线目标 target 从 600 文 / 0.6 / 40 点降到 142 文 / 0.033 / 12 点，
    按 10 天有效产能 P60 的 6–8 成设定。
  - `bc7013c` 玩家成长线（声望 / 技能 / 资产各 5 级）与 NPC 里程碑链（一级完成后自动建二级、三级）；
    新增维护成本：店铺月租 30 文/家、门派月费 6 文/人、技艺生疏 −0.002、关系衰减 −1.0。
  - `c999fb6` 在线玩家委托供给修复：过期单自动下架、日界按模板补发、拒绝原因可诊断
    （`quest_expired` / `reputation_too_low` / `too_many_quests`）；技能 L2 门槛 0.36 → 0.42、
    资产 L2 门槛 400 → 600。
  - `edb3560` 对外口径与 `known-limitations` 更新；`184859d` p3j 干净解包实测记录。
- **为什么改**：p3i 的目标体量按满产假设设定，实际 10 天产能下大量目标空转；委托会过期下架导致玩家无单可接；
  成长线缺少可见的下一阶段目标与面板。
- **验证方式**（`docs/DELIVERY-TEST-p3j.md`、`docs/RELEASE-NOTES-p3j.md`）
  - `python3 -m pytest`：**321 passed, 8 skipped**；`selfcheck.sh` 4/4 PASS；`framework_check.py` 6/6 PASS；
  - 规则臂 K1 **49.45% → 65.93%**，完成目标 **20 → 43**；攒钱 / 学手艺 / 结关系完成率
    **3.8% / 0% / 0% → 30.8% / 31.2% / 50.0%**；空转目标 38 → 23，未创建目标 22 → 15；
  - 事件数 3366 → 2707（−19.6%）；7 天委托压力测试 14 接 / 14 交 / 0 拒绝；
  - 新增基线 `GOLDEN_MILESTONE`（800 events）与 `PLAYER_SUPPLY_GOLDEN`；旧基线（824 / 791 / 796）显式 skip。
- **内容冻结**：`content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`
  （31 NPC / 41 物品 / 9 地点 / 27 任务模板）；种子 2 日世界 **836 事件 / NPC 自主行动 736**。
- **未达项（如实保留）**：模型在 K1 维度零增益；战略层实验 FAIL（PCR 22.73% < 28%），模型层机制实验永久收工；
  对话与好模板持平（非平手 55.9%，Wilson CI 44.1%–67.1%），默认走模板。
- **commit**：`5635c26`、`bc7013c`、`c999fb6`（引擎），`edb3560`、`184859d`（文档）。

---

## 2026-11-04 — delivery-p3i（tag `delivery-p3i` → `d13ff53`）

- **改了什么**
  - `19c298b` 目标生命周期审计（`reports/goal-lifecycle-audit.md`）。
  - `5833eca` 目标覆盖护栏 v2 与类型配额；**代码保留、默认关闭**。
  - `20dc739` 内容扩充：新增 10 件物品（麻绳 66 笔、墨条 9 笔、小米 2 笔、艾条 2 笔成交，另 38 次消耗）、
    4 步引导链、6 类日常委托、3 条长线目标；31/31 NPC 有关系边
    （朋友 47 / 对手 16 / 债主债户各 13 / 师徒各 5 / 亲族 8）。
  - `64365f8` 供需 + 库存驱动物价接入运行时，单日 ±20% 护栏。
  - `64458bd` 冻结快照 K1 对照与收工判定；`79caed3` 对外口径；`d13ff53` 干净解包实测。
- **为什么改**：p3h 的内容深度不足（摊位 / 引导 / 长线缺失），物价是静态的；K1 连续两轮未达标，需要冻结快照做归因，
  避免把内容扩充带来的提升误记为模型收益。
- **验证方式**（`docs/DELIVERY-TEST-p3i.md`、`docs/RELEASE-NOTES-p3i.md`）
  - `python3 -m pytest -q`：**301 passed, 4 skipped**；`selfcheck.sh` 4/4 PASS；framework 6/6 PASS；
  - 确定性 2 日 seed：事件 **633 → 838（+205，+32.4%）**；2 日 15 件商品中 **12 件有价差**，最高 **+12.5%**；
  - 新增基线 `GOLDEN_P3I_PRICE` = **791 events**（state `86871faf…`、event `ee7d0a90…`、sell 1145 / buy 842）；
  - 冻结快照下规则臂与模型臂 K1 **同为 56.04%（51/91）**，提升归因于内容扩充而非模型；
    模型 438 calls / **$3.03**，第 7 天触发 $3.0 hard 中止。
- **内容冻结**：`content_hash = fac4ac31fec9db5720d829d79827b215f5d0aee217be5fcb57bfdf38165ef8dd`
  （41 物品 / 31 NPC / 9 地点 / 27 任务模板 / 关系边 106）。
- **未达项（如实保留）**：K1 三次尝试均未超过规则臂，模型在该维度零收益、纯成本；对话结论不变（默认模板）。
- **commit**：`19c298b`、`5833eca`、`20dc739`、`64365f8`（引擎/分析），`64458bd`、`79caed3`、`d13ff53`（文档）。

---

## 2026-11-04 — delivery-p3h（tag `delivery-p3h` → `c19585b`，对外首发）

- **改了什么**
  - 对话层：`d780dcf` 接入 `text_guard` 与统一 system prompt（方括号主题词标签 / Markdown / 括号动作 /
    第三人称旁白两侧均为 0，角色口号去重）；`456c093` 收细诉求路由（问旧事 / 问路 / 借物 / 赊账拆开）并重建 r3b；
    `c4d1e66` 盲评扩到 70 对；`451aed3` 构建 r4 最终盲评包。
  - 自主目标（A3）：`f1010a0` 模型目标选择权与动作留存；`9242c1e` `not_together` 丢弃/改选、决策槽回放、成本硬闸；
    `c736849` 目标覆盖护栏与第二轮实测；`335a4ee` 竞态 trade 记 `stale`、intent 归零与护栏收口。
  - `1c046fd` 入库盲评与评测证据；`f22ad3d` 对外口径与打包范围；`205bfe4` 出包跳过 sqlite 二进制副本；
    `2c096c3` 对话评测输入改为 `runs/` 优先、`reports/evidence/a2-dialogue-30/` 回退，保证包内可跑；
    `c19585b` 干净解包实测记录。
- **为什么改**：p3g 存在对话标签泄漏、诉求路由过粗、盲评样本仅 30 对；A3 的 trade 竞态被误计为非法；
  干净包内 5 个测试硬编码 `runs/` 路径导致失败。
- **验证方式**（`docs/DELIVERY-TEST-p3h.md`、`docs/RELEASE-NOTES-p3h.md`）
  - 包内 **287 passed**；`selfcheck.sh` 4/4 PASS；framework 6/6 PASS；
  - K6：严格 **1.142%** / 含剪枝 **1.755%**（均 < 2% PASS），已执行非法 0；53 条 trade 竞态全部记 `stale`；
  - K1：模型 **41/91 = 45.05%** vs 规则 **45/91 = 49.45%**（−4.40pp，FAIL），停止规则生效；
  - K3 转 `diagnostic_only`（verb 17 vs 16、entropy 2.7231 vs 2.5842、pairwise JS 0.1448 vs 0.1087，
    对象层 entropy 5.0185 vs 5.0478）；
  - R5 盲评 70 对：模型 38 胜 / 模板 30 胜 / 平手 2，非平手胜率 **55.9%**，Wilson CI **44.1%–67.1%**；
    模型 70/70 更长（49.0 vs 31.0 字），17 条“更长但更空”全在模型侧；
  - 整 run 回放 `c4fc017c…`、`9b921eb1…` 均 MATCH，回放成本 0；A3 跨轮累计约 **$5.80**，对话生成约 **$0.33**。
- **content_hash**：未记录。
- **未达项（如实保留）**：K1 未达规则臂；K3 对象层 entropy 略低；模型侧套路化动作/口号 87 次。
- **commit**：`d780dcf`、`456c093`、`c4d1e66`、`451aed3`、`f1010a0`、`9242c1e`、`c736849`、`335a4ee`（引擎/评测），
  `1c046fd`、`f22ad3d`、`205bfe4`、`2c096c3`、`c19585b`（文档/打包）。

---

## 2026-11-04 — delivery-p3g（tag `delivery-p3g` → `60e8596`，内部快照）

- **改了什么**
  - `60e8596` 披露 fake run 世界改写、对外改用**含剪枝 K6**口径、补 golden 基线、全量 `blocked` / `pruned` 审计。
  - `7c32fb7` 透传 decision 批处理的 thinking 遥测；`d7f691e` 增加 K1 在局 / 已创建 / 剔除一次性口径；
    `0aa6b19` TalkAction 接入模型回复并支持 8s 超时回退；`cc3a475` 锁定 dialogue 开启路径 golden。
  - 对话 fallback 与路由：`98f0cdf`、`fe77e25`、`e0882b5`、`8f35270`、`e2151e0`、`7ff56c3`、`193fc7a`；
    `2dce941` 重建 R2 盲评包；`7251859` 修正 A2 成本口径。
  - `88ceebe` 更新 p3g 说明与已知限制。
- **为什么改**：原“录制 intent 重跑 = 确定性对照”的表述失实（重跑 220 events、`replay_miss=18`）；
  同 tick 调度里 `talk` 之外动词的顺序依赖制造了无关世界漂移；`blocked` / `pruned` 记录被截断无法对账。
- **验证方式**（`docs/DELIVERY-TEST-p3g.md`、`docs/RELEASE-NOTES-p3g.md`）
  - `pytest` **194 passed**；framework **6/6**；`selfcheck.sh` 4/4 PASS；
  - golden 基线 **824 events**（state `1bd870c4…`）；事件回放 222 events MATCH（state `e607d200…`）；
  - 对外含剪枝 K6：fake 2 天 **7/824 = 0.85%**、deepseek 2 天 **2/222 = 0.90%**（PASS）；严格口径 0.24% / 0.00%；
  - 世界漂移 816 → 824：sell 1165 → 849、buy 956 → 795、burned 112 → 113、商户现金 11925 → 12058。
- **content_hash**：未记录。
- **未达项（如实保留）**：K1 7/91 = 7.69%（FAIL）；`dialogue` 槽 0 次调用，文笔未评测；
  deepseek 新旧两次为独立采样，2 条 `blocked → pruned` 是换桶而非行为改善。
- **commit**：`60e8596`（tag），`7c32fb7`–`193fc7a`（引擎/评测），`88ceebe`（文档）。

---

## 2026-11-04 — delivery-p3f（tag `delivery-p3f` → `943645f`，内部里程碑）

- **改了什么**
  - `1c88cae` 同 tick 调度只保留 `talk` 优先，并补全 `blocked` / `pruned` 记录。
  - `943645f` 首发生成交付包：`start.sh` 一键启动、`scripts/world_feed.py` 世界观察、
    玩家前端断线提示、`tools/build_dist.py` 出包、`docs/QUICKSTART.md` 重写、
    `docs/known-limitations.md`、干净解包实测。
  - `b34317b` 事后标注该版本为过渡版本（包含他人 `headless.py` 在途改动，不作对外最终版）。
- **为什么改**：需要可交付的“一键启动 + 快速开始 + 可玩自检 + 断线提示”，并固定版本与已知限制。
- **验证方式**（`docs/DELIVERY-TEST-p3f.md`）
  - 干净临时目录解包后首次 `./start.sh` 自动建 venv、装 `requirements.lock`、生成 2 日种子世界；
  - 种子世界 **633 条事件 / NPC 自主行动 541 条**；健康检查 `mode=live, upstream_ok=true`；
  - 玩家闭环 enter → state → move → talk → quest_accept 全部 ok；断线后 `mode=degraded, upstream_ok=false`，前端仍 200；
  - `selfcheck.sh` 4/4 PASS：内容校验、**pytest 193 passed**、framework 6/6、玩家闭环。
- **content_hash**：未记录。
- **未达项**：浏览器自动化截图接口报 viewport 不可用（BLOCKED），未保存截图，改用 QUICKSTART 手工走查步骤。
- **commit**：`1c88cae`、`943645f`（交付），`b34317b`（标签说明）。

---

## 更早版本（不逐条展开）

`delivery-p2`、`delivery-p2.1`、`delivery-p2.2`、`p2b-baseline`、`delivery-p3a`–`delivery-p3e`
均已随仓库推送，tag 保留不改写。详细记录见 `reports/delivery-p2*.md`、
`reports/p3a-report.md`–`p3e` 系列与对应 `specs/`；本文件从 p3f 起补记，更早条目未逐条整理。
