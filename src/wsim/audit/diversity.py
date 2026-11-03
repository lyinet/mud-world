"""K3 行为多样性：双层诊断（verb 层 + 对象层）。

口径见 docs/metrics.md v1.1.0：
- 只统计成功执行的 NPC 动作；
- 对象层签名 = verb + 参数语义 + target 类型，不含 NPC id / 数量 / 时间 / 金额；
- 同时输出 move_collapsed 折叠视图，用于检验“通过是否来自拆桶”；
- P1 不做 pass/fail，只输出诊断数值。
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Callable

from ..core.models import Event
from ..core.rng import PRNG

SIGNATURE_SPEC_VERSION = "1.1.0"
SIGNATURE_SPEC_EFFECTIVE = "2026-11-03"
LEGACY_SPEC_VERSION = "1.0.0"

# P2-B 新增的引擎动词；v1.0.0 只定义 13 个动词，这 4 个在旧口径里都落到 `verb:?`。
NEW_ENGINE_VERBS = ("debt_settle", "quest_accept", "quest_deliver", "recruit")
DEBT_CLASSES = ("initial", "wage", "living", "escort")

# 历史阈值（v2）。已废止：设定时已看到实跑数值，且多数项会随 move 拆桶显著变化。
# 仅作对照输出，不参与任何结论；具体失败项数随 run 变化，以 folds.move_collapsed 实测为准。
REFERENCE_V2_THRESHOLDS = {
    "status": "revoked",
    "reason": "post-hoc thresholds; not pre-registered; see move_collapsed for collapse sensitivity",
    "signature_top10_share": 0.80,
    "signature_entropy_bits": 4.0,
    "npc_normalized_entropy_mean": 0.70,
    "npc_normalized_entropy_min": 0.50,
    "pairwise_js_mean": 0.30,
    "npc_min_distinct_verbs": 3,
}


def _movement_target(args: dict[str, Any]) -> str:
    return str(args.get("to") or args.get("at") or "?")


def _trade_bundle(raw: Any) -> str:
    if not isinstance(raw, list):
        return "-"
    items = sorted(
        str(row.get("item"))
        for row in raw
        if isinstance(row, dict) and row.get("item")
    )
    return "+".join(items) if items else "-"


def _debt_class(raw: Any) -> str:
    """债务来源分类：只看 debt id 的内容语义前缀，不看金额/当事人。"""
    text = str(raw or "")
    for cls in DEBT_CLASSES:
        if text.startswith(f"debt:{cls}:"):
            return cls
    if text.startswith("debt:init:"):
        return "initial"
    return "other"


def action_signature(e: Event, fold: str = "none", spec: str = SIGNATURE_SPEC_VERSION) -> str:
    """spec v1.1.0：verb + 参数语义 + target 类型。

    fold="none"           对象层签名
    fold="move_collapsed" 把 move:<地点> 折叠为 move:any，其余不变
    fold="verb_only"      纯动词（等同 verb 层）
    spec="1.0.0"          兼容口径：4 个新动词落到 `verb:?`
    """
    verb = e.verb
    if fold == "verb_only":
        return verb
    args = e.args or {}
    if spec == LEGACY_SPEC_VERSION:
        if verb in NEW_ENGINE_VERBS:
            return f"{verb}:?"
        return action_signature(e, fold=fold, spec=SIGNATURE_SPEC_VERSION)

    if verb == "move":
        return "move:any" if fold == "move_collapsed" else f"move:{_movement_target(args)}"
    if verb == "cultivate":
        return f"cultivate:{args.get('skill', '?')}"
    if verb == "work":
        return f"work:{args.get('at', '?')}"
    if verb in ("consume", "buy", "sell"):
        return f"{verb}:{args.get('item', '?')}"
    if verb == "trade":
        delta = args.get("money_delta", 0)
        try:
            direction = "even" if int(delta) == 0 else ("recv" if int(delta) > 0 else "pay")
        except (TypeError, ValueError):
            direction = "even"
        return f"trade:{_trade_bundle(args.get('offer'))}|{_trade_bundle(args.get('want'))}|{direction}"
    if verb in ("apprentice", "marry"):
        return f"{verb}:npc"
    if verb == "join_sect":
        target = str(args.get("target") or "")
        if target.startswith("sect:"):
            kind = "sect"
        elif target.startswith("org:"):
            kind = "org"
        else:
            kind = "location"
        return f"join_sect:{kind}"
    if verb == "open_shop":
        return f"open_shop:{args.get('shop_type', '?')}"
    if verb == "escort":
        route = args.get("route")
        if isinstance(route, list) and len(route) >= 2:
            return f"escort:{route[0]}->{route[-1]}"
        return "escort:?->?"
    if verb == "found_sect":
        return "found_sect:settlement"
    # ---- spec v1.1.0 新增 ----
    if verb == "debt_settle":
        return f"debt_settle:{_debt_class(args.get('debt'))}"
    if verb in ("quest_accept", "quest_deliver"):
        qverb = str(args.get("quest_verb") or "?")
        return f"{verb}:{qverb}"
    if verb == "recruit":
        return "recruit:npc"
    return f"{verb}:?"


def _entropy(counts: Counter[str] | dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    out = 0.0
    for value in counts.values():
        if value <= 0:
            continue
        p = value / total
        out -= p * math.log2(p)
    return out


def _js_divergence(a: dict[str, int], b: dict[str, int]) -> float:
    ta, tb = sum(a.values()), sum(b.values())
    if ta <= 0 or tb <= 0:
        return 0.0
    keys = set(a) | set(b)
    out = 0.0
    for key in keys:
        p = a.get(key, 0) / ta
        q = b.get(key, 0) / tb
        m = (p + q) / 2.0
        if p > 0:
            out += 0.5 * p * (math.log2(p) - math.log2(m))
        if q > 0:
            out += 0.5 * q * (math.log2(q) - math.log2(m))
    return out


def _npc_normalized_entropy(dist: dict[str, int]) -> float:
    k = len(dist)
    if k <= 1:
        return 1.0 if sum(dist.values()) > 0 else 0.0
    return _entropy(dist) / math.log2(k)


def _pairwise_js(per_npc: dict[str, dict[str, int]]) -> dict[str, Any]:
    ids = sorted(per_npc)
    vals: list[float] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            vals.append(_js_divergence(per_npc[a], per_npc[b]))
    if not vals:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "pairs": 0}
    return {
        "mean": round(sum(vals) / len(vals), 10),
        "min": round(min(vals), 10),
        "max": round(max(vals), 10),
        "pairs": len(vals),
    }


def _js_vs_global(per_npc: dict[str, dict[str, int]], global_counts: dict[str, int]) -> float:
    vals = [_js_divergence(dist, global_counts) for dist in per_npc.values() if sum(dist.values()) > 0]
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 10)


def _null_js(per_npc: dict[str, dict[str, int]], global_counts: dict[str, int], seed: int) -> float:
    """零假设：保持每个 NPC 的动作总量，按全局分布重采样。"""
    total = sum(global_counts.values())
    if total <= 0:
        return 0.0
    keys = sorted(global_counts)
    weights = [global_counts[k] / total for k in keys]
    rng = PRNG(seed, 0, "k3_null")
    sampled: dict[str, dict[str, int]] = {}
    for nid in sorted(per_npc):
        n = sum(per_npc[nid].values())
        if n <= 0:
            continue
        dist: dict[str, int] = {}
        for _ in range(n):
            key = rng.weighted_choice(keys, weights)
            dist[key] = dist.get(key, 0) + 1
        sampled[nid] = dist
    return _pairwise_js(sampled)["mean"] if len(sampled) >= 2 else 0.0


def _layer_stats(
    events: list[Event],
    npc_ids: set[str],
    signature: Callable[[Event], str],
    seed: int,
    null_resample: bool = False,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    per_npc: dict[str, Counter[str]] = {nid: Counter() for nid in sorted(npc_ids)}
    for e in events:
        if e.outcome != "ok" or e.source == "system" or e.verb.startswith("system_"):
            continue
        sig = signature(e)
        counts[sig] += 1
        if e.actor in per_npc:
            per_npc[e.actor][sig] += 1

    total = sum(counts.values())
    top10 = sum(x for _, x in counts.most_common(10))
    entropy = _entropy(counts)
    global_counts = dict(counts)

    npc_entropies = [_npc_normalized_entropy(dict(d)) for d in per_npc.values() if sum(d.values()) > 0]
    npc_entropy_mean = round(sum(npc_entropies) / len(npc_entropies), 10) if npc_entropies else 0.0
    npc_entropy_min = round(min(npc_entropies), 10) if npc_entropies else 0.0

    npc_distinct = [len(d) for d in per_npc.values() if sum(d.values()) > 0]
    pairwise = _pairwise_js({k: dict(v) for k, v in per_npc.items()})

    return {
        "actions_total": total,
        "categories": len(counts),
        "top10_share": round(top10 / total, 10) if total else 0.0,
        "entropy_bits": round(entropy, 10),
        "effective_categories": round(2 ** entropy, 4),
        "normalized_entropy": round(entropy / math.log2(len(counts)), 10) if len(counts) > 1 else 0.0,
        "per_npc_normalized_entropy_mean": npc_entropy_mean,
        "per_npc_normalized_entropy_min": npc_entropy_min,
        "per_npc_distinct_min": min(npc_distinct) if npc_distinct else 0,
        "per_npc_distinct_mean": round(sum(npc_distinct) / len(npc_distinct), 4) if npc_distinct else 0.0,
        "pairwise_js_mean": pairwise["mean"],
        "pairwise_js_min": pairwise["min"],
        "pairwise_js_max": pairwise["max"],
        "pairwise_js_pairs": pairwise["pairs"],
        "js_vs_global_mean": _js_vs_global({k: dict(v) for k, v in per_npc.items()}, global_counts),
        "counts": dict(sorted(counts.items())),
        "per_npc": {
            nid: {
                "actions": sum(d.values()),
                "distinct": len(d),
                "normalized_entropy": round(_npc_normalized_entropy(dict(d)), 10),
            }
            for nid, d in sorted(per_npc.items())
        },
        "null_js_mean": _null_js({k: dict(v) for k, v in per_npc.items()}, global_counts, seed) if null_resample else None,
        "signal_to_noise": None,
    }


def _object_summary(layer: dict[str, Any]) -> dict[str, Any]:
    return {
        "actions_total": layer["actions_total"],
        "categories": layer["categories"],
        "top10_share": layer["top10_share"],
        "entropy_bits": layer["entropy_bits"],
        "effective_categories": layer["effective_categories"],
        "normalized_entropy": layer["normalized_entropy"],
        "per_npc_normalized_entropy_mean": layer["per_npc_normalized_entropy_mean"],
        "per_npc_normalized_entropy_min": layer["per_npc_normalized_entropy_min"],
        "per_npc_distinct_mean": layer["per_npc_distinct_mean"],
        "pairwise_js_mean": layer["pairwise_js_mean"],
        "counts": layer["counts"],
    }


def diversity_stats(events: list[Event], npc_ids: set[str], seed: int = 42) -> dict[str, Any]:
    verb_layer = _layer_stats(events, npc_ids, lambda e: e.verb, seed, null_resample=False)
    object_layer = _layer_stats(events, npc_ids, lambda e: action_signature(e), seed, null_resample=True)
    move_layer = _layer_stats(
        events, npc_ids, lambda e: action_signature(e, "move_collapsed"), seed, null_resample=False
    )
    verb_only_layer = _layer_stats(events, npc_ids, lambda e: action_signature(e, "verb_only"), seed, null_resample=False)
    # spec v1.0.0 兼容口径：4 个新动词落 `verb:?`。同一 run 必须同时报告两版。
    v10_layer = _layer_stats(
        events, npc_ids, lambda e: action_signature(e, spec=LEGACY_SPEC_VERSION), seed, null_resample=False
    )
    # 固定敏感性对照：整体剔除 4 个新动词，检验改善是否由旧 13 个动词贡献。
    legacy_only_events = [e for e in events if e.verb not in NEW_ENGINE_VERBS]
    sensitivity_layer = _layer_stats(
        legacy_only_events, npc_ids, lambda e: action_signature(e), seed, null_resample=False
    )
    new_verb_sigs = {
        sig: count
        for sig, count in object_layer["counts"].items()
        if sig.split(":", 1)[0] in NEW_ENGINE_VERBS
    }
    new_verb_actions = sum(new_verb_sigs.values())
    new_verb_share = new_verb_actions / object_layer["actions_total"] if object_layer["actions_total"] else 0.0

    if object_layer["null_js_mean"]:
        object_layer["signal_to_noise"] = round(object_layer["pairwise_js_mean"] / object_layer["null_js_mean"], 4)

    def _subset(layer: dict[str, Any]) -> dict[str, Any]:
        return {
            "top10_share": layer["top10_share"],
            "entropy_bits": layer["entropy_bits"],
            "effective_categories": layer["effective_categories"],
            "per_npc_normalized_entropy_mean": layer["per_npc_normalized_entropy_mean"],
            "per_npc_normalized_entropy_min": layer["per_npc_normalized_entropy_min"],
            "pairwise_js_mean": layer["pairwise_js_mean"],
            "per_npc_distinct_min": layer["per_npc_distinct_min"],
        }

    return {
        "metric": "k3_dual_layer",
        "spec_version": SIGNATURE_SPEC_VERSION,
        "effective_date": SIGNATURE_SPEC_EFFECTIVE,
        "status": "diagnostic_only",
        "p1_note": (
            "P1 为日程驱动，verb 层集中是预期现象；K3 只做诊断，不设 pass/fail。"
            "多样性门槛推迟到 P3 引入意图层后判定。"
        ),
        "verb_layer": verb_layer,
        "object_layer": object_layer,
        "v10_compat": {
            "spec_version": LEGACY_SPEC_VERSION,
            "object_layer": _object_summary(v10_layer),
        },
        "sensitivity_excluding_new_verbs": {
            "excluded_verbs": sorted(NEW_ENGINE_VERBS),
            "object_layer": _object_summary(sensitivity_layer),
        },
        "new_verb_signatures": dict(sorted(new_verb_sigs.items())),
        "new_verb_action_share": round(new_verb_share, 10),
        "folds": {
            "move_collapsed": _subset(move_layer),
            "verb_only": _subset(verb_only_layer),
        },
        "reference_v2_thresholds": REFERENCE_V2_THRESHOLDS,
        # 兼容旧 summary 消费方的字段（值来自对象层；不参与判定）
        "verb_counts": verb_layer["counts"],
        "action_signatures": object_layer["categories"],
        "signature_counts": object_layer["counts"],
        "signature_top10_share": object_layer["top10_share"],
        "signature_entropy_bits": object_layer["entropy_bits"],
        "npc_normalized_entropy_mean": object_layer["per_npc_normalized_entropy_mean"],
        "npc_normalized_entropy_min": object_layer["per_npc_normalized_entropy_min"],
        "pairwise_js_mean": object_layer["pairwise_js_mean"],
        "pairwise_js_min": object_layer["pairwise_js_min"],
        "pairwise_js_max": object_layer["pairwise_js_max"],
        "js_vs_global_mean": object_layer["js_vs_global_mean"],
        "null_js_mean": object_layer["null_js_mean"],
        "signal_to_noise": object_layer["signal_to_noise"],
        "distinct_verbs_per_npc": verb_layer["per_npc"],
        "min_distinct_verbs": verb_layer["per_npc_distinct_min"],
        "legacy_verb_top10_share": verb_layer["top10_share"],
        "legacy_verb_entropy_bits": verb_layer["entropy_bits"],
        "top10_share": object_layer["top10_share"],
    }
