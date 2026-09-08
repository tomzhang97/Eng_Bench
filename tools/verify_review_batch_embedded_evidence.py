#!/usr/bin/env python3
"""Verify exact embedded evidence for generic Eng_Bench review-batch workbooks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from verify_human_audit_embedded_evidence import verify_workbook


TASK_EVIDENCE_FIELDS = {
    "microtext": "crop_path",
    "visualdiff": "panel_path",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def resolve_under(parent: Path, value: str) -> Path:
    parent = parent.resolve()
    resolved = (parent / value).resolve()
    if not resolved.is_relative_to(parent):
        raise ValueError(f"evidence path escapes review pack: {value}")
    return resolved


def verify_batch(batch_dir: Path) -> dict[str, Any]:
    batch_dir = batch_dir.resolve()
    payload_path = batch_dir / "workbook_build_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    reports: list[dict[str, Any]] = []
    issues: list[str] = []

    for task, evidence_field in TASK_EVIDENCE_FIELDS.items():
        task_payload = payload.get(task) or {}
        expected_rows = int(task_payload.get("rows") or 0)
        if expected_rows == 0:
            continue
        manifest_path = Path(str(task_payload.get("manifest") or ""))
        if not manifest_path.is_absolute():
            manifest_path = resolve_under(batch_dir, manifest_path.as_posix())
        manifest_path = manifest_path.resolve()
        rows = read_jsonl(manifest_path)
        if len(rows) != expected_rows:
            issues.append(
                f"{task}: payload expects {expected_rows} rows, manifest has {len(rows)}"
            )
        evidence_rows = []
        for row in rows:
            value = str(row.get(evidence_field) or "").strip()
            if not value:
                evidence_rows.append({"evidence_path": ""})
                continue
            evidence_rows.append(
                {"evidence_path": resolve_under(manifest_path.parent, value)}
            )
        report, workbook_issues = verify_workbook(
            batch_dir / str(task_payload["workbook"]),
            str(task_payload["sheet"]),
            evidence_rows,
        )
        report["task"] = task
        report["manifest"] = manifest_path.as_posix()
        reports.append(report)
        issues.extend(workbook_issues)

    if not reports:
        issues.append("review batch has no non-empty workbook payload")
    return {
        "goal": "Gold v2.0 Global",
        "batch_dir": batch_dir.as_posix(),
        "workbooks": len(reports),
        "assigned_image_rows": sum(report["expected_rows"] for report in reports),
        "embedded_picture_rows": sum(report["picture_rows"] for report in reports),
        "exact_image_matches": sum(report["exact_image_matches"] for report in reports),
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
        "reports": reports,
        "issues": issues,
        "valid": not issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    batch_dir = args.batch_dir if args.batch_dir.is_absolute() else root / args.batch_dir
    report_path = args.report_json if args.report_json.is_absolute() else root / args.report_json
    try:
        report = verify_batch(batch_dir)
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "goal": "Gold v2.0 Global",
            "batch_dir": batch_dir.resolve().as_posix(),
            "safe_to_merge_gold": False,
            "active_gold_modified": False,
            "issues": [str(exc)],
            "valid": False,
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
