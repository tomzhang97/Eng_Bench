#!/usr/bin/env python3
"""Fill unresolved review-row version IDs from verified manifest lineage."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


UNKNOWN_VERSION_IDS = {"", "unknown", "n/a", "na", "none", "null"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_KEYS = (
    "version_id",
    "sch_rev",
    "revision",
    "rev",
    "board_revision",
    "release",
    "snapshot",
    "publication_year",
    "edition",
    "standard",
    "ntrs_id",
    "report_number",
    "drawing_number",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_version_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def manifest_version_id(row: dict[str, Any]) -> tuple[str, str]:
    direct = normalize_version_id(row.get("version_id"))
    if direct:
        return direct, "manifest.version_id"

    version = row.get("version")
    if isinstance(version, str):
        return normalize_version_id(version), "manifest.version"
    if isinstance(version, dict):
        for key in VERSION_KEYS:
            normalized = normalize_version_id(version.get(key))
            if normalized:
                return normalized, f"manifest.version.{key}"

    source_sha256 = str(row.get("sha256") or "").strip().lower()
    if SHA256_PATTERN.fullmatch(source_sha256):
        return f"sha256_{source_sha256}", "manifest.sha256"
    return "", ""


def unresolved_version_tier_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    selected = [
        row
        for row in rows
        if "unresolved_version" in (row.get("unstaged_capacity_tier_reasons") or [])
    ]
    return selected, len(rows) - len(selected)


def enrich_rows(
    rows: list[dict[str, Any]],
    manifest_rows: list[dict[str, Any]],
    active_gold_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_docs = {
        str(row.get("doc_id") or ""): row
        for row in manifest_rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    gold_versions_by_doc: dict[str, set[str]] = {}
    for row in active_gold_rows or []:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        doc_id = str(row.get("doc_id") or metadata.get("doc_id") or "").strip()
        version_id = normalize_version_id(
            row.get("version_id") or metadata.get("version_id")
        )
        if doc_id and version_id not in UNKNOWN_VERSION_IDS:
            gold_versions_by_doc.setdefault(doc_id, set()).add(version_id)
    output: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    resolved_versions: Counter[str] = Counter()
    resolution_sources: Counter[str] = Counter()
    unresolved_docs: Counter[str] = Counter()

    for original in rows:
        row = dict(original)
        current = normalize_version_id(row.get("version_id"))
        if current not in UNKNOWN_VERSION_IDS:
            row["version_id"] = current
            status_counts["preserved"] += 1
            resolved_versions[current] += 1
            output.append(row)
            continue

        doc_id = str(row.get("doc_id") or "").strip()
        manifest_row = manifest_docs.get(doc_id, {})
        version_id, source = manifest_version_id(manifest_row)
        if not version_id:
            gold_versions = gold_versions_by_doc.get(doc_id, set())
            if len(gold_versions) == 1:
                version_id = next(iter(gold_versions))
                source = "active_gold.unique_version_id"
        if not version_id:
            status_counts["unresolved"] += 1
            unresolved_docs[doc_id or "<missing_doc_id>"] += 1
            output.append(row)
            continue

        row["version_id"] = version_id
        row["machine_version_enriched"] = True
        row["machine_version_source"] = source
        manifest_sha256 = str(manifest_row.get("sha256") or "").strip().lower()
        if SHA256_PATTERN.fullmatch(manifest_sha256):
            row["machine_version_manifest_sha256"] = manifest_sha256
        status_counts["enriched"] += 1
        resolution_sources[source] += 1
        resolved_versions[version_id] += 1
        output.append(row)

    report = {
        "totals": {
            "input_rows": len(rows),
            "output_rows": len(output),
            "enriched_rows": status_counts["enriched"],
            "preserved_rows": status_counts["preserved"],
            "unresolved_rows": status_counts["unresolved"],
            "manifest_documents": len(manifest_docs),
            "active_gold_documents_with_unique_known_version": sum(
                len(values) == 1 for values in gold_versions_by_doc.values()
            ),
        },
        "resolution_sources": dict(sorted(resolution_sources.items())),
        "resolved_versions": dict(sorted(resolved_versions.items())),
        "unresolved_documents": dict(sorted(unresolved_docs.items())),
        "valid": status_counts["unresolved"] == 0,
        "interpretation": (
            "Version enrichment changes review metadata only. Rows remain unreviewed and must "
            "not be promoted to gold until human acceptance and the normal merge gates pass."
        ),
    }
    return output, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# Manifest Version Enrichment",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Input/output rows: `{totals['input_rows']}` / `{totals['output_rows']}`",
        f"- Enriched rows: `{totals['enriched_rows']}`",
        f"- Preserved rows: `{totals['preserved_rows']}`",
        f"- Unresolved rows: `{totals['unresolved_rows']}`",
        "",
        "## Resolved Versions",
        "",
    ]
    for version_id, count in report["resolved_versions"].items():
        lines.append(f"- `{version_id}`: `{count}`")
    if report["unresolved_documents"]:
        lines.extend(["", "## Unresolved Documents", ""])
        for doc_id, count in report["unresolved_documents"].items():
            lines.append(f"- `{doc_id}`: `{count}`")
    lines.extend(["", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def partition_rows_by_version(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for original in rows:
        if normalize_version_id(original.get("version_id")) not in UNKNOWN_VERSION_IDS:
            resolved.append(dict(original))
            continue
        row = dict(original)
        row["review_status"] = "machine_held"
        row["machine_qa_status"] = "machine_held"
        row["machine_hold_reason"] = "unresolved_version_id"
        row["machine_qa_notes"] = (
            "Version lineage is unresolved; do not assign for human review."
        )
        unresolved.append(row)
    return resolved, unresolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("manifest.jsonl"))
    parser.add_argument(
        "--active-gold",
        type=Path,
        default=Path("microtext/annotations/microtext_items.jsonl"),
        help="Active gold used only as a fallback when a document has one known version ID.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--only-unresolved-version-tier",
        action="store_true",
        help=(
            "Process only ranked-capacity rows whose tier reasons include "
            "unresolved_version."
        ),
    )
    parser.add_argument(
        "--passing-only",
        action="store_true",
        help="Write only rows with a resolved version ID to --output.",
    )
    parser.add_argument(
        "--unresolved-output",
        type=Path,
        help="Optional hold ledger for rows with unresolved version IDs.",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    input_path = args.input if args.input.is_absolute() else root / args.input
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    active_gold_path = (
        args.active_gold if args.active_gold.is_absolute() else root / args.active_gold
    )
    output_path = args.output if args.output.is_absolute() else root / args.output
    report_json = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_md = args.report_md if args.report_md.is_absolute() else root / args.report_md

    input_rows = read_jsonl(input_path)
    excluded_by_input_filter = 0
    if args.only_unresolved_version_tier:
        input_rows, excluded_by_input_filter = unresolved_version_tier_rows(input_rows)
    output, report = enrich_rows(
        input_rows,
        read_jsonl(manifest_path),
        read_jsonl(active_gold_path) if active_gold_path.is_file() else [],
    )
    report["input_selection"] = {
        "mode": (
            "unresolved_version_tier_only"
            if args.only_unresolved_version_tier
            else "all_rows"
        ),
        "excluded_rows": excluded_by_input_filter,
    }
    resolved_rows, unresolved_rows = partition_rows_by_version(output)
    report["output_selection"] = {
        "mode": "passing_only" if args.passing_only else "all_rows",
        "output_rows": len(resolved_rows) if args.passing_only else len(output),
        "unresolved_output_rows": len(unresolved_rows) if args.unresolved_output else 0,
    }
    write_jsonl(output_path, resolved_rows if args.passing_only else output)
    if args.unresolved_output:
        unresolved_output = (
            args.unresolved_output
            if args.unresolved_output.is_absolute()
            else root / args.unresolved_output
        )
        write_jsonl(unresolved_output, unresolved_rows)
    write_report(report_json, report)
    write_markdown(report_md, report)
    print(json.dumps(report["totals"], indent=2, ensure_ascii=False))
    if args.strict and not report["valid"] and not args.passing_only:
        print("[ERROR] unresolved review-row version IDs")
        return 1
    if args.passing_only and not resolved_rows:
        print("[ERROR] no rows have resolved version IDs")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
