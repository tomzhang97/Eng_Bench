#!/usr/bin/env python3
"""Plan selective page rendering for fresh MicroText review evidence."""
from __future__ import annotations

import argparse
import csv
import json
import shlex
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_source_conversion_readiness as source_readiness


def load_manifest_docs(root: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("doc_id") or "").strip(): row
        for row in source_readiness.read_jsonl(root / "manifest.jsonl")
        if row.get("type") == "doc" and str(row.get("doc_id") or "").strip()
    }


def format_page_spec(page_indices: list[int]) -> str:
    """Compress sorted 0-based page indexes into a 1-based CLI page spec."""
    pages = sorted({page + 1 for page in page_indices})
    if not pages:
        return ""
    ranges: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def page_index_for(row: dict[str, Any]) -> int | None:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    value = row.get("page_index")
    if value in (None, ""):
        value = row.get("page")
    if value in (None, ""):
        value = metadata.get("page_index", metadata.get("page"))
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page >= 0 else None


def source_path_for(
    root: Path,
    inventory_row: dict[str, str],
    manifest_row: dict[str, Any] | None,
) -> str:
    candidates = [
        str(inventory_row.get("source_path") or inventory_row.get("path") or "").strip(),
        str((manifest_row or {}).get("path") or "").strip(),
    ]
    for value in candidates:
        if value and (root / value).is_file():
            return Path(value).as_posix()
    return next((Path(value).as_posix() for value in candidates if value), "")


