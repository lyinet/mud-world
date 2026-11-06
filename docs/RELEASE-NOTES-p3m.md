# Release Notes — delivery-p3m（开服版 · 草案）

> **状态**：草案，**尚未打 tag**。版本号 `delivery-p3m` 由发布统一入口下发；打点前需满足四条门禁
> （`selfcheck` 4/4、`tools/e2e_service_check.py` 全项、2 小时长跑复跑、工作树干净），当前仍在等待。
>
> **基线**：`delivery-p3l`（`d01ba9b`）。本说明覆盖其后的 **109 个提交**（截至编写时 HEAD `ad53f64`）。
> 文中 `【已实测】` 指仓库内有对应报告或命令输出；`【未实测】` 明确标注，不把推断当结论。

## 1. 相对 delivery-p3l 的变化

### 1.1 玩家可见

- **门派系统**（后端 `9c42736`、`ceb7528`；前端 `c6c7023`）：立派、收徒、派俸、驱逐、
  门派金库存取（6 个写动作），门派面板按三种视角展示门槛差额，13 个拒绝码有中文文案。
  【已实测】`tests/test_player_sect_actions.py`、`tests/test_player_sect_api.py`，
  面板证据见 `reports/w4-ui/checks.json`。
- **店铺经营五动作与账本**（五动作 `3d0ceab` / `4195796`；账本与断供归因 `bcfd374`；
  前端 `857172b`、`a7bd4de`）：补货、定价、辞退、提现、看账，加 7 日账本与缺货提示。
  【已实测】`tests/test_shop_operations.py`、`tests/test_player_shop_customers.py`，
  UI 证据见 `reports/w4-shopops/`、`reports/w4-commerce/`。
- **婚姻闭环**（后端 `68d9659`；前端 `2bd5c16`）：求婚裁决、成婚、婚后陪伴、和离；
  面板提供婚姻入口、配偶栏与和离二次确认。【已实测】`tests/test_player_marriage.py`。
- **玩家商业/经营面板**（`6f1f8b2`）：买、卖、现吃、开店、雇人、押镖入口统一。
- **委托**：保供（每天至少 2 条可接）与公平轮转是 **p3l 已有**内容
  （`docs/RELEASE-NOTES-p3l.md` §1.5、`docs/known-limitations.md` §4），本版无变化；本版只增加已完成委托按
  **7 世界日 / 1000 条**清理（`75f2faf`），属服务端生命周期管理，玩家不可见。

### 1.2 运维可见

- **管理控制台默认随栈启动**（`0c18a91`）：后端只挂 `xkx-admin-net`（internal），
  不映射宿主端口；`/admin/` 走 HTTP Basic，`/api/admin/*` 走 Bearer 会话令牌；
  `XKX_ADMIN_PUBLIC=0` 时整个管理路径返回 404。【已实测】`docs/OPS-RUNBOOK.md` §1.5。
- **LLM 预算刹车**（`9ad0026`）：主服务在**每次真实调用前**读预算快照并拦截，
  超限返回 `budget_exhausted`，≤5 秒生效；管理库读/写异常时**失败放行**且不阻断 tick，
  `GET /api/admin/budget` 的 `error` 字段暴露异常。【已实测】`docs/ADMIN-CONSOLE.md`。
- **TLS 反代与管理面隔离**（`45bf5af`、`0c18a91`）：80 端口只做 301 到 HTTPS
  （仅 `/.well-known/acme-challenge/` 明文例外）；TLS 1.2/1.3 + 强套件、HSTS、
  `nosniff`/`X-Frame-Options`/`Referrer-Policy`；`X-Forwarded-For` 用 `$remote_addr`
  覆盖，防客户端伪造。【已实测】`docs/OPS-RUNBOOK.md` §1.4。
- **真 `/api/health`**（`8bb9f30`、`2e4628d`）：校验内容哈希、事件 `seq` 连续性、
  完整性、磁盘余量与自走状态；`seq` 断洞返回 503。
- **`GET /api/metrics`**（`0410511`）：Prometheus 文本格式；回环地址默认开放，
  非回环需 `XKX_METRICS_TOKEN`（或用 `X-Metrics-Token`/Bearer）。
  【已实测】`tests/test_w1_metrics.py`（含非回环无令牌返回 404）；用法见
  `deploy/README.md`「滚动重启」。
