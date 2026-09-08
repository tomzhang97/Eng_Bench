#!/usr/bin/env python3
"""Build one de-duplicated future-capacity queue around the active assignment."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_source_conversion_readiness as source_readiness
import audit_staged_v2_capacity as staged_capacity
from audit_active_gold_provenance import file_sha256
from benchmark_utils import normalize_entity_text
from payload_alias_regions import load_payload_alias_map, payload_alias_region_key


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def primary_identity(row: dict[str, Any]) -> str:
    identity = staged_capacity.row_identity(row)
    return identity.split(":", 1)[1] if ":" in identity else identity


def alias_identities(row: dict[str, Any]) -> set[str]:
    task = staged_capacity.task_for_row(row)
    return {
        f"{task}:{alias}"
        for alias in staged_capacity.identity_aliases(row)
    }


def add_geometry(
    geometry_by_page: dict[tuple[str, int], list[tuple[tuple[int, int, int, int], str]]],
    row: dict[str, Any],
    origin: str,
) -> None:
    geometry = staged_capacity.microtext_region_geometry(row)
    if not geometry:
        return
    doc_id, page_index, bbox = geometry
    geometry_by_page[(doc_id, page_index)].append((bbox, origin))


def near_overlap_origin(
    geometry_by_page: dict[tuple[str, int], list[tuple[tuple[int, int, int, int], str]]],
    row: dict[str, Any],
) -> str:
    geometry = staged_capacity.microtext_region_geometry(row)
    if not geometry:
        return ""
    doc_id, page_index, bbox = geometry
    for existing_bbox, origin in geometry_by_page.get((doc_id, page_index), []):
        if (
            bbox[2] <= existing_bbox[0]
            or existing_bbox[2] <= bbox[0]
            or bbox[3] <= existing_bbox[1]
            or existing_bbox[3] <= bbox[1]
        ):
            continue
        if staged_capacity.bboxes_are_near_duplicates(bbox, existing_bbox):
            return origin
    return ""


def payload_alias_geometry(
    root: Path,
    row: dict[str, Any],
    alias_map: dict[str, str],
) -> tuple[tuple[str, int], tuple[int, int, int, int]] | None:
    geometry = staged_capacity.microtext_region_geometry(row)
    if not geometry:
        return None
    doc_id = geometry[0]
    if doc_id not in alias_map:
        return None
    normalized = payload_alias_region_key(
        row,
        geometry,
        root=root,
        alias_map=alias_map,
    )
    if not normalized:
        return None
    canonical_doc_id, page_index, bbox = normalized
    return (canonical_doc_id, page_index), bbox


def add_payload_alias_geometry(
    geometry_by_page: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ],
    root: Path,
    row: dict[str, Any],
    alias_map: dict[str, str],
    origin: str,
) -> bool:
    geometry = payload_alias_geometry(root, row, alias_map)
    if not geometry:
        return False
    page_key, bbox = geometry
    geometry_by_page[page_key].append((bbox, origin))
    return True


def payload_alias_overlap_origin(
    geometry_by_page: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ],
    root: Path,
    row: dict[str, Any],
    alias_map: dict[str, str],
) -> str:
    geometry = payload_alias_geometry(root, row, alias_map)
    if not geometry:
        return ""
    page_key, bbox = geometry
    for existing_bbox, origin in geometry_by_page.get(page_key, []):
        if staged_capacity.bboxes_are_near_duplicates(bbox, existing_bbox):
            return origin
    return ""


def payload_alias_label_key(
    row: dict[str, Any],
    alias_map: dict[str, str],
) -> tuple[str, int, str, str] | None:
    """Return a conservative same-payload label key for microtext rows."""
    geometry = staged_capacity.microtext_region_geometry(row)
    if not geometry:
        return None
    doc_id, page_index, _bbox = geometry
    canonical_doc_id = alias_map.get(doc_id)
    if not canonical_doc_id:
        return None
    text = next(
        (
            str(row.get(field) or "").strip()
            for field in ("corrected_text", "text_gt", "proposed_text", "target_text")
            if str(row.get(field) or "").strip()
        ),
        "",
    )
    category = str(row.get("corrected_category") or row.get("category") or "").strip()
    normalized_text = normalize_entity_text(text)
    normalized_category = category.casefold()
    if not normalized_text or not normalized_category:
        return None
    return canonical_doc_id, page_index, normalized_category, normalized_text


def add_payload_alias_label(
    labels: dict[tuple[str, int, str, str], str],
    row: dict[str, Any],
    alias_map: dict[str, str],
    origin: str,
) -> bool:
    key = payload_alias_label_key(row, alias_map)
    if not key:
        return False
    labels.setdefault(key, origin)
    return True


def payload_alias_label_overlap_origin(
    labels: dict[tuple[str, int, str, str], str],
    row: dict[str, Any],
    alias_map: dict[str, str],
) -> str:
    key = payload_alias_label_key(row, alias_map)
    return labels.get(key, "") if key else ""


def hold_row(
    row: dict[str, Any],
    *,
    cohort_name: str,
    cohort_path: str,
    reason: str,
) -> dict[str, Any]:
    held = dict(row)
    held["canonical_capacity_origin_cohort"] = cohort_name
    held["canonical_capacity_origin_path"] = cohort_path
    held["canonical_capacity_status"] = "excluded"
    held["canonical_capacity_hold_reason"] = reason
    return held


def build_canonical_capacity(
    root: Path,
    *,
    current_paths: list[Path],
    source_report_path: Path,
    preferred_cohorts: list[dict[str, str]] | None = None,
    payload_alias_report_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    source_report_path = resolve_path(root, source_report_path).resolve()
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    payload_alias_map: dict[str, str] = {}
    payload_alias_groups = 0
    resolved_payload_alias_report = ""
    if payload_alias_report_path:
        alias_path = resolve_path(root, payload_alias_report_path).resolve()
        payload_alias_map, payload_alias_groups = load_payload_alias_map(alias_path)
        resolved_payload_alias_report = display_path(root, alias_path)

    current_rows: list[dict[str, Any]] = []
    current_sources: list[dict[str, Any]] = []
    for value in current_paths:
        path = resolve_path(root, value).resolve()
        rows = staged_capacity.read_rows(path)
        current_rows.extend(rows)
        current_sources.append(
            {
                "path": display_path(root, path),
                "rows": len(rows),
                "sha256": file_sha256(path),
            }
        )

    active_rows = staged_capacity.read_rows(
        root / "microtext" / "annotations" / "microtext_items.jsonl"
    ) + staged_capacity.read_rows(
        root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    )
    active_capacity_ids = {
        staged_capacity.capacity_identity(row)
        for row in active_rows
        if staged_capacity.capacity_identity(row)
    }
    active_alias_ids = {
        alias
        for row in active_rows
        for alias in alias_identities(row)
    }
    current_capacity_ids: set[str] = set()
    current_alias_ids: set[str] = set()
    current_missing_identity = 0
    current_duplicate_identities = 0
    current_active_overlaps = 0
    for row in current_rows:
        identity = staged_capacity.capacity_identity(row)
        if not identity:
            current_missing_identity += 1
            continue
        if identity in current_capacity_ids:
            current_duplicate_identities += 1
        if identity in active_capacity_ids:
            current_active_overlaps += 1
        current_capacity_ids.add(identity)
        current_alias_ids.update(alias_identities(row))

    geometry_by_page: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = defaultdict(list)
    for row in active_rows:
        add_geometry(geometry_by_page, row, "active_gold")
    current_near_active_overlaps = 0
    current_near_assignment_overlaps = 0
    for row in current_rows:
        overlap_origin = near_overlap_origin(geometry_by_page, row)
        if overlap_origin == "active_gold":
            current_near_active_overlaps += 1
        elif overlap_origin == "current_assignment":
            current_near_assignment_overlaps += 1
        add_geometry(geometry_by_page, row, "current_assignment")

    payload_alias_geometry_by_page: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = defaultdict(list)
    for row in active_rows:
        add_payload_alias_geometry(
            payload_alias_geometry_by_page,
            root,
            row,
            payload_alias_map,
            "active_gold",
        )
    payload_alias_labels: dict[tuple[str, int, str, str], str] = {}
    for row in active_rows:
        add_payload_alias_label(
            payload_alias_labels,
            row,
            payload_alias_map,
            "active_gold",
        )
    current_payload_alias_active_overlaps = 0
    current_payload_alias_assignment_overlaps = 0
    current_payload_alias_unresolved_regions = 0
    for row in current_rows:
        doc_id = str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
        overlap_origin = payload_alias_overlap_origin(
            payload_alias_geometry_by_page,
            root,
            row,
            payload_alias_map,
        )
        if overlap_origin == "active_gold":
            current_payload_alias_active_overlaps += 1
        elif overlap_origin == "current_assignment":
            current_payload_alias_assignment_overlaps += 1
        added = add_payload_alias_geometry(
            payload_alias_geometry_by_page,
            root,
            row,
            payload_alias_map,
            "current_assignment",
        )
        if doc_id in payload_alias_map and not added:
            current_payload_alias_unresolved_regions += 1
        add_payload_alias_label(
            payload_alias_labels,
            row,
            payload_alias_map,
            "current_assignment",
        )

    terminal_identities = source_readiness.terminal_reviewed_row_keys(root)
    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    selected_capacity_ids: set[str] = set()
    selected_alias_ids: set[str] = set()
    reason_counts: Counter[str] = Counter()
    cohort_summaries: list[dict[str, Any]] = []
    missing_source_paths: list[str] = []

    source_cohorts = list(source_report.get("cohorts", []))
    ordered_cohorts = [*(preferred_cohorts or []), *source_cohorts]
    for cohort in ordered_cohorts:
        cohort_name = str(cohort.get("name") or "").strip()
        cohort_path_value = str(cohort.get("path") or "").strip()
        if not cohort_name or not cohort_path_value:
            missing_source_paths.append(cohort_path_value or "<missing>")
            continue
        cohort_path = resolve_path(root, cohort_path_value)
        if not cohort_path.is_file():
            missing_source_paths.append(cohort_path_value)
            continue
        rows = staged_capacity.read_rows(cohort_path)
        cohort_counts: Counter[str] = Counter(input_rows=len(rows))
        for row in rows:
            reason = ""
            exclusion = staged_capacity.capacity_exclusion_reason(row)
            identity = staged_capacity.capacity_identity(row)
            if exclusion:
                reason = f"non_capacity:{exclusion}"
            elif not identity:
                reason = "missing_capacity_identity"
            elif identity in active_capacity_ids or alias_identities(row) & active_alias_ids:
                reason = "active_gold_overlap"
            elif identity in current_capacity_ids:
                reason = "current_assignment_overlap"
            elif alias_identities(row) & current_alias_ids:
                reason = "current_assignment_alias_overlap"
            elif primary_identity(row) in terminal_identities:
                reason = "terminal_review_overlap"
            elif identity in selected_capacity_ids:
                reason = "duplicate_staged_identity"
            elif alias_identities(row) & selected_alias_ids:
                reason = "duplicate_staged_alias"
            else:
                overlap_origin = near_overlap_origin(geometry_by_page, row)
                if overlap_origin:
                    reason = f"near_{overlap_origin}_region_overlap"
                else:
                    doc_id = str(
                        row.get("doc_id") or row.get("source_doc_id") or ""
                    ).strip()
                    payload_alias_origin = payload_alias_overlap_origin(
                        payload_alias_geometry_by_page,
                        root,
                        row,
                        payload_alias_map,
                    )
                    if payload_alias_origin:
                        reason = f"payload_alias_{payload_alias_origin}_overlap"
                    else:
                        label_origin = payload_alias_label_overlap_origin(
                            payload_alias_labels,
                            row,
                            payload_alias_map,
                        )
                        if label_origin:
                            reason = (
                                f"payload_alias_{label_origin}_text_category_overlap"
                            )
                        elif doc_id in payload_alias_map and not payload_alias_geometry(
                            root, row, payload_alias_map
                        ):
                            reason = "payload_alias_region_unresolved"

            if reason:
                reason_counts[reason] += 1
                cohort_counts[f"held:{reason}"] += 1
                held.append(
                    hold_row(
                        row,
                        cohort_name=cohort_name,
                        cohort_path=cohort_path_value,
                        reason=reason,
                    )
                )
                continue

            canonical = dict(row)
            canonical["canonical_capacity_origin_cohort"] = cohort_name
            canonical["canonical_capacity_origin_phase"] = str(cohort.get("phase") or "")
            canonical["canonical_capacity_origin_path"] = cohort_path_value
            canonical["canonical_capacity_status"] = "future_review_capacity"
            selected.append(canonical)
            selected_capacity_ids.add(identity)
            selected_alias_ids.update(alias_identities(row))
            add_geometry(geometry_by_page, row, "future_capacity")
            add_payload_alias_geometry(
                payload_alias_geometry_by_page,
                root,
                row,
                payload_alias_map,
                "future_capacity",
            )
            add_payload_alias_label(
                payload_alias_labels,
                row,
                payload_alias_map,
                "future_capacity",
            )
            cohort_counts["selected_rows"] += 1

        cohort_summaries.append(
            {
                "name": cohort_name,
                "phase": str(cohort.get("phase") or ""),
                "path": cohort_path_value,
                "input_rows": len(rows),
                "selected_rows": cohort_counts["selected_rows"],
                "held_rows": len(rows) - cohort_counts["selected_rows"],
            }
        )

    selected.sort(
        key=lambda row: (
            staged_capacity.task_for_row(row),
            staged_capacity.capacity_identity(row),
        )
    )
    held.sort(
        key=lambda row: (
            str(row.get("canonical_capacity_hold_reason") or ""),
            staged_capacity.task_for_row(row),
            staged_capacity.capacity_identity(row),
        )
    )
    output_identities = [staged_capacity.capacity_identity(row) for row in selected]
    issues: list[str] = []
    if missing_source_paths:
        issues.append("missing_source_cohort_paths")
    if current_missing_identity:
        issues.append("current_assignment_missing_identity")
    if current_duplicate_identities:
        issues.append("current_assignment_duplicate_identity")
    if current_active_overlaps:
        issues.append("current_assignment_active_gold_overlap")
    if current_near_active_overlaps:
        issues.append("current_assignment_near_active_gold_overlap")
    if current_near_assignment_overlaps:
        issues.append("current_assignment_near_assignment_overlap")
    if current_payload_alias_active_overlaps:
        issues.append("current_assignment_payload_alias_active_gold_overlap")
    if current_payload_alias_assignment_overlaps:
        issues.append("current_assignment_payload_alias_assignment_overlap")
    if current_payload_alias_unresolved_regions:
        issues.append("current_assignment_payload_alias_region_unresolved")
    if len(output_identities) != len(set(output_identities)):
        issues.append("canonical_output_duplicate_identity")

    report = {
        "goal": "Gold v2.0 Global",
        "source_capacity_report": display_path(root, source_report_path),
        "source_capacity_report_sha256": file_sha256(source_report_path),
        "current_sources": current_sources,
        "current_rows": len(current_rows),
        "current_unique_capacity_rows": len(current_capacity_ids),
        "current_missing_identity": current_missing_identity,
        "current_duplicate_identities": current_duplicate_identities,
        "current_active_gold_overlaps": current_active_overlaps,
        "current_near_active_gold_overlaps": current_near_active_overlaps,
        "current_near_assignment_overlaps": current_near_assignment_overlaps,
        "payload_alias_report": resolved_payload_alias_report,
        "payload_alias_groups": payload_alias_groups,
        "current_payload_alias_active_gold_overlaps": current_payload_alias_active_overlaps,
        "current_payload_alias_assignment_overlaps": current_payload_alias_assignment_overlaps,
        "current_payload_alias_unresolved_regions": current_payload_alias_unresolved_regions,
        "source_cohorts": len(cohort_summaries),
        "preferred_cohorts": len(preferred_cohorts or []),
        "source_rows": sum(row["input_rows"] for row in cohort_summaries),
        "canonical_future_rows": len(selected),
        "held_rows": len(held),
        "hold_reasons": dict(sorted(reason_counts.items())),
        "missing_source_paths": sorted(set(missing_source_paths)),
        "cohorts": cohort_summaries,
        "issues": issues,
        "active_gold_modified": False,
        "valid": not issues,
    }
    return selected, held, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Canonical Staged Capacity",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Current primary assignment: `{report['current_rows']}` rows",
        f"- Canonical future review capacity: `{report['canonical_future_rows']}` rows",
        f"- Duplicate, stale, or non-capacity rows held: `{report['held_rows']}`",
        f"- Source cohorts consolidated: `{report['source_cohorts']}`",
        f"- Active gold modified: `{str(report['active_gold_modified']).lower()}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        "",
        "## Held Rows",
        "",
        "| Reason | Rows |",
        "| --- | ---: |",
    ]
    for reason, count in report["hold_reasons"].items():
        lines.append(f"| `{reason}` | {count} |")
    lines.extend(
        [
            "",
            "This registry is review capacity only. Human acceptance and every strict promotion gate remain required before any row may enter active gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--current", type=Path, action="append", required=True)
    parser.add_argument("--source-capacity-report", type=Path, required=True)
    parser.add_argument(
        "--payload-alias-report",
        type=Path,
        help="Source-payload duplicate audit for normalized cross-alias region checks.",
    )
    parser.add_argument(
        "--preferred-cohort",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help=(
            "Optional review-ready cohort processed before report cohorts so its rows win "
            "duplicate resolution. Repeat for multiple cohorts."
        ),
    )
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--hold-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    preferred_cohorts: list[dict[str, str]] = []
    for value in args.preferred_cohort:
        name, separator, path = value.partition("=")
        if not separator or not name.strip() or not path.strip():
            parser.error("--preferred-cohort must use NAME=PATH")
        preferred_cohorts.append(
            {"name": name.strip(), "path": path.strip(), "phase": "future_preferred"}
        )

    root = args.root.resolve()
    selected, held, report = build_canonical_capacity(
        root,
        current_paths=args.current,
        source_report_path=args.source_capacity_report,
        preferred_cohorts=preferred_cohorts,
        payload_alias_report_path=args.payload_alias_report,
    )
    output_path = resolve_path(root, args.output_jsonl)
    hold_path = resolve_path(root, args.hold_output)
    report_json_path = resolve_path(root, args.report_json)
    report_md_path = resolve_path(root, args.report_md)
    write_jsonl_atomic(output_path, selected)
    write_jsonl_atomic(hold_path, held)
    report["output_jsonl"] = display_path(root, output_path)
    report["output_sha256"] = file_sha256(output_path)
    report["hold_output"] = display_path(root, hold_path)
    report["hold_output_sha256"] = file_sha256(hold_path)
    write_json(report_json_path, report)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "current_rows": report["current_rows"],
                "canonical_future_rows": report["canonical_future_rows"],
                "held_rows": report["held_rows"],
                "valid": report["valid"],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
