#!/usr/bin/env python3
"""Atomically rebuild and validate a standalone human-review ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def unsafe_entry(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or bool(path.drive)


def rebuild_zip(
    source_dir: Path,
    output_path: Path,
    *,
    flat_root: bool = False,
) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    output_path = output_path.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"missing source directory: {source_dir}")
    if output_path == source_dir or output_path.is_relative_to(source_dir):
        raise ValueError("output ZIP must not be inside the source directory")

    files = sorted(
        (path for path in source_dir.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix().casefold(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{output_path.stem}.", suffix=".tmp.zip", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            for path in files:
                relative = path.relative_to(source_dir).as_posix()
                archive_name = relative if flat_root else f"{source_dir.name}/{relative}"
                archive.write(path, archive_name)

        with zipfile.ZipFile(temporary_path, "r") as archive:
            names = archive.namelist()
            corrupt_entry = archive.testzip()
        duplicate_entries = sorted(name for name, count in Counter(names).items() if count > 1)
        unsafe_entries = sorted(name for name in names if unsafe_entry(name))
        nested_zips = sorted(name for name in names if name.lower().endswith(".zip"))
        roots = sorted({PurePosixPath(name).parts[0] for name in names if name})
        issues: list[str] = []
        if corrupt_entry:
            issues.append(f"CRC failure: {corrupt_entry}")
        if duplicate_entries:
            issues.append(f"duplicate entries: {duplicate_entries[:10]}")
        if unsafe_entries:
            issues.append(f"unsafe entries: {unsafe_entries[:10]}")
        if nested_zips:
            issues.append(f"nested ZIP entries: {nested_zips[:10]}")
        if not flat_root and roots != [source_dir.name]:
            issues.append(f"expected one root {source_dir.name!r}, found {roots}")
        if len(names) != len(files):
            issues.append(f"entry count mismatch: expected {len(files)}, found {len(names)}")
        if issues:
            raise ValueError("; ".join(issues))

        os.replace(temporary_path, output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    return {
        "source_dir": source_dir.as_posix(),
        "output_zip": output_path.as_posix(),
        "entries": len(files),
        "archive_layout": "flat" if flat_root else "single_root",
        "flat_root": flat_root,
        "single_root": None if flat_root else source_dir.name,
        "nested_zip_entries": 0,
        "unsafe_entries": 0,
        "duplicate_entries": 0,
        "crc_valid": True,
        "size_bytes": output_path.stat().st_size,
        "sha256": sha256_file(output_path),
        "valid": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument(
        "--flat-root",
        action="store_true",
        help="Store source contents at ZIP root instead of under the source folder name.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = rebuild_zip(args.source_dir, args.output, flat_root=args.flat_root)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
