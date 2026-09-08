#!/usr/bin/env python3
"""Import the NASA GOX thruster P&ID as review-only source data."""
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


IMPORT_DATE = "2026-08-22"
WAVE = "wave556"
NTRS_ID = "20170005169"
DOC_ID = "nasa_20170005169_gox_thruster_pid"
VERSION_ID = "nasa_20170005169_wave556"
SOURCE_CANDIDATE_ID = "pid_086"
SOURCE_URL = f"https://ntrs.nasa.gov/citations/{NTRS_ID}"
METADATA_URL = f"https://ntrs.nasa.gov/api/citations/{NTRS_ID}"
DIRECT_SOURCE_URL = f"https://ntrs.nasa.gov/api/citations/{NTRS_ID}/downloads/{NTRS_ID}.pdf"
PUBLIC_STATUS = "government_public_use_permitted_nasa_candidate"
PDF_SHA256 = "094d4df50897b4f9461ed74e0331c249d37b4b81ab198537ec0ba61cdcc74e3a"
METADATA_SHA256 = "00fe48bb33910d6f2b909987b2e8de6a840da8c4ec885e9a5d758433706b0a58"
PDF_PAGES = 232
SELECTED_PAGE_1BASED = 181
SELECTED_PAGE_INDEX = SELECTED_PAGE_1BASED - 1
IMAGE_RECT = (55.95, 102.54, 558.05, 620.96)

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
    if copyright_row.get("determinationType") != "PUBLIC_USE_PERMITTED":
        failures.append("copyright determination is not PUBLIC_USE_PERMITTED")
    if copyright_row.get("containsThirdPartyMaterial") is not False:
        failures.append("third-party material is not explicitly false")
    if copyright_row.get("thirdPartyPermissionsProduced") is not False:
        failures.append("third-party permissions are not explicitly false")
    if export_row.get("isExportControl") != "NO":
        failures.append("export-control status is not NO")
    if export_row.get("ear") != "NO" or export_row.get("itar") != "NO":
        failures.append("EAR/ITAR status is not NO")
    downloads = payload.get("downloads") or []
    if not any(
        row.get("mimetype") == "application/pdf" and row.get("name") == f"{NTRS_ID}.pdf"
        for row in downloads
    ):
        failures.append("expected official PDF download is absent")
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
        page = document[SELECTED_PAGE_INDEX]
        caption = " ".join(page.get_text().split()).lower()
        if "figure 3" not in caption or "piping and instrumentation diagram" not in caption:
            raise ValueError("selected page lacks the expected Figure 3 P&ID caption")
        images = page.get_images(full=True)
        if len(images) != 1:
            raise ValueError(f"expected one embedded P&ID image, got {len(images)}")
        rects = page.get_image_rects(images[0][0])
        if len(rects) != 1:
            raise ValueError(f"expected one P&ID image placement, got {len(rects)}")
        actual = tuple(round(value, 2) for value in rects[0])
        expected = tuple(round(value, 2) for value in IMAGE_RECT)
        if any(abs(left - right) > 0.05 for left, right in zip(actual, expected)):
            raise ValueError(f"P&ID image rectangle changed: expected {expected}, got {actual}")
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


