#!/usr/bin/env python3
"""Import NASA Brayton drawing 306882 as review-only source data."""
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
WAVE = "wave213"
NTRS_ID = "19700032848"
DOC_ID = "nasa_19700032848_brayton_pid_control"
VERSION_ID = "nasa_19700032848_wave213"
SOURCE_CANDIDATE_ID = "pid_083"
SOURCE_URL = f"https://ntrs.nasa.gov/citations/{NTRS_ID}"
METADATA_URL = f"https://ntrs.nasa.gov/api/citations/{NTRS_ID}"
DIRECT_SOURCE_URL = (
    f"https://ntrs.nasa.gov/api/citations/{NTRS_ID}/downloads/{NTRS_ID}.pdf"
)
PUBLIC_STATUS = "government_public_use_permitted_nasa_candidate"
PDF_SHA256 = "2fa65ba7b93eccdb8edc65bde3b2e08e3ed4c7309400edc6ed1cdae803143f0e"
METADATA_SHA256 = "fcfc95e459f87790e05cb73879d62af53220f639f0c915c5ccec80971aa2b23a"
PDF_PAGES = 152
SELECTED_PAGES_1BASED = (20, 21)
SELECTED_PAGE_INDEXES = tuple(page - 1 for page in SELECTED_PAGES_1BASED)

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
    if copyright_row.get("thirdPartyPermissionsProduced") is not False:
        failures.append("third-party permissions flag is not explicitly false")
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
        for page_index in SELECTED_PAGE_INDEXES:
            page = document[page_index]
            if not page.get_images(full=True):
                raise ValueError(f"selected page {page_index + 1} has no embedded drawing image")
            if page.rect.width < 1200 or page.rect.height < 780:
                raise ValueError(f"selected page {page_index + 1} is not the expected foldout geometry")
        report_description = document[15].get_text().lower()
        if "306882" not in report_description or "piping and instrumentation" not in report_description:
            raise ValueError("report text does not identify drawing 306882 as the P&ID")
        if "legend" not in document[20].get_text().lower():
            raise ValueError("selected title-block half lacks its extracted legend marker")
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


