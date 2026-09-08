#!/usr/bin/env python3
"""Prepare the official FHWA WFL FP-24 standard-drawing intake queue."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


CANDIDATE_ID = "civil_018"
INDEX_URL = "https://highways.fhwa.dot.gov/federal-lands/std-drawings/wfl"
DIRECT_ROOT = "https://highways.fhwa.dot.gov/federal-lands/std-drawings"
LOCAL_ROOT = "microtext/docs/fhwa_wfl_fp24_standard_drawings"
RIGHTS_PATH = f"{LOCAL_ROOT}/RIGHTS_EVIDENCE.md"
PUBLIC_STATUS = "public_domain_us_federal_candidate"
LICENSE_NOTE = (
    "These standard drawings are published by the U.S. Department of Transportation, "
    "Federal Highway Administration. The intake records the federal authorship basis under "
    "17 U.S.C. Section 105, the official index and direct PDF URLs, retrieval date, and exact "
    "payload SHA-256. Human review is still required before annotation promotion."
)

SHEETS = [
    ("W101-1", "Plan Symbols and Abbreviations"),
    ("W157-1", "Silt Fence"),
    ("W157-2", "Temporary Inlet Protection"),
    ("W157-15", "Check Dam, Moderate Grades"),
    ("W157-16", "Check Dam with Rolled Erosion Control Product"),
    ("W157-18", "Sediment Filter Bag"),
    ("W157-19", "Stabilized Construction Exit"),
    ("W157-21", "Fiber Roll"),
    ("W251-1", "Placed Riprap at Culvert Outlets"),
    ("W253-1", "Gabion Basket"),
    ("W253-2", "Gabion Faced Wall, Sheet 1"),
    ("W253-3", "Gabion Faced Wall, Sheet 2"),
    ("W601-10", "Reinforced Concrete Headwall"),
    ("W606-10", "Spillway Assembly with Down Drain"),
    ("W606-11", "Spillway Assembly with Down Drain and Slip-Joint"),
    ("W606-12", "Spillway Assembly with Half-Round Down Drain"),
    ("W606-13", "Corrugated Metal Spillways and Inlets"),
    ("W606-14", "Pipe Anchor Assembly"),
    ("W617-83", "Removable Steel-backed Log Rail"),
    ("W621-1", "Right-of-Way Monumentation"),
    ("W628-10", "Temporary Diversion Berm Methods"),
    ("W633-7", "Permanent Sign Installation, Wood Posts"),
    ("W634-1", "Pavement Markings Symbols and Words"),
    ("W634-2", "Linear Pavement Markings"),
    ("W646-1", "Mailbox Turnout and Installation"),
    ("W646-2", "Mailbox Assembly, Series A"),
    ("W646-3", "Mailbox Assembly, Series B"),
    ("W646-4", "Mailbox Assembly, Series C"),
]

QUEUE_FIELDS = [
    "queue_rank",
    "candidate_id",
    "domain",
    "asset_kind",
    "link_type",
    "asset_title",
    "import_action",
    "source_url",
    "page_url",
    "direct_asset_url",
    "proposed_doc_id",
    "proposed_local_path",
    "rights_capture",
    "review_gate",
    "public_status",
    "license_note",
    "rights_evidence_url",
    "rights_evidence_path",
    "same_model_id",
    "doc_type",
    "version_json",
    "page_selection",
    "full_page_count",
    "notes",
]


def slug(sheet: str) -> str:
    return sheet.lower().replace("-", "_")


def build_rows(date_label: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for rank, (sheet, title) in enumerate(SHEETS, start=1):
        sheet_slug = slug(sheet)
        doc_id = f"fhwa_wfl_fp24_{sheet_slug}"
        rows.append(
            {
                "queue_rank": str(rank),
                "candidate_id": CANDIDATE_ID,
                "domain": "civil",
                "asset_kind": "pdf",
                "link_type": "asset",
                "asset_title": f"{sheet} - {title}",
                "import_action": "download_hash_render_textlayer",
                "source_url": INDEX_URL,
                "page_url": INDEX_URL,
                "direct_asset_url": f"{DIRECT_ROOT}/{sheet}.pdf",
                "proposed_doc_id": doc_id,
                "proposed_local_path": f"{LOCAL_ROOT}/{doc_id}.pdf",
                "rights_capture": PUBLIC_STATUS,
                "review_gate": "review_packet_required_before_gold",
                "public_status": PUBLIC_STATUS,
                "license_note": LICENSE_NOTE,
                "rights_evidence_url": INDEX_URL,
                "rights_evidence_path": RIGHTS_PATH,
                "same_model_id": "fhwa_wfl_fp24_standard_drawings",
                "doc_type": "federal_civil_standard_drawing_pdf",
                "version_json": json.dumps(
                    {
                        "standard": sheet,
                        "edition": "FP-24",
                        "retrieved": date_label,
                    },
                    separators=(",", ":"),
                ),
                "page_selection": "all",
                "full_page_count": "",
                "notes": (
                    f"Official FHWA WFL FP-24 sheet {sheet}, {title}. "
                    "Source conversion and review staging only; no Gold row is modified."
                ),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUEUE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    output_csv = args.output_csv if args.output_csv.is_absolute() else root / args.output_csv
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    rows = build_rows(args.date_label)
    write_csv(output_csv, rows)
    report = {
        "goal": "Gold v2.0 Global",
        "candidate_id": CANDIDATE_ID,
        "date_label": args.date_label,
        "index_url": INDEX_URL,
        "documents": len(rows),
        "unique_doc_ids": len({row["proposed_doc_id"] for row in rows}),
        "unique_direct_urls": len({row["direct_asset_url"] for row in rows}),
        "public_status": PUBLIC_STATUS,
        "rights_evidence_path": RIGHTS_PATH,
        "safe_to_merge_gold": False,
        "output_csv": output_csv.relative_to(root).as_posix(),
    }
    report["valid"] = (
        report["documents"] == len(SHEETS)
        and report["unique_doc_ids"] == len(SHEETS)
        and report["unique_direct_urls"] == len(SHEETS)
    )
    write_json(output_json, report)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
