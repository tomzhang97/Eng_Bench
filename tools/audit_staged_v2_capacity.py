#!/usr/bin/env python3
"""Audit staged human-review capacity against the open Eng_Bench v2.0 gates."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import (
    file_sha256,
    manifest_maps,
    read_csv,
    read_jsonl,
    resolve_visualdiff_docs,
    rights_blocker,
)
from audit_v2_0_gate import (
    MICROTEXT_CATEGORY_MINIMUMS,
    MICROTEXT_PIN_SHARE_MAX,
    microtext_category_balance,
    visualdiff_family,
)
from audit_source_conversion_readiness import microtext_region_identity, review_exclusion_reason
from mine_microtext_candidates import COMPONENT_VALUE_RE, usable_process_label
from payload_alias_regions import load_payload_alias_map, payload_alias_region_key
from text_encoding import mojibake_signatures


@dataclass(frozen=True)
class CohortSpec:
    phase: str
    name: str
    path: Path


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(
                f"JSON cohort must be a row array or an object with a row array at 'rows': {path}"
            )
        return rows
    return read_jsonl(path)


def task_for_row(row: dict[str, Any]) -> str:
    if row.get("pair_id") or (row.get("image_old") and row.get("image_new")):
        return "visualdiff"
    return "microtext"


def staged_split_unit(row: dict[str, Any]) -> tuple[str, str]:
    task = task_for_row(row)
    if task == "microtext":
        return task, str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
    project_id = str(row.get("project_id") or "").strip()
    if project_id:
        return task, project_id
    pair_id = str(row.get("pair_id") or row.get("id") or "").strip()
    stem, separator, suffix = pair_id.rpartition("__")
    return task, stem if separator and suffix.isdigit() else pair_id


def load_split_reservations(path: Path) -> tuple[dict[tuple[str, str], str], list[dict[str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    issues: list[dict[str, str]] = []
    if not payload.get("valid", False):
        issues.append({"type": "invalid_split_plan", "path": path.as_posix()})
    mapping: dict[tuple[str, str], str] = {}
    for row in payload.get("reservations", []):
        key = (str(row.get("task") or "").strip(), str(row.get("unit_id") or "").strip())
        split = str(row.get("split") or "").strip().lower()
        if not all(key) or split not in {"train", "dev", "test"}:
            issues.append(
                {"type": "invalid_split_reservation", "task": key[0], "unit_id": key[1], "split": split}
            )
            continue
        if key in mapping and mapping[key] != split:
            issues.append(
                {"type": "conflicting_split_reservation", "task": key[0], "unit_id": key[1], "split": split}
            )
            continue
        mapping[key] = split
    return mapping, issues


def identity_aliases(row: dict[str, Any]) -> set[str]:
    if task_for_row(row) == "visualdiff":
        return {
            str(value).strip()
            for value in (row.get("pair_id"), row.get("id"))
            if str(value or "").strip()
        }
    values = [
        row.get("candidate_id"),
        row.get("item_id"),
        row.get("pre_padding_candidate_id"),
    ]
    source_candidate_id = str(row.get("source_candidate_id") or "").strip()
    if source_candidate_id.startswith("mtcand__"):
        values.append(source_candidate_id)
    return {
        str(value).strip()
        for value in values
        if str(value or "").strip()
    }


def row_identity(row: dict[str, Any]) -> str:
    task = task_for_row(row)
    preferred = (
        (row.get("pair_id"), row.get("id"))
        if task == "visualdiff"
        else (row.get("candidate_id"), row.get("item_id"), row.get("source_candidate_id"))
    )
    value = next((str(item).strip() for item in preferred if str(item or "").strip()), "")
    return f"{task}:{value}" if value else ""


def capacity_identity(row: dict[str, Any]) -> str:
    """Prefer a physical microtext region over mutable candidate aliases."""
    if task_for_row(row) == "microtext":
        region = microtext_region_identity(row)
        if region:
            doc_id, page_index, bbox = region
            bbox_text = ",".join(str(value) for value in bbox)
            return f"microtext-region:{doc_id}:p{page_index}:{bbox_text}"
    return row_identity(row)


NEAR_OVERLAP_IOU_THRESHOLD = 0.80
NEAR_OVERLAP_CONTAINMENT_THRESHOLD = 0.92
NEAR_OVERLAP_AREA_RATIO_THRESHOLD = 0.55


def microtext_region_geometry(
    row: dict[str, Any],
) -> tuple[str, int, tuple[int, int, int, int]] | None:
    if task_for_row(row) != "microtext":
        return None
    region = microtext_region_identity(row)
    if not region:
        return None
    doc_id, page_index, bbox = region
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return doc_id, page_index, bbox


def bbox_overlap_metrics(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[float, float, float]:
    intersection_width = max(0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    if not intersection_width or not intersection_height:
        return 0.0, 0.0, 0.0
    intersection = intersection_width * intersection_height
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return (
        intersection / union,
        intersection / min(left_area, right_area),
        min(left_area, right_area) / max(left_area, right_area),
    )


def bboxes_are_near_duplicates(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> bool:
    iou, containment, area_ratio = bbox_overlap_metrics(left, right)
    return iou >= NEAR_OVERLAP_IOU_THRESHOLD or (
        containment >= NEAR_OVERLAP_CONTAINMENT_THRESHOLD
        and area_ratio >= NEAR_OVERLAP_AREA_RATIO_THRESHOLD
    )


def find_near_region_overlaps(
    rows: list[dict[str, Any]],
    *,
    example_limit: int = 500,
) -> tuple[int, list[dict[str, Any]]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        geometry = microtext_region_geometry(row["row"])
        if not geometry:
            continue
        doc_id, page_index, bbox = geometry
        grouped[(doc_id, page_index)].append({**row, "bbox": bbox})

    overlap_count = 0
    examples: list[dict[str, Any]] = []
    for (doc_id, page_index), entries in sorted(grouped.items()):
        entries.sort(key=lambda entry: (entry["bbox"][0], entry["identity"], entry["cohort"]))
        for index, left in enumerate(entries):
            for right in entries[index + 1 :]:
                if right["bbox"][0] >= left["bbox"][2]:
                    break
                if left["identity"] == right["identity"]:
                    continue
                if not bboxes_are_near_duplicates(left["bbox"], right["bbox"]):
                    continue
                overlap_count += 1
                if len(examples) >= example_limit:
                    continue
                iou, containment, area_ratio = bbox_overlap_metrics(
                    left["bbox"], right["bbox"]
                )
                examples.append(
                    {
                        "doc_id": doc_id,
                        "page_index": page_index,
                        "left_cohort": left["cohort"],
                        "left_identity": left["identity"],
                        "left_bbox": list(left["bbox"]),
                        "right_cohort": right["cohort"],
                        "right_identity": right["identity"],
                        "right_bbox": list(right["bbox"]),
                        "iou": round(iou, 6),
                        "containment": round(containment, 6),
                        "area_ratio": round(area_ratio, 6),
                    }
                )
    return overlap_count, examples


NON_CAPACITY_STATUSES = {
    "blocked",
    "blocked_rights",
    "duplicate_hold",
    "invalid",
    "machine_held",
    "machine_superseded",
    "not_reviewable",
    "policy_hold",
    "qa_duplicate_hold",
    "reject",
    "reject_unclear",
    "rejected",
    "rights_hold",
    "skip",
    "skipped",
    "superseded",
}

PROVENANCE_ONLY_TEXT_FIELDS = {
    "source_raw_text",
    "source_text_parts",
    "upstream_raw_text",
}


def benchmark_text_encoding_issues(
    value: Any,
    path: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Find mojibake outside fields retained only for source forensics."""
    issues: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PROVENANCE_ONLY_TEXT_FIELDS:
                continue
            issues.extend(benchmark_text_encoding_issues(child, (*path, str(key))))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(benchmark_text_encoding_issues(child, (*path, f"[{index}]")))
    elif isinstance(value, str):
        signatures = mojibake_signatures(value)
        if signatures:
            issues.append(
                {
                    "field": ".".join(path),
                    "signatures": list(signatures),
                    "text": value,
                }
            )
    return issues


