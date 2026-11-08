"""唯一写者。

一个 Intent = 一个事务：
  preconditions -> apply(纯函数) -> Effect[] -> clone 上原子应用 -> Event 追加
任何模块都不能绕过 Writer 修改 WorldState。
"""
from __future__ import annotations

import copy
import os
from collections import OrderedDict

from ..core.canonical import hash_obj
from ..core.clock import day_of
from ..core.models import Effect, Event, Intent, Reject
from ..core.rng import PRNG
from .effects import EffectError, apply_effects, effects_to_dicts
from .state import WorldState
from .store import EventStore


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def intent_id_for(actor: str, tick: int, verb: str, args: dict, reason: str = "") -> str:
    return "i:" + hash_obj({"actor": actor, "tick": tick, "verb": verb, "args": args, "reason": reason})


class Writer:
    def __init__(
        self, state: WorldState, store: EventStore, registry, seed: int, seen_max: int | None = None
    ):
        self.state = state
        self.store = store
        self.registry = registry
        self.seed = seed
        # 幂等缓存只保留最近 N 个 intent：旧 key 淘汰后重放会走正常校验，
        # 不改变事件语义；上限防止长时间运行时内存无界增长。
        self.seen_max = _env_int("XKX_WRITER_SEEN_MAX", 20_000) if seen_max is None else max(1, int(seen_max))
        # 只存 intent_id -> seq：Event 体积大（args/effects 深字典），
        # 常驻 2 万条会吃掉数十 MB；重放命中时按 seq 单行回读即可。
        self._seen: OrderedDict[str, int] = OrderedDict()
        self._player_ticks: dict[str, int] = {}
        self.committed_intents = 0

    def _remember(self, iid: str, event: Event) -> None:
        self._seen[iid] = int(event.seq)
        self._seen.move_to_end(iid)
        while len(self._seen) > self.seen_max:
            self._seen.popitem(last=False)

    def _recall(self, iid: str) -> Event | None:
        """幂等命中：按 seq 单行回读，避免 Event 常驻内存。"""
        seq = self._seen.get(iid)
        if seq is None:
            return None
        event = self.store.event_by_seq(seq)
        if event is None:
            self._seen.pop(iid, None)
        return event

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

    def reject(self, intent: Intent, reason: str) -> Event:
        """拒绝也是事件：供 API 层在校验阶段记录原因，仍然只由 Writer 追加。"""
        iid = intent.intent_id or intent_id_for(intent.actor, intent.tick, intent.verb, intent.args, intent.reason)
        existing = self._recall(iid)
        if existing is not None:
            return existing
        code = reason if reason.startswith("rejected:") else f"rejected:{reason}"
        return self._record_reject(intent, iid, self.store.count() + 1, code)

    def submit(self, intent: Intent) -> Event:
        iid = intent.intent_id or intent_id_for(intent.actor, intent.tick, intent.verb, intent.args, intent.reason)
        existing = self._recall(iid)
        if existing is not None:
            return existing

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
            # 玩家成功动作刷新 last_intent_tick，离线状态机才按“无玩家意图”计时。
            # enter 自己写 presence，leave 必须保持 offline；旧 run 的 visitor 无档案时跳过。
            if (
                self._is_player_intent(intent)
                and intent.verb not in ("player_enter", "player_leave")
                and intent.actor in self.state.players
            ):
                effects.append(
                    Effect(
                        "player_presence",
                        {"id": intent.actor, "presence": "online", "tick": intent.tick},
                    )
                )
            if intent.schedule_id:
                effects.append(Effect("schedule_remove", {"id": intent.schedule_id}))
            if effects:
                trial = self.state.clone()
                apply_effects(trial, effects)
            else:
                # 无 Effect 时不必整世界深拷贝：浅拷贝只替换 tick 字段，
                # 子结构与旧投影共享但不会被任何后续写入原地修改，
                # 因此“已提交投影不可变、快照读不加锁”的约定仍然成立。
                trial = copy.copy(self.state)
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
        self._remember(iid, event)
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
        self._remember(iid, event)
        return event
