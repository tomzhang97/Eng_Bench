#!/usr/bin/env python3
"""Import two rights-clear Pipettin mechanical drawings as review-only MicroText.

Two attractive manufacturing PDFs are deliberately excluded because their
title blocks contain contradictory reproduction language. Nothing in this
importer edits active annotation JSONL or unified Gold.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageStat

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.extract_svg_textlayer import extract_rows, write_jsonl


IMPORT_DATE = "2026-08-17"
WAVE = "wave211"
COMMIT = "b3f0c20615c7595e6b2b22d72bb7132a9f409158"
VERSION_ID = f"git_{COMMIT[:12]}"
REPO_URL = "https://gitlab.com/open-la/pipettin-bot"
PUBLIC_STATUS = "cern_ohl_s_2_0_hardware_with_cc_by_sa_4_0_documentation_candidate"
SOURCE_CANDIDATE_ID = "mech_024"

EVIDENCE_FILES = {
    "LICENSES.md": "a8591e7bed5e7e15c6dcbae253b6acd7494189b8fd6c6a077790874dd7558137",
    "README.md": "20a7ed260d64ed61f51f2a05f5c22b4cc1e28893a78cd864659c8bbb4f1ffd6b",
}

DOCS = [
    {
        "upstream_path": "models/labware/plasticware/platform_dimensions_draft_drawings.svg",
        "local_name": "platform_dimensions_draft_drawings.svg",
        "doc_id": "pipettin_platform_dimensions_draft",
        "sha256": "575ce4c5d5fbd8a3bd1c87979c91c4b7e0977987a45fe6fea29138483c790cc3",
        "candidates": [
            {"suffix": "diameter_62", "bbox": [246, 348, 326, 440], "text": "\u230062", "context": "diameter 62"},
            {"suffix": "height_5", "bbox": [50, 18, 90, 82], "text": "5", "context": "height 5"},
            {"suffix": "height_31", "bbox": [1158, 80, 1222, 150], "text": "31", "context": "height 31"},
            {"suffix": "width_20", "bbox": [718, 580, 790, 655], "text": "20", "context": "width 20"},
            {"suffix": "length_50", "bbox": [596, 886, 674, 950], "text": "50", "context": "length 50"},
            {"suffix": "length_51", "bbox": [1548, 884, 1615, 955], "text": "51", "context": "length 51"},
        ],
    },
    {
        "upstream_path": "models/labware/well_plates/images/96-Well_plate.svg",
        "local_name": "96-Well_plate.svg",
        "doc_id": "pipettin_96_well_plate_dimensions",
        "sha256": "81891b3dc0a80d2ce1a9c2d1c1717ff1d1743134200a61af80728d990890d68f",
        "candidates": [
            {"suffix": "127_76_mm", "bbox": [360, 0, 540, 45], "text": "127.76 mm", "context": "127.76 mm"},
            {"suffix": "9_mm", "bbox": [814, 106, 908, 168], "text": "9 mm", "context": "9 mm"},
            {"suffix": "85_48_mm", "bbox": [786, 197, 850, 360], "text": "85.48 mm", "context": "85.48 mm"},
            {"suffix": "72_mm", "bbox": [0, 242, 45, 360], "text": "72 mm", "context": "72 mm"},
            {"suffix": "108_mm", "bbox": [340, 535, 480, 579], "text": "108 mm", "context": "108 mm"},
        ],
    },
]

HELD_PDFS = [
    {
        "path": "models/MK3/tools/pmulti-MK1/pipette/drawings/PIP-TIP-HOLDER-PZ0101-COMBINED-IT07-A2.0.pdf",
        "sha256": "0cf6eb55c256a461dadedb51823fe0d6b8b52be3395e7fadd09382603ab0e8d1",
    },
    {
        "path": "models/MK3/tools/pmulti-MK1/pipette/drawings/PIP-TIP-HOLDER-V02-PZ0101-COMBINED-IT07-A2.0-V02.pdf",
        "sha256": "85f56f571c7ee3d478f2e8930ae3a5046957ae10b19f4473a3ca1c3c646d16ff",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_hash(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"hash mismatch for {path}: expected {expected}, got {actual}")


def pinned_commit(source_repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def verify_source_repo(source_repo: Path) -> None:
    actual_commit = pinned_commit(source_repo)
    if actual_commit != COMMIT:
        raise ValueError(f"expected Pipettin commit {COMMIT}, got {actual_commit}")
    for relpath, expected in EVIDENCE_FILES.items():
        assert_hash(source_repo / relpath, expected)
    for spec in DOCS:
        assert_hash(source_repo / spec["upstream_path"], spec["sha256"])
    conflict_phrase = "can't be reproduced or communicated without our written consent"
    for held in HELD_PDFS:
        path = source_repo / held["path"]
        assert_hash(path, held["sha256"])
        document = fitz.open(path)
        try:
            text = "\n".join(page.get_text() for page in document).lower()
        finally:
            document.close()
        if "license: cern-ohl-s" not in text or conflict_phrase not in text:
            raise ValueError(f"expected contradictory title-block terms were not found in {path}")


def copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    assert_hash(source, expected_hash)
    if destination.exists():
        assert_hash(destination, expected_hash)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    assert_hash(destination, expected_hash)


def render_svg(svg_path: Path, image_path: Path) -> None:
    document = fitz.open(stream=svg_path.read_bytes(), filetype="svg")
    try:
        pixmap = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(image_path)
    finally:
        document.close()


def crop_metrics(image: Image.Image) -> dict[str, float | int]:
    gray = image.convert("L")
    stat = ImageStat.Stat(gray)
    extrema = stat.extrema[0]
    histogram = gray.histogram()
    total = max(1, sum(histogram))
    return {
        "width": gray.width,
        "height": gray.height,
        "minimum": int(extrema[0]),
        "maximum": int(extrema[1]),
        "dynamic_range": int(extrema[1] - extrema[0]),
        "mean": round(float(stat.mean[0]), 3),
        "stddev": round(float(stat.stddev[0]), 3),
        "ink_fraction_below_245": round(float(sum(histogram[:245]) / total), 6),
    }


def build_review_rows(root: Path, spec: dict[str, Any], image_path: Path) -> list[dict[str, Any]]:
    crop_dir = root / "derived" / "quality" / "wave211_pipettin_mechanical_evidence_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with Image.open(image_path) as image:
        width, height = image.size
        for candidate in spec["candidates"]:
            candidate_id = f"mtcand__{spec['doc_id']}__{VERSION_ID}__p0000__dim_{candidate['suffix']}"
            bbox = list(candidate["bbox"])
            if not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
                raise ValueError(f"out-of-bounds crop {candidate_id}: {bbox} vs {(width, height)}")
            crop = image.crop(tuple(bbox))
            crop_rel = Path("derived/quality/wave211_pipettin_mechanical_evidence_crops") / f"{candidate_id}.png"
            crop.save(root / crop_rel)
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "doc_id": spec["doc_id"],
                    "version_id": VERSION_ID,
                    "page_index": 0,
                    "bbox": bbox,
                    "target_text": candidate["text"],
                    "proposed_text": candidate["text"],
                    "raw_text": candidate["context"],
                    "category": "dimension_value",
                    "source": "svg_native_text_candidate",
                    "review_status": "needs_review",
                    "corrected_text": "",
                    "question_text": "What dimension value is shown in this marked mechanical drawing region?",
                    "image_path": f"derived/pages_300dpi/{spec['doc_id']}/page_000.png",
                    "text_context": candidate["context"],
                    "review_notes": "Review-only Pipettin mechanical drawing candidate; confirm transcription and dimension category.",
                    "machine_qa_status": "selected_for_human_review",
                    "machine_qa_notes": "Exact native SVG text and manually inspected crop; no Gold promotion performed.",
                    "source_candidate_id": SOURCE_CANDIDATE_ID,
                    "source_payload_sha256": spec["sha256"],
                    "source_public_status": PUBLIC_STATUS,
                    "reserved_split": "test",
                    "promotion_state": "unreviewed_candidate",
                    "safe_to_merge_gold": False,
                    "review_bucket": "v2_0_source_expansion",
                    "crop_path": crop_rel.as_posix(),
                    "evidence_audit_status": "pixel_informative_needs_visual_review",
                    "evidence_audit_source": "authoritative_svg_render_bbox",
                    "evidence_audit_crop_bbox": bbox,
                    "evidence_audit_page_bbox_metrics": crop_metrics(crop),
                }
            )
    return rows


def load_manifest(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def read_inventory(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def attribution_text() -> str:
    return f"""# Pipettin mechanical drawings attribution

