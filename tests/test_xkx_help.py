"""校验 xkx 帮助内容包与命令覆盖矩阵。

这些测试不依赖 LPC 源码：源码口径已冻结在 tools/xkx_help/inventory.txt 与
tools/xkx_help/topics_index.txt。若本机存在源码 clone，会额外做一次源口径比对。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "tools" / "xkx_help" / "build.py"
OUT = ROOT / "content" / "xkx_help"
SPEC = ROOT / "specs" / "xkx-parity" / "help-coverage.md"
SOURCE = Path("/tmp/xkx-research/fluffos-xkx100")

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LPC_RE = re.compile(r"\$[A-Za-z]|@HELP|write\s*\(")
SYSTEMS = {"移动", "战斗", "物品", "社交", "门派", "经济", "系统"}
STATUSES = {"已实现", "待实现", "不做"}


def load_commands() -> list[dict]:
    payload = yaml.safe_load((OUT / "commands.yaml").read_text(encoding="utf-8"))
    return payload["commands"]


def load_topics() -> list[dict]:
    payload = yaml.safe_load((OUT / "topics.yaml").read_text(encoding="utf-8"))
    return payload["topics"]


def run_build(out_dir: Path, spec_path: Path, *extra: str) -> None:
    result = subprocess.run(
        [sys.executable, str(BUILD), "--out", str(out_dir), "--spec", str(spec_path), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_command_inventory_is_153():
    commands = load_commands()
    assert len(commands) == 153
    names = [c["name"] for c in commands]
    assert len(set(names)) == 153

    frozen = [
        line.split("\t", 1)[1]
        for line in (ROOT / "tools" / "xkx_help" / "inventory.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert names == frozen


def test_command_schema_and_chinese_aliases():
    commands = load_commands()
    for command in commands:
        assert command["system"] in SYSTEMS, command["name"]
        assert command["status"] in STATUSES, command["name"]
        assert command["aliases"], command["name"]
        for alias in command["aliases"]:
            assert CJK_RE.search(alias), (command["name"], alias)
        assert CJK_RE.search(command["summary"]), command["name"]
        assert command["params"].strip(), command["name"]
        assert len(command["evidence"].strip()) >= 10, command["name"]
        if command["status"] in {"待实现", "不做"}:
            assert command.get("blocker"), command["name"]
        if command["status"] == "已实现":
            assert any(token in command["evidence"] for token in ("src/", "GET /api/", "content/")), command["name"]


def test_topics_are_32_and_match_frozen_index():
    topics = load_topics()
    assert len(topics) == 32
    ids = [t["id"] for t in topics]
    frozen = [
        line.split("\t", 1)[0]
        for line in (ROOT / "tools" / "xkx_help" / "topics_index.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert ids == frozen
    for topic in topics:
        assert topic["title"] and CJK_RE.search(topic["title"])
        assert topic["category"]
        assert len(topic["body"].strip()) >= 40, topic["id"]
        assert CJK_RE.search(topic["body"]), topic["id"]


def test_no_machine_translation_artifacts():
    texts: list[tuple[str, str]] = []
    for command in load_commands():
        for field in ("summary", "params", "evidence"):
            texts.append((f"{command['name']}.{field}", str(command[field])))
    for topic in load_topics():
        texts.append((f"topic.{topic['id']}", topic["body"]))
    for label, text in texts:
        assert not ANSI_RE.search(text), label
        assert not LPC_RE.search(text), label
        for bad in ("�", "TODO", "TBD", "translated by", "机器翻译"):
            assert bad not in text, (label, bad)


def test_coverage_matrix_lists_every_command():
    text = SPEC.read_text(encoding="utf-8")
    commands = load_commands()
    assert "| # | 命令 |" in text
    rows = re.findall(r"^\| (\d+) \| `([^`]+)` \|", text, re.MULTILINE)
    assert len(rows) == 153
    row_names = [name for _, name in rows]
    assert row_names == [c["name"] for c in commands]
    for status in STATUSES:
        assert f"**{status}**" in text
    assert "引擎现在根本做不到的命令" in text


def test_output_is_byte_identical_across_two_runs():
    with tempfile.TemporaryDirectory(prefix="xkx-help-a-") as a, tempfile.TemporaryDirectory(prefix="xkx-help-b-") as b:
        out_a, out_b = Path(a) / "content", Path(b) / "content"
        spec_a, spec_b = Path(a) / "help-coverage.md", Path(b) / "help-coverage.md"
        run_build(out_a, spec_a)
        run_build(out_b, spec_b)
        for name in ("commands.yaml", "topics.yaml", "manifest.json"):
            assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), name
        assert spec_a.read_bytes() == spec_b.read_bytes()
        manifest = json.loads((out_a / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["counts"]["commands"] == 153
        assert manifest["counts"]["topics"] == 32
        assert manifest["counts"]["status"] == {"已实现": 41, "待实现": 51, "不做": 61}


def test_committed_outputs_are_up_to_date():
    result = subprocess.run(
        [sys.executable, str(BUILD), "--verify"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not SOURCE.is_dir(), reason="本机没有 xkx100 源码 clone")
def test_source_inventory_matches_frozen():
    with tempfile.TemporaryDirectory(prefix="xkx-help-src-") as tmp:
        out_dir = Path(tmp) / "content"
        spec = Path(tmp) / "help-coverage.md"
        run_build(out_dir, spec, "--src", str(SOURCE))
        assert (out_dir / "commands.yaml").read_bytes() == (OUT / "commands.yaml").read_bytes()
        assert (out_dir / "topics.yaml").read_bytes() == (OUT / "topics.yaml").read_bytes()
        assert spec.read_bytes() == SPEC.read_bytes()
