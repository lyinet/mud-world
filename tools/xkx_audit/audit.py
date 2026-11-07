"""xkx 复刻版确定性审计。

只做观测与判定，不修改世界代码。所有结论以本进程亲手跑出的证据为准。

七项：
1. 同 seed 两次逐字节一致（state/event 终态哈希、events.jsonl sha256）
2. 开关无关（模型配音开/关、旁路文件有/无，世界哈希不变）
3. 回放等价（事件全量重放后的 state hash == 在线终态）
4. 无隐藏随机（静态扫描 src/** 的 random/time/uuid/os.urandom）
5. 账本无模型文本（模型台词只在旁路；事件库必须有模板句）
6. 旧世界 golden 基线未被静默改动（git 历史逐版本比对）
7. 战斗红线（引擎/内容未落地时标 NOT_READY；落地后校验 rounds[] 自洽）

机器可读输出：reports/xkx-audit/audit.json；人读摘要：reports/xkx-audit/AUDIT.md。
任一条 redline FAIL → 退出码 1，可供 CI 直接红。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
REPORT_DIR = ROOT / "reports" / "xkx-audit"

for _p in (str(ROOT / "src"), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PASS = "PASS"
FAIL = "FAIL"
NOT_READY = "NOT_READY"
SKIP = "SKIP"

MODEL_MARKER = "XKXAUDITMODELMARKER"
MODEL_TEXT = f"{MODEL_MARKER} 模型配音今日尚可，街口风大，早些回。"

GOLDEN_FILE_REL = "tests/test_p3e_golden.py"
GOLDEN_FILE = ROOT / GOLDEN_FILE_REL


# --------------------------------------------------------------------------- 基础工具


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


@dataclass
class CheckResult:
    id: str
    title: str
    status: str
    summary: str
    severity: str = "redline"
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "severity": self.severity,
            "summary": self.summary,
            "evidence": self.evidence,
        }


def _fail_from_exception(check_id: str, title: str, exc: BaseException) -> CheckResult:
    return CheckResult(
        id=check_id,
        title=title,
        status=FAIL,
        summary=f"审计自身执行失败：{type(exc).__name__}: {exc}",
        evidence={"traceback": traceback.format_exc(limit=8)},
    )


# --------------------------------------------------------------------------- 无头世界


def _headless_run(
    out_root: Path,
    run_id: str,
    *,
    days: int,
    seed: int,
    npc_limit: int,
    enable_llm: bool = False,
) -> dict[str, Any]:
    from wsim.sim.headless import RunConfig, run_headless

    cfg = RunConfig(
        days=days,
        seed=seed,
        npc_limit=npc_limit,
        out_root=out_root,
        run_id=run_id,
        with_players=1,
        dump_state=True,
        enable_llm=enable_llm,
        llm_budget=(
            {
                "daily_tokens": 2_000_000,
                "monthly_tokens": 20_000_000,
                "daily_cost": 100.0,
                "monthly_cost": 1000.0,
            }
            if enable_llm
            else None
        ),
    )
    return run_headless(ROOT, cfg)


@contextmanager
def _fake_llm_env(dialogue: bool):
    """与 tests/test_p3e_golden.py 相同的 fake LLM 环境。"""
    from fake_llm_server import FakeLLMServer

    keys = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "WSIM_DIALOGUE")
    saved = {k: os.environ.get(k) for k in keys}
    srv = FakeLLMServer(port=0, seed=7)
    try:
        srv.start()
        os.environ["LLM_BASE_URL"] = srv.base_url
        os.environ["LLM_API_KEY"] = "fake-ci-key"
        os.environ["LLM_MODEL"] = "fake/model"
        if dialogue:
            os.environ["WSIM_DIALOGUE"] = "1"
        else:
            os.environ.pop("WSIM_DIALOGUE", None)
        yield
    finally:
        srv.stop()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _replay_state(run_dir: Path):
    from wsim.core.content import load_content
    from wsim.core.models import Effect
    from wsim.world.effects import apply_effects
    from wsim.world.state import WorldState
    from wsim.world.store import EventStore

    content = load_content(ROOT, min_npcs=0, allow_fixture=False, use_content=True)
    state = WorldState(locations=content.locations)
    store = EventStore(run_dir / "events.sqlite")
    try:
        events = store.read_all()
    finally:
        store.close()
    for event in events:
        if event.outcome == "ok":
            apply_effects(state, [Effect.from_dict(d) for d in event.effects])
        state.tick = event.tick
    return state, events, content.content_hash


def _event_hash(events) -> str:
    from wsim.world.hash import event_hash

    return event_hash(events)


# --------------------------------------------------------------------------- 1/3 同 seed + 回放


def _check_same_seed(run_a_dir: Path, run_b_dir: Path, summary_a: dict, summary_b: dict) -> CheckResult:
    files = {
        "events_jsonl": "events.jsonl",
        "events_sqlite": "events.sqlite",
        "world_hash": "world_hash.txt",
        "event_hash": "event_hash.txt",
        "daily_jsonl": "daily.jsonl",
    }
    hashes = {}
    for name, rel in files.items():
        a, b = run_a_dir / rel, run_b_dir / rel
        hashes[name] = {
            "a": sha256_file(a) if a.is_file() else "",
            "b": sha256_file(b) if b.is_file() else "",
            "same": a.is_file() and b.is_file() and sha256_file(a) == sha256_file(b),
        }
    compared = {
        "content_hash": (summary_a.get("content_hash"), summary_b.get("content_hash")),
        "events_total": (summary_a["events"]["total"], summary_b["events"]["total"]),
        "state_final": (summary_a["hashes"]["state_final"], summary_b["hashes"]["state_final"]),
        "event_final": (summary_a["hashes"]["event_final"], summary_b["hashes"]["event_final"]),
    }
    drift = {k: {"a": v[0], "b": v[1]} for k, v in compared.items() if v[0] != v[1]}
    # 逐字节一致以 state/event 哈希 + events.jsonl 为准；sqlite 仅作参考证据。
    byte_ok = hashes["events_jsonl"]["same"] and hashes["world_hash"]["same"] and hashes["event_hash"]["same"]
    ok = not drift and byte_ok
    return CheckResult(
        id="same_seed_twice",
        title="同 seed 两次逐字节一致",
        status=PASS if ok else FAIL,
        summary=(
            "两次 run 的 state/event 终态哈希与 events.jsonl sha256 完全一致"
            if ok
            else f"发现漂移：{sorted(drift) or ['events.jsonl/world_hash/event_hash 字节不一致']}"
        ),
        evidence={
            "runs": [str(run_a_dir), str(run_b_dir)],
            "compared": {k: {"a": v[0], "b": v[1], "same": v[0] == v[1]} for k, v in compared.items()},
            "file_sha256": hashes,
            "drift": drift,
            "events_total": summary_a["events"]["total"],
        },
    )


def _check_replay(
    run_dir: Path,
    summary: dict,
    state: Any | None = None,
    events: list[Any] | None = None,
    content_hash: str = "",
) -> CheckResult:
    if state is None or events is None:
        state, events, content_hash = _replay_state(run_dir)
    replayed = state.hash()
    stored = (run_dir / "world_hash.txt").read_text(encoding="utf-8").strip()
    replayed_event = _event_hash(events)
    stored_event = (run_dir / "event_hash.txt").read_text(encoding="utf-8").strip()
    ok = replayed == stored == summary["hashes"]["state_final"] and replayed_event == stored_event
    mismatch = {}
    if replayed != stored:
        mismatch["state_vs_world_hash"] = {"replayed": replayed, "stored": stored}
    if replayed != summary["hashes"]["state_final"]:
        mismatch["state_vs_summary"] = {"replayed": replayed, "summary": summary["hashes"]["state_final"]}
    if replayed_event != stored_event:
        mismatch["event_vs_event_hash"] = {"replayed": replayed_event, "stored": stored_event}
    return CheckResult(
        id="replay_equivalence",
        title="事件全量重放等价",
        status=PASS if ok else FAIL,
        summary=(
            f"{len(events)} 条事件全量重放后 state hash 与在线终态一致"
            if ok
            else "重放终态与在线终态不一致"
        ),
        evidence={
            "events_replayed": len(events),
            "state_replayed": replayed,
            "state_stored": stored,
            "event_hash_replayed": replayed_event,
            "event_hash_stored": stored_event,
            "content_hash_run": content_hash,
            "mismatch": mismatch,
        },
    )


# --------------------------------------------------------------------------- 2/5 开关 + 账本（API 会话）


def _template_reply(session, event_seq: int) -> str:
    for event in session.store.read_all():
        if int(event.seq) != int(event_seq):
            continue
        for eff in event.effects or []:
            data = eff.get("data") or {}
            if eff.get("op") == "memory_add" and data.get("source") == "talk:npc":
                return str(data.get("text") or "").removeprefix("我答：")
    return ""


def _run_session(run_dir: Path, run_id: str, *, seed: int, model_on: bool, act: bool) -> dict[str, Any]:
    from wsim.agent import dialogue_runtime
    from wsim.agent.dialogue_async import AsyncDialogueQueue
    from wsim.api.session import WorldSession
    from wsim.world.hash import event_hash, state_hash

    class _Client:
        def __init__(self) -> None:
            self.config = SimpleNamespace(model="xkx-audit/fake-dialogue")
            self.last_usage: dict[str, Any] = {}
            self.calls = 0

        def complete(self, system: str, user: str) -> str:
            self.calls += 1
            return MODEL_TEXT

    client = _Client()
    queue = AsyncDialogueQueue(client, log_dir=run_dir, defer_calls=True)
    original_build = dialogue_runtime.build_queue
    saved_env = os.environ.get("XKX_DIALOGUE_MODEL")
    session = None
    try:
        if model_on:
            os.environ["XKX_DIALOGUE_MODEL"] = "1"
            dialogue_runtime.build_queue = lambda root, d: queue  # noqa: ARG005
        else:
            os.environ.pop("XKX_DIALOGUE_MODEL", None)
        session = WorldSession(ROOT, run_id=run_id, seed=seed, run_dir=run_dir)
        result: dict[str, Any] = {"model_on": model_on, "acted": act, "dialogue_calls": client.calls}
        if act:
            enter = session.action(
                "audit_user",
                "enter",
                {"account_id": "audit_user", "display_name": "审计员", "archetype": "游侠"},
            )
            if not enter.get("ok"):
                result["fixture_error"] = f"enter 失败：{enter}"
                return result
            pid = "player:audit_user"
            ent = session.state.entities.get(pid)
            location = ent.location if ent is not None else session.spawn
            npc = next(
                (
                    e.id
                    for e in sorted(session.state.entities.values(), key=lambda x: x.id)
                    if not e.id.startswith("player:")
                    and e.status == "alive"
                    and e.location == location
                ),
                None,
            )
            if npc is None:
                result["fixture_error"] = f"{location} 没有可对话 NPC"
                return result
            args = {"target": npc, "message": "审计问候：今日可好？"}
            reply = session.action("audit_user", "talk", args)
            if not reply.get("ok"):
                result["fixture_error"] = f"talk 失败：{reply}"
                return result
            seq = int(reply.get("event_seq") or 0)
            if model_on:
                session.finalize_dialogue(reply, args)
                deadline = time.time() + 5.0
                while time.time() < deadline and not session.dialogue_aside.get(seq, npc):
                    time.sleep(0.02)
            template = str(_template_reply(session, seq) or "")
            event_blob = json.dumps([e.effects for e in session.store.read_all()], ensure_ascii=False)
            state_blob = json.dumps(session.state.to_dict(), ensure_ascii=False, sort_keys=True)
            aside = str(session.dialogue_aside.get(seq, npc) or "")
            result.update(
                {
                    "npc": npc,
                    "seq": seq,
                    "reply": str((reply.get("details") or {}).get("reply") or ""),
                    "dialogue_source": str((reply.get("details") or {}).get("dialogue_source") or ""),
                    "template": template,
                    "model_aside": aside,
                    "model_in_events": MODEL_MARKER in event_blob,
                    "model_in_state": MODEL_MARKER in state_blob,
                    "model_in_aside": MODEL_MARKER in aside,
                    "aside_exists": session.dialogue_aside.path.is_file(),
                    "aside_preview": aside[:120],
                    "aside_path": str(session.dialogue_aside.path),
                    "dialogue_calls": client.calls,
                }
            )
        result["state_hash"] = state_hash(session.state)
        result["event_hash"] = event_hash(session.store.read_all())
        result["events_total"] = session.store.count()
        return result
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - 审计清理失败不掩盖主结论
                pass
        dialogue_runtime.build_queue = original_build
        if saved_env is None:
            os.environ.pop("XKX_DIALOGUE_MODEL", None)
        else:
            os.environ["XKX_DIALOGUE_MODEL"] = saved_env
        try:
            queue.close()
        except Exception:  # noqa: BLE001
            pass


def _dialogue_fixture(runs_root: Path, seed: int) -> dict[str, Any]:
    on_dir = runs_root / "session-model-on"
    off_dir = runs_root / "session-model-off"
    on = _run_session(on_dir, "session-model-on", seed=seed, model_on=True, act=True)
    off = _run_session(off_dir, "session-model-off", seed=seed, model_on=False, act=True)
    reopened = None
    if on.get("aside_exists"):
        aside = Path(on["aside_path"])
        aside.unlink(missing_ok=True)
        reopened = _run_session(on_dir, "session-model-on", seed=seed, model_on=False, act=False)
    return {"on": on, "off": off, "reopened": reopened}


def _check_switch(
    fixture: dict[str, Any],
    headless_on: dict[str, Any] | None = None,
    headless_off: dict[str, Any] | None = None,
) -> CheckResult:
    on = fixture["on"]
    off = fixture["off"]
    reopened = fixture.get("reopened")
    if on.get("fixture_error") or off.get("fixture_error"):
        return CheckResult(
            id="switch_independence",
            title="开关无关（模型配音 / 旁路文件）",
            status=FAIL,
            summary="会话审计夹具失败，未能验证开关无关",
            evidence={"model_on": on, "model_off": off},
        )
    same = (
        on.get("state_hash") == off.get("state_hash")
        and on.get("event_hash") == off.get("event_hash")
        and on.get("events_total") == off.get("events_total")
    )
    headless_same = True
    headless_evidence: dict[str, Any] = {"ran": False}
    if headless_on is not None and headless_off is not None:
        headless_same = all(
            headless_on["hashes"][key] == headless_off["hashes"][key]
            for key in ("state_final", "event_final")
        ) and headless_on["events"]["total"] == headless_off["events"]["total"]
        headless_evidence = {
            "ran": True,
            "dialogue_on": {
                "state_final": headless_on["hashes"]["state_final"],
                "event_final": headless_on["hashes"]["event_final"],
                "events_total": headless_on["events"]["total"],
            },
            "dialogue_off": {
                "state_final": headless_off["hashes"]["state_final"],
                "event_final": headless_off["hashes"]["event_final"],
                "events_total": headless_off["events"]["total"],
            },
            "hashes_equal": headless_same,
        }
    aside_ok = bool(on.get("aside_exists"))
    reopen_ok = bool(reopened and reopened.get("state_hash") == on.get("state_hash")
                     and reopened.get("event_hash") == on.get("event_hash"))
    ok = same and aside_ok and reopen_ok and headless_same
    return CheckResult(
        id="switch_independence",
        title="开关无关（模型配音 / 旁路文件）",
        status=PASS if ok else FAIL,
        summary=(
            "配音开/关、旁路文件有/无 两种路径世界哈希一致"
            if ok
            else "世界哈希或旁路隔离不成立"
        ),
        evidence={
            "headless_dialogue_switch": headless_evidence,
            "model_on": {
                "state_hash": on.get("state_hash"),
                "event_hash": on.get("event_hash"),
                "events_total": on.get("events_total"),
                "dialogue_calls": on.get("dialogue_calls"),
                "aside_exists": on.get("aside_exists"),
            },
            "model_off": {
                "state_hash": off.get("state_hash"),
                "event_hash": off.get("event_hash"),
                "events_total": off.get("events_total"),
            },
            "aside_deleted_reopen": None
            if reopened is None
            else {
                "state_hash": reopened.get("state_hash"),
                "event_hash": reopened.get("event_hash"),
                "events_total": reopened.get("events_total"),
            },
            "hashes_switch_equal": same,
            "aside_deleted_hash_equal": reopen_ok,
        },
    )


def _check_ledger(fixture: dict[str, Any]) -> CheckResult:
    on = fixture["on"]
    if on.get("fixture_error"):
        return CheckResult(
            id="ledger_no_model_text",
            title="账本无模型文本",
            status=FAIL,
            summary="会话审计夹具失败，未能验证事件库隔离",
            evidence={"model_on": on},
        )
    if not on.get("aside_exists"):
        return CheckResult(
            id="ledger_no_model_text",
            title="账本无模型文本",
            status=NOT_READY,
            summary="模型旁路没有产出台词，无法证明隔离（夹具未触发模型调用）",
            evidence={"model_on": on},
        )
    ok = (
        not on.get("model_in_events")
        and not on.get("model_in_state")
        and bool(on.get("template"))
        and MODEL_MARKER not in (on.get("template") or "")
        and bool(on.get("model_in_aside"))
    )
    return CheckResult(
        id="ledger_no_model_text",
        title="账本无模型文本",
        status=PASS if ok else FAIL,
        summary=(
            "模型台词只在旁路；事件库与状态均只含确定性模板句"
            if ok
            else "模型台词越界进入事件库/状态，或模板句/旁路标记缺失"
        ),
        evidence={
            "event_seq": on.get("seq"),
            "npc": on.get("npc"),
            "template_present": bool(on.get("template")),
            "template_preview": (on.get("template") or "")[:80],
            "model_in_events": on.get("model_in_events"),
            "model_in_state": on.get("model_in_state"),
            "model_in_aside": on.get("model_in_aside"),
            "aside_preview": on.get("aside_preview"),
            "reply_is_template": MODEL_MARKER not in str(on.get("reply") or ""),
        },
    )


# --------------------------------------------------------------------------- 4 隐藏随机

_RANDOM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (r"(?m)^\s*import\s+random\b|\bfrom\s+random\s+import\b|"
     r"\brandom\.(?:random|randint|choice|choices|shuffle|uniform|sample|seed)\b", "random"),
    (r"\btime\.time\s*\(", "time.time"),
    (r"\btime\.monotonic\s*\(", "time.monotonic"),
    (r"\btime\.perf_counter\s*\(", "time.perf_counter"),
    (r"\bdatetime\.(?:now|today|utcnow)\s*\(", "datetime"),
    (r"\bdatetime\.datetime\.(?:now|today|utcnow)\s*\(", "datetime"),
    (r"\bdate\.today\s*\(", "datetime"),
    (r"(?m)^\s*import\s+uuid\b|\bfrom\s+uuid\s+import\b|\buuid\.(?:uuid1|uuid4)\b", "uuid"),
    (r"\bos\.urandom\s*\(", "os.urandom"),
    (r"(?m)^\s*import\s+secrets\b|\bfrom\s+secrets\s+import\b|"
     r"\bsecrets\.(?:token_bytes|token_hex|token_urlsafe|randbelow|choice|randbits)\b", "secrets"),
]
_WALLCLOCK_ANNOTATION = "# wallclock: audit-only"
_CRITICAL_DIRS = {"world", "actions", "sim"}
_WORLD_DIRS = {"world", "actions"}


def _classify(rel: Path, label: str) -> str:
    """rel 是相对 src 根的路径（如 wsim/world/state.py）。

    random 在任何引擎路径都算红线（既有 lint 口径）；
    uuid/os.urandom/墙钟只在投影/效果/调度路径算红线，日志 ID、管理台加密等记为 review。
    """
    parts = rel.parts
    sub = parts[1] if len(parts) >= 2 and parts[0] == "wsim" else ""
    if label == "random":
        return "critical" if sub else "review"
    if label in {"uuid", "os.urandom", "secrets"}:
        return "critical" if sub in _CRITICAL_DIRS else "review"
    if label == "time.perf_counter":
        # 耗时指标专用；只有落到 world/actions 才可能与结算相关。
        return "critical" if sub in _WORLD_DIRS else "review"
    # time.time / monotonic / datetime：投影、效果、调度路径视为红线。
    return "critical" if sub in _CRITICAL_DIRS else "review"


def scan_hidden_random(src_root: Path = SRC) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for path in sorted(src_root.rglob("*.py")):
        rel_src = path.relative_to(src_root)
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else rel_src
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for pattern, label in _RANDOM_PATTERNS:
            for match in re.finditer(pattern, text):
                line_no = text[: match.start()].count("\n") + 1
                line = lines[line_no - 1] if line_no <= len(lines) else ""
                if _WALLCLOCK_ANNOTATION in line:
                    continue
                if path.name == "clock.py" and path.parent.name == "admin":
                    # 既有策略：admin 墙钟唯一包装函数。
                    continue
                severity = _classify(rel_src, label)
                findings.append(
                    {
                        "file": str(rel),
                        "line": line_no,
                        "pattern": label,
                        "severity": severity,
                        "snippet": line.strip()[:160],
                    }
                )
    critical = [f for f in findings if f["severity"] == "critical"]
    return {"findings": findings, "critical": critical}


def _check_hidden_random() -> CheckResult:
    scan = scan_hidden_random()
    critical = scan["critical"]
    ok = not critical
    return CheckResult(
        id="no_hidden_random",
        title="无隐藏随机",
        status=PASS if ok else FAIL,
        summary=(
            f"src/** 投影/效果路径未发现 random/time/uuid/os.urandom（另有 {len(scan['findings'])} 条非关键墙钟仅记录）"
            if ok
            else f"投影/效果路径发现 {len(critical)} 处非确定性调用"
        ),
        evidence={
            "critical_count": len(critical),
            "critical": critical,
            "all_findings": scan["findings"],
        },
    )


# --------------------------------------------------------------------------- 6 golden 基线


def _parse_baselines(text: str) -> dict[str, dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not target.id.startswith(("GOLDEN", "DIALOGUE_GOLDEN")):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            out[target.id] = value
    return out


_REASON_MARKERS = ("基线", "baseline", "漂移", "golden", "迁移", "原因", "重新采样", "显式")


def _golden_got(summary: dict[str, Any]) -> dict[str, Any]:
    cf = summary["economy"]["cashflow"]
    return {
        "events_total": summary["events"]["total"],
        "state_final": summary["hashes"]["state_final"],
        "event_final": summary["hashes"]["event_final"],
        "sell_in": cf["inflow_by_reason"].get("sell", 0),
        "buy_out": cf["outflow_by_reason"].get("buy", 0),
        "burned": summary["economy"]["burned_total"],
        "business_cash": summary["economy"]["business_cash_now"],
        "precondition_blocked": summary["precondition_blocked"]["total"],
        "precondition_pruned": summary["precondition_pruned"]["total"],
    }


def _check_golden_baseline(
    fresh_summary: dict[str, Any] | None = None,
    fresh_dialogue_summary: dict[str, Any] | None = None,
) -> CheckResult:
    if not GOLDEN_FILE.is_file():
        return CheckResult(
            id="golden_baseline_untouched",
            title="旧世界 golden 基线未被静默改动",
            status=NOT_READY,
            summary="找不到 tests/test_p3e_golden.py",
        )
    worktree_text = _read_text(GOLDEN_FILE)
    head = _git("show", f"HEAD:{GOLDEN_FILE_REL}")
    if head.returncode != 0:
        return CheckResult(
            id="golden_baseline_untouched",
            title="旧世界 golden 基线未被静默改动",
            status=NOT_READY,
            summary="git 历史不可用，无法追溯基线改动",
            evidence={"git_error": head.stderr.strip()[:400]},
        )
    head_text = head.stdout
    worktree_baselines = _parse_baselines(worktree_text)
    head_baselines = _parse_baselines(head_text)
    uncommitted = {
        name: {"head": head_baselines.get(name), "worktree": worktree_baselines.get(name)}
        for name in sorted(set(head_baselines) | set(worktree_baselines))
        if head_baselines.get(name) != worktree_baselines.get(name)
    }

    log = _git("log", "--follow", "--reverse", "--format=%H", "--", GOLDEN_FILE_REL)
    if log.returncode != 0:
        return CheckResult(
            id="golden_baseline_untouched",
            title="旧世界 golden 基线未被静默改动",
            status=NOT_READY,
            summary="git log 读取失败",
            evidence={"git_error": log.stderr.strip()[:400]},
        )
    commits = [c for c in log.stdout.splitlines() if c.strip()]
    prev: dict[str, dict[str, Any]] | None = None
    in_place_changes: list[dict[str, Any]] = []
    additions: list[dict[str, Any]] = []
    traceability_failures: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []

    for sha in commits:
        show = _git("show", f"{sha}:{GOLDEN_FILE_REL}")
        if show.returncode != 0:
            continue
        cur = _parse_baselines(show.stdout)
        if not cur:
            continue
        msg = _git("show", "-s", "--format=%B", sha).stdout.strip()
        subject = _git("show", "-s", "--format=%s", sha).stdout.strip()
        has_reason = any(marker in msg for marker in _REASON_MARKERS)
        if prev is not None:
            for name in sorted(set(prev) & set(cur)):
                if prev[name] != cur[name]:
                    changed_keys = sorted(
                        k for k in set(prev[name]) | set(cur[name])
                        if prev[name].get(k) != cur[name].get(k)
                    )
                    row = {
                        "baseline": name,
                        "commit": sha,
                        "subject": subject,
                        "changed_keys": changed_keys,
                        "old": {k: prev[name].get(k) for k in changed_keys},
                        "new": {k: cur[name].get(k) for k in changed_keys},
                        "has_reason": has_reason,
                    }
                    in_place_changes.append(row)
                    if not has_reason:
                        traceability_failures.append(row)
        new_names = sorted(set(cur) - set(prev or {}))
        if new_names:
            row = {"baseline": new_names, "commit": sha, "subject": subject, "has_reason": has_reason}
            additions.append(row)
            if not has_reason:
                traceability_failures.append(row)
        history.append({"commit": sha, "subject": subject, "baselines": sorted(cur)})
        prev = cur

    fresh_repro: dict[str, Any] = {"ran": False}
    fresh_ok = True
    if fresh_summary is not None:
        baseline = worktree_baselines.get("GOLDEN_MILESTONE_V6", {})
        got = _golden_got(fresh_summary)
        drift = {
            key: {"baseline": baseline.get(key), "actual": got.get(key)}
            for key in baseline
            if got.get(key) != baseline.get(key)
        }
        fresh_ok = not drift
        fresh_repro = {
            "ran": True,
            "baseline": "GOLDEN_MILESTONE_V6",
            "actual": got,
            "drift": drift,
            "matched": fresh_ok,
        }
    dialogue_repro: dict[str, Any] = {"ran": False}
    dialogue_ok = True
    if fresh_dialogue_summary is not None:
        baseline = worktree_baselines.get("DIALOGUE_GOLDEN_MILESTONE_V6", {})
        stats = fresh_dialogue_summary["llm"].get("dialogue_async") or {}
        got = {
            "events_total": fresh_dialogue_summary["events"]["total"],
            "state_final": fresh_dialogue_summary["hashes"]["state_final"],
            "event_final": fresh_dialogue_summary["hashes"]["event_final"],
            "dialogue_calls": int(stats.get("calls", 0)),
        }
        drift = {
            key: {"baseline": baseline.get(key), "actual": got.get(key)}
            for key in baseline
            if got.get(key) != baseline.get(key)
        }
        dialogue_ok = not drift
        dialogue_repro = {
            "ran": True,
            "baseline": "DIALOGUE_GOLDEN_MILESTONE_V6",
            "actual": got,
            "drift": drift,
            "matched": dialogue_ok,
        }

    ok = not uncommitted and not traceability_failures and fresh_ok and dialogue_ok
    return CheckResult(
        id="golden_baseline_untouched",
        title="旧世界 golden 基线未被静默改动",
        status=PASS if ok else FAIL,
        summary=(
            f"历史 {len(commits)} 个版本逐版比对，且新鲜 run 复现 GOLDEN_MILESTONE_V6"
            f"（当前 {len(worktree_baselines)} 组基线）"
            if ok
            else (
                f"不可追溯/未复现：未提交 {len(uncommitted)} 组、"
                f"理由缺失 {len(traceability_failures)} 条、"
                f"新鲜 run 漂移 {'无' if fresh_ok else '有'}、"
                f"dialogue 基线漂移 {'无' if dialogue_ok else '有'}"
            )
        ),
        evidence={
            "current_baselines": sorted(worktree_baselines),
            "baselines_count": len(worktree_baselines),
            "uncommitted_changes": uncommitted,
            "in_place_value_changes": in_place_changes,
            "baseline_additions": additions,
            "traceability_failures": traceability_failures,
            "fresh_reproduction": fresh_repro,
            "dialogue_reproduction": dialogue_repro,
            "history_commits": len(commits),
            "history": history[-8:],
        },
    )


# --------------------------------------------------------------------------- 7 战斗


def _find_fight_events(events: list[Any]) -> list[Any]:
    verbs = {"fight", "combat", "attack", "kill"}
    return [e for e in events if str(getattr(e, "verb", "")) in verbs]


def _run_combat_session(run_dir: Path, seed: int) -> dict[str, Any]:
    """用真实 API 会话打一场确定性的 fight。

    默认世界 NPC 的 max_hp=0，无法直接开战；审计在内存里给同房 NPC 与玩家
    设置同一组固定战斗属性（不写入 content），再走真实 action/writer/PRNG。
    """
    import copy

    from wsim.api.session import WorldSession
    from wsim.core.models import Effect
    from wsim.world.effects import apply_effects
    from wsim.world.hash import event_hash, state_hash

    session = None
    try:
        session = WorldSession(ROOT, run_id="audit-combat", seed=seed, run_dir=run_dir)
        enter = session.action(
            "audit_fighter",
            "enter",
            {"account_id": "audit_fighter", "display_name": "审计武人", "archetype": "游侠"},
        )
        if not enter.get("ok"):
            return {"ok": False, "error": f"enter 失败：{enter}"}
        pid = "player:audit_fighter"
        attacker = session.state.entities.get(pid)
        if attacker is None:
            return {"ok": False, "error": "玩家实体未建立"}
        defender = next(
            (
                e
                for e in sorted(session.state.entities.values(), key=lambda x: x.id)
                if not e.id.startswith("player:")
                and e.location == attacker.location
                and e.status == "alive"
            ),
            None,
        )
        if defender is None:
            return {"ok": False, "error": f"{attacker.location} 没有可战 NPC"}
        # 固定夹具属性：双方都是整数；不依赖内容包，也不写回 content。
        attacker.hp, attacker.max_hp = 120, 120
        attacker.attrs = {"str": 30, "dex": 25, "con": 20, "sta": 20, "int": 15}
        attacker.combat_exp = 120
        attacker.skill_levels = {"unarmed": 60, "dodge": 40}
        defender.hp, defender.max_hp = 70, 70
        defender.attrs = {"str": 20, "dex": 18, "con": 15, "sta": 15, "int": 10}
        defender.combat_exp = 40
        defender.skill_levels = {"unarmed": 30, "dodge": 20}
        loot = {"wild_fruit": 2}
        pre_state = copy.deepcopy(session.state)
        pre = {
            "hp": {pid: attacker.hp, defender.id: defender.hp},
            "status": {pid: attacker.status, defender.id: defender.status},
        }
        result = session.action(
            "audit_fighter",
            "fight",
            {"target": defender.id, "loot": loot, "max_rounds": 20},
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "error": f"fight 失败：{result}",
                "attacker": pid,
                "defender": defender.id,
            }
        seq = int(result.get("event_seq") or 0)
        fight_event = next((e for e in session.store.read_all() if int(e.seq) == seq), None)
        if fight_event is None:
            return {"ok": False, "error": f"找不到 seq={seq} 的 fight 事件"}
        log = next(
            (
                (eff.get("data") or {})
                for eff in fight_event.effects or []
                if eff.get("op") == "combat_log"
            ),
            {},
        )
        post = {
            "hp": {
                pid: int(session.state.entities[pid].hp),
                defender.id: int(session.state.entities[defender.id].hp),
            },
            "status": {
                pid: session.state.entities[pid].status,
                defender.id: session.state.entities[defender.id].status,
            },
        }
        # 事件效果应用到 fight 前状态，必须与在线终态一致（账本自洽）。
        replay_state = copy.deepcopy(pre_state)
        apply_effects(replay_state, [Effect.from_dict(d) for d in fight_event.effects])
        replay_consistent = all(
            replay_state.entities[eid].hp == post["hp"][eid]
            and replay_state.entities[eid].status == post["status"][eid]
            for eid in (pid, defender.id)
        )
        return {
            "ok": True,
            "attacker": pid,
            "defender": defender.id,
            "loot": loot,
            "pre": pre,
            "post": post,
            "log": log,
            "effects": fight_event.effects,
            "event_seq": seq,
            "events_total": session.store.count(),
            "state_hash_pre": state_hash(pre_state),
            "state_hash_post": state_hash(session.state),
            "event_hash": event_hash(session.store.read_all()),
            "replay_consistent": replay_consistent,
        }
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass


def _combat_fixture(runs_root: Path, seed: int) -> dict[str, Any]:
    a = _run_combat_session(runs_root / "combat-a", seed)
    b = _run_combat_session(runs_root / "combat-b", seed)
    if not a.get("ok") or not b.get("ok"):
        return {"a": a, "b": b, "same_seed": False}
    same_seed = (
        a["state_hash_post"] == b["state_hash_post"]
        and a["event_hash"] == b["event_hash"]
        and json.dumps(a["effects"], ensure_ascii=False, sort_keys=True)
        == json.dumps(b["effects"], ensure_ascii=False, sort_keys=True)
    )
    return {"a": a, "b": b, "same_seed": same_seed}


def _check_combat(
    events: list[Any] | None,
    runs_skipped: bool,
    fixture: dict[str, Any] | None,
) -> CheckResult:
    actions_text = {
        p.name: _read_text(p) for p in sorted((SRC / "wsim" / "actions").glob("*.py"))
    }
    impl = sorted(
        name for name, text in actions_text.items()
        if re.search(r'(?:verb|VERB)\s*=\s*"fight"|\bfight\b', text)
    )
    xkx_packs = sorted(
        str(p.parent.name)
        for p in (ROOT / "content" / "packs").glob("*/manifest.json")
        if "xkx" in str(p).lower()
    )
    fight_events = _find_fight_events(events or [])
    if not impl:
        return CheckResult(
            id="combat_redline",
            title="战斗新红线",
            status=NOT_READY,
            summary="actions/ 下没有 fight 实现，战斗红线未启用",
            evidence={"action_files_with_combat": impl, "xkx_packs": xkx_packs},
        )
    if not fixture or not fixture.get("a", {}).get("ok") or not fixture.get("b", {}).get("ok"):
        errors = []
        for side in ("a", "b"):
            row = (fixture or {}).get(side) or {}
            if not row.get("ok"):
                errors.append(f"{side}: {row.get('error', 'fixture missing')}")
        return CheckResult(
            id="combat_redline",
            title="战斗新红线",
            status=NOT_READY,
            summary="战斗引擎存在，但审计夹具无法开战（" + "；".join(errors) + "）",
            evidence={"action_files_with_combat": impl, "xkx_packs": xkx_packs,
                      "fixture": fixture, "runs_skipped": runs_skipped},
        )

    a = fixture["a"]
    problems: list[dict[str, Any]] = []
    log = a.get("log") or {}
    rounds = log.get("rounds")
    if not isinstance(rounds, list):
        problems.append({"reason": "missing_rounds"})
        rounds = []
    else:
        if int(log.get("rounds_used", -1)) != len(rounds):
            problems.append(
                {"reason": "rounds_used_mismatch", "rounds_used": log.get("rounds_used"), "len": len(rounds)}
            )
        if not 1 <= len(rounds) <= 20:
            problems.append({"reason": "rounds_out_of_bounds", "len": len(rounds)})
        for idx, row in enumerate(rounds):
            if not isinstance(row, dict):
                problems.append({"reason": "round_not_object", "round": idx})
            elif not {"round", "actions"} <= set(row):
                problems.append({"reason": "round_missing_fields", "round": idx, "keys": sorted(row)})
            else:
                actions = row.get("actions")
                if not isinstance(actions, list) or not actions:
                    problems.append({"reason": "round_actions_empty", "round": idx})
                    continue
                for j, action in enumerate(actions):
                    if not isinstance(action, dict) or not {"actor", "target", "result"} <= set(action):
                        problems.append(
                            {"reason": "round_action_missing_fields", "round": idx, "action": j}
                        )
    hp = log.get("hp") or {}
    attacker, defender = a["attacker"], a["defender"]
    for eid in (attacker, defender):
        if int(hp.get(eid, -1)) != int(a["post"]["hp"][eid]):
            problems.append(
                {
                    "reason": "log_hp_mismatch",
                    "entity": eid,
                    "log": hp.get(eid),
                    "state": a["post"]["hp"][eid],
                }
            )
        delta = int(a["post"]["hp"][eid]) - int(a["pre"]["hp"][eid])
        lost = sum(
            int((eff.get("data") or {}).get("delta", 0))
            for eff in a["effects"]
            if eff.get("op") == "hp_add" and str((eff.get("data") or {}).get("entity")) == eid
        )
        if lost != delta:
            problems.append(
                {"reason": "hp_effect_mismatch", "entity": eid, "effect_sum": lost, "state_delta": delta}
            )
        if a["post"]["status"][eid] == "dead":
            if a["post"]["hp"][eid] != 0:
                problems.append({"reason": "dead_with_hp", "entity": eid, "hp": a["post"]["hp"][eid]})
            if not any(
                eff.get("op") == "entity_status"
                and str((eff.get("data") or {}).get("entity")) == eid
                and (eff.get("data") or {}).get("status") == "dead"
                for eff in a["effects"]
            ):
                problems.append({"reason": "dead_without_event_effect", "entity": eid})
    if a["post"]["hp"][defender] <= 0 and not defender.startswith("player:"):
        for item, qty in a["loot"].items():
            got = sum(
                int((eff.get("data") or {}).get("qty", 0))
                for eff in a["effects"]
                if eff.get("op") == "loot_create"
                and str((eff.get("data") or {}).get("item")) == item
            )
            if got != int(qty):
                problems.append({"reason": "loot_mismatch", "item": item, "expected": qty, "effect": got})
    if not a.get("replay_consistent"):
        problems.append({"reason": "event_replay_state_mismatch"})
    if not fixture.get("same_seed"):
        problems.append({"reason": "same_seed_refight_mismatch"})
    for ev in fight_events:
        for eff in ev.effects or []:
            data = eff.get("data") or {}
            if eff.get("op") == "combat_log":
                rows = data.get("rounds")
                if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
                    problems.append({"reason": "headless_rounds_invalid", "seq": ev.seq})
                if int(data.get("rounds_used", -1)) != len(rows or []):
                    problems.append({"reason": "headless_rounds_used_mismatch", "seq": ev.seq})

    summary = (
        f"1 场真实 fight（默认世界内存夹具）自洽：{len(rounds)} 回合、"
        f"事件效果/状态/log 一致、同 seed 重打一致"
        if not problems
        else f"战斗红线发现 {len(problems)} 处不自洽"
    )
    return CheckResult(
        id="combat_redline",
        title="战斗新红线",
        status=PASS if not problems else FAIL,
        summary=summary,
        evidence={
            "action_files_with_combat": impl,
            "xkx_packs": xkx_packs,
            "fixture_note": "默认 NPC max_hp=0；审计在内存给同房 NPC/玩家固定战斗属性，走真实 action/writer/PRNG",
            "rounds_used": log.get("rounds_used"),
            "rounds_len": len(rounds),
            "winner": log.get("winner"),
            "pre": a["pre"],
            "post": a["post"],
            "log_hp": hp,
            "event_seq": a.get("event_seq"),
            "events_total": a.get("events_total"),
            "state_hash_pre": a.get("state_hash_pre"),
            "state_hash_post": a.get("state_hash_post"),
            "event_hash": a.get("event_hash"),
            "same_seed_refight": fixture.get("same_seed"),
            "replay_consistent": a.get("replay_consistent"),
            "problems": problems,
            "headless_fight_events": len(fight_events),
            "runs_skipped": runs_skipped,
        },
    )


# --------------------------------------------------------------------------- 报告


def _status_counts(checks: list[CheckResult]) -> dict[str, int]:
    counts = {PASS: 0, FAIL: 0, NOT_READY: 0, SKIP: 0}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    return counts


def write_reports(result: dict[str, Any], out_json: Path) -> Path:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path = out_json.parent / "AUDIT.md"
    lines = [
        "# xkx 复刻版确定性审计",
        "",
        f"- commit: `{result['commit']}`",
        f"- 配置: days={result['config']['days']} seed={result['config']['seed']} "
        f"npc_limit={result['config']['npc_limit']} quick={result['config']['quick']}",
        f"- 汇总: 通过 {result['summary']['pass']} / 不通过 {result['summary']['fail']} / "
        f"未就绪 {result['summary']['not_ready']} / 跳过 {result['summary']['skip']}",
        f"- 退出码: {result['summary']['exit_code']}",
        "",
        "| # | 审计项 | 状态 | 结论 |",
        "|---|---|---|---|",
    ]
    for i, check in enumerate(result["checks"], 1):
        lines.append(f"| {i} | {check['title']} | **{check['status']}** | {check['summary']} |")
    lines += ["", "## 结论", ""]
    failed = [c for c in result["checks"] if c["status"] == "FAIL"]
    not_ready = [c for c in result["checks"] if c["status"] == "NOT_READY"]
    if failed:
        lines.append("**红线失败（阻断）：**")
        for check in failed:
            lines.append(f"- `{check['id']}`：{check['summary']}")
    else:
        lines.append("**红线失败：** 无。")
    lines.append("")
    if not_ready:
        lines.append("**未就绪（不计越界）：**")
        for check in not_ready:
            lines.append(f"- `{check['id']}`：{check['summary']}")
        lines.append("")
    lines += ["", "## 证据", ""]
    for check in result["checks"]:
        lines.append(f"### {check['id']} · {check['title']} — {check['status']}")
        lines.append("")
        lines.append(check["summary"])
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(check["evidence"], ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


# --------------------------------------------------------------------------- 入口


def run_audit(
    *,
    days: int = 2,
    seed: int = 42,
    npc_limit: int = 0,
    quick: bool = False,
    require_combat: bool = False,
    runs_dir: Path | None = None,
    keep_runs: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    owned_tmp: str | None = None
    if runs_dir is None:
        owned_tmp = tempfile.mkdtemp(prefix="xkx-audit-")
        runs_root = Path(owned_tmp)
    else:
        runs_root = Path(runs_dir)
        runs_root.mkdir(parents=True, exist_ok=True)
    checks: list[CheckResult] = []
    run_a_dir = runs_root / "same-a"
    run_b_dir = runs_root / "same-b"
    events_for_combat: list[Any] | None = None
    summary_a: dict[str, Any] | None = None
    summary_dialogue_on: dict[str, Any] | None = None

    try:
        if quick:
            checks.append(
                CheckResult(
                    id="same_seed_twice",
                    title="同 seed 两次逐字节一致",
                    status=SKIP,
                    summary="quick 模式跳过两次无头世界（完整模式默认运行）",
                )
            )
            checks.append(
                CheckResult(
                    id="replay_equivalence",
                    title="事件全量重放等价",
                    status=SKIP,
                    summary="quick 模式跳过无头世界回放（完整模式默认运行）",
                )
            )
        else:
            # 与 tests/test_p3e_golden.py 同配置：fake LLM + enable_llm=True，
            # 这样新鲜 run 才能与 GOLDEN_MILESTONE_V6 对账。
            with _fake_llm_env(False):
                summary_a = _headless_run(
                    runs_root, "same-a", days=days, seed=seed, npc_limit=npc_limit,
                    enable_llm=True,
                )
                summary_b = _headless_run(
                    runs_root, "same-b", days=days, seed=seed, npc_limit=npc_limit,
                    enable_llm=True,
                )
            with _fake_llm_env(True):
                summary_dialogue_on = _headless_run(
                    runs_root, "dialogue-on", days=days, seed=seed, npc_limit=npc_limit,
                    enable_llm=True,
                )
            checks.append(_check_same_seed(run_a_dir, run_b_dir, summary_a, summary_b))
            replay_state, replay_events, replay_content_hash = _replay_state(run_a_dir)
            checks.append(
                _check_replay(
                    run_a_dir, summary_a, replay_state, replay_events, replay_content_hash
                )
            )
            events_for_combat = replay_events
    except Exception as exc:  # noqa: BLE001
        for cid, title in (
            ("same_seed_twice", "同 seed 两次逐字节一致"),
            ("replay_equivalence", "事件全量重放等价"),
        ):
            checks.append(_fail_from_exception(cid, title, exc))

    try:
        fixture = _dialogue_fixture(runs_root, seed)
        checks.append(_check_switch(fixture, summary_dialogue_on, summary_a))
        checks.append(_check_ledger(fixture))
    except Exception as exc:  # noqa: BLE001
        for cid, title in (
            ("switch_independence", "开关无关（模型配音 / 旁路文件）"),
            ("ledger_no_model_text", "账本无模型文本"),
        ):
            checks.append(_fail_from_exception(cid, title, exc))

    try:
        checks.append(_check_hidden_random())
    except Exception as exc:  # noqa: BLE001
        checks.append(_fail_from_exception("no_hidden_random", "无隐藏随机", exc))

    try:
        checks.append(_check_golden_baseline(summary_a, summary_dialogue_on))
    except Exception as exc:  # noqa: BLE001
        checks.append(_fail_from_exception("golden_baseline_untouched", "旧世界 golden 基线未被静默改动", exc))

    try:
        combat_fixture = _combat_fixture(runs_root, seed)
        checks.append(_check_combat(events_for_combat, runs_skipped=quick, fixture=combat_fixture))
    except Exception as exc:  # noqa: BLE001
        checks.append(_fail_from_exception("combat_redline", "战斗新红线", exc))

    order = [
        "same_seed_twice",
        "switch_independence",
        "replay_equivalence",
        "no_hidden_random",
        "ledger_no_model_text",
        "golden_baseline_untouched",
        "combat_redline",
    ]
    checks = [next(c for c in checks if c.id == cid) for cid in order if any(c.id == cid for c in checks)]
    counts = _status_counts(checks)
    fail_ids = [c.id for c in checks if c.status == FAIL]
    combat_blocked = require_combat and next(
        (c.status for c in checks if c.id == "combat_redline"), PASS
    ) == NOT_READY
    exit_code = 1 if fail_ids or combat_blocked else 0
    commits = _git("rev-parse", "HEAD").stdout.strip()
    result = {
        "schema": "xkx-audit/1",
        "commit": commits,
        "config": {
            "days": days,
            "seed": seed,
            "npc_limit": npc_limit,
            "quick": quick,
            "require_combat": require_combat,
            "runs_dir": str(runs_root),
        },
        "checks": [c.to_dict() for c in checks],
        "summary": {
            "pass": counts[PASS],
            "fail": counts[FAIL],
            "not_ready": counts[NOT_READY],
            "skip": counts[SKIP],
            "fail_ids": fail_ids,
            "exit_code": exit_code,
            "duration_s": round(time.perf_counter() - started, 3),
        },
    }
    if owned_tmp is not None and not keep_runs:
        shutil.rmtree(owned_tmp, ignore_errors=True)
        result["config"]["runs_dir"] = "(temp removed)"
    return result
