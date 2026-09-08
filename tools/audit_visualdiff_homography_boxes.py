#!/usr/bin/env python3
"""Audit VisualDiff evidence boxes against saved old-to-new homographies."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


ACTIVE_PATHS = (
    "eng_bench.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
)


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in box):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def transform_bbox(value: Any, matrix: np.ndarray) -> list[float] | None:
    box = valid_bbox(value)
    if box is None or matrix.shape != (3, 3):
        return None
    x0, y0, x1, y1 = box
    corners = np.array(
        [[x0, y0, 1.0], [x1, y0, 1.0], [x1, y1, 1.0], [x0, y1, 1.0]],
        dtype=float,
    ).T
    mapped = matrix @ corners
    if np.any(np.isclose(mapped[2], 0)):
        return None
    mapped = mapped[:2] / mapped[2]
    if not np.isfinite(mapped).all():
        return None
    return [
        float(mapped[0].min()), float(mapped[1].min()),
        float(mapped[0].max()), float(mapped[1].max()),
    ]


def integer_bbox(value: list[float]) -> list[int]:
    return [math.floor(value[0]), math.floor(value[1]),
            math.ceil(value[2]), math.ceil(value[3])]


def within_image(value: Any, size: tuple[int, int]) -> bool:
    box = valid_bbox(value)
    return bool(
        box and box[0] >= 0 and box[1] >= 0
        and box[2] <= size[0] and box[3] <= size[1]
    )


def bbox_iou(first: Any, second: Any) -> float:
    a, b = valid_bbox(first), valid_bbox(second)
    if a is None or b is None:
        return 0.0
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - intersection)
    return intersection / union if union else 0.0


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).upper()


def spans_in_box(
    spans: list[dict[str, Any]], page: int, box: Any,
) -> list[str]:
    target = valid_bbox(box)
    if target is None:
        return []
    found = []
    for span in spans:
        span_box = valid_bbox(span.get("bbox_px"))
        text = normalized_text(str(span.get("text") or ""))
        if span.get("page") != page or span_box is None or not text:
            continue
        cx = (span_box[0] + span_box[2]) / 2
        cy = (span_box[1] + span_box[3]) / 2
        if target[0] <= cx <= target[2] and target[1] <= cy <= target[3]:
            found.append(text)
    return found


def local_visual_match(
    aligned_old: np.ndarray,
    new_image: np.ndarray,
    value: Any,
    pad: int = 30,
) -> dict[str, Any]:
    """Measure local rendered correspondence after global homography alignment."""
    import cv2

    box = valid_bbox(value)
    if box is None:
        return {"status": "invalid_bbox", "class": "unavailable"}
    x0, y0, x1, y1 = integer_bbox(box)
    if x0 < 0 or y0 < 0 or x1 > new_image.shape[1] or y1 > new_image.shape[0]:
        return {"status": "bbox_out_of_bounds", "class": "unavailable"}
    target = cv2.cvtColor(new_image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    sx0, sy0 = max(0, x0 - pad), max(0, y0 - pad)
    sx1, sy1 = min(aligned_old.shape[1], x1 + pad), min(aligned_old.shape[0], y1 + pad)
    search = cv2.cvtColor(aligned_old[sy0:sy1, sx0:sx1], cv2.COLOR_BGR2GRAY)
    if not target.size or search.shape[0] < target.shape[0] or search.shape[1] < target.shape[1]:
        return {"status": "empty_crop", "class": "unavailable"}
    result = cv2.matchTemplate(search, target, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    mx, my = sx0 + location[0], sy0 + location[1]
    matched = cv2.cvtColor(
        aligned_old[my:my + target.shape[0], mx:mx + target.shape[1]],
        cv2.COLOR_BGR2GRAY,
    )
    mae = float(np.mean(np.abs(matched.astype(float) - target.astype(float))) / 255.0)
    target_std = float(np.std(target))
    matched_std = float(np.std(matched))
    dark_old, dark_new = matched < 200, target < 200
    dark_union = int(np.logical_or(dark_old, dark_new).sum())
    dark_iou = float(np.logical_and(dark_old, dark_new).sum() / max(1, dark_union))
    edge_old, edge_new = cv2.Canny(matched, 50, 150) > 0, cv2.Canny(target, 50, 150) > 0
    edge_union = int(np.logical_or(edge_old, edge_new).sum())
    edge_iou = float(np.logical_and(edge_old, edge_new).sum() / max(1, edge_union))
    if target_std < 2.0 or matched_std < 2.0:
        match_class = "uninformative"
    elif score >= 0.90 and mae <= 0.15:
        match_class = "high"
    elif score >= 0.70:
        match_class = "medium"
    else:
        match_class = "low"
    return {
        "status": "measured",
        "class": match_class,
        "ncc": round(float(score), 6),
        "mae": round(mae, 6),
        "target_std": round(target_std, 6),
        "matched_std": round(matched_std, 6),
        "target_dark_fraction": round(float(dark_new.mean()), 6),
        "matched_dark_fraction": round(float(dark_old.mean()), 6),
        "dark_iou": round(dark_iou, 6),
        "edge_iou": round(edge_iou, 6),
        "offset_x": int(mx - x0),
        "offset_y": int(my - y0),
        "search_pad": pad,
    }


def render_panel(
    old_path: Path, new_path: Path, matrix: np.ndarray,
    bbox_new: list[float], output_path: Path,
) -> None:
    import cv2

    old = cv2.imread(str(old_path))
    new = cv2.imread(str(new_path))
    if old is None or new is None:
        raise ValueError("could not load source images")
    aligned = cv2.warpPerspective(
        old, matrix, (new.shape[1], new.shape[0]),
        borderValue=(255, 255, 255),
    )
    x0, y0, x1, y1 = integer_bbox(bbox_new)
    pad = 18
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(new.shape[1], x1 + pad), min(new.shape[0], y1 + pad)
    old_crop, new_crop = aligned[y0:y1, x0:x1], new[y0:y1, x0:x1]
    if not old_crop.size or not new_crop.size:
        raise ValueError("empty rendered crop")
    difference = cv2.absdiff(old_crop, new_crop)
    label_height = 34
    panels = []
    for label, crop in (("OLD aligned", old_crop), ("NEW", new_crop), ("ABS DIFF", difference)):
        canvas = np.full((crop.shape[0] + label_height, crop.shape[1], 3), 255, np.uint8)
        canvas[label_height:] = crop
        cv2.putText(canvas, label, (6, 23), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 0, 0), 1, cv2.LINE_AA)
        panels.append(canvas)
    separator = np.full((max(panel.shape[0] for panel in panels), 8, 3), 220, np.uint8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), np.hstack((panels[0], separator, panels[1], separator, panels[2])))


def build_audit(
    root: Path,
    project_id: str,
    output_dir: Path,
    render_limit: int = 0,
    render_ids: list[str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    quality_root = (root / "derived" / "quality").resolve()
    if not output_dir.is_relative_to(quality_root) or output_dir.exists():
        raise ValueError("output must be a new directory under root/derived/quality")
    if render_limit < 0:
        raise ValueError("render_limit must be nonnegative")

    before = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    pairs = [
        row for row in read_jsonl(root / ACTIVE_PATHS[1])
        if str(row.get("project_id") or "") == project_id
    ]
    if not pairs:
        raise ValueError(f"no active rows for project_id {project_id}")

    unified = {
        str(row.get("metadata", {}).get("pair_id") or ""): row
        for row in read_jsonl(root / ACTIVE_PATHS[0])
        if row.get("task") == "visualdiff" and isinstance(row.get("metadata"), dict)
    }
    manifests = {
        str(row.get("pair_id") or ""): row
        for row in read_jsonl(root / "manifest.jsonl") if row.get("type") == "pair"
    }
    manifest = manifests.get(project_id) or {}
    old_doc, new_doc = str(manifest.get("from_doc_id") or ""), str(manifest.get("to_doc_id") or "")
    old_spans = read_jsonl(root / "derived/textlayer" / f"{old_doc}.jsonl") if old_doc else []
    new_spans = read_jsonl(root / "derived/textlayer" / f"{new_doc}.jsonl") if new_doc else []

    h_cache: dict[int, tuple[np.ndarray, dict[str, Any], str]] = {}
    size_cache: dict[str, tuple[int, int]] = {}
    pairs.sort(key=lambda row: (int(row.get("page_index_new") or 0), str(row.get("pair_id") or "")))
    findings = []
    visual_page: int | None = None
    aligned_old: np.ndarray | None = None
    new_pixels: np.ndarray | None = None
    for row in pairs:
        pair_id = str(row.get("pair_id") or "")
        page = int(row.get("page_index_new") or 0)
        h_path = root / "derived/align" / project_id / f"H_page_{page:03d}.json"
        if page not in h_cache:
            payload = json.loads(h_path.read_text(encoding="utf-8")) if h_path.is_file() else {}
            matrix = np.asarray(payload.get("H") or [], dtype=float)
            if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
                matrix = np.empty((0, 0))
            h_cache[page] = (matrix, payload.get("info") or {}, h_path.relative_to(root).as_posix())
        matrix, h_info, h_relative = h_cache[page]

        linked = unified.get(pair_id) or {}
        images = linked.get("images") if isinstance(linked.get("images"), list) else []
        old_image = str(images[0]) if images else ""
        new_image = str(images[1]) if len(images) > 1 else ""
        for image_path in (old_image, new_image):
            if image_path and image_path not in size_cache:
                with Image.open(root / image_path) as image:
                    size_cache[image_path] = image.size

        inverse = np.linalg.inv(matrix) if matrix.shape == (3, 3) else np.empty((0, 0))
        projected = transform_bbox(row.get("bbox_new"), inverse)
        proposed = integer_bbox(projected) if projected else None
        old_size = size_cache.get(old_image, (0, 0))
        new_size = size_cache.get(new_image, (0, 0))
        stored_equals_new = row.get("bbox_old") == row.get("bbox_new")
        projected_valid = bool(projected and within_image(projected, old_size))
        h_ok = str(h_info.get("status") or "") == "ok"
        repair_candidate = bool(stored_equals_new and projected_valid and h_ok)
        old_text = spans_in_box(old_spans, int(row.get("page_index_old") or 0), proposed)
        new_text = spans_in_box(new_spans, page, row.get("bbox_new"))
        if old_text == new_text and old_text:
            text_relation = "exact_sequence_match"
        elif sorted(old_text) == sorted(new_text) and old_text:
            text_relation = "exact_multiset_match"
        elif not old_text and not new_text:
            text_relation = "no_text_in_either_box"
        elif old_text != new_text:
            text_relation = "different_text"
        else:
            text_relation = "other"
        if visual_page != page:
            import cv2

            old_pixels = cv2.imread(str(root / old_image))
            new_pixels = cv2.imread(str(root / new_image))
            if old_pixels is None or new_pixels is None or matrix.shape != (3, 3):
                aligned_old = None
            else:
                aligned_old = cv2.warpPerspective(
                    old_pixels, matrix, (new_pixels.shape[1], new_pixels.shape[0]),
                    borderValue=(255, 255, 255),
                )
            visual_page = page
        visual_match = (
            local_visual_match(aligned_old, new_pixels, row.get("bbox_new"))
            if aligned_old is not None and new_pixels is not None
            else {"status": "source_image_unavailable", "class": "unavailable"}
        )
        findings.append({
            "pair_id": pair_id,
            "split": str(row.get("split") or "unknown"),
            "current_description": str(row.get("change_desc_gt") or ""),
            "description_is_placeholder": str(row.get("change_desc_gt") or "").strip().upper() in {"TODO", "TBD", "CHANGE_DESC_TODO", "CHANGE_DESC_GT_TODO"},
            "page_index": page,
            "old_image": old_image,
            "new_image": new_image,
            "old_image_size": list(old_size),
            "new_image_size": list(new_size),
            "homography_path": h_relative,
            "homography_status": str(h_info.get("status") or "missing"),
            "homography_inlier_ratio": h_info.get("inlier_ratio"),
            "bbox_old_current": row.get("bbox_old"),
            "bbox_new_current": row.get("bbox_new"),
            "bbox_old_projected": proposed,
            "stored_old_equals_new": stored_equals_new,
            "projected_old_within_image": projected_valid,
            "stored_projected_iou": round(bbox_iou(row.get("bbox_old"), proposed), 6),
            "old_text_projected_box": old_text,
            "new_text_current_box": new_text,
            "text_relation": text_relation,
            "visual_match": visual_match,
            "repair_candidate": repair_candidate,
            "safe_to_apply": False,
        })

    findings.sort(key=lambda row: (not row["description_is_placeholder"], row["page_index"], row["pair_id"]))
    output_dir.mkdir(parents=True)
    proposals = output_dir / "proposed_bbox_repairs.jsonl"
    proposals.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in findings),
        encoding="utf-8",
    )
    csv_path = output_dir / "proposed_bbox_repairs.csv"
    csv_fields = (
        "pair_id", "split", "description_is_placeholder", "page_index",
        "homography_status", "homography_inlier_ratio", "bbox_old_current",
        "bbox_new_current", "bbox_old_projected", "stored_old_equals_new",
        "projected_old_within_image", "stored_projected_iou", "text_relation",
        "repair_candidate", "safe_to_apply",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in findings:
            writer.writerow({
                key: json.dumps(row[key], ensure_ascii=False)
                if isinstance(row.get(key), (list, dict)) else row.get(key, "")
                for key in csv_fields
            })

    requested_ids = set(render_ids or [])
    known_ids = {row["pair_id"] for row in findings}
    unknown_ids = requested_ids - known_ids
    if unknown_ids:
        raise ValueError(f"render IDs not active in project: {sorted(unknown_ids)}")
    selected = []
    seen = set()
    for row in [*findings[:render_limit], *[row for row in findings if row["pair_id"] in requested_ids]]:
        if row["pair_id"] not in seen:
            selected.append(row)
            seen.add(row["pair_id"])
    rendered = 0
    for row in selected:
        if not row["repair_candidate"]:
            continue
        matrix = h_cache[row["page_index"]][0]
        render_panel(
            root / row["old_image"], root / row["new_image"], matrix,
            row["bbox_new_current"], output_dir / "panels" / f"{row['pair_id']}.png",
        )
        rendered += 1

    after = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    report = {
        "goal": "Gold v2.0 Global",
        "status": "OPEN",
        "project_id": project_id,
        "active_rows": len(findings),
        "placeholder_rows": sum(row["description_is_placeholder"] for row in findings),
        "stored_old_equals_new_rows": sum(row["stored_old_equals_new"] for row in findings),
        "projected_old_within_image_rows": sum(row["projected_old_within_image"] for row in findings),
        "repair_candidate_rows": sum(row["repair_candidate"] for row in findings),
        "by_text_relation": dict(sorted(Counter(row["text_relation"] for row in findings).items())),
        "by_visual_match": dict(sorted(Counter(row["visual_match"]["class"] for row in findings).items())),
        "by_page": dict(sorted(Counter(str(row["page_index"]) for row in findings).items())),
        "rendered_panels": rendered,
        "proposal_jsonl": proposals.relative_to(root).as_posix(),
        "proposal_csv": csv_path.relative_to(root).as_posix(),
        "proposal_sha256": file_hash(proposals),
        "active_hashes_before": before,
        "active_hashes_after": after,
        "active_gold_modified": before != after,
        "safe_to_apply": False,
        "gold_rows_modified": 0,
        "limitation": (
            "A valid inverse homography proves the source-coordinate mapping, not the "
            "semantic correctness of a description. Apply repairs only through a "
            "snapshot-backed transaction and keep semantic holds separate."
        ),
    }
    if report["active_gold_modified"]:
        report["status"] = "FAIL"
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--render-limit", type=int, default=0)
    parser.add_argument("--render-pair", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_audit(
        root, args.project_id, root / args.output_dir,
        args.render_limit, args.render_pair,
    )
    print(json.dumps({
        key: report[key] for key in (
            "status", "project_id", "active_rows", "placeholder_rows",
            "stored_old_equals_new_rows", "projected_old_within_image_rows",
            "repair_candidate_rows", "by_text_relation", "by_visual_match", "rendered_panels",
            "active_gold_modified",
        )
    }, indent=2))
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
