#!/usr/bin/env python3
"""Apply a validated staged split plan to an unreviewed JSONL queue."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from plan_staged_splits import task_for_row, visualdiff_project_id


VALID_SPLITS = {"train", "dev", "test"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_identity(row: dict[str, Any]) -> str:
    for key in ("pair_id", "candidate_id", "item_id", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def split_unit(row: dict[str, Any]) -> tuple[str, str]:
    task = task_for_row(row)
    if task == "visualdiff":
        return task, visualdiff_project_id(row)
    return task, str(row.get("doc_id") or row.get("source_doc_id") or "").strip()


def reservation_map(plan: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    if not plan.get("valid", False):
        raise ValueError("staged split plan is not valid")
    reservations: dict[tuple[str, str], dict[str, Any]] = {}
    for row in plan.get("reservations") or []:
        task = str(row.get("task") or "").strip().lower()
        unit_id = str(row.get("unit_id") or "").strip()
        split = str(row.get("split") or "").strip().lower()
        if not task or not unit_id or split not in VALID_SPLITS:
            raise ValueError(f"invalid split reservation: {row}")
        key = (task, unit_id)
        existing = reservations.get(key)
        if existing and existing["split"] != split:
            raise ValueError(
                f"conflicting split reservations for {task}:{unit_id}: "
                f"{existing['split']} vs {split}"
            )
        reservations[key] = row
    return reservations


def apply_reservations(
    rows: list[dict[str, Any]],
    plan: dict[str, Any],
    *,
    plan_path: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reservations = reservation_map(plan)
    output: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    split_counts: Counter[str] = Counter()
    task_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for row_number, row in enumerate(rows, start=1):
        identity = row_identity(row)
        task, unit_id = split_unit(row)
        if not identity:
            issues.append({"row": row_number, "type": "missing_identity"})
            continue
        if identity in seen_ids:
            issues.append({"row": row_number, "type": "duplicate_identity", "id": identity})
            continue
        seen_ids.add(identity)
        if not unit_id:
            issues.append({"row": row_number, "type": "missing_split_unit", "id": identity})
            continue
        reservation = reservations.get((task, unit_id))
        if reservation is None:
            issues.append(
                {
                    "row": row_number,
                    "type": "missing_split_reservation",
                    "id": identity,
                    "task": task,
                    "unit_id": unit_id,
                }
            )
            continue
        reserved_split = str(reservation["split"])
        existing_split = str(row.get("reserved_split") or "").strip().lower()
        if existing_split in VALID_SPLITS and existing_split != reserved_split:
            issues.append(
                {
                    "row": row_number,
                    "type": "existing_split_conflict",
                    "id": identity,
                    "existing": existing_split,
                    "planned": reserved_split,
                }
            )
            continue
        prepared = dict(row)
        prepared["reserved_split"] = reserved_split
        prepared["split_reservation_id"] = str(reservation.get("reservation_id") or "")
        prepared["split_reservation_basis"] = str(reservation.get("assignment_basis") or "")
        prepared["split_reservation_plan"] = plan_path.replace("\\", "/")
        prepared["safe_to_merge_gold"] = False
        output.append(prepared)
        split_counts[reserved_split] += 1
        task_counts[task] += 1

    report = {
        "goal": "Gold v2.0 Global",
        "plan": plan_path.replace("\\", "/"),
        "input_rows": len(rows),
        "output_rows": len(output),
        "unique_output_ids": len(seen_ids),
        "rows_by_split": dict(sorted(split_counts.items())),
        "rows_by_task": dict(sorted(task_counts.items())),
        "issues": issues,
        "active_gold_modified": False,
        "valid": not issues and len(output) == len(rows),
        "interpretation": (
            "Split reservations remain review-only metadata. They do not modify frozen split files "
            "or authorize gold promotion."
        ),
    }
    return output, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Applied Staged Split Reservations",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Input rows: `{report['input_rows']}`",
        f"- Output rows: `{report['output_rows']}`",
        f"- Plan: `{report['plan']}`",
        f"- Active gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "## Reserved Rows",
        "",
    ]
    for split, count in report["rows_by_split"].items():
        lines.append(f"- `{split}`: `{count}`")
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        for issue in report["issues"]:
            lines.append(f"- `{json.dumps(issue, ensure_ascii=False, sort_keys=True)}`")
    lines.extend(["", report["interpretation"], ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    input_path = resolve(args.input)
    plan_path = resolve(args.plan)
    output_path = resolve(args.output)
    report_json = resolve(args.report_json)
    report_md = resolve(args.report_md)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    output, report = apply_reservations(
        read_jsonl(input_path),
        plan,
        plan_path=(
            plan_path.relative_to(root).as_posix()
            if plan_path.is_relative_to(root)
            else plan_path.as_posix()
        ),
    )
    report["input_path"] = (
        input_path.relative_to(root).as_posix()
        if input_path.is_relative_to(root)
        else input_path.as_posix()
    )
    report["input_sha256"] = file_sha256(input_path)
    if report["valid"]:
        write_jsonl(output_path, output)
        report["output_path"] = (
            output_path.relative_to(root).as_posix()
            if output_path.is_relative_to(root)
            else output_path.as_posix()
        )
        report["output_sha256"] = file_sha256(output_path)
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "input_rows": report["input_rows"],
                "output_rows": report["output_rows"],
                "rows_by_split": report["rows_by_split"],
                "issues": len(report["issues"]),
                "valid": report["valid"],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
