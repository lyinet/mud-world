"""启动回放与内容哈希拒绝契约（W1）。

事件日志是唯一事实源：进程重启后不重新创世、不重复发放初始资产，
而是把 `events.sqlite` 里的 ok 事件按 seq 顺序重放到全新 WorldState。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..core.models import Effect
from ..world.effects import apply_effects
from ..world.state import WorldState
from ..world.store import EventStore

CONTENT_HASH_MISMATCH = "content_hash_mismatch"
EXIT_CONTENT_HASH_MISMATCH = 3

# 回放时不需要恢复这些来源的 player tick 单调约束（系统事件不参与）。
_SYSTEM_SOURCES = frozenset({"npc", "planner", "system"})


class ContentHashMismatch(RuntimeError):
    """存档内容哈希与当前 content/ 不一致：拒绝启动，绝不带着脏档继续跑。"""

    code = CONTENT_HASH_MISMATCH
    exit_code = EXIT_CONTENT_HASH_MISMATCH

    def __init__(self, stored: str | None, current: str, run_dir: str | Path):
        self.stored = stored
        self.current = current
        self.run_dir = str(run_dir)
        super().__init__(
            f"content_hash_mismatch: stored={stored!r} current={current!r} run_dir={self.run_dir}"
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": False,
            "error": self.code,
            "stored_content_hash": self.stored,
            "current_content_hash": self.current,
            "run_dir": self.run_dir,
        }

    def log_line(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True)


def verify_content_hash(store: EventStore, current: str, run_dir: str | Path) -> None:
    """已有事件日志必须能证明内容包没变；缺失 meta 也按不匹配处理。"""
    stored = store.get_meta("content_hash")
    if stored != current:
        raise ContentHashMismatch(stored, current, run_dir)


def replay_into(
    store: EventStore,
    locations: dict,
    *,
    run_dir: str | Path | None = None,
    seed: int | None = None,
    content_hash: str | None = None,
) -> tuple[WorldState, dict[str, int]]:
    """把事件日志重放到新状态，并重建 Writer 的 player tick 兜底表。

    默认（关闭保留机制、未裁剪）走原全量回放路径，行为逐字节不变。
    一旦在线表被裁剪或开启了保留机制且存在快照，则改走
    `快照 -> 归档 -> 尾段` 的重建路径；快照损坏会自动退回归档段。
    """
    if run_dir is not None and _needs_artifact_rebuild(store, run_dir):
        from ..world.retention import rebuild_from_artifacts

        try:
            state, player_ticks, _source = rebuild_from_artifacts(
                store,
                Path(run_dir),
                locations,
                seed=42 if seed is None else int(seed),
                content_hash=content_hash,
            )
            return state, player_ticks
        except Exception as exc:  # noqa: BLE001 - 明确报错，绝不静默半重建
            raise RuntimeError(f"event_rebuild_failed:{exc}") from exc

    state = WorldState(locations=locations)
    player_ticks: dict[str, int] = {}
    for event in store.read_all():
        state.tick = int(event.tick)
        if event.outcome != "ok":
            continue
        apply_effects(state, [Effect.from_dict(d) for d in event.effects])
        actor = str(event.actor)
        if actor.startswith("player:") and str(event.source) not in _SYSTEM_SOURCES:
            player_ticks[actor] = max(player_ticks.get(actor, int(event.tick)), int(event.tick))
    return state, player_ticks


def _needs_artifact_rebuild(store: EventStore, run_dir: str | Path) -> bool:
    """是否必须/允许读归档与快照。

    - 已裁剪（min_seq>1）：必须读，否则会丢前缀；
    - 未裁剪但显式开启保留机制且存在快照：允许加速回放；
    - 其余情况保持今天的全量回放。
    """
    if store.min_seq() > 1:
        return True
    if os.environ.get("XKX_EVENT_RETENTION") != "1":
        return False
    return (Path(run_dir) / "snapshots" / "snapshot.json").exists()
