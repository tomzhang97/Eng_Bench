#!/usr/bin/env python3
"""Record a zero-novelty conversion plan as exhausted without editing Gold."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


FIELDS = ["doc_id", "status", "audit_date", "method", "evidence_path", "notes"]
ALLOWED_STATUSES = {
    "machine_exhausted_after_reviewed_pass",
    "machine_exhausted_no_candidate",
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_update(
    root: Path,
    *,
    plan_path: Path,
    novelty_report_path: Path,
    ledger_path: Path,
    snapshot_path: Path,
    status: str,
    audit_date: str,
    method: str,
    notes: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    root = root.resolve()
    plan_path = resolve(root, plan_path).resolve()
    novelty_report_path = resolve(root, novelty_report_path).resolve()
    ledger_path = resolve(root, ledger_path).resolve()
    snapshot_path = resolve(root, snapshot_path).resolve()
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"unsupported exhaustion status: {status}")
    if not plan_path.is_file() or not novelty_report_path.is_file():
        raise ValueError("plan and novelty report must exist")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    novelty = json.loads(novelty_report_path.read_text(encoding="utf-8"))
    novelty_totals = novelty.get("totals") or {}
    novelty_metric = next(
        (
            key
            for key in ("net_new_rows", "kept_rows")
            if key in novelty_totals
        ),
        "",
    )
    if not novelty_metric:
        raise ValueError(
            "novelty report must expose totals.net_new_rows or totals.kept_rows"
        )
    net_new = int(novelty_totals[novelty_metric] or 0)
    if net_new != 0:
        raise ValueError(f"novelty report is not exhausted: net_new_rows={net_new}")
    selected_source_rows = list(plan.get("selected_sources") or [])
    selected_source_rows.extend(plan.get("selected_actions") or [])
    selected_docs = sorted(
        {
            str(row.get("doc_id") or "").strip()
            for row in selected_source_rows
            if str(row.get("doc_id") or "").strip()
        }
    )
    if not selected_docs:
        raise ValueError("plan contains no selected source documents")
    if ledger_path.is_file():
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        if snapshot_path.exists():
            raise ValueError(f"snapshot already exists: {snapshot_path}")
        shutil.copyfile(ledger_path, snapshot_path)
    existing = read_csv(ledger_path)
    by_doc = {
        str(row.get("doc_id") or "").strip(): {field: str(row.get(field) or "") for field in FIELDS}
        for row in existing
        if str(row.get("doc_id") or "").strip()
    }
    evidence = display(root, novelty_report_path)
    inserted = 0
    replaced = 0
    for doc_id in selected_docs:
        if doc_id in by_doc:
            replaced += 1
        else:
            inserted += 1
        by_doc[doc_id] = {
            "doc_id": doc_id,
            "status": status,
            "audit_date": audit_date,
            "method": method,
            "evidence_path": evidence,
            "notes": notes,
        }
    output = [by_doc[doc_id] for doc_id in sorted(by_doc)]
    report = {
        "goal": "Gold v2.0 Global",
        "valid": True,
        "active_gold_modified": False,
        "inputs": {
            "plan": display(root, plan_path),
            "plan_sha256": file_sha256(plan_path),
            "novelty_report": evidence,
            "novelty_report_sha256": file_sha256(novelty_report_path),
            "novelty_metric": f"totals.{novelty_metric}",
            "novelty_net_new_rows": net_new,
        },
        "ledger": {
            "path": display(root, ledger_path),
            "rows_before": len(existing),
            "rows_after": len(output),
            "selected_documents": len(selected_docs),
            "inserted_documents": inserted,
            "replaced_documents": replaced,
            "snapshot": display(root, snapshot_path) if ledger_path.is_file() else "",
        },
        "selected_doc_ids": selected_docs,
        "interpretation": (
            "The selected source conversions produced zero physically novel rows after active, "
            "human-staged, machine-reserved, terminal-review, and near-region exclusion. The "
            "source ledger may suppress the same conversion plan from future ranking."
        ),
    }
    return output, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--novelty-report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=Path("SOURCE_CONVERSION_EXHAUSTION.csv"))
    parser.add_argument("--snapshot-output", type=Path, required=True)
    parser.add_argument("--status", choices=sorted(ALLOWED_STATUSES), required=True)
    parser.add_argument("--audit-date", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    ledger_path = resolve(root, args.ledger)
    rows, report = build_update(
        root,
        plan_path=args.plan,
        novelty_report_path=args.novelty_report,
        ledger_path=ledger_path,
        snapshot_path=args.snapshot_output,
        status=args.status,
        audit_date=args.audit_date,
        method=args.method,
        notes=args.notes,
    )
    write_csv(ledger_path, rows)
    report["ledger"]["sha256_after"] = file_sha256(ledger_path)
    report_path = resolve(root, args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["ledger"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
