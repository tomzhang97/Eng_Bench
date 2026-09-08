#!/usr/bin/env python3
"""Verify compatibility-first Eng_Bench human handoff folders and ZIPs."""
from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_ROOT_FILES = ("README_FIRST.md", "PACKET_WORKLIST.csv", "PACK_MAP.csv", "PATH_MAP.csv")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_rel_path(value: str) -> str:
    rel = value.replace("\\", "/").strip().strip("/")
    if rel.startswith("EB_HV_0615/"):
        rel = rel.removeprefix("EB_HV_0615/")
    return rel


def count_files(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file())


def verify_handoff_dir(handoff_dir: Path, max_path_length: int) -> dict[str, Any]:
    issues: list[str] = []
    if not handoff_dir.exists():
        return {"path": handoff_dir.as_posix(), "valid": False, "issues": ["missing handoff directory"]}

    for name in REQUIRED_ROOT_FILES:
        if not (handoff_dir / name).exists():
            issues.append(f"missing {name}")

    nested_zips = sorted(path.relative_to(handoff_dir).as_posix() for path in handoff_dir.rglob("*.zip"))
    if nested_zips:
        issues.append(f"nested zip files present: {len(nested_zips)}")

    all_files = sorted(path for path in handoff_dir.rglob("*") if path.is_file())
    max_rel_path = max((path.relative_to(handoff_dir).as_posix() for path in all_files), key=len, default="")
    if len(max_rel_path) > max_path_length:
        issues.append(f"max relative path length {len(max_rel_path)} exceeds {max_path_length}: {max_rel_path}")

    packet_root = handoff_dir / "01_packets"
    packet_dirs = sorted(path for path in packet_root.iterdir() if path.is_dir()) if packet_root.exists() else []
    if not packet_dirs:
        issues.append("missing packet directories under 01_packets")

    worklist_rows = read_csv_rows(handoff_dir / "PACKET_WORKLIST.csv")
    missing_worklist_paths: list[str] = []
    review_rows = 0
    for row in worklist_rows:
        review_rows += safe_int(row.get("review_rows"))
        rel = normalize_rel_path(row.get("folder") or row.get("assigned_path") or "")
        if rel and not (handoff_dir / rel).exists():
            missing_worklist_paths.append(rel)
    if missing_worklist_paths:
        sample = ", ".join(missing_worklist_paths[:5])
        issues.append(f"worklist paths missing: {len(missing_worklist_paths)} ({sample})")

    path_map_rows = read_csv_rows(handoff_dir / "PATH_MAP.csv")
    missing_map_paths: list[str] = []
    for row in path_map_rows:
        rel = normalize_rel_path(row.get("compat_relative_path") or row.get("compat_path") or "")
        if rel and not (handoff_dir / rel).exists():
            missing_map_paths.append(rel)
    if missing_map_paths:
        sample = ", ".join(missing_map_paths[:5])
        issues.append(f"path-map compat paths missing: {len(missing_map_paths)} ({sample})")

    packet_summaries = []
    totals = Counter()
    for packet_dir in packet_dirs:
        checklist_rows = 0
        checklist_files = sorted(packet_dir.rglob("*checklist*.csv"))
        for checklist in checklist_files:
            checklist_rows += len(read_csv_rows(checklist))
        png_files = sorted(packet_dir.rglob("*.png"))
        manifest_files = sorted(packet_dir.rglob("manifest.jsonl"))
        packet_file_count = count_files(packet_dir)
        packet_issues: list[str] = []
        if packet_file_count == 0:
            packet_issues.append("empty packet folder")
        if not checklist_files:
            packet_issues.append("no checklist csv")
        if not png_files:
            packet_issues.append("no png evidence files")
        issues.extend(f"{packet_dir.name}: {issue}" for issue in packet_issues)
        packet_summaries.append(
            {
                "packet": packet_dir.name,
                "files": packet_file_count,
                "checklist_files": len(checklist_files),
                "checklist_rows": checklist_rows,
                "manifest_files": len(manifest_files),
                "png_files": len(png_files),
                "issues": packet_issues,
                "valid": not packet_issues,
            }
        )
        totals["packet_files"] += packet_file_count
        totals["checklist_files"] += len(checklist_files)
        totals["checklist_rows"] += checklist_rows
        totals["manifest_files"] += len(manifest_files)
        totals["png_files"] += len(png_files)

    agreement_dir = handoff_dir / "02_agreement"
    agreement_files = count_files(agreement_dir) if agreement_dir.exists() else 0

    return {
        "path": handoff_dir.as_posix(),
        "valid": not issues,
        "issues": issues,
        "files": len(all_files),
        "nested_zip_files": len(nested_zips),
        "max_relative_path_length": len(max_rel_path),
        "max_relative_path": max_rel_path,
        "packet_folders": len(packet_dirs),
        "worklist_rows": len(worklist_rows),
        "worklist_review_rows": review_rows,
        "path_map_rows": len(path_map_rows),
        "pack_map_rows": len(read_csv_rows(handoff_dir / "PACK_MAP.csv")),
        "agreement_files": agreement_files,
        "packets": packet_summaries,
        "totals": dict(totals),
    }


