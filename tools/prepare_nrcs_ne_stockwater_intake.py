#!/usr/bin/env python3
"""Prepare the official USDA NRCS Nebraska stock-water handbook intake."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


CANDIDATE_ID = "civil_028"
INDEX_URL = "https://www.nrcs.usda.gov/state-offices/nebraska/nebraska-engineering"
DIRECT_ASSET_URL = (
    "https://efotg.sc.egov.usda.gov/api/CPSFile/29985/"
    "516_NE_OTH_Stockwater_Pipeline_Handbook"
)
LOCAL_ROOT = "microtext/docs/nrcs_ne_stockwater_handbook"
DOC_ID = "nrcs_ne_stockwater_pipeline_handbook_2008"
PDF_PATH = f"{LOCAL_ROOT}/{DOC_ID}.pdf"
RIGHTS_PATH = f"{LOCAL_ROOT}/RIGHTS_EVIDENCE.md"
PUBLIC_STATUS = "public_domain_us_federal_candidate"
FULL_PAGE_COUNT = 250
PAGE_SELECTION = (
    "18,21-26,28-29,36,38-41,51,61,84-85,87,89-90,92-93,95,102,104,"
    "106-107,109-112,117-120,123-131,134,136-141,143-145,147-154,"
    "156-159,162-165,167,169-175,177-178,180-184,186,203,223"
)
SELECTED_PAGE_COUNT = 89
LICENSE_NOTE = (
    "The handbook is published and authored by the U.S. Department of "
    "Agriculture, Natural Resources Conservation Service, Nebraska. The "
    "intake records the federal-authorship basis under 17 U.S.C. Section 105, "
    "the official catalog and eFOTG asset URLs, retrieval metadata, PDF "
    "metadata, and exact payload SHA-256. Candidate annotations remain staged "
    "until source, evidence, deduplication, category, and promotion gates pass."
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
            "asset_kind": "pdf",
            "link_type": "asset",
            "asset_title": "Nebraska Stockwater Pipeline Handbook, April 2008",
            "import_action": "download_hash_render_textlayer_rank_engineering_pages",
            "source_url": INDEX_URL,
            "page_url": INDEX_URL,
            "direct_asset_url": DIRECT_ASSET_URL,
            "proposed_doc_id": DOC_ID,
            "proposed_local_path": PDF_PATH,
            "rights_capture": PUBLIC_STATUS,
            "review_gate": "machine_filter_then_review_or_certification_before_gold",
            "public_status": PUBLIC_STATUS,
            "license_note": LICENSE_NOTE,
            "rights_evidence_url": INDEX_URL,
            "rights_evidence_path": RIGHTS_PATH,
            "same_model_id": "usda_nrcs_ne_stockwater_pipeline_handbook_2008",
            "doc_type": "federal_civil_stockwater_engineering_handbook_pdf",
            "version_json": json.dumps(
                {
                    "edition": "2008-04",
                    "asset_id": "29985",
                    "retrieved": date_label,
                },
                separators=(",", ":"),
            ),
            "page_selection": PAGE_SELECTION,
            "full_page_count": str(FULL_PAGE_COUNT),
            "notes": (
                "Official USDA NRCS Nebraska handbook. The full PDF is retained, "
                f"while {SELECTED_PAGE_COUNT} visually confirmed engineering figure, "
                "profile, equipment, control, tank, and table pages are rendered for "
                "machine-first candidate mining. No Gold row is modified."
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
        "full_page_count": FULL_PAGE_COUNT,
        "selected_page_count": SELECTED_PAGE_COUNT,
        "public_status": PUBLIC_STATUS,
        "rights_evidence_path": RIGHTS_PATH,
        "safe_to_merge_gold": False,
        "output_csv": output_csv.relative_to(root).as_posix(),
    }
    report["valid"] = (
        report["documents"] == 1
        and report["full_page_count"] == 250
        and report["selected_page_count"] == 89
    )
    write_json(output_json, report)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
