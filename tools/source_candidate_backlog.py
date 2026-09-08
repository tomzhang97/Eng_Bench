#!/usr/bin/env python3
"""Summarize and rank Eng_Bench source expansion candidates."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import Any


RIGHTS_SCORE = {
    "public_candidate": 3,
    "internal_only_candidate": 2,
    "rights_uncertain": 1,
}
YIELD_SCORE = {"high": 3, "medium": 2, "low": 1}
REVISION_SCORE = {"high": 3, "medium": 2, "low": 1}
TASK_SCORE = {
    "visualdiff,microtext": 3,
    "microtext,visualdiff": 3,
    "visualdiff": 2,
    "microtext": 2,
}


def load_candidates(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def normalize(value: str | None) -> str:
    return (value or "").strip()


def candidate_score(row: dict[str, str]) -> int:
    rights = RIGHTS_SCORE.get(normalize(row.get("rights_tier")), 0)
    yield_score = YIELD_SCORE.get(normalize(row.get("annotation_yield")), 0)
    revision = REVISION_SCORE.get(normalize(row.get("revision_family_potential")), 0)
    task = TASK_SCORE.get(normalize(row.get("task_fit")).lower(), 0)
    source_bonus = 1 if normalize(row.get("source_url")) else 0
    return rights + yield_score + revision + task + source_bonus


def rank_candidates(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in rows:
        enriched: dict[str, Any] = dict(row)
        enriched["priority_score"] = candidate_score(row)
        ranked.append(enriched)
    ranked.sort(
        key=lambda row: (
            -int(row["priority_score"]),
            normalize(row.get("domain")),
            normalize(row.get("candidate_id")),
        )
    )
    return ranked


def summarize_candidates(rows: list[dict[str, str]]) -> dict[str, Any]:
    by_domain = Counter(normalize(row.get("domain")) or "unknown" for row in rows)
    by_rights = Counter(normalize(row.get("rights_tier")) or "unknown" for row in rows)
    by_task = Counter(normalize(row.get("task_fit")) or "unknown" for row in rows)
    return {
        "total": len(rows),
        "by_domain": dict(sorted(by_domain.items())),
        "by_rights": dict(sorted(by_rights.items())),
        "by_task": dict(sorted(by_task.items())),
    }


def summarize_validation(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {}
    for row in rows:
        action = normalize(row.get("next_action")) or "unspecified"
        candidate_id = normalize(row.get("candidate_id"))
        if not candidate_id:
            continue
        summary.setdefault(action, []).append(candidate_id)
    for candidate_ids in summary.values():
        candidate_ids.sort()
    return dict(sorted(summary.items()))


def markdown_report(
    rows: list[dict[str, str]],
    *,
    validation_rows: list[dict[str, str]] | None = None,
) -> str:
    summary = summarize_candidates(rows)
    ranked = rank_candidates(rows)
    lines = [
        "# Source Candidate Backlog",
        "",
        "## Summary",
        f"- Total candidates: `{summary['total']}`",
        "",
        "## By Domain",
    ]
    lines.extend(f"- {domain}: {count}" for domain, count in summary["by_domain"].items())
    lines.extend(["", "## By Rights Tier"])
    lines.extend(f"- {tier}: {count}" for tier, count in summary["by_rights"].items())
    lines.extend(["", "## By Task Fit"])
    lines.extend(f"- {task}: {count}" for task, count in summary["by_task"].items())
    lines.extend(["", "## Highest-Priority Intake Candidates"])
    for row in ranked[:15]:
        lines.append(
            f"- `{row.get('candidate_id', '')}` | {row.get('domain', '')} | "
            f"{row.get('rights_tier', '')} | score `{row.get('priority_score', 0)}`"
        )
    validation_summary = summarize_validation(validation_rows or [])
    if validation_summary:
        lines.extend(["", "## Browser-Validated First Tranche"])
        for action, candidate_ids in validation_summary.items():
            joined = ", ".join(f"`{candidate_id}`" for candidate_id in candidate_ids)
            lines.append(f"- {action}: {joined}")
        lines.extend(
            [
                "- Validation ledger: `SOURCE_CANDIDATE_VALIDATION.csv`",
                "- Narrative readout: `SOURCE_CANDIDATE_VALIDATION.md`",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def write_ranked_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Eng_Bench source candidates")
    parser.add_argument("--input", default="SOURCE_CANDIDATES.csv")
    parser.add_argument("--validation", default="SOURCE_CANDIDATE_VALIDATION.csv")
    parser.add_argument("--report", default="SOURCE_CANDIDATE_BACKLOG.md")
    parser.add_argument("--ranked", default="SOURCE_CANDIDATES_RANKED.csv")
    args = parser.parse_args()

    rows = load_candidates(Path(args.input))
    validation_rows = load_candidates(Path(args.validation))
    ranked = rank_candidates(rows)
    Path(args.report).write_text(
        markdown_report(rows, validation_rows=validation_rows),
        encoding="utf-8",
    )
    write_ranked_csv(Path(args.ranked), ranked)

    summary = summarize_candidates(rows)
    print(f"[OK] Summarized {summary['total']} source candidates")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
