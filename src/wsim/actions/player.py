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

# 在线/离线只按世界 tick 判定：1 tick = 10 游戏分钟，144 tick = 1 游戏日。
# 36 tick = 6 游戏小时 = 1/4 日；活世界 tick_step=6 秒时约 216 秒（3.6 分钟）
# 无任何请求即视为断线。禁止墙钟，保证同 seed 可复现。
PRESENCE_TIMEOUT_TICKS = 36
# 战斗中的玩家按 tick 结算回合，等 36 tick 足够把人打死；18 tick = 3 游戏小时
# （活世界约 108 秒）仍大于心跳冷却 12 tick，正常客户端不会被误踢。
COMBAT_PRESENCE_TIMEOUT_TICKS = 18

# 初始资产的唯一事实来源：genesis 预置与首次进入发放共用。
STARTING_MONEY = 300
STARTING_ITEMS: dict[str, int] = {"flatbread": 3, "coarse_cloth": 1}

# ---- 玩家创世气血与属性 ----
# 来源区分（必须和内容包数值分开看）：
# - max_hp=100 是**原版**数值：logind.lpc:917-925 新玩家 max_qi=eff_qi=100；
#   race/human.lpc:116 在 age<=14 时 max_qi=max_jing=100；clone/user/user.lpc:59
#   初始 age=14。所以 100 不是我们编的。
# - 六维属性是**我们的默认**：xkx 登录时由玩家掷点决定，不可确定复刻；这里按身份
#   给一组固定值，保证新角色一定有属性、战斗可结算。
# - 初始武功/经验是**产品档位选择**，不是原版新手档：原版新手 combat_exp≈0、技能近 0，
#   本仓库保留其原始实测（reports/xkx-ref/hit-rate.md 的 NEW 档）作“新手体验”对照。
#   这里的 MID 档取原版“40 级武艺”的最低合法经验：40^3/10 = 6400
#   （cmds/skill/learn.lpc:102；practice/study 同门槛）。unarmed/dodge/parry 同级 40，
#   保证攻防两侧同一档位，不会出现“拳脚 40 级却只有 250 经验”的不自洽状态。
#   世界配置 `player_start` 可覆盖这三项。
PLAYER_MAX_HP = 100
PLAYER_START_COMBAT_EXP = 6400
PLAYER_START_SKILL_LEVELS: dict[str, int] = {"unarmed": 40, "dodge": 40, "parry": 40}
DEFAULT_ARCHETYPE = "游侠"
ARCHETYPE_ATTRS: dict[str, dict[str, int]] = {
    # 膂力 / 根骨 / 身法 / 悟性 / 精神 / 耐力
    # kar(福缘) / per(容貌)：原版 logind 用 `10 + random(21)` 各取一次；
    # 复刻改为确定性定值 20/20（大狼狗 `random(kar+per)` 判定的输入）。
    # cps(定力) / cor(胆识) 不写进创世属性：原版 human.lpc:67-68 是 `10+random(21)`，
    # 复刻由战斗层按 MID 快照实测 23/24 兜底（见 combat_rules.PLAYER_MID_CPS/COR），
    # 这样关闭出手门时旧世界的状态哈希逐字节不变。
    "游侠": {"str": 20, "con": 20, "dex": 18, "int": 15, "spi": 15, "sta": 20, "kar": 20, "per": 20},
    "商人": {"str": 15, "con": 16, "dex": 15, "int": 20, "spi": 18, "sta": 18, "kar": 20, "per": 20},
    "捕快": {"str": 18, "con": 18, "dex": 17, "int": 17, "spi": 16, "sta": 19, "kar": 20, "per": 20},
}


def _start_values(start: dict | None) -> tuple[int, dict[str, int]]:
    """把世界配置的 `player_start` 归一成 (combat_exp, skill_levels)。

    配置是外部输入：只接受非负整数，非法项静默回落默认值，不在这里抛异常。
    """
    combat_exp = PLAYER_START_COMBAT_EXP
    skills = dict(PLAYER_START_SKILL_LEVELS)
    if not isinstance(start, dict):
        return combat_exp, skills

    raw_exp = start.get("combat_exp")
    if isinstance(raw_exp, int) and not isinstance(raw_exp, bool) and raw_exp >= 0:
        combat_exp = raw_exp

    raw_skills = start.get("skills")
    if isinstance(raw_skills, dict):
        for key, value in raw_skills.items():
            if not isinstance(key, str) or not key:
                continue
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                skills[key] = value
    return combat_exp, skills


