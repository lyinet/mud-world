"""只读 run 视图：events.jsonl 回放 + MCP gateway 只读快照。

边界（本目录的硬约束）：
- 只读 run 目录：不打开 events.sqlite，不创建 / 修改 run 内任何文件；
- 世界数据经 MCPGateway 的 `world.read_snapshot`（read 权限）取得；
- `talk` 只走 `world.propose_intent`（propose_intent 权限）进入内存 outbox，
  不绑定 Writer，因此不会落地到世界状态；
- 内容包（locations/items/quests 名称）只做只读解析，不引入第三方依赖。

events.jsonl 是冻结快照，用于重建只读投影；它本身不是事实源，
事实源仍是该 run 的 events.sqlite，由主引擎与 Writer 维护。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True  # 不往 src/ 里写 __pycache__

WORLD_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = WORLD_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from wsim.core.models import Effect  # noqa: E402
from wsim.mcp.audit import AuditLog  # noqa: E402
from wsim.mcp.gateway import (  # noqa: E402
    GatewayError,
    MCPGateway,
    PERMISSION_PROPOSE,
    PERMISSION_READ,
)
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.hash import state_hash  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402

PLAYER_ID = "player:visitor"
PLAYER_NAME = "访客"
TICKS_PER_DAY = 144
CALLER_READ = "terminal:reader"
CALLER_PROPOSE = "terminal:proposer"
REQUIRED_FILES = ("summary.json", "events.jsonl")


class RunError(Exception):
    """run 目录不可用；由服务端转成一句人话。"""

    def __init__(self, message: str, hint: str = ""):
        self.message = message
        self.hint = hint or "请用 --run 指向包含 summary.json / events.jsonl 的 run 目录。"
        super().__init__(message)


# --------------------------------------------------------------------- 只读解析
def _clean(value: str) -> str:
    return value.strip().strip("'\"")


def _iter_lines(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [re.sub(r"\s+#.*$", "", line).rstrip() for line in raw.splitlines()]


def parse_locations(path: Path) -> dict[str, dict[str, Any]]:
    """极简只读 YAML 解析：只取 locations 的 id / name / exits / services。"""
    out: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for line in _iter_lines(path):
        m = re.match(r"\s*-\s*id:\s*(.+)$", line)
        if m:
            lid = _clean(m.group(1))
            current = {"id": lid, "name": lid, "exits": [], "services": []}
            out[lid] = current
            continue
        if current is None:
            continue
        m = re.match(r"\s*name:\s*(.+)$", line)
        if m:
            current["name"] = _clean(m.group(1))
            continue
        m = re.match(r"\s*exits:\s*\[(.+)\]\s*$", line)
        if m:
            current["exits"] = [_clean(x) for x in m.group(1).split(",") if x.strip()]
            continue
        m = re.match(r"\s*services:\s*\[(.+)\]\s*$", line)
        if m:
            current["services"] = [_clean(x) for x in m.group(1).split(",") if x.strip()]
    return out


def merge_locations(
    base: dict[str, dict[str, Any]], extra: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    merged = {lid: dict(row) for lid, row in base.items()}
    for lid, row in extra.items():
        merged.setdefault(lid, dict(row))
        merged[lid]["exits"] = sorted(set(merged[lid].get("exits") or []) | set(row.get("exits") or []))
    # additions 里常只写单向出口，这里补齐反向边，保证 go 的双向可走。
    for lid, row in merged.items():
        for other in row.get("exits") or []:
            if other in merged:
                merged[other]["exits"] = sorted(set(merged[other].get("exits") or []) | {lid})
        row["exits"] = sorted(set(row.get("exits") or []))
    return merged


def parse_id_name(path: Path) -> dict[str, str]:
    """解析形如 `- id: x` 后接 `name: 中文名` 的条目（物品、任务模板）。"""
    out: dict[str, str] = {}
    current = ""
    for line in _iter_lines(path):
        m = re.match(r"\s*-\s*id:\s*(.+)$", line)
        if m:
            current = _clean(m.group(1))
            continue
        if current:
            m = re.match(r"\s*name:\s*(.+)$", line)
            if m:
                out[current] = _clean(m.group(1))
                current = ""
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunError(f"缺少文件：{path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RunError(f"无法解析 {path.name}：{exc}") from exc


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
                    raise RunError(f"{path.name} 第 {lineno} 行不是合法 JSON：{exc}") from exc
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError as exc:
        raise RunError(f"缺少文件：{path.name}") from exc
    except OSError as exc:
        raise RunError(f"无法读取 {path.name}：{exc}") from exc
    return rows


def clock_label(tick: int) -> str:
    day = tick // TICKS_PER_DAY + 1
    hour = (tick % TICKS_PER_DAY) // 6
    minute = (tick % 6) * 10
    return f"第 {day} 天 {hour:02d}:{minute:02d}"


# ------------------------------------------------------------------------ 视图
class RunView:
    """一个 run 目录的只读视图 + MCP 网关。"""

    def __init__(self, run_dir: str | Path | None = None, day: int | None = None):
        self.run_label = ""
        self.error: RunError | None = None
        self.state: WorldState | None = None
        self.gateway: MCPGateway | None = None
        self.summary: dict[str, Any] = {}
        self.locations: dict[str, dict[str, Any]] = {}
        self.item_names: dict[str, str] = {}
        self.quest_names: dict[str, str] = {}
        self.tick = 0
        self.days = 0
        self.view_day = 0
        self.truncated = False
        self.events_count = 0
        self.state_hash_value = ""
        self.world_hash_expected = ""
        self.state_hash_match: bool | None = None
        try:
            self._load(run_dir, day)
        except RunError as exc:
            self.error = exc
        except Exception as exc:  # 兜底：绝不让浏览器看到栈
            self.error = RunError(f"加载 run 失败：{type(exc).__name__}: {exc}")

    # -------------------------------------------------------------- loading
    @staticmethod
    def _resolve_run(run_dir: str | Path | None) -> Path:
        if not run_dir:
            raise RunError("未指定 run 目录")
        path = Path(run_dir)
        if not path.is_absolute():
            path = (WORLD_ROOT / path).resolve()
        return path

    def _load(self, run_dir: str | Path | None, day: int | None) -> None:
        self.run_label = str(self._resolve_run(run_dir))
        if not self.run_label:
            return
        path = Path(self.run_label)
        if not path.exists():
            raise RunError(f"run 目录不存在：{path}")
        if not path.is_dir():
            raise RunError(f"--run 不是目录：{path}")
        missing = [name for name in REQUIRED_FILES if not (path / name).exists()]
        if missing:
            raise RunError(f"run 目录缺少文件：{', '.join(missing)}")

        self.summary = _read_json(path / "summary.json")
        self.days = int(self.summary.get("days") or 0)
        if self.days <= 0:
            raise RunError("summary.json 里的 days 无效")
        self.view_day = int(day or self.days)
        if self.view_day < 1 or self.view_day > self.days:
            raise RunError(f"--day 必须在 1..{self.days} 之间")

        self.locations = merge_locations(
            parse_locations(WORLD_ROOT / "content" / "locations.yaml"),
            parse_locations(WORLD_ROOT / "content" / "additions" / "locations.yaml"),
        )
        if not self.locations:
            raise RunError("没有解析到任何地点，拒绝在空地点图上回放")

        self.item_names = parse_id_name(WORLD_ROOT / "content" / "items.yaml")
        self.item_names.update(parse_id_name(WORLD_ROOT / "content" / "additions" / "items.yaml"))
        self.quest_names = parse_id_name(WORLD_ROOT / "content" / "quests.yaml")

        self._replay(path / "events.jsonl", self.view_day * TICKS_PER_DAY)

        self.world_hash_expected = ""
        hash_path = path / "world_hash.txt"
        if hash_path.exists():
            try:
                self.world_hash_expected = hash_path.read_text(encoding="utf-8").strip()
            except OSError:
                self.world_hash_expected = ""
        assert self.state is not None
        self.state_hash_value = state_hash(self.state)
        if not self.truncated and self.world_hash_expected:
            self.state_hash_match = self.world_hash_expected == self.state_hash_value
        else:
            self.state_hash_match = None

        self.gateway = MCPGateway(
            state_provider=lambda: self.state,
            writer=None,
            audit=AuditLog(None),  # 审计只留内存，不写磁盘
            tick_provider=lambda: int(self.state.tick if self.state else 0),
            identities={CALLER_READ: PERMISSION_READ, CALLER_PROPOSE: PERMISSION_PROPOSE},
        )

    def _replay(self, path: Path, limit_tick: int) -> None:
        state = WorldState(locations=self.locations)
        rows = _read_jsonl(path)
        applied = 0
        for row in rows:
            tick = int(row.get("tick") or 0)
            if tick >= limit_tick:
                break
            effects = [e for e in (row.get("effects") or []) if isinstance(e, dict)]
            try:
                apply_effects(state, [Effect.from_dict(e) for e in effects])
            except Exception as exc:
                raise RunError(
                    f"events.jsonl 第 {row.get('seq')} 条事件回放失败：{type(exc).__name__}: {exc}"
                ) from exc
            state.tick = tick
            applied += 1
        self.state = state
        self.tick = int(state.tick)
        self.events_count = applied
        self.truncated = applied < len(rows)

    # ------------------------------------------------------------- gateway
    def snapshot(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        if self.gateway is None:
            raise RunError("世界快照尚未就绪")
        result = self.gateway.call(CALLER_READ, "world.read_snapshot", {})
        return result.get("snapshot") or {}

    def propose(self, intent: dict[str, Any]) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        if self.gateway is None:
            raise RunError("世界快照尚未就绪")
        return self.gateway.call(CALLER_PROPOSE, "world.propose_intent", {"intent": intent})

    @property
    def outbox_count(self) -> int:
        return len(self.gateway.outbox) if self.gateway is not None else 0

    # -------------------------------------------------------------- helpers
    def loc_name(self, lid: str) -> str:
        return str(self.locations.get(lid, {}).get("name") or lid)

    def loc_exits(self, lid: str) -> list[str]:
        return list(self.locations.get(lid, {}).get("exits") or [])

    def resolve_location(self, token: str) -> str | None:
        token = (token or "").strip()
        if not token:
            return None
        if token in self.locations:
            return token
        for lid, row in self.locations.items():
            if str(row.get("name") or "") == token:
                return lid
        return None

    def item_name(self, iid: str) -> str:
        return self.item_names.get(iid, iid)

    def quest_name(self, template_id: str) -> str:
        return self.quest_names.get(template_id, template_id)

    def resolve_npc(self, token: str, snapshot: dict[str, Any]) -> str | None:
        token = (token or "").strip()
        entities = snapshot.get("entities") or {}
        if token in entities:
            return token
        for eid, ent in entities.items():
            if str(ent.get("name") or "") == token:
                return eid
        return None

    def npcs_at(self, snapshot: dict[str, Any], lid: str) -> list[dict[str, Any]]:
        entities = snapshot.get("entities") or {}
        rows = [
            ent
            for ent in entities.values()
            if str(ent.get("location") or "") == lid and str(ent.get("status") or "") == "alive"
        ]
        return sorted(rows, key=lambda ent: str(ent.get("name") or ""))

    def npc_name(self, snapshot: dict[str, Any], npc_id: str) -> str:
        ent = (snapshot.get("entities") or {}).get(npc_id) or {}
        return str(ent.get("name") or npc_id)

    def error_text(self) -> str:
        assert self.error is not None
        return f"{self.error.message}\n提示：{self.error.hint}"
