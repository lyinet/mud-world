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
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

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
from ..core.clock import TICKS_PER_DAY, TICKS_PER_HOUR, day_of
from ..core.content import load_content
from ..core.models import Intent
from ..mcp.gateway import MCPGateway, GatewayError
from ..sim.genesis import build_genesis
from ..sim.scheduler import AmbientConfig, AmbientScheduler
from ..world.state import WorldState
from ..world.store import EventStore
from ..world.writer import Writer
from ..actions.escort import resolve_payer, route_meta
from ..actions.business import daily_shop_tax
from ..actions.registry import build_registry
from ..actions.common import (
    business_selling,
    business_with_cash,
    cheapest_food,
    reputation_points,
    sell_price,
    transaction_tax,
    wallet as wallet_account,
)
from .event_tail import EventTail
from .growth import growth_view, today_view
from .persistence import replay_into, verify_content_hash

# 契约冻结版本：字段名/结构变更必须同步 docs/player-api.md 的 CHANGELOG。
PROFILE_VERSION = 1

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
}

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
}

# NPC 专属动作：玩家请求必须得到明确的“未开放”，而不是未知操作。
PLAYER_UNIMPLEMENTED_VERBS = frozenset({"marry", "found_sect", "apprentice"})

FORBIDDEN_VERBS = frozenset(
    {"rob", "attack", "kill", "marry_force", "player_expel", "item_create", "mint", "set_field"}
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
    "trust_too_low": "对方对你还不够信任；先建立信任再提亲。",
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
}


