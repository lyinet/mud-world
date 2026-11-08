"""score 门派显示：bai 写 Entity.faction，score 从同一事实源的嵌套视图读取。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from wsim.actions.common import path_between  # noqa: E402
from wsim.api import WorldSession  # noqa: E402

XKX_CONTENT = ROOT / "content" / "xkx"
XKX_WORLD = ROOT / "config" / "worlds" / "xkx.yaml"
MASTER_ROOM = "/d/hangzhou/liuhe1"


def _session(tmp_path):
    spawn = str((yaml.safe_load(XKX_WORLD.read_text(encoding="utf-8")) or {}).get("spawn"))
    return WorldSession(
        ROOT, run_id="xkx-score-test", seed=42, npc_limit=0,
        run_dir=tmp_path / "run", content_root=XKX_CONTENT,
        world_config=XKX_WORLD, spawn=spawn,
    )


def _walk(session, account: str, goal: str) -> None:
    player = f"player:{account}"
    while session.state.entities[player].location != goal:
        cur = session.state.entities[player].location
        route = path_between(session.state, cur, goal)
        assert route, (cur, goal)
        result = session.perform(account, "move", {"to": route[0]})
        assert result.get("ok") is True, (cur, route[0], result)


def _text(lines) -> str:
    return "\n".join(str(line.get("text") or "") for line in lines)


def test_bai_then_score_shows_same_faction(tmp_path):
    session = _session(tmp_path)
    try:
        account = "xkx_sect"
        before = session.xkx_command(account, {"account_id": account, "command": "score"})
        assert "门派：尚未拜入门派。" in _text(before["lines"])
        _walk(session, account, MASTER_ROOM)
        bai = session.xkx_command(account, {"account_id": account, "command": "bai 安健刚"})
        assert bai["ok"] is True, bai.get("lines")
        after = session.xkx_command(account, {"account_id": account, "command": "score"})
        text = _text(after["lines"])
        assert "门派：尚未拜入门派。" not in text
        assert "门派：红花会（弟子）" in text
        assert "师承：安健刚" in text
    finally:
        session.close()
