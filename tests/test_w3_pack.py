"""W3 scaleup_w3 扩展包：关闭态零污染、开启态计数、物化确定性与校验器 PASS。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from wsim.core.content import load_content  # noqa: E402
import content_scale  # noqa: E402

BASE_HASH = "5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4"
PACK = "scaleup_w3"


def test_off_mode_content_hash_and_counts_unchanged():
    content = load_content(ROOT, allow_fixture=False)
    assert content.content_hash == BASE_HASH
    assert len(content.npcs) == 31
    assert len(content.locations) == 9
    assert len(content.items) == 41
    assert len(content.quests) == 27
    assert "scaleup_w3" not in content.sources.get("npcs", "")


def test_pack_materialize_counts_and_hash_changes(tmp_path):
    counts = content_scale.materialize(PACK, tmp_path / "content")
    assert counts == {
        "npcs": 60, "locations": 20, "items": 70, "verbs": 25,
        "roles": 10, "sects": 4, "quests": 60, "routes": 4, "personas": 30,
    }
    enabled = load_content(tmp_path, allow_fixture=False)
    assert enabled.content_hash != BASE_HASH
    assert len(enabled.npcs) == 60
    assert len(enabled.locations) == 20
    assert len(enabled.items) == 70
    assert len(enabled.quests) == 60


def test_pack_materialize_is_byte_identical_twice(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    content_scale.materialize(PACK, a)
    content_scale.materialize(PACK, b)
    assert content_scale.tree_digest(a) == content_scale.tree_digest(b)


def test_validator_pass_off_and_on():
    for args in (
        [sys.executable, str(ROOT / "content" / "validate_content.py"), "--no-pack"],
        [sys.executable, str(ROOT / "content" / "validate_content.py"), "--pack", PACK],
    ):
        result = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