def missing_paths(root: Path, row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for value in source_readiness.path_fields(row):
        path = Path(value)
        full_path = path if path.is_absolute() else root / path
        if not full_path.exists():
            missing.append(value)
    return missing


def build_plan(
    root: Path,
    *,
    packet_date_label: str,
    doc_ids: set[str] | None = None,
) -> dict[str, Any]:
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in source_readiness.read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    manifest = load_manifest_docs(root)
    packeted = source_readiness.active_packet_row_keys(root, packet_date_label)
    resolved = (
        source_readiness.active_gold_row_keys(root)
        | source_readiness.terminal_reviewed_row_keys(root)
    )
    selected: dict[str, tuple[dict[str, Any], Path, list[str]]] = {}
    counters: Counter[str] = Counter()

    for path in sorted((root / "microtext" / "annotations").glob("microtext_review*.jsonl")):
        for row in source_readiness.read_jsonl(path):
            counters["rows_scanned"] += 1
            doc_id = str(row.get("doc_id") or "").strip()
            if doc_ids and doc_id not in doc_ids:
                continue
            if source_readiness.status_for(row) not in source_readiness.OPEN_STATUSES:
                continue
            if source_readiness.review_exclusion_reason(row):
                continue
            identity = source_readiness.row_identity(row, "microtext")
            if not identity:
                counters["missing_identity"] += 1
                continue
            if identity in packeted:
                counters["already_packeted"] += 1
                continue
            if identity in resolved:
                counters["already_resolved"] += 1
                continue
            source = inventory.get(doc_id, {})
            if not source_readiness.is_release_safe_status(source.get("public_status")):
                counters["rights_blocked"] += 1
                continue
            missing = missing_paths(root, row)
            if not missing:
                continue
            if identity in selected:
                counters["duplicate_identity"] += 1
                continue
            selected[identity] = (row, path.relative_to(root), missing)

    by_doc: dict[str, list[tuple[str, dict[str, Any], Path, list[str]]]] = defaultdict(list)
    for identity, (row, path, missing) in selected.items():
        by_doc[str(row.get("doc_id") or "")].append((identity, row, path, missing))

    documents: list[dict[str, Any]] = []
    issues: list[str] = []
    row_records: list[dict[str, Any]] = []
    for doc_id, records in sorted(by_doc.items()):
        inventory_row = inventory.get(doc_id, {})
        manifest_row = manifest.get(doc_id)
        source_path = source_path_for(root, inventory_row, manifest_row)
        pages = Counter()
        missing_ref_count = 0
        for identity, row, queue_path, missing in records:
            page_index = page_index_for(row)
            if page_index is None:
                issues.append(f"{identity}: missing valid page index")
                continue
            pages[page_index] += 1
            missing_ref_count += len(missing)
            row_records.append(
                {
                    "doc_id": doc_id,
                    "candidate_id": identity,
                    "page_index_0based": page_index,
                    "page_number_1based": page_index + 1,
                    "category": str(row.get("category") or ""),
                    "queue_path": queue_path.as_posix(),
                    "missing_paths": ";".join(missing),
                }
            )
        page_indices = sorted(pages)
        page_spec = format_page_spec(page_indices)
        if not source_path or not (root / source_path).is_file():
            issues.append(f"{doc_id}: source PDF is unavailable: {source_path or '<blank>'}")
        command_parts = [
            "python",
            "tools/01_render_pdf.py",
            "--root",
            ".",
            "--doc_id",
            doc_id,
            "--pdf_relpath",
            source_path,
            "--dpi",
            "300",
            "--grayscale",
            "--pages",
            page_spec,
        ]
        documents.append(
            {
                "doc_id": doc_id,
                "domain": str(inventory_row.get("domain") or ""),
                "public_status": str(inventory_row.get("public_status") or ""),
                "source_path": source_path,
                "fresh_rows_missing_evidence": len(records),
                "missing_reference_count": missing_ref_count,
                "unique_pages_to_render": len(page_indices),
                "page_indices_0based": page_indices,
                "page_spec_1based": page_spec,
                "rows_by_page_0based": {str(page): pages[page] for page in page_indices},
                "render_command": " ".join(shlex.quote(part) for part in command_parts),
            }
        )

    totals = {
        **dict(counters),
        "target_docs": len(doc_ids or set()),
        "documents_to_repair": len(documents),
        "unique_fresh_rows_missing_evidence": len(row_records),
        "unique_pages_to_render": sum(row["unique_pages_to_render"] for row in documents),
        "issues": len(issues),
    }
    return {
        "goal": "Gold v2.0 Global",
        "packet_date_label": packet_date_label,
        "target_doc_ids": sorted(doc_ids or set()),
        "totals": totals,
        "documents": documents,
        "rows": row_records,
        "issues": issues,
        "valid": not issues,
        "interpretation": (
            "This is a render plan only. Rendering repairs evidence availability; it does not "
            "review, accept, or promote any candidate into gold."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Review Evidence Render Plan",
        "",
        "- Goal: **Gold v2.0 Global**",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Fresh rows blocked only by missing evidence: `{totals['unique_fresh_rows_missing_evidence']}`",
        f"- Source documents: `{totals['documents_to_repair']}`",
        f"- Unique pages to render: `{totals['unique_pages_to_render']}`",
        f"- Issues: `{totals['issues']}`",
        "",
        "| Document | Fresh Rows | Pages | 1-Based Page Spec | Source |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for row in report["documents"]:
        lines.append(
            f"| `{row['doc_id']}` | {row['fresh_rows_missing_evidence']} | "
            f"{row['unique_pages_to_render']} | `{row['page_spec_1based']}` | `{row['source_path']}` |"
        )
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.extend(
        [
            "",
            "Rendering these pages does not change active gold. Rows still require strict assembly, visual QA, and human review.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    report: dict[str, Any],
    *,
    output_json: Path,
    output_csv: Path,
    output_md: Path,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "doc_id",
        "candidate_id",
        "page_index_0based",
        "page_number_1based",
        "category",
        "queue_path",
        "missing_paths",
    ]
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["rows"])
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--packet-date-label", required=True)
    parser.add_argument("--doc-id", action="append", default=[])
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_plan(
        root,
        packet_date_label=args.packet_date_label,
        doc_ids={value.strip() for value in args.doc_id if value.strip()} or None,
    )
    write_outputs(
        report,
        output_json=root / args.output_json,
        output_csv=root / args.output_csv,
        output_md=root / args.output_md,
    )
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
