# RELEASE-ROLLBACK

> 目标：任何一次发布都能退回上一版镜像，且回滚后世界存档仍可读、`seq` 连续、账号令牌仍有效。
> 本文只描述发布/回滚流程与数据兼容性判定；镜像构建参数见 `deploy/Dockerfile`，
> 构建脚本见 `tools/build_image.sh`，变更记录纪律见 [COMMIT-CONVENTION.md](COMMIT-CONVENTION.md)。

## 1. 版本标识从哪来

镜像里有三处同源版本信息，构建时由 `--build-arg XKX_VERSION` / `XKX_REVISION` 一次性注入：

| 位置 | 读取方式 | 用途 |
| --- | --- | --- |
| `org.opencontainers.image.version` | `docker image inspect` | 发布记录、回滚时确认目标 tag |
| `org.opencontainers.image.revision` | `docker image inspect` | 对应 git commit，便于回溯内容与 `content_hash` |
| `/app/VERSION` | `docker exec <ctr> cat /app/VERSION` | 容器内零依赖自查（无需 docker inspect） |

```bash
docker image inspect --format \
  'version={{index .Config.Labels "org.opencontainers.image.version"}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}' \
  xkx-world-sim:v1
docker exec xkx-world cat /app/VERSION
```

`/api/health` 未加版本字段（按要求不改 `src/**` 的 API 逻辑）；版本只走 label 与 `/app/VERSION`。

## 2. 发布步骤

```bash
cd world-sim

# 1) 确认工作区干净，revision 才能代表镜像内容
git status --short
git rev-parse HEAD

# 1.5) 核对 content_hash 与上一 tag 是否不同（不同即升级影响，必须写进发布说明）
PYTHONPATH=src python3 -c "from pathlib import Path; from wsim.core.content import load_content; \
print(load_content(Path('.'), allow_fixture=False, use_content=True).content_hash)"
git worktree add --detach /tmp/tag-prev <上一 tag>
(cd /tmp/tag-prev && PYTHONPATH=src python3 -c "from pathlib import Path; from wsim.core.content import load_content; \
print(load_content(Path('.'), allow_fixture=False, use_content=True).content_hash)")
git worktree remove --force /tmp/tag-prev
#    本次实测：delivery-p3l = 7ec670c1…c3d53f → 当前 = 5267a8a8…696da4（不同，
#    发布说明必须写“旧库拒绝启动，退出码 3 → 继续用旧镜像 / 新开世界”，见第 4.2 节）

# 2) 构建：同一镜像打 <version> 与 latest 两个 tag
tools/build_image.sh v1.2.0
#    脚本会打印 version / revision / tags / image_id / digest / /app/VERSION，整段贴进发布记录

# 3) 记录发布基线（镜像 ID 与 digest 必留，回滚时要能定位到具体层）
tools/build_image.sh v1.2.0 2>&1 | tee reports/release-v1.2.0.txt

# 4) 指向新版本并起服务（compose 里 image 改成新 tag）
#    deploy/docker-compose.yml 当前为 image: xkx/world-sim:w5；
#    若沿用旧镜像名，用 XKX_IMAGE_NAME=xkx/world-sim tools/build_image.sh v1.2.0
sed -i '' 's|image: .*world-sim:.*|image: xkx-world-sim:v1.2.0|' deploy/docker-compose.yml
docker compose -f deploy/docker-compose.yml up -d

# 5) 发布后验证
curl -s http://127.0.0.1:8020/api/service/info      # 必须 {"ok": true, ...}
curl -s http://127.0.0.1:8020/api/health            # 必须 200
docker exec xkx-world cat /app/VERSION              # 必须等于 v1.2.0
docker compose -f deploy/docker-compose.yml ps      # world 与 backup 均 healthy/running
```

发布记录必须包含：version、revision（commit 短哈希）、image_id、`/app/VERSION` 实际输出、
发布时的 `content_hash`（`sqlite3 runs/<RUN>/events.sqlite "SELECT value FROM meta WHERE key='content_hash';"`）。

## 3. 回滚步骤

回滚只换镜像，不动数据卷：

