"""玩家门派动作：立派 / 收徒 / 逐出 / 日结 / 存取款。

只由玩家 API 会话注册（`api/session.py`），不进入 `actions/registry.py` 的内置表，
因此不改变 NPC/无玩家世界的确定性。`found_sect` 对非玩家 actor 原样委托
`business.FoundSectAction`。接线契约见 `docs/player-api.md` §13。

`sect_id` 可省略：缺省取 actor 当前门派（`entity.faction`），未入派返回 `no_sect`。

资金纪律：所有金额只走 `transfer` / `debt_*`，不出现 `mint`；P1 也不 `burn`
（注册费改为真实转入 `treasury:region`）。
"""
from __future__ import annotations

import re
from typing import Any

from ..core.clock import TICKS_PER_DAY
from ..core.models import Effect, Intent, Reject
from ..world.state import Faction, WorldState
from .business import FoundSectAction
from .common import (
    arg_int,
    arg_str,
    explicit_goal_id,
    goal_progress,
    max_skill,
    memory,
    payment_with_shortfall,
    reputation_points,
    require_entity,
    wallet,
)
from .registry import Action

# —— 玩家立派门槛（唯一事实来源，接线时可外提到 economy.sect.player）——
REGISTRATION_FEE = 200
STARTUP_CAPITAL = 600
FOUNDING_COST = REGISTRATION_FEE + STARTUP_CAPITAL  # 800
MIN_REPUTATION = 20.0
MIN_NAME_LEN, MAX_NAME_LEN = 2, 12
MAX_DOCTRINE_LEN = 60
DEFAULT_DOCTRINE = "守望相济，不欺乡里"
DEFAULT_START_LOCATION = "treasury:region"

# —— 收徒 ——
MIN_AFFECTION = 30.0
MIN_TRUST = 30.0
MIN_TARGET_SKILL = 0.2
MIN_MASTER_SKILL = 0.4
DISCIPLE_CAP = 3

# —— 会费 / 俸禄 / 欠饷 ——
DUES_DAILY = 5
STIPEND_DAILY = 10
ARREARS_EXPEL = 3 * STIPEND_DAILY  # 30 文 ≈ 3 个世界日
CULTIVATE_GAIN = 0.02

# —— 金库 ——
WITHDRAW_CAP = 500
TREASURY_RESERVE = 100
SETTLE_MARKER = "门派日结"

_SLUG = re.compile(r"^[a-z0-9_-]{2,32}$")


def is_player(actor: Any) -> bool:
    return str(actor or "").startswith("player:")


def sect_faction_id(sid: str) -> str:
    return f"sect:{sid}"


def sect_treasury(sid: str) -> str:
    return f"treasury:sect:{sid}"


def _sect_sid(intent: Intent, default: str | None = None) -> str:
    raw = arg_str(intent, "sect_id", required=False)
    sid = (raw if raw is not None else default) or ""
    sid = sid.strip()
    if sid.startswith("sect:"):
        sid = sid[5:]
    if not _SLUG.match(sid):
        raise Reject("bad_arg:sect_id")
    return sid


def _default_sid(actor: str) -> str:
    local = str(actor).split(":")[-1].lower()
    return re.sub(r"[^a-z0-9_-]", "_", local)


def _faction(state: WorldState, sid: str) -> Faction | None:
    return state.factions.get(sect_faction_id(sid))


def _resolve_sect(state: WorldState, intent: Intent) -> tuple[str, Faction | None, str]:
    """解析目标门派：显式 `sect_id` 优先，缺省取 actor 的 `entity.faction`。

    返回 `(sid, faction, reason)`；`reason` 非空表示应先拒绝。
    "未入派" 与 "门派不存在" 分开：前者 `no_sect`，后者 `sect_not_found`。
    """
    raw = str(arg_str(intent, "sect_id", required=False) or "").strip()
    if raw.startswith("sect:"):
        raw = raw[5:]
    if raw:
        if not _SLUG.match(raw):
            return "", None, "bad_arg:sect_id"
        fac = _faction(state, raw)
        return raw, fac, "" if fac is not None else "sect_not_found"
    ent = require_entity(state, intent.actor)
    faction = str(ent.faction or "")
    if not faction.startswith("sect:"):
        return "", None, "no_sect"
    sid = faction[5:]
    fac = _faction(state, sid)
    return sid, fac, "" if fac is not None else "sect_not_found"


def _is_master(fac: Faction, actor: str) -> bool:
    return fac.leader == actor or fac.founder == actor


