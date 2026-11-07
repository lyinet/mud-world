"""xkx 确定性审计的 CI 门禁与自检。

默认跑 quick 模式（静态扫描 + 会话隔离 + 战斗就绪判定），确保：
- 当前仓库红线没有 FAIL（未就绪的项不算越界）；
- 审计扫描器真的能抓到越界代码（防止“永远绿”的假审计）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import ROOT

TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from xkx_audit.audit import GOLDEN_FILE, _parse_baselines, scan_hidden_random  # noqa: E402


def test_quick_audit_gate_matches_findings(tmp_path):
    """审计必须一条命令跑完；有 FAIL 时退出码必须为 1（CI 能红），无 FAIL 时为 0。"""
    out = tmp_path / "audit.json"
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "xkx_audit" / "__main__.py"),
            "--quick",
            "--out",
            str(out),
            "--runs-dir",
            str(runs),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    fails = [c for c in payload["checks"] if c["status"] == "FAIL"]
    expected_exit = 1 if fails else 0
    assert proc.returncode == expected_exit, proc.stdout + "\n" + proc.stderr
    assert payload["summary"]["fail"] == len(fails)
    assert payload["summary"]["exit_code"] == expected_exit
    ids = [c["id"] for c in payload["checks"]]
    assert ids == [
        "same_seed_twice",
        "switch_independence",
        "replay_equivalence",
        "no_hidden_random",
        "ledger_no_model_text",
        "golden_baseline_untouched",
        "combat_redline",
    ]
    assert payload["summary"]["skip"] == 2, payload["summary"]
    assert (out.parent / "AUDIT.md").is_file()
    # 会话隔离项必须在 quick 模式下真跑，不能靠跳过混过去。
    switch = next(c for c in payload["checks"] if c["id"] == "switch_independence")
    ledger = next(c for c in payload["checks"] if c["id"] == "ledger_no_model_text")
    assert switch["status"] in {"PASS", "FAIL"}
    assert ledger["status"] in {"PASS", "FAIL", "NOT_READY"}


def test_scanner_catches_hidden_random(tmp_path):
    target = tmp_path / "src" / "wsim" / "world"
    target.mkdir(parents=True)
    (target / "bad.py").write_text(
        "import random\n\n\ndef roll():\n    return random.random()\n",
        encoding="utf-8",
    )
    scan = scan_hidden_random(tmp_path / "src")
    assert scan["critical"], scan
    assert any(row["line"] == 5 and row["pattern"] == "random" for row in scan["critical"])


def test_scanner_flags_wallclock_in_effects_path(tmp_path):
    target = tmp_path / "src" / "wsim" / "actions"
    target.mkdir(parents=True)
    (target / "bad.py").write_text(
        "import time\n\n\ndef stamp():\n    return time.time()\n",
        encoding="utf-8",
    )
    scan = scan_hidden_random(tmp_path / "src")
    assert any(row["pattern"] == "time.time" for row in scan["critical"]), scan


def test_golden_baseline_parser_reads_current_values():
    baselines = _parse_baselines(GOLDEN_FILE.read_text(encoding="utf-8"))
    assert "GOLDEN_MILESTONE_V6" in baselines
    assert baselines["GOLDEN_MILESTONE_V6"]["events_total"] == 791
    assert "DIALOGUE_GOLDEN_MILESTONE_V6" in baselines