def verify_zip(zip_path: Path, max_path_length: int) -> dict[str, Any]:
    issues: list[str] = []
    if not zip_path.exists():
        return {"path": zip_path.as_posix(), "valid": False, "issues": ["missing zip"]}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad_entry = zf.testzip()
            names = zf.namelist()
            nested_zips = [name for name in names if name.lower().endswith(".zip")]
            max_name = max(names, key=len, default="")
            roots = sorted({name.split("/", 1)[0] for name in names if "/" in name})
            has_readme = any(name.endswith("/README_FIRST.md") or name == "README_FIRST.md" for name in names)
    except zipfile.BadZipFile as exc:
        return {"path": zip_path.as_posix(), "valid": False, "issues": [f"bad zip: {exc}"]}

    if bad_entry:
        issues.append(f"crc failure: {bad_entry}")
    if nested_zips:
        issues.append(f"nested zip entries present: {len(nested_zips)}")
    if len(max_name) > max_path_length:
        issues.append(f"max internal path length {len(max_name)} exceeds {max_path_length}: {max_name}")
    if not has_readme:
        issues.append("missing README_FIRST.md")
    if not roots:
        issues.append("could not determine archive root")

    return {
        "path": zip_path.as_posix(),
        "valid": not issues,
        "issues": issues,
        "entries": len(names),
        "bytes": zip_path.stat().st_size,
        "crc_ok": bad_entry is None,
        "bad_entry": bad_entry or "",
        "nested_zip_entries": len(nested_zips),
        "max_internal_path_length": len(max_name),
        "max_internal_path": max_name,
        "roots": roots,
        "has_readme": has_readme,
    }


def verify_split_dir(split_dir: Path, max_path_length: int) -> dict[str, Any]:
    issues: list[str] = []
    if not split_dir:
        return {"path": "", "valid": True, "issues": [], "zips": []}
    if not split_dir.exists():
        return {"path": split_dir.as_posix(), "valid": False, "issues": ["missing split directory"], "zips": []}
    zip_paths = sorted(split_dir.glob("*.zip"))
    if not zip_paths:
        issues.append("no split zip files")
    manifest_rows = read_csv_rows(split_dir / "SPLIT_ZIP_MANIFEST.csv")
    manifest_names = {row.get("zip", "") for row in manifest_rows}
    zip_names = {path.name for path in zip_paths}
    if manifest_rows and manifest_names != zip_names:
        issues.append(
            "split manifest zip set mismatch: "
            f"manifest_only={sorted(manifest_names - zip_names)}, disk_only={sorted(zip_names - manifest_names)}"
        )
    zip_reports = [verify_zip(path, max_path_length) for path in zip_paths]
    issues.extend(f"{Path(report['path']).name}: {issue}" for report in zip_reports for issue in report["issues"])
    return {
        "path": split_dir.as_posix(),
        "valid": not issues,
        "issues": issues,
        "zip_count": len(zip_paths),
        "manifest_rows": len(manifest_rows),
        "zips": zip_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--handoff-dir", default="")
    parser.add_argument("--zip", default="")
    parser.add_argument("--split-dir", default="")
    parser.add_argument("--max-path-length", type=int, default=160)
    parser.add_argument("--report", default="derived/quality/compatible_handoff_verification_latest.json")
    args = parser.parse_args()

    root = Path(args.root)
    reports: dict[str, Any] = {"max_path_length": args.max_path_length}
    if args.handoff_dir:
        reports["handoff_dir"] = verify_handoff_dir(root / args.handoff_dir, args.max_path_length)
    if args.zip:
        reports["zip"] = verify_zip(root / args.zip, args.max_path_length)
    if args.split_dir:
        reports["split_dir"] = verify_split_dir(root / args.split_dir, args.max_path_length)

    component_reports = [value for key, value in reports.items() if isinstance(value, dict) and key != "max_path_length"]
    reports["valid"] = all(report.get("valid") for report in component_reports) if component_reports else False
    write_json(root / args.report, reports)
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 0 if reports["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
