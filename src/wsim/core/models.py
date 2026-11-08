"""核心数据模型：Intent / Effect / Event。

设计约束：
- Intent 是唯一的外部输入格式（NPC、玩家、系统同一格式）。
- Effect 是唯一的变更载体，apply() 只能返回 Effect，不能直接改状态。
- Event 是唯一事实源，事件一旦追加不可修改。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Intent:
    actor: str
    verb: str
    args: dict[str, Any]
    tick: int
    source: str = "npc"  # npc / system / player / planner
    schedule_id: str | None = None
    intent_id: str = ""
    reason: str = ""  # 决策动机（一句话），写进事件日志供审计解释
    # NPC 日历时钟（ambient 步数）；只用于“按天”判定，不进 to_dict()/事件/哈希。
    # None 表示无自走环境，所有判定回退到 intent.tick（旧语义逐字节不变）。
    clock_tick: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "verb": self.verb,
            "args": self.args,
            "tick": self.tick,
            "source": self.source,
            "schedule_id": self.schedule_id,
            "intent_id": self.intent_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Effect:
    op: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "data": self.data}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Effect":
        return Effect(op=d["op"], data=d.get("data", {}))


@dataclass
class Event:
    seq: int
    tick: int
    day: int
    intent_id: str
    actor: str
    verb: str
    args: dict[str, Any]
    effects: list[dict[str, Any]]
    outcome: str  # ok / rejected
    reason: str
    source: str
    # 旧 run 的 player:visitor 事件在读取时标记；不参与规范哈希，保证旧 run 可回放。
    legacy: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = self.to_canonical_dict()
        if self.legacy:
            data["legacy"] = True
        return data

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "tick": self.tick,
            "day": self.day,
            "intent_id": self.intent_id,
            "actor": self.actor,
            "verb": self.verb,
            "args": self.args,
            "effects": self.effects,
            "outcome": self.outcome,
            "reason": self.reason,
            "source": self.source,
        }


class Reject(Exception):
    """事务被拒绝。拒绝也是一条事件（outcome=rejected），但状态不变。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class EffectError(Exception):
    """Effect 非法（引用不存在、余额不足、原子性失败等）。"""


@dataclass
class ScheduleItem:
    id: str
    tick: int
    actor: str
    verb: str
    args: dict[str, Any] = field(default_factory=dict)
    created_tick: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tick": self.tick,
            "actor": self.actor,
            "verb": self.verb,
            "args": self.args,
            "created_tick": self.created_tick,
        }
