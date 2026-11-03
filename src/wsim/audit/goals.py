"""K1：自主目标推进率（全量内容目标口径，见 docs/metrics.md v1.0.0）。

分母是 content/npcs.yaml 中的全部目标；未创建的目标记为未推进，
并给出阻塞它的前序目标。一次性目标（一次动作即完成）单独统计，
避免它们把 K1 的数字抬高而不被看见。
"""
from __future__ import annotations

from typing import Any

from ..actions.common import goal_kind
from ..core.content import Content
from ..world.state import WorldState


def _one_shot(kind: str, progress_log: list[tuple[int, float]], progress: float) -> bool:
    """一次动作直接完成：进度日志只有一条，且终值 >= 0.999。"""
    return len(progress_log) == 1 and progress >= 0.999


def goal_stats(
    state: WorldState,
    horizon_tick: int,
    content: Content | None = None,
    run_npc_ids: list[str] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    not_created: list[dict[str, Any]] = []

    if content is None:
        # 兼容旧调用：只统计已创建目标，并明确标记为部分口径
        for gid in sorted(state.goals):
            g = state.goals[gid]
            if g.source != "npc" or g.created_tick >= horizon_tick:
                continue
            values = [float(v) for _, v in g.progress_log]
            monotonic = all(b >= a - 1e-12 for a, b in zip(values, values[1:]))
            progressed = g.progress > 0.1
            rows.append(
                {
                    "id": g.id,
                    "npc": g.npc,
                    "kind": g.kind,
                    "content_type": g.content_type,
                    "created_tick": g.created_tick,
                    "progress": g.progress,
                    "status": g.status,
                    "monotonic": monotonic,
                    "progressed": progressed,
                    "completed": g.status == "completed",
                    "created": True,
                    "one_shot": _one_shot(g.kind, g.progress_log, g.progress),
                    "blocked_by": None,
                }
            )
        total = len(rows)
        passed = sum(1 for r in rows if r["monotonic"] and r["progressed"])
        one_shot = [r for r in rows if r["one_shot"] and r["monotonic"] and r["progressed"]]
        return {
            "k1_scope": "created_only",
            "comparison_basis": {
                "basis": "same_snapshot_only",
                "content_hash": None,
                "goals_total": total,
                "goals_in_scope": total,
                "run_npcs": len({r["npc"] for r in rows}),
                "cross_version_comparable": False,
            },
            "goals_total": total,
            "goals_created": total,
            "goals_progressed": passed,
            "k1_rate": passed / total if total else 0.0,
            "k1_rate_created_only": passed / total if total else 0.0,
            "k1_scope_npcs": len({r["npc"] for r in rows}),
            "k1_goals_out_of_run": 0,
            "k1_goals_progressed_in_scope": passed,
            "k1_rate_in_scope": passed / total if total else 0.0,
            "k1_rate_in_scope_created_only": passed / total if total else 0.0,
            "k1_rate_in_scope_excluding_one_shot": (
                (passed - len(one_shot)) / max(1, total - sum(1 for r in rows if r["one_shot"]))
            ),
            "k1_rate_excluding_one_shot": (passed - len(one_shot)) / max(1, total - sum(1 for r in rows if r["one_shot"])),
            "one_shot_created": sum(1 for r in rows if r["one_shot"]),
            "one_shot_progressed": len(one_shot),
            "one_shot_share_of_progressed": len(one_shot) / passed if passed else 0.0,
            "one_shot_by_type": _group(one_shot),
            "goals_completed": sum(1 for r in rows if r["completed"]),
            "goals_monotonic_violations": sum(1 for r in rows if not r["monotonic"]),
            "npcs_with_goals": len({r["npc"] for r in rows}),
            "npcs_progressed": len({r["npc"] for r in rows if r["monotonic"] and r["progressed"]}),
            "npc_progress_rate": 0.0,
            "by_kind": _by_kind(rows),
            "by_content_type": _by_content_type(rows),
            "not_created": [],
            "rows": rows,
        }

    # ---- 全量口径 ----
    content_goals: list[dict[str, Any]] = []
    for npc in content.npcs:
        ordered = sorted(
            npc.get("goals") or [],
            key=lambda g: (float(g.get("priority", 9)), str(g.get("id", ""))),
        )
        for g in ordered:
            content_goals.append(
                {
                    "npc": npc["id"],
                    "content_goal_id": str(g["id"]),
                    "content_type": str(g.get("type", "")),
                    "kind": goal_kind(str(g.get("type", ""))),
                    "priority": float(g.get("priority", 1.0)),
                    "state_goal_id": f"{npc['id']}:{g['id']}",
                }
            )

    state_rows = {}
    for gid in sorted(state.goals):
        g = state.goals[gid]
        if g.source != "npc":
            continue
        state_rows[f"{g.npc}:{g.content_goal_id}"] = g

    # 每个 NPC 的阻塞链：按内容顺序找第一个未完成的目标
    blockers: dict[str, str | None] = {}
    for npc in content.npcs:
        ordered = sorted(
            npc.get("goals") or [],
            key=lambda g: (float(g.get("priority", 9)), str(g.get("id", ""))),
        )
        blockers[npc["id"]] = None
        for g in ordered:
            gid = f"{npc['id']}:{g['id']}"
            sg = state_rows.get(gid)
            if sg is not None and sg.status != "completed":
                blockers[npc["id"]] = gid
                break
            if sg is None:
                blockers[npc["id"]] = f"not_created:{gid}"
                break

    for cg in content_goals:
        sg = state_rows.get(cg["state_goal_id"])
        if sg is None:
            not_created.append(
                {
                    "npc": cg["npc"],
                    "content_goal_id": cg["content_goal_id"],
                    "content_type": cg["content_type"],
                    "kind": cg["kind"],
                    "blocked_by": blockers.get(cg["npc"]),
                }
            )
            rows.append(
                {
                    "id": cg["state_goal_id"],
                    "npc": cg["npc"],
                    "kind": cg["kind"],
                    "content_type": cg["content_type"],
                    "created_tick": None,
                    "progress": 0.0,
                    "status": "not_created",
                    "monotonic": True,
                    "progressed": False,
                    "completed": False,
                    "created": False,
                    "one_shot": False,
                    "blocked_by": blockers.get(cg["npc"]),
                }
            )
            continue

        if sg.created_tick >= horizon_tick:
            rows.append(
                {
                    "id": sg.id,
                    "npc": sg.npc,
                    "kind": sg.kind,
                    "content_type": sg.content_type,
                    "created_tick": sg.created_tick,
                    "progress": sg.progress,
                    "status": sg.status,
                    "monotonic": True,
                    "progressed": False,
                    "completed": sg.status == "completed",
                    "created": False,
                    "one_shot": _one_shot(sg.kind, sg.progress_log, sg.progress),
                    "blocked_by": "created_after_horizon",
                }
            )
            continue

        values = [float(v) for _, v in sg.progress_log]
        monotonic = all(b >= a - 1e-12 for a, b in zip(values, values[1:]))
        progressed = sg.progress > 0.1
        one_shot = _one_shot(sg.kind, sg.progress_log, sg.progress)
        rows.append(
            {
                "id": sg.id,
                "npc": sg.npc,
                "kind": sg.kind,
                "content_type": sg.content_type,
                "created_tick": sg.created_tick,
                "progress": sg.progress,
                "status": sg.status,
                "monotonic": monotonic,
                "progressed": progressed,
                "completed": sg.status == "completed",
                "created": True,
                "one_shot": one_shot,
                "blocked_by": None,
            }
        )

    total = len(rows)
    created = sum(1 for r in rows if r["created"])
    passed = sum(1 for r in rows if r["monotonic"] and r["progressed"])
    one_shot_total = sum(1 for r in rows if r["one_shot"])
    one_shot_passed = [r for r in rows if r["one_shot"] and r["monotonic"] and r["progressed"]]
    npcs_progressed = len({r["npc"] for r in rows if r["monotonic"] and r["progressed"]})

    scope_ids = set(run_npc_ids) if run_npc_ids is not None else None
    scope_rows = [r for r in rows if scope_ids is None or r["npc"] in scope_ids]
    scope_total = len(scope_rows)
    scope_created = sum(1 for r in scope_rows if r["created"])
    scope_passed = sum(1 for r in scope_rows if r["monotonic"] and r["progressed"])
    scope_one_shot_total = sum(1 for r in scope_rows if r["one_shot"])
    scope_one_shot_passed = [
        r for r in scope_rows if r["one_shot"] and r["monotonic"] and r["progressed"]
    ]
    scope_npcs = len({r["npc"] for r in scope_rows})

    return {
        "k1_scope": "all_content_goals",
        "comparison_basis": {
            "basis": "same_snapshot_only",
            "content_hash": content.content_hash,
            "goals_total": total,
            "goals_in_scope": scope_total,
            "run_npcs": scope_npcs,
            "cross_version_comparable": False,
        },
        "goals_total": total,
        "goals_created": created,
        "goals_progressed": passed,
        "k1_rate": passed / total if total else 0.0,
        "k1_rate_created_only": passed / created if created else 0.0,
        # ---- 范围口径（不替代官方 k1_rate）----
        "k1_scope_npcs": scope_npcs,
        "k1_goals_out_of_run": total - scope_total,
        "k1_goals_progressed_in_scope": scope_passed,
        "k1_rate_in_scope": scope_passed / scope_total if scope_total else 0.0,
        "k1_rate_in_scope_created_only": scope_passed / scope_created if scope_created else 0.0,
        "k1_rate_in_scope_excluding_one_shot": (
            (scope_passed - len(scope_one_shot_passed))
            / (scope_total - scope_one_shot_total)
            if scope_total - scope_one_shot_total > 0
            else 0.0
        ),
        "k1_rate_excluding_one_shot": (
            (passed - len(one_shot_passed)) / (total - one_shot_total)
            if total - one_shot_total > 0
            else 0.0
        ),
        "one_shot_created": one_shot_total,
        "one_shot_progressed": len(one_shot_passed),
        "one_shot_share_of_progressed": len(one_shot_passed) / passed if passed else 0.0,
        "one_shot_by_type": _group(one_shot_passed),
        "goals_completed": sum(1 for r in rows if r["completed"]),
        "goals_monotonic_violations": sum(1 for r in rows if not r["monotonic"]),
        "npcs_with_goals": len({r["npc"] for r in rows}),
        "npcs_progressed": npcs_progressed,
        "npc_progress_rate": npcs_progressed / max(1, len({r["npc"] for r in rows})),
        "by_kind": _by_kind(rows),
        "by_content_type": _by_content_type(rows),
        "not_created": not_created,
        "rows": rows,
    }


def _group(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        key = r["content_type"] or r["kind"]
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _by_kind(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = r["kind"]
        row = out.setdefault(k, {"total": 0, "created": 0, "progressed": 0, "completed": 0})
        row["total"] += 1
        if r["created"]:
            row["created"] += 1
        if r["monotonic"] and r["progressed"]:
            row["progressed"] += 1
        if r["completed"]:
            row["completed"] += 1
    for row in out.values():
        row["rate"] = row["progressed"] / row["total"] if row["total"] else 0.0
    return dict(sorted(out.items()))


def _by_content_type(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = r["content_type"] or "unknown"
        row = out.setdefault(k, {"total": 0, "created": 0, "progressed": 0})
        row["total"] += 1
        if r["created"]:
            row["created"] += 1
        if r["monotonic"] and r["progressed"]:
            row["progressed"] += 1
    for row in out.values():
        row["rate"] = row["progressed"] / row["total"] if row["total"] else 0.0
    return dict(sorted(out.items()))