def active_disciples(state: WorldState, fac: Faction) -> list[str]:
    """权威名册：`faction.members` 里仍把 `entity.faction` 指向本派、且活着的人。

    `effects.py` 没有 `faction_leave`，离派者会留在历史 members 列表里；
    以实体当前归属为准，可避免离派者继续占名额或被重复日结。
    """
    out: list[str] = []
    for eid in sorted(fac.members):
        if eid in (fac.founder, fac.leader):
            continue
        ent = state.entities.get(eid)
        if ent is None or ent.status != "alive":
            continue
        if str(ent.faction or "") != fac.id:
            continue
        out.append(eid)
    return out


def _bond(state: WorldState, a: str, b: str) -> tuple[float, float]:
    """双向取最大好感/信任，与婚姻裁决口径一致。"""
    aff, trust = 0.0, 0.0
    for rel in state.relations.values():
        if rel.status != "active":
            continue
        if not ((rel.a == a and rel.b == b) or (rel.a == b and rel.b == a)):
            continue
        aff = max(aff, float(rel.affection))
        trust = max(trust, float(rel.trust))
    return aff, trust


def _top_skill(ent) -> str:
    if not ent.skills:
        return ""
    return max(ent.skills, key=lambda k: (ent.skills[k], k))


def _clean_name(intent: Intent) -> str:
    name = (arg_str(intent, "name") or "").strip()
    if not (MIN_NAME_LEN <= len(name) <= MAX_NAME_LEN):
        raise Reject("invalid_sect_name")
    return name


def _leave_effects(state: WorldState, fac: Faction, master: str, did: str, tick: int, text: str) -> list[Effect]:
    """弟子离派/被逐的共用清理：实体归属 + 双向关系 + 记忆（债务保留）。"""
    effects: list[Effect] = [
        Effect("set_field", {"entity": did, "field": "faction", "value": None}),
        Effect("set_field", {"entity": did, "field": "master", "value": None}),
        Effect(
            "relation_upsert",
            {"a": master, "b": did, "kind": "master", "status": "ended"},
        ),
        Effect(
            "relation_upsert",
            {"a": did, "b": master, "kind": "disciple", "status": "ended"},
        ),
    ]
    effects.extend(memory(state, master, tick, text, 0.7))
    effects.extend(memory(state, did, tick, text, 0.7))
    return effects


