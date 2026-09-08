#!/usr/bin/env python3
"""Import a hash-pinned batch of NASA NTRS sources for review-only mining."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any

import fitz


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import register_manifest_source_candidate as candidate_register


MUTABLE_REGISTRIES = (
    "manifest.jsonl",
    "SOURCE_INVENTORY.csv",
    "SOURCE_CANDIDATES.csv",
    "SOURCE_CANDIDATES_RANKED.csv",
    "SOURCE_CANDIDATE_VALIDATION.csv",
    "SOURCE_CONVERSION_EXHAUSTION.csv",
)
ALLOWED_DETERMINATIONS = {"GOV_PUBLIC_USE_PERMITTED", "PUBLIC_USE_PERMITTED"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_hash(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual.casefold() != expected.casefold():
        raise ValueError(f"hash mismatch for {path}: expected {expected}, got {actual}")


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.replace("\ufffd", " ").split())


def verify_metadata(payload: dict[str, Any], source: dict[str, Any]) -> None:
    ntrs_id = str(source["ntrs_id"])
    expected_determination = str(source["expected_determination_type"])
    copyright_row = payload.get("copyright") or {}
    export_row = payload.get("exportControl") or {}
    failures: list[str] = []
    if str(payload.get("id")) != ntrs_id:
        failures.append("citation id mismatch")
    if payload.get("distribution") != "PUBLIC":
        failures.append("distribution is not PUBLIC")
    if expected_determination not in ALLOWED_DETERMINATIONS:
        failures.append("spec uses an unsupported rights determination")
    if copyright_row.get("determinationType") != expected_determination:
        failures.append("copyright determination does not match the pinned spec")
    if copyright_row.get("containsThirdPartyMaterial") is not False:
        failures.append("third-party material is not explicitly false")
    permissions_produced = copyright_row.get("thirdPartyPermissionsProduced")
    if (
        "thirdPartyPermissionsProduced" in copyright_row
        and permissions_produced is not False
    ):
        failures.append("third-party permissions are not explicitly false")
    if export_row.get("isExportControl") != "NO":
        failures.append("export-control status is not NO")
    if export_row.get("ear") != "NO" or export_row.get("itar") != "NO":
        failures.append("EAR/ITAR status is not NO")
    expected_name = str(source.get("download_name") or f"{ntrs_id}.pdf")
    downloads = payload.get("downloads") or []
    if not any(
        row.get("mimetype") == "application/pdf" and row.get("name") == expected_name
        for row in downloads
    ):
        failures.append("expected official PDF download is absent")
    if failures:
        raise ValueError(f"NTRS {ntrs_id}: " + "; ".join(failures))


def require_fields(source: dict[str, Any], index: int) -> None:
    required = {
        "ntrs_id",
        "candidate_id",
        "doc_id",
        "version_id",
        "domain",
        "same_model_id",
        "doc_type",
        "source_family",
        "likely_asset_type",
        "pdf_input",
        "metadata_input",
        "pdf_sha256",
        "metadata_sha256",
        "pdf_pages",
        "selected_pages_1based",
        "expected_determination_type",
        "public_status",
        "source_rel_dir",
        "source_filename",
        "metadata_filename",
        "path_hint",
        "validation_next_action",
    }
    missing = sorted(field for field in required if source.get(field) in (None, "", []))
    if missing:
        raise ValueError(f"sources[{index}] is missing fields: {missing}")


def load_spec(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise ValueError("spec must be an object with a sources list")
    if not payload.get("wave") or not payload.get("import_date"):
        raise ValueError("spec requires wave and import_date")
    if not payload["sources"]:
        raise ValueError("spec sources list is empty")
    seen: dict[str, set[str]] = {
        "ntrs_id": set(),
        "candidate_id": set(),
        "doc_id": set(),
    }
    for index, raw in enumerate(payload["sources"], start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"sources[{index}] must be an object")
        require_fields(raw, index)
        for field, values in seen.items():
            value = str(raw[field]).strip()
            if value in values:
                raise ValueError(f"duplicate {field}: {value}")
            values.add(value)
        pages = raw["selected_pages_1based"]
        if not isinstance(pages, list) or any(
            not isinstance(page, int) or page < 1 for page in pages
        ):
            raise ValueError(f"sources[{index}].selected_pages_1based is invalid")
        if pages != sorted(set(pages)):
            raise ValueError(f"sources[{index}] selected pages must be sorted and unique")
        rotation = int(raw.get("rotate_cw_degrees") or 0)
        if rotation not in {0, 90, 180, 270}:
            raise ValueError(
                f"sources[{index}].rotate_cw_degrees must be one of 0, 90, 180, or 270"
            )
        raw["rotate_cw_degrees"] = rotation
    return payload


def input_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def verify_source(root: Path, source: dict[str, Any]) -> dict[str, Any]:
    pdf_path = input_path(root, str(source["pdf_input"]))
    metadata_path = input_path(root, str(source["metadata_input"]))
    assert_hash(pdf_path, str(source["pdf_sha256"]))
    assert_hash(metadata_path, str(source["metadata_sha256"]))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    verify_metadata(metadata, source)
    document = fitz.open(pdf_path)
    try:
        expected_pages = int(source["pdf_pages"])
        if document.page_count != expected_pages:
            raise ValueError(
                f"NTRS {source['ntrs_id']}: expected {expected_pages} pages, "
                f"got {document.page_count}"
            )
        minimum_images = int(source.get("minimum_images_per_selected_page") or 0)
        page_markers = source.get("page_markers") or {}
        for page_number in source["selected_pages_1based"]:
            page = document[page_number - 1]
            image_count = len(page.get_images(full=True))
            if image_count < minimum_images:
                raise ValueError(
                    f"NTRS {source['ntrs_id']} page {page_number}: expected at least "
                    f"{minimum_images} images, got {image_count}"
                )
            text = normalized_text(page.get_text())
            markers = page_markers.get(str(page_number), [])
            missing = [marker for marker in markers if normalized_text(str(marker)) not in text]
            if missing:
                raise ValueError(
                    f"NTRS {source['ntrs_id']} page {page_number}: missing markers {missing}"
                )
    finally:
        document.close()
    return {
        "source": source,
        "pdf_path": pdf_path,
        "metadata_path": metadata_path,
        "metadata": metadata,
    }


def copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    assert_hash(source, expected_hash)
    if destination.exists():
        assert_hash(destination, expected_hash)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    assert_hash(destination, expected_hash)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def append_manifest(path: Path, row: dict[str, Any]) -> bool:
    rows = read_jsonl(path)
    matches = [
        item
        for item in rows
        if item.get("type") == "doc" and item.get("doc_id") == row["doc_id"]
    ]
    if len(matches) > 1:
        raise ValueError(f"duplicate manifest rows for {row['doc_id']}")
    if matches:
        existing = matches[0]
        if str(existing.get("sha256") or "").casefold() != str(row["sha256"]).casefold():
            raise ValueError(f"manifest payload conflict for {row['doc_id']}")
        if existing.get("source_candidate_id") != row["source_candidate_id"]:
            raise ValueError(f"manifest candidate conflict for {row['doc_id']}")
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
    matches = [item for item in rows if item.get("doc_id") == row["doc_id"]]
    if len(matches) > 1:
        raise ValueError(f"duplicate inventory rows for {row['doc_id']}")
    if matches:
        if matches[0].get("source_path") != row["source_path"]:
            raise ValueError(f"inventory source conflict for {row['doc_id']}")
        return False
    with path.open("a", encoding="utf-8-sig", newline="") as stream:
        csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore").writerow(row)
    return True


def render_and_extract(
    pdf_path: Path,
    page_numbers: list[int],
    page_dir: Path,
    textlayer_dir: Path,
    rotate_cw_degrees: int = 0,
) -> dict[int, int]:
    if rotate_cw_degrees not in {0, 90, 180, 270}:
        raise ValueError("rotate_cw_degrees must be one of 0, 90, 180, or 270")

    def rotate_point(
        point: list[float] | tuple[float, float] | None,
        width: float,
        height: float,
    ) -> list[float] | None:
        if not point:
            return None
        x, y = float(point[0]), float(point[1])
        if rotate_cw_degrees == 90:
            return [height - y, x]
        if rotate_cw_degrees == 180:
            return [width - x, height - y]
        if rotate_cw_degrees == 270:
            return [y, width - x]
        return [x, y]

    def rotate_bbox(
        bbox: list[float] | tuple[float, float, float, float] | None,
        width: float,
        height: float,
    ) -> list[float] | None:
        if not bbox:
            return None
        x0, y0, x1, y1 = (float(value) for value in bbox)
        points = [
            rotate_point([x0, y0], width, height),
            rotate_point([x0, y1], width, height),
            rotate_point([x1, y0], width, height),
            rotate_point([x1, y1], width, height),
        ]
        xs = [point[0] for point in points if point]
        ys = [point[1] for point in points if point]
        return [min(xs), min(ys), max(xs), max(ys)]

    document = fitz.open(pdf_path)
    span_counts: dict[int, int] = {}
    try:
        for page_number in page_numbers:
            page_index = page_number - 1
            page = document[page_index]
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            matrix = fitz.Matrix(300 / 72, 300 / 72).prerotate(rotate_cw_degrees)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            page_dir.mkdir(parents=True, exist_ok=True)
            pixmap.save(page_dir / f"page_{page_index:03d}.png")
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
                                    "bbox": rotate_bbox(
                                        span.get("bbox"), page_width, page_height
                                    ),
                                    "origin": rotate_point(
                                        span.get("origin"), page_width, page_height
                                    ),
                                    "size": span.get("size"),
                                    "font": span.get("font"),
                                    "flags": span.get("flags"),
                                    "color": span.get("color"),
                                }
                            )
            textlayer_dir.mkdir(parents=True, exist_ok=True)
            (textlayer_dir / f"page_{page_index:03d}.json").write_text(
                json.dumps(
                    {
                        "doc_page": page_index,
                        "rotate_cw_degrees": rotate_cw_degrees,
                        "page_size_points": [
                            page_height if rotate_cw_degrees in {90, 270} else page_width,
                            page_width if rotate_cw_degrees in {90, 270} else page_height,
                        ],
                        "spans": spans,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            span_counts[page_index] = len(spans)
    finally:
        document.close()
    return span_counts


def source_urls(source: dict[str, Any]) -> tuple[str, str, str]:
    ntrs_id = str(source["ntrs_id"])
    name = str(source.get("download_name") or f"{ntrs_id}.pdf")
    record = f"https://ntrs.nasa.gov/citations/{ntrs_id}"
    metadata = f"https://ntrs.nasa.gov/api/citations/{ntrs_id}"
    direct = f"https://ntrs.nasa.gov/api/citations/{ntrs_id}/downloads/{name}"
    return record, metadata, direct


def rights_evidence(source: dict[str, Any]) -> str:
    record, metadata, direct = source_urls(source)
    pages = ", ".join(str(page) for page in source["selected_pages_1based"])
    return f"""# NASA NTRS rights evidence