def capacity_exclusion_reason(row: dict[str, Any]) -> str:
    """Return why a terminal row cannot contribute review-ready capacity."""
    return review_exclusion_reason(row)


def is_provenance_replacement(row: dict[str, Any]) -> bool:
    """Return whether a staged row replaces, rather than adds to, active Gold."""
    if "provenance_replacement" in row:
        return row.get("provenance_replacement") is True
    if row.get("provenance_replacement_candidate") is True:
        return True
    state = str(row.get("promotion_state") or "").strip().lower()
    reason = str(row.get("replacement_completion_reason") or "").strip().lower()
    return state == "unreviewed_provenance_replacement_candidate" or reason == (
        "retire_rights_blocked_active_gold_row"
    )


def active_aliases(rows: list[dict[str, Any]], task: str) -> set[str]:
    return {
        f"{task}:{alias}"
        for row in rows
        for alias in identity_aliases(row)
    }


def source_payload_key(row: dict[str, Any]) -> str:
    computed = str(row.get("computed_sha256") or "").strip().lower()
    recorded = str(row.get("recorded_sha256") or "").strip().lower()
    digest = computed if len(computed) == 64 else recorded
    doc_id = str(row.get("doc_id") or "").strip()
    return f"sha256:{digest}" if len(digest) == 64 else f"doc_id:{doc_id}"


def audit_source_doc(
    root: Path,
    doc_id: str,
    docs: dict[str, dict[str, Any]],
    inventory: dict[str, dict[str, str]],
) -> dict[str, Any]:
    manifest = docs.get(doc_id, {})
    source = inventory.get(doc_id, {})
    path_value = str(source.get("path") or source.get("source_path") or manifest.get("path") or "").strip()
    source_path = root / path_value if path_value else None
    path_exists = bool(source_path and source_path.is_file())
    computed_sha = file_sha256(source_path) if source_path and path_exists else ""
    recorded_sha = str(manifest.get("sha256") or "").strip().lower()
    source_url = str(source.get("source_url") or manifest.get("source_url") or "").strip()
    public_status = str(source.get("public_status") or manifest.get("public_status") or "").strip()
    blocker = rights_blocker(public_status)
    issues: list[str] = []
    if not source:
        issues.append("missing_inventory_record")
    if not manifest:
        issues.append("missing_manifest_doc")
    if not path_exists:
        issues.append("missing_source_payload")
    if not source_url:
        issues.append("missing_source_url")
    if blocker:
        issues.append(f"rights_blocked:{blocker}")
    if not recorded_sha:
        issues.append("missing_recorded_sha256")
    elif path_exists and computed_sha != recorded_sha:
        issues.append("source_sha256_mismatch")
    paper_ready = not issues
    result = {
        "doc_id": doc_id,
        "inventory_present": bool(source),
        "manifest_present": bool(manifest),
        "source_path": path_value,
        "source_payload_exists": path_exists,
        "source_url": source_url,
        "public_status": public_status,
        "rights_blocker": blocker,
        "recorded_sha256": recorded_sha,
        "computed_sha256": computed_sha,
        "sha256_matches": bool(recorded_sha and computed_sha == recorded_sha),
        "paper_ready": paper_ready,
        "issues": issues,
    }
    result["payload_key"] = source_payload_key(result)
    return result


def row_source_docs(
    row: dict[str, Any],
    docs: dict[str, dict[str, Any]],
    manifest_pairs: dict[str, dict[str, Any]],
) -> tuple[list[str], str]:
    if task_for_row(row) == "microtext":
        doc_id = str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
        return ([doc_id], "") if doc_id else ([], "missing_doc_id")
    return resolve_visualdiff_docs(row, docs, manifest_pairs)


def acceptance_projection(current_rows: int, expansion_rows: int) -> list[dict[str, int]]:
    projections = []
    for percent in (100, 75, 50, 25):
        accepted = (expansion_rows * percent + 50) // 100
        projections.append(
            {
                "acceptance_percent": percent,
                "accepted_expansion_rows": accepted,
                "projected_gold_rows": current_rows + accepted,
            }
        )
    return projections


