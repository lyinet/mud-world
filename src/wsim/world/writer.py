"""唯一写者。

一个 Intent = 一个事务：
  preconditions -> apply(纯函数) -> Effect[] -> clone 上原子应用 -> Event 追加
任何模块都不能绕过 Writer 修改 WorldState。
"""
from __future__ import annotations

from ..core.canonical import hash_obj
from ..core.clock import day_of
from ..core.models import Effect, Event, Intent, Reject
from ..core.rng import PRNG
from .effects import EffectError, apply_effects, effects_to_dicts
from .state import WorldState
from .store import EventStore


def intent_id_for(actor: str, tick: int, verb: str, args: dict, reason: str = "") -> str:
    return "i:" + hash_obj({"actor": actor, "tick": tick, "verb": verb, "args": args, "reason": reason})


class Writer:
    def __init__(self, state: WorldState, store: EventStore, registry, seed: int):
        self.state = state
        self.store = store
        self.registry = registry
        self.seed = seed
        self._seen: dict[str, Event] = {}
        self._player_ticks: dict[str, int] = {}
        self.committed_intents = 0

    def bootstrap(self, effects: list[Effect], tick: int = 0, verb: str = "genesis") -> Event:
        """系统引导事件（世界从空状态建立）。仍然经过事件日志。"""
        trial = self.state.clone()
        apply_effects(trial, effects)
        trial.tick = tick
        seq = self.store.count() + 1
        event = Event(
            seq=seq,
            tick=tick,
            day=day_of(tick),
            intent_id="i:genesis",
            actor="system",
            verb=verb,
            args={},
            effects=effects_to_dicts(effects),
            outcome="ok",
            reason="",
            source="system",
        )
        self.store.append(event)
        self.state = trial
        return event

    def submit(self, intent: Intent) -> Event:
        iid = intent.intent_id or intent_id_for(intent.actor, intent.tick, intent.verb, intent.args, intent.reason)
        if iid in self._seen:
            return self._seen[iid]

        seq = self.store.count() + 1
        # 玩家写操作必须单调且不能写未来：NPC/系统调度不受影响。
        if self._is_player_intent(intent):
            last = self._player_ticks.get(intent.actor)
            if (last is not None and intent.tick < last) or intent.tick > self.state.tick:
                return self._record_reject(intent, iid, seq, "rejected:stale_tick")
            self._player_ticks[intent.actor] = intent.tick

        action = self.registry.get(intent.verb)
        if action is None:
            return self._record_reject(intent, iid, seq, "rejected:unknown_verb")

        try:
            reasons = action.preconditions(self.state, intent) or []
            if reasons:
                return self._record_reject(intent, iid, seq, f"rejected:{reasons[0]}")
            rng = PRNG(self.seed, seq, f"action:{intent.verb}")
            effects = list(action.apply(self.state, intent, rng))
            if intent.schedule_id:
                effects.append(Effect("schedule_remove", {"id": intent.schedule_id}))
            trial = self.state.clone()
            apply_effects(trial, effects)
            trial.tick = intent.tick
        except Reject as exc:
            return self._record_reject(intent, iid, seq, f"rejected:{exc.reason}")
        except EffectError as exc:
            return self._record_reject(intent, iid, seq, f"rejected:effect_invalid({exc})")
        except (KeyError, TypeError, ValueError) as exc:
            return self._record_reject(intent, iid, seq, f"rejected:bad_args({exc})")

        event = Event(
            seq=seq,
            tick=intent.tick,
            day=day_of(intent.tick),
            intent_id=iid,
            actor=intent.actor,
            verb=intent.verb,
            args=intent.args,
            effects=effects_to_dicts(effects),
            outcome="ok",
            reason=intent.reason,
            source=intent.source,
        )
        self.store.append(event)
        self.state = trial
        self._seen[iid] = event
        self.committed_intents += 1
        return event

    def _is_player_intent(self, intent: Intent) -> bool:
        return (
            str(intent.actor).startswith("player:")
            and intent.source not in ("npc", "planner", "system")
        )

    def _record_reject(self, intent: Intent, iid: str, seq: int, reason: str) -> Event:
        event = Event(
            seq=seq,
            tick=intent.tick,
            day=day_of(intent.tick),
            intent_id=iid,
            actor=intent.actor,
            verb=intent.verb,
            args=intent.args,
            effects=[],
            outcome="rejected",
            reason=reason,
            source=intent.source,
        )
        self.store.append(event)
        self._seen[iid] = event
        return event
