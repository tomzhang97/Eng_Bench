#!/usr/bin/env python3
"""Build operator task cards from source URL preflight results."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


OUTPUT_FIELDS = [
    "rank",
    "candidate_id",
    "domain",
    "task_fit",
    "preflight_status",
    "task_card_type",
    "import_strategy",
    "operator_next_step",
    "source_url",
    "final_url",
    "http_status",
    "error",
    "checklist_summary",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def checklist_for(strategy: str) -> tuple[str, list[str], str]:
    if strategy == "direct_pdf":
        return (
            "direct_asset_import",
            [
                "download exact URL to the source-intake folder",
                "record SHA-256, source URL, final URL, and rights posture",
                "render pages and extract text layer",
                "mine candidates and export review pack only",
            ],
            "download_exact_url_and_hash_then_render",
        )
    if strategy in {"git_repository_page", "gitlab_repository_page"}:
        return (
            "repo_asset_selection",
            [
                "clone or download the repository/release assets to a source-intake folder",
                "select specific schematic/CAD/PDF assets and record commit or release tag",
                "capture repository license and per-asset provenance",
                "render or convert selected assets, then export review packets only",
            ],
            "select_repo_assets_capture_commit_and_license",
        )
    if strategy in {"wikimedia_commons_category", "wikimedia_commons_file_page"}:
        return (
            "commons_file_selection",
            [
                "select specific Commons files and capture per-file license, author, file page URL, and direct payload URL",
                "download exact payloads and record SHA-256",
                "render/extract text where possible; otherwise create image-region proposals",
                "exclude files with renderer or license blockers",
            ],
            "select_commons_files_capture_license_then_render",
        )
    if strategy in {"loc_search_page", "loc_collection_page"}:
        return (
            "loc_item_selection",
            [
                "select specific LOC item pages rather than using the search page as a source",
                "capture item-level rights, call number/item ID, file URL, and local hash",
                "render selected sheets and export review packets only",
            ],
            "select_loc_items_capture_item_metadata",
        )
    if strategy == "html_index_or_product_page":
        return (
            "html_asset_selection",
            [
                "inspect the HTML page and identify direct PDF/image/CAD assets",
                "capture source page, direct asset URL, license/rights note, and hash",
                "render pages and mine candidates after asset selection",
            ],
            "inspect_html_select_direct_assets",
        )
    return (
        "manual_strategy_review",
        [
            "inspect source manually and decide whether an import is release-safe",
            "record why the source is selected or deprioritized",
        ],
        "manual_review",
    )


def task_from_row(row: dict[str, str]) -> dict[str, Any]:
    strategy = str(row.get("import_strategy") or "unknown")
    status = str(row.get("preflight_status") or "")
    if status != "reachable":
        task_type = "browser_manual_fallback"
        checklist = [
            "open the source in Browser/Chrome or a normal browser session",
            "confirm whether the URL is reachable and whether a direct asset exists",
            "if reachable, rerun source URL preflight or add a replacement direct URL",
            "if still blocked, mark source as fallback/hold before import work",
        ]
        next_action = "browser_or_manual_url_resolution"
    else:
        task_type, checklist, next_action = checklist_for(strategy)
    return {
        "rank": row.get("rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "task_fit": row.get("task_fit", ""),
        "preflight_status": status,
        "http_status": row.get("http_status", ""),
        "source_url": row.get("source_url", ""),
        "final_url": row.get("final_url", ""),
        "content_type": row.get("content_type", ""),
        "content_length": row.get("content_length", ""),
        "import_strategy": strategy,
        "operator_next_step": next_action,
        "task_card_type": task_type,
        "operator_checklist": checklist,
        "checklist_summary": " | ".join(checklist),
        "error": row.get("error", ""),
    }


def build_report(
    root: str | Path,
    *,
    preflight_csv: str | Path,
    date_label: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(preflight_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    tasks = [task_from_row(row) for row in read_csv(input_path)]
    import_tasks = [row for row in tasks if row["preflight_status"] == "reachable"]
    fallback_tasks = [row for row in tasks if row["preflight_status"] != "reachable"]
    totals = {
        "date_label": date_label or date.today().isoformat(),
        "rows": len(tasks),
        "import_tasks": len(import_tasks),
        "fallback_tasks": len(fallback_tasks),
    }
    return {
        "date_label": totals["date_label"],
        "preflight_csv": input_path.as_posix(),
        "totals": totals,
        "task_card_type_counts": dict(sorted(Counter(row["task_card_type"] for row in tasks).items())),
        "import_tasks": import_tasks,
        "fallback_tasks": fallback_tasks,
        "all_tasks": tasks,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Import Task Cards",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Preflight CSV: `{report['preflight_csv']}`",
        f"- Rows: `{totals['rows']}`",
        f"- Import tasks: `{totals['import_tasks']}`",
        f"- Browser/manual fallback tasks: `{totals['fallback_tasks']}`",
        "- Do not merge unreviewed rows into gold.",
        "",
        "## Task Types",
        "",
    ]
    for task_type, count in report["task_card_type_counts"].items():
        lines.append(f"- {task_type}: `{count}`")
    lines.extend(
        [
            "",
            "## Import Tasks",
            "",
            "| Rank | Candidate | Domain | Type | Next Step | URL |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["import_tasks"]:
        lines.append(
            f"| {row['rank']} | `{row['candidate_id']}` | {row['domain']} | "
            f"{row['task_card_type']} | {row['operator_next_step']} | {row['final_url'] or row['source_url']} |"
        )
        for item in row["operator_checklist"]:
            lines.append(f"|  |  |  |  | - {item} |  |")
    lines.extend(
        [
            "",
            "## Browser Or Manual Fallback",
            "",
            "| Rank | Candidate | Status | HTTP | Error | URL |",
            "| ---: | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in report["fallback_tasks"]:
        lines.append(
            f"| {row['rank']} | `{row['candidate_id']}` | {row['preflight_status']} | "
            f"{row['http_status']} | {row['error']} | {row['source_url']} |"
        )
        for item in row["operator_checklist"]:
            lines.append(f"|  |  |  |  | {item} |  |")
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- These task cards are planning artifacts only.",
            "- Import work must capture source URL, direct asset URL, local path, SHA-256, rights posture, and attribution/license notes.",
            "- Rendered pages and mined candidates must go through review packets before any gold merge.",
            "- Do not merge unreviewed rows into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build source import task cards.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument(
        "--preflight-csv",
        default="derived/quality/source_url_preflight_2026-06-16.csv",
    )
    parser.add_argument("--output-json", default="derived/quality/source_import_task_cards_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_import_task_cards_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_import_task_cards_2026-06-16.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(root, preflight_csv=args.preflight_csv, date_label=args.date_label)
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_csv = Path(args.output_csv)
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    if not output_csv.is_absolute():
        output_csv = root / output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["all_tasks"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
