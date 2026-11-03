"""内容层：manifest、扩展内容、错误行号、生成器。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from wsim.core.content import load_content
from wsim.core.content_manifest import ContentValidationError, validate_content_dir

MANIFEST_TEXT = """\
manifest_version: "1.0.0"
engine_min_version: "0.1.0"
packages:
  - {id: base-npcs, type: npcs, path: npcs.yaml, version: "1.0.0"}
  - {id: base-locations, type: locations, path: locations.yaml, version: "1.0.0"}
  - {id: base-items, type: items, path: items.yaml, version: "1.0.0"}
  - {id: base-verbs, type: verbs, path: verbs.yaml, version: "1.0.0"}
  - {id: base-economy, type: economy, path: economy.yaml, version: "1.0.0"}
"""


def _minimal_content(tmp_path: Path, npcs: str, locations: str | None = None,
                     items: str | None = None) -> Path:
    d = tmp_path / "content"
    d.mkdir()
    (d / "manifest.yaml").write_text(MANIFEST_TEXT, encoding="utf-8")
    (d / "npcs.yaml").write_text(npcs, encoding="utf-8")
    (d / "locations.yaml").write_text(
        locations
        or "locations:\n  - {id: market, name: 集市, exits: [], services: [bread]}\n",
        encoding="utf-8",
    )
    (d / "items.yaml").write_text(
        items or "items:\n  - {id: bread, name: 饼, category: food, base_price: 5}\n",
        encoding="utf-8",
    )
    (d / "verbs.yaml").write_text("verbs:\n  - {name: move, args: [to], allowed_roles: []}\n", encoding="utf-8")
    (d / "economy.yaml").write_text("wages_daily:\n  worker: 20\n", encoding="utf-8")
    return d


def test_real_content_loads_additions_without_src_changes():
    c = load_content(ROOT, allow_fixture=False, use_content=True)
    assert c.manifest_version == "1.0.0"
    ids = {n["id"] for n in c.npcs}
    assert "ming_chabo" in ids
    assert "teahouse" in c.locations
    assert "tea" in c.items
    assert "茶博士" in c.roles
    assert "tea_guild" in c.sects
    assert "borrow_money" in c.verbs
    assert c.economy["wages_daily"]["茶博士"] == 22


def test_missing_field_reports_file_and_line(tmp_path):
    d = _minimal_content(
        tmp_path,
        "npcs:\n  - id: broken\n    name: 坏数据\n",  # 缺 role/home
    )
    with pytest.raises(ContentValidationError) as exc:
        validate_content_dir(d)
    text = "\n".join(exc.value.messages)
    assert "content/npcs.yaml:2" in text
    assert "缺少字段" in text and "role" in text and "home" in text


def test_unknown_reference_reports_file_and_line(tmp_path):
    d = _minimal_content(
        tmp_path,
        "npcs:\n"
        "  - id: n1\n"
        "    name: 甲\n"
        "    role: worker\n"
        "    home: nowhere\n",
    )
    with pytest.raises(ContentValidationError) as exc:
        validate_content_dir(d)
    text = "\n".join(exc.value.messages)
    assert "content/npcs.yaml:2" in text
    assert "home 引用不存在的地点: nowhere" in text


def test_master_cycle_reports_chain_and_line(tmp_path):
    d = _minimal_content(
        tmp_path,
        "npcs:\n"
        "  - id: n1\n"
        "    name: 甲\n"
        "    role: worker\n"
        "    home: market\n"
        "    relations:\n"
        "      - {target: n2, type: master}\n"
        "  - id: n2\n"
        "    name: 乙\n"
        "    role: worker\n"
        "    home: market\n"
        "    relations:\n"
        "      - {target: n1, type: master}\n",
    )
    with pytest.raises(ContentValidationError) as exc:
        validate_content_dir(d)
    text = "\n".join(exc.value.messages)
    assert "师徒关系成环" in text
    assert "content/npcs.yaml:2" in text


def test_generator_writes_and_validates_then_rolls_back_bad_reference(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(ROOT / "content", root / "content")
    tool = ROOT / "tools" / "new_content.py"
    npc_file = root / "content" / "additions" / "npcs.yaml"
    before = npc_file.read_text(encoding="utf-8")

    bad = subprocess.run(
        [sys.executable, str(tool), "--root", str(root), "npc", "--id", "bad_npc",
         "--name", "坏引用", "--role", "不存在的职业", "--home", "teahouse"],
        capture_output=True, text=True,
    )
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "校验失败，已回滚" in bad.stderr
    assert "content/additions/npcs.yaml" in bad.stderr
    assert npc_file.read_text(encoding="utf-8") == before

    good = subprocess.run(
        [sys.executable, str(tool), "--root", str(root), "npc", "--id", "ok_npc",
         "--name", "新茶客", "--role", "茶博士", "--home", "teahouse"],
        capture_output=True, text=True,
    )
    assert good.returncode == 0, good.stdout + good.stderr
    assert "ok_npc" in npc_file.read_text(encoding="utf-8")
    c = load_content(root, allow_fixture=False, use_content=True)
    assert "ok_npc" in {n["id"] for n in c.npcs}
