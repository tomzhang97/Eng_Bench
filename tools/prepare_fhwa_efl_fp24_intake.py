#!/usr/bin/env python3
"""Prepare a balance-oriented FHWA EFL FP-24 detail-drawing intake queue."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


CANDIDATE_ID = "civil_026"
INDEX_URL = "https://highways.dot.gov/federal-lands/std-drawings/efl"
DIRECT_ROOT = "https://highways.dot.gov/federal-lands/std-drawings"
LOCAL_ROOT = "microtext/docs/fhwa_efl_fp24_detail_drawings"
RIGHTS_PATH = f"{LOCAL_ROOT}/RIGHTS_EVIDENCE.md"
PUBLIC_STATUS = "public_domain_us_federal_candidate"
LICENSE_NOTE = (
    "These detail drawings are published by the U.S. Department of Transportation, "
    "Federal Highway Administration. The intake records the federal authorship basis "
    "under 17 U.S.C. Section 105, official index and direct PDF URLs, retrieval date, "
    "and exact payload SHA-256. Review and strict promotion remain required."
)

# First tranche emphasizes dense dimensional and construction-detail content.
SHEETS = [
    ("E157-01", "Stabilized Construction Exit"),
    ("E157-02", "Wire-Backed Silt Fence"),
    ("E157-04", "Fiber Roll"),
    ("E157-06", "Check Dam with Rolled Erosion Control Product"),
    ("E157-08", "Temporary Stormwater Diversion Berm Methods"),
    ("E157-09", "Bypass Pumping Diversion for Temporary Stormwater Diversions"),
    ("E157-10", "Sediment Filter Bag"),
    ("E157-11", "Floating Turbidity Curtains"),
    ("E204-01", "Cut and Fill Slope Transitions"),
    ("E204-02", "Benching for Embankment"),
    ("E204-05", "Roadway Connections"),
    ("E251-01", "Placed Riprap at Culvert Outlets"),
    ("E251-02", "Loose Riprap Channel at Culvert"),
    ("E252-01", "Mechanically-Placed Rock Embankment"),
    ("E401-01", "Pavement Transitions"),
    ("E414-01", "Asphalt Pavement Crack Sealing and Filling"),
    ("E602-01", "Metal Pipe Culvert"),
    ("E602-07", "Concrete Pipe Culvert Installation"),
    ("E602-08", "Elliptical Concrete Pipe"),
    ("E602-09", "Elliptical Concrete Pipe Culvert Installation"),
    ("E602-10", "Concrete End Section for Elliptical Concrete Pipe"),
    ("E604-01", "Inlet, Type 4A, 4B, and 4C"),
    ("E604-02", "Inlet, Type 4D"),
    ("E604-03", "Inlet, Type 5A Modified"),
    ("E604-04", "Inlet, Type 5B"),
    ("E604-05", "Inlet, Type 6A"),
    ("E604-06", "Inlet, Type 6A-6 and Metal Frame and Grate"),
    ("E604-07", "Junction Box"),
    ("E604-08", "Spring Box"),
    ("E604-09", "Precast Concrete Inlet Cap"),
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
    for rank, (sheet, title) in enumerate(SHEETS, 1):
        doc_id = f"fhwa_efl_fp24_{slug(sheet)}"
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
                "same_model_id": "fhwa_efl_fp24_detail_drawings",
                "doc_type": "federal_civil_detail_drawing_pdf",
                "version_json": json.dumps(
                    {"standard": sheet, "edition": "FP-24", "retrieved": date_label},
                    separators=(",", ":"),
                ),
                "page_selection": "all",
                "full_page_count": "",
                "notes": (
                    f"Official FHWA EFL FP-24 detail {sheet}, {title}. "
                    "Machine intake and review staging only; active Gold is unchanged."
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
        "same_model_id": "fhwa_efl_fp24_detail_drawings",
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
