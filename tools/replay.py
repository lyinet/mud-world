#!/usr/bin/env python3
"""从事件日志重建世界并校验哈希。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.core.content import load_content  # noqa: E402
from wsim.core.models import Effect  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True, help="runs/<run_id> 目录")
    args = p.parse_args()
    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir

    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    state = WorldState(locations=content.locations)
    store = EventStore(run_dir / "events.sqlite")
    stored_content = store.get_meta("content_hash")
    events = store.read_all()
    for e in events:
        if e.outcome == "ok":
            apply_effects(state, [Effect.from_dict(d) for d in e.effects])
        state.tick = e.tick
    store.close()

    if stored_content and stored_content != content.content_hash:
        print(f"WARN content_hash changed: run={stored_content[:12]} replay={content.content_hash[:12]}")
    actual = state.hash()
    stored = (run_dir / "world_hash.txt").read_text(encoding="utf-8").strip()
    print(f"events={len(events)}")
    print(f"replayed={actual}")
    print(f"stored  ={stored}")
    print("MATCH" if actual == stored else "MISMATCH")
    return 0 if actual == stored else 1


if __name__ == "__main__":
    raise SystemExit(main())
