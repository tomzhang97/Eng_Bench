#!/usr/bin/env python3
"""Curate a fail-closed supplemental label set from NASA coal-gasification diagrams."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.filter_nasa_epsdu_procurement_candidates import (  # noqa: E402
    parse_bbox,
    png_size,
    read_jsonl,
    sha256,
    write_jsonl,
)


EXPECTED_DOC_ID = "nasa_19810009690_coal_gasification_process_flows"


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
    spec("1-CV-3", "equipment_tag", fragment("ocrcand__ca7728c604444b5fa1b7", 113, "1-CV-3")),
    spec("1-S-3", "equipment_tag", fragment("ocrcand__610759adea0804577d44", 113, "1-S-3")),
    spec("11-S-1", "equipment_tag", fragment("ocrcand__1d445ec7bd537fa888db", 113, "11-S-1")),
    spec(
        "SCREW CONVEYOR",
        "equipment_tag",
        fragment("ocrcand__6d355ec6d9a27d67e635", 113, "SCREW"),
        fragment("ocrcand__a6b6bf82bd832578730d", 113, "CONVEYOR"),
    ),
    spec(
        "WEIGH BELT CONVEYOR",
        "equipment_tag",
        fragment("ocrcand__d26aa70d681d8a086ee5", 113, "WEIGH BELT"),
        fragment("ocrcand__1fac4a8785ad1dbcfa32", 113, "CONVEYOR"),
    ),
    spec(
        "PRIMARY CRUSHER",
        "equipment_tag",
        fragment("ocrcand__1b6f14adf5b96b4266c0", 113, "PRIMARY"),
        fragment("ocrcand__7f0108f933926ffeec51", 113, "CRUSHER"),
    ),
    spec(
        "HOLDING TANKS",
        "equipment_tag",
        fragment("ocrcand__f3678510a1e19014f204", 113, "HOLDING"),
        fragment("ocrcand__387633f67dc652d8d427", 113, "TANKS"),
    ),
    spec(
        "SECOND SLURRY TANK",
        "equipment_tag",
        fragment("ocrcand__2dc0cdf7e781fdb45b40", 113, "SECOND"),
        fragment("ocrcand__1237c1cb04cd9e8a8cfb", 113, "SLURRY"),
        fragment("ocrcand__e80422c5a4b264eaf673", 113, "TANK"),
    ),
    spec(
        "SLURRY CONTROL TANK",
        "equipment_tag",
        fragment("ocrcand__301c573597f3a57fe002", 113, "SLURRY"),
        fragment("ocrcand__058ed662119a2a13316f", 113, "CONTROL"),
        fragment("ocrcand__4787c95a42fc2ea0aa64", 113, "TANK"),
    ),
    spec("1-S-2", "equipment_tag", fragment("ocrcand__0da681fe90a69ce69d27", 113, "1-S-2")),
    spec("1-TK-4A", "equipment_tag", fragment("ocrcand__11e3a273f1f40fe3ea7f", 113, "1-TK-4A")),
    spec("5-E-4", "equipment_tag", fragment("ocrcand__953599f445d1f72168ad", 117, "5-E-4")),
    spec("5-E-3", "equipment_tag", fragment("ocrcand__b982e52d63271bcdfed5", 117, "5-E-3")),
    spec("5-F-1", "equipment_tag", fragment("ocrcand__966260780417301dcdfd", 117, "5-F-1")),
    spec("5-T-2", "equipment_tag", fragment("ocrcand__42a1fba8002e0be57f2c", 117, "5-T-2")),
    spec("5-E-6", "equipment_tag", fragment("ocrcand__c36d79fe9992364acabe", 117, "5-E-6")),
    spec("5-S-2", "equipment_tag", fragment("ocrcand__faa1048cc965f8f44647", 117, "5-S-2")),
    spec("5-E-2", "equipment_tag", fragment("ocrcand__ea8d7eccd43064276b4c", 117, "5-E-2")),
    spec(
        "ACID GAS FEED FROM ACID GAS REMOVAL",
        "process_label",
        fragment("ocrcand__f42c3749fcead990560c", 117, "ACID GAS FEED"),
        fragment("ocrcand__8e71617d81a496012ec0", 117, "FROM ACID"),
        fragment("ocrcand__13cda29ec5f25592aa1f", 117, "GAS REMOVAL"),
    ),
    spec(
        "SULFUR TO BYPRODUCT PROCESSING",
        "process_label",
        fragment("ocrcand__f27579a44490da6ab049", 117, "SULFUR TO"),
        fragment("ocrcand__b1230f07d41adeeb9a21", 117, "BYPRODUCT"),
        fragment("ocrcand__e3b501aa397ab1e9d63f", 117, "PROCESSING"),
    ),
    spec(
        "CONTACT COOLER",
        "equipment_tag",
        fragment("ocrcand__d38e5d74cec03e2620e1", 117, "CONTACT"),
        fragment("ocrcand__47b3ce8448950b43456e", 117, "COOLER"),
    ),
    spec(
        "SYSTEM 3 INITIAL GAS CLEAN UP AND COOLING",
        "process_label",
        fragment("ocrcand__95d07ac9a4341e6163d2", 121, "SYSTEM 3"),
        fragment("ocrcand__ee2dc7cccd33300bb6bf", 121, "INITIAL"),
        fragment("ocrcand__882f65a1a69958525885", 121, "GAS CLEAN UP"),
        fragment("ocrcand__2b69486878d03e3782e4", 121, "AND COOLING"),
    ),
    spec(
        "SYSTEM 4 ACID GAS REMOVER",
        "process_label",
        fragment("ocrcand__0fc3c369b145c97ecd61", 121, "SYSTEM 4"),
        fragment("ocrcand__c43a13725ce601dd1ec5", 121, "ACID GAS"),
        fragment("ocrcand__4afd50a11275ab7b4ca2", 121, "REMOVER"),
    ),
    spec(
        "HOLDING POND",
        "process_label",
        fragment("ocrcand__6dc0e4d48ef7585f7b6b", 122, "HOLDING"),
        fragment("ocrcand__18b852953f6eb111c397", 122, "POND"),
    ),
    spec(
        "RIVER WATER",
        "process_label",
        fragment("ocrcand__1cc86d7a56c8d5c8841e", 122, "RIVER"),
        fragment("ocrcand__9e7bc54f0ec1c95fdb5f", 122, "WATER"),
    ),
    spec(
        "BACK-WASH",
        "process_label",
        fragment("ocrcand__7ea6bfa0038dbc6e4720", 122, "BACK-"),
        fragment("ocrcand__41eb8d49782effeccaab", 122, "WASH"),
    ),
    spec(
        "ENTRANCE WATER",
        "process_label",
        fragment("ocrcand__f79edc514be220ac0712", 123, "ENTRANCE"),
        fragment("ocrcand__c5aefca89ceb05428382", 123, "WATER"),
    ),
    spec(
        "OIL TO DISPOSAL",
        "process_label",
        fragment("ocrcand__04999d6a00dbc72e7e16", 123, "OIL TO"),
        fragment("ocrcand__491d8c7a9f54956fdb9a", 123, "DISPOSAL"),
    ),
    spec(
        "WATER TREATMENT SOLIDS",
        "process_label",
        fragment("ocrcand__29582f9016f7347687fc", 123, "WATER"),
        fragment("ocrcand__ab53984ad63ebac91f22", 123, "TREATMENT"),
        fragment("ocrcand__3dbe1c92469221bc59ac", 123, "SOLIDS"),
    ),
    spec(
        "DEWATERING PIT (SYSTEM 8)",
        "equipment_tag",
        fragment("ocrcand__9c56c259f7852346f984", 123, "DEWATERING"),
        fragment("ocrcand__4cdc1c3be11f697a75b2", 123, "PIT"),
        fragment("ocrcand__ca2f383885974f1212ca", 123, "(SYSTEM 8)"),
    ),
)


QUESTION_BY_CATEGORY = {
    "equipment_tag": "What engineering component or equipment label is shown in this region?",
    "process_label": "What process or fluid label is shown in this region?",
    "process_value": "What engineering process value is shown in this region?",
}


def _candidate_id(specification: LabelSpec) -> str:
    value = "|".join(
        [EXPECTED_DOC_ID, specification.target_text, *(item.candidate_id for item in specification.fragments)]
    )
    return "labelcand__" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def excluded_source_candidate_ids(rows: Sequence[dict[str, Any]]) -> set[str]:
    excluded: set[str] = set()
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        if candidate_id:
            excluded.add(candidate_id)
        source_ids = row.get("source_candidate_ids")
        if isinstance(source_ids, list):
            excluded.update(str(item).strip() for item in source_ids if str(item).strip())
    return excluded


def curate(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    excluded_candidate_ids: set[str] | None = None,
    specs: Sequence[LabelSpec] = DEFAULT_SPECS,
    confidence_floor: float = 0.85,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    excluded_candidate_ids = set(excluded_candidate_ids or ())
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
        elif candidate_id in excluded_candidate_ids:
            reason = "excluded_by_prior_selected_cohort"
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
        if specification.category not in QUESTION_BY_CATEGORY:
            raise ValueError(f"unsupported category: {specification.category}")
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
                issues.append(
                    f"invalid:{expected.candidate_id}:{invalid_reason_by_id[expected.candidate_id]}"
                )
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
        raw_text = " ".join(item for item in raw_fragments if item)
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
                "split": "test",
                "review_status": "needs_review",
                "promotion_state": "unreviewed_candidate",
                "source_candidate_ids": source_ids,
                "machine_text_assembled": len(source_ids) > 1,
                "machine_text_corrected": raw_text.casefold() != specification.target_text.casefold(),
                "machine_qa_status": "strict_coal_supplemental_label_pass_pending_visual_review",
                "machine_qa_notes": (
                    "Source-specific label selected only by immutable OCR candidate identity, exact raw text, "
                    "page, confidence, image, bbox, and prior-cohort exclusion checks. Human visual review "
                    "is still required."
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
        reason = invalid_reason_by_id.get(candidate_id, "not_an_approved_coal_supplemental_label")
        output["machine_qa_status"] = "machine_held_nasa_coal_supplemental_label_filter"
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
        "excluded_candidate_ids": len(excluded_candidate_ids),
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
    parser.add_argument("--exclude-selected", type=Path, action="append", required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--confidence-floor", type=float, default=0.85)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    input_path = (root / args.input).resolve()
    exclusion_paths = [(root / path).resolve() for path in args.exclude_selected]
    selected_path = (root / args.selected_output).resolve()
    held_path = (root / args.held_output).resolve()
    report_path = (root / args.report_json).resolve()
    exclusion_rows = [row for path in exclusion_paths for row in read_jsonl(path)]
    selected, held, report = curate(
        read_jsonl(input_path),
        root=root,
        excluded_candidate_ids=excluded_source_candidate_ids(exclusion_rows),
        confidence_floor=args.confidence_floor,
    )
    write_jsonl(selected_path, selected)
    write_jsonl(held_path, held)
    report.update(
        {
            "input": input_path.as_posix(),
            "input_sha256": sha256(input_path),
            "exclude_selected": [path.as_posix() for path in exclusion_paths],
            "exclude_selected_sha256": [sha256(path) for path in exclusion_paths],
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
