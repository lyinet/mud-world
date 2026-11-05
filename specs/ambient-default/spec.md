# Spec · 世界自走默认开启（首发体验）

> 基线：`b23827a`（tag `delivery-p3k`）不动；HEAD 仅两笔文档提交。
> 目标：`./start.sh` 一条命令即可同时运行 API、前端与后台自走世界；默认开启，
> 可用 `XKX_AMBIENT=0` 显式关闭。零模型调用。

## 1. 决策摘要（需确认）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 在 `scripts/demo.sh` 内默认启动 ambient；`start.sh` 仍 `exec demo.sh` | 保持一键入口不变；关闭态与现状完全一致 |
| D2 | ambient 独立世界目录 `runs/<RUN_ID>/ambient/`，不写 API 的 `events.sqlite` | 不引入双写者；遵守单写者/事件溯源边界 |
| D3 | API 新增 `GET /api/health`；`GET /api/service/info` 只**追加** ambient 键；`ui/player` 的 `/api/health` **只追加** ambient 键 | 满足补充要求 #3：`ok/mode/readonly/profile_version/upstream_ok` 原样保留；W4 探活不降级 |
| D4 | CPU 护栏默认阈值 **70%**：先降速补睡；持续超限暂停自走；低于恢复线后自动恢复 | 满足“有实际动作”，而不是只打日志 |
| D5 | 进程日志持久化到 `runs/<RUN_ID>/logs/{api,ui,ambient}.log` | 两个进程各自可审计；不再随临时目录删除 |
| D6 | ambient 带 `--parent-pid $$` 看护；退出清理先 ambient 后 UI/API，并 `wait` 收割 | start.sh 退出即退出、无僵尸 |
| D7 | 玩家进出沿用原 0 回滚口径重跑（`enter_player`/`leave_player`） | 与 W2 证据口径一致，不换方案 |

## 2. 实施细节

### 2.1 `src/wsim/sim/scheduler.py`

`AmbientConfig` 新增/调整：
- `max_cpu_ratio: float = 0.70`（原 0.85）。
- `cpu_sample_s: float = 0.25`：CPU 采样窗口。
- `cpu_pause_after_s: float = 2.0`：持续超限多久后暂停。
- `cpu_resume_below_ratio: float = 0.35`：恢复线（相对 `max_cpu_ratio` 的绝对值，即 0.35）。
- `cpu_resume_after_s: float = 2.0`：低于恢复线持续多久后恢复。
- `parent_pid: int | None = None`：父进程看护。
- `status_file: Path | None = None`：默认 `<out_dir>/ambient_status.json`。

新增状态文件（原子写：tmp + `os.replace`），每 tick / pause / resume / stop / error 更新：

```json
{
  "ok": true,
  "run_id": "ambient",
  "pid": 12345,
  "state": "running|paused|stopped",
  "reason": "",
  "tick": 42,
  "last_tick_at": "2026-11-05T12:00:00+08:00",
  "updated_at": "2026-11-05T12:00:00+08:00",
  "speed": 1.0,
  "npc_count": 31,
  "events_total": 100,
  "cpu": {
    "ratio_avg": 0.0036,
    "limit": 0.7,
    "throttles": 0,
    "pauses": 0,
    "resumes": 0
  }
}
```

CPU 护栏（`_cpu_guard(wall, cpu)` 改造）：
1. 以 work wall（不含补睡）计算原始比例 `raw = cpu / work`。
2. `raw > limit`：补睡 `cpu/limit - work`，计 `cpu_guard_sleeps`；累计超限时长（work + 补睡）。
3. 超限时长 ≥ `cpu_pause_after_s`：`pause(source="cpu_guard", reason="cpu_ratio_over_limit")`，
   写状态 `paused`，计 `cpu_pauses`；不向服务端抛错。
4. 暂停期间由 `_cpu_pause_watch` 采样；比例 ≤ `cpu_resume_below_ratio` 持续
   `cpu_resume_after_s` 秒后 `resume(source="cpu_guard")`，计 `cpu_resumes`。
5. 普通暂停不触发 CPU 恢复；`run_ticks` 在 CPU 暂停时停止推进，避免忙等。

父进程看护：`start()` 每 0.5s 检查 `parent_pid`；父进程消失则
`stop(source="parent_gone")` 并写 `stopped`。

`final_summary()` 追加键：`cpu_pauses`、`cpu_resumes`、`cpu_ratio_limit`、
`parent_pid`、`status_file`；其余键语义不变。

### 2.2 `tools/ambient_run.py`

- 新增 `--parent-pid`、`--cpu-pause-after`、`--cpu-resume-below`、`--cpu-resume-after`。
- `--max-cpu` 仍按比例（0–1）传入；新增 `--max-cpu-pct`（70 表示 0.70）。
- `AMBIENT_RESULT` 追加 `cpu_pauses`、`cpu_resumes`、`status_file`，原有键不变。

### 2.3 `src/wsim/api/server.py`

新增模块内只读函数（不改 session 状态）：
- `_ambient_health_payload(session)`：读取
  `XKX_AMBIENT_STATUS` 或 `<session.run_dir>/ambient/ambient_status.json`；
  文件缺失/过期（>120s，可 `XKX_AMBIENT_STALE_S` 覆盖）/进程不存在 → `ambient="stopped"`。