```bash
cd world-sim

# 1) 找到上一版的 tag 与 image_id（发布记录或本地镜像列表）
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedAt}}' | grep xkx-world-sim

# 2) 改回上一版 tag（不要 down -v，不要删命名卷）
sed -i '' 's|image: .*world-sim:.*|image: xkx-world-sim:v1.1.0|' deploy/docker-compose.yml
docker compose -f deploy/docker-compose.yml up -d

# 3) 回滚后四项必查
docker exec xkx-world cat /app/VERSION                          # 必须是上一版版本号
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8020/api/state?account_id=u_demo   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8020/api/world/feed?limit=5        # 200
sqlite3 runs/<RUN>/events.sqlite \
  "SELECT COUNT(*), MIN(seq), MAX(seq) FROM events;"            # min=1 且 max=count 即 seq 连续

# 4) 旧令牌是否仍有效（回滚不迁移令牌表时，应继续有效）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8020/api/auth/me \
  -H 'Authorization: Bearer <回滚前签发的 token>'                 # 期望 200
```

回滚失败（容器起不来 / `content_hash_mismatch`）说明内容层不兼容，此时**不要删卷重建**，
按第 4 节判定是否需要同时回滚数据，否则恢复与存档匹配的 `content/`。

## 4. 数据兼容性检查清单

回滚前逐项核对。三项中任意一项变化，都必须按第 5 节判定表处理。

### 4.1 `events.sqlite` schema 是否变化

```bash
sqlite3 runs/<RUN>/events.sqlite "PRAGMA table_info(events);"
```

- 当前列：`seq / tick / day / intent_id / actor / verb / args / effects / outcome / reason / source / legacy`。
- 引擎对旧库采取**只读兼容**：缺 `legacy` 列时不 ALTER，读取时按 `actor/verb` 推断
  （见 [store.py](../src/wsim/world/store.py)）。
- **新增列**是向后兼容的（旧镜像读新库只会忽略新列）；**删列/改语义**不兼容。
- 判定：回滚目标镜像与当前库列集合是“超集/子集”关系 → 可直接回滚；两者都缺对方必需列 → 需回滚数据库。

### 4.2 `content_hash` 是否变化

```bash
sqlite3 runs/<RUN>/events.sqlite "SELECT value FROM meta WHERE key='content_hash';"
```

- 服务启动会比对存档里的 `content_hash` 与当前 `content/` 计算值；不一致**拒绝启动**，
  退出码 3，错误码 `content_hash_mismatch`，stderr 单行 JSON，不监听、不写事件
  （固定契约见 `docs/W1/spec.md` §5；实测见 `docs/known-limitations.md` §13）。
- 当前关闭态基线：`5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4`
  （2026-11-06 起，补齐店铺五动词声明后）；`delivery-p3l` 及以前为 `7ec670c1…c3d53f`。
  开启态见 `docs/CONTENT-SCALE.md`。
- **当前没有内容迁移路径**：CLI 无 `--allow-content-change` 或等价开关（`--allow-new-world`
  只覆盖“事件库缺失”的退出码 4），也没有旧库→新内容的重写工具。
  **内容哈希不同 = 升级等于开新世界**：要么继续用旧镜像，要么换新 run 目录/新卷重建。
- 判定：回滚镜像自带 `content/` 的哈希必须等于库里的 `content_hash`；不等则先恢复匹配的
  `content/`（或整卷恢复备份），再回滚镜像。

### 4.3 令牌表是否需要迁移

```bash
sqlite3 runs/<RUN>/service.sqlite "PRAGMA table_info(sessions); PRAGMA table_info(users);"
```

- 当前列：`sessions(id, account_id, token_hash, created_at, expires_at, last_seen_at,
  disconnected_at, tick_at_disconnect, status)`；`users(username, salt, hash_hex, iterations, created_at)`。
- 令牌只存 SHA-256 摘要，回滚不改变摘要算法 → 旧令牌继续有效（实测见第 6 节）。
- 判定：仅新增列/新增索引 → 无需迁移；改摘要算法、改 `status` 取值语义、删列 → 需要迁移，
  迁移前必须整卷备份 `service.sqlite`。

## 5. 哪些改动需要回滚数据库（判定表）

