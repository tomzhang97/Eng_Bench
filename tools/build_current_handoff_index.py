#!/usr/bin/env python3
"""Build an index-only control packet for current human review handoffs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


INDEX_FIELDS = [
    "priority",
    "packet_id",
    "packet_root",
    "zip_path",
    "ready_to_send",
    "checklist_files",
    "checklist_rows",
    "zip_bytes",
    "zip_entries",
    "nested_zip_entries",
    "sha256",
    "notes",
]
TRACKER_FIELDS = [
    "priority",
    "packet_id",
    "zip_path",
    "checklist_rows",
    "assigned_to",
    "date_sent",
    "date_returned",
    "return_status",
    "notes",
]


@dataclass(frozen=True)
class PacketSpec:
    packet_id: str
    packet_root: str
    zip_path: str
    priority: int = 100
    notes: str = ""


DEFAULT_PACKETS = [
    PacketSpec(
        packet_id="source_intake_page1",
        packet_root="derived/human_adjudication/2026-06-16_source_intake_candidate_review",
        zip_path="derived/human_adjudication/Eng_Bench_v2_0_source_intake_candidate_review_2026-06-16.zip",
        priority=1,
        notes="First-page source-intake candidates: 210 textlayer rows plus 111 region rows.",
    ),
    PacketSpec(
        packet_id="source_intake_deep_pages",
        packet_root="derived/human_adjudication/2026-06-16_source_intake_deep_page_review",
        zip_path="derived/human_adjudication/Eng_Bench_v2_0_source_intake_deep_page_review_2026-06-16.zip",
        priority=2,
        notes="Pages 2-5 from selected multipage PDFs: 52 textlayer rows plus 123 region rows.",
    ),
    PacketSpec(
        packet_id="arduino_uno_eagle",
        packet_root="derived/human_adjudication/2026-06-16_arduino_uno_eagle_review",
        zip_path="derived/human_adjudication/Eng_Bench_v2_0_arduino_uno_eagle_review_2026-06-16.zip",
        priority=3,
        notes="Arduino UNO Rev3e Eagle schematic/board candidates: 77 textlayer rows plus 14 region rows.",
    ),
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
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


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def checklist_counts(packet_root: Path) -> tuple[int, int]:
    checklist_paths = sorted(packet_root.glob("*checklist.csv"))
    row_count = 0
    for path in checklist_paths:
        row_count += len(read_csv_rows(path))
    return len(checklist_paths), row_count


def inspect_zip(zip_path: Path) -> dict[str, Any]:
    if not zip_path.exists():
        return {
            "zip_exists": False,
            "zip_bytes": 0,
            "zip_entries": 0,
            "nested_zip_entries": 0,
            "sha256": "",
        }
    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()
        nested = [entry for entry in entries if entry.filename.lower().endswith(".zip")]
    return {
        "zip_exists": True,
        "zip_bytes": zip_path.stat().st_size,
        "zip_entries": len(entries),
        "nested_zip_entries": len(nested),
        "sha256": sha256_file(zip_path),
    }


def packet_row(root: Path, spec: PacketSpec) -> dict[str, Any]:
    packet_root = resolve(root, spec.packet_root)
    zip_path = resolve(root, spec.zip_path)
    checklist_files, checklist_rows = checklist_counts(packet_root) if packet_root.exists() else (0, 0)
    zip_info = inspect_zip(zip_path)
    ready = (
        packet_root.exists()
        and zip_info["zip_exists"]
        and checklist_files > 0
        and checklist_rows > 0
        and zip_info["zip_entries"] > 0
        and zip_info["nested_zip_entries"] == 0
    )
    return {
        "priority": spec.priority,
        "packet_id": spec.packet_id,
        "packet_root": rel(packet_root, root),
        "zip_path": rel(zip_path, root),
        "ready_to_send": ready,
        "checklist_files": checklist_files,
        "checklist_rows": checklist_rows,
        "zip_bytes": zip_info["zip_bytes"],
        "zip_entries": zip_info["zip_entries"],
        "nested_zip_entries": zip_info["nested_zip_entries"],
        "sha256": zip_info["sha256"],
        "notes": spec.notes,
    }


def build_report(
    root: str | Path,
    *,
    date_label: str | None = None,
    packets: list[PacketSpec] | None = None,
) -> dict[str, Any]:
    root = Path(root)
    specs = sorted(packets or DEFAULT_PACKETS, key=lambda spec: (spec.priority, spec.packet_id))
    rows = [packet_row(root, spec) for spec in specs]
    status_counts = Counter(str(row["ready_to_send"]).lower() for row in rows)
    totals = {
        "date_label": date_label or date.today().isoformat(),
        "packets": len(rows),
        "ready_packets": sum(1 for row in rows if row["ready_to_send"]),
        "checklist_files": sum(int(row["checklist_files"]) for row in rows),
        "checklist_rows": sum(int(row["checklist_rows"]) for row in rows),
        "zip_count": sum(1 for row in rows if int(row["zip_entries"]) > 0),
        "zip_bytes": sum(int(row["zip_bytes"]) for row in rows),
        "nested_zip_entries": sum(int(row["nested_zip_entries"]) for row in rows),
        "all_ready": bool(rows) and all(row["ready_to_send"] for row in rows),
    }
    return {
        "date_label": totals["date_label"],
        "totals": totals,
        "ready_status_counts": dict(sorted(status_counts.items())),
        "packets": rows,
        "return_tracker_rows": [
            {
                "priority": row["priority"],
                "packet_id": row["packet_id"],
                "zip_path": row["zip_path"],
                "checklist_rows": row["checklist_rows"],
                "assigned_to": "",
                "date_sent": "",
                "date_returned": "",
                "return_status": "",
                "notes": "",
            }
            for row in rows
        ],
    }


def render_index_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Current Human Handoff Index",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Ready packets: `{totals['ready_packets']}` / `{totals['packets']}`",
        f"- Checklist files: `{totals['checklist_files']}`",
        f"- Checklist rows: `{totals['checklist_rows']}`",
        f"- Packet ZIP bytes: `{totals['zip_bytes']}`",
        f"- Nested ZIP entries: `{totals['nested_zip_entries']}`",
        "- This is an index-only handoff control sheet. It does not contain packet ZIPs.",
        "",
        "## Give the intern these separate packet ZIPs",
        "",
        "| Priority | Packet | Rows | ZIP | MB | Entries | SHA-256 | Notes |",
        "| ---: | --- | ---: | --- | ---: | ---: | --- | --- |",
    ]
    for row in report["packets"]:
        mb = int(row["zip_bytes"]) / (1024 * 1024) if row["zip_bytes"] else 0
        lines.append(
            f"| {row['priority']} | `{row['packet_id']}` | {row['checklist_rows']} | "
            f"`{row['zip_path']}` | {mb:.2f} | {row['zip_entries']} | "
            f"`{str(row['sha256'])[:12]}...` | {row['notes']} |"
        )
    lines.extend(
        [
            "",
            "## Human Instructions",
            "",
            f"1. Give the intern the {totals['packets']} packet ZIPs listed above plus this index folder or index ZIP.",
            "2. The intern should extract one packet ZIP at a time and open that packet's `HUMAN_REVIEW_STEPS.md` first.",
            "3. The intern fills only the checklist CSV files at the top level of each packet folder.",
            "4. Rows left blank are not reviewed. `accepted` means the visible crop exactly matches the proposed text/category. `edited` requires corrected text. `rejected` means unusable. `needs_full_page` means the crop needs more context.",
            "5. Return the edited checklist CSVs with their packet folder names preserved. Do not rename candidate IDs.",
            "",
            "## Maintainer Guardrails",
            "",
            "- Do not merge returned rows directly into gold.",
            "- First run checklist apply/verify tools in strict mode.",
            "- Then run provenance, split leakage, validator, and benchmark health gates before any gold merge.",
            "",
        ]
    )
    return "\n".join(lines)


def write_control_folder(root: Path, report: dict[str, Any], output_dir: Path) -> None:
    target = output_dir if output_dir.is_absolute() else root / output_dir
    target.mkdir(parents=True, exist_ok=True)
    (target / "README_FIRST.md").write_text(render_index_markdown(report), encoding="utf-8")
    write_json(target / "CURRENT_HANDOFF_INDEX.json", report)
    write_csv(target / "CURRENT_HANDOFF_INDEX.csv", report["packets"], INDEX_FIELDS)
    write_csv(target / "RETURN_TRACKER.csv", report["return_tracker_rows"], TRACKER_FIELDS)


def parse_packet(value: str) -> PacketSpec:
    parts = value.split("=", 4)
    if len(parts) < 3:
        raise argparse.ArgumentTypeError("packet must be ID=PACKET_ROOT=ZIP_PATH[=PRIORITY][=NOTES]")
    priority = int(parts[3]) if len(parts) >= 4 and parts[3] else 100
    notes = parts[4] if len(parts) >= 5 else ""
    return PacketSpec(parts[0], parts[1], parts[2], priority, notes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a current human handoff index.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--packet", action="append", type=parse_packet, help="ID=PACKET_ROOT=ZIP_PATH[=PRIORITY][=NOTES]")
    parser.add_argument("--output-dir", default="derived/human_adjudication/2026-06-16_current_intern_handoff_index")
    parser.add_argument("--output-json", default="derived/quality/current_handoff_index_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/current_handoff_index_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/current_handoff_index_2026-06-16.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(root, date_label=args.date_label, packets=args.packet)
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
    output_md.write_text(render_index_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["packets"], INDEX_FIELDS)
    write_control_folder(root, report, Path(args.output_dir))
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(f"[OK] Wrote control folder {args.output_dir}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0 if report["totals"]["all_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
