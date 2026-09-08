#!/usr/bin/env python3
"""Prepare the official USDA NRCS Pennsylvania standard-drawing intake queue."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


CANDIDATE_ID = "civil_019"
INDEX_URL = (
    "https://www.nrcs.usda.gov/state-offices/pennsylvania/"
    "standard-pennsylvania-drawings"
)
LOCAL_ROOT = "microtext/docs/nrcs_pa_standard_drawings"
RIGHTS_PATH = f"{LOCAL_ROOT}/RIGHTS_EVIDENCE.md"
PUBLIC_STATUS = "public_domain_us_federal_candidate"
LICENSE_NOTE = (
    "These standard drawings are published by the U.S. Department of Agriculture, "
    "Natural Resources Conservation Service. The intake records the federal-authorship "
    "basis under 17 U.S.C. Section 105, the official index and exact PDF URLs, retrieval "
    "metadata, and payload SHA-256. Candidate annotations still require human review."
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


def drawing_code(url: str) -> str:
    filename = unquote(Path(urlsplit(url).path).name)
    token = filename.split(maxsplit=1)[0]
    token = re.sub(r"\.pdf$", "", token, flags=re.IGNORECASE)
    if not re.fullmatch(r"PA-[A-Za-z0-9._()\-]+", token, flags=re.IGNORECASE):
        raise ValueError(f"cannot derive PA drawing code from {url}")
    return token.upper()


def slug_code(code: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", code.lower()).strip("_")


def build_rows(catalog: dict[str, Any], date_label: str) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    items = catalog.get("items")
    if not isinstance(items, list):
        raise ValueError("catalog items must be a list")

    rows: list[dict[str, str]] = []
    excluded: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_doc_ids: set[str] = set()
    for item in items:
        title = str(item.get("asset_title", "")).strip()
        url = str(item.get("direct_asset_url", "")).strip()
        if not title or not url:
            raise ValueError("catalog row is missing asset_title or direct_asset_url")
        if "vacant" in title.lower():
            excluded.append({"asset_title": title, "direct_asset_url": url, "reason": "vacant_catalog_slot"})
            continue
        if url in seen_urls:
            raise ValueError(f"duplicate PDF URL: {url}")
        seen_urls.add(url)

        code = drawing_code(url)
        doc_id = f"nrcs_pa_{slug_code(code)}"
        if doc_id in seen_doc_ids:
            raise ValueError(f"duplicate proposed doc ID: {doc_id}")
        seen_doc_ids.add(doc_id)
        rows.append(
            {
                "queue_rank": str(len(rows) + 1),
                "candidate_id": CANDIDATE_ID,
                "domain": "civil",
                "asset_kind": "pdf",
                "link_type": "asset",
                "asset_title": title,
                "import_action": "download_hash_render_textlayer",
                "source_url": INDEX_URL,
                "page_url": INDEX_URL,
                "direct_asset_url": url,
                "proposed_doc_id": doc_id,
                "proposed_local_path": f"{LOCAL_ROOT}/{doc_id}.pdf",
                "rights_capture": PUBLIC_STATUS,
                "review_gate": "review_packet_required_before_gold",
                "public_status": PUBLIC_STATUS,
                "license_note": LICENSE_NOTE,
                "rights_evidence_url": INDEX_URL,
                "rights_evidence_path": RIGHTS_PATH,
                "same_model_id": "usda_nrcs_pa_standard_drawings",
                "doc_type": "federal_civil_agricultural_standard_drawing_pdf",
                "version_json": json.dumps(
                    {
                        "standard": code,
                        "catalog": "PA NRCS Standard Drawings",
                        "retrieved": date_label,
                    },
                    separators=(",", ":"),
                ),
                "page_selection": "all",
                "full_page_count": "",
                "notes": (
                    f"Official USDA NRCS Pennsylvania standard drawing {code}. "
                    "Source conversion and review staging only; no Gold row is modified."
                ),
            }
        )
    return rows, excluded


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
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    catalog_path = args.catalog if args.catalog.is_absolute() else root / args.catalog
    output_csv = args.output_csv if args.output_csv.is_absolute() else root / args.output_csv
    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows, excluded = build_rows(catalog, args.date_label)
    write_csv(output_csv, rows)
    report = {
        "goal": "Gold v2.0 Global",
        "candidate_id": CANDIDATE_ID,
        "date_label": args.date_label,
        "index_url": INDEX_URL,
        "catalog": catalog_path.relative_to(root).as_posix(),
        "catalog_documents": len(catalog.get("items", [])),
        "documents": len(rows),
        "excluded_documents": len(excluded),
        "exclusions": excluded,
        "unique_doc_ids": len({row["proposed_doc_id"] for row in rows}),
        "unique_direct_urls": len({row["direct_asset_url"] for row in rows}),
        "public_status": PUBLIC_STATUS,
        "rights_evidence_path": RIGHTS_PATH,
        "safe_to_merge_gold": False,
        "output_csv": output_csv.relative_to(root).as_posix(),
    }
    report["valid"] = (
        report["catalog_documents"] == report["documents"] + report["excluded_documents"]
        and report["documents"] > 0
        and report["unique_doc_ids"] == report["documents"]
        and report["unique_direct_urls"] == report["documents"]
    )
    write_json(output_json, report)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
