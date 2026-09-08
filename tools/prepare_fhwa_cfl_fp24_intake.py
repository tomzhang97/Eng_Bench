#!/usr/bin/env python3
"""Prepare the official FHWA CFL FP-24 standard-drawing intake queue."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


CANDIDATE_ID = "civil_023"
INDEX_URL = "https://highways.dot.gov/federal-lands/std-drawings/cfl"
DIRECT_ROOT = "https://highways.dot.gov/federal-lands/std-drawings"
LOCAL_ROOT = "microtext/docs/fhwa_cfl_fp24_standard_drawings"
RIGHTS_PATH = f"{LOCAL_ROOT}/RIGHTS_EVIDENCE.md"
PUBLIC_STATUS = "public_domain_us_federal_candidate"
LICENSE_NOTE = (
    "These standard drawings are published by the U.S. Department of Transportation, "
    "Federal Highway Administration. The intake records the federal authorship basis under "
    "17 U.S.C. Section 105, the official index and direct PDF URLs, retrieval date, and exact "
    "payload SHA-256. Human review is still required before annotation promotion."
)

SHEETS = [
    ("C157-50", "Silt Fence"),
    ("C157-51", "Temporary Inlet Protection"),
    ("C157-53", "Check Dam"),
    ("C157-54", "Check Dam with Rolled Erosion Control Product"),
    ("C157-55", "Fiber Roll"),
    ("C157-57", "Slope Drain"),
    ("C204-50", "Embankment Benching and Furrow Ditch"),
    ("C204-51", "Subexcavation"),
    ("C251-50", "Placed Riprap At Culverts"),
    ("C251-51", "Placed Riprap Between Cut and Fill"),
    ("C257-50", "Contractor Designed Mechanically Stabilized Earth Wall"),
    ("C501-50", "Minor Cement Concrete Pavement Joints"),
    ("C602-50", "24-inch Run Down and Pipe Anchor Assembly"),
    ("C602-51", "24-inch Buried Run Down and Pipe Anchor Assembly"),
    ("C606-50", "CMP Spillway Assembly with Down Drain"),
    ("C619-50", "Wire Fences with Steel Posts (Type 3)"),
    ("C619-51", "Wire Fences with Wood Posts"),
    ("C619-52", "Cattleguard Gate and Widening"),
    ("C629-50", "Rolled Erosion Control Product on Slopes"),
    ("C629-51", "Rolled Erosion Control Product in Channels"),
    ("C633-50", "Rumble Strip"),
    ("C633-51", "Delineators"),
    ("C634-50", "Linear Pavement Markings"),
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
        doc_id = f"fhwa_cfl_fp24_{sheet_slug}"
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
                "same_model_id": "fhwa_cfl_fp24_standard_drawings",
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
                    f"Official FHWA CFL FP-24 sheet {sheet}, {title}. "
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
