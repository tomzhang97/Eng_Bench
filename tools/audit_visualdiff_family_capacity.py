#!/usr/bin/env python3
"""Measure distinct VisualDiff family capacity across staged review cohorts."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_v2_0_gate import visualdiff_family


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def families_for_rows(rows: list[dict[str, Any]]) -> set[str]:
    return {
        visualdiff_family(pair_id)
        for row in rows
        if (pair_id := str(row.get("pair_id") or row.get("id") or "").strip())
    }


def build_report(
    root: Path,
    cohorts: list[tuple[str, Path]],
    target_families: int = 30,
    date_label: str = "manual",
) -> dict[str, Any]:
    root = root.resolve()
    active_path = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    active_rows = read_rows(active_path)
    active_families = families_for_rows(active_rows)
    cohort_results: list[dict[str, Any]] = []
    cohort_sets: dict[str, set[str]] = {}

    for name, path in cohorts:
        absolute = path if path.is_absolute() else root / path
        rows = read_rows(absolute)
        families = families_for_rows(rows)
        new_families = families - active_families
        cohort_sets[name] = new_families
        cohort_results.append(
            {
                "name": name,
                "path": absolute.relative_to(root).as_posix()
                if absolute.is_relative_to(root)
                else str(absolute),
                "rows": len(rows),
                "families": len(families),
                "new_families": len(new_families),
                "active_family_overlaps": len(families & active_families),
                "new_family_ids": sorted(new_families),
            }
        )

    pairwise_overlaps: list[dict[str, Any]] = []
    names = [name for name, _path in cohorts]
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            overlap = cohort_sets[left] & cohort_sets[right]
            pairwise_overlaps.append(
                {
                    "left": left,
                    "right": right,
                    "overlap_families": len(overlap),
                    "family_ids": sorted(overlap),
                }
            )

    union_new = set().union(*cohort_sets.values()) if cohort_sets else set()
    missing_to_target = max(0, target_families - len(active_families))
    totals = {
        "active_gold_rows": len(active_rows),
        "active_gold_families": len(active_families),
        "target_families": target_families,
        "missing_families_to_target": missing_to_target,
        "distinct_staged_new_families": len(union_new),
        "staged_family_buffer_above_gap": len(union_new) - missing_to_target,
        "projected_families_if_every_staged_family_is_accepted": len(active_families | union_new),
    }
    return {
        "date_label": date_label,
        "totals": totals,
        "cohorts": cohort_results,
        "pairwise_overlaps": pairwise_overlaps,
        "distinct_staged_new_family_ids": sorted(union_new),
        "interpretation": (
            "Capacity only. A staged family counts toward gold only after at least one row is human-accepted "
            "and passes strict promotion, provenance, split, leakage, and duplicate gates."
        ),
    }


def write_outputs(report: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    totals = report["totals"]
    lines = [
        "# VisualDiff Staged Family Capacity",
        "",
        f"- Date: `{report['date_label']}`",
        f"- Active gold families: {totals['active_gold_families']}/{totals['target_families']}",
        f"- Missing families to target: {totals['missing_families_to_target']}",
        f"- Distinct staged new families: {totals['distinct_staged_new_families']}",
        f"- Staged buffer above the current gap: {totals['staged_family_buffer_above_gap']}",
        f"- Theoretical total if every staged family is accepted: {totals['projected_families_if_every_staged_family_is_accepted']}",
        "",
        "| Cohort | Rows | Families | New | Active Overlap |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in report["cohorts"]:
        lines.append(
            f"| `{row['name']}` | {row['rows']} | {row['families']} | "
            f"{row['new_families']} | {row['active_family_overlaps']} |"
        )
    lines.extend(["", "## Cohort Overlap", ""])
    for row in report["pairwise_overlaps"]:
        lines.append(
            f"- `{row['left']}` vs `{row['right']}`: {row['overlap_families']} family overlaps."
        )
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")


def parse_cohort(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("cohort must use NAME=PATH")
    name, path = value.split("=", 1)
    if not name.strip() or not path.strip():
        raise argparse.ArgumentTypeError("cohort must use non-empty NAME=PATH")
    return name.strip(), Path(path.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort", action="append", type=parse_cohort, required=True)
    parser.add_argument("--target-families", type=int, default=30)
    parser.add_argument("--date-label", default="manual")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_report(root, args.cohort, args.target_families, args.date_label)
    json_path = args.output_json if args.output_json.is_absolute() else root / args.output_json
    md_path = args.output_md if args.output_md.is_absolute() else root / args.output_md
    write_outputs(report, json_path, md_path)
    print(json.dumps(report["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
