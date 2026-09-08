#!/usr/bin/env python3
"""Curate fail-closed supplemental labels from the NASA plasma-reactor tables."""
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


EXPECTED_DOC_ID = "nasa_20200004338_plasma_reactor_pid_specs"
EXPECTED_SOURCE_CANDIDATE_ID = "pid_078"
EXPECTED_SOURCE = "textlayer_full_span_candidate"
EXPECTED_SOURCES = frozenset({EXPECTED_SOURCE, "textlayer_regex_candidate"})
EXPECTED_IMAGE_DIR = Path("derived/pages_300dpi") / EXPECTED_DOC_ID


@dataclass(frozen=True)
class FragmentSpec:
    candidate_id: str
    page_index: int
    raw_text: str
    bbox: tuple[int, int, int, int]
    source: str


@dataclass(frozen=True)
class LabelSpec:
    target_text: str
    category: str
    fragments: tuple[FragmentSpec, ...]


def fragment(
    candidate_id: str,
    page_index: int,
    raw_text: str,
    bbox: Sequence[int],
    source: str = EXPECTED_SOURCE,
) -> FragmentSpec:
    if len(bbox) != 4:
        raise ValueError("fragment bbox must have four coordinates")
    return FragmentSpec(candidate_id, page_index, raw_text, tuple(int(value) for value in bbox), source)


def spec(target_text: str, category: str, *fragments: FragmentSpec) -> LabelSpec:
    return LabelSpec(target_text, category, tuple(fragments))


