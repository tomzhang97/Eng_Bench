#!/usr/bin/env python3
"""Import pinned Espressif dimension images as review-only MicroText rows."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image


IMPORT_DATE = "2026-08-30"
WAVE = "wave1353"
SOURCE_CANDIDATE_ID = "ds_016"
REPO_URL = "https://github.com/espressif/esp-dev-kits"
PINNED_COMMIT = "df877cb1124a80835ad22fa5d8bafadb2348ce50"
PUBLIC_STATUS = "cc_by_sa_4_0_verified_git_pinned"
LICENSE_NAME = "LICENSE-CC-BY-SA"
LICENSE_SHA256 = "862a902374da5969fd509a6705faad4c8ea4c0d62f16043cb80c413d82723d8a"
SAME_MODEL_ID = "espressif_esp_dev_kits_dimensions"
VERSION_ID = "esp_dev_kits_df877cb1"

SOURCES: tuple[dict[str, Any], ...] = (
    {
        "doc_id": "espressif_esp32_devkitc_v4_dimensions_back",
        "git_path": "docs/_static/esp32-devkitc/esp32-devkitc-v4-dimensions-back.jpg",
        "sha256": "52d81d696ebdb0c804d30b5d5361f1eaabca886cafc16b14edf48abdc0de02f1",
        "size": (960, 540),
        "labels": (
            ("48.2 mm", (411, 74, 490, 102)),
            ("27.9 mm", (733, 242, 817, 275)),
            ("54.4 mm", (402, 404, 486, 438)),
        ),
    },
    {
        "doc_id": "espressif_esp32_pico_kit_v4_dimensions_back",
        "git_path": "docs/_static/esp32-pico-kit/esp32-pico-kit-v4-dimensions-back.jpg",
        "sha256": "25eb7aa9c0dc983b5dd8fc58ccc9685e9ebc96ca719f0be4d37979e4fdc77ac8",
        "size": (630, 310),
        "labels": (
            ("20.3mm", (560, 131, 614, 158)),
            ("52.0 mm", (260, 260, 331, 285)),
        ),
    },
    {
        "doc_id": "espressif_esp32_pico_kit_v4_dimensions_side",
        "git_path": "docs/_static/esp32-pico-kit/esp32-pico-kit-v4-dimensions-side.jpg",
        "sha256": "6a9ae55d2c52bf530f73952bcbd1aa80aea447c9fa995c10db2de97a2023d006",
        "size": (624, 240),
        "labels": (
            ("10 mm", (554, 84, 590, 132)),
            ("52 mm", (268, 181, 323, 207)),
        ),
    },
    {
        "doc_id": "espressif_esp32_pico_kit_v4_1_dimensions_back",
        "git_path": "docs/_static/esp32-pico-kit/esp32-pico-kit-v4.1-dimensions-back.jpg",
        "sha256": "cc4b26120c3cc2f83772c8aa9f1b4a7f7ae022d699239aa4aebac10c61f35ef7",
        "size": (624, 288),
        "labels": (
            ("20.3 mm", (560, 110, 614, 139)),
            ("52 mm", (271, 239, 322, 266)),
        ),
    },
)


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def assert_hash(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"hash mismatch for {path}: expected {expected}, got {actual}")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo.resolve().as_posix()}", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def validate_bbox(bbox: tuple[int, int, int, int], size: tuple[int, int]) -> None:
    x0, y0, x1, y1 = bbox
    width, height = size
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"bbox {bbox} is outside image size {size}")


def verify_inputs(repo: Path) -> None:
    if git(repo, "rev-parse", "HEAD") != PINNED_COMMIT:
        raise ValueError(f"source repository must be pinned to {PINNED_COMMIT}")
    license_path = repo / LICENSE_NAME
    assert_hash(license_path, LICENSE_SHA256)
    license_text = license_path.read_text(encoding="utf-8", errors="replace")
    if "Attribution-ShareAlike 4.0 International" not in license_text:
        raise ValueError("CC BY-SA 4.0 license marker is missing")
    for source in SOURCES:
        path = repo / source["git_path"]
        assert_hash(path, source["sha256"])
        with Image.open(path) as image:
            if image.size != source["size"]:
                raise ValueError(
                    f"unexpected image size for {source['doc_id']}: {image.size} != {source['size']}"
                )
        for _, bbox in source["labels"]:
            validate_bbox(bbox, source["size"])


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def append_manifest(path: Path, additions: list[dict[str, Any]]) -> int:
    rows = read_jsonl(path)
    keys = {
        (str(row.get("type") or ""), str(row.get("doc_id") or row.get("pair_id") or "")): row
        for row in rows
    }
    added = 0
    for row in additions:
        key = (str(row["type"]), str(row["doc_id"]))
        existing = keys.get(key)
        if existing is not None:
            if existing != row:
                raise ValueError(f"manifest conflict for {key}")
            continue
        rows.append(row)
        keys[key] = row
        added += 1
    if added:
        temporary = path.with_name(f".{path.name}.{WAVE}.tmp")
        write_jsonl(temporary, rows)
        temporary.replace(path)
    return added


def append_inventory(path: Path, additions: list[dict[str, str]]) -> int:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        existing = {str(row.get("doc_id") or ""): row for row in reader}
    added = 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        for row in additions:
            doc_id = row["doc_id"]
            if doc_id in existing:
                comparable = {field: row.get(field, "") for field in fields}
                if existing[doc_id] != comparable:
                    raise ValueError(f"inventory conflict for {doc_id}")
                continue
            writer.writerow({field: row.get(field, "") for field in fields})
            existing[doc_id] = row
            added += 1
    return added


def source_url(git_path: str, *, raw: bool = False) -> str:
    if raw:
        return f"https://raw.githubusercontent.com/espressif/esp-dev-kits/{PINNED_COMMIT}/{git_path}"
    return f"{REPO_URL}/blob/{PINNED_COMMIT}/{git_path}"


def attribution_text() -> str:
    lines = [
        "# Espressif development-kit dimension-image attribution",
        "",
        f"- Upstream repository: {REPO_URL}",
        f"- Pinned Git commit: `{PINNED_COMMIT}`",
        "- Documentation and images license: CC BY-SA 4.0",
        f"- Preserved license: `{LICENSE_NAME}` (`{LICENSE_SHA256}`)",
        f"- Source candidate: `{SOURCE_CANDIDATE_ID}`",
        "",
        "Imported files:",
    ]
    for source in SOURCES:
        lines.append(f"- `{source['git_path']}` (`{source['sha256']}`)")
    lines.extend(
        [
            "",
            "The source JPEGs are preserved byte-for-byte. Derived PNG page renders and review",
            "crops are format conversions and crops of the credited images. All candidate rows",
            "remain review-only until human approval and release gates pass.",
            "",
        ]
    )
    return "\n".join(lines)


def build_records() -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    docs: list[dict[str, Any]] = []
    inventory: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    for source in SOURCES:
        doc_id = str(source["doc_id"])
        source_rel = Path("microtext/docs/espressif_esp_dev_kits_dimensions") / Path(
            str(source["git_path"])
        ).name
        page_rel = Path("derived/pages_300dpi") / doc_id / "page_000.png"
        count = len(source["labels"])
        docs.append(
            {
                "type": "doc",
                "doc_id": doc_id,
                "task": "microtext",
                "source_candidate_id": SOURCE_CANDIDATE_ID,
                "same_model_id": SAME_MODEL_ID,
                "domain": "datasheet_spec",
                "doc_type": "image",
                "version": {
                    "git_commit": PINNED_COMMIT,
                    "git_path": source["git_path"],
                    "imported": IMPORT_DATE,
                },
                "path": source_rel.as_posix(),
                "sha256": source["sha256"],
                "pages": 1,
                "derived": {"pages_dir": f"derived/pages_300dpi/{doc_id}"},
                "source_url": source_url(source["git_path"]),
                "direct_source_url": source_url(source["git_path"], raw=True),
                "public_status": PUBLIC_STATUS,
                "license_note": "Espressif documentation and images are licensed CC BY-SA 4.0; preserve attribution, pinned commit, and exact source hashes.",
                "license_evidence": {
                    "path": "microtext/docs/espressif_esp_dev_kits_dimensions/LICENSE-CC-BY-SA",
                    "sha256": LICENSE_SHA256,
                },
                "attribution_path": "microtext/docs/espressif_esp_dev_kits_dimensions/ATTRIBUTION.md",
                "provenance": {
                    "git_commit": PINNED_COMMIT,
                    "git_path": source["git_path"],
                    "git_source_sha256": source["sha256"],
                },
                "notes": "Wave1353 review-only MicroText capacity; no unreviewed row is active Gold.",
            }
        )
        inventory.append(
            {
                "doc_id": doc_id,
                "domain": "datasheet_spec",
                "task": "microtext",
                "public_status": PUBLIC_STATUS,
                "source_path": source_rel.as_posix(),
                "rendered_pages": "1",
                "textlayer_spans": "0",
                "mineable_candidates": str(count),
                "review_rows": str(count),
                "open_review_rows": str(count),
                "unpacketed_open_review_rows": str(count),
                "fresh_open_review_rows": str(count),
                "unique_open_review_rows": str(count),
                "unique_fresh_open_review_rows": str(count),
                "duplicate_payload_alias": "False",
                "next_step": "human_review",
                "priority_score": str(40 + count),
                "source_url": source_url(source["git_path"]),
                "path": source_rel.as_posix(),
                "textlayer_status": "not_applicable_image_source",
                "render_status": "rendered_rgb_png",
                "notes": "Pinned CC BY-SA 4.0 engineering dimension image; review-only rows.",
            }
        )
        for index, (text, bbox) in enumerate(source["labels"]):
            candidates.append(
                {
                    "candidate_id": f"mtcand__{doc_id}__{VERSION_ID}__p0000__{index:06d}",
                    "doc_id": doc_id,
                    "version_id": VERSION_ID,
                    "page_index": 0,
                    "bbox": list(bbox),
                    "proposed_text": text,
                    "category": "dimension_value",
                    "question_text": "What dimension value is shown in this region?",
                    "answer": text,
                    "image_path": page_rel.as_posix(),
                    "source": "machine_curated_pinned_repository_image",
                    "source_candidate_id": SOURCE_CANDIDATE_ID,
                    "same_model_id": SAME_MODEL_ID,
                    "source_url": source_url(source["git_path"]),
                    "source_sha256": source["sha256"],
                    "review_status": "needs_review",
                    "promotion_state": "unreviewed_candidate",
                    "machine_qa_status": "selected_for_human_review",
                    "machine_visual_qa_status": "selected_for_human_review",
                    "review_bucket": "v2_0_source_expansion",
                    "reserved_split": "test",
                    "split": "provisional_review",
                    "safe_to_merge_gold": False,
                    "notes": "Pinned, licensed source and manually curated region; human confirmation is required before Gold promotion.",
                }
            )
    return docs, inventory, candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--source-repo",
        type=Path,
        default=Path(".codex_work/versioned_git_sources/espressif_esp_dev_kits"),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    repo = args.source_repo if args.source_repo.is_absolute() else root / args.source_repo
    repo = repo.resolve()
    verify_inputs(repo)
    docs, inventory_rows, candidates = build_records()
    if not args.apply:
        print(f"[READY] docs={len(docs)} rows={len(candidates)}; rerun with --apply")
        return 0

    manifest_path = root / "manifest.jsonl"
    inventory_path = root / "SOURCE_INVENTORY.csv"
    snapshot_dir = root / "derived/snapshots/2026-08-30-wave1353-espressif-dimensions"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for source in (manifest_path, inventory_path):
        snapshot = snapshot_dir / source.name
        if not snapshot.exists():
            shutil.copy2(source, snapshot)

    source_root = root / "microtext/docs/espressif_esp_dev_kits_dimensions"
    source_root.mkdir(parents=True, exist_ok=True)
    license_destination = source_root / LICENSE_NAME
    if license_destination.exists():
        assert_hash(license_destination, LICENSE_SHA256)
    else:
        shutil.copy2(repo / LICENSE_NAME, license_destination)
        assert_hash(license_destination, LICENSE_SHA256)
    attribution_path = source_root / "ATTRIBUTION.md"
    attribution = attribution_text()
    if attribution_path.exists() and attribution_path.read_text(encoding="utf-8") != attribution:
        raise ValueError(f"attribution conflict at {attribution_path}")
    attribution_path.write_text(attribution, encoding="utf-8")

    render_receipts: list[dict[str, Any]] = []
    for source in SOURCES:
        source_path = repo / source["git_path"]
        destination = source_root / Path(str(source["git_path"])).name
        if destination.exists():
            assert_hash(destination, source["sha256"])
        else:
            shutil.copy2(source_path, destination)
            assert_hash(destination, source["sha256"])
        page_path = root / "derived/pages_300dpi" / source["doc_id"] / "page_000.png"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as image:
            rgb = image.convert("RGB")
            rgb.save(page_path, format="PNG", optimize=False)
            render_receipts.append(
                {
                    "doc_id": source["doc_id"],
                    "source_size": list(image.size),
                    "output_size": list(rgb.size),
                    "output_path": page_path.relative_to(root).as_posix(),
                    "output_sha256": sha256_file(page_path),
                    "renderer": "Pillow RGB PNG conversion",
                }
            )

    additions_path = root / "derived/quality/v2_0_wave1353_espressif_dimensions_manifest_additions.jsonl"
    write_jsonl(additions_path, docs)
    manifest_added = append_manifest(manifest_path, docs)
    inventory_added = append_inventory(inventory_path, inventory_rows)
    queue_path = root / "derived/review_queues/v2_0_wave1353_espressif_dimensions_raw.jsonl"
    annotation_path = root / "microtext/annotations/microtext_review_espressif_dimensions_2026-08-30-wave1353.jsonl"
    write_jsonl(queue_path, candidates)
    write_jsonl(annotation_path, candidates)

    report = {
        "goal": "Gold v2.0 Global",
        "wave": WAVE,
        "source_candidate_id": SOURCE_CANDIDATE_ID,
        "repo_url": REPO_URL,
        "pinned_commit": PINNED_COMMIT,
        "public_status": PUBLIC_STATUS,
        "docs": len(docs),
        "review_rows": len(candidates),
        "manifest_rows_added": manifest_added,
        "inventory_rows_added": inventory_added,
        "queue": queue_path.relative_to(root).as_posix(),
        "annotation_queue": annotation_path.relative_to(root).as_posix(),
        "manifest_additions": additions_path.relative_to(root).as_posix(),
        "render_receipts": render_receipts,
        "reserved_split": "test",
        "safe_to_merge_gold": False,
        "active_gold_rows_modified": 0,
        "valid": True,
    }
    report_path = root / "derived/quality/v2_0_wave1353_espressif_dimensions_import.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
