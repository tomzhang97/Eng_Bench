#!/usr/bin/env python3
"""Build a flat, self-contained Eng_Bench human review handoff ZIP."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.export_review_packs import write_microtext_index
except ModuleNotFoundError:
    from export_review_packs import write_microtext_index


@dataclass(frozen=True)
class PacketSpec:
    packet_id: str
    packet_root: str
    priority: int
    summary: str


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def candidate_counter(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(row.get("candidate_id") or "").strip() for row in rows)


def matching_checklist(packet_root: Path, pack_name: str, manifest_rows: list[dict[str, Any]]) -> tuple[Path, list[str], list[dict[str, str]]]:
    direct = packet_root / f"{pack_name}_checklist.csv"
    candidates = [direct] if direct.exists() else sorted(packet_root.glob("*checklist*.csv"))
    expected = candidate_counter(manifest_rows)
    matches: list[tuple[Path, list[str], list[dict[str, str]]]] = []
    for path in candidates:
        fields, rows = read_csv(path)
        if candidate_counter(rows) == expected:
            matches.append((path, fields, rows))
    if len(matches) != 1:
        raise ValueError(f"could not match one checklist to review pack {packet_root / 'review_packs' / pack_name}")
    return matches[0]


def write_packet_steps(packet_root: Path, active_packs: list[dict[str, Any]], removed: int) -> None:
    lines = [
        "# Eng_Bench Current Packet Review",
        "",
        "This packet contains machine-generated candidates for human verification.",
        "It is not gold data. Review only the files listed below.",
        "",
        "## What To Review",
        "",
    ]
    for index, pack in enumerate(active_packs, start=1):
        categories = ", ".join(f"{count} {name}" for name, count in sorted(pack["categories"].items()))
        lines.extend(
            [
                f"{index}. Open `review_packs/{pack['pack_name']}/index.html`.",
                f"2. Record decisions in Excel tab `{packet_root.name}_{'region' if 'region' in pack['pack_name'] else 'text'}` ({pack['rows']} rows; {categories or 'uncategorized'}).",
            ]
        )
    if removed:
        lines.extend(
            [
                "",
                f"- This flat package removed {removed} duplicate candidate row(s) already assigned to an earlier priority packet.",
            ]
        )
    lines.extend(
        [
            "",
            "## Status Values",
            "",
            "- `accepted`: proposed text and category exactly match the visible label.",
            "- `edited`: the crop is usable, but text or category needs correction. Fill `corrected_text` and, when needed, `corrected_category`.",
            "- `rejected`: the crop is unreadable, non-engineering text, duplicate, or otherwise unusable.",
            "- `needs_full_page`: the crop needs the linked full-page context.",
            "",
            "For rows with blank proposed text, use `edited` with `corrected_text` when the label is readable.",
            "Do not edit JSONL files, HTML files, images, or candidate IDs.",
            "",
            "## Return",
            "",
            "Return the completed Excel workbook with the packet folder structure unchanged.",
            "",
        ]
    )
    (packet_root / "HUMAN_REVIEW_STEPS.md").write_text("\n".join(lines), encoding="utf-8")


def deduplicate_packet(packet_root: Path, seen_ids: set[str]) -> tuple[list[dict[str, Any]], int]:
    active_packs: list[dict[str, Any]] = []
    removed = 0
    for manifest_path in sorted(packet_root.glob("review_packs/*/manifest.jsonl")):
        pack_root = manifest_path.parent
        manifest_rows = load_jsonl(manifest_path)
        checklist_path, checklist_fields, checklist_rows = matching_checklist(packet_root, pack_root.name, manifest_rows)
        retained: list[dict[str, Any]] = []
        for row in manifest_rows:
            candidate_id = str(row.get("candidate_id") or "").strip()
            if candidate_id and candidate_id in seen_ids:
                removed += 1
                continue
            retained.append(row)
            if candidate_id:
                seen_ids.add(candidate_id)
        if not retained:
            checklist_path.unlink()
            shutil.rmtree(pack_root)
            continue
        remaining = candidate_counter(retained)
        filtered_checklist: list[dict[str, str]] = []
        for row in checklist_rows:
            candidate_id = str(row.get("candidate_id") or "").strip()
            if remaining[candidate_id] > 0:
                filtered_checklist.append(row)
                remaining[candidate_id] -= 1
        if any(remaining.values()):
            raise ValueError(f"checklist lost retained candidate rows for {pack_root}")
        write_jsonl(manifest_path, retained)
        write_csv(checklist_path, filtered_checklist, checklist_fields)
        write_microtext_index(pack_root, retained)
        categories = Counter(str(row.get("category") or "unknown") for row in retained)
        active_packs.append(
            {
                "pack_name": pack_root.name,
                "checklist_name": checklist_path.name,
                "rows": len(retained),
                "categories": dict(categories),
            }
        )
    if not active_packs:
        raise ValueError(f"packet has no unique candidates after deduplication: {packet_root}")
    write_packet_steps(packet_root, active_packs, removed)
    return active_packs, removed


def inspect_packet(root: Path, spec: PacketSpec) -> dict[str, Any]:
    packet_root = resolve(root, spec.packet_root)
    if not packet_root.is_dir():
        raise ValueError(f"packet directory missing: {packet_root}")
    if not (packet_root / "HUMAN_REVIEW_STEPS.md").is_file():
        raise ValueError(f"packet missing HUMAN_REVIEW_STEPS.md: {packet_root}")
    checklists = sorted(packet_root.glob("*checklist*.csv"))
    if not checklists:
        raise ValueError(f"packet missing top-level checklist CSV: {packet_root}")
    review_indexes = sorted(packet_root.glob("review_packs/**/index.html"))
    if not review_indexes:
        raise ValueError(f"packet missing review_packs index.html: {packet_root}")
    return {
        "priority": spec.priority,
        "packet_id": spec.packet_id,
        "source_folder": packet_root.name,
        "source_path": packet_root.as_posix(),
        "summary": spec.summary,
        "checklist_files": len(checklists),
        "checklist_rows": sum(csv_row_count(path) for path in checklists),
        "review_indexes": len(review_indexes),
        "source_nested_zips": sum(1 for path in packet_root.rglob("*.zip") if path.is_file()),
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_docs(output_dir: Path, date_label: str, packet_rows: list[dict[str, Any]]) -> None:
    total_rows = sum(int(row["checklist_rows"]) for row in packet_rows)
    total_checklists = sum(int(row["checklist_files"]) for row in packet_rows)
    readme = f"""# Eng_Bench Current Human Review Handoff

