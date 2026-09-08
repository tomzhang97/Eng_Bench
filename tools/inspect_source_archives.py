#!/usr/bin/env python3
"""Inventory downloaded source archives without extracting their contents."""
from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any


ARCHIVE_MEMBER_FIELDS = [
    "archive_rank",
    "download_rank",
    "candidate_id",
    "domain",
    "doc_id",
    "archive_path",
    "member_path",
    "member_size",
    "member_suffix",
    "member_status",
    "selected_for_next_step",
    "next_step",
]
ENGINEERING_SUFFIXES = {".brd", ".sch", ".pdf", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".dxf", ".dwg"}
JUNK_PREFIXES = ("__macosx/",)
JUNK_NAMES = {".ds_store", "thumbs.db"}


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
        writer = csv.DictWriter(f, fieldnames=ARCHIVE_MEMBER_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve_path(root: Path, path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return root / path


def is_archive_row(row: dict[str, str]) -> bool:
    local_path = str(row.get("local_path") or "").lower()
    return row.get("asset_kind") == "archive" or local_path.endswith(".zip")


def safe_member_path(member_path: str) -> bool:
    normalized = member_path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute():
        return False
    return ".." not in pure.parts


def classify_member(member_path: str, is_dir: bool) -> tuple[str, bool, str]:
    normalized = member_path.replace("\\", "/")
    lower = normalized.lower()
    if not safe_member_path(normalized):
        return "unsafe_path", False, "Do not extract; path escapes archive root."
    if is_dir:
        return "directory", False, "Directory entry only."
    if lower.startswith(JUNK_PREFIXES) or PurePosixPath(lower).name in JUNK_NAMES:
        return "ignored_junk", False, "Ignore generated OS metadata."
    suffix = PurePosixPath(normalized).suffix.lower()
    if suffix in {".brd", ".sch"}:
        return "selected_eagle_source", True, "Extract into a controlled work folder, render Eagle XML, then build review candidates."
    if suffix in ENGINEERING_SUFFIXES:
        return "selected_renderable_asset", True, "Extract into a controlled work folder, then render or convert before review."
    if suffix in {".txt", ".md", ".license"} or "license" in lower:
        return "license_or_readme", False, "Keep as rights/attribution evidence."
    return "not_selected", False, "No immediate render/conversion path selected."


def inspect_archive(root: Path, row: dict[str, str], archive_rank: int) -> list[dict[str, Any]]:
    archive_path_text = str(row.get("local_path") or "")
    archive_path = resolve_path(root, archive_path_text)
    if not archive_path.exists():
        return [
            {
                "archive_rank": archive_rank,
                "download_rank": row.get("download_rank", ""),
                "candidate_id": row.get("candidate_id", ""),
                "domain": row.get("domain", ""),
                "doc_id": row.get("doc_id", ""),
                "archive_path": archive_path_text,
                "member_path": "",
                "member_size": "",
                "member_suffix": "",
                "member_status": "missing_archive",
                "selected_for_next_step": False,
                "next_step": "Resolve missing archive payload before inspection.",
            }
        ]
    member_rows: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        return [
            {
                "archive_rank": archive_rank,
                "download_rank": row.get("download_rank", ""),
                "candidate_id": row.get("candidate_id", ""),
                "domain": row.get("domain", ""),
                "doc_id": row.get("doc_id", ""),
                "archive_path": archive_path_text,
                "member_path": "",
                "member_size": "",
                "member_suffix": "",
                "member_status": "bad_zip",
                "selected_for_next_step": False,
                "next_step": f"Repair or replace archive: {exc}",
            }
        ]
    for info in infos:
        status, selected, next_step = classify_member(info.filename, info.is_dir())
        member_rows.append(
            {
                "archive_rank": archive_rank,
                "download_rank": row.get("download_rank", ""),
                "candidate_id": row.get("candidate_id", ""),
                "domain": row.get("domain", ""),
                "doc_id": row.get("doc_id", ""),
                "archive_path": archive_path_text,
                "member_path": info.filename,
                "member_size": info.file_size,
                "member_suffix": PurePosixPath(info.filename).suffix.lower(),
                "member_status": status,
                "selected_for_next_step": selected,
                "next_step": next_step,
            }
        )
    return member_rows


def build_report(
    root: str | Path,
    *,
    receipts_csv: str | Path,
    date_label: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(receipts_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = read_csv(input_path)
    archive_rows = [row for row in rows if is_archive_row(row) and row.get("status") in {"downloaded", "skipped_existing"}]
    member_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(archive_rows, start=1):
        member_rows.extend(inspect_archive(root, row, idx))
    status_counts = Counter(str(row["member_status"]) for row in member_rows)
    totals: dict[str, Any] = {
        "date_label": date_label or date.today().isoformat(),
        "receipt_rows": len(rows),
        "archives": len(archive_rows),
        "members": len(member_rows),
        "selected_members": sum(1 for row in member_rows if row["selected_for_next_step"]),
        "unsafe_members": status_counts.get("unsafe_path", 0),
    }
    totals.update(dict(sorted(status_counts.items())))
    return {
        "date_label": totals["date_label"],
        "receipts_csv": input_path.as_posix(),
        "totals": totals,
        "member_status_counts": dict(sorted(status_counts.items())),
        "member_rows": member_rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Archive Inventory",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Receipts CSV: `{report['receipts_csv']}`",
        f"- Archives: `{totals['archives']}`",
        f"- Members: `{totals['members']}`",
        f"- Selected members: `{totals['selected_members']}`",
        f"- Unsafe members: `{totals['unsafe_members']}`",
        "- Inventory only: no archive members were extracted.",
        "",
        "## Member Status Counts",
        "",
    ]
    if report["member_status_counts"]:
        for status, count in report["member_status_counts"].items():
            lines.append(f"- {status}: `{count}`")
    else:
        lines.append("- none: `0`")
    lines.extend(
        [
            "",
            "## Members",
            "",
            "| Archive | Candidate | Status | Selected | Size | Member | Next Step |",
            "| ---: | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in report["member_rows"]:
        lines.append(
            f"| {row['archive_rank']} | `{row['candidate_id']}` | {row['member_status']} | "
            f"{str(row['selected_for_next_step']).lower()} | {row['member_size']} | `{row['member_path']}` | {row['next_step']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This report does not extract archive members and does not modify gold labels.",
            "- Only selected members should be extracted later into a controlled folder after path safety checks.",
            "- Eagle `.brd` and `.sch` files need conversion/rendering before candidate mining.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory downloaded source archives.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--receipts-csv", default="derived/quality/source_asset_download_receipts_2026-06-16.csv")
    parser.add_argument("--output-json", default="derived/quality/source_archive_inventory_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_archive_inventory_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_archive_inventory_2026-06-16.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(root, receipts_csv=args.receipts_csv, date_label=args.date_label)
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
    write_csv(output_csv, report["member_rows"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
