# XKX 世界服务运维手册（W5）

本文面向值守同学：如何启动、升级、备份、恢复、清理僵尸会话、看图告警与排查错误码。
所有命令默认在仓库根目录 `world-sim/` 执行。

架构前提（不要擅自改）：

- **单进程、单写者、sqlite**。没有 Redis / Celery / 消息队列；所有写请求经 `WorldSession.lock` 串行化。
- `runs/<RUN_ID>/events.sqlite` 是唯一存档（事件日志 + `meta.content_hash`）。
- `runs/<RUN_ID>/service.sqlite` 是账号与令牌库（PBKDF2）。
- 一个区域上限 200 席（`player_cap_reached` → HTTP 429）；创世预置的 `u_demo` 也占 1 席，所以默认只有 199 个新账号能进入。

---

## 1. 启动

### 1.1 本机（开发/单机）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock

# 引擎/API（默认 127.0.0.1:8020；鉴权 auto 会读取 deploy/service.yaml）
PYTHONPATH=src .venv/bin/python -m wsim.api --root . --run service --port 8020 --auth on

# 玩家前端（另开终端；默认 8030，代理到 8020）
.venv/bin/python ui/player/server.py --upstream http://127.0.0.1:8020 --port 8030
```

健康检查：

```bash
curl -s http://127.0.0.1:8020/api/service/info   # {"ok": true, ...}
curl -s http://127.0.0.1:8030/api/health         # {"ok": true, ...}
```

> **坑：启动不加载 `.env` = 模型开关等效关闭。** `wsim.api`、`wsim.admin`、`sim/run_headless.py`
> 都不会自动读 `world-sim/.env`；必须先 `set -a; . ./.env; set +a` 再启动。
> 验证进程是否真的拿到配置：`ps eww -p <pid> | grep -o 'LLM_[A-Z_]*' | sort -u`；
> 只有文件里有 `LLM_*`、进程环境里没有，模型仍然不会工作。
> 另：玩家 API 的对话模型走独立旁路，开关是 `XKX_DIALOGUE_MODEL=1`（默认关），
> 只挂 `TalkAction`，不会打开 decision / intent / strategy / night_batch 槽。
> `WSIM_DIALOGUE=1` 仅保留给 `sim/run_headless.py` 的自走对话路径；给 `wsim.api`
> 设 `WSIM_DIALOGUE=1` 不会生效。

#### 1.1.1 对话模型旁路（可选，默认关）

**为什么模型台词不能进账本**：`events.sqlite` / `state` / `state_final` / `event_final`
必须逐字节可复现，回放、审计、对账都依赖这一点。模型输出带随机性，一旦写进
`memory_add` 或世界状态，同 seed 两次跑出的哈希就会分叉，golden 也失效。
因此账本永远只写 `agent/dialogue.py::compose_talk` 的确定性模板；模型台词单独落在
`runs/<RUN_ID>/dialogue_aside.jsonl`，键为 `(event_seq, npc_id)`，不属于世界状态，
删掉只影响“听到的那句话”，不影响事件、存档和哈希。

开启（**必须先加载 `.env`**，否则等效关闭）：

```bash
set -a; . ./.env; set +a
export XKX_DIALOGUE_MODEL=1
# 可选：单次模型调用等待上限，默认 8 秒；中转站慢可调大
export XKX_DIALOGUE_TIMEOUT_S=30
PYTHONPATH=src .venv/bin/python -m wsim.api --root . --run <RUN_ID> --port 8031
```

关闭：删除 `XKX_DIALOGUE_MODEL` 或置 `0`，重启进程即可；已落盘的旁路不会再生效，
但可保留供排查。

失败语义（一律静默）：中转站不可用、key 无效、402/预算耗尽、超时，全部丢弃该句，
保留模板台词；玩家动作仍然成功，tick 照常推进，不重试、不回写事件。

排查：

```bash
curl -s http://127.0.0.1:8031/api/health | jq '.llm'
# status: active | disabled | unconfigured
# calls / ok / failures / timeouts / last_latency_s / by_slot.dialogue
# decision_calls 恒为 0（本开关不碰决策槽）
```

清空旁路（可在线删，重启后索引为空，历史对话自动退回模板）：

```bash
rm -f runs/<RUN_ID>/dialogue_aside.jsonl
```

对照文件：

- `runs/<RUN_ID>/dialogue_aside.jsonl`：模型配音，可删，不影响世界；
- `runs/<RUN_ID>/dialogue.jsonl`：对话队列的运行日志（含 fallback 原因），可删；
- `runs/<RUN_ID>/events.sqlite`：唯一世界账本，**里面永远是模板台词**。


### 1.2 容器（一条命令）

```bash
cd deploy
# 可选：把运行时配置放进 world-sim/.env（只读注入，不进入镜像层）
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100 world
```

- API `8020`、前端 `8030` **只绑宿主回环**（`127.0.0.1`），不对公网暴露；
  外部访问统一走 `proxy`（nginx，TLS 终止在 80/443），见 §1.4。
- 存档在命名卷 `xkx-w5-data` 的 `/app/runs/<RUN_ID>/`，同时包含 `events.sqlite` 与 `service.sqlite`。
- 容器以非 root（uid 10001）运行；镜像构建上下文通过 `deploy/Dockerfile.dockerignore` 排除 `.env`。
- 校验镜像确实没有密钥：

```bash
docker run --rm --entrypoint sh xkx/world-sim:w5 -c 'env | grep -Ei "api[_-]?key|secret|token" || echo no-secrets'
```

> W5 已在 colima 29.5.2 / aarch64 上实测：用本仓 `deploy/Dockerfile` 构建镜像后，
> `docker-compose up -d` 一条命令起 API(8020)+前端(8030)，容器 `Health=healthy`、
> uid 10001、镜像内无 `.env`、环境变量无密钥，`/app/runs`（命名卷 `xkx-w5-data`）内同时生成
> `events.sqlite` 与 `service.sqlite`。
> 注意：本机只有独立版 `docker-compose`（legacy builder，不读 `deploy/Dockerfile.dockerignore`，
> 构建上下文会偏大）。生产请用 Docker Compose v2 / BuildKit，`docker compose up -d --build`。
> 容器**默认世界自走**（与 `./start.sh` 一致）：compose/entrypoint 注入 `XKX_AMBIENT=1`、
> `XKX_AMBIENT_CPU=70`、`XKX_AMBIENT_SPEED=1`，不必额外设环境变量。需要静止容器时：
> `XKX_AMBIENT=0 docker compose up -d --force-recreate`。

### 1.3 关键启动参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--root` | `.` | 含 `content/`、`config/`、`runs/` 的根目录 |
| `--run` | `player-demo` | run 目录名，存档在 `<root>/runs/<run>/` |
| `--tick-step` | `6` | 每个玩家动作推进的世界 tick（6=1 小时） |
| `--auth` | `auto` | `auto/on/off`；有 `deploy/service.yaml` 时 auto=on |
| `--auth-iterations` | 生产默认 | PBKDF2 迭代数；压测可降到 `1000`，生产不要降 |
| `--token-ttl` | 默认 24h | 令牌有效期（秒） |
| `--reconnect-grace` | 默认 60s | 断线重连宽限 |
| `--session-db` | `<run>/service.sqlite` | 账号库位置 |

退出码：

- `0` 正常退出；`2` 端口/参数错误；`3` **内容 hash 不符**（拒绝启动，不写任何事件）。

### 1.4 对外入口与 TLS（反向代理，门 3）

**部署形态：后端只在内网/容器网络，公网只碰 proxy。**

| 组件 | 监听 | 暴露范围 |
|---|---|---|
| `xkx-proxy`（nginx 1.27） | 容器 `80`/`443` → 宿主 `${PUBLIC_HTTP_PORT:-80}` / `${PUBLIC_HTTPS_PORT:-443}` | 公网唯一入口 |
| `xkx-world`（API + 前端） | 容器 `8020` / `8030`，宿主发布仅 `127.0.0.1` | 仅本机调试；容器网络内由 proxy 访问 |
| `xkx-admin`（管理控制台） | 容器 `8031`，**不发布任何宿主端口**，只挂 `xkx-admin-net`（`internal: true`） | 仅 proxy（静态 IP `172.30.31.10`）可达 |
| `xkx-backup` | 仅容器网络 | 不对外 |

实测（`netstat` + 跨机访问）：

```text
$ netstat -an -f inet | grep LISTEN | grep -E '\.(80|443|8020|8030) '
tcp4  0 0  *.443       *.*  LISTEN     ← proxy 对外
tcp4  0 0  *.80        *.*  LISTEN     ← proxy 对外（只做 301）
tcp4  0 0  127.0.0.1.8020  *.*  LISTEN  ← 仅回环
tcp4  0 0  127.0.0.1.8030  *.*  LISTEN  ← 仅回环

$ curl -m 3 http://192.168.31.119:8020/api/service/info
curl: (7) Failed to connect … （局域网访问被拒绝，符合预期）
$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8020/api/service/info
200
```

**为什么选 nginx（而不是 caddy）**：

1. 需要把请求体上限严格对齐后端 8KB，并在同一层放行探活路径；
   `client_max_body_size` + `location` 语义直接，`nginx -t` 可静态校验。
2. 证书来自运维自有 CA/自签，不需要 ACME 域名校验；caddy 的自动 HTTPS 优势用不上。
3. 生产普及度高，排障资料多。

**起用**：

