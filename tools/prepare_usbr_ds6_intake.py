#!/usr/bin/env python3
"""Prepare the official USBR Design Standards No. 6 intake queue."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


CANDIDATE_ID = "mech_066"
INDEX_URL = (
    "https://www.usbr.gov/tsc/techreferences/designstandards-datacollectionguides/"
    "designstandards.html"
)
DIRECT_ROOT = (
    "https://www.usbr.gov/tsc/techreferences/designstandards-datacollectionguides/"
    "finalds-pdfs"
)
LOCAL_ROOT = "microtext/docs/usbr_design_standards_no_6"
RIGHTS_PATH = f"{LOCAL_ROOT}/RIGHTS_EVIDENCE.md"
PUBLIC_STATUS = "public_domain_us_federal_candidate"
LICENSE_NOTE = (
    "These design standards are official works of the U.S. Department of the Interior, "
    "Bureau of Reclamation. The intake records the federal-authorship basis under 17 U.S.C. "
    "Section 105, the official index and direct PDF URLs, retrieval metadata, and exact "
    "payload SHA-256. Candidate annotations still require human review before promotion."
)

CHAPTERS = [
    ("6", "Bulkhead Gates and Stoplogs", "2018-01", 37),
    ("11", "Heating, Ventilation, and Air-Conditioning Systems", "2013-08", 46),
    ("12", "Trashracks and Trashrack Cleaning Devices", "2016-12", 27),
    ("14", "Auxiliary Mechanical Systems", "2016-12", 21),
    ("16", "Machine Shop Equipment", "2016-12", 12),
    ("18", "Engine-Generator Sets", "2017-08", 25),
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


def build_rows(date_label: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for rank, (chapter, title, edition, pages) in enumerate(CHAPTERS, start=1):
        doc_id = f"usbr_ds6_ch{chapter}"
        rows.append(
            {
                "queue_rank": str(rank),
                "candidate_id": CANDIDATE_ID,
                "domain": "mechanical",
                "asset_kind": "pdf",
                "link_type": "asset",
                "asset_title": f"Design Standards No. 6, Chapter {chapter}: {title}",
                "import_action": "download_hash_render_textlayer",
                "source_url": INDEX_URL,
                "page_url": INDEX_URL,
                "direct_asset_url": f"{DIRECT_ROOT}/DS6-{chapter}.pdf",
                "proposed_doc_id": doc_id,
                "proposed_local_path": f"{LOCAL_ROOT}/{doc_id}.pdf",
                "rights_capture": PUBLIC_STATUS,
                "review_gate": "review_packet_required_before_gold",
                "public_status": PUBLIC_STATUS,
                "license_note": LICENSE_NOTE,
                "rights_evidence_url": INDEX_URL,
                "rights_evidence_path": RIGHTS_PATH,
                "same_model_id": "usbr_design_standards_no_6_hydraulic_mechanical_equipment",
                "doc_type": "federal_hydraulic_mechanical_design_standard_pdf",
                "version_json": json.dumps(
                    {
                        "standard": f"DS-6({chapter})",
                        "edition": edition,
                        "retrieved": date_label,
                    },
                    separators=(",", ":"),
                ),
                "page_selection": "all",
                "full_page_count": str(pages),
                "notes": (
                    f"Official USBR Design Standards No. 6 chapter {chapter}, {title}. "
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
        "expected_pages": sum(chapter[3] for chapter in CHAPTERS),
        "unique_doc_ids": len({row["proposed_doc_id"] for row in rows}),
        "unique_direct_urls": len({row["direct_asset_url"] for row in rows}),
        "public_status": PUBLIC_STATUS,
        "rights_evidence_path": RIGHTS_PATH,
        "safe_to_merge_gold": False,
        "output_csv": output_csv.relative_to(root).as_posix(),
    }
    report["valid"] = (
        report["documents"] == len(CHAPTERS)
        and report["unique_doc_ids"] == len(CHAPTERS)
        and report["unique_direct_urls"] == len(CHAPTERS)
        and report["expected_pages"] == 168
    )
    write_json(output_json, report)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
