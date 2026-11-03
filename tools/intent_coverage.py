#!/usr/bin/env python3
"""P0-5：意图模板覆盖探针。

用真实 intent 槽（config/llm.yaml 的 models.intent）逐条跑「模板 × 2 条原话」，
把模型原始输出、grounding 解析结果写进 JSONL，并按模板汇总成功率。

覆盖口径：
- 每条原话都必须字面包含该模板的必填参数（地点/人物/物品/任务/数字）；
- 成功 = 类别与模板一致、状态为 ok 或 needs_confirmation（destructive 未确认）、
  且没有 missing 必填字段；needs_clarification / reject / 类别漂移都记失败；
- 失败条目保留 blocked / dropped_fields / unknown_keys，便于判断是模型问题还是
  grounding 拦截。

用法（key 只从环境变量读）：
  LLM_API_KEY=... .venv/bin/python tools/intent_coverage.py \
      --records reports/evidence/p3e-deepseek-intent-coverage.jsonl \
      --summary reports/evidence/p3e-intent-coverage.json \
      --markdown reports/evidence/p3e-intent-coverage.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.agent.intent_model import (  # noqa: E402
    build_intent_prompt,
    load_intent_catalog,
    parse_intent_response,
)
from wsim.agent.prompt import build_state_summary  # noqa: E402
from wsim.agent.provider import (  # noqa: E402
    ProviderClient,
    ProviderConfig,
    ProviderConfigError,
    load_model_slots,
    load_provider_settings,
    prompt_sha,
)
from wsim.core.content import load_content  # noqa: E402
from wsim.sim.genesis import build_genesis  # noqa: E402
from wsim.world.effects import apply_effects  # noqa: E402
from wsim.world.state import WorldState  # noqa: E402

SPEAKER = "luo_qingxu"  # 罗轻絮：集市绣娘，技能=绣娘
TICK = 12

# (模板类别, 期望动词, [原话×2])
CASES: list[tuple[str, str, str]] = [
    ("去某地", "move", "去铁匠铺看看沈砚舟打的刀"),
    ("去某地", "move", "天黑了，回客栈歇一宿"),
    ("上工", "work", "去集市上工三个时辰"),
    ("上工", "work", "到药铺帮工五个时辰"),
    ("吃饭", "consume", "吃两个炊饼垫垫肚子"),
    ("吃饭", "consume", "喝一壶浊酒暖暖身子"),
    ("修炼", "cultivate", "练两个时辰绣娘手艺"),
    ("修炼", "cultivate", "练四个时辰绣娘针法"),
    ("交易", "trade", "拿两匹粗布跟柳三娘换一壶浊酒"),
    ("交易", "trade", "用三张硝皮换金满仓的百炼刀"),
    ("购买", "buy", "去集市买两斗糙米"),
    ("购买", "buy", "到药铺买三包艾草"),
    ("出售", "sell", "把三张硝皮卖去集市"),
    ("出售", "sell", "把一匹粗布卖到客栈"),
    ("拜师", "apprentice", "跟柳三娘学绣活三十天"),
    ("拜师", "apprentice", "拜沈砚舟为师学打铁六十天"),
    ("婚配", "marry", "我想跟柳三娘成亲"),
    ("婚配", "marry", "择日和金满仓拜堂"),
    ("入门派", "join_sect", "我要入清议社"),
    ("入门派", "join_sect", "加入茶行，交五十文会费"),
    ("开宗立派", "found_sect", "在集市立一个叫青石帮的门派"),
    ("开宗立派", "found_sect", "在城隍庙创个名叫白云观的门派"),
    ("开店", "open_shop", "在集市摆个摊位卖炊饼"),
    ("开店", "open_shop", "在客栈盘一间店铺"),
    ("护送", "escort", "走集市、铁匠铺这条镖，货值三百文，酬金五十文"),
    ("护送", "escort", "走镖局、河渡这条镖线，货值两百文，脚钱四十文"),
    ("接任务", "quest_accept", "接「送一匹绸样」这趟差事"),
    ("接任务", "quest_accept", "我想接任务：捎账本到客栈"),
    ("交任务", "quest_deliver", "把送一匹绸样交了"),
    ("交任务", "quest_deliver", "交掉捎账本到客栈这单"),
    ("对话", "talk", "跟柳三娘说「今日布价如何」"),
    ("对话", "talk", "对金满仓说「这批铁器我全要了」"),
    ("还债", "debt_settle", "把欠柳三娘的钱还了"),
    ("还债", "debt_settle", "还清金满仓那笔债"),
]


def build_intent_client() -> ProviderClient:
    settings = load_provider_settings(ROOT)
    slot = dict(load_model_slots(ROOT).get("intent") or settings)
    cfg = ProviderConfig.from_env(
        True,
        timeout_s=float(slot.get("timeout_s", 30)),
        max_retries=int(slot.get("max_retries", 2)),
        backoff_base_s=float(slot.get("backoff_base_s", 1.0)),
        max_tokens=int(slot.get("max_tokens", 1024)),
        temperature=float(slot.get("temperature", 0.0)),
        base_url=str(slot.get("base_url", "")),
        model=str(slot.get("model", "")),
        key_env=str(slot.get("api_key_env", "LLM_API_KEY")),
        json_mode=bool(slot.get("json_mode", True)),
        slot="intent",
        model_env=str(slot.get("model_env", "")),
        extra_body=list(slot.get("extra_body") or []),
        backoff_429_s=float(slot.get("backoff_429_s", 25.0)),
    )
    assert cfg is not None
    return ProviderClient(cfg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default="reports/evidence/p3e-deepseek-intent-coverage.jsonl")
    ap.add_argument("--summary", default="reports/evidence/p3e-intent-coverage.json")
    ap.add_argument("--markdown", default="reports/evidence/p3e-intent-coverage.md")
    ap.add_argument("--limit-templates", type=int, default=0,
                    help="只跑前 N 个模板（调试用，正式证据必须跑全量）")
    args = ap.parse_args()

    content = load_content(ROOT, allow_fixture=False, use_content=True)
    catalog = load_intent_catalog(ROOT, content.verbs)
    genesis = build_genesis(content, content.npcs, root=ROOT)
    state = WorldState(locations=content.locations)
    apply_effects(state, genesis.effects)
    state.tick = TICK

    try:
        client = build_intent_client()
    except ProviderConfigError as exc:
        print(f"intent 槽不可用：{exc}", file=sys.stderr)
        return 2
    cfg = client.config
    print(f"intent slot: base={cfg.base_url} model={cfg.model} extra_body={list(cfg.extra_body)}")

    templates = list(dict.fromkeys(c for c, _v, _u in CASES))
    if args.limit_templates:
        keep = set(templates[: args.limit_templates])
        cases = [row for row in CASES if row[0] in keep]
    else:
        cases = list(CASES)

    records_path = ROOT / args.records
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text("", encoding="utf-8")

    per_template: dict[str, dict[str, int]] = defaultdict(
        lambda: {"samples": 0, "success": 0, "ok": 0, "needs_confirmation": 0,
                 "needs_clarification": 0, "reject": 0}
    )
    rows: list[dict] = []
    started = time.perf_counter()
    for idx, (category, verb, utterance) in enumerate(cases, 1):
        tpl = catalog.by_category[category]
        summary = build_state_summary(content, state, SPEAKER, TICK, "spotlight")
        system, user = build_intent_prompt(catalog, utterance, summary, [])
        ground = json.dumps(
            {
                "history": [],
                "location": summary.get("location"),
                "location_name": summary.get("location_name"),
                "exits": summary.get("exits"),
                "reachable_locations": summary.get("reachable_locations"),
                "nearby_npcs": summary.get("nearby_npcs"),
            },
            ensure_ascii=False, sort_keys=True,
        )
        error = ""
        output = ""
        usage: dict[str, int] = {}
        latency = 0.0
        try:
            text = client.complete(system, user)
            output = text
            usage = dict(client.last_usage or {})
            latency = float(client.last_latency_s)
            resolution = parse_intent_response(
                text, catalog, content, state, SPEAKER,
                utterance=utterance, context_text=ground,
            )
        except Exception as exc:  # ProviderUnavailable / 任何端点异常都记录，不中断全量
            error = f"{type(exc).__name__}:{exc}"
            resolution = parse_intent_response(
                "", catalog, content, state, SPEAKER,
                utterance=utterance, context_text=ground,
            )

        category_match = resolution.category == category
        success = bool(
            category_match
            and resolution.status in ("ok", "needs_confirmation")
            and not resolution.missing
        )
        stats = per_template[category]
        stats["samples"] += 1
        stats["success"] += int(success)
        stats[resolution.status] = stats.get(resolution.status, 0) + 1

        row = {
            "case": idx,
            "category": category,
            "verb": verb,
            "utterance": utterance,
            "prompt_sha": prompt_sha(system, user),
            "output": output,
            "usage": usage,
            "latency_s": round(latency, 3),
            "error": error,
            "result": resolution.to_dict(),
            "success": success,
            "category_match": category_match,
            "template_destructive": tpl.destructive,
        }
        rows.append(row)
        with records_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        flag = "PASS" if success else "FAIL"
        print(f"[{idx:02d}/{len(cases)}] {flag} {category}:{verb} <- {utterance} "
              f"status={resolution.status} cat={resolution.category} "
              f"missing={resolution.missing} blocked={resolution.blocked}")

    elapsed = round(time.perf_counter() - started, 2)
    total = len(rows)
    passed = sum(1 for r in rows if r["success"])
    by_template = {
        cat: {
            "samples": s["samples"],
            "success": s["success"],
            "success_rate": round(s["success"] / s["samples"], 4) if s["samples"] else 0.0,
            "statuses": {k: v for k, v in s.items() if k in
                         ("ok", "needs_confirmation", "needs_clarification", "reject") and v},
        }
        for cat, s in per_template.items()
    }
    summary_doc = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": elapsed,
        "speaker": SPEAKER,
        "tick": TICK,
        "intent_slot": cfg.fingerprint(),
        "total": total,
        "success": passed,
        "success_rate": round(passed / total, 4) if total else 0.0,
        "templates_covered": len(by_template),
        "templates_with_ge2": sum(1 for v in by_template.values() if v["samples"] >= 2),
        "by_template": by_template,
        "latency": client.stats_snapshot(),
        "records_path": args.records,
    }
    (ROOT / args.summary).write_text(
        json.dumps(summary_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# 意图模板覆盖（真实 intent 槽）",
        "",
        f"- 生成时间：{summary_doc['generated_at']}；耗时 {elapsed}s",
        f"- 模型：`{cfg.model}`；extra_body={list(cfg.extra_body)}；base={cfg.base_url}",
        f"- 样本 {total} 条，覆盖 {len(by_template)} 个模板（≥2 条/模板："
        f"{summary_doc['templates_with_ge2']} 个），成功 {passed}/{total}"
        f"（{summary_doc['success_rate']:.2%}）",
        "- 成功口径：类别一致 + `ok`/`needs_confirmation` + 无 missing 必填字段",
        "",
        "| 模板 | 动词 | 样本 | 成功 | 成功率 | 状态 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for category in templates:
        if category not in per_template:
            continue
        row = by_template[category]
        verb = catalog.by_category[category].verb
        statuses = ", ".join(f"{k}={v}" for k, v in row["statuses"].items()) or "-"
        lines.append(
            f"| {category} | {verb} | {row['samples']} | {row['success']} "
            f"| {row['success_rate']:.0%} | {statuses} |"
        )
    fails = [r for r in rows if not r["success"]]
    lines += ["", "## 失败样本", ""]
    if not fails:
        lines.append("无。")
    else:
        for r in fails:
            res = r["result"]
            why = res.get("reason") or r.get("error") or "unknown"
            lines.append(
                f"- `{r['category']}` / {r['verb']}：{r['utterance']} → "
                f"status={res.get('status')} category={res.get('category')} "
                f"missing={res.get('missing')} blocked={res.get('blocked')} "
                f"dropped={res.get('dropped_fields')} reason={why}"
            )
    (ROOT / args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n覆盖 {len(by_template)} 模板 / {total} 样本，成功 {passed} "
          f"({summary_doc['success_rate']:.2%})，耗时 {elapsed}s")
    print(f"records -> {args.records}\nsummary -> {args.summary}\nmarkdown -> {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
