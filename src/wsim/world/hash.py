"""世界哈希与事件流哈希。"""
from __future__ import annotations

from ..core.canonical import hash_obj
from ..core.models import Event
from .state import WorldState


def state_hash(state: WorldState) -> str:
    return state.hash()


def event_hash(events: list[Event]) -> str:
    return hash_obj([e.to_dict() for e in events])


def combined_hash(state: WorldState, events: list[Event]) -> str:
    return hash_obj({"state": state.to_dict(), "events": [e.to_dict() for e in events]})