| 改动类型 | 例子 | 只回滚镜像够吗 | 数据库处理 |
| --- | --- | --- | --- |
| 纯 UI / 文档 / 脚本 | `ui/**`、`docs/**`、`deploy/entrypoint.sh` | 够 | 不动 |
| 引擎逻辑，不写新字段 | 定价公式、NPC 决策顺序 | 够 | 不动（事件仍可回放） |
| 新增事件 verb / 新 effects 字段 | 新动作写额外 `effects` 键 | 够（旧镜像忽略未知键） | 不动；但旧镜像无法再生成同类事件 |
| `events` 表**新增列** | 加 `region` 列 | 够（旧库只读兼容） | 不动 |
| `events` 表**删列 / 改语义** | 改 `source` 取值含义 | **不够** | 需整卷恢复到改动前备份 |
| `content/` 内容变更 | 改 NPC / 物品 / 委托 | **不够**（`content_hash_mismatch`，退出码 3） | 恢复匹配 `content/` 或整卷恢复备份 |
| 令牌摘要算法变更 | 换 PBKDF2 参数 / 换哈希 | 够（旧令牌只是失效） | 可选：整卷恢复 `service.sqlite` 以保留登录态 |
| `sessions` 表删列 | 删 `tick_at_disconnect` | **不够** | 需恢复 `service.sqlite` 备份 |
| 只加 `users` / `sessions` 新列 | 加 `last_ip` | 够 | 不动 |

判定原则：**append-only 且只增列 = 可只回滚镜像；任何删列、改语义、改 `content/` = 必须同时回滚数据。**

## 6. 回滚实测记录（2026-11-06）

环境：Docker Engine 29.9.2（colima context），macOS。两个镜像均由干净 `git archive` 树构建，
仅 `deploy/Dockerfile` 使用本次新增的版本注入版（旧 revision 的内容层未改动）。

| 项 | 当前版 | 上一版 |
| --- | --- | --- |
| tag | `xkx-world-sim:rb-current` | `xkx-world-sim:rb-previous` |
| revision | `6c84cb08bdb4c91e11f17e3c7b4c36b05d78a9e3` | `af4b64c147570f3338f0bd0cd17ff3cfcee029e1` |
| image_id | `sha256:72b2dcc5cfab56f38dfbab6cc4ed0e4debd75ce36ed08b2f54ac1ed0701a729b` | `sha256:d2bba5c819dd0701b6eea65fa4e5c1c94993b27a6e9992885564be7f03a5fb59` |
| `/app/VERSION` | `rb-current` | `rb-previous` |

数据卷：`xkx-rb-data`（`/app/runs`），`RUN_ID=rollback`，鉴权 `AUTH_MODE=on`。

### 6.1 命令

```bash
docker run -d --name xkx-rb-current -p 18020:8020 -p 18030:8030 \
  -v xkx-rb-data:/app/runs -e RUN_ID=rollback -e AUTH_MODE=on xkx-world-sim:rb-current
# 注册 / 登录 / 进世界 / 移动（产生 8 条事件）
docker rm -f xkx-rb-current
docker run -d --name xkx-rb-previous -p 18020:8020 -p 18030:8030 \
  -v xkx-rb-data:/app/runs -e RUN_ID=rollback -e AUTH_MODE=on xkx-world-sim:rb-previous
# 校验 /api/service/info、/api/auth/me、/api/state、/api/world/feed、events.sqlite，再追加一次 move
```

### 6.2 输出

```text
=== [1] 启动当前版镜像 rb-current（revision 6c84cb0） ===
service/info http=200
/app/VERSION=rb-current
labels=version=rb-current revision=6c84cb08bdb4c91e11f17e3c7b4c36b05d78a9e3
=== [2] 产生数据（注册/登录/进世界/移动） ===
register={"account_id": "rb_user", "created": true, "ok": true, "username": "rb_user"}
token=uDlQ-H9v…（只显示前 8 位）
enter ok=True
move to academy ok=True reject=
--- 当前版停止前数据库 ---
{"count": 8, "min_seq": 1, "max_seq": 8, "seq_contiguous": true, "columns": ["seq", "tick", "day", "intent_id", "actor", "verb", "args", "effects", "outcome", "reason", "source", "legacy"], "content_hash": "7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f"}
=== [3] 停当前版，同一卷启动上一版镜像 rb-previous（revision af4b64c） ===
service/info http=200
/app/VERSION=rb-previous
labels=version=rb-previous revision=af4b64c147570f3338f0bd0cd17ff3cfcee029e1
=== [4] 回滚后校验 ===
旧令牌 /api/auth/me: http=200 ok=True
/api/state: http=200 location=academy
/api/world/feed: http=200
{"count": 8, "min_seq": 1, "max_seq": 8, "seq_contiguous": true, "columns": [...同当前版...], "content_hash": "7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f"}
=== [5] 回滚后继续写入（证明 seq 可从上一版继续追加） ===
second move ok=True reject=
{"count": 10, "min_seq": 1, "max_seq": 10, "seq_contiguous": true, ...}
{"count_before": 8, "count_after": 10, "append_ok": true, "seq_contiguous_after": true, "content_hash_same": true, "content_hash": "7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f", "columns": ["seq", "tick", "day", "intent_id", "actor", "verb", "args", "effects", "outcome", "reason", "source", "legacy"]}
=== 回滚实测完成 ===
```

