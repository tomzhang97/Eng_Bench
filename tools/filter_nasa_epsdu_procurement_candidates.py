#!/usr/bin/env python3
"""Shortlist complete engineering labels from NASA EPSDU procurement tables."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


EXPECTED_DOC_ID = "nasa_19810021977_epsdu_equipment_procurement"
EQUIPMENT_NUMBER_RE = re.compile(r"^\d{3}-\d{2}(?:\s*[,.;]\s*\d{2}){0,4}$")
PURCHASE_ORDER_RE = re.compile(r"^\d{5}$")
EQUIPMENT_TERMS = {
    "air dryer",
    "air package",
    "blower",
    "column",
    "compressor",
    "condenser",
    "contactor",
    "cooler",
    "dryer",
    "filter",
    "furnace",
    "generator",
    "heat exchanger",
    "heater",
    "hood",
    "lockhopper",
    "melter",
    "motor",
    "panel",
    "pump",
    "reactor",
    "receiver",
    "scale",
    "scrubber",
    "silo",
    "strainer",
    "tank",
    "transformer",
    "valve",
    "venturi",
}
PROCESS_LABEL_TERMS = {
    "computer components",
    "expansion joints",
    "field instrumentation",
    "flange gasket",
    "gasket",
    "instrument equipment list",
    "instrumentation",
    "insulation",
    "o rings",
    "packing",
    "pipe fittings",
    "piping",
    "plant emergency trip system",
}
GENERIC_EXACT = {
    "column",
    "coolers",
    "filters",
    "panel a",
    "tanks",
    "tanks reactors",
    "valves",
}
HEADER_TERMS = {
    "appendix",
    "description",
    "equipment no name",
    "equipment number",
    "equipment procurement status",
    "inquiry",
    "june 1981",
    "p o number",
    "po number",
    "promise",
    "purchase order",
    "status",
    "vendor",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    if header[12:16] != b"IHDR":
        raise ValueError(f"PNG is missing IHDR: {path}")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid PNG dimensions: {path}")
    return width, height


def parse_bbox(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    value = row.get("bbox")
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if left < 0 or top < 0 or right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def overlap_over_smaller(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = width * height
    if intersection <= 0:
        return 0.0
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    return intersection / min(left_area, right_area)


def normalized_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def classify(text: str, original_category: str) -> tuple[str | None, str]:
    stripped = " ".join(text.strip().split())
    key = normalized_key(stripped)
    if not key:
        return None, "empty_text"
    if PURCHASE_ORDER_RE.fullmatch(stripped):
        return None, "purchase_order_number"
    if any(term in key for term in HEADER_TERMS):
        return None, "procurement_header"
    if key in GENERIC_EXACT:
        return None, "generic_non_atomic_label"
    if EQUIPMENT_NUMBER_RE.fullmatch(stripped):
        return "equipment_tag", "equipment_number"
    if len(stripped) < 4 or len(stripped) > 64:
        return None, "text_length_outside_bounds"
    if sum(character.isalpha() for character in stripped) < 3:
        return None, "insufficient_letters"
    if any(term in key for term in PROCESS_LABEL_TERMS):
        return "process_label", "process_label_phrase"
    if original_category in {"equipment_tag", "pin_label"} and any(
        term in key for term in EQUIPMENT_TERMS
    ):
        return "equipment_tag", "recognized_equipment_phrase"
    if any(term in key for term in EQUIPMENT_TERMS):
        return "equipment_tag", "recognized_equipment_phrase"
    return None, "unsupported_procurement_text"


def shortlist(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    phrase_confidence_floor: float = 0.92,
    equipment_number_confidence_floor: float = 0.98,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    provisional: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    hold_reasons: Counter[str] = Counter()
    image_sizes: dict[Path, tuple[int, int]] = {}
    seen_ids: set[str] = set()

    def hold(source_row: dict[str, Any], reason: str) -> None:
        output = dict(source_row)
        output["machine_qa_status"] = "machine_held_nasa_epsdu_procurement_filter"
        output["machine_hold_reason"] = reason
        output["safe_to_merge_gold"] = False
        held.append(output)
        hold_reasons[reason] += 1

    for source_row in rows:
        row = dict(source_row)
        if str(row.get("doc_id") or "") != EXPECTED_DOC_ID:
            hold(row, "unexpected_doc_id")
            continue
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in seen_ids:
            hold(row, "missing_or_duplicate_candidate_id")
            continue
        seen_ids.add(candidate_id)
        text = " ".join(str(row.get("proposed_text") or "").strip().split())
        category, classification = classify(text, str(row.get("category") or ""))
        if category is None:
            hold(row, classification)
            continue
        try:
            confidence = float(row.get("ocr_confidence"))
        except (TypeError, ValueError):
            hold(row, "missing_or_invalid_confidence")
            continue
        floor = (
            equipment_number_confidence_floor
            if classification == "equipment_number"
            else phrase_confidence_floor
        )
        if confidence < floor:
            hold(row, "below_confidence_floor")
            continue
        bbox = parse_bbox(row)
        if bbox is None:
            hold(row, "invalid_bbox")
            continue
        page_index = int(row.get("page_index", -1))
        if page_index < 47 or page_index > 71:
            hold(row, "outside_selected_appendix_pages")
            continue
        image_value = str(row.get("image_path") or "").strip()
        image_path = (root / image_value).resolve()
        try:
            image_path.relative_to(root)
        except ValueError:
            hold(row, "image_path_outside_root")
            continue
        if not image_path.is_file():
            hold(row, "missing_page_image")
            continue
        try:
            if image_path not in image_sizes:
                image_sizes[image_path] = png_size(image_path)
            width, height = image_sizes[image_path]
        except (OSError, ValueError):
            hold(row, "invalid_page_image")
            continue
        if bbox[2] > width or bbox[3] > height:
            hold(row, "bbox_outside_page")
            continue

        output = dict(row)
        output["raw_text"] = text
        output["target_text"] = text
        output["proposed_text"] = text
        output["text_context"] = text
        output["category"] = category
        output["question_text"] = (
            "What equipment identifier or equipment label is shown in this region?"
            if category == "equipment_tag"
            else "What engineering process label is shown in this region?"
        )
        output["review_status"] = "needs_review"
        output["promotion_state"] = "unreviewed_candidate"
        output["machine_original_category"] = row.get("category")
        output["machine_filter_classification"] = classification
        output["machine_filter_confidence_floor"] = floor
        output["machine_qa_status"] = "strict_procurement_phrase_pass_pending_visual_qa"
        output["machine_qa_notes"] = (
            "Passed source-specific phrase, confidence, page, image, and bounding-box "
            "checks. Human review remains required after crop-plus-page visual QA."
        )
        output["safe_to_merge_gold"] = False
        provisional.append(output)

    provisional.sort(
        key=lambda row: (
            -float(row.get("ocr_confidence") or 0),
            int(row.get("page_index", -1)),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
        )
    )
    selected: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    seen_regions: list[tuple[int, tuple[float, float, float, float]]] = []
    for row in provisional:
        page_index = int(row["page_index"])
        bbox = parse_bbox(row)
        assert bbox is not None
        if any(
            prior_page == page_index and overlap_over_smaller(prior_bbox, bbox) >= 0.75
            for prior_page, prior_bbox in seen_regions
        ):
            hold(row, "duplicate_overlapping_region")
            continue
        text_key = normalized_key(str(row["proposed_text"]))
        if text_key in seen_text:
            hold(row, "duplicate_normalized_text_in_source")
            continue
        seen_text.add(text_key)
        seen_regions.append((page_index, bbox))
        selected.append(row)

    selected.sort(
        key=lambda row: (
            int(row.get("page_index", -1)),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
        )
    )
    report = {
        "goal": "Gold v2.0 Global",
        "source_doc_id": EXPECTED_DOC_ID,
        "source_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "selected_unique_normalized_text": len(seen_text),
        "selected_by_category": dict(sorted(Counter(str(row["category"]) for row in selected).items())),
        "selected_by_page": dict(sorted(Counter(str(row["page_index"]) for row in selected).items(), key=lambda item: int(item[0]))),
        "hold_reasons": dict(sorted(hold_reasons.items())),
        "phrase_confidence_floor": phrase_confidence_floor,
        "equipment_number_confidence_floor": equipment_number_confidence_floor,
        "safe_to_merge_gold": False,
    }
    return selected, held, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--phrase-confidence-floor", type=float, default=0.92)
    parser.add_argument("--equipment-number-confidence-floor", type=float, default=0.98)
    args = parser.parse_args()

    root = args.root.resolve()
    input_path = (root / args.input).resolve()
    selected_path = (root / args.selected_output).resolve()
    held_path = (root / args.held_output).resolve()
    report_path = (root / args.report_json).resolve()
    selected, held, report = shortlist(
        read_jsonl(input_path),
        root=root,
        phrase_confidence_floor=args.phrase_confidence_floor,
        equipment_number_confidence_floor=args.equipment_number_confidence_floor,
    )
    write_jsonl(selected_path, selected)
    write_jsonl(held_path, held)
    report.update(
        {
            "input": input_path.as_posix(),
            "input_sha256": sha256(input_path),
            "selected_output": selected_path.as_posix(),
            "selected_sha256": sha256(selected_path),
            "held_output": held_path.as_posix(),
            "held_sha256": sha256(held_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
