#!/usr/bin/env python3
"""Assemble and gate multiple microtext staging files into one review queue."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import file_sha256, read_csv, read_jsonl, rights_blocker


CANONICAL_QUESTION_CATEGORY = {
    "What component value is shown in this region?": "component_value",
    "What dimension value is shown in this region?": "dimension_value",
    "What equipment tag is shown in this region?": "equipment_tag",
    "What instrument tag is shown in this region?": "instrument_tag",
    "What pin or component label is shown in this marked region?": "pin_label",
    "What pipe or process line tag is shown in this region?": "pipe_line_tag",
    "What process step or stream label is shown in this region?": "process_label",
    "What process value is shown in this region?": "process_value",
    "What room label is shown in this region?": "room_label",
    "What tolerance is specified in this small text region?": "tolerance_value",
}


def validated_source_candidate_map(
    root: Path, validation_path: Path | None
) -> tuple[dict[str, str], set[str]]:
    """Return unique release-candidate lineage mappings keyed by local doc ID."""
    if validation_path is None:
        return {}, set()
    path = validation_path if validation_path.is_absolute() else root / validation_path
    if not path.is_file():
        return {}, set()

    candidates_by_doc: dict[str, set[str]] = {}
    for row in read_csv(path):
        if str(row.get("release_posture") or "").strip() != "release_candidate":
            continue
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        for doc_id in str(row.get("linked_local_doc_ids") or "").split(";"):
            doc_id = doc_id.strip()
            if doc_id:
                candidates_by_doc.setdefault(doc_id, set()).add(candidate_id)

    ambiguous = {doc_id for doc_id, values in candidates_by_doc.items() if len(values) > 1}
    resolved = {
        doc_id: next(iter(values))
        for doc_id, values in candidates_by_doc.items()
        if len(values) == 1
    }
    return resolved, ambiguous


def bbox_tuple(row: dict[str, Any]) -> tuple[int, int, int, int]:
    values = row.get("bbox") or row.get("bbox_px") or []
    if len(values) != 4:
        return (0, 0, 0, 0)
    return tuple(int(round(float(value))) for value in values)


def region_key(row: dict[str, Any]) -> tuple[str, int, tuple[int, int, int, int]]:
    return (
        str(row.get("doc_id") or ""),
        int(row.get("page_index", row.get("page", 0)) or 0),
        bbox_tuple(row),
    )


def source_payload_path(root: Path, inventory: dict[str, str], manifest: dict[str, Any]) -> Path:
    value = str(
        inventory.get("path")
        or inventory.get("source_path")
        or manifest.get("path")
        or ""
    ).strip()
    return root / value if value else Path()


def audited_image_size(path: Path, max_image_pixels: int | None) -> tuple[int, int]:
    previous_limit = Image.MAX_IMAGE_PIXELS
    if max_image_pixels is not None:
        Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        with Image.open(path) as image:
            return image.size
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def build_report(
    root: Path,
    input_paths: list[Path],
    date_label: str = "manual",
    max_image_pixels: int | None = None,
    source_candidate_validation_path: Path | None = Path("SOURCE_CANDIDATE_VALIDATION.csv"),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    manifest_rows = read_jsonl(root / "manifest.jsonl")
    manifest_docs = {
        str(row.get("doc_id")): row
        for row in manifest_rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    inventory = {
        str(row.get("doc_id")): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }
    gold_rows = read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    gold_ids = {str(row.get("item_id") or "") for row in gold_rows}
    gold_regions = {region_key(row) for row in gold_rows}
    validated_candidate_ids, ambiguous_candidate_docs = validated_source_candidate_map(
        root, source_candidate_validation_path
    )

    rows: list[dict[str, Any]] = []
    input_counts: dict[str, int] = {}
    for value in input_paths:
        path = value if value.is_absolute() else root / value
        loaded = read_jsonl(path)
        input_counts[path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)] = len(loaded)
        rows.extend(dict(row) for row in loaded)

    id_counts = Counter(str(row.get("candidate_id") or "") for row in rows)
    region_counts = Counter(region_key(row) for row in rows)
    doc_results: dict[str, dict[str, Any]] = {}

    def audit_doc(doc_id: str) -> dict[str, Any]:
        if doc_id in doc_results:
            return doc_results[doc_id]
        manifest = manifest_docs.get(doc_id, {})
        source = inventory.get(doc_id, {})
        path = source_payload_path(root, source, manifest)
        exists = bool(str(path)) and path.is_file()
        computed_sha = file_sha256(path) if exists else ""
        recorded_sha = str(manifest.get("sha256") or "").strip().lower()
        public_status = str(source.get("public_status") or manifest.get("public_status") or "").strip()
        source_url = str(source.get("source_url") or manifest.get("source_url") or "").strip()
        blocker = rights_blocker(public_status)
        issues: list[str] = []
        if not manifest:
            issues.append("missing_manifest_doc")
        if not source:
            issues.append("missing_inventory_doc")
        if not exists:
            issues.append("missing_source_payload")
        if not source_url:
            issues.append("missing_source_url")
        if blocker:
            issues.append(f"rights_blocked:{blocker}")
        if not recorded_sha:
            issues.append("missing_recorded_sha256")
        elif exists and computed_sha != recorded_sha:
            issues.append("source_sha256_mismatch")
        manifest_candidate_id = str(manifest.get("source_candidate_id") or "").strip()
        validated_candidate_id = validated_candidate_ids.get(doc_id, "")
        source_candidate_id = manifest_candidate_id or validated_candidate_id
        if doc_id in ambiguous_candidate_docs and not manifest_candidate_id:
            issues.append("ambiguous_source_candidate_mapping")
        result = {
            "doc_id": doc_id,
            "source_candidate_id": source_candidate_id,
            "source_candidate_id_origin": (
                "manifest"
                if manifest_candidate_id
                else "validation_ledger_unique_release_candidate"
                if validated_candidate_id
                else ""
            ),
            "public_status": public_status,
            "source_url": source_url,
            "recorded_sha256": recorded_sha,
            "computed_sha256": computed_sha,
            "issues": issues,
            "passes": not issues,
        }
        doc_results[doc_id] = result
        return result

    assembled: list[dict[str, Any]] = []
    row_results: list[dict[str, Any]] = []
    for index, original in enumerate(rows, start=1):
        row = dict(original)
        candidate_id = str(row.get("candidate_id") or "")
        doc_id = str(row.get("doc_id") or "")
        issues: list[str] = []
        if not candidate_id:
            issues.append("missing_candidate_id")
        elif id_counts[candidate_id] > 1:
            issues.append("duplicate_candidate_id")
        elif candidate_id in gold_ids:
            issues.append("candidate_id_collides_with_gold")
        if not doc_id:
            issues.append("missing_doc_id")
        source = audit_doc(doc_id) if doc_id else {}
        if source and not source["passes"]:
            issues.append("source_not_paper_ready")
        source_candidate_lineage_recovered = False
        if not row.get("source_candidate_id") and source.get("source_candidate_id"):
            row["source_candidate_id"] = source["source_candidate_id"]
            source_candidate_lineage_recovered = (
                source.get("source_candidate_id_origin")
                == "validation_ledger_unique_release_candidate"
            )
        if not row.get("source_candidate_id"):
            issues.append("missing_source_candidate_id")
        if region_counts[region_key(row)] > 1:
            issues.append("duplicate_candidate_region")
        if region_key(row) in gold_regions:
            issues.append("region_collides_with_gold")
        if str(row.get("review_status") or "") != "needs_review":
            issues.append("review_status_not_needs_review")
        category = str(row.get("category") or "").strip()
        if not category:
            issues.append("missing_category")
        question_text = str(row.get("question_text") or "").strip()
        canonical_question_category = CANONICAL_QUESTION_CATEGORY.get(question_text)
        if canonical_question_category and canonical_question_category != category:
            issues.append("question_category_mismatch")
        if not str(row.get("proposed_text") or row.get("target_text") or "").strip():
            issues.append("missing_proposed_text")

        image_value = str(row.get("image_path") or "")
        image_path = root / image_value if image_value else Path()
        bbox = bbox_tuple(row)
        image_size: tuple[int, int] | tuple[()] = ()
        if not image_value or not image_path.is_file():
            issues.append("missing_source_image")
        elif bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            issues.append("invalid_bbox")
        else:
            try:
                image_size = audited_image_size(image_path, max_image_pixels)
                if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > image_size[0] or bbox[3] > image_size[1]:
                    issues.append("bbox_out_of_frame")
            except OSError as exc:
                issues.append(f"source_image_unreadable:{type(exc).__name__}")

        assembled.append(row)
        row_results.append(
            {
                "row_number": index,
                "candidate_id": candidate_id,
                "doc_id": doc_id,
                "category": category,
                "question_text": question_text,
                "proposed_text": str(row.get("proposed_text") or row.get("target_text") or ""),
                "bbox": list(bbox),
                "image_size": list(image_size),
                "source_candidate_lineage_recovered": source_candidate_lineage_recovered,
                "issues": list(dict.fromkeys(issues)),
                "passes": not issues,
            }
        )

    issue_counts = Counter(issue for row in row_results for issue in row["issues"])
    categories = Counter(str(row.get("category") or "") for row in assembled)
    proposed_texts = {
        str(row.get("proposed_text") or row.get("target_text") or "").strip()
        for row in assembled
    }
    totals = {
        "rows": len(assembled),
        "unique_candidate_ids": len({str(row.get("candidate_id") or "") for row in assembled}),
        "unique_regions": len({region_key(row) for row in assembled}),
        "unique_proposed_texts": len(proposed_texts),
        "source_documents": len(doc_results),
        "paper_ready_source_documents": sum(row["passes"] for row in doc_results.values()),
        "gold_region_collisions": issue_counts["region_collides_with_gold"],
        "passing_rows": sum(row["passes"] for row in row_results),
        "blocked_rows": sum(not row["passes"] for row in row_results),
        "validation_ledger_lineage_rows": sum(
            bool(row.get("source_candidate_lineage_recovered")) for row in row_results
        ),
    }
    report = {
        "date_label": date_label,
        "passes": bool(assembled) and totals["blocked_rows"] == 0,
        "inputs": input_counts,
        "totals": totals,
        "category_counts": dict(sorted(categories.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "documents": [doc_results[key] for key in sorted(doc_results)],
        "rows": row_results,
        "interpretation": "Review-only assembly. No row becomes gold until human acceptance and strict promotion.",
    }
    return assembled, report


def write_outputs(
    rows: list[dict[str, Any]],
    report: dict[str, Any],
    output_jsonl: Path,
    output_json: Path,
    output_md: Path,
) -> None:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    totals = report["totals"]
    lines = [
        "# Microtext Review Queue Assembly Audit",
        "",
        f"- Gate: `{'PASS' if report['passes'] else 'BLOCKED'}`",
        f"- Rows: {totals['passing_rows']}/{totals['rows']} passing",
        f"- Candidate IDs: {totals['unique_candidate_ids']} unique",
        f"- Regions: {totals['unique_regions']} unique",
        f"- Proposed texts: {totals['unique_proposed_texts']} unique",
        f"- Source documents: {totals['paper_ready_source_documents']}/{totals['source_documents']} paper-ready",
        f"- Gold region collisions: {totals['gold_region_collisions']}",
        f"- Rows with lineage recovered from validation ledger: {totals['validation_ledger_lineage_rows']}",
        "",
        "## Category Mix",
        "",
    ]
    for category, count in report["category_counts"].items():
        lines.append(f"- `{category}`: {count}")
    lines.extend(["", "## Blocking Issues", ""])
    if report["issue_counts"]:
        for issue, count in report["issue_counts"].items():
            lines.append(f"- `{issue}`: {count}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(lines), encoding="utf-8")


def partition_rows(
    rows: list[dict[str, Any]], report: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition assembled rows using the row-level audit in stable input order."""
    row_results = report.get("rows", [])
    if len(rows) != len(row_results):
        raise ValueError("assembled rows and audit row results have different lengths")
    passing: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row, result in zip(rows, row_results, strict=True):
        if result.get("passes"):
            passing.append(row)
            continue
        held = dict(row)
        issues = [str(value) for value in result.get("issues", []) if str(value)]
        held["review_status"] = "machine_held"
        held["machine_qa_status"] = "machine_held"
        held["machine_hold_reason"] = ";".join(issues) or "strict_assembly_blocked"
        held["machine_qa_notes"] = (
            "Blocked by strict review-queue assembly; do not assign for human review."
        )
        blocked.append(held)
    return passing, blocked


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--date-label", default="manual")
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        help="Opt-in Pillow safety ceiling for audited large source rasters.",
    )
    parser.add_argument(
        "--source-candidate-validation",
        type=Path,
        default=Path("SOURCE_CANDIDATE_VALIDATION.csv"),
        help=(
            "Validation ledger used only to recover a unique candidate ID for linked "
            "documents marked release_candidate."
        ),
    )
    parser.add_argument(
        "--passing-only",
        action="store_true",
        help="Write only passing rows to --output-jsonl; the report still audits all inputs.",
    )
    parser.add_argument(
        "--blocked-output-jsonl",
        type=Path,
        help="Optional hold ledger for blocked rows; intended for use with --passing-only.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    rows, report = build_report(
        root,
        args.input,
        args.date_label,
        args.max_image_pixels,
        args.source_candidate_validation,
    )
    output_jsonl = args.output_jsonl if args.output_jsonl.is_absolute() else root / args.output_jsonl
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
    passing_rows, blocked_rows = partition_rows(rows, report)
    report["output_selection"] = {
        "mode": "passing_only" if args.passing_only else "all_rows",
        "output_rows": len(passing_rows) if args.passing_only else len(rows),
        "blocked_output_rows": len(blocked_rows) if args.blocked_output_jsonl else 0,
    }
    output_rows = passing_rows if args.passing_only else rows
    write_outputs(output_rows, report, output_jsonl, output_json, output_md)
    if args.blocked_output_jsonl:
        blocked_output = (
            args.blocked_output_jsonl
            if args.blocked_output_jsonl.is_absolute()
            else root / args.blocked_output_jsonl
        )
        write_jsonl(blocked_output, blocked_rows)
    print(json.dumps(report["totals"], indent=2))
    if args.passing_only:
        return 0 if passing_rows else 1
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
