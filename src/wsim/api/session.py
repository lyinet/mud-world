"""玩家会话：把 HTTP JSON 命令变成 propose_intent → MCP → Writer 的单写者事务。

- 客户端只能给 account_id；服务端映射为 `player:<account_id>`，不接受客户端 actor。
- 客户端 tick 一律忽略，使用当前 `state.tick`；每个动作后由 system_tick 推进 6 tick。
- 写操作没有任何直连 Writer 的旁路：propose 入 outbox，admin 两段审批后 apply。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..actions.player import (
    ACCOUNT_RE,
    ARCHETYPES,
    CARRY_SOURCE,
    ENDOWMENT_SOURCE,
    ENDOWMENT_TOTAL_CAP,
    PLAYER_VERBS,
    RUNTIME_CARRY_SOURCE,
    RUNTIME_ENDOWMENT_SOURCE,
    STARTING_ITEMS,
    STARTING_MONEY,
    player_genesis_effects,
    player_status,
)
from ..core.clock import TICKS_PER_DAY, day_of
from ..core.content import load_content
from ..core.models import Intent
from ..mcp.gateway import MCPGateway, GatewayError
from ..sim.genesis import build_genesis
from ..world.state import WorldState
from ..world.store import EventStore
from ..world.writer import Writer
from ..actions.registry import build_registry
from ..actions.common import reputation_points
from .growth import growth_view, today_view
from .persistence import replay_into, verify_content_hash

# 契约冻结版本：字段名/结构变更必须同步 docs/player-api.md 的 CHANGELOG。
PROFILE_VERSION = 1

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
    "join_sect": "join_sect",
    # 统一动作入口使用的下划线别名（与 /api/player/quest/accept 等价）。
    "quest_accept": "quest_accept",
    "quest_deliver": "quest_deliver",
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
    "join_sect": "join_sect",
    "quest_accept": "quest/accept",
    "quest/accept": "quest/accept",
    "quest_deliver": "quest/deliver",
    "quest/deliver": "quest/deliver",
}

FORBIDDEN_VERBS = frozenset(
    {"rob", "attack", "kill", "marry_force", "player_expel", "item_create", "mint", "set_field"}
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
    "role_not_allowed": "你的身份不允许该操作。",
    "player_cap_reached": "本区域玩家席位已满，暂时无法创建新账号。",
    "endowment_already_granted": "该账号已经领取过初始资产，不能重复领取。",
    "endowment_cap_reached": "本区域初始资产发放总额已达上限；请联系运维。",
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
        self.requests: dict[str, dict[str, Any]] = {}
        self.intents: dict[str, dict[str, Any]] = {}

        self.content = load_content(self.root, allow_fixture=False, use_content=True)
        npcs = sorted(self.content.npcs, key=lambda n: n["id"])
        if npc_limit:
            npcs = npcs[:npc_limit]
        self.store = EventStore(self.run_dir / "events.sqlite", check_same_thread=False)
        self.registry = build_registry(self.content, seed)
        # 存档优先：已有事件日志时回放，绝不重新创世/重复发放初始资产。
        self.replayed = self.store.count() > 0
        if self.replayed:
            verify_content_hash(self.store, self.content.content_hash, self.run_dir)
            state, player_ticks = replay_into(self.store, self.content.locations)
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
                "created_tick": int(prof.created_tick),
                "last_intent_tick": int(prof.last_intent_tick),
                "endowed": bool(prof.endowed),
                "endowed_tick": int(prof.endowed_tick),
                "wallet": self._wallet_view(pid),
                "skills": {k: round(float(v), 4) for k, v in sorted(ent.skills.items())},
                "relations": view["relations"],
                "active_quests": view["active_quests"],
            }
            location = self._location_view(ent.location)
            present = self._present_view(pid, ent.location)
            online = self._online_players_view(pid)
            quest_board = self._quest_board_view(pid, ent.location)
            return {
                "ok": True,
                "profile_version": PROFILE_VERSION,
                "account_id": account,
                "tick": int(self.state.tick),
                "endowment_total": int(self.state.endowment_total),
                "player": player,
                "location": location,
                "present_npcs": present,
                "quest_board": quest_board,
                "online_players": online,
                # 兼容旧 /api/player/status 的扁平字段，前端可逐步迁移。
                "nearby": present,
                "active_quests_detail": view["active_quests_detail"],
                "growth": view["growth"],
                "today": view["today"],
            }

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

    def _wallet_view(self, pid: str) -> dict[str, Any]:
        from ..actions.common import wallet as wallet_of

        ent = self.state.entities[pid]
        return {
            "money": int(self.state.account_balance(wallet_of(pid))),
            "items": [
                {"item": item, "name": self._item_name(item), "qty": int(qty)}
                for item, qty in sorted(ent.inventory.items())
                if int(qty) > 0
            ],
        }

    def _location_view(self, loc_id: str) -> dict[str, Any]:
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
        return {
            "id": loc_id,
            "name": str(row.get("name") or loc_id),
            "description": str(row.get("description") or ""),
            "exits": exits,
            "services": sorted(str(x) for x in (row.get("services") or [])),
        }

    def _present_view(self, pid: str, loc_id: str) -> list[dict[str, Any]]:
        rows = []
        for ent in sorted(self.state.entities.values(), key=lambda e: e.id):
            if ent.id == pid or str(ent.id).startswith("player:"):
                continue
            if ent.location != loc_id or ent.status != "alive":
                continue
            rows.append(
                {
                    "id": ent.id,
                    "name": ent.name,
                    "role": ent.role,
                    "location": ent.location,
                    "presence": ent.presence,
                    "doing": "",
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
                self.intents[request_id] = response
                if response.get("intent_id"):
                    self.intents[str(response["intent_id"])] = response
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
        verb = WRITE_ACTIONS.get(action)
        if verb is None:
            return self._reject_early(account, action, "unknown_action", tick)
        if verb in FORBIDDEN_VERBS or verb not in PLAYER_VERBS:
            return self._reject_early(account, action, "forbidden_verb", tick)

        prof = self.state.players.get(pid)
        if action != "enter":
            if prof is None:
                return self._reject_early(account, action, "player_not_entered", tick)
            if not prof.online:
                return self._reject_early(account, action, "player_offline", tick)
        if verb not in ("move", "talk", "trade", "quest_accept", "quest_deliver"):
            allowed = self.content.allowed_roles(verb)
            if allowed is not None and "*" not in allowed and prof is not None and prof.role not in allowed:
                return self._reject_early(account, action, "role_not_allowed", tick)

        args = self._args(action, pid, account, payload)
        intent = Intent(actor=pid, verb=verb, args=args, tick=tick, source="player")
        created = prof is None
        event = self._submit(intent)
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
            self._advance()
        data = self._status_view(pid) if event.outcome == "ok" else {}
        response = self._response(event, data, action, pid)
        response["next_tick"] = self.state.tick
        if action == "enter" and event.outcome == "ok":
            response["details"]["created"] = created
            response["details"]["resumed"] = not created
            response["details"]["role"] = data.get("role", "")
            response["details"]["endowment"] = self._endowment_view(
                self.state.players.get(pid), grant
            )
        return response

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
        if action == "join_sect":
            ent = self.state.entities.get(pid)
            base["target"] = str(payload.get("target") or (ent.location if ent is not None else ""))
            base["dues"] = payload.get("dues", 0)
            return base
        return base

    def _submit(self, intent: Intent):
        data = {"actor": intent.actor, "verb": intent.verb, "args": intent.args,
                "tick": intent.tick, "source": intent.source}
        try:
            # 账号通过 API 校验后，服务端才授予该身份 propose 权限；权限不来自请求参数。
            if intent.actor.startswith("player:") and self.gateway.permission_of(intent.actor) is None:
                self.gateway.register_identity(intent.actor, "propose_intent")
            # 第一段：玩家本人 propose，只入 outbox，不改状态。
            self.gateway.call(intent.actor, "world.propose_intent", {"intent": data})
            # 第二段：运维身份申请 + 第二人批准 + 批准人 apply。
            token = self.gateway.call("ops:request", "admin.request",
                                      {"intent": data, "reason": "player_api"})["token"]
            self.gateway.call("ops:approve", "admin.approve", {"token": token})
            applied = self.gateway.call("ops:approve", "admin.apply", {"token": token})
        except GatewayError as exc:
            return self.writer.reject(intent, f"gateway_error({exc})")
        events = self.store.read_all()
        seq = int(applied["seq"])
        return events[seq - 1]

    def _advance(self) -> None:
        if self.tick_step <= 0:
            return
        tick = int(self.state.tick) + int(self.tick_step)
        if day_of(tick) != day_of(int(self.state.tick)):
            self.writer.submit(
                Intent(actor="system", verb="system_player_presence", args={},
                       tick=tick, source="system")
            )
            # 与 headless 日界一致：每日回复体力，否则玩家一次 8 小时工后永久卡住。
            self.writer.submit(
                Intent(actor="system", verb="system_recover", args={},
                       tick=tick, source="system")
            )
            # 日界先下架过期委托，再按模板补发，保证玩家每天有一批可接新单。
            self.writer.submit(
                Intent(actor="system", verb="system_quest_fail",
                       args={"prune_expired": True},
                       tick=tick, source="system")
            )
            self.writer.submit(
                Intent(actor="system", verb="system_quest_post", args={"day": day_of(tick)},
                       tick=tick, source="system")
            )
        self.writer.submit(
            Intent(actor="system", verb="system_tick", args={"step": self.tick_step},
                   tick=tick, source="system")
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

    def close(self) -> None:
        self.store.close()