### 6.3 结论（逐项）

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 上一版容器能起来 | PASS | `service/info http=200`，`/app/VERSION=rb-previous` |
| `/api/state` 返回 200 | PASS | `http=200 location=academy`（回滚前移动后的位置保留） |
| 事件仍可读 | PASS | `count=8`，`/api/world/feed http=200` |
| `seq` 连续 | PASS | `min_seq=1 max_seq=8 seq_contiguous=true`；回滚后再写入 `1..10` |
| 旧令牌仍有效 | PASS | `/api/auth/me http=200 ok=True` |
| `content_hash` 不变 | PASS | 回滚前后均为 `7ec670c1…` |
| 回滚后可继续写 | PASS | `count 8 → 10`，`append_ok=true` |

未做项：本次实测的两个镜像 `content/` 相同（同一 `content_hash`），因此**没有**验证
“内容变更后回滚必然 `content_hash_mismatch`”的反向场景；该判定来自
[DEPLOYMENT-W1.md](DEPLOYMENT-W1.md) 第 6 节的启动校验实现（退出码 3），非本轮实测。

## 7. 发布 tag 生成与核对清单（`delivery-*`）

> 适用：把当前 `master` 冻结成对外交付 tag、核对完整性、推送与按 tag 回滚代码。
> **本轮（2026-11-06）不实际打 tag**：版本号由发布统一入口下发后再执行本清单。

### 7.1 命名规则

- 格式：`delivery-<milestone>`，全小写、短横线分隔，例：`delivery-p3l`、`delivery-p4`。
- 一个里程碑只对应一个 tag；已推送的 tag **永不移动**（禁止 `git tag -f`、禁止
  `git push --force`）。需要修正时发新 tag（如 `delivery-p4a`）并在 CHANGELOG 说明。
- tag 必须指向 `master` 上已推送的提交（`HEAD == origin/master`），用 annotated tag，
  不打 lightweight tag（保留打 tag 人/时间/说明）。
- tag 消息写清里程碑与验收范围，例：
  `delivery-p4: 门派系统 + 管理控制台 + 店铺五动作（冷启动 30/30）`。

### 7.2 打 tag 前的唯一基线检查

```bash
git fetch origin
git status --porcelain          # 必须为空
git rev-parse HEAD
git rev-parse origin/master     # 必须与 HEAD 相同
```

如存在他人的未提交改动：**不要代提交**，先确认它们不属于本次交付，再继续。
`reports/**/*.log` 等本地产物已由 `.gitignore` 排除，不应出现在 `git status`。

### 7.3 打 tag

```bash
TAG=delivery-p4          # 用下发版本号替换
git tag -a "$TAG" -m "delivery-p4: <里程碑内容>"
```

### 7.4 推送前核对（全部通过才 push）

#### 7.4.1 指向与类型

```bash
git cat-file -t "$TAG"                                  # 期望：tag（annotated）
git rev-parse --short "$TAG^{commit}"                   # 期望：与 HEAD 相同的短哈希
test "$(git rev-parse "$TAG^{commit}")" = "$(git rev-parse HEAD)" && echo "commit OK"
```

#### 7.4.2 文件数与 HEAD 一致（防 tag 指向旧快照）

```bash
TAG_FILES=$(git ls-tree -r --name-only "$TAG" | wc -l | tr -d ' ')
HEAD_FILES=$(git ls-tree -r --name-only HEAD | wc -l | tr -d ' ')
echo "tag=$TAG_FILES head=$HEAD_FILES"
test "$TAG_FILES" = "$HEAD_FILES" || { echo "FAIL: 文件数与 HEAD 不一致，tag 可能指向旧提交"; exit 1; }
```

