#!/usr/bin/env python3
"""Propose unlabeled microtext regions from raster page images."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np



def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def page_image_path(root: Path, doc_id: str, page_index: int, dpi: int = 300) -> Path:
    base = root / "derived" / f"pages_{dpi}dpi" / doc_id
    for name in (f"page_{page_index:03d}.png", f"page_{page_index:04d}.png"):
        path = base / name
        if path.exists():
            return path
    return base / f"page_{page_index:03d}.png"


def available_page_indexes(root: Path, doc_id: str, dpi: int = 300) -> list[int]:
    base = root / "derived" / f"pages_{dpi}dpi" / doc_id
    pages: list[int] = []
    for path in sorted(base.glob("page_*.png")):
        stem = path.stem.removeprefix("page_")
        if stem.isdigit():
            pages.append(int(stem))
    return sorted(set(pages))


def scale_for_detection(width: int, height: int, max_detect_dim: int) -> float:
    largest = max(width, height)
    if largest <= max_detect_dim:
        return 1.0
    return max_detect_dim / largest


def clamp_bbox(bbox: tuple[int, int, int, int], width: int, height: int) -> list[int]:
    x1, y1, x2, y2 = bbox
    return [
        max(0, min(width - 1, x1)),
        max(0, min(height - 1, y1)),
        max(1, min(width, x2)),
        max(1, min(height, y2)),
    ]


def component_text_metrics(region: np.ndarray) -> dict[str, float | int]:
    """Estimate whether foreground components resemble a compact text label."""
    if region.size == 0:
        return {
            "component_count": 0,
            "glyph_components": 0,
            "tiny_component_ratio": 1.0,
            "glyph_center_span_ratio": 0.0,
            "wide_glyph_ratio": 0.0,
        }
    binary = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, 8)
    components = stats[1:count]
    height, width = region.shape[:2]
    area = max(1, height * width)
    glyph_components = 0
    glyph_centers: list[float] = []
    wide_glyphs = 0
    tiny_components = 0
    for _x, _y, component_width, component_height, component_area in components:
        is_tiny = (
            component_area < max(3, int(round(area * 0.0008)))
            or component_height < max(2, int(round(height * 0.10)))
        )
        if is_tiny:
            tiny_components += 1
        if (
            component_height >= max(3, int(round(height * 0.20)))
            and component_height <= max(3, int(round(height * 0.98)))
            and component_width <= max(3, int(round(width * 0.80)))
            and component_area >= max(3, int(round(area * 0.0008)))
            and component_area <= max(3, int(round(area * 0.40)))
        ):
            glyph_components += 1
            glyph_centers.append(float(_x + component_width / 2))
            if component_width / max(1, component_height) >= 0.12:
                wide_glyphs += 1
    component_count = len(components)
    glyph_center_span_ratio = (
        (max(glyph_centers) - min(glyph_centers)) / max(1, width)
        if len(glyph_centers) > 1
        else 0.0
    )
    return {
        "component_count": component_count,
        "glyph_components": glyph_components,
        "tiny_component_ratio": round(tiny_components / max(1, component_count), 4),
        "glyph_center_span_ratio": round(glyph_center_span_ratio, 4),
        "wide_glyph_ratio": round(wide_glyphs / max(1, glyph_components), 4),
    }


def select_spatially_diverse(
    proposals: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    limit: int,
    tile_columns: int = 1,
    tile_rows: int = 1,
    max_per_tile: int = 0,
) -> list[dict[str, Any]]:
    if tile_columns <= 1 and tile_rows <= 1:
        return proposals[:limit]
    tile_columns = max(1, tile_columns)
    tile_rows = max(1, tile_rows)
    selected: list[dict[str, Any]] = []
    by_tile: Counter[tuple[int, int]] = Counter()
    for proposal in proposals:
        x1, y1, x2, y2 = proposal["bbox"]
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        tile_x = min(tile_columns - 1, int(center_x * tile_columns / max(1, width)))
        tile_y = min(tile_rows - 1, int(center_y * tile_rows / max(1, height)))
        tile = (tile_x, tile_y)
        if max_per_tile > 0 and by_tile[tile] >= max_per_tile:
            continue
        proposal = dict(proposal)
        proposal["tile"] = [tile_x, tile_y]
        selected.append(proposal)
        by_tile[tile] += 1
        if len(selected) >= limit:
            break
    return selected


def propose_regions_for_image(
    image_path: Path,
    max_detect_dim: int = 2400,
    min_width: int = 24,
    min_height: int = 8,
    max_height: int = 120,
    max_width: int = 900,
    max_area: int = 60_000,
    limit: int = 80,
    min_glyph_components: int = 0,
    max_tiny_component_ratio: float = 1.0,
    min_glyph_center_span_ratio: float = 0.0,
    min_wide_glyph_ratio: float = 0.0,
    text_likeness_weight: float = 0.0,
    tile_columns: int = 1,
    tile_rows: int = 1,
    max_per_tile: int = 0,
) -> list[dict[str, Any]]:
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")
    height, width = image.shape[:2]
    scale = scale_for_detection(width, height, max_detect_dim)
    if scale < 1.0:
        detect = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        detect = image

    blurred = cv2.GaussianBlur(detect, (3, 3), 0)
    binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    detect_h, detect_w = detect.shape[:2]
    kernel_w = max(18, int(round(min(90 * scale, max(24, detect_w * 0.05)))))
    kernel_h = max(5, int(round(min(14 * scale, max(7, detect_h * 0.022)))))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, kernel_h))
    grouped = cv2.dilate(binary, kernel, iterations=1)

    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(grouped, 8)
    proposals: list[dict[str, Any]] = []
    inv_scale = 1.0 / scale
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        x1 = int(round(x * inv_scale))
        y1 = int(round(y * inv_scale))
        x2 = int(round((x + w) * inv_scale))
        y2 = int(round((y + h) * inv_scale))
        bbox = clamp_bbox((x1, y1, x2, y2), width, height)
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]
        if bw < min_width or bh < min_height:
            continue
        if bh > max_height or bw > max_width:
            continue
        if bw * bh > max_area:
            continue
        if bw / max(1, bh) > 18:
            continue
        if bw < bh * 1.4 and bh > 45:
            continue

        region = image[bbox[1] : bbox[3], bbox[0] : bbox[2]]
        dark = np.count_nonzero(region < 190)
        density = dark / max(1, region.size)
        if density < 0.015 or density > 0.65:
            continue
        text_metrics = component_text_metrics(region)
        if text_metrics["glyph_components"] < min_glyph_components:
            continue
        if text_metrics["tiny_component_ratio"] > max_tiny_component_ratio:
            continue
        if text_metrics["glyph_center_span_ratio"] < min_glyph_center_span_ratio:
            continue
        if text_metrics["wide_glyph_ratio"] < min_wide_glyph_ratio:
            continue
        base_score = float(density * min(bw, 240) * min(bh, 80))
        glyph_bonus = min(int(text_metrics["glyph_components"]), 12) / 12.0
        noise_penalty = 1.0 - float(text_metrics["tiny_component_ratio"])
        quality_score = base_score * (
            1.0 + max(0.0, text_likeness_weight) * glyph_bonus * noise_penalty
        )
        proposals.append(
            {
                "bbox": bbox,
                "width": bw,
                "height": bh,
                "dark_density": round(float(density), 4),
                **text_metrics,
                "score": round(quality_score, 4),
            }
        )

    proposals.sort(key=lambda row: (-row["score"], row["bbox"][1], row["bbox"][0]))
    return select_spatially_diverse(
        proposals,
        width=width,
        height=height,
        limit=limit,
        tile_columns=tile_columns,
        tile_rows=tile_rows,
        max_per_tile=max_per_tile,
    )


def build_review_rows(
    root: Path,
    doc_id: str,
    version_id: str,
    page_indexes: list[int],
    dpi: int = 300,
    limit_per_page: int = 80,
    total_limit: int = 200,
    category: str = "unknown_microtext",
    max_detect_dim: int = 2400,
    max_region_width: int = 900,
    max_region_height: int = 120,
    max_region_area: int = 60_000,
    min_glyph_components: int = 0,
    max_tiny_component_ratio: float = 1.0,
    min_glyph_center_span_ratio: float = 0.0,
    min_wide_glyph_ratio: float = 0.0,
    text_likeness_weight: float = 0.0,
    tile_columns: int = 1,
    tile_rows: int = 1,
    max_per_tile: int = 0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page_index in page_indexes:
        image_path = page_image_path(root, doc_id, page_index, dpi=dpi)
        if not image_path.exists():
            continue
        image_rel = image_path.relative_to(root).as_posix()
        proposals = propose_regions_for_image(
            image_path,
            max_detect_dim=max_detect_dim,
            max_width=max_region_width,
            max_height=max_region_height,
            max_area=max_region_area,
            limit=limit_per_page,
            min_glyph_components=min_glyph_components,
            max_tiny_component_ratio=max_tiny_component_ratio,
            min_glyph_center_span_ratio=min_glyph_center_span_ratio,
            min_wide_glyph_ratio=min_wide_glyph_ratio,
            text_likeness_weight=text_likeness_weight,
            tile_columns=tile_columns,
            tile_rows=tile_rows,
            max_per_tile=max_per_tile,
        )
        for proposal in proposals:
            candidate_id = (
                f"imgcand__{doc_id}__{version_id}__p{page_index:04d}__{len(rows):06d}"
            )
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "doc_id": doc_id,
                    "version_id": version_id,
                    "page_index": page_index,
                    "bbox": proposal["bbox"],
                    "target_text": "",
                    "proposed_text": "",
                    "category": category,
                    "source": "image_region_proposal",
                    "review_status": "needs_review",
                    "corrected_text": "",
                    "question_text": "What text is shown in this small engineering label region?",
                    "image_path": image_rel,
                    "text_context": "",
                    "review_notes": (
                        "machine-proposed raster region; "
                        f"dark_density={proposal['dark_density']}; "
                        f"glyph_components={proposal['glyph_components']}; "
                        f"tiny_component_ratio={proposal['tiny_component_ratio']}; "
                        f"glyph_center_span_ratio={proposal['glyph_center_span_ratio']}; "
                        f"wide_glyph_ratio={proposal['wide_glyph_ratio']}; "
                        f"tile={proposal.get('tile', '')}"
                    ),
                }
            )
            if len(rows) >= total_limit:
                return rows
    return rows


def resolve_pages(root: Path, doc_id: str, pages: str, dpi: int) -> list[int]:
    available = available_page_indexes(root, doc_id, dpi=dpi)
    if not available:
        return []
    if pages.strip().lower() == "all":
        return available
    available_by_number = {page_index + 1: page_index for page_index in available}
    selected: list[int] = []
    seen: set[int] = set()
    for raw_token in pages.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start = int(left)
            end = int(right)
        else:
            start = end = int(token)
        if start < 1 or end < 1:
            raise ValueError(f"Page specs are 1-based; got {token!r}")
        if end < start:
            raise ValueError(f"Invalid descending page range {token!r}")
        for page_number in range(start, end + 1):
            page_index = available_by_number.get(page_number)
            if page_index is None or page_index in seen:
                continue
            seen.add(page_index)
            selected.append(page_index)
    return selected


def parse_csv_filter(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose unlabeled microtext review regions")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--doc-id", help="Single doc_id to process")
    parser.add_argument("--doc-ids", help="Comma-separated doc_id list to process")
    parser.add_argument("--version-id", default="unknown")
    parser.add_argument("--pages", default="all", help="1-based page selection or all")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--limit-per-page", type=int, default=80)
    parser.add_argument("--total-limit", type=int, default=200)
    parser.add_argument("--category", default="unknown_microtext")
    parser.add_argument(
        "--max-detect-dim",
        type=int,
        default=2400,
        help="Maximum raster dimension used for region detection before mapping boxes back.",
    )
    parser.add_argument(
        "--max-region-width",
        type=int,
        default=900,
        help="Maximum candidate width in original page pixels.",
    )
    parser.add_argument(
        "--max-region-height",
        type=int,
        default=120,
        help="Maximum candidate height in original page pixels.",
    )
    parser.add_argument(
        "--max-region-area",
        type=int,
        default=60_000,
        help="Maximum candidate area in original page pixels.",
    )
    parser.add_argument("--min-glyph-components", type=int, default=0)
    parser.add_argument("--max-tiny-component-ratio", type=float, default=1.0)
    parser.add_argument("--min-glyph-center-span-ratio", type=float, default=0.0)
    parser.add_argument("--min-wide-glyph-ratio", type=float, default=0.0)
    parser.add_argument("--text-likeness-weight", type=float, default=0.0)
    parser.add_argument("--tile-columns", type=int, default=1)
    parser.add_argument("--tile-rows", type=int, default=1)
    parser.add_argument("--max-per-tile", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root)
    doc_ids = parse_csv_filter(args.doc_ids)
    if args.doc_id:
        doc_ids.append(args.doc_id)
    doc_ids = list(dict.fromkeys(doc_ids))
    if not doc_ids:
        parser.error("one of --doc-id or --doc-ids is required")

    rows: list[dict[str, Any]] = []
    for doc_id in doc_ids:
        page_indexes = resolve_pages(root, doc_id, args.pages, dpi=args.dpi)
        rows.extend(
            build_review_rows(
                root=root,
                doc_id=doc_id,
                version_id=args.version_id,
                page_indexes=page_indexes,
                dpi=args.dpi,
                limit_per_page=args.limit_per_page,
                total_limit=args.total_limit,
                category=args.category,
                max_detect_dim=max(512, args.max_detect_dim),
                max_region_width=max(1, args.max_region_width),
                max_region_height=max(1, args.max_region_height),
                max_region_area=max(1, args.max_region_area),
                min_glyph_components=max(0, args.min_glyph_components),
                max_tiny_component_ratio=max(0.0, min(1.0, args.max_tiny_component_ratio)),
                min_glyph_center_span_ratio=max(
                    0.0, min(1.0, args.min_glyph_center_span_ratio)
                ),
                min_wide_glyph_ratio=max(0.0, min(1.0, args.min_wide_glyph_ratio)),
                text_likeness_weight=max(0.0, args.text_likeness_weight),
                tile_columns=max(1, args.tile_columns),
                tile_rows=max(1, args.tile_rows),
                max_per_tile=max(0, args.max_per_tile),
            )
        )
    write_jsonl(root / args.output, rows)
    by_doc = Counter(row["doc_id"] for row in rows)
    by_page = Counter(row["page_index"] for row in rows)
    print(f"[OK] Wrote {len(rows)} raster-region candidates to {args.output}")
    print(f"by_doc: {dict(sorted(by_doc.items()))}")
    print(f"by_page: {dict(sorted(by_page.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