Created: {date_label}

This is the complete current human-review package. It intentionally contains
no ZIP files inside it. Do not mix it with older Eng_Bench handoff archives.

## Start Here

1. Extract this ZIP to a normal local folder. Do not review files while they
   are still inside the ZIP.
2. Open `Eng_Bench_Current_Human_Review_2026-06-23.xlsx` first. It is the
   primary review record and contains the packet tracker plus one editable tab
   for each checklist.
3. Open `PACKET_INDEX.csv`. It lists {len(packet_rows)} review packets, {total_checklists}
   checklist files, and {total_rows} candidate rows.
4. Work through `P/` in priority order. The packet folders are short names
   such as `p01`; `PACKET_INDEX.csv` maps each
   short name to its original source folder.
5. Inside each packet, read `HUMAN_REVIEW_STEPS.md` before opening any CSV.
6. In each packet's `review_packs/` folder, open every `index.html` in a web
   browser. These pages show the crop and full-page evidence.
7. Record decisions in the matching Excel worksheet. The top-level
   `*checklist*.csv` files are reference copies; do not edit them. Do not edit
   `manifest.jsonl`, crop images, page images, or candidate IDs.

## Evidence Paths

Use the local `review_packs/.../index.html` pages for all visual review.
The checklist `crop_path` and `page_path` values refer to files inside that
review pack. The `image_path` column is provenance metadata from the original
Eng_Bench workspace and may not resolve on the reviewer's computer; it is not
needed for human review.

## Review Decisions

- `accepted`: proposed text and category exactly match the visible label.
- `edited`: usable label, but text or category needs correction. Fill
  `corrected_text`; also fill `corrected_category` when needed.
- `rejected`: not a useful engineering-label benchmark row, unreadable crop,
  wrong crop, duplicate, or decorative text.
- `needs_full_page`: the crop needs more page context. Add a short note.

## Return

1. Record decisions in `Eng_Bench_Current_Human_Review_2026-06-23.xlsx`.
2. Leave the short packet folder names and checklist filenames unchanged.
3. Keep every row and every `candidate_id`; do not delete or reorder rows.
4. Return the whole extracted package folder, including the completed Excel
   workbook, or return the workbook plus the entire `P/` folder with its five
   packet folders unchanged.
5. The maintainer will export the workbook decisions into CSVs, then apply
   strict review gates. No returned row is merged into gold automatically.

See `RETURN_INSTRUCTIONS.md` for a short reviewer checklist.
"""
    return_instructions = """# Reviewer Return Instructions

Before returning the package, check each packet folder you were assigned:

1. Every assigned workbook row has a review_status, or has been deliberately
   left for another assigned reviewer.
2. Every `edited` row has corrected_text.
3. Every `needs_full_page` row has a short review_notes explanation.
4. Do not rename packet folders, checklist files, workbook tabs, or candidate IDs.
5. Do not edit manifest.jsonl, HTML files, crop images, page images, or the
   reference CSV checklists.
6. Zip the completed outer package folder, or send the complete
   `P` folder. Do not create a ZIP inside any packet folder.
7. Ignore the workbook `image_path` column during review. Use the local
   `review_packs` HTML pages and their crop/page links instead.
