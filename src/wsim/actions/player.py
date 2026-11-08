"""玩家生命周期动作（P3-E）。

玩家实体由服务端注册（`player:<account_id>`），所有写操作只经
propose_intent → MCP → Writer 落地。本模块不开放杀害、抢掠、强迫婚配。

初始资产有两条显式铸币路径，都会留下独立事件，绝不从 NPC 钱包扣款：
- 创世预置账号：`player_genesis_effects`（genesis 事件里的 mint）。
- 运行中新账号首次进入：`player_endowment` 事件（一次性，按 account_id 幂等）。
"""
from __future__ import annotations

import re

from ..agent.dialogue import PLAYER_NAME
from ..core.clock import TICKS_PER_HOUR, npc_tick_of
from ..core.models import Effect, Intent, Reject
from ..world.state import WorldState
from .common import arg_int, wallet
from .registry import Action

ACCOUNT_RE = re.compile(r"^[a-z0-9_-]{3,32}$")
ARCHETYPES: dict[str, str] = {"游侠": "镖师", "商人": "掌柜", "捕快": "捕快"}
PRESENCE_STATES = ("online", "offline", "custody", "dormant")

# 在线三态只看玩家自己的 wall-clock 活跃时间：全局 tick 每动作 +6，50 人并发时
# 一个“世界日”约 1 秒，拿 tick 差值判离线会把在线玩家批量误判。
# active（活跃）/ idle（在线静置）/ offline（真正离线，超过 W1 的 60 秒宽限）。
PRESENCE_ACTIVE_SECONDS = 30.0
PRESENCE_OFFLINE_SECONDS = 60.0

# 初始资产的唯一事实来源：genesis 预置与首次进入发放共用。
STARTING_MONEY = 300
STARTING_ITEMS: dict[str, int] = {"flatbread": 3, "coarse_cloth": 1}
ENDOWMENT_SOURCE = "genesis_player_endowment"
RUNTIME_ENDOWMENT_SOURCE = "player_endowment"
CARRY_SOURCE = "genesis_player_carry"
RUNTIME_CARRY_SOURCE = "player_endowment_carry"

# 防刷上限：单区域最多 200 个玩家席位，初始资产累计上限 200 * 300 = 60000。
# 超过后新账号 enter 会被拒绝（endowment_cap_reached / player_cap_reached）。
MAX_REGION_PLAYERS = 200
ENDOWMENT_TOTAL_CAP = MAX_REGION_PLAYERS * STARTING_MONEY

# API 层允许的玩家 verb 白名单；L3（伤害/抢掠/强迫婚配）不在其中。
PLAYER_VERBS = frozenset(
    {
        "player_enter",
        "player_heartbeat",
        "player_leave",
        "move",
        "talk",
        "trade",
        "cultivate",
        "work",
        "open_shop",
        # 店铺经营：补货、定价、辞退、提现、看账。
        "restock",
        "set_price",
        "dismiss",
        "withdraw",
        "shop_report",
        "join_sect",
        # 店铺雇佣：复用 RecruitAction 的 shop/position/wage 语义（店员，不是门派招揽）。
        "recruit",
        "quest_accept",
        "quest_deliver",
        # 消费类动作：语义由 BuyAction / SellAction / ConsumeAction / EscortAction 提供。
        "buy",
        "sell",
        "consume",
        "escort",
        # 休息：按小时恢复体力（客栈 > 住处 > 镇内 > 野外），押镖/临近委托截止时拒绝。
        "rest",
        # 玩家婚姻：求婚与和离；NPC 裁决事件由服务端追加。
        "marriage_propose",
        "marriage_end",
        # 玩家门派：立派 / 收徒 / 逐出 / 日结 / 金库存取（只由 API 会话注册）。
        "found_sect",
        "recruit_disciple",
        "expel_disciple",
        "pay_disciples",
        "sect_deposit",
        "sect_withdraw",
        # 装备与物品：槽位/已装备物品进世界状态；use_item 消耗背包物品恢复体力。
        "equip",
        "unequip",
        "use_item",
    }
)


