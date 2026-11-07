"""玩家会话：把 HTTP JSON 命令变成 propose_intent → MCP → Writer 的单写者事务。

- 客户端只能给 account_id；服务端映射为 `player:<account_id>`，不接受客户端 actor。
- 客户端 tick 一律忽略，使用当前 `state.tick`；每个动作后由 system_tick 推进 6 tick。
- 写操作没有任何直连 Writer 的旁路：propose 入 outbox，admin 两段审批后 apply。
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import weakref
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from ..actions.growth import (
    BaiAction,
    ClimbAction,
    JumpAction,
    LearnAction,
    PracticeAction,
    ReviveAction,
)
from ..actions.player import (
    ACCOUNT_RE,
    ARCHETYPES,
    CARRY_SOURCE,
    ENDOWMENT_SOURCE,
    ENDOWMENT_TOTAL_CAP,
    PLAYER_VERBS,
    PRESENCE_ACTIVE_SECONDS,
    PRESENCE_OFFLINE_SECONDS,
    RUNTIME_CARRY_SOURCE,
    RUNTIME_ENDOWMENT_SOURCE,
    STARTING_ITEMS,
    STARTING_MONEY,
    PlayerRestAction,
    SystemTickAction,
    activity_stage,
    player_genesis_effects,
    player_status,
)
from ..actions.player_sect import (
    ARREARS_EXPEL,
    DISCIPLE_CAP,
    DUES_DAILY,
    STIPEND_DAILY,
    PlayerExpelDiscipleAction,
    PlayerFoundSectAction,
    PlayerPayDisciplesAction,
    PlayerRecruitDiscipleAction,
    PlayerSectDepositAction,
    PlayerSectWithdrawAction,
    _bond,
    active_disciples,
)
from ..core.clock import TICKS_PER_DAY, TICKS_PER_HOUR, day_of
from ..core.content import load_content
from ..core.models import Intent
from ..agent import dialogue_runtime
from ..agent.dialogue_aside import DialogueAside
from ..mcp.gateway import MCPGateway, GatewayError
from ..sim.aggression import aggression_rules, rule_for
from ..sim.genesis import build_genesis
from ..sim.scheduler import AmbientConfig, AmbientScheduler
from ..world.state import WorldState
from ..world.store import EventStore
from ..world.writer import Writer
from ..actions.escort import resolve_payer, route_meta
from ..actions.business import daily_shop_tax
from ..actions.equipment import SLOT_LABELS, equip_cultivate_bonus, slot_for_item
from ..actions.registry import build_registry
from ..actions.common import (
    business_selling,
    business_with_cash,
    cheapest_food,
    path_between,
    quest_required_units,
    reputation_points,
    sell_price,
    transaction_tax,
    wallet as wallet_account,
)
from .event_tail import EventTail
from .growth import growth_view, today_view
from .persistence import replay_into, verify_content_hash
from .xkx_content import is_xkx_pack, load_xkx_content

# 契约冻结版本：字段名/结构变更必须同步 docs/player-api.md 的 CHANGELOG。
PROFILE_VERSION = 1

# 内容包 equip_slot → 展示名。引擎当前只实现了 tool/accessory 两槽，
# 因此 xkx 槽位只做只读展示并明确标注“引擎未接”，不冒充可穿脱。
XKX_EQUIP_SLOT_LABELS: dict[str, str] = {
    "head": "头部",
    "neck": "颈部",
    "cloth": "衣服",
    "waist": "腰带",
    "boots": "靴子",
    "finger": "手指",
    "sword": "剑",
    "blade": "刀",
    "whip": "鞭",
    "staff": "杖",
    "hammer": "锤",
    "club": "棍",
    "throwing": "暗器",
}

# 人物面板属性顺序与中文名。只做展示映射，值一律来自 Entity.attrs。
ATTR_LABELS: tuple[tuple[str, str], ...] = (
    ("str", "膂力"),
    ("con", "根骨"),
    ("dex", "身法"),
    ("int", "悟性"),
    ("spi", "精神"),
    ("sta", "耐力"),
    ("kar", "福缘"),
    ("cps", "容貌"),
    ("cor", "胆识"),
)

# 服务模式下匿名读的唯一可见字段（白名单）。新增字段默认不可见，
# 避免私有字段因为“忘记排除”自动泄漏。
PUBLIC_STATE_KEYS = (
    "ok",
    "name",
    "place",
    "activity",
    "idle_seconds",
    "online",
    "sect_name",
    "shop_name",
    "server_tick",
)

WRITE_ACTIONS: dict[str, str] = {
    "enter": "player_enter",
    "heartbeat": "player_heartbeat",
    "leave": "player_leave",
    "move": "move",
    "talk": "talk",
    "trade": "trade",
    "quest/accept": "quest_accept",
    "quest/deliver": "quest_deliver",
    "cultivate": "cultivate",
    "work": "work",
    "open_shop": "open_shop",
    # 店铺经营：补货 / 定价 / 辞退 / 提现 / 看账。
    "restock": "restock",
    "set_price": "set_price",
    "dismiss": "dismiss",
    "withdraw": "withdraw",
    "shop_report": "shop_report",
    "join_sect": "join_sect",
    # 店铺雇佣：参数与 RecruitAction 一致（target/position/wage，shop 缺省本人店铺）。
    "recruit": "recruit",
    # 消费类动作：复用 BuyAction / SellAction / ConsumeAction / EscortAction。
    "buy": "buy",
    "sell": "sell",
    "consume": "consume",
    "escort": "escort",
    # 休息：按小时恢复体力（客栈/住处/镇内/野外分级）。
    "rest": "rest",
    # 统一动作入口使用的下划线别名（与 /api/player/quest/accept 等价）。
    "quest_accept": "quest_accept",
    "quest_deliver": "quest_deliver",
    # 玩家婚姻：求婚 / 和离。NPC 决策事件由服务端自动追加，不对客户端开放。
    "marriage_propose": "marriage_propose",
    "marriage/propose": "marriage_propose",
    "marriage_end": "marriage_end",
    "marriage/end": "marriage_end",
    # 玩家门派：立派 / 收徒 / 逐出 / 日结 / 金库存取。
    "found_sect": "found_sect",
    "recruit_disciple": "recruit_disciple",
    "expel_disciple": "expel_disciple",
    "pay_disciples": "pay_disciples",
    "sect_deposit": "sect_deposit",
    "sect_withdraw": "sect_withdraw",
    # 装备与物品使用：槽位/已装备物品入世界状态；use_item 消耗食物/药品恢复体力。
    "equip": "equip",
    "unequip": "unequip",
    "use_item": "use_item",
    "wear": "wear",
    "wield": "wield",
    "remove": "remove",
    "unwield": "unwield",
    "drop": "drop",
    "sleep": "sleep",
    # xkx 文字壳：战斗动词由 actions 层提供，API 只在动作已注册时放行。
    "fight": "fight",
    "loot": "loot",
    "flee": "flee",
    # 成长线与生死：拜师 / 学习 / 练习 / 攀爬 / 跳跃 / 还阳。
    "bai": "bai",
    "learn": "learn",
    "practice": "practice",
    "climb": "climb",
    "jump": "jump",
    "revive": "revive",
}

# 门派写动作：响应统一附带最新私有快照（details.sect，与 player.sect 同源）。
_SECT_ACTIONS = frozenset(
    {
        "found_sect",
        "recruit_disciple",
        "expel_disciple",
        "pay_disciples",
        "sect_deposit",
        "sect_withdraw",
    }
)

# /api/player/action 的动词表：只接受客户端动词名，服务端映射为内部 action。
ACTION_ALIASES: dict[str, str] = {
    "enter": "enter",
    "player_enter": "enter",
    "leave": "leave",
    "player_leave": "leave",
    "heartbeat": "heartbeat",
    "player_heartbeat": "heartbeat",
    "move": "move",
    "talk": "talk",
    "trade": "trade",
    "cultivate": "cultivate",
    "work": "work",
    "open_shop": "open_shop",
    "restock": "restock",
    "set_price": "set_price",
    "dismiss": "dismiss",
    "withdraw": "withdraw",
    "shop_report": "shop_report",
    "join_sect": "join_sect",
    "recruit": "recruit",
    "quest_accept": "quest/accept",
    "quest/accept": "quest/accept",
    "quest_deliver": "quest/deliver",
    "quest/deliver": "quest/deliver",
    "buy": "buy",
    "sell": "sell",
    "consume": "consume",
    "escort": "escort",
    "rest": "rest",
    "marriage_propose": "marriage_propose",
    "marriage/propose": "marriage_propose",
    "marriage_end": "marriage_end",
    "marriage/end": "marriage_end",
    "found_sect": "found_sect",
    "recruit_disciple": "recruit_disciple",
    "expel_disciple": "expel_disciple",
    "pay_disciples": "pay_disciples",
    "sect_deposit": "sect_deposit",
    "sect_withdraw": "sect_withdraw",
    "equip": "equip",
    "unequip": "unequip",
    "use_item": "use_item",
    "wear": "wear",
    "wield": "wield",
    "remove": "remove",
    "unwield": "unwield",
    "drop": "drop",
    "sleep": "sleep",
    "fight": "fight",
    "loot": "loot",
    "flee": "flee",
    "bai": "bai",
    "learn": "learn",
    "practice": "practice",
    "climb": "climb",
    "jump": "jump",
    "revive": "revive",
}

# NPC 专属动作：玩家请求必须得到明确的“未开放”，而不是未知操作。
PLAYER_UNIMPLEMENTED_VERBS = frozenset({"marry", "apprentice"})

FORBIDDEN_VERBS = frozenset(
    {"rob", "attack", "kill", "marry_force", "player_expel", "item_create", "mint", "set_field"}
)

# xkx 文字壳可用的战斗动词：仅在 actions/ 层真正注册了同名动作时放行，
# 避免 API 层提前把未实现的动词暴露给玩家。
XKX_EXTRA_PLAYER_VERBS = frozenset(
    {"fight", "loot", "flee", "bai", "learn", "practice", "climb", "jump", "revive"}
)

# `/api/world/feed` 的固定上限与“重要事件”动词白名单（公共信息，不含玩家私有数据）。
WORLD_FEED_LIMIT_MAX = 50
WORLD_FEED_IMPORTANT_VERBS = frozenset(
    {
        "marry",
        "open_shop",
        "found_sect",
        "join_sect",
        "apprentice",
        "bai",
        "revive",
        "recruit",
        "escort",
        "quest_deliver",
        "breakthrough",
    }
)

REASON_TEXT: dict[str, str] = {
    "missing_account": "缺少账号；请先创建账号再进入世界。",
    "bad_account": "账号格式不合法（3-32 位小写字母、数字、下划线或连字符）。",
    "unknown_action": "未知操作；可用操作见 docs/player-api.md。",
    "forbidden_verb": "该操作在本阶段不开放（伤害/抢掠/强迫婚配等不可逆动作被禁用）。",
    "player_not_entered": "你还没有进入世界；请先调用 /api/player/enter。",
    "player_offline": "你已离线；请重新进入世界后再操作。",
    "already_offline": "你已经是离线状态。",
    "already_online": "你已经在线，无需重复进入。",
    "identity_locked": "身份进入后不可更改；请用原身份重新登录。",
    "bad_archetype": "身份必须是游侠、商人或捕快。",
    "bad_display_name": "角色名需 2-12 字，且不能与现有角色重名。",
    "bad_location": "地点不存在。",
    "unknown_location": "地点不存在；请从当前地点可用的出口中选择。",
    "not_adjacent": "目标地点与当前位置不相邻；请沿道路逐段移动。",
    "insufficient_stamina": "体力不足；请先休息或进食。",
    "unknown_target": "目标人物不存在。",
    "not_together": "对方不在你当前所在的地点。",
    "target_offline": "对方不在线，不能进行该操作。",
    "player_consent_required": "该操作需要对方本人同意；本阶段关闭 NPC 对玩家的婚配与招揽。",
    "bad_message": "消息不能为空且不超过 200 字。",
    "heartbeat_too_soon": "心跳过于频繁；请稍后再试。",
    "stale_tick": "你的请求使用了过期时间点；请刷新状态后重试。",
    "unknown_verb": "动作未注册。",
    "quest_not_found": "任务不存在。",
    "quest_not_open": "任务已被接走或已结束。",
    "giver_missing": "任务发布人已不在世界。",
    "not_at_giver": "你不在任务发布人所在的地点。",
    "busy_on_escort": "你正在押镖，不能同时接单。",
    "too_many_quests": "最多同时进行 2 个任务；请先交付其中一个。",
    "quest_not_accepted_by_actor": "这个任务不是由你接下的。",
    "quest_expired": "任务已超过截止时间。",
    "reputation_too_low": "声望不足；先完成委托或经营人情，达到这一单的门槛后再接。",
    "not_at_target": "你尚未到达任务目标地点或发布人处。",
    "quest_item_missing": "采购类任务需要你持有目标物品。",
    "quest_step_incomplete": "你还没有真正完成这单的目标动作；请先到指定地点上工/交易/买入或把镖单送到目标地点，再回来交付。",
    "item_not_equippable": "这件物品不能装备。",
    "item_not_wearable": "这件物品不能穿戴。",
    "item_not_weapon": "这件物品不是兵器。",
    "weapon_already_wielded": "你双手已经持有兵器；先放下再换。",
    "item_not_equipped": "你没有装备这件物品。",
    "not_worn_use_unwield": "兵器要用 unwield 放下，remove 只脱护具。",
    "not_wielded": "你手里没有兵器。",
    "item_equipped": "这件物品正装备在身上，先脱下再丢。",
    "player_required": "该操作需要玩家身份。",
    "player_dead": "你已经倒下，无法行动。",
    "loot_not_found": "这里地上没有你要找的东西。",
    "equipment_slot_empty": "该装备槽位没有已装备物品。",
    "unknown_equipment_slot": "未知的装备槽位。",
    "item_not_usable": "这件物品不能直接使用；只有食物、酒水和药品可用。",
    "bad_quantity": "数量不合法。",
    "trade_unfair": "交易价值明显不对等；对方拒绝了这笔买卖。",
    "service_item": "服务类物品不能用于玩家交易。",
    "offer_not_held": "你给出的物品数量不足。",
    "want_not_held": "对方没有你想要的物品。",
    "insufficient_money": "你的钱不够。",
    "target_insufficient_money": "对方的钱不够支付这笔差额。",
    "hostile": "对方对你敌对，拒绝交易。",
    "bad_arg:money_delta": "金额参数必须是整数。",
    # 玩家消费类动作的专属原因码（与 NPC 引擎码分开，语义见 docs/player-api.md）。
    "not_enough_money": "你的钱不够完成当前操作。",
    "item_not_available": "这件物品不在当前内容包里。",
    "item_missing": "你背包里没有足够的这件物品。",
    "not_here": "你不在这个交易地点。",
    "shop_closed": "这里没有店铺，不能买卖。",
    "not_a_merchant": "这里的店铺不做这件货的买卖。",
    "not_implemented_for_player": "这个玩法还没对玩家开放。",
    # 押镖参数与前置。
    "not_at_origin": "你不在镖单的起点。",
    "route_not_adjacent": "押镖路线不连续；只能沿相邻地点逐段走。",
    "already_on_escort": "你正在押镖，不能同时接第二单。",
    "cargo_too_low": "货值低于这条线路的最低承运要求。",
    "cargo_too_high": "货值超过这条线路的最高承运额度。",
    "fee_too_low": "镖银低于这条线路的最低标准。",
    "fee_too_high": "镖银超过这条线路的最高标准。",
    "no_escort_agency": "起点没有镖局，不能接镖。",
    "agency_insufficient_cash": "镖局现金不足以支付这单镖银。",
    "no_payer_funds": "目的地委托方现金不足，无法支付这单镖银；请换一条线路或降低镖银。",
    "bad_arg:route": "路线格式错误；请给地点 id 列表或 route_id。",
    "bad_arg:hours": "休息时长必须是 1-8 的整数（小时）。",
    "cannot_rest_while_escorting": "你正在押镖行程中，不能休息；先送达或等镖单收口。",
    "cannot_rest_now": "休息会错过已接委托的截止时间；先交付委托再休息。",
    "bad_arg:cargo_qty": "货物数量必须是 1-100 的整数。",
    "bad_arg:cargo_value": "货值必须是整数。",
    "bad_arg:fee": "镖银必须是整数。",
    "bad_arg:item": "物品参数不合法。",
    "bad_arg:qty": "数量参数不合法（1-20 的整数）。",
    "bad_arg:from": "交易地点参数不合法。",
    "bad_arg:to": "交易地点参数不合法。",
    "role_not_allowed": "你的身份不允许该操作。",
    "player_cap_reached": "本区域玩家席位已满，暂时无法创建新账号。",
    "endowment_already_granted": "该账号已经领取过初始资产，不能重复领取。",
    "endowment_cap_reached": "本区域初始资产发放总额已达上限；请联系运维。",
    # 玩家婚姻
    "already_married": "你已有配偶；和离后才能再谈婚嫁。",
    "target_in_couple": "对方已有婚约或配偶。",
    "spouse_not_here": "你的配偶不在当前地点；和离需当面说清。",
    "bond_too_low": "你们的好感还不够；先多相处、多说话再提亲。",
    "trust_too_low": "对方对你还不够信任；先相处一段时间、建立信任再试。",
    "gift_missing": "你指定的信物不在背包里或数量不足。",
    "self_propose": "不能向自己求婚。",
    "not_married": "你当前没有配偶，无需和离。",
    "spouse_not_alive": "你的配偶已不在人世。",
    "target_not_npc": "本轮只开放玩家向 NPC 求婚。",
    "target_not_player": "配偶目标必须是玩家。",
    "spouse_offline": "配偶不在线，今日的陪伴暂缓。",
    "unknown_actor": "发起人不存在或已不在世界。",
    "account_missing": "玩家钱包账户不存在，已被回滚。",
    "effect_invalid": "操作违反世界规则，已被回滚。",
    "bad_args": "参数不完整或格式错误。",
    # 玩家门派（契约见 docs/player-api.md §13）
    "no_sect": "你还没有加入任何门派；先立派或拜入一个门派。",
    "already_in_sect": "你已经在门派中，不能再立派；退出后才能另立门户。",
    "not_sect_leader": "只有掌门（立派者）能做这件事。",
    "sect_full": "收徒名额已满（上限 3 名）；先驱逐或等弟子离派。",
    "insufficient_rep": "声望不足；立派与收徒都要求声望至少 20。",
    "insufficient_funds": "钱不够完成这次门派操作（立派需 800 文）。",
    "disciple_not_found": "门派名册里没有这名弟子。",
    "favor_too_low": "对方对你的好感不足 30，不愿拜师。",
    "arrears_too_high": "欠饷累计已达 30 文，弟子自动离派。",
    "sect_not_found": "门派不存在或不是你创建的门派。",
    "sect_name_taken": "这个名号已被别的门派用了。",
    "sect_exists": "这个门派 id 已存在。",
    "invalid_sect_name": "门派名号需 2–12 字。",
    "wrong_location": "你必须身处门派驻地才能做这件事。",
    "no_disciples": "门中暂无弟子，无需日结。",
    "already_settled_today": "今日门派已日结过了，明日再来。",
    "insufficient_treasury": "门派金库余额不足。",
    "withdraw_over_cap": "单次最多提取 500 文。",
    "treasury_reserve": "提取后金库须留 100 文保底。",
    "target_not_disciple": "对方不是本门弟子。",
    "target_in_sect": "对方已属别的门派。",
    "target_is_player": "不能直接收玩家为徒；需玩家本人同意。",
    "already_disciple": "对方已在本门名下。",
    "skill_too_low": "对方根基太浅，或你的本事还不足以服众。",
    "target_not_alive": "对方已不在人世。",
    "target_not_found": "找不到这个人物。",
    "self_recruit": "不能收自己为徒。",
    "actor_not_alive": "你已不在人世，无法再做这件事。",
    "not_together": "你与对方不在同一地点，收徒要当面。",
    "bad_arg:sect_id": "门派 id 格式不合法。",
    "bad_arg:amount": "金额必须是 1–10000 的整数。",
    "bad_arg:name": "门派名号需 2–12 字。",
    "bad_arg:day": "结算日必须是非负整数。",
    # 成长线与生死
    "master_not_here": "这里没有你说的这位师父。",
    "master_ambiguous": "这里有不止一个人符合这个称呼，请说清楚。",
    "master_dead": "这位师父已经不在人世。",
    "not_a_master": "对方不是可以拜师的人物。",
    "already_apprentice": "你已经是这位师父的弟子了。",
    "already_has_master": "你已经另有师父；原版不允许随意改投。",
    "already_in_sect": "你已经身在门派之中。",
    "self_target": "不能拜自己为师。",
    "in_ghost_gate": "你身处鬼门关，做不得阳间的事。",
    "unknown_skill": "没有这门技能。",
    "not_apprentice_of_teacher": "你与这位师父没有师承关系，他不能教你。",
    "teacher_lacks_skill": "这位师父不会这门技能。",
    "skill_not_lower": "你的这门技能已经不低于师父，学不到新东西了。",
    "master_wont_teach": "技能已到 250 级，师父无法再教。",
    "insufficient_combat_exp": "你的实战经验不足以驾驭这门技能。",
    "insufficient_potential": "潜能不足：需要潜能减去已学点数至少够付这次次数。",
    "insufficient_jing": "精力不足，支撑不了这么多次学习。",
    "skill_not_learned": "你还没有学会这门技能，无法自行练习。",
    "basic_skill_missing": "你还没有对应的基本功，无法练习这门技能。",
    "practice_ratio_low": "基本功相对这门技能还太浅，练不上去。",
    "insufficient_stamina": "体力不足 30，练不动了。",
    "climb_wrong_place": "这里没有可以攀爬的树。",
    "climb_target_unknown": "原版这里只能「climb tree」。",
    "dodge_too_low": "你的轻功还不到 50 级，爬不上去。",
    "jump_wrong_place": "这里不是跳木桩的地方。",
    "jump_target_unknown": "你要跳向什么？原版这里只有木桩。",
    "not_dead": "你还活着，不必还阳。",
    "not_in_ghost_gate": "你不在鬼门关，无法还阳。",
    "no_revival_location": "找不到可以还阳的地点。",
}


def _reason_text(code: str) -> str:
    if not code:
        return ""
    if code.startswith("overloaded:"):
        parts = code.split(":")
        if len(parts) == 3:
            return f"负重超限：需要 {parts[1]}，上限 {parts[2]}；请先丢下物品或分批携带。"
    if code.startswith("slot_occupied:"):
        slot = code.split(":", 1)[1]
        return f"{SLOT_LABELS.get(slot, slot)}槽位已经有装备了；先脱下再换。"
    if code.startswith("bad_arg:"):
        return REASON_TEXT.get(code) or f"参数不合法：{code.split(':', 1)[1]}。"
    return REASON_TEXT.get(code) or f"操作未完成：{code}（详见 docs/player-api.md 错误码表）。"


class WorldSession:
    """单进程、单写者的玩家世界。HTTP 层只调用 `perform` / 只读视图。"""

    def __init__(
        self,
        root: Path,
        run_id: str = "p3e",
        seed: int = 42,
        npc_limit: int = 0,
        player_id: str = "player:u_demo",
        display_name: str = "陈捕快",
        spawn: str = "market",
        endow: bool = True,
        post_quests: bool = True,
        tick_step: int = 6,
        seed_accounts: list[str] | None = None,
        run_dir: Path | str | None = None,
        presence_clock: Callable[[], float] | None = None,
        presence_active_seconds: float = PRESENCE_ACTIVE_SECONDS,
        presence_offline_seconds: float = PRESENCE_OFFLINE_SECONDS,
        content_root: Path | str | None = None,
        world_config: Path | str | None = None,
    ):
        self.root = Path(root)
        self.content_root = Path(content_root) if content_root else self.root
        self.world_config_path = Path(world_config) if world_config else None
        # 世界配置里唯一的游走开关；缺省/缺文件/解析失败一律视为关闭。
        self.npc_wander_enabled = self._load_npc_wander(self.world_config_path)
        # 开局档位（combat_exp / 初始技能）：世界配置唯一来源，动作层只接收归一后的值。
        self.player_start = self._load_player_start(self.world_config_path)
        self.run_id = run_id
        self.run_dir = Path(run_dir) if run_dir else self.root / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self.player_id = player_id
        self.display_name = display_name
        self.spawn = spawn
        self.tick_step = tick_step
        self.lock = threading.RLock()
        # 请求/意图回看缓存：只保留最近 N 次写请求，防止长跑进程内存无界增长。
        # 淘汰后仍可通过事件日志重新查到（见 `intent()` 的回退分支）。
        self._recent_max = max(1, int(os.environ.get("XKX_RECENT_INTENTS_MAX") or 4096))
        self.requests: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.intents: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # 在线状态只看玩家自己的 wall-clock 活跃时间，绝不看全局 tick（并发下世界日会被压缩）。
        self._presence_clock = presence_clock or time.monotonic
        self._presence_active = max(0.0, float(presence_active_seconds))
        self._presence_offline = max(self._presence_active, float(presence_offline_seconds))
        self._last_active: dict[str, float] = {}
        # 读快照：Writer 只替换 `state` 引用、从不原地改已提交状态，因此读路径
        # 捕获一次引用后可在锁外计算；每请求的缓存放线程本地，避免并发读互相污染。
        self._read_local = threading.local()

        self.content = self._load_content_pack(self.content_root)
        # NPC 主动攻击规则只从内容包派生：旧九地点内容没有 aggressive 字段，
        # 也没有老虎/毒蛇/狼狗三个专属 id，规则表为空，入场不产生任何事件。
        self.aggression_rules = aggression_rules(self.content)
        # 世界自走与玩家共用 content：feed 只认这批 NPC，玩家 ID 永不进入公共事件流。
        self.npc_limit = int(npc_limit or 0)
        self._npc_ids = frozenset(
            [str(n["id"]) for n in self.content.npcs]
            + [str(row["id"]) for row in getattr(self.content, "npc_instances", []) or []]
        )
        self.ambient: AmbientScheduler | None = None
        self._ambient_thread: threading.Thread | None = None
        npcs = sorted(self.content.npcs, key=lambda n: n["id"])
        if npc_limit:
            npcs = npcs[:npc_limit]
        self.store = EventStore(self.run_dir / "events.sqlite", check_same_thread=False)
        self.registry = build_registry(self.content, seed)
        # 对话模型只挂 TalkAction；不构造 decision provider，不改 llm_enabled。
        # 旁路不属于世界状态：删掉只影响台词，不影响事件/状态/哈希。
        self.dialogue_aside = DialogueAside(self.run_dir)
        self.dialogue_queue = dialogue_runtime.attach_if_enabled(
            self.registry.get("talk"), self.root, self.run_dir
        )
        self.dialogue_model = ""
        if self.dialogue_queue is not None:
            cfg = getattr(getattr(self.dialogue_queue, "client", None), "config", None)
            self.dialogue_model = str(getattr(cfg, "model", "") or "")
        # 日界结算并入 system_tick（见 SystemTickAction）；此处把动作实例换成带 content 的版本。
        self.registry.register(SystemTickAction(self.content))
        # 玩家专属休息动词：只经 API 会话注册，不进入 NPC/系统动作链。
        self.registry.register(PlayerRestAction())
        # 玩家门派动作：同样只在本会话注册。`found_sect` 覆盖同名 NPC 动作，
        # 非玩家 actor 原样委托给 `business.FoundSectAction`，NPC 效果逐字节不变。
        self.registry.register(PlayerFoundSectAction(self.content))
        self.registry.register(PlayerRecruitDiscipleAction(self.content))
        self.registry.register(PlayerExpelDiscipleAction(self.content))
        self.registry.register(PlayerPayDisciplesAction(self.content))
        self.registry.register(PlayerSectDepositAction(self.content))
        self.registry.register(PlayerSectWithdrawAction(self.content))
        # 成长线与生死（只在本会话注册，不影响 NPC/系统动作链）。
        self.registry.register(BaiAction(self.content))
        self.registry.register(LearnAction(self.content))
        self.registry.register(PracticeAction(self.content))
        self.registry.register(ClimbAction(self.content))
        self.registry.register(JumpAction(self.content))
        self.registry.register(ReviveAction(self.content))
        # 存档优先：已有事件日志时回放，绝不重新创世/重复发放初始资产。
        self.replayed = self.store.count() > 0
        locations = self._world_locations()
        if self.replayed:
            try:
                verify_content_hash(self.store, self.content.content_hash, self.run_dir)
                state, player_ticks = replay_into(self.store, locations)
            except Exception:
                # 拒绝启动时释放 sqlite，避免部署脚本/测试留下锁文件。
                self.store.close()
                raise
            self.writer = Writer(state, self.store, self.registry, seed)
            self.writer._player_ticks.update(player_ticks)
            self.writer.committed_intents = self.store.count()
        else:
            state = WorldState(locations=locations)
            self.writer = Writer(state, self.store, self.registry, seed)
            genesis = build_genesis(self.content, npcs, root=self.root, seed=self.seed)
            self.writer.bootstrap(genesis.effects, tick=0, verb="genesis")
            # 创世预置账号：唯一发放 300 文 + 炊饼×3 + 粗布×1 的地方。
            seed_ids = [player_id]
            for acct in seed_accounts or []:
                pid = f"player:{str(acct).strip().lower()}"
                if pid not in seed_ids:
                    seed_ids.append(pid)
            if endow:
                for pid in seed_ids:
                    self.writer.bootstrap(
                        player_genesis_effects(
                            pid,
                            display_name if pid == player_id else pid.removeprefix("player:"),
                            spawn,
                            start=self.player_start,
                        ),
                        tick=0,
                        verb="genesis_player",
                    )
            if post_quests:
                self.writer.submit(
                    Intent(actor="system", verb="system_quest_post", args={}, tick=0, source="system")
                )
        # 写路径尾部缓存：启动时从 sqlite 重建，之后由每次事务成功后 observe 同步。
        self.tail = EventTail(self.store)
        # 回放/创世完成后，仍在线的档案从“服务启动时刻”开始计活跃；崩溃恢复不依赖内存历史。
        now = self._presence_clock()
        for pid, prof in self.state.players.items():
            if prof.online:
                self._last_active[pid] = now
        identities = {
            player_id: "propose_intent",
            "ops:request": "admin_apply",
            "ops:approve": "admin_apply",
        }
        self.gateway = MCPGateway(
            lambda: self.writer.state,
            writer=self.writer,
            audit_path=self.run_dir / "mcp_audit.jsonl",
            identities=identities,
        )
        self.store.set_meta("seed", str(seed))
        self.store.set_meta("content_hash", self.content.content_hash)
        self.store.set_meta("player_id", player_id)

    def _world_locations(self) -> dict[str, Any]:
        """内容地点副本；xkx 包额外注入原版鬼门关（不进 content_hash/房间统计）。"""
        locations = dict(self.content.locations)
        if any(str(lid).startswith("/d/") for lid in locations) and "/d/death/gate" not in locations:
            locations["/d/death/gate"] = {
                "id": "/d/death/gate",
                "kind": "room",
                "name": "鬼门关",
                "desc": "这里就是著名的阴间入口「鬼门关」，在你面前矗立着一座高大的\n"
                "黑色城楼，许多亡魂正哭哭啼啼地列队前进。",
                "desc_source": "lpc",
                "origin": "virtual",
                "flags": {"no_fight": 1, "no_clean_up": 0, "outdoors": ""},
                "objects": [],
                "source": {"line": 8, "path": "d/death/gate.lpc"},
            }
        return locations

    @property
    def state(self):
        """始终指向 Writer 的最新投影；读快照作用域内固定在捕获的那一版。"""
        snapshot = getattr(self._read_local, "snapshot", None)
        if snapshot is not None:
            return snapshot
        return self.writer.state

    @contextmanager
    def _read_snapshot(self):
        """把 `self.state` 固定到当前 Writer 投影，之后所有视图计算都不必占写锁。

        Writer 提交后只替换引用，已提交状态对象不再被原地修改，所以锁外读旧快照
        是安全的；一次请求内所有字段来自同一版本，不会出现跨版本的撕裂读。
        """
        snapshot = self.writer.state
        previous = getattr(self._read_local, "snapshot", None)
        self._read_local.snapshot = snapshot
        try:
            yield snapshot
        finally:
            self._read_local.snapshot = previous

    @property
    def _view_cache(self) -> dict[str, Any]:
        """兼容旧调用：返回当前线程的读视图缓存（同一 tick 内复用）。"""
        local = self._read_local
        cache = getattr(local, "view_cache", None)
        if cache is None:
            cache = {}
            local.view_cache = cache
        return cache

    def _sorted_quests(self) -> list:
        """按 id 排序的任务投影缓存；按线程 + state 对象身份失效，避免每请求重排。

        只持弱引用：请求结束后快照可以被回收，不会把旧世界钉在线程本地缓存里。
        """
        state = self.state
        local = self._read_local
        ref = getattr(local, "quest_src_ref", None)
        if ref is None or ref() is not state:
            local.quest_src_ref = weakref.ref(state)
            local.quest_order = sorted(state.quests.values(), key=lambda q: q.id)
        return local.quest_order

    # ------------------------------------------------------------------ read
    def status(self, account: str) -> dict[str, Any]:
        pid = self._pid(account)
        with self._read_snapshot():
            return self._status_view(pid)

    def quests(self, account: str) -> dict[str, Any]:
        pid = self._pid(account)
        with self._read_snapshot():
            return self._quests_view(pid)

    def _escrow_view(self) -> tuple[list[dict[str, Any]], int]:
        """托管桶视图：把 business.stock 里的 `escrow:<镖单id>:<item>` 键摊平。

        托管货物真实落在镖局库存里，不是隐形口袋；这里只做只读投影，
        供对账页直接看到“哪张镖单、多少钱、在谁手里”。
        """
        rows: list[dict[str, Any]] = []
        for bid in sorted(self.state.businesses):
            biz = self.state.businesses[bid]
            for key, qty in sorted(biz.stock.items()):
                if int(qty) <= 0 or not key.startswith("escrow:"):
                    continue
                account, _, item = key.rpartition(":")
                rows.append(
                    {
                        "account": account,
                        "holder": biz.id,
                        "holder_name": biz.name,
                        "item": item,
                        "qty": int(qty),
                    }
                )
        rows.sort(key=lambda r: (r["account"], r["item"], r["holder"]))
        return rows, sum(int(r["qty"]) for r in rows)

    def _vitals_view(self, ent) -> dict[str, Any]:
        """气血只读视图：引擎没有 max_hp 时返回 null + 原因，绝不由前端补算。"""
        max_hp = int(getattr(ent, "max_hp", 0) or 0)
        if max_hp <= 0:
            return {
                "hp": None,
                "max_hp": None,
                "hp_pct": None,
                "hp_reason": "当前角色没有气血字段（max_hp=0），气血暂未开放。",
                # 引擎目前没有内力字段，固定 null 并给出原因。
                "neili": None,
                "neili_reason": "引擎尚无内力（neili）字段，暂未开放。",
            }
        hp = int(getattr(ent, "hp", 0) or 0)
        return {
            "hp": hp,
            "max_hp": max_hp,
            "hp_pct": max(0, min(100, round(hp * 100 / max_hp))),
            "hp_reason": "",
            "neili": None,
            "neili_reason": "引擎尚无内力（neili）字段，暂未开放。",
        }

    def _attrs_view(self, ent) -> tuple[list[dict[str, Any]], str, dict[str, int]]:
        """属性只读视图：原始键值 + 固定顺序的中文展示项。"""
        attrs = {str(k): int(v) for k, v in sorted((ent.attrs or {}).items())}
        detail = [
            {"key": key, "label": label, "value": attrs.get(key)}
            for key, label in ATTR_LABELS
        ]
        reason = "" if attrs else "当前角色没有属性数据（引擎 attrs 为空），属性暂未开放。"
        return detail, reason, attrs

    def _skills_xkx_view(self, ent) -> list[dict[str, Any]]:
        """xkx 等级制技能（skill_levels/skill_xp）；为空时由调用方给原因。"""
        rows = []
        for name, level in sorted((ent.skill_levels or {}).items()):
            rows.append(
                {
                    "name": str(name),
                    "level": int(level),
                    "xp": int((ent.skill_xp or {}).get(name, 0)),
                }
            )
        return rows

    def _weight_view(self, ent) -> dict[str, Any]:
        """负重：行囊 + 已装备按内容包 weight 求和；上限按膂力/武功计算。"""
        from ..actions.common import carried_weight, max_encumbrance

        current = carried_weight(self.content, ent)
        limit = max_encumbrance(ent)
        if limit <= 0:
            return {
                "current": current,
                "max": None,
                "unit": "内容包原值",
                "reason": "当前角色没有膂力属性，负重上限不启用；只显示当前负重。",
            }
        return {"current": current, "max": limit, "unit": "内容包原值", "reason": ""}

    def _equipment_actions(self) -> dict[str, Any]:
        """穿脱按钮可用性：四个新 verb 与兼容 verb 都已在注册表。"""
        verbs = ("wear", "wield", "remove", "unwield", "equip", "unequip")
        registered = all(self.registry.get(verb) is not None for verb in verbs)
        return {
            "equip": bool(self.registry.get("equip") is not None),
            "unequip": bool(self.registry.get("unequip") is not None),
            "wear": bool(self.registry.get("wear") is not None),
            "wield": bool(self.registry.get("wield") is not None),
            "remove": bool(self.registry.get("remove") is not None),
            "unwield": bool(self.registry.get("unwield") is not None),
            "engine_registered": bool(registered),
            "reason": "" if registered else "部分穿脱动作未注册。",
        }

    def _visited_locations(self, pid: str) -> set[str]:
        """从事件日志重建玩家到过的房间（只读，不新增任何世界状态字段）。

        启动后惰性扫描一次；之后由每次 player_state 把当前房间补进来。
        """
        cache = getattr(self, "_visited_by_player", None)
        if cache is None:
            cache = {}
            try:
                for event in self.store.read_all():
                    if str(getattr(event, "outcome", "")) != "ok":
                        continue
                    actor = str(getattr(event, "actor", ""))
                    if not actor.startswith("player:"):
                        continue
                    for effect in getattr(event, "effects", []) or []:
                        if not isinstance(effect, dict):
                            continue
                        if str(effect.get("op") or "") != "set_location":
                            continue
                        data = effect.get("data") or {}
                        if str(data.get("entity") or "") != actor:
                            continue
                        to = str(data.get("to") or "")
                        if to:
                            cache.setdefault(actor, set()).add(to)
            except Exception:
                cache = {}
            self._visited_by_player = cache
        return cache.setdefault(pid, set())

    def _exploration_view(self, pid: str, loc_id: str) -> dict[str, Any]:
        """房间总数 / 已探索；区域只消费内容包 region，缺失时 null + 原因。"""
        visited = self._visited_locations(pid)
        if loc_id:
            visited.add(loc_id)
        loc = (self.content.locations or {}).get(loc_id) or {}
        region_id = str(loc.get("region") or "")
        region_name = str(loc.get("region_name") or "")
        if region_id:
            area = {"id": region_id, "name": region_name or region_id}
            area_total = sum(
                1
                for row in (self.content.locations or {}).values()
                if str(row.get("region") or "") == region_id
            )
            area_explored = sum(
                1
                for room_id in visited
                if str(
                    ((self.content.locations or {}).get(room_id) or {}).get("region") or ""
                )
                == region_id
            )
            reason = ""
        else:
            area = None
            area_total = None
            area_explored = None
            reason = "引擎/内容包尚未提供 region 字段，区域名与分区计数暂不可用。"
        return {
            "area": area,
            "area_total": area_total,
            "area_explored": area_explored,
            "area_reason": reason,
            "rooms_total": len(self.content.locations or {}),
            "rooms_explored": len(visited),
        }

    def player_state(self, account: str) -> dict[str, Any]:
        """`GET /api/player/state`：冻结结构的只读快照。"""
        account = str(account or "").strip().lower()
        with self._read_snapshot() as snapshot:
            if not ACCOUNT_RE.match(account):
                return {
                    "ok": False,
                    "profile_version": PROFILE_VERSION,
                    "reject_reason": "bad_account",
                    "reason_text": _reason_text("bad_account"),
                    "tick": snapshot.tick,
                }
            pid = self._pid(account)
            view = self._status_view(pid)
            if not view.get("ok"):
                view["profile_version"] = PROFILE_VERSION
                view["account_id"] = account
                return view
            prof = self.state.players[pid]
            ent = self.state.entities[pid]
            now = self._presence_clock()
            # 一次快照内按物品缓存成交价：内容层的供需乘数会扫全城库存，
            # 同一物品在 wallet/location/shop 三处重复取价会让并发读成倍变慢。
            prices: dict[str, tuple[int, int]] = {}
            tick = int(snapshot.tick)
            local = self._read_local
            if tick != getattr(local, "view_tick", -1):
                local.view_cache = {}
                local.view_tick = tick
            cached_views = getattr(local, "view_cache", {}).get(pid)
            if cached_views is None:
                cached_views = (
                    self._wallet_view(pid, prices),
                    self._location_view(ent.location, pid, prices),
                    self._shop_view(pid, ent.location, prices),
                    self._present_view(pid, ent.location),
                )
                self._read_local.view_cache[pid] = cached_views
            wallet_view, location_view, shop_view, present_view = cached_views
            vitals = self._vitals_view(ent)
            attrs_detail, attrs_reason, attrs = self._attrs_view(ent)
            skills_engine = self._skills_xkx_view(ent)
            skills_detail = self._skills_detail(ent)
            equipment_view = self._equipment_view(ent)
            equipment_view["actions"] = self._equipment_actions()
            player = {
                "entity_id": pid,
                "account_id": account,
                "name": ent.name,
                "archetype": prof.archetype,
                "role": ent.role,
                "location": ent.location,
                "location_name": self._loc_name(ent.location),
                "region": (self.content.locations.get(ent.location) or {}).get("region"),
                "region_name": (self.content.locations.get(ent.location) or {}).get("region_name"),
                "home": ent.home or "",
                "stamina": round(float(ent.stamina), 3),
                "max_stamina": round(float(ent.max_stamina), 3),
                "presence": ent.presence,
                "online": bool(prof.online),
                # 三种在线状态：active（活跃）/ idle（在线静置）/ offline（真正离线）。
                "activity": self._activity_of(pid, now),
                "idle_seconds": round(max(0.0, now - self._last_active.get(pid, now)), 1),
                "created_tick": int(prof.created_tick),
                "last_intent_tick": int(prof.last_intent_tick),
                "endowed": bool(prof.endowed),
                "endowed_tick": int(prof.endowed_tick),
                "wallet": wallet_view,
                "skills": {k: round(float(v), 4) for k, v in sorted(ent.skills.items())},
                # 只增不改：skills 保持字典；skills_detail 给每个技能等级/下一级/说明。
                "skills_detail": skills_detail,
                # xkx 等级制技能；与 skills_detail 二选一由前端按 available 判断。
                "skills_engine": skills_engine,
                "skills_status": {
                    "available": bool(skills_engine or skills_detail),
                    "reason": ""
                    if (skills_engine or skills_detail)
                    else "当前角色还没有已学技能；引擎的 skills/practice 字段接入后自动显示。",
                },
                "equipment": equipment_view,
                # 人物面板只读字段：气血/内力、经验、属性、负重、探索。
                "vitals": vitals,
                "combat_exp": int(getattr(ent, "combat_exp", 0) or 0),
                "attrs": attrs,
                "attrs_detail": attrs_detail,
                "attrs_reason": attrs_reason,
                "weight": self._weight_view(ent),
                "exploration": self._exploration_view(pid, ent.location),
                # xkx 成长线：真实技能等级、经验/潜能、师承与死亡记录。
                "status": ent.status,
                "hp": round(float(ent.hp), 3),
                "max_hp": round(float(ent.max_hp), 3),
                "potential": int(ent.potential),
                "learned_points": int(ent.learned_points),
                "death_count": int(ent.death_count),
                "master": {
                    "id": ent.master,
                    "name": self._npc_name(ent.master) if ent.master else "",
                },
                "martial_skills": self._martial_skills_view(ent),
                "relations": view["relations"],
                "active_quests": view["active_quests"],
                "marriage": view["marriage"],
                "sect": view["sect"],
            }
            location = location_view
            shop = shop_view
            present = present_view
            online = self._online_players_view(pid)
            quest_board = self._quest_board_view(pid, ent.location)
            escrow_rows, escrow_total = self._escrow_view()
            minted = int(self.state.minted_total)
            burned = int(self.state.burned_total)
            accounts_sum = sum(int(v) for v in self.state.accounts.values())
            result = {
                "ok": True,
                "profile_version": PROFILE_VERSION,
                "account_id": account,
                "tick": int(self.state.tick),
                "endowment_total": int(self.state.endowment_total),
                # 托管（物品科目）：逐桶可见，结束时必须归零或写明 orphaned。
                "escrow": escrow_rows,
                "escrow_total": escrow_total,
                # 对账（货币科目）：accounts_sum 必须等于 minted - burned；
                # 托管件数单列，不混进货币等式，但绝不允许“看不见”。
                "reconciliation": {
                    "accounts_sum": accounts_sum,
                    "minted_total": minted,
                    "burned_total": burned,
                    "net_issued": minted - burned,
                    "delta": accounts_sum - (minted - burned),
                    "escrow_accounts": len({str(r["account"]) for r in escrow_rows}),
                    "escrow_items": escrow_total,
                },
                "player": player,
                "marriage": view["marriage"],
                "sect": view["sect"],
                "location": location,
                "shop": shop,
                "present_npcs": present,
                # 当前房间地面（引擎 loot_create 的落点），按 item id 排序。
                "ground": [
                    {"item": str(key), "name": self._item_name(str(key)), "qty": int(qty)}
                    for key, qty in sorted((self.state.ground.get(ent.location) or {}).items())
                    if int(qty) > 0
                ],
                "escort_orders": [
                    order.to_dict()
                    for order in sorted(self.state.escorts.values(), key=lambda o: (o.created_tick, o.id))
                    if str(order.carrier or "") == pid
                ],
                "quest_board": quest_board,
                "online_players": online,
                # 兼容旧 /api/player/status 的扁平字段，前端可逐步迁移。
                "nearby": present,
                "active_quests_detail": view["active_quests_detail"],
                "growth": view["growth"],
                "today": view["today"],
            }
            # 只在真正交战时出现：旧世界/未交战的响应结构完全不变。
            if view.get("combat"):
                result["combat"] = view["combat"]
            return result

    # ------------------------------------------------ 服务模式匿名读（公共摘要）
    def public_state(self, account: str) -> dict[str, Any]:
        """服务模式匿名读：只返回 `PUBLIC_STATE_KEYS` 白名单内的公共摘要。

        白名单实现（不是剔除私有字段），新增字段默认不可见，防止将来
        加入钱包/背包类字段后自动泄漏。
        """
        account = str(account or "").strip().lower()
        with self._read_snapshot() as snapshot:
            if not ACCOUNT_RE.match(account):
                return {
                    "ok": False,
                    "reject_reason": "bad_account",
                    "reason_text": _reason_text("bad_account"),
                    "server_tick": int(snapshot.tick),
                }
            pid = self._pid(account)
            ent = self.state.entities.get(pid)
            prof = self.state.players.get(pid)
            if ent is None or prof is None:
                return {
                    "ok": False,
                    "reject_reason": "player_not_entered",
                    "reason_text": _reason_text("player_not_entered"),
                    "server_tick": int(snapshot.tick),
                }
            now = self._presence_clock()
            row = {
                "ok": True,
                "name": ent.name,
                "place": self._loc_name(ent.location),
                "activity": self._activity_of(pid, now),
                "idle_seconds": round(max(0.0, now - self._last_active.get(pid, now)), 1),
                "online": bool(prof.online),
                "sect_name": self._faction_name(ent),
                "shop_name": self._business_name(ent),
                "server_tick": int(self.state.tick),
            }
            return {key: row[key] for key in PUBLIC_STATE_KEYS}

    def public_quests(self) -> dict[str, Any]:
        """服务模式匿名读：只返回公共开放任务，不含奖励明细与任何人已接任务。"""
        rows: list[dict[str, Any]] = []
        with self._read_snapshot() as snapshot:
            for quest in self._sorted_quests():
                if quest.status != "open":
                    continue
                row = self._quest_row(quest)
                for key in ("reward_money", "reward_relation", "acceptable"):
                    row.pop(key, None)
                rows.append(row)
            return {
                "ok": True,
                "tick": int(snapshot.tick),
                "open": rows,
                "active": [],
            }

    def public_messages(self, account: str, since_tick: int = 0) -> dict[str, Any]:
        """服务模式匿名读：不返回任何人的私聊内容，形状与本人读一致。"""
        account = str(account or "").strip().lower()
        try:
            since = max(0, int(since_tick))
        except (TypeError, ValueError):
            since = 0
        with self.lock:
            return {
                "ok": True,
                "profile_version": PROFILE_VERSION,
                "account_id": account,
                "tick": int(self.state.tick),
                "since_tick": since,
                "next_since_tick": int(self.state.tick),
                "messages": [],
            }

    def world_feed(self, limit: Any = 20) -> dict[str, Any]:
        """`GET /api/world/feed`：最近的世界事件摘要（公共、只读、匿名可读）。

        只含 NPC 的自主/剧情事件；字段固定为
        `tick / actor / verb / place / summary / important`，
        不含任何玩家私有状态（钱包、背包、任务、对话）。
        """
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 20
        n = max(1, min(WORLD_FEED_LIMIT_MAX, n))
        with self.lock:
            rows = self.store.conn.execute(
                "SELECT seq,tick,actor,verb,args FROM events "
                "WHERE outcome='ok' AND actor != 'system' AND actor NOT LIKE 'player:%' "
                "AND verb NOT LIKE 'system%' ORDER BY seq DESC LIMIT ?",
                (n * 10,),
            ).fetchall()
            events: list[dict[str, Any]] = []
            for _seq, tick, actor, verb, raw_args in rows:
                actor = str(actor)
                verb = str(verb)
                if actor not in self._npc_ids:
                    continue
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                ent = self.state.entities.get(actor)
                place_id = str(ent.location) if ent is not None else ""
                place = self._loc_name(place_id) if place_id else ""
                events.append(
                    {
                        "tick": int(tick),
                        "actor": str(ent.name) if ent is not None else actor,
                        "verb": verb,
                        "place": place,
                        "summary": self._feed_summary(actor, verb, args, place),
                        "important": verb in WORLD_FEED_IMPORTANT_VERBS,
                    }
                )
                if len(events) >= n:
                    break
            events.reverse()
            return {
                "ok": True,
                "tick": int(self.state.tick),
                "limit": n,
                "events": events,
            }

    def world_map(self) -> dict[str, Any]:
        """`GET /api/world/map`：全城节点与真实邻接边（公共、只读、匿名可读）。

        数据直接来自世界状态的 `locations`（genesis 从 locations.yaml 复制），
        边由 `exits` 逐对推出；可达性逐对复用 `path_between`，保证与寻路一致。
        区域字段内容里没有，就如实返回 null，不在接口里补边/编区域。
        """
        with self.lock:
            locs = self.state.locations
            loc_ids = sorted(str(k) for k in locs)
            nodes: list[dict[str, Any]] = []
            edges: list[dict[str, Any]] = []
            seen_pairs: set[tuple[str, str]] = set()
            reachable: dict[str, list[str]] = {}
            for lid in loc_ids:
                data = locs.get(lid) or {}
                exits = sorted({str(x) for x in (data.get("exits") or []) if str(x) in locs})
                neighbors = [x for x in exits if path_between(self.state, lid, x)]
                nodes.append(
                    {
                        "id": lid,
                        "name": str(data.get("name") or lid),
                        "region": data.get("region"),
                        "region_name": data.get("region_name"),
                        "exits": exits,
                        "neighbors": neighbors,
                    }
                )
                reachable[lid] = sorted(
                    other for other in loc_ids if other != lid and path_between(self.state, lid, other)
                )
                for other in exits:
                    pair = (lid, other) if lid < other else (other, lid)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    a, b = pair
                    a_to_b = bool(path_between(self.state, a, b))
                    b_to_a = bool(path_between(self.state, b, a))
                    edges.append(
                        {
                            "from": a,
                            "to": b,
                            "from_name": str((locs.get(a) or {}).get("name") or a),
                            "to_name": str((locs.get(b) or {}).get("name") or b),
                            # 内容未提供方位标签，如实返回 null（不编造“东/西”）。
                            "direction": None,
                            "walkable": a_to_b and b_to_a,
                            "walkable_a_to_b": a_to_b,
                            "walkable_b_to_a": b_to_a,
                            "bidirectional": a_to_b and b_to_a,
                        }
                    )
            edges.sort(key=lambda row: (row["from"], row["to"]))
            has_region = any(
                (locs.get(lid) or {}).get("region") not in (None, "")
                for lid in loc_ids
            )
            return {
                "ok": True,
                "tick": int(self.state.tick),
                "run": self.run_id,
                "counts": {
                    "locations": len(nodes),
                    "edges": len(edges),
                    "directed_edges": sum(
                        int(row["walkable_a_to_b"]) + int(row["walkable_b_to_a"]) for row in edges
                    ),
                },
                "region_supported": has_region,
                "region_note": (
                    "location 内含 region 字段。"
                    if has_region
                    else "locations.yaml 未提供区域字段；所有地点 region/region_name 如实为 null，未编造。"
                ),
                "nodes": nodes,
                "edges": edges,
                "reachable": reachable,
            }

    def _entity_name(self, ent_id: str) -> str:
        ent = self.state.entities.get(str(ent_id))
        return str(ent.name) if ent is not None else str(ent_id)

    def _feed_summary(self, actor: str, verb: str, args: dict[str, Any], place: str) -> str:
        """一句中文短句，≤40 字；只描述公共可见行为。"""
        name = self._entity_name(actor)
        at = place or "此地"
        target = str(args.get("target") or "")
        target_name = self._entity_name(target) if target else ""
        to_name = self._loc_name(str(args.get("to"))) if args.get("to") else ""
        if verb == "move":
            text = f"动身前往{to_name}" if to_name else "换了去处"
        elif verb == "cultivate":
            skill = str(args.get("skill") or "")
            text = f"在{at}修炼{skill}" if skill else f"在{at}静心修炼"
        elif verb == "work":
            text = f"在{at}做工"
        elif verb in ("buy", "sell", "trade"):
            text = f"在{at}做买卖"
        elif verb == "talk":
            text = f"与{target_name}交谈" if target_name else "与人交谈"
        elif verb == "marry":
            text = f"在{at}成婚"
        elif verb == "open_shop":
            text = f"在{at}开了铺子"
        elif verb == "join_sect":
            text = f"加入了{str(args.get('sect') or '门派')}"
        elif verb == "escort":
            text = f"押镖前往{to_name or '外地'}"
        elif verb == "quest_accept":
            text = "接下一份委托"
        elif verb == "quest_deliver":
            text = "交了一份委托"
        elif verb == "consume":
            text = f"在{at}歇脚"
        else:
            text = f"{name}在{at}活动"
        return text[:40]

    def _faction_name(self, ent) -> str:
        faction = self.state.factions.get(str(ent.faction or ""))
        return faction.name if faction is not None else ""

    def _business_name(self, ent) -> str:
        biz = self.state.businesses.get(str(ent.business or ""))
        return biz.name if biz is not None else ""

    def action(
        self,
        account: str,
        verb: str,
        args: dict[str, Any] | None = None,
        client_tick: Any = None,
        request_id: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """`POST /api/player/action`：统一写入口。

        客户端 `client_tick` 只回显，不参与调度（服务端 tick 是唯一事实源）。
        """
        raw = str(verb or "").strip().lower()
        action = ACTION_ALIASES.get(raw, raw.replace("/", "_"))
        response = self.perform(account, action, args or {}, request_id)
        response.setdefault("profile_version", PROFILE_VERSION)
        response["client_tick"] = client_tick
        response["client_tick_ignored"] = client_tick is not None
        response["confirm"] = bool(confirm)
        response["status"] = "executed" if response.get("ok") else "rejected"
        return response

    def finalize_dialogue(self, result: dict[str, Any], args: dict[str, Any]) -> None:
        """事务提交后把配音排到后台：本次响应立即返回模板，旁路写入模型句。

        硬约束：本方法不在 Writer 事务内、不持有 `self.lock`、不等待模型；成功结果
        只写 `dialogue_aside.jsonl`，由 `GET /api/player/messages?since_seq=` 取回。
        """
        try:
            if not isinstance(result, dict) or str(result.get("verb") or "") != "talk":
                return
            details = result.get("details")
            if not isinstance(details, dict):
                details = {}
                result["details"] = details
            # 快路径：模板句立即成立，模型只做后台配音，不进入本次 HTTP 等待。
            details["dialogue_source"] = "template"
            queue = self.dialogue_queue
            if not result.get("ok") or queue is None:
                return
            if not getattr(queue, "defer_calls", False):
                return
            talk = self.registry.get("talk")
            take = getattr(talk, "take_pending", None)
            if take is None:
                return
            target = str(args.get("target") or "")
            message = str(args.get("message") or "").strip()
            tick = int(result.get("tick") or 0)
            seq = int(result.get("event_seq") or 0)
            player = str(result.get("player") or "")
            pending = take(player, target, tick, message)
            if not pending or seq <= 0:
                return

            def _deliver(reply: Any) -> None:
                text = str(getattr(reply, "text", "") or "").strip()
                if not text:
                    return
                try:
                    self.dialogue_aside.put(seq, target, text, self.dialogue_model)
                except Exception:  # noqa: BLE001 - 旁路失败不能影响本次玩家操作
                    pass

            accepted = queue.submit_background(
                npc=target,
                player_text=message,
                day=int(pending.get("day") or 0),
                tick=tick,
                persona=str(pending.get("persona") or ""),
                state_summary=dict(pending.get("summary") or {}),
                on_done=_deliver,
            )
            details["dialogue_pending"] = bool(accepted)
        except Exception:  # noqa: BLE001 - 配音失败一律退回模板，绝不向 HTTP 抛错
            return

    def intent(self, account: str, intent_id: str) -> dict[str, Any]:
        """`GET /api/player/intents/<id>`：按 request_id 或事件 intent_id 查询。"""
        account = str(account or "").strip().lower()
        pid = self._pid(account)
        with self.lock:
            cached = self.intents.get(str(intent_id))
            if cached is not None and cached.get("player") == pid:
                code = str(cached.get("reject_reason") or "")
                return {
                    "ok": bool(cached.get("ok")),
                    "found": True,
                    "profile_version": PROFILE_VERSION,
                    "intent_id": str(cached.get("intent_id") or intent_id),
                    "event_seq": int(cached.get("event_seq") or 0),
                    "tick": int(cached.get("tick") or 0),
                    "verb": str(cached.get("verb") or ""),
                    "outcome": "ok" if cached.get("ok") else "reject",
                    "reject_reason": code,
                    "reason_text": str(cached.get("reason_text") or ""),
                    "status": "executed" if cached.get("ok") else "rejected",
                }
            for event in self.store.read_all():
                if event.intent_id != intent_id or event.actor != pid:
                    continue
                return self._intent_view(event, found=True)
            return {
                "ok": False,
                "found": False,
                "profile_version": PROFILE_VERSION,
                "intent_id": str(intent_id),
                "tick": int(self.state.tick),
                "reject_reason": "intent_not_found",
                "reason_text": "没有找到这个请求；请确认 request_id 或事件 seq。",
            }

    def messages(self, account: str, since_tick: int = 0, since_seq: int | None = None) -> dict[str, Any]:
        """`GET /api/player/messages`：按 `since_tick` 拉对话回复，或按 `since_seq` 取回迟到配音。

        `since_seq` 模式只返回旁路覆盖的条目（模型已到），模型文本只来自
        `dialogue_aside.jsonl`，不含模板句冒充模型。
        """
        account = str(account or "").strip().lower()
        pid = self._pid(account)
        if since_seq is not None:
            try:
                since_seq_value = max(0, int(since_seq))
            except (TypeError, ValueError):
                since_seq_value = 0
            rows_since_seq: list[dict[str, Any]] = []
            with self.lock:
                for event in self.store.read_all():
                    seq = int(event.seq)
                    if seq <= since_seq_value or event.actor != pid:
                        continue
                    for eff in event.effects or []:
                        if eff.get("op") != "memory_add":
                            continue
                        data = eff.get("data") or {}
                        if data.get("source") != "talk:npc":
                            continue
                        npc = str(data.get("npc") or "")
                        row = self.dialogue_aside.get_row(seq, npc)
                        if not row:
                            continue
                        rows_since_seq.append(
                            {
                                "id": f"msg:{seq}",
                                "seq": seq,
                                "tick": int(event.tick),
                                "kind": "talk_reply",
                                "from": npc,
                                "to": pid,
                                "text": str(row.get("text") or ""),
                                "dialogue_source": "model",
                                "model": str(row.get("model") or ""),
                                "created_at": row.get("created_at"),
                            }
                        )
                next_seq = max([since_seq_value] + [r["seq"] for r in rows_since_seq])
                return {
                    "ok": True,
                    "profile_version": PROFILE_VERSION,
                    "account_id": account,
                    "tick": int(self.state.tick),
                    "since_seq": since_seq_value,
                    "next_since_seq": int(next_seq),
                    "messages": rows_since_seq,
                }
        try:
            since = max(0, int(since_tick))
        except (TypeError, ValueError):
            since = 0
        with self.lock:
            rows: list[dict[str, Any]] = []
            for event in self.store.read_all():
                if int(event.tick) <= since or event.actor != pid:
                    continue
                for eff in event.effects or []:
                    if eff.get("op") != "memory_add":
                        continue
                    data = eff.get("data") or {}
                    if data.get("source") != "talk:npc":
                        continue
                    npc = str(data.get("npc") or "")
                    template_text = str(data.get("text") or "").removeprefix("我答：")
                    aside_text = self.dialogue_aside.get(int(event.seq), npc)
                    rows.append(
                        {
                            "id": f"msg:{event.seq}",
                            "seq": int(event.seq),
                            "tick": int(event.tick),
                            "kind": "talk_reply",
                            "from": npc,
                            "to": pid,
                            "text": aside_text or template_text,
                            "dialogue_source": "model" if aside_text else "template",
                        }
                    )
            return {
                "ok": True,
                "profile_version": PROFILE_VERSION,
                "account_id": account,
                "tick": int(self.state.tick),
                "since_tick": since,
                "next_since_tick": int(self.state.tick),
                "messages": rows,
            }

    @staticmethod
    def _load_npc_wander(path: Path | None) -> bool:
        """只读世界配置的 `npc_wander.enabled`；默认 False，旧世界配置无需加键。"""
        if path is None:
            return False
        try:
            import yaml

            loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except Exception:
            return False
        if not isinstance(loaded, dict):
            return False
        raw = loaded.get("npc_wander") or {}
        return bool(raw.get("enabled")) if isinstance(raw, dict) else bool(raw)

    @staticmethod
    def _load_player_start(path: Path | None) -> dict | None:
        """只读世界配置的 `player_start`；缺省/解析失败/非 dict 一律回落 None。

        数值合法性由 `actions.player._start_values` 统一归一，这里只负责取配置。
        """
        if path is None:
            return None
        try:
            import yaml

            loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        if not isinstance(loaded, dict):
            return None
        raw = loaded.get("player_start")
        return dict(raw) if isinstance(raw, dict) else None

    def _load_content_pack(self, pack_root: Path):
        """按“内容包目录”加载内容，兼容 core.load_content 的 <root>/content 约定。

        xkx 的 JSON 内容包（rooms.json/exits.json/...）由本模块的只读适配层
        转成 Content；其它内容包仍走 core 的 YAML 加载。若目录本身就叫 content，
        或包内自带 content/ 子目录，直接沿用 core 的加载根；否则在 run 目录下
        建一个只读软链，把包目录挂成 content/。
        只做路径适配，不复制内容、不改 core 加载规则。
        """
        pack_root = Path(pack_root)
        content_dir = pack_root / "content" if (pack_root / "content").is_dir() else pack_root
        if is_xkx_pack(content_dir):
            return load_xkx_content(content_dir)
        if content_dir.name == "content":
            return load_content(content_dir.parent, allow_fixture=False, use_content=True)
        stage = self.run_dir / "_content_root"
        stage.mkdir(parents=True, exist_ok=True)
        link = stage / "content"
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                raise RuntimeError(f"内容挂载点不是软链，拒绝启动：{link}")
            link.unlink()
        link.symlink_to(content_dir, target_is_directory=True)
        return load_content(stage, allow_fixture=False, use_content=True)

    def _pid(self, account: str) -> str:
        return f"player:{str(account).strip().lower()}"

    def _xkx_extra_verb_allowed(self, verb: str) -> bool:
        """战斗类动词只在 actions/ 层已注册时对玩家放行。"""
        return str(verb) in XKX_EXTRA_PLAYER_VERBS and self.registry.get(str(verb)) is not None

    def _trigger_npc_aggression(self, pid: str, tick: int) -> list[dict[str, Any]]:
        """玩家进入房间时，对每个“尚未交战且有规则”的 NPC 判定一次。

        与旧世界隔离的关键：规则表由内容派生，旧 YAML 没有 aggressive 字段，
        也没有老虎/毒蛇/狼狗，`self.aggression_rules` 为空 -> 这里直接返回，
        不写任何事件。同一 NPC 在同一房间只会提交一条 trigger。
        """
        rules = self.aggression_rules
        if not rules:
            return []
        ent = self.state.entities.get(pid)
        if ent is None:
            return []
        rows: list[dict[str, Any]] = []
        for npc_id in sorted(self.state.entities):
            npc = self.state.entities[npc_id]
            if npc.location != ent.location:
                continue
            if npc.status != "alive" or npc.combat_target:
                continue
            if rule_for(npc, rules) is None:
                continue
            event = self._submit(
                Intent(
                    actor=npc_id,
                    verb="npc_aggression",
                    args={"mode": "trigger", "target": pid},
                    tick=int(tick),
                    source="npc",
                    reason=f"{npc_id} 见到 {pid} 进入房间（init 触发）",
                )
            )
            for eff in event.effects or []:
                if eff.get("op") != "aggression_log":
                    continue
                data = dict(eff.get("data") or {})
                rows.append(
                    {
                        "npc": npc_id,
                        "name": npc.name,
                        "triggered": bool(data.get("triggered")),
                        "result": str(data.get("result") or ""),
                        "roll": data.get("roll"),
                        "total": data.get("total"),
                        "threshold": data.get("threshold"),
                        "tick": int(event.tick),
                        "outcome": str(event.outcome),
                        "reason": str(event.reason),
                    }
                )
        return rows

    def _combat_view(self, pid: str) -> dict[str, Any]:
        """只读交战快照：哪个 NPC 正在打这名玩家、已结算到第几回合。"""
        for npc_id in sorted(self.state.entities):
            npc = self.state.entities[npc_id]
            if npc.combat_target != pid or npc.status != "alive":
                continue
            return {
                "active": True,
                "opponent": npc_id,
                "opponent_name": npc.name,
                "round": int(npc.combat_round),
                "started_tick": int(npc.combat_tick),
                "location": npc.location,
            }
        return {}

    def _status_view(self, pid: str) -> dict[str, Any]:
        view = player_status(self.state, pid)
        if not view:
            return {"ok": False, "reject_reason": "player_not_entered",
                    "reason_text": _reason_text("player_not_entered")}
        nearby = []
        loc = view["location"]
        for ent in sorted(self.state.entities.values(), key=lambda e: e.id):
            if ent.id == pid or ent.location != loc or ent.status != "alive":
                continue
            nearby.append(
                {"id": ent.id, "name": ent.name, "role": ent.role, "presence": ent.presence}
            )
        view["nearby"] = nearby
        view["active_quests_detail"] = [
            self._quest_row(self.state.quests[qid]) for qid in view["active_quests"]
            if qid in self.state.quests
        ]
        # P4：成长线 + “今天能做什么”。只读派生，随状态刷新。
        board = self._quest_board_view(pid, loc)
        today = today_view(
            self.state,
            self.content,
            pid,
            location=loc,
            quest_board=board,
            nearby=nearby,
        )
        today["location_name"] = self._loc_name(loc)
        view["growth"] = growth_view(self.state, self.content, pid)
        view["today"] = today
        view["marriage"] = self._marriage_view(pid)
        view["sect"] = self._sect_view(pid)
        combat = self._combat_view(pid)
        if combat:
            view["combat"] = combat
        view["tick"] = self.state.tick
        view["ok"] = True
        return view

    def _settled_today(self, pid: str, fac) -> bool:
        from ..actions.player_sect import SETTLE_MARKER

        marker = f"{SETTLE_MARKER}:{fac.id}:D{int(self.state.tick) // TICKS_PER_DAY}"
        return any(marker in str(m.get("text", "")) for m in self.state.memories.get(pid, ()))

    def _disciple_settled_today(self, did: str) -> bool:
        """逐人日结状态：日结成功时每名在册弟子都会写下「…日结：第 N 日…」记忆。"""
        day = int(self.state.tick) // TICKS_PER_DAY
        needle = f"日结：第{day}日"
        return any(needle in str(m.get("text", "")) for m in self.state.memories.get(did, ()))

    def _sect_arrears(self, fid: str, did: str) -> int:
        total = 0
        for debt in self.state.debts.values():
            if debt.status != "active":
                continue
            if str(debt.id).startswith(f"debt:sect_stipend:{fid}:{did}"):
                total += int(debt.amount)
        return total

    def _sect_view(self, pid: str) -> dict[str, Any]:
        """门派私有快照：只随本人令牌返回。

        匿名公共摘要里没有门派字段（`public_state` 只给 `sect_name`）；
        这里包含门主、名册、金库与欠饷，供本人页面显示。
        """
        ent = self.state.entities.get(pid)
        fac = self.state.factions.get(str(ent.faction or "")) if ent is not None else None
        if fac is None:
            return {"in_sect": False, "sect": None}
        leader = fac.leader or fac.founder
        role = "leader" if str(leader) == pid else "disciple"
        disciples = []
        for did in active_disciples(self.state, fac):
            d = self.state.entities.get(did)
            if d is None:
                continue
            affection, trust = _bond(self.state, pid, did)
            disciples.append(
                {
                    "id": did,
                    "name": d.name,
                    "skill": round(
                        max((float(v) for v in d.skills.values()), default=0.0), 4
                    ),
                    "presence": d.presence,
                    "at_sect": d.location == fac.location,
                    "arrears": self._sect_arrears(fac.id, did),
                    # 与收徒裁决同一份关系数据（`_bond`）；门主视角即裁决值。
                    "affection": float(affection),
                    "trust": float(trust),
                    "settled_today": self._disciple_settled_today(did),
                }
            )
        return {
            "in_sect": True,
            "sect": {
                "id": fac.id.removeprefix("sect:"),
                "faction_id": fac.id,
                "name": fac.name,
                "location": fac.location,
                "location_name": self._loc_name(fac.location),
                "role": role,
                "leader": leader,
                "leader_name": self._entity_name(leader),
                "member_count": 1 + len(disciples),
                "treasury": {
                    "account": fac.treasury_account,
                    "balance": int(self.state.account_balance(fac.treasury_account)),
                },
                "disciples": disciples,
                "limits": {
                    "disciple_cap": DISCIPLE_CAP,
                    "dues_daily": DUES_DAILY,
                    "stipend_daily": STIPEND_DAILY,
                    "arrears_expel": ARREARS_EXPEL,
                },
                "settled_today": self._settled_today(pid, fac) if role == "leader" else None,
            },
        }

    def sects_view(self, account: str = "") -> dict[str, Any]:
        """`GET /api/player/sects`：公开的可加入门派列表。

        只给安全字段（名号、门主、驻地、门众数、声望、`joinable`），不含金库金额
        与弟子明细；排序固定按 `faction.id`。`joinable` 仅在没有本人令牌时才只看
        门主是否存在；带本人令牌时叠加“尚未入派”（已入派者 `join_sect` 必然拒绝）。
        """
        account = str(account or "").strip().lower()
        with self._read_snapshot() as snapshot:
            viewer = (
                snapshot.entities.get(self._pid(account))
                if ACCOUNT_RE.match(account)
                else None
            )
            viewer_in_faction = viewer is not None and str(viewer.faction or "") != ""
            rows = []
            for fac in sorted(snapshot.factions.values(), key=lambda f: (f.id, f.name)):
                if not str(fac.id).startswith("sect:"):
                    continue  # 地点行会（org:*）不是玩家门派，不进本列表
                leader = str(fac.leader or fac.founder or "")
                if not leader:
                    continue  # 无门主的内容行会（如茶行）不可“择派加入”
                rows.append(
                    {
                        "id": fac.id.removeprefix("sect:"),
                        "faction_id": fac.id,
                        "name": fac.name,
                        "leader": leader,
                        "leader_name": self._entity_name(leader),
                        "location": fac.location,
                        "location_name": self._loc_name(fac.location),
                        "member_count": 1 + len(active_disciples(snapshot, fac)),
                        "reputation": (
                            round(float(reputation_points(snapshot, leader)), 1)
                            if leader
                            else 0.0
                        ),
                        "joinable": not viewer_in_faction,
                    }
                )
            return {
                "ok": True,
                "profile_version": PROFILE_VERSION,
                "tick": int(snapshot.tick),
                "count": len(rows),
                "sects": rows,
            }

    def _quests_view(self, pid: str) -> dict[str, Any]:
        ent = self.state.entities.get(pid)
        player_rep = reputation_points(self.state, pid) if ent is not None else 0.0
        open_rows = []
        locked_rows = []
        for quest in self._sorted_quests():
            if quest.status != "open":
                continue
            row = self._quest_row(quest)
            reason = self._quest_lock_reason(quest, player_rep)
            if reason:
                row.update(
                    {
                        "acceptable": False,
                        "can_accept_here": False,
                        "accepted_by_me": False,
                        "expired": reason == "quest_expired",
                        "lock_reason": reason,
                        "lock_text": _reason_text(reason),
                    }
                )
                locked_rows.append(row)
                continue
            row["acceptable"] = bool(ent and ent.location == row["giver_location"])
            row["category"] = str(
                (self.content.quests.get(quest.template_id) or {}).get("category", "")
            )
            open_rows.append(row)
        active = [
            self._quest_row(quest)
            for quest in self._sorted_quests()
            if quest.status == "accepted" and quest.acceptor == pid
        ]
        return {
            "ok": True,
            "tick": self.state.tick,
            "location": ent.location if ent else "",
            "open": open_rows,
            "locked": locked_rows,
            "active": active,
        }

    def _quest_row(self, quest) -> dict[str, Any]:
        giver = self.state.entities.get(quest.giver)
        tpl = self.content.quests.get(quest.template_id) or {}
        args = dict(quest.args or {})
        target_npc = str(quest.target_npc or "")
        required = quest_required_units(quest)
        required_action: dict[str, Any] = {
            "verb": quest.verb,
            "params": args,
            "target_npc": target_npc,
            "target_npc_name": self._entity_name(target_npc) if target_npc else "",
            "target_location": quest.target_location,
            "target_location_name": self._loc_name(quest.target_location),
            "hours": int(args.get("hours") or 0),
        }
        if quest.verb == "buy":
            item = str(args.get("item") or tpl.get("target_item") or "")
            required_action["target_item"] = item
            required_action["target_item_name"] = self._item_name(item) if item else ""
            required_action["qty"] = max(1, int(args.get("qty") or 1))
        return {
            "id": quest.id,
            "template": quest.template_id,
            "name": str(tpl.get("name") or quest.template_id),
            "category": str(tpl.get("category", "")),
            "giver": quest.giver,
            "giver_name": giver.name if giver else quest.giver,
            "giver_location": giver.location if giver else "",
            "target_npc": target_npc,
            "target_npc_name": required_action["target_npc_name"],
            "target_location": quest.target_location,
            "required_action": required_action,
            # 任务过程化：玩家可见“目标动作完成到哪一步”，前端据此画进度条/置灰按钮。
            "progress": {
                "done": quest.progress_units >= required,
                "units": int(quest.progress_units),
                "required": int(required),
                "verb": quest.verb,
                "tick": int(quest.progress_tick),
                "note": str(quest.progress_note or ""),
            },
            "reward_money": quest.reward_money,
            "reward_relation": quest.reward_relation,
            "deadline_tick": quest.deadline_tick,
            "deadline_day": quest.deadline_tick // TICKS_PER_DAY + 1 if quest.deadline_tick else None,
            "status": quest.status,
        }

    def _quest_lock_reason(self, quest, player_rep: float) -> str:
        """被挡住的开放任务：返回玩家可读原因码；可正常展示时返回空串。"""
        tick = int(self.state.tick)
        if quest.deadline_tick and tick > quest.deadline_tick:
            return "quest_expired"
        giver = self.state.entities.get(quest.giver)
        if giver is None or giver.status != "alive":
            return "giver_missing"
        tpl = self.content.quests.get(quest.template_id) or {}
        if float(tpl.get("require_reputation", 0) or 0) > player_rep:
            return "reputation_too_low"
        return ""

    def _loc_name(self, loc_id: str) -> str:
        row = (self.state.locations or {}).get(loc_id) or {}
        return str(row.get("name") or loc_id or "未知地点")

    def _npc_name(self, npc_id: str) -> str:
        for raw in getattr(self.content, "npcs", []) or []:
            if str(raw.get("id") or "") == npc_id:
                return str(raw.get("name") or npc_id)
        return npc_id

    def _item_name(self, item_id: str) -> str:
        row = (self.content.items or {}).get(item_id) or {}
        return str(row.get("name") or item_id)

    _ITEM_CATEGORY_TEXT = {
        "food": "食物，可食用恢复体力。",
        "drink": "酒水，可饮用恢复体力。",
        "medicine": "药材，可用于疗伤。",
        "material": "材料，可用于制作或交易。",
        "tool": "工具，可装备到工具槽。",
        "luxury": "奢侈品，可用于馈赠或交易。",
        "service": "服务，购买后在对应场所使用。",
    }

    def _item_meta(self, item_id: str) -> dict[str, Any]:
        """物品的只读派生字段：类别、说明、是否可装备及槽位。

        说明优先取内容 `description`，其次内容包 `item_catalog.usage`，
        最后按类别给一句如实说明；不修改内容文件。
        """
        row = (self.content.items or {}).get(item_id) or {}
        category = str(row.get("category") or "")
        description = str(row.get("description") or "")
        if not description:
            catalog = (self.content.economy or {}).get("item_catalog") or {}
            description = str((catalog.get(item_id) or {}).get("usage") or "")
        if not description:
            description = self._ITEM_CATEGORY_TEXT.get(category, "普通物品。")
        raw_slot = str(row.get("equip_slot") or "")
        slot = slot_for_item(item_id, category, raw_slot)
        return {
            "category": category,
            "description": description,
            "equippable": bool(slot),
            "slot": slot or None,
            # 内容包自己的装备槽（护具即引擎槽；兵器另给技能类型）。
            "equip_slot": raw_slot,
            "equip_slot_label": XKX_EQUIP_SLOT_LABELS.get(raw_slot, raw_slot),
        }

    _SKILL_TEXT = {
        "镖师": "武艺与押镖的基本功，影响护镖与近身应对。",
        "掌柜": "算账与议价的本事，影响店铺经营与买卖。",
        "捕快": "缉访与追踪的本事，影响查案与交涉。",
        "账房": "记账与核账的基本功，影响店铺账目。",
        "铁匠": "打铁与锻造的基本功，影响器物打造。",
        "郎中": "诊脉与用药的基本功，影响疗伤效果。",
        "厨子": "烹饪与食材处理的基本功。",
        "伙计": "跑堂与待客的基本功。",
        "更夫": "巡夜与守时的基本功。",
        "车夫": "赶车与认路的基本功。",
        "船工": "撑船与水性的基本功。",
        "绣娘": "针线与绣工的基本功。",
        "学究": "读书与考据的基本功。",
    }

    def _skills_detail(self, ent) -> list[dict[str, Any]]:
        """每个技能的等级/下一级/说明（0–1 熟练度按 10 级换算）。"""
        rows: list[dict[str, Any]] = []
        for name, value in sorted((ent.skills or {}).items()):
            val = max(0.0, min(1.0, float(value)))
            level = min(10, int(val * 10) + 1)
            next_level = level + 1 if level < 10 else None
            next_at = round(min(1.0, level * 0.1), 4) if level < 10 else None
            rows.append(
                {
                    "name": str(name),
                    "value": round(val, 4),
                    "level": int(level),
                    "next_level": next_level,
                    "next_at": next_at,
                    "to_next": round(max(0.0, (next_at or val) - val), 4) if next_at is not None else 0.0,
                    "description": self._SKILL_TEXT.get(str(name), f"{name}行当的基本功。"),
                }
            )
        return rows

    def _martial_skills_view(self, ent) -> list[dict[str, Any]]:
        """xkx 技能等级（skill_levels/skill_xp）：成长线写入的真实成长值。"""
        meta = {}
        for raw in getattr(self.content, "skills", []) or []:
            if isinstance(raw, dict) and raw.get("id"):
                meta[str(raw["id"])] = raw
        rows: list[dict[str, Any]] = []
        for sid in sorted(set(ent.skill_levels) | set(ent.skill_xp)):
            level = int(ent.skill_levels.get(sid, 0))
            xp = int(ent.skill_xp.get(sid, 0))
            if level <= 0 and xp <= 0:
                continue
            row = meta.get(sid) or {}
            limit = (level + 1) * (level + 1)
            rows.append(
                {
                    "id": sid,
                    "name": str(row.get("name") or sid),
                    "type": str(row.get("type") or ""),
                    "level": level,
                    "learned": xp,
                    "next_learned": limit,
                    "to_next": max(0, limit - xp),
                }
            )
        return rows

    def _equipment_view(self, ent) -> dict[str, Any]:
        slots = []
        for slot, label in SLOT_LABELS.items():
            item = ent.equipped.get(slot) or ""
            meta = self._item_meta(item) if item else {}
            slots.append(
                {
                    "slot": slot,
                    "slot_name": label,
                    "empty": not bool(item),
                    "item": item or None,
                    "name": self._item_name(item) if item else "",
                    "category": meta.get("category", ""),
                    "description": meta.get("description", ""),
                }
            )
        # 内容包 equip_slot：护具按 equip_slot 分槽，兵器统一进 weapon 槽。
        counts: dict[str, int] = {}
        for row in (self.content.items or {}).values():
            slot = str(row.get("equip_slot") or "")
            if slot:
                counts[slot] = counts.get(slot, 0) + 1
        xkx_slots = [
            {
                "slot": slot,
                "label": SLOT_LABELS.get(slot, XKX_EQUIP_SLOT_LABELS.get(slot, slot)),
                "items": counts[slot],
            }
            for slot in sorted(counts)
        ]
        return {
            "slots": slots,
            "weapon_type": str(getattr(ent, "weapon_type", "") or ""),
            "applied": dict(sorted((getattr(ent, "equipped_apply", None) or {}).items())),
            "effects": {
                "cultivate_bonus_pct": int(round(equip_cultivate_bonus(self.content, ent) * 100)),
            },
            "xkx_slots": xkx_slots,
            "xkx_slots_available": True,
            "xkx_slots_reason": "",
        }


    def _item_prices(self, prices: dict[str, tuple[int, int]] | None, item: str) -> tuple[int, int]:
        """一次读取内的成交价缓存：`(买入价, 卖出价)`，未知物品给 (0, 0)。

        供需乘数会扫全城库存，而同一个 tick 内只读快照不会改变库存，
        因此按 tick 缓存，tick 变化（任何写动作都会推进 tick）即失效。
        """
        if item not in self.content.items:
            return (0, 0)
        tick = int(self.state.tick)
        local = self._read_local
        if tick != getattr(local, "price_tick", -1):
            local.price_cache = {}
            local.price_tick = tick
        buy = getattr(local, "price_cache", {}).get(item)
        if buy is None:
            buy = int(self.content.item_price(item, self.state))
            local.price_cache[item] = buy
        ratio = float(self.content.economy.get("sell_ratio", 0.6))
        pair = (buy, max(1, int(round(buy * ratio))))
        if prices is not None:
            prices[item] = pair
        return pair

    def _wallet_view(self, pid: str, prices: dict[str, tuple[int, int]] | None = None) -> dict[str, Any]:
        from ..actions.common import wallet as wallet_of

        ent = self.state.entities[pid]
        return {
            "money": int(self.state.account_balance(wallet_of(pid))),
            "items": [
                {
                    "item": item,
                    "name": self._item_name(item),
                    "qty": int(qty),
                    # 只加不改：原 item/name/qty 语义不变，价格供 UI 直接展示。
                    "price": self._item_prices(prices, item)[0],
                    "sell_price": self._item_prices(prices, item)[1],
                    **self._item_meta(item),
                }
                for item, qty in sorted(ent.inventory.items())
                if int(qty) > 0
            ],
        }

    def _location_view(
        self,
        loc_id: str,
        pid: str = "",
        prices: dict[str, tuple[int, int]] | None = None,
    ) -> dict[str, Any]:
        row = (self.state.locations or {}).get(loc_id) or {}
        exits = []
        for nxt in sorted(str(x) for x in (row.get("exits") or [])):
            exits.append(
                {
                    "id": nxt,
                    "name": self._loc_name(nxt),
                    "exists": nxt in self.state.locations,
                }
            )
        services = sorted(str(x) for x in (row.get("services") or []))
        # 一次地点快照只扫一遍本地点商业体：逐件调 business_selling 会重复扫全城。
        stock_by_item: dict[str, Any] = {}
        for biz in self.state.business_at(loc_id):
            for item, qty in biz.stock.items():
                if int(qty) > 0 and item not in stock_by_item:
                    stock_by_item[item] = biz
        detail = []
        for item in services:
            if item not in self.content.items:
                detail.append(
                    {
                        "item": item, "name": item, "category": "",
                        "price": 0, "sell_price": 0, "tax": 0,
                        "stock": 0, "available": False, "business": "",
                        "business_name": "", "buy_limit": 0, "sell_limit": 0,
                    }
                )
                continue
            unit_price, unit_sell = self._item_prices(prices, item)
            biz = stock_by_item.get(item)
            stock = int(biz.stock.get(item, 0)) if biz is not None else 0
            detail.append(
                {
                    "item": item,
                    "name": self._item_name(item),
                    "category": str((self.content.items.get(item) or {}).get("category", "")),
                    "price": unit_price,
                    "sell_price": unit_sell,
                    "tax": int(transaction_tax(self.content, unit_price)),
                    "stock": stock,
                    "available": biz is not None,
                    "business": biz.id if biz is not None else "",
                    "business_name": biz.name if biz is not None else "",
                    "buy_limit": self._buy_limit(pid, unit_price, stock) if pid and biz else 0,
                    "sell_limit": self._sell_limit(pid, item, unit_sell, loc_id) if pid else 0,
                    **self._item_meta(item),
                }
            )
        return {
            "id": loc_id,
            "name": str(row.get("name") or loc_id),
            "description": str(row.get("description") or ""),
            "region": row.get("region"),
            "region_name": row.get("region_name"),
            "exits": exits,
            # 原字段保持不变（仍是 item id 列表）；价格与买卖上限走并列的新字段。
            "services": services,
            "services_detail": detail,
        }

    def _shop_view(
        self,
        pid: str,
        loc_id: str,
        prices: dict[str, tuple[int, int]] | None = None,
    ) -> dict[str, Any]:
        """当前地点的买卖面板：价格、库存、可买/可卖上限（只读派生）。"""
        ent = self.state.entities.get(pid)
        businesses = self.state.business_at(loc_id)
        buy_rows = []
        seen: set[str] = set()
        for biz in businesses:
            for item, qty in sorted(biz.stock.items()):
                if int(qty) <= 0 or item in seen or item not in self.content.items:
                    continue
                seen.add(item)
                unit_price = self._item_prices(prices, item)[0]
                tax = int(transaction_tax(self.content, unit_price))
                buy_rows.append(
                    {
                        "item": item,
                        "name": self._item_name(item),
                        "category": str((self.content.items.get(item) or {}).get("category", "")),
                        "price": unit_price,
                        "tax": tax,
                        "total": unit_price + tax,
                        "stock": int(qty),
                        "available": True,
                        "unavailable_reason": "",
                        "business": biz.id,
                        "business_name": biz.name,
                        "buy_limit": self._buy_limit(pid, unit_price, int(qty)),
                        **self._item_meta(item),
                    }
                )
        # 售罄商品仍留在面板里：地点 services 是货架目录，库存为 0 时给 stock=0
        # 与明确原因，避免商品“凭空消失”让玩家以为店铺坏了。
        loc_row = self.state.locations.get(loc_id) or {}
        for item in sorted(str(x) for x in (loc_row.get("services") or [])):
            if item in seen or item not in self.content.items:
                continue
            seen.add(item)
            unit_price = self._item_prices(prices, item)[0]
            tax = int(transaction_tax(self.content, unit_price))
            buy_rows.append(
                {
                    "item": item,
                    "name": self._item_name(item),
                    "category": str((self.content.items.get(item) or {}).get("category", "")),
                    "price": unit_price,
                    "tax": tax,
                    "total": unit_price + tax,
                    "stock": 0,
                    "available": False,
                    "unavailable_reason": "sold_out",
                    "business": "",
                    "business_name": "",
                    "buy_limit": 0,
                    **self._item_meta(item),
                }
            )
        sell_rows = []
        if ent is not None:
            for item, qty in sorted(ent.inventory.items()):
                if int(qty) <= 0 or item not in self.content.items:
                    continue
                unit_sell = self._item_prices(prices, item)[1]
                sell_rows.append(
                    {
                        "item": item,
                        "name": self._item_name(item),
                        "category": str((self.content.items.get(item) or {}).get("category", "")),
                        "qty": int(qty),
                        "price": self._item_prices(prices, item)[0],
                        "sell_price": unit_sell,
                        "sell_limit": self._sell_limit(pid, item, unit_sell, loc_id),
                        **self._item_meta(item),
                    }
                )
        # 玩家自己的店：优先当前地点，否则按 id 升序取第一；没有则空形状。
        owned = sorted(
            (b for b in self.state.businesses.values() if b.owner == pid),
            key=lambda b: b.id,
        )
        own = next((b for b in owned if b.location == loc_id), owned[0] if owned else None)
        tick = int(self.state.tick)
        employees: list[dict[str, Any]] = []
        stock_rows: list[dict[str, Any]] = []
        stock_count = 0
        cash = 0
        today_revenue = 0
        today_wage = 0
        today_tax = 0
        tax_arrears = 0
        if own is not None:
            unpaid_by: dict[str, int] = {}
            for debt in self.state.debts.values():
                if debt.status != "active" or debt.note != "unpaid_wage" or debt.debtor != own.id:
                    continue
                unpaid_by[debt.creditor] = unpaid_by.get(debt.creditor, 0) + int(debt.amount)
            for eid in sorted(own.employees):
                worker = self.state.entities.get(str(eid))
                employees.append(
                    {
                        "name": worker.name if worker is not None else str(eid),
                        "position": str(own.employee_positions.get(str(eid), "")),
                        "wage": int(own.employee_wages.get(str(eid), 0)),
                        # 当日是否到店：雇工实体存活且就在本店地点。
                        "present": bool(
                            worker is not None
                            and worker.status == "alive"
                            and worker.location == own.location
                        ),
                        "unpaid_wage": int(unpaid_by.get(str(eid), 0)),
                    }
                )
            for item, qty in sorted(own.stock.items()):
                qty = int(qty)
                # 托管货物（escrow:<镖单>:<item>）不属于店铺可售库存。
                if qty <= 0 or str(item).startswith("escrow:") or item not in self.content.items:
                    continue
                stock_rows.append(
                    {
                        "item": item,
                        "name": self._item_name(item),
                        "qty": qty,
                        "sell_price": self._item_prices(prices, item)[1],
                    }
                )
                stock_count += qty
            cash = int(self.state.account_balance(own.cash_account))
            today_revenue = int(own.day_revenue)
            today_wage = sum(int(v) for v in own.employee_wages.values())
            today_tax = int(daily_shop_tax(self.content, own.kind))
            tax_arrears = int(own.tax_arrears)
        return {
            "location": loc_id,
            "businesses": [
                {
                    "id": biz.id,
                    "name": biz.name,
                    "kind": biz.kind,
                    "cash": int(self.state.account_balance(biz.cash_account)),
                }
                for biz in businesses
            ],
            "buy": buy_rows,
            "sell": sell_rows,
            "employees": employees,
            "cash": cash,
            "stock_count": stock_count,
            "stock": stock_rows,
            "today_revenue": today_revenue,
            "today_wage": today_wage,
            "today_tax": today_tax,
            "tax_arrears": tax_arrears,
            "last_settle_tick": tick - tick % TICKS_PER_DAY,
        }

    def _present_view(self, pid: str, loc_id: str) -> list[dict[str, Any]]:
        # 同一 tick 内遍历一次关系，按对方取双向 active 关系里的最大 trust。
        trust_by: dict[str, float] = {}
        for rel in self.state.relations.values():
            if rel.status != "active":
                continue
            if rel.a == pid:
                other = rel.b
            elif rel.b == pid:
                other = rel.a
            else:
                continue
            trust_by[other] = max(trust_by.get(other, 0.0), float(rel.trust))
        rows = []
        npc_meta = {}
        for raw in getattr(self.content, "npcs", []) or []:
            if isinstance(raw, dict) and raw.get("id"):
                npc_meta[str(raw["id"])] = raw
        for ent in sorted(self.state.entities.values(), key=lambda e: e.id):
            if ent.id == pid or str(ent.id).startswith("player:"):
                continue
            if ent.location != loc_id or ent.status not in ("alive", "unconcious"):
                continue
            # 同模板多实例没有自己的元数据，显示名/身份回到模板那一行。
            meta = npc_meta.get(ent.template or ent.id) or {}
            top = max(ent.skills.items(), key=lambda kv: (float(kv[1]), kv[0])) if ent.skills else None
            rows.append(
                {
                    "id": ent.id,
                    "name": str(meta.get("display_name") or ent.name),
                    "role": ent.role,
                    "location": ent.location,
                    "presence": ent.presence,
                    # P4a：参考快照初始状态（unconcious）也要可见；look 据此追加
                    # `<昏迷不醒>`，其余状态与原版一致（dead 不显示）。
                    "status": ent.status,
                    "doing": "",
                    # 同模板多实例：模板维度供 look 数量分组，槽位供 fight 按序号取目标。
                    # 规范实例 template 为空、instance 为 0，行为与改造前一致。
                    "template": ent.template or ent.id,
                    "instance": int(ent.instance),
                    "is_master": bool(meta.get("is_master")),
                    "sect": str(meta.get("sect") or ""),
                    "skills": (
                        {"name": str(top[0]), "level": round(float(top[1]), 4)}
                        if top is not None
                        else None
                    ),
                    # 只增不改：每个技能的等级/下一级/说明，不再只有主修一行。
                    "skills_detail": self._skills_detail(ent),
                    "employed_by": ent.employed_at or ent.business or None,
                    "bond": float(self.state.affection_between(pid, ent.id)),
                    "trust": float(trust_by.get(ent.id, 0.0)),
                }
            )
        return rows

    def _online_players_view(self, pid: str) -> list[dict[str, Any]]:
        rows = []
        for other_id in sorted(self.state.players):
            prof = self.state.players[other_id]
            ent = self.state.entities.get(other_id)
            if not prof.online or ent is None:
                continue
            rows.append(
                {
                    "id": other_id,
                    "name": ent.name,
                    "archetype": prof.archetype,
                    "role": ent.role,
                    "location": ent.location,
                    "presence": ent.presence,
                    "self": other_id == pid,
                }
            )
        return rows

    def _quest_board_view(self, pid: str, loc_id: str) -> dict[str, Any]:
        open_rows = []
        locked_rows = []
        active_rows = []
        active_count = sum(
            1
            for q in self.state.quests.values()
            if q.status == "accepted" and q.acceptor == pid
        )
        player = self.state.entities.get(pid)
        tick = int(self.state.tick)
        player_rep = reputation_points(self.state, pid) if player is not None else 0.0
        for quest in self._sorted_quests():
            if quest.status == "open":
                row = self._quest_row(quest)
                reason = self._quest_lock_reason(quest, player_rep)
                if reason:
                    # 不直接消失：放进 locked[] 并附原因，前端可解释为什么这单接不了。
                    row.update(
                        {
                            "acceptable": False,
                            "can_accept_here": False,
                            "accepted_by_me": False,
                            "expired": reason == "quest_expired",
                            "lock_reason": reason,
                            "lock_text": _reason_text(reason),
                        }
                    )
                    locked_rows.append(row)
                    continue
                giver = self.state.entities.get(quest.giver)
                can_accept = bool(
                    giver.location == loc_id
                    and active_count < 2
                    and player is not None
                    and not player.escort_id
                )
                row["acceptable"] = can_accept
                row["can_accept_here"] = can_accept
                row["accepted_by_me"] = False
                row["expired"] = False
                open_rows.append(row)
            elif quest.status == "accepted" and quest.acceptor == pid:
                row = self._quest_row(quest)
                row["acceptable"] = False
                row["can_accept_here"] = False
                row["accepted_by_me"] = True
                row["expired"] = bool(quest.deadline_tick) and tick > quest.deadline_tick
                active_rows.append(row)
        return {
            "counts": {
                "open": len(open_rows),
                "acceptable_here": sum(1 for row in open_rows if row["acceptable"]),
                "accepted_by_me": len(active_rows),
            },
            "rows": open_rows,
            "locked": locked_rows,
            "active": active_rows,
        }

    def _intent_view(self, event, found: bool) -> dict[str, Any]:
        raw = str(event.reason or "")
        code = raw[len("rejected:"):] if raw.startswith("rejected:") else ""
        return {
            "ok": event.outcome == "ok",
            "found": found,
            "profile_version": PROFILE_VERSION,
            "intent_id": str(event.intent_id),
            "event_seq": int(event.seq),
            "tick": int(event.tick),
            "verb": str(event.verb),
            "outcome": str(event.outcome),
            "reject_reason": code,
            "reason_text": _reason_text(code) if code else "",
            "status": "executed" if event.outcome == "ok" else "rejected",
        }

    # ----------------------------------------------------------------- write
    def perform(
        self,
        account: str,
        action: str,
        payload: dict[str, Any] | None = None,
        request_id: str = "",
        advance: bool = True,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        with self.lock:
            cache_key = f"{account}:{request_id}" if request_id else ""
            if cache_key and cache_key in self.requests:
                cached = self.requests[cache_key]
                cached.setdefault("status", "executed" if cached.get("ok") else "rejected")
                return cached
            response = self._perform(account, action, payload, advance=advance)
            response.setdefault("profile_version", PROFILE_VERSION)
            response.setdefault("status", "executed" if response.get("ok") else "rejected")
            if cache_key:
                self.requests[cache_key] = response
                self.requests.move_to_end(cache_key)
                self.intents[request_id] = response
                self.intents.move_to_end(request_id)
                if response.get("intent_id"):
                    intent_id = str(response["intent_id"])
                    self.intents[intent_id] = response
                    self.intents.move_to_end(intent_id)
                while len(self.requests) > self._recent_max:
                    old_key, _ = self.requests.popitem(last=False)
                    self.intents.pop(old_key.split(":", 1)[-1], None)
                while len(self.intents) > self._recent_max * 2:
                    self.intents.popitem(last=False)
            return response

    def _xkx_shell_instance(self):
        """单例文字壳：help 目录与命令路由共用同一份只读帮助数据。"""
        from .xkx_shell import XKXShell

        shell = getattr(self, "_xkx_shell", None)
        if shell is None:
            with self.lock:
                shell = getattr(self, "_xkx_shell", None)
                if shell is None:
                    shell = XKXShell(self)
                    self._xkx_shell = shell
        return shell

    def xkx_command(
        self,
        account: str,
        payload: dict[str, Any] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        """xkx 文字壳入口：把一行自然语言命令翻译成引擎动作。"""
        shell = self._xkx_shell_instance()
        return shell.execute(str(account or ""), dict(payload or {}), request_id=str(request_id or ""))

    def xkx_help_catalog(self) -> dict[str, Any]:
        """`GET /api/xkx/help`：153 命令 / 32 主题的只读目录，供游戏内 help 与补全。"""
        return self._xkx_shell_instance().catalog()

    def resume(
        self,
        account: str,
        *,
        session_id: str = "",
        token: str = "",
        expires_at: int = 0,
        new_session: bool = False,
        tick_at_disconnect: int | None = None,
        reconnect_grace: int = 60,
    ) -> dict[str, Any]:
        """重连恢复：不推进 tick，只把离线档案重新上线并返回完整快照。

        60 秒内与超时后的区别由 `AccountStore.reconnect` 决定；这里只保证
        位置/钱包/任务/成长线来自事件日志，不因重连被重置。
        """
        account = str(account or "").strip().lower()
        with self.lock:
            if not ACCOUNT_RE.match(account):
                return {
                    "ok": False,
                    "profile_version": PROFILE_VERSION,
                    "resumed": True,
                    "new_session": bool(new_session),
                    "session_id": session_id,
                    "token": token,
                    "token_type": "Bearer",
                    "expires_at": int(expires_at),
                    "tick": int(self.state.tick),
                    "tick_at_disconnect": int(tick_at_disconnect or 0),
                    "reconnect_grace_seconds": int(reconnect_grace),
                    "reject_reason": "bad_account",
                    "reason_text": _reason_text("bad_account"),
                }
            pid = self._pid(account)
            # 重连即视为活跃：宽限期内的静置不应在恢复后被判离线。
            self._last_active[pid] = self._presence_clock()
            prof = self.state.players.get(pid)
            if prof is None or not prof.online:
                # advance=False：重连不额外推进 tick；enter 自身仍写入事件日志。
                self._perform(account, "enter", {"account_id": account}, advance=False)
            state = self.player_state(account)
            payload: dict[str, Any] = {
                "ok": bool(state.get("ok")),
                "profile_version": PROFILE_VERSION,
                "resumed": True,
                "new_session": bool(new_session),
                "session_id": session_id,
                "token": token,
                "token_type": "Bearer",
                "expires_at": int(expires_at),
                "tick": int(self.state.tick),
                "tick_at_disconnect": int(
                    tick_at_disconnect if tick_at_disconnect is not None else self.state.tick
                ),
                "reconnect_grace_seconds": int(reconnect_grace),
                "state": state,
            }
            if state.get("ok"):
                payload["player"] = state["player"]
                payload["quests"] = state["player"]["active_quests"]
            else:
                payload["reject_reason"] = str(state.get("reject_reason") or "")
                payload["reason_text"] = str(state.get("reason_text") or "")
            return payload

    def keepalive(self, account: str) -> dict[str, Any]:
        """轻量保活：只刷新本机会话的活跃时钟，不写事件、不推进 tick。

        与 `player_heartbeat` 写事件不同，这里是页面停留期间的“我还在这里”，
        避免 60 秒静置被误判离线；真正离线时如实返回 `player_offline`，
        由客户端走 enter 恢复。
        """
        account = str(account or "").strip().lower()
        with self._read_snapshot() as snapshot:
            tick = int(snapshot.tick)
            if not ACCOUNT_RE.match(account):
                return {
                    "ok": False,
                    "online": False,
                    "profile_version": PROFILE_VERSION,
                    "reject_reason": "bad_account",
                    "reason_text": _reason_text("bad_account"),
                    "tick": tick,
                    "server_tick": tick,
                }
            pid = self._pid(account)
            prof = snapshot.players.get(pid)
            if prof is None or pid not in snapshot.entities:
                return {
                    "ok": False,
                    "online": False,
                    "profile_version": PROFILE_VERSION,
                    "reject_reason": "player_not_entered",
                    "reason_text": _reason_text("player_not_entered"),
                    "tick": tick,
                    "server_tick": tick,
                }
            if not prof.online:
                return {
                    "ok": False,
                    "online": False,
                    "profile_version": PROFILE_VERSION,
                    "reject_reason": "player_offline",
                    "reason_text": _reason_text("player_offline"),
                    "tick": tick,
                    "server_tick": tick,
                }
            self._last_active[pid] = self._presence_clock()
            return {
                "ok": True,
                "online": True,
                "activity": "active",
                "idle_seconds": 0.0,
                "profile_version": PROFILE_VERSION,
                "tick": tick,
                "server_tick": tick,
            }

    def _perform(
        self, account: str, action: str, payload: dict[str, Any], advance: bool = True
    ) -> dict[str, Any]:
        account = str(account or "").strip().lower()
        tick = self.state.tick
        if not ACCOUNT_RE.match(account):
            return self._reject_early(account, action, "bad_account", tick)
        pid = self._pid(account)
        if action in PLAYER_UNIMPLEMENTED_VERBS:
            return self._reject_early(account, action, "not_implemented_for_player", tick)
        verb = WRITE_ACTIONS.get(action)
        if verb is None:
            return self._reject_early(account, action, "unknown_action", tick)
        if verb in FORBIDDEN_VERBS or (
            verb not in PLAYER_VERBS and not self._xkx_extra_verb_allowed(verb)
        ):
            return self._reject_early(account, action, "forbidden_verb", tick)

        prof = self.state.players.get(pid)
        now = self._presence_clock()
        if action != "enter":
            if prof is None:
                return self._reject_early(account, action, "player_not_entered", tick)
            if not prof.online:
                return self._reject_early(account, action, "player_offline", tick)
            idle = now - self._last_active.get(pid, now)
            if idle > self._presence_offline:
                # 只有玩家自己的 wall-clock 静置超过宽限期才判离线；全局 tick 不参与判定。
                self._mark_offline(pid, tick)
                return self._reject_early(account, action, "player_offline", tick)
        self._last_active[pid] = now
        if verb not in ("move", "talk", "trade", "quest_accept", "quest_deliver"):
            allowed = self.content.allowed_roles(verb)
            if allowed is not None and "*" not in allowed and prof is not None and prof.role not in allowed:
                return self._reject_early(account, action, "role_not_allowed", tick)

        args = self._args(action, pid, account, payload)
        # 玩家可读原因码：用现有内容/状态助手做只读前置判定，命中即写 reject 事件；
        # 未命中仍交给原动作执行（不复制任何交易效果）。
        reason = self._precheck_player_action(action, pid, args, tick)
        if reason:
            return self._reject_early(account, action, reason, tick)
        ent_before = self.state.entities.get(pid)
        loc_before = ent_before.location if ent_before is not None else ""
        intent = Intent(actor=pid, verb=verb, args=args, tick=tick, source="player")
        created = prof is None
        event = self._submit(intent)
        # 婚姻是唯一的多事件链：求婚 -> NPC 裁决事件 -> 成婚。
        # 所有后续事件都由服务端按确定性裁决生成，客户端无法伪造。
        marriage_decision = ""
        if verb == "marriage_propose" and event.outcome == "ok":
            meta = self._event_marriage_meta(event)
            marriage_decision = str(meta.get("decision") or "")
            target = str(args.get("target") or "")
            if marriage_decision == "accept":
                accept_event = self._submit(
                    Intent(
                        actor=target,
                        verb="marriage_accept",
                        args={"player": pid, "target": target, "proposal_tick": int(event.tick)},
                        tick=int(self.state.tick),
                        source="npc",
                        reason=f"{pid}求婚，{target}接受",
                    )
                )
                if accept_event.outcome == "ok":
                    event = self._submit(
                        Intent(
                            actor="system",
                            verb="marriage",
                            args={
                                "a": pid,
                                "b": target,
                                "gift_money": int(args.get("gift_money") or 0),
                                "gift_item": str(args.get("gift_item") or ""),
                                "gift_qty": int(args.get("gift_qty") or 1)
                                if args.get("gift_item")
                                else 0,
                                "proposal_tick": int(accept_event.tick),
                            },
                            tick=int(self.state.tick),
                            source="system",
                            reason=f"{pid}与{target}成婚",
                        )
                    )
                else:
                    event = accept_event
            elif marriage_decision == "reject":
                event = self._submit(
                    Intent(
                        actor=target,
                        verb="marriage_reject",
                        args={
                            "player": pid,
                            "target": target,
                            "reason_code": str(meta.get("reason_code") or "bond_too_low"),
                        },
                        tick=int(self.state.tick),
                        source="npc",
                        reason=f"{pid}求婚，{target}婉拒",
                    )
                )
        # 首次进入的一次性初始资产是独立事件：enter 本身仍不铸币，
        # `player_endowment` 显式 mint + 造物，按 account_id 幂等。
        grant = None
        if action == "enter" and event.outcome == "ok":
            prof_now = self.state.players.get(pid)
            if prof_now is not None and not prof_now.endowed:
                grant = self._submit(
                    Intent(
                        actor=pid,
                        verb="player_endowment",
                        args={"account_id": account, "player_id": pid, "reason": "first_entry"},
                        tick=int(event.tick),
                        source="player",
                    )
                )
        # 原版 `feature/attack.lpc` 的 aggressive `init()`：对象移入环境时触发，
        # 这里只在“进入新房间”的写动作成功后判定一次；真正的出手由 ambient
        # 每 tick 的 continue 结算，给玩家留出和原版一样的下一个心跳窗口。
        aggression: list[dict[str, Any]] = []
        if event.outcome == "ok" and action in ("enter", "move", "climb", "jump", "revive"):
            ent_after = self.state.entities.get(pid)
            if ent_after is not None and ent_after.status == "alive" and (
                action == "enter" or ent_after.location != loc_before
            ):
                aggression = self._trigger_npc_aggression(pid, int(event.tick))
        if advance:
            # 上工/修炼按声明小时真实推进世界钟（1 小时 = TICKS_PER_HOUR tick），
            # 不能只推 1 小时却让界面假装 N 小时；其余动作仍按 tick_step。
            step = None
            if action in ("rest", "sleep", "work", "cultivate") and event.outcome == "ok":
                hours = event.args.get("hours")
                if isinstance(hours, int) and not isinstance(hours, bool):
                    step = hours * TICKS_PER_HOUR
            before = int(self.state.tick)
            self._advance(step)
            advanced = int(self.state.tick) - before
        else:
            advanced = 0
        data = self._status_view(pid) if event.outcome == "ok" else {}
        response = self._response(event, data, action, pid)
        response["next_tick"] = self.state.tick
        # 实际推进了多少世界钟（所有写动作都带，便于前端按真实耗时动画）。
        response["duration_ticks"] = int(advanced)
        response["duration_hours"] = round(float(advanced) / TICKS_PER_HOUR, 2)
        if action in ("marriage_propose", "marriage_end"):
            response["details"]["decision"] = (
                "ended" if action == "marriage_end" and event.outcome == "ok" else marriage_decision
            )
            if data:
                response["details"]["marriage"] = dict(data.get("marriage") or {})
        if aggression:
            response["details"]["aggression"] = aggression
        if action == "enter" and event.outcome == "ok":
            response["details"]["created"] = created
            response["details"]["resumed"] = not created
            response["details"]["role"] = data.get("role", "")
            response["details"]["endowment"] = self._endowment_view(
                self.state.players.get(pid), grant
            )
        return response

    def _event_marriage_meta(self, event) -> dict[str, Any]:
        for eff in event.effects or []:
            if eff.get("op") == "marriage_meta":
                return dict(eff.get("data") or {})
        return {}

    def _marriage_view(self, pid: str) -> dict[str, Any]:
        """当前婚姻/求处快照；只读派生，不写状态。"""
        from ..actions.marriage import pair_key

        ent = self.state.entities.get(pid)
        if ent is not None and ent.spouse:
            spouse = self.state.entities.get(ent.spouse)
            rec = self.state.marriages.get(pair_key(pid, ent.spouse), {})
            return {
                "status": "married",
                "spouse_id": ent.spouse,
                "spouse_name": spouse.name if spouse is not None else ent.spouse,
                "since_tick": int(rec.get("since_tick") or 0),
                "last_daily_tick": int(rec.get("last_daily_tick") or 0),
                "daily_note": str(rec.get("daily_note") or ""),
            }
        pending: dict[str, Any] | None = None
        for rec in self.state.marriages.values():
            if pid not in (rec.get("a"), rec.get("b")):
                continue
            if rec.get("status") not in ("proposed", "accepted", "courtship"):
                continue
            if pending is None or int(rec.get("proposal_tick") or 0) >= int(
                pending.get("proposal_tick") or 0
            ):
                pending = rec
        if pending is not None:
            other = str(pending.get("b") if pending.get("a") == pid else pending.get("a"))
            spouse = self.state.entities.get(other)
            return {
                "status": str(pending.get("status")),
                "spouse_id": other,
                "spouse_name": spouse.name if spouse is not None else other,
                "since_tick": int(pending.get("since_tick") or 0),
                "expires_tick": int(pending.get("expires_tick") or 0),
                "reason_code": str(pending.get("reason_code") or ""),
            }
        return {
            "status": "single",
            "spouse_id": "",
            "spouse_name": "",
            "since_tick": 0,
            "last_daily_tick": 0,
            "daily_note": "",
        }

    def _endowment_view(self, prof, grant) -> dict[str, Any]:
        """`details.endowment`：本次是否发放、钱的来源与可审计总额。"""
        granted_now = bool(grant is not None and grant.outcome == "ok")
        code = ""
        if grant is not None and grant.outcome != "ok":
            raw = str(grant.reason or "")
            code = raw[len("rejected:"):] if raw.startswith("rejected:") else raw
        source = str(getattr(prof, "endowed_source", "") or "")
        return {
            "granted": granted_now,
            "already_granted": bool(prof is not None and prof.endowed) and not granted_now,
            "money": STARTING_MONEY if prof is not None and prof.endowed else 0,
            "items": [
                {"item": item, "name": self._item_name(item), "qty": qty}
                for item, qty in sorted(STARTING_ITEMS.items())
            ],
            "money_source": source,
            "item_source": (
                CARRY_SOURCE
                if source == ENDOWMENT_SOURCE
                else RUNTIME_CARRY_SOURCE if source == RUNTIME_ENDOWMENT_SOURCE else ""
            ),
            "event_seq": int(grant.seq) if granted_now else 0,
            "endowed_tick": int(prof.endowed_tick) if prof is not None else 0,
            # 兼容旧字段，但含义已修正为“来源是否创世预置”，不再表示“本次未发放”。
            "granted_at_genesis": bool(prof is not None and prof.endowed_source == ENDOWMENT_SOURCE),
            "endowment_total": int(self.state.endowment_total),
            "cap": ENDOWMENT_TOTAL_CAP,
            "reject_reason": code,
            "reason_text": _reason_text(code) if code else "",
            "note": "首次进入一次性发放；同账号重复进入不再发放。跨 run 重启是新世界，可重新领取。",
        }

    def _args(self, action: str, pid: str, account: str, payload: dict[str, Any]) -> dict[str, Any]:
        # 先把下划线别名归一成规范动词，否则 quest_accept 会漏掉 quest 参数。
        action = ACTION_ALIASES.get(action, action)
        base: dict[str, Any] = {"account_id": account, "player_id": pid}
        if action == "enter":
            prof = self.state.players.get(pid)
            ent = self.state.entities.get(pid)
            at = (ent.location if ent is not None else self.spawn)
            base.update(
                {
                    "display_name": str(payload.get("display_name") or self.display_name),
                    "archetype": str(payload.get("archetype") or ""),
                    "at": at,
                    "player_start": self.player_start,
                }
            )
            return base
        if action in ("bai", "learn", "practice", "climb", "jump"):
            if action == "bai":
                base["target"] = str(payload.get("target") or "")
            elif action == "learn":
                base["teacher"] = str(payload.get("teacher") or "")
                base["skill"] = str(payload.get("skill") or "")
                base["times"] = payload.get("times", 1)
            elif action == "practice":
                base["skill"] = str(payload.get("skill") or "")
                base["times"] = payload.get("times", 1)
            else:
                base["arg"] = str(payload.get("arg") or "")
            return base
        if action == "revive":
            return base
        if action == "move":
            base["to"] = str(payload.get("to") or "")
            return base
        if action == "talk":
            base["target"] = str(payload.get("target") or "")
            base["message"] = str(payload.get("message") or "")
            return base
        if action == "trade":
            base["target"] = str(payload.get("target") or "")
            base["offer"] = payload.get("offer") or []
            base["want"] = payload.get("want") or []
            base["money_delta"] = payload.get("money_delta", 0)
            return base
        if action in ("quest/accept", "quest/deliver"):
            base["quest"] = str(payload.get("quest") or "")
            return base
        if action == "cultivate":
            # 客户端不指定技能时交给动作层按角色自动取；空字符串会被 arg_str 判成 bad_arg。
            skill = str(payload.get("skill") or "")
            if skill:
                base["skill"] = skill
            base["hours"] = payload.get("hours", 2)
            return base
        if action == "work":
            ent = self.state.entities.get(pid)
            base["at"] = str(payload.get("at") or (ent.location if ent is not None else ""))
            base["hours"] = payload.get("hours", 8)
            return base
        if action == "open_shop":
            ent = self.state.entities.get(pid)
            base["at"] = str(payload.get("at") or (ent.location if ent is not None else ""))
            base["shop_type"] = str(payload.get("shop_type") or "stall")
            base["name"] = str(payload.get("name") or f"{ent.name if ent else '玩家'}的摊子")
            return base
        if action == "restock":
            # 空串会被 arg_str 判成 bad_arg，因此只透传非空 item/from。
            base["item"] = str(payload.get("item") or "")
            base["qty"] = payload.get("qty", 1)
            supplier = str(payload.get("from") or "")
            if supplier:
                base["from"] = supplier
            return base
        if action == "set_price":
            base["item"] = str(payload.get("item") or "")
            base["price"] = payload.get("price")
            return base
        if action == "dismiss":
            base["target"] = str(payload.get("target") or "")
            return base
        if action == "withdraw":
            # UI 侧用 amount，后端动作用 qty；两种键都收，缺省取 qty。
            base["qty"] = payload.get("qty", payload.get("amount"))
            return base
        if action == "shop_report":
            return base
        if action == "join_sect":
            ent = self.state.entities.get(pid)
            base["target"] = str(payload.get("target") or (ent.location if ent is not None else ""))
            base["dues"] = payload.get("dues", 0)
            return base
        if action == "recruit":
            # 与 RecruitAction 一致：target / position / wage，shop 缺省为本人店铺。
            # 空串会被 arg_str 判成 bad_arg，所以只透传非空键。
            base["target"] = str(payload.get("target") or "")
            shop = str(payload.get("shop") or "")
            position = str(payload.get("position") or "")
            if shop:
                base["shop"] = shop
            if position:
                base["position"] = position
            wage = payload.get("wage")
            if wage is not None and wage != "":
                base["wage"] = wage
            return base
        if action == "buy":
            base["item"] = str(payload.get("item") or "")
            base["qty"] = payload.get("qty", 1)
            if payload.get("from") is not None:
                base["from"] = str(payload.get("from") or "")
            return base
        if action == "sell":
            base["item"] = str(payload.get("item") or "")
            base["qty"] = payload.get("qty", 1)
            if payload.get("to") is not None:
                base["to"] = str(payload.get("to") or "")
            return base
        if action == "consume":
            item = payload.get("item")
            if isinstance(item, str) and item.strip():
                base["item"] = item.strip()
            base["qty"] = payload.get("qty", 1)
            return base
        if action == "escort":
            route = payload.get("route")
            route_id = str(payload.get("route_id") or "").strip()
            if route is None and route_id:
                meta = (self.content.escort_routes or {}).get(route_id) or {}
                route = list(meta.get("route") or [])
            elif isinstance(route, str):
                route = [x.strip() for x in route.split(",") if x.strip()]
            base["route"] = route
            if route_id:
                base["route_id"] = route_id
            cargo = payload.get("cargo")
            if isinstance(cargo, dict):
                item = str(cargo.get("item") or "").strip()
                qty = cargo.get("qty", 1)
            elif isinstance(cargo, str) and cargo.strip():
                item = cargo.strip()
                qty = payload.get("cargo_qty", 1)
            else:
                item = ""
                qty = 1
            if item:
                base["cargo_item"] = item
                base["cargo_qty"] = qty
                valid_qty = isinstance(qty, int) and not isinstance(qty, bool) and qty >= 1
                if item in self.content.items and valid_qty:
                    base["cargo_value"] = self.content.item_price(item, self.state) * qty
                else:
                    base["cargo_value"] = payload.get("cargo_value", 0)
            else:
                base["cargo_value"] = payload.get("cargo_value", 100)
            base["fee"] = payload.get("fee", 0)
            return base
        if action in ("rest", "sleep"):
            base["hours"] = payload.get("hours", 4)
            return base
        if action in ("wear", "wield"):
            base["item"] = str(payload.get("item") or "")
            return base
        if action in ("remove", "unwield"):
            base["item"] = str(payload.get("item") or "")
            base["slot"] = str(payload.get("slot") or "")
            return base
        if action == "drop":
            base["item"] = str(payload.get("item") or "")
            base["qty"] = payload.get("qty", 1)
            return base
        if action == "equip":
            base["item"] = str(payload.get("item") or "")
            return base
        if action == "unequip":
            base["slot"] = str(payload.get("slot") or "")
            return base
        if action == "use_item":
            base["item"] = str(payload.get("item") or "")
            base["qty"] = payload.get("qty", 1)
            return base
        if action in XKX_EXTRA_PLAYER_VERBS:
            # 战斗动词的参数契约由 actions/ 层决定；这里透传整包，
            # 让另一路可以自行取 target/opponent/item 等字段。
            base["target"] = str(payload.get("target") or "").strip()
            for key, value in payload.items():
                if key not in base and key not in ("account_id", "player_id"):
                    base[key] = value
            return base
        if action == "marriage_propose":
            base["target"] = str(payload.get("target") or "").strip()
            money = payload.get("gift_money", 0)
            if isinstance(money, int) and not isinstance(money, bool):
                base["gift_money"] = money
            else:
                base["gift_money"] = 0
            item = str(payload.get("gift_item") or "").strip()
            if item:
                base["gift_item"] = item
                qty = payload.get("gift_qty", 1)
                base["gift_qty"] = qty if isinstance(qty, int) and not isinstance(qty, bool) else 1
            return base
        if action == "marriage_end":
            return base
        if action in (
            "found_sect",
            "recruit_disciple",
            "expel_disciple",
            "pay_disciples",
            "sect_deposit",
            "sect_withdraw",
        ):
            # sect_id 可省略：动作层缺省取本人门派，未入派返回 no_sect。
            sid = str(payload.get("sect_id") or "").strip()
            if sid:
                base["sect_id"] = sid
            if action == "found_sect":
                ent = self.state.entities.get(pid)
                base["at"] = str(payload.get("at") or (ent.location if ent is not None else ""))
                name = str(payload.get("name") or "").strip()
                if name:
                    base["name"] = name
                doctrine = str(payload.get("doctrine") or "").strip()
                if doctrine:
                    base["doctrine"] = doctrine[:60]
                return base
            if action in ("recruit_disciple", "expel_disciple"):
                base["target"] = str(payload.get("target") or "").strip()
                return base
            if action == "pay_disciples":
                day = payload.get("day")
                if isinstance(day, int) and not isinstance(day, bool):
                    base["day"] = day
                return base
            base["amount"] = payload.get("amount")
            return base
        return base

    def _precheck_player_action(
        self, action: str, pid: str, args: dict[str, Any], tick: int
    ) -> str:
        """玩家消费类动作的只读前置校验，返回可读原因码（空串表示交给动作层）。

        语义与 BuyAction / SellAction / ConsumeAction / EscortAction 的前置一致，
        只读 content / state，不产生任何效果或半写。
        """
        action = ACTION_ALIASES.get(action, action)
        if action == "marriage_propose":
            from ..actions.marriage import structural_reason

            return (
                structural_reason(
                    self.state,
                    self.content,
                    pid,
                    str(args.get("target") or ""),
                    int(args.get("gift_money") or 0),
                    str(args.get("gift_item") or "") or None,
                    int(args.get("gift_qty") or 1),
                )
                or ""
            )
        if action == "marriage_end":
            ent = self.state.entities.get(pid)
            if ent is None:
                return ""
            if ent.spouse is None:
                return "not_married"
            other = self.state.entities.get(ent.spouse)
            if other is None or other.status != "alive":
                return "spouse_not_alive"
            if ent.location != other.location:
                return "spouse_not_here"
            return ""
        if action not in ("buy", "sell", "consume", "escort"):
            return ""
        ent = self.state.entities.get(pid)
        if ent is None:
            return ""
        if action == "buy":
            item = str(args.get("item") or "")
            if not item or item not in self.content.items:
                return "item_not_available" if item else ""
            at = str(args.get("from") or ent.location)
            if at != ent.location:
                return "not_here"
            qty = args.get("qty", 1)
            if isinstance(qty, bool) or not isinstance(qty, int) or not (1 <= qty <= 20):
                return ""
            seller = business_selling(self.state, ent.location, item)
            if seller is None:
                return "not_a_merchant" if self.state.business_at(ent.location) else "shop_closed"
            cost = self.content.item_price(item, self.state) * qty
            if self.state.account_balance(wallet_account(pid)) < cost + transaction_tax(self.content, cost):
                return "not_enough_money"
            return ""
        if action == "sell":
            item = str(args.get("item") or "")
            if not item or item not in self.content.items:
                return "item_not_available" if item else ""
            at = str(args.get("to") or ent.location)
            if at != ent.location:
                return "not_here"
            qty = args.get("qty", 1)
            if isinstance(qty, bool) or not isinstance(qty, int) or not (1 <= qty <= 20):
                return ""
            if int(ent.inventory.get(item, 0)) < qty:
                return "item_missing"
            revenue = sell_price(self.content, item, self.state) * qty
            if business_with_cash(self.state, ent.location, revenue) is None:
                return "not_a_merchant" if self.state.business_at(ent.location) else "shop_closed"
            return ""
        if action == "consume":
            qty = args.get("qty", 1)
            if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
                return ""
            item = str(args.get("item") or "")
            if item and item not in self.content.items:
                return "item_not_available"
            if not item:
                item = cheapest_food(self.content, ent.location, self.state) or ""
            if not item:
                return "not_a_merchant" if self.state.business_at(ent.location) else "shop_closed"
            if business_selling(self.state, ent.location, item) is None:
                return "not_a_merchant" if self.state.business_at(ent.location) else "shop_closed"
            cost = self.content.item_price(item, self.state) * qty
            if self.state.account_balance(wallet_account(pid)) < cost + transaction_tax(self.content, cost):
                return "not_enough_money"
            return ""
        # escort：起点必须是当前位置；玩家必须带货，运费必须由目的地委托方真实钱包支付。
        route = args.get("route")
        if isinstance(route, list) and len(route) >= 2 and str(route[0]) != ent.location:
            return "not_here"
        cargo_item = str(args.get("cargo_item") or "")
        if not cargo_item:
            return "item_missing"
        if cargo_item not in self.content.items:
            return "item_not_available"
        qty = args.get("cargo_qty")
        if isinstance(qty, bool) or not isinstance(qty, int) or not (1 <= qty <= 100):
            return "bad_arg:cargo_qty"
        # 押镖货物必须是玩家背包里的真实物品：没有就先给出 item_missing，
        # 而不是放行一张空头镖单。
        if int(ent.inventory.get(cargo_item, 0)) < qty:
            return "item_missing"
        cargo_value = self.content.item_price(cargo_item, self.state) * qty
        fee = args.get("fee")
        if isinstance(fee, int) and not isinstance(fee, bool):
            # 净赚门槛：fee 覆盖货值 1.15 倍且扣税后仍为正。
            floor = max(20, int(math.ceil(cargo_value * 1.15)))
            if fee < floor or fee - cargo_value - transaction_tax(self.content, fee) <= 0:
                return "fee_too_low"
            if not isinstance(route, list) or len(route) < 2:
                return "bad_arg:route"
            meta = route_meta(self.content, str(args.get("route_id") or ""), route)
            cut = float(
                (meta or {}).get("agency_cut", (self.content.economy.get("escort") or {}).get("agency_cut", 0.30))
            )
            if not resolve_payer(self.state, str(route[-1]), fee, cut):
                return "no_payer_funds"
        return ""

    def _buy_limit(self, pid: str, unit_price: int, stock: int) -> int:
        """可买上限：库存、单笔 20 件、含税可负担三者取小。"""
        limit = min(max(0, int(stock)), 20)
        money = self.state.account_balance(wallet_account(pid))
        while limit > 0:
            cost = unit_price * limit
            if cost + transaction_tax(self.content, cost) <= money:
                break
            limit -= 1
        return limit

    def _sell_limit(self, pid: str, item: str, unit_price: int, location: str) -> int:
        """可卖上限：持有量、单笔 20 件、本地店铺现金三者取小。"""
        ent = self.state.entities.get(pid)
        if ent is None or item not in self.content.items:
            return 0
        limit = min(int(ent.inventory.get(item, 0)), 20)
        cash = max(
            (self.state.account_balance(biz.cash_account) for biz in self.state.business_at(location)),
            default=0,
        )
        while limit > 0 and unit_price * limit > cash:
            limit -= 1
        return limit

    def _submit(self, intent: Intent):
        data = {"actor": intent.actor, "verb": intent.verb, "args": intent.args,
                "tick": intent.tick, "source": intent.source}
        try:
            # 账号通过 API 校验后，服务端才授予该身份 propose 权限；权限不来自请求参数。
            # NPC/系统事件链（婚姻裁决）也由服务端构造，同样在首次提交前登记。
            if self.gateway.permission_of(intent.actor) is None:
                self.gateway.register_identity(intent.actor, "propose_intent")
            # 第一段：玩家本人 propose，只入 outbox，不改状态。
            self.gateway.call(intent.actor, "world.propose_intent", {"intent": data})
            # 第二段：运维身份申请 + 第二人批准 + 批准人 apply。
            token = self.gateway.call("ops:request", "admin.request",
                                      {"intent": data, "reason": "player_api"})["token"]
            self.gateway.call("ops:approve", "admin.approve", {"token": token})
            applied = self.gateway.call("ops:approve", "admin.apply", {"token": token})
        except GatewayError as exc:
            event = self.writer.reject(intent, f"gateway_error({exc})")
            self.tail.observe(event)
            return event
        # 常量时间取本次事务的事件（主键单行），不再为取尾部而 read_all 整条日志。
        seq = int(applied["seq"])
        event = self.tail.event_at(seq)
        if event is None or int(event.seq) != seq:
            raise RuntimeError(f"event_missing_after_apply:{seq}")
        return event

    def _advance(self, step: int | None = None) -> None:
        step = int(step or self.tick_step)
        if step <= 0:
            return
        tick = int(self.state.tick) + step
        args: dict[str, Any] = {"step": step}
        if day_of(tick) != day_of(int(self.state.tick)):
            # 日界结算合并进这一条 system_tick 事件（体力回复 → 过期委托下架 → 补发）。
            # 改前每个世界日额外写 5 条（presence/recover/quest_fail/quest_post/tick）；
            # 改后只写 1 条；system_player_presence 不再按全局 tick 批量把在线玩家标离线。
            args.update(
                {
                    "day_boundary": True,
                    "day": day_of(tick),
                    "prune_expired": True,
                }
            )
        event = self.writer.submit(
            Intent(actor="system", verb="system_tick", args=args, tick=tick, source="system")
        )
        self.tail.observe(event)
        # 玩家押镖闭环：仅当有玩家镖单到期时才补一条到达结算，不改变无镖单世界的 seq。
        if self._player_escort_due(tick):
            settle = self.writer.submit(
                Intent(actor="system", verb="system_escort_arrive", args={}, tick=tick, source="system")
            )
            self.tail.observe(settle)

    def _player_escort_due(self, tick: int) -> bool:
        return any(
            order.status == "active"
            and int(order.arrive_tick) <= int(tick)
            and str(order.carrier or "").startswith("player:")
            for order in self.state.escorts.values()
        )

    def _mark_offline(self, pid: str, tick: int) -> None:
        """把静置超宽限期的玩家落成离线事件（可回放），供下次重连走 resume/enter。"""
        prof = self.state.players.get(pid)
        if prof is None or not prof.online:
            return
        event = self.writer.submit(
            Intent(
                actor=pid,
                verb="player_leave",
                args={"player_id": pid, "reason": "presence_idle_timeout"},
                tick=int(tick),
                source="system",
            )
        )
        self.tail.observe(event)

    def _activity_of(self, pid: str, now: float | None = None) -> str:
        """active（活跃）/ idle（在线但静置）/ offline（真正离线）。"""
        prof = self.state.players.get(pid)
        if prof is None or not prof.online:
            return "offline"
        now_f = self._presence_clock() if now is None else float(now)
        idle = max(0.0, now_f - self._last_active.get(pid, now_f))
        return activity_stage(
            idle, active_seconds=self._presence_active, offline_seconds=self._presence_offline
        )

    def _reject_early(self, account: str, action: str, code: str, tick: int) -> dict[str, Any]:
        pid = f"player:{account}" if account else "api:anonymous"
        intent = Intent(
            actor=pid,
            verb=WRITE_ACTIONS.get(action, action),
            args={"account": account, "reason": code},
            tick=tick,
            source="player",
        )
        event = self.writer.reject(intent, code)
        self.tail.observe(event)
        return self._response(event, {}, action, pid)

    def _response(self, event, data: dict[str, Any], action: str, pid: str) -> dict[str, Any]:
        raw = str(event.reason or "")
        code = raw[len("rejected:"):] if raw.startswith("rejected:") else ""
        if data:
            # data 是动作后的状态快照；tick 统一为事件 tick，别和 next_tick 混淆。
            data["tick"] = int(event.tick)
        details: dict[str, Any] = {}
        for eff in event.effects or []:
            if eff.get("op") == "relation_upsert":
                rel = dict(eff.get("data") or {})
                details["relation"] = {
                    "target": rel.get("b"),
                    "kind": rel.get("kind"),
                    "affection": rel.get("affection", rel.get("affection_delta")),
                    "trust": rel.get("trust", rel.get("trust_delta")),
                }
            elif eff.get("op") == "memory_add":
                m = dict(eff.get("data") or {})
                if m.get("source") == "talk:npc":
                    details["reply"] = str(m.get("text", "")).removeprefix("我答：")
            elif eff.get("op") == "shop_report":
                # 新增的只读透传：不改任何既有视图字段，只给 shop_report 动作附带 7 日账。
                # W4 的 UI 读 details.days，同时保留 details.report（含店现/价格覆盖）。
                report = dict(eff.get("data") or {})
                details["report"] = report
                details["days"] = list(report.get("days") or [])
            elif eff.get("op") == "combat_log":
                # 战斗战报：原样透传引擎的 rounds[]/overkill，不做任何截断或重排。
                details["combat"] = dict(eff.get("data") or {})
            elif eff.get("op") == "growth_log":
                # 成长线明细：拜师/学习/练习/攀爬/跳跃/还阳，逐字段原样透传。
                details["growth"] = dict(eff.get("data") or {})
            elif eff.get("op") == "death_log":
                # 死亡结算：掉经验/掉技能/掉落/死亡计数，供壳层写“死亡后果”。
                details["death"] = dict(eff.get("data") or {})
            elif eff.get("op") == "loot_take":
                # 捡拾明细：一条 loot_take 一件物品，供壳层逐条显示。
                ld = dict(eff.get("data") or {})
                details.setdefault("loot", []).append(
                    {"item": str(ld.get("item") or ""), "qty": int(ld.get("qty") or 0)}
                )
        if action in _SECT_ACTIONS and data.get("sect") is not None:
            # 门派动作统一附带最新私有快照（与 player.sect 同源）。
            details["sect"] = data["sect"]
        if action == "pay_disciples" and event.outcome == "ok":
            dues = 0
            stipend = 0
            leavers: list[dict[str, Any]] = []
            for eff in event.effects or []:
                op = eff.get("op")
                ed = dict(eff.get("data") or {})
                if op == "transfer" and ed.get("reason") == "sect_dues":
                    dues += int(ed.get("amount") or 0)
                elif op == "transfer" and ed.get("reason") == "sect_stipend":
                    stipend += int(ed.get("amount") or 0)
                elif op == "set_field" and ed.get("field") == "faction" and ed.get("value") is None:
                    leavers.append(
                        {"target": str(ed.get("entity")), "reason": "arrears_too_high"}
                    )
            details["settlement"] = {"dues": dues, "stipend": stipend, "leavers": leavers}
        return {
            "ok": event.outcome == "ok",
            "status": "executed" if event.outcome == "ok" else "rejected",
            "profile_version": PROFILE_VERSION,
            "action": action,
            "player": pid,
            "verb": event.verb,
            "intent_id": str(event.intent_id),
            "event_seq": int(event.seq),
            "tick": int(event.tick),
            "reject_reason": code,
            "reason_text": _reason_text(code) if code else "",
            "details": details,
            "data": data,
        }

    # ------------------------------------------------------------------ ambient
    def start_ambient(
        self,
        *,
        speed: float = 1.0,
        cpu_ratio: float = 0.70,
    ) -> AmbientScheduler:
        """在同一进程、同一把写锁、同一份 events.sqlite 上启动世界自走。

        注入的是本会话已有的 Writer/EventStore/registry，绝不再建第二个写者；
        NPC 事件与玩家事件因此落在同一条 seq 序列里。
        """
        if self.ambient is not None:
            return self.ambient
        cfg = AmbientConfig(
            out_dir=self.run_dir / "ambient",
            run_id=self.run_id,
            seed=self.seed,
            npc_limit=self.npc_limit,
            speed=speed,
            max_cpu_ratio=cpu_ratio,
            parent_pid=None,
            llm_enabled=False,
            llm_max_calls=0,
            start_tick=int(self.state.tick),
            npc_wander=bool(self.npc_wander_enabled),
        )
        scheduler = AmbientScheduler(
            self.root,
            cfg,
            content=self.content,
            writer=self.writer,
            store=self.store,
            registry=self.registry,
            lock=self.lock,
        )
        self.ambient = scheduler
        self._ambient_thread = scheduler.start_background()
        return scheduler

    def start_ambient_from_env(self) -> dict[str, Any] | None:
        """`XKX_AMBIENT` 为真，或世界配置显式打开 `npc_wander` 时启动 LOD 唤醒。"""
        raw = str(os.environ.get("XKX_AMBIENT", "0")).strip().lower()
        if raw in ("", "0", "false", "off", "no") and not self.npc_wander_enabled:
            return None
        try:
            speed = float(os.environ.get("XKX_AMBIENT_SPEED") or 1.0)
        except ValueError:
            speed = 1.0
        try:
            pct = float(os.environ.get("XKX_AMBIENT_CPU") or 70.0)
        except ValueError:
            pct = 70.0
        try:
            cpu_ratio = max(0.0, min(1.0, pct / 100.0))
            scheduler = self.start_ambient(speed=speed, cpu_ratio=cpu_ratio)
        except Exception as exc:  # 自走起不来不能拖死玩家 API
            return {"ok": False, "ambient": "error", "error": f"{type(exc).__name__}: {exc}"}
        return {
            "ok": True,
            "ambient": "running",
            "ambient_tick": int(self.state.tick),
            "ambient_speed": scheduler.speed,
            "ambient_cpu_ratio": float(scheduler.cfg.max_cpu_ratio),
        }

    def stop_ambient(self) -> None:
        scheduler = self.ambient
        if scheduler is None:
            return
        scheduler.stop(source="api_shutdown")
        thread = self._ambient_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._ambient_thread = None

    def close(self) -> None:
        # 先收自走（它会写世界），再关 sqlite，避免半截写入。
        self.stop_ambient()
        self.store.close()