class PlayerFoundSectAction(Action):
    """玩家立派；非玩家 actor 原样委托给 NPC 的 `FoundSectAction`。"""

    verb = "found_sect"

    def __init__(self, content=None):
        self.content = content
        self._npc = FoundSectAction(content)

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        if not is_player(intent.actor):
            return self._npc.preconditions(state, intent)
        ent = state.entities.get(intent.actor)
        if ent is None:
            return ["unknown_actor"]
        if ent.status != "alive":
            return ["actor_not_alive"]
        if str(ent.faction or "").startswith("sect:"):
            return ["already_in_sect"]

        at = arg_str(intent, "at")
        if at != ent.location:
            return ["wrong_location"]
        if at not in state.locations:
            return ["unknown_location"]

        name = (arg_str(intent, "name") or "").strip()
        if not (MIN_NAME_LEN <= len(name) <= MAX_NAME_LEN):
            return ["invalid_sect_name"]
        if any(fac.name == name for fac in state.factions.values()):
            return ["sect_name_taken"]

        sid = _sect_sid(intent, _default_sid(ent.id))
        if sect_faction_id(sid) in state.factions:
            return ["sect_exists"]
        if state.account_balance(wallet(ent.id)) < FOUNDING_COST:
            return ["insufficient_funds"]
        if reputation_points(state, ent.id) < MIN_REPUTATION:
            return ["insufficient_rep"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        if not is_player(intent.actor):
            return self._npc.apply(state, intent, rng)
        ent = require_entity(state, intent.actor)
        sid = _sect_sid(intent, _default_sid(ent.id))
        fid = sect_faction_id(sid)
        treasury = sect_treasury(sid)
        at = arg_str(intent, "at")
        name = _clean_name(intent)
        doctrine = str(intent.args.get("doctrine") or "").strip()[:MAX_DOCTRINE_LEN]
        if not doctrine:
            doctrine = DEFAULT_DOCTRINE
        tick = int(intent.tick)
        effects: list[Effect] = [
            Effect("create_account", {"account": treasury}),
            Effect(
                "transfer",
                {
                    "src": wallet(ent.id),
                    "dst": DEFAULT_START_LOCATION,
                    "amount": REGISTRATION_FEE,
                    "reason": "sect_registration_fee",
                },
            ),
            Effect(
                "transfer",
                {
                    "src": wallet(ent.id),
                    "dst": treasury,
                    "amount": STARTUP_CAPITAL,
                    "reason": "sect_endowment",
                },
            ),
            Effect(
                "create_faction",
                {
                    "id": fid,
                    "name": name,
                    "location": at,
                    "founder": ent.id,
                    "treasury_account": treasury,
                },
            ),
            Effect("faction_join", {"entity": ent.id, "faction": fid}),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": tick}),
        ]
        effects.extend(
            goal_progress(state, ent.id, "faction", 1.0, tick, goal_id=explicit_goal_id(intent))
        )
        effects.extend(
            memory(
                state,
                ent.id,
                tick,
                f"在{at}创立{name}，宗旨：{doctrine}；注册费{REGISTRATION_FEE}文，"
                f"启动金{STARTUP_CAPITAL}文",
                0.9,
            )
        )
        return effects


class PlayerRecruitDiscipleAction(Action):
    verb = "recruit_disciple"

    def __init__(self, content=None):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        sid, fac, reason = _resolve_sect(state, intent)
        if reason:
            return [reason]
        if not _is_master(fac, ent.id):
            return ["not_sect_leader"]
        if ent.location != fac.location:
            return ["wrong_location"]
        if reputation_points(state, ent.id) < MIN_REPUTATION:
            return ["insufficient_rep"]
        target = arg_str(intent, "target")
        tgt = state.entities.get(target)
        if tgt is None:
            return ["target_not_found"]
        if tgt.status != "alive":
            return ["target_not_alive"]
        if tgt.id == ent.id:
            return ["self_recruit"]
        if is_player(tgt.id):
            return ["target_is_player"]
        if tgt.location != ent.location:
            return ["not_together"]
        if str(tgt.faction or "").startswith("sect:"):
            return ["already_disciple"] if tgt.faction == fac.id else ["target_in_sect"]
        if len(active_disciples(state, fac)) >= DISCIPLE_CAP:
            return ["sect_full"]
        aff, trust = _bond(state, ent.id, tgt.id)
        if aff < MIN_AFFECTION:
            return ["favor_too_low"]
        if trust < MIN_TRUST:
            return ["trust_too_low"]
        if max_skill(tgt) < MIN_TARGET_SKILL or max_skill(ent) < MIN_MASTER_SKILL:
            return ["skill_too_low"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        sid, fac, _ = _resolve_sect(state, intent)
        assert fac is not None
        fid = sect_faction_id(sid)
        target = arg_str(intent, "target")
        tgt = require_entity(state, target)
        tick = int(intent.tick)
        aff, trust = _bond(state, ent.id, tgt.id)
        effects: list[Effect] = [
            Effect("faction_join", {"entity": tgt.id, "faction": fid}),
            Effect("add_disciple", {"master": ent.id, "disciple": tgt.id}),
            Effect("set_field", {"entity": tgt.id, "field": "master", "value": ent.id}),
            Effect(
                "relation_upsert",
                {
                    "a": ent.id,
                    "b": tgt.id,
                    "kind": "master",
                    "affection": min(100.0, aff + 5.0),
                    "trust": min(100.0, trust + 5.0),
                },
            ),
            Effect(
                "relation_upsert",
                {
                    "a": tgt.id,
                    "b": ent.id,
                    "kind": "disciple",
                    "affection": min(100.0, aff + 5.0),
                    "trust": min(100.0, trust + 5.0),
                },
            ),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": tick}),
            Effect("set_field", {"entity": tgt.id, "field": "last_action_tick", "value": tick}),
        ]
        effects.extend(
            memory(
                state,
                ent.id,
                tick,
                f"收{tgt.name}为徒：日奉会费{DUES_DAILY}文，日给俸禄{STIPEND_DAILY}文",
                0.7,
            )
        )
        effects.extend(
            memory(
                state,
                tgt.id,
                tick,
                f"拜入{fid}：日奉会费{DUES_DAILY}文，日领俸禄{STIPEND_DAILY}文",
                0.7,
            )
        )
        return effects


class PlayerExpelDiscipleAction(Action):
    verb = "expel_disciple"

    def __init__(self, content=None):
        self.content = content

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        sid, fac, reason = _resolve_sect(state, intent)
        if reason:
            return [reason]
        if not _is_master(fac, ent.id):
            return ["not_sect_leader"]
        if ent.location != fac.location:
            return ["wrong_location"]
        target = arg_str(intent, "target")
        tgt = state.entities.get(target)
        if tgt is None:
            return ["disciple_not_found"]
        if tgt.id == ent.id:
            return ["self_recruit"]
        if str(tgt.faction or "") != fac.id:
            return ["target_not_disciple"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        sid, fac, _ = _resolve_sect(state, intent)
        target = arg_str(intent, "target")
        tgt = require_entity(state, target)
        tick = int(intent.tick)
        assert fac is not None
        effects = _leave_effects(
            state, fac, ent.id, tgt.id, tick, f"掌门逐出弟子{tgt.name}"
        )
        effects.append(Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": tick}))
        return effects


class PlayerPayDisciplesAction(Action):
    """门派日结：收会费 → 发俸禄 → 记欠饷 → 欠饷满额逐出。

    P1 没有日界系统动作，因此由掌门（或接线后的日界调度）显式调用；
    同一天重复调用以掌门记忆里的 `门派日结:<fid>:D<day>` 判定幂等。
    """

    verb = "pay_disciples"

    def __init__(self, content=None):
        self.content = content

    def _day(self, intent: Intent) -> int:
        day = arg_int(intent, "day", default=int(intent.tick) // TICKS_PER_DAY, minimum=0)
        if day > int(intent.tick) // TICKS_PER_DAY:
            raise Reject("bad_arg:day")
        return day

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        sid, fac, reason = _resolve_sect(state, intent)
        if reason:
            return [reason]
        if not _is_master(fac, ent.id):
            return ["not_sect_leader"]
        if ent.location != fac.location:
            return ["wrong_location"]
        day = self._day(intent)
        if not active_disciples(state, fac):
            return ["no_disciples"]
        marker = f"{SETTLE_MARKER}:{fac.id}:D{day}"
        if any(marker in str(m.get("text", "")) for m in state.memories.get(ent.id, ())):
            return ["already_settled_today"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        sid, fac, _ = _resolve_sect(state, intent)
        assert fac is not None
        tick = int(intent.tick)
        day = self._day(intent)
        disciples = active_disciples(state, fac)
        treasury = fac.treasury_account
        effects: list[Effect] = []

        # 1) 会费：弟子钱包 → 金库。各自独立钱包，可用统一短款记账。
        dues_in = 0
        for did in disciples:
            # 弟子钱包余额是日结事务开始时的值，直接由 Wallet 派生（没有并发）。
            dues_in += min(state.account_balance(wallet(did)), DUES_DAILY)
            effects.extend(
                payment_with_shortfall(
                    state,
                    did,
                    fac.id,
                    DUES_DAILY,
                    debt_id=f"debt:sect_dues:{fac.id}:{did}",
                    created_tick=tick,
                    note="unpaid_dues",
                    transfer_reason="sect_dues",
                )
            )

        # 2) 俸禄：预算 = 日结开始时金库 + 当日实收会费（现金部分）之和；
        #    P1 是单线程提交，`apply` 内不存在并发，按 id 顺序分配即可。
        budget = state.account_balance(treasury) + dues_in
        stipend_out = 0
        leavers: list[str] = []
        for did in disciples:
            tgt = state.entities[did]
            base_debt = f"debt:sect_stipend:{fac.id}:{did}"
            debt = state.debts.get(base_debt)
            if debt is not None and debt.status != "active":
                base_debt = f"{base_debt}:{tick}"
                debt = state.debts.get(base_debt)
            old = int(debt.amount) if debt is not None and debt.status == "active" else 0

            pay_now = min(budget, STIPEND_DAILY)
            budget -= pay_now
            stipend_out += pay_now
            short = STIPEND_DAILY - pay_now
            if pay_now:
                effects.append(
                    Effect(
                        "transfer",
                        {
                            "src": treasury,
                            "dst": wallet(did),
                            "amount": pay_now,
                            "reason": "sect_stipend",
                        },
                    )
                )
            settle = min(budget, old)
            budget -= settle
            if settle:
                effects.append(Effect("debt_settle", {"id": base_debt, "amount": settle}))
            if old:
                if short:
                    effects.append(Effect("debt_increase", {"id": base_debt, "amount": short}))
                arrears = old - settle + short
            else:
                if short:
                    effects.append(
                        Effect(
                            "debt_create",
                            {
                                "id": base_debt,
                                "creditor": did,
                                "debtor": fac.id,
                                "amount": short,
                                "created_tick": tick,
                                "note": "unpaid_stipend",
                            },
                        )
                    )
                arrears = short

            # 3) 出勤：在途委托 > 在驻地修炼 > 不在驻地。
            on_quest = any(
                q.acceptor == did and q.status == "accepted" for q in state.quests.values()
            )
            duty = "quest" if on_quest else ("cultivate" if tgt.location == fac.location else "away")
            if duty == "cultivate":
                skill = _top_skill(tgt)
                if skill:
                    effects.append(
                        Effect("skill", {"entity": did, "skill": skill, "delta": CULTIVATE_GAIN})
                    )
            effects.extend(memory(state, did, tick, f"{fac.name}日结：第{day}日，{_duty_text(duty)}", 0.3))

            # 4) 欠饷满额：当日离派（债务保留，门派仍欠弟子）。
            if arrears >= ARREARS_EXPEL:
                leavers.append(did)
                effects.extend(
                    _leave_effects(
                        state,
                        fac,
                        ent.id,
                        did,
                        tick,
                        f"{tgt.name}欠饷{arrears}文，离开{fac.name}",
                    )
                )

        # 5) 掌门日结摘要（同时是幂等标记）。
        text = (
            f"{SETTLE_MARKER}:{fac.id}:D{day} 收会费{dues_in}文，发俸禄{stipend_out}文"
            + (f"，{len(leavers)}名弟子欠饷离派" if leavers else "")
        )
        effects.extend(memory(state, ent.id, tick, text, 0.5))
        effects.append(Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": tick}))
        return effects


def _duty_text(duty: str) -> str:
    if duty == "quest":
        return "外出任务"
    if duty == "cultivate":
        return "在驻地修炼"
    return "不在驻地"


class PlayerSectDepositAction(Action):
    verb = "sect_deposit"

    def __init__(self, content=None):
        self.content = content

    def _amount(self, intent: Intent) -> int:
        return arg_int(intent, "amount", minimum=1, maximum=10000)

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        sid, fac, reason = _resolve_sect(state, intent)
        if reason:
            return [reason]
        if not _is_master(fac, ent.id):
            return ["not_sect_leader"]
        if ent.location != fac.location:
            return ["wrong_location"]
        amount = self._amount(intent)
        if state.account_balance(wallet(ent.id)) < amount:
            return ["insufficient_funds"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        sid, fac, _ = _resolve_sect(state, intent)
        assert fac is not None
        amount = self._amount(intent)
        tick = int(intent.tick)
        effects: list[Effect] = [
            Effect(
                "transfer",
                {
                    "src": wallet(ent.id),
                    "dst": fac.treasury_account,
                    "amount": amount,
                    "reason": "sect_deposit",
                },
            ),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": tick}),
        ]
        effects.extend(memory(state, ent.id, tick, f"向{fac.name}金库存入{amount}文", 0.4))
        return effects


class PlayerSectWithdrawAction(Action):
    verb = "sect_withdraw"

    def __init__(self, content=None):
        self.content = content

    def _amount(self, intent: Intent) -> int:
        return arg_int(intent, "amount", minimum=1, maximum=10000)

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        ent = require_entity(state, intent.actor)
        sid, fac, reason = _resolve_sect(state, intent)
        if reason:
            return [reason]
        if not _is_master(fac, ent.id):
            return ["not_sect_leader"]
        if ent.location != fac.location:
            return ["wrong_location"]
        amount = self._amount(intent)
        if amount > WITHDRAW_CAP:
            return ["withdraw_over_cap"]
        balance = state.account_balance(fac.treasury_account)
        if balance < amount:
            return ["insufficient_treasury"]
        if balance - amount < TREASURY_RESERVE:
            return ["treasury_reserve"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = require_entity(state, intent.actor)
        sid, fac, _ = _resolve_sect(state, intent)
        assert fac is not None
        amount = self._amount(intent)
        tick = int(intent.tick)
        effects: list[Effect] = [
            Effect(
                "transfer",
                {
                    "src": fac.treasury_account,
                    "dst": wallet(ent.id),
                    "amount": amount,
                    "reason": "sect_withdraw",
                },
            ),
            Effect("set_field", {"entity": ent.id, "field": "last_action_tick", "value": tick}),
        ]
        effects.extend(memory(state, ent.id, tick, f"从{fac.name}金库提取{amount}文", 0.4))
        return effects