```bash
cd deploy
./gen_selfsigned_cert.sh              # 仅演练：生成 deploy/certs/xkx.crt|key
XKX_ADMIN_BASIC_PASS='<强口令>' ./gen_admin_basic_auth.sh   # 管理面 Basic 凭据
# 生产：把运维签发的证书按同名放进去（不入库，deploy/certs/ 已在 .gitignore）
mkdir -p certs/acme                   # ACME HTTP-01 webroot（已随仓库保留占位）

docker compose up -d                  # world + admin + backup + proxy 一起起
docker compose ps                     # xkx-proxy / xkx-admin 应为 healthy
docker exec xkx-proxy nginx -t        # 配置语法校验
```

证书替换后：`docker compose restart proxy`（nginx 启动时读证书，不热加载）。

**证书放置与续期**

| 文件 | 宿主路径 | 容器内 | 权限 |
|---|---|---|---|
| 证书链 | `deploy/certs/xkx.crt` | `/etc/nginx/certs/xkx.crt`（只读） | `644` |
| 私钥 | `deploy/certs/xkx.key` | `/etc/nginx/certs/xkx.key`（只读） | `600` |
| ACME webroot | `deploy/certs/acme/` | `/var/www/certbot`（只读） | 目录 `755` |

- **正式证书**：把 CA 签发的 fullchain 覆盖为 `xkx.crt`、私钥覆盖为 `xkx.key`，
  `docker compose restart proxy`，再用 `openssl s_client -connect <域名>:443 -servername <域名>`
  核对 `Verify return code: 0` 与证书 SAN。
- **ACME HTTP-01 续期**（80 只需放行 `/.well-known/acme-challenge/`，其余是 301）：

```bash
certbot certonly --webroot -w deploy/certs/acme -d xkx.example.com \
  --deploy-hook 'cp /etc/letsencrypt/live/xkx.example.com/fullchain.pem deploy/certs/xkx.crt && \
                 cp /etc/letsencrypt/live/xkx.example.com/privkey.pem deploy/certs/xkx.key && \
                 chmod 600 deploy/certs/xkx.key && docker compose -f deploy/docker-compose.yml restart proxy'
```

  续期失败不会自动降级为 HTTP：证书过期时客户端握手失败，按下方"回滚到 HTTP"处置。
  建议用 `systemd timer`/cron 每天跑一次 `certbot renew --quiet`，并监控
  `openssl s_client` 的 `notAfter`。
- **不要**把证书/私钥写进 compose 或镜像；`deploy/certs/` 只允许保留 `acme/.gitkeep`。

**应急回滚到 HTTP（仅在 TLS 无法恢复时）**

1. 备份模板：`cp deploy/nginx/templates/default.conf.template /tmp/default.conf.template.https.bak`
2. 把 80 端口的 `location /` 从 `return 301 …` 改成玩家后端，并把 443 整段删除（或注释）：
   ```nginx
   location /api/ { proxy_pass http://world:8020; }
   location /        { proxy_pass http://world:8030; }
   ```
   （同时把 443 的 `ssl_*` 行与 `listen 443` 移除；`docker exec xkx-proxy nginx -t` 必须通过）
3. `docker compose up -d --force-recreate proxy`，用 `http://<host>/api/health` 验证。
4. 恢复 TLS：`git checkout -- deploy/nginx/templates/default.conf.template` 后重建 proxy；
   若仓库不可用，用第 1 步的备份覆盖。
5. 回滚期间 HSTS 仍可能让**已经访问过**的浏览器强制 https（`max-age=300`，5 分钟后失效）；
   这也是 HSTS 先给短 max-age 的原因。

**路由与安全策略**（`deploy/nginx/templates/default.conf.template`）：

| 项 | 配置 | 说明 |
|---|---|---|
| HTTP→HTTPS | `return 301 https://$host$request_uri` | 唯一例外：`/.well-known/acme-challenge/`（HTTP-01） |
| 探活 | `location = /healthz` 只在 443 | 80 无明文探活；容器 HEALTHCHECK 用 `wget --no-check-certificate https://…` |
| TLS 协议 | `ssl_protocols TLSv1.2 TLSv1.3` | 1.0/1.1 已废弃，实测握手被拒 |
| 套件 | Mozilla intermediate（仅 AEAD GCM/CHACHA20） | 无 RSA 密钥交换、无 CBC；`ssl_prefer_server_ciphers off` |
| 会话 | `ssl_session_cache shared:SSL:10m`、`timeout 1d`、`ssl_session_tickets off` | 减少握手开销，禁用可被重放的 ticket |
| HSTS | `Strict-Transport-Security: max-age=300` | 先短；调长条件见下 |
| 其他响应头 | `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer` | `always` 生效；带 `add_header` 的 location 内需重复声明 |
| 请求体上限 | `client_max_body_size 8k` | 与后端 `_MAX_BODY_BYTES = 8*1024` 一致 |
| 头部/读超时 | `client_header_timeout 15s`、`proxy_read_timeout 30s` | 与 `XKX_REQUEST_TIMEOUT` 默认 30s 对齐 |
| 来源头 | `X-Real-IP`、`X-Forwarded-For $remote_addr`、`X-Forwarded-Proto $scheme`、`X-Forwarded-Host $host` | XFF 直接写实测来源，客户端自带的 XFF 被覆盖（防伪造）；见下方实测 |
| 隐藏版本 | `server_tokens off` | 不回显 nginx 版本 |

HSTS `max-age` 调长条件：连续 ≥7 天无 https 故障、页面无混合内容、所有入口都走 443
之后，再改 `max-age=31536000`；`includeSubDomains` 要等所有子域都支持 https；
`preload` 需先做子域清单审计且难以快速撤销，最后再做。

**实测（自签，2026-11-06；先贴健康状态）**

```text
$ docker ps --format 'table {{.Names}}\t{{.Status}}'
NAMES        STATUS
xkx-proxy    Up 18 seconds (healthy)
xkx-world    Up 2 minutes (healthy)
xkx-admin    Up 8 hours (healthy)
xkx-backup   Up 8 hours (healthy)
```

```text
# 证书与 https（CA=自签证书本体）
$ echo | openssl s_client -connect 127.0.0.1:443 -servername localhost | openssl x509 -noout -subject -ext subjectAltName
subject=CN = localhost, O = XKX-DEV
X509v3 Subject Alternative Name:
    DNS:localhost, IP Address:127.0.0.1, IP Address:0:0:0:0:0:0:0:1

$ curl -s --cacert deploy/certs/xkx.crt -o /dev/null \
    -w 'code=%{http_code} http_version=%{http_version} ssl_verify=%{ssl_verify_result}\n' https://127.0.0.1/api/state
code=200 http_version=2 ssl_verify=0

$ curl -s --cacert deploy/certs/xkx.crt https://127.0.0.1/api/health
{"ambient": "running", "ambient_last_tick_at": "2026-11-06T03:32:34+0000", …, "ok": true, …}

# 80 只做 301（路径与查询串保留）
$ curl -s -o /dev/null -D - http://127.0.0.1/api/state?x=1
HTTP/1.1 301 Moved Permanently
Location: https://127.0.0.1/api/state?x=1
$ curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' http://127.0.0.1/healthz
301 -> https://127.0.0.1/healthz

# TLS 版本与套件
$ curl -s --cacert deploy/certs/xkx.crt --tlsv1.2 --tls-max 1.2 -o /dev/null -w 'code=%{http_code}\n' https://127.0.0.1/api/health
code=200
$ echo | openssl s_client -connect 127.0.0.1:443 -servername localhost -tls1_3 -CAfile deploy/certs/xkx.crt 2>&1 | grep -E 'New,|Verify return code'
New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
Verify return code: 0 (ok)
$ curl -s --cacert deploy/certs/xkx.crt --tlsv1.1 --tls-max 1.1 -o /dev/null https://127.0.0.1/api/health; echo "curl_exit=$?"
curl_exit=35            ← 握手被拒
$ echo | openssl s_client -connect 127.0.0.1:443 -tls1_1 2>&1 | grep -E 'alert|Protocol'
tlsv1 alert protocol version (SSL alert number 70), Protocol: TLSv1.1 (NONE)
$ curl -s --cacert deploy/certs/xkx.crt --tlsv1.2 --tls-max 1.2 --ciphers AES128-SHA -o /dev/null https://127.0.0.1/api/health; echo "curl_exit=$?"
curl_exit=35            ← 弱套件（CBC/RSA 密钥交换）不在白名单

# 安全响应头（/api/health 与 /admin/ 各取一次）
$ curl -sI --cacert deploy/certs/xkx.crt https://127.0.0.1/api/health | grep -iE 'strict-transport|x-content-type|x-frame|referrer|server:'
server: nginx
strict-transport-security: max-age=300
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: no-referrer
$ curl -sI --cacert deploy/certs/xkx.crt -u 'admin:<口令>' https://127.0.0.1/admin/ | grep -iE 'strict-transport|x-content-type|x-frame|referrer|cache-control'
strict-transport-security: max-age=300
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: no-referrer
cache-control: no-store

# ACME HTTP-01 例外（唯一不跳转的明文路径）
$ curl -s -o /dev/null -w 'acme=%{http_code}\n' http://127.0.0.1/.well-known/acme-challenge/probe.txt
acme=200
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/.well-known/acme-challenge-nope
301
```

管理台（改配置后回归）：`https://127.0.0.1/admin/` 无凭证 **401**、正确 Basic **200
size=8967**；`/api/admin/settings` 无 Bearer 401（Bearer 会话流程见 §1.5）。

`X-Forwarded-*` 是否真的到达后端（临时回显上游，验收后已删除；客户端故意自带伪造 XFF）：

