#!/usr/bin/env python3
"""Build review-only visualdiff rows by diffing exact PDF/SVG text layers.

Pixel diffing fails when two revisions render at different sheet sizes,
orientations, or anti-aliasing settings. This builder compares the exact
text spans of both revisions instead:

1. Pages are mapped between revisions by text-content Jaccard similarity,
   so reordered or resized sheets still pair correctly.
2. Span texts present on only one side become added/removed candidates.
3. A removed and an added span that share the same nearest stable anchor
   span are paired into a single value-change candidate with exact boxes
   on both sides.

All rows are review-only seeds: humans must confirm the change and write
the description before any gold promotion.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image

TODO_DESCRIPTION = "CHANGE_DESC_GT_TODO"
FURNITURE_PATTERNS = [
    re.compile(r"^(date|time|file|sheet|title|size|number|of|rev|revision)[:.]?$", re.IGNORECASE),
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?$", re.IGNORECASE),
]


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_furniture_text(text: str, max_span_len: int) -> bool:
    if len(text) < 2 or len(text) > max_span_len:
        return True
    if not any(character.isalnum() for character in text):
        return True
    return any(pattern.match(text) for pattern in FURNITURE_PATTERNS)


def is_titleblock_corner(
    bbox: list[float],
    width: int,
    height: int,
    x_start_ratio: float,
    y_start_ratio: float,
) -> bool:
    return bbox[0] >= width * x_start_ratio and bbox[1] >= height * y_start_ratio


def int_bbox(bbox: list[float]) -> list[int]:
    x1, y1, x2, y2 = (float(value) for value in bbox)
    box = [int(round(x1)), int(round(y1)), int(round(max(x2, x1 + 1))), int(round(max(y2, y1 + 1)))]
    return box


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def load_page_spans(
    root: Path,
    doc_id: str,
    max_span_len: int,
    titleblock_x_start_ratio: float,
    titleblock_y_start_ratio: float,
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, tuple[int, int]], Counter[str]]:
    """Load filtered spans per page plus rendered page dimensions."""
    textlayer_path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
    stats: Counter[str] = Counter()
    pages: dict[int, list[dict[str, Any]]] = defaultdict(list)
    dims: dict[int, tuple[int, int]] = {}
    with textlayer_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            page_index = int(row.get("page", 0))
            if page_index not in dims:
                image_path = root / "derived" / "pages_300dpi" / doc_id / f"page_{page_index:03d}.png"
                if not image_path.exists():
                    stats["skipped_missing_page_image"] += 1
                    dims[page_index] = (0, 0)
                else:
                    with Image.open(image_path) as image:
                        dims[page_index] = image.size
            width, height = dims[page_index]
            if not width or not height:
                continue
            text = normalize_text(row.get("text"))
            if is_furniture_text(text, max_span_len):
                stats["skipped_furniture_spans"] += 1
                continue
            bbox = [float(value) for value in (row.get("bbox_px") or [])[:4]]
            if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                stats["skipped_invalid_bbox_spans"] += 1
                continue
            if is_titleblock_corner(
                bbox, width, height, titleblock_x_start_ratio, titleblock_y_start_ratio
            ):
                stats["skipped_titleblock_spans"] += 1
                continue
            pages[page_index].append({"text": text, "bbox": bbox})
            stats["kept_spans"] += 1
    return dict(pages), dims, stats


def page_text_sets(pages: dict[int, list[dict[str, Any]]]) -> dict[int, set[str]]:
    return {page: {span["text"] for span in spans} for page, spans in pages.items()}


def map_pages(
    old_pages: dict[int, list[dict[str, Any]]],
    new_pages: dict[int, list[dict[str, Any]]],
    min_jaccard: float,
) -> tuple[list[tuple[int, int, float]], Counter[str]]:
    """Greedy best-first page mapping by text-content Jaccard similarity."""
    stats: Counter[str] = Counter()
    old_sets = page_text_sets(old_pages)
    new_sets = page_text_sets(new_pages)
    scored: list[tuple[float, int, int]] = []
    for old_page, old_set in old_sets.items():
        if not old_set:
            continue
        for new_page, new_set in new_sets.items():
            if not new_set:
                continue
            union = len(old_set | new_set)
            if not union:
                continue
            jaccard = len(old_set & new_set) / union
            if jaccard >= min_jaccard:
                scored.append((jaccard, old_page, new_page))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_old: set[int] = set()
    used_new: set[int] = set()
    mapping: list[tuple[int, int, float]] = []
    for jaccard, old_page, new_page in scored:
        if old_page in used_old or new_page in used_new:
            continue
        used_old.add(old_page)
        used_new.add(new_page)
        mapping.append((old_page, new_page, jaccard))
    mapping.sort(key=lambda item: item[1])
    stats["mapped_pages"] = len(mapping)
    stats["unmapped_old_pages"] = len([p for p in old_pages if p not in used_old])
    stats["unmapped_new_pages"] = len([p for p in new_pages if p not in used_new])
    return mapping, stats


def map_pages_explicit(
    old_pages: dict[int, list[dict[str, Any]]],
    new_pages: dict[int, list[dict[str, Any]]],
    page_mapping: dict[str, Any],
) -> tuple[list[tuple[int, int, float]], Counter[str]]:
    """Resolve a curator-supplied one-to-one page map and retain similarity as QA evidence."""
    if page_mapping.get("type") != "explicit":
        raise ValueError("page_mapping.type must be explicit")
    entries = page_mapping.get("pairs")
    if not isinstance(entries, list) or not entries:
        raise ValueError("page_mapping.pairs must contain at least one page pair")
    old_sets = page_text_sets(old_pages)
    new_sets = page_text_sets(new_pages)
    used_old: set[int] = set()
    used_new: set[int] = set()
    mapping: list[tuple[int, int, float]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"page_mapping.pairs[{index}] must be an object")
        old_page = entry.get("page_A")
        new_page = entry.get("page_B")
        if old_page not in old_sets:
            raise ValueError(f"explicit old page is missing from text layer: {old_page}")
        if new_page not in new_sets:
            raise ValueError(f"explicit new page is missing from text layer: {new_page}")
        if old_page in used_old or new_page in used_new:
            raise ValueError("explicit page mapping must be one-to-one")
        used_old.add(old_page)
        used_new.add(new_page)
        union = len(old_sets[old_page] | new_sets[new_page])
        jaccard = len(old_sets[old_page] & new_sets[new_page]) / union if union else 0.0
        mapping.append((old_page, new_page, jaccard))
    mapping.sort(key=lambda item: item[1])
    stats: Counter[str] = Counter()
    stats["mapping_mode_explicit"] = 1
    stats["mapped_pages"] = len(mapping)
    stats["unmapped_old_pages"] = len([page for page in old_pages if page not in used_old])
    stats["unmapped_new_pages"] = len([page for page in new_pages if page not in used_new])
    return mapping, stats


def stable_anchor_texts(old_spans: list[dict[str, Any]], new_spans: list[dict[str, Any]]) -> set[str]:
    old_freq = Counter(span["text"] for span in old_spans)
    new_freq = Counter(span["text"] for span in new_spans)
    return {
        text
        for text, count in old_freq.items()
        if count == 1 and new_freq.get(text) == 1
    }


def nearest_anchor(
    span: dict[str, Any],
    anchors: list[dict[str, Any]],
    max_anchor_distance: float,
) -> dict[str, Any] | None:
    center = bbox_center(span["bbox"])
    best: dict[str, Any] | None = None
    best_distance = max_anchor_distance
    for anchor in anchors:
        anchor_center = bbox_center(anchor["bbox"])
        distance = ((center[0] - anchor_center[0]) ** 2 + (center[1] - anchor_center[1]) ** 2) ** 0.5
        if distance < best_distance:
            best = anchor
            best_distance = distance
    return best


def same_orientation(old_dims: tuple[int, int], new_dims: tuple[int, int]) -> bool:
    old_landscape = old_dims[0] >= old_dims[1]
    new_landscape = new_dims[0] >= new_dims[1]
    return old_landscape == new_landscape


def estimate_counterpart_bbox(
    bbox: list[float],
    source_dims: tuple[int, int],
    target_dims: tuple[int, int],
) -> list[int]:
    scale_x = target_dims[0] / source_dims[0]
    scale_y = target_dims[1] / source_dims[1]
    return int_bbox([bbox[0] * scale_x, bbox[1] * scale_y, bbox[2] * scale_x, bbox[3] * scale_y])


def cluster_one_sided(
    spans: list[dict[str, Any]],
    merge_gap_px: int,
) -> list[dict[str, Any]]:
    """Merge nearby same-kind one-sided spans into single change regions."""
    remaining = sorted(spans, key=lambda span: (span["bbox"][1], span["bbox"][0]))
    clusters: list[dict[str, Any]] = []
    for span in remaining:
        merged = False
        for cluster in clusters:
            box = cluster["bbox"]
            gap_x = max(span["bbox"][0] - box[2], box[0] - span["bbox"][2], 0)
            gap_y = max(span["bbox"][1] - box[3], box[1] - span["bbox"][3], 0)
            if gap_x <= merge_gap_px and gap_y <= merge_gap_px:
                cluster["bbox"] = [
                    min(box[0], span["bbox"][0]),
                    min(box[1], span["bbox"][1]),
                    max(box[2], span["bbox"][2]),
                    max(box[3], span["bbox"][3]),
                ]
                cluster["texts"].append(span["text"])
                merged = True
                break
        if not merged:
            clusters.append({"bbox": list(span["bbox"]), "texts": [span["text"]]})
    return clusters


def diff_mapped_page(
    old_spans: list[dict[str, Any]],
    new_spans: list[dict[str, Any]],
    old_dims: tuple[int, int],
    new_dims: tuple[int, int],
    merge_gap_px: int,
    max_anchor_distance: float,
    stats: Counter[str],
) -> dict[str, list[dict[str, Any]]]:
    old_freq = Counter(span["text"] for span in old_spans)
    new_freq = Counter(span["text"] for span in new_spans)
    removed = [span for span in old_spans if not new_freq.get(span["text"])]
    added = [span for span in new_spans if not old_freq.get(span["text"])]
    stats["raw_removed_spans"] += len(removed)
    stats["raw_added_spans"] += len(added)

    anchor_texts = stable_anchor_texts(old_spans, new_spans)
    old_anchors = [span for span in old_spans if span["text"] in anchor_texts]
    new_anchors = [span for span in new_spans if span["text"] in anchor_texts]

    changed_pairs: list[dict[str, Any]] = []
    unpaired_removed: list[dict[str, Any]] = []
    used_added: set[int] = set()
    added_by_anchor: dict[str, list[int]] = defaultdict(list)
    added_anchor_cache: list[dict[str, Any] | None] = []
    for index, span in enumerate(added):
        anchor = nearest_anchor(span, new_anchors, max_anchor_distance)
        added_anchor_cache.append(anchor)
        if anchor is not None:
            added_by_anchor[anchor["text"]].append(index)

    for span in removed:
        anchor = nearest_anchor(span, old_anchors, max_anchor_distance)
        paired_index: int | None = None
        if anchor is not None:
            for candidate_index in added_by_anchor.get(anchor["text"], []):
                if candidate_index not in used_added:
                    paired_index = candidate_index
                    break
        if paired_index is None:
            unpaired_removed.append(span)
            continue
        used_added.add(paired_index)
        partner = added[paired_index]
        changed_pairs.append(
            {
                "old_text": span["text"],
                "new_text": partner["text"],
                "anchor_text": anchor["text"] if anchor else "",
                "bbox_old": int_bbox(span["bbox"]),
                "bbox_new": int_bbox(partner["bbox"]),
            }
        )
    unpaired_added = [span for index, span in enumerate(added) if index not in used_added]
    stats["changed_pairs"] += len(changed_pairs)

    one_sided: dict[str, list[dict[str, Any]]] = {"removed": [], "added": []}
    if same_orientation(old_dims, new_dims):
        for kind, spans, source_dims, target_dims in (
            ("removed", unpaired_removed, old_dims, new_dims),
            ("added", unpaired_added, new_dims, old_dims),
        ):
            clusters = cluster_one_sided(spans, merge_gap_px)
            stats[f"clustered_{kind}_regions"] += len(clusters)
            for cluster in clusters:
                exact = int_bbox(cluster["bbox"])
                estimate = estimate_counterpart_bbox(cluster["bbox"], source_dims, target_dims)
                one_sided[kind].append(
                    {
                        "texts": cluster["texts"],
                        "bbox_exact": exact,
                        "bbox_estimate": estimate,
                    }
                )
    else:
        stats["skipped_one_sided_orientation_mismatch"] += len(unpaired_removed) + len(unpaired_added)

    return {"changed_pairs": changed_pairs, **one_sided}


def build_review_rows(
    root: Path,
    pair_family: str,
    source_candidate_id: str,
    old_doc_id: str,
    new_doc_id: str,
    min_jaccard: float = 0.30,
    max_span_len: int = 60,
    titleblock_x_start_ratio: float = 0.55,
    titleblock_y_start_ratio: float = 0.82,
    merge_gap_px: int = 36,
    max_anchor_distance: float = 900.0,
    max_per_page: int = 6,
    max_total: int = 60,
    max_per_change_key: int = 2,
    page_mapping: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: Counter[str] = Counter()
    old_pages, old_dims, old_stats = load_page_spans(
        root, old_doc_id, max_span_len, titleblock_x_start_ratio, titleblock_y_start_ratio
    )
    new_pages, new_dims, new_stats = load_page_spans(
        root, new_doc_id, max_span_len, titleblock_x_start_ratio, titleblock_y_start_ratio
    )
    for key, value in old_stats.items():
        stats[f"old_{key}"] += value
    for key, value in new_stats.items():
        stats[f"new_{key}"] += value

    if page_mapping is None:
        mapping, mapping_stats = map_pages(old_pages, new_pages, min_jaccard)
    else:
        mapping, mapping_stats = map_pages_explicit(old_pages, new_pages, page_mapping)
    stats.update(mapping_stats)

    page_rows: dict[int, list[dict[str, Any]]] = {}
    for old_page, new_page, jaccard in mapping:
        diff = diff_mapped_page(
            old_pages[old_page],
            new_pages[new_page],
            old_dims[old_page],
            new_dims[new_page],
            merge_gap_px=merge_gap_px,
            max_anchor_distance=max_anchor_distance,
            stats=stats,
        )
        old_rel = f"derived/pages_300dpi/{old_doc_id}/page_{old_page:03d}.png"
        new_rel = f"derived/pages_300dpi/{new_doc_id}/page_{new_page:03d}.png"
        candidates: list[dict[str, Any]] = []
        for pair in diff["changed_pairs"]:
            candidates.append(
                {
                    "change_type": "text_change_candidate",
                    "bbox_old": pair["bbox_old"],
                    "bbox_new": pair["bbox_new"],
                    "old_text": pair["old_text"],
                    "new_text": pair["new_text"],
                    "notes": (
                        f"Exact text-layer change near anchor '{pair['anchor_text']}': "
                        f"'{pair['old_text']}' -> '{pair['new_text']}'. Human must confirm the visible "
                        "change inside both crops and describe it before gold promotion."
                    ),
                }
            )
        for cluster in diff["removed"]:
            texts = ", ".join(cluster["texts"][:5])
            candidates.append(
                {
                    "change_type": "text_removed_candidate",
                    "bbox_old": cluster["bbox_exact"],
                    "bbox_new": cluster["bbox_estimate"],
                    "old_text": " | ".join(cluster["texts"][:8]),
                    "new_text": "",
                    "notes": (
                        f"Text present only in the old revision ({texts}). The new-side box is a "
                        "scaled estimate; human must confirm the removal and describe it before gold promotion."
                    ),
                }
            )
        for cluster in diff["added"]:
            texts = ", ".join(cluster["texts"][:5])
            candidates.append(
                {
                    "change_type": "text_added_candidate",
                    "bbox_old": cluster["bbox_estimate"],
                    "bbox_new": cluster["bbox_exact"],
                    "old_text": "",
                    "new_text": " | ".join(cluster["texts"][:8]),
                    "notes": (
                        f"Text present only in the new revision ({texts}). The old-side box is a "
                        "scaled estimate; human must confirm the addition and describe it before gold promotion."
                    ),
                }
            )
        if len(candidates) > max_per_page:
            stats["skipped_per_page_limit"] += len(candidates) - max_per_page
            candidates = candidates[:max_per_page]
        for candidate in candidates:
            candidate.update(
                {
                    "page_old": old_page,
                    "page_new": new_page,
                    "image_old": old_rel,
                    "image_new": new_rel,
                    "page_similarity_jaccard": round(jaccard, 4),
                }
            )
        if candidates:
            page_rows[new_page] = candidates

    rows: list[dict[str, Any]] = []
    counters: dict[int, int] = defaultdict(int)
    change_key_counts: Counter[tuple[str, str, str]] = Counter()
    while len(rows) < max_total:
        emitted = False
        for new_page in sorted(page_rows):
            queue = page_rows[new_page]
            if counters[new_page] >= len(queue) or len(rows) >= max_total:
                continue
            candidate = queue[counters[new_page]]
            counters[new_page] += 1
            emitted = True
            change_key = (
                str(candidate["change_type"]),
                str(candidate.get("old_text") or ""),
                str(candidate.get("new_text") or ""),
            )
            if change_key_counts[change_key] >= max_per_change_key:
                stats["skipped_repeated_change_key"] += 1
                continue
            change_key_counts[change_key] += 1
            index_on_page = sum(
                1 for row in rows if row["page_new"] == candidate["page_new"]
            )
            rows.append(
                {
                    "pair_id": f"{pair_family}__p{candidate['page_new']:04d}__txt{index_on_page:03d}",
                    "project_id": pair_family,
                    "source_candidate_id": source_candidate_id,
                    "split": "provisional_review",
                    "review_bucket": "source_expansion_unverified",
                    "review_status": "needs_review",
                    "description": TODO_DESCRIPTION,
                    "source": "textlayer_span_diff_from_mapped_pages",
                    "confidence": 0.4,
                    **candidate,
                }
            )
        if not emitted:
            break
    leftover = sum(len(queue) for queue in page_rows.values()) - sum(counters.values())
    if leftover > 0:
        stats["skipped_total_limit"] += leftover
    stats["accepted_rows"] = len(rows)

    report: dict[str, Any] = {
        "pair_family": pair_family,
        "source_candidate_id": source_candidate_id,
        "old_doc_id": old_doc_id,
        "new_doc_id": new_doc_id,
        "row_count": len(rows),
        **dict(sorted(stats.items())),
    }
    return rows, report


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--pair-family", required=True)
    parser.add_argument("--source-candidate-id", required=True)
    parser.add_argument("--old-doc-id", required=True)
    parser.add_argument("--new-doc-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    parser.add_argument("--min-jaccard", type=float, default=0.30)
    parser.add_argument("--max-span-len", type=int, default=60)
    parser.add_argument("--titleblock-x-start-ratio", type=float, default=0.55)
    parser.add_argument("--titleblock-y-start-ratio", type=float, default=0.82)
    parser.add_argument("--merge-gap-px", type=int, default=36)
    parser.add_argument("--max-anchor-distance", type=float, default=900.0)
    parser.add_argument("--max-per-page", type=int, default=6)
    parser.add_argument("--max-total", type=int, default=60)
    parser.add_argument("--max-per-change-key", type=int, default=2)
    parser.add_argument(
        "--page-mapping-json",
        help="Optional JSON file containing an explicit manifest-style page mapping.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    page_mapping = None
    if args.page_mapping_json:
        page_mapping_path = Path(args.page_mapping_json)
        if not page_mapping_path.is_absolute():
            page_mapping_path = root / page_mapping_path
        page_mapping = json.loads(page_mapping_path.read_text(encoding="utf-8"))
    rows, report = build_review_rows(
        root=root,
        pair_family=args.pair_family,
        source_candidate_id=args.source_candidate_id,
        old_doc_id=args.old_doc_id,
        new_doc_id=args.new_doc_id,
        min_jaccard=args.min_jaccard,
        max_span_len=args.max_span_len,
        titleblock_x_start_ratio=args.titleblock_x_start_ratio,
        titleblock_y_start_ratio=args.titleblock_y_start_ratio,
        merge_gap_px=args.merge_gap_px,
        max_anchor_distance=args.max_anchor_distance,
        max_per_page=args.max_per_page,
        max_total=args.max_total,
        max_per_change_key=args.max_per_change_key,
        page_mapping=page_mapping,
    )
    write_jsonl(root / args.output, rows)
    if args.report:
        report_path = root / args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {len(rows)} review-only textlayer visualdiff rows to {args.output}")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
