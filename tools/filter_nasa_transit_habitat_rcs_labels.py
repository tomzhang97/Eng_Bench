#!/usr/bin/env python3
"""Curate complete non-tag labels from the NASA Transit Habitat RCS source."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.filter_nasa_transit_habitat_rcs_tags import (  # noqa: E402
    EXPECTED_DOC_ID,
    parse_bbox,
    png_size,
    read_jsonl,
    sha256,
    write_jsonl,
)


@dataclass(frozen=True)
class LabelSpec:
    target_text: str
    category: str
    page_index: int
    window: tuple[float, float, float, float]
    fragments: tuple[str, ...]


def spec(
    target_text: str,
    category: str,
    page_index: int,
    window: tuple[float, float, float, float],
    *fragments: str,
) -> LabelSpec:
    return LabelSpec(target_text, category, page_index, window, tuple(fragments))


DEFAULT_SPECS: tuple[LabelSpec, ...] = (
    # Page 20 symbol legend. Multi-line labels are assembled only from the
    # explicitly named OCR fragments inside the corresponding legend cell.
    spec("Pressurant Tank", "equipment_tag", 20, (680, 1820, 900, 1940), "Pressurant", "Tank"),
    spec("Fuel Tank", "equipment_tag", 20, (900, 1835, 1130, 1930), "Fuel Tank"),
    spec("Test Port", "equipment_tag", 20, (1120, 1820, 1340, 1925), "Test Port"),
    spec(
        "Liq. Gas Phase Separator",
        "equipment_tag",
        20,
        (1360, 1810, 1610, 1940),
        "Liq. Gas Phase",
        "Separator",
    ),
    spec(
        "Fluid Transfer Coupling",
        "equipment_tag",
        20,
        (1600, 1820, 1825, 1950),
        "Fluid Transfer",
        "Coupling",
    ),
    spec("Iso valve", "equipment_tag", 20, (1820, 1830, 2050, 1930), "Iso valve"),
    spec(
        "Pyro Valve Normal Close",
        "equipment_tag",
        20,
        (2040, 1810, 2290, 1940),
        "Pyro Valve",
        "Normal Close",
    ),
    spec(
        "Pyro Valve Normal Open",
        "equipment_tag",
        20,
        (2270, 1810, 2510, 1940),
        "Pyro Valve",
        "Normal Open",
    ),
    spec("Oxidizer Tank", "equipment_tag", 20, (680, 1910, 910, 2010), "Oxidizer Tank"),
    spec(
        "Isovalve w/ Back P Relief",
        "equipment_tag",
        20,
        (900, 1890, 1120, 2020),
        "Isovalve w/",
        "Back P Relief",
    ),
    spec("Orifice", "equipment_tag", 20, (1120, 1900, 1320, 2010), "Orifice"),
    spec("Filter", "equipment_tag", 20, (1360, 1900, 1580, 2010), "Filter"),
    spec("Check valve", "equipment_tag", 20, (1600, 1900, 1825, 2010), "Check valve"),
    spec(
        "Iso Latch valve w/ BPR",
        "equipment_tag",
        20,
        (1820, 1890, 2070, 2020),
        "Iso Latch valve",
        "w/ BPR",
    ),
    spec(
        "Drawing Connection",
        "equipment_tag",
        20,
        (2040, 1885, 2280, 2010),
        "Dawing",
        "Connection",
    ),
    spec("Burst Disk", "equipment_tag", 20, (2270, 1890, 2510, 2010), "Burst Disk"),
    spec("Pressure relief", "equipment_tag", 20, (680, 1980, 920, 2080), "Pressure relief"),
    spec(
        "Single-valve RCS Thruster",
        "equipment_tag",
        20,
        (900, 1970, 1130, 2100),
        "Single-valve",
        "RCS Thruster",
    ),
    spec(
        "Overboard Vent",
        "equipment_tag",
        20,
        (1120, 1970, 1340, 2110),
        "Overboard",
        "Vent",
    ),
    spec(
        "Fill/Drain Valve w. Cap",
        "equipment_tag",
        20,
        (1360, 1970, 1610, 2110),
        "Fill/Drain",
        "Valve w. Cap",
    ),
    spec(
        "Helium Control Valve",
        "equipment_tag",
        20,
        (1600, 1970, 1825, 2110),
        "Helium",
        "Control Valve",
    ),
    spec(
        "Iso Latch Valve",
        "equipment_tag",
        20,
        (1820, 1970, 2050, 2110),
        "Iso Latch",
        "Valve",
    ),
    spec(
        "Pressure Transducer",
        "instrument_tag",
        20,
        (2040, 1970, 2280, 2100),
        "Pressure",
        "Transducer",
    ),
    spec(
        "Temperature Sensor",
        "instrument_tag",
        20,
        (2270, 1970, 2520, 2100),
        "Temperature",
        "Sensor",
    ),
    # Source-unique process and subsystem labels from the same verified figure.
    spec("NTO", "process_label", 20, (600, 1080, 820, 1250), "NTO"),
    spec("MMH", "process_label", 20, (1680, 1080, 1930, 1250), "MMH"),
    spec("POD A", "equipment_tag", 20, (760, 1700, 980, 1840), "POD A"),
    spec("POD B", "equipment_tag", 20, (1340, 1700, 1570, 1840), "POD B"),
    spec("POD C", "equipment_tag", 20, (1800, 1700, 2030, 1840), "POD C"),
    spec("POD D", "equipment_tag", 20, (2380, 1700, 2600, 1840), "POD D"),
    spec(
        "Helium Refilling Assembly",
        "equipment_tag",
        22,
        (1450, 650, 1900, 800),
        "Helium Refilling Assembly",
    ),
)


QUESTION_BY_CATEGORY = {
    "equipment_tag": "What engineering component or equipment label is shown in this region?",
    "instrument_tag": "What instrument label is shown in this region?",
    "process_label": "What process or fluid label is shown in this region?",
}


def center_in_window(
    bbox: tuple[float, float, float, float],
    window: tuple[float, float, float, float],
) -> bool:
    center_x = (bbox[0] + bbox[2]) / 2.0
    center_y = (bbox[1] + bbox[3]) / 2.0
    return window[0] <= center_x <= window[2] and window[1] <= center_y <= window[3]


def _candidate_id(specification: LabelSpec, source_ids: Sequence[str]) -> str:
    value = "|".join(
        [EXPECTED_DOC_ID, str(specification.page_index), specification.target_text, *sorted(source_ids)]
    )
    return "labelcand__" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _joined_fragments(fragments: Sequence[str]) -> str:
    return " ".join(part.strip() for part in fragments if part.strip())


def curate(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    specs: Sequence[LabelSpec] = DEFAULT_SPECS,
    confidence_floor: float = 0.96,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    valid_rows: list[dict[str, Any]] = []
    invalid_reason_by_id: dict[str, str] = {}
    seen_ids: set[str] = set()
    image_sizes: dict[Path, tuple[int, int]] = {}

    for source_row in rows:
        row = dict(source_row)
        candidate_id = str(row.get("candidate_id") or "").strip()
        reason = ""
        if str(row.get("doc_id") or "") != EXPECTED_DOC_ID:
            reason = "unexpected_doc_id"
        elif not candidate_id or candidate_id in seen_ids:
            reason = "missing_or_duplicate_candidate_id"
        else:
            seen_ids.add(candidate_id)
            try:
                confidence = float(row.get("ocr_confidence"))
            except (TypeError, ValueError):
                confidence = -1.0
            if confidence < confidence_floor:
                reason = "below_confidence_floor"
            bbox = parse_bbox(row)
            if not reason and bbox is None:
                reason = "invalid_bbox"
            image_value = str(row.get("image_path") or "").strip()
            image_path = (root / image_value).resolve()
            try:
                image_path.relative_to(root)
            except ValueError:
                reason = reason or "image_path_outside_root"
            if not reason and not image_path.is_file():
                reason = "missing_page_image"
            if not reason:
                try:
                    if image_path not in image_sizes:
                        image_sizes[image_path] = png_size(image_path)
                    width, height = image_sizes[image_path]
                except (OSError, ValueError):
                    reason = "invalid_page_image"
                else:
                    assert bbox is not None
                    if bbox[2] > width or bbox[3] > height:
                        reason = "bbox_outside_page"
        if reason:
            invalid_reason_by_id[candidate_id] = reason
        else:
            valid_rows.append(row)

    selected: list[dict[str, Any]] = []
    consumed_ids: set[str] = set()
    matched_ids: set[str] = set()
    missing_specs: list[dict[str, Any]] = []
    target_texts: set[str] = set()

    for specification in specs:
        if specification.target_text.casefold() in target_texts:
            raise ValueError(f"duplicate target spec: {specification.target_text}")
        target_texts.add(specification.target_text.casefold())
        chosen: list[dict[str, Any]] = []
        missing_fragments: list[str] = []
        for fragment in specification.fragments:
            candidates: list[dict[str, Any]] = []
            for row in valid_rows:
                if int(row.get("page_index", -1)) != specification.page_index:
                    continue
                if str(row.get("proposed_text") or "").strip() != fragment:
                    continue
                bbox = parse_bbox(row)
                assert bbox is not None
                if center_in_window(bbox, specification.window):
                    candidates.append(row)
                    matched_ids.add(str(row["candidate_id"]))
            if not candidates:
                missing_fragments.append(fragment)
                continue
            candidates.sort(
                key=lambda row: (
                    -float(row.get("ocr_confidence") or 0),
                    str(row.get("candidate_id") or ""),
                )
            )
            chosen.append(candidates[0])
        if missing_fragments:
            missing_specs.append(
                {
                    "target_text": specification.target_text,
                    "page_index": specification.page_index,
                    "missing_fragments": missing_fragments,
                }
            )
            continue

        source_ids = [str(row["candidate_id"]) for row in chosen]
        if any(source_id in consumed_ids for source_id in source_ids):
            raise ValueError(f"source fragment reused by spec: {specification.target_text}")
        consumed_ids.update(source_ids)
        bboxes = [parse_bbox(row) for row in chosen]
        assert all(bbox is not None for bbox in bboxes)
        typed_bboxes = [bbox for bbox in bboxes if bbox is not None]
        bbox = [
            int(min(value[0] for value in typed_bboxes)),
            int(min(value[1] for value in typed_bboxes)),
            int(max(value[2] for value in typed_bboxes)),
            int(max(value[3] for value in typed_bboxes)),
        ]
        representative = chosen[0]
        raw_fragments = [str(row.get("proposed_text") or "").strip() for row in chosen]
        joined = _joined_fragments(raw_fragments)
        output = dict(representative)
        output.update(
            {
                "candidate_id": _candidate_id(specification, source_ids),
                "bbox": bbox,
                "raw_text": joined,
                "target_text": specification.target_text,
                "proposed_text": specification.target_text,
                "corrected_text": "",
                "text_context": specification.target_text,
                "category": specification.category,
                "question_text": QUESTION_BY_CATEGORY[specification.category],
                "split": "test",
                "review_status": "needs_review",
                "promotion_state": "unreviewed_candidate",
                "source_candidate_ids": source_ids,
                "machine_text_assembled": len(source_ids) > 1,
                "machine_text_corrected": joined.casefold() != specification.target_text.casefold(),
                "machine_qa_status": "strict_rcs_complete_label_pass_pending_visual_review",
                "machine_qa_notes": (
                    "Complete source-specific engineering label reconstructed only from exact OCR "
                    "fragments inside a visually verified page window. Human visual review is still required."
                ),
                "ocr_confidence": min(float(row.get("ocr_confidence") or 0) for row in chosen),
                "safe_to_merge_gold": False,
            }
        )
        selected.append(output)

    selected.sort(
        key=lambda row: (
            int(row.get("page_index", -1)),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
        )
    )
    held: list[dict[str, Any]] = []
    hold_reasons: Counter[str] = Counter()
    for source_row in rows:
        candidate_id = str(source_row.get("candidate_id") or "").strip()
        if candidate_id in consumed_ids:
            continue
        reason = invalid_reason_by_id.get(candidate_id)
        if not reason:
            reason = (
                "duplicate_fragment_candidate"
                if candidate_id in matched_ids
                else "not_a_complete_supported_rcs_non_tag_label"
            )
        output = dict(source_row)
        output["machine_qa_status"] = "machine_held_nasa_rcs_non_tag_label_filter"
        output["machine_hold_reason"] = reason
        output["safe_to_merge_gold"] = False
        held.append(output)
        hold_reasons[reason] += 1

    report = {
        "goal": "Gold v2.0 Global",
        "source_doc_id": EXPECTED_DOC_ID,
        "source_rows": len(rows),
        "selected_rows": len(selected),
        "consumed_source_rows": len(consumed_ids),
        "held_source_rows": len(held),
        "selected_unique_targets": len({str(row["target_text"]).casefold() for row in selected}),
        "selected_by_category": dict(sorted(Counter(str(row["category"]) for row in selected).items())),
        "selected_by_page": dict(sorted(Counter(str(row["page_index"]) for row in selected).items())),
        "assembled_rows": sum(bool(row["machine_text_assembled"]) for row in selected),
        "machine_corrected_rows": sum(bool(row["machine_text_corrected"]) for row in selected),
        "missing_specs": missing_specs,
        "hold_reasons": dict(sorted(hold_reasons.items())),
        "confidence_floor": confidence_floor,
        "split_policy": "whole source family reserved for test",
        "safe_to_merge_gold": False,
    }
    return selected, held, report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--confidence-floor", type=float, default=0.96)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    input_path = (root / args.input).resolve()
    selected_path = (root / args.selected_output).resolve()
    held_path = (root / args.held_output).resolve()
    report_path = (root / args.report_json).resolve()
    selected, held, report = curate(
        read_jsonl(input_path),
        root=root,
        confidence_floor=args.confidence_floor,
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
    return 2 if report["missing_specs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
