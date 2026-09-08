#!/usr/bin/env python3
"""Partition VisualDiff family candidates by authoritative human-review state."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


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
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_identity(row: dict[str, Any]) -> str:
    for field in ("pair_id", "record_id", "candidate_id", "id"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _index(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(rows, start=1):
        identity = row_identity(row)
        if not identity:
            raise ValueError(f"{label}:{row_number}: missing row identity")
        if identity in index:
            raise ValueError(f"{label}: duplicate identity: {identity}")
        index[identity] = row
    return index


def reconcile(
    candidates: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
    assigned: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    candidate_index = _index(candidates, "candidates")
    reviewed_index = _index(reviewed, "reviewed")
    assigned_index = _index(assigned, "assigned")
    partitions: dict[str, list[dict[str, Any]]] = {
        "reviewed": [],
        "currently_assigned": [],
        "unassigned": [],
    }
    families: Counter[str] = Counter()

    for identity, source in candidate_index.items():
        row = dict(source)
        family = str(row.get("project_id") or row.get("visualdiff_family_id") or "")
        families[family or "unknown"] += 1
        if identity in reviewed_index:
            state = "reviewed"
            reference = reviewed_index[identity]
            reason = "identity_present_in_reviewed_rows"
        elif identity in assigned_index:
            state = "currently_assigned"
            reference = assigned_index[identity]
            reason = "identity_present_in_current_assignment"
        else:
            state = "unassigned"
            reference = {}
            reason = "identity_absent_from_reviewed_and_current_assignment"
        row["assignment_reconciliation"] = {
            "state": state,
            "reason": reason,
            "reference_review_status": str(
                reference.get("primary_reviewer_status")
                or reference.get("review_status")
                or reference.get("current_assignment_status")
                or ""
            ),
            "reference_primary_index": reference.get("primary_index"),
        }
        partitions[state].append(row)

    partition_ids = {
        name: {row_identity(row) for row in rows}
        for name, rows in partitions.items()
    }
    pairwise_overlap = {
        "reviewed_vs_assigned": len(
            partition_ids["reviewed"] & partition_ids["currently_assigned"]
        ),
        "reviewed_vs_unassigned": len(
            partition_ids["reviewed"] & partition_ids["unassigned"]
        ),
        "assigned_vs_unassigned": len(
            partition_ids["currently_assigned"] & partition_ids["unassigned"]
        ),
    }
    accounted_ids = set().union(*partition_ids.values())
    report = {
        "input_candidates": len(candidates),
        "reviewed_reference_rows": len(reviewed),
        "current_assignment_reference_rows": len(assigned),
        "counts": {name: len(rows) for name, rows in partitions.items()},
        "candidate_families": len(families),
        "families": dict(sorted(families.items())),
        "pairwise_overlap": pairwise_overlap,
        "accounted_candidates": len(accounted_ids),
        "all_candidates_accounted": len(accounted_ids) == len(candidate_index),
        "partitions_disjoint": not any(pairwise_overlap.values()),
        "safe_to_issue_unassigned_only": (
            len(accounted_ids) == len(candidate_index) and not any(pairwise_overlap.values())
        ),
        "policy": {
            "precedence": ["reviewed", "currently_assigned", "unassigned"],
            "human_handoff": "Only the unassigned partition may enter a new handoff.",
            "gold_promotion": "No partition is promoted by this reconciliation.",
        },
    }
    return partitions, report


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    return "\n".join(
        [
            "# VisualDiff Family Assignment Reconciliation",
            "",
            f"- Candidate rows: `{report['input_candidates']}`",
            f"- Already reviewed: `{counts['reviewed']}`",
            f"- Currently assigned: `{counts['currently_assigned']}`",
            f"- Genuinely unassigned: `{counts['unassigned']}`",
            f"- Distinct candidate families: `{report['candidate_families']}`",
            f"- All candidates accounted: `{report['all_candidates_accounted']}`",
            f"- Partitions disjoint: `{report['partitions_disjoint']}`",
            "",
            "Only the unassigned partition may be used for a new human handoff. This tool does not modify Gold data.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--reviewed", required=True)
    parser.add_argument("--assigned", required=True)
    parser.add_argument("--output-prefix", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    resolve = lambda value: Path(value) if Path(value).is_absolute() else root / value
    partitions, report = reconcile(
        read_jsonl(resolve(args.candidates)),
        read_jsonl(resolve(args.reviewed)),
        read_jsonl(resolve(args.assigned)),
    )
    prefix = resolve(args.output_prefix)
    write_jsonl(prefix.with_name(prefix.name + "_reviewed.jsonl"), partitions["reviewed"])
    write_jsonl(
        prefix.with_name(prefix.name + "_currently_assigned.jsonl"),
        partitions["currently_assigned"],
    )
    write_jsonl(prefix.with_name(prefix.name + "_unassigned.jsonl"), partitions["unassigned"])
    prefix.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["safe_to_issue_unassigned_only"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
