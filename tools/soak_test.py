#!/usr/bin/env python3
"""长跑稳定性测试（soak）：起真实服务 → 持续施压 → 30s 采样 → 优雅停机/重启恢复校验。

回答的问题：这个服务能不能连续跑一个月不崩、不变慢、不涨内存。

用法（典型）：

    .venv/bin/python tools/soak_test.py --duration 7200 --players 50 --think-time 2

产出：

    reports/soak/<run>/samples.jsonl   每 30s 一行采样（RSS/CPU/事件数/DB 体积/窗口延迟）
    reports/soak/<run>/summary.json    汇总：首末 10 分钟 P95 对照、RSS 回归斜率、
                                       DB 增长（MB/h、条/h）、错误率、seq/守恒/完整性、
                                       优雅停机与重启恢复耗时、外推表、验收结论
    reports/soak/<run>/server.log      服务进程完整 stdout/stderr
    reports/soak/<run>/gc_probe.jsonl  服务进程内的定期 gc 计数/类型普查（定位内存热点用）

纪律：本工具不修改 src/**、ui/**、content/**、deploy/**；只用标准库 + 复用 tools/load_test.py
的虚拟玩家与校验函数。服务进程按规格以 `python -m wsim.api --auth off --run soak-<id>` 启动
并带 `XKX_AMBIENT=1`；gc 普查通过 PYTHONPATH 注入的 sitecustomize.py（写在报告目录内）
挂载到目标进程，不需要改源码。

另有一个诊断模式：

    .venv/bin/python tools/soak_test.py --mode heap --heap-minutes 20 --players 50

在同一进程内起 WorldSession（同样带 ambient）跑本地负载，用 tracemalloc 定期抓快照，
定位"哪个结构在涨"。仅在主 soak 发现 RSS 增长时按需使用。
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import random
import re
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import load_test as lt  # noqa: E402  复用现有驱动的会话/请求/校验能力


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mono() -> float:
    return time.monotonic()


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- window recorder
class WindowRecorder:
    """与 tools/load_test.py 的 Recorder 口径一致，但支持按采样窗口切片。

    误差口径刻意与 Recorder 保持一致：
      - error：连接失败/超时/5xx/除 429 容量拒绝外的 4xx；
      - capacity_rejection：429 且 code ∈ {player_cap_reached, endowment_cap_reached}；
      - business_rejection：HTTP 200 且 ok=false（任务过期、体力不足等业务拒绝，不算 error）。
    """

    KEYS = (
        "requests", "ok", "errors", "http_5xx", "http_401",
        "capacity_rejections", "business_rejections",
    )

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._total = {k: 0 for k in self.KEYS}
        self.reject_codes: Counter[str] = Counter()
        self.error_samples: list[str] = []
        self._new_window()

    def _new_window(self) -> None:
        self.w_started = _mono()
        self._win = {k: 0 for k in self.KEYS}
        self._lat: dict[str, list[float]] = {}

    def _bump(self, key: str) -> None:
        self._total[key] += 1
        self._win[key] += 1

    def record(self, endpoint: str, status: int, payload: dict[str, Any], elapsed_ms: float, error: str) -> None:
        code = lt._reason(payload)
        with self.lock:
            self._bump("requests")
            self._lat.setdefault(endpoint, []).append(elapsed_ms)
            if error:
                self._bump("errors")
                if len(self.error_samples) < 30:
                    self.error_samples.append(f"{endpoint}: {error}")
                return
            if status >= 500:
                self._bump("errors")
                self._bump("http_5xx")
                if len(self.error_samples) < 30:
                    self.error_samples.append(f"{endpoint}: HTTP {status} {code}")
                return
            if status == 429 and code in lt.CAPACITY_CODES:
                self._bump("capacity_rejections")
                return
            if status >= 400:
                if (endpoint == "POST /api/auth/register" and status == 409
                        and code == "auth_username_taken"):
                    self._bump("ok")
                    return
                self._bump("errors")
                if status == 401:
                    self._bump("http_401")
                if len(self.error_samples) < 30:
                    self.error_samples.append(f"{endpoint}: HTTP {status} {code}")
                return
            if status == 200:
                if payload.get("ok") is False:
                    self._bump("business_rejections")
                    self.reject_codes[code or "unknown_business_reject"] += 1
                else:
                    self._bump("ok")
                return
            self._bump("errors")
            if len(self.error_samples) < 30:
                self.error_samples.append(f"{endpoint}: HTTP {status} {code}")

    @staticmethod
    def _latencies(by_ep: dict[str, list[float]], prefixes: tuple[str, ...]) -> list[float]:
        merged: list[float] = []
        for ep, rows in by_ep.items():
            if ep.startswith(prefixes):
                merged.extend(rows)
        return merged

    def window(self, label: str = "") -> dict[str, Any]:
        """取走并重置当前窗口；返回窗口内计数与分端点 P50/P95/P99。"""
        with self.lock:
            by_ep = {
                ep: lt.percentiles(rows)
                for ep, rows in sorted(self._lat.items())
                if rows
            }
            read = self._latencies(self._lat, ("GET /api/state", "GET /api/player/state"))
            write = self._latencies(self._lat, ("POST /api/player/", "POST /api/state"))
            snapshot = {
                "window_s": round(_mono() - self.w_started, 2),
                "counts": dict(self._win),
                "read": lt.percentiles(read),
                "write": lt.percentiles(write),
                "by_endpoint": by_ep,
                "_raw_read": read,
                "_raw_write": write,
                "_t_end": _mono(),
            }
            self._new_window()
        snapshot["label"] = label
        return snapshot

    def totals(self) -> dict[str, Any]:
        with self.lock:
            denom = self._total["requests"] or 1
            return {
                **dict(self._total),
                "error_rate_pct": round(self._total["errors"] / denom * 100.0, 4),
                "rejections_by_code": dict(sorted(self.reject_codes.items())),
                "error_samples": list(self.error_samples),
            }


# --------------------------------------------------------------------------- server process
class ManagedServer:
    """以规格命令启动 `python -m wsim.api --auth off --run <run>`，并测量启动/停机耗时。"""

    def __init__(
        self,
        *,
        run_id: str,
        port: int,
        log_path: Path,
        probe_dir: Path | None,
        phase: str,
        ambient: bool = True,
        ambient_cpu: float = 70.0,
        extra_args: list[str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.port = port
        self.log_path = log_path
        self.probe_dir = probe_dir
        self.phase = phase
        self.ambient = ambient
        self.ambient_cpu = ambient_cpu
        self.extra_args = list(extra_args or [])
        self.proc: subprocess.Popen[bytes] | None = None
        self.pid = 0
        self.spawn_to_health_s: float | None = None
        self.exit_code: int | None = None
        self.shutdown_s: float | None = None
        self._log_fh = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        parts = [str(ROOT / "src")]
        if self.probe_dir is not None:
            parts.insert(0, str(self.probe_dir))
        env["PYTHONPATH"] = os.pathsep.join(parts)
        env["PYTHONUNBUFFERED"] = "1"
        env["XKX_AMBIENT"] = "1" if self.ambient else "0"
        env["XKX_AMBIENT_CPU"] = str(self.ambient_cpu)
        if self.ambient:
            # 允许 soak 在取样指纹前把 ambient 暂停，避免“取样后仍在写事件”
            # 被重启校验误判成回放失败。
            env["XKX_AMBIENT_CONTROL"] = str(
                ROOT / "runs" / self.run_id / "ambient" / "ambient_control_in.json"
            )
        env["XKX_SOAK_PHASE"] = self.phase
        if self.probe_dir is not None:
            env["XKX_SOAK_PROBE_DIR"] = str(self.probe_dir)
            env.setdefault("XKX_SOAK_PROBE_INTERVAL", os.environ.get("XKX_SOAK_PROBE_INTERVAL", "300"))
        return env

    def start(self, timeout: float = 120.0) -> None:
        cmd = [
            sys.executable, "-m", "wsim.api",
            "--root", str(ROOT),
            "--run", self.run_id,
            "--auth", "off",
            "--port", str(self.port),
            *self.extra_args,
        ]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = self.log_path.open("a", encoding="utf-8")
        self._log_fh.write(f"\n# ---- {_iso()} launch ({self.phase}) pid_placeholder cmd={' '.join(cmd)}\n")
        self._log_fh.flush()
        t0 = _mono()
        self.proc = subprocess.Popen(
            cmd, cwd=str(ROOT), env=self._env(),
            stdout=self._log_fh, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.pid = int(self.proc.pid)
        deadline = t0 + timeout
        while _mono() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"服务进程提前退出 rc={self.proc.returncode}，日志见 {self.log_path}"
                )
            try:
                status, _, _ = lt._request(self.base, "/api/health", timeout=2.0)
                if status == 200:
                    self.spawn_to_health_s = round(_mono() - t0, 3)
                    self._log_fh.write(f"# ready pid={self.pid} spawn_to_health_s={self.spawn_to_health_s}\n")
                    self._log_fh.flush()
                    return
            except Exception:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"服务 {timeout}s 内未就绪：{self.log_path}")

    def stop(self, timeout: float = 90.0) -> dict[str, Any]:
        if self.proc is None:
            return {"exit_code": None, "shutdown_s": None}
        if self.proc.poll() is None:
            t0 = _mono()
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
                self.shutdown_s = round(_mono() - t0, 3)
                self.exit_code = self.proc.returncode
                note = "SIGTERM 超时后 SIGKILL"
            else:
                self.shutdown_s = round(_mono() - t0, 3)
                self.exit_code = self.proc.returncode
                note = "SIGTERM 正常退出"
        else:
            self.exit_code = self.proc.returncode
            note = "进程已自行退出"
        if self._log_fh is not None:
            self._log_fh.write(f"# ---- {_iso()} stopped rc={self.exit_code} shutdown_s={self.shutdown_s} ({note})\n")
            self._log_fh.flush()
            self._log_fh.close()
            self._log_fh = None
        return {
            "signal": "SIGTERM",
            "exit_code": self.exit_code,
            "shutdown_s": self.shutdown_s,
            "note": note,
        }


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _db_snapshot(run_dir: Path, *, count_rows: bool = True) -> dict[str, Any]:
    db = run_dir / "events.sqlite"
    out: dict[str, Any] = {"db_bytes_main": 0, "wal_bytes": 0, "db_bytes": 0,
                           "events_total": None, "seq_last": None, "seq_first": None}
    if not db.exists():
        return out
    main = db.stat().st_size
    wal = Path(str(db) + "-wal")
    wal_b = wal.stat().st_size if wal.exists() else 0
    out["db_bytes_main"] = int(main)
    out["wal_bytes"] = int(wal_b)
    out["db_bytes"] = int(main + wal_b)
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            if count_rows:
                out["events_total"] = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            out["seq_last"] = int(conn.execute("SELECT MAX(seq) FROM events").fetchone()[0] or 0)
            out["seq_first"] = int(conn.execute("SELECT MIN(seq) FROM events").fetchone()[0] or 0)
        finally:
            conn.close()
    except Exception as exc:
        out["db_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _health(base: str) -> dict[str, Any]:
    try:
        status, payload, _ = lt._request(base, "/api/health", timeout=5.0)
        if status == 200:
            return payload
    except Exception:
        pass
    return {}


def _ambient_status(run_dir: Path) -> dict[str, Any]:
    """读 ambient_status.json 的归因字段（每步原子重写，采样时取一次即可）。"""
    path = run_dir / "ambient" / "ambient_status.json"
    out: dict[str, Any] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for key in ("step_ms_p95", "step_ms_max", "lock_wait_ms_p95", "lock_wait_ms_max",
                "in_lock_ms_p95", "in_lock_ms_max", "step_parts", "store_ck",
                "step_ms_samples", "lag_ticks", "lag_ticks_max", "lag_steps",
                "ambient_tick", "world_tick", "npc_tick", "queue_backlog"):
        if key in raw:
            out[key] = raw[key]
    return out


def _machine_load() -> dict[str, Any]:
    """同机其他进程负载：loadavg + 前 5 个吃 CPU 的进程（不含本进程）。"""
    out: dict[str, Any] = {}
    try:
        load = os.getloadavg()
        out["loadavg"] = [round(v, 2) for v in load]
    except Exception:
        pass
    try:
        proc = subprocess.run(
            ["ps", "-Ao", "pid,pcpu,comm", "-r"],
            capture_output=True, text=True, timeout=5,
        )
        rows = []
        for line in proc.stdout.splitlines()[1:6]:
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                rows.append({"pid": int(parts[0]), "cpu": float(parts[1]),
                             "comm": os.path.basename(parts[2].strip())})
            except ValueError:
                continue
        out["top_cpu"] = rows
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- probe (server-side)
_SITE_CUSTOMIZE = '''"""soak 探针：由 tools/soak_test.py 生成，只做事后诊断，不改任何业务代码。