DEFAULT_SPECS: tuple[LabelSpec, ...] = (
    spec(
        "Heater block #1 current",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0082__fp_31f2472cb28575c9", 82, "Heater block #1 current", [602, 822, 946, 879]),
    ),
    spec(
        "Reactor heater block #1A",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0082__fp_02582378e29b5ffb", 82, "Reactor heater block #1A", [589, 1428, 959, 1485]),
    ),
    spec(
        "Reactor heater power supply",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0082__fp_49f748af3bb81ec4", 82, "Reactor heater power", [618, 2191, 931, 2248]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0082__fp_1e9dee731c02ebdd", 82, "supply", [716, 2229, 833, 2286]),
    ),
    spec(
        "Plasma reactor power supply",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0082__fp_36dde08b8afef176", 82, "Plasma reactor power", [616, 2286, 933, 2343]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0082__fp_4fd12afcd2a6c03a", 82, "supply", [715, 2324, 833, 2381]),
    ),
    spec(
        "Pressure control valve set point",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_0f3475262ee5f95e", 83, "Pressure control valve set", [590, 511, 959, 568]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_0870d0b91cd3d730", 83, "point", [726, 549, 823, 606]),
    ),
    spec(
        "Facility power 120 Vac",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_6294cac51e34555b", 83, "Facility power 120 Vac", [603, 696, 945, 753]),
    ),
    spec(
        "Facility power 208 Vac",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_1d8fac7ccf3b1523", 83, "Facility power 208 Vac", [603, 754, 945, 811]),
    ),
    spec(
        "Hydrogen line pressure",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_f817623f21cc61f2", 83, "Hydrogen line pressure", [604, 901, 944, 958]),
    ),
    spec(
        "Reactor internal pressure",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_1c05106f443398bf", 83, "Reactor internal pressure", [593, 958, 955, 1015]),
    ),
    spec(
        "Vacuum line pressure",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_80e85177f86a3f65", 83, "Vacuum line pressure", [615, 1015, 934, 1072]),
    ),
    spec(
        "Coolant flowmeter supply pressure",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_2dbbac008a268bb1", 83, "Coolant flowmeter supply", [587, 1072, 962, 1129]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_5cb30fa60b22c95f", 83, "pressure", [705, 1110, 844, 1167]),
    ),
    spec(
        "Coolant flowmeter return pressure",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_7301dd0114bd7670", 83, "Coolant flowmeter return", [591, 1168, 958, 1225]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_4449ce1acde92f06", 83, "pressure", [705, 1206, 844, 1263]),
    ),
    spec(
        "Reactor vacuum pressure",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_ccf6fdbb5fea79ab", 83, "Reactor vacuum pressure", [591, 1263, 957, 1320]),
    ),
    spec(
        "Vacuum switch",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_8e2bb99e846da198", 83, "Vacuum switch", [656, 1320, 893, 1377]),
    ),
    spec(
        "Partial pressure control",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_faeddaf0d93cdc37", 83, "Partial pressure control", [606, 1416, 943, 1473]),
    ),
    spec(
        "Low pressure gauge",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0083__fp_9a62a806b416f7e7", 83, "Low pressure gauge", [626, 1683, 923, 1740]),
    ),
    spec(
        "Calorimeter surface - side b, aft",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_c967761deca5bab9", 84, "Calorimeter surface - side", [587, 511, 961, 568]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_04564f9a8bcf72d7", 84, "b, aft", [726, 549, 823, 606]),
    ),
    spec(
        "Support stand - forward leg, high",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_4080da1bacb107f5", 84, "Support stand - forward", [601, 606, 947, 663]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_8d10732536fc6c48", 84, "leg, high", [702, 644, 847, 701]),
    ),
    spec(
        "Support stand - forward leg, mid",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_bb28670d5889e86f", 84, "Support stand - forward", [602, 701, 947, 758]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_26483de0163db13e", 84, "leg, mid", [706, 740, 843, 797]),
    ),
    spec(
        "Support stand - forward leg, low",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_ea76f908673353d9", 84, "Support stand - forward", [602, 797, 947, 854]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_68d1686508cfa76c", 84, "leg, low", [707, 835, 842, 892]),
    ),
    spec(
        "Insulation surface - aft face, high",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_57c9955a61afd2ca", 84, "Insulation surface - aft", [609, 1942, 939, 1999]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_01059f260d03070d", 84, "face, high", [695, 1980, 854, 2037]),
    ),
    spec(
        "Insulation surface - aft face, low",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_c01a77dc888d16a4", 84, "Insulation surface - aft", [609, 2037, 939, 2094]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_4527e8994ac7c33e", 84, "face, low", [699, 2075, 849, 2132]),
    ),
    spec(
        "Arc reactor surface - aft face, high",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_d6aad07f9540a6ca", 84, "Arc reactor surface - aft", [601, 2323, 948, 2380]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_0d033c6c8bd3f6a5", 84, "face, high", [695, 2362, 854, 2419]),
    ),
    spec(
        "Arc reactor surface - aft face, low",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_68f57be140b8becc", 84, "Arc reactor surface - aft", [601, 2419, 948, 2476]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0084__fp_5e72676ca58e6d0c", 84, "face, low", [699, 2457, 849, 2514]),
    ),
    spec(
        "Heater #1a heater temperature",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0085__fp_30663d7bb7b07c8c", 85, "Heater #1a heater", [643, 1599, 906, 1656]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0085__fp_68e48db52c63b872", 85, "temperature", [681, 1637, 868, 1694]),
    ),
    spec(
        "Arc voltage",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0085__fp_0a197942e5d3110f", 85, "Arc voltage", [682, 2461, 867, 2518]),
    ),
    spec(
        "Heater block #1 voltage",
        "process_label",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0085__fp_b9231aecc90bf03d", 85, "Heater block #1 voltage", [601, 2518, 948, 2575]),
    ),
    spec(
        "Turbomolecular pump assembly",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_0f25af25c47e4675", 86, "Turbomolecular pump", [611, 666, 938, 723]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_4f1cbbe282f2ade2", 86, "assembly", [698, 705, 850, 762]),
    ),
    spec(
        "Coolant diverter valve",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_eac3141b7f74de37", 86, "Coolant diverter valve", [611, 852, 937, 909]),
    ),
    spec(
        "Hydrogen supply isolation solenoid valve",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_ecf6c5d2ad2c02f3", 86, "Hydrogen supply", [645, 1210, 903, 1267]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_d87ec55535eebbcc", 86, "isolation solenoid valve", [602, 1248, 947, 1305]),
    ),
    spec(
        "Reactor isolation solenoid valve",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_c25fb31f11895686", 86, "Reactor isolation solenoid", [586, 1306, 963, 1363]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_63b95e5ebc915de4", 86, "valve", [724, 1344, 824, 1401]),
    ),
    spec(
        "Reactor assembly isolation solenoid valve",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_bcba93f6e5b52ee9", 86, "Reactor assembly", [642, 1496, 906, 1553]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_6504bde38a28ae5c", 86, "isolation solenoid valve", [602, 1535, 947, 1592]),
    ),
    spec(
        "Partial pressure control isolation valve - upstream",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_660ed40737499fb0", 86, "Partial pressure control", [605, 1592, 943, 1649]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_333bd07422489eec", 86, "isolation valve - upstream", [587, 1630, 961, 1687]),
    ),
    spec(
        "Transducer protection valve",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_462b83847c27a40d", 86, "Transducer protection", [613, 1821, 935, 1878]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_c322be804505f9a1", 86, "valve", [724, 1859, 824, 1916]),
    ),
    spec(
        "Reactor evacuation solenoid valve",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_fe47ca1222784081", 86, "Reactor evacuation", [632, 1916, 917, 1973]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_131a4d1af2d656c7", 86, "solenoid valve", [663, 1955, 885, 2012]),
    ),
    spec(
        "Inert gas supply solenoid valve",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_e51ee65d9cb6fde6", 86, "Inert gas supply solenoid", [593, 2012, 956, 2069]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_3e2707ceafe010f6", 86, "valve", [724, 2050, 824, 2107]),
    ),
    spec(
        "Partial pressure controller isolation valve",
        "equipment_tag",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_8529e76f2f3c9ae2", 86, "Partial pressure controller", [588, 2203, 960, 2260]),
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_f5bce089112cf133", 86, "isolation valve", [662, 2241, 886, 2298]),
    ),
    spec(
        "0.00 to 5.00 A",
        "process_value",
        fragment("mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0086__fp_d5bbce38818e04de", 86, "0.00 to 5.00 A", [993, 2626, 1213, 2683]),
    ),
    spec(
        "0 to 75 psia",
        "process_value",
        fragment(
            "mtcand__nasa_20200004338_plasma_reactor_pid_specs__unknown__p0087__fp_212aab49964d7043",
            87,
            "0 to 75 psia",
            [653, 2671, 836, 2728],
            source="textlayer_regex_candidate",
        ),
    ),
)