- Project: Open Lab Automata / Pipettin Bot
- Repository: {REPO_URL}
- Pinned commit: `{COMMIT}`
- Imported paths: `models/labware/plasticware/platform_dimensions_draft_drawings.svg` and `models/labware/well_plates/images/96-Well_plate.svg`
- Hardware license: CERN Open Hardware Licence Version 2 - Strongly Reciprocal
- Web documentation license: Creative Commons Attribution-ShareAlike 4.0 International
- Local release posture: `{PUBLIC_STATUS}`

The exact source hashes, repository README, and complete LICENSES notice are
preserved. Two tip-holder PDFs at the same commit were excluded because their
title blocks combine CERN-OHL-S with contradictory no-reproduction language.
No unreviewed candidate is active Gold.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--source-repo", default=".codex_tmp/wave211_pipettin_bot")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    source_repo = Path(args.source_repo)
    if not source_repo.is_absolute():
        source_repo = (root / source_repo).resolve()
    verify_source_repo(source_repo)
    if not args.apply:
        print(f"[READY] commit={COMMIT} docs={len(DOCS)} review_rows=11 held_pdfs=2; rerun with --apply")
        return 0

    manifest_path = root / "manifest.jsonl"
    inventory_path = root / "SOURCE_INVENTORY.csv"
    snapshot_dir = root / "derived" / "snapshots" / "2026-08-17-wave211-pipettin"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for path in (manifest_path, inventory_path):
        snapshot = snapshot_dir / path.name
        if not snapshot.exists():
            shutil.copy2(path, snapshot)

    source_dir = root / "microtext" / "docs" / "pipettin_bot_mechanical_drawings"
    source_dir.mkdir(parents=True, exist_ok=True)
    for relpath, expected in EVIDENCE_FILES.items():
        copy_verified(source_repo / relpath, source_dir / relpath, expected)
    (source_dir / "ATTRIBUTION.md").write_text(attribution_text(), encoding="utf-8")

    manifest_rows = load_manifest(manifest_path)
    manifest_by_id = {
        str(row.get("doc_id")): row for row in manifest_rows if row.get("type") == "doc"
    }
    inventory_fields, inventory_rows = read_inventory(inventory_path)
    inventory_by_id = {row.get("doc_id", ""): row for row in inventory_rows}
    manifest_appends: list[dict[str, Any]] = []
    inventory_appends: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    import_docs: list[dict[str, Any]] = []

    for spec in DOCS:
        doc_id = spec["doc_id"]
        svg_rel = Path("microtext/docs/pipettin_bot_mechanical_drawings") / spec["local_name"]
        page_rel = Path("derived/pages_300dpi") / doc_id / "page_000.png"
        copy_verified(source_repo / spec["upstream_path"], root / svg_rel, spec["sha256"])
        render_svg(root / svg_rel, root / page_rel)
        text_rows = extract_rows(root / svg_rel, root / page_rel)
        textlayer_rel = Path("derived/textlayer") / f"{doc_id}.jsonl"
        write_jsonl(root / textlayer_rel, text_rows)
        review_rows.extend(build_review_rows(root, spec, root / page_rel))

        quoted_path = spec["upstream_path"].replace(" ", "%20")
        source_blob_url = f"{REPO_URL}/-/blob/{COMMIT}/{quoted_path}"
        direct_url = f"{REPO_URL}/-/raw/{COMMIT}/{quoted_path}"
        manifest_row = {
            "type": "doc",
            "doc_id": doc_id,
            "task": "microtext",
            "source_candidate_id": SOURCE_CANDIDATE_ID,
            "same_model_id": "pipettin_bot_mechanical_drawings",
            "domain": "mechanical_cad",
            "doc_type": "svg_dimensioned_mechanical_drawing",
            "version": {"git_commit": COMMIT, "imported": IMPORT_DATE},
            "path": svg_rel.as_posix(),
            "sha256": spec["sha256"],
            "pages": 1,
            "render": {"dpi": None, "scale": 2, "colorspace": "rgb", "rotate_cw90": False},
            "derived": {
                "pages_dir": f"derived/pages_300dpi/{doc_id}",
                "rendered_source_path": page_rel.as_posix(),
                "textlayer_jsonl": textlayer_rel.as_posix(),
            },
            "source_url": source_blob_url,
            "direct_source_url": direct_url,
            "public_status": PUBLIC_STATUS,
            "license_note": "Repository LICENSES.md assigns hardware CERN-OHL-S-2.0 and web documentation CC BY-SA 4.0; preserve the complete notices and exact hashes.",
            "attribution_path": "microtext/docs/pipettin_bot_mechanical_drawings/ATTRIBUTION.md",
            "notes": "Wave211 review-only vector mechanical drawing; conflicting tip-holder PDFs excluded; no unreviewed row is active Gold.",
        }
        existing_manifest = manifest_by_id.get(doc_id)
        if existing_manifest:
            if existing_manifest.get("sha256") != spec["sha256"]:
                raise ValueError(f"manifest conflict for {doc_id}")
        else:
            manifest_appends.append(manifest_row)

        count = len(spec["candidates"])
        inventory_row = {
            "doc_id": doc_id,
            "domain": "mechanical_cad",
            "task": "microtext",
            "public_status": PUBLIC_STATUS,
            "source_path": svg_rel.as_posix(),
            "rendered_pages": "1",
            "textlayer_spans": str(len(text_rows)),
            "mineable_candidates": str(count),
            "review_rows": str(count),
            "open_review_rows": str(count),
            "unpacketed_open_review_rows": str(count),
            "fresh_open_review_rows": str(count),
            "unique_open_review_rows": str(count),
            "unique_fresh_open_review_rows": str(count),
            "next_step": "packet_fresh_open_review_rows",
            "priority_score": "40",
            "source_url": source_blob_url,
            "path": svg_rel.as_posix(),
            "textlayer_status": "present",
            "render_status": "present",
            "notes": f"Wave211 pinned commit {COMMIT}; {count} review-only dimension crops; two contradictory-rights PDFs excluded; no Gold promotion.",
        }
        existing_inventory = inventory_by_id.get(doc_id)
        if existing_inventory:
            if COMMIT not in str(existing_inventory.get("notes") or ""):
                raise ValueError(f"inventory conflict for {doc_id}")
        else:
            inventory_appends.append(inventory_row)
        import_docs.append(
            {
                "doc_id": doc_id,
                "source_path": svg_rel.as_posix(),
                "source_sha256": spec["sha256"],
                "page_image": page_rel.as_posix(),
                "textlayer_spans": len(text_rows),
                "review_candidates": count,
            }
        )

    if manifest_appends:
        with manifest_path.open("a", encoding="utf-8") as stream:
            for row in manifest_appends:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    if inventory_appends:
        with inventory_path.open("a", encoding="utf-8", newline="") as stream:
            csv.DictWriter(stream, fieldnames=inventory_fields).writerows(inventory_appends)

    queue_rel = Path("derived/review_queues/microtext_pipettin_mechanical_review_only_2026-08-17-wave211.jsonl")
    annotation_queue_rel = Path("microtext/annotations/microtext_review_pipettin_mechanical_2026-08-17-wave211.jsonl")
    write_jsonl(root / queue_rel, review_rows)
    write_jsonl(root / annotation_queue_rel, review_rows)

    rights_conflict = {
        "wave": WAVE,
        "pinned_commit": COMMIT,
        "held_assets": [
            {
                **held,
                "source_url": f"{REPO_URL}/-/blob/{COMMIT}/{held['path'].replace(' ', '%20')}",
                "blocker": "contradictory_title_block_reproduction_terms",
                "evidence": "The same title block states CERN-OHL-S and requires written consent for reproduction or communication.",
                "required_resolution": "Obtain an explicit upstream clarification or corrected drawing before release intake.",
            }
            for held in HELD_PDFS
        ],
        "held_from_import": True,
    }
    conflict_path = root / "derived" / "quality" / "v2_0_pipettin_pdf_rights_conflict_2026-08-17-wave211.json"
    conflict_path.write_text(json.dumps(rights_conflict, indent=2) + "\n", encoding="utf-8")

    receipt = {
        "wave": WAVE,
        "import_date": IMPORT_DATE,
        "source_repository": REPO_URL,
        "pinned_commit": COMMIT,
        "public_status": PUBLIC_STATUS,
        "source_candidate_id": SOURCE_CANDIDATE_ID,
        "documents": import_docs,
        "review_queue": queue_rel.as_posix(),
        "canonical_review_staging": annotation_queue_rel.as_posix(),
        "review_rows": len(review_rows),
        "rights_conflict_report": conflict_path.relative_to(root).as_posix(),
        "held_pdf_assets": len(HELD_PDFS),
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
        "snapshot_dir": snapshot_dir.relative_to(root).as_posix(),
    }
    receipt_path = root / "derived" / "source_imports" / "pipettin_mechanical_import_2026-08-17-wave211.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    quality_path = root / "derived" / "quality" / "v2_0_pipettin_mechanical_intake_2026-08-17-wave211.json"
    quality_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] docs={len(DOCS)} manifest+={len(manifest_appends)} inventory+={len(inventory_appends)} "
        f"review_rows={len(review_rows)} held_pdfs={len(HELD_PDFS)} active_gold_modified=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
