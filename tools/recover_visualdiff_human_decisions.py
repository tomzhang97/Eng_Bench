#!/usr/bin/env python3
"""Recover prior VisualDiff human decisions from an existing assignment.

The output is an audit cohort only. It never edits active Gold or changes the
authoritative assignment.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from prioritize_visualdiff_assignment_families import (
    best_review_rows,
    has_substantive_human_decision,
    merge_enrichment,
    pair_id,
    read_jsonl,
    review_richness,
    row_evidence_exists,
)
from visualdiff_merge import normalized_change_type, review_status


TODO = "CHANGE_DESC_GT_TODO"
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
FINAL_STATUSES = {"accepted", "edited", "valid", "edit"}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def active_pair_ids(root: Path) -> set[str]:
    return {
        pair_id(row)
        for row in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
        if pair_id(row)
    }


def best_human_review_rows(root: Path) -> dict[str, tuple[float, str, dict[str, Any]]]:
    best: dict[str, tuple[float, str, dict[str, Any]]] = {}
    queue_root = root / "visualdiff" / "annotations"
    for path in sorted(queue_root.glob("visualdiff_review*.jsonl")):
        for row in read_jsonl(path):
            identifier = pair_id(row)
            if not identifier or not has_substantive_human_decision(row):
                continue
            base_score, reasons = review_richness(row, path)
            status = review_status(row)
            score = base_score
            if str(row.get("human_description") or "").strip():
                score += 20.0
            if status in FINAL_STATUSES:
                score += 10.0
            if str(row.get("reviewed_by") or "").strip():
                score += 2.0
            previous = best.get(identifier)
            if previous is None or score > previous[0]:
                selected = dict(row)
                selected["priority_enrichment_queue"] = path.relative_to(root).as_posix()
                best[identifier] = (score, reasons, selected)
    return best


def recovery_flags(root: Path, row: dict[str, Any], active_ids: set[str]) -> list[str]:
    flags: list[str] = []
    identifier = pair_id(row)
    status = review_status(row)
    human_description = str(row.get("human_description") or "").strip()
    machine_description = str(row.get("description") or "").strip()
    if identifier in active_ids:
        flags.append("already_active_gold")
    if not row_evidence_exists(root, row):
        flags.append("missing_or_invalid_evidence")
    if status not in FINAL_STATUSES:
        flags.append(f"nonfinal_human_status:{status or 'blank'}")
    if not human_description:
        flags.append("missing_human_description")
    elif CJK_RE.search(human_description):
        flags.append("needs_english_localization")
    if not machine_description or machine_description == TODO:
        flags.append("missing_machine_description")
    if "unknown" in normalized_change_type(row):
        flags.append("ambiguous_change_type")
    return flags


def recover_decisions(
    root: Path,
    assignment_path: Path,
    *,
    date_label: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    assignment_path = assignment_path if assignment_path.is_absolute() else root / assignment_path
    assignment_rows = read_jsonl(assignment_path)
    general_enrichment = best_review_rows(root)
    human_enrichment = best_human_review_rows(root)
    active_ids = active_pair_ids(root)
    recovered: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    seen: set[str] = set()
    issues: list[str] = []

    for assignment_row in assignment_rows:
        counters["assignment_rows"] += 1
        if str(assignment_row.get("task") or "").strip().lower() != "visualdiff":
            continue
        counters["visualdiff_rows"] += 1
        identifier = pair_id(assignment_row)
        assignment_has_decision = has_substantive_human_decision(assignment_row)
        selected_human = human_enrichment.get(identifier)
        if not assignment_has_decision and selected_human is None:
            continue
        merged = merge_enrichment(assignment_row, general_enrichment.get(identifier))
        if selected_human is not None:
            selected_row = selected_human[2]
            for field in (
                "human_description",
                "human_review_notes",
                "human_review_status",
                "human_status",
                "reviewed_by",
            ):
                if selected_row.get(field) not in (None, "", []):
                    merged[field] = selected_row[field]
        for field in (
            "human_description",
            "human_review_notes",
            "human_review_status",
            "human_status",
            "reviewed_by",
        ):
            if assignment_row.get(field) not in (None, "", []):
                merged[field] = assignment_row[field]
        counters["substantive_human_decision_rows"] += 1
        if not identifier:
            issues.append("missing_pair_id")
            continue
        if identifier in seen:
            issues.append(f"duplicate_pair_id:{identifier}")
            continue
        seen.add(identifier)
        flags = recovery_flags(root, merged, active_ids)
        flag_counts.update(flags)
        row = dict(merged)
        row["recovery_date_label"] = date_label
        row["recovery_source_queue"] = str(
            (selected_human[2].get("priority_enrichment_queue") if selected_human else "")
            or row.get("priority_enrichment_queue")
            or "assignment_embedded_decision"
        )
        row["recovery_flags"] = flags
        row["recovery_status"] = "needs_machine_visual_audit"
        row["safe_to_merge_gold"] = False
        recovered.append(row)

    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "mode": "read_only_recovery_audit",
        "assignment": assignment_path.relative_to(root).as_posix(),
        "counts": dict(sorted(counters.items())),
        "recovered_rows": len(recovered),
        "source_review_queues": len(
            {str(row.get("recovery_source_queue") or "") for row in recovered}
        ),
        "reserved_splits": dict(
            sorted(Counter(str(row.get("reserved_split") or "") for row in recovered).items())
        ),
        "source_candidates": dict(
            sorted(Counter(str(row.get("source_candidate_id") or "") for row in recovered).items())
        ),
        "flag_counts": dict(sorted(flag_counts.items())),
        "active_gold_rows_modified": 0,
        "safe_to_merge_gold_rows": 0,
        "issues": issues,
        "valid": not issues,
        "interpretation": (
            "Recovered human decisions require machine visual reconciliation and strict preview. "
            "No recovered row is Gold or ready for automatic apply."
        ),
    }
    return recovered, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# VisualDiff Existing Human Decision Recovery",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Valid extraction: `{str(report['valid']).lower()}`",
        f"- Recovered rows: `{report['recovered_rows']}`",
        f"- Source review queues: `{report['source_review_queues']}`",
        f"- Active Gold rows modified: `{report['active_gold_rows_modified']}`",
        f"- Safe-to-merge-Gold rows: `{report['safe_to_merge_gold_rows']}`",
        "",
        "## Recovery Flags",
        "",
    ]
    if report["flag_counts"]:
        lines.extend(
            f"- `{name}`: `{count}`" for name, count in report["flag_counts"].items()
        )
    else:
        lines.append("- none")
    lines.extend(["", report["interpretation"], ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    rows, report = recover_decisions(
        root,
        args.assignment,
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
                "recovered_rows": report["recovered_rows"],
                "flag_counts": report["flag_counts"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
