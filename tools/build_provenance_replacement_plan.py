#!/usr/bin/env python3
"""Build a release-safe review plan for replacing rights-blocked active Gold rows."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from source_rights import is_release_safe_status


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def identity_from_row(row: dict[str, Any]) -> str:
    return str(
        row.get("candidate_id")
        or row.get("pair_id")
        or row.get("record_id")
        or row.get("item_id")
        or row.get("id")
        or ""
    ).strip()


def read_identity_file(path: Path) -> set[str]:
    rows: list[dict[str, Any]]
    if path.suffix.lower() == ".csv":
        rows = read_csv(path)
    else:
        rows = read_jsonl(path)
    return {identity_from_row(row) for row in rows if identity_from_row(row)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_gap_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "task",
        "split",
        "category",
        "affected_gold_rows",
        "selected_candidates",
        "exact_category_candidates",
        "fallback_candidates",
        "remaining_gap",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def task_for(row: dict[str, Any]) -> str:
    task = str(row.get("task") or "").strip().lower()
    if task in {"microtext", "visualdiff"}:
        return task
    if row.get("pair_id") or (row.get("image_old") and row.get("image_new")):
        return "visualdiff"
    return "microtext"


def normalized_category(value: Any) -> str:
    if isinstance(value, list):
        parts = sorted({str(item).strip() for item in value if str(item).strip()})
        return "+".join(parts) if parts else "unknown"
    text = str(value or "").strip()
    return text or "unknown"


def category_for(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return normalized_category(
        row.get("category")
        or metadata.get("category")
        or row.get("change_type")
        or metadata.get("change_type")
    )


def strip_candidate_suffix(pair_id: str) -> str:
    stem, separator, suffix = pair_id.rpartition("__")
    if separator and (suffix.isdigit() or suffix.startswith("p") or suffix.startswith("txt")):
        return stem
    return pair_id


def manifest_pair_docs(manifest: list[dict[str, Any]]) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for row in manifest:
        if row.get("type") != "pair":
            continue
        pair_id = str(row.get("pair_id") or "").strip()
        docs = {
            str(value).strip()
            for value in (row.get("from_doc_id"), row.get("to_doc_id"))
            if str(value or "").strip()
        }
        if pair_id and docs:
            mapping[pair_id] = docs
    return mapping


def active_source_docs(row: dict[str, Any], pair_docs: dict[str, set[str]]) -> set[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if task_for(row) == "microtext":
        doc_id = str(
            metadata.get("source_doc_id")
            or metadata.get("doc_id")
            or row.get("doc_id")
            or ""
        ).strip()
        return {doc_id} if doc_id else set()

    raw_pair_id = str(metadata.get("pair_id") or row.get("pair_id") or row.get("id") or "")
    if raw_pair_id.startswith("q_"):
        raw_pair_id = raw_pair_id[2:]
    candidates = [raw_pair_id]
    current = raw_pair_id
    for _ in range(3):
        current = strip_candidate_suffix(current)
        candidates.append(current)
    for pair_id in candidates:
        if pair_id in pair_docs:
            return pair_docs[pair_id]
    return set()


def candidate_identity(row: dict[str, Any]) -> str:
    return identity_from_row(row)


def active_candidate_identities(
    active_rows: list[dict[str, Any]],
    *,
    active_items: list[dict[str, Any]] | None = None,
    active_pairs: list[dict[str, Any]] | None = None,
) -> set[str]:
    """Return active question, annotation, and originating candidate identities."""
    identities: set[str] = set()
    for row in active_rows:
        direct = str(row.get("id") or row.get("qid") or "").strip()
        if direct:
            identities.add(direct)
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        for field in ("pair_id", "item_id"):
            value = str(metadata.get(field) or "").strip()
            if value:
                identities.add(value)
        identities.update(
            str(value).strip()
            for value in metadata.get("item_ids") or []
            if str(value).strip()
        )
    for row in active_items or []:
        for field in ("item_id", "source_candidate_id"):
            value = str(row.get(field) or "").strip()
            if value:
                identities.add(value)
    for row in active_pairs or []:
        for field in ("pair_id", "id", "source_candidate_id"):
            value = str(row.get(field) or "").strip()
            if value:
                identities.add(value)
    return identities


def candidate_source_unit(row: dict[str, Any]) -> str:
    if task_for(row) == "visualdiff":
        return str(row.get("project_id") or strip_candidate_suffix(str(row.get("pair_id") or "")))
    return str(row.get("doc_id") or row.get("source_doc_id") or "").strip()


def candidate_source_docs(row: dict[str, Any], pair_docs: dict[str, set[str]]) -> set[str]:
    docs = {
        str(value).strip()
        for value in (
            row.get("doc_id"),
            row.get("source_doc_id"),
            row.get("old_doc_id"),
            row.get("new_doc_id"),
        )
        if str(value or "").strip()
    }
    if task_for(row) != "visualdiff":
        return docs
    raw_pair_ids = [
        str(row.get("project_id") or "").strip(),
        str(row.get("pair_id") or "").strip(),
    ]
    for raw_pair_id in raw_pair_ids:
        current = raw_pair_id
        for _ in range(4):
            if current in pair_docs:
                docs.update(pair_docs[current])
                break
            current = strip_candidate_suffix(current)
    return docs


def false_value(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    return str(value or "").strip().lower() in {"", "0", "false", "no", "none"}


@lru_cache(maxsize=8)
def load_rgb_image(path: str) -> Image.Image:
    previous_max_image_pixels = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 1_000_000_000
    try:
        with Image.open(path) as source:
            return source.convert("RGB")
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max_image_pixels


def crop_pixel_digest(root: Path, image_value: Any, bbox_value: Any, pad_px: int = 28) -> str:
    image_path = root / str(image_value or "")
    bbox = list(bbox_value or [])
    if not image_path.exists() or len(bbox) < 4:
        raise ValueError("missing image or bbox")
    image = load_rgb_image(str(image_path.resolve()))
    x1, y1, x2, y2 = [int(round(float(value))) for value in bbox[:4]]
    left = max(0, x1 - pad_px)
    top = max(0, y1 - pad_px)
    right = min(image.width, x2 + pad_px)
    bottom = min(image.height, y2 + pad_px)
    if right <= left or bottom <= top:
        raise ValueError("out-of-frame evidence crop")
    crop = image.crop((left, top, right, bottom))
    digest = hashlib.sha256()
    digest.update(f"{crop.width}x{crop.height}:".encode("ascii"))
    digest.update(crop.tobytes())
    return digest.hexdigest()


def candidate_evidence_fingerprint(root: Path, row: dict[str, Any]) -> tuple[str, str]:
    identity = candidate_identity(row)
    task = task_for(row)
    try:
        if task == "microtext":
            digest = crop_pixel_digest(root, row.get("image_path"), row.get("bbox"))
        else:
            old_digest = crop_pixel_digest(root, row.get("image_old"), row.get("bbox_old"))
            new_digest = crop_pixel_digest(root, row.get("image_new"), row.get("bbox_new"))
            digest = hashlib.sha256(f"{old_digest}:{new_digest}".encode("ascii")).hexdigest()
        return f"{task}:sha256:{digest}", "pixel_crop_sha256"
    except (OSError, ValueError, TypeError):
        return f"{task}:identity:{identity}", "identity_fallback_missing_evidence"


def candidate_is_release_safe(
    row: dict[str, Any],
    inventory_status: dict[str, str],
    blocked_docs: set[str],
    pair_docs: dict[str, set[str]],
) -> tuple[bool, str]:
    doc_ids = candidate_source_docs(row, pair_docs)
    if doc_ids & blocked_docs:
        return False, "blocked_source_doc"

    statuses = []
    explicit = str(row.get("source_public_status") or "").strip()
    if explicit:
        statuses.append(explicit)
    statuses.extend(inventory_status[doc_id] for doc_id in sorted(doc_ids) if doc_id in inventory_status)
    if not statuses:
        return False, "missing_release_status"
    if not all(is_release_safe_status(status) for status in statuses):
        return False, "non_release_safe_status"
    return True, "release_safe_status"


def build_plan(
    *,
    root: Path,
    provenance_report: Path,
    current_assignment: Path,
    future_capacity: Path,
    date_label: str,
    max_per_source: int,
    preferred_issued: list[Path] | None = None,
    excluded_candidate_ids: set[str] | None = None,
    additional_capacity: list[Path] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    load_rgb_image.cache_clear()
    provenance = read_json(provenance_report)
    blocked_docs = {
        str(row.get("doc_id") or "").strip()
        for row in provenance.get("documents", [])
        if str(row.get("doc_id") or "").strip() and not bool(row.get("paper_ready"))
    }
    manifest = read_jsonl(root / "manifest.jsonl")
    pair_docs = manifest_pair_docs(manifest)
    active_rows = read_jsonl(root / "eng_bench.jsonl")
    active_candidate_ids = active_candidate_identities(
        active_rows,
        active_items=read_jsonl(
            root / "microtext" / "annotations" / "microtext_items.jsonl"
        ),
        active_pairs=read_jsonl(
            root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
        ),
    )

    affected_rows: list[dict[str, Any]] = []
    demand: Counter[tuple[str, str, str]] = Counter()
    unresolved_active_visualdiff_rows = 0
    for row in active_rows:
        docs = active_source_docs(row, pair_docs)
        blocked = sorted(docs & blocked_docs)
        if not blocked:
            continue
        task = task_for(row)
        split = str(row.get("split") or "").strip().lower()
        category = category_for(row)
        if task == "visualdiff" and not docs:
            unresolved_active_visualdiff_rows += 1
        demand[(task, split, category)] += 1
        affected_rows.append(
            {
                "active_id": str(row.get("id") or row.get("qid") or ""),
                "task": task,
                "split": split,
                "category": category,
                "blocked_source_doc_ids": blocked,
                "replacement_state": "awaiting_reviewed_release_safe_replacement",
            }
        )

    inventory_status = {
        str(row.get("doc_id") or "").strip(): str(
            row.get("public_status") or row.get("rights_tier") or ""
        ).strip()
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }

    preferred_issued_ids = {
        candidate_identity(row)
        for path in (preferred_issued or [])
        for row in read_jsonl(path)
        if candidate_identity(row)
    }
    excluded_candidate_ids = set(excluded_candidate_ids or set())

    candidate_rows: list[dict[str, Any]] = []
    rejected_candidates: Counter[str] = Counter()
    seen_identities: set[str] = set()
    capacity_sources = [
        ("current_assignment", current_assignment),
        ("future_capacity", future_capacity),
    ]
    capacity_sources.extend(
        ("supplemental_capacity", path) for path in (additional_capacity or [])
    )
    for phase, path in capacity_sources:
        for row in read_jsonl(path):
            identity = candidate_identity(row)
            if not identity or identity in seen_identities:
                rejected_candidates["missing_or_duplicate_identity"] += 1
                continue
            seen_identities.add(identity)
            if identity in excluded_candidate_ids:
                rejected_candidates["excluded_candidate_identity"] += 1
                continue
            if identity in active_candidate_ids:
                rejected_candidates["already_active_gold_identity"] += 1
                continue
            split = str(row.get("reserved_split") or row.get("split") or "").strip().lower()
            if split not in {"train", "dev", "test"}:
                rejected_candidates["missing_reserved_split"] += 1
                continue
            if not false_value(row.get("safe_to_merge_gold")):
                rejected_candidates["already_mergeable_or_invalid_safety"] += 1
                continue
            rights_ok, rights_reason = candidate_is_release_safe(
                row,
                inventory_status,
                blocked_docs,
                pair_docs,
            )
            if not rights_ok:
                rejected_candidates[rights_reason] += 1
                continue
            candidate_rows.append(
                {
                    **row,
                    "_replacement_identity": identity,
                    "_replacement_origin_phase": phase,
                    "_replacement_task": task_for(row),
                    "_replacement_split": split,
                    "_replacement_category": category_for(row),
                    "_replacement_source_unit": candidate_source_unit(row),
                    "_replacement_rights_check": rights_reason,
                    "_replacement_preferred_issued": identity in preferred_issued_ids,
                }
            )

    phase_rank = {
        "current_assignment": 0,
        "future_capacity": 1,
        "supplemental_capacity": 2,
    }

    def candidate_rank(row: dict[str, Any]) -> tuple[Any, ...]:
        reservoir_rank = row.get("reservoir_rank")
        try:
            numeric_rank = int(reservoir_rank)
        except (TypeError, ValueError):
            numeric_rank = 10**9
        return (
            phase_rank.get(str(row.get("_replacement_origin_phase")), 9),
            numeric_rank,
            str(row.get("_replacement_source_unit") or ""),
            str(row.get("_replacement_identity") or ""),
        )

    candidate_rows.sort(key=candidate_rank)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_evidence_fingerprints: set[str] = set()
    duplicate_evidence_skips: set[str] = set()
    evidence_fingerprint_status: Counter[str] = Counter()
    selected_per_source: Counter[str] = Counter()
    selected_by_group: Counter[tuple[str, str, str]] = Counter()
    exact_by_group: Counter[tuple[str, str, str]] = Counter()
    fallback_by_group: Counter[tuple[str, str, str]] = Counter()

    def take(
        group: tuple[str, str, str],
        *,
        exact: bool,
        enforce_cap: bool,
        preferred_only: bool = False,
    ) -> None:
        needed = demand[group] - selected_by_group[group]
        if needed <= 0:
            return
        task, split, category = group
        for row in candidate_rows:
            if needed <= 0:
                break
            identity = str(row["_replacement_identity"])
            if identity in selected_ids:
                continue
            if preferred_only and not row["_replacement_preferred_issued"]:
                continue
            if row["_replacement_task"] != task or row["_replacement_split"] != split:
                continue
            if exact and row["_replacement_category"] != category:
                continue
            source_unit = str(row.get("_replacement_source_unit") or "")
            if enforce_cap and source_unit and selected_per_source[source_unit] >= max_per_source:
                continue
            evidence_fingerprint, fingerprint_status = candidate_evidence_fingerprint(root, row)
            if evidence_fingerprint in selected_evidence_fingerprints:
                duplicate_evidence_skips.add(identity)
                continue
            category_matches = row["_replacement_category"] == category
            match_level = (
                "task_split_category" if category_matches else "task_split_fallback"
            )
            output = {
                key: value for key, value in row.items() if not key.startswith("_replacement_")
            }
            output.update(
                {
                    "provenance_replacement_candidate": True,
                    "provenance_replacement_date_label": date_label,
                    "replacement_for_task": task,
                    "replacement_for_split": split,
                    "replacement_for_category": category,
                    "replacement_match_level": match_level,
                    "replacement_origin_phase": row["_replacement_origin_phase"],
                    "replacement_rights_check": row["_replacement_rights_check"],
                    "replacement_source_unit": source_unit,
                    "replacement_evidence_fingerprint": evidence_fingerprint,
                    "replacement_evidence_fingerprint_status": fingerprint_status,
                    "promotion_state": "unreviewed_provenance_replacement_candidate",
                    "review_status": "needs_review",
                    "safe_to_merge_gold": False,
                }
            )
            selected.append(output)
            selected_ids.add(identity)
            selected_evidence_fingerprints.add(evidence_fingerprint)
            evidence_fingerprint_status[fingerprint_status] += 1
            selected_per_source[source_unit] += 1
            selected_by_group[group] += 1
            if category_matches:
                exact_by_group[group] += 1
            else:
                fallback_by_group[group] += 1
            needed -= 1

    groups = sorted(demand)
    for group in groups:
        take(group, exact=False, enforce_cap=True, preferred_only=True)
    for group in groups:
        take(group, exact=True, enforce_cap=True)
    for group in groups:
        take(group, exact=False, enforce_cap=True)
    for group in groups:
        take(group, exact=False, enforce_cap=False)

    gap_rows = []
    for group in groups:
        task, split, category = group
        selected_count = selected_by_group[group]
        gap_rows.append(
            {
                "task": task,
                "split": split,
                "category": category,
                "affected_gold_rows": demand[group],
                "selected_candidates": selected_count,
                "exact_category_candidates": exact_by_group[group],
                "fallback_candidates": fallback_by_group[group],
                "remaining_gap": demand[group] - selected_count,
            }
        )

    affected_by_task_split = Counter((row["task"], row["split"]) for row in affected_rows)
    selected_by_task_split = Counter(
        (row["replacement_for_task"], row["replacement_for_split"]) for row in selected
    )
    gap_by_task_split = {
        f"{task}:{split}": affected_by_task_split[(task, split)]
        - selected_by_task_split[(task, split)]
        for task, split in sorted(affected_by_task_split)
    }
    candidate_identities = {str(row["_replacement_identity"]) for row in candidate_rows}
    selected_preferred_issued = len(selected_ids & preferred_issued_ids)
    retired_active_preferred = preferred_issued_ids & active_candidate_ids
    excluded_preferred_issued = preferred_issued_ids & excluded_candidate_ids
    missing_preferred_issued = (
        preferred_issued_ids
        - retired_active_preferred
        - excluded_preferred_issued
        - candidate_identities
    )
    summary = {
        "date_label": date_label,
        "blocked_active_source_docs": sorted(blocked_docs),
        "blocked_active_source_doc_count": len(blocked_docs),
        "affected_gold_rows": len(affected_rows),
        "affected_gold_rows_by_task_split": {
            f"{task}:{split}": count
            for (task, split), count in sorted(affected_by_task_split.items())
        },
        "candidate_pool_rows": len(candidate_rows),
        "candidate_pool_rejections": dict(sorted(rejected_candidates.items())),
        "selected_replacement_candidates": len(selected),
        "selected_replacement_candidates_by_origin": dict(
            sorted(Counter(row["replacement_origin_phase"] for row in selected).items())
        ),
        "already_assigned_human_review_rows": sum(
            row["replacement_origin_phase"] == "current_assignment" for row in selected
        ),
        "new_human_review_priority_rows": sum(
            row["replacement_origin_phase"] != "current_assignment" for row in selected
        ),
        "selected_replacement_candidates_by_task_split": {
            f"{task}:{split}": count
            for (task, split), count in sorted(selected_by_task_split.items())
        },
        "remaining_replacement_gap_by_task_split": gap_by_task_split,
        "remaining_replacement_gap": sum(max(0, row["remaining_gap"]) for row in gap_rows),
        "exact_category_matches": sum(exact_by_group.values()),
        "task_split_fallback_matches": sum(fallback_by_group.values()),
        "selected_source_units": len({row["replacement_source_unit"] for row in selected}),
        "selected_unique_evidence_fingerprints": len(selected_evidence_fingerprints),
        "duplicate_evidence_candidate_skips": len(duplicate_evidence_skips),
        "evidence_fingerprint_status": dict(sorted(evidence_fingerprint_status.items())),
        "max_candidates_per_source_soft_cap": max_per_source,
        "preferred_issued_requested": len(preferred_issued_ids),
        "preferred_issued_available": len(preferred_issued_ids & candidate_identities),
        "preferred_issued_selected": selected_preferred_issued,
        "preferred_issued_retired_active_gold": len(retired_active_preferred),
        "preferred_issued_excluded": len(excluded_preferred_issued),
        "preferred_issued_missing": len(missing_preferred_issued),
        "excluded_candidate_identities_requested": len(excluded_candidate_ids),
        "excluded_candidate_identities_present": len(
            excluded_candidate_ids & (seen_identities | active_candidate_ids)
        ),
        "all_replacement_capacity_available": all(row["remaining_gap"] == 0 for row in gap_rows),
        "reviewed_replacements_promoted": 0,
        "active_gold_rows_modified": 0,
        "safe_to_merge_gold_rows": 0,
        "unresolved_active_visualdiff_source_mappings": unresolved_active_visualdiff_rows,
        "interpretation": (
            "Selected rows are review priorities only. Active Gold remains unchanged until replacements "
            "receive human acceptance and pass strict promotion, split, leakage, dedup, and provenance gates."
        ),
    }
    return summary, affected_rows, selected, gap_rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--provenance-report", type=Path, required=True)
    parser.add_argument("--current-assignment", type=Path, required=True)
    parser.add_argument("--future-capacity", type=Path, required=True)
    parser.add_argument(
        "--additional-capacity",
        type=Path,
        action="append",
        default=[],
        help=(
            "Additional release-safe capacity JSONL evaluated after the canonical future pool; "
            "repeat for independently generated supplemental reservoirs."
        ),
    )
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--max-per-source", type=int, default=50)
    parser.add_argument(
        "--preferred-issued",
        type=Path,
        action="append",
        default=[],
        help="Previously issued review queue whose still-valid rows should win future-capacity selection.",
    )
    parser.add_argument(
        "--require-all-preferred-issued",
        action="store_true",
        help="Fail when any preferred-issued identity is absent from the validated candidate pool.",
    )
    parser.add_argument(
        "--exclude-candidates",
        type=Path,
        action="append",
        default=[],
        help=(
            "CSV or JSONL containing candidate identities that must not be selected; "
            "repeat for human holds, evidence holds, or superseded candidates."
        ),
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--affected-jsonl", type=Path, required=True)
    parser.add_argument("--candidates-jsonl", type=Path, required=True)
    parser.add_argument("--new-review-candidates-jsonl", type=Path)
    parser.add_argument("--already-assigned-candidates-jsonl", type=Path)
    parser.add_argument("--gap-csv", type=Path, required=True)
    return parser.parse_args(argv)


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    if args.max_per_source <= 0:
        raise ValueError("--max-per-source must be positive")
    excluded_paths = [resolve(root, path) for path in args.exclude_candidates]
    additional_capacity_paths = [resolve(root, path) for path in args.additional_capacity]
    excluded_candidate_ids = {
        identity
        for path in excluded_paths
        for identity in read_identity_file(path)
    }
    summary, affected, candidates, gaps = build_plan(
        root=root,
        provenance_report=resolve(root, args.provenance_report),
        current_assignment=resolve(root, args.current_assignment),
        future_capacity=resolve(root, args.future_capacity),
        date_label=args.date_label,
        max_per_source=args.max_per_source,
        preferred_issued=[resolve(root, path) for path in args.preferred_issued],
        excluded_candidate_ids=excluded_candidate_ids,
        additional_capacity=additional_capacity_paths,
    )
    summary["additional_capacity_sources"] = [
        {
            "path": path.as_posix(),
            "rows": len(read_jsonl(path)),
        }
        for path in additional_capacity_paths
    ]
    summary["excluded_candidate_sources"] = [
        {
            "path": path.as_posix(),
            "identities": len(read_identity_file(path)),
        }
        for path in excluded_paths
    ]
    if args.require_all_preferred_issued and summary["preferred_issued_missing"]:
        raise ValueError(
            f"{summary['preferred_issued_missing']} preferred-issued rows are absent from the candidate pool"
        )
    write_json(resolve(root, args.output_json), summary)
    write_jsonl(resolve(root, args.affected_jsonl), affected)
    write_jsonl(resolve(root, args.candidates_jsonl), candidates)
    if args.new_review_candidates_jsonl:
        write_jsonl(
            resolve(root, args.new_review_candidates_jsonl),
            [
                row
                for row in candidates
                if row.get("replacement_origin_phase") != "current_assignment"
            ],
        )
    if args.already_assigned_candidates_jsonl:
        write_jsonl(
            resolve(root, args.already_assigned_candidates_jsonl),
            [
                row
                for row in candidates
                if row.get("replacement_origin_phase") == "current_assignment"
            ],
        )
    write_gap_csv(resolve(root, args.gap_csv), gaps)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