```text
$ curl -s --cacert deploy/certs/xkx.crt -H 'X-Forwarded-For: 1.2.3.4, 5.6.7.8' \
    'https://127.0.0.1/_echo?probe=1'
{
  "client_address": "172.18.0.4",
  "headers": {
    "Host": "127.0.0.1",
    "X-Real-IP": "172.18.0.1",
    "X-Forwarded-For": "172.18.0.1",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "127.0.0.1"
  }
}
```

`X-Forwarded-For` 里输入的两段伪造地址没有出现——被 `$remote_addr` 覆盖。
`client_address` 是 upstream 看到的 TCP 对端 = proxy 容器地址 `172.18.0.4`。

请求体上限对齐：

```text
$ curl -s --cacert deploy/certs/xkx.crt -o /dev/null -w '%{http_code}\n' -X POST --data-binary @9KB.json https://127.0.0.1/api/auth/login
413                    ← nginx 先拒
$ curl -s -o /dev/null -w '%{http_code}\n' -X POST --data-binary @9KB.json http://127.0.0.1:8020/api/auth/login
413                    ← 后端同为 8KB 上限，直连也拒
$ curl -s --cacert deploy/certs/xkx.crt -o /dev/null -w '%{http_code}\n' -X POST --data-binary @7KB.json https://127.0.0.1/api/auth/login
401                    ← 未超限，正常到达后端
```

**来源 IP 的实际语义（重要，未改代码）**

TLS 终止本身不丢来源：nginx 用 `$remote_addr` 生成 `X-Real-IP` / `X-Forwarded-For`，
上面回显已证实。但**应用侧不读这些头**：

- 玩家 API `src/wsim/api/server.py::_client_ip()` 明确只取 TCP 对端，
  注释即"不信任 X-Forwarded-For（可伪造）"。经 proxy 后，对端固定是 proxy 容器地址。
- 管理台 `src/wsim/admin/server.py::_check_ip()` 同样取 TCP 对端；proxy 的
  `172.30.31.10` 是白名单里唯一放行的地址。

实测（经 https 反代发起一次失败登录后查库）：

```text
$ curl -s --cacert deploy/certs/xkx.crt -H 'Content-Type: application/json' \
    -d '{"username":"ghost-probe","password":"wrong-pass-123"}' https://127.0.0.1/api/auth/login
{"code": "auth_invalid_credentials", "ok": false, …}
$ docker exec xkx-world python -c "import sqlite3; c=sqlite3.connect('/app/runs/service/service.sqlite'); \
    print(c.execute(\"select ip,account from login_failures where account='ghost-probe'\").fetchall())"
[('172.18.0.4', 'ghost-probe')]      ← proxy 在默认网络中的地址，不是真实客户端
```

因此登录失败限流（键为 `(ip, account)`）与按 IP 审计**按 proxy 地址计数，即全局
per-account 预算**，不是"每个真实客户端一份"。这不是 TLS 终止造成的（明文反代也一样），
而是后端有意不信任 XFF。处置：

- 单机/单入口部署可接受（等于"全局失败预算"），误锁用
  `accounts.clear_login_locks()` 解锁（见 §5）。
- 若要按真实客户端限流，需后端在可信代理后读取 XFF（属于 API 域，另立任务）；
  在那之前，公网入口前应再加一层按真实 IP 的连接/请求速率限制（WAF/LB）。
- 管理台白名单只放行 proxy 固定 IP 是**预期行为**：不要为了"看到真实 IP"把
  `XKX_ADMIN_ALLOW_IPS` 放宽成公网网段。

**故障排查**：

| 现象 | 原因 | 处置 |
|---|---|---|
| `xkx-proxy` 起不来，日志 `cannot load certificate` | 证书缺失/文件名不符/权限 | 跑 `deploy/gen_selfsigned_cert.sh`，确认 `deploy/certs/xkx.crt`/`xkx.key` 存在且 `644`/`600` |
| `xkx-proxy` 一直 `unhealthy`，`/healthz` 404 | 模板名不是 `default.conf.template`，镜像自带站点抢占 80 | 改回文件名并重建 proxy（见文件头注释） |
| 443 握手成功但 `/api/*` 502 | world 未 healthy 或服务名解析失败 | `docker compose ps`、`docker logs xkx-world`；确认 upstream 为 `world:8020` |
| 浏览器提示证书不受信任 | 自签证书 | 演练预期；生产换正式证书 |
| 浏览器仍强制 https（已回滚到 HTTP） | HSTS 已缓存 | `max-age=300`，最多 5 分钟自动失效；期间用无痕/新配置访问 |
| ACME 校验 404 | 文件路径不是 `<webroot>/.well-known/acme-challenge/<token>` | 确认 `deploy/certs/acme/.well-known/acme-challenge/` 下有文件；`root` 指向 webroot |
| 弱协议客户端连不上 | TLS1.0/1.1 已禁用 | 升级客户端；不要为兼容降级 `ssl_protocols` |
| 管理页缺少安全头 | location 内 `add_header` 覆盖 server 级 | 在 `/admin/` 内重复声明（模板已处理） |
| 大请求返回 nginx 的 413 HTML 而非 API JSON | 请求体 > 8KB 在 proxy 层被拒 | 预期行为（阈值与后端一致）；客户端应按 8KB 控制 |
| 所有客户端共享登录失败计数 | 后端按 TCP 对端限流且不读 XFF | 见上"来源 IP 的实际语义"；单入口下按全局预算处理 |

### 1.5 管理控制台（admin 服务 + 反代隔离）

**部署形态**

| 项 | 取值 | 说明 |
|---|---|---|
| 服务 | `xkx-admin`（同一镜像 `xkx/world-sim:w5`） | `entrypoint: python -m wsim.admin`，`--root /app --run ${RUN_ID:-service} --host 0.0.0.0 --port 8031` |
| 数据 | 命名卷 `xkx-w5-data` → `/app/runs` | 与 `xkx-world` **同一个 `service.sqlite`**，不是副本 |
| 网络 | `xkx-admin-net`（`internal: true`，`172.30.31.0/24`） | admin 只在此网内；**没有任何宿主端口映射** |
| 反代地址 | `xkx-proxy` 在 `admin-net` 固定 `172.30.31.10` | `XKX_ADMIN_ALLOW_IPS` 只白名单这个地址 |
| 启动顺序 | `depends_on: world: service_healthy` | world 未 healthy 时 admin 不启动 |

容器内没有对外的 http 端口：`docker inspect xkx-admin` 的 `NetworkSettings.Ports` 为
`{"8020/tcp":null,"8030/tcp":null}`（镜像 EXPOSE 的遗留，`8031` 未被 publish），
网络只有 `xkx-admin-net=172.30.31.2`。宿主直连 `127.0.0.1:8031` 为 `connection refused`。

**`XKX_SECRET_KEY` 的唯一来源：`world-sim/.env`（`env_file: ../.env`）**

- `world` 与 `admin` 都只声明 `env_file: ../.env`（`required: false`），**共用同一份密钥**；
  compose 的 `environment` 里**不写** `${XKX_SECRET_KEY}`/`${XKX_SECRET_KEY:-}`。
  原因：compose 的 `environment` 优先级高于 `env_file`，宿主未导出时会把 `.env` 里的密钥
  覆盖成空串 —— 表现就是"密钥看似配了，库里的 provider key 解不开，静默回退环境变量"。
- 镜像里不包含任何密钥；`docker-compose.yml` 中对 `XKX_SECRET_KEY` 的引用只出现在注释里
  （`grep -n 'XKX_SECRET_KEY' deploy/docker-compose.yml` 仅 4 行注释，无赋值）。
- 密钥只存在于宿主 `.env`，`.env` 已在 `.gitignore`。当前单机形态不引入 docker secret；
  若改为多机/编排平台托管，再换成 `secrets:` 并把两处 `environment` 同步改掉。

实测两容器拿到同一主密钥（只比较 sha256，不打印密钥）：

```text
$ W=$(docker exec xkx-world sh -c 'printf %s "$XKX_SECRET_KEY" | sha256sum | cut -d" " -f1')
$ A=$(docker exec xkx-admin sh -c 'printf %s "$XKX_SECRET_KEY" | sha256sum | cut -d" " -f1')
world=5644e4e311dd35e18ea5958c18e5e478ae590334c4f14171fa101bf42c48c4e5
admin=5644e4e311dd35e18ea5958c18e5e478ae590334c4f14171fa101bf42c48c4e5
MATCH

$ docker exec xkx-world sh -c 'ls -l /app/runs/service/service.sqlite'
-rw-r--r-- 1 app app 53248 Nov  5 19:35 /app/runs/service/service.sqlite
$ docker exec xkx-admin sh -c 'ls -l /app/runs/service/service.sqlite'
-rw-r--r-- 1 app app 53248 Nov  5 19:35 /app/runs/service/service.sqlite
```

**访问路径与两层鉴权（路径不与玩家入口混用）**

| 路径 | 反代层 | 应用层 | 说明 |
|---|---|---|---|
| `/admin`、`/admin/` | HTTP Basic（`admin.htpasswd`） | 静态页 | `deploy/certs/admin.htpasswd` 由 `deploy/gen_admin_basic_auth.sh` 生成，不入库 |
| `/api/admin/login` | HTTP Basic | `XKX_ADMIN_TOKEN` 换 24h 会话 | 浏览器已缓存 Basic，登录请求自动带 |
| `/api/admin/*`（其余） | 不加 Basic | `Authorization: Bearer <session>` | **不能加 Basic**：前端发 Bearer 会覆盖同一个 `Authorization` 头，导致 401 |
| 玩家 `/api/*`、`/` | 无 | 原样 | 管理路径是独立前缀，玩家入口不受影响 |

Basic 凭据生成：

