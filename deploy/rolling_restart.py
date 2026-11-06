#!/usr/bin/env python3
"""滚动重启守护：内存持续上涨 / 读延迟连续超标 / CPU 连续超标 → 优雅重启 world。

在宿主机运行（只通过 docker 与 HTTP 观测容器，不依赖容器内代码）：

    python3 deploy/rolling_restart.py --once --dry-run --rss-limit-mb 50 --rss-streak 1
    python3 deploy/rolling_restart.py --record-baseline          # 记录 CPU 基线
    python3 deploy/rolling_restart.py --loop --interval 60       # 常驻巡检
    python3 deploy/rolling_restart.py --loop --max-restarts 1    # 演练：只重启一次

阈值语义（与 docs/SOAK-RESULTS.md §10 一致）：

- 内存**主判据是当前 RSS**（容器内 API 进程 `/proc/<pid>/status` 的 `VmRSS`）。
  容器 PID 1 是 `deploy/entrypoint.sh`，真正的 API 是它的子进程，因此按 cmdline
  含 `wsim.api` 动态定位 pid，并打印该 pid，避免读错进程。
  触发条件：连续 `--rss-streak`（默认 3）个采样窗严格单调上涨，且平均斜率
  > `--rss-slope-mb-min`（默认 0.5 MB/min，约 800MB 时的 3.75%/h，低于验收线 5%/h），
  同时当前 RSS ≥ `--rss-floor-mb`（默认 200，躲开启动预热）。
  另保留“绝对上限 + 连续 N 窗”兜底（默认 800MB × 3 窗），防止低斜率的大内存常驻。
  `VmHWM`（等价 `ru_maxrss` 的峰值）只记录、不参与决策——峰值只增不减，
  一次备份/GC 尖峰就会永久超阈值，无法区分尖峰与泄漏。

- 读 P95：Prometheus 直方图两次采样的**增量**算 P95，连续 `--p95-streak`（默认 3）
  窗 > `--p95-limit-ms`（默认 500ms）才重启。

- CPU：`docker stats` 进程 CPU% 连续 `--cpu-streak`（默认 2）窗 > `--cpu-mult`（默认 2×）
  基线。基线用 `--record-baseline` 在正常负载下采样，写入 `deploy/.cpu_baseline`
  （已 gitignore）。基线漂移判断：正常时段连续 3 次采样相对基线 > ±30% 视为世界
  负载/配速变了，需要重录基线，而不是按旧基线重启。

为什么用 compose stop/up 而不是 docker restart：`stop_grace_period`（当前 45s，
依据 2 小时 soak 实测 SIGTERM→退出 31.24s + 余量）只在 stop 路径生效。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

METRIC_PREFIX = "xkx_http_request_duration_seconds_bucket"
READ_PATHS = ("/api/state", "/api/player/state", "/api/player/status", "/api/world/feed")
_BUCKET_MS = (5, 10, 25, 50, 100, 250, 500, 1000)

_RSS_PY = r"""
import glob
for entry in glob.glob("/proc/[0-9]*"):
    try:
        cmd = open(entry + "/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        continue
    if "wsim.api" not in cmd:
        continue
    pid = entry.rsplit("/", 1)[1]
    rss = hwm = 0
    with open(entry + "/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1])
            elif line.startswith("VmHWM:"):
                hwm = int(line.split()[1])
    print(f"{pid} {rss} {hwm}")
    break
else:
    print("0 0 0")
"""


def _sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, text=True, capture_output=True).stdout.strip()


def _http_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


def metric_snapshot(url: str, paths: tuple[str, ...], token: str = "") -> dict[str, list[int]]:
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-Metrics-Token", token)
    with urllib.request.urlopen(req, timeout=5) as resp:
        text = resp.read().decode("utf-8", "replace")
    out: dict[str, list[int]] = {}
    for line in text.splitlines():
        if not line.startswith(METRIC_PREFIX):
            continue
        match = re.match(r'[^}]*path="([^"]+)"[^}]*le="([^"]+)"} (\d+)', line)
        if not match:
            continue
        path, le, value = match.group(1), match.group(2), int(match.group(3))
        if path not in paths:
            continue
        out.setdefault(path, []).append(value)  # 桶顺序由 metrics.py 固定
    return out


def p95_ms(prev: dict[str, list[int]], cur: dict[str, list[int]]) -> float | None:
    total = 0
    buckets = [0] * 9
    for path, cur_buckets in cur.items():
        old = prev.get(path, [0] * len(cur_buckets))
        for i, value in enumerate(cur_buckets):
            delta = value - (old[i] if i < len(old) else 0)
            if delta > 0:
                buckets[i] += delta
                total += delta
    if total == 0:
        return None
    target = 0.95 * total
    cumulative = 0
    for i, value in enumerate(buckets):
        cumulative += value
        if cumulative >= target:
            return 1000.0 if i >= 8 else float(_BUCKET_MS[i])
    return None


def rss_sample(container: str) -> tuple[int, int, int]:
    """返回 (pid, 当前 RSS MB, 峰值 VmHWM MB)；读不到时退回 docker stats。"""
    try:
        raw = _sh(["docker", "exec", container, "python", "-c", _RSS_PY])
        pid, rss_kb, hwm_kb = (int(x) for x in raw.split()[:3])
        if pid:
            return pid, rss_kb // 1024, hwm_kb // 1024
    except (subprocess.CalledProcessError, ValueError):
        pass
    raw = _sh(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container])
    used = raw.split("/")[0].strip()
    match = re.match(r"([\d.]+)\s*([KMG]i?B)", used)
    if not match:
        raise SystemExit(f"cannot parse docker stats mem: {raw}")
    value = float(match.group(1))
    unit = match.group(2)
    mb = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "KB": 1 / 1000, "MB": 1, "GB": 1000}[unit]
    return 0, int(value * mb), int(value * mb)


def cpu_pct(container: str) -> float:
    raw = _sh(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", container])
    return float(raw.strip().rstrip("%"))


def compose(args: list[str], compose_files: list[str], project: str | None) -> None:
    base = ["-f", compose_files[0]]
    for extra in compose_files[1:]:
        base += ["-f", extra]
    if project:
        base = ["-p", project] + base
    for cmd in (["docker", "compose"], ["docker-compose"]):
        try:
            _sh(cmd + base + args)
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise SystemExit("docker compose 不可用")


def wait_health(url: str, timeout: float) -> float:
    started = time.time()
    while time.time() - started < timeout:
        try:
            body = _http_json(url)
            if body.get("ok"):
                return round(time.time() - started, 2)
        except Exception:
            pass
        time.sleep(1.0)
    return -1.0


_WARNED: set[str] = set()


def metrics_snapshot(url: str, paths: tuple[str, ...], token: str) -> dict[str, list[int]]:
    """抓取直方图；不可用时（404/鉴权/超时）只告警一次，P95 判据降级为停用。"""
    if "metrics" not in url:
        return {}
    try:
        return metric_snapshot(url, paths, token)
    except Exception as exc:
        if "metrics" not in _WARNED:
            _WARNED.add("metrics")
            print(
                f"[rolling-restart] WARN /api/metrics 不可用（{exc}），P95 判据停用；"
                "容器内 API 绑 0.0.0.0 时需要设置 XKX_METRICS_TOKEN 并用 --metrics-token 传入",
                file=sys.stderr,
                flush=True,
            )
        return {}


def _fingerprint(state: dict) -> dict:
    """重启前后对比的**持久化**字段；排除 idle_seconds/online/activity 等运行时量。

    `/api/state?account_id=` 把玩家放在 `player` 下，tick 在顶层；钱包是数据无损的
    直接证据，所以一并比较。
    """
    player = state.get("player") or {}
    wallet = player.get("wallet") or {}
    return {
        "ok": state.get("ok"),
        "tick": state.get("tick"),
        "name": player.get("name"),
        "archetype": player.get("archetype"),
        "place": player.get("location_name"),
        "money": wallet.get("money"),
        "items": sorted((i.get("item"), i.get("qty")) for i in (wallet.get("items") or [])),
        "marriage": (player.get("marriage") or {}).get("status"),
        "skills": sorted((player.get("skills") or {}).items()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="xkx-world")
    parser.add_argument("--service", default="world")
    parser.add_argument("-f", "--compose-file", action="append", dest="compose_files",
                        help="可重复，后一个覆盖前一个（演练用 override）")
    parser.add_argument("--project", default=None, help="compose 项目名（演练时隔离用）")
    parser.add_argument("--health-url", default="http://127.0.0.1:8020/api/health")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:8020/api/metrics")
    parser.add_argument("--metrics-token", default=None,
                        help="容器内 API 绑 0.0.0.0 时 /api/metrics 需要令牌；默认读 XKX_METRICS_TOKEN")
    parser.add_argument("--state-url", default="", help="如 http://127.0.0.1:8020/api/state?account_id=u")
    parser.add_argument("--health-timeout", type=float, default=90.0)
    parser.add_argument("--rss-limit-mb", type=int, default=800)
    parser.add_argument("--rss-streak", type=int, default=3)
    parser.add_argument("--rss-slope-mb-min", type=float, default=0.5)
    parser.add_argument("--rss-floor-mb", type=int, default=200)
    parser.add_argument("--p95-limit-ms", type=float, default=500.0)
    parser.add_argument("--p95-streak", type=int, default=3)
    parser.add_argument("--cpu-mult", type=float, default=2.0)
    parser.add_argument("--cpu-streak", type=int, default=2)
    parser.add_argument("--cpu-baseline-pct", type=float, default=None)
    parser.add_argument("--baseline-file", default=str(Path(__file__).with_name(".cpu_baseline")))
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--once", action="store_true", help="立即采样一次（不等 interval）")
    parser.add_argument("--record-baseline", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="只打印触发原因，不重启")
    parser.add_argument("--max-restarts", type=int, default=0, help="0=不限")
    args = parser.parse_args()

    compose_files = args.compose_files or [str(Path(__file__).with_name("docker-compose.yml"))]
    baseline_file = Path(args.baseline_file)
    if args.record_baseline:
        baseline_file.write_text(f"{cpu_pct(args.container):.2f}\n")
        print(f"[rolling-restart] cpu baseline = {baseline_file.read_text().strip()}%")
        return 0
    if args.cpu_baseline_pct is not None:
        baseline = float(args.cpu_baseline_pct)
    elif baseline_file.exists():
        baseline = float(baseline_file.read_text().strip())
    else:
        print("[rolling-restart] 缺少 CPU 基线：先跑 --record-baseline，或传 --cpu-baseline-pct",
              file=sys.stderr)
        return 2

    metrics_token = args.metrics_token or os.environ.get("XKX_METRICS_TOKEN", "")
    prev = metrics_snapshot(args.metrics_url, READ_PATHS, metrics_token)
    rss_hist: list[float] = []
    rss_abs_streak = 0
    cpu_streak = 0
    p95_streak = 0
    restarts = 0
    pre_state = _http_json(args.state_url) if args.state_url else None
    sleep_s = 0.0 if (args.once or args.dry_run or args.record_baseline) else args.interval
    while True:
        if sleep_s:
            time.sleep(sleep_s)
        cur = metrics_snapshot(args.metrics_url, READ_PATHS, metrics_token)
        p95 = p95_ms(prev, cur) if prev else None
        prev = cur
        pid, rss, peak = rss_sample(args.container)
        cpu = cpu_pct(args.container)
        rss_hist.append(float(rss))
        if len(rss_hist) > args.rss_streak + 1:
            rss_hist.pop(0)
        rss_abs_streak = rss_abs_streak + 1 if rss > args.rss_limit_mb else 0
        cpu_streak = cpu_streak + 1 if cpu > args.cpu_mult * baseline else 0
        p95_streak = p95_streak + 1 if (p95 is not None and p95 > args.p95_limit_ms) else 0

        reasons = []
        if rss_abs_streak >= args.rss_streak:
            reasons.append(f"rss={rss}MB>={args.rss_limit_mb}MB for {rss_abs_streak} windows")
        if len(rss_hist) >= args.rss_streak + 1:
            strictly_up = all(b > a for a, b in zip(rss_hist, rss_hist[1:]))
            slope = (rss_hist[-1] - rss_hist[0]) / (len(rss_hist) - 1) / (args.interval / 60.0)
            if strictly_up and slope > args.rss_slope_mb_min and rss >= args.rss_floor_mb:
                reasons.append(
                    f"rss_slope={slope:.2f}MB/min>{args.rss_slope_mb_min}MB/min over "
                    f"{len(rss_hist) - 1} windows ({rss_hist[0]:.0f}->{rss_hist[-1]:.0f}MB)"
                )
        if cpu_streak >= args.cpu_streak:
            reasons.append(f"cpu={cpu:.1f}%>{args.cpu_mult:.1f}x baseline({baseline:.1f}%) "
                           f"for {cpu_streak} windows")
        if p95_streak >= args.p95_streak:
            reasons.append(f"read_p95={p95}ms>{args.p95_limit_ms}ms for {p95_streak} windows")

        print(json.dumps({
            "ts": time.strftime("%H:%M:%S"), "api_pid": pid, "rss_mb": rss,
            "peak_rss_mb": peak, "cpu_pct": round(cpu, 1), "p95_ms": p95,
            "rss_windows": len(rss_hist), "rss_abs_streak": rss_abs_streak,
            "cpu_streak": cpu_streak, "p95_streak": p95_streak,
            "trigger": bool(reasons), "reasons": reasons,
        }, ensure_ascii=False), flush=True)

        if reasons:
            if args.dry_run:
                print("[rolling-restart] DRY-RUN: 命中阈值，未执行重启", flush=True)
                return 1
            print(f"[rolling-restart] restart service={args.service}: " + "; ".join(reasons), flush=True)
            t_stop = time.time()
            compose(["stop", args.service], compose_files, args.project)
            stop_s = round(time.time() - t_stop, 2)
            compose(["up", "-d", args.service], compose_files, args.project)
            restarts += 1
            health_s = wait_health(args.health_url, args.health_timeout)
            health = {}
            try:
                health = _http_json(args.health_url)
            except Exception:
                pass
            post_state = _http_json(args.state_url) if args.state_url else None
            result = {
                "restart": restarts,
                "stop_s": stop_s,
                "health_200": health.get("ok", False),
                "health_ready_s": health_s,
                "seq_gap": (health.get("storage") or {}).get("seq_gap"),
                "events_ok": (health.get("storage") or {}).get("events_ok"),
                "integrity": (health.get("storage") or {}).get("integrity"),
                "state_match": None if (pre_state is None or post_state is None)
                else bool(_fingerprint(pre_state) == _fingerprint(post_state)),
            }
            print("[rolling-restart] post-restart " + json.dumps(result, ensure_ascii=False),
                  flush=True)
            pre_state = post_state
            rss_hist.clear()
            rss_abs_streak = cpu_streak = p95_streak = 0
            if args.max_restarts and restarts >= args.max_restarts:
                return 0
            prev = metrics_snapshot(args.metrics_url, READ_PATHS, metrics_token)
        if not args.loop:
            return 0


if __name__ == "__main__":
    sys.exit(main())
