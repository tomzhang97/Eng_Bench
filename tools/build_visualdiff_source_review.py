#!/usr/bin/env python3
"""Build bounded review-only visualdiff rows from aligned source-revision diff boxes."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def map_bbox_to_old(
    bbox_new: list[int],
    homography_old_to_new: list[list[float]],
    old_width: int,
    old_height: int,
) -> list[int]:
    inverse = np.linalg.inv(np.asarray(homography_old_to_new, dtype=float))
    x1, y1, x2, y2 = [float(value) for value in bbox_new]
    corners = np.asarray(
        [[x1, y1, 1.0], [x2, y1, 1.0], [x2, y2, 1.0], [x1, y2, 1.0]],
        dtype=float,
    )
    mapped = (inverse @ corners.T).T
    mapped = mapped[:, :2] / mapped[:, 2:3]
    old_x1 = max(0, math.floor(float(mapped[:, 0].min())))
    old_y1 = max(0, math.floor(float(mapped[:, 1].min())))
    old_x2 = min(old_width, math.ceil(float(mapped[:, 0].max())))
    old_y2 = min(old_height, math.ceil(float(mapped[:, 1].max())))
    return [old_x1, old_y1, old_x2, old_y2]


def touches_border(bbox: list[int], width: int, height: int, margin: int) -> bool:
    x1, y1, x2, y2 = bbox
    return x1 <= margin or y1 <= margin or x2 >= width - margin or y2 >= height - margin


def is_titleblock_corner(
    bbox: list[int],
    width: int,
    height: int,
    start_ratio: float,
) -> bool:
    x1, y1, _, _ = bbox
    return x1 >= width * start_ratio and y1 >= height * start_ratio


def boxes_near(a: list[int], b: list[int], gap_px: int) -> bool:
    return not (
        a[2] + gap_px < b[0]
        or b[2] + gap_px < a[0]
        or a[3] + gap_px < b[1]
        or b[3] + gap_px < a[1]
    )


def cluster_candidate_boxes(candidates: list[dict[str, Any]], gap_px: int = 24) -> list[list[int]]:
    boxes = [
        [int(value) for value in candidate.get("bbox", [])]
        for candidate in candidates
        if len(candidate.get("bbox", [])) == 4
    ]
    clusters: list[list[int]] = []
    for box in sorted(boxes, key=lambda value: (value[0], value[1], value[2], value[3])):
        merged_indexes = [
            index for index, cluster in enumerate(clusters) if boxes_near(cluster, box, gap_px)
        ]
        if not merged_indexes:
            clusters.append(box)
            continue
        merged = box
        for index in reversed(merged_indexes):
            cluster = clusters.pop(index)
            merged = [
                min(merged[0], cluster[0]),
                min(merged[1], cluster[1]),
                max(merged[2], cluster[2]),
                max(merged[3], cluster[3]),
            ]
        changed = True
        while changed:
            changed = False
            for index in reversed(range(len(clusters))):
                if boxes_near(clusters[index], merged, gap_px):
                    cluster = clusters.pop(index)
                    merged = [
                        min(merged[0], cluster[0]),
                        min(merged[1], cluster[1]),
                        max(merged[2], cluster[2]),
                        max(merged[3], cluster[3]),
                    ]
                    changed = True
        clusters.append(merged)
    return sorted(clusters, key=lambda value: (value[0], value[1], value[2], value[3]))


def build_review_rows(
    root: Path,
    pair_family: str,
    source_candidate_id: str,
    old_doc_id: str,
    new_doc_id: str,
    min_area: int = 1200,
    max_area_ratio: float = 0.08,
    border_margin: int = 12,
    titleblock_corner_start_ratio: float = 0.82,
    min_inlier_ratio: float = 0.25,
    merge_gap_px: int = 24,
    max_per_page: int = 30,
    max_total: int = 120,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    align_dir = root / "derived" / "align" / pair_family
    rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()

    for homography_path in sorted(align_dir.glob("H_page_*.json")):
        homography = read_json(homography_path)
        page_index = int(homography.get("page_index", 0))
        info = homography.get("info") or {}
        if info.get("status") != "ok" or not homography.get("H"):
            stats["skipped_bad_alignment_pages"] += 1
            continue
        if float(info.get("inlier_ratio") or 0.0) < min_inlier_ratio:
            stats["skipped_low_inlier_pages"] += 1
            continue

        candidate_path = align_dir / f"candidates_page_{page_index:03d}.json"
        if not candidate_path.exists():
            stats["skipped_missing_candidate_pages"] += 1
            continue
        old_rel = Path(f"derived/pages_300dpi/{old_doc_id}/page_{page_index:03d}.png")
        new_rel = Path(f"derived/pages_300dpi/{new_doc_id}/page_{page_index:03d}.png")
        old_path = root / old_rel
        new_path = root / new_rel
        if not old_path.exists() or not new_path.exists():
            stats["skipped_missing_image_pages"] += 1
            continue

        with Image.open(old_path) as image:
            old_width, old_height = image.size
        with Image.open(new_path) as image:
            new_width, new_height = image.size
        page_area = new_width * new_height
        raw_candidates = read_json(candidate_path).get("candidates") or []
        stats["raw_candidate_boxes"] += len(raw_candidates)
        eligible_candidates: list[dict[str, list[int]]] = []
        for candidate in raw_candidates:
            bbox = [int(value) for value in candidate.get("bbox", [])]
            if len(bbox) != 4:
                stats["skipped_invalid_bbox"] += 1
                continue
            x1, y1, x2, y2 = bbox
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area < min_area:
                stats["skipped_too_small"] += 1
                continue
            if area > max_area_ratio * page_area:
                stats["skipped_too_large"] += 1
                continue
            if touches_border(bbox, new_width, new_height, border_margin):
                stats["skipped_border"] += 1
                continue
            if is_titleblock_corner(bbox, new_width, new_height, titleblock_corner_start_ratio):
                stats["skipped_titleblock_corner"] += 1
                continue
            eligible_candidates.append({"bbox": bbox})

        candidates = cluster_candidate_boxes(eligible_candidates, gap_px=merge_gap_px)
        stats["clustered_candidate_boxes"] += len(candidates)
        accepted_on_page = 0
        for bbox_new in candidates:
            x1, y1, x2, y2 = bbox_new
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area > max_area_ratio * page_area:
                stats["skipped_cluster_too_large"] += 1
                continue
            if touches_border(bbox_new, new_width, new_height, border_margin):
                stats["skipped_cluster_border"] += 1
                continue
            if is_titleblock_corner(
                bbox_new,
                new_width,
                new_height,
                titleblock_corner_start_ratio,
            ):
                stats["skipped_cluster_titleblock_corner"] += 1
                continue
            bbox_old = map_bbox_to_old(
                bbox_new,
                homography["H"],
                old_width=old_width,
                old_height=old_height,
            )
            if touches_border(bbox_old, old_width, old_height, border_margin):
                stats["skipped_mapped_border"] += 1
                continue
            if accepted_on_page >= max_per_page or len(rows) >= max_total:
                stats["skipped_limit"] += 1
                continue

            row_id = f"{pair_family}__p{page_index:04d}__{accepted_on_page:03d}"
            rows.append(
                {
                    "pair_id": row_id,
                    "project_id": pair_family,
                    "source_candidate_id": source_candidate_id,
                    "split": "provisional_review",
                    "review_bucket": "source_expansion_unverified",
                    "review_status": "needs_review",
                    "change_type": "schematic_change_candidate",
                    "description": "CHANGE_DESC_GT_TODO",
                    "page_old": page_index,
                    "page_new": page_index,
                    "bbox_old": bbox_old,
                    "bbox_new": bbox_new,
                    "image_old": old_rel.as_posix(),
                    "image_new": new_rel.as_posix(),
                    "source": "auto_diff_candidate_from_aligned_source_revisions",
                    "confidence": 0.35,
                    "alignment_inlier_ratio": float(info.get("inlier_ratio") or 0.0),
                    "alignment_inliers": int(info.get("inliers") or 0),
                    "notes": (
                        "Machine-generated visualdiff seed. Human must confirm that the "
                        "visible change is inside the marked crop and describe it before gold promotion."
                    ),
                }
            )
            accepted_on_page += 1
            stats["accepted_rows"] += 1
        stats["processed_pages"] += 1
        if len(rows) >= max_total:
            break

    report: dict[str, Any] = {
        "pair_family": pair_family,
        "source_candidate_id": source_candidate_id,
        "old_doc_id": old_doc_id,
        "new_doc_id": new_doc_id,
        "row_count": len(rows),
        **dict(sorted(stats.items())),
    }
    return rows, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--pair-family", required=True)
    parser.add_argument("--source-candidate-id", required=True)
    parser.add_argument("--old-doc-id", required=True)
    parser.add_argument("--new-doc-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    parser.add_argument("--min-area", type=int, default=1200)
    parser.add_argument("--max-area-ratio", type=float, default=0.08)
    parser.add_argument("--border-margin", type=int, default=12)
    parser.add_argument("--titleblock-corner-start-ratio", type=float, default=0.82)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.25)
    parser.add_argument("--merge-gap-px", type=int, default=24)
    parser.add_argument("--max-per-page", type=int, default=30)
    parser.add_argument("--max-total", type=int, default=120)
    args = parser.parse_args()

    root = Path(args.root)
    rows, report = build_review_rows(
        root=root,
        pair_family=args.pair_family,
        source_candidate_id=args.source_candidate_id,
        old_doc_id=args.old_doc_id,
        new_doc_id=args.new_doc_id,
        min_area=args.min_area,
        max_area_ratio=args.max_area_ratio,
        border_margin=args.border_margin,
        titleblock_corner_start_ratio=args.titleblock_corner_start_ratio,
        min_inlier_ratio=args.min_inlier_ratio,
        merge_gap_px=args.merge_gap_px,
        max_per_page=args.max_per_page,
        max_total=args.max_total,
    )
    write_jsonl(root / args.output, rows)
    if args.report:
        report_path = root / args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {len(rows)} review-only visualdiff rows to {args.output}")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