```bash
cd deploy
XKX_ADMIN_BASIC_USER=admin XKX_ADMIN_BASIC_PASS='<强口令>' ./gen_admin_basic_auth.sh
# 写入 deploy/certs/admin.htpasswd（600），该目录已被 .gitignore 忽略
```

**公网屏蔽开关**：compose 的 `XKX_ADMIN_PUBLIC=0` → 渲染 `ADMIN_ENABLED=0` →
`/admin`、`/admin/`、`/api/admin/*` **全部返回 404**（不暴露路径是否存在），玩家路径不受影响。
默认 `1`（开放）。修改后需 `docker compose up -d --force-recreate proxy`（环境变量在容器创建时渲染）。

**健康检查**（`deploy/admin_healthcheck.py`，`XKX_ADMIN_REQUIRE_SECRET=1` 默认）：

- `/api/admin/health` 不可达 / 非 200 / `ok=false` → `unhealthy`，exit 1；
- `master_key=false` → `unhealthy`（显式拒绝"密钥缺失却静默回退 env"）；
- 正常 → `healthy: admin v… settings_version=… providers=… master_key=…`，exit 0。

**proxy 自身也必须探管理面**（`docker-compose.yml` 的 proxy healthcheck）：

```text
wget -q --no-check-certificate -O /dev/null https://127.0.0.1/healthz
&& wget -q --no-check-certificate -O /dev/null https://127.0.0.1/api/admin/health
```

只探 `/healthz` 会让 "admin 冷启动 502、proxy 仍 healthy" 的情况被掩盖（独立审计实测
24/24 502）。`/api/admin/health` 免 Basic，经反代直达 admin：`200`=可达，`502/000`=失败。
任一失败 → proxy `unhealthy`（`start_period: 60s` 给冷启动留窗口）。

**上游解析必须用变量式 `proxy_pass`**：

```nginx
resolver 127.0.0.11 valid=10s ipv6=off;   # Docker 内置 DNS
set $admin_api admin:8031;                # world_api / world_ui 同理
proxy_pass http://$admin_api;
```

静态 `proxy_pass http://admin:8031` 只在 nginx 启动时解析一次；冷启动时 admin 还没在
Docker DNS 注册，nginx 会把错误地址（实测 `198.18.3.188:8031`）缓存下来，之后 admin
healthy 也持续 502，只能 `docker restart proxy`。变量式让每次请求按当前 DNS 解析。

冷启动实测（`docker compose down` → `up -d`，不 restart）：

```text
$ docker compose down && docker compose up -d
xkx-world Healthy / xkx-admin Started / xkx-proxy Started
all healthy after 6s

$ docker ps --format 'table {{.Names}}\t{{.Status}}'
NAMES        STATUS
xkx-proxy    Up 6 seconds (healthy)
xkx-admin    Up 6 seconds (healthy)
xkx-world    Up 12 seconds (healthy)
xkx-backup   Up 12 seconds (healthy)

# 连续 30 轮探测（settings 无 Bearer 应 401，admin health 应 200；502/000 计失败）
settings=401 admin_health=200   …（30 轮相同）
FAILURES(502/000)=0 / 30
```

失败可见性回归（停掉 admin，不 restart proxy）：

```text
$ docker stop xkx-admin && sleep 55
xkx-proxy    Up About a minute (unhealthy)
proxy_health=unhealthy failing=3
$ docker start xkx-admin && sleep 35
xkx-proxy    Up 2 minutes (healthy)
xkx-admin    Up 35 seconds (healthy)
admin_health=200
```

**实测（2026-11-06，整套 compose）**

```text
# 页面：无凭证 / 错误凭证 / 正确凭证
$ curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/admin/
401
$ curl -sk -D - -o /dev/null https://127.0.0.1/admin/ | grep -i www-authenticate
www-authenticate: Basic realm="XKX Admin"
$ curl -sk -o /dev/null -u 'admin:wrongpass' -w '%{http_code}\n' https://127.0.0.1/admin/
401
$ curl -sk -o /dev/null -u 'admin:<正确口令>' -w '%{http_code} size=%{size_download}\n' https://127.0.0.1/admin/
200 size=8967

# 应用层：无会话 / 有会话
$ curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/api/admin/settings
401
$ curl -sk -u 'admin:<正确口令>' -X POST -H 'Content-Type: application/json' \
    -d '{"token":"<XKX_ADMIN_TOKEN>"}' https://127.0.0.1/api/admin/login
{"ok": true, "token": "…", "expires_at_iso": "2026-11-06T19:46:25Z"}
$ curl -sk -o /dev/null -w '%{http_code} size=%{size_download}\n' \
    -H "Authorization: Bearer <session>" https://127.0.0.1/api/admin/settings
200 size=7611
```

`settings` 顶层键（真实返回）：`budget / capabilities / capabilities_evidence /
capabilities_last_check_at / capabilities_status / env_bridge / hot / hot_notes / llm /
providers / routing / version / world / world_state`；
`capabilities_status` 为
`{"dialogue":"unconfigured","night_batch":"unconfigured","persona_gen":"stub","quest_gen":"stub",
"self_goal":"stub","strategy":"unconfigured","validation":"stub"}`。

```text
$ curl -sk -H "Authorization: Bearer <session>" https://127.0.0.1/api/admin/providers
providers=0 capability_slots=7
slots: dialogue, night_batch, persona_gen, quest_gen, self_goal, strategy, validation
```

来源 IP 白名单（从同网另一容器直连，非 proxy）：

```text
$ docker run --rm --network xkx-admin-net --entrypoint python xkx/world-sim:w5 -c '…'
HTTP 403 {"code": "admin_ip_not_allowed", "detail": "", "ok": false}
```

公网屏蔽开关（`XKX_ADMIN_PUBLIC=0` 重建 proxy 后）：

```text
/admin/            -> 404
/api/admin/settings -> 404
/api/service/info   -> 200      ← 玩家入口不受影响
```

健康检查与"失败必须可见"：

```text
$ docker exec xkx-admin python /app/deploy/admin_healthcheck.py
healthy: admin v1.0.0 settings_version=0 providers=0 master_key=True
exit=0
$ docker exec -e XKX_ADMIN_HEALTH_URL=http://127.0.0.1:9/api/admin/health \
    xkx-admin python /app/deploy/admin_healthcheck.py
unhealthy: 管理接口不可达 URLError: <urlopen error [Errno 111] Connection refused>
exit=1

# 进程被冻结（等价"管理服务不响应"）
$ docker pause xkx-admin
$ docker ps --format '{{.Names}}\t{{.Status}}' | grep xkx-admin
xkx-admin	Up 9 minutes (Paused)
$ docker inspect xkx-admin --format 'docker_health={{.State.Health.Status}}'
docker_health=unhealthy
$ curl -sk -m 40 -o /dev/null -w '%{http_code} after %{time_total}s\n' \
    https://127.0.0.1/api/admin/settings
504 after 30.113570s          ← 反代在后端无响应时明确失败，不是静默 200

# 容器停止（不是"静默 Up"）
$ docker stop xkx-admin
$ docker ps -a --format '{{.Names}}\t{{.Status}}' | grep xkx-admin
xkx-admin	Exited (0) Less than a second ago
$ curl -sk -m 15 -o /dev/null -w '%{http_code}\n' -u 'admin:<正确口令>' \
    -X POST -H 'Content-Type: application/json' -d '{"token":"x"}' https://127.0.0.1/api/admin/login
504
$ docker start xkx-admin && sleep 25
xkx-admin	Up 25 seconds (healthy)
```

**故障排查**

| 现象 | 原因 | 处置 |
|---|---|---|
| `/admin/` 401（浏览器弹 Basic 框） | 未带/错 Basic 凭据 | 用 `admin.htpasswd` 对应账号；htpasswd 丢了重跑 `gen_admin_basic_auth.sh` |
| `/admin/` 404 | `XKX_ADMIN_PUBLIC=0` 或少了尾部 `/` | 改回 `1` 并重建 proxy；`/admin` 会 301 到 `/admin/` |
| `/api/admin/*` 401 `unauthorized` | 无 Bearer 或会话过期（24h） | 重新 `POST /api/admin/login` 取会话 |
| `/api/admin/*` 403 `admin_ip_not_allowed` | 来源不是 proxy 固定 IP | 确认请求经 proxy；直连只允许容器内回环或精确命中 `XKX_ADMIN_ALLOW_IPS`（不支持 CIDR） |
| `/admin/` 或 `/api/admin/*` 504 | admin 暂停/卡死，反代读超时 30s | `docker ps` 看健康；`docker logs xkx-admin`；必要时 `docker restart xkx-admin` |
| 冷启动后 `/api/admin/*` 持续 502，`docker restart proxy` 才恢复 | 静态 `proxy_pass` 在启动时缓存了错误上游 | 确认模板用 `resolver 127.0.0.11` + `proxy_pass http://$admin_api`（见上） |
| `xkx-proxy` healthy 但管理面 502 | proxy 健康检查没覆盖 admin | 确认 healthcheck 同时探 `/api/admin/health` |
| `/api/admin/login` 502/504 | admin 未启动或不在 `admin-net` | `docker compose ps`；确认 `xkx-proxy` 的 `admin-net` IP 仍为 `172.30.31.10` |
| `xkx-admin` 一直 `unhealthy`，日志 `master_key=false` | `.env` 缺 `XKX_SECRET_KEY` 或值被覆盖 | 检查 `world-sim/.env`；确认 compose 未用 `${XKX_SECRET_KEY}` 注入；重建 admin |
| `xkx-admin` `unhealthy`，`admin_ip_not_allowed` 满日志 | `XKX_ADMIN_ALLOW_IPS` 与 proxy 实际 IP 不一致 | 核对 `admin-net` 静态 IP 与 `ALLOW_IPS` |

