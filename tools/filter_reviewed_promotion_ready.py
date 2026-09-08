"""Partition completed review rows into promotion-ready and explicit holds."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_staged_v2_capacity as staged
from audit_active_gold_provenance import file_sha256


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def parse_cohort(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("cohort must use NAME=PATH")
    name, path = value.split("=", 1)
    if not name.strip() or not path.strip():
        raise argparse.ArgumentTypeError("cohort must use non-empty NAME=PATH")
    return name.strip(), Path(path.strip())


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        "# Reviewed Promotion Partial-Release Partition",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Input rows: `{counts['input_rows']}`",
        f"- Ready rows: `{counts['ready_rows']}`",
        f"- Held rows: `{counts['held_rows']}`",
        f"- All rows accounted: `{str(report['all_rows_accounted']).lower()}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "## Holds",
        "",
        "| Reason | Rows |",
        "|---|---:|",
    ]
    reasons = report.get("hold_reason_counts") or {}
    if reasons:
        lines.extend(f"| `{reason}` | {count} |" for reason, count in reasons.items())
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Ready rows may enter the normal read-only strict promotion preview. Held rows remain outside Gold until their stated issue is resolved.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def row_aliases(row: dict[str, Any]) -> set[str]:
    return {
        str(row.get(field) or "").strip()
        for field in ("candidate_id", "record_id", "pair_id", "item_id")
        if str(row.get(field) or "").strip()
    }


def fatal_issues(path: Path) -> dict[tuple[str, str], set[str]]:
    issues: dict[tuple[str, str], set[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("severity") or "").strip().lower() != "fatal":
                continue
            key = (str(row.get("cohort") or "").strip(), str(row.get("identity") or "").strip())
            if not all(key):
                raise ValueError(f"fatal contract issue is missing cohort or identity: {row}")
            issues.setdefault(key, set()).add(str(row.get("issue") or "fatal_contract_issue"))
    return issues


def build_partition(
    root: Path,
    cohorts: list[tuple[str, Path]],
    contract_issues_path: Path,
    hold_ids: set[str],
    output_dir: Path,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = resolve(root, output_dir)
    contract_issues_path = resolve(root, contract_issues_path)
    issues = fatal_issues(contract_issues_path)
    unmatched_issues = set(issues)
    ready: dict[str, list[dict[str, Any]]] = {"microtext": [], "visualdiff": []}
    holds: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    cohort_artifacts: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    input_rows = 0

    for cohort_name, raw_path in cohorts:
        path = resolve(root, raw_path)
        cohort_rows = staged.read_rows(path)
        cohort_artifacts.append(
            {
                "name": cohort_name,
                "path": display(root, path),
                "rows": len(cohort_rows),
                "sha256": file_sha256(path),
            }
        )
        for row in cohort_rows:
            input_rows += 1
            task = staged.task_for_row(row)
            if task not in ready:
                raise ValueError(f"unsupported task for {cohort_name}: {task!r}")
            identity = staged.capacity_identity(row)
            if not identity:
                raise ValueError(f"missing capacity identity in {cohort_name}")
            if identity in seen_identities:
                raise ValueError(f"duplicate input capacity identity: {identity}")
            seen_identities.add(identity)
            key = (cohort_name, identity)
            reasons = set(issues.get(key, set()))
            if key in unmatched_issues:
                unmatched_issues.remove(key)
            if row_aliases(row) & hold_ids:
                reasons.add("explicit_historical_review_conflict")
            if reasons:
                sorted_reasons = sorted(reasons)
                reason_counts.update(sorted_reasons)
                holds.append(
                    {
                        "cohort": cohort_name,
                        "task": task,
                        "identity": identity,
                        "hold_reasons": sorted_reasons,
                        "row": row,
                    }
                )
            else:
                ready[task].append(row)

    if unmatched_issues:
        sample = sorted(f"{cohort}:{identity}" for cohort, identity in unmatched_issues)[:10]
        raise ValueError(f"fatal contract issues did not match input rows: {sample}")
    if input_rows != len(ready["microtext"]) + len(ready["visualdiff"]) + len(holds):
        raise AssertionError("input partition accounting failed")

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "ready_microtext": output_dir / "promotion_ready_microtext.jsonl",
        "ready_visualdiff": output_dir / "promotion_ready_visualdiff.jsonl",
        "holds": output_dir / "promotion_holds.jsonl",
        "report_json": output_dir / "partial_release_report.json",
        "report_md": output_dir / "partial_release_report.md",
    }
    write_jsonl(paths["ready_microtext"], ready["microtext"])
    write_jsonl(paths["ready_visualdiff"], ready["visualdiff"])
    write_jsonl(paths["holds"], holds)
    report = {
        "schema": "eng_bench_reviewed_partial_release_partition_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_modified": False,
        "all_rows_accounted": True,
        "cohorts": cohort_artifacts,
        "contract_issues": {
            "path": display(root, contract_issues_path),
            "sha256": file_sha256(contract_issues_path),
        },
        "explicit_hold_ids": sorted(hold_ids),
        "counts": {
            "input_rows": input_rows,
            "ready_rows": len(ready["microtext"]) + len(ready["visualdiff"]),
            "ready_microtext_rows": len(ready["microtext"]),
            "ready_visualdiff_rows": len(ready["visualdiff"]),
            "held_rows": len(holds),
        },
        "hold_reason_counts": dict(sorted(reason_counts.items())),
        "artifacts": {
            name: {
                "path": display(root, path),
                "sha256": file_sha256(path),
            }
            for name, path in paths.items()
            if name not in {"report_json", "report_md"}
        },
        "interpretation": "Promotion-ready rows are individually completed human reviews with fatal contract rows and explicit historical conflicts held out. No Gold file was modified.",
    }
    write_json(paths["report_json"], report)
    write_markdown(paths["report_md"], report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort", action="append", type=parse_cohort, required=True)
    parser.add_argument("--contract-issues", type=Path, required=True)
    parser.add_argument("--hold-id", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    args = parser.parse_args(argv)
    report = build_partition(
        args.root,
        args.cohort,
        args.contract_issues,
        {str(value).strip() for value in args.hold_id if str(value).strip()},
        args.output_dir,
        args.date_label,
    )
    print(json.dumps(report["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
