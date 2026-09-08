#!/usr/bin/env python3
"""Filter mapped text-layer VisualDiff seeds into human-ready and machine-held rows."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


DESIGNATOR = re.compile(r"^([A-Z]{1,4})(\d+)([A-Z]?)$", re.IGNORECASE)
PART_NUMBER = re.compile(r"^([A-Z]{2,8})[A-Z0-9#._+\-/]*\d[A-Z0-9#._+\-/]*$", re.IGNORECASE)
ENGINEERING_VALUE = re.compile(
    r"^[+\-]?(?:\d+(?:\.\d+)?|\.\d+)\s*(V|A|HZ|OHM|R|K|M|UF|NF|PF|UH|MH|DB|%)$",
    re.IGNORECASE,
)


def read_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8-sig") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def coherent_text_family(old_text: str, new_text: str) -> str:
    old_designator = DESIGNATOR.fullmatch(old_text)
    new_designator = DESIGNATOR.fullmatch(new_text)
    if old_designator and new_designator:
        if old_designator.group(1).casefold() == new_designator.group(1).casefold():
            return "same_reference_designator_family"

    old_part = PART_NUMBER.fullmatch(old_text)
    new_part = PART_NUMBER.fullmatch(new_text)
    if old_part and new_part:
        old_prefix = old_part.group(1).casefold()
        new_prefix = new_part.group(1).casefold()
        if old_prefix.startswith(new_prefix) or new_prefix.startswith(old_prefix):
            return "same_part_number_family"

    old_value = ENGINEERING_VALUE.fullmatch(old_text.replace(" ", ""))
    new_value = ENGINEERING_VALUE.fullmatch(new_text.replace(" ", ""))
    if old_value and new_value and old_value.group(1).casefold() == new_value.group(1).casefold():
        return "same_engineering_value_family"
    return ""


def resolve_image(root: Path, value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else root / path


def exact_crop_metrics(
    root: Path,
    row: dict[str, Any],
    image_key: str,
    bbox_key: str,
    threshold: int,
) -> dict[str, Any]:
    path = resolve_image(root, row.get(image_key))
    bbox = row.get(bbox_key)
    if not path.is_file() or not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(f"missing or invalid {image_key}/{bbox_key}")
    x1, y1, x2, y2 = (int(value) for value in bbox)
    with Image.open(path) as source:
        gray = source.convert("L")
        if not (0 <= x1 < x2 <= gray.width and 0 <= y1 < y2 <= gray.height):
            raise ValueError(f"out-of-bounds {bbox_key}")
        crop = gray.crop((x1, y1, x2, y2))
    histogram = crop.histogram()
    ink_pixels = sum(histogram[:threshold])
    return {
        "ink_pixels": ink_pixels,
        "ink_ratio": ink_pixels / max(1, crop.width * crop.height),
        "width": crop.width,
        "height": crop.height,
    }


def filter_rows(
    root: Path,
    rows: list[dict[str, Any]],
    *,
    min_stable_page_similarity: float = 0.45,
    min_one_sided_page_similarity: float = 0.80,
    ink_threshold: int = 180,
    min_exact_ink_ratio: float = 0.01,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    identifiers = [str(row.get("pair_id") or "") for row in rows]
    duplicate_ids = sorted(
        identifier
        for identifier, count in Counter(identifiers).items()
        if identifier and count > 1
    )
    if any(not identifier for identifier in identifiers) or duplicate_ids:
        raise ValueError("input rows contain missing or duplicate pair_id values")

    passing: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    dispositions: Counter[str] = Counter()
    for source_row in rows:
        row = dict(source_row)
        change_type = str(row.get("change_type") or "")
        old_text = normalized_text(row.get("old_text"))
        new_text = normalized_text(row.get("new_text"))
        similarity = float(row.get("page_similarity_jaccard") or 0.0)
        keep = False
        reason = "unsupported_change_type"
        metrics: dict[str, Any] = {}
        try:
            if change_type == "text_change_candidate" and old_text and new_text and old_text != new_text:
                old_metrics = exact_crop_metrics(
                    root, row, "image_old", "bbox_old", ink_threshold
                )
                new_metrics = exact_crop_metrics(
                    root, row, "image_new", "bbox_new", ink_threshold
                )
                metrics = {"old": old_metrics, "new": new_metrics}
                has_ink = (
                    old_metrics["ink_ratio"] >= min_exact_ink_ratio
                    and new_metrics["ink_ratio"] >= min_exact_ink_ratio
                )
                family_reason = coherent_text_family(old_text, new_text)
                if has_ink and similarity >= min_stable_page_similarity:
                    keep = True
                    reason = "stable_mapped_page_text_change"
                elif has_ink and family_reason:
                    keep = True
                    reason = family_reason
                else:
                    reason = "weak_or_incoherent_text_pair"
            elif change_type in {"text_added_candidate", "text_removed_candidate"}:
                exact_image_key = "image_new" if change_type == "text_added_candidate" else "image_old"
                exact_bbox_key = "bbox_new" if change_type == "text_added_candidate" else "bbox_old"
                exact_metrics = exact_crop_metrics(
                    root, row, exact_image_key, exact_bbox_key, ink_threshold
                )
                metrics = {"exact": exact_metrics}
                keep = (
                    similarity >= min_one_sided_page_similarity
                    and exact_metrics["ink_ratio"] >= min_exact_ink_ratio
                )
                reason = (
                    "stable_mapped_page_one_sided_text"
                    if keep
                    else "weak_one_sided_mapping_or_evidence"
                )
        except (OSError, TypeError, ValueError) as exc:
            reason = f"invalid_evidence:{exc}"

        row["machine_qa"] = {
            "decision": "human_ready" if keep else "hold",
            "reason": reason,
            "page_similarity_jaccard": similarity,
            "min_stable_page_similarity": min_stable_page_similarity,
            "min_one_sided_page_similarity": min_one_sided_page_similarity,
            "ink_threshold": ink_threshold,
            "min_exact_ink_ratio": min_exact_ink_ratio,
            **metrics,
        }
        row["safe_to_merge_gold"] = False
        dispositions[reason] += 1
        if keep:
            row["machine_qa_status"] = "human_ready_after_textlayer_filter"
            row["review_status"] = "needs_human_review"
            passing.append(row)
        else:
            row["machine_qa_status"] = "machine_held_textlayer_pairing"
            row["review_status"] = "machine_held"
            held.append(row)

    report = {
        "goal": "Gold v2.0 Global",
        "mode": "mapped_textlayer_visualdiff_candidate_filter",
        "input_rows": len(rows),
        "passing_rows": len(passing),
        "held_rows": len(held),
        "dispositions": dict(sorted(dispositions.items())),
        "settings": {
            "min_stable_page_similarity": min_stable_page_similarity,
            "min_one_sided_page_similarity": min_one_sided_page_similarity,
            "ink_threshold": ink_threshold,
            "min_exact_ink_ratio": min_exact_ink_ratio,
        },
        "duplicate_pair_ids": duplicate_ids,
        "human_review_required": True,
        "safe_to_merge_gold": False,
        "active_gold_rows_modified": 0,
        "valid": len(passing) + len(held) == len(rows) and not duplicate_ids,
    }
    return passing, held, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hold-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--min-stable-page-similarity", type=float, default=0.45)
    parser.add_argument("--min-one-sided-page-similarity", type=float, default=0.80)
    parser.add_argument("--ink-threshold", type=int, default=180)
    parser.add_argument("--min-exact-ink-ratio", type=float, default=0.01)
    args = parser.parse_args()

    root = args.root.resolve()
    inputs = [path if path.is_absolute() else root / path for path in args.input]
    rows = read_jsonl(inputs)
    passing, held, report = filter_rows(
        root,
        rows,
        min_stable_page_similarity=max(0.0, min(1.0, args.min_stable_page_similarity)),
        min_one_sided_page_similarity=max(0.0, min(1.0, args.min_one_sided_page_similarity)),
        ink_threshold=max(1, min(255, args.ink_threshold)),
        min_exact_ink_ratio=max(0.0, min(1.0, args.min_exact_ink_ratio)),
    )
    report["inputs"] = [path.relative_to(root).as_posix() for path in inputs]
    report["input_sha256"] = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in inputs
    }
    output = args.output if args.output.is_absolute() else root / args.output
    hold_output = args.hold_output if args.hold_output.is_absolute() else root / args.hold_output
    report_path = args.report_json if args.report_json.is_absolute() else root / args.report_json
    write_jsonl(output, passing)
    write_jsonl(hold_output, held)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passing_rows": len(passing), "held_rows": len(held)}, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
