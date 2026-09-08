#!/usr/bin/env python3
"""Calibrate objective MicroText rules against completed historical review.

The selection side of this audit uses only pre-human candidate evidence. Human
outcomes are consulted only after a row independently passes provenance, text,
shape, crop, duplicate-region, and OCR checks. The tool is read-only with
respect to active Gold and never emits release-authorized rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_machine_certification_eligibility as eligibility
from audit_active_gold_provenance import file_sha256, manifest_maps, read_csv
from audit_staged_promotion_contract import parse_bbox, source_audit


FINAL_HUMAN_STATUSES = {"accepted", "edited", "rejected"}
CORRECT_HUMAN_STATUSES = {"accepted"}
SCHEMA = "eng_bench_historical_machine_calibration_v1"


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def iter_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                yield line_number, row


def review_jsonl_paths(
    annotation_dir: Path,
    additional_review_paths: Iterable[Path],
) -> list[Path]:
    """Return a deduplicated, fail-closed set of historical decision files."""
    excluded_names = {"microtext_items.jsonl", "microtext_questions.jsonl"}
    paths: dict[Path, Path] = {}

    def add(path: Path) -> None:
        if path.name in excluded_names or path.suffix.lower() != ".jsonl":
            return
        resolved = path.resolve()
        paths.setdefault(resolved, path)

    for path in sorted(annotation_dir.glob("*.jsonl")):
        add(path)
    for value in additional_review_paths:
        path = value.resolve()
        if path.is_file():
            add(path)
        elif path.is_dir():
            for child in sorted(path.rglob("*.jsonl")):
                add(child)
        else:
            raise FileNotFoundError(f"historical review path not found: {path}")
    return [paths[key] for key in sorted(paths, key=lambda item: item.as_posix())]


def collect_human_records(
    root: Path,
    paths: Iterable[Path],
) -> tuple[list[tuple[Path, int, dict[str, Any]]], list[dict[str, Any]], int]:
    records: list[tuple[Path, int, dict[str, Any]]] = []
    inventory: list[dict[str, Any]] = []
    scanned_rows = 0
    for path in paths:
        file_rows = 0
        final_rows = 0
        for line_number, row in iter_jsonl(path):
            scanned_rows += 1
            file_rows += 1
            if str(row.get("review_status") or "").strip().lower() in FINAL_HUMAN_STATUSES:
                records.append((path, line_number, row))
                final_rows += 1
        inventory.append(
            {
                "path": display(root, path),
                "sha256": file_sha256(path),
                "rows": file_rows,
                "final_human_rows": final_rows,
            }
        )
    return records, inventory, scanned_rows


def human_outcome(row: dict[str, Any], machine_text: str, machine_category: str) -> str:
    status = str(row.get("review_status") or "").strip().lower()
    if status not in FINAL_HUMAN_STATUSES:
        return "unclear"
    if status not in CORRECT_HUMAN_STATUSES:
        return "incorrect"
    corrected_text = eligibility.clean_text(row.get("corrected_text"))
    corrected_category = str(row.get("corrected_category") or "").strip()
    if corrected_text and corrected_text != machine_text:
        return "incorrect"
    if corrected_category and corrected_category != machine_category:
        return "incorrect"
    return "correct"


def precision_lower_bound(successes: int, total: int, confidence: float) -> float:
    if total <= 0 or successes != total:
        return 0.0
    return math.pow(1.0 - confidence, 1.0 / total)


def deduplicate_human_rows(
    records: list[tuple[Path, int, dict[str, Any]]],
) -> tuple[list[tuple[Path, int, dict[str, Any]]], list[str]]:
    grouped: dict[str, list[tuple[Path, int, dict[str, Any]]]] = defaultdict(list)
    for record in records:
        candidate_id = str(record[2].get("candidate_id") or "").strip()
        if candidate_id:
            grouped[candidate_id].append(record)
    selected: list[tuple[Path, int, dict[str, Any]]] = []
    conflicts: list[str] = []
    comparison_fields = (
        "review_status",
        "corrected_text",
        "corrected_category",
        "raw_text",
        "target_text",
        "proposed_text",
        "category",
        "doc_id",
        "version_id",
        "page_index",
        "bbox",
        "image_path",
        "source",
    )
    for candidate_id, rows in sorted(grouped.items()):
        signatures = {
            json.dumps(
                {field: row.get(field) for field in comparison_fields},
                ensure_ascii=False,
                sort_keys=True,
            )
            for _, _, row in rows
        }
        if len(signatures) != 1:
            conflicts.append(candidate_id)
            continue
        selected.append(rows[0])
    return selected, conflicts


def candidate_evidence(
    root: Path,
    row: dict[str, Any],
    docs: dict[str, dict[str, Any]],
    inventory: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    candidate_id = str(row.get("candidate_id") or "").strip()
    category = str(row.get("category") or "").strip()
    source_kind = str(row.get("source") or "").strip()
    doc_id = str(row.get("doc_id") or "").strip()
    raw = eligibility.clean_text(row.get("raw_text"))
    target = eligibility.clean_text(row.get("target_text"))
    proposed = eligibility.clean_text(row.get("proposed_text"))
    machine_text = proposed or target
    bbox = parse_bbox(row.get("bbox"))
    image_path = resolve(root, str(row.get("image_path") or ""))

    if not candidate_id:
        reasons.append("missing_candidate_id")
    if category not in eligibility.AUTO_CATEGORIES:
        reasons.append("category_not_objective")
    if source_kind not in eligibility.DETERMINISTIC_SOURCES:
        reasons.append("non_deterministic_extraction_source")
    text_contract = eligibility.source_text_contract(
        category,
        raw,
        target,
        proposed,
        machine_text,
    )
    if not text_contract:
        reasons.append("source_text_fields_do_not_exactly_agree")
    if not eligibility.category_shape_safe(category, machine_text):
        reasons.append("category_text_pattern_not_deterministic")
    if eligibility.pin_label_matches_source_revision(category, machine_text, docs.get(doc_id)):
        reasons.append("pin_label_matches_source_revision")
    if not doc_id:
        reasons.append("missing_doc_id")
    else:
        reasons.extend(source_audit(root, doc_id, docs, inventory))
    if bbox is None:
        reasons.append("invalid_bbox")
    if not image_path.is_file():
        reasons.append("missing_page_image")

    page_sha = ""
    if bbox is not None and image_path.is_file():
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            with Image.open(image_path) as page:
                width, height = page.size
            x1, y1, x2, y2 = bbox
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                reasons.append("bbox_outside_page")
            page_sha = file_sha256(image_path)
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit

    manifest = docs.get(doc_id) or {}
    payload_sha = str(manifest.get("sha256") or "").strip().lower()
    evidence = {
        "candidate_id": candidate_id,
        "source_payload_sha256": payload_sha,
        "page_image_sha256": page_sha,
        "doc_id": doc_id,
        "version_id": str(row.get("version_id") or ""),
        "page_index": int(row.get("page_index") or 0),
        "bbox": list(bbox) if bbox else [],
        "category": category,
        "answer": machine_text,
        "source": source_kind,
        "source_text_contract": text_contract,
        "normalized_raw_text": raw,
        "normalized_target_text": target,
        "normalized_proposed_text": proposed,
        "policy_version": eligibility.POLICY_VERSION,
        "calibration_mode": "retrospective_pre_human_selection",
    }
    return evidence, sorted(set(reasons))


def build_report(
    root: Path,
    annotation_dir: Path,
    cohort_path: Path,
    output_dir: Path,
    *,
    date_label: str,
    ocr_mode: str,
    ocr_cache_path: Path | None,
    ocr_cache_output_path: Path | None,
    ocr_min_confidence: float,
    ocr_scale: int,
    minimum_sample_rows: int,
    confidence: float,
    minimum_precision_bound: float,
    additional_review_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    root = root.resolve()
    annotation_dir = resolve(root, annotation_dir)
    cohort_path = resolve(root, cohort_path)
    output_dir = resolve(root, output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)

    active_hashes_before = {
        path: file_sha256(root / path)
        for path in (
            "visualdiff/annotations/visualdiff_pairs.jsonl",
            "visualdiff/annotations/visualdiff_questions.jsonl",
            "microtext/annotations/microtext_items.jsonl",
            "microtext/annotations/microtext_questions.jsonl",
            "eng_bench.jsonl",
        )
    }
    docs, _ = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or ""): row
        for row in read_csv(root / "source_inventory.csv")
        if row.get("doc_id")
    }

    resolved_additional_paths = [resolve(root, path) for path in additional_review_paths]
    review_paths = review_jsonl_paths(annotation_dir, resolved_additional_paths)
    raw_records, review_source_inventory, scanned_rows = collect_human_records(
        root, review_paths
    )
    records, conflicting_ids = deduplicate_human_rows(raw_records)

    prepared: list[dict[str, Any]] = []
    excluded_reasons: Counter[str] = Counter()
    region_counts: Counter[tuple[str, int, tuple[int, int, int, int]]] = Counter()
    for path, line_number, row in records:
        evidence, reasons = candidate_evidence(root, row, docs, inventory)
        bbox = parse_bbox(row.get("bbox"))
        if bbox is not None:
            region_counts[(str(row.get("doc_id") or ""), int(row.get("page_index") or 0), bbox)] += 1
        prepared.append(
            {
                "source_review_path": display(root, path),
                "source_review_line": line_number,
                "row": row,
                "evidence": evidence,
                "reasons": reasons,
            }
        )
    for item in prepared:
        row = item["row"]
        bbox = parse_bbox(row.get("bbox"))
        region = (str(row.get("doc_id") or ""), int(row.get("page_index") or 0), bbox)
        if bbox is not None and region_counts[region] > 1:
            item["reasons"].append("duplicate_reviewed_physical_region")
        item["reasons"] = sorted(set(item["reasons"]))

    cache = eligibility.load_ocr_cache(ocr_cache_path)
    cache_handle = None
    if ocr_cache_output_path is not None:
        ocr_cache_output_path = resolve(root, ocr_cache_output_path)
        if ocr_cache_path is not None and ocr_cache_output_path.resolve() == ocr_cache_path.resolve():
            raise ValueError("OCR cache input and output paths must differ")
        ocr_cache_output_path.parent.mkdir(parents=True, exist_ok=True)
        cache_handle = ocr_cache_output_path.open("x", encoding="utf-8", newline="\n")

    engine = None
    np_module = None
    if ocr_mode == "rapidocr":
        try:
            import numpy as np
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError("RapidOCR unavailable; use the OCR environment") from exc
        engine = RapidOCR()
        np_module = np

    ocr_runs = 0
    ocr_cache_hits = 0
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        for item in prepared:
            if item["reasons"]:
                excluded_reasons.update(item["reasons"])
                continue
            evidence = item["evidence"]
            evidence_sha = eligibility.evidence_record_sha256(evidence)
            result = cache.get(evidence_sha)
            if result:
                ocr_cache_hits += 1
            elif engine is not None:
                row = item["row"]
                image_path = resolve(root, str(row.get("image_path") or ""))
                bbox = parse_bbox(row.get("bbox"))
                assert bbox is not None
                with Image.open(image_path) as page:
                    crop = page.convert("RGB").crop(bbox)
                ocr_text, ocr_score = eligibility.recognize_crop(engine, np_module, crop, ocr_scale)
                result = {
                    "evidence_sha256": evidence_sha,
                    "candidate_id": evidence["candidate_id"],
                    "ocr_engine": "RapidOCR PP-OCRv6 recognition",
                    "ocr_text": ocr_text,
                    "ocr_confidence": round(ocr_score, 6),
                    "ocr_scale": ocr_scale,
                }
                cache[evidence_sha] = result
                if cache_handle is not None:
                    cache_handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
                    cache_handle.flush()
                ocr_runs += 1
            if not result:
                item["reasons"].append("independent_ocr_consensus_not_run")
            else:
                item["ocr"] = result
                if eligibility.clean_text(result.get("ocr_text")) != evidence["answer"]:
                    item["reasons"].append("independent_ocr_text_mismatch")
                if float(result.get("ocr_confidence") or 0.0) < ocr_min_confidence:
                    item["reasons"].append("independent_ocr_confidence_below_threshold")
            if item["reasons"]:
                excluded_reasons.update(item["reasons"])
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
        if cache_handle is not None:
            cache_handle.close()

    labeled_rows: list[dict[str, Any]] = []
    for item in prepared:
        if item["reasons"]:
            continue
        evidence = item["evidence"]
        row = item["row"]
        outcome = human_outcome(row, evidence["answer"], evidence["category"])
        labeled_rows.append(
            {
                **evidence,
                "machine_calibration_evidence_sha256": eligibility.evidence_record_sha256(evidence),
                "ocr": item.get("ocr") or {},
                "historical_human_status": str(row.get("review_status") or "").lower(),
                "historical_corrected_text": eligibility.clean_text(row.get("corrected_text")),
                "historical_corrected_category": str(row.get("corrected_category") or ""),
                "historical_outcome": outcome,
                "source_review_path": item["source_review_path"],
                "source_review_line": item["source_review_line"],
            }
        )

    by_category: dict[str, dict[str, Any]] = {}
    qualifying_categories: set[str] = set()
    for category in sorted(eligibility.AUTO_CATEGORIES):
        category_rows = [row for row in labeled_rows if row["category"] == category]
        correct = sum(row["historical_outcome"] == "correct" for row in category_rows)
        incorrect = sum(row["historical_outcome"] == "incorrect" for row in category_rows)
        unclear = sum(row["historical_outcome"] == "unclear" for row in category_rows)
        lower = precision_lower_bound(correct, len(category_rows), confidence)
        qualifies = (
            len(category_rows) >= minimum_sample_rows
            and incorrect == 0
            and unclear == 0
            and lower >= minimum_precision_bound
        )
        if qualifies:
            qualifying_categories.add(category)
        by_category[category] = {
            "rows": len(category_rows),
            "correct": correct,
            "incorrect": incorrect,
            "unclear": unclear,
            "observed_precision": correct / len(category_rows) if category_rows else 0.0,
            "one_sided_precision_lower_bound": round(lower, 8),
            "minimum_sample_rows": minimum_sample_rows,
            "minimum_precision_lower_bound": minimum_precision_bound,
            "qualifies": qualifies,
        }

    cohort_rows = list(eligibility.read_jsonl(cohort_path))
    covered_rows: list[dict[str, Any]] = []
    for row in cohort_rows:
        if str(row.get("category") or "") not in qualifying_categories:
            continue
        output = dict(row)
        output["historical_calibration_qualified"] = True
        output["historical_calibration_status"] = "qualified_pending_policy_and_promotion_control"
        output["safe_to_merge_gold"] = False
        covered_rows.append(output)

    labeled_path = output_dir / "historical_machine_eligible_labeled.jsonl"
    covered_path = output_dir / "cohort_qualified_pending_policy_update.jsonl"
    review_inventory_path = output_dir / "review_source_inventory.jsonl"
    write_jsonl(labeled_path, labeled_rows)
    write_jsonl(covered_path, covered_rows)
    write_jsonl(review_inventory_path, review_source_inventory)

    active_hashes_after = {path: file_sha256(root / path) for path in active_hashes_before}
    report = {
        "schema": SCHEMA,
        "date_label": date_label,
        "goal": "Gold v2.0 Global",
        "mode": "read_only_retrospective_machine_calibration",
        "policy_version": eligibility.POLICY_VERSION,
        "selection_uses_human_outcome": False,
        "human_outcome_used_only_for_evaluation": True,
        "active_gold_modified": active_hashes_before != active_hashes_after,
        "inputs": {
            "annotation_dir": display(root, annotation_dir),
            "additional_review_paths": [
                display(root, path) for path in resolved_additional_paths
            ],
            "review_source_inventory": display(root, review_inventory_path),
            "review_source_inventory_sha256": file_sha256(review_inventory_path),
            "cohort": display(root, cohort_path),
            "cohort_sha256": file_sha256(cohort_path),
            "ocr_cache": display(root, ocr_cache_path) if ocr_cache_path else "",
        },
        "scan": {
            "files": len(review_paths),
            "rows": scanned_rows,
            "final_human_records": len(raw_records),
            "deduplicated_human_records": len(records),
            "conflicting_candidate_ids": len(conflicting_ids),
            "conflicting_ids": conflicting_ids,
        },
        "pre_human_selection": {
            "machine_eligible_historically_labeled": len(labeled_rows),
            "excluded_reason_counts": dict(sorted(excluded_reasons.items())),
            "ocr_runs": ocr_runs,
            "ocr_cache_hits": ocr_cache_hits,
            "ocr_min_confidence": ocr_min_confidence,
            "ocr_scale": ocr_scale,
        },
        "calibration": {
            "confidence": confidence,
            "minimum_sample_rows": minimum_sample_rows,
            "minimum_precision_lower_bound": minimum_precision_bound,
            "by_category": by_category,
            "qualifying_categories": sorted(qualifying_categories),
        },
        "cohort_coverage": {
            "input_rows": len(cohort_rows),
            "qualified_pending_policy_rows": len(covered_rows),
            "not_release_authorized": True,
        },
        "artifacts": {
            "historical_labeled": {
                "path": display(root, labeled_path),
                "sha256": file_sha256(labeled_path),
            },
            "qualified_cohort_pending_policy": {
                "path": display(root, covered_path),
                "sha256": file_sha256(covered_path),
            },
            "review_source_inventory": {
                "path": display(root, review_inventory_path),
                "sha256": file_sha256(review_inventory_path),
            },
        },
        "active_hashes_before": active_hashes_before,
        "active_hashes_after": active_hashes_after,
    }
    if ocr_cache_output_path is not None:
        report["artifacts"]["ocr_cache_output"] = {
            "path": display(root, ocr_cache_output_path),
            "sha256": file_sha256(ocr_cache_output_path),
        }
    report_path = output_dir / "historical_machine_calibration_report.json"
    write_json(report_path, report)
    markdown = [
        "# Historical Machine Calibration Audit",
        "",
        "**Goal:** Gold v2.0 Global",
        "",
        f"- Historical machine-eligible rows: `{len(labeled_rows)}`",
        f"- Qualifying categories: `{', '.join(sorted(qualifying_categories)) or 'none'}`",
        f"- Covered cohort rows pending policy control: `{len(covered_rows)}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "| Category | Rows | Correct | Incorrect | Lower bound | Qualifies |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for category, values in by_category.items():
        markdown.append(
            f"| {category} | {values['rows']} | {values['correct']} | "
            f"{values['incorrect']} | {values['one_sided_precision_lower_bound']:.8f} | "
            f"{'PASS' if values['qualifies'] else 'OPEN'} |"
        )
    markdown.extend(
        [
            "",
            "This audit does not certify or promote rows. A qualifying category still requires an explicit policy update, a hash-bound attestation, and the normal promotion gates.",
        ]
    )
    (output_dir / "historical_machine_calibration_report.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate machine rules against historical human outcomes without editing Gold."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--annotation-dir", type=Path, default=Path("microtext/annotations"))
    parser.add_argument(
        "--additional-review-path",
        type=Path,
        action="append",
        default=[],
        help="Additional processed-return JSONL file or directory; repeat as needed.",
    )
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--ocr-mode", choices=("rapidocr", "cache-only"), default="cache-only")
    parser.add_argument("--ocr-cache", type=Path)
    parser.add_argument("--ocr-cache-output", type=Path)
    parser.add_argument("--ocr-min-confidence", type=float, default=0.98)
    parser.add_argument("--ocr-scale", type=int, default=3)
    parser.add_argument("--minimum-sample-rows", type=int, default=300)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--minimum-precision-bound", type=float, default=0.99)
    args = parser.parse_args()
    root = args.root.resolve()
    cache = resolve(root, args.ocr_cache) if args.ocr_cache else None
    cache_output = resolve(root, args.ocr_cache_output) if args.ocr_cache_output else None
    report = build_report(
        root,
        args.annotation_dir,
        args.cohort,
        args.output_dir,
        date_label=args.date_label,
        ocr_mode=args.ocr_mode,
        ocr_cache_path=cache,
        ocr_cache_output_path=cache_output,
        ocr_min_confidence=args.ocr_min_confidence,
        ocr_scale=args.ocr_scale,
        minimum_sample_rows=args.minimum_sample_rows,
        confidence=args.confidence,
        minimum_precision_bound=args.minimum_precision_bound,
        additional_review_paths=args.additional_review_path,
    )
    print(
        json.dumps(
            {
                "historical_machine_eligible": report["pre_human_selection"]["machine_eligible_historically_labeled"],
                "qualifying_categories": report["calibration"]["qualifying_categories"],
                "qualified_cohort_rows": report["cohort_coverage"]["qualified_pending_policy_rows"],
                "active_gold_modified": report["active_gold_modified"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