def _reason_text(code: str) -> str:
    if not code:
        return ""
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
    ):
        self.root = Path(root)
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
        # 价格缓存按 tick 失效（tick 不变则库存/供需不变，读快照无需重复扫全城）。
        self._price_tick = -1
        self._price_cache: dict[str, int] = {}
        # 同一 tick 内钱包/地点/店铺视图不变（库存与钱只会随写动作推进 tick 而变）。
        self._view_tick = -1
        self._view_cache: dict[
            str,
            tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]],
        ] = {}

        self.content = load_content(self.root, allow_fixture=False, use_content=True)
        # 世界自走与玩家共用 content：feed 只认这批 NPC，玩家 ID 永不进入公共事件流。
        self.npc_limit = int(npc_limit or 0)
        self._npc_ids = frozenset(str(n["id"]) for n in self.content.npcs)
        self.ambient: AmbientScheduler | None = None
        self._ambient_thread: threading.Thread | None = None
        npcs = sorted(self.content.npcs, key=lambda n: n["id"])
        if npc_limit:
            npcs = npcs[:npc_limit]
        self.store = EventStore(self.run_dir / "events.sqlite", check_same_thread=False)
        self.registry = build_registry(self.content, seed)
        # 日界结算并入 system_tick（见 SystemTickAction）；此处把动作实例换成带 content 的版本。
        self.registry.register(SystemTickAction(self.content))
        # 玩家专属休息动词：只经 API 会话注册，不进入 NPC/系统动作链。
        self.registry.register(PlayerRestAction())
        # 存档优先：已有事件日志时回放，绝不重新创世/重复发放初始资产。
        self.replayed = self.store.count() > 0
        if self.replayed:
            try:
                verify_content_hash(self.store, self.content.content_hash, self.run_dir)
                state, player_ticks = replay_into(self.store, self.content.locations)
            except Exception:
                # 拒绝启动时释放 sqlite，避免部署脚本/测试留下锁文件。
                self.store.close()
                raise
            self.writer = Writer(state, self.store, self.registry, seed)
            self.writer._player_ticks.update(player_ticks)
            self.writer.committed_intents = self.store.count()
        else:
            state = WorldState(locations=self.content.locations)
            self.writer = Writer(state, self.store, self.registry, seed)
            genesis = build_genesis(self.content, npcs, root=self.root)
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

    @property
    def state(self):
        """始终指向 Writer 的最新投影，避免 bootstrap/submit 替换后读到旧对象。"""
        return self.writer.state

    # ------------------------------------------------------------------ read
    def status(self, account: str) -> dict[str, Any]:
        pid = self._pid(account)
        with self.lock:
            return self._status_view(pid)

    def quests(self, account: str) -> dict[str, Any]:
        pid = self._pid(account)
        with self.lock:
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

    def player_state(self, account: str) -> dict[str, Any]:
        """`GET /api/player/state`：冻结结构的只读快照。"""
        account = str(account or "").strip().lower()
        with self.lock:
            if not ACCOUNT_RE.match(account):
                return {
                    "ok": False,
                    "profile_version": PROFILE_VERSION,
                    "reject_reason": "bad_account",
                    "reason_text": _reason_text("bad_account"),
                    "tick": self.state.tick,
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
            tick = int(self.state.tick)
            if tick != self._view_tick:
                self._view_cache.clear()
                self._view_tick = tick
            cached_views = self._view_cache.get(pid)
            if cached_views is None:
                cached_views = (
                    self._wallet_view(pid, prices),
                    self._location_view(ent.location, pid, prices),
                    self._shop_view(pid, ent.location, prices),
                    self._present_view(pid, ent.location),
                )
                self._view_cache[pid] = cached_views
            wallet_view, location_view, shop_view, present_view = cached_views
            player = {
                "entity_id": pid,
                "account_id": account,
                "name": ent.name,
                "archetype": prof.archetype,
                "role": ent.role,
                "location": ent.location,
                "location_name": self._loc_name(ent.location),
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
                "relations": view["relations"],
                "active_quests": view["active_quests"],
                "marriage": view["marriage"],
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
            return {
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
                "location": location,
                "shop": shop,
                "present_npcs": present,
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

    # ------------------------------------------------ 服务模式匿名读（公共摘要）
    def public_state(self, account: str) -> dict[str, Any]:
        """服务模式匿名读：只返回 `PUBLIC_STATE_KEYS` 白名单内的公共摘要。

        白名单实现（不是剔除私有字段），新增字段默认不可见，防止将来
        加入钱包/背包类字段后自动泄漏。
        """
        account = str(account or "").strip().lower()
        with self.lock:
            if not ACCOUNT_RE.match(account):
                return {
                    "ok": False,
                    "reject_reason": "bad_account",
                    "reason_text": _reason_text("bad_account"),
                    "server_tick": int(self.state.tick),
                }
            pid = self._pid(account)
            ent = self.state.entities.get(pid)
            prof = self.state.players.get(pid)
            if ent is None or prof is None:
                return {
                    "ok": False,
                    "reject_reason": "player_not_entered",
                    "reason_text": _reason_text("player_not_entered"),
                    "server_tick": int(self.state.tick),
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
        with self.lock:
            for quest in sorted(self.state.quests.values(), key=lambda q: q.id):
                if quest.status != "open":
                    continue
                row = self._quest_row(quest)
                for key in ("reward_money", "reward_relation", "acceptable"):
                    row.pop(key, None)
                rows.append(row)
            return {
                "ok": True,
                "tick": int(self.state.tick),
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

    def messages(self, account: str, since_tick: int = 0) -> dict[str, Any]:
        """`GET /api/player/messages?since_tick=`：拉取该玩家 tick 之后的对话回复。"""
        account = str(account or "").strip().lower()
        pid = self._pid(account)
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
                    rows.append(
                        {
                            "id": f"msg:{event.seq}",
                            "seq": int(event.seq),
                            "tick": int(event.tick),
                            "kind": "talk_reply",
                            "from": str(data.get("npc") or ""),
                            "to": pid,
                            "text": str(data.get("text") or "").removeprefix("我答："),
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

    def _pid(self, account: str) -> str:
        return f"player:{str(account).strip().lower()}"

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
        view["tick"] = self.state.tick
        view["ok"] = True
        return view

    def _quests_view(self, pid: str) -> dict[str, Any]:
        ent = self.state.entities.get(pid)
        open_rows = []
        for quest in sorted(self.state.quests.values(), key=lambda q: q.id):
            if quest.status != "open":
                continue
            row = self._quest_row(quest)
            row["acceptable"] = bool(ent and ent.location == row["giver_location"])
            row["category"] = str(
                (self.content.quests.get(quest.template_id) or {}).get("category", "")
            )
            open_rows.append(row)
        active = [
            self._quest_row(quest)
            for quest in sorted(self.state.quests.values(), key=lambda q: q.id)
            if quest.status == "accepted" and quest.acceptor == pid
        ]
        return {
            "ok": True,
            "tick": self.state.tick,
            "location": ent.location if ent else "",
            "open": open_rows,
            "active": active,
        }

    def _quest_row(self, quest) -> dict[str, Any]:
        giver = self.state.entities.get(quest.giver)
        tpl = self.content.quests.get(quest.template_id) or {}
        return {
            "id": quest.id,
            "template": quest.template_id,
            "name": str(tpl.get("name") or quest.template_id),
            "category": str(tpl.get("category", "")),
            "giver": quest.giver,
            "giver_name": giver.name if giver else quest.giver,
            "giver_location": giver.location if giver else "",
            "target_location": quest.target_location,
            "reward_money": quest.reward_money,
            "reward_relation": quest.reward_relation,
            "deadline_tick": quest.deadline_tick,
            "deadline_day": quest.deadline_tick // TICKS_PER_DAY + 1 if quest.deadline_tick else None,
            "status": quest.status,
        }

    def _loc_name(self, loc_id: str) -> str:
        row = (self.content.locations or {}).get(loc_id) or {}
        return str(row.get("name") or loc_id or "未知地点")

    def _item_name(self, item_id: str) -> str:
        row = (self.content.items or {}).get(item_id) or {}
        return str(row.get("name") or item_id)

    def _item_prices(self, prices: dict[str, tuple[int, int]] | None, item: str) -> tuple[int, int]:
        """一次读取内的成交价缓存：`(买入价, 卖出价)`，未知物品给 (0, 0)。

        供需乘数会扫全城库存，而同一个 tick 内只读快照不会改变库存，
        因此按 tick 缓存，tick 变化（任何写动作都会推进 tick）即失效。
        """
        if item not in self.content.items:
            return (0, 0)
        tick = int(self.state.tick)
        if tick != self._price_tick:
            self._price_cache.clear()
            self._price_tick = tick
        buy = self._price_cache.get(item)
        if buy is None:
            buy = int(self.content.item_price(item, self.state))
            self._price_cache[item] = buy
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
        row = (self.content.locations or {}).get(loc_id) or {}
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
                }
            )
        return {
            "id": loc_id,
            "name": str(row.get("name") or loc_id),
            "description": str(row.get("description") or ""),
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
                        "business": biz.id,
                        "business_name": biz.name,
                        "buy_limit": self._buy_limit(pid, unit_price, int(qty)),
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
        for ent in sorted(self.state.entities.values(), key=lambda e: e.id):
            if ent.id == pid or str(ent.id).startswith("player:"):
                continue
            if ent.location != loc_id or ent.status != "alive":
                continue
            top = max(ent.skills.items(), key=lambda kv: (float(kv[1]), kv[0])) if ent.skills else None
            rows.append(
                {
                    "id": ent.id,
                    "name": ent.name,
                    "role": ent.role,
                    "location": ent.location,
                    "presence": ent.presence,
                    "doing": "",
                    "skills": (
                        {"name": str(top[0]), "level": round(float(top[1]), 4)}
                        if top is not None
                        else None
                    ),
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
        active_rows = []
        active_count = sum(
            1
            for q in self.state.quests.values()
            if q.status == "accepted" and q.acceptor == pid
        )
        player = self.state.entities.get(pid)
        tick = int(self.state.tick)
        player_rep = reputation_points(self.state, pid) if player is not None else 0.0
        for quest in sorted(self.state.quests.values(), key=lambda q: q.id):
            if quest.status == "open":
                # 已过期残单（日界清理前的窗口）与发布人已不在的单，一律不下架展示。
                if quest.deadline_tick and tick > quest.deadline_tick:
                    continue
                row = self._quest_row(quest)
                giver = self.state.entities.get(quest.giver)
                if giver is None or giver.status != "alive":
                    continue
                tpl = self.content.quests.get(quest.template_id) or {}
                if float(tpl.get("require_reputation", 0) or 0) > player_rep:
                    continue
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
        if verb in FORBIDDEN_VERBS or verb not in PLAYER_VERBS:
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
        if advance:
            # 休息按小时消耗世界钟（1 小时 = TICKS_PER_HOUR tick）；其它动作仍是 tick_step。
            step = None
            if action == "rest" and event.outcome == "ok":
                hours = event.args.get("hours")
                if isinstance(hours, int) and not isinstance(hours, bool):
                    step = hours * TICKS_PER_HOUR
            self._advance(step)
        data = self._status_view(pid) if event.outcome == "ok" else {}
        response = self._response(event, data, action, pid)
        response["next_tick"] = self.state.tick
        if action in ("marriage_propose", "marriage_end"):
            response["details"]["decision"] = (
                "ended" if action == "marriage_end" and event.outcome == "ok" else marriage_decision
            )
            if data:
                response["details"]["marriage"] = dict(data.get("marriage") or {})
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
                }
            )
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
        if action == "rest":
            base["hours"] = payload.get("hours", 4)
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
        """仅当 `XKX_AMBIENT` 为真时启动；默认不启动，保持旧行为。"""
        raw = str(os.environ.get("XKX_AMBIENT", "0")).strip().lower()
        if raw in ("", "0", "false", "off", "no"):
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
