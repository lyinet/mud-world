"""K3 新口径：签名构造、每 NPC 熵、NPC 间 JS。"""
from __future__ import annotations

from wsim.audit.diversity import action_signature, diversity_stats
from wsim.core.models import Event


def _event(seq, actor, verb, args):
    return Event(
        seq=seq,
        tick=seq,
        day=0,
        intent_id=f"i:{seq}",
        actor=actor,
        verb=verb,
        args=args,
        effects=[],
        outcome="ok",
        reason="",
        source="npc",
    )


def test_signature_uses_semantics_not_npc_identity():
    e1 = _event(1, "n1", "apprentice", {"target": "n2"})
    e2 = _event(2, "n3", "apprentice", {"target": "n4"})
    assert action_signature(e1) == action_signature(e2) == "apprentice:npc"

    m1 = _event(3, "n1", "move", {"to": "market"})
    m2 = _event(4, "n1", "move", {"to": "inn"})
    assert action_signature(m1) != action_signature(m2)


def test_entropy_and_pairwise_js():
    events = []
    for seq in range(4):
        events.append(_event(seq, "n1", "move", {"to": "market" if seq < 2 else "inn"}))
    for seq in range(4, 8):
        events.append(_event(seq, "n2", "consume", {"item": "noodle" if seq < 6 else "wine"}))
    stats = diversity_stats(events, {"n1", "n2"}, seed=7)
    assert stats["status"] == "diagnostic_only"
    # 两个 NPC 各自都只有两种签名且均匀 -> 归一化熵 = 1
    assert stats["npc_normalized_entropy_mean"] == 1.0
    assert stats["npc_normalized_entropy_min"] == 1.0
    # 分布完全不相交 -> JS 达到最大 1 bit
    assert stats["pairwise_js_mean"] == 1.0
    assert stats["action_signatures"] == 4
    # 双层与折叠视图必须同时存在
    assert stats["verb_layer"]["actions_total"] == 8
    assert set(stats["folds"]) == {"move_collapsed", "verb_only"}
    assert stats["reference_v2_thresholds"]["status"] == "revoked"
    assert "criteria_pass" not in stats