- **审计日志轮转**（`116231a`）：按大小轮转，默认 100MB / 保留 5 份
  （`XKX_AUDIT_MAX_BYTES`、`XKX_AUDIT_BACKUPS`）；进程内缓存上限默认 4096 条。
- **备份默认生效**（`328b5d9`、`7b8d51b`）：`backup` 服务默认运行，失败不再静默
  （容器 `Exited` 非零 + `unhealthy`），每轮状态写 `last_backup_status.json`。
  **异机/异地副本**（`7b6bb74`、`tools/backup_offsite.sh`）：默认 `disabled`，
  配置 `XKX_OFFSITE_MODE/TARGET` 后在**远端**逐项校验 sha256/size/manifest，
  不一致即失败。【已实测】`docs/BACKUP-RESTORE.md`。
- **丢库 fail-closed**（`df8b283`）：`service.sqlite` 已有账号但 `events.sqlite`
  缺失 → 退出码 **4**（`events_missing`，可用 `--allow-new-world` 显式放行）；
  事件库 `integrity_check` 不为 `ok` → 退出码 **5**（`events_corrupt`，
  `--allow-new-world` 也不放行）。【已实测】`docs/OPS-RUNBOOK.md`。
- **冷启动管理面 502 修复**（`5bd8125`）：nginx 改用 Docker DNS `resolver` +
  变量式 `proxy_pass`，避免启动时把错误上游缓存住；proxy 健康检查同时探
  `/healthz` 与 `/api/admin/health`，管理面不可用即 `unhealthy`。
  【已实测】从零 `docker compose down` → `up -d`，连续 **30/30** 次探测
  `/api/admin/settings`=401、`/api/admin/health`=200，`502/000 = 0`，四容器全 healthy；
  停 `admin` 55s 后 `proxy` 转 `unhealthy`，`start` 后（未重启 proxy）自动恢复。
  详见 `docs/OPS-RUNBOOK.md` §1.5「proxy 自身也必须探管理面」。
- **滚动重启脚本入库**：`deploy/rolling_restart.py`，`stop_grace_period: 45s` 的依据是
  50 并发 2 小时 soak 实测 SIGTERM→退出 31.24s（`docs/SOAK-RESULTS.md`），
  容器默认 10s 会直接 SIGKILL。

### 1.3 未实测 / 待复跑

- **缺的就是“修复后的 2 小时长跑”这一项，且尚未开跑**：`docs/SOAK-RESULTS.md` 的
  120 分钟实测是**修复前**代码（RSS 增长与 P95 漂移 FAIL，正确性项 PASS）。
  **为什么还没跑**：当前正在修请求路径上的全库校验——`/api/health` 的
  `PRAGMA integrity_check` 仍按 60 秒缓存执行一次全库扫描（`src/wsim/api/health.py`
  `INTEGRITY_CACHE_S=60`），长跑会把这项固定开销计入 P95/RSS，结论不可用；
  修复合入后再按 10 条 check 复跑，未通过前不会打 tag。
- 发布说明中的容量数字来自 `docs/CAPACITY.md`（50–300 席实测），不含修复后的长跑外推。

## 2. 升级影响（必须看）

> **本轮是全新开服，没有存量玩家库**，因此对本次交付不构成实际迁移问题；
> 但任何“沿用旧存档”的升级都会遇到下面的硬阻断。

- `content_hash` 从 `7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`
  变为 `5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4`。
- **旧库启动会被拒绝，退出码 3**，错误 `content_hash_mismatch`；**当前没有迁移路径**。
- 结论：**升级等于开一个新世界**。新内容与旧存档不能在同一库上混用。
- 【已实测】用旧世界目录启动当前版本，输出
  `{"current_content_hash": "5267a8a8…", "stored_content_hash": "7ec670c1…", "error": "content_hash_mismatch", "ok": false}`
  （完整记录见 `docs/known-limitations.md` §13）。
- 镜像回滚**不能**消除这个差异；同库跨版本处置见第 5 节。

## 3. 容量与限制

- **首发对外 150 席**（运营建议/文档口径，见 `docs/RELEASE-NOTES-p3l.md` §4）。
- **实测**（`docs/CAPACITY.md`，读写混合口径）：
  - 安全线 **100 席**：读 <1ms，进程内写动作 P95 约 15.8ms；
  - **>100 席开始劣化**：150 席写动作 P95 958ms、CPU 91%，无余量；
  - **单进程极限约 150 席**：读仍快，写请求排队进入秒级。