> 管理面文档与代码不一致时以代码为准（`GET /api/admin/settings` 的真实字段与
> `admin_audit.action` 取值见 [docs/ADMIN-CONSOLE.md](ADMIN-CONSOLE.md)）。

---

## 2. 升级

1. **先备份**（见第 3 节）。
2. 拉代码/镜像并构建：`cd deploy && docker compose build`。
3. 检查 `content/` 是否变化：若内容变更，`content_hash` 会变，旧存档会拒绝启动（退出码 3）。这是保护机制，不是故障。
4. 滚动重启（单机只有一个写者，必须停旧起新，不能双写）：

```bash
docker compose up -d --build   # 会重建容器并挂回同一卷
docker compose logs -f world   # 看到 [player-ui] mode=live 与 API 监听
```

5. 升级后自检：`pytest -q`、`bash scripts/selfcheck.sh`、`curl /api/service/info`。

---

## 3. 备份

唯一事实源是 `runs/<RUN_ID>/events.sqlite`；`service.sqlite` 必须一起备份（账号/令牌）。
**统一使用 `tools/backup_snapshot.py`**（sqlite 在线备份 API，不用 `cp`）：

```bash
# 宿主机（仓库根）
.venv/bin/python tools/backup_snapshot.py --root . --run service

# 容器内（镜像已带 tools/；等价于 backup 容器每轮的循环动作）
docker exec xkx-world python /app/tools/backup_snapshot.py --root /app --run service

# 异地副本（推送后在远端取 sha256 与 manifest 逐项比对）
XKX_OFFSITE_MODE=rsync XKX_OFFSITE_SSH_HOST=backup-host XKX_OFFSITE_TARGET=/srv/xkx-backups \
  sh tools/backup_offsite.sh --backup-dir backups
```

产物在 `backups/<UTC 时间戳>/`（`events.sqlite`、`service.sqlite`、`manifest.json`）。
完整字段、轮转策略（48 小时滚动 + 最近 7 日每日 1 份）、删除安全校验、定时方案与
实测 RPO/RTO 见 [docs/BACKUP-RESTORE.md](BACKUP-RESTORE.md)。

要点：

- 不要只 `cp events.sqlite` 而不拷 `-wal`/`-shm`；要么停服后拷贝，要么用 `.backup`。
- 备份工具在服务运行中执行，`manifest.json` 里有 `sha256`、`seq`、`content_hash`、
  `state_hash`，可直接用于恢复校验。
- 备份目录权限 `700`、文件 `600`（含账号哈希与令牌，勿对全员开放）。

---

## 4. 恢复

1. **停止服务**（单写者，禁止边恢复边写）：`docker compose stop world` 或 kill API 进程。
2. 选备份：看 `manifest.json` 的 `created_at` 与 `seq.max`，选故障前最近一份。
3. 校验并装回（非零退出即中止）：
   ```bash
   .venv/bin/python tools/restore_verify.py --root . \
     --backup-dir backups/20261105T172730Z --apply-dir runs/service
   ```
4. 启动并验证：
   ```bash
   curl -s http://127.0.0.1:8020/api/service/info
   curl -s -o /dev/null -w '%{http_code}\n' \
     'http://127.0.0.1:8020/api/state?account_id=<account>'     # 期望 200
   .venv/bin/python tools/load_test.py --verify-only --run-dir runs/service
   ```

`restore_verify.py` 逐项校验：文件 sha256/大小、`integrity_check=ok`、seq 连续且
min/max/count 与 manifest 一致、重放 `state_hash` 一致、`content_hash` 与当前内容一致、
`event_hash` 一致。任何一项失败退出码为 `2`，**不要继续起服**。

5. 内容 hash 不符（起服退出码 3）：说明备份对应旧内容或内容被改。要么恢复对应版本的
   `content/`，要么用新内容开新 run；**不要手改 hash 绕过**。
6. `service.sqlite` 回滚后，备份之后签发的令牌/新账号会失效，通知玩家重新登录。

---

## 5. 僵尸会话清理

现象：玩家已断线，但 `online=true` / 仍占席位；或大量 `player_offline` 拒绝（历史现象，见 7.3 已修复）。

W1 契约：断线 60 秒内可重连；超时后会话作废但**存档保留**，玩家可重新 `enter`。

处理顺序：

1. 让玩家重新 `POST /api/player/enter`（会恢复同一角色，不重复发放初始资产）。
2. 若账号/令牌异常，调用 `POST /api/auth/logout` 或等待 TTL 过期；查看令牌：

```bash
sqlite3 runs/service/service.sqlite \
  "SELECT account_id, datetime(expires_at,'unixepoch'), datetime(last_seen_at,'unixepoch'), status
   FROM sessions ORDER BY last_seen_at DESC LIMIT 20;"
```

3. 清理过期令牌（**停服后**执行，运行中直接写 service.sqlite 会与 API 竞争）：

```bash
docker compose stop world
sqlite3 runs/service/service.sqlite \
  "DELETE FROM sessions WHERE expires_at < strftime('%s','now') OR status='revoked';"
docker compose start world
```

4. 若席位被已不存在的玩家占用（数据修复场景），从事件日志确认该 `player:*` 的 `player_leave`/`presence` 状态，再决定是否重放/开新 run；**不要直接改 events.sqlite**。

### 5.1 世界库不做删除式清理（纪律）

- **世界库不做删除式清理；要干净就新开世界。测试账号可删凭证，事件保留。**
- 原因：`events.sqlite` 是 append-only 事实源，`seq` 连续性同时被 `health`、备份 manifest、
  `replay`、`restore_verify` 依赖。为抹掉几条测试痕迹去 `DELETE FROM events` 会制造
  `seq_gap`，等于拿完整性契约换表面干净——**禁止**。
- 要“干净”的唯一正规路径是**换 `RUN_ID` 开新世界**（旧 run 目录整体归档），不是改旧库。
- 测试账号只删**凭证**，保留事件与世界内实体：

```bash
docker compose stop world   # 运行中直接写 service.sqlite 会与 API 竞争
docker compose exec -T world python - <<'PY'
import sqlite3
c = sqlite3.connect('/app/runs/service/service.sqlite', timeout=10)
for u in ('test_account_1', 'test_account_2'):
    c.execute("DELETE FROM users WHERE username=?", (u,))
    c.execute("DELETE FROM sessions WHERE account_id=?", (u,))
    c.execute("DELETE FROM login_failures WHERE account=?", (u,))
    c.execute("DELETE FROM login_locks WHERE account=?", (u,))
c.commit()
PY
docker compose start world
```

删完必须复核 `/api/health`：`seq_gap` 仍为 0、`integrity=ok`；`events` 条数与 `max(seq)` 不变。

当前实际状态（2026-11-06）：验证账号 `u_dockcheck`、`stopcheck`、`tlsgate3` 的凭证已全部删除
（`users` 0 行、`sessions` 0 行），三者的测试事件保留；`u_dockcheck` 残留
seq 37116 / 37117 / 37119 三条（`player_enter` / `player_endowment` / `move`），
`/api/health` 报 `seq_gap=0`、`integrity=ok`。未执行 `down -v`、未做备份回滚。

---

## 6. 容量告警阈值

单区硬上限 200 席，第 201 席返回 `429 player_cap_reached` 属正常限流，不计入错误率。

W5 修复后（commit `500369e3`）实测容量曲线（50 并发口径 `think=3s`，单进程单机）：

| 席位 | 请求/s | `/api/state` P95 | CPU（单核%） | 结论 |
|---|---|---|---|---|
| 50（10 分钟） | 49 | 29ms | 37% | 验收通过 |
| 100（60s） | 68 | 33ms | 43% | 余量充足 |
| 150（60s） | 100 | 156ms | 79% | 接近 300ms 门槛 |
| 199（120s） | 85（被延迟反压） | 1,762ms | 90% | 单核已饱和，超 300ms |

**首发交付边界（按 W5 实测定）**：单区按 **150 席对外**；200 席只承诺“可连入、第 201 席返回 429”，
**不承诺 P95**（199 席实测 1,762ms）。要对外承诺 300ms，必须按下面的分区方案扩容。

- **单进程/单区在 300ms P95 下的实际上限约 150–165 席**（瓶颈是 GIL + 全局 `WorldSession.lock`，见 7.3）。
- 199 席实测 `/api/state` P95 = **1,762ms**、CPU 90%（单核饱和），所以 200 席不是“再挤一挤”的量。
- 外推到 500 席：按 150 席 ≈ 100 req/s、每请求约 7–9ms CPU 估算，500 席约需 311 req/s ≈ **2.5–3 核**的纯计算量；
  按每区 ≤150 席拆 **3–4 个区（每区 125–167 席）**，保守按 **每区 4 核 / 2GB 预留（整机 12–16 核 / 8GB）**，
  实测 4 核 / 2GB 整机即可跑满 500 席的计算量，每区 4 核属于冗余预留。内存按 0.3–0.5MB/席 ≈ 250–350MB。
- 单机“一个 500 席大区”不可行：单区硬上限 200，且单进程在 ~160 席后延迟即越过 300ms；扩席位靠**加区（加进程）**，不是加单进程线程。

建议告警（连续 5 分钟）：

