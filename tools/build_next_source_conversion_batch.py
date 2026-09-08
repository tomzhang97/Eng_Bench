#!/usr/bin/env python3
"""Plan the next safe source-conversion batch for Eng_Bench."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


MACHINE_READY_STEPS = {
    "",
    "import_render_extract",
    "extract_textlayer_or_ocr",
    "mine_candidates_and_export_review",
    "ocr_or_manual_region_proposal",
    "human_review",
    "human_review_partial_packeted",
}
HUMAN_BLOCKED_STEPS = {
    "await_human_return",
    "human_review",
    "human_review_partial_packeted",
}
RIGHTS_BLOCKED_STEPS = {"rights_review_or_hold"}
NONRELEASE_MARKERS = (
    "internal_only",
    "rights_uncertain",
    "restricted",
    "proprietary",
    "reference_only",
)
ACTION_PRIORITY = {
    "intake_first": 60,
    "intake_second": 35,
    "license_capture_then_intake": 25,
}
DOMAIN_PRIORITY = {
    "pid": 15,
    "civil": 12,
    "architectural": 11,
    "mechanical_cad": 10,
    "pcb_schematic": 8,
}

OUTPUT_FIELDS = [
    "rank",
    "candidate_id",
    "domain",
    "task_fit",
    "validation_next_action",
    "recommended_next_step",
    "source_url",
    "source_family",
    "priority_score",
    "planning_score",
    "notes",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fieldnames or OUTPUT_FIELDS
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").strip()))
    except ValueError:
        return default


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def by_candidate(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        str(row.get("candidate_id") or "").strip(): row
        for row in rows
        if str(row.get("candidate_id") or "").strip()
    }


def readiness_queue_path(root: Path) -> Path:
    dated = sorted(
        (root / "derived" / "quality").glob("source_conversion_candidates_*.csv")
    )
    if dated:
        return dated[-1]
    return root / "docs" / "SOURCE_CONVERSION_CANDIDATE_QUEUE.csv"


def release_safe(row: dict[str, Any]) -> bool:
    posture = str(row.get("release_posture") or "").lower()
    rights = str(row.get("rights_tier") or "").lower()
    if posture and posture != "release_candidate":
        return False
    return not any(marker in rights for marker in NONRELEASE_MARKERS)


def recommended_step(row: dict[str, Any]) -> str:
    step = str(row.get("next_step") or "").strip()
    if step:
        return step
    action = str(row.get("validation_next_action") or "").strip()
    if action in {"intake_first", "intake_second", "license_capture_then_intake"}:
        return "import_render_extract"
    return "hold_or_research"


def planning_score(row: dict[str, Any]) -> int:
    action = str(row.get("validation_next_action") or "")
    task_fit = str(row.get("task_fit") or "")
    revision = str(row.get("revision_family_potential") or "").lower()
    yield_hint = str(row.get("annotation_yield") or "").lower()
    score = ACTION_PRIORITY.get(action, 0)
    score += as_int(row.get("priority_score"), 0)
    score += DOMAIN_PRIORITY.get(str(row.get("domain") or ""), 0)
    if "visualdiff" in task_fit and "microtext" in task_fit:
        score += 12
    elif "visualdiff" in task_fit:
        score += 7
    elif "microtext" in task_fit:
        score += 5
    if revision == "high":
        score += 10
    elif revision == "medium":
        score += 5
    if yield_hint == "high":
        score += 8
    elif yield_hint == "medium":
        score += 4
    if as_bool(row.get("imported_or_staged")):
        score -= 8
    return score


def merged_rows(root: Path) -> list[dict[str, Any]]:
    ranked = read_csv(root / "SOURCE_CANDIDATES_RANKED.csv") or read_csv(root / "SOURCE_CANDIDATES.csv")
    validations = by_candidate(read_csv(root / "SOURCE_CANDIDATE_VALIDATION.csv"))
    readiness = by_candidate(read_csv(readiness_queue_path(root)))
    rows: list[dict[str, Any]] = []
    for candidate in ranked:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        validation = validations.get(candidate_id, {})
        ready = readiness.get(candidate_id, {})
        row: dict[str, Any] = {
            **candidate,
            "release_posture": validation.get("release_posture", ""),
            "source_validity": validation.get("source_validity", ""),
            "validation_next_action": validation.get("next_action", ""),
            "validation_notes": validation.get("notes", ""),
            "next_step": ready.get("next_step", ""),
            "imported_or_staged": ready.get("imported_or_staged", ""),
            "open_review_rows": ready.get("open_review_rows", "0"),
            "packeted_open_review_rows": ready.get("packeted_open_review_rows", "0"),
            "fresh_open_review_rows": ready.get("fresh_open_review_rows", "0"),
            "rights_blocked_open_review_rows": ready.get("rights_blocked_open_review_rows", "0"),
            "linked_local_source_count": ready.get("linked_local_source_count", "0"),
            "linked_local_gold_rows": ready.get("linked_local_gold_rows", "0"),
        }
        row["recommended_next_step"] = recommended_step(row)
        row["release_safe"] = release_safe(row)
        row["planning_score"] = planning_score(row)
        rows.append(row)
    return rows


def is_machine_ready(row: dict[str, Any]) -> bool:
    action = str(row.get("validation_next_action") or "")
    step = str(row.get("recommended_next_step") or "")
    if not bool(row.get("release_safe")):
        return False
    if action not in ACTION_PRIORITY:
        return False
    if step in HUMAN_BLOCKED_STEPS or step in RIGHTS_BLOCKED_STEPS:
        return False
    return step in MACHINE_READY_STEPS or step == "import_render_extract"


def canonical_source_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    host = parts.netloc.casefold()
    path = parts.path.rstrip("/")
    if host in {"github.com", "www.github.com"}:
        path = path.casefold()
    return urlunsplit((parts.scheme.casefold(), host, path, parts.query, ""))


def deduplicate_source_urls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = canonical_source_url(row.get("source_url"))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduplicated.append(row)
    return deduplicated


def select_batch(rows: list[dict[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    machine_ready = [row for row in rows if is_machine_ready(row)]
    machine_ready.sort(
        key=lambda row: (
            as_int(row.get("planning_score")),
            as_int(row.get("priority_score")),
            str(row.get("candidate_id") or ""),
        ),
        reverse=True,
    )
    machine_ready = deduplicate_source_urls(machine_ready)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for row in machine_ready:
        by_domain.setdefault(str(row.get("domain") or "unknown"), []).append(row)
    domain_order = sorted(
        by_domain,
        key=lambda domain: (
            DOMAIN_PRIORITY.get(domain, 0),
            as_int(by_domain[domain][0].get("planning_score")),
            domain,
        ),
        reverse=True,
    )
    if machine_ready and batch_size > 0:
        top = machine_ready[0]
        selected.append(top)
        selected_ids.add(str(top.get("candidate_id") or ""))
    if batch_size >= len(domain_order):
        for domain in domain_order:
            row = by_domain[domain][0]
            candidate_id = str(row.get("candidate_id") or "")
            if candidate_id in selected_ids:
                continue
            if len(selected) >= batch_size:
                break
            selected.append(row)
            selected_ids.add(candidate_id)
    for row in machine_ready:
        if len(selected) >= batch_size:
            break
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(candidate_id)
    for index, row in enumerate(selected, start=1):
        row["rank"] = index
    return selected


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": row.get("rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "task_fit": row.get("task_fit", ""),
        "rights_tier": row.get("rights_tier", ""),
        "release_posture": row.get("release_posture", ""),
        "source_validity": row.get("source_validity", ""),
        "validation_next_action": row.get("validation_next_action", ""),
        "recommended_next_step": row.get("recommended_next_step", ""),
        "imported_or_staged": row.get("imported_or_staged", ""),
        "open_review_rows": as_int(row.get("open_review_rows")),
        "packeted_open_review_rows": as_int(row.get("packeted_open_review_rows")),
        "fresh_open_review_rows": as_int(row.get("fresh_open_review_rows")),
        "rights_blocked_open_review_rows": as_int(row.get("rights_blocked_open_review_rows")),
        "linked_local_source_count": as_int(row.get("linked_local_source_count")),
        "linked_local_gold_rows": as_int(row.get("linked_local_gold_rows")),
        "source_url": row.get("source_url", ""),
        "source_family": row.get("source_family", ""),
        "revision_family_potential": row.get("revision_family_potential", ""),
        "annotation_yield": row.get("annotation_yield", ""),
        "priority_bucket": row.get("priority_bucket", ""),
        "priority_score": as_int(row.get("priority_score")),
        "planning_score": as_int(row.get("planning_score")),
        "notes": row.get("notes", ""),
    }


def build_report(root: str | Path, batch_size: int = 20, date_label: str | None = None) -> dict[str, Any]:
    root = Path(root)
    rows = merged_rows(root)
    selected = select_batch(rows, batch_size)
    human_blocked = [
        row for row in rows if str(row.get("recommended_next_step") or "") in HUMAN_BLOCKED_STEPS
    ]
    rights_blocked = [
        row
        for row in rows
        if not bool(row.get("release_safe"))
        or str(row.get("recommended_next_step") or "") in RIGHTS_BLOCKED_STEPS
    ]
    machine_ready = [row for row in rows if is_machine_ready(row)]
    machine_ready.sort(
        key=lambda row: (
            as_int(row.get("planning_score")),
            as_int(row.get("priority_score")),
            str(row.get("candidate_id") or ""),
        ),
        reverse=True,
    )
    machine_ready = deduplicate_source_urls(machine_ready)
    selected_public = [public_row(row) for row in selected]
    domain_selected = Counter(str(row.get("domain") or "unknown") for row in selected)
    totals = {
        "date_label": date_label or date.today().isoformat(),
        "candidate_rows": len(rows),
        "selected_machine_ready": len(selected),
        "machine_ready_pool": len(machine_ready),
        "blocked_human_return": len(human_blocked),
        "blocked_rights_or_nonrelease": len(rights_blocked),
        "intake_first_rows": sum(1 for row in rows if row.get("validation_next_action") == "intake_first"),
        "intake_second_rows": sum(1 for row in rows if row.get("validation_next_action") == "intake_second"),
        "batch_size": batch_size,
    }
    return {
        "date_label": totals["date_label"],
        "totals": totals,
        "domain_coverage": {
            "selected": dict(sorted(domain_selected.items())),
            "machine_ready_pool": dict(sorted(Counter(str(row.get("domain") or "unknown") for row in machine_ready).items())),
        },
        "selected_machine_batch": selected_public,
        "machine_ready_pool": [public_row(row) for row in machine_ready],
        "blocked_human_return": [public_row(row) for row in sorted(human_blocked, key=lambda row: as_int(row.get("planning_score")), reverse=True)],
        "blocked_rights_or_nonrelease": [public_row(row) for row in sorted(rights_blocked, key=lambda row: as_int(row.get("planning_score")), reverse=True)],
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Next Source Conversion Batch",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Selected machine-ready rows: `{totals['selected_machine_ready']}` / batch size `{totals['batch_size']}`",
        f"- Machine-ready pool: `{totals['machine_ready_pool']}`",
        f"- Intake-first rows in metadata: `{totals['intake_first_rows']}`",
        f"- Intake-second rows in metadata: `{totals['intake_second_rows']}`",
        f"- Blocked on human return: `{totals['blocked_human_return']}`",
        f"- Blocked on rights/nonrelease posture: `{totals['blocked_rights_or_nonrelease']}`",
        "",
        "## Selected Machine Batch",
        "",
        "| Rank | Candidate | Domain | Task Fit | Next Step | Action | Score | Source |",
        "| ---: | --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in report["selected_machine_batch"]:
        lines.append(
            f"| {row['rank']} | `{row['candidate_id']}` | {row['domain']} | {row['task_fit']} | "
            f"{row['recommended_next_step']} | {row['validation_next_action']} | {row['planning_score']} | {row['source_url']} |"
        )
    lines.extend(
        [
            "",
            "## Domain Coverage",
            "",
            f"- Selected: `{report['domain_coverage']['selected']}`",
            f"- Machine-ready pool: `{report['domain_coverage']['machine_ready_pool']}`",
            "",
            "## Human-Return Blocked",
            "",
            "| Candidate | Domain | Open Review | Packeted Open | Next Step |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in report["blocked_human_return"][:20]:
        lines.append(
            f"| `{row['candidate_id']}` | {row['domain']} | {row['open_review_rows']} | "
            f"{row['packeted_open_review_rows']} | {row['recommended_next_step']} |"
        )
    lines.extend(
        [
            "",
            "## Rights Or Nonrelease Blocked",
            "",
            "| Candidate | Domain | Rights Tier | Release Posture | Next Step |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["blocked_rights_or_nonrelease"][:20]:
        lines.append(
            f"| `{row['candidate_id']}` | {row['domain']} | {row['rights_tier']} | "
            f"{row['release_posture']} | {row['recommended_next_step']} |"
        )
    lines.extend(
        [
            "",
            "## Operator Notes",
            "",
            "- This is a planning artifact only. It does not download sources, rewrite JSONL, or promote gold rows.",
            "- Work the selected rows top-down: import/download, capture provenance, render pages, extract text layers/OCR, mine candidates, then export review packets.",
            "- Do not repackage rows listed under human-return blocked; they are already waiting for completed CSVs.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan the next source conversion batch.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(root, batch_size=args.batch_size, date_label=args.date_label)
    output_json = Path(
        args.output_json
        or f"derived/quality/next_source_conversion_batch_{args.date_label}.json"
    )
    output_md = Path(
        args.output_md
        or f"derived/quality/next_source_conversion_batch_{args.date_label}.md"
    )
    output_csv = Path(
        args.output_csv
        or f"derived/quality/next_source_conversion_batch_{args.date_label}.csv"
    )
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    if not output_csv.is_absolute():
        output_csv = root / output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["selected_machine_batch"], fieldnames=OUTPUT_FIELDS)
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
