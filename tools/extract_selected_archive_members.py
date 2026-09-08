#!/usr/bin/env python3
"""Extract selected archive members into a controlled source-import folder."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any


EXTRACT_FIELDS = [
    "extract_rank",
    "archive_rank",
    "download_rank",
    "candidate_id",
    "domain",
    "doc_id",
    "archive_path",
    "member_path",
    "member_size",
    "member_status",
    "status",
    "output_path",
    "bytes",
    "sha256",
    "error",
    "next_step",
]
DEFAULT_OUTPUT_ROOT = "derived/source_imports/archive_extracts"


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
        writer = csv.DictWriter(f, fieldnames=EXTRACT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def truthy(value: str | bool | None) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def resolve_path(root: Path, path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return root / path


def safe_member_path(member_path: str) -> bool:
    normalized = member_path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute():
        return False
    return ".." not in pure.parts


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def output_member_path(root: Path, output_root: str, date_label: str, doc_id: str, member_path: str) -> Path:
    pure = PurePosixPath(member_path.replace("\\", "/"))
    return root / output_root / date_label / doc_id / Path(*pure.parts)


def portable_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def base_receipt(row: dict[str, str], rank: int) -> dict[str, Any]:
    return {
        "extract_rank": rank,
        "archive_rank": row.get("archive_rank", ""),
        "download_rank": row.get("download_rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "doc_id": row.get("doc_id", ""),
        "archive_path": row.get("archive_path", ""),
        "member_path": row.get("member_path", ""),
        "member_size": row.get("member_size", ""),
        "member_status": row.get("member_status", ""),
        "status": "",
        "output_path": "",
        "bytes": "",
        "sha256": "",
        "error": "",
        "next_step": "",
    }


def extract_row(
    root: Path,
    row: dict[str, str],
    rank: int,
    *,
    date_label: str,
    output_root: str,
    dry_run: bool,
    force: bool,
) -> dict[str, Any]:
    receipt = base_receipt(row, rank)
    archive_path_text = str(row.get("archive_path") or "")
    member_path = str(row.get("member_path") or "")
    doc_id = str(row.get("doc_id") or "archive").strip()
    if not safe_member_path(member_path):
        receipt.update(status="skipped_unsafe_member", error="unsafe member path")
        return receipt
    archive_path = resolve_path(root, archive_path_text)
    if not archive_path.exists():
        receipt.update(status="failed_missing_archive", error=f"missing archive:{archive_path_text}")
        return receipt
    output_path = output_member_path(root, output_root, date_label, doc_id, member_path)
    receipt["output_path"] = portable_path(output_path, root)
    if dry_run:
        receipt.update(
            status="dry_run",
            next_step="Dry run only. Re-run without --dry-run to extract this member.",
        )
        return receipt
    if output_path.exists() and not force:
        payload = output_path.read_bytes()
        receipt.update(
            status="skipped_existing",
            bytes=len(payload),
            sha256=sha256_bytes(payload),
            next_step="Existing extracted member retained. Render or convert in a later controlled pass.",
        )
        return receipt
    try:
        with zipfile.ZipFile(archive_path) as archive:
            payload = archive.read(member_path)
    except KeyError:
        receipt.update(status="failed_missing_member", error=f"member not found:{member_path}")
        return receipt
    except zipfile.BadZipFile as exc:
        receipt.update(status="failed_bad_zip", error=str(exc))
        return receipt
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_name(f"{output_path.name}.part")
    part_path.write_bytes(payload)
    os.replace(part_path, output_path)
    receipt.update(
        status="extracted",
        bytes=len(payload),
        sha256=sha256_bytes(payload),
        next_step="Render or convert this extracted source member before candidate mining.",
    )
    return receipt


def build_report(
    root: str | Path,
    *,
    inventory_csv: str | Path,
    date_label: str | None = None,
    output_root: str = DEFAULT_OUTPUT_ROOT,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    date_value = date_label or date.today().isoformat()
    input_path = Path(inventory_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = read_csv(input_path)
    selected_rows = [row for row in rows if truthy(row.get("selected_for_next_step"))]
    receipts = [
        extract_row(
            root,
            row,
            idx + 1,
            date_label=date_value,
            output_root=output_root,
            dry_run=dry_run,
            force=force,
        )
        for idx, row in enumerate(selected_rows)
    ]
    status_counts = Counter(str(row["status"]) for row in receipts)
    totals: dict[str, Any] = {
        "date_label": date_value,
        "inventory_rows": len(rows),
        "selected_rows": len(selected_rows),
        "output_root": output_root,
        "dry_run_mode": dry_run,
        "force": force,
    }
    totals.update(dict(sorted(status_counts.items())))
    return {
        "date_label": date_value,
        "inventory_csv": input_path.as_posix(),
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "extract_receipts": receipts,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Selected Archive Member Extraction",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Inventory CSV: `{report['inventory_csv']}`",
        f"- Inventory rows: `{totals['inventory_rows']}`",
        f"- Selected rows: `{totals['selected_rows']}`",
        f"- Output root: `{totals['output_root']}`",
        f"- Dry run mode: `{totals['dry_run_mode']}`",
        "- Extraction only: no rendering, candidate mining, or gold merge.",
        "",
        "## Status Counts",
        "",
    ]
    if report["status_counts"]:
        for status, count in report["status_counts"].items():
            lines.append(f"- {status}: `{count}`")
    else:
        lines.append("- none: `0`")
    lines.extend(
        [
            "",
            "## Extract Receipts",
            "",
            "| Rank | Candidate | Status | Bytes | SHA-256 | Member | Output |",
            "| ---: | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in report["extract_receipts"]:
        sha = str(row.get("sha256") or "")
        short_sha = f"`{sha[:12]}...`" if sha else ""
        lines.append(
            f"| {row['extract_rank']} | `{row['candidate_id']}` | {row['status']} | "
            f"{row['bytes']} | {short_sha} | `{row['member_path']}` | {row['output_path']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Only members marked `selected_for_next_step` are considered.",
            "- Unsafe member paths are refused before extraction.",
            "- Extracted files remain source-import artifacts until rendered, reviewed, and explicitly promoted later.",
            "- Do not merge unreviewed rows into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract selected archive members into a controlled folder.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--inventory-csv", default="derived/quality/source_archive_inventory_2026-06-16.csv")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--output-json", default="derived/quality/source_archive_extract_receipts_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_archive_extract_receipts_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_archive_extract_receipts_2026-06-16.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        inventory_csv=args.inventory_csv,
        date_label=args.date_label,
        output_root=args.output_root,
        dry_run=args.dry_run,
        force=args.force,
    )
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
    write_csv(output_csv, report["extract_receipts"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
