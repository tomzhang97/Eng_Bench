#!/usr/bin/env python3
"""Find genuinely unused release-safe local sources without duplicating pair work."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from source_rights import is_release_safe_status
except ModuleNotFoundError:
    from tools.source_rights import is_release_safe_status


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def integer(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def revision_pair_index(root: Path) -> dict[str, list[str]]:
    pairs_by_doc: dict[str, set[str]] = defaultdict(set)
    for path in sorted((root / "visualdiff" / "docs").glob("*/source_bundle.json")):
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for pair in payload.get("pairs") or []:
            if not isinstance(pair, dict):
                continue
            pair_id = str(pair.get("pair_id") or "").strip()
            for key in ("from_doc_id", "to_doc_id"):
                doc_id = str(pair.get(key) or "").strip()
                if doc_id and pair_id:
                    pairs_by_doc[doc_id].add(pair_id)
    return {doc_id: sorted(pair_ids) for doc_id, pair_ids in sorted(pairs_by_doc.items())}


def classify(row: dict[str, Any], paired_docs: dict[str, list[str]]) -> tuple[str, list[str]]:
    doc_id = str(row.get("doc_id") or "").strip()
    reasons: list[str] = []
    if not is_release_safe_status(str(row.get("public_status") or "")):
        reasons.append("rights_not_release_safe")
    if bool(row.get("duplicate_payload_alias")):
        reasons.append("duplicate_payload_alias")
    if integer(row, "rendered_pages") <= 0:
        reasons.append("missing_rendered_pages")
    if integer(row, "textlayer_spans") <= 0:
        reasons.append("missing_textlayer")
    if integer(row, "gold_rows") > 0:
        reasons.append("represented_in_active_gold")
    if integer(row, "staged_future_rows") > 0:
        reasons.append("represented_in_canonical_future")
    if integer(row, "open_review_rows") > 0:
        reasons.append("represented_in_open_review")
    elif integer(row, "review_rows") > 0:
        reasons.append("represented_in_review_history")
    if str(row.get("conversion_exhaustion_status") or "").strip():
        reasons.append("machine_conversion_exhausted")
    if str(row.get("task") or "") == "visualdiff" and doc_id in paired_docs:
        reasons.append("represented_by_registered_revision_pair")
    if integer(row, "mineable_candidates") <= 0:
        reasons.append("no_mineable_candidates")
    if reasons:
        return "not_actionable", reasons
    task = str(row.get("task") or "").strip()
    if task == "microtext":
        return "actionable_microtext", []
    if task == "visualdiff":
        return "actionable_visualdiff_unpaired", []
    return "not_actionable", ["unsupported_task"]


def build_report(root: Path, readiness_path: Path, *, date_label: str) -> dict[str, Any]:
    root = root.resolve()
    readiness_path = resolve(root, readiness_path)
    readiness = read_json(readiness_path)
    paired_docs = revision_pair_index(root)
    rows: list[dict[str, Any]] = []
    disposition_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for source in readiness.get("local_sources") or []:
        if not isinstance(source, dict):
            continue
        disposition, reasons = classify(source, paired_docs)
        disposition_counts[disposition] += 1
        reason_counts.update(reasons)
        rows.append(
            {
                "doc_id": str(source.get("doc_id") or ""),
                "task": str(source.get("task") or ""),
                "domain": str(source.get("domain") or ""),
                "public_status": str(source.get("public_status") or ""),
                "source_path": str(source.get("source_path") or ""),
                "rendered_pages": integer(source, "rendered_pages"),
                "textlayer_spans": integer(source, "textlayer_spans"),
                "mineable_candidates": integer(source, "mineable_candidates"),
                "gold_rows": integer(source, "gold_rows"),
                "staged_future_rows": integer(source, "staged_future_rows"),
                "review_rows": integer(source, "review_rows"),
                "open_review_rows": integer(source, "open_review_rows"),
                "conversion_exhaustion_status": str(source.get("conversion_exhaustion_status") or ""),
                "registered_revision_pair_ids": paired_docs.get(str(source.get("doc_id") or ""), []),
                "disposition": disposition,
                "reason_codes": reasons,
            }
        )
    actionable = [row for row in rows if row["disposition"].startswith("actionable_")]
    actionable.sort(key=lambda row: (-row["mineable_candidates"], row["doc_id"]))
    return {
        "schema": "eng_bench_unused_release_safe_source_audit_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_modified": False,
        "readiness_input": readiness_path.resolve().relative_to(root).as_posix(),
        "local_sources_checked": len(rows),
        "registered_revision_pair_docs": len(paired_docs),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "actionable_sources": actionable,
        "actionable_source_count": len(actionable),
        "sources": rows,
        "status": "PASS",
        "interpretation": (
            "Only actionable_sources are safe candidates for a new conversion pass. "
            "Registered revision assets, existing review/future rows, aliases, exhausted sources, "
            "and rights-blocked documents are excluded to prevent duplicate work."
        ),
    }


def write_outputs(output_json: Path, report: dict[str, Any]) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Unused Release-Safe Source Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Local sources checked: `{report['local_sources_checked']}`",
        f"- Registered revision-pair documents: `{report['registered_revision_pair_docs']}`",
        f"- Genuinely actionable sources: `{report['actionable_source_count']}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "## Actionable Sources",
        "",
    ]
    for row in report["actionable_sources"]:
        lines.append(
            f"- `{row['doc_id']}`: {row['disposition']}, "
            f"mineable candidates `{row['mineable_candidates']}`"
        )
    if not report["actionable_sources"]:
        lines.append("- None. Existing local sources are already represented, exhausted, blocked, or unsuitable.")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    output_json.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--readiness", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    report = build_report(root, Path(args.readiness), date_label=args.date_label)
    output = resolve(root.resolve(), args.output_json)
    write_outputs(output, report)
    print(json.dumps({key: report[key] for key in ("status", "local_sources_checked", "registered_revision_pair_docs", "actionable_source_count", "disposition_counts")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
