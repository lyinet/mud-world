"""玩家最小切片：进入 / 在场心跳 / 离开。

玩家实体仍由服务端注册（`player:<id>`），动作只经 propose → Writer 落地；
本模块不开放杀害、抢掠、强迫婚配。
"""
from __future__ import annotations

from ..agent.dialogue import PLAYER_NAME
from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import wallet
from .registry import Action


def _player_id(intent: Intent) -> str:
    pid = str(intent.args.get("player_id") or "").strip()
    if pid:
        return pid if pid.startswith("player:") else ""
    actor = str(intent.actor or "").strip()
    return actor if actor.startswith("player:") else ""


def _at(intent: Intent) -> str:
    value = intent.args.get("at")
    return value.strip() if isinstance(value, str) else ""


class PlayerEnterAction(Action):
    verb = "player_enter"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["missing_player_id"]
        at = _at(intent)
        if not at or at not in state.locations:
            return ["bad_location"]
        ent = state.entities.get(pid)
        if ent is not None and ent.presence == "online":
            return ["already_online"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        if not pid:
            raise Reject("missing_player_id")
        at = _at(intent)
        tick = int(intent.tick)
        effects: list[Effect] = []
        if pid not in state.entities:
            name = str(intent.args.get("name") or PLAYER_NAME).strip() or PLAYER_NAME
            effects.append(
                Effect(
                    "create_entity",
                    {
                        "id": pid,
                        "name": name,
                        "role": "visitor",
                        "location": at,
                        "home": at,
                        "stamina": 100.0,
                        "max_stamina": 100.0,
                    },
                )
            )
        else:
            if state.entities[pid].location != at:
                effects.append(Effect("set_field", {"entity": pid, "field": "location", "value": at}))
            effects.append(Effect("set_field", {"entity": pid, "field": "home", "value": at}))
        if wallet(pid) not in state.accounts:
            effects.append(Effect("create_account", {"account": wallet(pid), "balance": 0}))
        effects.append(Effect("set_field", {"entity": pid, "field": "presence", "value": "online"}))
        effects.append(Effect("set_field", {"entity": pid, "field": "last_seen_tick", "value": tick}))
        effects.append(Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": tick}))
        return effects


class PlayerPresenceAction(Action):
    verb = "player_presence"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        if not _player_id(intent):
            return ["missing_player_id"]
        if _player_id(intent) not in state.entities:
            return ["unknown_player"]
        at = intent.args.get("at")
        if at is not None and str(at) not in state.locations:
            return ["bad_location"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        if not pid:
            raise Reject("missing_player_id")
        tick = int(intent.tick)
        effects: list[Effect] = []
        at = intent.args.get("at")
        if isinstance(at, str) and at and state.entities[pid].location != at:
            effects.append(Effect("set_field", {"entity": pid, "field": "location", "value": at}))
        effects.append(Effect("set_field", {"entity": pid, "field": "presence", "value": "online"}))
        effects.append(Effect("set_field", {"entity": pid, "field": "last_seen_tick", "value": tick}))
        return effects


class PlayerLeaveAction(Action):
    verb = "player_leave"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        if not _player_id(intent):
            return ["missing_player_id"]
        if _player_id(intent) not in state.entities:
            return ["unknown_player"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        if not pid:
            raise Reject("missing_player_id")
        tick = int(intent.tick)
        return [
            Effect("set_field", {"entity": pid, "field": "presence", "value": "offline"}),
            Effect("set_field", {"entity": pid, "field": "last_seen_tick", "value": tick}),
        ]
