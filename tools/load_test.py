#!/usr/bin/env python3
"""W5 压测与不变量校验工具（客户端侧，只依赖标准库 + PyYAML）。

三种模式：

1) 负载模式（默认）
   python tools/load_test.py --players 50 --duration 600 \
       --base-url http://127.0.0.1:8020 --run-dir runs/service \
       --server-pid <pid> --report reports/w5/load-50x10m.json
   N 个虚拟玩家并发：注册 → 登录 → 进世界 → 移动 → 接单 → 交付 → 对话，
   记录分端点 P50/P95/P99、HTTP 错误率（429 容量拒绝单列）、业务拒绝码分布、
   服务端进程 CPU/RSS 采样，并在结束后校验 events.sqlite 不变量。

2) 席位探测
   python tools/load_test.py --capacity-check --players 200 --probe-count 5 \
       --base-url http://127.0.0.1:8020 --run-dir runs/cap
   填满 200 席后，第 201 席必须 HTTP 429 + player_cap_reached（不是超时/500）。

3) 只校验
   python tools/load_test.py --verify-only --run-dir runs/service
   输出事件 seq 连续性、钱包守恒、无半写、sqlite integrity_check 的 JSON。

错误率口径（写进报告，避免拿业务拒绝美化数字）：
- error：连接失败/超时/HTTP 5xx/除 429 外的 4xx（401/403 也算错）。
- capacity_rejection：429 且 code/reject_reason ∈ {player_cap_reached, endowment_cap_reached}。
- business_rejection：HTTP 200 且 ok=false（任务过期、体力不足等业务拒绝）。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # content/locations.yaml 的图；缺依赖时退化为单跳猜测
    import yaml  # type: ignore
except Exception:  # pragma: no cover - requirements.lock 已固定 PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
CAPACITY_CODES = frozenset({"player_cap_reached", "endowment_cap_reached"})
STATE_ENDPOINT = "GET /api/player/state"


# --------------------------------------------------------------------------- http
def _request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str = "",
    timeout: float = 10.0,
    recorder: "Recorder | None" = None,
    label: str = "",
) -> tuple[int, dict[str, Any], float]:
    """发一次 JSON 请求，返回 (status, payload, elapsed_ms)。连接失败 status=0。"""
    url = base.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    started = time.perf_counter()
    status = 0
    payload: dict[str, Any] = {}
    error = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
    except Exception as exc:  # URLError / timeout / reset / json
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if recorder is not None:
        recorder.record(label or f"{method} {path}", status, payload, elapsed_ms, error)
    return status, payload, elapsed_ms


def _reason(payload: dict[str, Any]) -> str:
    return str(payload.get("code") or payload.get("reject_reason") or "")


# ---------------------------------------------------------------------- recorder
def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "max": 0.0}
    ordered = sorted(values)
    n = len(ordered)

    def _pct(p: float) -> float:
        idx = max(0, min(n - 1, int(math.ceil(p / 100.0 * n)) - 1))
        return round(ordered[idx], 3)

    return {
        "count": n,
        "p50": _pct(50),
        "p95": _pct(95),
        "p99": _pct(99),
        "mean": round(statistics.fmean(ordered), 3),
        "max": round(ordered[-1], 3),
    }


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.by_endpoint: dict[str, list[float]] = {}
        self.requests = 0
        self.ok = 0
        self.errors = 0
        self.capacity_rejections = 0
        self.business_rejections = 0
        self.reject_codes: Counter[str] = Counter()
        self.http_5xx = 0
        self.http_401 = 0
        self.error_samples: list[str] = []
        self.by_status: Counter[str] = Counter()
        self.by_endpoint_count: Counter[str] = Counter()

    def record(self, endpoint: str, status: int, payload: dict[str, Any], elapsed_ms: float, error: str) -> None:
        code = _reason(payload)
        with self.lock:
            self.requests += 1
            self.by_endpoint.setdefault(endpoint, []).append(elapsed_ms)
            self.by_endpoint_count[endpoint] += 1
            self.by_status["timeout" if error else str(status)] += 1
            if error:
                self.errors += 1
                if len(self.error_samples) < 30:
                    self.error_samples.append(f"{endpoint}: {error}")
                return
            if status >= 500:
                self.errors += 1
                self.http_5xx += 1
                if len(self.error_samples) < 30:
                    self.error_samples.append(f"{endpoint}: HTTP {status} {code}")
                return
            if status == 429 and code in CAPACITY_CODES:
                self.capacity_rejections += 1
                return
            if status >= 400:
                if (endpoint == "POST /api/auth/register" and status == 409
                        and code == "auth_username_taken"):
                    # 复用已有账号跑基线时，注册冲突是预期结果，不算错误。
                    self.ok += 1
                    return
                self.errors += 1
                if status == 401:
                    self.http_401 += 1
                if len(self.error_samples) < 30:
                    self.error_samples.append(f"{endpoint}: HTTP {status} {code}")
                return
            if status == 200:
                if payload.get("ok") is False:
                    self.business_rejections += 1
                    self.reject_codes[code or "unknown_business_reject"] += 1
                else:
                    self.ok += 1
                return
            # 其余状态码按错误处理
            self.errors += 1
            if len(self.error_samples) < 30:
                self.error_samples.append(f"{endpoint}: HTTP {status} {code}")

    def summary(self) -> dict[str, Any]:
        with self.lock:
            all_lat = [v for rows in self.by_endpoint.values() for v in rows]
            denom = self.requests or 1
            return {
                "requests": self.requests,
                "ok": self.ok,
                "errors": self.errors,
                "http_5xx": self.http_5xx,
                "http_401": self.http_401,
                "capacity_rejections": self.capacity_rejections,
                "business_rejections": self.business_rejections,
                "error_rate_pct": round(self.errors / denom * 100.0, 4),
                "latency_ms": {
                    "all": percentiles(all_lat),
                    "by_endpoint": {k: percentiles(v) for k, v in sorted(self.by_endpoint.items())},
                },
                "status_codes": dict(sorted(self.by_status.items())),
                "request_counts_by_endpoint": dict(sorted(self.by_endpoint_count.items())),
                "rejections_by_code": dict(sorted(self.reject_codes.items())),
                "error_samples": list(self.error_samples),
            }


# ------------------------------------------------------------------- content graph
class Locations:
    def __init__(self, root: Path) -> None:
        self.exits: dict[str, list[str]] = {}
        self.names: dict[str, str] = {}
        path = root / "content" / "locations.yaml"
        if yaml is not None and path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                for row in data.get("locations", []):
                    lid = str(row.get("id") or "")
                    if lid:
                        self.exits[lid] = [str(x) for x in (row.get("exits") or [])]
                        self.names[lid] = str(row.get("name") or lid)
            except Exception:
                pass

    def next_hop(self, src: str, dst: str) -> str:
        """返回 src→dst 的最短路下一步；无图/无路时返回第一个出口。"""
        if not self.exits or src == dst:
            return dst
        prev: dict[str, str] = {src: ""}
        queue = [src]
        while queue:
            cur = queue.pop(0)
            if cur == dst:
                break
            for nxt in self.exits.get(cur, []):
                if nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        if dst not in prev:
            outs = self.exits.get(src, [])
            return outs[0] if outs else dst
        node = dst
        while prev.get(node) not in ("", src):
            node = prev[node]
        return node

    def random_exit(self, src: str) -> str:
        outs = self.exits.get(src) or []
        return random.choice(outs) if outs else src


# --------------------------------------------------------------------- virtual player
@dataclass
class PlayerStats:
    account: str
    entered: bool = False
    cycles: int = 0
    errors: list[str] = field(default_factory=list)


ARCHETYPES = ("游侠", "商人", "捕快")


class VirtualPlayer:
    def __init__(
        self,
        base: str,
        index: int,
        tag: str,
        recorder: Recorder,
        locations: Locations,
        *,
        think_time: float,
        timeout: float,
        stop: threading.Event,
        deadline: float,
        account_prefix: str = "w5",
        display_prefix: str = "测",
    ) -> None:
        self.base = base
        self.index = index
        self.tag = tag
        self.account = f"{account_prefix}{tag}{index:03d}"
        self.display = f"{display_prefix}{tag}{index:02d}"[:12]
        self.recorder = recorder
        self.locations = locations
        self.think_time = think_time
        self.timeout = timeout
        self.stop = stop
        self.deadline = deadline
        self.token = ""
        self.stats = PlayerStats(account=self.account)
        self._rejected_enter = ""

    # -------------------------------------------------------------- auth/enter
    def _register_login(self) -> bool:
        status, payload, _ = _request(
            self.base, "/api/auth/register", method="POST",
            body={"username": self.account, "password": "w5-secret123"},
            timeout=self.timeout, recorder=self.recorder, label="POST /api/auth/register",
        )
        if status not in (200, 409):
            self.stats.errors.append(f"register HTTP {status} {_reason(payload)}")
            return False
        status, payload, _ = _request(
            self.base, "/api/auth/login", method="POST",
            body={"username": self.account, "password": "w5-secret123"},
            timeout=self.timeout, recorder=self.recorder, label="POST /api/auth/login",
        )
        if status != 200 or not payload.get("ok"):
            self.stats.errors.append(f"login HTTP {status} {_reason(payload)}")
            return False
        self.token = str(payload.get("token") or "")
        return bool(self.token)

    def _enter(self) -> bool:
        status, payload, _ = _request(
            self.base, "/api/player/enter", method="POST",
            body={"display_name": self.display, "archetype": ARCHETYPES[self.index % 3]},
            token=self.token, timeout=self.timeout,
            recorder=self.recorder, label="POST /api/player/enter",
        )
        if status == 200 and payload.get("ok"):
            self.stats.entered = True
            return True
        code = _reason(payload)
        if code == "already_online":
            # 崩溃恢复后重跑同一 run：在线状态来自事件回放，不需要重复进入。
            self.stats.entered = True
            return True
        self._rejected_enter = code
        return False

    def _state(self) -> dict[str, Any]:
        status, payload, _ = _request(
            self.base, f"/api/player/state?account_id={self.account}",
            timeout=self.timeout, recorder=self.recorder, label=STATE_ENDPOINT,
        )
        if status != 200:
            return {}
        return payload

    # ---------------------------------------------------------------- actions
    def _write(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        body = dict(body)
        body.setdefault("account_id", self.account)
        status, payload, _ = _request(
            self.base, path, method="POST", body=body, token=self.token,
            timeout=self.timeout, recorder=self.recorder, label=f"POST {path}",
        )
        if status == 401:
            # 令牌被轮换/过期：客户端重新登录（压测里 401 仍会记为错误，便于暴露鉴权风暴）。
            self._register_login()
        elif status == 200 and not payload.get("ok"):
            code = _reason(payload)
            if code in ("player_offline", "player_not_entered"):
                # 世界时钟按全局动作推进，多玩家并发时玩家会被日界判为离线；
                # 客户端按 W1 契约重新进入（不重复发放初始资产）。
                self._enter()
        return payload

    def _talk(self, present: list[dict[str, Any]]) -> bool:
        if not present:
            return False
        npc = random.choice(present)
        return bool(self._write("/api/player/talk", {
            "target": str(npc.get("id") or ""),
            "message": f"你好，{npc.get('name') or '朋友'}",
        }).get("ok"))

    def _one_cycle(self) -> bool:
        state = self._state()
        if not state.get("ok"):
            code = _reason(state)
            if code in ("player_not_entered", "player_offline"):
                self._enter()
            return bool(state.get("ok"))
        player = state.get("player") or {}
        loc = str(player.get("location") or "")
        present = state.get("present_npcs") or []
        board = state.get("quest_board") or {}
        active = board.get("active") or []

        # 对话循环：每轮约 1/3 概率先跟同地点 NPC 说一句话。
        if present and random.random() < 0.33:
            self._talk(present)
            return True

        if active:
            quest = active[0]
            qid = str(quest.get("id") or "")
            target = str(quest.get("target_location") or "")
            giver_loc = str(quest.get("giver_location") or "")
            if loc and (loc == target or loc == giver_loc):
                self._write("/api/player/quest/deliver", {"quest": qid})
            else:
                self._move(loc, target or giver_loc or "market")
            return True

        acceptable = [r for r in (board.get("rows") or []) if r.get("acceptable")]
        if acceptable:
            quest = random.choice(acceptable)
            self._write("/api/player/quest/accept", {"quest": str(quest.get("id") or "")})
            return True

        # 没有可接单：去集市找发布人；已在集市就修炼/闲聊，保持写流量。
        if loc and loc != "market":
            self._move(loc, "market")
            return True
        if present:
            self._talk(present)
            return True
        status, payload, _ = _request(
            self.base, "/api/player/action", method="POST",
            body={"verb": "cultivate", "args": {"hours": 2}, "account_id": self.account},
            token=self.token, timeout=self.timeout,
            recorder=self.recorder, label="POST /api/player/action",
        )
        return status == 200 and bool(payload.get("ok"))

    def _move(self, src: str, dst: str) -> None:
        hop = self.locations.next_hop(src, dst) if src else self.locations.random_exit("market")
        if not hop or hop == src:
            hop = self.locations.random_exit(src)
        if hop and hop != src:
            self._write("/api/player/move", {"to": hop})

    # -------------------------------------------------------------------- run
    def run(self) -> PlayerStats:
        if not self._register_login():
            return self.stats
        if not self._enter():
            return self.stats
        while not self.stop.is_set() and time.time() < self.deadline:
            try:
                self._one_cycle()
                self.stats.cycles += 1
            except Exception as exc:  # 单个玩家异常不拖垮压测
                self.stats.errors.append(f"cycle: {type(exc).__name__}: {exc}")
            if self.stop.is_set():
                break
            delay = self.think_time * random.uniform(0.75, 1.25) if self.think_time > 0 else 0
            if delay > 0:
                self.stop.wait(delay)
        return self.stats


# --------------------------------------------------------------- resource sampler
_CPU_TIME_RE = re.compile(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")


def _parse_cpu_time(raw: str) -> float | None:
    s = raw.strip()
    m = _CPU_TIME_RE.match(s)
    if not m:
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3))
    seconds = float(m.group(4))
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _sample_process(pid: int) -> tuple[float, float] | None:
    """返回 (rss_mb, cpu_seconds_total)；进程不存在时返回 None。"""
    if pid <= 0:
        return None
    try:
        out = subprocess.check_output(
            ["ps", "-o", "rss=,time=", "-p", str(pid)], text=True, timeout=3
        ).strip()
    except Exception:
        return None
    if not out:
        return None
    parts = out.split()
    if len(parts) < 2:
        return None
    try:
        rss_mb = int(parts[0]) / 1024.0
    except ValueError:
        return None
    cpu = _parse_cpu_time(parts[-1])
    if cpu is None:
        return None
    return rss_mb, cpu


class ResourceSampler(threading.Thread):
    def __init__(
        self,
        pid: int,
        stop: threading.Event,
        interval: float,
        run_dir: Path | None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(name="w5-resource-sampler", daemon=True)
        self.pid = pid
        self.stop = stop
        self.interval = max(0.5, interval)
        self.run_dir = run_dir
        self.base_url = base_url.rstrip("/") if base_url else None
        self.samples: list[dict[str, Any]] = []
        self.event_counts: list[dict[str, Any]] = []
        self.health_samples: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def _event_count(self) -> int | None:
        if self.run_dir is None:
            return None
        db = self.run_dir / "events.sqlite"
        if not db.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
            try:
                return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            finally:
                conn.close()
        except Exception:
            return None

    def _db_size_mb(self) -> float | None:
        if self.run_dir is None:
            return None
        db = self.run_dir / "events.sqlite"
        try:
            total = db.stat().st_size
            wal = db.with_suffix(".sqlite-wal")
            if wal.exists():
                total += wal.stat().st_size
        except OSError:
            return None
        return round(total / (1024 * 1024), 3)

    def _health(self) -> dict[str, Any] | None:
        if not self.base_url:
            return None
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/health", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        storage = data.get("storage") or {}
        return {
            "seq_gap": int(storage.get("seq_gap") or 0),
            "events_ok": storage.get("events_ok"),
            "tick": int(data.get("tick") or 0),
        }

    def run(self) -> None:
        t0 = time.monotonic()
        prev = _sample_process(self.pid)
        prev_at = time.monotonic()
        with self._lock:
            if prev is not None:
                self.samples.append({"t": 0.0, "rss_mb": round(prev[0], 2), "cpu_pct": 0.0,
                                     "cpu_s": round(prev[1], 1), "alive": True})
        while not self.stop.wait(self.interval):
            now = _sample_process(self.pid)
            now_at = time.monotonic()
            if now is None:
                with self._lock:
                    self.samples.append({"t": round(now_at - t0, 2), "rss_mb": None,
                                         "cpu_pct": None, "cpu_s": None, "alive": False})
                prev = None
                prev_at = now_at
                continue
            cpu_pct = 0.0
            if prev is not None and now_at > prev_at:
                cpu_pct = max(0.0, (now[1] - prev[1]) / (now_at - prev_at) * 100.0)
            health = self._health()
            db_size = self._db_size_mb()
            with self._lock:
                self.samples.append({
                    "t": round(now_at - t0, 2),
                    "rss_mb": round(now[0], 2),
                    "cpu_pct": round(cpu_pct, 2),
                    "cpu_s": round(now[1], 1),
                    "alive": True,
                    "db_size_mb": db_size,
                    "seq_gap": health.get("seq_gap") if health else None,
                })
                if health is not None:
                    self.health_samples.append({"t": round(now_at - t0, 2), **health})
            count = self._event_count()
            if count is not None:
                with self._lock:
                    self.event_counts.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                              "count": count})
            prev = now
            prev_at = now_at

    def summary(self) -> dict[str, Any]:
        with self._lock:
            rows = [r for r in self.samples if r.get("rss_mb") is not None]
            cpu_rows = [r["cpu_pct"] for r in rows if r.get("cpu_pct") is not None]
            rss = [r["rss_mb"] for r in rows]
            baseline = rss[0] if rss else None
            first_cpu = rows[0]["cpu_s"] if rows else 0.0
            last_cpu = rows[-1]["cpu_s"] if rows else 0.0
            cpu_seconds = None
            if rows and first_cpu is not None and last_cpu is not None:
                cpu_seconds = round(max(0.0, last_cpu - first_cpu), 1)
            counts = [r["count"] for r in self.event_counts]
            monotonic = all(b >= a for a, b in zip(counts, counts[1:]))
            gaps = [r["seq_gap"] for r in rows if r.get("seq_gap") is not None]
            db_sizes = [r["db_size_mb"] for r in rows if r.get("db_size_mb") is not None]
            duration = (rows[-1]["t"] - rows[0]["t"]) if len(rows) >= 2 else 0.0
            growth_mb_per_s = None
            if len(db_sizes) >= 2 and duration > 0:
                growth_mb_per_s = round((db_sizes[-1] - db_sizes[0]) / duration, 6)
            return {
                "samples": rows,
                "rss_baseline_mb": baseline,
                "rss_peak_mb": max(rss) if rss else None,
                "rss_end_mb": rss[-1] if rss else None,
                "rss_growth_mb": round((rss[-1] - baseline), 2) if rss else None,
                "cpu_peak_pct": max(cpu_rows) if cpu_rows else None,
                "cpu_mean_pct": round(statistics.fmean(cpu_rows), 2) if cpu_rows else None,
                "cpu_seconds_total": cpu_seconds,
                "event_count_samples": list(self.event_counts),
                "event_count_monotonic": monotonic,
                "seq_gap_max": max(gaps) if gaps else None,
                "seq_gap_end": gaps[-1] if gaps else None,
                "db_size_mb_start": db_sizes[0] if db_sizes else None,
                "db_size_mb_end": db_sizes[-1] if db_sizes else None,
                "db_growth_mb_per_s": growth_mb_per_s,
                "health_samples": list(self.health_samples),
            }


# -------------------------------------------------------------- invariant checks
def verify_event_log(run_dir: str | Path) -> dict[str, Any]:
    """只读校验 events.sqlite：seq 连续唯一、钱包守恒、无半写、integrity_check。"""
    run_dir = Path(run_dir)
    db = run_dir / "events.sqlite"
    out: dict[str, Any] = {
        "run_dir": str(run_dir),
        "db_exists": db.exists(),
        "ok": False,
        "errors": [],
    }
    if not db.exists():
        out["errors"].append("events.sqlite 不存在")
        return out

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        out["integrity_check"] = integrity
        if integrity != "ok":
            out["errors"].append(f"integrity_check={integrity}")

        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(events)")}
        base_cols = "seq,tick,day,actor,verb,args,effects,outcome,reason,source,intent_id"
        rows = conn.execute(f"SELECT {base_cols} FROM events ORDER BY seq ASC").fetchall()
        count = len(rows)
        out["event_count"] = count
        if count == 0:
            out["errors"].append("事件日志为空（没有创世事件）")
            return out

        seqs = [int(r["seq"]) for r in rows]
        contiguous = seqs == list(range(1, count + 1))
        out["seq"] = {
            "first": seqs[0],
            "last": seqs[-1],
            "count": count,
            "distinct": len(set(seqs)),
            "contiguous": contiguous,
        }
        if not contiguous:
            missing = sorted(set(range(1, max(seqs) + 1)) - set(seqs))[:20]
            out["seq"]["missing_sample"] = missing
            out["errors"].append(f"seq 不连续/有重复：count={count} max={max(seqs)} missing={missing[:5]}")

        accounts: dict[str, int] = {}
        minted = 0
        burned = 0
        invalid_rows: list[str] = []
        negative_rows: list[str] = []
        transfers_before_after = 0
        outcomes: Counter[str] = Counter()

        for r in rows:
            seq = int(r["seq"])
            outcomes[str(r["outcome"])] += 1
            if str(r["outcome"]) not in ("ok", "rejected"):
                invalid_rows.append(f"seq={seq} outcome={r['outcome']!r}")
                continue
            try:
                args = json.loads(r["args"])
                effects = json.loads(r["effects"])
            except Exception as exc:
                invalid_rows.append(f"seq={seq} JSON 损坏: {type(exc).__name__}")
                continue
            if not isinstance(args, dict) or not isinstance(effects, list):
                invalid_rows.append(f"seq={seq} args/effects 类型错误")
                continue
            if str(r["outcome"]) != "ok":
                continue
            for eff in effects:
                if not isinstance(eff, dict):
                    invalid_rows.append(f"seq={seq} effect 非对象")
                    continue
                op = str(eff.get("op") or "")
                data = eff.get("data") or {}
                try:
                    if op == "create_account":
                        acc = str(data.get("account"))
                        accounts.setdefault(acc, int(data.get("balance") or 0))
                    elif op == "mint":
                        acc = str(data.get("account"))
                        amount = int(data.get("amount") or 0)
                        if amount <= 0:
                            invalid_rows.append(f"seq={seq} mint 非正数")
                            continue
                        accounts[acc] = accounts.get(acc, 0) + amount
                        minted += amount
                    elif op == "burn":
                        acc = str(data.get("account"))
                        amount = int(data.get("amount") or 0)
                        accounts[acc] = accounts.get(acc, 0) - amount
                        burned += amount
                        if accounts[acc] < 0:
                            negative_rows.append(f"seq={seq} burn 后 {acc}={accounts[acc]}")
                    elif op == "transfer":
                        src = str(data.get("src"))
                        dst = str(data.get("dst"))
                        amount = int(data.get("amount") or 0)
                        if amount <= 0:
                            invalid_rows.append(f"seq={seq} transfer 非正数")
                            continue
                        if src == dst:
                            invalid_rows.append(f"seq={seq} transfer src==dst")
                            continue
                        src_bal = accounts.get(src, 0)
                        if src_bal < amount:
                            negative_rows.append(f"seq={seq} transfer 透支 {src} {src_bal}<{amount}")
                        accounts[src] = src_bal - amount
                        accounts[dst] = accounts.get(dst, 0) + amount
                        transfers_before_after += 1
                except (TypeError, ValueError) as exc:
                    invalid_rows.append(f"seq={seq} {op} 数据不合法: {exc}")

        total_balance = sum(accounts.values())
        expected = minted - burned
        wallet_delta = total_balance - expected
        out["wallet"] = {
            "accounts": len(accounts),
            "sum_balance": total_balance,
            "minted_total": minted,
            "burned_total": burned,
            "expected_sum": expected,
            "delta": wallet_delta,
            "transfers": transfers_before_after,
        }
        if wallet_delta != 0:
            out["errors"].append(f"钱包不守恒：sum={total_balance} expected={expected} delta={wallet_delta}")
        if negative_rows:
            out["errors"].append(f"出现负余额/透支：{negative_rows[:5]}")
        out["negative_rows"] = negative_rows[:20]
        out["half_write_rows"] = invalid_rows[:20]
        if invalid_rows:
            out["errors"].append(f"半写/损坏行：{len(invalid_rows)} 条")

        genesis = conn.execute(
            "SELECT COUNT(*) FROM events WHERE verb IN ('genesis','genesis_player')"
        ).fetchone()[0]
        genesis_ok = conn.execute(
            "SELECT COUNT(*) FROM events WHERE verb='genesis' AND outcome='ok'"
        ).fetchone()[0]
        out["genesis_events"] = int(genesis)
        out["genesis_ok_events"] = int(genesis_ok)
        out["outcomes"] = dict(sorted(outcomes.items()))
        out["ok"] = not out["errors"]
        return out
    finally:
        conn.close()


def _count_events(run_dir: Path) -> int | None:
    db = run_dir / "events.sqlite"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return None


def bench_read_all(run_dir: str | Path, repeats: int = 5) -> dict[str, Any]:
    """复现 `session._submit` 的 O(n) 成本：每次写都全量读事件日志。

    直接复用 EventStore.read_all()，与线上代码路径一致；只读打开，不写事件。
    """
    run_dir = Path(run_dir)
    db = run_dir / "events.sqlite"
    if not db.exists():
        return {"ok": False, "error": "events.sqlite 不存在", "run_dir": str(run_dir)}
    sys.path.insert(0, str(ROOT / "src"))
    from wsim.world.store import EventStore  # noqa: PLC0415

    store = EventStore(db, check_same_thread=False)
    try:
        samples = []
        n = 0
        for _ in range(max(1, repeats)):
            started = time.perf_counter()
            events = store.read_all()
            elapsed = (time.perf_counter() - started) * 1000.0
            n = len(events)
            samples.append(round(elapsed, 2))
        db_bytes = db.stat().st_size
        return {
            "ok": True,
            "run_dir": str(run_dir),
            "event_count": n,
            "db_size_mb": round(db_bytes / (1024 * 1024), 2),
            "samples_ms": samples,
            "median_ms": round(statistics.median(samples), 2),
            "min_ms": min(samples),
            "max_ms": max(samples),
            "ms_per_event": round(statistics.median(samples) / max(1, n), 6),
            "implication": "线上每次玩家写都调用一次 read_all；单写者下写吞吐上限≈1/median_ms",
        }
    finally:
        store.close()


# ------------------------------------------------------------------ load runner
def run_load(args: argparse.Namespace) -> dict[str, Any]:
    base = args.base_url.rstrip("/")
    tag = args.tag or f"{random.randint(0, 0xFFFFFF):06x}"
    recorder = Recorder()
    locations = Locations(Path(args.root).resolve())
    stop = threading.Event()
    started_wall = time.time()
    deadline = started_wall + max(1.0, args.duration)
    run_dir = Path(args.run_dir).resolve() if args.run_dir else None

    count_before = _count_events(run_dir) if run_dir else None
    sampler = ResourceSampler(args.server_pid, stop, args.sample_interval, run_dir, base_url=base)
    if args.server_pid > 0 or run_dir is not None:
        sampler.start()

    players = [
        VirtualPlayer(
            base, i, tag, recorder, locations,
            think_time=args.think_time, timeout=args.timeout,
            stop=stop, deadline=deadline,
            account_prefix=args.account_prefix, display_prefix=args.display_prefix,
        )
        for i in range(1, args.players + 1)
    ]
    player_stats: list[PlayerStats] = []
    stats_lock = threading.Lock()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _run_one(player: VirtualPlayer, delay: float) -> None:
        if delay > 0:
            stop.wait(delay)
        if stop.is_set():
            return
        st = player.run()
        with stats_lock:
            player_stats.append(st)

    threads = [
        threading.Thread(target=_run_one, args=(p, args.ramp_up * (i / max(1, len(players)))),
                         name=f"w5-player-{i}", daemon=True)
        for i, p in enumerate(players)
    ]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop.set()
        for t in threads:
            t.join(timeout=5)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)
        if sampler.is_alive():
            sampler.join(timeout=5)

    actual = round(time.time() - started_wall, 2)
    count_after = _count_events(run_dir) if run_dir else None
    invariants = verify_event_log(run_dir) if run_dir else None
    res = sampler.summary()
    rec = recorder.summary()
    state_p95 = rec["latency_ms"]["by_endpoint"].get(STATE_ENDPOINT, {}).get("p95")
    total = rec["requests"] or 1
    entered = sum(1 for s in player_stats if s.entered)
    report = {
        "schema": "w5-load-report/v1",
        "started_at": started_at,
        "duration_s": args.duration,
        "actual_duration_s": actual,
        "config": {
            "base_url": base,
            "players": args.players,
            "think_time_s": args.think_time,
            "ramp_up_s": args.ramp_up,
            "tag": tag,
            "run_dir": str(run_dir) if run_dir else None,
            "server_pid": args.server_pid,
        },
        "players": {
            "started": len(player_stats),
            "entered": entered,
            "failed_enter": len(player_stats) - entered,
            "cycles": sum(s.cycles for s in player_stats),
            "player_errors": [e for s in player_stats for e in s.errors][:20],
        },
        "totals": rec,
        "resources": res,
        "event_growth": {
            "count_before": count_before,
            "count_after": count_after,
            "delta": (count_after - count_before) if count_before is not None and count_after is not None else None,
            "monotonic": bool(res.get("event_count_monotonic", True)),
        },
        "invariants": invariants,
        "pass": {
            "state_p95_under_300ms": bool(state_p95 is not None and state_p95 < 300.0),
            "error_rate_under_0_5pct": bool(rec["error_rate_pct"] < 0.5),
            "zero_http_500": bool(rec["http_5xx"] == 0),
            "invariants_pass": bool(invariants is None or invariants.get("ok")),
            "event_count_monotonic": bool(res.get("event_count_monotonic", True)),
        },
    }
    report["pass"]["all"] = all(bool(v) for v in report["pass"].values())
    # 吞吐：读 / 写分开算。写动作 = 除 state 与 auth/enter 之外的 POST。
    counts = rec.get("request_counts_by_endpoint", {})
    write_ops = sum(n for ep, n in counts.items()
                    if ep.startswith("POST ") and not ep.startswith("POST /api/auth")
                    and ep not in ("POST /api/player/enter",))
    report["throughput"] = {
        "requests_per_second": round(total / max(1.0, actual), 2),
        "write_ops_per_second": round(write_ops / max(1.0, actual), 2),
        "reads_per_second": round((counts.get(STATE_ENDPOINT, 0)) / max(1.0, actual), 2),
        "events_per_second": round((report["event_growth"]["delta"] or 0) / max(1.0, actual), 2),
        "counts_by_endpoint": counts,
    }
    report["per_seat"] = {
        "requests_per_seat": round(total / max(1, args.players), 2),
        "requests_per_seat_second": round(total / max(1, args.players) / max(1.0, actual), 4),
        "cpu_seconds_total": res.get("cpu_seconds_total"),
        "cpu_seconds_per_request": round(res["cpu_seconds_total"] / total, 6)
        if res.get("cpu_seconds_total") is not None and total else None,
        "rss_growth_mb_per_seat": round((res.get("rss_growth_mb") or 0.0) / max(1, args.players), 3)
        if res.get("rss_growth_mb") is not None else None,
    }
    return report


# ------------------------------------------------------ write throughput probe
def run_write_throughput(args: argparse.Namespace) -> dict[str, Any]:
    """单线程连续写 N 次：测当前实现的写吞吐上界与每次 write 的读事件表成本。

    建议对一台 `--tick-step 0` 的独立服务跑（避免日界/离线判定干扰），
    这样数字只反映写路径本身：read_all + apply + append。
    """
    base = args.base_url.rstrip("/")
    tag = args.tag or f"{random.randint(0, 0xFFFFFF):06x}"
    recorder = Recorder()
    account = f"w5wt{tag}"
    display = f"吞吐{tag}"[:12]
    run_dir = Path(args.run_dir).resolve() if args.run_dir else None

    status, payload, _ = _request(base, "/api/auth/register", method="POST",
                                  body={"username": account, "password": "w5-secret123"},
                                  timeout=args.timeout, recorder=recorder,
                                  label="POST /api/auth/register")
    if status not in (200, 409):
        return {"ok": False, "error": f"register HTTP {status} {_reason(payload)}"}
    status, payload, _ = _request(base, "/api/auth/login", method="POST",
                                  body={"username": account, "password": "w5-secret123"},
                                  timeout=args.timeout, recorder=recorder,
                                  label="POST /api/auth/login")
    if status != 200 or not payload.get("ok"):
        return {"ok": False, "error": f"login HTTP {status} {_reason(payload)}"}
    token = str(payload.get("token") or "")
    status, payload, _ = _request(base, "/api/player/enter", method="POST",
                                  body={"display_name": display, "archetype": "游侠"},
                                  token=token, timeout=args.timeout, recorder=recorder,
                                  label="POST /api/player/enter")
    if status != 200 or not payload.get("ok"):
        return {"ok": False, "error": f"enter HTTP {status} {_reason(payload)}"}

    status, state, _ = _request(base, f"/api/player/state?account_id={account}",
                                timeout=args.timeout, recorder=recorder,
                                label=STATE_ENDPOINT)
    present = (state.get("present_npcs") or []) if status == 200 else []
    target = str(present[0].get("id") or "") if present else ""
    if not target:
        return {"ok": False, "error": "当前地点没有可对话 NPC，无法构造稳定写请求"}

    before_count = _count_events(run_dir) if run_dir else None
    bench_before = bench_read_all(run_dir, 3) if run_dir else None

    latencies: list[float] = []
    successes = 0
    rejects: Counter[str] = Counter()
    started = time.perf_counter()
    for _ in range(max(1, int(args.writes))):
        status, payload, elapsed = _request(
            base, "/api/player/talk", method="POST",
            body={"account_id": account, "target": target, "message": "吞吐测试"},
            token=token, timeout=args.timeout, recorder=recorder,
            label="POST /api/player/talk",
        )
        latencies.append(elapsed)
        if status == 200 and payload.get("ok"):
            successes += 1
        else:
            code = _reason(payload)
            rejects[code or f"http_{status}"] += 1
            if code in ("player_offline", "player_not_entered"):
                _request(base, "/api/player/enter", method="POST",
                         body={"display_name": display, "archetype": "游侠"},
                         token=token, timeout=args.timeout, recorder=recorder,
                         label="POST /api/player/enter")
    elapsed = time.perf_counter() - started
    after_count = _count_events(run_dir) if run_dir else None
    bench_after = bench_read_all(run_dir, 3) if run_dir else None
    events_delta = (after_count - before_count) if before_count is not None and after_count is not None else None

    result = {
        "ok": successes > 0,
        "config": {"base_url": base, "writes": args.writes, "account": account,
                   "tag": tag, "run_dir": str(run_dir) if run_dir else None},
        "attempted": args.writes,
        "successful": successes,
        "rejected": sum(rejects.values()),
        "rejects_by_code": dict(sorted(rejects.items())),
        "elapsed_s": round(elapsed, 3),
        "writes_per_second": round(successes / elapsed, 3) if elapsed > 0 else 0.0,
        "attempts_per_second": round(args.writes / elapsed, 3) if elapsed > 0 else 0.0,
        "latency_ms": percentiles(latencies),
        "events_delta": events_delta,
        "events_per_second": round(events_delta / elapsed, 3)
        if events_delta is not None and elapsed > 0 else None,
        "read_all_before": bench_before,
        "read_all_after": bench_after,
        "implication": "writes_per_second 是当前单写者实现的写吞吐上界；read_all_after.median_ms 是修复后应消除的固定成本",
    }
    return result


# --------------------------------------------------------------- capacity probe
def run_capacity_check(args: argparse.Namespace) -> dict[str, Any]:
    base = args.base_url.rstrip("/")
    tag = args.tag or f"{random.randint(0, 0xFFFFFF):06x}"
    recorder = Recorder()
    seats = int(args.players)
    probes = max(1, int(args.probe_count))
    total_accounts = seats + probes
    tokens: dict[int, str] = {}
    results: list[dict[str, Any]] = []

    def _prepare(i: int) -> None:
        account = f"w5cap{tag}{i:03d}"
        _request(base, "/api/auth/register", method="POST",
                 body={"username": account, "password": "w5-secret123"},
                 timeout=args.timeout, recorder=recorder, label="POST /api/auth/register")
        status, payload, _ = _request(base, "/api/auth/login", method="POST",
                                      body={"username": account, "password": "w5-secret123"},
                                      timeout=args.timeout, recorder=recorder,
                                      label="POST /api/auth/login")
        if status == 200 and payload.get("ok"):
            tokens[i] = str(payload.get("token") or "")
        results.append({"i": i, "stage": "auth", "status": status, "code": _reason(payload),
                        "account": account})

    for i in range(1, total_accounts + 1):
        _prepare(i)

    sem = threading.Semaphore(max(1, int(args.concurrency or 4)))
    enter_rows: dict[int, dict[str, Any]] = {}

    def _enter(i: int) -> None:
        account = f"w5cap{tag}{i:03d}"
        with sem:
            status, payload, _ = _request(
                base, "/api/player/enter", method="POST",
                body={"display_name": f"席{tag}{i:03d}"[:12],
                      "archetype": ARCHETYPES[i % 3]},
                token=tokens.get(i, ""), timeout=args.timeout, recorder=recorder,
                label="POST /api/player/enter",
            )
        enter_rows[i] = {"i": i, "account": account, "status": status,
                         "ok": bool(payload.get("ok")), "code": _reason(payload)}

    threads = [threading.Thread(target=_enter, args=(i,), daemon=True)
               for i in range(1, seats + 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    filled = sum(1 for row in enter_rows.values() if row["ok"])
    enter_failures = [
        row for row in enter_rows.values()
        if not row["ok"] and not (row["status"] == 429 and row["code"] == "player_cap_reached")
    ]
    probe_rows: list[dict[str, Any]] = []
    for i in range(seats + 1, total_accounts + 1):
        status, payload, _ = _request(
            base, "/api/player/enter", method="POST",
            body={"display_name": f"席{tag}{i:03d}"[:12],
                  "archetype": ARCHETYPES[i % 3],
                  "account_id": f"w5cap{tag}{i:03d}"},
            token=tokens.get(i, ""), timeout=args.timeout, recorder=recorder,
            label="POST /api/player/enter",
        )
        code = _reason(payload)
        state_status, state_payload, _ = _request(
            base, f"/api/player/state?account_id=w5cap{tag}{i:03d}",
            timeout=args.timeout, recorder=recorder, label=STATE_ENDPOINT,
        )
        probe_rows.append({
            "i": i, "status": status, "code": code,
            "is_429_capacity": bool(status == 429 and code == "player_cap_reached"),
            "state_status": state_status,
            "state_reject": _reason(state_payload),
            "player_registered": bool(state_payload.get("ok")),
        })

    preexisting = max(0, int(getattr(args, "preexisting_seats", 0)))
    effective = filled + preexisting
    ok = (
        effective >= seats
        and not enter_failures
        and all(row["is_429_capacity"] for row in probe_rows)
        and all(not row["player_registered"] and row["state_reject"] == "player_not_entered"
                for row in probe_rows)
    )
    report = {
        "schema": "w5-capacity-report/v1",
        "config": {"base_url": base, "seats": seats, "probes": probes, "tag": tag,
                   "preexisting_seats": preexisting},
        "auth": {"accounts": total_accounts, "tokens": len(tokens)},
        "fill": {
            "attempted": seats,
            "filled": filled,
            "failed": seats - filled,
            "preexisting": preexisting,
            "effective_seats": effective,
            "failures": enter_failures[:20],
        },
        "probes": probe_rows,
        "totals": recorder.summary(),
        "ok": ok,
        "checks": {
            "effective_seats_reached": effective >= seats,
            "no_unexpected_enter_failure": not enter_failures,
            "201st_is_429_capacity": bool(probe_rows and probe_rows[0]["is_429_capacity"]),
            "all_probes_429_capacity": all(row["is_429_capacity"] for row in probe_rows),
            "no_probe_player_created": all(not row["player_registered"] for row in probe_rows),
            "no_http_500": recorder.http_5xx == 0,
        },
    }
    return report


# --------------------------------------------------------------------- CLI/output
def _write_report(report: dict[str, Any], path: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _print_load_summary(report: dict[str, Any]) -> None:
    rec = report["totals"]
    lat = rec["latency_ms"]
    print("=== W5 负载结果 ===")
    print(f"玩家: {report['players']['entered']}/{report['players']['started']} 已进入, "
          f"周期 {report['players']['cycles']}, 实际时长 {report['actual_duration_s']}s")
    print(f"请求: {rec['requests']}  错误: {rec['errors']} ({rec['error_rate_pct']}%)  "
          f"5xx: {rec['http_5xx']}  429容量: {rec['capacity_rejections']}  "
          f"业务拒绝: {rec['business_rejections']}")
    print(f"全端点延迟 ms: {lat['all']}")
    state = lat["by_endpoint"].get(STATE_ENDPOINT)
    if state:
        print(f"/api/state P95: {state['p95']}ms  (门槛 <300ms: "
              f"{'PASS' if state['p95'] < 300 else 'FAIL'})")
    res = report["resources"]
    print(f"RSS: baseline={res.get('rss_baseline_mb')}MB peak={res.get('rss_peak_mb')}MB "
          f"end={res.get('rss_end_mb')}MB; CPU peak={res.get('cpu_peak_pct')}% "
          f"mean={res.get('cpu_mean_pct')}% total={res.get('cpu_seconds_total')}s")
    inv = report.get("invariants")
    if inv:
        print(f"不变量: {'PASS' if inv.get('ok') else 'FAIL'}  events={inv.get('event_count')} "
              f"钱包delta={inv['wallet']['delta']} seq连续={inv['seq']['contiguous']} "
              f"integrity={inv.get('integrity_check')}")
    print(f"验收: {json.dumps(report['pass'], ensure_ascii=False)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W5 并发压测 / 席位探测 / 不变量校验")
    parser.add_argument("--base-url", default="http://127.0.0.1:8020")
    parser.add_argument("--players", type=int, default=50, help="虚拟玩家数；容量模式为席位数")
    parser.add_argument("--duration", type=float, default=600, help="压测时长（秒）")
    parser.add_argument("--concurrency", type=int, default=0,
                        help="容量模式的进入并发；0=players。负载模式始终 players 线程")
    parser.add_argument("--think-time", type=float, default=2.0, help="每个动作后的思考时间（秒）")
    parser.add_argument("--ramp-up", type=float, default=10.0, help="玩家分批进入的爬坡时长（秒）")
    parser.add_argument("--timeout", type=float, default=10.0, help="单请求超时（秒）")
    parser.add_argument("--root", default=str(ROOT), help="仓库根目录（读取 content/locations.yaml）")
    parser.add_argument("--run-dir", default="", help="服务端 run 目录；给出即做不变量校验")
    parser.add_argument("--server-pid", type=int, default=0, help="服务端进程 PID；给出即采样 CPU/RSS")
    parser.add_argument("--sample-interval", type=float, default=5.0)
    parser.add_argument("--tag", default="", help="账号/角色名后缀，默认随机 6 位")
    parser.add_argument("--account-prefix", default="w5",
                        help="账号前缀；复用容量探测的账号时设为 w5cap")
    parser.add_argument("--display-prefix", default="测", help="角色名前缀")
    parser.add_argument("--report", default="", help="把 JSON 报告写入该路径")
    parser.add_argument("--capacity-check", action="store_true", help="席位探测模式")
    parser.add_argument("--probe-count", type=int, default=5, help="容量模式额外探测席位数")
    parser.add_argument("--preexisting-seats", type=int, default=0,
                        help="容量模式下世界创世已占用的席位（默认 0；服务默认会预置 u_demo=1）")
    parser.add_argument("--verify-only", action="store_true", help="只校验 --run-dir 的事件日志")
    parser.add_argument("--bench-read-all", action="store_true",
                        help="复现服务端每次写 read_all 的 O(n) 成本，输出 ms")
    parser.add_argument("--bench-repeats", type=int, default=5)
    parser.add_argument("--write-throughput", action="store_true",
                        help="单线程连续写 N 次，测写吞吐上界（建议对 --tick-step 0 服务）")
    parser.add_argument("--writes", type=int, default=200, help="写吞吐模式的写次数")
    parser.add_argument("--pretty", action="store_true", help="stdout 也输出完整 JSON")
    args = parser.parse_args(argv)

    if args.bench_read_all:
        if not args.run_dir:
            print("--bench-read-all 需要 --run-dir", file=sys.stderr)
            return 2
        report = bench_read_all(args.run_dir, args.bench_repeats)
        _write_report(report, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    if args.write_throughput:
        report = run_write_throughput(args)
        _write_report(report, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    if args.verify_only:
        if not args.run_dir:
            print("--verify-only 需要 --run-dir", file=sys.stderr)
            return 2
        report = verify_event_log(args.run_dir)
        _write_report(report, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    if args.capacity_check:
        report = run_capacity_check(args)
        _write_report(report, args.report)
        if args.pretty:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("=== W5 席位探测 ===")
            print(f"填席: {report['fill']['filled']}/{report['fill']['attempted']}")
            for row in report["probes"]:
                print(f"第 {row['i']} 席: HTTP {row['status']} {row['code']} "
                      f"(state={row['state_reject']})")
            print(f"检查: {json.dumps(report['checks'], ensure_ascii=False)}")
            print(f"结论: {'PASS' if report['ok'] else 'FAIL'}")
        if args.run_dir:
            inv = verify_event_log(args.run_dir)
            _write_report({**report, "invariants": inv}, args.report)
            if not inv.get("ok"):
                print(f"不变量 FAIL: {inv.get('errors')}", file=sys.stderr)
                return 1
        return 0 if report["ok"] else 1

    report = run_load(args)
    _write_report(report, args.report)
    if args.pretty:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_load_summary(report)
    return 0 if report["pass"]["all"] else 1


if __name__ == "__main__":
    sys.exit(main())