QUESTION_BY_CATEGORY = {
    "equipment_tag": "What engineering component or equipment label is shown in this region?",
    "process_label": "What engineering process or parameter label is shown in this region?",
    "process_value": "What engineering process value is shown in this region?",
}


def _candidate_id(specification: LabelSpec) -> str:
    value = "|".join(
        [EXPECTED_DOC_ID, specification.target_text, *(item.candidate_id for item in specification.fragments)]
    )
    return "labelcand__" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _expected_image_path(page_index: int) -> str:
    return (EXPECTED_IMAGE_DIR / f"page_{page_index:03d}.png").as_posix()


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
        elif str(row.get("source_candidate_id") or "") != EXPECTED_SOURCE_CANDIDATE_ID:
            reason = "unexpected_source_candidate_id"
        elif str(row.get("source") or "") not in EXPECTED_SOURCES:
            reason = "unexpected_candidate_source"
        elif not candidate_id or candidate_id in row_by_id:
            reason = "missing_or_duplicate_candidate_id"
        elif candidate_id in excluded_candidate_ids:
            reason = "excluded_by_prior_selected_cohort"
        else:
            bbox = parse_bbox(row)
            if bbox is None:
                reason = "invalid_bbox"
            page_index = int(row.get("page_index", -1))
            expected_relative = _expected_image_path(page_index)
            relative_path = Path(str(row.get("image_path") or "")).as_posix()
            if not reason and relative_path != expected_relative:
                reason = "unexpected_page_image_path"
            image_path = (root / relative_path).resolve()
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
                issues.append(f"invalid:{expected.candidate_id}:{invalid_reason_by_id[expected.candidate_id]}")
                continue
            if int(row.get("page_index", -1)) != expected.page_index:
                issues.append(f"page_mismatch:{expected.candidate_id}")
            if str(row.get("proposed_text") or "").strip() != expected.raw_text:
                issues.append(f"proposed_text_mismatch:{expected.candidate_id}")
            if str(row.get("raw_text") or "").strip() != expected.raw_text:
                issues.append(f"raw_text_mismatch:{expected.candidate_id}")
            if str(row.get("source") or "") != expected.source:
                issues.append(f"source_mismatch:{expected.candidate_id}")
            if parse_bbox(row) != expected.bbox:
                issues.append(f"bbox_mismatch:{expected.candidate_id}")
            if Path(str(row.get("image_path") or "")).as_posix() != _expected_image_path(expected.page_index):
                issues.append(f"image_path_mismatch:{expected.candidate_id}")
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
        typed_bboxes = [parse_bbox(row) for row in chosen]
        assert all(bbox is not None for bbox in typed_bboxes)
        bboxes = [bbox for bbox in typed_bboxes if bbox is not None]
        raw_fragments = [str(row.get("proposed_text") or "").strip() for row in chosen]
        raw_text = " ".join(item for item in raw_fragments if item)
        output = dict(chosen[0])
        output.update(
            {
                "candidate_id": _candidate_id(specification),
                "bbox": [
                    int(min(bbox[0] for bbox in bboxes)),
                    int(min(bbox[1] for bbox in bboxes)),
                    int(max(bbox[2] for bbox in bboxes)),
                    int(max(bbox[3] for bbox in bboxes)),
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
                "machine_qa_status": "strict_plasma_supplemental_label_pass_pending_visual_review",
                "machine_qa_notes": (
                    "Selected only by immutable text-layer candidate identity, exact source, page, raw text, "
                    "bbox, image, and prior-cohort exclusion checks. Human visual review is still required."
                ),
                "safe_to_merge_gold": False,
            }
        )
        selected.append(output)

    selected.sort(key=lambda row: (int(row.get("page_index", -1)), row["bbox"][1], row["bbox"][0]))
    held: list[dict[str, Any]] = []
    hold_reasons: Counter[str] = Counter()
    for source_row in rows:
        candidate_id = str(source_row.get("candidate_id") or "").strip()
        if candidate_id in consumed_ids:
            continue
        output = dict(source_row)
        reason = invalid_reason_by_id.get(candidate_id, "not_an_approved_plasma_supplemental_label")
        output["machine_qa_status"] = "machine_held_nasa_plasma_supplemental_label_filter"
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
        "split_policy": "whole source family reserved for train",
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
