#!/usr/bin/env python3
"""Build a provenance-checked MicroText category-closure review overlay.

The overlay is planning/review capacity only. It never edits active Gold and
marks every selected row unsafe to merge until the normal review gates pass.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import warnings
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from source_rights import is_release_safe_status, rights_blocker


MICROTEXT_CATEGORY_MINIMUMS = {
    "pin_label": 1000,
    "component_value": 300,
    "dimension_value": 900,
    "equipment_tag": 300,
    "instrument_tag": 300,
    "pipe_line_tag": 150,
    "process_label": 150,
    "process_value": 150,
    "room_label": 150,
    "tolerance_value": 150,
}
VALID_SPLITS = {"train", "dev", "test"}
SPLIT_PRIORITY = {"test": 0, "dev": 1, "train": 2}
SELECTION_MODES = {"category_closure", "all_known_nonpin"}
MICROTEXT_PIN_SHARE_LIMIT = 0.45
PAGE_PATTERN = re.compile(r"page_(\d+)", re.IGNORECASE)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    # Historical review exports include a few Windows-encoded note cells. The
    # identity columns are ASCII, so replacement decoding preserves the fields
    # needed for conservative packet exclusion without dropping the packet.
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def text_value(row: dict[str, Any]) -> str:
    for key in ("corrected_text", "target_text", "proposed_text", "raw_text", "answer"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def candidate_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(
        row.get("candidate_id")
        or row.get("record_id")
        or metadata.get("candidate_id")
        or metadata.get("item_id")
        or row.get("id")
        or ""
    ).strip()


def row_category(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(row.get("category") or metadata.get("category") or "unknown").strip() or "unknown"


def row_doc_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(row.get("doc_id") or metadata.get("doc_id") or metadata.get("source_doc_id") or "").strip()


def row_bbox(row: dict[str, Any]) -> tuple[int, int, int, int] | None:
    bbox = row.get("bbox")
    if bbox is None:
        evidence = row.get("evidence")
        if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
            bbox = evidence[0].get("bbox")
    if isinstance(bbox, str):
        try:
            bbox = json.loads(bbox)
        except json.JSONDecodeError:
            return None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        values = tuple(int(round(float(value))) for value in bbox)
    except (TypeError, ValueError):
        return None
    return values


def row_image_path(row: dict[str, Any]) -> str:
    value = str(row.get("image_path") or "").strip()
    if value:
        return value
    images = row.get("images")
    if isinstance(images, list) and images:
        return str(images[0] or "").strip()
    return ""


def row_page_index(row: dict[str, Any]) -> int | None:
    value = row.get("page_index")
    if value not in (None, ""):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    match = PAGE_PATTERN.search(Path(row_image_path(row)).stem)
    return int(match.group(1)) if match else None


def geometry_key(row: dict[str, Any]) -> tuple[str, int, tuple[int, int, int, int]] | None:
    doc_id = row_doc_id(row)
    page_index = row_page_index(row)
    bbox = row_bbox(row)
    if not doc_id or page_index is None or bbox is None:
        return None
    return doc_id, page_index, bbox


def is_microtext(row: dict[str, Any]) -> bool:
    task = str(row.get("task") or "").strip().lower()
    if task:
        return task == "microtext"
    return bool(row.get("candidate_id") or row.get("category"))


def row_split(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(row.get("reserved_split") or row.get("split") or metadata.get("split") or "").strip()


def is_provenance_replacement(row: dict[str, Any]) -> bool:
    if "provenance_replacement" in row:
        return row.get("provenance_replacement") is True
    if row.get("provenance_replacement_candidate") is True:
        return True
    state = str(row.get("promotion_state") or "").strip().lower()
    reason = str(row.get("replacement_completion_reason") or "").strip().lower()
    return state == "unreviewed_provenance_replacement_candidate" or reason == (
        "retire_rights_blocked_active_gold_row"
    )


def boolish(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def image_dimensions(path: Path) -> tuple[int, int]:
    """Read trusted local render dimensions without decoding the full raster."""
    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                return image.size
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def index_manifest(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        doc_id = str(row.get("doc_id") or "").strip()
        if not doc_id or str(row.get("type") or "doc") != "doc":
            continue
        current = result.get(doc_id)
        if current is None or (not current.get("sha256") and row.get("sha256")):
            result[doc_id] = row
    return result


def load_packet_exclusions(
    root: Path, index_path: Path
) -> tuple[set[str], set[tuple[str, int, tuple[int, int, int, int]]], dict[str, int]]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    packets = payload.get("packets")
    if not isinstance(packets, list):
        packets = payload.get("packs", [])
    ids: set[str] = set()
    geometries: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    ready_packets = 0
    scanned_rows = 0
    for packet in packets:
        if not packet.get("ready_to_send") or int(packet.get("mergeable_rows") or 0) > 0:
            continue
        folder_value = str(packet.get("folder_path") or "").strip()
        if not folder_value:
            continue
        packet_root = resolve_path(root, folder_value)
        if not packet_root.exists():
            continue
        ready_packets += 1
        row_files = list(sorted(packet_root.rglob("manifest.jsonl")))
        row_files.extend(sorted(packet_root.rglob("*.csv")))
        for row_path in row_files:
            rows = read_jsonl(row_path) if row_path.suffix.lower() == ".jsonl" else read_csv(row_path)
            for row in rows:
                scanned_rows += 1
                row_id = candidate_id(row)
                if row_id:
                    ids.add(row_id)
                geometry = geometry_key(row)
                if geometry is not None:
                    geometries.add(geometry)
    return ids, geometries, {
        "ready_packets_scanned": ready_packets,
        "packet_rows_scanned": scanned_rows,
        "packet_candidate_ids": len(ids),
        "packet_physical_regions": len(geometries),
    }


def load_microtext_split_reservations(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("valid") is not True:
        raise ValueError(f"split plan is not valid: {path}")
    reservations: dict[str, str] = {}
    for row in payload.get("reservations", []):
        if str(row.get("task") or "").strip() != "microtext":
            continue
        doc_id = str(row.get("unit_id") or "").strip()
        split = str(row.get("split") or "").strip().lower()
        if not doc_id or split not in VALID_SPLITS:
            raise ValueError(f"invalid MicroText split reservation in {path}: {row!r}")
        previous = reservations.get(doc_id)
        if previous and previous != split:
            raise ValueError(f"conflicting MicroText split reservation for {doc_id}")
        reservations[doc_id] = split
    return reservations


def count_categories(rows: Iterable[dict[str, Any]]) -> Counter[str]:
    return Counter(row_category(row) for row in rows if is_microtext(row))


def category_projection(
    active_rows: list[dict[str, Any]],
    assignment_rows: list[dict[str, Any]],
    eligible_rows: list[dict[str, Any]],
) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str]]:
    active_counts = count_categories(active_rows)
    eligible_by_id = {
        candidate_id(row): row
        for row in eligible_rows
        if is_microtext(row) and candidate_id(row)
    }
    eligible_counts = count_categories(eligible_by_id.values())
    remaining_assignment: dict[str, dict[str, Any]] = {}
    for row in assignment_rows:
        row_id = candidate_id(row)
        if not is_microtext(row) or not row_id or row_id in eligible_by_id:
            continue
        remaining_assignment.setdefault(row_id, row)
    assignment_counts = count_categories(remaining_assignment.values())
    projected = active_counts + eligible_counts + assignment_counts
    return active_counts, eligible_counts, assignment_counts, projected


def diverse_select(rows: list[dict[str, Any]], limit: int, *, soft_doc_cap: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    ordered = sorted(
        rows,
        key=lambda row: (
            SPLIT_PRIORITY.get(str(row.get("reserved_split") or ""), 9),
            row_doc_id(row),
            row_page_index(row) if row_page_index(row) is not None else 10**9,
            row_bbox(row) or (10**9, 10**9, 10**9, 10**9),
            candidate_id(row),
        ),
    )
    by_doc: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in ordered:
        by_doc[row_doc_id(row)].append(row)
    selected: list[dict[str, Any]] = []
    doc_counts: Counter[str] = Counter()
    doc_order = sorted(
        by_doc,
        key=lambda doc_id: (
            SPLIT_PRIORITY.get(str(by_doc[doc_id][0].get("reserved_split") or ""), 9),
            doc_id,
        ),
    )
    progress = True
    while len(selected) < limit and progress:
        progress = False
        for doc_id in doc_order:
            if len(selected) >= limit:
                break
            if not by_doc[doc_id] or doc_counts[doc_id] >= soft_doc_cap:
                continue
            selected.append(by_doc[doc_id].popleft())
            doc_counts[doc_id] += 1
            progress = True
    if len(selected) < limit:
        remainder = sorted(
            (row for queue in by_doc.values() for row in queue),
            key=lambda row: (
                SPLIT_PRIORITY.get(str(row.get("reserved_split") or ""), 9),
                doc_counts[row_doc_id(row)],
                row_doc_id(row),
                candidate_id(row),
            ),
        )
        selected.extend(remainder[: limit - len(selected)])
    return selected


def _hold(row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    held = dict(row)
    held["closure_overlay_disposition"] = "held"
    held["closure_overlay_hold_reasons"] = sorted(set(reasons))
    held["safe_to_merge_gold"] = False
    return held


def build_overlay(
    root: Path,
    active_path: Path,
    assignment_path: Path,
    eligible_path: Path,
    future_path: Path,
    inventory_path: Path,
    manifest_path: Path,
    *,
    date_label: str,
    min_acceptance_rate: float = 0.65,
    soft_doc_cap: int = 12,
    category_minimums: dict[str, int] | None = None,
    supplemental_future_paths: list[Path] | None = None,
    packet_index_path: Path | None = None,
    split_plan_path: Path | None = None,
    verify_row_split_plans: bool = False,
    required_splits: set[str] | None = None,
    exclude_provenance_replacements: bool = False,
    selection_mode: str = "category_closure",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not 0 < min_acceptance_rate <= 1:
        raise ValueError("min_acceptance_rate must be in (0, 1]")
    if soft_doc_cap <= 0:
        raise ValueError("soft_doc_cap must be positive")
    if selection_mode not in SELECTION_MODES:
        raise ValueError(f"selection_mode must be one of {sorted(SELECTION_MODES)}")
    required_splits = set(required_splits or [])
    invalid_required_splits = required_splits - VALID_SPLITS
    if invalid_required_splits:
        raise ValueError(f"invalid required splits: {sorted(invalid_required_splits)}")
    root = root.resolve()
    paths = {
        "active": resolve_path(root, active_path).resolve(),
        "assignment": resolve_path(root, assignment_path).resolve(),
        "machine_eligible": resolve_path(root, eligible_path).resolve(),
        "future_capacity": resolve_path(root, future_path).resolve(),
        "inventory": resolve_path(root, inventory_path).resolve(),
        "manifest": resolve_path(root, manifest_path).resolve(),
    }
    for index, path in enumerate(supplemental_future_paths or [], start=1):
        paths[f"supplemental_future_{index}"] = resolve_path(root, path).resolve()
    if packet_index_path is not None:
        paths["packet_index"] = resolve_path(root, packet_index_path).resolve()
    if split_plan_path is not None:
        paths["split_plan"] = resolve_path(root, split_plan_path).resolve()
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"{name} input not found: {path}")

    active_rows = read_jsonl(paths["active"])
    assignment_rows = read_jsonl(paths["assignment"])
    eligible_rows = read_jsonl(paths["machine_eligible"])
    future_rows = read_jsonl(paths["future_capacity"])
    for name, path in paths.items():
        if name.startswith("supplemental_future_"):
            future_rows.extend(read_jsonl(path))
    inventory_rows = read_csv(paths["inventory"])
    manifest_rows = read_jsonl(paths["manifest"])
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in inventory_rows
        if str(row.get("doc_id") or "").strip()
    }
    manifest = index_manifest(manifest_rows)
    minimums = dict(category_minimums or MICROTEXT_CATEGORY_MINIMUMS)
    packet_ids: set[str] = set()
    packet_geometry: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    packet_summary = {
        "ready_packets_scanned": 0,
        "packet_rows_scanned": 0,
        "packet_candidate_ids": 0,
        "packet_physical_regions": 0,
    }
    if "packet_index" in paths:
        packet_ids, packet_geometry, packet_summary = load_packet_exclusions(
            root, paths["packet_index"]
        )
    split_reservations = (
        load_microtext_split_reservations(paths["split_plan"])
        if "split_plan" in paths
        else {}
    )

    active_counts, eligible_counts, assignment_counts, projected = category_projection(
        active_rows, assignment_rows, eligible_rows
    )
    shortfalls = {
        category: max(0, minimum - projected.get(category, 0))
        for category, minimum in minimums.items()
    }
    if selection_mode == "all_known_nonpin":
        target_categories = set(minimums) - {"pin_label"}
    else:
        target_categories = {category for category, value in shortfalls.items() if value > 0}
    requested = {
        category: math.ceil(shortfalls[category] / min_acceptance_rate)
        for category in target_categories
    }

    excluded_ids = {
        candidate_id(row)
        for row in active_rows + assignment_rows + eligible_rows
        if candidate_id(row)
    }
    excluded_geometry = {
        key
        for row in active_rows + assignment_rows + eligible_rows
        if (key := geometry_key(row)) is not None
    }
    seen_ids: set[str] = set()
    seen_geometry: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    holds: list[dict[str, Any]] = []
    exclusion_counts: Counter[str] = Counter()
    prefilter_counts: Counter[str] = Counter()
    ignored_categories: Counter[str] = Counter()
    source_cache: dict[str, tuple[bool, str, str, str]] = {}
    image_size_cache: dict[Path, tuple[int, int]] = {}
    row_split_plan_cache: dict[Path, dict[str, str]] = {}
    row_split_plan_hashes: dict[Path, str] = {}

    def source_audit(doc_id: str) -> tuple[bool, str, str, str]:
        if doc_id in source_cache:
            return source_cache[doc_id]
        manifest_row = manifest.get(doc_id)
        inventory_row = inventory.get(doc_id)
        if manifest_row is None:
            result = (False, "manifest_doc_missing", "", "")
        elif inventory_row is None:
            result = (False, "inventory_doc_missing", "", "")
        elif boolish(inventory_row.get("duplicate_payload_alias")):
            result = (False, "duplicate_payload_alias", "", "")
        else:
            manifest_status = str(manifest_row.get("public_status") or "").strip()
            inventory_status = str(inventory_row.get("public_status") or "").strip()
            statuses = [status for status in (manifest_status, inventory_status) if status]
            blocker = next((rights_blocker(status) for status in statuses if rights_blocker(status)), "")
            if not statuses:
                result = (False, "source_public_status_missing", "", "")
            elif blocker:
                result = (False, f"source_rights:{blocker}", "", "")
            else:
                relative_source = str(manifest_row.get("path") or inventory_row.get("source_path") or inventory_row.get("path") or "").strip()
                expected_sha = str(manifest_row.get("sha256") or "").strip().lower()
                source_path = resolve_path(root, relative_source).resolve() if relative_source else root
                if not relative_source:
                    result = (False, "source_path_missing", "", "")
                elif not source_path.is_file():
                    result = (False, "source_payload_missing", str(source_path), expected_sha)
                elif not expected_sha:
                    result = (False, "manifest_sha256_missing", str(source_path), "")
                else:
                    actual_sha = file_sha256(source_path)
                    if actual_sha.lower() != expected_sha:
                        result = (False, "source_payload_sha256_mismatch", str(source_path), expected_sha)
                    else:
                        result = (True, "", str(source_path), expected_sha)
        source_cache[doc_id] = result
        return result

    for row in future_rows:
        category = row_category(row)
        if category not in target_categories:
            ignored_categories[category] += 1
            continue
        split = row_split(row)
        if required_splits and split not in required_splits:
            prefilter_counts["required_split_mismatch"] += 1
            continue
        reasons: list[str] = []
        row_id = candidate_id(row)
        doc_id = row_doc_id(row)
        bbox = row_bbox(row)
        page_index = row_page_index(row)
        geometry = geometry_key(row)
        image_value = row_image_path(row)
        if not row_id:
            reasons.append("candidate_id_missing")
        elif row_id in excluded_ids:
            reasons.append("candidate_id_already_active_assigned_or_eligible")
        elif row_id in packet_ids:
            reasons.append("candidate_id_in_active_review_packet")
        elif row_id in seen_ids:
            reasons.append("duplicate_candidate_id_in_future_capacity")
        if geometry is None:
            reasons.append("physical_region_key_missing")
        elif geometry in excluded_geometry:
            reasons.append("physical_region_already_active_assigned_or_eligible")
        elif geometry in packet_geometry:
            reasons.append("physical_region_in_active_review_packet")
        elif geometry in seen_geometry:
            reasons.append("duplicate_physical_region_in_future_capacity")
        if exclude_provenance_replacements and is_provenance_replacement(row):
            reasons.append("provenance_replacement_not_expansion_capacity")
        if split not in VALID_SPLITS:
            reasons.append("reserved_split_invalid")
        if split_reservations:
            expected_split = split_reservations.get(doc_id)
            if expected_split is None:
                reasons.append("source_doc_missing_from_split_plan")
            elif split != expected_split:
                reasons.append(f"split_plan_mismatch:{expected_split}")
        elif verify_row_split_plans:
            row_plan_value = str(row.get("split_reservation_plan") or "").strip()
            if not row_plan_value:
                reasons.append("row_split_plan_missing")
            else:
                row_plan_path = resolve_path(root, row_plan_value).resolve()
                if not row_plan_path.is_file():
                    reasons.append("row_split_plan_file_missing")
                else:
                    try:
                        if row_plan_path not in row_split_plan_cache:
                            row_split_plan_cache[row_plan_path] = (
                                load_microtext_split_reservations(row_plan_path)
                            )
                            row_split_plan_hashes[row_plan_path] = file_sha256(
                                row_plan_path
                            )
                        expected_split = row_split_plan_cache[row_plan_path].get(doc_id)
                    except (OSError, ValueError):
                        reasons.append("row_split_plan_invalid")
                    else:
                        if expected_split is None:
                            reasons.append("source_doc_missing_from_row_split_plan")
                        elif split != expected_split:
                            reasons.append(
                                f"row_split_plan_mismatch:{expected_split}"
                            )
        if not text_value(row):
            reasons.append("candidate_text_missing")
        if str(row.get("machine_qa_status") or "").strip() != "selected_for_human_review":
            reasons.append("machine_qa_not_selected_for_human_review")
        if not doc_id:
            reasons.append("doc_id_missing")
        source_ok, source_issue, source_path, source_sha = source_audit(doc_id) if doc_id else (False, "doc_id_missing", "", "")
        if not source_ok and source_issue not in reasons:
            reasons.append(source_issue)
        row_status = str(row.get("source_public_status") or row.get("public_status") or "").strip()
        if row_status and not is_release_safe_status(row_status):
            reasons.append(f"row_rights:{rights_blocker(row_status)}")
        row_sha = str(row.get("source_payload_sha256") or "").strip().lower()
        if row_sha and source_sha and row_sha != source_sha:
            reasons.append("row_source_payload_sha256_mismatch")
        if not image_value:
            reasons.append("image_path_missing")
        else:
            image_path = resolve_path(root, image_value).resolve()
            if not image_path.is_file():
                reasons.append("image_missing")
            elif bbox is None:
                reasons.append("bbox_invalid")
            else:
                try:
                    if image_path not in image_size_cache:
                        image_size_cache[image_path] = image_dimensions(image_path)
                    width, height = image_size_cache[image_path]
                    x0, y0, x1, y1 = bbox
                    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                        reasons.append("bbox_out_of_bounds")
                except OSError:
                    reasons.append("image_unreadable")
        if page_index is None:
            reasons.append("page_index_missing")

        if reasons:
            for reason in sorted(set(reasons)):
                exclusion_counts[reason] += 1
            holds.append(_hold(row, reasons))
            continue
        seen_ids.add(row_id)
        if geometry is not None:
            seen_geometry.add(geometry)
        prepared = dict(row)
        prepared["source_payload_sha256"] = source_sha
        if not row_sha:
            prepared["source_payload_sha256_backfill_source"] = "manifest.jsonl"
        prepared["machine_provenance_status"] = "verified_local_manifest_sha256"
        prepared["machine_provenance_source_path"] = source_path
        candidates[category].append(prepared)

    selected: list[dict[str, Any]] = []
    selected_by_category: Counter[str] = Counter()
    for category in sorted(target_categories):
        selection_limit = (
            len(candidates.get(category, []))
            if selection_mode == "all_known_nonpin"
            else min(requested[category], len(candidates.get(category, [])))
        )
        chosen = diverse_select(
            candidates.get(category, []),
            selection_limit,
            soft_doc_cap=soft_doc_cap,
        )
        for row in chosen:
            prepared = dict(row)
            prepared.update(
                {
                    "closure_overlay_only": True,
                    "closure_overlay_date_label": date_label,
                    "closure_priority_category": category,
                    "projected_shortfall_before_overlay": shortfalls[category],
                    "priority_activation_status": "review_capacity_not_yet_packeted",
                    "machine_review_prefill_status": "proposal_category_provenance_ready",
                    "promotion_state": "unreviewed_candidate",
                    "review_status": "needs_review",
                    "safe_to_merge_gold": False,
                }
            )
            selected.append(prepared)
            selected_by_category[category] += 1
    selected.sort(
        key=lambda row: (
            row_category(row),
            SPLIT_PRIORITY.get(str(row.get("reserved_split") or ""), 9),
            row_doc_id(row),
            row_page_index(row) or 0,
            candidate_id(row),
        )
    )

    full_projection = Counter(projected)
    expected_projection = Counter(projected)
    category_details: dict[str, Any] = {}
    source_buffer_priorities: list[dict[str, Any]] = []
    for category in sorted(minimums):
        selected_count = selected_by_category.get(category, 0)
        expected_accepted = math.floor(selected_count * min_acceptance_rate)
        full_projection[category] += selected_count
        expected_projection[category] += expected_accepted
        full_shortfall = max(0, minimums[category] - full_projection[category])
        expected_shortfall = max(0, minimums[category] - expected_projection[category])
        available = len(candidates.get(category, []))
        buffer_gap = max(0, requested.get(category, 0) - available)
        category_details[category] = {
            "minimum": minimums[category],
            "active": active_counts.get(category, 0),
            "machine_eligible_pending_calibration": eligible_counts.get(category, 0),
            "remaining_current_human_assignment": assignment_counts.get(category, 0),
            "projected_before_overlay": projected.get(category, 0),
            "shortfall_before_overlay": shortfalls.get(category, 0),
            "review_rows_requested_at_acceptance_floor": requested.get(category, 0),
            "usable_future_candidates": available,
            "selected_overlay_rows": selected_count,
            "expected_accepted_at_floor": expected_accepted,
            "projected_after_full_acceptance": full_projection[category],
            "shortfall_after_full_acceptance": full_shortfall,
            "projected_after_acceptance_floor": expected_projection[category],
            "shortfall_after_acceptance_floor": expected_shortfall,
            "additional_candidates_needed_for_acceptance_buffer": buffer_gap,
            "minimum_actual_acceptance_rate_to_close": (
                round(shortfalls.get(category, 0) / selected_count, 6)
                if selected_count and shortfalls.get(category, 0)
                else 0.0
            ),
        }
        if buffer_gap:
            source_buffer_priorities.append(
                {
                    "category": category,
                    "additional_candidates_needed": buffer_gap,
                    "reason": "canonical future capacity cannot absorb the configured acceptance-rate buffer",
                }
            )

    active_microtext_rows = [row for row in active_rows if is_microtext(row)]
    active_pin_rows = sum(row_category(row) == "pin_label" for row in active_microtext_rows)
    projected_microtext_total = sum(projected.values())
    projected_pin_rows = int(projected.get("pin_label", 0))
    selected_pin_rows = selected_by_category.get("pin_label", 0)
    modeled_selected_rows = sum(
        math.floor(count * min_acceptance_rate) for count in selected_by_category.values()
    )
    modeled_pin_rows = math.floor(selected_pin_rows * min_acceptance_rate)
    active_microtext_total = len(active_microtext_rows)
    minimum_total_rows_for_pin_limit = (
        math.ceil(projected_pin_rows / MICROTEXT_PIN_SHARE_LIMIT)
        if projected_pin_rows
        else 0
    )
    additional_full_acceptance_nonpin = max(
        0, minimum_total_rows_for_pin_limit - (projected_microtext_total + len(selected))
    )
    additional_modeled_accepted_nonpin = max(
        0,
        minimum_total_rows_for_pin_limit
        - (projected_microtext_total + modeled_selected_rows),
    )
    balance_projection = {
        "pin_share_limit": MICROTEXT_PIN_SHARE_LIMIT,
        "active_microtext_rows": active_microtext_total,
        "active_pin_rows": active_pin_rows,
        "active_pin_share": round(active_pin_rows / active_microtext_total, 6)
        if active_microtext_total
        else 0.0,
        "projected_before_overlay_microtext_rows": projected_microtext_total,
        "projected_before_overlay_pin_rows": projected_pin_rows,
        "projected_before_overlay_pin_share": round(
            projected_pin_rows / projected_microtext_total, 6
        )
        if projected_microtext_total
        else 0.0,
        "selected_rows": len(selected),
        "selected_pin_rows": selected_pin_rows,
        "selected_known_nonpin_rows": len(selected) - selected_pin_rows,
        "pin_share_after_full_acceptance": round(
            (projected_pin_rows + selected_pin_rows)
            / (projected_microtext_total + len(selected)),
            6,
        )
        if projected_microtext_total + len(selected)
        else 0.0,
        "modeled_accepted_rows": modeled_selected_rows,
        "pin_share_after_modeled_acceptance": round(
            (projected_pin_rows + modeled_pin_rows)
            / (projected_microtext_total + modeled_selected_rows),
            6,
        )
        if projected_microtext_total + modeled_selected_rows
        else 0.0,
        "improves_pin_share_at_full_acceptance": (
            selected_pin_rows * projected_microtext_total
            <= projected_pin_rows * len(selected)
        ),
        "additional_accepted_nonpin_rows_to_limit_after_full_acceptance": (
            additional_full_acceptance_nonpin
        ),
        "additional_accepted_nonpin_rows_to_limit_after_modeled_acceptance": (
            additional_modeled_accepted_nonpin
        ),
        "additional_review_rows_at_modeled_rate_to_limit": math.ceil(
            additional_modeled_accepted_nonpin / min_acceptance_rate
        ),
    }

    report = {
        "date_label": date_label,
        "target": "Gold v2.0 Global",
        "policy": {
            "review_overlay_only": True,
            "active_gold_modified": False,
            "unreviewed_rows_mergeable": False,
            "minimum_acceptance_rate": min_acceptance_rate,
            "soft_per_document_selection_cap": soft_doc_cap,
            "release_safe_sources_only": True,
            "manifest_sha256_verified_against_local_payload": True,
            "image_and_bbox_validation_required": True,
            "split_reservation_required": True,
            "candidate_and_physical_region_dedup_required": True,
            "selection_mode": selection_mode,
            "required_splits": sorted(required_splits),
            "active_packet_exclusion_required": packet_index_path is not None,
            "authoritative_split_plan_required": split_plan_path is not None,
            "row_split_plan_verification_required": verify_row_split_plans,
            "provenance_replacements_excluded": exclude_provenance_replacements,
        },
        "inputs": {
            name: {
                "path": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
                "sha256": file_sha256(path),
            }
            for name, path in paths.items()
        },
        "row_split_plans": {
            "count": len(row_split_plan_hashes),
            "files": [
                {
                    "path": (
                        str(path.relative_to(root))
                        if path.is_relative_to(root)
                        else str(path)
                    ),
                    "sha256": row_split_plan_hashes[path],
                }
                for path in sorted(row_split_plan_hashes, key=str)
            ],
        },
        "counts": {
            "active_rows": len(active_rows),
            "active_microtext_rows": sum(active_counts.values()),
            "assignment_rows": len(assignment_rows),
            "machine_eligible_rows": len(eligible_rows),
            "future_capacity_rows": len(future_rows),
            "target_future_rows_examined": sum(len(rows) for rows in candidates.values()) + len(holds),
            "usable_target_candidates": sum(len(rows) for rows in candidates.values()),
            "selected_overlay_rows": len(selected),
            "held_target_rows": len(holds),
            "selected_source_documents": len({row_doc_id(row) for row in selected}),
            "selected_splits": dict(sorted(Counter(str(row.get("reserved_split") or "") for row in selected).items())),
            "selected_categories": dict(sorted(selected_by_category.items())),
            "selected_known_nonpin_rows": len(selected) - selected_pin_rows,
            "selected_unknown_rows": selected_by_category.get("unknown_microtext", 0),
            "prefilter_reasons": dict(sorted(prefilter_counts.items())),
            "ignored_non_target_categories": dict(sorted(ignored_categories.items())),
            **packet_summary,
        },
        "balance_projection": balance_projection,
        "category_projection": category_details,
        "source_buffer_priorities": source_buffer_priorities,
        "exclusion_reasons": dict(sorted(exclusion_counts.items())),
        "acceptance_summary": {
            "all_category_floors_pass_after_full_acceptance": not any(
                details["shortfall_after_full_acceptance"] for details in category_details.values()
            ),
            "all_category_floors_pass_at_acceptance_floor": not any(
                details["shortfall_after_acceptance_floor"] for details in category_details.values()
            ),
            "categories_still_open_at_acceptance_floor": [
                category
                for category, details in category_details.items()
                if details["shortfall_after_acceptance_floor"]
            ],
        },
    }
    return selected, holds, report


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Balanced MicroText Closure Overlay",
        "",
        f"- Target: **{report['target']}**",
        f"- Selected review rows: **{report['counts']['selected_overlay_rows']}**",
        f"- Selected source documents: **{report['counts']['selected_source_documents']}**",
        f"- Active Gold modified: **{report['policy']['active_gold_modified']}**",
        f"- Minimum modeled acceptance rate: **{report['policy']['minimum_acceptance_rate']:.0%}**",
        "",
        "## Category Projection",
        "",
        "| Category | Before | Floor | Selected | Full acceptance | At modeled rate | Buffer gap |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for category, details in report["category_projection"].items():
        lines.append(
            f"| {category} | {details['projected_before_overlay']} | {details['minimum']} | "
            f"{details['selected_overlay_rows']} | {details['projected_after_full_acceptance']} | "
            f"{details['projected_after_acceptance_floor']} | "
            f"{details['additional_candidates_needed_for_acceptance_buffer']} |"
        )
    lines.extend(["", "## Next Source Priorities", ""])
    if report["source_buffer_priorities"]:
        for item in report["source_buffer_priorities"]:
            lines.append(
                f"- `{item['category']}`: mine at least {item['additional_candidates_needed']} "
                "additional release-safe candidates."
            )
    else:
        lines.append("- Existing canonical capacity covers the modeled acceptance buffer.")
    lines.extend(
        [
            "",
            "## Balance Projection",
            "",
            f"- Active pin share: **{report['balance_projection']['active_pin_share']:.2%}**",
            f"- Projected pin share after machine calibration and existing primary priorities: **{report['balance_projection']['projected_before_overlay_pin_share']:.2%}**",
            f"- Pin share after full acceptance: **{report['balance_projection']['pin_share_after_full_acceptance']:.2%}**",
            f"- Pin share at the modeled acceptance rate: **{report['balance_projection']['pin_share_after_modeled_acceptance']:.2%}**",
            f"- Selected known non-pin rows: **{report['balance_projection']['selected_known_nonpin_rows']}**",
            f"- Additional accepted non-pin rows still needed after full acceptance: **{report['balance_projection']['additional_accepted_nonpin_rows_to_limit_after_full_acceptance']}**",
            f"- Additional review rows needed at the modeled rate: **{report['balance_projection']['additional_review_rows_at_modeled_rate_to_limit']}**",
            "",
            "## Safety Contract",
            "",
            "- This is a review overlay only; every row remains `safe_to_merge_gold=false`.",
            "- Source payload SHA-256 is checked against the local file before selection.",
            "- Candidate IDs and physical regions already active, assigned, or machine-eligible are excluded.",
            "- Active review-packet rows and provenance replacements are excluded when configured.",
            "- Human or separately calibrated machine certification is still required before promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--active", type=Path, default=Path("eng_bench.jsonl"))
    parser.add_argument(
        "--assignment",
        type=Path,
        default=Path("derived/quality/v2_0_current_assignment_4018_2026-08-22-wave340-split-reserved.jsonl"),
    )
    parser.add_argument(
        "--machine-eligible",
        type=Path,
        default=Path("derived/quality/v2_0_machine_certification_2026-08-22-wave493-nonpin-only/auto_eligible_pending_calibration.jsonl"),
    )
    parser.add_argument(
        "--future-capacity",
        type=Path,
        default=Path("derived/quality/v2_0_canonical_future_capacity_2026-08-22-wave564-nasa-gox-split.jsonl"),
    )
    parser.add_argument("--inventory", type=Path, default=Path("SOURCE_INVENTORY.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("manifest.jsonl"))
    parser.add_argument(
        "--supplemental-future",
        action="append",
        type=Path,
        default=[],
        help="Additional review-only capacity JSONL; repeatable.",
    )
    parser.add_argument("--date-label", default="2026-08-22-wave662")
    parser.add_argument("--min-acceptance-rate", type=float, default=0.65)
    parser.add_argument("--soft-doc-cap", type=int, default=12)
    parser.add_argument(
        "--selection-mode",
        choices=sorted(SELECTION_MODES),
        default="category_closure",
    )
    parser.add_argument(
        "--required-split",
        action="append",
        choices=sorted(VALID_SPLITS),
        default=[],
        help="Only select rows reserved to this split; repeatable.",
    )
    parser.add_argument(
        "--packet-index",
        type=Path,
        help="Exclude rows and physical regions already present in ready review packets.",
    )
    parser.add_argument(
        "--split-plan",
        type=Path,
        help="Require row reservations to match this authoritative family-level split plan.",
    )
    parser.add_argument(
        "--verify-row-split-plans",
        action="store_true",
        help="Require every row to match its own split_reservation_plan file.",
    )
    parser.add_argument(
        "--exclude-provenance-replacements",
        action="store_true",
        help="Hold replacement rows so the overlay measures only net expansion capacity.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("derived/review_queues/v2_0_wave662_balanced_microtext_closure_overlay.jsonl"),
    )
    parser.add_argument(
        "--holds-output",
        type=Path,
        default=Path("derived/quality/v2_0_wave662_balanced_microtext_closure_holds.jsonl"),
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        default=Path("derived/quality/v2_0_wave662_balanced_microtext_closure_report.json"),
    )
    parser.add_argument(
        "--report-md",
        type=Path,
        default=Path("derived/quality/v2_0_wave662_balanced_microtext_closure_report.md"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    selected, holds, report = build_overlay(
        root,
        args.active,
        args.assignment,
        args.machine_eligible,
        args.future_capacity,
        args.inventory,
        args.manifest,
        date_label=args.date_label,
        min_acceptance_rate=args.min_acceptance_rate,
        soft_doc_cap=args.soft_doc_cap,
        supplemental_future_paths=args.supplemental_future,
        packet_index_path=args.packet_index,
        split_plan_path=args.split_plan,
        verify_row_split_plans=args.verify_row_split_plans,
        required_splits=set(args.required_split),
        exclude_provenance_replacements=args.exclude_provenance_replacements,
        selection_mode=args.selection_mode,
    )
    output = resolve_path(root, args.output)
    holds_output = resolve_path(root, args.holds_output)
    report_json = resolve_path(root, args.report_json)
    report_md = resolve_path(root, args.report_md)
    write_jsonl(output, selected)
    write_jsonl(holds_output, holds)
    report["outputs"] = {
        "selected": {"path": str(output.relative_to(root)), "sha256": file_sha256(output)},
        "holds": {"path": str(holds_output.relative_to(root)), "sha256": file_sha256(holds_output)},
        "report_json": str(report_json.relative_to(root)),
        "report_markdown": str(report_md.relative_to(root)),
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        "Balanced MicroText closure overlay: "
        f"selected={len(selected)} held={len(holds)} "
        f"sources={report['counts']['selected_source_documents']}"
    )
    print(f"Report: {report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
