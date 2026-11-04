#!/usr/bin/env python3
"""玩家门面 · 反向代理（只用 Python 标准库）。

两种模式：
- 默认 live：把 `/api/player/*` 原样转发到真 API（默认 http://127.0.0.1:8020），
  只做状态码归一（写动作被拒 → 409 + reason/message），不重写业务逻辑。
- `--readonly` 或上游不可达：降级为本地只读回放 + `propose_intent` 提案，
  不写任何 events.sqlite，页面显示“只读模式”。

启动（真 API 需另开终端，见 docs/player-api.md）：
    cd world-sim && PYTHONPATH=src python3 -m wsim.api --run player-demo-<时间> --port 8020
    python3 ui/player/server.py --upstream http://127.0.0.1:8020 --port 8030

本文件不修改 src/、content/、config/、tests/、runs/；只改 ui/player/。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

# 必须在导入 wsim 前关闭字节码缓存，交付目录里不出现 __pycache__。
sys.dont_write_bytecode = True

WORLD_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = WORLD_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from wsim.actions.player import ACCOUNT_RE  # noqa: E402
from wsim.api.session import (  # noqa: E402
    ACTION_ALIASES,
    PROFILE_VERSION,
    _reason_text,
)
from wsim.core.content import load_content  # noqa: E402
from wsim.core.models import Effect  # noqa: E402
from wsim.mcp.audit import AuditLog  # noqa: E402
from wsim.mcp.gateway import (  # noqa: E402
    MCPGateway,
    PERMISSION_PROPOSE,
    PERMISSION_READ,
)
from wsim.sim.intent import GOAL_LABEL  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402

READER = "ui-player:read"
PROPOSER = "ui-player:propose"
UI_DIR = Path(__file__).resolve().parent
TICKS_PER_DAY = 144
TICKS_PER_HOUR = 6
SPAWN_LOCATION = "market"
DEFAULT_READONLY_RUN = "runs/p3c-players-1d"
DEFAULT_UPSTREAM = os.environ.get("PLAYER_API", "http://127.0.0.1:8020")
DEFAULT_UI_PORT = 8030
WRITE_PATHS = {
    "/api/player/enter",
    "/api/player/action",
    "/api/player/leave",
    "/api/player/heartbeat",
    "/api/player/move",
    "/api/player/talk",
    "/api/player/trade",
    "/api/player/quest/accept",
    "/api/player/quest/deliver",
}
READ_PATHS = {"/api/player/state", "/api/player/messages", "/api/player/status", "/api/player/quests"}

ARCHETYPES: dict[str, dict[str, Any]] = {
    "游侠": {"role": "镖师", "skill": "镖师", "wage": 30, "escort": True, "found_sect": False},
    "商人": {"role": "掌柜", "skill": "掌柜", "wage": 36, "escort": False, "found_sect": True},
    "捕快": {"role": "捕快", "skill": "捕快", "wage": 26, "escort": True, "found_sect": False},
}
ROLE_TO_ARCHETYPE = {row["role"]: name for name, row in ARCHETYPES.items()}

VERB_LABEL = {
    "move": "移动",
    "cultivate": "修炼",
    "work": "做工",
    "consume": "吃饭",
    "buy": "采购",
    "sell": "售卖",
    "trade": "交易",
    "apprentice": "拜师",
    "join_sect": "入派",
    "marry": "成婚",
    "open_shop": "开店",
    "escort": "押镖",
    "found_sect": "立派",
    "recruit": "招揽",
    "quest_accept": "接任务",
    "quest_deliver": "交任务",
    "debt_settle": "还债",
    "talk": "对话",
    "player_enter": "进入世界",
    "player_presence": "在场",
    "player_leave": "离开",
    "system_goal": "系统·目标",
    "system_living": "系统·生活",
    "system_recover": "系统·恢复",
    "system_quest_post": "系统·发任务",
    "system_quest_fail": "系统·任务失败",
    "system_debts": "系统·债务",
    "system_escort_arrive": "系统·镖到",
    "system_savings_tax": "系统·利息税",
    "system_shop_dividend": "系统·分红",
    "system_shop_tax": "系统·营业税",
}

INTERFACE_GAPS = [
    {
        "name": "POST /api/player/enter (player_enter)",
        "state": "动作存在，HTTP 入口与落地缺失",
        "impact": "进入只进入 outbox；role/身份、初始 300 文与物品、PlayerProfile 都不会出现",
    },
    {
        "name": "POST /api/player/state / actions / preview",
        "state": "规格已定义，代码未提供",
        "impact": "本页用只读快照自行组装状态栏与可行动作",
    },
    {
        "name": "POST /api/player/action 统一写入口",
        "state": "未提供",
        "impact": "本页只能逐个 propose_intent，无法做服务端统一前置校验/频率限制",
    },
    {
        "name": "online / trustee / dormant 在线状态",
        "state": "只有 Entity.presence，无 PlayerProfile",
        "impact": "在线人数按 player: 实体的 presence 估算，无离线托管状态",
    },
]


class LoadError(Exception):
    def __init__(self, message: str, hint: str = ""):
        self.message = message
        self.hint = hint or "请用 --run 指向包含 summary.json / events.jsonl 的 run 目录。"
        super().__init__(message)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LoadError(f"缺少文件：{path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LoadError(f"无法解析 {path.name}：{exc}") from exc


def time_label(tick: int) -> str:
    day = tick // TICKS_PER_DAY
    hour = (tick % TICKS_PER_DAY) // TICKS_PER_HOUR
    minute = (tick % TICKS_PER_HOUR) * 10
    return f"第 {day + 1} 天 {hour:02d}:{minute:02d}"


def day_label(tick: int) -> str:
    return f"第 {tick // TICKS_PER_DAY + 1} 天"


class WorldView:
    """run -> 只读世界视图 + 提案入口。"""

    def __init__(self, run_dir: str | Path):
        self.run_path = self._resolve_run(run_dir)
        self.run_label = str(self.run_path)
        self.writable = False
        self.error: LoadError | None = None
        self.lock = threading.RLock()
        self.state: WorldState | None = None
        self.content = None
        self.summary: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self.last_action: dict[str, dict[str, Any]] = {}
        self.tick = 0
        self.hash_match = False
        self.state_hash_value = ""
        self.gateway: MCPGateway | None = None
        self.proposals: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self._proposal_seq = 0
        try:
            self._load()
        except LoadError as exc:
            self.error = exc
        except Exception as exc:  # 兜底：页面显示错误卡，不白屏
            self.error = LoadError(f"加载 run 失败：{type(exc).__name__}: {exc}")

    @staticmethod
    def _resolve_run(run_dir: str | Path) -> Path:
        path = Path(run_dir)
        if not path.is_absolute():
            path = (WORLD_ROOT / path).resolve()
        return path

    # ---------------------------------------------------------------- loading
    def _load(self) -> None:
        if not self.run_path.exists():
            raise LoadError(f"run 目录不存在：{self.run_path}")
        if not self.run_path.is_dir():
            raise LoadError(f"--run 不是目录：{self.run_path}")
        missing = [n for n in ("summary.json", "events.jsonl") if not (self.run_path / n).exists()]
        if missing:
            raise LoadError(f"run 目录缺少文件：{', '.join(missing)}")

        self.summary = _read_json(self.run_path / "summary.json")
        self.content = load_content(WORLD_ROOT, min_npcs=0, allow_fixture=False, use_content=True)
        if not self.content.locations:
            raise LoadError("内容包里没有地点，拒绝在空地点图上回放")

        state = WorldState(locations=self.content.locations)
        self.events = []
        path = self.run_path / "events.jsonl"
        try:
            with path.open("r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise LoadError(f"events.jsonl 第 {lineno} 行不是合法 JSON：{exc}") from exc
                    if not isinstance(row, dict):
                        continue
                    try:
                        apply_effects(
                            state,
                            [Effect.from_dict(e) for e in (row.get("effects") or []) if isinstance(e, dict)],
                        )
                    except Exception as exc:
                        raise LoadError(
                            f"events.jsonl 第 {row.get('seq')} 条事件回放失败：{type(exc).__name__}: {exc}"
                        ) from exc
                    state.tick = int(row.get("tick") or 0)
                    view = self._event_view(row)
                    self.events.append(view)
                    if not str(view["verb"]).startswith("system_") and view["outcome"] == "ok":
                        self.last_action[view["actor"]] = view
        except OSError as exc:
            raise LoadError(f"无法读取 events.jsonl：{exc}") from exc

        self.state = state
        self.tick = int(state.tick)

        expected = ""
        hash_path = self.run_path / "world_hash.txt"
        if hash_path.exists():
            expected = hash_path.read_text(encoding="utf-8").strip()
        self.state_hash_value = state_hash(state)
        self.hash_match = bool(expected) and expected == self.state_hash_value

        self.gateway = MCPGateway(
            state_provider=lambda: self.state,
            writer=None,  # 关键：不绑定 Writer，提案不会落地
            audit=AuditLog(None),
            tick_provider=lambda: int(self.state.tick),
            identities={READER: PERMISSION_READ, PROPOSER: PERMISSION_PROPOSE},
        )

    def _event_view(self, row: dict[str, Any]) -> dict[str, Any]:
        tick = int(row.get("tick") or 0)
        return {
            "seq": int(row.get("seq") or 0),
            "tick": tick,
            "time": time_label(tick),
            "day": day_label(tick),
            "actor": str(row.get("actor") or ""),
            "verb": str(row.get("verb") or ""),
            "verb_label": VERB_LABEL.get(str(row.get("verb") or ""), str(row.get("verb") or "")),
            "outcome": str(row.get("outcome") or ""),
            "reason": str(row.get("reason") or ""),
            "source": str(row.get("source") or ""),
            "args": row.get("args") or {},
        }

    # ---------------------------------------------------------------- helpers
    def _ready(self) -> None:
        if self.error is not None:
            raise self.error
        if self.state is None or self.gateway is None:
            raise LoadError("世界快照尚未就绪")

    def _snapshot(self) -> tuple[dict[str, Any], int]:
        self._ready()
        assert self.gateway is not None
        result = self.gateway.call(READER, "world.read_snapshot", {})
        return result.get("snapshot") or {}, int(result.get("tick") or 0)

    def _loc_name(self, loc: str) -> str:
        row = (self.content.locations or {}).get(loc) if self.content else {}
        return str((row or {}).get("name") or loc or "未知地点")

    def _item_name(self, item: str) -> str:
        row = (self.content.items or {}).get(item) if self.content else {}
        return str((row or {}).get("name") or item)

    def _quest_name(self, template_id: str) -> str:
        row = (self.content.quests or {}).get(template_id) if self.content else {}
        name = str((row or {}).get("name") or "").strip()
        return name or f"未收录任务（{template_id}）"

    def _player_ids(self) -> list[str]:
        self._ready()
        assert self.state is not None
        return sorted(k for k in self.state.entities if k.startswith("player:"))

    def _entity(self, pid: str):
        self._ready()
        assert self.state is not None
        return self.state.entities.get(pid)

    def _wallet(self, pid: str) -> int:
        self._ready()
        assert self.state is not None
        return int(self.state.accounts.get(f"wallet:{pid}") or 0)

    @staticmethod
    def _field(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _inventory(self, ent) -> list[dict[str, Any]]:
        inventory = self._field(ent, "inventory") or {}
        return [
            {"item": item, "name": self._item_name(item), "qty": int(qty)}
            for item, qty in sorted(inventory.items())
            if int(qty) > 0
        ]

    def _goal_view(self, goal: dict[str, Any] | None) -> dict[str, Any] | None:
        if not goal:
            return None
        kind = str(goal.get("kind") or "")
        ctype = str(goal.get("content_type") or "")
        label = GOAL_LABEL.get(ctype) or GOAL_LABEL.get(kind) or ctype or kind or "目标"
        progress = float(goal.get("progress") or 0.0)
        target = float(goal.get("target") or 1.0)
        pct = 0.0 if target <= 0 else min(100.0, progress / target * 100.0)
        return {
            "id": goal.get("id"),
            "label": label,
            "kind": kind,
            "content_type": ctype,
            "progress": progress,
            "target": target,
            "pct": round(pct, 1),
            "status": goal.get("status"),
        }

    def _doing(self, npc_id: str) -> str:
        row = self.last_action.get(npc_id)
        if not row:
            return "无记录"
        result = "完成" if row["outcome"] == "ok" else "未成"
        return f"{row['verb_label']}{result}（{row['time']}）"

    # ------------------------------------------------------------- API views
    def health(self) -> dict[str, Any]:
        if self.error is not None:
            return {
                "ok": False,
                "run": self.run_label,
                "error": self.error.message,
                "hint": self.error.hint,
            }
        return {
            "ok": True,
            "mode": "readonly",
            "writable": False,
            "readonly": True,
            "run": self.run_label,
            "tick": self.tick,
            "clock": time_label(self.tick),
            "events": len(self.events),
            "entities": len(self.state.entities) if self.state else 0,
            "locations": len(self.content.locations) if self.content else 0,
            "state_hash_match": self.hash_match,
            "state_hash": self.state_hash_value,
            "player_entities": self._player_ids(),
            "player_profiles": 0,
            "interfaces": INTERFACE_GAPS,
        }

    def scene(self, pid: str = "", archetype: str = "") -> dict[str, Any]:
        snapshot, tick = self._snapshot()
        assert self.state is not None and self.content is not None
        entities = snapshot.get("entities") or {}
        accounts = snapshot.get("accounts") or {}
        goals = snapshot.get("goals") or {}
        quests = snapshot.get("quests") or {}
        relations = snapshot.get("relations") or {}

        degraded: list[str] = []
        ent = entities.get(pid) if pid else None
        registered = ent is not None
        if pid and not registered:
            degraded.append(
                f"世界未注册玩家 {pid}："
                + ("请先在“进入世界”里完成进入；写操作会被拒绝（player_not_entered）。"
                   if self.writable else
                   "player_enter 提案只能进 outbox，本页不绑定 Writer，进入尚未落地。")
            )
        if not pid:
            degraded.append("尚未进入世界；以下显示集市出生点与只读世界。")

        role = str((ent or {}).get("role") or "")
        archetype_selected = archetype if archetype in ARCHETYPES else ROLE_TO_ARCHETYPE.get(role, "")
        if ent is not None and role == "visitor":
            degraded.append(
                "当前是兼容访客实体（role=visitor）；没有 PlayerProfile，也没有 300 文/物品的创世配给。"
                if self.writable else
                "当前是旧 run 的兼容访客实体 player:visitor（role=visitor）；"
                "没有 PlayerProfile，也没有 300 文/物品的创世配给。"
            )
        if registered and role in ROLE_TO_ARCHETYPE:
            archetype_selected = ROLE_TO_ARCHETYPE[role]

        loc_id = str((ent or {}).get("location") or SPAWN_LOCATION)
        if loc_id not in self.content.locations:
            loc_id = SPAWN_LOCATION if SPAWN_LOCATION in self.content.locations else sorted(self.content.locations)[0]
        loc_row = self.content.locations.get(loc_id) or {}
        exits = [
            {"id": dest, "name": self._loc_name(dest), "exists": dest in self.content.locations}
            for dest in (loc_row.get("exits") or [])
            if dest in self.content.locations
        ]

        def public_entity(nid: str) -> dict[str, Any] | None:
            row = entities.get(nid)
            if not row or nid.startswith("player:"):  # 玩家实体不混进 NPC 列表
                return None
            if str(row.get("status") or "") != "alive":
                return None
            return row

        present = []
        for nid, row in sorted(entities.items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
            if nid.startswith("player:"):
                continue
            if str(row.get("location") or "") != loc_id or str(row.get("status") or "") != "alive":
                continue
            goal = self._goal_view(goals.get(row.get("goal_id"))) if row.get("goal_id") else None
            present.append(
                {
                    "id": nid,
                    "name": row.get("name") or nid,
                    "role": row.get("role") or "无职业",
                    "location": loc_id,
                    "goal_label": (goal or {}).get("label", "无进行中目标"),
                    "goal_pct": (goal or {}).get("pct", 0),
                    "doing": self._doing(nid),
                }
            )

        all_npcs = []
        for nid, row in sorted(entities.items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
            if nid.startswith("player:"):
                continue
            all_npcs.append(
                {
                    "id": nid,
                    "name": row.get("name") or nid,
                    "role": row.get("role") or "无职业",
                    "location": str(row.get("location") or ""),
                    "location_name": self._loc_name(str(row.get("location") or "")),
                    "status": row.get("status") or "unknown",
                }
            )

        money = int(accounts.get(f"wallet:{pid}") or 0) if pid else 0
        inv = self._inventory(ent) if ent is not None else []
        my_goals = [
            self._goal_view(g)
            for g in sorted(goals.values(), key=lambda x: int(x.get("created_tick") or 0))
            if str(g.get("npc")) == pid and str(g.get("status")) == "active"
        ]
        my_goals = [g for g in my_goals if g]
        active_quests = [
            q for q in quests.values()
            if str(q.get("acceptor") or "") == pid and str(q.get("status")) == "accepted"
        ]

        online_players = [
            nid for nid in self._player_ids()
            if str((entities.get(nid) or {}).get("presence") or "online") == "online"
        ]
        world_npcs = sum(
            1 for nid, row in entities.items()
            if not nid.startswith("player:") and str(row.get("status") or "alive") == "alive"
        )

        rows = self._quest_rows(entities, quests, pid, loc_id, tick)
        counts = {
            "open": sum(1 for r in rows if r["status"] == "open"),
            "acceptable_here": sum(1 for r in rows if r["can_accept_here"]),
            "accepted_by_me": sum(1 for r in rows if r["accepted_by_me"]),
        }

        log = self._log(pid)

        relation_rows = []
        if pid and registered:
            for rel in relations.values():
                a, b = str(rel.get("a")), str(rel.get("b"))
                if pid not in (a, b):
                    continue
                other = b if a == pid else a
                relation_rows.append(
                    {
                        "target": other,
                        "target_name": str((entities.get(other) or {}).get("name") or other),
                        "kind": rel.get("kind"),
                        "affection": rel.get("affection"),
                        "trust": rel.get("trust"),
                        "status": rel.get("status"),
                    }
                )

        return {
            "ok": True,
            "mode": "readonly" if not self.writable else "writable",
            "readonly": not self.writable,
            "notice": (
                "实时世界：写操作经 propose → 审批 → Writer 落地，每次动作推进 6 tick。"
                if self.writable else
                "只读世界快照；写操作只提交 propose_intent，世界侧异步执行。"
            ),
            "degraded": degraded,
            "interfaces": [] if self.writable else INTERFACE_GAPS,
            "run": {
                "path": self.run_label,
                "run_id": self.summary.get("run_id"),
                "days": self.summary.get("days"),
                "npc_count": self.summary.get("npc_count"),
                "state_hash_match": self.hash_match,
                "player_profiles": len(self.state.players) if self.writable and self.state else 0,
            },
            "clock": {
                "tick": tick,
                "label": time_label(tick),
                "day": day_label(tick),
                "day_index": tick // TICKS_PER_DAY + 1,
            },
            "me": {
                "entity_id": pid,
                "registered": registered,
                "account_id": pid[len("player:"):] if pid.startswith("player:") else "",
                "name": (ent or {}).get("name") or ("未注册" if pid else "未进入"),
                "role": role or "",
                "archetype": archetype_selected,
                "archetype_role": ARCHETYPES.get(archetype_selected, {}).get("role", role),
                "location": loc_id,
                "location_name": self._loc_name(loc_id),
                "stamina": float((ent or {}).get("stamina") or 0.0),
                "max_stamina": float((ent or {}).get("max_stamina") or 100.0),
                "presence": (ent or {}).get("presence") or ("online" if registered else ("未注册" if pid else "未进入")),
            },
            "location": {
                "id": loc_id,
                "name": self._loc_name(loc_id),
                "description": "这里是世界的出生点与集市枢纽。",
                "exits": exits,
                "services": loc_row.get("services") or [],
            },
            "present_npcs": present,
            "all_npcs": all_npcs,
            "status": {
                "money": money,
                "items": inv,
                "goals": my_goals,
                "active_quests": len(active_quests),
                "online_players": len(online_players),
                "online_player_ids": online_players,
                "world_npcs": world_npcs,
                "stamina": float((ent or {}).get("stamina") or 0.0),
                "max_stamina": float((ent or {}).get("max_stamina") or 100.0),
            },
            "relations": relation_rows,
            "quests": {"counts": counts, "rows": rows},
            "log": log,
        }

    def _quest_rows(
        self,
        entities: dict[str, Any],
        quests: dict[str, Any],
        pid: str,
        loc_id: str,
        tick: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        player_ent = self._entity(pid) if pid else None
        active_by_me = sum(
            1 for q in quests.values()
            if str(q.get("acceptor") or "") == pid and str(q.get("status")) == "accepted"
        )
        for q in sorted(quests.values(), key=lambda x: int(x.get("deadline_tick") or 0)):
            status = str(q.get("status") or "")
            if status not in {"open", "accepted"}:
                continue
            giver = str(q.get("giver") or "")
            giver_ent = entities.get(giver) or {}
            giver_loc = str(giver_ent.get("location") or "")
            giver_name = str(giver_ent.get("name") or giver)
            deadline = int(q.get("deadline_tick") or 0)
            expired = bool(deadline) and tick > deadline
            target_npc = str(q.get("target_npc") or "")
            target_name = str((entities.get(target_npc) or {}).get("name") or target_npc) if target_npc else ""
            target_loc = str(q.get("target_location") or "")
            accepted_by_me = status == "accepted" and str(q.get("acceptor") or "") == pid
            acceptable = status == "open" and not expired and str(giver_ent.get("status") or "") == "alive"
            can_accept_here = (
                acceptable
                and player_ent is not None
                and loc_id == giver_loc
                and active_by_me < 2
                and not player_ent.escort_id
            )
            if expired:
                hint = f"已过期（截止第 {deadline // TICKS_PER_DAY + 1} 天）"
            elif status == "accepted":
                hint = f"进行中：{str((entities.get(str(q.get('acceptor') or '')) or {}).get('name') or '已被接走')}"
            elif acceptable and can_accept_here:
                hint = "可在这里直接接单"
            elif acceptable:
                hint = f"可接：先去{self._loc_name(giver_loc)}找{giver_name}"
            else:
                hint = f"发布人{giver_name}当前不可用"
            rows.append(
                {
                    "id": q.get("id"),
                    "name": self._quest_name(str(q.get("template_id") or "")),
                    "status": status,
                    "giver": giver,
                    "giver_name": giver_name,
                    "giver_location": giver_loc,
                    "giver_location_name": self._loc_name(giver_loc),
                    "target_name": target_name,
                    "target_location_name": self._loc_name(target_loc) if target_loc else "",
                    "reward_money": int(q.get("reward_money") or 0),
                    "reward_relation": float(q.get("reward_relation") or 0),
                    "deadline_day": deadline // TICKS_PER_DAY + 1 if deadline else None,
                    "acceptable": acceptable,
                    "can_accept_here": bool(can_accept_here),
                    "accepted_by_me": accepted_by_me,
                    "hint": hint,
                }
            )
        rows.sort(key=lambda r: (0 if r["can_accept_here"] else 1, 0 if r["status"] == "open" else 1, r["deadline_day"] or 999))
        return rows

    def _log(self, pid: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        with self.lock:
            for p in self.proposals[-30:]:
                if pid and p["player"] != pid:
                    continue
                entries.append(
                    {
                        "kind": "proposal",
                        "tick": p["tick"],
                        "time": time_label(p["tick"]),
                        "verb": p["verb"],
                        "verb_label": p["verb_label"],
                        "text": p["text"],
                        "intent_id": p["intent_id"],
                        "pending": True,
                    }
                )
            for d in self.decisions[-20:]:
                if pid and d["player"] not in ("", pid):
                    continue
                entries.append(
                    {
                        "kind": "rejected",
                        "tick": d["tick"],
                        "time": time_label(d["tick"]),
                        "verb": d["verb"],
                        "verb_label": VERB_LABEL.get(d["verb"], d["verb"]),
                        "text": d["human"],
                        "pending": False,
                    }
                )
        for row in self.events[-60:]:
            actor = row["actor"]
            name = self._actor_name(actor)
            if row["outcome"] == "ok":
                text = f"{name} {row['verb_label']}"
            else:
                reason = row["reason"].removeprefix("rejected:")
                text = f"{name} {row['verb_label']}被拒（{reason or '原因未记录'}）"
            entries.append(
                {
                    "kind": "world",
                    "seq": row["seq"],
                    "tick": row["tick"],
                    "time": row["time"],
                    "verb": row["verb"],
                    "verb_label": row["verb_label"],
                    "text": text,
                    "mine": bool(pid) and actor == pid,
                    "pending": False,
                }
            )
        entries.sort(key=lambda e: (e["tick"], e.get("seq") or 999999))
        return entries[-60:]

    def _actor_name(self, actor: str) -> str:
        if actor == "system":
            return "世界"
        ent = self._entity(actor)
        if ent is not None:
            return str(ent.name)
        return actor

    def npc_detail(self, npc_id: str, pid: str = "") -> dict[str, Any]:
        snapshot, tick = self._snapshot()
        entities = snapshot.get("entities") or {}
        if npc_id not in entities:
            raise KeyError(npc_id)
        row = entities[npc_id]
        goals = snapshot.get("goals") or {}
        relations = snapshot.get("relations") or {}
        goal = self._goal_view(goals.get(row.get("goal_id"))) if row.get("goal_id") else None
        all_goals = [
            self._goal_view(g)
            for g in sorted(goals.values(), key=lambda x: int(x.get("created_tick") or 0))
            if str(g.get("npc")) == npc_id
        ]
        all_goals = [g for g in all_goals if g]
        recent = [e for e in self.events if e["actor"] == npc_id][-8:]
        rel = None
        if pid:
            for r in relations.values():
                a, b = str(r.get("a")), str(r.get("b"))
                if {a, b} == {pid, npc_id}:
                    rel = {
                        "kind": r.get("kind"),
                        "affection": r.get("affection"),
                        "trust": r.get("trust"),
                        "status": r.get("status"),
                    }
                    break
        return {
            "ok": True,
            "tick": tick,
            "npc": {
                "id": npc_id,
                "name": row.get("name") or npc_id,
                "role": row.get("role") or "无职业",
                "status": row.get("status") or "unknown",
                "location": row.get("location"),
                "location_name": self._loc_name(str(row.get("location") or "")),
                "doing": self._doing(npc_id),
                "goal": goal,
                "goals": all_goals,
                "recent": [
                    {
                        "time": e["time"],
                        "verb": e["verb_label"],
                        "outcome": e["outcome"],
                        "reason": e["reason"].removeprefix("rejected:"),
                    }
                    for e in recent
                ],
                "relation_to_player": rel,
            },
        }

    # --------------------------------------------------------------- writing
    def propose(self, actor: str, verb: str, args: dict[str, Any], text: str) -> dict[str, Any]:
        """唯一写入口：world.propose_intent -> gateway.outbox。"""
        self._ready()
        assert self.gateway is not None
        result = self.gateway.call(
            PROPOSER,
            "world.propose_intent",
            {
                "intent": {
                    "actor": actor,
                    "verb": verb,
                    "args": dict(args),
                    "tick": self.tick,
                    "source": "player",
                }
            },
        )
        intent_id = str(result.get("intent_id") or "")
        with self.lock:
            self._proposal_seq += 1
            self.proposals.append(
                {
                    "seq": self._proposal_seq,
                    "tick": self.tick,
                    "player": actor,
                    "verb": verb,
                    "verb_label": VERB_LABEL.get(verb, verb),
                    "args": dict(args),
                    "text": text,
                    "intent_id": intent_id,
                    "state_unchanged": bool(result.get("state_unchanged")),
                }
            )
        return {
            "ok": True,
            "pending": True,
            "intent_id": intent_id,
            "tick": self.tick,
            "state_unchanged": bool(result.get("state_unchanged")),
            "verb": verb,
            "message": text,
        }

    def reject(self, pid: str, verb: str, human: str, code: str) -> dict[str, Any]:
        with self.lock:
            self.decisions.append(
                {"player": pid, "verb": verb, "tick": self.tick, "reason": code, "human": human}
            )
        return {"ok": False, "pending": False, "reject_reason": code, "message": human}

    def enter(self, account_id: str, display_name: str, archetype: str, legacy: bool = False) -> dict[str, Any]:
        self._ready()
        if legacy:
            if "player:visitor" not in (self.state.entities if self.state else {}):
                raise ValueError("这个 run 没有兼容访客实体 player:visitor")
            ent = self._entity("player:visitor")
            return {
                "ok": True,
                "registered": True,
                "legacy": True,
                "resumed": True,
                "entity_id": "player:visitor",
                "account_id": "visitor",
                "display_name": ent.name if ent else "访客",
                "archetype": "",
                "role": ent.role if ent else "visitor",
                "location": ent.location if ent else SPAWN_LOCATION,
                "location_name": self._loc_name(ent.location if ent else SPAWN_LOCATION),
                "cash": self._wallet("player:visitor"),
                "items": self._inventory(ent) if ent else [],
                "stamina": float(ent.stamina) if ent else 0.0,
                "note": "兼容模式：直接沿用旧 run 的 player:visitor 实体；不等同于新账号进入。",
            }

        if not re.fullmatch(r"[a-z0-9_-]{3,32}", account_id or ""):
            raise ValueError("账号 id 需为 3-32 位小写字母/数字/-/_")
        if not (2 <= len(display_name or "") <= 12):
            raise ValueError("角色名需为 2-12 个字符")
        if archetype not in ARCHETYPES:
            raise ValueError("身份只能是 游侠 / 商人 / 捕快")
        pid = f"player:{account_id}"
        if pid in (self.state.entities if self.state else {}):
            ent = self._entity(pid)
            return {
                "ok": True,
                "registered": True,
                "legacy": False,
                "resumed": True,
                "entity_id": pid,
                "account_id": account_id,
                "display_name": ent.name if ent else display_name,
                "archetype": ROLE_TO_ARCHETYPE.get(ent.role if ent else "", archetype),
                "role": ent.role if ent else "",
                "location": ent.location if ent else SPAWN_LOCATION,
                "location_name": self._loc_name(ent.location if ent else SPAWN_LOCATION),
                "cash": self._wallet(pid),
                "items": self._inventory(ent) if ent else [],
                "stamina": float(ent.stamina) if ent else 0.0,
                "note": "该玩家实体已存在，按幂等恢复处理；不发资产。",
            }

        proposal = self.propose(
            pid,
            "player_enter",
            {"player_id": pid, "name": display_name, "at": SPAWN_LOCATION},
            f"提交进入提案：{display_name}（{archetype}）从{self._loc_name(SPAWN_LOCATION)}入世",
        )
        chooser = ARCHETYPES[archetype]
        return {
            "ok": True,
            "registered": False,
            "legacy": False,
            "resumed": False,
            "degraded": True,
            "entity_id": pid,
            "account_id": account_id,
            "display_name": display_name,
            "archetype": archetype,
            "role": "未写入（现有 player_enter 只创建 role=visitor）",
            "location": SPAWN_LOCATION,
            "location_name": self._loc_name(SPAWN_LOCATION),
            "cash": 0,
            "items": [],
            "stamina": 100.0,
            "proposal": proposal,
            "identity_preview": {
                "archetype": archetype,
                "role": chooser["role"],
                "skill": chooser["skill"],
                "wage_daily": chooser["wage"],
                "can_escort": chooser["escort"],
                "found_sect_by_content_whitelist": chooser["found_sect"],
            },
            "expected_if_landed": {
                "visitor_mode": {"cash": 0, "items": [], "stamina": 100.0, "role": "visitor"},
                "native_genesis": {"cash": 300, "items": [{"item": "flatbread", "name": "炊饼", "qty": 3}, {"item": "coarse_cloth", "name": "粗布（一匹）", "qty": 1}], "stamina": 100.0},
            },
            "blocked_at": "player_enter 只有 Action，没有 HTTP 入口，也没有绑定的 PlayerProfile/Writer；提案停在 outbox。",
            "note": "出生地点按规格为集市；身份与 300 文/物品需要世界侧 player_enter 落地后才可显示。",
        }

    def move(self, pid: str, to: str) -> dict[str, Any]:
        ent = self._entity(pid)
        if ent is None:
            return self.reject(
                pid,
                "move",
                f"世界未注册玩家 {pid}，移动提案无法执行。请先用兼容访客身份进入，或等世界侧落地 player_enter。",
                "world_player_not_registered",
            )
        if to not in self.content.locations:
            return self.reject(pid, "move", f"目标地点不存在：{to}", "unknown_location")
        if str(ent.status) != "alive":
            return self.reject(pid, "move", "你现在不能行动（状态不是 alive）。", "not_alive")
        here = str(ent.location)
        exits = (self.content.locations.get(here) or {}).get("exits") or []
        if to not in exits:
            near = "、".join(self._loc_name(x) for x in exits) or "（无出口）"
            return self.reject(
                pid,
                "move",
                f"这里不直接通往{self._loc_name(to)}。当前可走：{near}。",
                "not_adjacent",
            )
        cost = float((self.content.economy or {}).get("stamina_move", 5))
        if float(ent.stamina) < cost:
            return self.reject(
                pid,
                "move",
                f"体力不足：剩 {ent.stamina:.0f}，走一趟要 {cost:.0f}。",
                "insufficient_stamina",
            )
        return self.propose(
            pid,
            "move",
            {"to": to},
            f"已提交移动提案：{self._loc_name(here)} → {self._loc_name(to)}（体力 -{cost:.0f} 由世界执行）",
        )

    def talk(self, pid: str, target: str, message: str) -> dict[str, Any]:
        ent = self._entity(pid)
        if ent is None:
            return self.reject(
                pid,
                "talk",
                f"世界未注册玩家 {pid}，对话提案无法执行。",
                "world_player_not_registered",
            )
        if not message or not message.strip():
            return self.reject(pid, "talk", "消息不能为空。", "bad_message")
        if len(message) > 200:
            return self.reject(pid, "talk", "消息超过 200 字，世界会拒绝。", "bad_message")
        other = self._entity(target)
        if other is None:
            return self.reject(pid, "talk", f"世界里没有这个 NPC：{target}", "unknown_target")
        if str(other.status) != "alive":
            return self.reject(pid, "talk", f"{other.name}现在不能对话（状态：{other.status}）。", "not_alive")
        if str(other.location) != str(ent.location):
            return self.reject(
                pid,
                "talk",
                f"{other.name}不在{self._loc_name(str(ent.location))}（在{self._loc_name(str(other.location))}）。先走过去，或换一位在场的 NPC。",
                "not_together",
            )
        return self.propose(
            pid,
            "talk",
            {"target": target, "message": message.strip()},
            f"已提交对话提案：对{other.name}说「{message.strip()[:40]}」。回应由世界异步产生，本页不显示即时回复。",
        )

    def accept_quest(self, pid: str, quest_id: str) -> dict[str, Any]:
        ent = self._entity(pid)
        if ent is None:
            return self.reject(
                pid,
                "quest_accept",
                f"世界未注册玩家 {pid}，接任务提案无法执行。",
                "world_player_not_registered",
            )
        if self.state is None:
            raise LoadError("世界快照尚未就绪")
        quest = self.state.quests.get(quest_id)
        if quest is None:
            return self.reject(pid, "quest_accept", "这个任务已不在世界任务板上（可能已过期或在重生成中）。", "quest_not_found")
        status = str(quest.status)
        if status != "open":
            who = self._actor_name(str(quest.acceptor or "")) if quest.acceptor else "别人"
            return self.reject(pid, "quest_accept", f"这张任务不是开放状态（当前：{status}，接单人：{who}）。", "quest_not_open")
        deadline = int(quest.deadline_tick or 0)
        if deadline and self.tick > deadline:
            return self.reject(
                pid,
                "quest_accept",
                f"任务已过期（截止第 {deadline // TICKS_PER_DAY + 1} 天）。",
                "quest_expired",
            )
        giver = self._entity(str(quest.giver))
        if giver is None or str(giver.status) != "alive":
            return self.reject(pid, "quest_accept", "发布人当前不在世界里或不可用。", "giver_missing")
        if str(ent.location) != str(giver.location):
            return self.reject(
                pid,
                "quest_accept",
                f"接单要去{self._loc_name(str(giver.location))}找{giver.name}。你当前在{self._loc_name(str(ent.location))}。",
                "not_at_giver",
            )
        if ent.escort_id is not None:
            return self.reject(pid, "quest_accept", "你在押镖，不能同时接任务。", "busy_on_escort")
        active = sum(
            1 for q in self.state.quests.values()
            if str(q.acceptor or "") == pid and str(q.status) == "accepted"
        )
        if active >= 2:
            return self.reject(pid, "quest_accept", "你已有 2 个进行中任务，达到规格上限。", "too_many_quests")
        return self.propose(
            pid,
            "quest_accept",
            {"quest": quest_id},
            f"已提交接任务提案：{self._quest_name(str(quest.template_id or ''))}（发布人 {giver.name}，报酬 {quest.reward_money} 文）",
        )


# --------------------------------------------------------------- readonly API
def readonly_player_state(view: WorldView, account: str) -> dict[str, Any]:
    account = str(account or "").strip().lower()
    if not ACCOUNT_RE.match(account):
        return {
            "ok": False,
            "readonly": True,
            "profile_version": PROFILE_VERSION,
            "reject_reason": "bad_account",
            "reason_text": _reason_text("bad_account"),
            "tick": view.tick,
        }
    pid = f"player:{account}"
    scene = view.scene(pid=pid)
    me = scene.get("me") or {}
    status = scene.get("status") or {}
    clock = scene.get("clock") or {}
    quests = scene.get("quests") or {}
    rows = quests.get("rows") or []
    active = [row for row in rows if row.get("accepted_by_me")]
    return {
        "ok": True,
        "readonly": True,
        "profile_version": PROFILE_VERSION,
        "account_id": account,
        "tick": clock.get("tick", view.tick),
        "player": {
            "entity_id": pid,
            "account_id": account,
            "name": me.get("name") or "未注册",
            "archetype": me.get("archetype") or "",
            "role": me.get("role") or "",
            "location": me.get("location") or SPAWN_LOCATION,
            "location_name": me.get("location_name") or "",
            "home": me.get("location") or SPAWN_LOCATION,
            "stamina": me.get("stamina", 0.0),
            "max_stamina": me.get("max_stamina", 100.0),
            "presence": me.get("presence") or "未注册",
            "online": bool(me.get("registered")),
            "created_tick": 0,
            "last_intent_tick": 0,
            "wallet": {"money": status.get("money", 0), "items": status.get("items") or []},
            "skills": {},
            "relations": scene.get("relations") or [],
            "active_quests": [row.get("id") for row in active],
            "registered": bool(me.get("registered")),
        },
        "location": scene.get("location") or {},
        "present_npcs": scene.get("present_npcs") or [],
        "quest_board": {
            "counts": quests.get("counts") or {},
            "rows": [row for row in rows if row.get("status") == "open"],
            "active": active,
        },
        "online_players": [
            {"id": pid_, "self": pid_ == pid}
            for pid_ in (status.get("online_player_ids") or [])
        ],
        "nearby": scene.get("present_npcs") or [],
        "active_quests_detail": active,
    }


def readonly_action(view: WorldView, account: str, verb: str, args: dict[str, Any] | None = None,
                    client_tick: Any = None, request_id: str = "", confirm: bool = False) -> dict[str, Any]:
    account = str(account or "").strip().lower()
    pid = f"player:{account}"
    raw = str(verb or "").strip().lower()
    args = dict(args or {})
    if raw in ("enter", "player_enter"):
        return dict(view.enter(account, str(args.get("display_name") or "").strip(),
                               str(args.get("archetype") or "").strip()), readonly=True)
    if raw == "move":
        result = view.move(pid, str(args.get("to") or "").strip())
    elif raw == "talk":
        result = view.talk(pid, str(args.get("target") or "").strip(), str(args.get("message") or ""))
    elif raw in ("quest_accept", "quest/accept"):
        result = view.accept_quest(pid, str(args.get("quest") or "").strip())
    else:
        return {
            "ok": False,
            "readonly": True,
            "status": "rejected",
            "profile_version": PROFILE_VERSION,
            "action": raw,
            "player": pid,
            "reject_reason": "readonly_mode",
            "reason_text": "只读模式不支持该动作；请去掉 --readonly 并指向可写 run。",
            "tick": view.tick,
        }
    code = str(result.get("reject_reason") or "")
    if code == "world_player_not_registered":
        code = "player_not_entered"
    if code == "player_not_entered":
        message = "该账号在这个只读 run 里没有玩家档案；进入提案不会落地，所以移动/说话/接任务无法执行。"
    else:
        message = str(result.get("message") or result.get("reason_text") or _reason_text(code))
    return {
        "ok": bool(result.get("ok")),
        "readonly": True,
        "status": "proposed" if result.get("ok") else "rejected",
        "profile_version": PROFILE_VERSION,
        "action": raw,
        "player": pid,
        "verb": ACTION_ALIASES.get(raw, raw),
        "intent_id": str(result.get("intent_id") or ""),
        "tick": view.tick,
        "reject_reason": code,
        "reason_text": message,
        "details": {},
        "data": {},
        "pending": bool(result.get("pending")),
        "state_unchanged": bool(result.get("state_unchanged", True)),
        "client_tick": client_tick,
        "confirm": bool(confirm),
    }


def readonly_intent(view: WorldView, intent_id: str) -> dict[str, Any]:
    with view.lock:
        for proposal in view.proposals:
            if str(proposal.get("intent_id")) == str(intent_id):
                return {
                    "ok": True,
                    "found": True,
                    "readonly": True,
                    "profile_version": PROFILE_VERSION,
                    "intent_id": str(intent_id),
                    "tick": int(proposal.get("tick") or 0),
                    "verb": str(proposal.get("verb") or ""),
                    "outcome": "proposed",
                    "status": "proposed",
                    "reject_reason": "",
                    "reason_text": "只读模式：提案停在 outbox，未落地。",
                }
    return {
        "ok": False,
        "found": False,
        "readonly": True,
        "profile_version": PROFILE_VERSION,
        "intent_id": str(intent_id),
        "tick": view.tick,
        "reject_reason": "intent_not_found",
        "reason_text": "没有找到这个请求；只读模式只记录当前进程内的提案。",
    }


def readonly_messages(view: WorldView, account: str, since_tick: Any = 0) -> dict[str, Any]:
    try:
        since = max(0, int(since_tick or 0))
    except (TypeError, ValueError):
        since = 0
    return {
        "ok": True,
        "readonly": True,
        "profile_version": PROFILE_VERSION,
        "account_id": str(account or "").strip().lower(),
        "tick": view.tick,
        "since_tick": since,
        "next_since_tick": view.tick,
        "messages": [],
    }


# --------------------------------------------------------------------- HTTP
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
}


class UpstreamUnavailable(Exception):
    """真 API 不可达（网络层，不是业务拒绝）。"""


def probe_upstream(base: str, timeout: float) -> bool:
    """轻量探活：GET /api/player/state?account_id=u_demo，必须返回契约 JSON。"""
    url = base.rstrip("/") + "/api/player/state?account_id=u_demo"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return isinstance(payload, dict) and payload.get("profile_version") == PROFILE_VERSION
    except (HTTPError, URLError, OSError, ValueError):
        return False


def forward_upstream(
    base: str,
    method: str,
    path_query: str,
    body: bytes | None,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """原样转发；上游的 HTTP 状态码与响应体一并带回。

    headers 用于透传 Authorization 等鉴权头：GET/POST 都必须带，
    否则服务模式（auth_required=True）经代理的读写会被上游判成匿名。
    """
    url = base.rstrip("/") + path_query
    merged = {"Accept": "application/json"}
    if headers:
        merged.update({k: v for k, v in headers.items() if v})
    data = None
    if method == "POST":
        data = body or b"{}"
        merged.setdefault("Content-Type", "application/json; charset=utf-8")
    req = Request(url, data=data, headers=merged, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except (URLError, OSError) as exc:
        raise UpstreamUnavailable(str(exc)) from exc


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _add_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    """在冻结字段之外补 reason/message 别名；不新增任何业务码。"""
    if payload.get("ok") is False or payload.get("status") == "rejected":
        payload.setdefault("reason", str(payload.get("reject_reason") or ""))
        payload.setdefault("message", str(payload.get("reason_text") or ""))
    return payload


class Handler(BaseHTTPRequestHandler):
    view: WorldView | None = None
    mode: str = "live"          # live / readonly
    upstream: str = DEFAULT_UPSTREAM
    timeout: float = 5.0
    server_version = "WorldSimPlayerUI/0.3"

    # --------------------------------------------------------------- helpers
    def _query_account(self, query: dict[str, list[str]]) -> str:
        raw = (query.get("account_id") or query.get("player") or [""])[0]
        return str(raw).strip().lower().removeprefix("player:")

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b"{}"

    def _upstream_headers(self, method: str) -> dict[str, str]:
        """只透传鉴权与内容类型，不把浏览器的其它头带给上游。"""
        headers = {"Accept": "application/json"}
        authorization = self.headers.get("Authorization")
        if authorization:
            headers["Authorization"] = authorization
        if method == "POST":
            headers["Content-Type"] = "application/json; charset=utf-8"
        return headers

    def _send_bytes(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        self._send_bytes(code, _json_bytes(payload))

    def _proxy_unavailable(self) -> None:
        self._json(
            502,
            {
                "ok": False,
                "mode": self.mode,
                "reason": "upstream_unreachable",
                "message": f"真 API 不可达：{self.upstream}。请先启动 wsim.api，或加 --readonly 使用只读降级。",
                "upstream": self.upstream,
            },
        )

    def _health(self) -> None:
        if self.mode == "live":
            ok = probe_upstream(self.upstream, self.timeout)
            self._json(
                200,
                {
                    "ok": True,
                    "mode": "live" if ok else "degraded",
                    "readonly": False,
                    "profile_version": PROFILE_VERSION,
                    "upstream": self.upstream,
                    "upstream_ok": ok,
                    "docs": "world-sim/docs/player-api.md",
                },
            )
            return
        view = self.view
        if view is None:
            self._json(
                200,
                {
                    "ok": False,
                    "mode": "readonly",
                    "readonly": True,
                    "reason": "readonly_unavailable",
                    "message": "只读 run 不可用。",
                    "upstream": self.upstream,
                    "upstream_ok": False,
                },
            )
            return
        payload = view.health()
        payload.update({"mode": "readonly", "readonly": True, "upstream": self.upstream, "upstream_ok": False})
        self._json(200 if payload.get("ok") else 503, payload)

    # ------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in STATIC_FILES:
                self._static(STATIC_FILES[path])
                return
            if path == "/api/health":
                self._health()
                return
            if path.startswith("/api/player/"):
                self._read(path, query)
                return
            if path in ("/api/auth/me", "/api/service/info", "/api/state"):
                self._read(path, query)
                return
            if self.mode == "readonly" and path == "/api/scene":
                self._json(200, self.view.scene(
                    pid=(query.get("player") or [""])[0],
                    archetype=(query.get("archetype") or [""])[0],
                ))
                return
            if self.mode == "readonly" and path.startswith("/api/npc/"):
                npc_id = unquote(path[len("/api/npc/"):])
                self._json(200, self.view.npc_detail(
                    npc_id,
                    pid=(query.get("player") or [""])[0],
                ))
                return
            self._json(
                404,
                {
                    "ok": False,
                    "reason": "not_supported",
                    "message": "真 API 模式没有这个接口；请使用 /api/player/state。",
                },
            )
        except LoadError as exc:
            self._json(503, {"ok": False, "error": exc.message, "hint": exc.hint})
        except Exception as exc:  # 兜底：不白屏
            self._json(500, {"ok": False, "error": f"服务端错误：{type(exc).__name__}: {exc}"})

    def _read(self, path: str, query: dict[str, list[str]]) -> None:
        if self.mode == "readonly":
            self._read_local(path, query)
            return
        try:
            code, body = forward_upstream(
                self.upstream, "GET", self.path, None, self.timeout,
                headers=self._upstream_headers("GET"),
            )
        except UpstreamUnavailable:
            self._proxy_unavailable()
            return
        payload = self._parse(body)
        if payload is None:
            self._send_bytes(code, body)
            return
        _add_aliases(payload)
        if code >= 400:
            # 鉴权错误必须原样保留 401/403 与错误体，不能吞成 200/502。
            self._json(code, payload)
            return
        # 读接口成功一律 200：页面不会把业务性 ok:false 当成网络错误。
        self._json(200, payload)

    def _read_local(self, path: str, query: dict[str, list[str]]) -> None:
        view = self.view
        if view is None:
            self._json(503, {"ok": False, "reason": "readonly_unavailable", "message": "只读 run 不可用。"})
            return
        account = self._query_account(query)
        if path == "/api/player/state":
            payload = readonly_player_state(view, account)
        elif path == "/api/player/messages":
            payload = readonly_messages(view, account, (query.get("since_tick") or ["0"])[0])
        elif path.startswith("/api/player/intents/"):
            payload = readonly_intent(view, unquote(path[len("/api/player/intents/"):]))
        else:
            self._json(404, {"ok": False, "reason": "not_supported", "message": "只读模式只支持 state/messages/intents。"})
            return
        _add_aliases(payload)
        self._json(200, payload)

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path.startswith("/api/auth/") or path == "/api/player/reconnect":
                if self.mode != "live":
                    self._json(404, {"ok": False, "reason": "not_supported",
                                     "message": "只读模式不转发鉴权端点。"})
                    return
                self._forward_auth()
                return
            if path in WRITE_PATHS:
                body = self._body()
                self._write(path, body)
                return
            self._json(
                404,
                {
                    "ok": False,
                    "reason": "unknown_action",
                    "message": "未知接口；可用接口见 docs/player-api.md。",
                },
            )
        except ValueError as exc:
            self._json(400, {"ok": False, "reason": "bad_args", "message": f"请求不合法：{exc}"})
        except Exception as exc:
            self._json(500, {"ok": False, "reason": "server_error", "message": f"服务端错误：{type(exc).__name__}: {exc}"})

    def _write(self, path: str, body: bytes) -> None:
        if self.mode == "readonly":
            self._write_local(path, body)
            return
        try:
            code, raw = forward_upstream(
                self.upstream, "POST", self.path, body, self.timeout,
                headers=self._upstream_headers("POST"),
            )
        except UpstreamUnavailable:
            self._proxy_unavailable()
            return
        payload = self._parse(raw)
        if payload is None:
            self._send_bytes(code, raw)
            return
        _add_aliases(payload)
        if code >= 400 and code != 409:
            # 401/403（以及 400/429/5xx）原样带回，前端才能区分重新登录与业务拒绝。
            self._json(code, payload)
            return
        rejected = payload.get("ok") is False or payload.get("status") == "rejected"
        self._json(409 if rejected else 200, payload)

    def _forward_auth(self) -> None:
        """鉴权端点原样转发（POST），保留 200/400/401/409/503 语义。"""
        body = self._body()
        try:
            code, raw = forward_upstream(
                self.upstream, "POST", self.path, body, self.timeout,
                headers=self._upstream_headers("POST"),
            )
        except UpstreamUnavailable:
            self._proxy_unavailable()
            return
        payload = self._parse(raw)
        if payload is None:
            self._send_bytes(code, raw)
            return
        self._json(code, payload)

    def _write_local(self, path: str, body: bytes) -> None:
        view = self.view
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(data, dict):
            raise ValueError("body_must_be_object")
        if view is None:
            self._json(503, {"ok": False, "reason": "readonly_unavailable", "message": "只读 run 不可用。"})
            return
        account = str(data.get("account_id") or data.get("pid") or "").strip().lower().removeprefix("player:")
        if path == "/api/player/enter":
            verb = "enter"
            args = {
                "display_name": data.get("display_name") or "",
                "archetype": data.get("archetype") or "",
            }
        else:
            verb = str(data.get("verb") or data.get("action") or "").strip().lower()
            raw_args = data.get("args")
            args = raw_args if isinstance(raw_args, dict) else data
        payload = readonly_action(
            view,
            account,
            verb,
            args,
            data.get("client_tick"),
            str(data.get("request_id") or ""),
            bool(data.get("confirm")),
        )
        _add_aliases(payload)
        rejected = payload.get("ok") is False or payload.get("status") == "rejected"
        self._json(409 if rejected else 200, payload)

    @staticmethod
    def _parse(raw: bytes) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    # ---------------------------------------------------------------- static
    def _static(self, name: str) -> None:
        file_path = UI_DIR / name
        if not file_path.exists():
            self._json(500, {"ok": False, "error": f"{name} 不存在"})
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(file_path.suffix, "application/octet-stream")
        self._send_bytes(200, file_path.read_bytes(), ctype)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[player-ui] %s - %s\n" % (self.address_string(), fmt % args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="玩家门面：真 API 反向代理（默认），--readonly 降级")
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM, help=f"真 API 地址（默认 {DEFAULT_UPSTREAM}）")
    parser.add_argument("--port", type=int, default=DEFAULT_UI_PORT, help=f"本服务端口（默认 {DEFAULT_UI_PORT}）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument(
        "--readonly",
        action="store_true",
        help="强制只读降级：本地回放 + propose_intent，不写 events.sqlite",
    )
    parser.add_argument("--run", default=DEFAULT_READONLY_RUN, help=f"只读 run（默认 {DEFAULT_READONLY_RUN}）")
    parser.add_argument("--upstream-timeout", type=float, default=5.0, help="上游超时秒数（默认 5）")
    args = parser.parse_args(argv)

    upstream_ok = False if args.readonly else probe_upstream(args.upstream, args.upstream_timeout)
    view: WorldView | None = None
    if args.readonly or not upstream_ok:
        try:
            view = WorldView(args.run)
        except LoadError as exc:
            print(f"[player-ui] 只读 run 启动失败：{exc.message}", file=sys.stderr)
            print(f"[player-ui] 提示：{exc.hint}", file=sys.stderr)
            return 3
        mode = "readonly"
        if not args.readonly:
            print(
                f"[player-ui] 上游不可达：{args.upstream}，已自动降级为只读模式。"
                "先启动真 API 可获得真实写链路。",
                file=sys.stderr,
            )
    else:
        mode = "live"

    Handler.view = view
    Handler.mode = mode
    Handler.upstream = args.upstream
    Handler.timeout = args.upstream_timeout

    if mode == "live":
        print(f"[player-ui] mode=live upstream={args.upstream}", file=sys.stderr)
    else:
        print(f"[player-ui] mode=readonly run={view.run_label if view else args.run}", file=sys.stderr)
        if view is not None and view.error is not None:
            print(f"[player-ui] run 不可用：{view.error.message}", file=sys.stderr)
        else:
            print(
                "[player-ui] 只读模式：写操作只提交提案，不落地；页面会显示“只读模式”。",
                file=sys.stderr,
            )
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"[player-ui] 无法监听 {args.host}:{args.port}：{exc}", file=sys.stderr)
        return 2
    httpd.daemon_threads = True
    print(f"[player-ui] 打开 http://{args.host}:{args.port}/  （Ctrl+C 停止）", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[player-ui] 已停止", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