- **运行时硬上限是 200，且是硬编码**：`MAX_REGION_PLAYERS = 200`
  （`src/wsim/actions/player.py:41`），**没有** `XKX_PLAYER_CAP` 之类环境变量；
  `deploy/service.yaml` 的 `max_region_players: 200` 只是服务模式标记，代码不读取它。
  所以本版**未把 150 写进运行时**：超过 150 不会自动 429，需要运营按写 P95 监控控制；
  第 201 席才会 `429 player_cap_reached`。
- **200 席的实测来源是 5 分钟压测**（`docs/CAPACITY.md` §6，`tools/soak_test.py`，
  原始数据 `reports/capacity/raw-opt2-soak-200.json`）：写动作 P95 **4974ms（约 5s）**、
  读 P95 52.1ms；错误率 0、`seq_gap` 0、不变量 PASS。**写 P95 约 5 秒属过载**，
  不能当作 200 席可用。
- **上限值当前不可从接口读出**：`/api/health` 与 `/api/service/info` 都不返回席位上限
  字段（现状记录，本版不新造接口）；只能从第 201 席的 429 行为观察。
- **500 席必须分区**：`docs/PARTITIONING.md` 与 `docs/OPS-RUNBOOK.md` §8 都是方案文档、
  **未实现**；最便宜的方案是独立世界实例（每实例一份完整世界 + 100 席，实例间不互通），
  共享世界按区域分区需 6–10 周且运维复杂度更高。

## 4. 已知限制（只列真实存在项）

1. **无告警通道**：已有 `/api/health` 与 `/api/metrics`，但未部署 Prometheus/Alertmanager，
   也没有告警规则；异常要靠 `docker ps` 与状态文件人工巡检（`docs/PRODUCTION-READINESS.md` P1-1）。
2. **世界事件库无 schema 版本/迁移机制**：`admin` 库有 `PRAGMA user_version`，`events.sqlite`
   没有；跨版本只能新开世界（`docs/PRODUCTION-READINESS.md` P1-4，同第 2 节）。
3. **账号生命周期缺失**：没有改密、删号、数据导出接口（`docs/PRODUCTION-READINESS.md` P1-5）。
4. **备份恢复演练未定期自动化**：备份、异机副本与远端校验已有；恢复演练仍为手工步骤
   （`docs/BACKUP-RESTORE.md`、`docs/PRODUCTION-READINESS.md` P1-6）。
5. **管理面访问范围**：管理后端只在 internal 网、无宿主端口；公网访问经反代
   Basic + Bearer，可用 `XKX_ADMIN_PUBLIC=0` 整体 404。管理前端不是公网产品界面
   （`docs/OPS-RUNBOOK.md` §1.5）。
6. **单机部署不引入 docker secret**：密钥只存在宿主 `.env`（已 gitignore）；
   迁到多机/编排平台前不换 `secrets:`（`docs/OPS-RUNBOOK.md` §1.5）。
7. **只验证 macOS**：Python 3.12.7 + colima；Linux 与 Windows/WSL **【未实测】**
   （`docs/known-limitations.md` §9 第 7 条）。
8. **部分配置需重启生效**：`ambient_speed` **不是**热配置（`docs/ADMIN-CONSOLE.md`
   的 `hot` 列表为 false），改后需重启 world；真正热生效的是 `global_enabled`、
   `daily_budget_usd` 等。
9. **四件零成交物品**：`salt_lamp`、`salt_scale`、`rare_herb_box`、`service_night_watch`
   已定位、本轮未修（`docs/known-limitations.md` §12）。
10. **模型不是默认对话路径**：对话默认走模板（零成本、可复现）；真实模型只用于
    spotlight NPC 或玩家主动深聊，失败回退固定兜底句。模型层两次机制实验均 FAIL、
    永久收工（`docs/known-limitations.md` §1.1）。
11. **目标按 10 天窗口校准**：更长周期（72 天等）是否仍成立 **【未实测】**
    （`docs/known-limitations.md` §7 第 2 条、`deploy/README.md`「不要做什么」）。
12. **委托补发只在玩家会话作用域**：NPC 侧不发补发，NPC 委托仍可能因过期减少
    （`docs/known-limitations.md` §4）。