- `GET /api/health`（新端点，匿名）返回：

```json
{
  "ok": true, "mode": "live", "readonly": false, "profile_version": 1,
  "run": "player-demo", "auth_required": false, "tick": 0,
  "ambient": "running", "ambient_tick": 42,
  "ambient_last_tick_at": "2026-11-05T12:00:00+08:00",
  "ambient_reason": "", "ambient_pid": 12345, "ambient_speed": 1.0
}
```

- `GET /api/service/info` 保持原键与值，仅追加上述 `ambient*` 键。
- `ui/player/server.py` 的 `_health()` **只追加** `ambient*` 键：
  live 模式从上游 `/api/service/info` 取 ambient 字段；readonly 模式读
  `<run>/ambient/ambient_status.json`（缺失 = stopped）。
  `ok/mode/readonly/profile_version/upstream/upstream_ok/docs` 原值与判定逻辑一律不动。

### 2.4 `scripts/demo.sh` + `start.sh`

- 环境变量：`XKX_AMBIENT`（默认 `1`；`0/false/off/no` 关闭）、
  `XKX_AMBIENT_CPU`（默认 `70`，百分比）、`XKX_AMBIENT_SPEED`（默认 `1`）。
- `RUN_ID` 确定后再算日志目录 `runs/<RUN_ID>/logs/`；API/UI/ambient 各自写
  `api.log` / `ui.log` / `ambient.log`，退出不删除。
- API 健康检查通过后启动 ambient（后台、不阻塞）：
  `tools/ambient_run.py --run-id ambient --out runs/<RUN_ID> --speed ... --max-cpu-pct ... --parent-pid $$`。
- 若 `runs/<RUN_ID>/ambient` 已存在，先整体改名为 `ambient.<时间戳>.<pid>` 保留旧数据，再起新世界。
- 退出清理顺序：ambient → UI → API；每个进程 `kill` 后 `wait` 收割，防僵尸。
- ambient 意外退出：日志告警并继续提供 API/UI（不拖垮服务端）；API/UI 退出仍整体清理。
- `XKX_AMBIENT=0` 时不启动 ambient、不创建 ambient 目录，日志与进程行为与当前版本一致。

### 2.5 测试与文档

- 新增 `tests/test_ambient_default.py`：
  1. 默认 `max_cpu_ratio == 0.70`。
  2. 合成 CPU 超限：先补睡（throttle），累计 ≥2s 后 `paused`，恢复线以下 2s 后 `running`；
     状态文件与 control 日志同步。
  3. 状态文件在 tick/pause/resume/stop 更新且字段齐全。
  4. API `/api/health` 与 `/api/service/info`：ambient 三态、旧键不变；无状态文件 = `stopped`。
  5. `parent_pid` 不存在 → `start()` 快速 `stopped`。
  6. `scripts/demo.sh` / `start.sh` 的 `bash -n` 与 `XKX_AMBIENT=0` 静态分支检查。
- 更新 `docs/AMBIENT.md`：默认开启、关闭开关、状态文件、CPU 护栏、日志位置、本轮实测。
- `CHANGELOG.md` 追加一条（改了什么/为什么/验证/commit）。

## 3. 验收（干净 HEAD 执行）

```bash
.venv/bin/python -m pytest
bash scripts/selfcheck.sh
bash -n start.sh scripts/demo.sh
```

硬指标：
- 300 ticks 空场：≥300 ticks、≥12 NPC 自主事件、≥4 个 NPC、≥3 动词、0 LLM。
- 玩家第 150 tick 进入、第 210 tick 退出：progress 0..299 连续，进入前前缀逐字节不变，0 回滚。
- CPU 护栏：**注入真实 CPU 负载**（临时 harness 在每个 tick 内忙等）在 70% 真实阈值下
  观测 `throttle → paused → running` 全链路；另报告日常 0 负载占用。不接受只测正常态。
- `start.sh` 默认启动：`/api/health` 返回 `ambient="running"` 且 tick 递增；
  `XKX_AMBIENT=0` 返回 `ambient="stopped"`；Ctrl-C 后无残留进程/僵尸、日志文件保留。
- 零模型调用：`llm_calls == 0`。

## 4. 提交与边界

- 显式 pathspec，分次提交：scheduler+runner / API+测试 / demo 脚本 / 文档+CHANGELOG；逐次 push。
- 不改：`src/wsim/sim/intent.py`（另一会话修改中）、`content/**`；不打 tag、不重打 p3k。
  `ui/player/server.py` 仅限 `_health()` 的 ambient 字段追加（W4 e2e 已确认 service 模式全通）。
- 不使用 `git add -A`。

## 5. 已知取舍

- `ui/player` 的 `/api/health` 仅追加 ambient 字段，不改任何现有键与 live/degraded 判定；
  若并行 W4 会话在同一函数上有改动，合并时以“现有键零改动”为准。
- ambient 与 API 是两个独立世界（各自 `events.sqlite`），本轮不把自走事件并入玩家存档，
  避免引入双写者；玩家进出的“0 回滚”证据按调度器模拟口径复验。玩家可观测入口为
  `/api/health` 的 `ambient_tick`。