def render_and_extract(
    pdf_path: Path, page_dir: Path, textlayer_dir: Path
) -> tuple[dict[int, int], dict[int, str]]:
    document = fitz.open(pdf_path)
    span_counts: dict[int, int] = {}
    page_paths: dict[int, str] = {}
    try:
        for page_index in SELECTED_PAGE_INDEXES:
            page = document[page_index]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
            page_dir.mkdir(parents=True, exist_ok=True)
            page_path = page_dir / f"page_{page_index:03d}.png"
            pixmap.save(page_path)
            page_paths[page_index] = page_path.name

            spans: list[dict[str, Any]] = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = str(span.get("text") or "").strip()
                        if text:
                            spans.append(
                                {
                                    "page_index": page_index,
                                    "text": text,
                                    "bbox": span.get("bbox"),
                                    "origin": span.get("origin"),
                                    "size": span.get("size"),
                                    "font": span.get("font"),
                                    "flags": span.get("flags"),
                                    "color": span.get("color"),
                                }
                            )
            textlayer_dir.mkdir(parents=True, exist_ok=True)
            (textlayer_dir / f"page_{page_index:03d}.json").write_text(
                json.dumps({"doc_page": page_index, "spans": spans}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            span_counts[page_index] = len(spans)
    finally:
        document.close()
    return span_counts, page_paths


def candidate_spec() -> dict[str, Any]:
    notes = (
        "NASA NTRS records the Brayton power-system report as PUBLIC with "
        "GOV_PUBLIC_USE_PERMITTED, no third-party material, and no export-control flags. "
        "Exact metadata, payload hash, both halves of drawing 306882, text layers, and "
        "source URLs are preserved; all candidates remain outside Gold until human "
        "adjudication and strict promotion gates pass."
    )
    return {
        "candidate": {
            "candidate_id": SOURCE_CANDIDATE_ID,
            "path_hint": "candidate/pid/nasa_brayton_306882",
            "domain": "pid",
            "task_fit": "microtext",
            "rights_tier": PUBLIC_STATUS,
            "source_url": SOURCE_URL,
            "source_family": "nasa_brayton_306882",
            "likely_asset_type": "federal_brayton_pid_control_report_pdf",
            "revision_family_potential": "low",
            "annotation_yield": "high",
            "priority_bucket": "A",
            "notes": notes,
        },
        "doc_ids": [DOC_ID],
        "priority_score": 15,
        "validation": {
            "release_posture": "release_candidate",
            "validation_next_action": "mine_guarded_pid_labels",
        },
    }


def rights_evidence_text() -> str:
    pages = ", ".join(str(value) for value in SELECTED_PAGES_1BASED)
    return f"""# NASA NTRS rights evidence

- NTRS record: {SOURCE_URL}
- NTRS metadata API: {METADATA_URL}
- Direct PDF: {DIRECT_SOURCE_URL}
- NTRS ID: `{NTRS_ID}`
- Distribution: `PUBLIC`
- Copyright determination: `GOV_PUBLIC_USE_PERMITTED`
- Contains third-party material: `false`
- Third-party permissions produced: `false`
- Export control, EAR, and ITAR: `NO`
- PDF SHA-256: `{PDF_SHA256.upper()}`
- Metadata SHA-256: `{METADATA_SHA256.upper()}`

The exact metadata response is preserved beside the payload. This source is
not described as public domain; release use follows the NTRS public-use
determination. Only PDF pages {pages}, the two scanned halves of NASA drawing
306882, are selected for review-candidate mining. No unreviewed row is Gold.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--pdf", default=".codex_tmp/wave213_nasa_19700032848/source.pdf"
    )
    parser.add_argument(
        "--metadata", default=".codex_tmp/wave213_nasa_19700032848/metadata.json"
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
            f"pages={SELECTED_PAGES_1BASED} rights=verified; rerun with --apply"
        )
        return 0

    snapshot_dir = root / "derived" / "snapshots" / "2026-08-17-wave213-nasa-brayton"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for relative in MUTABLE_REGISTRIES:
        source = root / relative
        destination = snapshot_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)

    source_rel_dir = Path("microtext/docs/source_intake_2026_08_17_wave213")
    pdf_rel = source_rel_dir / f"{DOC_ID}.pdf"
    metadata_rel = source_rel_dir / "ntrs_19700032848_metadata.json"
    rights_rel = source_rel_dir / "RIGHTS_EVIDENCE.md"
    copy_verified(pdf_input, root / pdf_rel, PDF_SHA256)
    copy_verified(metadata_input, root / metadata_rel, METADATA_SHA256)
    (root / rights_rel).write_text(rights_evidence_text(), encoding="utf-8")

    page_rel_dir = Path("derived/pages_300dpi") / DOC_ID
    textlayer_rel_dir = Path("derived/textlayer") / DOC_ID
    span_counts, page_names = render_and_extract(
        root / pdf_rel, root / page_rel_dir, root / textlayer_rel_dir
    )

    manifest_row = {
        "type": "doc",
        "doc_id": DOC_ID,
        "task": "microtext",
        "source_candidate_id": SOURCE_CANDIDATE_ID,
        "same_model_id": "nasa_brayton_306882",
        "domain": "pid",
        "doc_type": "federal_brayton_pid_control_report_pdf",
        "version": {
            "ntrs_id": NTRS_ID,
            "report_number": "APS-5284-R24",
            "drawing_number": "306882",
            "receipt_date": f"{IMPORT_DATE}-{WAVE}",
        },
        "path": pdf_rel.as_posix(),
        "sha256": PDF_SHA256,
        "pages": PDF_PAGES,
        "render": {"dpi": 300, "colorspace": "rgb", "rotate_cw90": False},
        "derived": {
            "pages_dir": page_rel_dir.as_posix(),
            "textlayer_dir": textlayer_rel_dir.as_posix(),
            "rendered_page_selection_1based": ",".join(
                str(value) for value in SELECTED_PAGES_1BASED
            ),
        },
        "source_url": SOURCE_URL,
        "direct_source_url": DIRECT_SOURCE_URL,
        "public_status": PUBLIC_STATUS,
        "license_note": (
            "NASA NTRS records distribution PUBLIC, determination "
            "GOV_PUBLIC_USE_PERMITTED, no third-party material or permissions, "
            "and NO export-control flags."
        ),
        "rights_evidence_url": METADATA_URL,
        "rights_evidence_path": rights_rel.as_posix(),
        "notes": (
            "PDF pages 20-21 are the two scanned halves of drawing 306882, Piping & "
            "Instrumentation Diagram, Brayton Turboelectric Engine Control System. "
            "Registration and candidate generation do not imply Gold promotion."
        ),
    }
    manifest_added = append_jsonl(root / "manifest.jsonl", manifest_row)

    inventory_row = {
        "doc_id": DOC_ID,
        "domain": "pid",
        "task": "microtext",
        "public_status": PUBLIC_STATUS,
        "source_path": pdf_rel.as_posix(),
        "rendered_pages": str(len(SELECTED_PAGES_1BASED)),
        "textlayer_spans": str(sum(span_counts.values())),
        "mineable_candidates": "",
        "review_rows": "0",
        "open_review_rows": "0",
        "next_step": "mine_guarded_pid_labels",
        "priority_score": "50",
        "source_url": SOURCE_URL,
        "path": pdf_rel.as_posix(),
        "textlayer_status": "present",
        "render_status": "present",
        "notes": (
            "Wave213 hash-pinned NTRS intake; selected PDF pages 20-21, the two "
            "halves of drawing 306882; source-only registration; no Gold promotion."
        ),
    }
    inventory_added = append_inventory(root / "SOURCE_INVENTORY.csv", inventory_row)

    spec_rel = Path("derived/quality/v2_0_wave213_pid083_registration_spec.json")
    (root / spec_rel).write_text(
        json.dumps(candidate_spec(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    registration = candidate_register.register(root, root / spec_rel, "2026-08-17-wave213")
    registration_rel = Path("derived/quality/v2_0_wave213_pid083_registration.json")
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
        "selected_pages_1based": list(SELECTED_PAGES_1BASED),
        "page_images": [
            (page_rel_dir / page_names[index]).as_posix() for index in SELECTED_PAGE_INDEXES
        ],
        "textlayer_dir": textlayer_rel_dir.as_posix(),
        "textlayer_spans_by_page": {str(key): value for key, value in span_counts.items()},
        "manifest_added": manifest_added,
        "inventory_added": inventory_added,
        "registration_report": registration_rel.as_posix(),
        "snapshot_dir": snapshot_dir.relative_to(root).as_posix(),
        "candidate_rows_generated": 0,
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
    }
    receipt_path = (
        root / "derived" / "source_imports" / "nasa_brayton_pid_import_2026-08-17-wave213.json"
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] doc={DOC_ID} pages={SELECTED_PAGES_1BASED} "
        f"spans={sum(span_counts.values())} manifest+={int(manifest_added)} "
        f"inventory+={int(inventory_added)} active_gold_modified=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
