#!/usr/bin/env python3
"""Verify standalone Eng_Bench review-batch folders and ZIPs."""
from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import Counter
from io import TextIOWrapper
from pathlib import Path
from typing import Any

try:
    from . import verify_handoff_package
except ImportError:  # Direct script execution from tools/.
    import verify_handoff_package


REQUIRED_BATCH_FILES = (
    "README.md",
    "HUMAN_REVIEW_STEPS.md",
    "NEXT_REVIEW_BATCH_MANIFEST.csv",
    "next_review_batch_build_report.json",
    "next_review_batch_build_report.md",
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_manifest_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def int_field(row: dict[str, str], key: str) -> int:
    try:
        return int(str(row.get(key) or "0"))
    except ValueError:
        return 0


def verify_batch_dir(batch_dir: Path) -> dict[str, Any]:
    issues: list[str] = []
    for name in REQUIRED_BATCH_FILES:
        if not (batch_dir / name).exists():
            issues.append(f"missing {name}")
    review_root = batch_dir / "review_packs"
    if not review_root.exists():
        issues.append("missing review_packs")

    manifest_rows = read_manifest_csv(batch_dir / "NEXT_REVIEW_BATCH_MANIFEST.csv")
    packs: list[dict[str, Any]] = []
    for row in manifest_rows:
        pack_name = str(row.get("pack_name") or "").strip()
        if not pack_name:
            issues.append("manifest row missing pack_name")
            continue
        pack_dir = review_root / pack_name
        if not pack_dir.exists():
            issues.append(f"{pack_name}: missing review pack folder")
            continue
        pack_report = verify_handoff_package.verify_pack_dir(pack_dir)
        expected_rows = int_field(row, "rows")
        expected_checklist = str(row.get("checklist") or "").strip()
        if pack_report["manifest_rows"] != expected_rows:
            pack_report["issues"].append(
                f"manifest row count mismatch: expected {expected_rows}, found {pack_report['manifest_rows']}"
            )
        if pack_report["checklist_rows"] != expected_rows:
            pack_report["issues"].append(
                f"checklist row count mismatch: expected {expected_rows}, found {pack_report['checklist_rows']}"
            )
        if expected_checklist and expected_checklist not in pack_report["checklists"]:
            pack_report["issues"].append(f"missing listed checklist {expected_checklist}")
        pack_report["valid"] = not pack_report["issues"]
        packs.append(pack_report)
        issues.extend(f"{pack_name}: {issue}" for issue in pack_report["issues"])

    totals = Counter()
    for pack in packs:
        totals["manifest_rows"] += int(pack["manifest_rows"])
        totals["checklist_rows"] += int(pack["checklist_rows"])
        totals["crop_files"] += int(pack["crop_files"])
        totals["page_files"] += int(pack["page_files"])
    return {
        "batch_dir": batch_dir.as_posix(),
        "manifest_rows": len(manifest_rows),
        "packs": packs,
        "totals": dict(totals),
        "issues": issues,
        "valid": not issues,
    }


def zip_single_root(entries: set[str]) -> str:
    roots = sorted({entry.split("/", 1)[0] for entry in entries if "/" in entry})
    return roots[0] if len(roots) == 1 else ""


def csv_rows_from_zip(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with zf.open(name) as f:
        return list(csv.DictReader(TextIOWrapper(f, encoding="utf-8-sig")))


def jsonl_rows_from_zip(zf: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    with zf.open(name) as f:
        return verify_handoff_package.read_jsonl_lines(TextIOWrapper(f, encoding="utf-8"))


def verify_zip_pack(
    zf: zipfile.ZipFile,
    entries: set[str],
    batch_root: str,
    manifest_row: dict[str, str],
) -> dict[str, Any]:
    pack_name = str(manifest_row.get("pack_name") or "").strip()
    expected_rows = int_field(manifest_row, "rows")
    expected_checklist = str(manifest_row.get("checklist") or "").strip()
    pack_prefix = f"{batch_root}/review_packs/{pack_name}/"
    manifest_name = f"{pack_prefix}manifest.jsonl"
    checklist_name = f"{pack_prefix}{expected_checklist}" if expected_checklist else ""
    issues: list[str] = []

    manifest_rows: list[dict[str, Any]] = []
    if manifest_name in entries:
        manifest_rows = jsonl_rows_from_zip(zf, manifest_name)
    else:
        issues.append("missing manifest.jsonl")
    checklist_rows: list[dict[str, str]] = []
    if checklist_name and checklist_name in entries:
        checklist_rows = csv_rows_from_zip(zf, checklist_name)
    else:
        issues.append(f"missing listed checklist {expected_checklist or '<blank>'}")

    crop_files = sum(
        1
        for entry in entries
        if any(entry.startswith(f"{pack_prefix}{folder}/") for folder in ("crops", "panels"))
        and entry.endswith(".png")
    )
    page_files = sum(
        1
        for entry in entries
        if any(entry.startswith(f"{pack_prefix}{folder}/") for folder in ("pages", "pages_old", "pages_new"))
        and entry.endswith(".png")
    )
    if len(manifest_rows) != expected_rows:
        issues.append(f"manifest row count mismatch: expected {expected_rows}, found {len(manifest_rows)}")
    if len(checklist_rows) != expected_rows:
        issues.append(f"checklist row count mismatch: expected {expected_rows}, found {len(checklist_rows)}")
    if manifest_rows and crop_files == 0:
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
            value = str(row.get(field) or "").replace("\\", "/").strip()
            if value and f"{pack_prefix}{folder}/{Path(value).name}" not in entries:
                manifest_missing_evidence += 1
    if manifest_missing_evidence:
        issues.append(f"manifest evidence paths missing: {manifest_missing_evidence}")

    checklist_nonlocal = 0
    checklist_missing = 0
    for row in checklist_rows:
        for field in evidence_folders:
            value = str(row.get(field) or "").replace("\\", "/").strip()
            if not value:
                continue
            if value.startswith("derived/") or value.startswith("images/"):
                checklist_nonlocal += 1
            if f"{pack_prefix}{value}" not in entries:
                checklist_missing += 1
    if checklist_nonlocal:
        issues.append(f"checklist uses nonlocal evidence paths: {checklist_nonlocal}")
    if checklist_missing:
        issues.append(f"checklist local evidence paths missing: {checklist_missing}")

    return {
        "pack": pack_name,
        "manifest_rows": len(manifest_rows),
        "checklist_rows": len(checklist_rows),
        "checklists": [Path(checklist_name).name] if checklist_name else [],
        "crop_files": crop_files,
        "page_files": page_files,
        "issues": issues,
        "valid": not issues,
    }


def verify_batch_zip(zip_path: Path) -> dict[str, Any]:
    if not zip_path.exists():
        return {"zip_path": zip_path.as_posix(), "issues": ["missing zip"], "valid": False}
    issues: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        entries = {name.replace("\\", "/") for name in zf.namelist()}
        batch_root = zip_single_root(entries)
        if not batch_root:
            return {
                "zip_path": zip_path.as_posix(),
                "issues": ["could not determine single batch root"],
                "valid": False,
            }
        for name in REQUIRED_BATCH_FILES:
            if f"{batch_root}/{name}" not in entries:
                issues.append(f"missing {name}")
        manifest_name = f"{batch_root}/NEXT_REVIEW_BATCH_MANIFEST.csv"
        manifest_rows = csv_rows_from_zip(zf, manifest_name) if manifest_name in entries else []
        packs = [
            verify_zip_pack(zf, entries, batch_root, row)
            for row in manifest_rows
            if str(row.get("pack_name") or "").strip()
        ]
        issues.extend(f"{pack['pack']}: {issue}" for pack in packs for issue in pack["issues"])

    totals = Counter()
    for pack in packs:
        totals["manifest_rows"] += int(pack["manifest_rows"])
        totals["checklist_rows"] += int(pack["checklist_rows"])
        totals["crop_files"] += int(pack["crop_files"])
        totals["page_files"] += int(pack["page_files"])
    return {
        "zip_path": zip_path.as_posix(),
        "batch_root": batch_root,
        "entry_count": len(entries),
        "manifest_rows": len(manifest_rows),
        "packs": packs,
        "totals": dict(totals),
        "issues": issues,
        "valid": not issues,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Review Batch Package Verification",
        "",
        f"- Valid: `{str(report.get('valid', False)).lower()}`",
        f"- Issues: `{len(report.get('issues', []))}`",
    ]
    if "zip_path" in report:
        lines.append(f"- ZIP: `{report['zip_path']}`")
        lines.append(f"- Entries: `{report.get('entry_count', 0)}`")
    if "batch_dir" in report:
        lines.append(f"- Folder: `{report['batch_dir']}`")
    totals = report.get("totals", {})
    lines.extend(
        [
            f"- Manifest rows: `{report.get('manifest_rows', 0)}`",
            f"- Pack manifest rows: `{totals.get('manifest_rows', 0)}`",
            f"- Checklist rows: `{totals.get('checklist_rows', 0)}`",
            f"- Crop files: `{totals.get('crop_files', 0)}`",
            f"- Page files: `{totals.get('page_files', 0)}`",
            "",
            "## Packs",
            "",
            "| Pack | Manifest Rows | Checklist Rows | Crops | Pages | Valid |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for pack in report.get("packs", []):
        lines.append(
            f"| `{pack['pack']}` | {pack['manifest_rows']} | {pack['checklist_rows']} | "
            f"{pack['crop_files']} | {pack['page_files']} | {str(pack['valid']).lower()} |"
        )
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an Eng_Bench standalone review batch folder or ZIP.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--batch-dir", help="Review batch folder to verify")
    target.add_argument("--zip", dest="zip_path", help="Review batch ZIP to verify")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = (
        verify_batch_zip(Path(args.zip_path))
        if args.zip_path
        else verify_batch_dir(Path(args.batch_dir))
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
