#!/usr/bin/env python3
"""Prioritize distinct VisualDiff families inside an existing assignment.

This is a planning overlay only. It never edits the assignment or promotes rows.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_v2_0_gate import visualdiff_family


TODO = "CHANGE_DESC_GT_TODO"
CANONICAL_CHANGE_TYPES = {
    "addition",
    "deletion",
    "layout",
    "symbol",
    "text",
    "value",
}
NONDECISION_HUMAN_STATUSES = {
    "",
    "awaiting_review",
    "needs_review",
    "pending",
    "unassigned",
}
ENRICHMENT_FIELDS = {
    "alignment_h_path",
    "alignment_status",
    "change_desc_gt",
    "change_type",
    "confidence",
    "description",
    "description_source",
    "human_description",
    "human_review_notes",
    "human_review_status",
    "human_status",
    "machine_audit",
    "machine_curation_status",
    "machine_qa_status",
    "machine_visual_qa_notes",
    "machine_visual_qa_status",
    "new_doc_id",
    "new_text",
    "notes",
    "old_doc_id",
    "old_text",
    "review_confidence",
    "reviewed_by",
    "source_public_status",
    "source_rights_check",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pair_id(row: dict[str, Any]) -> str:
    return str(row.get("pair_id") or row.get("id") or row.get("record_id") or "").strip()


def usable_description(row: dict[str, Any]) -> str:
    value = str(
        row.get("human_description")
        or row.get("description")
        or row.get("change_desc_gt")
        or ""
    ).strip()
    return "" if value == TODO else value


def confidence_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return {"high": 1.0, "medium": 0.5, "low": 0.0}.get(
            str(value or "").strip().lower(), 0.0
        )


def row_evidence_exists(root: Path, row: dict[str, Any]) -> bool:
    for field in ("image_old", "image_new"):
        value = str(row.get(field) or "").strip()
        if not value or not (root / value).is_file():
            return False
    for field in ("bbox_old", "bbox_new"):
        bbox = row.get(field)
        if not isinstance(bbox, list) or len(bbox) != 4:
            return False
        try:
            x0, y0, x1, y1 = (float(value) for value in bbox)
        except (TypeError, ValueError):
            return False
        if x1 <= x0 or y1 <= y0:
            return False
    return True


def review_richness(row: dict[str, Any], path: Path) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []
    if usable_description(row):
        score += 12.0
        reasons.append("description")
    old_text = str(row.get("old_text") or "").strip()
    new_text = str(row.get("new_text") or "").strip()
    if (old_text or new_text) and old_text != new_text:
        score += 8.0
        reasons.append("text_delta")
    if str(row.get("machine_visual_qa_status") or "").lower() in {
        "selected_for_human_review",
        "pass",
        "passed",
    }:
        score += 5.0
        reasons.append("visual_qa")
    if "pass" in str(row.get("machine_qa_status") or "").lower():
        score += 4.0
        reasons.append("queue_qa")
    if str(row.get("change_type") or "").strip().lower() in CANONICAL_CHANGE_TYPES:
        score += 3.0
        reasons.append("canonical_type")
    if str(row.get("machine_curation_status") or "").lower() in {"keep", "accepted"}:
        score += 3.0
        reasons.append("machine_keep")
    if "curated" in path.name.lower():
        score += 2.0
        reasons.append("curated_queue")
    score += min(1.0, confidence_value(row.get("confidence")))
    return score, ",".join(reasons)


def best_review_rows(root: Path) -> dict[str, tuple[float, str, dict[str, Any]]]:
    best: dict[str, tuple[float, str, dict[str, Any]]] = {}
    queue_root = root / "visualdiff" / "annotations"
    for path in sorted(queue_root.glob("visualdiff_review*.jsonl")):
        for row in read_jsonl(path):
            identifier = pair_id(row)
            if not identifier:
                continue
            score, reasons = review_richness(row, path)
            previous = best.get(identifier)
            if previous is None or score > previous[0]:
                enriched = dict(row)
                enriched["priority_enrichment_queue"] = path.relative_to(root).as_posix()
                best[identifier] = (score, reasons, enriched)
    return best


def active_families(root: Path) -> set[str]:
    return {
        visualdiff_family(identifier)
        for row in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
        if (identifier := pair_id(row))
    }


def merge_enrichment(
    assignment_row: dict[str, Any],
    enrichment: tuple[float, str, dict[str, Any]] | None,
) -> dict[str, Any]:
    merged = dict(assignment_row)
    if enrichment is None:
        merged["priority_enrichment_score"] = 0.0
        merged["priority_enrichment_reasons"] = ""
        return merged
    score, reasons, review_row = enrichment
    for field in ENRICHMENT_FIELDS:
        if field in review_row and review_row[field] not in (None, "", []):
            merged[field] = review_row[field]
    merged["priority_enrichment_score"] = round(score, 6)
    merged["priority_enrichment_reasons"] = reasons
    merged["priority_enrichment_queue"] = review_row.get("priority_enrichment_queue", "")
    return merged


def priority_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    score = float(row.get("priority_enrichment_score") or 0.0)
    reasons: list[str] = []
    if usable_description(row):
        score += 12.0
        reasons.append("usable_description")
    old_text = str(row.get("old_text") or "").strip()
    new_text = str(row.get("new_text") or "").strip()
    if (old_text or new_text) and old_text != new_text:
        score += 8.0
        reasons.append("visible_text_delta")
    change_type = str(row.get("change_type") or "").strip().lower()
    if change_type in CANONICAL_CHANGE_TYPES:
        score += 4.0
        reasons.append("canonical_change_type")
    machine_audit = row.get("machine_audit") if isinstance(row.get("machine_audit"), dict) else {}
    if machine_audit.get("pixel_exact_match") is False:
        score += 3.0
        reasons.append("nonidentical_machine_crop")
    for field in ("normalized_changed_pixel_ratio_gt16", "changed_pixel_ratio_gt16"):
        try:
            delta = float(machine_audit.get(field) or 0.0)
        except (TypeError, ValueError):
            delta = 0.0
        if delta:
            score += min(3.0, delta * 15.0)
            reasons.append("measured_pixel_delta")
            break
    if str(row.get("assignment_workbook") or "").strip():
        score += 10.0
        reasons.append("already_materialized_workbook")
    if str(row.get("reserved_split") or "").strip() in {"train", "dev", "test"}:
        score += 2.0
        reasons.append("split_reserved")
    if str(row.get("source_candidate_id") or "").strip():
        score += 1.0
        reasons.append("registered_source_candidate")
    return round(score, 6), reasons


def family_diversity_key(row: dict[str, Any]) -> str:
    return str(row.get("source_candidate_id") or row.get("project_id") or "unknown").strip()


def has_substantive_human_decision(row: dict[str, Any]) -> bool:
    if str(row.get("human_description") or "").strip():
        return True
    if str(row.get("reviewed_by") or "").strip():
        return True
    for field in ("human_review_status", "human_status"):
        value = str(row.get(field) or "").strip().lower()
        if value not in NONDECISION_HUMAN_STATUSES:
            return True
    return False


def select_priority_overlay(
    root: Path,
    assignment_path: Path,
    *,
    target_new_families: int = 23,
    rows_per_family: int = 2,
    max_families_per_source_candidate: int = 3,
    date_label: str = "manual",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    assignment_path = assignment_path if assignment_path.is_absolute() else root / assignment_path
    assignment_rows = read_jsonl(assignment_path)
    gold_families = active_families(root)
    enrichment = best_review_rows(root)
    counters: Counter[str] = Counter()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for assignment_row in assignment_rows:
        counters["assignment_rows"] += 1
        if str(assignment_row.get("task") or "").strip().lower() != "visualdiff":
            counters["excluded_non_visualdiff"] += 1
            continue
        counters["visualdiff_rows"] += 1
        identifier = pair_id(assignment_row)
        family = visualdiff_family(identifier) if identifier else ""
        if not identifier or not family:
            counters["excluded_missing_identity"] += 1
            continue
        if family in gold_families:
            counters["excluded_active_gold_family"] += 1
            continue
        merged = merge_enrichment(assignment_row, enrichment.get(identifier))
        if has_substantive_human_decision(merged):
            counters["excluded_existing_human_decision"] += 1
            continue
        if not row_evidence_exists(root, merged):
            counters["excluded_missing_evidence"] += 1
            continue
        machine_audit = merged.get("machine_audit") if isinstance(merged.get("machine_audit"), dict) else {}
        if machine_audit.get("pixel_exact_match") is True:
            counters["excluded_pixel_identical"] += 1
            continue
        score, reasons = priority_score(merged)
        merged["visualdiff_family_id"] = family
        merged["machine_priority_score"] = score
        merged["machine_priority_reasons"] = reasons
        merged["priority_overlay_only"] = True
        merged["safe_to_merge_gold"] = False
        grouped[family].append(merged)

    family_entries: list[dict[str, Any]] = []
    for family, rows in grouped.items():
        ranked = sorted(
            rows,
            key=lambda row: (-float(row["machine_priority_score"]), pair_id(row)),
        )
        source_key = family_diversity_key(ranked[0])
        delivered = any(str(row.get("assignment_workbook") or "").strip() for row in ranked)
        family_score = float(ranked[0]["machine_priority_score"]) + min(2.0, math.log2(len(ranked) + 1))
        if delivered:
            family_score += 10.0
        family_entries.append(
            {
                "family": family,
                "source_key": source_key,
                "delivered": delivered,
                "family_score": round(family_score, 6),
                "rows": ranked,
            }
        )
    # A family already present in a delivered workbook is cheaper for humans to
    # act on, so keep that constraint ahead of semantic richness in the sort.
    family_entries.sort(
        key=lambda item: (
            -int(item["delivered"]),
            -item["family_score"],
            item["family"],
        )
    )

    selected_families: list[dict[str, Any]] = []
    selected_names: set[str] = set()
    source_counts: Counter[str] = Counter()
    cap = max(1, max_families_per_source_candidate)
    for source_cap in range(1, cap + 1):
        for entry in family_entries:
            if len(selected_families) >= target_new_families:
                break
            if entry["family"] in selected_names:
                continue
            if source_counts[entry["source_key"]] >= source_cap:
                continue
            selected_families.append(entry)
            selected_names.add(entry["family"])
            source_counts[entry["source_key"]] += 1
    if len(selected_families) < target_new_families:
        for entry in family_entries:
            if len(selected_families) >= target_new_families:
                break
            if entry["family"] in selected_names:
                continue
            selected_families.append(entry)
            selected_names.add(entry["family"])
            source_counts[entry["source_key"]] += 1

    selected_rows: list[dict[str, Any]] = []
    family_summaries: list[dict[str, Any]] = []
    split_counts: Counter[str] = Counter()
    workbook_rows = 0
    for family_rank, entry in enumerate(selected_families, start=1):
        chosen = entry["rows"][: max(1, rows_per_family)]
        for row_rank, row in enumerate(chosen, start=1):
            selected = dict(row)
            selected["family_priority_rank"] = family_rank
            selected["row_priority_within_family"] = row_rank
            selected["family_rows_available"] = len(entry["rows"])
            selected["priority_overlay_date_label"] = date_label
            selected_rows.append(selected)
            split_counts[str(selected.get("reserved_split") or "unassigned")] += 1
            if str(selected.get("assignment_workbook") or "").strip():
                workbook_rows += 1
        family_summaries.append(
            {
                "priority": family_rank,
                "family": entry["family"],
                "source_candidate_id": entry["source_key"],
                "available_rows": len(entry["rows"]),
                "selected_rows": len(chosen),
                "family_score": entry["family_score"],
                "already_materialized_workbook": entry["delivered"],
            }
        )

    issues: list[str] = []
    required = min(target_new_families, len(family_entries))
    if len(selected_families) != required:
        issues.append(
            f"selected {len(selected_families)} families but expected {required} from available capacity"
        )
    if len({pair_id(row) for row in selected_rows}) != len(selected_rows):
        issues.append("selected rows contain duplicate pair IDs")
    if len({row["visualdiff_family_id"] for row in selected_rows}) != len(selected_families):
        issues.append("selected family count does not match row family identities")

    report = {
        "date_label": date_label,
        "assignment_path": assignment_path.relative_to(root).as_posix()
        if assignment_path.is_relative_to(root)
        else str(assignment_path),
        "active_gold_families": len(gold_families),
        "target_family_gate": 30,
        "families_needed_to_gate": max(0, 30 - len(gold_families)),
        "available_new_assignment_families": len(family_entries),
        "selected_new_families": len(selected_families),
        "selected_rows": len(selected_rows),
        "rows_per_family": rows_per_family,
        "projected_family_upper_bound_if_one_per_selected_family_is_accepted": len(gold_families)
        + len(selected_families),
        "selected_rows_already_in_materialized_workbook": workbook_rows,
        "selected_source_candidates": len(source_counts),
        "source_candidate_family_counts": dict(sorted(source_counts.items())),
        "reserved_split_counts": dict(sorted(split_counts.items())),
        "counters": dict(sorted(counters.items())),
        "families": family_summaries,
        "issues": issues,
        "valid": not issues,
        "active_gold_modified": False,
        "interpretation": (
            "This is a priority overlay on an existing assignment. It creates no new human rows, "
            "does not duplicate an assignment, and promotes nothing. Reviewing at least one strong "
            "row per selected family first is the shortest human path to the 30-family gate."
        ),
    }
    return selected_rows, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# VisualDiff Assignment Family Priority Overlay",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Active Gold families: `{report['active_gold_families']}/30`",
        f"- Available new families in assignment: `{report['available_new_assignment_families']}`",
        f"- Selected priority families: `{report['selected_new_families']}`",
        f"- Selected priority rows: `{report['selected_rows']}`",
        f"- Projected family upper bound: `{report['projected_family_upper_bound_if_one_per_selected_family_is_accepted']}/30`",
        f"- Rows already present in a materialized workbook: `{report['selected_rows_already_in_materialized_workbook']}`",
        f"- Source candidates represented: `{report['selected_source_candidates']}`",
        "- Active Gold modified: `false`",
        "",
        "| Priority | Family | Source | Available | Selected | Workbook Materialized |",
        "| ---: | --- | --- | ---: | ---: | --- |",
    ]
    for row in report["families"]:
        lines.append(
            f"| {row['priority']} | `{row['family']}` | `{row['source_candidate_id']}` | "
            f"{row['available_rows']} | {row['selected_rows']} | "
            f"`{str(row['already_materialized_workbook']).lower()}` |"
        )
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    if report["issues"]:
        lines.extend(["## Issues", "", *[f"- {issue}" for issue in report["issues"]], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--target-new-families", type=int, default=23)
    parser.add_argument("--rows-per-family", type=int, default=2)
    parser.add_argument("--max-families-per-source-candidate", type=int, default=3)
    parser.add_argument("--date-label", default="manual")
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    rows, report = select_priority_overlay(
        root,
        args.assignment,
        target_new_families=max(0, args.target_new_families),
        rows_per_family=max(1, args.rows_per_family),
        max_families_per_source_candidate=max(1, args.max_families_per_source_candidate),
        date_label=args.date_label,
    )
    output_jsonl = args.output_jsonl if args.output_jsonl.is_absolute() else root / args.output_jsonl
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
    write_jsonl(output_jsonl, rows)
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "valid": report["valid"],
                "available_new_assignment_families": report["available_new_assignment_families"],
                "selected_new_families": report["selected_new_families"],
                "selected_rows": report["selected_rows"],
                "projected_family_upper_bound": report[
                    "projected_family_upper_bound_if_one_per_selected_family_is_accepted"
                ],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