def microtext_label_text(row: dict[str, Any]) -> str:
    for key in ("text_gt", "corrected_text", "proposed_text", "target_text", "answer"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def canonical_staged_category(row: dict[str, Any]) -> bool:
    """Require strict machine evidence for the v2 taxonomy extensions."""
    category = str(row.get("category") or "").strip()
    if category not in MICROTEXT_CATEGORY_MINIMUMS:
        return False
    value = microtext_label_text(row)
    if category == "component_value":
        return bool(COMPONENT_VALUE_RE.fullmatch(value))
    if category == "process_label":
        return usable_process_label(value)
    return True


def balance_compliant_pin_additions(
    active_rows: int,
    active_pin_rows: int,
    non_pin_additions: int,
    available_pin_additions: int,
) -> int:
    numerator = MICROTEXT_PIN_SHARE_MAX * (active_rows + non_pin_additions) - active_pin_rows
    if numerator < 0:
        return 0
    maximum = int(numerator / (1.0 - MICROTEXT_PIN_SHARE_MAX) + 1e-9)
    return min(available_pin_additions, maximum)


def microtext_capacity_balance(
    active_microtext: list[dict[str, Any]],
    staged_microtext: list[dict[str, Any]],
    *,
    current_gold_rows: int,
    row_target: int,
    staged_visualdiff_rows: int,
) -> dict[str, Any]:
    active_balance = microtext_category_balance(active_microtext)
    canonical_categories = set(MICROTEXT_CATEGORY_MINIMUMS)
    staged_counts = Counter(str(row.get("category") or "unknown").strip() or "unknown" for row in staged_microtext)
    label_complete = [row for row in staged_microtext if microtext_label_text(row)]
    canonical = [
        row for row in label_complete
        if canonical_staged_category(row)
    ]
    provisional = [
        row for row in label_complete
        if not canonical_staged_category(row)
    ]
    missing_label_rows = len(staged_microtext) - len(label_complete)
    canonical_counts = Counter(str(row.get("category") or "").strip() for row in canonical)
    provisional_counts = Counter(str(row.get("category") or "").strip() or "unknown" for row in provisional)
    canonical_non_pin = sum(count for category, count in canonical_counts.items() if category != "pin_label")
    canonical_pin = canonical_counts.get("pin_label", 0)
    provisional_non_pin = len(provisional)

    row_gap = max(0, row_target - current_gold_rows)
    visualdiff_used_for_target = min(row_gap, staged_visualdiff_rows)
    microtext_needed_for_target = max(0, row_gap - visualdiff_used_for_target)
    final_microtext_rows_at_target = len(active_microtext) + microtext_needed_for_target
    max_final_pin_rows = int(MICROTEXT_PIN_SHARE_MAX * final_microtext_rows_at_target + 1e-9)
    allowed_pin_additions_at_target = max(0, max_final_pin_rows - active_balance["pin_label_rows"])
    required_non_pin_at_target = max(
        0,
        microtext_needed_for_target - min(canonical_pin, allowed_pin_additions_at_target),
    )
    canonical_non_pin_shortfall = max(0, required_non_pin_at_target - canonical_non_pin)
    taxonomy_inclusive_non_pin_shortfall = max(
        0,
        required_non_pin_at_target - canonical_non_pin - provisional_non_pin,
    )

    canonical_pin_allowed = balance_compliant_pin_additions(
        len(active_microtext),
        active_balance["pin_label_rows"],
        canonical_non_pin,
        canonical_pin,
    )
    taxonomy_pin_allowed = balance_compliant_pin_additions(
        len(active_microtext),
        active_balance["pin_label_rows"],
        canonical_non_pin + provisional_non_pin,
        canonical_pin,
    )
    projected_category_counts = Counter(active_balance["category_counts"])
    projected_category_counts.update(canonical_counts)
    floor_shortfalls_after_canonical = {
        category: max(0, minimum - projected_category_counts.get(category, 0))
        for category, minimum in MICROTEXT_CATEGORY_MINIMUMS.items()
    }
    return {
        "policy": {
            "pin_label_share_max": MICROTEXT_PIN_SHARE_MAX,
            "category_minimums": dict(MICROTEXT_CATEGORY_MINIMUMS),
            "provisional_taxonomy_rows_do_not_count_as_canonical": True,
            "component_value_requires_full_span_profile_match": True,
            "process_label_requires_strict_label_filter": True,
        },
        "active": active_balance,
        "staged": {
            "rows": len(staged_microtext),
            "category_counts": dict(sorted(staged_counts.items())),
            "label_complete_rows": len(label_complete),
            "missing_label_rows": missing_label_rows,
            "canonical_rows": len(canonical),
            "canonical_category_counts": dict(sorted(canonical_counts.items())),
            "canonical_non_pin_rows": canonical_non_pin,
            "canonical_pin_rows": canonical_pin,
            "provisional_taxonomy_rows": len(provisional),
            "provisional_taxonomy_category_counts": dict(sorted(provisional_counts.items())),
        },
        "row_target_projection": {
            "row_gap": row_gap,
            "staged_visualdiff_rows_used": visualdiff_used_for_target,
            "microtext_additions_needed": microtext_needed_for_target,
            "required_non_pin_microtext_additions": required_non_pin_at_target,
            "available_canonical_non_pin_rows": canonical_non_pin,
            "additional_canonical_non_pin_rows_needed": canonical_non_pin_shortfall,
            "additional_non_pin_rows_needed_if_provisional_taxonomy_is_approved": taxonomy_inclusive_non_pin_shortfall,
            "canonical_balance_compliant_row_target_feasible": canonical_non_pin_shortfall == 0
            and canonical_non_pin + canonical_pin >= microtext_needed_for_target,
        },
        "balance_compliant_upper_bounds": {
            "canonical_taxonomy_gold_rows": current_gold_rows
            + staged_visualdiff_rows
            + canonical_non_pin
            + canonical_pin_allowed,
            "taxonomy_inclusive_gold_rows": current_gold_rows
            + staged_visualdiff_rows
            + canonical_non_pin
            + provisional_non_pin
            + taxonomy_pin_allowed,
            "canonical_pin_rows_selectable": canonical_pin_allowed,
            "taxonomy_inclusive_pin_rows_selectable": taxonomy_pin_allowed,
        },
        "category_floor_shortfalls_after_all_canonical_staged_rows": floor_shortfalls_after_canonical,
    }


def build_report(
    root: Path,
    cohorts: list[CohortSpec],
    agreement_decisions: int = 0,
    packet_index_path: Path | None = None,
    date_label: str | None = None,
    row_target: int = 25_000,
    source_target: int = 150,
    family_target: int = 30,
    test_target: int = 5_000,
    split_plan_path: Path | None = None,
    payload_alias_report_path: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    docs, manifest_pairs = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    active_microtext = read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    active_visualdiff = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    active_ids = active_aliases(active_microtext, "microtext") | active_aliases(
        active_visualdiff, "visualdiff"
    )
    active_rows = active_microtext + active_visualdiff
    active_test_rows = sum(str(row.get("split") or "").strip().lower() == "test" for row in active_rows)
    active_families = {
        visualdiff_family(str(row.get("pair_id") or row.get("id") or ""))
        for row in active_visualdiff
        if row.get("pair_id") or row.get("id")
    }

    active_doc_ids: set[str] = {
        str(row.get("doc_id") or "").strip()
        for row in active_microtext
        if str(row.get("doc_id") or "").strip()
    }
    unresolved_active_sources: list[dict[str, str]] = []
    for row in active_visualdiff:
        resolved, reason = row_source_docs(row, docs, manifest_pairs)
        active_doc_ids.update(resolved)
        if reason:
            unresolved_active_sources.append(
                {"identity": row_identity(row), "reason": reason}
            )

    source_cache: dict[str, dict[str, Any]] = {}

    payload_alias_map: dict[str, str] = {}
    payload_alias_group_count = 0
    payload_alias_report = ""
    if payload_alias_report_path:
        absolute_alias_report = (
            payload_alias_report_path
            if payload_alias_report_path.is_absolute()
            else root / payload_alias_report_path
        )
        payload_alias_report = (
            absolute_alias_report.relative_to(root).as_posix()
            if absolute_alias_report.is_relative_to(root)
            else absolute_alias_report.as_posix()
        )
        payload_alias_map, payload_alias_group_count = load_payload_alias_map(
            absolute_alias_report
        )

    def source_audit(doc_id: str) -> dict[str, Any]:
        if doc_id not in source_cache:
            source_cache[doc_id] = audit_source_doc(root, doc_id, docs, inventory)
        return source_cache[doc_id]

    active_source_rows = [source_audit(doc_id) for doc_id in sorted(active_doc_ids)]
    active_payloads = {source_payload_key(row) for row in active_source_rows if row["paper_ready"]}

    payload_alias_unresolved_regions: list[dict[str, str]] = []
    active_payload_alias_region_index: dict[
        tuple[str, int], list[dict[str, Any]]
    ] = defaultdict(list)
    for row in active_microtext:
        doc_id = str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
        if doc_id not in payload_alias_map:
            continue
        alias_region = payload_alias_region_key(
            row,
            microtext_region_geometry(row),
            root=root,
            alias_map=payload_alias_map,
        )
        if not alias_region:
            payload_alias_unresolved_regions.append(
                {
                    "cohort": "active_gold",
                    "identity": capacity_identity(row),
                    "doc_id": doc_id,
                }
            )
            continue
        canonical_doc_id, page_index, bbox = alias_region
        active_payload_alias_region_index[(canonical_doc_id, page_index)].append(
            {"bbox": bbox, "identity": capacity_identity(row)}
        )

    active_region_ids = {
        identity
        for row in active_microtext
        for identity in [capacity_identity(row)]
        if identity.startswith("microtext-region:")
    }
    identity_cohorts: dict[str, list[str]] = defaultdict(list)
    identity_phases: dict[str, set[str]] = defaultdict(set)
    staged_region_rows: list[dict[str, Any]] = []
    staged_microtext_expansion_rows: dict[str, dict[str, Any]] = {}
    staged_visualdiff_expansion_ids: set[str] = set()
    cohort_results: list[dict[str, Any]] = []
    staged_family_sets: dict[str, set[str]] = {"current": set(), "future": set()}
    staged_doc_sets: dict[str, set[str]] = {"current": set(), "future": set()}
    staged_expansion_sets: dict[str, set[str]] = {"current": set(), "future": set()}
    staged_replacement_sets: dict[str, set[str]] = {"current": set(), "future": set()}
    all_staged_splits: Counter[str] = Counter()
    all_staged_expansion_splits: Counter[str] = Counter()
    unresolved_staged_sources: list[dict[str, str]] = []
    staged_text_encoding_issues: list[dict[str, Any]] = []
    staged_payload_alias_region_index: dict[
        tuple[str, int], list[dict[str, Any]]
    ] = defaultdict(list)
    payload_alias_active_overlap_count = 0
    payload_alias_staged_overlap_count = 0
    payload_alias_active_overlap_examples: list[dict[str, Any]] = []
    payload_alias_staged_overlap_examples: list[dict[str, Any]] = []
    split_reservations: dict[tuple[str, str], str] = {}
    split_plan_issues: list[dict[str, str]] = []
    split_plan_report_path = ""
    if split_plan_path:
        absolute_split_plan = split_plan_path if split_plan_path.is_absolute() else root / split_plan_path
        split_plan_report_path = (
            absolute_split_plan.relative_to(root).as_posix()
            if absolute_split_plan.is_relative_to(root)
            else absolute_split_plan.as_posix()
        )
        split_reservations, split_plan_issues = load_split_reservations(absolute_split_plan)

    for cohort in cohorts:
        absolute = cohort.path if cohort.path.is_absolute() else root / cohort.path
        input_rows = read_rows(absolute)
        excluded_reasons = Counter(
            reason
            for row in input_rows
            for reason in [capacity_exclusion_reason(row)]
            if reason
        )
        rows = [row for row in input_rows if not capacity_exclusion_reason(row)]
        identities = [capacity_identity(row) for row in rows]
        identity_counts = Counter(identity for identity in identities if identity)
        missing_identifiers = sum(not identity for identity in identities)
        active_overlaps: set[str] = set()
        expansion_ids: set[str] = set()
        replacement_ids: set[str] = set()
        cohort_docs: set[str] = set()
        cohort_expansion_docs: set[str] = set()
        cohort_families: set[str] = set()
        cohort_expansion_families: set[str] = set()
        task_counts: Counter[str] = Counter()
        split_counts: Counter[str] = Counter()
        expansion_split_counts: Counter[str] = Counter()
        replacement_split_counts: Counter[str] = Counter()
        cohort_payload_alias_active_overlaps = 0
        cohort_payload_alias_staged_overlaps = 0
        for row, identity in zip(rows, identities):
            task = task_for_row(row)
            task_counts[task] += 1
            row_encoding_issues = benchmark_text_encoding_issues(row)
            for issue in row_encoding_issues:
                if len(staged_text_encoding_issues) < 100:
                    staged_text_encoding_issues.append(
                        {
                            "cohort": cohort.name,
                            "identity": identity,
                            **issue,
                        }
                    )
            split_unit = staged_split_unit(row)
            split = split_reservations.get(split_unit, "")
            if split_plan_path and not split:
                split_plan_issues.append(
                    {
                        "type": "missing_split_reservation",
                        "task": split_unit[0],
                        "unit_id": split_unit[1],
                    }
                )
            if not split:
                split = str(
                    row.get("reserved_split") or row.get("split") or "unassigned"
                ).strip().lower()
            if not split or split in {"provisional", "provisional_review", "needs_review"}:
                split = "unassigned_or_provisional"
            split_counts[split] += 1
            all_staged_splits[split] += 1
            if not identity:
                continue
            if task == "microtext":
                staged_region_rows.append(
                    {"cohort": cohort.name, "identity": identity, "row": row}
                )
            aliases = {f"{task}:{alias}" for alias in identity_aliases(row)}
            alias_region = None
            alias_active_collision: dict[str, Any] | None = None
            alias_staged_collision: dict[str, Any] | None = None
            if task == "microtext":
                doc_id = str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
                if doc_id in payload_alias_map:
                    alias_region = payload_alias_region_key(
                        row,
                        microtext_region_geometry(row),
                        root=root,
                        alias_map=payload_alias_map,
                    )
                    if not alias_region:
                        payload_alias_unresolved_regions.append(
                            {"cohort": cohort.name, "identity": identity, "doc_id": doc_id}
                        )
                    else:
                        canonical_doc_id, alias_page_index, alias_bbox = alias_region
                        alias_active_collision = next(
                            (
                                entry
                                for entry in active_payload_alias_region_index[
                                    (canonical_doc_id, alias_page_index)
                                ]
                                if bboxes_are_near_duplicates(alias_bbox, entry["bbox"])
                            ),
                            None,
                        )
                        alias_staged_collision = next(
                            (
                                entry
                                for entry in staged_payload_alias_region_index[
                                    (canonical_doc_id, alias_page_index)
                                ]
                                if bboxes_are_near_duplicates(alias_bbox, entry["bbox"])
                            ),
                            None,
                        )

            is_expansion = False
            if aliases & active_ids or identity in active_region_ids or alias_active_collision:
                active_overlaps.add(identity)
                if alias_active_collision:
                    payload_alias_active_overlap_count += 1
                    cohort_payload_alias_active_overlaps += 1
                    if len(payload_alias_active_overlap_examples) < 500:
                        payload_alias_active_overlap_examples.append(
                            {
                                "cohort": cohort.name,
                                "identity": identity,
                                "canonical_doc_id": alias_region[0],
                                "page_index": alias_region[1],
                                "bbox": list(alias_region[2]),
                                "active_identity": alias_active_collision["identity"],
                                "active_bbox": list(alias_active_collision["bbox"]),
                            }
                        )
            elif alias_staged_collision:
                payload_alias_staged_overlap_count += 1
                cohort_payload_alias_staged_overlaps += 1
                if len(payload_alias_staged_overlap_examples) < 500:
                    payload_alias_staged_overlap_examples.append(
                        {
                            "cohort": cohort.name,
                            "identity": identity,
                            "canonical_doc_id": alias_region[0],
                            "page_index": alias_region[1],
                            "bbox": list(alias_region[2]),
                            "prior_cohort": alias_staged_collision["cohort"],
                            "prior_identity": alias_staged_collision["identity"],
                            "prior_bbox": list(alias_staged_collision["bbox"]),
                        }
                    )
            elif is_provenance_replacement(row):
                replacement_ids.add(identity)
                staged_replacement_sets[cohort.phase].add(identity)
                replacement_split_counts[split] += 1
            else:
                is_expansion = True
                expansion_ids.add(identity)
                staged_expansion_sets[cohort.phase].add(identity)
                expansion_split_counts[split] += 1
                all_staged_expansion_splits[split] += 1
                if task == "microtext":
                    staged_microtext_expansion_rows.setdefault(identity, row)
                else:
                    staged_visualdiff_expansion_ids.add(identity)
            if alias_region:
                canonical_doc_id, alias_page_index, alias_bbox = alias_region
                staged_payload_alias_region_index[(canonical_doc_id, alias_page_index)].append(
                    {
                        "bbox": alias_bbox,
                        "identity": identity,
                        "cohort": cohort.name,
                        "is_expansion": is_expansion,
                    }
                )
            identity_cohorts[identity].append(cohort.name)
            identity_phases[identity].add(cohort.phase)
            resolved_docs, reason = row_source_docs(row, docs, manifest_pairs)
            cohort_docs.update(resolved_docs)
            if is_expansion:
                cohort_expansion_docs.update(resolved_docs)
                staged_doc_sets[cohort.phase].update(resolved_docs)
            if reason:
                unresolved_staged_sources.append(
                    {"cohort": cohort.name, "identity": identity, "reason": reason}
                )
            if task == "visualdiff":
                family = visualdiff_family(identity.removeprefix("visualdiff:"))
                cohort_families.add(family)
                if is_expansion:
                    cohort_expansion_families.add(family)
        staged_family_sets[cohort.phase].update(
            cohort_expansion_families - active_families
        )
        cohort_source_rows = [source_audit(doc_id) for doc_id in sorted(cohort_docs)]
        cohort_expansion_source_rows = [
            source_audit(doc_id) for doc_id in sorted(cohort_expansion_docs)
        ]
        cohort_payloads = {source_payload_key(row) for row in cohort_source_rows if row["paper_ready"]}
        cohort_expansion_payloads = {
            source_payload_key(row)
            for row in cohort_expansion_source_rows
            if row["paper_ready"]
        }
        cohort_results.append(
            {
                "phase": cohort.phase,
                "name": cohort.name,
                "path": absolute.relative_to(root).as_posix()
                if absolute.is_relative_to(root)
                else str(absolute),
                "input_rows": len(input_rows),
                "rows": len(rows),
                "excluded_non_capacity_rows": len(input_rows) - len(rows),
                "excluded_non_capacity_reasons": dict(sorted(excluded_reasons.items())),
                "task_counts": dict(sorted(task_counts.items())),
                "split_counts": dict(sorted(split_counts.items())),
                "expansion_split_counts": dict(sorted(expansion_split_counts.items())),
                "replacement_split_counts": dict(sorted(replacement_split_counts.items())),
                "missing_identifiers": missing_identifiers,
                "duplicate_identities_within_cohort": sum(
                    count - 1 for count in identity_counts.values() if count > 1
                ),
                "active_gold_overlaps": len(active_overlaps),
                "payload_alias_active_overlaps": cohort_payload_alias_active_overlaps,
                "payload_alias_staged_overlaps": cohort_payload_alias_staged_overlaps,
                "expansion_rows": len(expansion_ids),
                "provenance_replacement_rows": len(replacement_ids),
                "source_docs": len(cohort_docs),
                "paper_ready_source_docs": sum(row["paper_ready"] for row in cohort_source_rows),
                "source_payloads": len(cohort_payloads),
                "new_source_payloads_vs_active": len(
                    cohort_expansion_payloads - active_payloads
                ),
                "visualdiff_families": len(cohort_families),
                "new_visualdiff_families": len(
                    cohort_expansion_families - active_families
                ),
                "text_encoding_errors": sum(
                    len(benchmark_text_encoding_issues(row)) for row in rows
                ),
            }
        )

    overlapping_identities = {
        identity: sorted(set(names))
        for identity, names in identity_cohorts.items()
        if len(set(names)) > 1
    }
    near_region_overlap_count, near_region_overlap_examples = find_near_region_overlaps(
        staged_region_rows
    )
    all_expansion_ids = staged_expansion_sets["current"] | staged_expansion_sets["future"]
    future_only_expansion_ids = staged_expansion_sets["future"] - staged_expansion_sets["current"]
    all_replacement_ids = staged_replacement_sets["current"] | staged_replacement_sets["future"]
    future_only_replacement_ids = (
        staged_replacement_sets["future"] - staged_replacement_sets["current"]
    )
    current_source_rows = [source_audit(doc_id) for doc_id in sorted(staged_doc_sets["current"])]
    all_staged_doc_ids = staged_doc_sets["current"] | staged_doc_sets["future"]
    all_staged_source_rows = [source_audit(doc_id) for doc_id in sorted(all_staged_doc_ids)]
    current_staged_payloads = {
        source_payload_key(row) for row in current_source_rows if row["paper_ready"]
    }
    all_staged_payloads = {
        source_payload_key(row) for row in all_staged_source_rows if row["paper_ready"]
    }
    current_new_payloads = current_staged_payloads - active_payloads
    all_new_payloads = all_staged_payloads - active_payloads
    current_new_families = staged_family_sets["current"]
    all_new_families = staged_family_sets["current"] | staged_family_sets["future"]

    packet_index: dict[str, Any] = {}
    packet_index_checks: dict[str, Any] = {}
    if packet_index_path:
        absolute_index = packet_index_path if packet_index_path.is_absolute() else root / packet_index_path
        packet_index = json.loads(absolute_index.read_text(encoding="utf-8"))
        expected = packet_index.get("totals", {})
        current_input_rows = sum(row["rows"] for row in cohort_results if row["phase"] == "current")
        packet_index_checks = {
            "path": absolute_index.relative_to(root).as_posix()
            if absolute_index.is_relative_to(root)
            else str(absolute_index),
            "expected_review_decisions": int(expected.get("review_rows") or 0),
            "expected_expansion_rows": int(expected.get("gold_expansion_rows") or 0),
            "current_cohort_expansion_rows": len(staged_expansion_sets["current"]),
            "agreement_decisions": agreement_decisions,
            "decision_count_matches": current_input_rows + agreement_decisions
            == int(expected.get("review_rows") or 0),
            "expansion_count_matches": len(staged_expansion_sets["current"])
            == int(expected.get("gold_expansion_rows") or 0),
        }

    non_paper_ready_docs = [row for row in all_staged_source_rows if not row["paper_ready"]]
    capacity_input_clean = bool(
        not overlapping_identities
        and not near_region_overlap_count
        and not payload_alias_active_overlap_count
        and not payload_alias_staged_overlap_count
        and not payload_alias_unresolved_regions
        and not unresolved_staged_sources
        and not split_plan_issues
        and not non_paper_ready_docs
        and not staged_text_encoding_issues
        and all(row["missing_identifiers"] == 0 for row in cohort_results)
        and all(row["duplicate_identities_within_cohort"] == 0 for row in cohort_results)
        and all(row["active_gold_overlaps"] == 0 for row in cohort_results)
        and (not packet_index_checks or packet_index_checks["decision_count_matches"])
        and (not packet_index_checks or packet_index_checks["expansion_count_matches"])
    )

    current_gold_rows = len(active_rows)
    balance_capacity = microtext_capacity_balance(
        active_microtext,
        list(staged_microtext_expansion_rows.values()),
        current_gold_rows=current_gold_rows,
        row_target=row_target,
        staged_visualdiff_rows=len(staged_visualdiff_expansion_ids),
    )
    current_unique_sources = len(active_payloads)
    current_test_rows = active_test_rows
    unassigned_staged_rows = (
        all_staged_expansion_splits.get("unassigned", 0)
        + all_staged_expansion_splits.get("unassigned_or_provisional", 0)
    )
    report = {
        "date_label": date_label or date.today().isoformat(),
        "capacity_input_clean": capacity_input_clean,
        "current_gold": {
            "rows": current_gold_rows,
            "microtext_rows": len(active_microtext),
            "visualdiff_rows": len(active_visualdiff),
            "test_rows": current_test_rows,
            "source_docs": len(active_doc_ids),
            "unique_source_payloads": current_unique_sources,
            "visualdiff_families": len(active_families),
            "unresolved_source_rows": len(unresolved_active_sources),
        },
        "human_work": {
            "agreement_decisions": agreement_decisions,
            "current_expansion_rows": len(staged_expansion_sets["current"]),
            "future_expansion_rows_after_current_dedup": len(future_only_expansion_ids),
            "all_unique_expansion_rows": len(all_expansion_ids),
            "current_provenance_replacement_rows": len(staged_replacement_sets["current"]),
            "future_provenance_replacement_rows_after_current_dedup": len(
                future_only_replacement_ids
            ),
            "all_unique_provenance_replacement_rows": len(all_replacement_ids),
            "all_review_decisions_including_agreement": agreement_decisions
            + len(staged_expansion_sets["current"])
            + len(future_only_expansion_ids)
            + len(all_replacement_ids),
        },
        "split_plan": {
            "path": split_plan_report_path,
            "reservations": len(split_reservations),
            "issues": split_plan_issues,
            "applied": bool(split_plan_path),
        },
        "capacity": {
            "current_assignment_all_accepted": {
                "gold_rows": current_gold_rows + len(staged_expansion_sets["current"]),
                "unique_source_payloads_upper_bound": current_unique_sources + len(current_new_payloads),
                "visualdiff_families_upper_bound": len(active_families | current_new_families),
            },
            "all_staged_expansion_accepted": {
                "gold_rows": current_gold_rows + len(all_expansion_ids),
                "unique_source_payloads_upper_bound": current_unique_sources + len(all_new_payloads),
                "visualdiff_families_upper_bound": len(active_families | all_new_families),
                "explicit_test_rows": current_test_rows
                + all_staged_expansion_splits.get("test", 0),
                "unassigned_or_provisional_rows": unassigned_staged_rows,
            },
            "acceptance_scenarios_rows_only": acceptance_projection(
                current_gold_rows, len(all_expansion_ids)
            ),
        },
        "targets": {
            "rows": {
                "target": row_target,
                "current": current_gold_rows,
                "all_staged_upper_bound": current_gold_rows + len(all_expansion_ids),
                "remaining_after_all_staged": max(
                    0, row_target - current_gold_rows - len(all_expansion_ids)
                ),
                "can_close_from_staged_capacity": current_gold_rows + len(all_expansion_ids)
                >= row_target,
            },
            "unique_source_payloads": {
                "target": source_target,
                "current": current_unique_sources,
                "all_staged_upper_bound": current_unique_sources + len(all_new_payloads),
                "remaining_after_all_staged": max(
                    0, source_target - current_unique_sources - len(all_new_payloads)
                ),
                "can_close_from_staged_capacity": current_unique_sources + len(all_new_payloads)
                >= source_target,
            },
            "visualdiff_families": {
                "target": family_target,
                "current": len(active_families),
                "all_staged_upper_bound": len(active_families | all_new_families),
                "remaining_after_all_staged": max(
                    0, family_target - len(active_families | all_new_families)
                ),
                "can_close_from_staged_capacity": len(active_families | all_new_families)
                >= family_target,
            },
            "test_rows": {
                "target": test_target,
                "current": current_test_rows,
                "explicit_all_staged_upper_bound": current_test_rows
                + all_staged_expansion_splits.get("test", 0),
                "unassigned_or_provisional_staged_rows": unassigned_staged_rows,
                "remaining_before_split_assignment": max(
                    0,
                    test_target
                    - current_test_rows
                    - all_staged_expansion_splits.get("test", 0),
                ),
                "can_close_from_explicit_staged_test_capacity": current_test_rows
                + all_staged_expansion_splits.get("test", 0)
                >= test_target,
            },
            "agreement": {
                "target": 185,
                "current_completed": 0,
                "assigned": agreement_decisions,
                "can_close_when_returned_and_validated": agreement_decisions >= 185,
            },
        },
        "cohorts": cohort_results,
        "packet_index_checks": packet_index_checks,
        "source_capacity": {
            "current_new_payloads": len(current_new_payloads),
            "all_new_payloads": len(all_new_payloads),
            "all_staged_source_docs": len(all_staged_doc_ids),
            "all_staged_paper_ready_source_docs": sum(
                row["paper_ready"] for row in all_staged_source_rows
            ),
            "non_paper_ready_docs": [row["doc_id"] for row in non_paper_ready_docs],
            "new_payload_keys": sorted(all_new_payloads),
        },
        "family_capacity": {
            "current_new_families": len(current_new_families),
            "all_new_families": len(all_new_families),
            "new_family_ids": sorted(all_new_families),
        },
        "microtext_balance_capacity": balance_capacity,
        "overlaps": {
            "cross_cohort_identity_count": len(overlapping_identities),
            "cross_cohort_identities": overlapping_identities,
            "near_region_pair_count": near_region_overlap_count,
            "near_region_examples": near_region_overlap_examples,
            "near_region_examples_truncated": near_region_overlap_count
            > len(near_region_overlap_examples),
            "near_region_policy": {
                "iou_threshold": NEAR_OVERLAP_IOU_THRESHOLD,
                "containment_threshold": NEAR_OVERLAP_CONTAINMENT_THRESHOLD,
                "minimum_area_ratio": NEAR_OVERLAP_AREA_RATIO_THRESHOLD,
            },
            "payload_alias_report": payload_alias_report,
            "payload_alias_groups": payload_alias_group_count,
            "payload_alias_doc_ids": len(payload_alias_map),
            "payload_alias_active_overlap_count": payload_alias_active_overlap_count,
            "payload_alias_active_overlap_examples": payload_alias_active_overlap_examples,
            "payload_alias_staged_overlap_count": payload_alias_staged_overlap_count,
            "payload_alias_staged_overlap_examples": payload_alias_staged_overlap_examples,
            "payload_alias_unresolved_region_count": len(
                payload_alias_unresolved_regions
            ),
        },
        "issues": {
            "unresolved_active_sources": unresolved_active_sources,
            "unresolved_staged_sources": unresolved_staged_sources,
            "non_paper_ready_source_docs": non_paper_ready_docs,
            "split_plan": split_plan_issues,
            "text_encoding": staged_text_encoding_issues,
            "text_encoding_examples_truncated": sum(
                row["text_encoding_errors"] for row in cohort_results
            )
            > len(staged_text_encoding_issues),
            "payload_alias_unresolved_regions": payload_alias_unresolved_regions,
        },
        "interpretation": (
            "Capacity is not gold. Terminal held, rejected, blocked, and superseded rows are excluded. "
            "Remaining rows and source/family upper bounds count only after human acceptance and strict "
            "provenance, split, leakage, duplicate, and v2 validation. A supplied family-safe split plan "
            "counts only as a reservation; it does not modify frozen split files or current gold."
        ),
    }
    return report


def write_outputs(report: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    current = report["current_gold"]
    work = report["human_work"]
    targets = report["targets"]
    lines = [
        "# Eng_Bench Gold v2.0 Staged Capacity Audit",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Capacity inputs clean: `{str(report['capacity_input_clean']).lower()}`",
        f"- Near-duplicate staged region pairs: `{report['overlaps']['near_region_pair_count']}`",
        f"- Payload-alias overlaps with active Gold: `{report['overlaps']['payload_alias_active_overlap_count']}`",
        f"- Payload-alias overlaps within staged cohorts: `{report['overlaps']['payload_alias_staged_overlap_count']}`",
        f"- Unresolved payload-alias regions: `{report['overlaps']['payload_alias_unresolved_region_count']}`",
        f"- Current gold rows: `{current['rows']}`",
        f"- Current assignment: `{work['agreement_decisions']}` agreement plus "
        f"`{work['current_expansion_rows']}` expansion decisions",
        f"- Future expansion buffer: `{work['future_expansion_rows_after_current_dedup']}` rows",
        f"- All unique staged expansion rows: `{work['all_unique_expansion_rows']}`",
        f"- Balance-compliant canonical upper bound: "
        f"`{report['microtext_balance_capacity']['balance_compliant_upper_bounds']['canonical_taxonomy_gold_rows']}` Gold rows",
        f"- Additional canonical non-pin rows needed for the 25k target: "
        f"`{report['microtext_balance_capacity']['row_target_projection']['additional_canonical_non_pin_rows_needed']}`",
        "",
        "## Open-Gate Capacity",
        "",
        "| Gate | Current | All-staged upper bound | Target | Remaining | Can close |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
        f"| Gold rows | {targets['rows']['current']} | {targets['rows']['all_staged_upper_bound']} | "
        f"{targets['rows']['target']} | {targets['rows']['remaining_after_all_staged']} | "
        f"`{targets['rows']['can_close_from_staged_capacity']}` |",
        f"| Unique source payloads | {targets['unique_source_payloads']['current']} | "
        f"{targets['unique_source_payloads']['all_staged_upper_bound']} | "
        f"{targets['unique_source_payloads']['target']} | "
        f"{targets['unique_source_payloads']['remaining_after_all_staged']} | "
        f"`{targets['unique_source_payloads']['can_close_from_staged_capacity']}` |",
        f"| VisualDiff families | {targets['visualdiff_families']['current']} | "
        f"{targets['visualdiff_families']['all_staged_upper_bound']} | "
        f"{targets['visualdiff_families']['target']} | "
        f"{targets['visualdiff_families']['remaining_after_all_staged']} | "
        f"`{targets['visualdiff_families']['can_close_from_staged_capacity']}` |",
        f"| Explicit test rows | {targets['test_rows']['current']} | "
        f"{targets['test_rows']['explicit_all_staged_upper_bound']} | "
        f"{targets['test_rows']['target']} | "
        f"{targets['test_rows']['remaining_before_split_assignment']} | "
        f"`{targets['test_rows']['can_close_from_explicit_staged_test_capacity']}` |",
        "",
        "## Cohorts",
        "",
        "| Phase | Cohort | Input | Eligible | Excluded | Expansion | Docs | New payloads | New families |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["cohorts"]:
        lines.append(
            f"| {row['phase']} | `{row['name']}` | {row['input_rows']} | {row['rows']} | "
            f"{row['excluded_non_capacity_rows']} | {row['expansion_rows']} | {row['source_docs']} | "
            f"{row['new_source_payloads_vs_active']} | "
            f"{row['new_visualdiff_families']} |"
        )
    balance = report["microtext_balance_capacity"]
    lines.extend(
        [
            "",
            "## MicroText Balance Capacity",
            "",
            f"- Active pin-label share: `{balance['active']['pin_label_share']:.2%}` "
            f"(maximum `{balance['policy']['pin_label_share_max']:.0%}`).",
            f"- Canonical staged non-pin rows: `{balance['staged']['canonical_non_pin_rows']}`.",
            f"- Provisional-taxonomy rows: `{balance['staged']['provisional_taxonomy_rows']}`.",
            f"- Missing-label staged rows: `{balance['staged']['missing_label_rows']}`.",
            f"- Canonical balance-compliant Gold upper bound: "
            f"`{balance['balance_compliant_upper_bounds']['canonical_taxonomy_gold_rows']}`.",
            f"- Taxonomy-inclusive balance-compliant Gold upper bound: "
            f"`{balance['balance_compliant_upper_bounds']['taxonomy_inclusive_gold_rows']}`.",
            f"- Additional canonical non-pin rows needed to reach the row target: "
            f"`{balance['row_target_projection']['additional_canonical_non_pin_rows_needed']}`.",
            f"- Category-floor shortfalls after all canonical staged rows: "
            f"`{balance['category_floor_shortfalls_after_all_canonical_staged_rows']}`.",
        ]
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            report["interpretation"],
            "",
        ]
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")


def parse_cohort(phase: str, value: str) -> CohortSpec:
    if "=" not in value:
        raise argparse.ArgumentTypeError("cohort must use NAME=PATH")
    name, path = value.split("=", 1)
    if not name.strip() or not path.strip():
        raise argparse.ArgumentTypeError("cohort must use non-empty NAME=PATH")
    return CohortSpec(phase=phase, name=name.strip(), path=Path(path.strip()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--current-cohort", action="append", default=[], type=lambda value: parse_cohort("current", value)
    )
    parser.add_argument(
        "--future-cohort", action="append", default=[], type=lambda value: parse_cohort("future", value)
    )
    parser.add_argument("--agreement-decisions", type=int, default=0)
    parser.add_argument("--packet-index", type=Path)
    parser.add_argument("--date-label", default=None)
    parser.add_argument("--row-target", type=int, default=25_000)
    parser.add_argument("--source-target", type=int, default=150)
    parser.add_argument("--family-target", type=int, default=30)
    parser.add_argument("--test-target", type=int, default=5_000)
    parser.add_argument("--split-plan", type=Path)
    parser.add_argument(
        "--payload-alias-report",
        type=Path,
        help="Source-payload duplicate audit for normalized cross-alias region checks.",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    cohorts = args.current_cohort + args.future_cohort
    if not cohorts:
        parser.error("at least one --current-cohort or --future-cohort is required")
    root = args.root.resolve()
    report = build_report(
        root,
        cohorts,
        agreement_decisions=args.agreement_decisions,
        packet_index_path=args.packet_index,
        date_label=args.date_label,
        row_target=args.row_target,
        source_target=args.source_target,
        family_target=args.family_target,
        test_target=args.test_target,
        split_plan_path=args.split_plan,
        payload_alias_report_path=args.payload_alias_report,
    )
    json_path = args.output_json if args.output_json.is_absolute() else root / args.output_json
    md_path = args.output_md if args.output_md.is_absolute() else root / args.output_md
    write_outputs(report, json_path, md_path)
    print(json.dumps({
        "capacity_input_clean": report["capacity_input_clean"],
        "current_gold": report["current_gold"],
        "human_work": report["human_work"],
        "targets": report["targets"],
    }, indent=2))
    return 0 if report["capacity_input_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
