#!/usr/bin/env python3
"""Select fresh microtext rows from inactive, release-safe source documents."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_source_conversion_readiness as source_readiness
import audit_source_payload_duplicates
import review_queue_inventory
from audit_active_gold_provenance import file_sha256, rights_blocker


DEFAULT_CATEGORIES = {
    "component_value",
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "pin_label",
    "pipe_line_tag",
    "process_label",
    "process_value",
    "room_label",
    "tolerance_value",
}

CATEGORY_PRIORITY = {
    "equipment_tag": 8,
    "instrument_tag": 7,
    "room_label": 6,
    "tolerance_value": 6,
    "dimension_value": 5,
    "pipe_line_tag": 4,
    "process_label": 4,
    "process_value": 3,
    "component_value": 2,
    "pin_label": 1,
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_review_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    return read_jsonl(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def release_candidate_ids(root: Path) -> set[str]:
    path = root / "SOURCE_CANDIDATE_VALIDATION.csv"
    if not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    allowed: set[str] = set()
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        posture = str(row.get("release_posture") or "").strip().lower()
        next_action = " ".join(
            str(row.get(field) or "").strip().lower()
            for field in ("validation_next_action", "next_action", "next_step")
        )
        if candidate_id and "release_candidate" in posture and not any(
            marker in next_action for marker in ("rights_hold", "rights_review", "reject", "blocked")
        ):
            allowed.add(candidate_id)
    return allowed


def source_candidate_ids(root: Path) -> set[str]:
    path = root / "SOURCE_CANDIDATE_VALIDATION.csv"
    if not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return {
            str(row.get("candidate_id") or "").strip()
            for row in csv.DictReader(handle)
            if str(row.get("candidate_id") or "").strip()
        }


def release_safe_inventory(root: Path) -> dict[str, dict[str, str]]:
    return {
        str(row.get("doc_id") or "").strip(): row
        for row in source_readiness.read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
        and source_readiness.is_release_safe_status(str(row.get("public_status") or ""))
    }


def paper_ready_doc_ids(root: Path) -> set[str]:
    inventory = release_safe_inventory(root)
    manifests = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_jsonl(root / "manifest.jsonl")
        if row.get("type") == "doc" and str(row.get("doc_id") or "").strip()
    }
    ready: set[str] = set()
    for doc_id, source in inventory.items():
        manifest = manifests.get(doc_id)
        if not manifest:
            continue
        path_value = str(source.get("source_path") or source.get("path") or manifest.get("path") or "").strip()
        path = root / path_value if path_value else None
        recorded_sha = str(manifest.get("sha256") or "").strip().lower()
        source_url = str(source.get("source_url") or manifest.get("source_url") or "").strip()
        public_status = str(source.get("public_status") or manifest.get("public_status") or "").strip()
        if (
            path
            and path.is_file()
            and recorded_sha
            and file_sha256(path) == recorded_sha
            and source_url
            and not rights_blocker(public_status)
        ):
            ready.add(doc_id)
    return ready


def manifest_source_candidate_ids(root: Path) -> dict[str, str]:
    return {
        str(row.get("doc_id") or "").strip(): str(row.get("source_candidate_id") or "").strip()
        for row in read_jsonl(root / "manifest.jsonl")
        if row.get("type") == "doc"
        and str(row.get("doc_id") or "").strip()
        and str(row.get("source_candidate_id") or "").strip()
    }


def candidate_ids_from_review_files(root: Path, paths: list[Path]) -> set[str]:
    identities, _regions = review_exclusions_from_files(root, paths)
    return identities


def review_exclusions_from_files(
    root: Path, paths: list[Path]
) -> tuple[set[str], set[tuple[str, int, tuple[int, int, int, int]]]]:
    identities: set[str] = set()
    regions: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    for path in paths:
        absolute = path if path.is_absolute() else root / path
        for row in read_review_rows(absolute):
            if row.get("pair_id") or (row.get("image_old") and row.get("image_new")):
                continue
            identity = source_readiness.row_identity(row, "microtext")
            if identity:
                identities.add(identity)
            region = source_readiness.microtext_region_identity(row)
            if region:
                regions.add(region)
    return identities, regions


def normalized_semantic_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\s+", " ", text)


def microtext_semantic_identity(row: dict[str, Any]) -> tuple[str, int, str, str] | None:
    """Identify equivalent microtext prompts independently of render geometry."""
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    doc_id = str(row.get("doc_id") or metadata.get("doc_id") or "").strip()
    if not doc_id:
        return None
    page_value = row.get("page_index")
    if page_value in (None, ""):
        page_value = metadata.get("page_index")
    try:
        page_index = int(page_value)
    except (TypeError, ValueError):
        return None
    row_category = category(row)
    row_text = normalized_semantic_text(proposed_text(row))
    if not row_category or not row_text:
        return None
    return doc_id, page_index, row_category, row_text


def review_semantic_exclusions_from_files(
    root: Path, paths: list[Path]
) -> set[tuple[str, int, str, str]]:
    """Load conservative same-page semantic exclusions from staged cohorts."""
    identities: set[tuple[str, int, str, str]] = set()
    for path in paths:
        absolute = path if path.is_absolute() else root / path
        for row in read_review_rows(absolute):
            if row.get("pair_id") or (row.get("image_old") and row.get("image_new")):
                continue
            identity = microtext_semantic_identity(row)
            if identity:
                identities.add(identity)
    return identities


def terminal_review_vetoes(
    paths: list[Path],
) -> tuple[
    set[str],
    set[tuple[str, int, tuple[int, int, int, int]]],
    Counter[str],
]:
    """Collect tombstones so stale open copies cannot re-enter a fresh queue."""
    identities: set[str] = set()
    regions: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    reasons: Counter[str] = Counter()
    for path in paths:
        for row in read_jsonl(path):
            exclusion_reason = source_readiness.review_exclusion_reason(row)
            status = source_readiness.status_for(row)
            if not exclusion_reason and status in source_readiness.OPEN_STATUSES:
                continue
            identity = source_readiness.row_identity(row, "microtext")
            if identity:
                identities.add(identity)
            region = source_readiness.microtext_region_identity(row)
            if region:
                regions.add(region)
            reasons[exclusion_reason or f"review_status:{status or 'unknown'}"] += 1
    return identities, regions, reasons


def capacity_report_review_paths(root: Path, report_paths: list[Path]) -> list[Path]:
    """Resolve every cohort in clean staged-capacity reports, failing closed."""
    resolved: list[Path] = []
    seen: set[Path] = set()
    for report_path in report_paths:
        absolute_report = report_path if report_path.is_absolute() else root / report_path
        if not absolute_report.is_file():
            raise FileNotFoundError(f"capacity report not found: {absolute_report}")
        payload = json.loads(absolute_report.read_text(encoding="utf-8"))
        if payload.get("capacity_input_clean") is not True:
            raise ValueError(f"capacity report is not clean: {absolute_report}")
        cohorts = payload.get("cohorts")
        if not isinstance(cohorts, list):
            raise ValueError(f"capacity report has no cohort list: {absolute_report}")
        for cohort in cohorts:
            value = str(cohort.get("path") or "").strip() if isinstance(cohort, dict) else ""
            if not value:
                raise ValueError(f"capacity report cohort has no path: {absolute_report}")
            cohort_path = Path(value)
            absolute_cohort = cohort_path if cohort_path.is_absolute() else root / cohort_path
            absolute_cohort = absolute_cohort.resolve()
            if not absolute_cohort.is_file():
                raise FileNotFoundError(
                    f"capacity report cohort not found: {absolute_cohort}"
                )
            if absolute_cohort not in seen:
                resolved.append(absolute_cohort)
                seen.add(absolute_cohort)
    return resolved


def microtext_split_reservations(root: Path, split_plan_path: Path) -> dict[str, str]:
    """Load a validated microtext document-to-split reservation map."""
    absolute = split_plan_path if split_plan_path.is_absolute() else root / split_plan_path
    if not absolute.is_file():
        raise FileNotFoundError(f"split plan not found: {absolute}")
    payload = json.loads(absolute.read_text(encoding="utf-8"))
    if payload.get("valid") is not True:
        raise ValueError(f"split plan is not valid: {absolute}")
    reservations = payload.get("reservations")
    if not isinstance(reservations, list):
        raise ValueError(f"split plan has no reservation list: {absolute}")

    mapping: dict[str, str] = {}
    for row in reservations:
        if not isinstance(row, dict) or str(row.get("task") or "").strip() != "microtext":
            continue
        doc_id = str(row.get("unit_id") or "").strip()
        reserved_split = str(row.get("split") or "").strip().lower()
        if not doc_id or reserved_split not in {"train", "dev", "test"}:
            raise ValueError(f"invalid microtext reservation in split plan: {row!r}")
        previous = mapping.get(doc_id)
        if previous and previous != reserved_split:
            raise ValueError(
                f"conflicting microtext reservation for {doc_id}: {previous} vs {reserved_split}"
            )
        mapping[doc_id] = reserved_split
    return mapping


def apply_authoritative_split_reservations(
    root: Path,
    rows: list[dict[str, Any]],
    split_plan_path: Path,
    required_split: str,
) -> list[dict[str, Any]]:
    absolute = split_plan_path if split_plan_path.is_absolute() else root / split_plan_path
    payload = json.loads(absolute.read_text(encoding="utf-8"))
    if payload.get("valid") is not True:
        raise ValueError(f"split plan is not valid: {absolute}")
    reservations = {
        str(item.get("unit_id") or "").strip(): item
        for item in payload.get("reservations", [])
        if str(item.get("task") or "").strip() == "microtext"
    }
    enriched: list[dict[str, Any]] = []
    for row in rows:
        doc_id = str(row.get("doc_id") or "").strip()
        reservation = reservations.get(doc_id)
        if reservation is None:
            raise ValueError(f"selected source doc is missing from split plan: {doc_id}")
        split = str(reservation.get("split") or "").strip().lower()
        if split != required_split:
            raise ValueError(
                f"selected source doc split drifted: {doc_id}: {split} != {required_split}"
            )
        prepared = dict(row)
        prepared["reserved_split"] = split
        prepared["split_reservation_id"] = str(reservation.get("reservation_id") or "")
        prepared["split_reservation_basis"] = str(
            reservation.get("assignment_basis") or "authoritative_split_plan"
        )
        prepared["split_reservation_plan"] = (
            absolute.relative_to(root).as_posix()
            if absolute.is_relative_to(root)
            else absolute.as_posix()
        )
        prepared["safe_to_merge_gold"] = False
        enriched.append(prepared)
    return enriched


def split_plan_doc_ids(root: Path, split_plan_path: Path, split: str) -> set[str]:
    """Return microtext document units reserved to one validated split."""
    split = split.strip().lower()
    if split not in {"train", "dev", "test"}:
        raise ValueError(f"unsupported split: {split}")
    mapping = microtext_split_reservations(root, split_plan_path)
    return {doc_id for doc_id, reserved_split in mapping.items() if reserved_split == split}


def bbox_overlap_over_smaller(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> float:
    left_area = max(0, left[2] - left[0]) * max(0, left[3] - left[1])
    right_area = max(0, right[2] - right[0]) * max(0, right[3] - right[1])
    smaller = min(left_area, right_area)
    if smaller <= 0:
        return 0.0
    intersection_width = max(0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    return intersection_width * intersection_height / smaller


def region_overlaps_any(
    region: tuple[str, int, tuple[int, int, int, int]],
    indexed_regions: dict[tuple[str, int], list[tuple[int, int, int, int]]],
    threshold: float,
) -> bool:
    if threshold <= 0:
        return False
    doc_id, page_index, bbox = region
    return any(
        bbox_overlap_over_smaller(bbox, other) >= threshold
        for other in indexed_regions.get((doc_id, page_index), [])
    )


def active_source_doc_ids(root: Path) -> set[str]:
    active: set[str] = set()
    paths = (
        root / "microtext" / "annotations" / "microtext_items.jsonl",
        root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
    )
    keys = (
        "doc_id",
        "source_doc_id",
        "old_doc_id",
        "new_doc_id",
        "doc_id_old",
        "doc_id_new",
    )
    for path in paths:
        for row in read_jsonl(path):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            for source in (row, metadata):
                for key in keys:
                    value = str(source.get(key) or "").strip()
                    if value:
                        active.add(value)
    return active


def active_packet_source_doc_ids(root: Path, packet_date_label: str) -> set[str]:
    _keys, index_path = source_readiness.load_active_packet_row_keys(root, packet_date_label)
    docs: set[str] = set()
    keys = (
        "doc_id",
        "source_doc_id",
        "old_doc_id",
        "new_doc_id",
        "doc_id_old",
        "doc_id_new",
    )
    for _kind, row in source_readiness.load_active_packet_manifest_rows(root, index_path):
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        for source in (row, metadata):
            for key in keys:
                value = str(source.get(key) or "").strip()
                if value:
                    docs.add(value)
    return docs


def duplicate_payload_alias_doc_ids(
    root: Path, preferred_doc_id_groups: tuple[set[str], ...] = ()
) -> set[str]:
    aliases: set[str] = set()
    report = audit_source_payload_duplicates.build_report(root)
    for group in report["duplicate_groups"]:
        doc_ids = set(group["doc_ids"])
        canonical = str(group["canonical_doc_id"])
        for preferred_doc_ids in preferred_doc_id_groups:
            matches = sorted(doc_ids & preferred_doc_ids)
            if matches:
                canonical = matches[0]
                break
        aliases.update(doc_ids - {canonical})
    return aliases


def proposed_text(row: dict[str, Any]) -> str:
    return str(row.get("proposed_text") or row.get("target_text") or row.get("raw_text") or "").strip()


def category(row: dict[str, Any]) -> str:
    return str(row.get("category") or row.get("region_type") or row.get("label") or "").strip().lower()


def usable_text(text: str) -> bool:
    if not text or len(text) > 80 or text.count("\n") > 2:
        return False
    lowered = text.lower()
    return lowered not in {"todo", "unknown", "n/a", "none", "null"}


def resolved_version_id(row: dict[str, Any]) -> str:
    value = str(row.get("version_id") or "").strip()
    if value.casefold() in {"", "unknown", "none", "null", "n/a", "na", "tbd"}:
        return ""
    return value


def domain_category_compatible(domain: str, row_category: str) -> bool:
    domain = {
        "mechanical": "mechanical_cad",
        "civil_hydraulic": "civil_architectural",
        "civil_structural": "civil_architectural",
    }.get(domain.strip().lower(), domain.strip().lower())
    if domain == "pcb_schematic":
        return row_category in {
            "component_value",
            "dimension_value",
            "pin_label",
            "tolerance_value",
        }
    if domain == "datasheet_spec":
        return row_category in {
            "component_value",
            "dimension_value",
            "pin_label",
            "tolerance_value",
        }
    if domain == "mechanical_cad":
        return row_category in {"dimension_value", "equipment_tag", "tolerance_value"}
    if domain == "civil_architectural":
        return row_category in {
            "dimension_value",
            "equipment_tag",
            "room_label",
            "tolerance_value",
        }
    if domain == "pid":
        return row_category in {
            "dimension_value",
            "equipment_tag",
            "instrument_tag",
            "pipe_line_tag",
            "process_label",
            "process_value",
        }
    return True


def preference_score(path: Path, row: dict[str, Any]) -> tuple[int, int, int, int, float, str]:
    source = str(row.get("source") or "").lower()
    curated = int("curated" in path.name.lower() or "curated" in source or "manual" in source)
    machine_keep = int(
        str(row.get("machine_curation_status") or "").strip().lower() in {"keep", "accepted"}
    )
    category_rank = CATEGORY_PRIORITY.get(category(row), 0)
    has_context = int(bool(str(row.get("text_context") or "").strip()))
    try:
        confidence = float(row.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = {"high": 1.0, "medium": 0.5, "low": 0.0}.get(
            str(row.get("confidence") or "").strip().lower(),
            0.0,
        )
    return curated, machine_keep, category_rank, has_context, confidence, str(row.get("candidate_id") or "")


def choose_source_rows(
    candidates: list[tuple[tuple[int, int, int, int, float, str], Path, dict[str, Any]]],
    limit: int,
) -> list[tuple[tuple[int, int, int, int, float, str], Path, dict[str, Any]]]:
    ordered = sorted(candidates, key=lambda item: item[0], reverse=True)
    chosen: list[tuple[tuple[int, int, int, int, float, str], Path, dict[str, Any]]] = []
    seen_categories: set[str] = set()
    seen_texts: set[str] = set()

    for item in ordered:
        row = item[2]
        row_category = category(row)
        row_text = proposed_text(row).casefold()
        if row_category in seen_categories or row_text in seen_texts:
            continue
        chosen.append(item)
        seen_categories.add(row_category)
        seen_texts.add(row_text)
        if len(chosen) >= limit:
            return chosen

    for item in ordered:
        if item in chosen:
            continue
        row_text = proposed_text(item[2]).casefold()
        if row_text in seen_texts:
            continue
        chosen.append(item)
        seen_texts.add(row_text)
        if len(chosen) >= limit:
            return chosen
    return chosen


def round_robin_docs(
    grouped: dict[str, list[tuple[tuple[int, int, int, int, float, str], Path, dict[str, Any]]]],
    inventory: dict[str, dict[str, str]],
    limit: int,
) -> list[str]:
    by_domain: dict[str, list[str]] = defaultdict(list)
    for doc_id, rows in grouped.items():
        domain = str(inventory[doc_id].get("domain") or "unknown")
        by_domain[domain].append(doc_id)
    for docs in by_domain.values():
        docs.sort(key=lambda doc_id: (-len(grouped[doc_id]), doc_id))

    selected: list[str] = []
    domains = sorted(by_domain)
    while len(selected) < limit and domains:
        next_domains: list[str] = []
        for domain in domains:
            docs = by_domain[domain]
            if docs and len(selected) < limit:
                selected.append(docs.pop(0))
            if docs:
                next_domains.append(domain)
        domains = next_domains
    return selected


def select_source_rows(
    root: Path,
    *,
    packet_date_label: str,
    target_source_docs: int,
    rows_per_source: int,
    input_review_paths: list[Path] | None = None,
    allowed_categories: set[str] | None = None,
    excluded_source_candidate_ids: set[str] | None = None,
    excluded_doc_ids: set[str] | None = None,
    excluded_candidate_ids: set[str] | None = None,
    excluded_region_keys: set[tuple[str, int, tuple[int, int, int, int]]] | None = None,
    excluded_semantic_keys: set[tuple[str, int, str, str]] | None = None,
    excluded_region_overlap_threshold: float = 0.0,
    allowed_doc_ids: set[str] | None = None,
    require_release_candidate: bool = True,
    require_paper_ready_source: bool = True,
    allow_item_audited_source_docs: bool = False,
    allow_active_packet_source_docs: bool = False,
    allow_active_source_docs: bool = False,
    require_resolved_version: bool = True,
    allow_domain_category_mismatch: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    allowed_categories = allowed_categories or DEFAULT_CATEGORIES
    excluded_source_candidate_ids = excluded_source_candidate_ids or set()
    excluded_doc_ids = excluded_doc_ids or set()
    excluded_candidate_ids = excluded_candidate_ids or set()
    excluded_region_keys = excluded_region_keys or set()
    excluded_semantic_keys = excluded_semantic_keys or set()
    if not 0.0 <= excluded_region_overlap_threshold <= 1.0:
        raise ValueError("excluded_region_overlap_threshold must be between 0 and 1")
    excluded_region_index: dict[tuple[str, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    for doc_id, page_index, bbox in excluded_region_keys:
        excluded_region_index[(doc_id, page_index)].append(bbox)
    packet_keys, packet_index_path = source_readiness.load_active_packet_row_keys(
        root, packet_date_label
    )
    packet_region_keys = {
        region
        for kind, row in source_readiness.load_active_packet_manifest_rows(root, packet_index_path)
        if kind == "microtext"
        for region in [source_readiness.microtext_region_identity(row)]
        if region
    }
    resolved_keys = source_readiness.active_gold_row_keys(root) | source_readiness.terminal_reviewed_row_keys(root)
    resolved_region_keys = {
        region
        for row in read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
        for region in [source_readiness.microtext_region_identity(row)]
        if region
    }
    for path in sorted((root / "microtext" / "annotations").glob("*_reviewed.jsonl")):
        for row in read_jsonl(path):
            if source_readiness.status_for(row) not in source_readiness.TERMINAL_REVIEW_STATUSES:
                continue
            region = source_readiness.microtext_region_identity(row)
            if region:
                resolved_region_keys.add(region)
    inventory = release_safe_inventory(root)
    strictly_paper_ready_docs = paper_ready_doc_ids(root)
    paper_ready_docs = strictly_paper_ready_docs if require_paper_ready_source else set(inventory)
    active_docs = active_source_doc_ids(root)
    packet_docs = active_packet_source_doc_ids(root, packet_date_label)
    allowed_candidates = release_candidate_ids(root)
    known_candidate_ids = source_candidate_ids(root)
    manifest_candidate_ids = manifest_source_candidate_ids(root)
    duplicate_alias_docs = duplicate_payload_alias_doc_ids(
        root,
        (active_docs, paper_ready_docs, set(inventory)),
    )
    counters: Counter[str] = Counter()
    best_by_identity: dict[
        tuple[Any, ...], tuple[tuple[int, int, int, int, float, str], Path, dict[str, Any]]
    ] = {}

    queue_root = root / "microtext" / "annotations"
    all_review_paths = sorted(queue_root.glob("microtext_review*.jsonl"))
    terminal_veto_ids, terminal_veto_regions, terminal_veto_reasons = terminal_review_vetoes(
        all_review_paths
    )
    if input_review_paths is None:
        queue_paths = all_review_paths
    else:
        queue_paths = []
        for value in input_review_paths:
            path = value if value.is_absolute() else root / value
            if not path.is_file():
                raise FileNotFoundError(f"input review file not found: {path}")
            queue_paths.append(path.resolve())
    for path in queue_paths:
        for row in read_jsonl(path):
            counters["input_rows"] += 1
            if (
                review_queue_inventory.status_for(row) not in review_queue_inventory.OPEN_STATUSES
                or source_readiness.review_exclusion_reason(row)
            ):
                counters["excluded_non_open"] += 1
                continue
            identity = source_readiness.row_identity(row, "microtext")
            doc_id = str(row.get("doc_id") or "").strip()
            if not identity or not doc_id:
                counters["excluded_missing_identity_or_doc"] += 1
                continue
            if identity in terminal_veto_ids:
                counters["excluded_terminal_history_identity"] += 1
                continue
            if allowed_doc_ids is not None and doc_id not in allowed_doc_ids:
                counters["excluded_not_allowed_doc"] += 1
                continue
            region = source_readiness.microtext_region_identity(row)
            if region is not None and region in terminal_veto_regions:
                counters["excluded_terminal_history_region"] += 1
                continue
            if identity in packet_keys or (region is not None and region in packet_region_keys):
                counters["excluded_active_packet"] += 1
                continue
            if identity in resolved_keys or (region is not None and region in resolved_region_keys):
                counters["excluded_resolved"] += 1
                continue
            if identity in excluded_candidate_ids:
                counters["excluded_candidate_id"] += 1
                counters["excluded_candidate"] += 1
                continue
            if region is not None and region in excluded_region_keys:
                counters["excluded_region"] += 1
                counters["excluded_candidate"] += 1
                continue
            if region is not None and region_overlaps_any(
                region, excluded_region_index, excluded_region_overlap_threshold
            ):
                counters["excluded_region_overlap"] += 1
                counters["excluded_candidate"] += 1
                continue
            semantic_identity = microtext_semantic_identity(row)
            if semantic_identity is not None and semantic_identity in excluded_semantic_keys:
                counters["excluded_semantic_duplicate"] += 1
                counters["excluded_candidate"] += 1
                continue
            if doc_id in active_docs and not allow_active_source_docs:
                counters["excluded_active_source_doc"] += 1
                continue
            if doc_id in packet_docs and not allow_active_packet_source_docs:
                counters["excluded_active_packet_source_doc"] += 1
                continue
            if doc_id in excluded_doc_ids:
                counters["excluded_doc"] += 1
                continue
            if doc_id in duplicate_alias_docs:
                counters["excluded_duplicate_payload_alias"] += 1
                continue
            if doc_id not in inventory:
                counters["excluded_not_release_safe"] += 1
                continue
            if doc_id not in paper_ready_docs:
                counters["excluded_not_paper_ready"] += 1
                continue
            candidate_id = str(row.get("source_candidate_id") or "").strip()
            candidate_inferred = False
            manifest_candidate_id = manifest_candidate_ids.get(doc_id, "")
            if not candidate_id:
                candidate_id = manifest_candidate_id
                candidate_inferred = bool(candidate_id)
            if candidate_id in excluded_source_candidate_ids:
                counters["excluded_source_candidate"] += 1
                continue
            if require_release_candidate and candidate_id not in allowed_candidates:
                if allow_active_source_docs and doc_id in active_docs:
                    counters["active_source_registration_exemptions"] += 1
                elif (
                    allow_item_audited_source_docs
                    and doc_id in strictly_paper_ready_docs
                    and candidate_id
                    and candidate_id == manifest_candidate_id
                    and candidate_id in known_candidate_ids
                ):
                    counters["item_audited_source_doc_registration_exemptions"] += 1
                else:
                    counters["excluded_not_release_registered"] += 1
                    continue
            if review_queue_inventory.missing_paths(root, row):
                counters["excluded_missing_evidence"] += 1
                continue
            if require_resolved_version and not resolved_version_id(row):
                counters["excluded_unresolved_version"] += 1
                continue
            row_text = proposed_text(row)
            if not usable_text(row_text):
                counters["excluded_unusable_text"] += 1
                continue
            row_category = category(row)
            if row_category not in allowed_categories:
                counters["excluded_category"] += 1
                continue
            domain = str(inventory[doc_id].get("domain") or "unknown")
            if not domain_category_compatible(domain, row_category):
                if not allow_domain_category_mismatch:
                    counters["excluded_domain_category_mismatch"] += 1
                    continue
                counters["domain_category_mismatch_overrides"] += 1
            candidate_row = dict(row)
            if candidate_inferred:
                candidate_row["source_candidate_id"] = candidate_id
                candidate_row["selection_source_candidate_inferred"] = True
                counters["inferred_source_candidate_from_manifest"] += 1
            score = preference_score(path, candidate_row)
            dedup_key: tuple[Any, ...] = ("region", *region) if region else ("id", identity)
            previous = best_by_identity.get(dedup_key)
            if previous is None or score > previous[0]:
                best_by_identity[dedup_key] = (score, path, candidate_row)

    candidate_items = sorted(best_by_identity.values(), key=lambda item: item[0], reverse=True)
    if excluded_region_overlap_threshold > 0:
        deduplicated_items = []
        seen_region_index: dict[
            tuple[str, int], list[tuple[int, int, int, int]]
        ] = defaultdict(list)
        for item in candidate_items:
            region = source_readiness.microtext_region_identity(item[2])
            if region is not None and region_overlaps_any(
                region, seen_region_index, excluded_region_overlap_threshold
            ):
                counters["excluded_input_region_overlap"] += 1
                continue
            deduplicated_items.append(item)
            if region is not None:
                doc_id, page_index, bbox = region
                seen_region_index[(doc_id, page_index)].append(bbox)
        candidate_items = deduplicated_items

    grouped: dict[
        str, list[tuple[tuple[int, int, int, int, float, str], Path, dict[str, Any]]]
    ] = defaultdict(list)
    for item in candidate_items:
        grouped[str(item[2].get("doc_id") or "").strip()].append(item)

    selected_docs = round_robin_docs(grouped, inventory, target_source_docs)
    selected_rows: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    for doc_id in selected_docs:
        available = grouped[doc_id]
        chosen = choose_source_rows(available, rows_per_source)
        for _score, path, row in chosen:
            selected = dict(row)
            selected["selection_original_review_bucket"] = str(row.get("review_bucket") or "")
            selected["selection_source_queue"] = (
                path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
            )
            selected["review_bucket"] = "v2_0_source_expansion"
            selected_rows.append(selected)
        source_summaries.append(
            {
                "doc_id": doc_id,
                "domain": str(inventory[doc_id].get("domain") or "unknown"),
                "source_candidate_ids": sorted(
                    {str(item[2].get("source_candidate_id") or "") for item in available}
                ),
                "available_rows": len(available),
                "selected_rows": len(chosen),
                "selected_categories": dict(sorted(Counter(category(item[2]) for item in chosen).items())),
                "queue_files": sorted(
                    {
                        item[1].relative_to(root).as_posix()
                        if item[1].is_relative_to(root)
                        else item[1].as_posix()
                        for item in available
                    }
                ),
            }
        )

    selected_rows.sort(key=lambda row: (str(row.get("doc_id") or ""), str(row.get("candidate_id") or "")))
    report = {
        "packet_date_label": packet_date_label,
        "target_source_docs": target_source_docs,
        "rows_per_source": rows_per_source,
        "require_release_candidate": require_release_candidate,
        "require_paper_ready_source": require_paper_ready_source,
        "allow_item_audited_source_docs": allow_item_audited_source_docs,
        "allow_active_packet_source_docs": allow_active_packet_source_docs,
        "allow_active_source_docs": allow_active_source_docs,
        "allow_domain_category_mismatch": allow_domain_category_mismatch,
        "require_resolved_version": require_resolved_version,
        "allowed_categories": sorted(allowed_categories),
        "input_review_files": [
            path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
            for path in queue_paths
        ],
        "excluded_source_candidate_ids": sorted(excluded_source_candidate_ids),
        "excluded_doc_ids": sorted(excluded_doc_ids),
        "excluded_candidate_ids": sorted(excluded_candidate_ids),
        "excluded_region_count": len(excluded_region_keys),
        "excluded_semantic_count": len(excluded_semantic_keys),
        "excluded_region_overlap_threshold": excluded_region_overlap_threshold,
        "terminal_history_identity_count": len(terminal_veto_ids),
        "terminal_history_region_count": len(terminal_veto_regions),
        "terminal_history_reason_counts": dict(sorted(terminal_veto_reasons.items())),
        "allowed_doc_count": len(allowed_doc_ids) if allowed_doc_ids is not None else None,
        "active_source_docs_seen": len(active_docs),
        "active_packet_source_docs_seen": len(packet_docs),
        "duplicate_payload_alias_docs_seen": len(duplicate_alias_docs),
        "available_inactive_source_docs": len(grouped),
        "available_source_docs": len(grouped),
        "selected_source_docs": len(selected_docs),
        "selected_rows": len(selected_rows),
        "selected_domains": dict(sorted(Counter(row["domain"] for row in source_summaries).items())),
        "selected_categories": dict(sorted(Counter(category(row) for row in selected_rows).items())),
        "counters": dict(sorted(counters.items())),
        "sources": source_summaries,
    }
    return selected_rows, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Microtext Fresh-Source Review Queue",
        "",
        f"- Active packet index: `{report['packet_date_label']}`",
        f"- Available paper-ready source docs: `{report.get('available_source_docs', report['available_inactive_source_docs'])}`",
        f"- Item-audited source-document override: `{str(report.get('allow_item_audited_source_docs', False)).lower()}`",
        f"- Active-packet source docs allowed: `{str(report.get('allow_active_packet_source_docs', False)).lower()}`",
        f"- Active-gold source docs allowed: `{str(report.get('allow_active_source_docs', False)).lower()}`",
        f"- Domain/category mismatch inspection override: `{str(report.get('allow_domain_category_mismatch', False)).lower()}`",
        f"- Split restriction: `{report.get('restricted_split') or 'none'}`",
        f"- Capacity reports excluded: `{len(report.get('capacity_exclusion_reports', []))}`",
        f"- Selected source docs: `{report['selected_source_docs']}`",
        f"- Selected rows: `{report['selected_rows']}`",
        f"- Rows per source cap: `{report['rows_per_source']}`",
        f"- Domains: `{report['selected_domains']}`",
        f"- Categories: `{report['selected_categories']}`",
        "",
        "| Source document | Domain | Available | Selected | Categories | Source candidate |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in report["sources"]:
        lines.append(
            f"| `{row['doc_id']}` | {row['domain']} | {row['available_rows']} | "
            f"{row['selected_rows']} | `{row['selected_categories']}` | "
            f"`{', '.join(row['source_candidate_ids'])}` |"
        )
    lines.extend(
        [
            "",
            "This is a review-only queue. No selected row is gold until human acceptance and strict promotion gates pass.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--packet-date-label", required=True)
    parser.add_argument("--target-source-docs", type=int, default=50)
    parser.add_argument("--rows-per-source", type=int, default=3)
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument(
        "--input-review-file",
        action="append",
        type=Path,
        default=[],
        help="Restrict selection to these review JSONL inputs; repeatable.",
    )
    parser.add_argument("--exclude-source-candidate", action="append", default=[])
    parser.add_argument(
        "--include-doc-id",
        action="append",
        default=[],
        help=(
            "Restrict selection to these exact document IDs; repeatable. When a split plan is "
            "also supplied, the whitelist is intersected with split-eligible documents."
        ),
    )
    parser.add_argument("--exclude-doc-id", action="append", default=[])
    parser.add_argument("--exclude-candidate-id", action="append", default=[])
    parser.add_argument(
        "--exclude-review-file",
        action="append",
        type=Path,
        default=[],
        help="Exclude candidate IDs present in another JSONL review queue or pack manifest.",
    )
    parser.add_argument(
        "--exclude-capacity-report",
        action="append",
        type=Path,
        default=[],
        help=(
            "Exclude every microtext identity and physical region already counted by a clean "
            "staged-capacity report. Missing cohort files fail closed."
        ),
    )
    parser.add_argument(
        "--allow-unresolved-version",
        action="store_true",
        help="Allow placeholder version IDs. Disabled by default for release-safe queues.",
    )
    parser.add_argument(
        "--exclude-region-overlap-threshold",
        type=float,
        default=0.0,
        help=(
            "Also exclude boxes whose intersection covers this fraction of the smaller box; "
            "zero disables spatial suppression."
        ),
    )
    parser.add_argument(
        "--split-plan",
        type=Path,
        help="Validated staged split plan used to restrict source-document units.",
    )
    parser.add_argument(
        "--split",
        choices=("train", "dev", "test"),
        help="Select only microtext document units reserved to this split.",
    )
    parser.add_argument(
        "--include-unreserved",
        action="store_true",
        help=(
            "With --split-plan/--split, also admit source documents absent from the plan while "
            "still excluding documents reserved to the other splits."
        ),
    )
    parser.add_argument("--allow-unregistered-source-candidates", action="store_true")
    parser.add_argument(
        "--allow-item-audited-source-docs",
        action="store_true",
        help=(
            "Allow an exact paper-ready source document to override only its parent candidate's "
            "release-registration hold. The document must retain hash-matched manifest provenance "
            "and a matching known source-candidate lineage."
        ),
    )
    parser.add_argument(
        "--allow-unregistered-source-docs",
        action="store_true",
        help="Allow rows whose source document lacks strict manifest/path/hash provenance.",
    )
    parser.add_argument(
        "--allow-active-packet-source-docs",
        action="store_true",
        help=(
            "Allow fresh identities from source documents already represented in the active human packet. "
            "Existing packet identities remain excluded. Intended for later scale waves."
        ),
    )
    parser.add_argument(
        "--allow-active-source-docs",
        action="store_true",
        help=(
            "Allow fresh identities from source documents already represented in active gold. "
            "Existing gold identities remain excluded. Intended for category and row-count scale waves."
        ),
    )
    parser.add_argument(
        "--allow-domain-category-mismatch",
        action="store_true",
        help=(
            "Admit category/domain mismatches for explicit visual correction. This inspection-only "
            "override requires at least one --include-doc-id and does not make rows release-ready."
        ),
    )
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)

    if bool(args.split_plan) != bool(args.split):
        parser.error("--split-plan and --split must be supplied together")
    if args.include_unreserved and not args.split_plan:
        parser.error("--include-unreserved requires --split-plan and --split")
    if args.allow_domain_category_mismatch and not args.include_doc_id:
        parser.error("--allow-domain-category-mismatch requires --include-doc-id")

    root = Path(args.root).resolve()
    capacity_paths = capacity_report_review_paths(root, args.exclude_capacity_report)
    review_file_candidate_ids, excluded_region_keys = review_exclusions_from_files(
        root, [*args.exclude_review_file, *capacity_paths]
    )
    excluded_semantic_keys = review_semantic_exclusions_from_files(
        root, [*args.exclude_review_file, *capacity_paths]
    )
    excluded_candidate_ids = set(args.exclude_candidate_id) | review_file_candidate_ids
    split_mapping = (
        microtext_split_reservations(root, args.split_plan)
        if args.split_plan and args.split
        else {}
    )
    allowed_doc_ids = None
    plan_excluded_doc_ids: set[str] = set()
    if args.split_plan and args.split:
        if args.include_unreserved:
            plan_excluded_doc_ids = {
                doc_id for doc_id, reserved_split in split_mapping.items() if reserved_split != args.split
            }
        else:
            allowed_doc_ids = {
                doc_id for doc_id, reserved_split in split_mapping.items() if reserved_split == args.split
            }
    include_doc_ids = set(args.include_doc_id)
    if include_doc_ids:
        allowed_doc_ids = (
            include_doc_ids
            if allowed_doc_ids is None
            else allowed_doc_ids & include_doc_ids
        )
    rows, report = select_source_rows(
        root,
        packet_date_label=args.packet_date_label,
        target_source_docs=max(0, args.target_source_docs),
        rows_per_source=max(1, args.rows_per_source),
        input_review_paths=args.input_review_file or None,
        allowed_categories=set(args.category) if args.category else DEFAULT_CATEGORIES,
        excluded_source_candidate_ids=set(args.exclude_source_candidate),
        excluded_doc_ids=set(args.exclude_doc_id) | plan_excluded_doc_ids,
        excluded_candidate_ids=excluded_candidate_ids,
        excluded_region_keys=excluded_region_keys,
        excluded_semantic_keys=excluded_semantic_keys,
        excluded_region_overlap_threshold=args.exclude_region_overlap_threshold,
        allowed_doc_ids=allowed_doc_ids,
        require_release_candidate=not args.allow_unregistered_source_candidates,
        require_paper_ready_source=not args.allow_unregistered_source_docs,
        allow_item_audited_source_docs=args.allow_item_audited_source_docs,
        allow_active_packet_source_docs=args.allow_active_packet_source_docs,
        allow_active_source_docs=args.allow_active_source_docs,
        require_resolved_version=not args.allow_unresolved_version,
        allow_domain_category_mismatch=args.allow_domain_category_mismatch,
    )
    if args.split_plan and args.split:
        rows = apply_authoritative_split_reservations(
            root, rows, args.split_plan, args.split
        )
    report["restricted_split"] = args.split or ""
    report["included_doc_ids"] = sorted(include_doc_ids)
    report["include_unreserved"] = args.include_unreserved
    report["plan_excluded_doc_count"] = len(plan_excluded_doc_ids)
    report["split_plan"] = (
        args.split_plan.as_posix() if args.split_plan else ""
    )
    report["authoritative_split_rows"] = len(rows) if args.split_plan else 0
    report["capacity_exclusion_reports"] = [path.as_posix() for path in args.exclude_capacity_report]
    report["capacity_exclusion_files"] = [
        path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        for path in capacity_paths
    ]
    output_jsonl = Path(args.output_jsonl)
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    write_jsonl(output_jsonl if output_jsonl.is_absolute() else root / output_jsonl, rows)
    write_json(output_json if output_json.is_absolute() else root / output_json, report)
    markdown_path = output_md if output_md.is_absolute() else root / output_md
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {"selected_source_docs": report["selected_source_docs"], "selected_rows": len(rows)},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
