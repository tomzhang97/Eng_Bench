#!/usr/bin/env python3
"""Verify human handoff package structure and review-pack evidence files."""
from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import Counter
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Iterable


REQUIRED_TOP_LEVEL = ("README.md", "HUMAN_REVIEW_ORDER.md")
REQUIRED_STATUS_FILES = (
    "GOLD_EXPANSION_HUMAN_QUEUE.csv",
    "GOLD_EXPANSION_MACHINE_QUEUE.csv",
    "SOURCE_CONVERSION_LOCAL_QUEUE.csv",
    "SOURCE_CONVERSION_CANDIDATE_QUEUE.csv",
)


def read_jsonl_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def has_dir_with_prefix(entries: set[str], prefix: str) -> bool:
    normalized = prefix.rstrip("/") + "/"
    return any(entry.startswith(normalized) for entry in entries)


def local_path_exists(pack_root: Path, value: str) -> bool:
    if not value:
        return False
    path = Path(value)
    if path.is_absolute():
        return path.exists()
    return (pack_root / path).exists()


def basename_path_exists(pack_root: Path, folder: str, value: str) -> bool:
    if not value:
        return False
    name = Path(value.replace("\\", "/")).name
    return bool(name) and (pack_root / folder / name).exists()


def verify_pack_dir(pack_root: Path) -> dict[str, Any]:
    issues: list[str] = []
    manifest_path = pack_root / "manifest.jsonl"
    manifest_rows = read_jsonl_lines(manifest_path.read_text(encoding="utf-8").splitlines()) if manifest_path.exists() else []
    checklist_paths = sorted(pack_root.glob("*checklist*.csv"))
    crop_files = []
    for folder in ("crops", "panels"):
        crop_files.extend(sorted((pack_root / folder).glob("*.png")) if (pack_root / folder).exists() else [])
    page_files = []
    for folder in ("pages", "pages_old", "pages_new"):
        page_files.extend(sorted((pack_root / folder).glob("*.png")) if (pack_root / folder).exists() else [])

    if not manifest_path.exists():
        issues.append("missing manifest.jsonl")
    if not checklist_paths:
        issues.append("missing checklist csv")
    if manifest_rows and not crop_files:
        issues.append("missing crops directory/files")

    manifest_missing_evidence = 0
    evidence_folders = {
        "crop_path": "crops",
        "page_path": "pages",
        "old_crop_path": "old",
        "new_crop_path": "new",
        "panel_path": "panels",
        "old_page_path": "pages_old",
        "new_page_path": "pages_new",
    }
    for row in manifest_rows:
        for field, folder in evidence_folders.items():
            value = str(row.get(field) or "")
            if value and not (
                local_path_exists(pack_root, value) or basename_path_exists(pack_root, folder, value)
            ):
                manifest_missing_evidence += 1
    if manifest_missing_evidence:
        issues.append(f"manifest evidence paths missing: {manifest_missing_evidence}")

    checklist_rows = 0
    checklist_missing_local_evidence = 0
    checklist_nonlocal_evidence = 0
    for checklist_path in checklist_paths:
        for row in read_csv_rows(checklist_path):
            checklist_rows += 1
            for field in evidence_folders:
                value = str(row.get(field) or "").replace("\\", "/").strip()
                if not value:
                    continue
                if value.startswith("derived/") or value.startswith("images/"):
                    checklist_nonlocal_evidence += 1
                if not local_path_exists(pack_root, value):
                    checklist_missing_local_evidence += 1
    if checklist_nonlocal_evidence:
        issues.append(f"checklist uses nonlocal evidence paths: {checklist_nonlocal_evidence}")
    if checklist_missing_local_evidence:
        issues.append(f"checklist local evidence paths missing: {checklist_missing_local_evidence}")

    return {
        "pack": pack_root.as_posix(),
        "manifest_rows": len(manifest_rows),
        "checklist_rows": checklist_rows,
        "checklists": [path.name for path in checklist_paths],
        "crop_files": len(crop_files),
        "page_files": len(page_files),
        "issues": issues,
        "valid": not issues,
    }


