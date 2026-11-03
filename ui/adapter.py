"""只读适配层：把冻结 run 目录变成 UI 可用的视图。

边界：
- 世界状态只通过 MCPGateway 的 `world.read_snapshot` 获取；
- 对话走 `world.propose_intent` → `admin.request/approve/apply`，由 Writer 落到
  一份 run 目录的临时副本（不写回原 run）；
- run 目录里的 JSON/JSONL 只做只读解析；SQLite 只复制到临时 session 后写入；
- 本模块只用 Python 标准库（导入的 wsim 内核模块也均为标准库实现）。

数据来源（默认 runs/p2b-72d）：
summary.json / events.jsonl / lod.json / decisions.jsonl / events.sqlite /
content/locations.yaml + content/additions/locations.yaml /
content/personas/<id>.md。

注意：这是离线 run 快照，不是实时世界。事件回放重建只读投影；
对话写入只发生在临时 session 副本，原 run 不被修改。
"""
from __future__ import annotations

import atexit
import hashlib
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

WORLD_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = WORLD_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from wsim.actions.registry import build_registry  # noqa: E402
from wsim.agent.dialogue import PLAYER_ID, compose_talk  # noqa: E402
from wsim.core.content import load_content  # noqa: E402
from wsim.core.models import Effect  # noqa: E402
from wsim.mcp.audit import AuditLog  # noqa: E402
from wsim.mcp.gateway import (  # noqa: E402
    GatewayError,
    MCPGateway,
    PERMISSION_ADMIN,
    PERMISSION_PROPOSE,
)
from wsim.sim.intent import GOAL_LABEL  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402
from wsim.world.store import EventStore  # noqa: E402
from wsim.world.writer import Writer  # noqa: E402

UI_CALLER = "ui"
UI_REQUESTER = "ui:requester"
UI_REVIEWER = "ui:reviewer"
NOTICE = "数据来自离线 run 快照；对话经审批链落到临时 session，原 run 不被修改。"