def _player_vitals(archetype: str = "", start: dict | None = None) -> dict:
    """确定性玩家气血/属性/初始武功；未知身份回落到游侠。

    只由玩家创世路径调用；是否启用由内容包门控（见 `content_has_combat_data`）。
    `start` 来自世界配置 `player_start`，缺省/非法项回落到模块默认（MID 档）。
    """
    attrs = ARCHETYPE_ATTRS.get(archetype or DEFAULT_ARCHETYPE, ARCHETYPE_ATTRS[DEFAULT_ARCHETYPE])
    combat_exp, skill_levels = _start_values(start)
    return {
        "max_hp": PLAYER_MAX_HP,
        "hp": PLAYER_MAX_HP,
        "attrs": dict(attrs),
        "combat_exp": combat_exp,
        "skill_levels": skill_levels,
        # 原版 logind.lpc 新玩家 potential = 99，learned_points 从 0 起。
        "potential": 99,
        "learned_points": 0,
        # 原版人类 `combat_action` 兜底招式（`adm/daemons/race/human.lpc`）。
        # 只由带战斗数据的内容包启用；旧九地点玩家创世不带这些键。
        "race": "人类",
    }


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
        "wear",
        "wield",
        "remove",
        "unwield",
        "drop",
        "sleep",
        # 战斗：一次动作原子结算最多 20 回合，只写 1 条事件（rounds[] 战报）。
        "fight",
        # 取物：loot 与 get 是同一动作的两个 verb，取当前房间地面掉落。
        "loot",
        "get",
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


def _in_live_combat(state: WorldState, pid: str) -> bool:
    """玩家是否正在与同房间、存活的 NPC 交战（多敌表或主目标任一命中）。

    只看世界状态，不耗 RNG、不读墙钟；用于战斗态的短超时阈值。
    """
    ent = state.entities.get(pid)
    if ent is None:
        return False
    for eid, other in state.entities.items():
        if eid.startswith("player:") or other.status != "alive":
            continue
        if other.location != ent.location:
            continue
        if str(other.combat_target or "") == pid or pid in (other.combat_enemies or []):
            return True
    target = str(ent.combat_target or "")
    opponent = state.entities.get(target) if target and not target.startswith("player:") else None
    if opponent is not None and opponent.status == "alive" and opponent.location == ent.location:
        return True
    return False


def _in_transit(state: WorldState, pid: str) -> bool:
    """是否有一单正在履约的押镖：在途玩家由世界钟推进路程，不算断线。

    卸货/失败后订单不再是 active，下一 tick 恢复 36 tick 判定。
    """
    for order in state.escorts.values():
        if order.status == "active" and order.carrier == pid:
            return True
    return False


def presence_timeout_pids(state: WorldState, tick: int, timeout: int = PRESENCE_TIMEOUT_TICKS) -> list[str]:
    """返回本 tick 应置为 offline 的玩家 id（确定性、按 id 排序）。

    判定只读 `profile.last_intent_tick`（由每次成功意图/心跳刷新）；
    `Entity.last_seen_tick` 只是投影，离线 effect 会把它写成当前 tick，不能用来判定。
    战斗中的玩家使用更短的阈值，到点立即脱战（见 SystemPlayerPresenceAction）。
    """
    due: list[str] = []
    now = int(tick)
    for pid in sorted(state.players):
        prof = state.players[pid]
        if not prof.online or pid not in state.entities:
            continue
        if _in_transit(state, pid):
            continue
        limit = COMBAT_PRESENCE_TIMEOUT_TICKS if _in_live_combat(state, pid) else int(timeout)
        if now - int(prof.last_intent_tick) < limit:
            continue
        due.append(pid)
    return due


def combat_clear_effects(state: WorldState, pids: list[str]) -> list[Effect]:
    """把给定玩家从所有实体的交战登记中摘掉，并清其自身战斗字段。

    确定性：玩家 id 按入参顺序（调用方传 sorted），NPC 按 id 排序，不耗随机。
    离线/离场后 NPC 不得再对这些人结算伤害。
    """
    effects: list[Effect] = []
    for pid in pids:
        ent = state.entities.get(pid)
        if ent is None:
            continue
        if ent.combat_target:
            effects.append(Effect("set_field", {"entity": pid, "field": "combat_target", "value": ""}))
        if ent.combat_enemies:
            effects.append(Effect("set_field", {"entity": pid, "field": "combat_enemies", "value": []}))
    for eid in sorted(state.entities):
        npc = state.entities[eid]
        if eid.startswith("player:"):
            continue
        enemies = list(npc.combat_enemies or [])
        remaining = [e for e in enemies if e not in pids]
        if remaining != enemies:
            effects.append(
                Effect("set_field", {"entity": eid, "field": "combat_enemies", "value": remaining})
            )
        if str(npc.combat_target or "") in pids:
            effects.append(Effect("set_field", {"entity": eid, "field": "combat_target", "value": ""}))
    return effects