| 指标 | Warning | Critical | 说明 |
|---|---|---|---|
| `GET /api/player/state` P95 | >300ms | >600ms | 验收门槛 300ms |
| 任意写端点 P95 | >500ms | >1000ms | 写请求串行，先看锁等待 |
| HTTP 5xx | >0 | >0 持续 1 分钟 | 任何 500 都要当事故 |
| HTTP 401 | >1%/分钟 | >5%/分钟 | 令牌风暴/客户端时钟漂移 |
| 错误率（非业务拒绝） | >0.5% | >2% | 与验收口径一致 |
| `player_cap_reached` 429 | 持续出现 | 持续出现 + 玩家投诉 | 说明需要扩区，不是扩容单机 |
| 进程 RSS | >1.5GB | >2.5GB | 实测 200 席峰值约 100MB |
| CPU（单核百分比） | >70% | >90% | 单进程受 GIL 限制，单核到顶即延迟拐点 |
| `events.sqlite` 事件数 | >200,000 | >500,000 | 写路径已改为按 seq 取尾，事件数不再线性抬延迟；此阈值只用于备份/归档规划 |
| `events.sqlite` 文件 | >200MB | >500MB | 备份/恢复窗口同步变长 |
| WAL 文件 | >128MB | >512MB | 检查长事务/备份拖尾 |

排查第一步永远是：

```bash
curl -s http://127.0.0.1:8020/api/service/info
.venv/bin/python tools/load_test.py --verify-only --run-dir runs/service
tail -100 runs/service/*.log 2>/dev/null
```

---

## 7. 故障排查 / 错误码对照

### 7.1 HTTP 层

| HTTP | code | 含义 | 处置 |
|---|---|---|---|
| 400 | `bad_json` | 请求体不是合法 JSON | 检查客户端 |
| 400 | `bad_args` / `bad_display_name` / `bad_archetype` | 参数/名字/职业不合法 | 角色名 2–12 字符且唯一 |
| 401 | `auth_missing_token` | 没带 Bearer | 客户端补头 |
| 401 | `auth_invalid_token` / `auth_expired_token` | 令牌无效/过期 | 重新登录或 `POST /api/auth/refresh` |
| 403 | `auth_account_mismatch` | 令牌账号与请求账号不一致 | 不要跨账号用令牌 |
| 409 | `auth_username_taken` | 注册重名 | 换用户名；压测复用账号时属预期 |
| 400 | `auth_bad_password` / `auth_invalid_credentials` | 密码错误 | 检查密码 |
| 429 | `player_cap_reached` | 200 席已满 | **容量拒绝，不是错误**；扩区或等待空位 |
| 429 | `endowment_cap_reached` | 单区初始资产总额触顶 | 同上；注意 `u_demo` 也占额度 |
| 404 | `quest_not_found` | 委托不存在 | 刷新 `/api/player/state` |
| 500 | — | 未预期异常 | 立刻保留日志与 events.sqlite，按第 7.3 节定位 |

### 7.2 业务拒绝（HTTP 200 + `ok=false`）

| code | 含义 | 处置 |
|---|---|---|
| `player_not_entered` | 账号还没进世界 | 先 `POST /api/player/enter` |
| `player_offline` | 玩家被判定离线 | 重新 `enter`；见第 5 节 |
| `player_consent_required` | 需要玩家同意条款 | 客户端确认后再进入 |
| `quest_not_open` | 委托未开放/已被接走 | 刷新委托板 |
| `quest_expired` | 委托过了截止 tick | 放弃，重新接单 |
| `quest_item_missing` | 交付缺少物品 | 先完成前置动作 |
| `quest_not_accepted_by_actor` | 不是接单人交付 | 用接单账号操作 |
| `bad_location` | 目标地点不可达/不合法 | 检查出口与移动路径 |
| `bad_message` | 对话内容为空/过长 | 2–200 字符 |
| `endowment_already_granted` | 初始资产只发一次 | 正常，忽略 |

### 7.3 高负载现象与修复状态（W5 实测）

已修复（commit `500369e3`，改前 → 改后，同一 50 并发 ×10 分钟口径）：

| 现象 | 改前 | 改后 |
|---|---|---|
| `/api/state` P95 | 3,700ms | 29ms |
| 全端点 P95 | 3,750ms | 40ms |
| 单线程写吞吐（17k 事件） | 4.28 次/s | 95.8 次/s |
| 每次写的 `read_all` | 1 次（≈160ms） | 0 次（`EventTail.event_at(seq)` 单行查询） |
| `player_offline` 业务拒绝 | 4,727 次 | 0 次（wall-clock 30s 活跃 / 60s 离线） |
| 每个世界日系统事件 | 5 条 | 1 条（`system_tick` 合并日界结算） |

修复后的**剩余瓶颈**（尚未改，扩容时按此判断）：

1. **单进程 GIL + 全局写锁**：`src/wsim/api/session.py:680` 的 `WorldSession.lock` 把整个
   `perform`（propose→approve→apply→advance）串行化；`/api/player/state` 也在同一把锁内构图
   （`session.py:278`）。实测 1 核约 100 req/s 到顶，150 席 P95 156ms、199 席 P95 1.76s。
2. **状态视图在锁内构建**：`_status_view` / `_quest_board_view` / `_online_players_view`（
   `session.py:444/602/582`）随在线人数与委托量增长，仍是 O(在线玩家) 成本。
3. 写路径已无 O(n) 读；事件条数增长只影响备份/恢复窗口，不直接影响在线延迟。

扩容方式：**加区（多进程、各自 sqlite），不要试图靠多线程/加核提升单区吞吐**。单区仍受 200 席硬上限保护。

### 7.4 常用诊断命令

```bash
# 事件总量 / seq 连续性 / 钱包守恒 / 半写
.venv/bin/python tools/load_test.py --verify-only --run-dir runs/service

# 200 席与第 201 席 429
.venv/bin/python tools/load_test.py --capacity-check --players 200 --probe-count 3 \
  --preexisting-seats 1 --base-url http://127.0.0.1:8020 --run-dir runs/service

# 50 并发 ×10 分钟（验收口径）
.venv/bin/python tools/load_test.py --players 50 --duration 600 --think-time 2 \
  --base-url http://127.0.0.1:8020 --run-dir runs/service --server-pid <API_PID> \
  --report reports/w5/load-50x10m.json

# sqlite 自检
sqlite3 runs/service/events.sqlite "PRAGMA integrity_check; SELECT COUNT(*),MIN(seq),MAX(seq) FROM events;"
```

WAL 异常或磁盘满时：先停服，保留 `events.sqlite`/`-wal`/`-shm` 原样，再执行
`sqlite3 events.sqlite "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA integrity_check;"`，不要删除 WAL。

---

## 8. 扩容路线：150 → 500 席（方案，**未实现**）

> 本章只是可执行方案与风险清单，代码/脚本均未落地；不要按此直接改生产。
> 原则不变：不引入 Redis / Celery / 消息队列，仍是“每区单进程、单写者、sqlite”。

### 8.1 分区怎么切

- **区（region）= 一个独立 run + 独立进程**：`runs/region-<id>/`（各自 `events.sqlite`、`service.sqlite`）、
  独立 API/UI 端口、独立命名卷。区之间不共享事件日志，避免跨区写锁。
- 首发 1 区 150 席；扩到 500 席时起 **3–4 个区，每区 125–167 席**（硬上限 200 留缓冲）。
- 分区键优先按**地理/主题**切（如 江南/塞北），保证同区玩家社交密度；不足时再按注册序号均衡。
- 所有区的 `content/` 必须一致（`content_hash` 相同），否则跨区委托模板与物品不一致，启动会退出码 3。

### 8.2 账号在哪个区

- 新增**全局账号索引** `runs/_global/accounts.sqlite`（`username → region_id`、状态、创建时间），
  由注册入口单写者维护；用户名唯一性在这里判定（各区的 `service.sqlite` 不再是全局唯一来源）。
- 注册时按负载/玩家选择分配**归属区**并写索引；账号终生归属该区，**只允许在归属区 `enter`**，
  防止同一角色在两个区被双写。
- 令牌仍由归属区的 `service.sqlite` 签发与校验；跨区不做令牌漫游（需要跨区登录时重新在目标区认证，
  且目标区必须已有该角色，当前不支持）。

### 8.3 跨区社交 / 跨区委托怎么处理

- **跨区聊天/好友/世界频道**：共享 `runs/_global/cross_region.sqlite` 的 `outbox` 表
  （`message_id PK, from_region, to_region, payload_json, status, created_at`）。
  各区后台以 1–5s 轮询自己的收件箱，按 `message_id` 幂等去重；允许秒级延迟，不承诺实时强一致。
- **跨区镖单（A 区发布、B 区交付）**：两段式 + 托管（escrow）：
  1. A 区接单时冻结物品与报酬，写 `cross_region_quests(status=escrowed)`；
  2. B 区交付确认写 `delivered` 并生成回执；
  3. A 区轮询到回执后释放报酬、标记 `completed`；
  4. 任一段超时/失败进入 `failed`，由对账任务补偿或人工处理。
  跨区没有原子事务，必须靠**幂等键 + 唯一约束 + 对账**兜底，禁止“先发货后记账”。
- 世界 tick 各区分开推进，跨区业务一律用真实时间戳（UTC）做超时与排序，不能用世界 tick 比较。

### 8.4 共享哪些数据

| 数据 | 位置 | 读写方式 |
|---|---|---|
| 账号归属索引 | `runs/_global/accounts.sqlite` | 注册入口单写；各区只读 |
| 跨区聊天/好友投递 | `runs/_global/cross_region.sqlite` | 各区投递 + 轮询消费（幂等） |
| 跨区委托托管/回执 | 同上 `cross_region_quests` | 两段式状态机 + 对账 |
| 全局榜单/在线快照 | 同上 `global_stats` | 各区定期写，展示端聚合读 |
| 玩家存档/钱包/事件 | **各区私有** | 不共享、不跨区写 |
| 账号密码/令牌 | 各区私有 `service.sqlite` | 仅归属区读写 |

