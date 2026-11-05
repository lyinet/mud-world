# Tasks · 世界自走默认开启

## T1 调度器 CPU 护栏与状态
- [ ] `src/wsim/sim/scheduler.py`：`AmbientConfig` 默认 `max_cpu_ratio=0.70`，新增采样/暂停/恢复/父进程字段
- [ ] 新增 `ambient_status.json` 原子写（running/paused/stopped + tick + last_tick_at + cpu 计数）
- [ ] `_cpu_guard` 改造：超限补睡 → 持续超限 `pause(source="cpu_guard")`
- [ ] `_cpu_pause_watch`：低于恢复线持续 2s 自动恢复
- [ ] `start()` 父进程看护（`parent_pid` 消失即 stop）
- [ ] `final_summary()` 追加 `cpu_pauses/cpu_resumes/cpu_ratio_limit/status_file`
- [ ] `tools/ambient_run.py`：`--parent-pid`、`--max-cpu-pct` 等参数与结果字段

## T2 API 健康字段
- [ ] `src/wsim/api/server.py`：`_ambient_health_payload(session)`（文件缺失/过期/进程消失 → stopped）
- [ ] 新增 `GET /api/health`（mode/profile_version 与 W4 契约一致）
- [ ] `GET /api/service/info` 只追加 `ambient*` 键，原键值不变
- [ ] `ui/player/server.py::_health()` 只追加 `ambient*`，保留 ok/mode/readonly/profile_version/upstream_ok

## T3 一键默认启动与进程管理
- [ ] `scripts/demo.sh`：`XKX_AMBIENT` 默认开启、`XKX_AMBIENT=0` 关闭
- [ ] 日志落 `runs/<RUN_ID>/logs/{api,ui,ambient}.log`（退出保留）
- [ ] API 就绪后独立启动 ambient；不阻塞启动
- [ ] 旧 `runs/<RUN_ID>/ambient` 改名归档，避免重复创世
- [ ] 清理顺序 ambient→UI→API，全部 `wait` 收割；ambient 崩溃不拖垮 API/UI
- [ ] `start.sh` 使用说明与启动日志提示

## T4 测试
- [ ] `tests/test_ambient_default.py`：默认阈值、throttle→pause→resume、状态文件、父进程看护
- [ ] API health/service-info 三态与旧键兼容测试
- [ ] `bash -n start.sh scripts/demo.sh`

## T5 实测与证据
- [ ] 300 ticks 空场：事件量 / NPC 数 / 动词 / 0 LLM
- [ ] 玩家第 150 tick 进入、210 tick 退出：progress 连续、0 回滚
- [ ] CPU 护栏实测触发与恢复（70% 真实阈值 + 注入忙等负载） + 日常占用
- [ ] `start.sh` 默认运行 smoke：UI/API `/api/health ambient=running` 且 tick 递增；`XKX_AMBIENT=0` 为 stopped
- [ ] Ctrl-C 后无残留/僵尸，日志文件存在

## T6 文档与提交
- [ ] `docs/AMBIENT.md` 更新
- [ ] `CHANGELOG.md` 追加一条
- [ ] 显式 pathspec 分 4 次 commit 并 push（不打 tag）
- [ ] 干净 HEAD：`pytest` 计数 + `selfcheck.sh` 4/4