def _player_id(intent: Intent) -> str:
    pid = str(intent.args.get("player_id") or "").strip()
    if pid:
        return pid if pid.startswith("player:") else ""
    actor = str(intent.actor or "").strip()
    return actor if actor.startswith("player:") else ""


def _account_id(intent: Intent) -> str:
    raw = str(intent.args.get("account_id") or "").strip().lower()
    if not raw:
        pid = _player_id(intent)
        if pid.startswith("player:"):
            raw = pid[len("player:") :]
    if raw.startswith("player:"):
        raw = raw[len("player:") :]
    return raw if ACCOUNT_RE.match(raw) else ""


def _at(intent: Intent, state: WorldState, pid: str = "") -> str:
    value = intent.args.get("at")
    if isinstance(value, str) and value.strip():
        return value.strip()
    # 已有实体重进必须留在原地：只有新玩家才落到集市/首个地点，
    # 否则离线久了的玩家一 enter 就被瞬移回 market。
    ent = state.entities.get(pid) if pid else None
    if ent is not None and ent.location:
        return ent.location
    if "market" in state.locations:
        return "market"
    return sorted(state.locations)[0] if state.locations else ""


def _valid_name(state: WorldState, name: str, pid: str = "") -> bool:
    if not (2 <= len(name) <= 12):
        return False
    return all(ent.name != name for eid, ent in state.entities.items() if eid != pid)


def activity_stage(
    idle_seconds: float,
    *,
    active_seconds: float = PRESENCE_ACTIVE_SECONDS,
    offline_seconds: float = PRESENCE_OFFLINE_SECONDS,
) -> str:
    """按玩家自己的真实活跃时间（wall-clock 秒）判定在线三态。

    不得用全局 tick / 世界日：并发越高 tick 推进越快，会把仍在线的玩家误判为离线。
    """
    idle = max(0.0, float(idle_seconds))
    if idle <= float(active_seconds):
        return "active"
    if idle <= float(offline_seconds):
        return "idle"
    return "offline"


def player_genesis_effects(
    pid: str,
    name: str,
    at: str,
    money: int = STARTING_MONEY,
    items: dict[str, int] | None = None,
) -> list[Effect]:
    """创世预置：允许铸币/造物的玩家入口之一（另一条是首次进入发放）。"""
    items = items if items is not None else dict(STARTING_ITEMS)
    effects: list[Effect] = [
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
        ),
        Effect("create_account", {"account": wallet(pid), "balance": 0}),
        Effect("mint", {"account": wallet(pid), "amount": money, "source": ENDOWMENT_SOURCE}),
        Effect(
            "player_upsert",
            {"id": pid, "name": name, "archetype": "", "role": "visitor", "created_tick": 0},
        ),
        # 创世预置同样算一次发放：enter 时不会再触发 player_endowment。
        Effect("player_endow", {"id": pid, "amount": money, "tick": 0, "source": ENDOWMENT_SOURCE}),
        Effect("player_presence", {"id": pid, "presence": "offline", "tick": 0}),
    ]
    for item, qty in sorted(items.items()):
        if qty > 0:
            effects.append(Effect("item_create", {"owner": pid, "item": item, "qty": qty, "source": CARRY_SOURCE}))
    return effects


