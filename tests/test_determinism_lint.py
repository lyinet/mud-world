"""确定性静态检查：引擎不得使用墙钟或全局随机源。

规则：
- `sim/`、`actions/`、`world/` 等引擎目录零容忍，任何豁免都不生效；
- `admin/` 的审计/遥测时间戳允许墙钟，但必须收敛到唯一包装函数
  `admin/clock.py::now_utc()`，或在该函数/该行显式标注 `# wallclock: audit-only`；
- 白名单精确到函数，不做文件级或目录级放行。
"""
from __future__ import annotations

import ast
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

ANNOTATION = "# wallclock: audit-only"
# 精确到函数的白名单：仅 admin/clock.py 的 now_utc 允许直接取墙钟。
ALLOWED_FUNCS = {("clock.py", "now_utc")}


def _is_admin(path: Path, root: Path) -> bool:
    return path.parent == root / "admin"


def _exempt_ranges(path: Path, text: str, lines: list[str], root: Path) -> list[tuple[int, int]]:
    """admin 内可豁免的行区间；引擎目录永远返回空。"""
    if not _is_admin(path, root):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (path.name, node.name) in ALLOWED_FUNCS:
            ranges.append((node.lineno, node.end_lineno))
            continue
        # 标注写在 def 上一行（允许中间有空行）时，豁免整个函数体。
        prev = node.lineno - 1
        while prev >= 1 and not lines[prev - 1].strip():
            prev -= 1
        if prev >= 1 and ANNOTATION in lines[prev - 1]:
            ranges.append((node.lineno, node.end_lineno))
    return ranges


def _find_offenders(root: Path, *, display_root: Path | None = None) -> list[str]:
    offenders = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        ranges = _exempt_ranges(path, text, lines, root)
        for pattern, label in BANNED:
            for match in re.finditer(pattern, text):
                line = text[: match.start()].count("\n") + 1
                if any(start <= line <= end for start, end in ranges):
                    continue
                # 行内标注仅在 admin 生效，且只豁免该行本身。
                if _is_admin(path, root) and ANNOTATION in lines[line - 1]:
                    continue
                shown = path.relative_to(display_root) if display_root else path
                offenders.append(f"{shown}:{line}: {label}")
    return offenders


def test_engine_has_no_wallclock_or_global_random():
    offenders = _find_offenders(SRC, display_root=ROOT)
    assert offenders == [], "发现非确定性调用:\n" + "\n".join(offenders)


def test_admin_clock_wrapper_is_whitelisted(tmp_path):
    target = tmp_path / "admin" / "clock.py"
    target.parent.mkdir()
    target.write_text(
        "import time\n\n\ndef now_utc():\n    return time.time()\n",
        encoding="utf-8",
    )
    assert _find_offenders(tmp_path) == []


def test_admin_annotation_exempts_audit_field(tmp_path):
    target = tmp_path / "admin" / "audit.py"
    target.parent.mkdir()
    target.write_text(
        "import time\n\n\n# wallclock: audit-only\ndef stamp():\n    return time.time()\n",
        encoding="utf-8",
    )
    assert _find_offenders(tmp_path) == []


def test_admin_without_annotation_still_fails(tmp_path):
    target = tmp_path / "admin" / "misc.py"
    target.parent.mkdir()
    target.write_text("import time\n\n\ndef stamp():\n    return time.time()\n", encoding="utf-8")
    offenders = _find_offenders(tmp_path)
    assert len(offenders) == 1 and offenders[0].endswith("admin/misc.py:5: time.time")


def test_annotation_never_relaxes_engine_domains(tmp_path):
    target = tmp_path / "sim" / "engine.py"
    target.parent.mkdir()
    target.write_text(
        "import time\n\n\n# wallclock: audit-only\ndef tick():\n    return time.time()\n",
        encoding="utf-8",
    )
    offenders = _find_offenders(tmp_path)
    assert len(offenders) == 1 and "engine.py:6: time.time" in offenders[0]
