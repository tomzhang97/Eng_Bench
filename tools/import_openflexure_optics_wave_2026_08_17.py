#!/usr/bin/env python3
"""Import a pinned OpenFlexure optics-diagram slice as review-only MicroText.

The importer is deliberately narrow. It registers three authored SVG diagrams,
preserves upstream rights evidence, derives exact SVG text spans, and stages five
dimension candidates. It never edits active annotation JSONL or unified Gold.
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

from PIL import Image, ImageStat

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.extract_svg_textlayer import extract_rows, write_jsonl


IMPORT_DATE = "2026-08-17"
WAVE = "wave210"
COMMIT = "f2298b6803675c166bc753bfbb442bbacb034dd9"
VERSION_ID = f"git_{COMMIT[:12]}"
REPO_URL = "https://gitlab.com/openflexure/openflexure-microscope"
PUBLIC_STATUS = "gpl_3_0_documentation_with_cern_ohl_s_2_0_repo_candidate"
SOURCE_CANDIDATE_ID = "mech_017"
SOURCE_CANDIDATE_ALIASES = ["mech_042"]

EVIDENCE_FILES = {
    "License": "ab3d9eff0150304871b510f1ec20f9d035bed838b984392fa5519f8b9e666dd8",
    "README.md": "8ff7e10593d327800fa311607e59707f362cb950a39467eec96b20e5f57e5789",
    "okh.yml": "93f2506cbadcceb7e31a8a1a4e43428497f9ef7975195641db60f24a574516bc",
    "docs/info_pages/imaging_optics_explanation.md":
        "0564bbd31a24ef9fde90b092817962b1268028c274c47022583f32f926b814b4",
}

DOCS = [
    {
        "stem": "tube_length",
        "doc_id": "openflexure_microscope_tube_length",
        "svg_sha256": "fc47eb042deb96d6c7c5c1ac58d497714881db5cd0432f969e59681823f03e7b",
        "png_sha256": "6284f90077abc53390010abc781a797aa4fd16baa39edcbfd5501010e1399383",
        "candidates": [
            {
                "suffix": "160_mm",
                "bbox": [600, 45, 730, 100],
                "target_text": "160 mm",
                "text_context": "Mechanical tube length (160 mm)",
            },
            {
                "suffix": "150_mm_approx",
                "bbox": [505, 320, 640, 375],
                "target_text": "150 mm",
                "text_context": "(approximately 150 mm)",
            },
        ],
    },
    {
        "stem": "magnification",
        "doc_id": "openflexure_microscope_magnification",
        "svg_sha256": "831986d818e2081218605fce0d73a10daa903af84d91c66f7345016124647e68",
        "png_sha256": "c626b52cad6f51a266d0e9001321f456ce42b48f6eb319c7eaad88473f1e6d54",
        "candidates": [
            {
                "suffix": "17_mm",
                "bbox": [900, 94, 1040, 162],
                "target_text": "17mm",
                "text_context": "Diameter: 17mm",
            },
            {
                "suffix": "4_6_mm",
                "bbox": [925, 502, 1075, 575],
                "target_text": "4.6mm",
                "text_context": "Diagonal: 4.6mm",
            },
        ],
    },
    {
        "stem": "DigitalMicroscope",
        "doc_id": "openflexure_microscope_digital_microscope",
        "svg_sha256": "6a68614a8808f0e53a87fb21a559650027c169bac86708dd970b03ccb46781e6",
        "png_sha256": "74421d630330cced695ba32fbc5f2a73c306ad18f0fb22fc685ff35b66e6ef04",
        "candidates": [
            {
                "suffix": "150_mm",
                "bbox": [425, 252, 551, 321],
                "target_text": "150 mm",
                "text_context": "150 mm",
            },
        ],
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
        raise ValueError(f"expected OpenFlexure commit {COMMIT}, got {actual_commit}")
    for relpath, expected in EVIDENCE_FILES.items():
        assert_hash(source_repo / relpath, expected)
    for spec in DOCS:
        base = source_repo / "docs" / "diagrams" / spec["stem"]
        assert_hash(base.with_suffix(".svg"), spec["svg_sha256"])
        assert_hash(base.with_suffix(".png"), spec["png_sha256"])


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def read_inventory(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    assert_hash(source, expected_hash)
    if destination.exists():
        assert_hash(destination, expected_hash)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    assert_hash(destination, expected_hash)


def crop_metrics(image: Image.Image) -> dict[str, float | int]:
    gray = image.convert("L")
    stat = ImageStat.Stat(gray)
    extrema = stat.extrema[0]
    histogram = gray.histogram()
    total = max(1, sum(histogram))
    ink = sum(histogram[:245]) / total
    return {
        "width": gray.width,
        "height": gray.height,
        "minimum": int(extrema[0]),
        "maximum": int(extrema[1]),
        "dynamic_range": int(extrema[1] - extrema[0]),
        "mean": round(float(stat.mean[0]), 3),
        "stddev": round(float(stat.stddev[0]), 3),
        "ink_fraction_below_245": round(float(ink), 6),
    }


def build_review_rows(
    root: Path,
    spec: dict[str, Any],
    image_path: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    crop_dir = root / "derived" / "quality" / "wave210_openflexure_optics_evidence_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        width, height = image.size
        for candidate in spec["candidates"]:
            candidate_id = (
                f"mtcand__{spec['doc_id']}__{VERSION_ID}__p0000__dim_{candidate['suffix']}"
            )
            bbox = list(candidate["bbox"])
            if not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
                raise ValueError(f"out-of-bounds crop {candidate_id}: {bbox} vs {(width, height)}")
            crop = image.crop(tuple(bbox))
            crop_rel = Path("derived/quality/wave210_openflexure_optics_evidence_crops") / f"{candidate_id}.png"
            crop.save(root / crop_rel)
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "doc_id": spec["doc_id"],
                    "version_id": VERSION_ID,
                    "page_index": 0,
                    "bbox": bbox,
                    "target_text": candidate["target_text"],
                    "proposed_text": candidate["target_text"],
                    "raw_text": candidate["text_context"],
                    "category": "dimension_value",
                    "source": "svg_native_text_candidate",
                    "review_status": "needs_review",
                    "corrected_text": "",
                    "question_text": "What dimension value is shown in this marked engineering diagram region?",
                    "image_path": f"derived/pages_300dpi/{spec['doc_id']}/page_000.png",
                    "text_context": candidate["text_context"],
                    "review_notes": "Review-only OpenFlexure optics candidate; human transcription/category confirmation required.",
                    "machine_qa_status": "selected_for_human_review",
                    "machine_qa_notes": "Exact native SVG text and manually inspected target crop; no Gold promotion performed.",
                    "source_candidate_id": SOURCE_CANDIDATE_ID,
                    "source_candidate_aliases": SOURCE_CANDIDATE_ALIASES,
                    "source_payload_sha256": spec["svg_sha256"],
                    "source_public_status": PUBLIC_STATUS,
                    "reserved_split": "test",
                    "promotion_state": "unreviewed_candidate",
                    "safe_to_merge_gold": False,
                    "review_bucket": "v2_0_source_expansion",
                    "crop_path": crop_rel.as_posix(),
                    "evidence_audit_status": "pixel_informative_needs_visual_review",
                    "evidence_audit_source": "authoritative_upstream_png_bbox",
                    "evidence_audit_crop_bbox": bbox,
                    "evidence_audit_page_bbox_metrics": crop_metrics(crop),
                }
            )
    return rows


def attribution_text() -> str:
    return f"""# OpenFlexure Microscope optics diagrams attribution

