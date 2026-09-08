#!/usr/bin/env python3
"""Curate fail-closed supplemental labels from NASA lunar-separation diagrams."""
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


EXPECTED_DOC_ID = "nasa_19790021033_lunar_separation_process_flow"


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
    spec("RECYCLE", "process_label", fragment("ocrcand__56e28cbf8449e864c979", 201, "RECYCLE")),
    spec("PYROLYSIS", "process_label", fragment("ocrcand__62050bb3c6fd41643afa", 201, "PYROLYSIS")),
    spec(
        "TO RECOVERY",
        "process_label",
        fragment("ocrcand__68fca1abee02cf3ba075", 201, "TO"),
        fragment("ocrcand__018d58cf1cdc02cf98d6", 201, "RECOVERY"),
    ),
    spec(
        "METHANOL SYNTH",
        "process_label",
        fragment("ocrcand__af9c44e8cf9fb4836710", 201, "METHANOL"),
        fragment("ocrcand__a85e81ba38aee466e205", 201, "SYNTH"),
    ),
    spec(
        "FUSED SALT",
        "process_label",
        fragment("ocrcand__ff86c65b8396c7965728", 201, "FUSED SALT"),
    ),
    spec(
        "HCOONa FUSED SALT",
        "process_label",
        fragment("ocrcand__fea7195ca819c51d612a", 201, "HCOONG"),
        fragment("ocrcand__5f8861cf73bf00832bf8", 201, "FUSED"),
        fragment("ocrcand__d04323710b748a6070f9", 201, "SALT"),
    ),
    spec(
        "EQUIPMENT & SUPPLIES",
        "equipment_tag",
        fragment("ocrcand__52ed43af04c31db9638f", 202, "EQUIPMENT"),
        fragment("ocrcand__6918d83a90e3e16f7ca0", 202, "SUPPLIES"),
    ),
    spec(
        "ORE STORAGE",
        "equipment_tag",
        fragment("ocrcand__3f1b27f322bf5386a37a", 202, "ORE"),
        fragment("ocrcand__21e4640c5fc08eb693a3", 202, "STORAGE"),
    ),
    spec(
        "PRODUCT STORAGE",
        "equipment_tag",
        fragment("ocrcand__5db9972446898ebda6f5", 202, "/PRODUCT"),
        fragment("ocrcand__1d02c1d2c2f575915279", 202, "STORAGE"),
    ),
    spec(
        "ACID LEACH",
        "process_label",
        fragment("ocrcand__d9a28f92eb4497af9ccf", 202, "ACID"),
        fragment("ocrcand__ebbfede82aa9ba1ae671", 202, "LEACH"),
    ),
    spec(
        "FLASH EVAP",
        "process_label",
        fragment("ocrcand__61960e3fbb0a55b9b038", 202, "FLASH"),
        fragment("ocrcand__8a21cd53109d07f6706c", 202, "EVAP"),
    ),
    spec("HYDROLYZE", "process_label", fragment("ocrcand__8bf00144edd7f3a91894", 202, "HYDROLYZE")),
    spec("SEPARATION", "process_label", fragment("ocrcand__0e3e61af04af80495f73", 202, "SEPARATION")),
    spec(
        "MIX-DISSOLVE",
        "process_label",
        fragment("ocrcand__4e0fe23852aab3e99c75", 202, "MIX-"),
        fragment("ocrcand__59e019bad6006a8aae74", 202, "DISSOLVE"),
    ),
    spec(
        "TO STORAGE",
        "process_label",
        fragment("ocrcand__9d578fe3260a1aafa9ed", 202, "TO"),
        fragment("ocrcand__9b7a036aad0c5f4be5fd", 202, "STORAGE"),
    ),
    spec(
        "TO HYDROLYSIS",
        "process_label",
        fragment("ocrcand__21d56996e2a38fbd70e7", 202, "TO HYDROLYSIS"),
    ),
    spec(
        "PLAT. METALS",
        "process_label",
        fragment("ocrcand__c72c8b21ba6b6f976cc1", 202, "PLAT"),
        fragment("ocrcand__1df9d0b6f06ea7158b8a", 202, "METALS"),
    ),
    spec(
        "ION-EXCHANGE",
        "process_label",
        fragment("ocrcand__b77d4df95ee1035d35fc", 202, "ION-"),
        fragment("ocrcand__2d2b610286db403b4639", 202, "EXCHANGE"),
    ),
    spec("REDUCTION", "process_label", fragment("ocrcand__cbe09d23c3ce51734cb2", 203, "REDUCTION")),
    spec(
        "ELECTROLYZE",
        "process_label",
        fragment("ocrcand__18d9d0a13dec0cc2c2a9", 203, "ELECTROLYZE"),
    ),
    spec(
        "FLUOSILICIC ACID",
        "process_label",
        fragment("ocrcand__04d9aea8329919979535", 204, "PLUOGILICIC ACID"),
    ),
    spec("STRIPPER", "equipment_tag", fragment("ocrcand__6adeeee9ef3fbd9e41f7", 204, "STRIPPER")),
    spec(
        "CELL (ELECTROLYSIS)",
        "equipment_tag",
        fragment("ocrcand__f613405675853f065d09", 204, "CELL"),
        fragment("ocrcand__89f5348a3207ea0e89ba", 204, "(ELECTROLYSIS)"),
    ),
    spec(
        "METALS OR SILICON",
        "process_label",
        fragment("ocrcand__c47526d7e98a5692f8ca", 204, "METALS"),
        fragment("ocrcand__7526d254346daa684e3b", 204, "OR"),
        fragment("ocrcand__5e98cc7f9dec235638a3", 204, "SILICON"),
    ),
    spec(
        "ION EXCHANGE",
        "process_label",
        fragment("ocrcand__c22fe64d4b95cbfc0b0a", 204, "ION"),
        fragment("ocrcand__1b9d66193db4f188a3ca", 204, "EXCHANGE"),
    ),
    spec(
        "PRECIPITATOR",
        "equipment_tag",
        fragment("ocrcand__ca8976d503bc790e1e53", 204, "RECIPI"),
        fragment("ocrcand__c574b16ed2af1cd1bb2f", 204, "TATOR"),
    ),
    spec("CONDENSER", "equipment_tag", fragment("ocrcand__24c1177f2215f5e86a19", 204, "LCONDENSER")),
    spec("LEACH", "process_label", fragment("ocrcand__4404fbaf5f6838cc65f4", 204, "LEACH")),
    spec("TO DRYER", "process_label", fragment("ocrcand__2d496834be91379202ab", 204, "TO DRYER")),
    spec(
        "PLATABLE METALS SEPARATION",
        "process_label",
        fragment("ocrcand__30172bda6b128f49e21d", 204, "PLATABLE"),
        fragment("ocrcand__51fac61a52daee36cc07", 204, "METALS"),
        fragment("ocrcand__9e4ca444ee9ebad6345d", 204, "SEPARATION"),
    ),
)


QUESTION_BY_CATEGORY = {
    "equipment_tag": "What engineering component or equipment label is shown in this region?",
    "process_label": "What process or fluid label is shown in this region?",
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
                "machine_qa_status": "strict_lunar_supplemental_label_pass_pending_visual_review",
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
        reason = invalid_reason_by_id.get(candidate_id, "not_an_approved_lunar_supplemental_label")
        output["machine_qa_status"] = "machine_held_nasa_lunar_supplemental_label_filter"
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
