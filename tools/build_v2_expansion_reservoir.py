#!/usr/bin/env python3
"""Build a conservative, deduplicated Gold v2.0 human-review reservoir."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import manifest_maps, read_csv, read_jsonl
from audit_source_conversion_readiness import TERMINAL_REVIEW_STATUSES, status_for
from audit_staged_v2_capacity import (
    NEAR_OVERLAP_AREA_RATIO_THRESHOLD,
    NEAR_OVERLAP_CONTAINMENT_THRESHOLD,
    NEAR_OVERLAP_IOU_THRESHOLD,
    audit_source_doc,
    bboxes_are_near_duplicates,
    capacity_exclusion_reason,
    capacity_identity,
    load_split_reservations,
    microtext_region_geometry,
    read_rows,
    staged_split_unit,
    task_for_row,
)


OPEN_STATUSES = {"", "candidate", "needs_review", "provisional_review", "todo"}
CATEGORY_PRIORITY = {
    "tolerance_value": 0,
    "pipe_line_tag": 1,
    "process_value": 2,
    "room_label": 3,
    "instrument_tag": 4,
    "dimension_value": 5,
    "equipment_tag": 6,
    "wire_number": 7,
    "gdandt_symbol": 8,
    "pin_label": 9,
}


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip().casefold()


def textlayer_visible_text(value: Any) -> str:
    text = str(value or "")
    return " ".join(re.findall(r">([^<>]+)<", text)) if "<tspan" in text.lower() else ""


def semantic_hold_issue(row: dict[str, Any]) -> str:
    """Keep audited semantic failures out of a review-ready machine reservoir."""
    upstream_tier = str(row.get("unstaged_capacity_tier") or "").strip().lower()
    if upstream_tier and upstream_tier != "machine_prequalified_needs_visual_qa":
        return f"upstream_{upstream_tier}"
    target = str(row.get("target_text") or row.get("proposed_text") or "").strip()
    raw = row.get("raw_text") or row.get("text_context") or ""
    visible = textlayer_visible_text(raw)
    if visible and normalized_text(target) not in normalized_text(visible):
        return "textlayer_target_not_in_visible_text"
    return ""


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    return resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else resolved.as_posix()


def normalized_bbox(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, str):
        value = [part.strip() for part in value.strip().strip("[]()").split(",")]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = tuple(int(round(float(item))) for item in value)
    except (TypeError, ValueError):
        return None
    return bbox if bbox[2] > bbox[0] and bbox[3] > bbox[1] else None


def region_near_overlaps(
    row: dict[str, Any],
    regions: dict[tuple[str, int], list[tuple[int, int, int, int]]],
) -> bool:
    geometry = microtext_region_geometry(row)
    if not geometry:
        return False
    doc_id, page_index, bbox = geometry
    return any(
        bboxes_are_near_duplicates(bbox, existing)
        for existing in regions.get((doc_id, page_index), [])
    )


def add_region(
    row: dict[str, Any],
    regions: dict[tuple[str, int], list[tuple[int, int, int, int]]],
) -> None:
    geometry = microtext_region_geometry(row)
    if not geometry:
        return
    doc_id, page_index, bbox = geometry
    regions[(doc_id, page_index)].append(bbox)


def row_aliases(row: dict[str, Any]) -> set[str]:
    """Return row-level aliases without treating a source intake ID as a row ID."""
    if task_for_row(row) == "visualdiff":
        values = (row.get("pair_id"), row.get("id"))
    else:
        values = [
            row.get("candidate_id"),
            row.get("item_id"),
            row.get("pre_padding_candidate_id"),
        ]
        source_candidate_id = str(row.get("source_candidate_id") or "").strip()
        if source_candidate_id.startswith("mtcand__"):
            values.append(source_candidate_id)
    return {str(value).strip() for value in values if str(value or "").strip()}


@contextmanager
def audited_pillow_pixel_limit(max_image_pixels: int | None):
    previous_limit = Image.MAX_IMAGE_PIXELS
    if max_image_pixels is not None:
        Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        yield
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def evidence_issue(
    root: Path,
    row: dict[str, Any],
    image_sizes: dict[Path, tuple[int, int] | None],
    crop_information: dict[tuple[Path, tuple[int, int, int, int]], bool],
    max_image_pixels: int | None = None,
) -> str:
    image_value = str(row.get("image_path") or row.get("crop_path") or "").strip()
    if not image_value:
        return "missing_image_path"
    image_path = Path(image_value)
    image_path = image_path if image_path.is_absolute() else root / image_path
    image_path = image_path.resolve()
    if not image_path.is_file():
        return "missing_image_file"
    if image_path not in image_sizes:
        try:
            with audited_pillow_pixel_limit(max_image_pixels):
                with Image.open(image_path) as image:
                    image_sizes[image_path] = image.size
        except (OSError, ValueError):
            image_sizes[image_path] = None
    size = image_sizes[image_path]
    if not size:
        return "unreadable_image"
    bbox = normalized_bbox(row.get("bbox") or row.get("bbox_px"))
    if not bbox:
        return "invalid_bbox"
    width, height = size
    if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > width or bbox[3] > height:
        return "bbox_out_of_bounds"
    crop_key = (image_path, bbox)
    if crop_key not in crop_information:
        try:
            with audited_pillow_pixel_limit(max_image_pixels):
                with Image.open(image_path) as image:
                    grayscale = image.convert("L").crop(bbox)
                    extrema = grayscale.getextrema()
                    deviation = float(ImageStat.Stat(grayscale).stddev[0])
            crop_information[crop_key] = bool(extrema and extrema[1] - extrema[0] >= 8 and deviation >= 3.0)
        except (OSError, ValueError):
            crop_information[crop_key] = False
    if not crop_information[crop_key]:
        return "low_information_crop"
    return ""


def load_existing_identities(
    root: Path,
    capacity_report_path: Path,
) -> tuple[
    set[str],
    set[str],
    dict[str, int],
    dict[tuple[str, int], list[tuple[int, int, int, int]]],
]:
    report = json.loads(capacity_report_path.read_text(encoding="utf-8"))
    physical: set[str] = set()
    aliases: set[str] = set()
    counts = Counter()
    regions: dict[tuple[str, int], list[tuple[int, int, int, int]]] = defaultdict(list)

    def add_rows(rows: list[dict[str, Any]], source: str) -> None:
        for row in rows:
            identity = capacity_identity(row)
            if identity:
                physical.add(identity)
            aliases.update(row_aliases(row))
            add_region(row, regions)
            counts[source] += 1

    for cohort in report.get("cohorts", []):
        value = str(cohort.get("path") or "").strip()
        if not value:
            continue
        path = Path(value)
        path = path if path.is_absolute() else root / path
        if path.is_file():
            add_rows(read_rows(path), "capacity_cohort_rows")

    add_rows(read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl"), "active_rows")
    add_rows(read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"), "active_rows")

    for directory in (root / "microtext" / "annotations", root / "visualdiff" / "annotations"):
        for path in sorted(directory.glob("*_reviewed.jsonl")):
            terminal = [row for row in read_jsonl(path) if status_for(row) in TERMINAL_REVIEW_STATUSES]
            add_rows(terminal, "terminal_reviewed_rows")

    return physical, aliases, dict(counts), regions


def candidate_quality(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
    target = str(row.get("target_text") or row.get("proposed_text") or "").strip()
    raw = str(row.get("raw_text") or row.get("text_context") or "").strip()
    category = str(row.get("category") or "").strip()
    candidate_id = str(row.get("candidate_id") or "").strip()
    return (
        1 if target and raw == target else 0,
        1 if "__fp_" in candidate_id else 0,
        1 if str(row.get("version_id") or "").strip().lower() not in {"", "unknown"} else 0,
        -CATEGORY_PRIORITY.get(category, 99),
        candidate_id,
    )


def hold_row(row: dict[str, Any], reason: str) -> dict[str, Any]:
    held = deepcopy(row)
    held["reservoir_disposition"] = "held"
    held["reservoir_hold_reason"] = reason
    held["safe_to_merge_gold"] = False
    return held


def select_source_balanced(
    rows: list[dict[str, Any]],
    target_rows: int,
    max_per_doc: int,
    max_per_doc_category: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    global_categories = Counter(str(row.get("category") or "") for row in rows)
    for row in rows:
        by_doc[str(row.get("doc_id") or "")].append(row)
    for doc_rows in by_doc.values():
        doc_rows.sort(
            key=lambda row: (
                global_categories[str(row.get("category") or "")],
                CATEGORY_PRIORITY.get(str(row.get("category") or ""), 99),
                str(row.get("candidate_id") or ""),
            )
        )

    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    selected_by_doc = Counter()
    selected_by_doc_category = Counter()
    cursors = {doc_id: 0 for doc_id in by_doc}
    doc_ids = sorted(by_doc)
    made_progress = True
    while len(selected) < target_rows and made_progress:
        made_progress = False
        for doc_id in doc_ids:
            if len(selected) >= target_rows:
                break
            if selected_by_doc[doc_id] >= max_per_doc:
                continue
            rows_for_doc = by_doc[doc_id]
            while cursors[doc_id] < len(rows_for_doc):
                row = rows_for_doc[cursors[doc_id]]
                cursors[doc_id] += 1
                category = str(row.get("category") or "")
                key = (doc_id, category)
                if selected_by_doc_category[key] >= max_per_doc_category:
                    held.append(hold_row(row, "per_doc_category_cap"))
                    continue
                selected.append(row)
                selected_by_doc[doc_id] += 1
                selected_by_doc_category[key] += 1
                made_progress = True
                break

    selected_ids = {capacity_identity(row) for row in selected}
    for doc_id, rows_for_doc in by_doc.items():
        for row in rows_for_doc[cursors[doc_id] :]:
            if capacity_identity(row) in selected_ids:
                continue
            reason = "target_cap" if len(selected) >= target_rows else "per_doc_cap"
            held.append(hold_row(row, reason))
    return selected, held


def build_reservoir(
    root: Path,
    input_path: Path,
    capacity_report_path: Path,
    split_plan_path: Path | None,
    target_rows: int,
    max_per_doc: int,
    max_per_doc_category: int,
    date_label: str,
    max_image_pixels: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    input_path = input_path if input_path.is_absolute() else root / input_path
    capacity_report_path = (
        capacity_report_path if capacity_report_path.is_absolute() else root / capacity_report_path
    )
    candidates = read_jsonl(input_path)
    docs, _manifest_pairs = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    (
        existing_physical,
        existing_aliases,
        exclusion_sources,
        existing_regions,
    ) = load_existing_identities(root, capacity_report_path)
    reservations: dict[tuple[str, str], str] = {}
    split_issues: list[dict[str, str]] = []
    if split_plan_path:
        split_plan_path = split_plan_path if split_plan_path.is_absolute() else root / split_plan_path
        reservations, split_issues = load_split_reservations(split_plan_path)

    source_cache = {
        doc_id: audit_source_doc(root, doc_id, docs, inventory)
        for doc_id in sorted({str(row.get("doc_id") or "").strip() for row in candidates})
        if doc_id
    }
    image_sizes: dict[Path, tuple[int, int] | None] = {}
    crop_information: dict[tuple[Path, tuple[int, int, int, int]], bool] = {}
    preeligible: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    held_reasons = Counter()

    for original in candidates:
        row = deepcopy(original)
        reason = ""
        semantic_issue = semantic_hold_issue(row)
        if task_for_row(row) != "microtext":
            reason = "unsupported_task"
        elif capacity_exclusion_reason(row):
            reason = "terminal_or_superseded_status"
        elif status_for(row) not in OPEN_STATUSES:
            reason = "not_open_status"
        elif not str(row.get("candidate_id") or row.get("item_id") or "").strip():
            reason = "missing_candidate_id"
        elif not str(row.get("doc_id") or "").strip():
            reason = "missing_doc_id"
        elif str(row.get("version_id") or "").strip().lower() in {"", "unknown"}:
            reason = "unresolved_version"
        elif not str(row.get("category") or "").strip():
            reason = "missing_category"
        elif not str(row.get("target_text") or row.get("proposed_text") or "").strip():
            reason = "missing_target_text"
        elif semantic_issue:
            reason = semantic_issue
        else:
            identity = capacity_identity(row)
            aliases = row_aliases(row)
            if not identity:
                reason = "missing_physical_identity"
            elif identity in existing_physical or aliases & existing_aliases:
                reason = "existing_gold_review_or_capacity_overlap"
            elif region_near_overlaps(row, existing_regions):
                reason = "existing_gold_review_or_capacity_near_overlap"
            else:
                source = source_cache.get(str(row.get("doc_id") or "").strip(), {})
                if not source.get("paper_ready", False):
                    reason = "source_not_paper_ready"
                else:
                    reason = evidence_issue(
                        root,
                        row,
                        image_sizes,
                        crop_information,
                        max_image_pixels=max_image_pixels,
                    )
        if reason:
            held.append(hold_row(row, reason))
            held_reasons[reason] += 1
            continue
        preeligible.append(row)

    by_physical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in preeligible:
        by_physical[capacity_identity(row)].append(row)
    exact_eligible: list[dict[str, Any]] = []
    for identity in sorted(by_physical):
        variants = sorted(by_physical[identity], key=candidate_quality, reverse=True)
        exact_eligible.append(variants[0])
        for duplicate in variants[1:]:
            held.append(hold_row(duplicate, "duplicate_input_region"))
            held_reasons["duplicate_input_region"] += 1

    eligible: list[dict[str, Any]] = []
    eligible_regions: dict[tuple[str, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    for row in sorted(exact_eligible, key=candidate_quality, reverse=True):
        if region_near_overlaps(row, eligible_regions):
            held.append(hold_row(row, "duplicate_input_near_region"))
            held_reasons["duplicate_input_near_region"] += 1
            continue
        eligible.append(row)
        add_region(row, eligible_regions)

    selected, selection_holds = select_source_balanced(
        eligible,
        target_rows=max(0, target_rows),
        max_per_doc=max(1, max_per_doc),
        max_per_doc_category=max(1, max_per_doc_category),
    )
    held.extend(selection_holds)
    held_reasons.update(str(row.get("reservoir_hold_reason") or "") for row in selection_holds)

    for rank, row in enumerate(selected, 1):
        source = source_cache[str(row.get("doc_id") or "")]
        row["review_status"] = "needs_review"
        row["machine_qa_status"] = "v2_expansion_reservoir"
        row["reservoir_disposition"] = "selected"
        row["reservoir_date_label"] = date_label
        row["reservoir_rank"] = rank
        row["promotion_state"] = "unreviewed_candidate"
        row["safe_to_merge_gold"] = False
        row["source_payload_sha256"] = source.get("computed_sha256", "")
        row["source_public_status"] = source.get("public_status", "")
        row["reserved_split"] = reservations.get(staged_split_unit(row), "unassigned")

    selected_physical = [capacity_identity(row) for row in selected]
    selected_aliases = [alias for row in selected for alias in row_aliases(row)]
    selected_docs = sorted({str(row.get("doc_id") or "") for row in selected})
    selected_source_audits = [source_cache[doc_id] for doc_id in selected_docs]
    selected_regions: dict[tuple[str, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    selected_near_regions_unique = True
    zero_existing_near_overlap = True
    for row in selected:
        if region_near_overlaps(row, selected_regions):
            selected_near_regions_unique = False
        if region_near_overlaps(row, existing_regions):
            zero_existing_near_overlap = False
        add_region(row, selected_regions)
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "input_path": relative_path(root, input_path),
        "capacity_report": relative_path(root, capacity_report_path),
        "split_plan": relative_path(root, split_plan_path) if split_plan_path else "",
        "input_rows": len(candidates),
        "preeligible_rows": len(preeligible),
        "eligible_unique_regions": len(eligible),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "target_rows": target_rows,
        "selection_shortfall": max(0, target_rows - len(selected)),
        "selected_docs": len(selected_docs),
        "selected_categories": dict(sorted(Counter(str(row.get("category") or "") for row in selected).items())),
        "selected_splits": dict(sorted(Counter(str(row.get("reserved_split") or "") for row in selected).items())),
        "selected_rows_by_doc": dict(sorted(Counter(str(row.get("doc_id") or "") for row in selected).items())),
        "held_reasons": dict(sorted(held_reasons.items())),
        "existing_exclusion_sources": exclusion_sources,
        "existing_physical_identities": len(existing_physical),
        "existing_aliases": len(existing_aliases),
        "near_overlap_policy": {
            "iou_threshold": NEAR_OVERLAP_IOU_THRESHOLD,
            "containment_threshold": NEAR_OVERLAP_CONTAINMENT_THRESHOLD,
            "minimum_area_ratio": NEAR_OVERLAP_AREA_RATIO_THRESHOLD,
        },
        "max_image_pixels": max_image_pixels,
        "source_audits": selected_source_audits,
        "paper_ready_selected_docs": sum(bool(row.get("paper_ready")) for row in selected_source_audits),
        "split_plan_issues": split_issues,
        "checks": {
            "input_conserved": len(selected) + len(held) == len(candidates),
            "selected_physical_unique": len(selected_physical) == len(set(selected_physical)),
            "selected_aliases_unique": len(selected_aliases) == len(set(selected_aliases)),
            "zero_existing_physical_overlap": not (set(selected_physical) & existing_physical),
            "zero_existing_alias_overlap": not (set(selected_aliases) & existing_aliases),
            "zero_existing_near_region_overlap": zero_existing_near_overlap,
            "selected_near_regions_unique": selected_near_regions_unique,
            "all_selected_sources_paper_ready": all(row.get("paper_ready") for row in selected_source_audits),
            "all_selected_safe_to_merge_false": all(row.get("safe_to_merge_gold") is False for row in selected),
            "split_plan_valid": not split_issues,
        },
    }
    report["valid"] = bool(selected) and all(report["checks"].values())
    return selected, held, report


def render_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks", {})
    lines = [
        "# Eng_Bench Gold v2.0 Expansion Reservoir",
        "",
        f"- Date label: `{report.get('date_label', '')}`",
        f"- Input rows: `{report.get('input_rows', 0)}`",
        f"- Unique eligible regions: `{report.get('eligible_unique_regions', 0)}`",
        f"- Selected review rows: `{report.get('selected_rows', 0)}`",
        f"- Held rows: `{report.get('held_rows', 0)}`",
        f"- Source documents: `{report.get('selected_docs', 0)}`",
        f"- Valid: `{str(report.get('valid', False)).lower()}`",
        "",
        "## Selected Categories",
        "",
        "| Category | Rows |",
        "| --- | ---: |",
    ]
    lines.extend(f"| `{key}` | {value} |" for key, value in report.get("selected_categories", {}).items())
    lines.extend(["", "## Reserved Splits", "", "| Split | Rows |", "| --- | ---: |"])
    lines.extend(f"| `{key}` | {value} |" for key, value in report.get("selected_splits", {}).items())
    lines.extend(["", "## Holds", "", "| Reason | Rows |", "| --- | ---: |"])
    lines.extend(f"| `{key}` | {value} |" for key, value in report.get("held_reasons", {}).items())
    lines.extend(["", "## Integrity Checks", "", "| Check | Pass |", "| --- | --- |"])
    lines.extend(f"| `{key}` | `{str(value).lower()}` |" for key, value in checks.items())
    lines.extend(
        [
            "",
            "Selected rows are unreviewed capacity only. Every row is marked",
            "`safe_to_merge_gold=false` and still requires human review plus all release gates.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--capacity-report", type=Path, required=True)
    parser.add_argument("--split-plan", type=Path)
    parser.add_argument("--target-rows", type=int, default=5_000)
    parser.add_argument("--max-per-doc", type=int, default=250)
    parser.add_argument("--max-per-doc-category", type=int, default=175)
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        help="Opt-in Pillow safety ceiling for audited large raster sources.",
    )
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--held-output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    selected, held, report = build_reservoir(
        root=root,
        input_path=args.input,
        capacity_report_path=args.capacity_report,
        split_plan_path=args.split_plan,
        target_rows=args.target_rows,
        max_per_doc=args.max_per_doc,
        max_per_doc_category=args.max_per_doc_category,
        date_label=args.date_label,
        max_image_pixels=args.max_image_pixels,
    )
    outputs = [args.output_jsonl, args.held_output_jsonl, args.report_json, args.report_md]
    outputs = [path if path.is_absolute() else root / path for path in outputs]
    write_jsonl(outputs[0], selected)
    write_jsonl(outputs[1], held)
    write_json(outputs[2], report)
    outputs[3].parent.mkdir(parents=True, exist_ok=True)
    outputs[3].write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "selected_rows": report["selected_rows"],
        "held_rows": report["held_rows"],
        "selected_docs": report["selected_docs"],
        "selected_splits": report["selected_splits"],
        "valid": report["valid"],
    }, indent=2))
    return 1 if args.strict and not report["valid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
