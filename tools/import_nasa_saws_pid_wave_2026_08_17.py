#!/usr/bin/env python3
"""Import the NASA SAWS regenerative-fuel-cell P&ID as review-only source data.

The importer is deliberately source-only: it registers provenance, renders the
selected P&ID page, and extracts its sparse PDF text layer. Candidate rows are
generated and curated by the shared OCR pipeline after this command succeeds.
No active annotation or unified Gold file is edited.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import fitz


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import register_manifest_source_candidate as candidate_register


IMPORT_DATE = "2026-08-17"
WAVE = "wave212"
NTRS_ID = "20200004325"
DOC_ID = "nasa_20200004325_saws_rfc_pid"
VERSION_ID = "nasa_20200004325_wave212"
SOURCE_CANDIDATE_ID = "pid_082"
SOURCE_URL = f"https://ntrs.nasa.gov/citations/{NTRS_ID}"
METADATA_URL = f"https://ntrs.nasa.gov/api/citations/{NTRS_ID}"
DIRECT_SOURCE_URL = (
    f"https://ntrs.nasa.gov/api/citations/{NTRS_ID}/downloads/"
    "NASA%20TM2018-219945.pdf"
)
PUBLIC_STATUS = "government_public_use_permitted_nasa_candidate"
PDF_SHA256 = "81f428c24707fc2f4754e109c3e188a789d1ea83968e0c7b7bd0509ae39c56e4"
METADATA_SHA256 = "8f9189159403b53cf98746cd22f3ed1b2527b826580c859aed0e6b3c814cb13f"
PDF_PAGES = 286
SELECTED_PAGE_1BASED = 104
SELECTED_PAGE_INDEX = SELECTED_PAGE_1BASED - 1

MUTABLE_REGISTRIES = (
    "manifest.jsonl",
    "SOURCE_INVENTORY.csv",
    "SOURCE_CANDIDATES.csv",
    "SOURCE_CANDIDATES_RANKED.csv",
    "SOURCE_CANDIDATE_VALIDATION.csv",
    "SOURCE_CONVERSION_EXHAUSTION.csv",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_hash(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual.lower() != expected.lower():
        raise ValueError(f"hash mismatch for {path}: expected {expected}, got {actual}")


def verify_metadata(payload: dict[str, Any]) -> None:
    copyright_row = payload.get("copyright") or {}
    export_row = payload.get("exportControl") or {}
    failures: list[str] = []
    if str(payload.get("id")) != NTRS_ID:
        failures.append("citation id mismatch")
    if payload.get("distribution") != "PUBLIC":
        failures.append("distribution is not PUBLIC")
    if copyright_row.get("determinationType") != "GOV_PUBLIC_USE_PERMITTED":
        failures.append("copyright determination is not GOV_PUBLIC_USE_PERMITTED")
    if copyright_row.get("containsThirdPartyMaterial") is not False:
        failures.append("third-party material is not explicitly false")
    if export_row.get("isExportControl") != "NO":
        failures.append("export-control status is not NO")
    if export_row.get("ear") != "NO" or export_row.get("itar") != "NO":
        failures.append("EAR/ITAR status is not NO")
    if failures:
        raise ValueError("; ".join(failures))


def verify_inputs(pdf_path: Path, metadata_path: Path) -> dict[str, Any]:
    assert_hash(pdf_path, PDF_SHA256)
    assert_hash(metadata_path, METADATA_SHA256)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    verify_metadata(metadata)
    document = fitz.open(pdf_path)
    try:
        if document.page_count != PDF_PAGES:
            raise ValueError(f"expected {PDF_PAGES} PDF pages, got {document.page_count}")
        selected = document[SELECTED_PAGE_INDEX]
        caption = selected.get_text()
        if "Figure 82" not in caption or "P&ID" not in caption:
            raise ValueError("selected page does not contain the expected Figure 82 P&ID caption")
        if not selected.get_images(full=True):
            raise ValueError("selected P&ID page has no embedded image")
    finally:
        document.close()
    return metadata


def copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    assert_hash(source, expected_hash)
    if destination.exists():
        assert_hash(destination, expected_hash)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    assert_hash(destination, expected_hash)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> bool:
    rows = load_jsonl(path)
    matches = [item for item in rows if item.get("type") == "doc" and item.get("doc_id") == DOC_ID]
    if len(matches) > 1:
        raise ValueError(f"duplicate manifest rows for {DOC_ID}")
    if matches:
        if str(matches[0].get("sha256") or "").lower() != PDF_SHA256:
            raise ValueError(f"manifest conflict for {DOC_ID}")
        return False
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def append_inventory(path: Path, row: dict[str, str]) -> bool:
    fields, rows = read_csv(path)
    matches = [item for item in rows if item.get("doc_id") == DOC_ID]
    if len(matches) > 1:
        raise ValueError(f"duplicate inventory rows for {DOC_ID}")
    if matches:
        if str(matches[0].get("source_path") or "") != row["source_path"]:
            raise ValueError(f"inventory conflict for {DOC_ID}")
        return False
    with path.open("a", encoding="utf-8-sig", newline="") as stream:
        csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore").writerow(row)
    return True


def render_and_extract(pdf_path: Path, page_path: Path, textlayer_path: Path) -> int:
    document = fitz.open(pdf_path)
    try:
        page = document[SELECTED_PAGE_INDEX]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
        page_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(page_path)
        spans: list[dict[str, Any]] = []
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = str(span.get("text") or "").strip()
                    if text:
                        spans.append(
                            {
                                "page_index": SELECTED_PAGE_INDEX,
                                "text": text,
                                "bbox": span.get("bbox"),
                                "origin": span.get("origin"),
                                "size": span.get("size"),
                                "font": span.get("font"),
                                "flags": span.get("flags"),
                                "color": span.get("color"),
                            }
                        )
    finally:
        document.close()
    textlayer_path.parent.mkdir(parents=True, exist_ok=True)
    textlayer_path.write_text(
        json.dumps({"doc_page": SELECTED_PAGE_INDEX, "spans": spans}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return len(spans)


def candidate_spec() -> dict[str, Any]:
    notes = (
        "NASA NTRS records the SAWS memorandum as PUBLIC with "
        "GOV_PUBLIC_USE_PERMITTED, no third-party material, and no export-control flags. "
        "Exact metadata, payload hash, Figure 82 render, sparse text layer, and source URLs "
        "are preserved; all candidates remain outside Gold until human adjudication and "
        "strict promotion gates pass."
    )
    return {
        "candidate": {
            "candidate_id": SOURCE_CANDIDATE_ID,
            "path_hint": "candidate/pid/nasa_saws_rfc_pid",
            "domain": "pid",
            "task_fit": "microtext",
            "rights_tier": PUBLIC_STATUS,
            "source_url": SOURCE_URL,
            "source_family": "nasa_saws_rfc_pid",
            "likely_asset_type": "federal_regenerative_fuel_cell_pid_report_pdf",
            "revision_family_potential": "low",
            "annotation_yield": "medium",
            "priority_bucket": "A",
            "notes": notes,
        },
        "doc_ids": [DOC_ID],
        "priority_score": 10,
        "validation": {
            "release_posture": "release_candidate",
            "validation_next_action": "mine_guarded_pid_labels",
        },
    }


def rights_evidence_text() -> str:
    return f"""# NASA NTRS rights evidence

