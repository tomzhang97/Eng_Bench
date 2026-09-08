#!/usr/bin/env python3
"""Rescue objective train MicroText rows with strict multi-view OCR consensus.

This tool only writes supplemental OCR cache evidence. The normal machine
certification audit must be rerun against the combined cache before any row can
become eligible, and active Gold is never edited here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import file_sha256
from audit_machine_certification_eligibility import (
    AUTO_CATEGORIES,
    clean_text,
    identity_for,
    read_jsonl,
    recognize_crop,
    resolve,
    task_for,
    write_json,
    write_jsonl,
)
from audit_staged_promotion_contract import parse_bbox


OCR_ONLY_REASONS = {
    "independent_ocr_confidence_below_threshold",
    "independent_ocr_text_mismatch",
}
RESCUE_POLICY_VERSION = "1.0"


def select_rescue_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        reasons = set(row.get("machine_certification_reasons") or [])
        split = str(row.get("reserved_split") or row.get("split") or "").strip().lower()
        category = str(row.get("corrected_category") or row.get("category") or "").strip().lower()
        if (
            reasons
            and reasons.issubset(OCR_ONLY_REASONS)
            and task_for(row) == "microtext"
            and split == "train"
            and category in AUTO_CATEGORIES
        ):
            selected.append(row)
    return selected


def padded(image: Image.Image) -> Image.Image:
    x_border = max(6, image.width // 6)
    y_border = max(6, image.height // 3)
    return ImageOps.expand(image, border=(x_border, y_border, x_border, y_border), fill="white")


def rescue_views(crop: Image.Image) -> list[tuple[str, Image.Image]]:
    rgb = crop.convert("RGB")
    gray = ImageOps.autocontrast(ImageOps.grayscale(rgb))
    sharp = gray.filter(ImageFilter.UnsharpMask(radius=1.0, percent=180, threshold=2))
    return [
        ("rgb_padded", padded(rgb)),
        ("gray_autocontrast", padded(gray.convert("RGB"))),
        ("gray_sharpened", padded(sharp.convert("RGB"))),
        ("binary_192", padded(gray.point(lambda value: 255 if value >= 192 else 0).convert("RGB"))),
        ("binary_224", padded(gray.point(lambda value: 255 if value >= 224 else 0).convert("RGB"))),
    ]


def consensus_result(
    expected: str,
    attempts: list[dict[str, Any]],
    *,
    minimum_confidence: float,
    minimum_exact_views: int,
) -> dict[str, Any] | None:
    exact = [
        attempt
        for attempt in attempts
        if clean_text(attempt.get("ocr_text")) == clean_text(expected)
        and float(attempt.get("ocr_confidence") or 0.0) >= minimum_confidence
    ]
    distinct = {str(attempt.get("view") or "") for attempt in exact}
    if len(distinct) < minimum_exact_views:
        return None
    exact.sort(key=lambda item: (-float(item.get("ocr_confidence") or 0.0), str(item.get("view") or "")))
    return {
        "ocr_text": clean_text(expected),
        "ocr_confidence": round(min(float(item["ocr_confidence"]) for item in exact), 6),
        "exact_views": exact,
    }


def latest_cache_by_candidate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("candidate_id") or "").strip(): row
        for row in rows
        if str(row.get("candidate_id") or "").strip()
    }


def exclude_previously_attempted(
    rows: list[dict[str, Any]],
    prior_attempt_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    attempted_ids = {
        str(row.get("candidate_id") or "").strip()
        for row in prior_attempt_rows
        if str(row.get("candidate_id") or "").strip()
    }
    filtered = [row for row in rows if identity_for(row) not in attempted_ids]
    return filtered, len(attempted_ids)


def build_rescue(
    root: Path,
    human_rows: list[dict[str, Any]],
    base_cache_rows: list[dict[str, Any]],
    engine: Any,
    np_module: Any,
    *,
    scale: int,
    minimum_confidence: float,
    minimum_exact_views: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected = select_rescue_rows(human_rows)
    cache_by_candidate = latest_cache_by_candidate(base_cache_rows)
    overrides: list[dict[str, Any]] = []
    attempts_output: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        for row in selected:
            candidate_id = identity_for(row)
            base = cache_by_candidate.get(candidate_id) or {}
            evidence_sha = str(base.get("evidence_sha256") or "").strip()
            image_path = resolve(root, str(row.get("image_path") or ""))
            bbox = parse_bbox(row.get("bbox"))
            expected = clean_text(row.get("corrected_text") or row.get("proposed_text") or row.get("target_text"))
            if not evidence_sha:
                issue_counts["missing_base_evidence_sha256"] += 1
                continue
            if not image_path.is_file():
                issue_counts["missing_image"] += 1
                continue
            if bbox is None:
                issue_counts["invalid_bbox"] += 1
                continue
            with Image.open(image_path) as page:
                crop = page.convert("RGB").crop(bbox)
            attempts: list[dict[str, Any]] = []
            for view_name, view in rescue_views(crop):
                text, confidence = recognize_crop(engine, np_module, view, scale)
                attempts.append(
                    {
                        "view": view_name,
                        "ocr_text": text,
                        "ocr_confidence": round(confidence, 6),
                    }
                )
            consensus = consensus_result(
                expected,
                attempts,
                minimum_confidence=minimum_confidence,
                minimum_exact_views=minimum_exact_views,
            )
            attempt_record = {
                "candidate_id": candidate_id,
                "evidence_sha256": evidence_sha,
                "expected_text": expected,
                "attempts": attempts,
                "rescued": consensus is not None,
            }
            attempt_record["attempts_sha256"] = hashlib.sha256(
                json.dumps(attempt_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            attempts_output.append(attempt_record)
            if consensus is None:
                issue_counts["insufficient_exact_high_confidence_views"] += 1
                continue
            category = str(row.get("category") or "")
            category_counts[category] += 1
            overrides.append(
                {
                    "candidate_id": candidate_id,
                    "evidence_sha256": evidence_sha,
                    "ocr_engine": "RapidOCR PP-OCRv6 multi-view exact consensus",
                    "ocr_text": consensus["ocr_text"],
                    "ocr_confidence": consensus["ocr_confidence"],
                    "ocr_scale": scale,
                    "ocr_rescue_policy_version": RESCUE_POLICY_VERSION,
                    "ocr_rescue_minimum_confidence": minimum_confidence,
                    "ocr_rescue_minimum_exact_views": minimum_exact_views,
                    "ocr_rescue_exact_views": consensus["exact_views"],
                    "ocr_rescue_attempts_sha256": attempt_record["attempts_sha256"],
                }
            )
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
    report = {
        "goal": "Gold v2.0 Global",
        "policy_version": RESCUE_POLICY_VERSION,
        "active_gold_modified": False,
        "counts": {
            "input_human_required_rows": len(human_rows),
            "ocr_only_rescue_pool": len(selected),
            "rescued_cache_overrides": len(overrides),
            "remaining_in_rescue_pool": len(selected) - len(overrides),
            "rescued_categories": dict(sorted(category_counts.items())),
        },
        "thresholds": {
            "ocr_scale": scale,
            "minimum_confidence_per_exact_view": minimum_confidence,
            "minimum_distinct_exact_views": minimum_exact_views,
            "views": [name for name, _ in rescue_views(Image.new("RGB", (10, 10), "white"))],
        },
        "issues": dict(sorted(issue_counts.items())),
        "interpretation": (
            "Successful rows are supplemental OCR cache evidence only. The normal machine-certification audit "
            "must rerun all provenance, source-text, category, split, evidence, deduplication, and calibration "
            "checks before any row can become eligible."
        ),
    }
    return overrides, attempts_output, report


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        "# Machine OCR Rescue Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- OCR-only rescue pool: `{counts['ocr_only_rescue_pool']}`",
        f"- Strict cache overrides: `{counts['rescued_cache_overrides']}`",
        f"- Remaining in rescue pool: `{counts['remaining_in_rescue_pool']}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        report["interpretation"],
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--human-required", type=Path, required=True)
    parser.add_argument("--base-cache", type=Path, required=True)
    parser.add_argument("--output-overrides", type=Path, required=True)
    parser.add_argument("--output-attempts", type=Path, required=True)
    parser.add_argument("--output-combined-cache", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument(
        "--prior-attempts",
        type=Path,
        action="append",
        default=[],
        help="Prior rescue-attempt JSONL whose candidate IDs must not be rerun. Repeatable.",
    )
    parser.add_argument("--ocr-scale", type=int, default=4)
    parser.add_argument("--minimum-confidence", type=float, default=0.98)
    parser.add_argument("--minimum-exact-views", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.ocr_scale <= 4:
        parser.error("--ocr-scale must be between 1 and 4")
    if not 0.0 < args.minimum_confidence <= 1.0:
        parser.error("--minimum-confidence must be in (0, 1]")
    if not 2 <= args.minimum_exact_views <= 5:
        parser.error("--minimum-exact-views must be between 2 and 5")

    root = args.root.resolve()
    human_path = resolve(root, args.human_required)
    cache_path = resolve(root, args.base_cache)
    human_rows = read_jsonl(human_path)
    input_human_rows = len(human_rows)
    prior_attempt_rows: list[dict[str, Any]] = []
    prior_attempt_paths: list[Path] = []
    for value in args.prior_attempts:
        path = resolve(root, value)
        prior_attempt_paths.append(path)
        prior_attempt_rows.extend(read_jsonl(path))
    human_rows, prior_attempt_ids = exclude_previously_attempted(
        human_rows,
        prior_attempt_rows,
    )
    try:
        import numpy as np
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("RapidOCR unavailable; use the isolated OCR environment") from exc
    overrides, attempts, report = build_rescue(
        root,
        human_rows,
        read_jsonl(cache_path),
        RapidOCR(),
        np,
        scale=args.ocr_scale,
        minimum_confidence=args.minimum_confidence,
        minimum_exact_views=args.minimum_exact_views,
    )
    report["inputs"] = {
        "human_required": human_path.as_posix(),
        "human_required_sha256": file_sha256(human_path),
        "base_cache": cache_path.as_posix(),
        "base_cache_sha256": file_sha256(cache_path),
        "human_required_rows_before_prior_attempt_filter": input_human_rows,
        "prior_attempt_files": [
            {"path": path.as_posix(), "sha256": file_sha256(path)}
            for path in prior_attempt_paths
        ],
        "prior_attempt_candidate_ids": prior_attempt_ids,
    }
    override_path = resolve(root, args.output_overrides)
    attempts_path = resolve(root, args.output_attempts)
    combined_path = resolve(root, args.output_combined_cache)
    write_jsonl(override_path, overrides)
    write_jsonl(attempts_path, attempts)
    write_jsonl(combined_path, read_jsonl(cache_path) + overrides)
    report["artifacts"] = {
        "overrides": {"path": override_path.as_posix(), "sha256": file_sha256(override_path)},
        "attempts": {"path": attempts_path.as_posix(), "sha256": file_sha256(attempts_path)},
        "combined_cache": {"path": combined_path.as_posix(), "sha256": file_sha256(combined_path)},
    }
    report_json = resolve(root, args.report_json)
    report_md = resolve(root, args.report_md)
    write_json(report_json, report)
    write_markdown(report_md, report)
    print(json.dumps(report["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
