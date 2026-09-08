#!/usr/bin/env python3
"""Build an actionable v1.0 source-expansion queue."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from build_source_quotas import load_csv, quota_domain, summarize_quotas


CANDIDATE_ID_RE = re.compile(r"`([a-z]+_\d+)`")


def imported_candidate_ids(intake_log: str) -> set[str]:
    imported: set[str] = set()
    for line in intake_log.splitlines():
        if "Candidate" in line or "candidate" in line:
            imported.update(CANDIDATE_ID_RE.findall(line))
    return imported


def validation_by_candidate(validations: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("candidate_id", ""): row for row in validations if row.get("candidate_id")}


def quota_need_by_domain(quotas: list[dict[str, int | str]]) -> dict[str, int]:
    return {str(row["domain"]): int(row["remaining_to_v1_0"]) for row in quotas}


def numeric_score(row: dict[str, str]) -> int:
    try:
        return int(float(row.get("priority_score") or 0))
    except ValueError:
        return 0


def rank_candidate(
    row: dict[str, str],
    validation: dict[str, str] | None,
    domain_need: int,
) -> tuple[int, str]:
    score = numeric_score(row) + domain_need * 2
    reasons = []
    if validation:
        if validation.get("next_action") == "intake_first":
            score += 6
            if validation.get("browser_result", "").startswith("web_triaged"):
                reasons.append("web_triaged_intake_first")
            else:
                reasons.append("browser_validated_intake_first")
        if validation.get("release_posture") == "release_candidate":
            score += 3
            reasons.append("release_candidate")
    if row.get("rights_tier") == "public_candidate":
        score += 2
        reasons.append("public_candidate")
    if "visualdiff" in row.get("task_fit", "") and "microtext" in row.get("task_fit", ""):
        score += 2
        reasons.append("dual_task_fit")
    if not reasons:
        reasons.append("ranked_backlog")
    return score, ", ".join(reasons)


def build_queue(
    candidates: list[dict[str, str]],
    validations: list[dict[str, str]],
    quotas: list[dict[str, int | str]],
    imported_ids: set[str],
    limit: int = 25,
) -> list[dict[str, str]]:
    validations_by_id = validation_by_candidate(validations)
    needs = quota_need_by_domain(quotas)
    queue = []
    for row in candidates:
        candidate_id = row.get("candidate_id", "")
        if not candidate_id or candidate_id in imported_ids:
            continue
        validation = validations_by_id.get(candidate_id, {})
        if validation.get("next_action") in {"deprioritize", "prototype_only"}:
            continue
        rights = row.get("rights_tier", "").lower()
        release_posture = validation.get("release_posture", "")
        if "internal_only" in rights or "restricted" in rights or "proprietary" in rights:
            continue
        if "public_candidate" not in rights and release_posture != "release_candidate":
            continue
        domain = row.get("domain", "")
        domain_need = needs.get(quota_domain(domain), needs.get(domain, 0))
        score, reason = rank_candidate(row, validation, domain_need)
        queue.append(
            {
                "candidate_id": candidate_id,
                "domain": domain,
                "task_fit": row.get("task_fit", ""),
                "rights_tier": row.get("rights_tier", ""),
                "next_action": validation.get("next_action", ""),
                "release_posture": validation.get("release_posture", ""),
                "domain_need_v1_0": str(domain_need),
                "queue_score": str(score),
                "reason": reason,
                "source_url": row.get("source_url", ""),
                "notes": row.get("notes", ""),
            }
        )
    return sorted(queue, key=lambda item: int(item["queue_score"]), reverse=True)[:limit]


def render_markdown(queue: list[dict[str, str]]) -> str:
    lines = [
        "# Eng_Bench v1.0 Expansion Queue",
        "",
        "Prioritized from source candidates, browser/web-validation ledger, already imported candidate IDs, and current v1.0 source-domain quotas.",
        "",
        "| Rank | Candidate | Domain | Task Fit | Score | Next Action | Reason |",
        "| ---: | --- | --- | --- | ---: | --- | --- |",
    ]
    for rank, row in enumerate(queue, start=1):
        lines.append(
            f"| {rank} | `{row['candidate_id']}` | {row['domain']} | {row['task_fit']} | "
            f"{row['queue_score']} | {row['next_action'] or 'backlog'} | {row['reason']} |"
        )
    lines.extend(
        [
            "",
            "## Next Handling",
            "",
            "- Import only release-safe candidates first.",
            "- Render pages and extract text layers before candidate mining.",
            "- Keep each source family in exactly one split.",
            "- Use crop/contact-sheet review before promoting any rows into gold JSONL.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id",
        "domain",
        "task_fit",
        "rights_tier",
        "next_action",
        "release_posture",
        "domain_need_v1_0",
        "queue_score",
        "reason",
        "source_url",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build v1.0 source-expansion queue.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--output-md", default="docs/V1_EXPANSION_QUEUE.md")
    parser.add_argument("--output-csv", default="docs/V1_EXPANSION_QUEUE.csv")
    parser.add_argument("--output-json", default="docs/V1_EXPANSION_QUEUE.json")
    args = parser.parse_args(argv)

    root = Path(args.root)
    candidates = load_csv(root / "SOURCE_CANDIDATES_RANKED.csv")
    if not candidates:
        candidates = load_csv(root / "SOURCE_CANDIDATES.csv")
    validations = load_csv(root / "SOURCE_CANDIDATE_VALIDATION.csv")
    quotas = summarize_quotas(
        load_csv(root / "SOURCE_INVENTORY.csv"),
        load_csv(root / "SOURCE_CANDIDATES.csv"),
        validations,
    )
    imported = imported_candidate_ids((root / "SOURCE_INTAKE_LOG.md").read_text(encoding="utf-8"))
    queue = build_queue(candidates, validations, quotas, imported, limit=args.limit)

    md_path = root / args.output_md
    csv_path = root / args.output_csv
    json_path = root / args.output_json
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(queue), encoding="utf-8")
    write_csv(csv_path, queue)
    json_path.write_text(json.dumps(queue, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {md_path}")
    print(f"[OK] Wrote {csv_path}")
    print(f"[OK] Wrote {json_path}")
    print(f"[OK] Queue rows: {len(queue)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