"""
    (output_dir / "README_FIRST.md").write_text(readme, encoding="utf-8")
    (output_dir / "RETURN_INSTRUCTIONS.md").write_text(return_instructions, encoding="utf-8")
    write_csv(
        output_dir / "PACKET_INDEX.csv",
        packet_rows,
        [
            "priority",
            "packet_id",
            "package_folder",
            "source_folder",
            "checklist_files",
            "checklist_rows",
            "review_indexes",
            "summary",
            "assigned_to",
            "reviewer_status",
            "reviewer_notes",
        ],
    )


def make_zip(source_dir: Path, zip_path: Path, archive_root: str) -> dict[str, Any]:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, f"{archive_root}/{path.relative_to(source_dir).as_posix()}")
    with zipfile.ZipFile(zip_path) as archive:
        bad_entry = archive.testzip()
        names = archive.namelist()
    nested = [name for name in names if name.lower().endswith(".zip")]
    return {
        "path": zip_path.as_posix(),
        "bytes": zip_path.stat().st_size,
        "entries": len(names),
        "crc_ok": bad_entry is None,
        "bad_entry": bad_entry or "",
        "nested_zip_entries": len(nested),
        "max_internal_path_length": max((len(name) for name in names), default=0),
        "sha256": sha256_file(zip_path),
    }


def build_package(
    root: str | Path,
    *,
    date_label: str,
    packets: list[PacketSpec],
    output_dir: str | Path,
    zip_path: str | Path,
) -> dict[str, Any]:
    root = Path(root)
    output_dir = resolve(root, output_dir)
    zip_path = resolve(root, zip_path)
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    if zip_path.exists():
        raise FileExistsError(f"output ZIP already exists: {zip_path}")
    if not packets:
        raise ValueError("at least one packet is required")

    packet_rows = [inspect_packet(root, spec) for spec in sorted(packets, key=lambda item: (item.priority, item.packet_id))]
    output_packets = output_dir / "P"
    output_packets.mkdir(parents=True)
    seen_ids: set[str] = set()
    for row in packet_rows:
        source = Path(row["source_path"])
        short_folder = f"p{int(row['priority']):02d}"
        target = output_packets / short_folder
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("*.zip", "__pycache__", "*.pyc"))
        active_packs, removed = deduplicate_packet(target, seen_ids)
        row["package_folder"] = target.relative_to(output_dir).as_posix()
        row["checklist_files"] = len(active_packs)
        row["checklist_rows"] = sum(int(pack["rows"]) for pack in active_packs)
        row["review_indexes"] = len(active_packs)
        row["deduplicated_rows_removed"] = removed
        row["assigned_to"] = ""
        row["reviewer_status"] = ""
        row["reviewer_notes"] = ""

    write_docs(output_dir, date_label, packet_rows)
    manifest = {
        "date_label": date_label,
        "packets": packet_rows,
        "totals": {
            "packets": len(packet_rows),
            "checklist_files": sum(int(row["checklist_files"]) for row in packet_rows),
            "checklist_rows": sum(int(row["checklist_rows"]) for row in packet_rows),
            "review_indexes": sum(int(row["review_indexes"]) for row in packet_rows),
            "source_nested_zips_excluded": sum(int(row["source_nested_zips"]) for row in packet_rows),
            "deduplicated_rows_removed": sum(int(row["deduplicated_rows_removed"]) for row in packet_rows),
        },
    }
    (output_dir / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    archive_root = "EB"
    zip_report = make_zip(output_dir, zip_path, archive_root)
    report = {
        "date_label": date_label,
        "output_dir": output_dir.as_posix(),
        "zip": zip_report,
        "packets": packet_rows,
        "totals": manifest["totals"],
        "valid": (
            zip_report["crc_ok"]
            and zip_report["nested_zip_entries"] == 0
            and zip_report["max_internal_path_length"] <= 220
        ),
    }
    return report


def parse_packet(value: str) -> PacketSpec:
    parts = value.split("=", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("packet must be ID=PACKET_ROOT=PRIORITY=SUMMARY")
    return PacketSpec(parts[0], parts[1], int(parts[2]), parts[3])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--packet", action="append", type=parse_packet, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--zip-output", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_package(
        root,
        date_label=args.date_label,
        packets=args.packet,
        output_dir=args.output_dir,
        zip_path=args.zip_output,
    )
    report_json = resolve(root, args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report_md:
        report_md = resolve(root, args.report_md)
        report_md.parent.mkdir(parents=True, exist_ok=True)
        totals = report["totals"]
        zip_report = report["zip"]
        report_md.write_text(
            "\n".join(
                [
                    "# Flat Current Human Handoff Build",
                    "",
                    f"- Valid: `{str(report['valid']).lower()}`",
                    f"- Packets: `{totals['packets']}`",
                    f"- Checklist files: `{totals['checklist_files']}`",
                    f"- Checklist rows: `{totals['checklist_rows']}`",
                    f"- ZIP entries: `{zip_report['entries']}`",
                    f"- Nested ZIP entries: `{zip_report['nested_zip_entries']}`",
                    f"- Maximum internal path length: `{zip_report['max_internal_path_length']}`",
                    f"- CRC OK: `{str(zip_report['crc_ok']).lower()}`",
                    f"- SHA-256: `{zip_report['sha256']}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
