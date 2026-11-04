# W1 任务清单（执行顺序）

每完成一项：跑对应测试 → 只 stage 自有文件 → commit。禁止 `git add -A`。

1. [x] **账号存储**：新建 `src/wsim/api/auth.py`；测试 `tests/test_w1_auth.py`
   - PBKDF2 盐化摘要、明文不落盘、注册/登录/令牌/过期/刷新/注销/僵尸清理。
   - commit: `feat(w1): 账号与令牌存储（PBKDF2 + sqlite，口令不落明文）`

2. [x] **存档回放**：新建 `src/wsim/api/persistence.py`，改 `session.py` 启动分支；测试 `tests/test_w1_persistence.py`
   - 重启后 6 类字段逐项断言 0 丢失；事件数不翻倍。
   - commit: `feat(w1): 事件日志回放恢复玩家存档（重启不丢档）`

3. [x] **hash 拒绝启动契约**：`persistence.py` 异常 + `__main__.py` 退出码/日志；测试 `tests/test_w1_hash_mismatch.py`
   - 退出码 3、错误码 `content_hash_mismatch`、stderr JSON。
   - commit: `feat(w1): 内容 hash 不匹配拒绝启动（退出码 3 + 契约日志）`

4. [x] **HTTP 鉴权**：改 `server.py`/`__main__.py`；测试 `tests/test_w1_http_auth.py`、`test_w1_mode_isolation.py`
   - 所有写端点缺失/无效/过期令牌 401；账号不一致 403；读匿名；auth 契约固定。
   - commit: `feat(w1): 写端点 Bearer 鉴权与 auth 契约（401 全覆盖）`

5. [x] **模式隔离**：`resolve_auth_required` + 双模式测试（与第 4 项同 commit 或紧随）
   - 有 `deploy/service.yaml` 无匿名写旁路；无 yaml 的 legacy 保持可用。
   - commit: `feat(w1): auto 模式安全边界（有配置强制鉴权、无配置 legacy 隔离）`

6. [x] **断线重连**：`session.py` 的 `resume`/`advance=False`，`server.py` 的连接断开与重连；测试 `tests/test_w1_reconnect.py`
   - 60s 内同会话恢复 tick/位置/钱包/任务；>60s 新会话、存档仍在。
   - commit: `feat(w1): 断线重连会话恢复与 60 秒宽限`

7. [x] **并发隔离**：测试 `tests/test_w1_concurrency.py`（必要时只改 `session.py` 的锁内行为）
   - 双客户端 20 轮交错：互见、0 串档、0 未授权写、同 NPC 关系不互相覆盖。
   - commit: `test(w1): 双客户端 20 轮并发隔离回归`

8. [x] **200 席限额**：测试 `tests/test_w1_capacity.py`；`server.py` 429 映射
   - 201 个连接，第 201 个 429 + `player_cap_reached`。
   - commit: `feat(w1): 201 席返回 429 明确原因码`

9. [x] **部署资产与文档**：`deploy/service.yaml`、`deploy/run_service.sh`、`src/wsim/api/admin.py`、`docs/DEPLOYMENT-W1.md`、`docs/AUTH-API-W1.md`
   - commit: `docs(w1): 启动/备份恢复/僵尸会话清理部署说明`

10. [x] **总验收**：全量 `pytest` 计数、`./scripts/selfcheck.sh` 4/4、旧 golden 未改；中文回报。

## 验证命令
```bash
.venv/bin/python -m pytest -q
./scripts/selfcheck.sh
git status --short
```
