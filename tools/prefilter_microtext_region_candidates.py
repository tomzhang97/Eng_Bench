#!/usr/bin/env python3
"""Apply a conservative machine-QA allowlist to raster-region candidates."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_allowlist(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    by_id: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError(f"missing candidate_id at {path}:{row_number}")
        if candidate_id in by_id:
            raise ValueError(f"duplicate candidate_id in allowlist: {candidate_id}")
        proposed_text = str(row.get("proposed_text") or "").strip()
        category = str(row.get("category") or "").strip()
        if not proposed_text or not category:
            raise ValueError(f"allowlisted row lacks proposed_text/category: {candidate_id}")
        by_id[candidate_id] = row
    return by_id


def build_prefilter(
    candidates: list[dict[str, Any]],
    allowlist: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    candidate_ids = {str(row.get("candidate_id") or "") for row in candidates}
    missing = sorted(set(allowlist) - candidate_ids)
    if missing:
        raise ValueError("allowlist IDs missing from candidates: " + ", ".join(missing))

    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for row in candidates:
        candidate_id = str(row.get("candidate_id") or "")
        decision = allowlist.get(candidate_id)
        updated = dict(row)
        if decision is not None:
            updated.update(
                proposed_text=str(decision["proposed_text"]).strip(),
                category=str(decision["category"]).strip(),
                review_status="needs_review",
                machine_qa_status="selected_for_human_review",
                machine_qa_notes=str(decision.get("notes") or "").strip(),
            )
            selected.append(updated)
        else:
            updated.update(
                review_status="machine_held",
                machine_qa_status="held",
                machine_qa_notes=(
                    "Contact-sheet QA: nonbenchmark heading/prose/scale text, isolated number, "
                    "non-text geometry, noise, insufficient engineering-label context, or a "
                    "duplicate of an already staged region."
                ),
            )
            held.append(updated)

    report = {
        "candidate_rows": len(candidates),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "selected_by_category": dict(sorted(Counter(row["category"] for row in selected).items())),
        "selected_by_doc": dict(sorted(Counter(row["doc_id"] for row in selected).items())),
    }
    return selected, held, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Microtext Raster-Region Machine QA",
        "",
        f"- Candidate rows inspected: `{report['candidate_rows']}`",
        f"- Selected for human review: `{report['selected_rows']}`",
        f"- Machine-held: `{report['held_rows']}`",
        "- Gold rows added: `0`",
        "",
        "Selected rows retain `needs_review`; this tool never accepts or promotes annotations.",
        "Held rows remain auditable in a separate JSONL ledger.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--allowlist-csv", type=Path, required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--summary-md", type=Path, required=True)
    args = parser.parse_args()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else args.root / path

    selected, held, report = build_prefilter(
        read_jsonl(resolve(args.input)),
        read_allowlist(resolve(args.allowlist_csv)),
    )
    write_jsonl(resolve(args.selected_output), selected)
    write_jsonl(resolve(args.held_output), held)
    summary_json = resolve(args.summary_json)
    summary_md = resolve(args.summary_md)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_md.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