- NTRS record: {record}
- NTRS metadata API: {metadata}
- Direct PDF: {direct}
- NTRS ID: `{source['ntrs_id']}`
- Distribution: `PUBLIC`
- Copyright determination: `{source['expected_determination_type']}`
- Contains third-party material: `false`
- Third-party permissions produced: `false`
- Export control, EAR, and ITAR: `NO`
- PDF SHA-256: `{str(source['pdf_sha256']).upper()}`
- Metadata SHA-256: `{str(source['metadata_sha256']).upper()}`

The exact metadata response is preserved beside the payload. The source is not
described as public domain; release use follows the NTRS public-use
determination. Only PDF pages {pages} were selected after page-level visual
inspection. Source registration and candidate generation do not promote any
row to active Gold.
"""


def candidate_spec(source: dict[str, Any]) -> dict[str, Any]:
    record, _metadata, _direct = source_urls(source)
    notes = str(source.get("candidate_notes") or source.get("notes") or "").strip()
    return {
        "candidate": {
            "candidate_id": str(source["candidate_id"]),
            "path_hint": str(source["path_hint"]),
            "domain": str(source["domain"]),
            "task_fit": "microtext",
            "rights_tier": str(source["public_status"]),
            "source_url": record,
            "source_family": str(source["source_family"]),
            "likely_asset_type": str(source["likely_asset_type"]),
            "revision_family_potential": str(source.get("revision_family_potential") or "low"),
            "annotation_yield": str(source.get("annotation_yield") or "high"),
            "priority_bucket": str(source.get("priority_bucket") or "A"),
            "notes": notes,
        },
        "doc_ids": [str(source["doc_id"])],
        "priority_score": int(source.get("priority_score") or 12),
        "validation": {
            "release_posture": "release_candidate",
            "validation_next_action": str(source["validation_next_action"]),
        },
    }


def snapshot_registries(root: Path, spec: dict[str, Any]) -> Path:
    snapshot = (
        root
        / "derived"
        / "snapshots"
        / f"{spec['import_date']}-{spec['wave']}-nasa-review-source-batch"
    )
    snapshot.mkdir(parents=True, exist_ok=True)
    for relative in MUTABLE_REGISTRIES:
        source = root / relative
        destination = snapshot / source.name
        if source.exists() and not destination.exists():
            shutil.copy2(source, destination)
    return snapshot


def import_one(
    root: Path,
    spec: dict[str, Any],
    verified: dict[str, Any],
    snapshot: Path,
) -> dict[str, Any]:
    source = verified["source"]
    source_dir = Path(str(source["source_rel_dir"]))
    pdf_rel = source_dir / str(source["source_filename"])
    metadata_rel = source_dir / str(source["metadata_filename"])
    rights_rel = source_dir / f"RIGHTS_EVIDENCE_{source['ntrs_id']}.md"
    copy_verified(verified["pdf_path"], root / pdf_rel, str(source["pdf_sha256"]))
    copy_verified(
        verified["metadata_path"], root / metadata_rel, str(source["metadata_sha256"])
    )
    (root / rights_rel).write_text(rights_evidence(source), encoding="utf-8")

    doc_id = str(source["doc_id"])
    page_dir = root / "derived/pages_300dpi" / doc_id
    textlayer_dir = root / "derived/textlayer" / doc_id
    span_counts = render_and_extract(
        root / pdf_rel,
        list(source["selected_pages_1based"]),
        page_dir,
        textlayer_dir,
        rotate_cw_degrees=int(source.get("rotate_cw_degrees") or 0),
    )
    record_url, metadata_url, direct_url = source_urls(source)
    manifest_row = {
        "type": "doc",
        "doc_id": doc_id,
        "task": "microtext",
        "source_candidate_id": str(source["candidate_id"]),
        "same_model_id": str(source["same_model_id"]),
        "domain": str(source["domain"]),
        "doc_type": str(source["doc_type"]),
        "version": {
            "ntrs_id": str(source["ntrs_id"]),
            "receipt_date": f"{spec['import_date']}-{spec['wave']}",
            "version_id": str(source["version_id"]),
        },
        "path": pdf_rel.as_posix(),
        "sha256": str(source["pdf_sha256"]).lower(),
        "pages": int(source["pdf_pages"]),
        "render": {
            "dpi": 300,
            "colorspace": "rgb",
            "rotate_cw90": int(source.get("rotate_cw_degrees") or 0) == 90,
            "rotate_cw_degrees": int(source.get("rotate_cw_degrees") or 0),
        },
        "derived": {
            "pages_dir": f"derived/pages_300dpi/{doc_id}",
            "textlayer_dir": f"derived/textlayer/{doc_id}",
            "rendered_page_selection_1based": ",".join(
                str(page) for page in source["selected_pages_1based"]
            ),
        },
        "source_url": record_url,
        "direct_source_url": direct_url,
        "public_status": str(source["public_status"]),
        "license_note": (
            "NASA NTRS records PUBLIC distribution, the pinned public-use "
            "determination, no third-party material, and no export-control flags."
        ),
        "rights_evidence_url": metadata_url,
        "rights_evidence_path": rights_rel.as_posix(),
        "notes": str(source.get("notes") or ""),
    }
    manifest_added = append_manifest(root / "manifest.jsonl", manifest_row)

    inventory_row = {
        "doc_id": doc_id,
        "domain": str(source["domain"]),
        "task": "microtext",
        "public_status": str(source["public_status"]),
        "source_path": pdf_rel.as_posix(),
        "rendered_pages": str(len(source["selected_pages_1based"])),
        "textlayer_spans": str(sum(span_counts.values())),
        "mineable_candidates": "",
        "review_rows": "0",
        "open_review_rows": "0",
        "next_step": str(source["validation_next_action"]),
        "priority_score": str(source.get("inventory_priority_score") or 50),
        "source_url": record_url,
        "path": pdf_rel.as_posix(),
        "textlayer_status": "present",
        "render_status": "present",
        "notes": (
            f"{spec['wave']} hash-pinned NASA intake; selected PDF pages "
            + ",".join(str(page) for page in source["selected_pages_1based"])
            + "; source-only registration; no Gold promotion."
        ),
    }
    inventory_added = append_inventory(root / "SOURCE_INVENTORY.csv", inventory_row)

    quality_dir = root / "derived/quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    spec_path = quality_dir / f"v2_0_{spec['wave']}_{source['candidate_id']}_registration_spec.json"
    spec_path.write_text(
        json.dumps(candidate_spec(source), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    registration = candidate_register.register(
        root,
        spec_path,
        f"{spec['import_date']}-{spec['wave']}",
    )
    registration_path = (
        quality_dir / f"v2_0_{spec['wave']}_{source['candidate_id']}_registration.json"
    )
    registration_path.write_text(
        json.dumps(registration, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "ntrs_id": str(source["ntrs_id"]),
        "candidate_id": str(source["candidate_id"]),
        "doc_id": doc_id,
        "version_id": str(source["version_id"]),
        "source_path": pdf_rel.as_posix(),
        "source_sha256": str(source["pdf_sha256"]).lower(),
        "metadata_path": metadata_rel.as_posix(),
        "metadata_sha256": str(source["metadata_sha256"]).lower(),
        "rights_evidence_path": rights_rel.as_posix(),
        "selected_pages_1based": list(source["selected_pages_1based"]),
        "rotate_cw_degrees": int(source.get("rotate_cw_degrees") or 0),
        "rendered_pages": len(source["selected_pages_1based"]),
        "textlayer_spans": sum(span_counts.values()),
        "manifest_added": manifest_added,
        "inventory_added": inventory_added,
        "registration_report": registration_path.relative_to(root).as_posix(),
        "snapshot_dir": snapshot.relative_to(root).as_posix(),
        "candidate_rows_generated": 0,
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    spec_path = input_path(root, args.spec)
    spec = load_spec(spec_path)
    verified = [verify_source(root, source) for source in spec["sources"]]
    if not args.apply:
        print(
            f"[READY] sources={len(verified)} pages="
            f"{sum(len(row['source']['selected_pages_1based']) for row in verified)} "
            "rights=verified; rerun with --apply"
        )
        return 0

    snapshot = snapshot_registries(root, spec)
    receipts = [import_one(root, spec, row, snapshot) for row in verified]
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "review_only_source_registration",
        "wave": str(spec["wave"]),
        "import_date": str(spec["import_date"]),
        "spec_path": spec_path.relative_to(root).as_posix(),
        "spec_sha256": sha256(spec_path),
        "sources": receipts,
        "totals": {
            "sources": len(receipts),
            "rendered_pages": sum(row["rendered_pages"] for row in receipts),
            "textlayer_spans": sum(row["textlayer_spans"] for row in receipts),
        },
        "safe_to_merge_gold": False,
        "active_gold_modified": False,
    }
    report_path = (
        root
        / "derived/source_imports"
        / f"v2_0_{spec['wave']}_nasa_review_source_batch.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] sources={report['totals']['sources']} "
        f"pages={report['totals']['rendered_pages']} "
        f"spans={report['totals']['textlayer_spans']} active_gold_modified=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
