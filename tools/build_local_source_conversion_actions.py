#!/usr/bin/env python3
"""Build an actionable local-source conversion queue from readiness output."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


MACHINE_STEPS = {"extract_textlayer_or_ocr", "ocr_or_manual_region_proposal"}
HUMAN_BLOCKED_STEPS = {"await_human_return", "human_review", "human_review_partial_packeted"}
RIGHTS_BLOCKING_MARKERS = (
    "unknown",
    "restricted",
    "proprietary",
    "reference_only",
    "internal_only",
    "rights_uncertain",
    "release_review_needed",
)
RELEASE_SAFE_MARKERS = (
    "cc_by",
    "cc0",
    "gpl",
    "apache",
    "mit_license",
    "public_domain",
    "public_agency_source_url_terms_sha256",
)
STEP_PRIORITY = {
    "extract_textlayer_or_ocr": 30,
    "ocr_or_manual_region_proposal": 20,
}
DOMAIN_PRIORITY = {
    "pid": 15,
    "mechanical_cad": 12,
    "civil": 11,
    "architectural": 11,
    "pcb_schematic": 8,
}
OUTPUT_FIELDS = [
    "rank",
    "doc_id",
    "task",
    "domain",
    "next_step",
    "recommended_command",
    "source_path",
    "rendered_pages",
    "textlayer_spans",
    "review_rows",
    "open_review_rows",
    "packeted_open_review_rows",
    "gold_rows",
    "public_status",
    "priority_score",
    "planning_score",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in OUTPUT_FIELDS})


def as_int(value: Any) -> int:
    try:
        return int(float(str(value or "").strip()))
    except ValueError:
        return 0


def is_release_safe(row: dict[str, Any]) -> bool:
    status = str(row.get("public_status") or "").lower()
    return bool(status) and not any(marker in status for marker in RIGHTS_BLOCKING_MARKERS)


def is_machine_release_safe(row: dict[str, Any]) -> bool:
    """Require explicit rights evidence before emitting executable actions."""
    status = str(row.get("public_status") or "").lower()
    return is_release_safe(row) and any(marker in status for marker in RELEASE_SAFE_MARKERS)


def excluded_doc_ids(paths: list[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"reviewed-hold JSONL not found: {path}")
        with path.open(encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
                doc_id = str(row.get("doc_id") or "").strip()
                if doc_id:
                    excluded.add(doc_id)
    return excluded


def latest_readiness_json(root: Path) -> Path:
    paths = sorted((root / "derived" / "quality").glob("source_conversion_readiness_*.json"))
    if not paths:
        raise FileNotFoundError("no source_conversion_readiness_*.json found")
    return paths[-1]


def recommended_command(row: dict[str, Any], date_label: str) -> str:
    doc_id = str(row.get("doc_id") or "")
    step = str(row.get("next_step") or "")
    source_path = str(row.get("source_path") or "").replace("\\", "/")
    rendered_pages = as_int(row.get("rendered_pages"))
    if step == "extract_textlayer_or_ocr" and source_path:
        return (
            f"python tools\\02_extract_textlayer.py --root . --doc_id {doc_id} "
            f"--pdf_relpath {source_path}"
        )
    if step == "extract_textlayer_or_ocr" and rendered_pages > 0:
        output = f"microtext\\annotations\\microtext_review_{doc_id}_{date_label}.jsonl"
        return (
            f"python tools\\propose_microtext_regions.py --root . --doc-id {doc_id} "
            f"--pages all --limit-per-page 8 --total-limit 80 --category unknown_microtext "
            f"--output {output}"
        )
    if step == "extract_textlayer_or_ocr":
        return f"locate source PDF for {doc_id}, then run tools\\02_extract_textlayer.py"
    if step == "ocr_or_manual_region_proposal":
        output = f"microtext\\annotations\\microtext_review_{doc_id}_{date_label}.jsonl"
        return (
            f"python tools\\propose_microtext_regions.py --root . --doc-id {doc_id} "
            f"--pages all --limit-per-page 8 --total-limit 80 --category unknown_microtext "
            f"--output {output}"
        )
    return "manual triage required"


def planning_score(row: dict[str, Any]) -> int:
    step = str(row.get("next_step") or "")
    domain = str(row.get("domain") or "")
    score = STEP_PRIORITY.get(step, 0)
    score += DOMAIN_PRIORITY.get(domain, 0)
    score += as_int(row.get("priority_score"))
    if as_int(row.get("rendered_pages")) > 0:
        score += 5
    if as_int(row.get("textlayer_spans")) == 0:
        score += 3
    return score


def public_row(row: dict[str, Any], *, rank: int, date_label: str) -> dict[str, Any]:
    out = {
        "rank": rank,
        "doc_id": row.get("doc_id", ""),
        "task": row.get("task", ""),
        "domain": row.get("domain", ""),
        "next_step": row.get("next_step", ""),
        "recommended_command": recommended_command(row, date_label),
        "source_path": str(row.get("source_path") or "").replace("\\", "/"),
        "rendered_pages": as_int(row.get("rendered_pages")),
        "textlayer_spans": as_int(row.get("textlayer_spans")),
        "review_rows": as_int(row.get("review_rows")),
        "open_review_rows": as_int(row.get("open_review_rows")),
        "packeted_open_review_rows": as_int(row.get("packeted_open_review_rows")),
        "gold_rows": as_int(row.get("gold_rows")),
        "public_status": row.get("public_status", ""),
        "priority_score": as_int(row.get("priority_score")),
        "planning_score": as_int(row.get("planning_score")),
    }
    return out


def build_report(
    *,
    root: Path,
    readiness_json: Path | None = None,
    batch_size: int = 20,
    date_label: str | None = None,
    exclude_jsonl: list[Path] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    date_label = date_label or date.today().isoformat()
    readiness_path = readiness_json or latest_readiness_json(root)
    if not readiness_path.is_absolute():
        readiness_path = root / readiness_path
    readiness = read_json(readiness_path)
    local_sources = list(readiness.get("local_sources", []))
    exclusion_paths: list[Path] = []
    for path in exclude_jsonl or []:
        exclusion_paths.append(path if path.is_absolute() else root / path)
    held_doc_ids = excluded_doc_ids(exclusion_paths)

    machine_pool = [dict(row) for row in local_sources if str(row.get("next_step") or "") in MACHINE_STEPS]
    task_mismatch = [row for row in machine_pool if str(row.get("task") or "") != "microtext"]
    task_compatible = [row for row in machine_pool if str(row.get("task") or "") == "microtext"]
    rights_blocked = [row for row in task_compatible if not is_machine_release_safe(row)]
    rights_safe = [row for row in task_compatible if is_machine_release_safe(row)]
    explicitly_held = [row for row in rights_safe if str(row.get("doc_id") or "") in held_doc_ids]
    machine_actionable = [
        row for row in rights_safe if str(row.get("doc_id") or "") not in held_doc_ids
    ]
    for row in machine_actionable:
        row["planning_score"] = planning_score(row)
    machine_actionable.sort(
        key=lambda row: (
            as_int(row.get("planning_score")),
            as_int(row.get("priority_score")),
            str(row.get("doc_id") or ""),
        ),
        reverse=True,
    )
    selected_raw = machine_actionable[: max(0, batch_size)]
    selected = [
        public_row(row, rank=index, date_label=date_label)
        for index, row in enumerate(selected_raw, start=1)
    ]
    human_blocked = [
        row for row in local_sources if str(row.get("next_step") or "") in HUMAN_BLOCKED_STEPS
    ]
    totals = {
        "date_label": date_label,
        "readiness_json": str(readiness_path.relative_to(root)).replace("\\", "/")
        if readiness_path.is_relative_to(root)
        else str(readiness_path),
        "local_sources": len(local_sources),
        "machine_candidate_pool": len(machine_pool),
        "machine_actionable_pool": len(machine_actionable),
        "selected_actions": len(selected),
        "rights_blocked_machine_rows": len(rights_blocked),
        "task_mismatch_machine_rows": len(task_mismatch),
        "explicit_hold_machine_rows": len(explicitly_held),
        "exclude_jsonl": [
            str(path.relative_to(root)).replace("\\", "/")
            if path.is_relative_to(root)
            else str(path)
            for path in exclusion_paths
        ],
        "human_blocked_rows": len(human_blocked),
        "batch_size": batch_size,
    }
    return {
        "date_label": date_label,
        "totals": totals,
        "domain_coverage": {
            "selected": dict(sorted(Counter(row["domain"] for row in selected).items())),
            "machine_actionable_pool": dict(
                sorted(
                    Counter(str(row.get("domain") or "unknown") for row in machine_actionable).items()
                )
            ),
        },
        "selected_actions": selected,
        "rights_blocked_machine_rows": [
            public_row(dict(row, planning_score=planning_score(row)), rank=index, date_label=date_label)
            for index, row in enumerate(rights_blocked[:25], start=1)
        ],
        "task_mismatch_machine_rows": [
            public_row(dict(row, planning_score=planning_score(row)), rank=index, date_label=date_label)
            for index, row in enumerate(task_mismatch[:25], start=1)
        ],
        "explicit_hold_machine_rows": [
            public_row(dict(row, planning_score=planning_score(row)), rank=index, date_label=date_label)
            for index, row in enumerate(explicitly_held[:25], start=1)
        ],
        "human_blocked_sample": [
            public_row(dict(row, planning_score=planning_score(row)), rank=index, date_label=date_label)
            for index, row in enumerate(human_blocked[:25], start=1)
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Local Source Conversion Actions",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Readiness JSON: `{totals['readiness_json']}`",
        f"- Selected actions: `{totals['selected_actions']}` / batch size `{totals['batch_size']}`",
        f"- Machine-actionable local rows: `{totals['machine_actionable_pool']}`",
        f"- Rights-blocked machine rows: `{totals['rights_blocked_machine_rows']}`",
        f"- Task-mismatched machine rows: `{totals['task_mismatch_machine_rows']}`",
        f"- Explicit reviewed-hold machine rows: `{totals['explicit_hold_machine_rows']}`",
        f"- Human-blocked rows: `{totals['human_blocked_rows']}`",
        "",
        "## Selected Actions",
        "",
        "| Rank | Doc ID | Domain | Step | Pages | Spans | Command |",
        "| ---: | --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in report["selected_actions"]:
        lines.append(
            f"| {row['rank']} | `{row['doc_id']}` | {row['domain']} | {row['next_step']} | "
            f"{row['rendered_pages']} | {row['textlayer_spans']} | `{row['recommended_command']}` |"
        )
    lines.extend(
        [
            "",
            "## Domain Coverage",
            "",
            f"- Selected: `{report['domain_coverage']['selected']}`",
            f"- Machine-actionable pool: `{report['domain_coverage']['machine_actionable_pool']}`",
            "",
            "## Operator Notes",
            "",
            "- This is a planning/control artifact only. It does not rewrite annotation JSONL or promote rows to gold.",
            "- After running extraction or region proposal commands, export review packs and rebuild the supplemental review-pack index before adding rows to any human handoff.",
            "- Keep rights-blocked rows out of public gold until their status is resolved.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    report: dict[str, Any],
    *,
    output_json: Path,
    output_md: Path,
    output_csv: Path,
) -> None:
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["selected_actions"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--readiness-json", type=Path)
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument(
        "--exclude-jsonl",
        type=Path,
        action="append",
        default=[],
        help="Reviewed-hold JSONL whose doc_ids must not be scheduled; repeatable",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(
        root=args.root,
        readiness_json=args.readiness_json,
        batch_size=args.batch_size,
        date_label=args.date_label,
        exclude_jsonl=args.exclude_jsonl,
    )
    write_outputs(
        report,
        output_json=args.output_json,
        output_md=args.output_md,
        output_csv=args.output_csv,
    )
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
