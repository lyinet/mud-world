"""soak 探针：由 tools/soak_test.py 生成，只做事后诊断，不改任何业务代码。

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


def _loop():
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
            row["structures"] = _snapshot_structures(objs)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            with open(os.path.join(_DIR, "gc_probe.error.log"), "a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc() + "\n")


try:
    threading.Thread(target=_loop, daemon=True, name="soak-gc-probe").start()
except Exception:
    pass
