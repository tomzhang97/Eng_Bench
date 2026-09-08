#!/usr/bin/env python3
"""Validate Eng_Bench leaderboard submission JSONL format without labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ANSWER_FIELDS = ("answer", "answer_text", "change_desc_gt", "text_gt")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: each JSONL row must be an object")
            rows.append(row)
    return rows


def row_id(row: dict[str, Any]) -> str:
    value = row.get("id") or row.get("question_id") or row.get("qid")
    return str(value) if value is not None else ""


def has_answer(row: dict[str, Any]) -> bool:
    return any(field in row and row.get(field) is not None for field in ANSWER_FIELDS)


def validate_bbox(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return "evidence entries must be objects"
    bbox = entry.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return "evidence bbox must be a four-number list"
    try:
        values = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return "evidence bbox values must be numeric"
    if values[2] <= values[0] or values[3] <= values[1]:
        return "evidence bbox must have positive width and height"
    if "image_index" in entry:
        try:
            int(entry["image_index"])
        except (TypeError, ValueError):
            return "evidence image_index must be an integer"
    return None


def validate_submission(
    inputs: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    allow_missing: bool = False,
) -> dict[str, Any]:
    expected_ids = [row_id(row) for row in inputs]
    expected_set = {identifier for identifier in expected_ids if identifier}
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    unknown_ids: set[str] = set()

    for index, row in enumerate(predictions, start=1):
        identifier = row_id(row)
        if not identifier:
            errors.append(f"prediction row {index}: missing id/question_id/qid")
            continue
        if identifier in seen:
            duplicate_ids.add(identifier)
        seen.add(identifier)
        if identifier not in expected_set:
            unknown_ids.add(identifier)
        if not has_answer(row):
            errors.append(f"{identifier}: missing answer field")
        evidence = row.get("evidence")
        if evidence is None:
            warnings.append(f"{identifier}: no evidence field supplied")
        elif not isinstance(evidence, list):
            errors.append(f"{identifier}: evidence must be a list")
        else:
            for evidence_index, entry in enumerate(evidence):
                bbox_error = validate_bbox(entry)
                if bbox_error:
                    errors.append(f"{identifier}: evidence[{evidence_index}]: {bbox_error}")

    missing_ids = sorted(expected_set - seen)
    if missing_ids and not allow_missing:
        errors.append(f"missing predictions for {len(missing_ids)} input rows")
    if duplicate_ids:
        errors.append(f"duplicate prediction ids: {', '.join(sorted(duplicate_ids)[:10])}")
    if unknown_ids:
        errors.append(f"unknown prediction ids: {', '.join(sorted(unknown_ids)[:10])}")

    return {
        "passed": not errors,
        "input_rows": len(inputs),
        "prediction_rows": len(predictions),
        "missing_count": len(missing_ids),
        "duplicate_count": len(duplicate_ids),
        "unknown_count": len(unknown_ids),
        "errors": errors,
        "warnings": warnings[:50],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Eng_Bench prediction JSONL format.")
    parser.add_argument("--inputs", required=True, help="Public input-only JSONL")
    parser.add_argument("--predictions", required=True, help="Prediction JSONL")
    parser.add_argument("--allow-missing", action="store_true", help="Permit partial prediction files")
    parser.add_argument("--report-json", help="Optional JSON report output")
    args = parser.parse_args(argv)

    report = validate_submission(
        load_jsonl(args.inputs),
        load_jsonl(args.predictions),
        allow_missing=args.allow_missing,
    )
    if args.report_json:
        output = Path(args.report_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
