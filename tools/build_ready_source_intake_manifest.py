#!/usr/bin/env python3
"""Build a ready intake manifest and repair queue from asset preflight rows."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


READY_FIELDS = [
    "intake_rank",
    "queue_rank",
    "candidate_id",
    "domain",
    "asset_kind",
    "import_action",
    "doc_id",
    "local_path",
    "download_url",
    "source_url",
    "page_url",
    "content_type",
    "content_length",
    "license_short_name",
    "license_url",
    "artist",
    "commons_sha1",
    "rights_capture",
    "review_gate",
    "intake_status",
    "notes",
]
REPAIR_FIELDS = [
    "queue_rank",
    "candidate_id",
    "domain",
    "asset_kind",
    "import_action",
    "preflight_status",
    "repair_action",
    "source_url",
    "page_url",
    "attempted_download_url",
    "content_type",
    "error",
    "proposed_doc_id",
    "proposed_local_path",
    "notes",
]
IMPORT_DIR = "microtext/docs/source_intake_2026_06_16"
IMPORT_ROOT = "microtext/docs"
SOURCE_INTAKE_DIR_PATTERN = re.compile(r"^(?P<prefix>.*?)(?:microtext/docs/)?source_intake_[^/\\]+[/\\](?P<name>[^/\\]+)$")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def extension_from_url(url: str, fallback: str = "") -> str:
    path = unquote(urlparse(url).path)
    suffix = Path(path).suffix.lower()
    return suffix or fallback


def import_dir_from_date_label(date_label: str | None) -> str:
    label = str(date_label or "").strip()
    if not label:
        return IMPORT_DIR
    safe = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
    return f"{IMPORT_ROOT}/source_intake_{safe}" if safe else IMPORT_DIR


def rewrite_intake_dir(existing: str, import_dir: str) -> str:
    normalized = existing.replace("\\", "/")
    match = SOURCE_INTAKE_DIR_PATTERN.match(normalized)
    if not match:
        return normalized
    return f"{import_dir.rstrip('/')}/{match.group('name')}"


def inferred_local_path(row: dict[str, str], import_dir: str) -> str:
    existing = str(row.get("proposed_local_path") or "").strip()
    if existing:
        return rewrite_intake_dir(existing, import_dir)
    download_url = str(row.get("resolved_direct_asset_url") or row.get("direct_asset_url") or "").strip()
    suffix = extension_from_url(download_url, fallback=".bin")
    doc_id = str(row.get("proposed_doc_id") or row.get("candidate_id") or "source_asset").strip()
    return f"{import_dir.rstrip('/')}/{doc_id}{suffix}"


def repair_action(row: dict[str, str]) -> str:
    status = str(row.get("preflight_status") or "")
    if status == "blocked_content_type_mismatch":
        return "replace_with_direct_payload_url"
    if status == "blocked_missing_direct_url":
        return "fill_direct_asset_url"
    if status == "blocked_commons_license":
        return "exclude_or_rights_review"
    if status == "blocked_commons_resolution":
        return "resolve_commons_file_page"
    if status == "blocked_unreachable":
        return "browser_or_manual_url_resolution"
    return "manual_repair_review"


def ready_row(row: dict[str, str], intake_rank: int, import_dir: str) -> dict[str, Any]:
    download_url = str(row.get("resolved_direct_asset_url") or row.get("direct_asset_url") or "").strip()
    return {
        "intake_rank": intake_rank,
        "queue_rank": row.get("queue_rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "asset_kind": row.get("asset_kind", ""),
        "import_action": row.get("import_action", ""),
        "doc_id": row.get("proposed_doc_id", ""),
        "local_path": inferred_local_path(row, import_dir),
        "download_url": download_url,
        "source_url": row.get("source_url", ""),
        "page_url": row.get("page_url", ""),
        "content_type": row.get("content_type", ""),
        "content_length": row.get("content_length", ""),
        "license_short_name": row.get("license_short_name", ""),
        "license_url": row.get("license_url", ""),
        "artist": row.get("artist", ""),
        "commons_sha1": row.get("commons_sha1", ""),
        "rights_capture": row.get("rights_capture", ""),
        "review_gate": row.get("review_gate", ""),
        "intake_status": "ready_download_hash_then_render",
        "notes": "Manifest row only. Download exact payload, compute SHA-256, render/convert, mine candidates, and export review packets before gold.",
    }


def repair_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "queue_rank": row.get("queue_rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "asset_kind": row.get("asset_kind", ""),
        "import_action": row.get("import_action", ""),
        "preflight_status": row.get("preflight_status", ""),
        "repair_action": repair_action(row),
        "source_url": row.get("source_url", ""),
        "page_url": row.get("page_url", ""),
        "attempted_download_url": row.get("resolved_direct_asset_url") or row.get("direct_asset_url", ""),
        "content_type": row.get("content_type", ""),
        "error": row.get("error", ""),
        "proposed_doc_id": row.get("proposed_doc_id", ""),
        "proposed_local_path": row.get("proposed_local_path", ""),
        "notes": "Do not import until repaired and preflighted again.",
    }


def build_report(
    root: str | Path,
    *,
    preflight_csv: str | Path,
    date_label: str | None = None,
    import_dir: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(preflight_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = read_csv(input_path)
    label = date_label or date.today().isoformat()
    resolved_import_dir = import_dir or import_dir_from_date_label(label)
    ready_rows = [
        ready_row(row, len([r for r in rows[:idx] if truthy(r.get("ready_for_intake"))]) + 1, resolved_import_dir)
        for idx, row in enumerate(rows)
        if truthy(row.get("ready_for_intake"))
    ]
    repair_rows = [repair_row(row) for row in rows if not truthy(row.get("ready_for_intake"))]
    totals = {
        "date_label": label,
        "import_dir": resolved_import_dir,
        "input_rows": len(rows),
        "ready_rows": len(ready_rows),
        "repair_rows": len(repair_rows),
    }
    return {
        "date_label": totals["date_label"],
        "preflight_csv": input_path.as_posix(),
        "totals": totals,
        "ready_by_action": dict(sorted(Counter(row["import_action"] for row in ready_rows).items())),
        "ready_by_domain": dict(sorted(Counter(row["domain"] for row in ready_rows).items())),
        "repair_by_action": dict(sorted(Counter(row["repair_action"] for row in repair_rows).items())),
        "ready_rows": ready_rows,
        "repair_rows": repair_rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Ready Source Intake Manifest",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Preflight CSV: `{report['preflight_csv']}`",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Ready rows: `{totals['ready_rows']}`",
        f"- Repair rows: `{totals['repair_rows']}`",
        f"- Import dir: `{totals['import_dir']}`",
        "- Manifest only: do not merge unreviewed rows into gold.",
        "",
        "## Ready By Action",
        "",
    ]
    for action, count in report["ready_by_action"].items():
        lines.append(f"- {action}: `{count}`")
    lines.extend(["", "## Repair By Action", ""])
    if report["repair_by_action"]:
        for action, count in report["repair_by_action"].items():
            lines.append(f"- {action}: `{count}`")
    else:
        lines.append("- none: `0`")
    lines.extend(
        [
            "",
            "## Ready Rows",
            "",
            "| Rank | Candidate | Domain | Kind | Action | Local Path | Download URL |",
            "| ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["ready_rows"]:
        lines.append(
            f"| {row['intake_rank']} | `{row['candidate_id']}` | {row['domain']} | {row['asset_kind']} | "
            f"{row['import_action']} | {row['local_path']} | {row['download_url']} |"
        )
    lines.extend(
        [
            "",
            "## Repair Rows",
            "",
            "| Queue | Candidate | Status | Repair Action | Attempted URL |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for row in report["repair_rows"]:
        lines.append(
            f"| {row['queue_rank']} | `{row['candidate_id']}` | {row['preflight_status']} | "
            f"{row['repair_action']} | {row['attempted_download_url']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This manifest does not download, hash, render, mine, packet, or promote rows.",
            "- The next intake step must download exact payloads, compute SHA-256, capture rights/attribution metadata, and render/convert before candidate mining.",
            "- Rows in the repair queue must not be imported until their direct payload URL or rights issue is fixed and preflighted again.",
            "- Do not merge unreviewed rows into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a ready intake manifest from preflight rows.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--preflight-csv", default="derived/quality/source_asset_intake_preflight_2026-06-16.csv")
    parser.add_argument("--output-json", default="derived/quality/ready_source_intake_manifest_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/ready_source_intake_manifest_2026-06-16.md")
    parser.add_argument("--ready-csv", default="derived/quality/ready_source_intake_manifest_2026-06-16.csv")
    parser.add_argument("--repair-csv", default="derived/quality/source_asset_repair_queue_2026-06-16.csv")
    parser.add_argument(
        "--import-dir",
        help="Override the local import directory. Defaults to microtext/docs/source_intake_<date_label>.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        preflight_csv=args.preflight_csv,
        date_label=args.date_label,
        import_dir=args.import_dir,
    )
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    ready_csv = Path(args.ready_csv)
    repair_csv = Path(args.repair_csv)
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    if not ready_csv.is_absolute():
        ready_csv = root / ready_csv
    if not repair_csv.is_absolute():
        repair_csv = root / repair_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(ready_csv, report["ready_rows"], READY_FIELDS)
    write_csv(repair_csv, report["repair_rows"], REPAIR_FIELDS)
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {ready_csv}")
    print(f"[OK] Wrote {repair_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
