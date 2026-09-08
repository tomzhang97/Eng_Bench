#!/usr/bin/env python3
"""Convert a returned manual-region checklist into gated review JSONL.

The converter never edits active gold. Accepted human rows are promoted to the
review staging output only when their page evidence, taxonomy, split, and
source provenance all pass. Every other accepted row is written to a hold
ledger with explicit reasons.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from source_rights import rights_blocker


ACCEPTED_CATEGORIES = {
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "pin_label",
    "pipe_line_tag",
    "process_label",
    "process_value",
    "room_label",
    "tolerance_value",
}

QUESTION_BY_CATEGORY = {
    "dimension_value": "What dimension value is shown in this region?",
    "equipment_tag": "What equipment tag is shown in this region?",
    "instrument_tag": "What instrument tag is shown in this region?",
    "pin_label": "What pin or connector label is shown in this region?",
    "pipe_line_tag": "What pipe or process line tag is shown in this region?",
    "process_label": "What process step or stream label is shown in this region?",
    "process_value": "What process value is shown in this region?",
    "room_label": "Which room label appears in this region?",
    "tolerance_value": "What tolerance value is shown in this region?",
}

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=None)
def pixel_sha256(path: Path) -> str:
    with Image.open(path) as image:
        normalized = image.convert("RGB")
        digest = hashlib.sha256()
        digest.update(f"{normalized.width}x{normalized.height}:RGB:".encode("ascii"))
        digest.update(normalized.tobytes())
        return digest.hexdigest()


@lru_cache(maxsize=None)
def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def parse_bbox(value: str) -> list[int]:
    parts = [part.strip() for part in str(value or "").split(",")]
    if len(parts) != 4:
        raise ValueError("bbox_xyxy must contain four comma-separated integers")
    bbox = [int(part) for part in parts]
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise ValueError("bbox_xyxy must satisfy x1 < x2 and y1 < y2")
    return bbox


def source_version(manifest: dict[str, Any]) -> str:
    version = manifest.get("version")
    if isinstance(version, dict):
        for field in ("revision", "snapshot", "version", "imported"):
            value = str(version.get(field) or "").strip()
            if value:
                return value
    value = str(version or "").strip()
    return value or "manual_region_2026_07_31"


def load_split_map(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for split in ("train", "dev", "test"):
        path = root / "splits" / f"microtext_{split}.txt"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            doc_id = line.strip()
            if not doc_id or doc_id.startswith("#"):
                continue
            if doc_id in result:
                raise ValueError(f"duplicate split assignment for {doc_id}")
            result[doc_id] = split
    return result


def canonical_page_path(root: Path, doc_id: str, page_index: int) -> Path | None:
    folder = root / "derived" / "pages_300dpi" / doc_id
    for name in (
        f"page_{page_index:04d}.png",
        f"page_{page_index:03d}.png",
        f"p{page_index:04d}.png",
    ):
        path = folder / name
        if path.is_file():
            return path
    return None


def manifest_source_errors(root: Path, manifest: dict[str, Any] | None) -> list[str]:
    if not manifest:
        return ["missing_manifest"]
    reasons: list[str] = []
    source_path = root / str(manifest.get("path") or "")
    if not str(manifest.get("path") or "").strip() or not source_path.is_file():
        reasons.append("missing_source_file")
    else:
        expected_sha = str(manifest.get("sha256") or "").strip().lower()
        if not expected_sha:
            reasons.append("missing_source_sha256")
        elif file_sha256(source_path) != expected_sha:
            reasons.append("source_sha256_mismatch")

    public_status = str(manifest.get("public_status") or "").strip().lower()
    blocker = rights_blocker(public_status)
    if blocker == "missing_public_status":
        reasons.append("missing_public_status")
    elif blocker == "license_evidence_missing":
        reasons.append("release_license_not_explicit")
    elif blocker:
        reasons.append("rights_blocked")
    if not str(manifest.get("source_url") or "").strip():
        reasons.append("missing_source_url")
    if not str(manifest.get("license_note") or "").strip():
        reasons.append("missing_license_note")
    return reasons


def active_gold_page_hashes(root: Path) -> dict[str, set[str]]:
    page_hashes: dict[str, set[str]] = defaultdict(set)
    for item in read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl"):
        doc_id = str(item.get("doc_id") or "")
        page_index = int(item.get("page_index") or 0)
        page_path = canonical_page_path(root, doc_id, page_index)
        if page_path:
            page_hashes[pixel_sha256(page_path)].add(doc_id)
    return page_hashes


def normalize_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def build_review_row(
    original: dict[str, str],
    manifest: dict[str, Any],
    canonical_page: Path,
    bbox: list[int],
    split: str,
    root: Path,
) -> dict[str, Any]:
    text = str(original.get("transcribed_text") or "").strip()
    category = str(original.get("category") or "").strip()
    review_row = {
        "candidate_id": str(original.get("row_id") or "").strip(),
        "doc_id": str(original.get("doc_id") or "").strip(),
        "version_id": source_version(manifest),
        "page_index": int(original.get("page_index") or 0),
        "bbox": bbox,
        "target_text": text,
        "raw_text": text,
        "proposed_text": text,
        "corrected_text": "",
        "category": category,
        "source": "human_manual_region_return",
        "review_status": "accepted",
        "review_notes": str(original.get("notes") or "").strip(),
        "image_path": normalize_relative(canonical_page, root),
        "question_text": QUESTION_BY_CATEGORY[category],
        "split": split,
        "provenance_source_candidate_id": str(manifest.get("source_candidate_id") or ""),
        "source_sha256": str(manifest.get("sha256") or ""),
        "evidence_page_pixel_sha256": pixel_sha256(canonical_page),
        "human_return_row_id": str(original.get("row_id") or "").strip(),
    }
    if original.get("machine_correction_reason"):
        review_row["machine_correction_reason"] = original["machine_correction_reason"]
        review_row["human_return_bbox_xyxy"] = original.get("human_return_bbox_xyxy", "")
    return review_row


def read_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("override file must be a JSON object keyed by row_id")
    result: dict[str, dict[str, Any]] = {}
    for row_id, value in payload.items():
        if not isinstance(value, dict):
            raise ValueError(f"override for {row_id} must be a JSON object")
        result[str(row_id)] = value
    return result


def convert_checklist(
    root: Path,
    checklist: Path,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    with checklist.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    manifests = {
        str(row.get("doc_id")): row
        for row in read_jsonl(root / "manifest.jsonl")
        if row.get("type") == "doc" and row.get("doc_id")
    }
    split_by_doc = load_split_map(root)
    active_hashes = active_gold_page_hashes(root)
    staged: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    by_doc: dict[str, Counter[str]] = defaultdict(Counter)
    seen_ids: set[str] = set()
    overrides = overrides or {}

    for original in rows:
        status = str(original.get("status") or "").strip().lower()
        counters[f"status_{status or 'blank'}"] += 1
        if status != "accepted":
            continue

        original = dict(original)
        row_id = str(original.get("row_id") or "").strip()
        override = overrides.get(row_id, {})
        if override.get("corrected_bbox") is not None:
            corrected_bbox = override["corrected_bbox"]
            if not isinstance(corrected_bbox, list) or len(corrected_bbox) != 4:
                raise ValueError(f"corrected_bbox for {row_id} must contain four values")
            original["human_return_bbox_xyxy"] = original.get("bbox_xyxy", "")
            original["bbox_xyxy"] = ",".join(str(int(value)) for value in corrected_bbox)
            original["machine_correction_reason"] = str(override.get("reason") or "bbox correction")
            counters["machine_corrected_bbox"] += 1
        doc_id = str(original.get("doc_id") or "").strip()
        page_index = int(original.get("page_index") or 0)
        category = str(original.get("category") or "").strip()
        text = str(original.get("transcribed_text") or "").strip()
        reasons: list[str] = []
        if override.get("hold_reason"):
            reasons.append(str(override["hold_reason"]))

        if not row_id or row_id in seen_ids:
            reasons.append("missing_or_duplicate_row_id")
        seen_ids.add(row_id)
        if not text:
            reasons.append("missing_transcribed_text")
        if category not in ACCEPTED_CATEGORIES:
            reasons.append("invalid_or_unknown_category")

        try:
            bbox = parse_bbox(str(original.get("bbox_xyxy") or ""))
        except (TypeError, ValueError):
            bbox = [0, 0, 0, 0]
            reasons.append("invalid_bbox")

        packet_page = checklist.parent / str(original.get("page_image") or "")
        canonical_page = canonical_page_path(root, doc_id, page_index)
        if not packet_page.is_file():
            reasons.append("missing_packet_page")
        if canonical_page is None:
            reasons.append("missing_canonical_page")
        elif packet_page.is_file():
            if pixel_sha256(packet_page) != pixel_sha256(canonical_page):
                reasons.append("packet_page_differs_from_canonical")

        if canonical_page is not None and bbox != [0, 0, 0, 0]:
            width, height = image_size(canonical_page)
            x1, y1, x2, y2 = bbox
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                reasons.append("bbox_out_of_bounds")
            page_hash = pixel_sha256(canonical_page)
            duplicate_docs = sorted(active_hashes.get(page_hash, set()) - {doc_id})
            if duplicate_docs:
                reasons.append("duplicate_page_active_gold:" + ";".join(duplicate_docs))

        manifest = manifests.get(doc_id)
        reasons.extend(manifest_source_errors(root, manifest))
        split = split_by_doc.get(doc_id, "")
        if not split:
            reasons.append("missing_split_assignment")

        reasons = list(dict.fromkeys(reasons))
        if reasons:
            hold = dict(original)
            hold["hold_reasons"] = reasons
            hold["canonical_page"] = (
                normalize_relative(canonical_page, root) if canonical_page is not None else ""
            )
            hold["source_candidate_id"] = str((manifest or {}).get("source_candidate_id") or "")
            held.append(hold)
            counters["held"] += 1
            for reason in reasons:
                counters[f"hold_{reason.split(':', 1)[0]}"] += 1
            by_doc[doc_id]["held"] += 1
            continue

        assert manifest is not None and canonical_page is not None
        staged.append(build_review_row(original, manifest, canonical_page, bbox, split, root))
        counters["staged"] += 1
        by_doc[doc_id]["staged"] += 1

    report = {
        "checklist": normalize_relative(checklist, root),
        "total_rows": len(rows),
        "accepted_human_rows": counters["status_accepted"],
        "nonaccepted_human_rows": len(rows) - counters["status_accepted"],
        "staged_rows": len(staged),
        "held_accepted_rows": len(held),
        "counters": dict(sorted(counters.items())),
        "documents": {
            doc_id: dict(sorted(counts.items())) for doc_id, counts in sorted(by_doc.items())
        },
    }
    return staged, held, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Manual Region Return Conversion",
        "",
        f"- Checklist rows: `{report['total_rows']}`",
        f"- Human-accepted rows: `{report['accepted_human_rows']}`",
        f"- Strict-gate review rows: `{report['staged_rows']}`",
        f"- Held accepted rows: `{report['held_accepted_rows']}`",
        "",
        "## Documents",
        "",
        "| Document | Staged | Held |",
        "| --- | ---: | ---: |",
    ]
    for doc_id, counts in report["documents"].items():
        lines.append(f"| `{doc_id}` | {counts.get('staged', 0)} | {counts.get('held', 0)} |")
    lines.extend(["", "## Hold Counters", ""])
    for key, value in report["counters"].items():
        if key.startswith("hold_"):
            lines.append(f"- `{key[5:]}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert returned manual-region checklist rows into gated review JSONL."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--checklist", required=True)
    parser.add_argument("--output-reviewed", required=True)
    parser.add_argument("--output-held", required=True)
    parser.add_argument("--output-report-json", required=True)
    parser.add_argument("--output-report-md", required=True)
    parser.add_argument(
        "--overrides",
        help="Optional JSON object of auditable bbox corrections or explicit hold reasons.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    checklist = Path(args.checklist)
    if not checklist.is_absolute():
        checklist = root / checklist
    override_path = Path(args.overrides) if args.overrides else None
    if override_path is not None and not override_path.is_absolute():
        override_path = root / override_path
    staged, held, report = convert_checklist(
        root,
        checklist.resolve(),
        read_overrides(override_path),
    )
    write_jsonl(root / args.output_reviewed, staged)
    write_jsonl(root / args.output_held, held)
    write_json(root / args.output_report_json, report)
    report_md = root / args.output_report_md
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"[OK] Staged {len(staged)} reviewed rows; held {len(held)} accepted rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