def verify_handoff_dir(handoff_dir: Path) -> dict[str, Any]:
    issues: list[str] = []
    for name in REQUIRED_TOP_LEVEL:
        if not (handoff_dir / name).exists():
            issues.append(f"missing {name}")
    if not (handoff_dir / "01_current_5_25_packet").exists():
        issues.append("missing 01_current_5_25_packet")
    status_dir = handoff_dir / "02_status_and_queues"
    for name in REQUIRED_STATUS_FILES:
        if not (status_dir / name).exists():
            issues.append(f"missing 02_status_and_queues/{name}")

    extra_root = handoff_dir / "03_new_machine_review_packs"
    packs = [verify_pack_dir(path) for path in sorted(extra_root.iterdir()) if path.is_dir()] if extra_root.exists() else []
    issues.extend(
        f"{Path(pack['pack']).name}: {issue}"
        for pack in packs
        for issue in pack["issues"]
    )
    totals = Counter()
    for pack in packs:
        totals["manifest_rows"] += int(pack["manifest_rows"])
        totals["checklist_rows"] += int(pack["checklist_rows"])
        totals["crop_files"] += int(pack["crop_files"])
        totals["page_files"] += int(pack["page_files"])
    return {
        "handoff_dir": handoff_dir.as_posix(),
        "extra_packs": packs,
        "totals": dict(totals),
        "issues": issues,
        "valid": not issues,
    }


