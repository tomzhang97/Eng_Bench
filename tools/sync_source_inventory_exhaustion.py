#!/usr/bin/env python3
"""Synchronize source-conversion exhaustion state into SOURCE_INVENTORY.csv."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

try:
    from .source_rights import is_release_safe_status
except ImportError:  # Direct script execution adds tools/ to sys.path.
    from source_rights import is_release_safe_status


ALLOWED_STATUSES = {
    "machine_exhausted_after_reviewed_pass",
    "machine_exhausted_no_candidate",
}
SYNC_FIELDS = (
    "conversion_exhaustion_status",
    "conversion_exhaustion_evidence",
    "next_step",
    "priority_score",
)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def duplicate_values(rows: list[dict[str, str]], field: str) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def synchronize_inventory_rows(
    inventory_rows: list[dict[str, str]],
    exhaustion_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    inventory_duplicates = duplicate_values(inventory_rows, "doc_id")
    exhaustion_duplicates = duplicate_values(exhaustion_rows, "doc_id")
    invalid_status_rows = sorted(
        str(row.get("doc_id") or "")
        for row in exhaustion_rows
        if str(row.get("status") or "").strip() not in ALLOWED_STATUSES
    )

    inventory_doc_ids = {
        str(row.get("doc_id") or "").strip()
        for row in inventory_rows
        if str(row.get("doc_id") or "").strip()
    }
    exhaustion_by_doc = {
        str(row.get("doc_id") or "").strip(): row
        for row in exhaustion_rows
        if str(row.get("doc_id") or "").strip()
    }
    missing_inventory_doc_ids = sorted(set(exhaustion_by_doc) - inventory_doc_ids)
    # Multiple local assets may intentionally share one manifest doc_id. Apply
    # the same document-level exhaustion state to every matching inventory row.
    fatal_issues = bool(
        exhaustion_duplicates or invalid_status_rows or missing_inventory_doc_ids
    )

    synchronized_rows: list[dict[str, str]] = []
    drifted_doc_ids: list[str] = []
    if not fatal_issues:
        for source_row in inventory_rows:
            row = dict(source_row)
            doc_id = str(row.get("doc_id") or "").strip()
            exhaustion = exhaustion_by_doc.get(doc_id)
            if exhaustion:
                exhaustion_status = str(exhaustion["status"]).strip()
                next_step = (
                    exhaustion_status
                    if is_release_safe_status(str(row.get("public_status") or ""))
                    else "rights_review_or_hold"
                )
                expected = {
                    "conversion_exhaustion_status": exhaustion_status,
                    "conversion_exhaustion_evidence": str(
                        exhaustion.get("evidence_path") or ""
                    ).strip(),
                    "next_step": next_step,
                    "priority_score": "-100",
                }
                if any(str(row.get(key) or "") != value for key, value in expected.items()):
                    drifted_doc_ids.append(doc_id)
                row.update(expected)
            synchronized_rows.append(row)
    else:
        synchronized_rows = [dict(row) for row in inventory_rows]

    report: dict[str, Any] = {
        "valid": not fatal_issues,
        "inventory_rows": len(inventory_rows),
        "exhaustion_rows": len(exhaustion_rows),
        "matched_exhaustion_doc_ids": len(set(exhaustion_by_doc) & inventory_doc_ids),
        "matched_inventory_rows": sum(
            1
            for row in inventory_rows
            if str(row.get("doc_id") or "").strip() in exhaustion_by_doc
        ),
        "drifted_rows": len(drifted_doc_ids),
        "drifted_doc_ids": drifted_doc_ids,
        "inventory_duplicate_doc_ids": inventory_duplicates,
        "exhaustion_duplicate_doc_ids": exhaustion_duplicates,
        "invalid_status_doc_ids": invalid_status_rows,
        "missing_inventory_doc_ids": missing_inventory_doc_ids,
    }
    return synchronized_rows, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize the exhaustion ledger into SOURCE_INVENTORY.csv."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--inventory", default="SOURCE_INVENTORY.csv")
    parser.add_argument("--exhaustion", default="SOURCE_CONVERSION_EXHAUSTION.csv")
    parser.add_argument("--report-json")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write synchronized fields to the inventory. Without this flag, check only.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    inventory_path = root / args.inventory
    exhaustion_path = root / args.exhaustion
    fieldnames, inventory_rows = read_csv(inventory_path)
    _, exhaustion_rows = read_csv(exhaustion_path)
    synchronized_rows, report = synchronize_inventory_rows(
        inventory_rows, exhaustion_rows
    )
    report.update(
        {
            "inventory_path": str(inventory_path.relative_to(root).as_posix()),
            "exhaustion_path": str(exhaustion_path.relative_to(root).as_posix()),
            "apply_requested": args.apply,
            "inventory_updated": bool(args.apply and report["valid"]),
        }
    )

    if args.apply and report["valid"]:
        for field in SYNC_FIELDS:
            if field not in fieldnames:
                fieldnames.append(field)
        write_csv(inventory_path, fieldnames, synchronized_rows)

    if args.report_json:
        report_path = root / args.report_json
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["valid"]:
        return 2
    if not args.apply and report["drifted_rows"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
