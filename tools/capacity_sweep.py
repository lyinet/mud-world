#!/usr/bin/env python3
"""容量阶梯实测驱动：每档位起一个干净服务端 → 跑 load_test → 关服务端。

用法：
  .venv/bin/python tools/capacity_sweep.py \
      --levels 50,100,150,200,300 --duration 90 --think-time 2 \
      --out reports/capacity

200 席以上档位会自动注入 tools/capacity_overrides（临时抬高席位上限），
出厂上限仍是 200；报告中必须注明该档位是实验口径。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _free_port(start: int) -> int:
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port")


def _health(base: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _wait_health(base: str, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _health(base)
        if data and data.get("ok"):
            return data
        time.sleep(0.5)
    raise RuntimeError(f"server not healthy: {base}")


def _loadavg() -> list[float]:
    return list(os.getloadavg())


def run_level(level: int, args: argparse.Namespace, index: int) -> dict:
    run_id = f"cap-{args.label}-{level}"
    run_dir = ROOT / "runs" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    port = _free_port(8140 + index * 3)
    base = f"http://127.0.0.1:{port}"
    log = args.out / f"server-{level}.log"
    env = dict(os.environ)
    env["XKX_AMBIENT"] = "0"
    pythonpath = [str(SRC)]
    if level > 200:
        pythonpath.insert(0, str(ROOT / "tools" / "capacity_overrides"))
        env["XKX_BENCH_MAX_SEATS"] = str(level)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    server = subprocess.Popen(
        [sys.executable, "-m", "wsim.api", "--root", str(ROOT), "--run", run_id,
         "--auth", "on", "--port", str(port), "--seed", "42"],
        cwd=str(ROOT), env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT,
    )
    try:
        health = _wait_health(base)
        loadavg_before = _loadavg()
        report_path = args.out / f"raw-{args.label}-{level}.json"
        cmd = [
            sys.executable, str(ROOT / "tools" / "load_test.py"),
            "--players", str(level),
            "--duration", str(args.duration),
            "--think-time", str(args.think_time),
            "--ramp-up", str(args.ramp_up),
            "--base-url", base,
            "--run-dir", str(run_dir),
            "--server-pid", str(server.pid),
            "--sample-interval", str(args.sample_interval),
            "--report", str(report_path),
        ]
        result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=900)
        (args.out / f"load-{level}.log").write_text(result.stdout + result.stderr)
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        report["host"] = {
            "loadavg_before": loadavg_before,
            "loadavg_after": _loadavg(),
            "bench_cap_override": level > 200,
        }
        report["health_at_start"] = health
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return report
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="50,100,150,200,300")
    parser.add_argument("--duration", type=float, default=90)
    parser.add_argument("--think-time", type=float, default=2.0)
    parser.add_argument("--ramp-up", type=float, default=8.0)
    parser.add_argument("--sample-interval", type=float, default=2.0)
    parser.add_argument("--label", default="base")
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "capacity")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    for i, level in enumerate(levels):
        print(f"=== level {level} ===", flush=True)
        report = run_level(level, args, i)
        totals = report.get("totals", {})
        state = (totals.get("latency_ms", {}).get("by_endpoint", {})
                 .get("GET /api/player/state", {}))
        print(
            f"  state p50/p95/p99={state.get('p50')}/{state.get('p95')}/{state.get('p99')}ms "
            f"err={totals.get('errors')} 429cap={totals.get('capacity_rejections')} "
            f"writes/s={report.get('throughput', {}).get('write_ops_per_second')} "
            f"cpu={report.get('resources', {}).get('cpu_mean_pct')}% "
            f"rss={report.get('resources', {}).get('rss_peak_mb')}MB "
            f"seq_gap_max={report.get('resources', {}).get('seq_gap_max')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
