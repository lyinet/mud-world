"""K6：关系一致性（重婚、门派领袖非成员、单向强依赖）。"""
from __future__ import annotations

from ..world.state import WorldState

STRONG_KINDS = {"master", "disciple", "family", "creditor", "debtor", "ally", "marriage"}
ALL_KINDS = STRONG_KINDS | {"friend", "rival"}


def relation_stats(state: WorldState, kinds: set[str] | None = None) -> dict:
    kinds = kinds or STRONG_KINDS
    # 重婚
    bigamy = []
    for eid in sorted(state.entities):
        e = state.entities[eid]
        rels = [
            r
            for r in state.relations.values()
            if r.kind == "marriage" and r.status == "active" and eid in (r.a, r.b)
        ]
        partners = {r.b if r.a == eid else r.a for r in rels}
        if len(partners) > 1:
            bigamy.append(eid)
        if e.spouse is not None:
            if e.spouse not in partners:
                bigamy.append(eid)
    # 门派领袖
    leader_not_member = []
    for fid in sorted(state.factions):
        f = state.factions[fid]
        if f.leader and f.leader not in f.members:
            leader_not_member.append(fid)
    # 单向强依赖
    pairs: dict[tuple[str, str, str], dict[str, float]] = {}
    for r in state.relations.values():
        if r.kind not in kinds:
            continue
        a, b = sorted((r.a, r.b))
        row = pairs.setdefault((a, b, r.kind), {})
        row[r.a] = r.affection
    conflicts = []
    for (a, b, kind), row in sorted(pairs.items()):
        diff = abs(row.get(a, 0.0) - row.get(b, 0.0))
        if diff > 60.0:
            conflicts.append({"a": a, "b": b, "kind": kind, "diff": diff})
    checks = len(pairs)
    total_conflicts = len(bigamy) + len(leader_not_member) + len(conflicts)
    return {
        "relation_checks": checks,
        "conflicts": total_conflicts,
        "conflict_rate": total_conflicts / checks if checks else 0.0,
        "bigamy": bigamy,
        "leader_not_member": leader_not_member,
        "one_way_strong": conflicts,
        "kinds": sorted(kinds),
    }