def verify_handoff_zip(zip_path: Path) -> dict[str, Any]:
    issues: list[str] = []
    if not zip_path.exists():
        return {"zip_path": zip_path.as_posix(), "issues": ["missing zip"], "valid": False}
    with zipfile.ZipFile(zip_path, "r") as zf:
        entries = {name.replace("\\", "/") for name in zf.namelist()}
        roots = sorted({entry.split("/", 1)[0] for entry in entries if "/" in entry})
        handoff_root = roots[0] if len(roots) == 1 else ""
        if not handoff_root:
            issues.append("could not determine single handoff root")
            return {"zip_path": zip_path.as_posix(), "issues": issues, "valid": False}
        for name in REQUIRED_TOP_LEVEL:
            if f"{handoff_root}/{name}" not in entries:
                issues.append(f"missing {name}")
        if not has_dir_with_prefix(entries, f"{handoff_root}/01_current_5_25_packet"):
            issues.append("missing 01_current_5_25_packet")
        for name in REQUIRED_STATUS_FILES:
            if f"{handoff_root}/02_status_and_queues/{name}" not in entries:
                issues.append(f"missing 02_status_and_queues/{name}")
        extra_prefix = f"{handoff_root}/03_new_machine_review_packs/"
        pack_names = sorted(
            {
                entry.removeprefix(extra_prefix).split("/", 1)[0]
                for entry in entries
                if entry.startswith(extra_prefix) and "/" in entry.removeprefix(extra_prefix)
            }
        )
        packs: list[dict[str, Any]] = []
        for pack_name in pack_names:
            pack_prefix = f"{extra_prefix}{pack_name}/"
            manifest_name = f"{pack_prefix}manifest.jsonl"
            checklist_names = sorted(
                entry for entry in entries if entry.startswith(pack_prefix) and "checklist" in Path(entry).name and entry.endswith(".csv")
            )
            crop_count = sum(
                1
                for entry in entries
                if any(entry.startswith(f"{pack_prefix}{folder}/") for folder in ("crops", "panels"))
                and entry.endswith(".png")
            )
            page_count = sum(
                1
                for entry in entries
                if any(entry.startswith(f"{pack_prefix}{folder}/") for folder in ("pages", "pages_old", "pages_new"))
                and entry.endswith(".png")
            )
            pack_issues: list[str] = []
            manifest_rows: list[dict[str, Any]] = []
            if manifest_name in entries:
                with zf.open(manifest_name) as f:
                    manifest_rows = read_jsonl_lines(TextIOWrapper(f, encoding="utf-8"))
            else:
                pack_issues.append("missing manifest.jsonl")
            if not checklist_names:
                pack_issues.append("missing checklist csv")
            if manifest_rows and crop_count == 0:
                pack_issues.append("missing crops directory/files")
            checklist_rows = 0
            checklist_nonlocal = 0
            checklist_missing = 0
            for checklist_name in checklist_names:
                with zf.open(checklist_name) as f:
                    for row in csv.DictReader(TextIOWrapper(f, encoding="utf-8-sig")):
                        checklist_rows += 1
                        for field in (
                            "crop_path",
                            "page_path",
                            "old_crop_path",
                            "new_crop_path",
                            "panel_path",
                            "old_page_path",
                            "new_page_path",
                        ):
                            value = str(row.get(field) or "").replace("\\", "/").strip()
                            if not value:
                                continue
                            if value.startswith("derived/") or value.startswith("images/"):
                                checklist_nonlocal += 1
                            if f"{pack_prefix}{value}" not in entries:
                                checklist_missing += 1
            if checklist_nonlocal:
                pack_issues.append(f"checklist uses nonlocal evidence paths: {checklist_nonlocal}")
            if checklist_missing:
                pack_issues.append(f"checklist local evidence paths missing: {checklist_missing}")
            packs.append(
                {
                    "pack": pack_name,
                    "manifest_rows": len(manifest_rows),
                    "checklist_rows": checklist_rows,
                    "checklists": [Path(name).name for name in checklist_names],
                    "crop_files": crop_count,
                    "page_files": page_count,
                    "issues": pack_issues,
                    "valid": not pack_issues,
                }
            )
            issues.extend(f"{pack_name}: {issue}" for issue in pack_issues)
    totals = Counter()
    for pack in packs:
        totals["manifest_rows"] += int(pack["manifest_rows"])
        totals["checklist_rows"] += int(pack["checklist_rows"])
        totals["crop_files"] += int(pack["crop_files"])
        totals["page_files"] += int(pack["page_files"])
    return {
        "zip_path": zip_path.as_posix(),
        "handoff_root": handoff_root,
        "entry_count": len(entries),
        "extra_packs": packs,
        "totals": dict(totals),
        "issues": issues,
        "valid": not issues,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Human Handoff Verification",
        "",
        f"- Valid: `{str(report.get('valid', False)).lower()}`",
        f"- Issues: `{len(report.get('issues', []))}`",
    ]
    if "zip_path" in report:
        lines.append(f"- ZIP: `{report['zip_path']}`")
        lines.append(f"- Entries: `{report.get('entry_count', 0)}`")
    if "handoff_dir" in report:
        lines.append(f"- Folder: `{report['handoff_dir']}`")
    totals = report.get("totals", {})
    lines.extend(
        [
            f"- Extra-pack manifest rows: `{totals.get('manifest_rows', 0)}`",
            f"- Extra-pack checklist rows: `{totals.get('checklist_rows', 0)}`",
            f"- Extra-pack crop files: `{totals.get('crop_files', 0)}`",
            f"- Extra-pack page files: `{totals.get('page_files', 0)}`",
            "",
            "## Extra Packs",
            "",
            "| Pack | Manifest Rows | Checklist Rows | Crops | Pages | Valid |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for pack in report.get("extra_packs", []):
        lines.append(
            f"| `{Path(pack['pack']).name}` | {pack['manifest_rows']} | {pack['checklist_rows']} | "
            f"{pack['crop_files']} | {pack['page_files']} | {str(pack['valid']).lower()} |"
        )
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an Eng_Bench human handoff folder or ZIP.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--handoff-dir", help="Handoff folder to verify")
    target.add_argument("--zip", dest="zip_path", help="Handoff ZIP to verify")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = (
        verify_handoff_zip(Path(args.zip_path))
        if args.zip_path
        else verify_handoff_dir(Path(args.handoff_dir))
    )
    if args.output_json:
        write_json(Path(args.output_json), report)
        print(f"[OK] Wrote {args.output_json}")
    if args.output_md:
        path = Path(args.output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
        print(f"[OK] Wrote {args.output_md}")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
