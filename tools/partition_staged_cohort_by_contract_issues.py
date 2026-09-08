#!/usr/bin/env python3
"""Partition staged rows using fatal findings from a promotion-contract audit."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_staged_v2_capacity as staged


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def fatal_findings(path: Path) -> dict[tuple[str, str], list[str]]:
    findings: dict[tuple[str, str], list[str]] = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("severity") or "").strip().lower() != "fatal":
                continue
            task = str(row.get("task") or "").strip().lower()
            identity = str(row.get("identity") or "").strip()
            issue = str(row.get("issue") or "").strip()
            if task and identity and issue and issue not in findings[(task, identity)]:
                findings[(task, identity)].append(issue)
    return findings


def partition_rows(
    rows: list[dict[str, Any]],
    findings: dict[tuple[str, str], list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    ready: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for source_row in rows:
        row = dict(source_row)
        task = staged.task_for_row(row)
        identity = staged.capacity_identity(row)
        issues = findings.get((task, identity), [])
        if issues:
            row["promotion_contract_status"] = "held"
            row["promotion_contract_hold_reasons"] = list(issues)
            row["safe_to_merge_gold"] = False
            held.append(row)
            reasons.update(issues)
        else:
            row["promotion_contract_status"] = "ready_for_strict_preview"
            row["safe_to_merge_gold"] = False
            ready.append(row)
    return ready, held, reasons


def parse_cohort(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("cohort must use NAME=PATH")
    name, raw_path = value.split("=", 1)
    if not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("cohort must use non-empty NAME=PATH")
    return name.strip(), Path(raw_path.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort", action="append", type=parse_cohort, required=True)
    parser.add_argument("--issues-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    issues_path = resolve(args.issues_csv)
    output_dir = resolve(args.output_dir)
    findings = fatal_findings(issues_path)

    report_cohorts: list[dict[str, Any]] = []
    total_input = total_ready = total_held = 0
    all_reasons: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    valid = True

    for name, raw_path in args.cohort:
        input_path = resolve(raw_path)
        rows = staged.read_rows(input_path)
        ready, held, reasons = partition_rows(rows, findings)
        task_counts = Counter(staged.task_for_row(row) for row in rows)
        if len(task_counts) != 1:
            raise SystemExit(f"cohort {name!r} must contain exactly one task; found {dict(task_counts)}")
        task = next(iter(task_counts))
        ready_path = output_dir / f"{name}_ready.jsonl"
        held_path = output_dir / f"{name}_held.jsonl"
        write_jsonl(ready_path, ready)
        write_jsonl(held_path, held)

        for row in ready:
            key = (task, staged.capacity_identity(row))
            if not key[1] or key in seen:
                valid = False
            seen.add(key)
        if len(ready) + len(held) != len(rows):
            valid = False

        total_input += len(rows)
        total_ready += len(ready)
        total_held += len(held)
        all_reasons.update(reasons)
        report_cohorts.append(
            {
                "name": name,
                "task": task,
                "input": input_path.relative_to(root).as_posix(),
                "input_sha256": sha256(input_path),
                "input_rows": len(rows),
                "ready": ready_path.relative_to(root).as_posix(),
                "ready_sha256": sha256(ready_path),
                "ready_rows": len(ready),
                "held": held_path.relative_to(root).as_posix(),
                "held_sha256": sha256(held_path),
                "held_rows": len(held),
            }
        )

    report = {
        "goal": "Gold v2.0 Global",
        "issues_csv": issues_path.relative_to(root).as_posix(),
        "issues_csv_sha256": sha256(issues_path),
        "cohorts": report_cohorts,
        "counts": {
            "input_rows": total_input,
            "ready_rows": total_ready,
            "held_rows": total_held,
            "hold_reasons": dict(sorted(all_reasons.items())),
        },
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "valid": valid and total_input == total_ready + total_held,
        "interpretation": (
            "Ready rows passed this contract-issue partition only. They still require "
            "the strict read-only promotion preview before any Gold mutation."
        ),
    }
    report_json = resolve(args.report_json)
    report_md = resolve(args.report_md)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Promotion Contract Partition",
        "",
        f"- Input rows: `{total_input}`",
        f"- Ready for strict preview: `{total_ready}`",
        f"- Held: `{total_held}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        "- Active Gold modified: `false`",
        "",
        "## Hold Reasons",
        "",
    ]
    lines.extend(f"- `{reason}`: `{count}`" for reason, count in sorted(all_reasons.items()))
    lines.extend(["", report["interpretation"], ""])
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report["counts"], indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
