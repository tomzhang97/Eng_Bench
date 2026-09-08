#!/usr/bin/env python3
"""Run one RapidOCR expansion pass across a validated multi-document spec."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from propose_microtext_ocr_regions import (
    deduplicate_rows,
    load_rapidocr,
    page_index,
    propose_page_rows,
    select_page_paths,
    write_jsonl,
    write_report,
)


PROFILE_FIELDS = {
    "unitless_dimensions": "include_unitless_dimensions",
    "pcb_pin_signals": "include_pcb_pin_signals",
    "architectural_room_labels": "include_architectural_room_labels",
    "pid_labels": "include_pid_labels",
    "civil_slope_values": "include_civil_slope_values",
    "unclassified_engineering_text": "include_unclassified",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_batch_spec(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        raise ValueError("Batch spec must be an object with a documents list")
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload["documents"], start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"documents[{index}] must be an object")
        row = dict(raw)
        doc_id = str(row.get("doc_id") or "").strip()
        version_id = str(row.get("version_id") or "").strip()
        if not doc_id or not version_id:
            raise ValueError(f"documents[{index}] requires doc_id and version_id")
        if doc_id in seen:
            raise ValueError(f"Duplicate doc_id in batch spec: {doc_id}")
        seen.add(doc_id)
        profiles = row.get("profiles") or []
        if not isinstance(profiles, list):
            raise ValueError(f"documents[{index}].profiles must be a list")
        unknown_profiles = sorted(set(str(value) for value in profiles) - set(PROFILE_FIELDS))
        if unknown_profiles:
            raise ValueError(f"documents[{index}] has unknown profiles: {unknown_profiles}")
        page_indices = row.get("page_indices") or []
        if not isinstance(page_indices, list) or any(
            not isinstance(value, int) or value < 0 for value in page_indices
        ):
            raise ValueError(f"documents[{index}].page_indices must contain nonnegative integers")
        rotate_cw_degrees = int(row.get("rotate_cw_degrees") or 0)
        if rotate_cw_degrees not in {0, 90, 180, 270}:
            raise ValueError(
                f"documents[{index}].rotate_cw_degrees must be one of 0, 90, 180, or 270"
            )
        row["doc_id"] = doc_id
        row["version_id"] = version_id
        row["profiles"] = sorted(set(str(value) for value in profiles))
        row["page_indices"] = page_indices
        row["rotate_cw_degrees"] = rotate_cw_degrees
        ocr_scale = float(row.get("ocr_scale") or 1.0)
        if not 0 < ocr_scale <= 4:
            raise ValueError(
                f"documents[{index}].ocr_scale must be greater than 0 and at most 4"
            )
        row["ocr_scale"] = ocr_scale
        documents.append(row)
    if not documents:
        raise ValueError("Batch spec documents list is empty")
    return documents


def run_batch(
    root: Path,
    documents: list[dict[str, Any]],
    *,
    engine: Any,
    np_module: Any,
    tile_size: int,
    overlap: int,
    min_confidence: float,
    padding: int,
    limit_per_page: int,
    max_image_pixels: int,
    max_total: int = 0,
    propose_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]] = propose_page_rows,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    rows: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    document_reports: list[dict[str, Any]] = []
    for document in documents:
        doc_id = document["doc_id"]
        page_dir = root / "derived" / "pages_300dpi" / doc_id
        if not page_dir.is_dir():
            raise FileNotFoundError(f"Missing rendered page directory: {page_dir}")
        page_paths = select_page_paths(
            page_dir.glob("page_*.png"),
            document.get("page_indices") or [],
        )
        if not page_paths:
            raise ValueError(f"No rendered pages found for {doc_id}")
        profiles = set(document.get("profiles") or [])
        profile_args = {
            field: profile in profiles for profile, field in PROFILE_FIELDS.items()
        }
        document_rows = 0
        for image_path in page_paths:
            proposed, page_report = propose_fn(
                root=root,
                image_path=image_path,
                doc_id=doc_id,
                version_id=document["version_id"],
                page_index=page_index(image_path),
                engine=engine,
                np_module=np_module,
                tile_size=int(document.get("tile_size", tile_size)),
                overlap=int(document.get("overlap", overlap)),
                min_confidence=float(document.get("min_confidence", min_confidence)),
                padding=int(document.get("padding", padding)),
                limit_per_page=int(document.get("limit_per_page", limit_per_page)),
                max_image_pixels=int(document.get("max_image_pixels", max_image_pixels)),
                ocr_scale=float(document.get("ocr_scale") or 1.0),
                rotate_cw_degrees=int(document.get("rotate_cw_degrees") or 0),
                **profile_args,
            )
            for row in proposed:
                row["review_status"] = "needs_review"
                row["promotion_state"] = "unreviewed_candidate"
                row["machine_qa_status"] = "ocr_batch_proposal"
                row["safe_to_merge_gold"] = False
                row["ocr_batch_profiles"] = sorted(profiles)
            rows.extend(proposed)
            pages.append(page_report)
            document_rows += len(proposed)
            print(f"[OCR] {doc_id} page {page_report['page_index']}: {len(proposed)} candidates")
        document_reports.append(
            {
                "doc_id": doc_id,
                "version_id": document["version_id"],
                "profiles": sorted(profiles),
                "pages": len(page_paths),
                "rotate_cw_degrees": int(document.get("rotate_cw_degrees") or 0),
                "ocr_scale": float(document.get("ocr_scale") or 1.0),
                "review_candidates_before_global_dedup": document_rows,
            }
        )

    rows = deduplicate_rows(rows)
    if max_total > 0:
        rows = sorted(
            rows,
            key=lambda row: (
                -float(row.get("ocr_confidence") or 0.0),
                str(row.get("doc_id") or ""),
                int(row.get("page_index") or 0),
                tuple(row.get("bbox") or ()),
            ),
        )[:max_total]
    rows.sort(
        key=lambda row: (
            str(row.get("doc_id") or ""),
            int(row.get("page_index") or 0),
            tuple(row.get("bbox") or ()),
            str(row.get("candidate_id") or ""),
        )
    )
    report = {
        "goal": "Gold v2.0 Global",
        "documents": document_reports,
        "pages": pages,
        "totals": {
            "documents": len(documents),
            "pages": len(pages),
            "tiles": sum(int(row.get("tiles") or 0) for row in pages),
            "ocr_detections": sum(int(row.get("ocr_detections") or 0) for row in pages),
            "review_candidates": len(rows),
            "categories": dict(
                sorted(Counter(str(row.get("category") or "") for row in rows).items())
            ),
        },
        "gold_rows_added": 0,
        "safe_to_merge_gold": False,
    }
    return rows, report


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    totals = report["totals"]
    lines = [
        "# OCR Microtext Batch Proposal Report",
        "",
        "Review-only candidates. No row in this report is gold.",
        "",
        f"- Documents: `{totals['documents']}`",
        f"- Pages: `{totals['pages']}`",
        f"- OCR detections: `{totals['ocr_detections']}`",
        f"- Review candidates: `{totals['review_candidates']}`",
        f"- Categories: `{totals['categories']}`",
        "- Safe to merge gold: `false`",
        "",
        "## Documents",
        "",
        "| Document | Version | Profiles | Pages | Candidates |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in report["documents"]:
        lines.append(
            f"| `{row['doc_id']}` | `{row['version_id']}` | "
            f"`{','.join(row['profiles'])}` | {row['pages']} | "
            f"{row['review_candidates_before_global_dedup']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--tile-size", type=int, default=5000)
    parser.add_argument("--overlap", type=int, default=512)
    parser.add_argument("--min-confidence", type=float, default=0.94)
    parser.add_argument("--padding", type=int, default=24)
    parser.add_argument("--limit-per-page", type=int, default=160)
    parser.add_argument("--max-total", type=int, default=0)
    parser.add_argument("--max-image-pixels", type=int, default=400_000_000)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    spec_path = args.spec if args.spec.is_absolute() else root / args.spec
    documents = load_batch_spec(spec_path)
    engine, np_module = load_rapidocr()
    rows, report = run_batch(
        root,
        documents,
        engine=engine,
        np_module=np_module,
        tile_size=args.tile_size,
        overlap=args.overlap,
        min_confidence=args.min_confidence,
        padding=args.padding,
        limit_per_page=args.limit_per_page,
        max_image_pixels=args.max_image_pixels,
        max_total=args.max_total,
    )
    report["spec_path"] = spec_path.relative_to(root).as_posix()
    report["spec_sha256"] = file_sha256(spec_path)
    output_jsonl = args.output_jsonl if args.output_jsonl.is_absolute() else root / args.output_jsonl
    report_json = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report_md = args.report_md if args.report_md.is_absolute() else root / args.report_md
    write_jsonl(output_jsonl, rows)
    write_report(report_json, report)
    write_markdown(report_md, report)
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