def player_genesis_effects(
    pid: str,
    name: str,
    at: str,
    money: int = STARTING_MONEY,
    items: dict[str, int] | None = None,
    start: dict | None = None,
) -> list[Effect]:
    """创世预置：允许铸币/造物的玩家入口之一（另一条是首次进入发放）。"""
    items = items if items is not None else dict(STARTING_ITEMS)
    entity_data = {
        "id": pid,
        "name": name,
        "role": "visitor",
        "location": at,
        "home": at,
        "stamina": 100.0,
        "max_stamina": 100.0,
    }
    # 预置账号没有身份参数，按默认身份（游侠）落地气血与属性。
    entity_data.update(_player_vitals(DEFAULT_ARCHETYPE, start))
    effects: list[Effect] = [
        Effect("create_entity", entity_data),
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


def content_has_combat_data(content) -> bool:
    """内容包是否带战斗数据（xkx 切片有 vitals/attrs/skills，legacy 没有）。

    玩家创世气血只在带战斗数据的世界上生效：旧 `content/` 世界（九地点）的
    玩家创世事件与哈希因此逐字节不变。
    """
    return any(
        any(key in npc for key in ("vitals", "attrs", "skills"))
        for npc in (getattr(content, "npcs", None) or [])
    )


class PlayerEnterAction(Action):
    verb = "player_enter"

    def __init__(self, content=None) -> None:
        self.content = content

    def _vitals(self, archetype: str, start: dict | None = None) -> dict:
        # content=None（单测/旧调用）按开启处理，避免新角色无血。
        if self.content is not None and not content_has_combat_data(self.content):
            return {}
        return _player_vitals(archetype, start)

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
        start = intent.args.get("player_start")
        if not isinstance(start, dict):
            start = None
        prof = state.players.get(pid)
        effects: list[Effect] = []

        if prof is None:
            display = display or PLAYER_NAME
            archetype = archetype or "游侠"
            role = ARCHETYPES[archetype]
            if pid not in state.entities:
                data = {
                    "id": pid,
                    "name": display,
                    "role": role,
                    "location": at,
                    "home": at,
                    "stamina": 100.0,
                    "max_stamina": 100.0,
                }
                data.update(self._vitals(archetype, start))
                effects.append(Effect("create_entity", data))
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
                data = {
                    "id": pid,
                    "name": prof.name,
                    "role": role,
                    "location": at,
                    "home": at,
                    "stamina": 100.0,
                    "max_stamina": 100.0,
                }
                data.update(self._vitals(archetype or prof.archetype, start))
                effects.append(Effect("create_entity", data))
            elif int(state.entities[pid].max_hp) <= 0:
                # 早于本次改动创建的旧玩家实体没有气血：进入时补一次，
                # 保证 8011 上的既有存档也不会“没有血量”。
                backfill = self._vitals(archetype or prof.archetype, start)
                if backfill:
                    effects.append(Effect("vitals_set", {"entity": pid, **backfill}))
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
    """休息/睡觉：按小时恢复体力与气血，押镖中或会误掉委托截止时拒绝。

    `rest` 按地点系数逐 tick 恢复；`sleep` 沿用原版睡醒口径，回满 hp 与 stamina。
    """

    verb = "rest"
    aliases = ("sleep",)

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
        tick = int(intent.tick)
        if str(intent.verb) == "sleep":
            stamina_delta = max(0.0, float(ent.max_stamina) - float(ent.stamina))
            hp_delta = max(0, int(ent.max_hp) - int(ent.hp)) if int(ent.max_hp) > 0 else 0
            reason = "sleep"
        else:
            rate = rest_rate(state, str(ent.location), str(ent.home or ""))
            delta = float(int(hours) * TICKS_PER_HOUR) * rate
            stamina_delta = delta
            hp_delta = int(delta) if int(ent.max_hp) > 0 else 0
            reason = "rest"
        effects: list[Effect] = [
            Effect("stamina", {"entity": pid, "delta": stamina_delta, "reason": reason, "hours": int(hours)}),
        ]
        if hp_delta > 0:
            effects.append(Effect("hp_add", {"entity": pid, "delta": hp_delta, "reason": reason}))
        effects.append(Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": tick}))
        return effects


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
        data: dict = {"id": pid, "presence": "offline", "tick": tick}
        for key in ("reason", "operator", "note"):
            value = intent.args.get(key)
            if value:
                data[key] = str(value)
        return [
            Effect("player_presence", data),
            Effect("set_field", {"entity": pid, "field": "last_action_tick", "value": tick}),
            *combat_clear_effects(state, [pid]),
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
    """tick 超时结算：把超过阈值无请求的玩家置 offline，并立即脱战。

    判定只读 `profile.last_intent_tick` 与世界 tick，不读墙钟；同 seed 可复现。
    同一 tick 重复调用时 due 为空，天然幂等（调度与 API 推进会双路径触发）。
    """

    verb = "system_player_presence"

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        tick = int(intent.tick)
        due = presence_timeout_pids(state, tick)
        if not due:
            return []
        effects: list[Effect] = []
        for pid in due:
            effects.append(
                Effect(
                    "player_presence",
                    {
                        "id": pid,
                        "presence": "offline",
                        "tick": tick,
                        "reason": "presence_idle_timeout",
                        "source": "system",
                    },
                )
            )
        # 双方战斗字段一次清空：离线目标不许再被结算伤害。
        effects.extend(combat_clear_effects(state, due))
        return effects


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
        "region": (state.locations.get(ent.location) or {}).get("region"),
        "region_name": (state.locations.get(ent.location) or {}).get("region_name"),
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
