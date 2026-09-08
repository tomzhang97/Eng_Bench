#!/usr/bin/env python3
"""Shortlist source-unique NASA Transit Habitat RCS tags for human review."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


EXPECTED_DOC_ID = "nasa_20240015687_transit_habitat_rcs_propulsion"
TAG_RE = re.compile(r"^([A-Z0-9]{2,5})(\d{3})$")
PREFIX_ALIASES = {
    "0IV": "OIV",
    "0HC": "OHC",
    "0HV": "OHV",
    "0TP": "OTP",
    "0BV": "OBV",
    "00R": "OOR",
    "05V": "OSV",
    "O5V": "OSV",
    "QTP": "OTP",
    "QBD": "OBD",
    "DIV": "OIV",
    "HIP": "HTP",
}
VALID_NUMBERS = {
    "HIV": set(range(102, 118, 2)),
    "HHC": set(range(102, 110, 2)),
    "HPV": set(range(101, 109)),
    "HSV": {102},
    "HCV": set(range(102, 110, 2)),
    "HOV": {102, 104},
    "HOR": set(range(102, 112, 2)),
    "HTP": set(range(102, 140, 2)),
    "OIV": set(range(102, 142, 2)),
    "OHC": set(range(102, 134, 2)),
    "OHV": set(range(102, 138, 2)),
    "OTP": set(range(102, 124, 2)),
    "OBD": set(range(102, 110, 2)),
    "OPR": set(range(102, 110, 2)),
    "OSV": set(range(102, 110, 2)),
    "OOR": {102},
    "OBV": set(range(142, 174, 2)) | set(range(173, 183)),
    "OPV": {102, 104},
    "FBV": set(range(173, 183)),
}
FIGURE_Y_WINDOWS = {
    20: ((0.10, 0.50),),
    22: ((0.10, 0.50),),
    35: ((0.37, 0.57),),
    36: ((0.09, 0.25), (0.57, 0.77)),
    37: ((0.22, 0.44),),
    38: ((0.11, 0.27),),
    39: ((0.57, 0.83),),
    41: ((0.07, 0.30),),
    42: ((0.08, 0.34),),
    43: ((0.07, 0.44),),
    54: ((0.11, 0.48),),
    55: ((0.11, 0.48),),
}
PAGE_PRIORITY = {
    35: 0,
    36: 0,
    37: 0,
    38: 0,
    39: 0,
    41: 0,
    42: 0,
    43: 0,
    20: 1,
    22: 1,
    54: 2,
    55: 2,
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
    return struct.unpack(">II", header[16:24])


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


def normalized_token(value: str) -> tuple[str, str, bool] | None:
    raw = re.sub(r"[^A-Z0-9]", "", value.upper())
    match = TAG_RE.fullmatch(raw)
    if not match:
        return None
    raw_prefix, number_text = match.groups()
    prefix = PREFIX_ALIASES.get(raw_prefix, raw_prefix)
    number = int(number_text)
    if number not in VALID_NUMBERS.get(prefix, set()):
        return None
    return raw, f"{prefix}{number_text}", raw_prefix != prefix


def within_figure(page_index: int, bbox: tuple[float, float, float, float], height: int) -> bool:
    center = ((bbox[1] + bbox[3]) / 2.0) / height
    return any(low <= center <= high for low, high in FIGURE_Y_WINDOWS.get(page_index, ()))


def shortlist(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    exact_confidence_floor: float = 0.82,
    corrected_confidence_floor: float = 0.88,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    held: list[dict[str, Any]] = []
    provisional: list[dict[str, Any]] = []
    hold_reasons: Counter[str] = Counter()
    image_sizes: dict[Path, tuple[int, int]] = {}
    seen_ids: set[str] = set()

    def hold(source_row: dict[str, Any], reason: str) -> None:
        output = dict(source_row)
        output["machine_qa_status"] = "machine_held_nasa_rcs_tag_filter"
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
        token = normalized_token(str(row.get("proposed_text") or ""))
        if token is None:
            hold(row, "not_a_supported_rcs_tag")
            continue
        raw_text, normalized_text, corrected = token
        try:
            confidence = float(row.get("ocr_confidence"))
        except (TypeError, ValueError):
            hold(row, "missing_or_invalid_confidence")
            continue
        floor = corrected_confidence_floor if corrected else exact_confidence_floor
        if confidence < floor:
            hold(row, "below_confidence_floor")
            continue
        bbox = parse_bbox(row)
        if bbox is None:
            hold(row, "invalid_bbox")
            continue
        page_index = int(row.get("page_index", -1))
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
        if not within_figure(page_index, bbox, height):
            hold(row, "outside_verified_figure_window")
            continue

        output = dict(row)
        output["raw_text"] = raw_text
        output["target_text"] = normalized_text
        output["proposed_text"] = normalized_text
        output["text_context"] = normalized_text
        output["category"] = "instrument_tag"
        output["question_text"] = "What instrument, valve, or test-port tag is shown in this region?"
        output["review_status"] = "needs_review"
        output["promotion_state"] = "unreviewed_candidate"
        output["machine_original_category"] = row.get("category")
        output["machine_text_normalized"] = corrected
        output["machine_qa_status"] = "strict_rcs_tag_pass_pending_visual_review"
        output["machine_filter_confidence_floor"] = floor
        output["machine_qa_notes"] = (
            "Passed source-specific tag prefix, engineering sequence, confidence, "
            "page-bounds, and verified figure-window gates. Repeated normalized tags "
            "within this source are held. Human visual review is still required."
        )
        output["safe_to_merge_gold"] = False
        provisional.append(output)

    provisional.sort(
        key=lambda row: (
            bool(row.get("machine_text_normalized")),
            PAGE_PRIORITY.get(int(row.get("page_index", -1)), 99),
            -float(row.get("ocr_confidence") or 0),
            int(row.get("page_index", -1)),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
        )
    )
    selected: list[dict[str, Any]] = []
    selected_regions: list[tuple[int, tuple[float, float, float, float]]] = []
    seen_text: set[str] = set()
    for row in provisional:
        page_index = int(row["page_index"])
        bbox = parse_bbox(row)
        assert bbox is not None
        if any(
            prior_page == page_index and overlap_over_smaller(prior_bbox, bbox) >= 0.75
            for prior_page, prior_bbox in selected_regions
        ):
            hold(row, "duplicate_overlapping_region")
            continue
        normalized = str(row["proposed_text"])
        if normalized in seen_text:
            hold(row, "duplicate_normalized_text_in_source")
            continue
        seen_text.add(normalized)
        selected_regions.append((page_index, bbox))
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
        "selected_unique_normalized_tags": len(seen_text),
        "selected_by_page": dict(sorted(Counter(str(row["page_index"]) for row in selected).items())),
        "selected_by_prefix": dict(sorted(Counter(str(row["proposed_text"])[:3] for row in selected).items())),
        "machine_normalized_rows": sum(bool(row.get("machine_text_normalized")) for row in selected),
        "hold_reasons": dict(sorted(hold_reasons.items())),
        "exact_confidence_floor": exact_confidence_floor,
        "corrected_confidence_floor": corrected_confidence_floor,
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
    parser.add_argument("--exact-confidence-floor", type=float, default=0.82)
    parser.add_argument("--corrected-confidence-floor", type=float, default=0.88)
    args = parser.parse_args()

    root = args.root.resolve()
    input_path = (root / args.input).resolve()
    selected_path = (root / args.selected_output).resolve()
    held_path = (root / args.held_output).resolve()
    report_path = (root / args.report_json).resolve()
    selected, held, report = shortlist(
        read_jsonl(input_path),
        root=root,
        exact_confidence_floor=args.exact_confidence_floor,
        corrected_confidence_floor=args.corrected_confidence_floor,
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
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