class PlayerEnterAction(Action):
    verb = "player_enter"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["missing_player_id"]
        if not _account_id(intent):
            return ["bad_account"]
        prof = state.players.get(pid)
        at = _at(intent, state, pid)
        if not at or at not in state.locations:
            return ["bad_location"]
        archetype = str(intent.args.get("archetype") or "").strip()
        if prof is None and len(state.players) >= MAX_REGION_PLAYERS:
            return ["player_cap_reached"]
        if prof is None and not archetype:
            # 兼容旧 run 的 visitor 实体：未显式选身份时按游侠落地，默认技能 0.35。
            archetype = "游侠"
        if prof is None and archetype not in ARCHETYPES:
            return ["bad_archetype"]
        if prof is not None and archetype and prof.archetype and archetype != prof.archetype:
            return ["identity_locked"]
        if prof is not None and prof.online and state.entities.get(pid) is not None:
            return ["already_online"]
        display = str(intent.args.get("display_name") or "").strip()
        if prof is None and not display:
            display = PLAYER_NAME
        if display and not _valid_name(state, display, pid):
            return ["bad_display_name"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        if not pid:
            raise Reject("missing_player_id")
        account = _account_id(intent)
        if not account:
            raise Reject("bad_account")
        at = _at(intent, state, pid)
        tick = int(intent.tick)
        display = str(intent.args.get("display_name") or "").strip()
        archetype = str(intent.args.get("archetype") or "").strip()
        prof = state.players.get(pid)
        effects: list[Effect] = []

        if prof is None:
            display = display or PLAYER_NAME
            archetype = archetype or "游侠"
            role = ARCHETYPES[archetype]
            if pid not in state.entities:
                effects.append(
                    Effect(
                        "create_entity",
                        {
                            "id": pid,
                            "name": display,
                            "role": role,
                            "location": at,
                            "home": at,
                            "stamina": 100.0,
                            "max_stamina": 100.0,
                        },
                    )
                )
            if wallet(pid) not in state.accounts:
                effects.append(Effect("create_account", {"account": wallet(pid), "balance": 0}))
            # 职业技能初始 0.35；身份只影响 role/skill，不产生资产。
            effects.append(Effect("skill", {"entity": pid, "skill": role, "delta": 0.35}))
            effects.append(
                Effect(
                    "player_upsert",
                    {"id": pid, "name": display, "archetype": archetype, "role": role, "created_tick": tick},
                )
            )
        else:
            role = prof.role or ARCHETYPES.get(archetype, "visitor")
            if pid not in state.entities:
                effects.append(
                    Effect(
                        "create_entity",
                        {
                            "id": pid,
                            "name": prof.name,
                            "role": role,
                            "location": at,
                            "home": at,
                            "stamina": 100.0,
                            "max_stamina": 100.0,
                        },
                    )
                )
            if wallet(pid) not in state.accounts:
                effects.append(Effect("create_account", {"account": wallet(pid), "balance": 0}))
            if archetype and prof.archetype and archetype != prof.archetype:
                raise Reject("identity_locked")
            if archetype and not prof.archetype:
                effects.append(
                    Effect(
                        "player_upsert",
                        {"id": pid, "name": prof.name, "archetype": archetype, "role": ARCHETYPES[archetype], "created_tick": prof.created_tick},
                    )
                )
                effects.append(Effect("set_field", {"entity": pid, "field": "role", "value": ARCHETYPES[archetype]}))
                effects.append(Effect("skill", {"entity": pid, "skill": ARCHETYPES[archetype], "delta": 0.35}))
            current = state.entities[pid].location if pid in state.entities else at
            if current != at:
                effects.append(Effect("set_field", {"entity": pid, "field": "location", "value": at}))
            # 重进只恢复在线，不改写已有住处；仅历史数据缺 home 时补当前位置。
            home = state.entities[pid].home if pid in state.entities else ""
            if not home:
                effects.append(Effect("set_field", {"entity": pid, "field": "home", "value": at}))

        effects.append(Effect("player_presence", {"id": pid, "presence": "online", "tick": tick}))
        effects.append(Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": tick}))
        return effects


class PlayerEndowmentAction(Action):
    """首次进入的一次性初始资产：显式 mint + 造物，独立事件可审计。

    幂等键是 `PlayerProfile.endowed`（由 account_id 映射的 player id 决定）：
    重复 enter / already_online / leave 后重进都不会再发。跨 run 重启是新世界，
    档案不存在即可以重新领取（见 docs/player-api.md）。
    """

    verb = "player_endowment"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["missing_player_id"]
        prof = state.players.get(pid)
        if prof is None or pid not in state.entities:
            return ["player_not_entered"]
        if prof.endowed:
            return ["endowment_already_granted"]
        if wallet(pid) not in state.accounts:
            return ["account_missing"]
        if int(state.endowment_total) + STARTING_MONEY > ENDOWMENT_TOTAL_CAP:
            return ["endowment_cap_reached"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        if not pid:
            raise Reject("missing_player_id")
        tick = int(intent.tick)
        effects: list[Effect] = [
            Effect(
                "mint",
                {
                    "account": wallet(pid),
                    "amount": STARTING_MONEY,
                    "source": RUNTIME_ENDOWMENT_SOURCE,
                    "reason": "first_entry",
                },
            )
        ]
        for item, qty in sorted(STARTING_ITEMS.items()):
            if qty > 0:
                effects.append(
                    Effect(
                        "item_create",
                        {"owner": pid, "item": item, "qty": qty, "source": RUNTIME_CARRY_SOURCE},
                    )
                )
        effects.append(
            Effect(
                "player_endow",
                {"id": pid, "amount": STARTING_MONEY, "tick": tick, "source": RUNTIME_ENDOWMENT_SOURCE},
            )
        )
        return effects


class PlayerHeartbeatAction(Action):
    verb = "player_heartbeat"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["missing_player_id"]
        prof = state.players.get(pid)
        if prof is None or pid not in state.entities:
            return ["player_not_entered"]
        if not prof.online:
            return ["player_offline"]
        if int(intent.tick) - int(prof.last_intent_tick) < 12:
            return ["heartbeat_too_soon"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        if not pid:
            raise Reject("missing_player_id")
        tick = int(intent.tick)
        return [
            Effect("player_presence", {"id": pid, "presence": "online", "tick": tick}),
            Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": tick}),
        ]


REST_HOURS_DEFAULT = 4
REST_HOURS_MAX = 8
# 体力恢复速率（每 tick）：客栈有客房，住处次之，镇内有铺面，野外再减半。
REST_RATE_INN = 2.0
REST_RATE_HOME = 1.5
REST_RATE_TOWN = 0.5
REST_RATE_WILD = 0.25


def rest_rate(state: WorldState, location: str, home: str) -> float:
    loc = state.locations.get(location) or {}
    services = loc.get("services") or []
    if "service_room" in services:
        return REST_RATE_INN
    if location and location == home:
        return REST_RATE_HOME
    if any(str(biz.location) == location for biz in state.businesses.values()):
        return REST_RATE_TOWN
    return REST_RATE_WILD


def _on_active_escort(state: WorldState, pid: str) -> bool:
    return any(
        str(order.carrier or "") == pid and order.status == "active"
        for order in state.escorts.values()
    )


def _quest_interrupts_rest(state: WorldState, pid: str, hours: int, tick: int) -> bool:
    """休息窗口跨过已接委托的截止 tick：先做完委托再休息，避免白白睡掉一单。"""
    window = int(hours) * TICKS_PER_HOUR
    for quest in state.quests.values():
        if (
            quest.acceptor == pid
            and quest.status == "accepted"
            and tick < int(quest.deadline_tick) <= tick + window
        ):
            return True
    return False


class PlayerRestAction(Action):
    """休息：按小时恢复体力，押镖中或会误掉委托截止时拒绝。"""

    verb = "rest"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["missing_player_id"]
        prof = state.players.get(pid)
        if prof is None or pid not in state.entities:
            return ["player_not_entered"]
        if not prof.online:
            return ["player_offline"]
        try:
            hours = arg_int(intent, "hours", REST_HOURS_DEFAULT, minimum=1, maximum=REST_HOURS_MAX)
        except Reject:
            return ["bad_arg:hours"]
        if _on_active_escort(state, pid):
            return ["cannot_rest_while_escorting"]
        if _quest_interrupts_rest(state, pid, hours, npc_tick_of(intent)):
            return ["cannot_rest_now"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        if not pid:
            raise Reject("missing_player_id")
        hours = arg_int(intent, "hours", REST_HOURS_DEFAULT, minimum=1, maximum=REST_HOURS_MAX)
        ent = state.entities[pid]
        rate = rest_rate(state, str(ent.location), str(ent.home or ""))
        delta = float(int(hours) * TICKS_PER_HOUR) * rate
        tick = int(intent.tick)
        return [
            Effect("stamina", {"entity": pid, "delta": delta, "reason": "rest", "hours": int(hours)}),
            Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": tick}),
        ]


class PlayerLeaveAction(Action):
    verb = "player_leave"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["missing_player_id"]
        prof = state.players.get(pid)
        if prof is None or pid not in state.entities:
            return ["player_not_entered"]
        if not prof.online:
            return ["already_offline"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        pid = _player_id(intent)
        if not pid:
            raise Reject("missing_player_id")
        tick = int(intent.tick)
        return [
            Effect("player_presence", {"id": pid, "presence": "offline", "tick": tick}),
            Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": tick}),
        ]


class PlayerPresenceAction(Action):
    """P3-C 兼容：玩家上线/在场心跳。新代码请用 player_heartbeat。"""

    verb = "player_presence"

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        pid = _player_id(intent)
        if not pid:
            return ["missing_player_id"]
        if pid not in state.entities or pid not in state.players:
            return ["player_not_entered"]
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
        effects.append(Effect("player_presence", {"id": pid, "presence": "online", "tick": tick}))
        return effects


class SystemPlayerPresenceAction(Action):
    """日界在线状态结算：保留 verb 以兼容旧事件/插件，但不再批量改玩家状态。

    API 会话的在线三态改由 `WorldSession` 按玩家自己的 wall-clock 活跃时间判定
    （见 `activity_stage`），日界不再按全局 tick 把在线玩家降级。
    """

    verb = "system_player_presence"

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        return []


class SystemTickAction(Action):
    """API 会话推进世界钟。

    日界（`args["day_boundary"]=True`）时把当日结算合并进这一条事件：
    体力回复 → 过期委托下架 → 补发。改前每个世界日额外写 5 条系统事件
    （presence/recover/quest_fail/quest_post/tick），改后只写这 1 条。
    """

    verb = "system_tick"

    def __init__(self, content=None):
        self.content = content

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        if self.content is None or not bool(intent.args.get("day_boundary")):
            return []
        # 延迟导入，避免 actions 模块之间的循环依赖。
        from ..world.effects import apply_effects
        from .quests import SystemQuestFailAction, SystemQuestPostAction
        from .system import SystemRecoverAction

        effects: list[Effect] = []
        # 用状态副本串行试算，保持“回复 → 下架 → 补发”的先后语义（补发要看到已下架的板）。
        trial = state.clone()
        for action in (
            SystemRecoverAction(self.content),
            SystemQuestFailAction(self.content),
            SystemQuestPostAction(self.content),
        ):
            part = action.apply(trial, intent, rng)
            effects.extend(part)
            apply_effects(trial, part)
        return effects


def player_status(state: WorldState, pid: str) -> dict:
    """只读视图：给 API /status 使用，不含任何写语义。"""
    ent = state.entities.get(pid)
    prof = state.players.get(pid)
    if ent is None or prof is None:
        return {}
    relations = []
    for key, rel in sorted(state.relations.items()):
        if rel.status != "active":
            continue
        if pid not in (rel.a, rel.b):
            continue
        other = rel.b if rel.a == pid else rel.a
        other_ent = state.entities.get(other)
        relations.append(
            {
                "target": other,
                "name": other_ent.name if other_ent else other,
                "kind": rel.kind,
                "affection": round(float(rel.affection), 3),
                "trust": round(float(rel.trust), 3),
            }
        )
    return {
        "id": pid,
        "name": ent.name,
        "archetype": prof.archetype,
        "role": ent.role,
        "presence": ent.presence,
        "online": prof.online,
        "location": ent.location,
        "home": ent.home,
        "stamina": round(float(ent.stamina), 3),
        "max_stamina": round(float(ent.max_stamina), 3),
        "wallet": int(state.account_balance(wallet(pid))),
        "inventory": dict(sorted(ent.inventory.items())),
        "skills": {k: round(float(v), 4) for k, v in sorted(ent.skills.items())},
        "relations": relations,
        "active_quests": sorted(
            q.id for q in state.quests.values() if q.acceptor == pid and q.status == "accepted"
        ),
    }
