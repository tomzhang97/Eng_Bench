#!/usr/bin/env python3
"""Import the 2026-06-11 FDOT Standard Plans wave (candidate civil_012).

Downloads a curated set of FDOT Standard Plans index sheets across the
FY2023, FY2024, and FY2025 edition paths, renders each edition at 300 DPI
with exact text layers, records inventory/manifest/attribution evidence,
and registers same-sheet cross-edition revision pairs whose payloads
actually differ. Review staging runs separately; nothing here touches
active gold.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(".")
IMPORT_DATE = "2026-06-11"
CANDIDATE = "civil_012"
BASE = "https://fdotwww.blob.core.windows.net/sitefinity/docs/default-source/design/standardplans"
INDEX_URL = "https://www.fdot.gov/design/standardplans/current/default.shtm"
EDITIONS = ["2023", "2024", "2025"]
SHEETS = [
    "000-510", "102-100", "102-600", "110-100", "120-002", "125-001",
    "160-001", "330-001", "400-010", "425-001", "425-010", "425-030",
    "425-052", "430-001", "430-010", "430-011",
]
USER_AGENT = "Eng-Bench-source-intake/1.0 (engineering-document benchmark; contact: maintainer)"
LICENSE_NOTE = (
    "FDOT Standard Plans are published Florida Department of Transportation public records issued "
    "for use in roadway contract plans; preserve the FDOT source URL, edition year, sheet index "
    "number, and exact hash when redistributing."
)


def fetch(url: str, timeout: int = 60) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def run_tool(args: list[str]) -> None:
    result = subprocess.run([sys.executable, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"tool failed: {' '.join(args)}\n{result.stdout[-300:]}\n{result.stderr[-300:]}")


def count_spans(doc_id: str) -> int:
    path = ROOT / "derived" / "textlayer" / f"{doc_id}.jsonl"
    return sum(1 for line in path.open(encoding="utf-8") if line.strip()) if path.exists() else 0


def main() -> int:
    inventory_path = ROOT / "SOURCE_INVENTORY.csv"
    with inventory_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        inventory_fields = reader.fieldnames
        inventory_ids = {row["doc_id"] for row in reader}
    manifest_path = ROOT / "manifest.jsonl"
    manifest_rows = [json.loads(line) for line in manifest_path.open(encoding="utf-8") if line.strip()]
    manifest_doc_ids = {row.get("doc_id") for row in manifest_rows if row.get("type") == "doc"}
    manifest_pair_ids = {row.get("pair_id") for row in manifest_rows if row.get("type") == "pair"}

    docs_dir = ROOT / "visualdiff" / "docs" / "fdot_standard_plans"
    docs_dir.mkdir(parents=True, exist_ok=True)

    imported, skipped, pairs = [], [], []
    inventory_appends, manifest_appends = [], []
    by_sheet: dict[str, list[dict]] = {}

    for sheet in SHEETS:
        sheet_slug = sheet.replace("-", "_")
        for edition in EDITIONS:
            doc_id = f"fdot_sp_{sheet_slug}_fy{edition}"
            if doc_id in inventory_ids:
                skipped.append({"doc_id": doc_id, "reason": "already_in_inventory"})
                continue
            url = f"{BASE}/{edition}/idx/{sheet}.pdf"
            time.sleep(1.0)
            payload = fetch(url)
            if payload is None:
                skipped.append({"doc_id": doc_id, "reason": "edition_not_published_404"})
                continue
            local_rel = f"visualdiff/docs/fdot_standard_plans/{doc_id}.pdf"
            (ROOT / local_rel).write_bytes(payload)
            sha256 = hashlib.sha256(payload).hexdigest()

            run_tool(["tools/01_render_pdf.py", "--root", ".", "--doc_id", doc_id, "--pdf_relpath", local_rel, "--dpi", "300", "--grayscale"])
            run_tool(["tools/02_extract_textlayer.py", "--root", ".", "--doc_id", doc_id, "--pdf_relpath", local_rel])
            run_tool(["tools/02b_convert_textlayer_to_jsonl.py", "--root", ".", "--doc_id", doc_id, "--dpi", "300", "--pdf_relpath", local_rel])
            spans = count_spans(doc_id)
            pages = len(list((ROOT / "derived" / "pages_300dpi" / doc_id).glob("page_*.png")))

            record = {
                "doc_id": doc_id,
                "sheet": sheet,
                "edition": edition,
                "local_path": local_rel,
                "direct_source_url": url,
                "sha256": sha256,
                "pages": pages,
                "textlayer_spans": spans,
            }
            by_sheet.setdefault(sheet, []).append(record)
            imported.append(record)
            print(f"[OK] {doc_id}: pages={pages} spans={spans}")

            inventory_appends.append(
                {
                    "path": local_rel,
                    "task": "visualdiff",
                    "doc_id": doc_id,
                    "domain": "civil_architectural",
                    "source_url": INDEX_URL,
                    "public_status": "misc_public_candidate",
                    "textlayer_status": "present" if spans else "absent",
                    "render_status": "present" if pages else "absent",
                    "notes": (
                        f"Candidate {CANDIDATE}; FDOT Standard Plans Index {sheet}, FY{edition} edition path; "
                        f"sha256={sha256.upper()}; {spans} exact text spans across {pages} pages; published "
                        "FDOT public-record standard plan sheet; attribution in "
                        "visualdiff/docs/fdot_standard_plans/ATTRIBUTION.md."
                    ),
                }
            )
            manifest_appends.append(
                {
                    "type": "doc",
                    "doc_id": doc_id,
                    "task": "visualdiff",
                    "source_candidate_id": CANDIDATE,
                    "same_model_id": f"fdot_sp_{sheet_slug}",
                    "domain": "civil_architectural",
                    "doc_type": "pdf_standard_plan_sheet",
                    "version": {"revision": f"FY{edition} edition", "imported": IMPORT_DATE},
                    "path": local_rel,
                    "sha256": sha256,
                    "pages": pages,
                    "render": {"dpi": 300, "colorspace": "gray", "rotate_cw90": False},
                    "derived": {
                        "pages_dir": f"derived/pages_300dpi/{doc_id}",
                        "textlayer_dir": f"derived/textlayer/{doc_id}",
                        "textlayer_jsonl": f"derived/textlayer/{doc_id}.jsonl",
                    },
                    "source_url": INDEX_URL,
                    "direct_source_url": url,
                    "public_status": "misc_public_candidate",
                    "license_note": LICENSE_NOTE,
                    "attribution_path": "visualdiff/docs/fdot_standard_plans/ATTRIBUTION.md",
                    "notes": (
                        f"Candidate {CANDIDATE}. FDOT Standard Plans Index {sheet} FY{edition}; "
                        "no unreviewed row is active gold."
                    ),
                }
            )

    for sheet, records in sorted(by_sheet.items()):
        records.sort(key=lambda record: record["edition"])
        for older, newer in zip(records, records[1:]):
            if older["sha256"] == newer["sha256"]:
                skipped.append(
                    {
                        "doc_id": f"{older['doc_id']}->{newer['doc_id']}",
                        "reason": "byte_identical_editions_no_pair",
                    }
                )
                continue
            sheet_slug = sheet.replace("-", "_")
            pair_id = f"vdiff__fdot_sp_{sheet_slug}__fy{older['edition']}__to__fy{newer['edition']}"
            pairs.append({"pair_id": pair_id, "from": older["doc_id"], "to": newer["doc_id"]})
            if pair_id in manifest_pair_ids:
                continue
            manifest_appends.append(
                {
                    "type": "pair",
                    "pair_id": pair_id,
                    "task": "visualdiff",
                    "pair_type": "same_model_revision",
                    "from_doc_id": older["doc_id"],
                    "to_doc_id": newer["doc_id"],
                    "page_mapping": {"type": "by_text_similarity"},
                    "derived": {},
                    "notes": (
                        f"FDOT Standard Plans Index {sheet} cross-edition pair "
                        f"(FY{older['edition']} -> FY{newer['edition']}); payload hashes differ; review-only "
                        "textlayer diff rows staged separately; rows are not gold until human adjudication."
                    ),
                }
            )

    attribution = [
        "# FDOT Standard Plans Attribution",
        "",
        "- Publisher: Florida Department of Transportation (FDOT)",
        f"- Index page: {INDEX_URL}",
        "- FDOT Standard Plans are public records published for use in roadway",
        "  contract plans. Preserve the FDOT source URL, edition year, sheet",
        "  index number, and the exact per-file hashes recorded in",
        "  `derived/source_imports/fdot_standard_plans_wave_import_2026-06-11.json`.",
        f"- Import date: {IMPORT_DATE}",
    ]
    (docs_dir / "ATTRIBUTION.md").write_text("\n".join(attribution) + "\n", encoding="utf-8")

    if inventory_appends:
        with inventory_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=inventory_fields)
            writer.writerows(inventory_appends)
    if manifest_appends:
        with manifest_path.open("a", encoding="utf-8") as f:
            for row in manifest_appends:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    evidence = {
        "wave": "fdot_standard_plans_wave",
        "import_date": IMPORT_DATE,
        "candidate_id": CANDIDATE,
        "index_url": INDEX_URL,
        "release_posture": "review_only_until_human_adjudication",
        "imported": imported,
        "pairs": pairs,
        "skipped": skipped,
    }
    evidence_path = ROOT / "derived" / "source_imports" / "fdot_standard_plans_wave_import_2026-06-11.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] imported={len(imported)} pairs={len(pairs)} skipped={len(skipped)}")
    print(f"[OK] inventory rows appended: {len(inventory_appends)}; manifest rows appended: {len(manifest_appends)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