### 8.5 落地步骤（按顺序）

1. 固化“单区 150 席对外、200 席仅可连入”的交付口径与告警（第 6 节已定）。
2. 引入 `region_id` 与全局账号索引；注册路由按归属区分配；改造前端按账号归属路由到区端口。
3. 起 3–4 个区进程（不同 `--run`/端口/卷，均 `--auth on`），验证单区 150 席 P95 仍 <300ms。
4. 先上跨区聊天（低风险），再上跨区镖单（escrow + 对账）；每步都补幂等键与死信处理。
5. 按区复跑 `tools/load_test.py` 50×10 与 200 席基线；告警、备份、容量全部按区独立配置。
6. 升级流程改为**逐区停旧起新**（单写者不可双写）；内容升级需全区间时停机，避免 hash 分叉。

### 8.6 已知风险（未实现部分）

- **全局索引/投递库成为新的单写者热点**：轮询频率高时 `cross_region.sqlite` 会排队；备选是每区独立 outbox 文件 + 文件投递（仍不引入 MQ），但要自己做顺序与去重。
- **跨区最终一致**：网络/进程中断会造成“已交付未结算”，必须有对账任务与人工兜底；不能当作强一致交易。
- **角色迁移未实现**：目前只能整档 replay 到新区，单角色跨区搬档需要新工具与内容一致性校验。
- **世界时间不同步**：各区的世界日不同，跨区活动/榜单要按真实时间而非 tick。
- **运维面 ×N**：备份、恢复、告警、僵尸会话清理都要按区执行；全局索引需单独备份，损坏时从各区 `service.sqlite` 重建。
- **容量不可共享**：席位、钱包铸币额度、200 席上限都是区内概念；跨区不能“借席位”。

---

## 9. P0 备份与恢复（工具化，实测）

> 完整设计、manifest 字段、轮转算法与全部原始输出见
> [docs/BACKUP-RESTORE.md](BACKUP-RESTORE.md)。本节只放值守要用的结论与数字。

### 9.1 资产与命令

| 资产 | 位置 | 备份后丢失的后果 |
|---|---|---|
| 事件日志（唯一事实源） | `runs/<RUN_ID>/events.sqlite` | 世界状态全部丢失 |
| 账号/令牌 | `runs/<RUN_ID>/service.sqlite` | 玩家需重新注册/登录 |
| 备份产物 | `backups/<UTC 时间戳>/`（容器为 `xkx-w5-backups:/app/backups`） | — |

```bash
# 在线备份（服务不用停）
.venv/bin/python tools/backup_snapshot.py --root . --run service

# 校验并装回（必须先停服）
.venv/bin/python tools/restore_verify.py --root . \
  --backup-dir backups/<STAMP> --apply-dir runs/service
```

退出码：备份 `0/1/2`；恢复 `0` 通过、`2` 校验失败（任一失败都不得起服）。

### 9.2 轮转与调度

- 保留：**最近 48 小时全部** + **最近 7 个 UTC 日每日最新 1 份**；其余删除。
- 删除仅限 `backups/` 直接子目录，且必须带 `generator=tools/backup_snapshot.py` 的
  `manifest.json`；目录或其子项含符号链接时跳过并记录 `symlink_refused`。
- 宿主机：`scripts/backup_loop.sh`，默认 `BK_INTERVAL=21600`（6 小时），启动先备一次。
- 容器：`cd deploy && docker compose --profile backup up -d`（独立容器，不侵入世界进程；
  镜像需重建以包含 `tools/`）。主 `docker compose up -d` 不含备份容器，属 profile 的预期行为。

### 9.3 实测 RPO / RTO

| 项目 | 实测值 | 口径 |
|---|---|---|
| 备份耗时 | 2.46–6.29s（29MB / 29.4k 事件） | `duration_ms`，含 state_hash 重放 |
| 备份 SQLite copy | 91.9–334.8ms | `timing.backup_api_ms` |
| 备份对写路径阻塞 | 最大 **31.252ms**（另次 8.226ms） | 存储层连续写探针，阈值 200ms |
| RPO（6h 周期） | ≈ 6h00m07s | 6h + 最长备份耗时；事件增多会变长 |
| 演练实际丢失 | 52 秒 / 20 条事件 | 最后备份 17:27:30Z → 故障 17:28:22Z |
| RTO | **6.510s** | restore+verify 5.002s + API 到 `/api/state` 200 1.505s |
| 循环漂移 | ≤1s（10s 间隔，5 次） | 时间戳 17:36:18/29/40/50/17:37:00Z |

### 9.4 演练结论（2026-11-06）

- **A 正常恢复**：写入 6 个动作 → 备份（seq 29438）→ 停服 → 移走 `events.sqlite` →
  恢复（15/15 校验）→ 起服；`/api/state` HTTP 200，`location/market`、`wallet.money=300`、
  `tick=88152` 与备份前逐字段一致，seq `count=distinct=max=29438`。
- **B 损坏恢复**：截断到 4096 字节 → `sqlite3 PRAGMA integrity_check` 退出码 11
  （`database disk image is malformed`）→ API 启动退出码 1（`store.py:43` 同一报错）→
  从备份恢复 15/15 → 起服成功，`/api/health ok=true`、`/api/state` 200。
- **C 恢复后继续写**：恢复后的 run 再写 10 个动作，seq `32581–32600` 接续自 32580，
  `count=32600`、`distinct=32600`，无重复。
- **D 备份不阻塞**：同起点 20 读 + 10 写 ×30s，基线写 P95 `394.95 / 358.52ms`，
  备份期间 `269.86 / 368.44ms`（备份组不高于基线，单机噪声占主导）；
  存储层探针在 6.289s 备份内 310 次写，最大 **31.252ms**，证明 SQLite 备份读快照不阻塞 WAL 写者。

### 9.5 值守检查清单

- 每日看 `backups/` 是否有当天目录，`manifest.json` 的 `verification_ok=true`。
- 每周对最新备份跑一次 `restore_verify.py`（不带 `--apply-dir`），失败立即告警。
- 备份与主库同盘：磁盘损坏会双丢，异地副本不在 P0 范围。
- 内容包升级前先备份；升级后旧备份会因 `content_hash` 不符而无法启动（预期）。
- 备份含账号哈希/令牌：目录 `700`、文件 `600`，拷离机器前加密。

## 10. 世界丢失 fail-closed 与真实健康检查（2026-11-06）

### 10.1 启动前世界身份检查

服务在创建 `WorldSession` 之前先做一次只读检查（`api/world_guard.py`）：

| 场景 | 行为 | 退出码 |
| --- | --- | --- |
| 无 `service.sqlite` 或无账号 | 正常创世 | 0 |
| 有账号 + `events.sqlite` 缺失/0 事件 | 拒绝启动，日志一行 JSON `events_missing` | **4** |
| `events.sqlite` `PRAGMA integrity_check` 非 `ok` | 拒绝启动，`events_corrupt` | **5** |
| 内容包 `content_hash` 不符（既有行为） | 拒绝启动 | 3 |
| 有账号 + 丢库，但显式 `--allow-new-world` | 允许创世，日志 `world: new` | 0 |

拒绝启动时示例（stderr 单行 JSON）：

```json
{"accounts": 1, "code": "events_missing", "events_db": ".../runs/lost/events.sqlite",
 "exit_code": 4, "ok": false,
 "reason": "检测到已有账号数据但事件库缺失，疑似卷挂载错误；如需开新世界请显式 --allow-new-world",
 "service_db": ".../runs/lost/service.sqlite"}
```

首次创世会在 `runs/<run>/world_id.json` 写入世界身份标记
（`run_id` / `content_hash` / `seed` / `created_at`），既有世界不覆盖。

> 注意：`--allow-new-world` 只放行“丢库开新世界”，**不会**放行损坏库；损坏库必须从备份恢复。

### 10.2 `/api/health`

每次请求真实检查，返回 200/503：

```json
{"ok": true, "mode": "live", "readonly": false, "profile_version": 1, "upstream_ok": true,
 "run": "player-demo", "tick": 88152,
 "storage": {"events_ok": true, "seq_gap": 0, "disk_free_mb": 41234,
             "disk_free_pct": 41.2, "last_commit_age_s": 0.2, "integrity": "ok"},
 "world": {"run": "player-demo", "tick": 88152, "new_world": false}, "ambient": "running", ...}
```

判定口径：

- `storage.events_ok`：`EventStore.count()`（COUNT(*) 缓存）与 `EventTail.last_seq`
  （MAX(seq) 缓存）一致，且 `integrity_check = ok`。两者差异即 `seq_gap`；O(1)，不 `read_all`。
- `integrity_check` 默认 60 秒缓存（`XKX_INTEGRITY_CACHE_S`）。
- `disk_free_pct` 低于 5% → 503（`XKX_DISK_MIN_FREE_PCT`）。
- 写者最后提交（`events.sqlite` / `-wal` mtime）超过 60 秒 → 503（`XKX_WRITER_IDLE_MAX_S`）；
  刚启动时以进程启动时刻为基准，不会因旧库 mtime 误判。
- 任一不满足 → `ok=false` + **HTTP 503**；既有键保持不变，只新增 `storage` / `world`。


---

## 11. 审计日志轮转与内存上限（2026-11-06）

**审计文件** `runs/<RUN_ID>/mcp_audit.jsonl` 是按大小轮转的：

- 默认 **100MB / 保留 5 份**：活动文件 + `.1`…`.5`（`.5` 最旧，共 6 个文件上限）。
- 轮转由后台线程 `audit-rotate` 执行；写入路径只做 append + 计数，不等待改名/删除。
  轮转瞬间写入的新行可能落在 `.1`，**不会丢行**。