VERB_LABEL = {
    "move": "移动",
    "cultivate": "修炼",
    "work": "上工",
    "consume": "吃饭",
    "buy": "采购",
    "sell": "售卖",
    "trade": "交易",
    "apprentice": "拜师",
    "join_sect": "入会",
    "marry": "成婚",
    "open_shop": "开店",
    "escort": "押镖",
    "found_sect": "开宗立派",
    "recruit": "招揽",
    "quest_accept": "接任务",
    "quest_deliver": "交任务",
    "debt_settle": "还债",
    "borrow_money": "借钱",
    "talk": "发言",
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


class SnapshotError(Exception):
    """run 目录不可用。server 会把它转成友好提示，而不是白屏。"""

    def __init__(self, message: str, hint: str = ""):
        self.message = message
        self.hint = hint or "请用 --run 指向包含 summary.json / events.jsonl / lod.json 的 run 目录。"
        super().__init__(message)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SnapshotError(f"缺少文件：{path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"无法解析 {path.name}：{exc}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SnapshotError(f"{path.name} 第 {lineno} 行不是合法 JSON：{exc}") from exc
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError as exc:
        raise SnapshotError(f"缺少文件：{path.name}") from exc
    except OSError as exc:
        raise SnapshotError(f"无法读取 {path.name}：{exc}") from exc
    return rows


def _parse_locations(path: Path) -> dict[str, dict[str, Any]]:
    """极简只读 YAML 解析：只取 locations 的 id / name / exits / services。

    不引入 PyYAML，避免 UI 变成第三方依赖。内容包格式固定且简单，
    解析失败时返回空表，由更上层的“地点”展示降级为 id。
    """
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    current: dict[str, Any] | None = None
    in_exits = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        m = re.match(r"\s*-\s*id:\s*(.+)$", line)
        if m:
            lid = m.group(1).strip().strip("'\"")
            current = {"id": lid, "name": lid, "exits": [], "services": []}
            out[lid] = current
            in_exits = False
            continue
        if current is None:
            continue
        m = re.match(r"\s*name:\s*(.+)$", line)
        if m:
            current["name"] = m.group(1).strip().strip("'\"")
            continue
        m = re.match(r"\s*exits:\s*\[(.+)\]\s*$", line)
        if m:
            current["exits"] = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
            continue
        m = re.match(r"\s*services:\s*\[(.+)\]\s*$", line)
        if m:
            current["services"] = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
            continue
    return out


def _merge_locations(base: dict[str, dict[str, Any]], extra: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged = dict(base)
    for lid, row in extra.items():
        if lid not in merged:
            merged[lid] = row
        else:
            merged[lid].setdefault("exits", [])
            merged[lid]["exits"] = sorted(set(merged[lid]["exits"]) | set(row.get("exits") or []))
    # additions 只有单向 exit：补齐反向边，便于 UI 展示连通关系。
    for lid, row in merged.items():
        for other in row.get("exits") or []:
            if other in merged:
                merged[other].setdefault("exits", [])
                if lid not in merged[other]["exits"]:
                    merged[other]["exits"].append(lid)
        row["exits"] = sorted(set(row.get("exits") or []))
    return merged


def _no_wall_clock_note() -> str:
    return "tick 为虚拟时间；本界面不读取墙钟做世界推进。"


class WorldUIAdapter:
    """run 目录 -> 只读视图 + gateway 提案入口。"""

    def __init__(self, run_dir: str | Path):
        self.run_path = self._resolve_run(run_dir)
        self.run_label = str(self.run_path)
        self.lock = threading.RLock()
        self.error: SnapshotError | None = None
        self.state: WorldState | None = None
        self.gateway: MCPGateway | None = None
        self.session_dir: Path | None = None
        self.session_writer: Writer | None = None
        self.summary: dict[str, Any] = {}
        self.lod: dict[str, Any] = {}
        self.locations: dict[str, dict[str, Any]] = {}
        self.personas: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self.decisions_by_npc: dict[str, dict[str, Any]] = {}
        self.state_hash_recomputed = ""
        self.state_hash_match = False
        self.days = 0
        self.final_day = 0
        try:
            self._load()
            self.error = None
        except SnapshotError as exc:
            self.error = exc
        except Exception as exc:  # 兜底也要变成友好提示
            self.error = SnapshotError(f"加载 run 失败：{type(exc).__name__}: {exc}")

    # ---------------------------------------------------------------- loading
    @staticmethod
    def _resolve_run(run_dir: str | Path) -> Path:
        path = Path(run_dir)
        if not path.is_absolute():
            path = (WORLD_ROOT / path).resolve()
        return path

    def _load(self) -> None:
        if not self.run_path.exists():
            raise SnapshotError(f"run 目录不存在：{self.run_path}")
        if not self.run_path.is_dir():
            raise SnapshotError(f"--run 不是目录：{self.run_path}")

        required = ["summary.json", "events.jsonl", "lod.json"]
        missing = [name for name in required if not (self.run_path / name).exists()]
        if missing:
            raise SnapshotError(f"run 目录缺少文件：{', '.join(missing)}")

        self.summary = _read_json(self.run_path / "summary.json")
        self.lod = _read_json(self.run_path / "lod.json")
        self.days = int(self.summary.get("days") or 0)
        if self.days <= 0:
            raise SnapshotError("summary.json 里的 days 无效")

        base = _parse_locations(WORLD_ROOT / "content" / "locations.yaml")
        extra = _parse_locations(WORLD_ROOT / "content" / "additions" / "locations.yaml")
        self.locations = _merge_locations(base, extra)

        persona_dir = WORLD_ROOT / "content" / "personas"
        if persona_dir.is_dir():
            for p in sorted(persona_dir.glob("*.md")):
                self.personas[p.stem] = p.read_text(encoding="utf-8")

        decisions_path = self.run_path / "decisions.jsonl"
        if decisions_path.exists():
            for row in _read_jsonl(decisions_path):
                npc = str(row.get("npc") or "")
                if npc:
                    self.decisions_by_npc[npc] = row

        self._replay_events()
        self.final_day = int(self.summary.get("days") or self.days)

        expected = ""
        hash_path = self.run_path / "world_hash.txt"
        if hash_path.exists():
            expected = hash_path.read_text(encoding="utf-8").strip()
        assert self.state is not None
        self.state_hash_recomputed = state_hash(self.state)
        self.state_hash_match = bool(expected) and expected == self.state_hash_recomputed

        self._setup_session_writer()

        # 对话走审批链：proposer 只能提案，requester/reviewer 是两个不同身份的管理员。
        self.gateway = MCPGateway(
            state_provider=self._active_state,
            writer=self.session_writer,
            audit=AuditLog(None),
            tick_provider=lambda: int(self._active_state().tick),
            identities={
                UI_CALLER: PERMISSION_PROPOSE,
                UI_REQUESTER: PERMISSION_ADMIN,
                UI_REVIEWER: PERMISSION_ADMIN,
            },
        )

    def _active_state(self) -> WorldState:
        if self.session_writer is not None:
            return self.session_writer.state
        assert self.state is not None
        return self.state

    def _setup_session_writer(self) -> None:
        """把 run 的 events.sqlite 复制到临时目录，绑定 Writer 做对话落地。"""
        src = self.run_path / "events.sqlite"
        if not src.exists() or self.state is None:
            return
        self.session_dir = Path(tempfile.mkdtemp(prefix="wsim-ui-"))
        dst = self.session_dir / "events.sqlite"
        shutil.copy2(src, dst)
        content = load_content(WORLD_ROOT, min_npcs=0, allow_fixture=False, use_content=True)
        seed = int(self.summary.get("seed") or 42)
        store = EventStore(dst)
        registry = build_registry(content, seed)
        self.session_writer = Writer(self.state.clone(), store, registry, seed)
        atexit.register(shutil.rmtree, str(self.session_dir), True)

    def _replay_events(self) -> None:
        """用 events.jsonl 的 Effect 重建只读投影，并建立事件索引。"""
        assert self.locations, "没有解析到任何地点，拒绝在空地点图上回放"
        state = WorldState(locations=self.locations)
        events: list[dict[str, Any]] = []

        for row in _read_jsonl(self.run_path / "events.jsonl"):
            effects = row.get("effects") or []
            actor = str(row.get("actor") or "")
            loc_before = state.entities[actor].location if actor in state.entities else ""
            debt_parties: dict[str, tuple[str, str]] = {}
            for eff in effects:
                if not isinstance(eff, dict) or eff.get("op") != "debt_settle":
                    continue
                did = str((eff.get("data") or {}).get("id") or "")
                debt = state.debts.get(did)
                if debt is not None:
                    debt_parties[did] = (debt.creditor, debt.debtor)

            try:
                apply_effects(state, [Effect.from_dict(e) for e in effects if isinstance(e, dict)])
            except Exception as exc:
                raise SnapshotError(
                    f"events.jsonl 第 {row.get('seq')} 条事件回放失败：{type(exc).__name__}: {exc}"
                ) from exc
            state.tick = int(row.get("tick") or 0)

            tick = int(row.get("tick") or 0)
            day = int(row.get("day") or 0)
            dest = state.entities[actor].location if actor in state.entities else ""
            delta = self._money_delta(effects, actor, debt_parties)
            events.append(
                {
                    "seq": int(row.get("seq") or 0),
                    "tick": tick,
                    "day": day,
                    "day_label": f"第 {day + 1} 天",
                    "time": self._time_label(tick),
                    "actor": actor,
                    "verb": str(row.get("verb") or ""),
                    "verb_label": VERB_LABEL.get(str(row.get("verb") or ""), str(row.get("verb") or "")),
                    "outcome": str(row.get("outcome") or ""),
                    "reason": str(row.get("reason") or ""),
                    "source": str(row.get("source") or ""),
                    "location": loc_before,
                    "location_name": self.locations.get(loc_before, {}).get("name", loc_before),
                    "dest": dest,
                    "dest_name": self.locations.get(dest, {}).get("name", dest),
                    "money_delta": delta,
                    "args": row.get("args") or {},
                }
            )

        self.state = state
        self.events = events

    @staticmethod
    def _time_label(tick: int) -> str:
        day = tick // 144
        hour = (tick % 144) // 6
        minute = (tick % 6) * 10
        return f"第 {day + 1} 天 {hour:02d}:{minute:02d}"

    @staticmethod
    def _money_delta(effects: list[dict[str, Any]], actor: str, debt_parties: dict[str, tuple[str, str]]) -> int:
        if not actor or actor == "system":
            return 0
        wallet = f"wallet:{actor}"
        delta = 0
        for eff in effects:
            if not isinstance(eff, dict):
                continue
            op = eff.get("op")
            data = eff.get("data") or {}
            if op == "transfer":
                amount = int(data.get("amount") or 0)
                if data.get("src") == wallet:
                    delta -= amount
                if data.get("dst") == wallet:
                    delta += amount
            elif op in ("mint", "burn"):
                amount = int(data.get("amount") or 0)
                if data.get("account") == wallet:
                    delta += amount if op == "mint" else -amount
            elif op == "debt_settle":
                amount = int(data.get("amount") or 0)
                parties = debt_parties.get(str(data.get("id") or ""))
                if parties:
                    creditor, debtor = parties
                    if actor == debtor:
                        delta -= amount
                    if actor == creditor:
                        delta += amount
        return delta

    # ---------------------------------------------------------------- helpers
    def _require_ready(self) -> None:
        if self.error is not None:
            raise self.error
        if self.gateway is None or self.state is None:
            raise SnapshotError("世界快照尚未就绪")

    def _read_snapshot(self) -> tuple[dict[str, Any], int]:
        """唯一的状态入口：MCP gateway 的只读快照。"""
        self._require_ready()
        assert self.gateway is not None
        result = self.gateway.call(UI_CALLER, "world.read_snapshot", {})
        snapshot = result.get("snapshot") or {}
        return snapshot, int(result.get("tick") or 0)

    def _goal_view(self, goal: dict[str, Any] | None) -> dict[str, Any] | None:
        if not goal:
            return None
        kind = str(goal.get("kind") or "")
        ctype = str(goal.get("content_type") or "")
        label = GOAL_LABEL.get(ctype) or GOAL_LABEL.get(kind) or ctype or kind or "目标"
        progress = float(goal.get("progress") or 0.0)
        target = float(goal.get("target") or 1.0)
        return {
            "id": goal.get("id"),
            "kind": kind,
            "content_type": ctype,
            "label": label,
            "progress": progress,
            "progress_pct": round(min(1.0, progress) * 100, 1),
            "target": target,
            "status": goal.get("status"),
            "created_tick": goal.get("created_tick"),
        }

    def _lod_view(self, npc: str) -> dict[str, Any]:
        stats = (self.lod.get("stats") or {}).get(npc) or {}
        changes = self.lod.get("changes") or []
        mine = [c for c in changes if str(c.get("npc")) == npc]
        return {
            "tier": stats.get("lod") or "未记录",
            "wakes": int(stats.get("wakes") or 0),
            "interval": int(stats.get("interval") or 0),
            "cost_weight": stats.get("cost_weight"),
            "changes": mine[-5:],
        }

    @staticmethod
    def _entity_name(entities: dict[str, Any], eid: str) -> str:
        row = entities.get(eid) or {}
        return str(row.get("name") or eid)

    def _event_view(self, e: dict[str, Any]) -> dict[str, Any]:
        return {
            "seq": e["seq"],
            "tick": e["tick"],
            "day": e["day"] + 1,
            "day_label": e["day_label"],
            "time": e["time"],
            "actor": e["actor"],
            "verb": e["verb"],
            "verb_label": e["verb_label"],
            "outcome": e["outcome"],
            "reason": e["reason"],
            "source": e["source"],
            "location": e["location_name"] or e["location"],
            "dest": e["dest_name"] if e["dest"] != e["location"] else "",
            "money_delta": e["money_delta"],
        }

    # ---------------------------------------------------------------- views
    def overview(self) -> dict[str, Any]:
        snapshot, tick = self._read_snapshot()
        entities = snapshot.get("entities") or {}
        accounts = snapshot.get("accounts") or {}
        goals = snapshot.get("goals") or {}
        businesses = snapshot.get("businesses") or {}
        by_tier = self.lod.get("by_tier") or {}

        loc_npcs: dict[str, list[str]] = {lid: [] for lid in self.locations}
        for npc, ent in entities.items():
            loc = str(ent.get("location") or "")
            loc_npcs.setdefault(loc, []).append(str(npc))
            loc_npcs[loc].sort()

        biz_count: dict[str, int] = {}
        for row in businesses.values():
            loc = str(row.get("location") or "")
            biz_count[loc] = biz_count.get(loc, 0) + 1

        npc_rows = []
        for npc, ent in sorted(entities.items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
            goal = goals.get(ent.get("goal_id")) if ent.get("goal_id") else None
            loc = str(ent.get("location") or "")
            npc_rows.append(
                {
                    "id": npc,
                    "name": ent.get("name"),
                    "role": ent.get("role"),
                    "location": loc,
                    "location_name": self.locations.get(loc, {}).get("name", loc),
                    "home": ent.get("home"),
                    "home_name": self.locations.get(str(ent.get("home") or ""), {}).get("name", ent.get("home")),
                    "status": ent.get("status"),
                    "cash": int(accounts.get(f"wallet:{npc}") or 0),
                    "goal": self._goal_view(goal),
                    "lod": self._lod_view(str(npc)),
                }
            )

        locations = []
        for lid, row in sorted(self.locations.items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
            locations.append(
                {
                    "id": lid,
                    "name": row.get("name") or lid,
                    "exits": row.get("exits") or [],
                    "npc_count": len(loc_npcs.get(lid, [])),
                    "business_count": biz_count.get(lid, 0),
                    "npcs": loc_npcs.get(lid, []),
                }
            )

        return {
            "notice": NOTICE,
            "clock": {
                "tick": tick,
                "day": self._time_label(tick),
                "final_day": self.final_day,
                "ticks_per_day": 144,
            },
            "run": {
                "run_id": self.summary.get("run_id"),
                "seed": self.summary.get("seed"),
                "days": self.summary.get("days"),
                "npc_count": self.summary.get("npc_count"),
                "content_hash": self.summary.get("content_hash"),
                "hashes": self.summary.get("hashes"),
                "state_hash_recomputed": self.state_hash_recomputed,
                "state_hash_match": self.state_hash_match,
                "path": self.run_label,
                "source": "离线 run 快照",
            },
            "counts": {
                "npcs": len(entities),
                "locations": len(self.locations),
                "events": len(self.events),
                "days": self.days,
            },
            "lod_by_tier": by_tier,
            "locations": locations,
            "npcs": npc_rows,
        }

    def npc_detail(self, npc_id: str) -> dict[str, Any]:
        self._require_ready()
        snapshot, tick = self._read_snapshot()
        entities = snapshot.get("entities") or {}
        if npc_id not in entities:
            raise KeyError(npc_id)
        ent = entities[npc_id]
        goals_all = snapshot.get("goals") or {}
        relations = snapshot.get("relations") or {}
        debts = snapshot.get("debts") or {}
        accounts = snapshot.get("accounts") or {}
        factions = snapshot.get("factions") or {}
        businesses = snapshot.get("businesses") or {}

        my_goals = [
            self._goal_view(g)
            for g in sorted(goals_all.values(), key=lambda x: int(x.get("created_tick") or 0))
            if str(g.get("npc")) == npc_id
        ]
        my_goals.sort(key=lambda g: (0 if g and g.get("status") == "active" else 1, -(g or {}).get("created_tick") or 0))

        rel_rows = []
        for rel in relations.values():
            a, b = str(rel.get("a")), str(rel.get("b"))
            if npc_id not in (a, b):
                continue
            other = b if a == npc_id else a
            rel_rows.append(
                {
                    "direction": "出" if a == npc_id else "入",
                    "target": other,
                    "target_name": self._entity_name(entities, other),
                    "kind": rel.get("kind"),
                    "affection": rel.get("affection"),
                    "trust": rel.get("trust"),
                    "debt": int(rel.get("debt") or 0),
                    "status": rel.get("status"),
                }
            )
        rel_rows.sort(key=lambda r: (r["kind"], r["target_name"]))

        debt_rows = []
        for debt in sorted(debts.values(), key=lambda d: str(d.get("id"))):
            creditor, debtor = str(debt.get("creditor")), str(debt.get("debtor"))
            if npc_id not in (creditor, debtor):
                continue
            role = "债权人" if creditor == npc_id else "债务人"
            other = debtor if creditor == npc_id else creditor
            debt_rows.append(
                {
                    "id": debt.get("id"),
                    "role": role,
                    "counterparty": other,
                    "counterparty_name": self._entity_name(entities, other),
                    "amount": int(debt.get("amount") or 0),
                    "long_term": bool(debt.get("long_term")),
                    "status": debt.get("status"),
                    "note": debt.get("note"),
                    "created_tick": debt.get("created_tick"),
                }
            )

        mine = [e for e in self.events if e["actor"] == npc_id]
        recent = [self._event_view(e) for e in sorted(mine, key=lambda x: x["seq"], reverse=True)[:10]]
        today_index = self.final_day - 1
        today = [self._event_view(e) for e in mine if e["day"] == today_index]
        today.sort(key=lambda e: e["seq"])

        persona_raw = self.personas.get(npc_id, "")
        voice_lines = []
        for line in persona_raw.splitlines():
            m = re.match(r"\s*>\s*(.+)$", line)
            if m:
                text = m.group(1).strip().strip("“”\"'")
                if text:
                    voice_lines.append(text)

        loc = str(ent.get("location") or "")
        faction_id = ent.get("faction")
        biz_id = ent.get("business") or ent.get("employed_at")
        return {
            "notice": NOTICE,
            "clock": {"tick": tick, "label": self._time_label(tick), "final_day": self.final_day},
            "npc": {
                "id": npc_id,
                "name": ent.get("name"),
                "role": ent.get("role"),
                "status": ent.get("status"),
                "location": loc,
                "location_name": self.locations.get(loc, {}).get("name", loc),
                "home": ent.get("home"),
                "home_name": self.locations.get(str(ent.get("home") or ""), {}).get("name", ent.get("home")),
                "cash": int(accounts.get(f"wallet:{npc_id}") or 0),
                "stamina": ent.get("stamina"),
                "max_stamina": ent.get("max_stamina"),
                "inventory": ent.get("inventory") or {},
                "skills": ent.get("skills") or {},
                "personality": ent.get("personality") or {},
                "faction": faction_id,
                "faction_name": (factions.get(str(faction_id)) or {}).get("name") if faction_id else "",
                "business": biz_id,
                "business_name": (businesses.get(str(biz_id)) or {}).get("name") if biz_id else "",
                "spouse": ent.get("spouse"),
                "spouse_name": self._entity_name(entities, ent.get("spouse")) if ent.get("spouse") else "",
                "master": ent.get("master"),
                "master_name": self._entity_name(entities, ent.get("master")) if ent.get("master") else "",
                "disciples": ent.get("disciples") or [],
            },
            "goals": my_goals,
            "relations": rel_rows,
            "debts": debt_rows,
            "recent_events": recent,
            "today_schedule": today,
            "today_label": f"第 {self.final_day} 天",
            "last_plan": self.decisions_by_npc.get(npc_id),
            "lod": self._lod_view(npc_id),
            "persona": {
                "path": f"content/personas/{npc_id}.md",
                "voice_lines": voice_lines,
                "raw": persona_raw[:4000],
            },
        }

    def timeline(
        self,
        day: int | None = None,
        npc: str = "",
        location: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        self._require_ready()
        chosen_day = int(day or self.final_day)
        if chosen_day < 1 or chosen_day > self.days:
            raise ValueError(f"day 必须在 1..{self.days}")
        day_index = chosen_day - 1
        selected = [e for e in self.events if e["day"] == day_index]
        if npc:
            selected = [e for e in selected if e["actor"] == npc]
        if location:
            selected = [e for e in selected if e["location"] == location or e["dest"] == location]
        total = len(selected)
        limited = selected[: max(1, min(limit, 2000))]
        entities: dict[str, Any] = {}
        if self.gateway is not None and self.state is not None:
            snapshot, _ = self._read_snapshot()
            entities = snapshot.get("entities") or {}
        rows = []
        for e in limited:
            row = self._event_view(e)
            row["actor_name"] = self._entity_name(entities, e["actor"]) if entities else e["actor"]
            rows.append(row)
        return {
            "notice": NOTICE + " 时间轴按天回放事件日志。",
            "day": chosen_day,
            "days": list(range(1, self.days + 1)),
            "filters": {"npc": npc, "location": location},
            "total": total,
            "returned": len(rows),
            "truncated": total > len(rows),
            "events": rows,
            "npcs": [
                {"id": npc_id, "name": ent.get("name")}
                for npc_id, ent in sorted(entities.items(), key=lambda kv: str(kv[1].get("name") or kv[0]))
            ],
        }

    # ---------------------------------------------------------------- dialogue
    def dialogue(self, npc_id: str, message: str) -> dict[str, Any]:
        """对话落地：人设卡回应 → propose_intent → 双人审批 → Writer 写事件。

        - 回应由 `agent.dialogue.compose_talk` 消费人设卡（台词原文 + 反应倾向）；
        - 提案走 MCPGateway 的 world.propose_intent 进入 outbox；
        - 再经 admin.request → admin.approve → admin.apply，由 Writer 写入临时 session；
        - 记忆与关系变化随 Effect 进事件日志，可回放；不产生任何钱变动。
        """
        self._require_ready()
        message = (message or "").strip()
        if not message:
            raise ValueError("message 不能为空")
        if len(message) > 200:
            raise ValueError("message 最多 200 字")
        assert self.gateway is not None and self.state is not None

        with self.lock:
            snapshot, tick = self._read_snapshot()
            entities = snapshot.get("entities") or {}
            ent = entities.get(npc_id)
            if ent is None:
                raise KeyError(npc_id)

            started = time.perf_counter()
            loc = str(ent.get("location") or "")
            name = str(ent.get("name") or npc_id)
            role = str(ent.get("role") or "")
            talk = compose_talk(self.personas.get(npc_id, ""), npc_id, name, role, message, tick)
            reply = str(talk["reply"])
            prev_rel = self.state.relation(PLAYER_ID, npc_id, "acquaintance")
            prev_aff = prev_rel.affection if prev_rel is not None else 0.0
            prev_trust = prev_rel.trust if prev_rel is not None else 40.0
            reasoning_ms = round((time.perf_counter() - started) * 1000, 1)

            raw_intent = {
                "actor": PLAYER_ID,
                "verb": "talk",
                "args": {
                    "target": npc_id,
                    "message": message,
                    "reply": reply,
                    "affection_delta": talk["affection_delta"],
                    "trust_delta": talk["trust_delta"],
                    "from": "visitor",
                },
                "tick": tick,
                "source": "ui:proposal",
            }
            before = len(self.gateway.audit.read_all())
            proposal: dict[str, Any]
            landed: dict[str, Any]
            relation: dict[str, Any] = {}
            memory: list[dict[str, Any]] = []
            try:
                proposed = self.gateway.call(UI_CALLER, "world.propose_intent", {"intent": raw_intent})
                proposal = {
                    "ok": True,
                    "status": "已提交意图",
                    "accepted": bool(proposed.get("accepted")),
                    "intent_id": proposed.get("intent_id"),
                    "tick": proposed.get("tick"),
                    "state_unchanged": bool(proposed.get("state_unchanged")),
                }
            except GatewayError as exc:
                proposed = {}
                proposal = {"ok": False, "status": "被拒", "error": str(exc), "state_unchanged": True}

            if self.session_writer is None:
                landed = {"landed": False, "error": "run_missing_events_sqlite"}
            elif not proposal["ok"]:
                landed = {"landed": False, "error": proposal.get("error", "proposal_rejected")}
            else:
                try:
                    request = self.gateway.call(
                        UI_REQUESTER, "admin.request", {"intent": raw_intent, "reason": "ui dialogue"}
                    )
                    self.gateway.call(UI_REVIEWER, "admin.approve", {"token": request["token"]})
                    applied = self.gateway.call(UI_REVIEWER, "admin.apply", {"token": request["token"]})
                    self.state = self.session_writer.state
                    memory = [
                        m
                        for m in self.state.memories.get(npc_id, [])
                        if str(m.get("source", "")).startswith("talk:")
                    ][-2:]
                    rel = self.state.relation(PLAYER_ID, npc_id, "acquaintance")
                    if rel is not None:
                        relation = {
                            "kind": rel.kind,
                            "affection": round(rel.affection, 4),
                            "trust": round(rel.trust, 4),
                            "affection_delta": round(rel.affection - prev_aff, 4),
                            "trust_delta": round(rel.trust - prev_trust, 4),
                        }
                    landed = {
                        "landed": applied.get("outcome") == "ok",
                        "seq": applied.get("seq"),
                        "outcome": applied.get("outcome"),
                        "verb": applied.get("verb"),
                        "memory": memory,
                        "relation": relation,
                    }
                except GatewayError as exc:
                    landed = {"landed": False, "error": str(exc)}

            audit_records = self.gateway.audit.read_all()[before:]
            audit = audit_records[-1] if audit_records else {}

            return {
                "notice": "对话已落地：记忆与关系已写入事件日志，可回放；不产生任何钱变动。",
                "npc": {
                    "id": npc_id,
                    "name": name,
                    "role": role,
                    "location_name": self.locations.get(loc, {}).get("name", loc),
                },
                "message": message,
                "reply": reply,
                "proposal": proposal,
                "landed": landed,
                "relation": relation,
                "memory": memory,
                "audit": audit,
                "audit_records": audit_records,
                "mock_reasoner": {
                    "kind": "MockReasoner（人设卡台词 + 确定性规则，非真实模型）",
                    "seed": self.summary.get("seed"),
                    "virtual_tick": tick,
                    "reasoning_ms": reasoning_ms,
                    "quote": talk["quote"],
                    "style": talk["style"],
                },
            }
