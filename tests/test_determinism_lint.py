"""确定性静态检查：引擎不得使用墙钟或全局随机源。"""
from __future__ import annotations

import re
from pathlib import Path

from conftest import ROOT

SRC = ROOT / "src" / "wsim"

BANNED = [
    (r"\bimport\s+random\b", "模块级 random"),
    (r"\bfrom\s+random\s+import\b", "from random"),
    (r"\brandom\.(?:random|randint|choice|choices|shuffle)\b", "random 调用"),
    (r"\btime\.time\s*\(", "time.time"),
    (r"\btime\.monotonic\s*\(", "time.monotonic"),
    (r"\bdatetime\.(?:now|today|utcnow)\s*\(", "datetime.now"),
    (r"\bdatetime\.datetime\.(?:now|today|utcnow)\s*\(", "datetime.datetime.now"),
    (r"\bdate\.today\s*\(", "date.today"),
]


def test_engine_has_no_wallclock_or_global_random():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern, label in BANNED:
            for match in re.finditer(pattern, text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {label}")
    assert offenders == [], "发现非确定性调用:\n" + "\n".join(offenders)