13. **物价单日最多 ±20%**：按日与库存变化，超出幅度的经济行为不会发生
    （`docs/known-limitations.md` §5）。
14. **社交面有限**：只有同地点对话、交易、婚姻、门派；没有好友、私聊、跨地点频道
    （`src/wsim/api/server.py` 无对应路由，检索 `friend|whisper|group_chat` 无命中）。
15. **预算闸的有意降级（不是缺陷）**：预算耗尽时**不中断服务**——真实调用被拒后，
    `LLMReasoner` 按既有降级链回退到 deterministic/mock，tick 继续推进；管理库读/写
    异常时闸门**放行**（fail-open），仅通过 `/api/admin/budget` 的 `last_error` /
    `errors_by_kind` 暴露。这是“先保证世界不停转”的有意选择，不是故障
    （`docs/ADMIN-CONSOLE.md` 预算闸验收：`budget_exhausted=2` 时 tick 未崩）。

## 5. 回滚

- 步骤与数据兼容判定见 **[docs/RELEASE-ROLLBACK.md](./RELEASE-ROLLBACK.md)**：
  镜像发布 §2、回滚步骤 §3、数据兼容清单 §4。
- **回滚镜像不解决 `content_hash` 变化**：同库跨版本必须按 **§4.2** 处置——
  先查 `events.sqlite` 里的 `content_hash`，与当前 `content/` 不一致时
  **不要删卷重建**，继续用旧镜像或新开世界。
- 回滚时不要执行 `down -v`，不要删除命名卷（`docs/RELEASE-ROLLBACK.md` §3 步骤 2）。

## 6. 口径修正记录

本说明在评审中按仓库证据纠正过以下口述数字/判断，留档避免后人误引：

| # | 原口述 / 草案 | 定稿 | 依据 |
|---|---|---|---|
| 1 | 相对 p3l 落后 **101** 个提交 | **109** 个 | `git rev-list --count delivery-p3l..HEAD`（HEAD 已推进到 `ad53f64`） |
| 2 | “委托保供”列为 p3m 新增 | **p3l 已有**（源起 p3j），本版只加 completed 清理 | `docs/RELEASE-NOTES-p3l.md` §1.5、`docs/known-limitations.md` §4、`75f2faf` |
| 3 | “婚姻闭环如已在 p3l 之后”不确定 | **确在 p3l 之后**，列入本版 | `git log delivery-p3l..HEAD`：后端 `68d9659`、前端 `2bd5c16` |
| 4 | 首发 150 席，“超限走 429” | 运行时硬上限 **200**（第 201 席才 429）；150 是运营建议、**不强制** | `src/wsim/actions/player.py:41`、`docs/CAPACITY.md` §6、`reports/production-readiness/capacity-200-check.json` |
| 5 | “200 席可用/极限” | 200 席**属过载**：5 分钟压测写 P95 **4974ms（约 5s）** | `docs/CAPACITY.md` §6、`reports/capacity/raw-opt2-soak-200.json` |
| 6 | 长跑“已复跑/已通过” | 现有 120 分钟数据是**修复前**；修复后**未复跑** | `docs/SOAK-RESULTS.md`、§1.3 |
| 7 | 席位上限“可配置/有 env” | **硬编码常量**，无 `XKX_PLAYER_CAP`；`service.yaml` 同名项不被读取 | `src/wsim/actions/player.py:41`、`src/wsim/api/server.py`（`service/info` 无上限字段） |

## 附：证据索引

| 主题 | 仓库内证据 |
|---|---|
| 容量与席位 | `docs/CAPACITY.md`、`docs/PARTITIONING.md`、`reports/capacity/*.json`、`src/wsim/actions/player.py:41` |
| 长跑 | `docs/SOAK-RESULTS.md`（修复前实测，修复后待复跑） |
| 运维口径 | `docs/OPS-RUNBOOK.md` §1.4 / §1.5 / §8 |
| 管理台与预算 | `docs/ADMIN-CONSOLE.md` |
| 备份与恢复 | `docs/BACKUP-RESTORE.md` |
| 升级影响 | `docs/RELEASE-ROLLBACK.md` §4.2、`docs/known-limitations.md` §13 |
| 发布自检 | `scripts/selfcheck.sh`、`tools/e2e_service_check.py`、`deploy/README.md` |
