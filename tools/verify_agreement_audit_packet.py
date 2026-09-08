#!/usr/bin/env python3
"""Verify Eng_Bench human-agreement audit folders and ZIPs."""
from __future__ import annotations

import argparse
import csv
import json
import zipfile
from io import TextIOWrapper
from pathlib import Path
from typing import Any


REQUIRED_FILES = (
    "README.md",
    "HUMAN_REVIEW_STEPS.md",
    "index.html",
    "sample_reference.csv",
    "reviewer_a_checklist.csv",
    "reviewer_b_checklist.csv",
    "agreement_packet_build_report.json",
    "INTERN_INSTRUCTIONS_ZH.md",
)
EVIDENCE_FIELDS = ("primary_evidence_path", "page_path", "old_page_path", "new_page_path")
PROVENANCE_FIELDS = ("doc_id", "source_doc_ids", "source_url", "source_status")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def zip_csv(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with zf.open(name) as f:
        return list(csv.DictReader(TextIOWrapper(f, encoding="utf-8-sig")))


def sheet_ids(rows: list[dict[str, str]]) -> list[str]:
    return [str(row.get("id") or "").strip() for row in rows]


def evidence_values(rows: list[dict[str, str]]) -> list[str]:
    return [
        str(row.get(field) or "").replace("\\", "/").strip()
        for row in rows
        for field in EVIDENCE_FIELDS
        if str(row.get(field) or "").strip()
    ]


def base_report(
    reference: list[dict[str, str]],
    reviewer_a: list[dict[str, str]],
    reviewer_b: list[dict[str, str]],
    missing_required_files: list[str],
    missing_evidence_refs: int,
    build_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues = [f"missing {name}" for name in missing_required_files]
    ids = sheet_ids(reference)
    if len(ids) != len(set(ids)):
        issues.append("duplicate IDs in sample_reference.csv")
    if sheet_ids(reviewer_a) != ids:
        issues.append("reviewer_a IDs/order differ from sample_reference.csv")
    if sheet_ids(reviewer_b) != ids:
        issues.append("reviewer_b IDs/order differ from sample_reference.csv")
    if missing_evidence_refs:
        issues.append(f"missing evidence refs: {missing_evidence_refs}")
    missing_provenance_values = sum(
        not str(row.get(field) or "").strip() for row in reference for field in PROVENANCE_FIELDS
    )
    if missing_provenance_values:
        issues.append(f"missing provenance values: {missing_provenance_values}")
    release_claim_present = bool(
        reference and "source_release_ready" in reference[0]
    )
    non_release_ready_rows = (
        sum(
            str(row.get("source_release_ready") or "").strip().lower() != "true"
            for row in reference
        )
        if release_claim_present
        else 0
    )
    source_blocker_rows = sum(
        bool(str(row.get("source_blockers") or "").strip()) for row in reference
    )
    if release_claim_present and non_release_ready_rows:
        issues.append(f"non-release-ready reference rows: {non_release_ready_rows}")
    if release_claim_present and source_blocker_rows:
        issues.append(f"reference rows with source blockers: {source_blocker_rows}")
    build_report = build_report or {}
    release_filter_required = bool(build_report.get("release_ready_filter_required"))
    if release_filter_required:
        if not build_report.get("release_ready_filter_enabled"):
            issues.append("build report requires release filtering but it was not enabled")
        if not release_claim_present:
            issues.append("release-filtered packet lacks source_release_ready fields")
        if build_report.get("selected_release_ready_rows") != len(reference):
            issues.append("build report release-ready row count does not match reference")
    return {
        "sample_rows": len(reference),
        "reviewer_a_rows": len(reviewer_a),
        "reviewer_b_rows": len(reviewer_b),
        "missing_required_files": missing_required_files,
        "missing_evidence_refs": missing_evidence_refs,
        "missing_provenance_values": missing_provenance_values,
        "release_claim_present": release_claim_present,
        "non_release_ready_rows": non_release_ready_rows,
        "source_blocker_rows": source_blocker_rows,
        "release_filter_required": release_filter_required,
        "reviewer_sheets_identical": reviewer_a == reviewer_b,
        "issues": issues,
        "valid": not issues and bool(reference),
    }


def verify_dir(packet_dir: Path) -> dict[str, Any]:
    missing = [name for name in REQUIRED_FILES if not (packet_dir / name).exists()]
    reference = read_csv(packet_dir / "sample_reference.csv") if not missing or (packet_dir / "sample_reference.csv").exists() else []
    reviewer_a = read_csv(packet_dir / "reviewer_a_checklist.csv") if (packet_dir / "reviewer_a_checklist.csv").exists() else []
    reviewer_b = read_csv(packet_dir / "reviewer_b_checklist.csv") if (packet_dir / "reviewer_b_checklist.csv").exists() else []
    missing_evidence = sum(not (packet_dir / value).exists() for value in evidence_values(reference))
    build_report_path = packet_dir / "agreement_packet_build_report.json"
    build_report = (
        json.loads(build_report_path.read_text(encoding="utf-8"))
        if build_report_path.exists()
        else {}
    )
    report = base_report(
        reference,
        reviewer_a,
        reviewer_b,
        missing,
        missing_evidence,
        build_report,
    )
    report["packet_dir"] = packet_dir.as_posix()
    return report


def single_root(entries: set[str]) -> str:
    roots = sorted({name.split("/", 1)[0] for name in entries if "/" in name})
    return roots[0] if len(roots) == 1 else ""


def verify_zip(zip_path: Path) -> dict[str, Any]:
    if not zip_path.exists():
        return {"zip_path": zip_path.as_posix(), "issues": ["missing zip"], "valid": False}
    with zipfile.ZipFile(zip_path) as zf:
        entries = {name.replace("\\", "/") for name in zf.namelist()}
        root = single_root(entries)
        if not root:
            return {
                "zip_path": zip_path.as_posix(),
                "issues": ["could not determine single packet root"],
                "valid": False,
            }
        missing = [name for name in REQUIRED_FILES if f"{root}/{name}" not in entries]
        reference_name = f"{root}/sample_reference.csv"
        reviewer_a_name = f"{root}/reviewer_a_checklist.csv"
        reviewer_b_name = f"{root}/reviewer_b_checklist.csv"
        reference = zip_csv(zf, reference_name) if reference_name in entries else []
        reviewer_a = zip_csv(zf, reviewer_a_name) if reviewer_a_name in entries else []
        reviewer_b = zip_csv(zf, reviewer_b_name) if reviewer_b_name in entries else []
        missing_evidence = sum(f"{root}/{value}" not in entries for value in evidence_values(reference))
        build_report_name = f"{root}/agreement_packet_build_report.json"
        build_report = (
            json.loads(zf.read(build_report_name).decode("utf-8"))
            if build_report_name in entries
            else {}
        )
    report = base_report(
        reference,
        reviewer_a,
        reviewer_b,
        missing,
        missing_evidence,
        build_report,
    )
    report.update({"zip_path": zip_path.as_posix(), "packet_root": root, "entry_count": len(entries)})
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agreement Audit Packet Verification",
        "",
        f"- Valid: `{str(report.get('valid', False)).lower()}`",
        f"- Sample rows: `{report.get('sample_rows', 0)}`",
        f"- Reviewer A rows: `{report.get('reviewer_a_rows', 0)}`",
        f"- Reviewer B rows: `{report.get('reviewer_b_rows', 0)}`",
        f"- Missing evidence refs: `{report.get('missing_evidence_refs', 0)}`",
        f"- Missing provenance values: `{report.get('missing_provenance_values', 0)}`",
        f"- Release-ready claim present: `{str(report.get('release_claim_present', False)).lower()}`",
        f"- Non-release-ready rows: `{report.get('non_release_ready_rows', 0)}`",
        f"- Source-blocker rows: `{report.get('source_blocker_rows', 0)}`",
        f"- Reviewer sheets initially identical: `{str(report.get('reviewer_sheets_identical', False)).lower()}`",
    ]
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an Eng_Bench agreement-audit packet.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--packet-dir")
    group.add_argument("--zip", dest="zip_path")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)
    report = verify_zip(Path(args.zip_path)) if args.zip_path else verify_dir(Path(args.packet_dir))
    if args.output_json:
        write_json(Path(args.output_json), report)
    if args.output_md:
        path = Path(args.output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
