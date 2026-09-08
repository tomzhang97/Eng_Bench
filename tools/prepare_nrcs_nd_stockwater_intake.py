#!/usr/bin/env python3
"""Prepare the official USDA NRCS North Dakota stock-water drawing intake."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


CANDIDATE_ID = "civil_027"
INDEX_URL = (
    "https://www.nrcs.usda.gov/state-offices/north-dakota/"
    "design-tool-data-engineering"
)
DIRECT_ASSET_URL = (
    "https://www.nrcs.usda.gov/sites/default/files/2025-04/"
    "ND%20Stockwater%20PDF%204-30-2025-2.zip"
)
LOCAL_ROOT = "microtext/docs/nrcs_nd_stockwater_drawings"
ARCHIVE_DOC_ID = "nrcs_nd_stockwater_pdf_2025_04_30"
ARCHIVE_PATH = f"{LOCAL_ROOT}/{ARCHIVE_DOC_ID}.zip"
RIGHTS_PATH = f"{LOCAL_ROOT}/RIGHTS_EVIDENCE.md"
PUBLIC_STATUS = "public_domain_us_federal_candidate"
LICENSE_NOTE = (
    "The archive is published by the U.S. Department of Agriculture, Natural "
    "Resources Conservation Service. The intake records the federal-authorship "
    "basis under 17 U.S.C. Section 105, the official catalog and asset URLs, "
    "retrieval metadata, and exact payload SHA-256. Extracted drawing members "
    "remain staged until source-level rights and annotation gates pass."
)

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
    return [
        {
            "queue_rank": "1",
            "candidate_id": CANDIDATE_ID,
            "domain": "civil",
            "asset_kind": "archive",
            "link_type": "asset",
            "asset_title": "NRCS North Dakota Stockwater PDF Drawings, 2025-04-30",
            "import_action": "download_hash_inspect_extract_render_textlayer",
            "source_url": INDEX_URL,
            "page_url": INDEX_URL,
            "direct_asset_url": DIRECT_ASSET_URL,
            "proposed_doc_id": ARCHIVE_DOC_ID,
            "proposed_local_path": ARCHIVE_PATH,
            "rights_capture": PUBLIC_STATUS,
            "review_gate": "review_packet_required_before_gold",
            "public_status": PUBLIC_STATUS,
            "license_note": LICENSE_NOTE,
            "rights_evidence_url": INDEX_URL,
            "rights_evidence_path": RIGHTS_PATH,
            "same_model_id": "usda_nrcs_nd_stockwater_standard_drawings_2025",
            "doc_type": "federal_civil_stockwater_engineering_drawing_archive",
            "version_json": json.dumps(
                {
                    "catalog": "NRCS ND Stockwater PDFs",
                    "archive_date": "2025-04-30",
                    "retrieved": date_label,
                },
                separators=(",", ":"),
            ),
            "page_selection": "all_archive_pdf_members",
            "full_page_count": "",
            "notes": (
                "Official USDA NRCS North Dakota stock-water engineering drawing "
                "archive. Inspect every member and register extracted PDFs as "
                "separate source documents; no Gold row is modified."
            ),
        }
    ]


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
        "direct_asset_url": DIRECT_ASSET_URL,
        "documents": len(rows),
        "unique_doc_ids": len({row["proposed_doc_id"] for row in rows}),
        "unique_direct_urls": len({row["direct_asset_url"] for row in rows}),
        "public_status": PUBLIC_STATUS,
        "rights_evidence_path": RIGHTS_PATH,
        "safe_to_merge_gold": False,
        "output_csv": output_csv.relative_to(root).as_posix(),
    }
    report["valid"] = (
        report["documents"] == 1
        and report["unique_doc_ids"] == 1
        and report["unique_direct_urls"] == 1
    )
    write_json(output_json, report)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