- Upstream project: OpenFlexure Microscope
- Repository: {REPO_URL}
- Pinned commit: `{COMMIT}`
- Imported source paths: `docs/diagrams/tube_length.*`, `docs/diagrams/magnification.*`, and `docs/diagrams/DigitalMicroscope.*`
- Repository hardware license: CERN Open Hardware Licence Version 2 - Strongly Reciprocal (`License` and repository metadata)
- Documentation license metadata: GPL-3.0 (`okh.yml`)
- Local release posture: `{PUBLIC_STATUS}`

The benchmark preserves the exact SVG and PNG payload hashes, upstream README,
license, OKH metadata, and optics explanation. The more conservative combined
status is retained because upstream metadata distinguishes hardware and
documentation licensing. No unreviewed candidate is active Gold.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--source-repo", default=".codex_tmp/wave209_openflexure")
    parser.add_argument("--apply", action="store_true", help="Apply the verified import")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    source_repo = Path(args.source_repo)
    if not source_repo.is_absolute():
        source_repo = (root / source_repo).resolve()
    verify_source_repo(source_repo)

    if not args.apply:
        print(f"[READY] commit={COMMIT} docs={len(DOCS)} review_rows=5; rerun with --apply")
        return 0

    manifest_path = root / "manifest.jsonl"
    inventory_path = root / "SOURCE_INVENTORY.csv"
    snapshot_dir = root / "derived" / "snapshots" / "2026-08-17-wave210-openflexure"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for path in (manifest_path, inventory_path):
        snapshot = snapshot_dir / path.name
        if not snapshot.exists():
            shutil.copy2(path, snapshot)

    source_dir = root / "microtext" / "docs" / "openflexure_microscope_optics"
    source_dir.mkdir(parents=True, exist_ok=True)
    for relpath, expected in EVIDENCE_FILES.items():
        flat_name = relpath.replace("/", "__")
        copy_verified(source_repo / relpath, source_dir / flat_name, expected)
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
        stem = spec["stem"]
        doc_id = spec["doc_id"]
        upstream_base = source_repo / "docs" / "diagrams" / stem
        svg_rel = Path("microtext/docs/openflexure_microscope_optics") / f"{stem}.svg"
        png_rel = Path("microtext/docs/openflexure_microscope_optics") / f"{stem}.png"
        page_rel = Path("derived/pages_300dpi") / doc_id / "page_000.png"
        copy_verified(upstream_base.with_suffix(".svg"), root / svg_rel, spec["svg_sha256"])
        copy_verified(upstream_base.with_suffix(".png"), root / png_rel, spec["png_sha256"])
        copy_verified(upstream_base.with_suffix(".png"), root / page_rel, spec["png_sha256"])

        text_rows = extract_rows(root / svg_rel, root / page_rel)
        textlayer_rel = Path("derived/textlayer") / f"{doc_id}.jsonl"
        write_jsonl(root / textlayer_rel, text_rows)
        review_rows.extend(build_review_rows(root, spec, root / page_rel))

        source_blob_url = f"{REPO_URL}/-/blob/{COMMIT}/docs/diagrams/{stem}.svg"
        direct_url = f"{REPO_URL}/-/raw/{COMMIT}/docs/diagrams/{stem}.svg"
        manifest_row = {
            "type": "doc",
            "doc_id": doc_id,
            "task": "microtext",
            "source_candidate_id": SOURCE_CANDIDATE_ID,
            "source_candidate_aliases": SOURCE_CANDIDATE_ALIASES,
            "same_model_id": "openflexure_microscope_optics_docs",
            "domain": "mechanical_cad",
            "doc_type": "svg_optics_engineering_diagram",
            "version": {"git_commit": COMMIT, "imported": IMPORT_DATE},
            "path": svg_rel.as_posix(),
            "sha256": spec["svg_sha256"],
            "pages": 1,
            "render": {"dpi": None, "colorspace": "rgb", "rotate_cw90": False},
            "derived": {
                "pages_dir": f"derived/pages_300dpi/{doc_id}",
                "rendered_source_path": page_rel.as_posix(),
                "textlayer_jsonl": textlayer_rel.as_posix(),
            },
            "source_url": source_blob_url,
            "direct_source_url": direct_url,
            "public_status": PUBLIC_STATUS,
            "license_note": "Pinned OpenFlexure repository evidence records documentation as GPL-3.0 and hardware as CERN-OHL-S-2.0; preserve both notices and exact hashes.",
            "attribution_path": "microtext/docs/openflexure_microscope_optics/ATTRIBUTION.md",
            "notes": "Wave210 review-only optics slice; upstream companion PNG preserved; no unreviewed row is active Gold.",
        }
        existing_manifest = manifest_by_id.get(doc_id)
        if existing_manifest:
            if existing_manifest.get("sha256") != spec["svg_sha256"]:
                raise ValueError(f"manifest conflict for {doc_id}")
        else:
            manifest_appends.append(manifest_row)

        candidate_count = len(spec["candidates"])
        inventory_row = {
            "doc_id": doc_id,
            "domain": "mechanical_cad",
            "task": "microtext",
            "public_status": PUBLIC_STATUS,
            "source_path": svg_rel.as_posix(),
            "rendered_pages": "1",
            "textlayer_spans": str(len(text_rows)),
            "mineable_candidates": str(candidate_count),
            "review_rows": str(candidate_count),
            "open_review_rows": str(candidate_count),
            "unpacketed_open_review_rows": str(candidate_count),
            "fresh_open_review_rows": str(candidate_count),
            "unique_open_review_rows": str(candidate_count),
            "unique_fresh_open_review_rows": str(candidate_count),
            "next_step": "packet_fresh_open_review_rows",
            "priority_score": "40",
            "source_url": source_blob_url,
            "path": svg_rel.as_posix(),
            "textlayer_status": "present",
            "render_status": "present",
            "notes": f"Wave210 pinned commit {COMMIT}; exact SVG spans and {candidate_count} review-only dimension crops; no Gold promotion.",
        }
        existing_inventory = inventory_by_id.get(doc_id)
        if existing_inventory:
            existing_hash = str(existing_inventory.get("notes") or "")
            if COMMIT not in existing_hash:
                raise ValueError(f"inventory conflict for {doc_id}")
        else:
            inventory_appends.append(inventory_row)

        import_docs.append(
            {
                "doc_id": doc_id,
                "source_svg": svg_rel.as_posix(),
                "source_png": png_rel.as_posix(),
                "page_image": page_rel.as_posix(),
                "svg_sha256": spec["svg_sha256"],
                "png_sha256": spec["png_sha256"],
                "textlayer_spans": len(text_rows),
                "review_candidates": candidate_count,
            }
        )

    if manifest_appends:
        with manifest_path.open("a", encoding="utf-8") as stream:
            for row in manifest_appends:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    if inventory_appends:
        with inventory_path.open("a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=inventory_fields)
            writer.writerows(inventory_appends)

    queue_rel = Path("derived/review_queues/microtext_openflexure_optics_review_only_2026-08-17-wave210.jsonl")
    annotation_queue_rel = Path("microtext/annotations/microtext_review_openflexure_optics_2026-08-17-wave210.jsonl")
    write_jsonl(root / queue_rel, review_rows)
    write_jsonl(root / annotation_queue_rel, review_rows)
    receipt = {
        "wave": WAVE,
        "import_date": IMPORT_DATE,
        "source_repository": REPO_URL,
        "pinned_commit": COMMIT,
        "public_status": PUBLIC_STATUS,
        "source_candidate_id": SOURCE_CANDIDATE_ID,
        "source_candidate_aliases": SOURCE_CANDIDATE_ALIASES,
        "documents": import_docs,
        "review_queue": queue_rel.as_posix(),
        "canonical_review_staging": annotation_queue_rel.as_posix(),
        "review_rows": len(review_rows),
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
        "snapshot_dir": snapshot_dir.relative_to(root).as_posix(),
    }
    receipt_path = root / "derived" / "source_imports" / "openflexure_optics_import_2026-08-17-wave210.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    quality_path = root / "derived" / "quality" / "v2_0_openflexure_optics_intake_2026-08-17-wave210.json"
    quality_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] docs={len(DOCS)} manifest+={len(manifest_appends)} "
        f"inventory+={len(inventory_appends)} review_rows={len(review_rows)} active_gold_modified=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