- 配置：`XKX_AUDIT_MAX_BYTES`（默认 `104857600`；`0` 关闭轮转）、
  `XKX_AUDIT_BACKUPS`（默认 `5`）、`XKX_AUDIT_MEMORY_MAX`（进程内缓存条数，默认 `4096`）。

**无界内存结构上限**（长跑进程不再线性增长）：

| 结构 | 位置 | 环境变量 | 默认 |
|---|---|---|---|
| 审计记录缓存 | `mcp/audit.py` `records` | `XKX_AUDIT_MEMORY_MAX` | 4096 条（deque） |
| writer 幂等缓存 | `world/writer.py` `_seen` | `XKX_WRITER_SEEN_MAX` | 20000 条（OrderedDict） |
| 请求回看缓存 | `api/session.py` `requests` / `intents` | `XKX_RECENT_INTENTS_MAX` | 4096 个请求（意图索引最多 2×） |

实测（全默认 100MB）：连续写入 `357430` 条后触发轮转；

```
max_bytes=104857600 backups=5 memory_max=4096
records_written=357430 rotation_count=1 records_kept=4096
-rw-r--r--  1 lyinet wheel  104857790 mcp_audit.jsonl.1
-rw-r--r--  1 lyinet wheel       4410 mcp_audit.jsonl
```

运维动作：`.1`…`.5` 可直接归档或删除；`service.sqlite` 的 `audit_log`（登录锁定/解锁/吊销）
是另一套审计，不受此轮转影响。

---

## 12. 反代后的真实客户端 IP（可信代理，2026-11-06 起）

反代终止 TLS 后，API 的 TCP 对端是代理地址（通常是容器网段，如 `172.18.0.4`）。
**必须**把反代网段写进可信代理，否则登录限流的 `(ip, account)` 键退化为只按账号。

```bash
# deploy/compose 或进程环境
XKX_TRUSTED_PROXIES=172.18.0.0/16
```

nginx 侧保持标准转发头：

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Real-IP $remote_addr;
```

规则：

- 只有 TCP 对端落在 `XKX_TRUSTED_PROXIES` 内，才会采信转发头；XFF 从右向左跳过可信
  代理，取第一个不可信跳。来源不可信或头畸形时，一律回落 TCP 地址。
- 默认空值 = 不信任任何转发头（与旧行为一致，安全但会把所有玩家算成同一个 IP）。
- 管理台走自己的 `XKX_ADMIN_ALLOW_IPS`，与这里无关；不要把管理网段和反代网段混用。

上线后自检（服务运行中只读查询）：

```bash
sqlite3 runs/<RUN_ID>/service.sqlite \
  "SELECT ip, account, COUNT(*) FROM login_failures GROUP BY ip, account ORDER BY 3 DESC LIMIT 10;"
```

预期看到公网客户端 IP，而不是 `172.18.0.4`；若仍是代理地址，说明 `XKX_TRUSTED_PROXIES`
没配或网段写错（非法项会在 stderr 打印“忽略非法可信代理网段”）。

不配置的后果：跨账号爆破、按 IP 的总体限流全部失效；同代理后的玩家还会共享失败计数。

## 13. 分支发布规则（2026-11-07 起）

xkx 复刻与现有九地点世界并存，**两条线靠分支隔离**，不靠“大家小心一点”。

### 13.1 分支职责

| 分支 | 内容 | 发布状态 |
|---|---|---|
| `master` | 现有九地点世界（`config/worlds/default.yaml` + `content/` YAML 包） | **必须始终可发布**。不再直接接 xkx 半成品 |
| `xkx-parity` | xkx 复刻主线：内容、引擎、玩家壳、公式、参照版、帮助六条线 | 开发主线，允许半成品；**不代表可发布** |

分支切点：`xkx-parity` 从 `121fb37` 拉出，`master` 同期停在同一 commit（未前进、未污染）。

### 13.2 两个基线 tag 的语义与用途

两个 tag 都是 annotated、都已推送，**不移动、不改写**；说明有误时只能新增 tag 纠正，不得重打。

| tag | 指向 | 日期 / 提交标题 | 语义 |
|---|---|---|---|
| `world-legacy-20261107` | `121fb37` | 2026-11-07 / `feat(xkx-player): 接入 xkx 真实内容包，195 房/92 NPC 可走可问可买` | **切分支时的九地点可用状态（已含 xkx 内容包接入）** |
| `pre-xkx-20261107` | `df2b37c` | 2026-11-07 / `docs(dialogue): DIALOGUE-QUALITY 补 model_delivery_rate 指标与 ≥80% 门槛` | **侠客行承接方案（`7f9550c`）与 xkx 内容接入之前的九地点世界快照**，不含任何 xkx 改动 |

用途区分：

- 要回退到“**世界能跑**”的九地点状态 → `world-legacy-20261107`（`121fb37`）；
- 要回退到“**完全没有 xkx 改动**”的九地点状态 → `pre-xkx-20261107`（`df2b37c`）。

`df2b37c..121fb37` 之间共有 16 个提交，其中 15 个属于 xkx 线（内容、引擎、壳、公式、参照版、帮助、审计），
仅 `377b815`（`fix(sim): scheduler 留存封顶 + canonical 去重`）是通用引擎/soak 修复。
合回 master 或整体回滚时按这份边界核对：

```bash
git log --format='%h|%ad|%s' --date=short df2b37c..121fb37
```

### 13.3 提交进哪条线

- 九地点世界的修复 / 运维 / 安全补丁 → `master`，合入后立刻验证旧世界可启动、可登录。
- xkx 复刻六条线（内容、引擎、壳、公式、参照版、帮助）的**所有**提交 → `xkx-parity`。
- 两条线都需要的公共修复（如启动脚本、代理、鉴权）：先在 `master` 修好验证，再 `git cherry-pick` 到
  `xkx-parity`；**不要反过来把 xkx 分支的整树合进 master**。

### 13.4 合并回 master 的条件（全部满足才整体合）

1. **能走**：xkx 世界可从出生点出发，按出口方向连续移动，边界出口给出中文原因且不困死玩家；
2. **能打**：`fight` 对带气血/攻防的实体真实开战，能打出战报，不是“暂不可用”；
3. **能捡**：`loot` 已注册并能把掉落放进玩家行囊；
4. **确定性审计全绿**：同 seed 两次运行 `state_final` / `event_final` 哈希一致，审计脚本无红项；
5. **旧世界 golden 未变**：九地点世界的 golden 基线（房间数、事件数、`content_hash`）逐项不变，
   旧世界仍可用 `config/worlds/default.yaml` 启动。

任一条不满足 → 留在 `xkx-parity`，master 不接受“先合进去再修”。

### 13.5 操作纪律

- 提交前确认自己在哪条线：`git branch --show-current`；xkx 工作必须在 `xkx-parity`。
- `git add` 用**显式路径**，不用 `git add -A` / `git add .`，避免把他人未提交改动或运行产物带进提交。
- 推送前 `git pull --rebase origin <当前分支>`；**禁止** `--force` / `--force-with-lease`。
- 冲突或历史分叉：停下来报，不强推、不改写已推送历史。
- 新分支首次推送用 `git push -u origin <分支名>`。

### 13.6 每日对账

```bash
git fetch origin --tags
git branch -vv                                   # 本地分支与上游跟踪关系
git rev-parse HEAD origin/master origin/xkx-parity
git log --oneline -5 origin/master               # master 是否混入 xkx 提交
git tag -l --format='%(refname:short)|%(creatordate:short)|%(subject)' 'world-legacy-*' 'pre-xkx-*' 'master-before-reset-*'
```

判读：`origin/master` 上出现 `feat(xkx` / `fix(xkx` 类提交即为违规，需回退或说明；
`xkx-parity` 落后 `origin/xkx-parity` 说明有人没推。

### 13.7 master 重置记录（2026-11-07）

| 项 | 值 |
|---|---|
| 执行人 | 第三路（玩家壳与接入 session，`6ae85066887df6503c32fcd7`） |
| 原因 | `master` 直接接入了 xkx 半成品（`98d9c02`…`121fb37`），违反 §13.1“master 必须始终可发布”；老板明确授权退回 |
| 退回到 | `df2b37c`（`pre-xkx-20261107` 指向的 commit） |
| 保险 tag | `master-before-reset-20261107` → `121fb37`（annotated，已推远端，tag 对象 `9c07873`） |
| 操作方式 | 独立 worktree `/tmp/wsim-master-reset` 中 `git reset --hard df2b37c`，再 `git push --force-with-lease=master:121fb37 origin master`（未使用裸 `--force`，未改写任何 tag） |
| 验证 | `git ls-remote origin refs/heads/master` = `df2b37c`；`origin/master` 顶端三条为 `df2b37c` / `961c3dd` / `9f1661c`；`origin/xkx-parity` 仍为 `53e89a9` |

重置后**只存在于 `xkx-parity`** 的提交共 16 条（`df2b37c..121fb37`，均可在 `xkx-parity` 祖先链上找到，
无丢失；其中 `377b815` 为通用调度器修复，但同样已在 `xkx-parity`）：

```text
121fb37 f79d51c e525de4 b587b60 9812625 fec936e 3b6349e 15eeaca
c934768 927da92 98d9c02 a7b4887 5c32a3b e323244 3218800 377b815
```

需要恢复重置前状态：`git reset --hard master-before-reset-20261107`（本地）或
`git push --force-with-lease=master:df2b37c origin master-before-reset-20261107:master`（远端，需老板授权）。

