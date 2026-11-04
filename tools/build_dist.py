#!/usr/bin/env python3
"""构建可解包运行的首发交付包（tar.gz + zip）。

用法:
  .venv/bin/python tools/build_dist.py --version p3f
产物:
  dist/xkx-world-sim-p3f.tar.gz
  dist/xkx-world-sim-p3f.zip
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = [
    "start.sh",
    "requirements.txt",
    "requirements.lock",
    "pyproject.toml",
    ".env.example",
    "ARCHITECTURE.md",
    "spec.md",
    "tasks.md",
    "SECURITY.md",
    "CHANGELOG.md",
]
TOP_DIRS = [
    "src",
    "sim",
    "ui",
    "config",
    "content",
    "scripts",
    "tools",
    "tests",
    "specs",
    "fixtures",
    "plugins",
    "client",
    "docs",
    "reports",
]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".git", "dist", ".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite-wal", ".sqlite-shm"}


def iter_files(path: Path):
    if path.is_file():
        yield path
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name in SKIP_DIRS or name.startswith(".") or Path(name).suffix in SKIP_SUFFIXES:
                continue
            yield Path(dirpath) / name


def collect() -> list[Path]:
    files: list[Path] = []
    files.extend(ROOT / name for name in TOP_FILES)
    for name in TOP_DIRS:
        base = ROOT / name
        if not base.exists():
            continue
        files.extend(iter_files(base))
    return sorted({p for p in files if p.exists()}, key=lambda p: str(p.relative_to(ROOT)))


def arcname(rel: Path, top: str) -> str:
    if rel.as_posix() == "deploy/README.md":
        return f"{top}/README.md"
    if rel.as_posix() == "README.md":
        return f"{top}/README-PROJECT.md"
    return f"{top}/{rel.as_posix()}"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_tar(files: list[Path], target: Path, top: str) -> None:
    with tarfile.open(target, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for src in files:
            rel = src.relative_to(ROOT)
            info = tf.gettarinfo(str(src), arcname=arcname(rel, top))
            info.mtime = 1793721600  # 2026-11-04 00:00 UTC，固定时间戳便于比对
            if rel.as_posix() == "deploy/README.md":
                info.name = f"{top}/README.md"
            with src.open("rb") as fh:
                tf.addfile(info, fh)


def write_zip(files: list[Path], target: Path, top: str) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src in files:
            rel = src.relative_to(ROOT)
            name = arcname(rel, top)
            mode = src.stat().st_mode
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 4, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | (mode & 0o777)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, src.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="p3f")
    parser.add_argument("--out", default="dist")
    args = parser.parse_args()
    tag = args.version if args.version.startswith("delivery-") else f"delivery-{args.version}"
    top = "world-sim"
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    files = collect()
    readme = ROOT / "deploy" / "README.md"
    files.append(readme)
    files.append(ROOT / "README.md")
    files = sorted({p for p in files if p.exists()}, key=lambda p: str(p.relative_to(ROOT)))

    manifest = ROOT / "dist" / f"MANIFEST-{tag}.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    line_items = []
    for src in files:
        rel = arcname(src.relative_to(ROOT), top)
        line_items.append(f"{sha256(src)}  {rel}")
    manifest.write_text("\n".join(line_items) + "\n", encoding="utf-8")

    tar_path = out_dir / f"xkx-world-sim-{tag}.tar.gz"
    zip_path = out_dir / f"xkx-world-sim-{tag}.zip"
    write_tar(files, tar_path, top)
    write_zip(files, zip_path, top)

    print(f"files: {len(files)}")
    print(f"tar.gz: {tar_path} ({tar_path.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"zip:    {zip_path} ({zip_path.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
