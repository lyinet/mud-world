"""原版 `wimpy` 命令：设置自动逃跑的气血阈值。

对应 `cmds/usr/wimpy.lpc:7-20`：
- 无参数显示当前阈值；
- 参数只接受 0..80；越界返回帮助，不写状态；
- 合法时写 `env/wimpy`（本引擎落在 `Entity.wimpy`）。

自动逃跑本身在 NPC 心跳（`actions/aggression.py` 的 continue）里执行，
与 `inherit/char/char.lpc:80-97` 的“先逃、再打”顺序一致。
"""
from __future__ import annotations

from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .registry import Action

WIMPY_MIN = 0
WIMPY_MAX = 80


class WimpyAction(Action):
    verb = "wimpy"

    def _value(self, intent: Intent) -> int:
        raw = intent.args.get("value")
        if raw is None:
            raise Reject("bad_wimpy")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise Reject("bad_wimpy") from None
        if value < WIMPY_MIN or value > WIMPY_MAX:
            raise Reject("bad_wimpy")
        return value

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = str(intent.actor)
        if pid not in state.players or pid not in state.entities:
            return ["player_not_entered"]
        try:
            self._value(intent)
        except Reject as exc:
            return [str(exc)]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = state.entities[str(intent.actor)]
        value = self._value(intent)
        previous = int(ent.wimpy)
        return [
            Effect(op="set_field", data={"entity": ent.id, "field": "wimpy", "value": value}),
            Effect(
                op="wimpy_log",
                data={"action": "set", "value": value, "previous": previous},
            ),
        ]
