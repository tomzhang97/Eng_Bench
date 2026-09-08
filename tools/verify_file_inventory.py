#!/usr/bin/env python3
"""Verify a directory against a deterministic SHA-256 file inventory."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from tools.build_file_inventory import sha256_file
except ModuleNotFoundError:
    from build_file_inventory import sha256_file


def safe_relative_path(value: str) -> PurePosixPath | None:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        return None
    if path.parts and ":" in path.parts[0]:
        return None
    return path


def verify_inventory(root: Path, inventory: Path, require_exact: bool = True) -> dict[str, Any]:
    root = root.resolve()
    inventory = inventory.resolve()
    issues: list[str] = []
    missing: list[str] = []
    size_mismatches: list[str] = []
    hash_mismatches: list[str] = []
    unsafe_paths: list[str] = []
    duplicate_paths: list[str] = []
    expected: set[str] = set()

    with inventory.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        raw_path = str(row.get("relative_path") or "")
        relative = safe_relative_path(raw_path)
        if relative is None:
            unsafe_paths.append(raw_path)
            continue
        key = relative.as_posix()
        if key in expected:
            duplicate_paths.append(key)
            continue
        expected.add(key)
        path = root.joinpath(*relative.parts)
        if not path.is_file():
            missing.append(key)
            continue
        try:
            expected_size = int(str(row.get("size_bytes") or ""))
        except ValueError:
            size_mismatches.append(key)
            continue
        if path.stat().st_size != expected_size:
            size_mismatches.append(key)
            continue
        if sha256_file(path) != str(row.get("sha256") or "").strip().upper():
            hash_mismatches.append(key)

    inventory_relative = inventory.relative_to(root).as_posix()
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.resolve() != inventory
    }
    extras = sorted(actual - expected) if require_exact else []
    if unsafe_paths:
        issues.append("unsafe_inventory_paths")
    if duplicate_paths:
        issues.append("duplicate_inventory_paths")
    if missing:
        issues.append("missing_files")
    if size_mismatches:
        issues.append("size_mismatches")
    if hash_mismatches:
        issues.append("hash_mismatches")
    if extras:
        issues.append("unlisted_files")

    return {
        "valid": not issues,
        "root": root.as_posix(),
        "inventory": inventory_relative,
        "inventory_rows": len(rows),
        "unique_inventory_paths": len(expected),
        "actual_files_excluding_inventory": len(actual),
        "require_exact": require_exact,
        "issues": issues,
        "unsafe_paths": sorted(unsafe_paths),
        "duplicate_paths": sorted(set(duplicate_paths)),
        "missing_files": sorted(missing),
        "size_mismatches": sorted(size_mismatches),
        "hash_mismatches": sorted(hash_mismatches),
        "unlisted_files": extras,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# File Inventory Verification",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Inventory rows: `{report['inventory_rows']}`",
        f"- Unique inventory paths: `{report['unique_inventory_paths']}`",
        f"- Actual files excluding inventory: `{report['actual_files_excluding_inventory']}`",
        f"- Missing files: `{len(report['missing_files'])}`",
        f"- Size mismatches: `{len(report['size_mismatches'])}`",
        f"- Hash mismatches: `{len(report['hash_mismatches'])}`",
        f"- Unlisted files: `{len(report['unlisted_files'])}`",
        f"- Unsafe paths: `{len(report['unsafe_paths'])}`",
        f"- Duplicate paths: `{len(report['duplicate_paths'])}`",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--allow-unlisted", action="store_true")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args(argv)

    report = verify_inventory(args.root, args.inventory, require_exact=not args.allow_unlisted)
    if args.output_json:
        write_report(args.output_json, report)
    if args.output_md:
        write_markdown(args.output_md, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