def append_manifest(path: Path, row: dict[str, Any]) -> bool:
    matches = [
        item
        for item in load_jsonl(path)
        if item.get("type") == "doc" and item.get("doc_id") == DOC_ID
    ]
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
        if matches[0].get("source_path") != row["source_path"]:
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
        "NASA NTRS records this technical memorandum as PUBLIC with "
        "PUBLIC_USE_PERMITTED, no third-party material, and no export-control flags. "
        "The exact payload, metadata, Figure 3 render, and source URLs are pinned. "
        "Rows remain outside Gold until adjudication and strict promotion gates pass."
    )
    return {
        "candidate": {
            "candidate_id": SOURCE_CANDIDATE_ID,
            "path_hint": "candidate/pid/nasa_gox_thruster_pid",
            "domain": "pid",
            "task_fit": "microtext",
            "rights_tier": PUBLIC_STATUS,
            "source_url": SOURCE_URL,
            "source_family": "nasa_gox_thruster_pid",
            "likely_asset_type": "federal_thruster_test_pid_report_pdf",
            "revision_family_potential": "low",
            "annotation_yield": "high",
            "priority_bucket": "A",
            "notes": notes,
        },
        "doc_ids": [DOC_ID],
        "priority_score": 12,
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
- Copyright determination: `PUBLIC_USE_PERMITTED`
- Contains third-party material: `false`
- Third-party permissions produced: `false`
- Export control, EAR, and ITAR: `NO`
- PDF SHA-256: `{PDF_SHA256.upper()}`
- Metadata SHA-256: `{METADATA_SHA256.upper()}`

The exact metadata response is preserved beside the payload. This source is
not described as public domain; release use follows the NTRS public-use
determination. Only PDF page {SELECTED_PAGE_1BASED}, Figure 3, is selected for
review-candidate mining. No unreviewed row is active Gold.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--pdf", default=".codex_tmp/wave556_nasa_oxygen/20170005169.pdf")
    parser.add_argument(
        "--metadata", default=".codex_tmp/wave556_nasa_oxygen/20170005169.json"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

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

    snapshot_dir = root / "derived/snapshots/2026-08-22-wave556-nasa-gox-thruster"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for relative in MUTABLE_REGISTRIES:
        source = root / relative
        destination = snapshot_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)

    source_rel = Path("microtext/docs/source_intake_2026_08_22_wave556_nasa_gox")
    pdf_rel = source_rel / f"{NTRS_ID}.pdf"
    metadata_rel = source_rel / f"ntrs_{NTRS_ID}_metadata.json"
    rights_rel = source_rel / "RIGHTS_EVIDENCE.md"
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
        "same_model_id": "nasa_gox_thruster_pid",
        "domain": "pid",
        "doc_type": "federal_thruster_test_pid_report_pdf",
        "version": {
            "ntrs_id": NTRS_ID,
            "report_number": "NASA/TM-2017-218234",
            "embedded_article": "Vacuum Test Measurements of Novel Green-Propellant Thruster for Small Spacecraft",
            "figure": "Figure 3",
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
            "NASA NTRS records distribution PUBLIC, determination PUBLIC_USE_PERMITTED, "
            "no third-party material or permissions, and NO export-control flags."
        ),
        "rights_evidence_url": METADATA_URL,
        "rights_evidence_path": rights_rel.as_posix(),
        "notes": (
            "PDF page 181 contains Figure 3, a GOX thruster-test piping and instrumentation "
            "diagram. Registration and candidate generation do not imply Gold promotion."
        ),
    }
    manifest_added = append_manifest(root / "manifest.jsonl", manifest_row)

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
        "priority_score": "50",
        "source_url": SOURCE_URL,
        "path": pdf_rel.as_posix(),
        "textlayer_status": "present_caption_only",
        "render_status": "present",
        "notes": (
            f"Wave556 hash-pinned NTRS intake; selected PDF page {SELECTED_PAGE_1BASED}; "
            "source-only registration; no Gold promotion."
        ),
    }
    inventory_added = append_inventory(root / "SOURCE_INVENTORY.csv", inventory_row)

    spec_rel = Path("derived/quality/v2_0_wave556_pid086_registration_spec.json")
    (root / spec_rel).write_text(
        json.dumps(candidate_spec(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    registration = candidate_register.register(root, root / spec_rel, "2026-08-22-wave556")
    registration_rel = Path("derived/quality/v2_0_wave556_pid086_registration.json")
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
    receipt_path = root / "derived/source_imports/nasa_gox_thruster_pid_2026-08-22-wave556.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] doc={DOC_ID} page={SELECTED_PAGE_1BASED} spans={textlayer_spans} "
        f"manifest+={int(manifest_added)} inventory+={int(inventory_added)} "
        "active_gold_modified=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
