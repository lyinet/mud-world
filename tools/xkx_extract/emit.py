"""规范化 JSON 输出、manifest 与幂等校验。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

DATA_FILES = (
    "rooms.json",
    "exits.json",
    "items.json",
    "npcs.json",
    "dialogue.json",
    "shops.json",
    "sects.json",
    "skills.json",
)

TABLES = {
    "rooms.json": "rooms",
    "exits.json": "exits",
    "items.json": "items",
    "npcs.json": "npcs",
    "dialogue.json": "dialogue",
    "shops.json": "shops",
    "sects.json": "sects",
    "skills.json": "skills",
}


def dumps(obj) -> str:
    """固定分隔符与排序，保证同一输入逐字节一致。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def dumps_jsonl(rows) -> str:
    lines = [
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def dedupe(diags: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for d in diags:
        key = (d.get("rule"), d.get("path"), d.get("line"), d.get("message"))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    out.sort(key=lambda d: (
        d.get("path") or "",
        d.get("line") or 0,
        d.get("rule") or "",
        d.get("message") or "",
    ))
    return out


def build_outputs(pack, diagnostics, counts, coverage, source_info) -> dict[str, str]:
    """返回 {文件名: 文本内容}，不写盘。"""
    diags = dedupe(diagnostics)
    sev = {"error": 0, "warning": 0, "info": 0}
    by_rule: dict[str, int] = {}
    for d in diags:
        sev[d["severity"]] = sev.get(d["severity"], 0) + 1
        by_rule[d["rule"]] = by_rule.get(d["rule"], 0) + 1

    files: dict[str, str] = {}
    for filename, key in TABLES.items():
        files[filename] = dumps({key: pack[key]})

    files["diagnostics.jsonl"] = dumps_jsonl(diags)

    summary = {
        "counts": counts,
        "coverage": coverage,
        "errors": sev.get("error", 0),
        "warnings": sev.get("warning", 0),
        "infos": sev.get("info", 0),
        "by_rule": dict(sorted(by_rule.items())),
    }
    files["summary.json"] = dumps(summary)

    hashes = {
        name: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for name, text in files.items()
    }
    content_hash = hashlib.sha256(
        "".join(f"{name}:{hashes[name]}\n" for name in sorted(hashes)).encode("utf-8")
    ).hexdigest()

    manifest = {
        "schema_version": 1,
        "rules_version": 1,
        "generator": "tools/xkx_extract",
        "source": source_info,
        "areas": source_info.get("areas", []),
        "counts": counts,
        "coverage": coverage,
        "files": {
            name: {"sha256": hashes[name], "bytes": len(files[name].encode("utf-8"))}
            for name in sorted(files)
        },
        "content_hash": content_hash,
    }
    files["manifest.json"] = dumps(manifest)
    return files


def write_outputs(outdir: str, files: dict[str, str]) -> dict[str, int]:
    os.makedirs(outdir, exist_ok=True)
    sizes = {}
    for name in sorted(files):
        path = os.path.join(outdir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(files[name])
        sizes[name] = len(files[name].encode("utf-8"))
    return sizes


def verify_outputs(outdir: str, files: dict[str, str]) -> list[str]:
    """逐文件比对现有产物与本次重抽结果，返回差异文件名。"""
    diffs = []
    for name in sorted(files):
        path = os.path.join(outdir, name)
        if not os.path.isfile(path):
            diffs.append(name)
            continue
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
        if existing != files[name]:
            diffs.append(name)
    return diffs


def extract_to_temp(src_root, areas):
    """幂等自检用：重新抽一遍到临时目录。"""
    from . import extract as extract_mod

    ex = extract_mod.Extractor(src_root, areas)
    pack = ex.run()
    from . import validate as validate_mod

    diags = list(ex.diagnostics) + validate_mod.validate(pack)
    counts = ex.counts()
    source_info = {
        "repo": "fluffos/xkx100",
        "commit": extract_mod.source_commit(src_root),
        "areas": list(areas),
    }
    files = build_outputs(pack, diags, counts, ex.coverage, source_info)
    tmp = tempfile.mkdtemp(prefix="xkx-verify-")
    write_outputs(tmp, files)
    return tmp, files
