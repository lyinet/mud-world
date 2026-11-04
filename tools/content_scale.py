#!/usr/bin/env python3
"""W3 内容扩容包工具：物化、确定性校验、开启态跑种子世界、10 日使用率审计。

设计边界：
- 关闭态默认世界完全不读本包；`content_hash` 仍由 content/validate_content.py 守 7ec670c1。
- 开启态不直接改 content/manifest.yaml，而是把 base + additions + pack 物化成一个
  独立 content 目录（带自己的 manifest），交给现有 `load_content()` 读取。
- 物化过程不读墙钟、不用 random、不依赖 set 迭代顺序；同一输入两次输出字节一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
PACKS = CONTENT / "packs"

try:
  import yaml
except Exception:  # pragma: no cover
  yaml = None

if yaml is None:  # pragma: no cover
  raise SystemExit("需要 PyYAML：pip install pyyaml")


def _read(path: Path):
  return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _entries(path: Path, key: str) -> list:
  data = _read(path)
  value = data.get(key)
  return list(value) if isinstance(value, list) else []


def _dump(path: Path, data) -> None:
  path.write_text(
      yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000),
      encoding="utf-8",
  )


def _deep_merge(base: dict, extra: dict) -> dict:
  out = dict(base)
  for key, value in extra.items():
    if isinstance(value, dict) and isinstance(out.get(key), dict):
      out[key] = _deep_merge(out[key], value)
    else:
      out[key] = value
  return out


def _close_locations(locations: list[dict]) -> list[dict]:
  """补全反向出口；只产出排序后的列表，不把 set 顺序写进内容。"""
  by_id = {loc["id"]: loc for loc in locations if isinstance(loc, dict) and loc.get("id")}
  for loc in by_id.values():
    loc["exits"] = [x for x in (loc.get("exits") or []) if x in by_id and x != loc["id"]]
  for loc in by_id.values():
    for target in list(loc["exits"]):
      other = by_id[target]
      if loc["id"] not in other.setdefault("exits", []):
        other["exits"].append(loc["id"])
  for loc in by_id.values():
    loc["exits"] = sorted(set(loc["exits"]))
  return locations


def pack_meta(pack_id: str) -> dict:
  path = PACKS / pack_id / "pack.yaml"
  if not path.is_file():
    raise SystemExit(f"内容包不存在: {path}")
  return _read(path)


def _pack_file(pack_id: str, meta: dict, key: str) -> Path:
  rel = (meta.get("files") or {}).get(key)
  if not rel:
    raise SystemExit(f"内容包 {pack_id} 未声明 files.{key}")
  return PACKS / pack_id / rel


def materialize(pack_id: str, out_dir: Path) -> dict[str, int]:
  """把 base + additions + pack 写成可被 load_content 读取的独立 content 目录。"""
  meta = pack_meta(pack_id)
  files = meta.get("files") or {}

  npcs = (_entries(CONTENT / "npcs.yaml", "npcs")
          + _entries(CONTENT / "additions" / "npcs.yaml", "npcs")
          + _entries(_pack_file(pack_id, meta, "npcs"), "npcs"))
  locations = (_entries(CONTENT / "locations.yaml", "locations")
               + _entries(CONTENT / "additions" / "locations.yaml", "locations")
               + _entries(_pack_file(pack_id, meta, "locations"), "locations"))
  items = (_entries(CONTENT / "items.yaml", "items")
           + _entries(CONTENT / "additions" / "items.yaml", "items")
           + _entries(_pack_file(pack_id, meta, "items"), "items"))
  verbs = (_entries(CONTENT / "verbs.yaml", "verbs")
           + _entries(CONTENT / "additions" / "verbs.yaml", "verbs"))
  roles = (_entries(CONTENT / "additions" / "roles.yaml", "roles")
           + _entries(_pack_file(pack_id, meta, "roles"), "roles"))
  sects = (_entries(CONTENT / "sects.yaml", "sects")
           + _entries(CONTENT / "additions" / "sects.yaml", "sects"))
  quests = (_entries(CONTENT / "quests.yaml", "quests")
            + _entries(_pack_file(pack_id, meta, "quests"), "quests"))
  routes = _entries(CONTENT / "escort_routes.yaml", "escort_routes")

  economy = _deep_merge(_read(CONTENT / "economy.yaml"), _read(CONTENT / "additions" / "economy_p3i.yaml"))
  if files.get("economy"):
    pack_economy = _read(_pack_file(pack_id, meta, "economy"))
    economy = _deep_merge(economy, pack_economy.get("economy") or pack_economy)

  _close_locations(locations)

  if out_dir.exists():
    shutil.rmtree(out_dir)
  out_dir.mkdir(parents=True, exist_ok=True)

  _dump(out_dir / "npcs.yaml", {"npcs": npcs})
  _dump(out_dir / "locations.yaml", {"locations": locations})
  _dump(out_dir / "items.yaml", {"items": items})
  _dump(out_dir / "verbs.yaml", {"verbs": verbs})
  _dump(out_dir / "roles.yaml", {"roles": roles})
  _dump(out_dir / "sects.yaml", {"sects": sects})
  _dump(out_dir / "quests.yaml", {"quests": quests})
  _dump(out_dir / "escort_routes.yaml", {"escort_routes": routes})
  _dump(out_dir / "economy.yaml", economy)

  manifest = {
      "manifest_version": "1.0.0",
      "engine_min_version": "0.1.0",
      "packages": [
          {"id": f"{pack_id}-npcs", "type": "npcs", "path": "npcs.yaml", "version": "1.0.0"},
          {"id": f"{pack_id}-locations", "type": "locations", "path": "locations.yaml", "version": "1.0.0"},
          {"id": f"{pack_id}-items", "type": "items", "path": "items.yaml", "version": "1.0.0"},
          {"id": f"{pack_id}-verbs", "type": "verbs", "path": "verbs.yaml", "version": "1.0.0"},
          {"id": f"{pack_id}-economy", "type": "economy", "path": "economy.yaml", "version": "1.0.0"},
          {"id": f"{pack_id}-roles", "type": "roles", "path": "roles.yaml", "version": "1.0.0"},
      ],
  }
  _dump(out_dir / "manifest.yaml", manifest)

  persona_src = CONTENT / "personas"
  persona_dst = out_dir / "personas"
  persona_dst.mkdir()
  for src in sorted(persona_src.glob("*.md")):
    shutil.copyfile(src, persona_dst / src.name)

  return {
      "npcs": len(npcs), "locations": len(locations), "items": len(items),
      "verbs": len(verbs), "roles": len(roles), "sects": len(sects),
      "quests": len(quests), "routes": len(routes), "personas": len(list(persona_dst.glob("*.md"))),
  }


def tree_digest(directory: Path) -> str:
  h = hashlib.sha256()
  for path in sorted(p for p in directory.rglob("*") if p.is_file()):
    rel = path.relative_to(directory).as_posix()
    h.update(rel.encode("utf-8"))
    h.update(b"\x00")
    h.update(path.read_bytes())
    h.update(b"\x00")
  return h.hexdigest()


def _prepare_work_root(pack_id: str) -> Path:
  """在 runs/_w3/ 下准备一个带 content/ 与 config/ 的临时根目录。"""
  work = ROOT / "runs" / "_w3" / pack_id
  if work.exists():
    shutil.rmtree(work)
  work.mkdir(parents=True)
  materialize(pack_id, work / "content")
  os.symlink(ROOT / "config", work / "config")
  if (ROOT / "fixtures").is_dir():
    os.symlink(ROOT / "fixtures", work / "fixtures")
  return work


def load_enabled(pack_id: str):
  sys.path.insert(0, str(ROOT / "src"))
  from wsim.core.content import load_content
  work = _prepare_work_root(pack_id)
  return work, load_content(work, min_npcs=0, allow_fixture=False, use_content=True)


def cmd_materialize(args) -> int:
  counts = materialize(args.pack, Path(args.out))
  print(json.dumps({"out": str(Path(args.out).resolve()), "counts": counts}, ensure_ascii=False, indent=2))
  return 0


def cmd_determinism(args) -> int:
  base = ROOT / "runs" / "_w3-determinism"
  a, b = base / "a", base / "b"
  materialize(args.pack, a)
  materialize(args.pack, b)
  da, db = tree_digest(a), tree_digest(b)
  same = da == db
  files_a = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
  files_b = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
  print(json.dumps({
      "pack": args.pack, "digest_a": da, "digest_b": db,
      "byte_identical": same and files_a == files_b, "files": files_a,
  }, ensure_ascii=False, indent=2))
  shutil.rmtree(base)
  return 0 if same and files_a == files_b else 1


def cmd_validate(args) -> int:
  import subprocess
  cmd = [sys.executable, str(CONTENT / "validate_content.py")]
  cmd += ["--pack", args.pack] if args.pack else ["--no-pack"]
  return subprocess.call(cmd, cwd=str(ROOT))


def cmd_run(args) -> int:
  sys.path.insert(0, str(ROOT / "src"))
  from wsim.sim.headless import RunConfig, run_headless
  work, content = load_enabled(args.pack)
  cfg = RunConfig(
      days=args.days, seed=args.seed, npc_limit=0, out_root=ROOT / "runs",
      run_id=args.run_id, with_players=0, dump_state=True, enable_llm=False,
  )
  summary = run_headless(work, cfg, content=content)
  print(json.dumps({
      "run_id": summary.get("run_id"), "days": summary.get("days"),
      "npc_count": summary.get("npc_count"), "events_total": (summary.get("events") or {}).get("total"),
      "state_final": (summary.get("hashes") or {}).get("state_final"),
      "event_final": (summary.get("hashes") or {}).get("event_final"),
      "illegal_rate": (summary.get("events") or {}).get("illegal_rate"),
      "content_hash": summary.get("content_hash"),
  }, ensure_ascii=False, indent=2))
  return 0


def _quest_template(quest_ref: str) -> str:
  """quest:<template>:<tick> -> <template>；兼容不带前缀的写法。"""
  if not isinstance(quest_ref, str):
    return ""
  parts = quest_ref.split(":")
  if len(parts) >= 3 and parts[0] == "quest":
    return parts[1]
  return parts[0]


def cmd_audit(args) -> int:
  run_dir = Path(args.run_dir)
  summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
  events_path = run_dir / "events.jsonl"
  meta = pack_meta(args.pack)
  pack_npcs = {n["id"] for n in _entries(_pack_file(args.pack, meta, "npcs"), "npcs")}
  pack_items = {i["id"] for i in _entries(_pack_file(args.pack, meta, "items"), "items")}
  pack_quests = {q["id"] for q in _entries(_pack_file(args.pack, meta, "quests"), "quests")}

  accepted, delivered = {}, {}
  traded, npc_events = {}, {}
  reject_reasons = {}
  for line in events_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    event = json.loads(line)
    verb = event.get("verb")
    args = event.get("args") or {}
    actor = event.get("actor")
    outcome = event.get("outcome")
    if actor in pack_npcs and verb != "genesis":
      npc_events[actor] = npc_events.get(actor, 0) + 1
    if outcome != "ok":
      reject_reasons[verb] = reject_reasons.get(verb, 0) + 1
      continue
    if verb == "quest_accept":
      template = _quest_template(args.get("quest"))
      if template in pack_quests:
        accepted[template] = accepted.get(template, 0) + 1
    elif verb == "quest_deliver":
      template = _quest_template(args.get("quest"))
      if template in pack_quests:
        delivered[template] = delivered.get(template, 0) + 1
    elif verb in ("buy", "sell"):
      item = args.get("item")
      if item in pack_items:
        traded[item] = traded.get(item, 0) + 1
    elif verb == "trade":
      for entry in (args.get("offer") or []) + (args.get("want") or []):
        if isinstance(entry, dict) and entry.get("item") in pack_items:
          item = entry["item"]
          traded[item] = traded.get(item, 0) + 1

  def _sorted_map(m):
    return {key: m[key] for key in sorted(m)}

  covered_templates = len(accepted)
  covered_items = len(traded)
  covered_npcs = len(npc_events)
  denom = len(pack_quests) + len(pack_items) + len(pack_npcs)
  covered = covered_templates + covered_items + covered_npcs
  strict_rate = covered / denom if denom else 0.0
  result = {
      "run_id": summary.get("run_id"),
      "days": summary.get("days"),
      "events_total": (summary.get("events") or {}).get("total"),
      "state_final": (summary.get("hashes") or {}).get("state_final"),
      "event_final": (summary.get("hashes") or {}).get("event_final"),
      "illegal_rate": (summary.get("events") or {}).get("illegal_rate"),
      "k1_completed": {
          kind: (summary.get("k1") or {}).get("by_kind", {}).get(kind, {}).get("completed")
          for kind in sorted((summary.get("k1") or {}).get("by_kind", {}))
      },
      "new_quest_templates": len(pack_quests),
      "new_quest_templates_accepted": covered_templates,
      "new_items": len(pack_items),
      "new_items_traded": covered_items,
      "new_npcs": len(pack_npcs),
      "new_npcs_active": covered_npcs,
      "usage_rate_strict": round(strict_rate, 4),
      "usage_pass_40pct": strict_rate >= 0.4,
      "accepted_templates": _sorted_map(accepted),
      "delivered_templates": _sorted_map(delivered),
      "traded_items": _sorted_map(traded),
      "active_npcs": _sorted_map(npc_events),
      "rejected_by_verb": _sorted_map(reject_reasons),
  }
  print(json.dumps(result, ensure_ascii=False, indent=2))
  return 0 if strict_rate >= 0.4 else 1


def main() -> int:
  parser = argparse.ArgumentParser(description="W3 内容扩容包工具")
  sub = parser.add_subparsers(dest="cmd", required=True)

  p_mat = sub.add_parser("materialize", help="物化开启态 content 目录")
  p_mat.add_argument("--pack", default="scaleup_w3")
  p_mat.add_argument("--out", required=True)
  p_mat.set_defaults(func=cmd_materialize)

  p_det = sub.add_parser("determinism", help="同输入物化两次并比较字节")
  p_det.add_argument("--pack", default="scaleup_w3")
  p_det.set_defaults(func=cmd_determinism)

  p_val = sub.add_parser("validate", help="调用内容校验器")
  p_val.add_argument("--pack", default="scaleup_w3")
  p_val.add_argument("--off", action="store_true", help="改跑关闭态 --no-pack")
  p_val.set_defaults(func=lambda a: cmd_validate(argparse.Namespace(
      pack=None if a.off else a.pack)))

  p_run = sub.add_parser("run", help="开启态跑种子世界")
  p_run.add_argument("--pack", default="scaleup_w3")
  p_run.add_argument("--days", type=int, default=2)
  p_run.add_argument("--seed", type=int, default=42)
  p_run.add_argument("--run-id", required=True)
  p_run.set_defaults(func=cmd_run)

  p_audit = sub.add_parser("audit", help="审计 10 日新内容使用率")
  p_audit.add_argument("--pack", default="scaleup_w3")
  p_audit.add_argument("--run-dir", required=True)
  p_audit.set_defaults(func=cmd_audit)

  args = parser.parse_args()
  return args.func(args)


if __name__ == "__main__":
  raise SystemExit(main())
