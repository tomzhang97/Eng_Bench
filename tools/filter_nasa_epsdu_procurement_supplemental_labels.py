#!/usr/bin/env python3
"""Curate a fail-closed supplemental label set from NASA EPSDU procurement tables."""
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

from tools.filter_nasa_epsdu_procurement_candidates import (  # noqa: E402
    EXPECTED_DOC_ID,
    parse_bbox,
    png_size,
    read_jsonl,
    sha256,
    write_jsonl,
)


@dataclass(frozen=True)
class FragmentSpec:
    candidate_id: str
    page_index: int
    raw_text: str


@dataclass(frozen=True)
class LabelSpec:
    target_text: str
    category: str
    fragments: tuple[FragmentSpec, ...]


def fragment(candidate_id: str, page_index: int, raw_text: str) -> FragmentSpec:
    return FragmentSpec(candidate_id, page_index, raw_text)


def spec(target_text: str, category: str, *fragments: FragmentSpec) -> LabelSpec:
    return LabelSpec(target_text, category, tuple(fragments))


DEFAULT_SPECS: tuple[LabelSpec, ...] = (
    spec("Boule Cart", "equipment_tag", fragment("ocrcand__2b96ca1e0c3b770fc405", 51, "Boule Cart")),
    spec(
        "Silica Drum Packer",
        "equipment_tag",
        fragment("ocrcand__eb344059b8de94f49d0e", 51, "Silica Drum Packer"),
    ),
    spec("Waste Burners", "equipment_tag", fragment("ocrcand__7f50d8386767ac67f1e9", 52, "Waste Burners")),
    spec(
        "Silica Agglomerator",
        "equipment_tag",
        fragment("ocrcand__87fd75c0facc020061dc", 52, "Silica Agglomerator"),
    ),
    spec("Cooling Tower", "equipment_tag", fragment("ocrcand__e35ddb039c691023dae8", 52, "Cooling Tower")),
    spec(
        "Cooling Tower Treatment",
        "process_label",
        fragment("ocrcand__4a18ed93ff5276e7b67f", 52, "Cooling Tower Treatment"),
    ),
    spec(
        "Refrigeration System",
        "equipment_tag",
        fragment("ocrcand__1c4ed5733219f9931b38", 52, "Refrigeration System"),
    ),
    spec(
        "Quality Control Trailer",
        "equipment_tag",
        fragment("ocrcand__f9b74dc973e5c0cb8d4b", 53, "Quality Control Trailer"),
    ),
    spec(
        "UV Spectrophotometer",
        "instrument_tag",
        fragment("ocrcand__53ba63a5df5397d32a9d", 54, "UV Spectrophotometer"),
    ),
    spec("Agitator", "equipment_tag", fragment("ocrcand__fef8323e7d25ea8efd21", 54, "Agitator")),
    spec(
        "Solids Conveyor",
        "equipment_tag",
        fragment("ocrcand__b38fe6e68b90baf10ba6", 55, "Solids Conveyor"),
    ),
    spec(
        "Pressure Lubricators",
        "equipment_tag",
        fragment("ocrcand__bdb1732224e2226b05a5", 57, "Pressure"),
        fragment("ocrcand__ba77f4fec66bbd7f6adf", 57, "Lubricators"),
    ),
    spec(
        "Thermocouples",
        "instrument_tag",
        fragment("ocrcand__aaec0496375f18c5ffd5", 57, "Thermocouples"),
    ),
    spec(
        "Sewer Tie-In Line",
        "pipe_line_tag",
        fragment("ocrcand__cedb59819bf908ae4371", 57, "Sewer Tie-In Line"),
    ),
    spec(
        "Lever Transmitters & Switches",
        "instrument_tag",
        fragment("ocrcand__ddba3450d86d700adb28", 57, "Lever Transmitters"),
        fragment("ocrcand__0a90fcc0c363e0376a5e", 57, "& Switches"),
    ),
    spec(
        "Sample Conditioners",
        "equipment_tag",
        fragment("ocrcand__5cafab2116f170d147d4", 57, "Sample Conditioners"),
    ),
    spec(
        "Level Guage",
        "instrument_tag",
        fragment("ocrcand__3c7cb9456cfca6814f52", 65, "Level Guage"),
    ),
    spec(
        "HCl Monitor",
        "instrument_tag",
        fragment("ocrcand__5ee184afd7a3ad29ec3e", 66, "HC1 Monitor"),
    ),
    spec(
        "Pressure Indicators",
        "instrument_tag",
        fragment("ocrcand__1a3f9823d68638717ec2", 68, "Pressure Indicators"),
    ),
    spec(
        "Pressure Guages",
        "instrument_tag",
        fragment("ocrcand__98556e53f6ac2c745042", 68, "Pressure Guages"),
    ),
    spec(
        "Temperature Indicators",
        "instrument_tag",
        fragment("ocrcand__4415d86c363d4638026c", 68, "Temperature Indicators"),
    ),
    spec(
        "Back Flow Preventers",
        "equipment_tag",
        fragment("ocrcand__f4d8a966972bb8a1f96f", 68, "Back Flow Preventers"),
    ),
    spec(
        "Porex Breathers",
        "equipment_tag",
        fragment("ocrcand__ac461fec86c3fa55e7b9", 69, "Porex Breathers"),
    ),
    spec("Caged Ladder", "equipment_tag", fragment("ocrcand__c07026da3df4374a0971", 71, "Caged Ladder")),
    spec(
        "Plant Emergency Trip System",
        "instrument_tag",
        fragment("ocrcand__8859dc40f749c7612358", 71, "Plant Erergency"),
        fragment("ocrcand__367503870629cecdd40a", 71, "Trip System"),
    ),
    spec(
        "Bursting Discs",
        "equipment_tag",
        fragment("ocrcand__8f5e1a8ae180eadacb06", 63, "Bursting Discs"),
    ),
)