- 定期用 gc.get_objects() 统计对象总数与类型分布（廉价，可长期跑）；
- 找到 WorldSession / WorldState / AmbientScheduler 实例，记录其内部容器长度，
  用于回答"若有增长，是哪个结构在涨"；
- 结果追加写到 XKX_SOAK_PROBE_DIR/gc_probe.jsonl。
"""
from __future__ import annotations

import gc
import json
import os
import sys
import threading
import time
import traceback

_DIR = os.environ.get("XKX_SOAK_PROBE_DIR") or ""
_INTERVAL = float(os.environ.get("XKX_SOAK_PROBE_INTERVAL") or "300")
_PHASE = os.environ.get("XKX_SOAK_PHASE") or "soak"


def _snapshot_structures(objs):
    """只记录"活"的那一份：WorldSession.state 与 AmbientScheduler 自己的容器。

    不统计游离的 WorldState 副本：day-boundary 会 clone 状态，等待 gc 的副本会
    把总和算大，反而掩盖真实增长。
    """
    out = {}
    for obj in objs:
        try:
            name = type(obj).__name__
        except Exception:
            continue
        try:
            if name == "WorldSession":
                sizes = {}
                state = obj.state
                for key, value in vars(state).items():
                    if isinstance(value, (dict, list, set, tuple)) and value:
                        sizes[f"state.{key}"] = len(value)
                for key in ("requests", "intents", "_last_active", "_view_cache", "_price_cache"):
                    value = getattr(obj, key, None)
                    if isinstance(value, (dict, list, set, tuple)) and value:
                        sizes[f"session.{key}"] = len(value)
                out["WorldSession"] = sizes
            elif name == "AmbientScheduler":
                sizes = {}
                for key, value in vars(obj).items():
                    if isinstance(value, (dict, list, set, tuple)) and value:
                        sizes[f"ambient.{key}"] = len(value)
                out["AmbientScheduler"] = sizes
        except Exception:
            continue
    return out


_DUMPED = False
_DUMP_THRESHOLD = int(os.environ.get("XKX_SOAK_DUMP_EVENTS") or "21000")
_WANTED = ("Writer", "WorldSession", "AuditLog", "MCPGateway", "EventStore",
           "WorldState", "AmbientScheduler")


def _chain(obj, max_depth=3, max_nodes=60):
    out = []
    seen = {id(obj)}
    frontier = [(obj, 0)]
    while frontier and len(out) < max_nodes:
        cur, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        for ref in gc.get_referrers(cur):
            if id(ref) in seen:
                continue
            seen.add(id(ref))
            t = type(ref).__name__
            info = {"type": t, "depth": depth + 1}
            code = getattr(ref, "f_code", None)
            if code is not None:
                info["code"] = {"name": code.co_name,
                                "file": os.path.basename(code.co_filename),
                                "line": code.co_firstlineno}
            if isinstance(ref, dict):
                info["len"] = len(ref)
                info["keys"] = [str(k) for k in list(ref.keys())[:8]]
            elif isinstance(ref, (list, tuple, set, frozenset)):
                info["len"] = len(ref)
            else:
                try:
                    attrs = [k for k, v in list(vars(ref).items())[:80] if v is cur]
                except Exception:
                    attrs = []
                if attrs:
                    info["attrs"] = attrs
            out.append(info)
            if t not in _WANTED:
                frontier.append((ref, depth + 1))
    return out


def _dump_owners(objs, counter):
    """一次性回溯：最老 3 个 Event（已从 _seen 淘汰）+ 首尾 AuditRecord + WorldState。"""
    global _DUMPED
    held = {"instances": {n: counter.get(n, 0) for n in _WANTED + ("Event", "AuditRecord")},
            "events": [], "audit": [], "states": []}
    evs = [o for o in objs if type(o).__name__ == "Event"]
    evs.sort(key=lambda e: int(getattr(e, "seq", 0)))
    oldest = evs[:3]
    newest = evs[-3:]
    for tag, batch in (("oldest", oldest), ("newest", newest)):
        for ev in batch:
            held["events"].append({"age": tag, "seq": getattr(ev, "seq", None),
                                   "chain": _chain(ev)})
    del evs, oldest, newest
    recs = [o for o in objs if type(o).__name__ == "AuditRecord"]
    recs.sort(key=lambda r: int(getattr(r, "seq", 0)))
    for tag, batch in (("oldest", recs[:3]), ("newest", recs[-3:])):
        for rec in batch:
            held["audit"].append({"age": tag, "seq": getattr(rec, "seq", None),
                                  "chain": _chain(rec)})
    del recs
    states = [o for o in objs if type(o).__name__ == "WorldState"][:3]
    for st in states:
        held["states"].append({"tick": getattr(st, "tick", None),
                               "id": id(st), "chain": _chain(st)})
    del states
    with open(os.path.join(_DIR, "owners.json"), "w", encoding="utf-8") as fh:
        json.dump(held, fh, ensure_ascii=False, indent=1)


# ---- write latency phase probe --------------------------------------------
# 只做事后诊断：给 WorldSession.perform / Writer.submit / sqlite / audit 包一层计时，
# 命中慢请求时把"锁等待 vs 执行"与线程栈一起落盘，用于定位 write.max 尖刺。
_LAT_ENABLED = str(os.environ.get("XKX_SOAK_LAT_PROBE") or "1").strip().lower() not in (
    "", "0", "false", "off", "no")
_LAT_RECORD_MS = float(os.environ.get("XKX_SOAK_LAT_RECORD_MS") or "100")
_LAT_STACK_MS = float(os.environ.get("XKX_SOAK_LAT_STACK_MS") or "250")
_LAT_PATH = os.path.join(_DIR, "lat_probe.jsonl")
_LAT_STACK_PATH = os.path.join(_DIR, "lat_stacks.jsonl")
_LAT_SAMPLE_AFTER_MS = float(os.environ.get("XKX_SOAK_LAT_SAMPLE_AFTER_MS") or "80")
_LAT_SAMPLE_EVERY_S = max(0.01, float(os.environ.get("XKX_SOAK_LAT_SAMPLE_S") or "0.05"))
_LAT_LOCK = threading.Lock()
_LAT_TLS = threading.local()
_LAT_ACTIVE: dict[str, dict] = {}


def _lat_ms(t0):
    return (time.perf_counter() - t0) * 1000.0


def _lat_rec():
    return getattr(_LAT_TLS, "rec", None)


def _lat_bump(key, dt):
    r = _lat_rec()
    if r is not None:
        r["phases"][key] = round(r["phases"].get(key, 0.0) + float(dt), 3)


def _lat_write(row):
    with _LAT_LOCK:
        try:
            with open(_LAT_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\\n")
        except Exception:
            pass


def _lat_write_to(path, row):
    with _LAT_LOCK:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\\n")
        except Exception:
            pass


# GC 停顿 >15ms 单独记一条：区分"应用在等磁盘"和"解释器整体被 GC 停住"。
_GC_PAUSE_PATH = os.path.join(_DIR, "gc_pauses.jsonl")
_GC_T0: list[float] = []


def _gc_pause_cb(phase, info):
    try:
        if phase == "start":
            _GC_T0.append(time.perf_counter())
            return
        if not _GC_T0:
            return
        dt = _lat_ms(_GC_T0.pop())
        if dt >= 15.0:
            _lat_write_to(_GC_PAUSE_PATH, {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "gen": info.get("generation"),
                "ms": round(dt, 2),
                "collected": info.get("collected"),
            })
    except Exception:
        pass


try:
    gc.callbacks.append(_gc_pause_cb)
except Exception:
    pass


def _lat_stacks():
    out: dict[str, list[str]] = {}
    names = {t.ident: t.name for t in threading.enumerate()}
    for tid, fr in sys._current_frames().items():
        frames = traceback.extract_stack(fr, limit=40)[-12:]
        out[names.get(tid, str(tid))] = [
            f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in frames
        ]
    return out


def _lat_sizes(session):
    out: dict[str, object] = {}
    try:
        st = session.writer.state
        for key, value in vars(st).items():
            if isinstance(value, (dict, list, set, tuple)) and value:
                out["state." + key] = len(value)
        out["writer.seen"] = len(getattr(session.writer, "_seen", {}))
        out["writer.player_ticks"] = len(getattr(session.writer, "_player_ticks", {}))
        out["session.requests"] = len(getattr(session, "requests", {}))
        out["store.count"] = session.store.count()
    except Exception:
        pass
    return out


def _patch_latency():
    S = sys.modules.get("wsim.api.session")
    if S is None or not _LAT_ENABLED:
        return not _LAT_ENABLED
    if getattr(S, "_soak_lat_patched", False):
        return True
    try:
        import wsim.mcp.audit as A
        import wsim.mcp.gateway as G
        import wsim.world.state as WS
        import wsim.world.store as ST
        import wsim.world.writer as W
    except Exception:
        return False

    class _ConnProxy:
        """透明代理 sqlite 连接：把 execute / commit 分别计时（checkpoint 在 commit 里）。"""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                return self._conn.execute(*args, **kwargs)
            finally:
                _lat_bump("sql_execute_ms", _lat_ms(t0))

        def commit(self, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                return self._conn.commit(*args, **kwargs)
            finally:
                _lat_bump("sql_commit_ms", _lat_ms(t0))

        def __getattr__(self, name):
            return getattr(self._conn, name)

    orig_perform = S.WorldSession.perform
    orig_perform_inner = S.WorldSession._perform
    orig_status_view = S.WorldSession._status_view
    orig_submit = W.Writer.submit
    orig_append = ST.EventStore.append
    orig_store_init = ST.EventStore.__init__
    orig_clone = WS.WorldState.clone
    orig_gcall = G.MCPGateway.call
    orig_audit_append = A.AuditLog.append
    orig_effects = W.apply_effects

    def perform(self, account, action, payload=None, request_id="", advance=True):
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "thread": threading.current_thread().name,
            "account": str(account),
            "action": str(action),
            "t0": time.perf_counter(),
            "phases": {},
            "verbs": [],
        }
        _LAT_TLS.rec = row
        try:
            return orig_perform(self, account, action, payload, request_id, advance)
        finally:
            total = _lat_ms(row.pop("t0"))
            row["total_ms"] = round(total, 2)
            if total >= _LAT_RECORD_MS:
                if total >= _LAT_STACK_MS:
                    row["active"] = {
                        k: {"action": v.get("action"), "account": v.get("account"),
                            "elapsed_ms": round(_lat_ms(v["t0"]), 2)}
                        for k, v in list(_LAT_ACTIVE.items())[:6]
                    }
                    # 只有"头车"（几乎没等锁）才值得抓栈；跟车者的栈都在锁上。
                    if float(row.get("lock_wait_ms") or 0.0) < 60.0:
                        row["stacks"] = _lat_stacks()
                row["sizes"] = _lat_sizes(self)
                row["gc_count"] = list(gc.get_count())
                _lat_write(row)
            _LAT_TLS.rec = None

    def perform_inner(self, account, action, payload, advance=True):
        row = _lat_rec()
        t0 = time.perf_counter()
        if row is not None and "exec_t0" not in row:
            row["exec_t0"] = t0
            row["lock_wait_ms"] = round(_lat_ms(row["t0"]), 2)
        name = threading.current_thread().name
        _LAT_ACTIVE[name] = {"action": str(action), "account": str(account), "t0": t0,
                             "ident": threading.get_ident()}
        try:
            return orig_perform_inner(self, account, action, payload, advance)
        finally:
            if row is not None:
                row["exec_ms"] = round(_lat_ms(t0), 2)
            _LAT_ACTIVE.pop(name, None)

    def status_view(self, pid):
        t0 = time.perf_counter()
        try:
            return orig_status_view(self, pid)
        finally:
            _lat_bump("status_view_ms", _lat_ms(t0))

    def submit(self, intent):
        t0 = time.perf_counter()
        row = _lat_rec()
        try:
            return orig_submit(self, intent)
        finally:
            _lat_bump("submit_ms", _lat_ms(t0))
            if row is not None:
                row["verbs"].append(str(getattr(intent, "verb", "")))
                row["phases"]["submit_calls"] = row["phases"].get("submit_calls", 0) + 1

    def append(self, event):
        t0 = time.perf_counter()
        try:
            if not isinstance(self.conn, _ConnProxy):
                self.conn = _ConnProxy(self.conn)
            return orig_append(self, event)
        finally:
            _lat_bump("store_append_ms", _lat_ms(t0))
            r = _lat_rec()
            if r is not None:
                r["phases"]["append_calls"] = r["phases"].get("append_calls", 0) + 1
                r["phases"]["append_bytes"] = r["phases"].get("append_bytes", 0) + len(
                    str(getattr(event, "args", "")) + str(getattr(event, "effects", ""))
                )

    def store_init(self, *args, **kwargs):
        orig_store_init(self, *args, **kwargs)
        if not isinstance(self.conn, _ConnProxy):
            self.conn = _ConnProxy(self.conn)

    def clone(self):
        t0 = time.perf_counter()
        try:
            return orig_clone(self)
        finally:
            _lat_bump("clone_ms", _lat_ms(t0))
            r = _lat_rec()
            if r is not None:
                r["phases"]["clone_calls"] = r["phases"].get("clone_calls", 0) + 1

    def gcall(self, caller, method, params=None):
        t0 = time.perf_counter()
        try:
            return orig_gcall(self, caller, method, params)
        finally:
            _lat_bump("gateway_ms", _lat_ms(t0))
            r = _lat_rec()
            if r is not None:
                key = "gateway:" + str(method)
                r["phases"][key] = round(r["phases"].get(key, 0.0) + _lat_ms(t0), 3)

    def audit_append(self, record):
        t0 = time.perf_counter()
        try:
            return orig_audit_append(self, record)
        finally:
            _lat_bump("audit_ms", _lat_ms(t0))
            r = _lat_rec()
            if r is not None:
                r["phases"]["audit_calls"] = r["phases"].get("audit_calls", 0) + 1

    def effects(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig_effects(*a, **k)
        finally:
            _lat_bump("effects_ms", _lat_ms(t0))

    S.WorldSession.perform = perform
    S.WorldSession._perform = perform_inner
    S.WorldSession._status_view = status_view
    W.Writer.submit = submit
    W.apply_effects = effects
    ST.EventStore.append = append
    ST.EventStore.__init__ = store_init
    WS.WorldState.clone = clone
    G.MCPGateway.call = gcall
    A.AuditLog.append = audit_append
    # 只有全部替换成功才置位：早期导入可能与主线程 import 竞争而抛错，
    # 置位过早会让后续轮询直接返回、探针静默失效。
    S._soak_lat_patched = True
    return True


def _lat_patch_loop():
    for _ in range(4000):
        try:
            if _patch_latency():
                return
        except Exception:
            try:
                with open(os.path.join(_DIR, "lat_probe.error.log"), "a", encoding="utf-8") as fh:
                    fh.write(traceback.format_exc() + "\\n")
            except Exception:
                pass
        time.sleep(0.05)


def _lat_sample_loop():
    """慢请求"正在执行时"的栈：每 50ms 扫一次仍在跑的请求，钉死具体卡点。"""
    if _LAT_SAMPLE_AFTER_MS <= 0:
        return
    last: dict[str, float] = {}
    while True:
        time.sleep(_LAT_SAMPLE_EVERY_S)
        try:
            frames = sys._current_frames()
            names = {t.ident: t.name for t in threading.enumerate()}
            now = time.perf_counter()
            for name, info in list(_LAT_ACTIVE.items()):
                elapsed = _lat_ms(info["t0"])
                if elapsed < _LAT_SAMPLE_AFTER_MS:
                    continue
                if now - last.get(name, 0.0) < 0.1:
                    continue
                fr = frames.get(info.get("ident"))
                if fr is None:
                    continue
                last[name] = now
                stack = [
                    f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}"
                    for f in traceback.extract_stack(fr, limit=60)[-18:]
                ]
                _lat_write_to(_LAT_STACK_PATH, {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "thread": names.get(info.get("ident"), name),
                    "action": info.get("action"),
                    "account": info.get("account"),
                    "elapsed_ms": round(elapsed, 2),
                    "stack": stack,
                })
        except Exception:
            pass


def _loop():
    global _DUMPED
    path = os.path.join(_DIR, "gc_probe.jsonl")
    started = time.monotonic()
    while True:
        time.sleep(_INTERVAL)
        try:
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "phase": _PHASE,
                   "elapsed_s": round(time.monotonic() - started, 1),
                   "pid": os.getpid()}
            try:
                import resource
                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                # macOS 的 ru_maxrss 单位是字节，Linux 是 KB
                row["maxrss_mb"] = round(peak / (1048576.0 if sys.platform == "darwin" else 1024.0), 2)
            except Exception:
                pass
            try:
                row["allocatedblocks"] = sys.getallocatedblocks()
            except Exception:
                pass
            objs = gc.get_objects()
            row["gc_objects"] = len(objs)
            row["gc_count"] = list(gc.get_count())
            row["gc_stats"] = [{"gen": i, "collections": s.get("collections", 0),
                                "collected": s.get("collected", 0)}
                               for i, s in enumerate(gc.get_stats())]
            counter = {}
            for obj in objs:
                try:
                    nm = type(obj).__name__
                except Exception:
                    continue
                counter[nm] = counter.get(nm, 0) + 1
            row["types_top"] = [[k, v] for k, v in sorted(counter.items(), key=lambda kv: -kv[1])[:25]]
            row["instances"] = {n: counter.get(n, 0) for n in (
                "Writer", "WorldSession", "AuditLog", "MCPGateway", "EventStore",
                "WorldState", "Event", "AuditRecord", "OrderedDict", "deque")}
            if not _DUMPED and counter.get("Event", 0) > _DUMP_THRESHOLD:
                _DUMPED = True
                try:
                    _dump_owners(objs, counter)
                except Exception:
                    with open(os.path.join(_DIR, "owners.error.log"), "a", encoding="utf-8") as fh:
                        fh.write(traceback.format_exc() + "\\n")
            row["structures"] = _snapshot_structures(objs)
            # 关键：objs 里含本线程 frame，frame 的局部变量又引用 objs，构成环；
            # 不显式删除会让每次快照把整个对象图钉住（Event/AuditRecord/WorldState 逐轮累积）。
            del objs, counter
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\\n")
        except Exception:
            with open(os.path.join(_DIR, "gc_probe.error.log"), "a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc() + "\\n")


try:
    threading.Thread(target=_loop, daemon=True, name="soak-gc-probe").start()
except Exception:
    pass

try:
    threading.Thread(target=_lat_patch_loop, daemon=True, name="soak-lat-probe").start()
except Exception:
    pass

try:
    threading.Thread(target=_lat_sample_loop, daemon=True, name="soak-lat-sampler").start()
except Exception:
    pass
'''


def _write_probe_dir(report_dir: Path) -> Path:
    probe = report_dir / "_probe"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "sitecustomize.py").write_text(_SITE_CUSTOMIZE, encoding="utf-8")
    return probe


def _median_rss(pid: int) -> tuple[float, float] | None:
    """ps 读到的 RSS 在 macOS 上会因页回收抖动；取 3 次中位数后再用。"""
    readings: list[tuple[float, float]] = []
    for _ in range(3):
        sample = lt._sample_process(pid)
        if sample is None:
            return None
        readings.append(sample)
        time.sleep(0.15)
    readings.sort(key=lambda row: row[0])
    return readings[len(readings) // 2]


def _memory_profile(report_dir: Path, warmup: float) -> dict[str, Any] | None:
    """把服务进程内 gc_probe.jsonl 归纳成"哪个结构在涨"。只统计 soak 阶段。"""
    path = report_dir / "_probe" / "gc_probe.jsonl"
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("phase") == "soak":
            rows.append(row)
    if len(rows) < 2:
        return None
    rows = [r for r in rows if float(r.get("elapsed_s") or 0) >= warmup] or rows
    first, last = rows[0], rows[-1]
    hours = max(1e-6, (float(last.get("elapsed_s") or 0) - float(first.get("elapsed_s") or 0)) / 3600.0)

    def _aggregate(row: dict[str, Any]) -> dict[str, int]:
        agg: dict[str, int] = {}
        for cls, attrs in (row.get("structures") or {}).items():
            base = cls.split("#")[0]
            for attr, value in (attrs or {}).items():
                key = f"{base}.{attr}"
                agg[key] = agg.get(key, 0) + int(value)
        return agg

    agg_first, agg_last = _aggregate(first), _aggregate(last)
    structures = {}
    for key in sorted(set(agg_first) | set(agg_last)):
        a, b = agg_first.get(key, 0), agg_last.get(key, 0)
        if b - a == 0 and b == 0:
            continue
        structures[key] = {"first": a, "last": b, "delta": b - a, "per_hour": round((b - a) / hours, 1)}
    type_first = dict(first.get("types_top") or [])
    type_last = dict(last.get("types_top") or [])
    type_rows = [
        {"type": name, "first": int(c1), "last": int(type_last.get(name, 0)),
         "delta": int(type_last.get(name, 0)) - int(c1)}
        for name, c1 in (first.get("types_top") or [])
    ]
    type_rows += [
        {"type": name, "first": 0, "last": count, "delta": count}
        for name, count in (last.get("types_top") or [])
        if name not in type_first
    ]
    type_rows.sort(key=lambda r: -abs(r["delta"]))
    maxrss_vals = [float(r["maxrss_mb"]) for r in rows if r.get("maxrss_mb") is not None]
    peak_slope = 0.0
    if len(maxrss_vals) >= 3:
        peak_slope = round(_regress(
            [float(r.get("elapsed_s") or 0) / 3600.0 for r in rows if r.get("maxrss_mb") is not None],
            maxrss_vals,
        )["slope"], 3)
    return {
        "probe_rows": len(rows),
        "window_s": [first.get("elapsed_s"), last.get("elapsed_s")],
        "gc_objects": {"first": first.get("gc_objects"), "last": last.get("gc_objects"),
                       "per_hour": round(((last.get("gc_objects") or 0) - (first.get("gc_objects") or 0)) / hours, 0)},
        "allocatedblocks": {"first": first.get("allocatedblocks"), "last": last.get("allocatedblocks")},
        "maxrss_peak_mb": {"first": first.get("maxrss_mb"), "last": last.get("maxrss_mb"),
                           "slope_mb_per_hour": peak_slope},
        "structures": structures,
        "types_top_growth": type_rows[:12],
    }


# --------------------------------------------------------------------------- sampler
class SoakSampler(threading.Thread):
    """每 interval 秒采样一次：进程 RSS/CPU、事件数/DB 体积、世界 tick、窗口延迟。"""

    def __init__(
        self,
        *,
        server: ManagedServer,
        run_dir: Path,
        recorder: WindowRecorder,
        samples_path: Path,
        interval: float,
        stop: threading.Event,
        start_at: float,
        phase: str,
    ) -> None:
        super().__init__(name="soak-sampler", daemon=True)
        self.server = server
        self.run_dir = run_dir
        self.recorder = recorder
        self.samples_path = samples_path
        self.interval = max(1.0, interval)
        self.stop = stop
        self.start_at = start_at
        self.phase = phase
        self.samples: list[dict[str, Any]] = []
        self.windows: list[dict[str, Any]] = []
        self._prev_cpu: tuple[float, float] | None = None
        self._lock = threading.Lock()

    def _sample(self) -> None:
        now = _mono()
        elapsed = now - self.start_at
        proc = _median_rss(self.server.pid)
        cpu_pct = None
        if proc is not None:
            if self._prev_cpu is not None:
                dt = now - self._prev_cpu[0]
                if dt > 0:
                    cpu_pct = round(max(0.0, (proc[1] - self._prev_cpu[1]) / dt * 100.0), 2)
            self._prev_cpu = (now, proc[1])
        health = _health(self.server.base)
        db = _db_snapshot(self.run_dir)
        amb = _ambient_status(self.run_dir)
        machine = _machine_load()
        win = self.recorder.window(label=f"{self.phase}@{round(elapsed)}s")
        epoch_end = time.time()
        row = {
            "ts": _iso(),
            "epoch": round(epoch_end, 3),
            "phase": self.phase,
            "elapsed_s": round(elapsed, 2),
            "rss_mb": round(proc[0], 2) if proc is not None else None,
            "cpu_pct": cpu_pct,
            "cpu_seconds": round(proc[1], 2) if proc is not None else None,
            "machine": machine,
            "ambient_probe": amb,
            "events_total": db.get("events_total"),
            "seq_last": db.get("seq_last"),
            "db_bytes": db.get("db_bytes"),
            "db_bytes_main": db.get("db_bytes_main"),
            "wal_bytes": db.get("wal_bytes"),
            "world_tick": health.get("tick"),
            "ambient": ({k: health.get(k) for k in
                        ("ambient", "ambient_tick", "ambient_reason", "ambient_speed")
                        if k in health} or None),
            "window_s": win["window_s"],
            "requests": win["counts"]["requests"],
            "errors": win["counts"]["errors"],
            "http_5xx": win["counts"]["http_5xx"],
            "http_401": win["counts"]["http_401"],
            "capacity_rejections": win["counts"]["capacity_rejections"],
            "business_rejections": win["counts"]["business_rejections"],
            "read": {k: win["read"][k] for k in ("count", "p50", "p95", "p99", "mean", "max")},
            "write": {k: win["write"][k] for k in ("count", "p50", "p95", "p99", "mean", "max")},
            "by_endpoint": {ep: {"count": v["count"], "p50": v["p50"], "p95": v["p95"], "p99": v["p99"]}
                            for ep, v in win["by_endpoint"].items()},
        }
        with self._lock:
            self.samples.append(row)
            self.windows.append({
                "t_start_s": round(max(0.0, elapsed - win["window_s"]), 2),
                "t_end_s": round(elapsed, 2),
                "epoch_start": round(epoch_end - float(win["window_s"]), 3),
                "epoch_end": round(epoch_end, 3),
                "read": win["_raw_read"],
                "write": win["_raw_write"],
                "requests": win["counts"]["requests"],
                "errors": win["counts"]["errors"],
                "ambient_probe": amb,
                "machine": machine,
            })
        with self.samples_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def run(self) -> None:
        self._prev_cpu = (self.start_at, (lt._sample_process(self.server.pid) or (0.0, 0.0))[1])
        while not self.stop.wait(self.interval):
            try:
                self._sample()
            except Exception as exc:  # 采样失败不应该杀掉负载
                print(f"[soak] sample error: {type(exc).__name__}: {exc}", flush=True)

    def snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self._lock:
            return list(self.samples), list(self.windows)


# --------------------------------------------------------------------------- players
class LegacyPlayer(lt.VirtualPlayer):
    """`--auth off` 下的玩家驱动：复用 load_test 的循环，只跳过注册/登录。

    旧模式（auth 关闭）里服务端以 body.account_id 作为唯一事实源，
    因此 enter 也必须显式带 account_id；读取走 `/api/state` 别名（与
    `/api/player/state` 同一 handler），让报告里的读端点名字与规格一致。
    """

    def _register_login(self) -> bool:
        self.token = ""
        return True

    def _enter(self) -> bool:
        status, payload, _ = lt._request(
            self.base, "/api/player/enter", method="POST",
            body={
                "display_name": self.display,
                "archetype": lt.ARCHETYPES[self.index % 3],
                "account_id": self.account,
            },
            token="", timeout=self.timeout,
            recorder=self.recorder, label="POST /api/player/enter",
        )
        if status == 200 and payload.get("ok"):
            self.stats.entered = True
            return True
        if lt._reason(payload) == "already_online":
            self.stats.entered = True
            return True
        self._rejected_enter = lt._reason(payload)
        return False

    def _state(self) -> dict[str, Any]:
        status, payload, _ = lt._request(
            self.base, f"/api/state?account_id={self.account}",
            timeout=self.timeout, recorder=self.recorder, label="GET /api/state",
        )
        return payload if status == 200 else {}


def _pause_ambient(server: "ManagedServer", timeout: float = 8.0) -> bool:
    """通过控制文件暂停 ambient，并等 health 确认（best-effort，不影响主流程）。"""
    if not server.ambient:
        return False
    ctrl = ROOT / "runs" / server.run_id / "ambient" / "ambient_control_in.json"
    try:
        ctrl.parent.mkdir(parents=True, exist_ok=True)
        ctrl.write_text(json.dumps({"command": "pause"}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    t0 = _mono()
    while _mono() - t0 < timeout:
        status, payload, _ = lt._request(server.base, "/api/health", timeout=3.0)
        if status == 200 and str(payload.get("ambient") or "") == "paused":
            return True
        time.sleep(0.2)
    return False


def _state_fingerprint(base: str, account: str) -> dict[str, Any]:
    """重启前后可比较的状态指纹：钱包/位置/技能/任务/对账（排除 tick/在线时长等易变字段）。"""
    status, payload, _ = lt._request(base, f"/api/state?account_id={account}", timeout=8.0)
    if status != 200 or not payload.get("ok"):
        return {"ok": False, "http": status, "code": lt._reason(payload)}
    player = payload.get("player") or {}
    return {
        "ok": True,
        "account_id": payload.get("account_id"),
        "tick": payload.get("tick"),
        "location": player.get("location"),
        "wallet": player.get("wallet"),
        "skills": player.get("skills"),
        "active_quests": player.get("active_quests"),
        "endowed": player.get("endowed"),
        "endowment_total": payload.get("endowment_total"),
        "reconciliation": payload.get("reconciliation"),
        "escrow_total": payload.get("escrow_total"),
    }


def _fingerprint_core(fp: dict[str, Any]) -> dict[str, Any]:
    """去掉会随 wall-clock/世界时钟推进而变化的字段，用于比较“同一份事实”。

    价格/在线时长/tick 都可能因重启后 ambient 继续推进而变，故密码只取
    钱、物品数量、技能、位置、任务 id、对账等式——这些只能由事件日志重建。
    """
    wallet = fp.get("wallet") or {}
    out: dict[str, Any] = {
        "location": fp.get("location"),
        "money": wallet.get("money"),
        "items": sorted(
            (str(row.get("item")), int(row.get("qty") or 0))
            for row in (wallet.get("items") or [])
        ),
        "skills": fp.get("skills"),
        "endowed": fp.get("endowed"),
        "endowment_total": fp.get("endowment_total"),
        "escrow_total": fp.get("escrow_total"),
    }
    recon = fp.get("reconciliation") or {}
    out["reconciliation"] = {k: recon.get(k) for k in ("accounts_sum", "minted_total", "burned_total", "delta")}
    quests = fp.get("active_quests") or []
    out["active_quest_ids"] = sorted(str(q.get("id") if isinstance(q, dict) else q) for q in quests)
    return out


# --------------------------------------------------------------------------- stats
def _regress(xs: list[float], ys: list[float]) -> dict[str, float]:
    n = len(xs)
    if n < 2:
        return {"slope": 0.0, "intercept": 0.0, "r2": 0.0, "n": n}
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (sxy ** 2) / (sxx * syy) if sxx and syy else 0.0
    return {"slope": slope, "intercept": intercept, "r2": round(r2, 4), "n": n}


def _pooled(windows: list[dict[str, Any]], bucket: str, t_lo: float, t_hi: float) -> dict[str, Any]:
    values: list[float] = []
    for win in windows:
        overlap = min(win["t_end_s"], t_hi) - max(win["t_start_s"], t_lo)
        if overlap <= 0:
            continue
        values.extend(win[bucket])
    return lt.percentiles(values)


def _mb(value: int | float | None) -> float:
    return round(float(value or 0) / (1024.0 * 1024.0), 3)


def _extrapolation(summary: dict[str, Any]) -> dict[str, Any]:
    db_end_mb = _mb(summary["db"]["end_bytes"])
    events_end = float(summary["db"]["end_events"] or 0)
    db_slope = summary["db"]["slope_mb_per_hour"]
    row_slope = summary["db"]["slope_rows_per_hour"]
    rss_slope = max(0.0, summary["resources"]["rss"]["slope_mb_per_hour"])
    rss_end = summary["resources"]["rss"]["end_mb"] or 0.0
    rows = []
    for days, label in ((7, "7d"), (30, "30d"), (90, "90d")):
        hours = days * 24
        db_mb = db_end_mb + db_slope * hours
        rows_total = int(events_end + row_slope * hours)
        rss_mb = rss_end + rss_slope * hours
        rows.append({
            "horizon": label,
            "days": days,
            "events_total": rows_total,
            "db_mb": round(db_mb, 1),
            "db_gb": round(db_mb / 1024.0, 2),
            "rss_mb": round(rss_mb, 1),
            "rss_gb": round(rss_mb / 1024.0, 2),
        })
    return {"basis": {
        "db_mb_per_hour": db_slope,
        "rows_per_hour": row_slope,
        "rss_mb_per_hour": rss_slope,
        "end_db_mb": round(db_end_mb, 1),
        "end_events": int(events_end),
        "end_rss_mb": rss_end,
    }, "horizons": rows}


# --------------------------------------------------------------------------- attribution
def _epoch_of_iso(text: str) -> float | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except Exception:
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _build_attribution(
    *,
    report_dir: Path,
    run_dir: Path,
    windows: list[dict[str, Any]],
    warmup: float,
) -> dict[str, Any]:
    """把写延迟的 30s 窗口与 ambient 每步耗时、GC、checkpoint、同机负载逐窗口对齐。

    判定口径（严格）：任一窗口 write.p95 > 500ms 即未过；这里只为坏窗口给出
    “锁内/锁外”归因，不改变判定。
    """
    steps = _read_jsonl(run_dir / "ambient" / "ambient_steps.jsonl")
    gc_rows = _read_jsonl(report_dir / "_probe" / "gc_pauses.jsonl")
    gc_events: list[tuple[float, int, float]] = []
    for row in gc_rows:
        ts = _epoch_of_iso(str(row.get("ts") or ""))
        if ts is None:
            continue
        try:
            gc_events.append((ts, int(row.get("gen") or -1), float(row.get("ms") or 0.0)))
        except (TypeError, ValueError):
            continue

    def _steps_in(lo: float, hi: float) -> list[dict[str, Any]]:
        return [s for s in steps if lo <= float(s.get("epoch") or 0) <= hi]

    table: list[dict[str, Any]] = []
    prev_ck: dict[str, Any] | None = None
    for win in windows:
        if float(win.get("t_end_s") or 0) < warmup and steps:
            # 预热窗口保留在 samples.jsonl，但归因表只看正式段，避免把启动抖动
            # 混进来；控制组（无 steps）不裁剪。
            continue
        lo, hi = float(win.get("epoch_start") or 0.0), float(win.get("epoch_end") or 0.0)
        read_p = lt.percentiles(win.get("read") or [])
        write_p = lt.percentiles(win.get("write") or [])
        seg = _steps_in(lo, hi)
        lock_waits = [float(s.get("lock_wait_ms") or 0.0) for s in seg]
        in_locks = [float(s.get("in_lock_ms") or 0.0) for s in seg]
        step_vals = [float(s.get("step_ms") or 0.0) for s in seg]
        parts: dict[str, float] = {}
        for s in seg:
            for key, val in (s.get("parts") or {}).items():
                try:
                    parts[key] = max(parts.get(key, 0.0), float(val))
                except (TypeError, ValueError):
                    continue
        gc_seg = [g for g in gc_events if lo <= g[0] <= hi]
        machine = win.get("machine") or {}
        machine_cpu = round(sum(float(p.get("cpu") or 0.0)
                                for p in (machine.get("top_cpu") or [])), 2)
        amb = win.get("ambient_probe") or {}
        ck = amb.get("store_ck") or {}
        ck_delta: dict[str, Any] = {}
        if prev_ck is not None and ck:
            ck_delta = {
                "runs": int(ck.get("runs", 0)) - int(prev_ck.get("runs", 0)),
                "ms": round(float(ck.get("ms_total", 0.0)) - float(prev_ck.get("ms_total", 0.0)), 3),
                "busy_runs": int(ck.get("busy_runs", 0)) - int(prev_ck.get("busy_runs", 0)),
            }
        if ck:
            prev_ck = ck
        row = {
            "t_start_s": win.get("t_start_s"),
            "t_end_s": win.get("t_end_s"),
            "requests": win.get("requests"),
            "errors": win.get("errors"),
            "read_p95": read_p.get("p95"),
            "write_p95": write_p.get("p95"),
            "write_p99": write_p.get("p99"),
            "write_max": write_p.get("max"),
            "ambient_steps": len(seg),
            "step_ms_p95": lt.percentiles(step_vals).get("p95") if step_vals else None,
            "step_ms_max": round(max(step_vals), 2) if step_vals else None,
            "lock_wait_ms_p95": lt.percentiles(lock_waits).get("p95") if lock_waits else None,
            "lock_wait_ms_max": round(max(lock_waits), 2) if lock_waits else None,
            "in_lock_ms_p95": lt.percentiles(in_locks).get("p95") if in_locks else None,
            "in_lock_ms_max": round(max(in_locks), 2) if in_locks else None,
            "in_lock_parts_max_ms": {k: round(v, 2) for k, v in sorted(parts.items())},
            "ambient_status": {
                k: amb.get(k) for k in ("step_ms_p95", "lock_wait_ms_p95", "in_lock_ms_p95",
                                        "lag_ticks", "lag_steps", "queue_backlog")
                if k in amb
            },
            "gc_pauses": {
                "count": len(gc_seg),
                "max_ms": round(max((g[2] for g in gc_seg), default=0.0), 2),
                "by_gen": {str(g): sum(1 for e in gc_seg if e[1] == g) for g in
                           sorted({e[1] for e in gc_seg})},
            },
            "checkpoint_delta": ck_delta,
            "machine": {
                "loadavg": machine.get("loadavg"),
                "top_cpu": machine.get("top_cpu"),
                "cpu_sum_pct": machine_cpu,
            },
        }
        bad = bool(write_p.get("p95") and float(write_p["p95"]) > 500.0)
        row["bad"] = bad
        if bad:
            row["attribution"] = _classify_window(row)
        table.append(row)

    bad_rows = [r for r in table if r.get("bad")]
    counts: dict[str, int] = {}
    for r in bad_rows:
        key = str((r.get("attribution") or {}).get("cause") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return {
        "schema": "soak-attribution/v1",
        "warmup_s": warmup,
        "windows_total": len(table),
        "bad_windows": len(bad_rows),
        "bad_window_range_s": [
            bad_rows[0]["t_start_s"], bad_rows[-1]["t_end_s"]
        ] if bad_rows else None,
        "cause_counts": counts,
        "gc_pauses_total": len(gc_events),
        "gc_pauses_max_ms": round(max((g[2] for g in gc_events), default=0.0), 2),
        "windows": table,
    }


def _classify_window(row: dict[str, Any]) -> dict[str, Any]:
    """给坏窗口落到具体一项：GC / checkpoint / JSONL flush / 锁内自走 / 同机 CPU。"""
    write_p95 = float(row.get("write_p95") or 0.0)
    in_lock = float(row.get("in_lock_ms_max") or 0.0)
    gc_max = float((row.get("gc_pauses") or {}).get("max_ms") or 0.0)
    ck = row.get("checkpoint_delta") or {}
    ck_ms = float(ck.get("ms") or 0.0)
    ck_runs = int(ck.get("runs") or 0)
    parts = row.get("in_lock_parts_max_ms") or {}
    flush = float(parts.get("progress_flush_ms") or 0.0)
    status_file = float(parts.get("status_file_ms") or 0.0)
    state_hash = float(parts.get("progress_hash_ms") or 0.0)
    machine_cpu = float((row.get("machine_cpu_sum") or 0.0))
    candidates = {
        "gc": gc_max,
        "checkpoint": ck_ms,
        "progress_flush": flush,
        "status_file": status_file,
        "state_hash": state_hash,
        "in_lock_total": in_lock,
        "machine_cpu": machine_cpu,
    }
    top = max(candidates.items(), key=lambda kv: kv[1])
    return {
        "cause": top[0] if top[1] > 0 else "unknown",
        "evidence_ms": round(top[1], 2),
        "write_p95_ms": round(write_p95, 2),
        "in_lock_ms_max": round(in_lock, 2),
        "gc_max_ms": round(gc_max, 2),
        "gc_count": (row.get("gc_pauses") or {}).get("count"),
        "checkpoint": ck,
        "flush_ms": round(flush, 2),
        "status_file_ms": round(status_file, 2),
        "state_hash_ms": round(state_hash, 2),
        "machine_cpu_sum_pct": round(machine_cpu, 2),
    }


# --------------------------------------------------------------------------- report
def _build_summary(
    *,
    args: argparse.Namespace,
    run_id: str,
    server: ManagedServer,
    recorder: WindowRecorder,
    sampler: SoakSampler,
    players: list[lt.VirtualPlayer],
    duration_actual: float,
    stop_info: dict[str, Any],
    invariants: dict[str, Any],
    restart: dict[str, Any],
    report_dir: Path,
    run_dir: Path,
    started_at: str,
    memory_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    samples, windows = sampler.snapshot()
    if samples:
        # 以最后一个采样点为准：采样线程停止前还会补采一行，可能略大于循环时长。
        duration_actual = max(duration_actual, float(samples[-1].get("elapsed_s") or 0.0))
    total = recorder.totals()
    span = min(600.0, max(60.0, duration_actual / 2.0))
    first_at, last_at = 0.0, min(span, duration_actual)
    tail_lo = max(0.0, duration_actual - span)
    first = {"read": _pooled(windows, "read", first_at, first_at + span),
             "write": _pooled(windows, "write", first_at, first_at + span)}
    last = {"read": _pooled(windows, "read", tail_lo, duration_actual),
            "write": _pooled(windows, "write", tail_lo, duration_actual)}

    def _ratio(bucket: str) -> float | None:
        a = first[bucket].get("p95") or 0.0
        b = last[bucket].get("p95") or 0.0
        if not a or not b:
            return None
        return round(b / a, 3)

    # 预热口径：必须覆盖 _seen 填满（上限 20000；50 席约 410s），
    # 不再用 duration*25% 静默截短。仅保证至少留 300s 观察窗。
    warmup = min(float(args.warmup), max(30.0, duration_actual - 300.0))
    rss_rows = [s for s in samples if s.get("rss_mb") is not None]
    warm_rows = [s for s in rss_rows if s["elapsed_s"] >= warmup]
    rss_slope_h = 0.0
    rss_mean = 0.0
    rss_r2 = 0.0
    if len(warm_rows) >= 3:
        reg = _regress([s["elapsed_s"] / 3600.0 for s in warm_rows], [s["rss_mb"] for s in warm_rows])
        rss_slope_h = round(reg["slope"], 4)
        rss_mean = round(statistics.fmean([s["rss_mb"] for s in warm_rows]), 2)
        rss_r2 = reg["r2"]
    baseline_rss = rss_rows[0]["rss_mb"] if rss_rows else None
    warmup_start_rss = warm_rows[0]["rss_mb"] if warm_rows else baseline_rss
    end_rss = rss_rows[-1]["rss_mb"] if rss_rows else None
    peak_rss = max((s["rss_mb"] for s in rss_rows), default=None)
    growth_pct_h = round(rss_slope_h / rss_mean * 100.0, 3) if rss_mean else 0.0

    db_rows = [s for s in samples if s.get("db_bytes")]
    db_warm = [s for s in db_rows if s["elapsed_s"] >= warmup] or db_rows
    row_warm = [s for s in db_rows if s["elapsed_s"] >= warmup] or db_rows
    db_reg = _regress([s["elapsed_s"] / 3600.0 for s in db_warm],
                      [_mb(s["db_bytes"]) for s in db_warm]) if len(db_warm) >= 3 else {"slope": 0.0, "r2": 0.0}
    row_reg = _regress([s["elapsed_s"] / 3600.0 for s in row_warm],
                       [float(s["events_total"] or 0) for s in row_warm]) if len(row_warm) >= 3 else {"slope": 0.0, "r2": 0.0}
    db_reg_all = _regress([s["elapsed_s"] / 3600.0 for s in db_rows],
                          [_mb(s["db_bytes"]) for s in db_rows]) if len(db_rows) >= 3 else {"slope": 0.0}

    db_start = db_rows[0] if db_rows else {}
    db_end = db_rows[-1] if db_rows else {}
    peak_slope = ((memory_profile or {}).get("maxrss_peak_mb") or {}).get("slope_mb_per_hour")
    peak_growth_pct = round(peak_slope / rss_mean * 100.0, 3) if (peak_slope is not None and rss_mean) else None
    cpu_vals = [s["cpu_pct"] for s in samples if s.get("cpu_pct") is not None]

    inv_seq = (invariants.get("seq") or {})
    inv_wallet = (invariants.get("wallet") or {})
    tail_requests = sum(w.get("requests", 0) for w in windows if w["t_end_s"] > tail_lo)
    tail_errors = sum(w.get("errors", 0) for w in windows if w["t_end_s"] > tail_lo)

    summary: dict[str, Any] = {
        "schema": "soak-report/v1",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": _iso(),
        "config": {
            "duration_s": args.duration,
            "duration_actual_s": round(duration_actual, 2),
            "players": args.players,
            "think_time_s": args.think_time,
            "sample_interval_s": args.sample_interval,
            "warmup_s": warmup,
            "ambient": True,
            "ambient_cpu_pct": args.ambient_cpu,
            "auth": "off",
            "service_cmd": f"python -m wsim.api --auth off --run {run_id} --port {server.port}",
        },
        "service": {
            "pid": server.pid,
            "port": server.port,
            "run_dir": str(ROOT / "runs" / run_id),
            "report_dir": str(report_dir),
            "spawn_to_health_s": server.spawn_to_health_s,
        },
        "players": {
            "started": len(players),
            "entered": sum(1 for p in players if p.stats.entered),
            "cycles": sum(p.stats.cycles for p in players),
            "player_errors": sum(len(p.stats.errors) for p in players),
            "cycles_per_second": round(sum(p.stats.cycles for p in players) / duration_actual, 3)
            if duration_actual else None,
        },
        "totals": total,
        "resources": {
            "rss": {
                "baseline_mb": baseline_rss,
                "warmup_start_mb": warmup_start_rss,
                "end_mb": end_rss,
                "peak_sampled_mb": peak_rss,
                "warmup_s": warmup,
                "warm_mean_mb": rss_mean,
                "slope_mb_per_hour": rss_slope_h,
                "growth_pct_per_hour": growth_pct_h,
                "peak_slope_mb_per_hour": peak_slope,
                "peak_growth_pct_per_hour": peak_growth_pct,
                "r2": round(rss_r2, 4),
                "samples": len(rss_rows),
            },
            "cpu": {
                "mean_pct": round(statistics.fmean(cpu_vals), 2) if cpu_vals else None,
                "peak_pct": round(max(cpu_vals), 2) if cpu_vals else None,
                "total_seconds": samples[-1].get("cpu_seconds") if samples else None,
            },
        },
        "db": {
            "start_bytes": db_start.get("db_bytes") or 0,
            "end_bytes": db_end.get("db_bytes") or 0,
            "start_events": db_start.get("events_total") or 0,
            "end_events": db_end.get("events_total"),
            "warmup_start_bytes": db_warm[0].get("db_bytes") if db_warm else None,
            "warmup_start_events": db_warm[0].get("events_total") if db_warm else None,
            "slope_mb_per_hour": round(db_reg["slope"], 3),
            "slope_mb_per_hour_incl_warmup": round(db_reg_all["slope"], 3),
            "slope_rows_per_hour": round(row_reg["slope"], 1),
            "r2_db": db_reg.get("r2", 0.0),
            "r2_rows": row_reg.get("r2", 0.0),
            "wal_bytes_at_end": db_end.get("wal_bytes"),
            "events_per_mb": round((db_end.get("events_total") or 0) / _mb(db_end.get("db_bytes")), 1)
            if db_end and _mb(db_end.get("db_bytes")) > 0 else None,
        },
        "latency": {
            "window_span_s": span,
            "window_10m": {"first": first, "last": last,
                           "p95_ratio": {"read": _ratio("read"), "write": _ratio("write")}},
            "overall": {
                "read": _pooled(windows, "read", 0.0, duration_actual),
                "write": _pooled(windows, "write", 0.0, duration_actual),
            },
            "tail_requests": tail_requests,
            "tail_errors": tail_errors,
        },
        "invariants": {
            "verify": invariants,
            "seq_contiguous": bool(inv_seq.get("contiguous")),
            "seq_first": inv_seq.get("first"),
            "seq_last": inv_seq.get("last"),
            "seq_count": inv_seq.get("count"),
            "wallet_delta": inv_wallet.get("delta"),
            "integrity_check": invariants.get("integrity_check"),
        },
        "shutdown": stop_info,
        "restart": restart,
        "memory_profile": memory_profile,
        "checks": {},
    }
    attribution = _build_attribution(report_dir=report_dir, run_dir=run_dir, windows=windows, warmup=warmup)
    summary["attribution"] = attribution
    _json_dump(report_dir / "attribution.json", attribution)
    summary["extrapolation"] = _extrapolation(summary)
    slow_ratio = summary["latency"]["window_10m"]["p95_ratio"]
    checks = {
        "strict_no_30s_window_write_p95_over_500": bool(attribution.get("bad_windows") == 0),
        "rss_growth_lt_5pct_per_hour": bool(
            growth_pct_h < 5.0 and (peak_growth_pct is None or peak_growth_pct < 5.0)
        ),
        "p95_drift_read_lt_1_3": bool(slow_ratio.get("read") and slow_ratio["read"] < 1.3),
        "p95_drift_write_lt_1_3": bool(slow_ratio.get("write") and slow_ratio["write"] < 1.3),
        "zero_errors": bool(total["errors"] == 0),
        "zero_5xx": bool(total["http_5xx"] == 0),
        "seq_contiguous": bool(inv_seq.get("contiguous")),
        "wallet_delta_zero": bool(inv_wallet.get("delta") == 0),
        "integrity_ok": bool(invariants.get("integrity_check") == "ok"),
        "sigterm_graceful": bool(stop_info.get("exit_code") == 0),
        "restart_state_match": bool(restart.get("state_match")),
    }
    summary["checks"] = checks
    summary["passed"] = all(checks.values())
    return summary


# --------------------------------------------------------------------------- soak run
def _run_soak(args: argparse.Namespace) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = args.run or f"soak-{stamp}"
    report_dir = ROOT / "reports" / "soak" / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    run_dir = ROOT / "runs" / run_id
    samples_path = report_dir / "samples.jsonl"
    if samples_path.exists():
        samples_path.unlink()
    samples_path.touch()
    probe_dir = _write_probe_dir(report_dir)
    port = args.port or _free_port()
    started_at = _iso()
    print(f"[soak] run={run_id} duration={args.duration}s players={args.players} port={port}", flush=True)
    print(f"[soak] report={report_dir}", flush=True)

    server = ManagedServer(
        run_id=run_id, port=port, log_path=report_dir / "server.log",
        probe_dir=probe_dir, phase="soak", ambient=not args.no_ambient,
        ambient_cpu=args.ambient_cpu,
        extra_args=["--seed", str(args.seed)] + (["--tick-step", str(args.tick_step)] if args.tick_step else []),
    )
    server.start()

    recorder = WindowRecorder()
    locations = lt.Locations(ROOT)
    stop = threading.Event()
    deadline = time.time() + args.duration
    start_at = _mono()
    sampler = SoakSampler(
        server=server, run_dir=run_dir, recorder=recorder, samples_path=samples_path,
        interval=args.sample_interval, stop=stop, start_at=start_at, phase="soak",
    )
    sampler.start()

    player_cls = LegacyPlayer
    tag = args.tag or f"{random.randint(0, 9999):04d}"
    players: list[lt.VirtualPlayer] = []
    threads: list[threading.Thread] = []
    for idx in range(args.players):
        p = player_cls(
            server.base, idx, tag, recorder, locations,
            think_time=args.think_time, timeout=args.timeout, stop=stop,
            deadline=deadline, account_prefix="soak", display_prefix="长",
        )
        players.append(p)
        th = threading.Thread(target=p.run, name=f"player-{idx:03d}", daemon=True)
        threads.append(th)
        th.start()
        if args.ramp_up > 0 and args.players > 1:
            time.sleep(args.ramp_up / args.players)

    t_start = _mono()
    next_log = t_start + 300.0
    while _mono() < t_start + args.duration:
        time.sleep(1.0)
        if _mono() >= next_log:
            next_log += 300.0
            rows, _ = sampler.snapshot()
            if rows:
                last = rows[-1]
                print(
                    f"[soak] t={last['elapsed_s']:.0f}s rss={last['rss_mb']}MB "
                    f"cpu={last['cpu_pct']}% events={last['events_total']} "
                    f"db={_mb(last['db_bytes'])}MB tick={last['world_tick']} "
                    f"read_p95={last['read']['p95']}ms write_p95={last['write']['p95']}ms "
                    f"err={last['errors']}",
                    flush=True,
                )
        if not all(th.is_alive() for th in threads) and _mono() < t_start + args.duration:
            # 玩家线程全部退出（极端情况下）也不必等满时长
            if not any(th.is_alive() for th in threads):
                break

    duration_actual = _mono() - t_start
    print(f"[soak] load finished after {duration_actual:.1f}s, stopping players", flush=True)
    stop.set()
    for th in threads:
        th.join(timeout=30)
    sampler.stop.set()
    sampler.join(timeout=10)
    time.sleep(1.0)
    # 采样线程停后再补最后一行，保证末窗口完整
    try:
        sampler._sample()  # noqa: SLF001  —— 结束前补采一次，保证末 10 分钟数据齐全
    except Exception:
        pass

    # 先冻结 ambient 再取指纹：否则 ambient 会在 pre_fp 与 SIGTERM 之间继续写事件，
    # 让 restart_state_match 把“正常写入”误判成回放不一致。
    _pause_ambient(server)
    pre_fp = _state_fingerprint(server.base, players[0].account) if players else {"ok": False}
    pre_db = _db_snapshot(run_dir)
    stop_info = server.stop()

    invariants = lt.verify_event_log(run_dir)
    print(f"[soak] invariants: ok={invariants.get('ok')} errors={invariants.get('errors')}", flush=True)

    restart = _restart_check(args, run_id, report_dir, probe_dir, players[0].account if players else "",
                             pre_fp, pre_db)
    memory_profile = _memory_profile(report_dir, args.warmup)
    summary = _build_summary(
        args=args, run_id=run_id, server=server, recorder=recorder, sampler=sampler,
        players=players, duration_actual=duration_actual, stop_info=stop_info,
        invariants=invariants, restart=restart, report_dir=report_dir, run_dir=run_dir,
        started_at=started_at,
        memory_profile=memory_profile,
    )
    _json_dump(report_dir / "summary.json", summary)
    print(f"[soak] summary -> {report_dir / 'summary.json'}", flush=True)
    for key, ok in summary["checks"].items():
        print(f"[soak] check {key}: {'PASS' if ok else 'FAIL'}", flush=True)
    print(f"[soak] overall: {'PASS' if summary['passed'] else 'FAIL'}", flush=True)
    return 0 if (summary["passed"] or not args.strict) else 1


def _restart_check(
    args: argparse.Namespace,
    run_id: str,
    report_dir: Path,
    probe_dir: Path,
    account: str,
    pre_fp: dict[str, Any],
    pre_db: dict[str, Any],
) -> dict[str, Any]:
    """SIGTERM 之后用同一 run 目录重启：测恢复耗时，比对重启前后的事实指纹与 seq。

    重启校验刻意关掉 ambient：ambient 在 1 世界日/秒的速度下，重启后哪怕 1 秒
    也可能让任务过期、NPC 移动，导致"指纹不等"被误判为回放失败。关掉之后世界
    冻结在最后一条事件，指纹差异只可能来自回放本身。
    """
    print("[soak] restart check: re-launching same run dir (ambient off)", flush=True)
    srv = ManagedServer(
        run_id=run_id, port=args.port or _free_port(), log_path=report_dir / "server.log",
        probe_dir=probe_dir, phase="restart", ambient=False, ambient_cpu=args.ambient_cpu,
        extra_args=["--seed", str(args.seed)] + (["--tick-step", str(args.tick_step)] if args.tick_step else []),
    )
    result: dict[str, Any] = {"started": False}
    try:
        srv.start(timeout=180.0)
        result["started"] = True
        recovery_s = srv.spawn_to_health_s
        post_fp = _state_fingerprint(srv.base, account) if account else {"ok": False}
        post_db = _db_snapshot(Path(ROOT / "runs" / run_id))
        core_pre = _fingerprint_core(pre_fp)
        core_post = _fingerprint_core(post_fp)
        seq_ok = (
            post_db.get("seq_first") == pre_db.get("seq_first")
            and int(post_db.get("seq_last") or 0) >= int(pre_db.get("seq_last") or 0)
        )
        result.update({
            "recovery_s": recovery_s,
            "pre_events": pre_db.get("events_total"),
            "post_events": post_db.get("events_total"),
            "pre_seq_last": pre_db.get("seq_last"),
            "post_seq_last": post_db.get("seq_last"),
            "seq_ok": bool(seq_ok),
            "pre_tick": pre_fp.get("tick"),
            "post_tick": post_fp.get("tick"),
            "state_match": bool(core_pre == core_post),
            "state_diff": None if core_pre == core_post else {"pre": core_pre, "post": core_post},
        })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["state_match"] = False
    finally:
        result["restart_exit"] = srv.stop()
    return result


# --------------------------------------------------------------------------- heap profile
def _inproc_cycle(session: Any, account: str, locations: lt.Locations, seed: int) -> None:
    """在进程内复刻 VirtualPlayer._one_cycle 的决策，用于 tracemalloc 诊断。"""
    state = session.player_state(account)
    if not state.get("ok"):
        session.perform(account, "enter", {
            "display_name": f"诊断{account[-3:]}", "archetype": lt.ARCHETYPES[seed % 3],
            "account_id": account,
        })
        return
    player = state.get("player") or {}
    loc = str(player.get("location") or "")
    present = state.get("present_npcs") or []
    board = state.get("quest_board") or {}
    active = board.get("active") or []
    if present:
        session.perform(account, "talk", {"target": str(present[seed % len(present)].get("id") or ""),
                                          "message": "你好，朋友"})
        return
    if active:
        q = active[0]
        if loc and loc == str(q.get("target_location") or q.get("giver_location") or ""):
            session.perform(account, "quest/deliver", {"quest": str(q.get("id") or "")})
        else:
            hop = locations.next_hop(loc, str(q.get("target_location") or "market"))
            if hop and hop != loc:
                session.perform(account, "move", {"to": hop})
        return
    acceptable = [r for r in (board.get("rows") or []) if r.get("acceptable")]
    if acceptable:
        session.perform(account, "quest/accept", {"quest": str(acceptable[0].get("id") or "")})
        return
    session.action(account, "cultivate", {"hours": 2})


def _run_heap(args: argparse.Namespace) -> int:
    import tracemalloc

    sys.path.insert(0, str(ROOT / "src"))
    from wsim.api.session import WorldSession  # noqa: WPS433  —— 仅诊断模式导入

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = args.run or f"soak-heap-{stamp}"
    report_dir = ROOT / "reports" / "soak" / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    run_dir = ROOT / "runs" / run_id
    os.environ["XKX_AMBIENT"] = "1"
    os.environ["XKX_AMBIENT_CPU"] = str(args.ambient_cpu)
    tracemalloc.start(1)
    session = WorldSession(ROOT, run_id=run_id, run_dir=run_dir, seed=args.seed)
    session.start_ambient_from_env()
    locations = lt.Locations(ROOT)
    stop = threading.Event()
    accounts = [f"soakdiag{args.tag or '0000'}{i:03d}" for i in range(args.players)]
    for idx, account in enumerate(accounts):
        session.perform(account, "enter", {
            "display_name": f"诊断{idx:02d}", "archetype": lt.ARCHETYPES[idx % 3], "account_id": account,
        })
    threads: list[threading.Thread] = []

    def _loop(idx: int) -> None:
        while not stop.is_set():
            try:
                _inproc_cycle(session, accounts[idx], locations, idx)
            except Exception:
                pass
            stop.wait(args.think_time)

    for idx in range(len(accounts)):
        th = threading.Thread(target=_loop, args=(idx,), daemon=True)
        th.start()
        threads.append(th)

    baseline = None
    rows: list[dict[str, Any]] = []
    t0 = _mono()
    while True:
        time.sleep(args.heap_interval)
        elapsed = _mono() - t0
        gc.collect()
        snap = tracemalloc.take_snapshot()
        stats = snap.statistics("lineno")
        top = [{"site": str(s.traceback), "size_kb": round(s.size / 1024.0, 1),
                "count": s.count} for s in stats[:15]]
        objs = gc.get_objects()
        counter = Counter(type(o).__name__ for o in objs)
        row = {
            "ts": _iso(), "elapsed_s": round(elapsed, 1),
            "gc_objects": len(objs),
            "tracemalloc_current_mb": round(tracemalloc.get_traced_memory()[0] / 1048576.0, 1),
            "types": counter.most_common(20),
            "structures": {
                "quests": len(session.state.quests),
                "players": len(session.state.players),
                "entities": len(session.state.entities),
                "accounts": len(session.state.accounts),
                "session_requests": len(session.requests),
                "session_intents": len(session.intents),
                "session_view_cache": len(getattr(session, "_view_cache", {})),
                "ambient_summaries": len(getattr(getattr(session, "ambient", None), "_summaries", []) or []),
                "ambient_player_actions": len(getattr(getattr(session, "ambient", None), "_player_actions", []) or []),
            },
            "top_alloc": top,
        }
        rows.append(row)
        with (report_dir / "heap_samples.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[heap] t={elapsed:.0f}s objects={len(objs)} tm={row['tracemalloc_current_mb']}MB "
              f"quests={row['structures']['quests']}", flush=True)
        if elapsed >= args.heap_minutes * 60:
            break

    stop.set()
    for th in threads:
        th.join(timeout=10)
    session.close()
    first, last = rows[0], rows[-1]

    def _delta(key: str) -> dict[str, Any]:
        a, b = first["structures"].get(key), last["structures"].get(key)
        return {"first": a, "last": b,
                "per_hour": round((b - a) / max(last["elapsed_s"], 1.0) * 3600.0, 1)
                if isinstance(a, int) and isinstance(b, int) else None}

    summary = {
        "schema": "soak-heap/v1",
        "run_id": run_id,
        "minutes": args.heap_minutes,
        "players": args.players,
        "gc_objects": {"first": first["gc_objects"], "last": last["gc_objects"]},
        "tracemalloc_mb": {"first": first["tracemalloc_current_mb"], "last": last["tracemalloc_current_mb"]},
        "structures": {k: _delta(k) for k in first["structures"]},
        "types_growth": {
            t: {"first": c1, "last": dict(last["types"]).get(t, 0)}
            for t, c1 in first["types"]
            if dict(last["types"]).get(t, 0) != c1
        },
        "top_alloc_last": last["top_alloc"],
    }
    _json_dump(report_dir / "heap_summary.json", summary)
    print(f"[heap] summary -> {report_dir / 'heap_summary.json'}", flush=True)
    return 0


# --------------------------------------------------------------------------- cli
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="wsim 长跑稳定性测试（soak）")
    parser.add_argument("--mode", choices=("soak", "heap"), default="soak")
    parser.add_argument("--run", default="", help="run id；默认 soak-<UTC 时间戳>")
    parser.add_argument("--duration", type=float, default=7200, help="soak 时长（秒，默认 120 分钟）")
    parser.add_argument("--players", type=int, default=50, help="并发虚拟玩家数")
    parser.add_argument("--think-time", type=float, default=2.0, help="每个动作后的思考时间（秒）")
    parser.add_argument("--ramp-up", type=float, default=10.0, help="玩家进入爬坡时长（秒）")
    parser.add_argument("--timeout", type=float, default=15.0, help="单请求超时（秒）")
    parser.add_argument("--sample-interval", type=float, default=30.0, help="采样间隔（秒）")
    parser.add_argument("--warmup", type=float, default=600.0,
                        help=("RSS 斜率计算跳过预热期（秒，默认 600）。幂等缓存 _seen 上限 "
                              "20000、50 席实测约 410s 填满；默认值覆盖填满窗口，否则会把"
                              "缓存填充误判成泄漏。"))
    parser.add_argument("--port", type=int, default=0, help="服务端口；0=自动挑空闲端口")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tag", default="", help="账号标签（默认随机 4 位）")
    parser.add_argument("--tick-step", type=int, default=0, help="传给服务端的 --tick-step；0=用服务默认")
    parser.add_argument("--ambient-cpu", type=float, default=70.0, help="XKX_AMBIENT_CPU")
    parser.add_argument("--no-ambient", action="store_true",
                        help="关掉 ambient（同机对照组：判断 >500ms 是代码还是机器噪声）")
    parser.add_argument("--strict", action="store_true", help="任一验收项 FAIL 时返回码 1")
    parser.add_argument("--heap-minutes", type=float, default=20.0)
    parser.add_argument("--heap-interval", type=float, default=300.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "heap":
        return _run_heap(args)
    return _run_soak(args)


if __name__ == "__main__":
    raise SystemExit(main())