- NTRS record: {SOURCE_URL}
- NTRS metadata API: {METADATA_URL}
- Direct PDF: {DIRECT_SOURCE_URL}
- NTRS ID: `{NTRS_ID}`
- Distribution: `PUBLIC`
- Copyright determination: `GOV_PUBLIC_USE_PERMITTED`
- Contains third-party material: `false`
- Export control, EAR, and ITAR: `NO`
- PDF SHA-256: `{PDF_SHA256.upper()}`
- Metadata SHA-256: `{METADATA_SHA256.upper()}`

The exact metadata response is preserved beside the payload. This source is
not described as public domain; release use follows the NTRS public-use
determination. Only PDF page {SELECTED_PAGE_1BASED}, NASA Figure 82, is selected
for review-candidate mining. No unreviewed row is active Gold.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--pdf", default=".codex_tmp/wave212_nasa_20200004325.pdf")
    parser.add_argument(
        "--metadata", default=".codex_tmp/wave212_nasa_20200004325_metadata.json"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    pdf_input = Path(args.pdf)
    metadata_input = Path(args.metadata)
    if not pdf_input.is_absolute():
        pdf_input = (root / pdf_input).resolve()
    if not metadata_input.is_absolute():
        metadata_input = (root / metadata_input).resolve()
    verify_inputs(pdf_input, metadata_input)
    if not args.apply:
        print(
            f"[READY] doc={DOC_ID} candidate={SOURCE_CANDIDATE_ID} "
            f"page={SELECTED_PAGE_1BASED} rights=verified; rerun with --apply"
        )
        return 0

    snapshot_dir = root / "derived" / "snapshots" / "2026-08-17-wave212-nasa-saws"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for relative in MUTABLE_REGISTRIES:
        source = root / relative
        destination = snapshot_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)

    source_dir = root / "microtext" / "docs" / "source_intake_2026_08_17_wave212"
    source_dir.mkdir(parents=True, exist_ok=True)
    pdf_rel = Path("microtext/docs/source_intake_2026_08_17_wave212") / f"{DOC_ID}.pdf"
    metadata_rel = Path("microtext/docs/source_intake_2026_08_17_wave212") / "ntrs_20200004325_metadata.json"
    rights_rel = Path("microtext/docs/source_intake_2026_08_17_wave212") / "RIGHTS_EVIDENCE.md"
    copy_verified(pdf_input, root / pdf_rel, PDF_SHA256)
    copy_verified(metadata_input, root / metadata_rel, METADATA_SHA256)
    (root / rights_rel).write_text(rights_evidence_text(), encoding="utf-8")

    page_rel = Path("derived/pages_300dpi") / DOC_ID / f"page_{SELECTED_PAGE_INDEX:03d}.png"
    textlayer_rel = Path("derived/textlayer") / DOC_ID / f"page_{SELECTED_PAGE_INDEX:03d}.json"
    textlayer_spans = render_and_extract(root / pdf_rel, root / page_rel, root / textlayer_rel)

    manifest_row = {
        "type": "doc",
        "doc_id": DOC_ID,
        "task": "microtext",
        "source_candidate_id": SOURCE_CANDIDATE_ID,
        "same_model_id": "nasa_saws_rfc_pid",
        "domain": "pid",
        "doc_type": "federal_regenerative_fuel_cell_pid_report_pdf",
        "version": {
            "ntrs_id": NTRS_ID,
            "report_number": "NASA/TM-2018-219945",
            "receipt_date": f"{IMPORT_DATE}-{WAVE}",
        },
        "path": pdf_rel.as_posix(),
        "sha256": PDF_SHA256,
        "pages": PDF_PAGES,
        "render": {"dpi": 300, "colorspace": "rgb", "rotate_cw90": False},
        "derived": {
            "pages_dir": f"derived/pages_300dpi/{DOC_ID}",
            "textlayer_dir": f"derived/textlayer/{DOC_ID}",
            "rendered_page_selection_1based": str(SELECTED_PAGE_1BASED),
        },
        "source_url": SOURCE_URL,
        "direct_source_url": DIRECT_SOURCE_URL,
        "public_status": PUBLIC_STATUS,
        "license_note": (
            "NASA NTRS records distribution PUBLIC, determination "
            "GOV_PUBLIC_USE_PERMITTED, no third-party material, and NO export-control flags."
        ),
        "rights_evidence_url": METADATA_URL,
        "rights_evidence_path": rights_rel.as_posix(),
        "notes": (
            "PDF page 104 contains NASA Figure 82, a full-page notional PEM fuel-cell P&ID. "
            "Neighboring explanatory and duplicate overview figures are excluded. Registration "
            "and candidate generation do not imply Gold promotion."
        ),
    }
    manifest_added = append_jsonl(root / "manifest.jsonl", manifest_row)

    inventory_row = {
        "doc_id": DOC_ID,
        "domain": "pid",
        "task": "microtext",
        "public_status": PUBLIC_STATUS,
        "source_path": pdf_rel.as_posix(),
        "rendered_pages": "1",
        "textlayer_spans": str(textlayer_spans),
        "mineable_candidates": "",
        "review_rows": "0",
        "open_review_rows": "0",
        "next_step": "mine_guarded_pid_labels",
        "priority_score": "40",
        "source_url": SOURCE_URL,
        "path": pdf_rel.as_posix(),
        "textlayer_status": "present",
        "render_status": "present",
        "notes": (
            f"Wave212 hash-pinned NTRS intake; selected PDF page {SELECTED_PAGE_1BASED}; "
            "source-only registration; no Gold promotion."
        ),
    }
    inventory_added = append_inventory(root / "SOURCE_INVENTORY.csv", inventory_row)

    spec_rel = Path("derived/quality/v2_0_wave212_pid082_registration_spec.json")
    (root / spec_rel).write_text(
        json.dumps(candidate_spec(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    registration = candidate_register.register(root, root / spec_rel, "2026-08-17-wave212")
    registration_rel = Path("derived/quality/v2_0_wave212_pid082_registration.json")
    (root / registration_rel).write_text(
        json.dumps(registration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    receipt = {
        "goal": "Gold v2.0 Global",
        "wave": WAVE,
        "import_date": IMPORT_DATE,
        "source_candidate_id": SOURCE_CANDIDATE_ID,
        "doc_id": DOC_ID,
        "ntrs_id": NTRS_ID,
        "source_url": SOURCE_URL,
        "direct_source_url": DIRECT_SOURCE_URL,
        "source_path": pdf_rel.as_posix(),
        "source_sha256": PDF_SHA256,
        "metadata_path": metadata_rel.as_posix(),
        "metadata_sha256": METADATA_SHA256,
        "rights_evidence_path": rights_rel.as_posix(),
        "selected_page_1based": SELECTED_PAGE_1BASED,
        "page_image": page_rel.as_posix(),
        "textlayer_path": textlayer_rel.as_posix(),
        "textlayer_spans": textlayer_spans,
        "manifest_added": manifest_added,
        "inventory_added": inventory_added,
        "registration_report": registration_rel.as_posix(),
        "snapshot_dir": snapshot_dir.relative_to(root).as_posix(),
        "candidate_rows_generated": 0,
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
    }
    receipt_path = root / "derived" / "source_imports" / "nasa_saws_pid_import_2026-08-17-wave212.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] doc={DOC_ID} page={SELECTED_PAGE_1BASED} spans={textlayer_spans} "
        f"manifest+={int(manifest_added)} inventory+={int(inventory_added)} active_gold_modified=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