#### 7.4.3 关键路径必须存在

```bash
for path in src/wsim/admin/ ui/admin/ src/wsim/actions/player_sect.py; do
  git ls-tree -r --name-only "$TAG" | grep -q "^$path" \
    || { echo "FAIL: tag 缺少 $path"; exit 1; }
done
echo "关键路径 OK"
```

#### 7.4.4 店铺五动作进 `WRITE_ACTIONS`

`WRITE_ACTIONS` 的实际位置是 `src/wsim/api/session.py`（不是 `src/wsim/actions/`）：

```bash
for verb in restock set_price shop_report; do
  git show "$TAG:src/wsim/api/session.py" | grep -q "\"$verb\"" \
    || { echo "FAIL: WRITE_ACTIONS 缺少 $verb"; exit 1; }
done
echo "店铺动作 OK"
```

若交付说明包含“店铺五动作”，建议一并核对整组六键：

```bash
for verb in open_shop restock set_price dismiss withdraw shop_report; do
  git show "$TAG:src/wsim/api/session.py" | grep -q "\"$verb\"" \
    || { echo "FAIL: WRITE_ACTIONS 缺少 $verb"; exit 1; }
done
```

#### 7.4.5 可构建/可起服务（可选但推荐）

```bash
git switch --detach "$TAG"
cd deploy && docker compose build && docker compose up -d
docker ps --format 'table {{.Names}}\t{{.Status}}'      # 四容器全 healthy
cd .. && git switch master
```

### 7.5 推送 tag 并验证远端

```bash
git push origin "$TAG"
git ls-remote --tags origin "$TAG" "${TAG}^{}"   # 应看到 tag 对象与 peeled commit
git rev-parse --short "$TAG^{commit}"            # 与 7.4.1 一致
```

禁止：`git push --force origin "$TAG"`、`git tag -f`、`git push --tags`（会顺带推送
本地遗留的临时 tag）。

### 7.6 按 tag 回滚代码（回到上一交付）

#### 7.6.1 找到上一 tag

```bash
git fetch --tags --prune
git tag --sort=-creatordate | grep '^delivery-' | head -5   # 最新在前
PREV=$(git tag --sort=-creatordate | grep '^delivery-' | sed -n '2p')
echo "$PREV"
```

#### 7.6.2 先备份数据，再切代码

回滚只回代码，不回数据库 schema：

```bash
# 1) 先做一次带远端校验的备份（见 docs/BACKUP-RESTORE.md）
docker exec xkx-backup python /app/tools/backup_snapshot.py \
  --root /app --run "${RUN_ID:-service}" --backup-dir /app/backups
docker exec xkx-backup sh -c 'cat /app/backups/last_backup_status.json'

# 2) 切到上一 tag 并重建
git switch --detach "$PREV"
docker compose down
docker compose up -d --build
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

#### 7.6.3 若新版本已写过库

旧代码读新 schema 可能失败。此时不要直接跑旧版本，先按第 4、5 节判定，必要时按
[docs/BACKUP-RESTORE.md](BACKUP-RESTORE.md) 恢复到新版本之前的备份，再起旧 tag。

#### 7.6.4 回到主线

```bash
git switch master
git pull --ff-only
```

已推送的坏 tag **不删除、不移动**：发布一个新 tag（如 `delivery-p4a`）指向修复后的
提交，并在 CHANGELOG 标注旧 tag 不再用于部署。

### 7.7 当前基线快照（2026-11-06，供对照）

```text
$ git rev-parse --short 'delivery-p3l^{commit}'
d01ba9b
$ git rev-list --count delivery-p3l..HEAD
101
$ echo "tag=$(git ls-tree -r --name-only delivery-p3l | wc -l) head=$(git ls-tree -r --name-only HEAD | wc -l)"
tag=541 head=751
```

结论：`delivery-p3l` 是旧快照（缺门派、管理控制台、店铺五动作，文件数少 210），
**不可用于本次交付**；等阻断项修完、版本号下发后按 7.2–7.5 重新打 tag。
`deploy/README.md` 里的 `b23827a` 是 `delivery-p3k^{commit}` 的历史基线，不是当前 HEAD。