QUESTION_BY_CATEGORY = {
    "equipment_tag": "What engineering component or equipment label is shown in this region?",
    "instrument_tag": "What instrument label is shown in this region?",
    "pipe_line_tag": "What pipe or line label is shown in this region?",
    "process_label": "What process or fluid label is shown in this region?",
}


def _candidate_id(specification: LabelSpec) -> str:
    value = "|".join(
        [
            EXPECTED_DOC_ID,
            specification.target_text,
            *(fragment.candidate_id for fragment in specification.fragments),
        ]
    )
    return "labelcand__" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def curate(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    specs: Sequence[LabelSpec] = DEFAULT_SPECS,
    confidence_floor: float = 0.98,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    row_by_id: dict[str, dict[str, Any]] = {}
    invalid_reason_by_id: dict[str, str] = {}
    image_sizes: dict[Path, tuple[int, int]] = {}

    for source_row in rows:
        row = dict(source_row)
        candidate_id = str(row.get("candidate_id") or "").strip()
        reason = ""
        if str(row.get("doc_id") or "") != EXPECTED_DOC_ID:
            reason = "unexpected_doc_id"
        elif not candidate_id or candidate_id in row_by_id:
            reason = "missing_or_duplicate_candidate_id"
        else:
            try:
                confidence = float(row.get("ocr_confidence"))
            except (TypeError, ValueError):
                confidence = -1.0
            if confidence < confidence_floor:
                reason = "below_confidence_floor"
            bbox = parse_bbox(row)
            if not reason and bbox is None:
                reason = "invalid_bbox"
            image_path = (root / str(row.get("image_path") or "")).resolve()
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
        if candidate_id and candidate_id not in row_by_id:
            row_by_id[candidate_id] = row
        if reason:
            invalid_reason_by_id[candidate_id] = reason

    selected: list[dict[str, Any]] = []
    consumed_ids: set[str] = set()
    missing_specs: list[dict[str, Any]] = []
    target_keys: set[str] = set()

    for specification in specs:
        target_key = specification.target_text.casefold()
        if target_key in target_keys:
            raise ValueError(f"duplicate target spec: {specification.target_text}")
        target_keys.add(target_key)
        chosen: list[dict[str, Any]] = []
        issues: list[str] = []
        pages: set[int] = set()
        for expected in specification.fragments:
            row = row_by_id.get(expected.candidate_id)
            if row is None:
                issues.append(f"missing:{expected.candidate_id}")
                continue
            if expected.candidate_id in invalid_reason_by_id:
                issues.append(f"invalid:{expected.candidate_id}:{invalid_reason_by_id[expected.candidate_id]}")
                continue
            if int(row.get("page_index", -1)) != expected.page_index:
                issues.append(f"page_mismatch:{expected.candidate_id}")
                continue
            if str(row.get("proposed_text") or "").strip() != expected.raw_text:
                issues.append(f"text_mismatch:{expected.candidate_id}")
                continue
            chosen.append(row)
            pages.add(expected.page_index)
        if len(pages) > 1:
            issues.append("fragments_cross_pages")
        if issues:
            missing_specs.append({"target_text": specification.target_text, "issues": issues})
            continue

        source_ids = [str(row["candidate_id"]) for row in chosen]
        if any(candidate_id in consumed_ids for candidate_id in source_ids):
            raise ValueError(f"source fragment reused by spec: {specification.target_text}")
        consumed_ids.update(source_ids)
        bboxes = [parse_bbox(row) for row in chosen]
        assert all(bbox is not None for bbox in bboxes)
        typed_bboxes = [bbox for bbox in bboxes if bbox is not None]
        raw_fragments = [str(row.get("proposed_text") or "").strip() for row in chosen]
        raw_text = " ".join(fragment for fragment in raw_fragments if fragment)
        representative = chosen[0]
        output = dict(representative)
        output.update(
            {
                "candidate_id": _candidate_id(specification),
                "bbox": [
                    int(min(bbox[0] for bbox in typed_bboxes)),
                    int(min(bbox[1] for bbox in typed_bboxes)),
                    int(max(bbox[2] for bbox in typed_bboxes)),
                    int(max(bbox[3] for bbox in typed_bboxes)),
                ],
                "raw_text": raw_text,
                "target_text": specification.target_text,
                "proposed_text": specification.target_text,
                "corrected_text": "",
                "text_context": specification.target_text,
                "category": specification.category,
                "question_text": QUESTION_BY_CATEGORY[specification.category],
                "split": "train",
                "review_status": "needs_review",
                "promotion_state": "unreviewed_candidate",
                "source_candidate_ids": source_ids,
                "machine_text_assembled": len(source_ids) > 1,
                "machine_text_corrected": raw_text.casefold() != specification.target_text.casefold(),
                "machine_qa_status": "strict_epsdu_supplemental_label_pass_pending_visual_review",
                "machine_qa_notes": (
                    "Source-specific label selected only by immutable OCR candidate identity, exact raw text, "
                    "page, confidence, image, and bbox checks. Human visual review is still required."
                ),
                "ocr_confidence": min(float(row.get("ocr_confidence") or 0) for row in chosen),
                "safe_to_merge_gold": False,
            }
        )
        selected.append(output)

    selected.sort(
        key=lambda row: (int(row.get("page_index", -1)), float(row["bbox"][1]), float(row["bbox"][0]))
    )
    held: list[dict[str, Any]] = []
    hold_reasons: Counter[str] = Counter()
    for source_row in rows:
        candidate_id = str(source_row.get("candidate_id") or "").strip()
        if candidate_id in consumed_ids:
            continue
        output = dict(source_row)
        reason = invalid_reason_by_id.get(candidate_id, "not_an_approved_epsdu_supplemental_label")
        output["machine_qa_status"] = "machine_held_nasa_epsdu_supplemental_label_filter"
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
        "split_policy": "whole source family reserved for train",
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
    parser.add_argument("--confidence-floor", type=float, default=0.98)
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
