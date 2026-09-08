#!/usr/bin/env python3
"""Verify that every rights-blocked active source has a current evidence decision."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .apply_source_rights_resolution import build_resolution, sha256_file
    from .audit_active_gold_provenance import build_report as build_provenance_report
except ImportError:  # pragma: no cover - direct script execution
    from apply_source_rights_resolution import build_resolution, sha256_file
    from audit_active_gold_provenance import build_report as build_provenance_report


def display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def audit_coverage(root: Path, evidence_csv: Path, date_label: str) -> dict[str, Any]:
    root = root.resolve()
    evidence_csv = evidence_csv if evidence_csv.is_absolute() else root / evidence_csv
    provenance = build_provenance_report(root, date_label)
    resolution, _manifest, _inventory, _fields = build_resolution(root, evidence_csv)

    blocked = {
        str(row["doc_id"]): row
        for row in provenance["documents"]
        if str(row.get("blocker") or "").strip()
    }
    decisions = {
        str(row.get("doc_id") or "").strip(): row
        for row in resolution["decisions"]
        if str(row.get("doc_id") or "").strip()
    }
    issues = list(resolution["issues"])
    missing = sorted(set(blocked) - set(decisions))
    stale = sorted(set(decisions) - set(blocked))
    issues.extend(f"missing blocked-source decision: {doc_id}" for doc_id in missing)
    issues.extend(f"stale nonblocked-source decision: {doc_id}" for doc_id in stale)

    rows: list[dict[str, Any]] = []
    for doc_id in sorted(blocked):
        source = blocked[doc_id]
        decision = decisions.get(doc_id, {})
        row_issues = list(decision.get("issues") or [])
        decision_value = str(decision.get("decision") or "").strip()
        evidence_path = str(decision.get("evidence_path") or "").strip()
        terms_url = str(decision.get("terms_url") or "").strip()
        expected_sha = str(decision.get("expected_sha256") or "").strip().lower()
        computed_sha = str(source.get("computed_sha256") or "").strip().lower()
        if decision_value not in {"hold", "release_safe"}:
            row_issues.append("decision must be hold or release_safe")
        if not terms_url.startswith("https://"):
            row_issues.append("authoritative HTTPS terms_url missing")
        if not evidence_path:
            row_issues.append("stored evidence_path missing")
        if not expected_sha:
            row_issues.append("expected_sha256 missing")
        elif expected_sha != computed_sha:
            row_issues.append("evidence SHA-256 does not match active source")
        if decision_value == "release_safe":
            row_issues.append("release-safe decision is not yet applied to the active source")
        issues.extend(f"{doc_id}: {issue}" for issue in row_issues)
        rows.append(
            {
                "doc_id": doc_id,
                "active_row_references": int(source.get("active_row_references") or 0),
                "blocker": str(source.get("blocker") or ""),
                "decision": decision_value,
                "terms_url": terms_url,
                "evidence_path": evidence_path,
                "expected_sha256": expected_sha,
                "computed_sha256": computed_sha,
                "issues": row_issues,
            }
        )

    covered = sum(bool(row["decision"]) for row in rows)
    holds = sum(row["decision"] == "hold" for row in rows)
    active_rows_covered = sum(
        row["active_row_references"] for row in rows if row["decision"]
    )
    active_rows_blocked = sum(row["active_row_references"] for row in rows)
    return {
        "schema": "eng_bench_active_rights_resolution_coverage_v1",
        "date_label": date_label,
        "status": "PASS" if not issues else "FAIL",
        "coverage_complete": not issues and covered == len(blocked),
        "provenance_release_ready": bool(provenance["paper_ready_provenance_complete"]),
        "active_gold_modified": False,
        "inputs": {
            "evidence_csv": display_path(root, evidence_csv),
            "evidence_csv_sha256": sha256_file(evidence_csv),
        },
        "totals": {
            "active_gold_rows": provenance["totals"]["active_gold_rows"],
            "active_source_docs": provenance["totals"]["active_source_docs"],
            "paper_ready_docs": provenance["totals"]["paper_ready_docs"],
            "blocked_active_docs": len(blocked),
            "covered_blocked_docs": covered,
            "hold_decisions": holds,
            "active_row_references_blocked": active_rows_blocked,
            "active_row_references_covered": active_rows_covered,
            "missing_decisions": len(missing),
            "stale_decisions": len(stale),
        },
        "missing_doc_ids": missing,
        "stale_doc_ids": stale,
        "rows": rows,
        "issues": sorted(set(issues)),
        "interpretation": (
            "PASS means every currently rights-blocked active source has a hash-bound "
            "evidence decision. It does not make held sources release-ready."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Active Rights Resolution Coverage",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Status: `{report['status']}`",
        f"- Coverage complete: `{str(report['coverage_complete']).lower()}`",
        f"- Provenance release-ready: `{str(report['provenance_release_ready']).lower()}`",
        f"- Blocked active documents covered: `{totals['covered_blocked_docs']}/{totals['blocked_active_docs']}`",
        f"- Blocked active row references covered: `{totals['active_row_references_covered']}/{totals['active_row_references_blocked']}`",
        f"- Hold decisions: `{totals['hold_decisions']}`",
        "",
        "PASS here means every blocked source has a current, hash-bound decision. "
        "It does not clear the Gold provenance gate.",
        "",
        "## Decisions",
        "",
        "| Document | Active references | Decision | Blocker | Evidence |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| `{row['doc_id']}` | {row['active_row_references']} | "
            f"`{row['decision'] or 'missing'}` | `{row['blocker']}` | "
            f"`{row['evidence_path'] or 'missing'}` |"
        )
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--evidence-csv", type=Path, required=True)
    parser.add_argument("--date-label", default="2026-09-09-wave2306")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    report = audit_coverage(root, args.evidence_csv, args.date_label)
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["totals"]}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
