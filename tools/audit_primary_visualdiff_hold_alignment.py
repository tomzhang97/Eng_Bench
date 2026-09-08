#!/usr/bin/env python3
"""Triage primary-review VisualDiff evidence holds with saved alignments.

This tool is deliberately read-only with respect to active Gold. It separates
human-rejected no-change candidates from cross-location failures and from rows
whose evidence can be rerendered with the saved old-to-new homography.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from .candidate_evidence_holds import evidence_hold_ids
except ImportError:
    from candidate_evidence_holds import evidence_hold_ids


ACTIVE_PATHS = (
    "eng_bench.jsonl",
    "manifest.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
)

LANE_RETIRE_NO_CHANGE = "retire_human_confirmed_no_change"
LANE_RETIRE_MACHINE_NO_CHANGE = "retire_machine_confirmed_no_engineering_change"
LANE_RETIRE_CROSS_LOCATION = "retire_machine_confirmed_invalid_or_cross_location"
LANE_RERENDER = "rerender_aligned_evidence_then_human_rereview"
LANE_MACHINE_INSPECT = "machine_alignment_inspection_required"
LANE_REBUILD = "rebuild_missing_alignment_then_human_rereview"


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def spans_in_box(
    spans: list[dict[str, Any]], page: int, value: Any,
) -> list[str]:
    box = valid_bbox(value)
    if box is None:
        return []
    found: list[str] = []
    for span in spans:
        span_box = valid_bbox(span.get("bbox_px"))
        text = normalized_text(span.get("text"))
        if int(span.get("page") or 0) != page or span_box is None or not text:
            continue
        center_x = (span_box[0] + span_box[2]) / 2.0
        center_y = (span_box[1] + span_box[3]) / 2.0
        if box[0] <= center_x <= box[2] and box[1] <= center_y <= box[3]:
            found.append(text)
    return found


def textlayer_relation(old_text: list[str], new_text: list[str]) -> str:
    if old_text == new_text and old_text:
        return "exact_sequence_match"
    if sorted(old_text) == sorted(new_text) and old_text:
        return "exact_multiset_match"
    if not old_text and not new_text:
        return "no_text_in_either_box"
    return "different_text"


def source_doc_id(source: dict[str, Any], side: str) -> str:
    explicit = str(source.get(f"{side}_doc_id") or "").strip()
    if explicit:
        return explicit
    image_path = str(source.get(f"image_{side}") or "").strip()
    return Path(image_path).parent.name if image_path else ""


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


def integer_bbox(value: Any) -> list[int] | None:
    box = valid_bbox(value)
    if box is None:
        return None
    return [math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])]


def transform_bbox(value: Any, matrix: np.ndarray) -> list[float] | None:
    box = valid_bbox(value)
    if box is None or matrix.shape != (3, 3):
        return None
    x0, y0, x1, y1 = box
    corners = np.asarray(
        [[x0, y0, 1.0], [x1, y0, 1.0], [x1, y1, 1.0], [x0, y1, 1.0]],
        dtype=float,
    ).T
    mapped = matrix @ corners
    if np.any(np.isclose(mapped[2], 0.0)):
        return None
    mapped = mapped[:2] / mapped[2]
    if not np.isfinite(mapped).all():
        return None
    return [
        float(mapped[0].min()),
        float(mapped[1].min()),
        float(mapped[0].max()),
        float(mapped[1].max()),
    ]


def bbox_iou(first: Any, second: Any) -> float:
    a, b = valid_bbox(first), valid_bbox(second)
    if a is None or b is None:
        return 0.0
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (
        (a[2] - a[0]) * (a[3] - a[1])
        + (b[2] - b[0]) * (b[3] - b[1])
        - intersection
    )
    return intersection / union if union > 0 else 0.0


def normalized_center_distance(first: Any, second: Any) -> float | None:
    a, b = valid_bbox(first), valid_bbox(second)
    if a is None or b is None:
        return None
    ac = ((a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0)
    bc = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
    scale = max(10.0, math.hypot(b[2] - b[0], b[3] - b[1]))
    return math.hypot(ac[0] - bc[0], ac[1] - bc[1]) / scale


def page_index(path: str) -> int:
    match = re.search(r"page_(\d+)\.png$", path.replace("\\", "/"))
    return int(match.group(1)) if match else 0


def clamp_context(
    image: np.ndarray, value: Any, pad: int,
) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    box = integer_bbox(value)
    if box is None:
        return None
    x0 = max(0, box[0] - pad)
    y0 = max(0, box[1] - pad)
    x1 = min(image.shape[1], box[2] + pad)
    y1 = min(image.shape[0], box[3] + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    return image[y0:y1, x0:x1], (x0, y0, x1, y1)


def local_correspondence(
    aligned_old: np.ndarray, new_image: np.ndarray, value: Any,
) -> dict[str, Any]:
    box = valid_bbox(value)
    if box is None:
        return {"status": "invalid_bbox", "class": "unavailable"}
    width, height = box[2] - box[0], box[3] - box[1]
    context_pad = max(18, min(72, round(max(width, height) * 0.75)))
    search_pad = max(48, min(220, round(max(width, height) * 2.0)))
    target_info = clamp_context(new_image, box, context_pad)
    if target_info is None:
        return {"status": "new_context_unavailable", "class": "unavailable"}
    target_color, target_bounds = target_info
    tx0, ty0, tx1, ty1 = target_bounds
    sx0, sy0 = max(0, tx0 - search_pad), max(0, ty0 - search_pad)
    sx1 = min(aligned_old.shape[1], tx1 + search_pad)
    sy1 = min(aligned_old.shape[0], ty1 + search_pad)
    search_color = aligned_old[sy0:sy1, sx0:sx1]
    if (
        not target_color.size
        or search_color.shape[0] < target_color.shape[0]
        or search_color.shape[1] < target_color.shape[1]
    ):
        return {"status": "empty_search", "class": "unavailable"}
    target = cv2.cvtColor(target_color, cv2.COLOR_BGR2GRAY)
    search = cv2.cvtColor(search_color, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(search, target, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    mx, my = sx0 + location[0], sy0 + location[1]
    matched = cv2.cvtColor(
        aligned_old[my : my + target.shape[0], mx : mx + target.shape[1]],
        cv2.COLOR_BGR2GRAY,
    )
    delta = np.abs(matched.astype(np.int16) - target.astype(np.int16))
    mae = float(delta.mean() / 255.0)
    changed_ratio = float((delta > 16).mean())
    target_std = float(np.std(target))
    matched_std = float(np.std(matched))
    target_dark, matched_dark = target < 200, matched < 200
    dark_union = int(np.logical_or(target_dark, matched_dark).sum())
    dark_iou = float(np.logical_and(target_dark, matched_dark).sum() / max(1, dark_union))
    target_edges = cv2.Canny(target, 50, 150) > 0
    matched_edges = cv2.Canny(matched, 50, 150) > 0
    edge_union = int(np.logical_or(target_edges, matched_edges).sum())
    edge_iou = float(np.logical_and(target_edges, matched_edges).sum() / max(1, edge_union))
    informative = bool(
        target_std >= 4.0
        and matched_std >= 4.0
        and (float(target_dark.mean()) >= 0.005 or int(target_edges.sum()) >= 12)
    )
    if not informative:
        match_class = "uninformative"
    elif score >= 0.92 and mae <= 0.16:
        match_class = "high"
    elif score >= 0.75 and mae <= 0.25:
        match_class = "medium"
    else:
        match_class = "low"
    return {
        "status": "measured",
        "class": match_class,
        "ncc": round(float(score), 6),
        "mae": round(mae, 6),
        "changed_pixel_ratio_gt16": round(changed_ratio, 6),
        "target_std": round(target_std, 6),
        "matched_std": round(matched_std, 6),
        "target_dark_fraction": round(float(target_dark.mean()), 6),
        "dark_iou": round(dark_iou, 6),
        "edge_iou": round(edge_iou, 6),
        "best_offset_x": int(mx - tx0),
        "best_offset_y": int(my - ty0),
        "context_pad": context_pad,
        "search_pad": search_pad,
    }


def bbox_signal(image: np.ndarray | None, value: Any) -> dict[str, Any]:
    """Measure whether the exact evidence box contains visible engineering ink."""
    if image is None:
        return {"status": "image_unavailable", "informative": False}
    box = integer_bbox(value)
    if box is None:
        return {"status": "invalid_bbox", "informative": False}
    x0, y0 = max(0, box[0]), max(0, box[1])
    x1, y1 = min(image.shape[1], box[2]), min(image.shape[0], box[3])
    if x1 <= x0 or y1 <= y0:
        return {"status": "empty_bbox", "informative": False}
    gray = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    dark_fraction = float((gray < 220).mean())
    std = float(np.std(gray))
    edge_pixels = int((cv2.Canny(gray, 50, 150) > 0).sum())
    informative = bool(std >= 3.0 and (dark_fraction >= 0.01 or edge_pixels >= 4))
    return {
        "status": "measured",
        "informative": informative,
        "dark_fraction": round(dark_fraction, 6),
        "std": round(std, 6),
        "edge_pixels": edge_pixels,
        "width": int(gray.shape[1]),
        "height": int(gray.shape[0]),
    }


def classify_hold(
    decision_code: int,
    homography_ok: bool,
    correspondence_class: str,
    projected_iou: float,
    center_distance: float | None,
    new_box_informative: bool = True,
    proposed_old_box_informative: bool = True,
    corrected_text_relation: str = "unavailable",
    best_offset_x: int | None = None,
    best_offset_y: int | None = None,
    changed_pixel_ratio: float | None = None,
) -> tuple[str, str]:
    """Return a conservative machine action; never authorize Gold promotion."""
    if decision_code == 3:
        return LANE_RETIRE_NO_CHANGE, "retire_candidate_keep_out_of_gold"
    if decision_code != 4:
        return LANE_MACHINE_INSPECT, "inspect_invalid_review_decision"
    if not homography_ok:
        return LANE_REBUILD, "rebuild_alignment_and_rerender_before_human_rereview"
    both_boxes_informative = new_box_informative and proposed_old_box_informative
    small_residual_offset = bool(
        best_offset_x is not None
        and best_offset_y is not None
        and abs(best_offset_x) <= 3
        and abs(best_offset_y) <= 3
    )
    bounded_pixel_delta = bool(
        changed_pixel_ratio is not None and changed_pixel_ratio <= 0.25
    )
    text_and_visual_agree = bool(
        corrected_text_relation == "exact_sequence_match"
        and correspondence_class in {"high", "medium"}
        and small_residual_offset
        and bounded_pixel_delta
    )
    visual_only_agrees = bool(
        corrected_text_relation == "no_text_in_either_box"
        and correspondence_class == "high"
        and both_boxes_informative
        and small_residual_offset
        and bounded_pixel_delta
    )
    if text_and_visual_agree or visual_only_agrees:
        return (
            LANE_RETIRE_MACHINE_NO_CHANGE,
            "retire_machine_confirmed_no_engineering_change_keep_out_of_gold",
        )
    # With a valid homography, one-sided blank evidence can be a real addition
    # or deletion. Preserve it for corrected human review.
    return LANE_RERENDER, "use_inverse_homography_old_box_and_human_rereview"


def draw_context(
    image: np.ndarray, value: Any, label: str, pad: int = 36,
) -> np.ndarray:
    crop_info = clamp_context(image, value, pad)
    if crop_info is None:
        return np.full((180, 320, 3), 255, np.uint8)
    crop, bounds = crop_info
    x0, y0, _, _ = bounds
    box = integer_bbox(value) or [0, 0, 1, 1]
    panel = crop.copy()
    cv2.rectangle(
        panel,
        (max(0, box[0] - x0), max(0, box[1] - y0)),
        (min(panel.shape[1] - 1, box[2] - x0), min(panel.shape[0] - 1, box[3] - y0)),
        (0, 0, 220),
        3,
    )
    header = np.full((34, panel.shape[1], 3), 255, np.uint8)
    cv2.putText(header, label, (6, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return np.vstack([header, panel])


def normalize_panel(panel: np.ndarray, width: int = 360, height: int = 280) -> np.ndarray:
    scale = min(width / panel.shape[1], height / panel.shape[0])
    resized = cv2.resize(
        panel,
        (max(1, round(panel.shape[1] * scale)), max(1, round(panel.shape[0] * scale))),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    canvas = np.full((height, width, 3), 245, np.uint8)
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def render_panel(
    old_image: np.ndarray,
    aligned_old: np.ndarray,
    new_image: np.ndarray,
    bbox_old: Any,
    bbox_old_recommended: Any,
    bbox_new: Any,
    output: Path,
) -> None:
    old_current = normalize_panel(draw_context(old_image, bbox_old, "OLD current crop"))
    aligned = normalize_panel(draw_context(aligned_old, bbox_new, "OLD aligned at NEW"))
    new = normalize_panel(draw_context(new_image, bbox_new, "NEW crop"))
    inverse = normalize_panel(draw_context(old_image, bbox_old_recommended, "OLD proposed crop"))
    separator = np.full((280, 8, 3), 200, np.uint8)
    canvas = np.hstack([old_current, separator, aligned, separator, new, separator, inverse])
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise ValueError(f"could not write panel: {output}")


def load_contact_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\consola.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def build_contact_sheets(
    root: Path,
    output_dir: Path,
    findings: list[dict[str, Any]],
    rows_per_sheet: int = 12,
) -> list[dict[str, Any]]:
    rendered = [row for row in findings if row.get("inspection_panel")]
    by_lane: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rendered:
        by_lane[row["triage_lane"]].append(row)
    font = load_contact_font(13)
    result: list[dict[str, Any]] = []
    tile_width, tile_height, columns = 760, 205, 2
    for lane, rows in sorted(by_lane.items()):
        lane_dir = output_dir / "contact_sheets" / lane
        lane_dir.mkdir(parents=True, exist_ok=True)
        for sheet_index in range(math.ceil(len(rows) / rows_per_sheet)):
            page_rows = rows[sheet_index * rows_per_sheet : (sheet_index + 1) * rows_per_sheet]
            sheet_rows = math.ceil(len(page_rows) / columns)
            sheet = Image.new(
                "RGB", (tile_width * columns, tile_height * sheet_rows), "white"
            )
            draw = ImageDraw.Draw(sheet)
            for offset, row in enumerate(page_rows):
                x = (offset % columns) * tile_width
                y = (offset // columns) * tile_height
                panel_path = root / row["inspection_panel"]
                with Image.open(panel_path) as source:
                    panel = source.convert("RGB")
                panel.thumbnail((tile_width - 12, tile_height - 42), Image.Resampling.LANCZOS)
                sheet.paste(panel, (x + 6, y + 34))
                label = f"#{row['primary_index']} {row['pair_id']}"
                draw.text((x + 6, y + 8), label[:100], fill="black", font=font)
                draw.rectangle(
                    (x, y, x + tile_width - 1, y + tile_height - 1),
                    outline=(180, 180, 180), width=1,
                )
            sheet_path = lane_dir / f"sheet_{sheet_index + 1:02d}.png"
            sheet.save(sheet_path, optimize=True)
            result.append({
                "lane": lane,
                "rows": len(page_rows),
                "path": sheet_path.relative_to(root).as_posix(),
                "sha256": file_hash(sheet_path),
            })
    return result


def build_audit(
    root: Path,
    holds_path: Path,
    pool_path: Path,
    output_dir: Path,
    render_per_project_lane: int = 1,
    render_lanes: set[str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    holds_path, pool_path, output_dir = (
        holds_path.resolve(), pool_path.resolve(), output_dir.resolve()
    )
    quality_root = (root / "derived/quality").resolve()
    if output_dir.exists() or not output_dir.is_relative_to(quality_root):
        raise ValueError("output must be a new directory under derived/quality")
    if render_per_project_lane < 0:
        raise ValueError("render-per-project-lane must be nonnegative")
    for path, name in ((holds_path, "holds"), (pool_path, "pool")):
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError(f"{name} input must be an existing file inside root")

    before = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    holds = read_jsonl(holds_path)
    pool = read_jsonl(pool_path)
    if not holds or not pool:
        raise ValueError("holds and pool must be nonempty")
    hold_ids = [str(row.get("pair_id") or "") for row in holds]
    if "" in hold_ids or len(set(hold_ids)) != len(hold_ids):
        raise ValueError("hold identities are missing or duplicated")
    active_holds = evidence_hold_ids(root)
    if not set(hold_ids).issubset(active_holds):
        raise ValueError("input contains VisualDiff rows not present in active evidence holds")
    pool_by_id = {
        str(row.get("pair_id") or row.get("record_id") or ""): row
        for row in pool
        if str(row.get("pair_id") or row.get("record_id") or "")
    }
    missing = sorted(set(hold_ids) - set(pool_by_id))
    if missing:
        raise ValueError(f"held rows missing from assignment pool: {missing[:5]}")

    output_dir.mkdir(parents=True)
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for hold in holds:
        source = pool_by_id[hold["pair_id"]]
        old_path = str(source.get("image_old") or hold.get("old_page_path") or "")
        new_path = str(source.get("image_new") or hold.get("new_page_path") or "")
        grouped[(str(hold.get("project_id") or source.get("project_id") or ""), page_index(new_path))].append(hold)

    findings: list[dict[str, Any]] = []
    rendered_by_key: Counter[tuple[str, str]] = Counter()
    textlayer_cache: dict[str, list[dict[str, Any]]] = {}

    def textlayer(doc_id: str) -> list[dict[str, Any]]:
        if doc_id not in textlayer_cache:
            path = root / "derived/textlayer" / f"{doc_id}.jsonl"
            textlayer_cache[doc_id] = read_jsonl(path) if path.is_file() else []
        return textlayer_cache[doc_id]

    for (project_id, page), project_holds in sorted(grouped.items()):
        sample = pool_by_id[project_holds[0]["pair_id"]]
        old_relative = str(sample.get("image_old") or project_holds[0].get("old_page_path") or "")
        new_relative = str(sample.get("image_new") or project_holds[0].get("new_page_path") or "")
        old_path, new_path = root / old_relative, root / new_relative
        old_image = cv2.imread(str(old_path)) if old_path.is_file() else None
        new_image = cv2.imread(str(new_path)) if new_path.is_file() else None
        h_path = root / "derived/align" / project_id / f"H_page_{page:03d}.json"
        h_payload: dict[str, Any] = {}
        matrix = np.empty((0, 0))
        if h_path.is_file():
            h_payload = json.loads(h_path.read_text(encoding="utf-8"))
            candidate = np.asarray(h_payload.get("H") or [], dtype=float)
            if candidate.shape == (3, 3) and np.isfinite(candidate).all():
                matrix = candidate
        h_info = h_payload.get("info") if isinstance(h_payload.get("info"), dict) else {}
        homography_ok = bool(matrix.shape == (3, 3) and h_info.get("status") == "ok")
        aligned_old = None
        inverse = None
        if homography_ok and old_image is not None and new_image is not None:
            aligned_old = cv2.warpPerspective(
                old_image,
                matrix,
                (new_image.shape[1], new_image.shape[0]),
                borderValue=(255, 255, 255),
            )
            inverse = np.linalg.inv(matrix)

        for hold in sorted(project_holds, key=lambda row: str(row["pair_id"])):
            source = pool_by_id[hold["pair_id"]]
            bbox_old, bbox_new = source.get("bbox_old"), source.get("bbox_new")
            projected = transform_bbox(bbox_old, matrix) if homography_ok else None
            recommended_old = transform_bbox(bbox_new, inverse) if inverse is not None else None
            iou = bbox_iou(projected, bbox_new)
            distance = normalized_center_distance(projected, bbox_new)
            correspondence = (
                local_correspondence(aligned_old, new_image, bbox_new)
                if aligned_old is not None and new_image is not None
                else {"status": "alignment_or_image_unavailable", "class": "unavailable"}
            )
            new_signal = bbox_signal(new_image, bbox_new)
            proposed_old_signal = bbox_signal(old_image, recommended_old)
            old_doc_id = source_doc_id(source, "old")
            new_doc_id = source_doc_id(source, "new")
            old_page = int(source.get("page_old") or source.get("page_index_old") or page)
            new_page = int(source.get("page_new") or source.get("page_index_new") or page)
            corrected_old_text = spans_in_box(
                textlayer(old_doc_id), old_page, recommended_old,
            )
            corrected_new_text = spans_in_box(
                textlayer(new_doc_id), new_page, bbox_new,
            )
            corrected_relation = textlayer_relation(corrected_old_text, corrected_new_text)
            decision_code = int(hold.get("primary_reviewer_decision_code") or 0)
            lane, action = classify_hold(
                decision_code,
                homography_ok,
                str(correspondence.get("class") or "unavailable"),
                iou,
                distance,
                bool(new_signal.get("informative")),
                bool(proposed_old_signal.get("informative")),
                corrected_relation,
                correspondence.get("best_offset_x"),
                correspondence.get("best_offset_y"),
                correspondence.get("changed_pixel_ratio_gt16"),
            )
            finding = {
                "pair_id": hold["pair_id"],
                "project_id": project_id,
                "reserved_split": hold.get("reserved_split"),
                "primary_index": hold.get("primary_index"),
                "reviewer_decision_code": decision_code,
                "reviewer_status": hold.get("primary_reviewer_status"),
                "reviewer_basis": hold.get("engineering_review_basis"),
                "hold_reason": hold.get("hold_reason"),
                "image_old": old_relative,
                "image_new": new_relative,
                "page_index": page,
                "bbox_old_current": bbox_old,
                "bbox_new_current": bbox_new,
                "bbox_old_projected_to_new": projected,
                "bbox_old_recommended_from_new": recommended_old,
                "projected_new_iou": round(iou, 6),
                "projected_center_distance_norm": round(distance, 6) if distance is not None else None,
                "homography_path": h_path.relative_to(root).as_posix(),
                "homography_status": "ok" if homography_ok else "missing_or_invalid",
                "homography_inlier_ratio": h_info.get("inlier_ratio"),
                "local_correspondence": correspondence,
                "new_box_signal": new_signal,
                "proposed_old_box_signal": proposed_old_signal,
                "old_doc_id": old_doc_id,
                "new_doc_id": new_doc_id,
                "corrected_old_text": corrected_old_text,
                "corrected_new_text": corrected_new_text,
                "corrected_text_relation": corrected_relation,
                "triage_lane": lane,
                "recommended_action": action,
                "requires_new_human_review": lane in {LANE_RERENDER, LANE_REBUILD},
                "safe_to_merge_gold": False,
            }
            render_key = (project_id, lane)
            if (
                render_per_project_lane
                and (not render_lanes or lane in render_lanes)
                and rendered_by_key[render_key] < render_per_project_lane
                and old_image is not None
                and new_image is not None
                and aligned_old is not None
                and recommended_old is not None
            ):
                panel = output_dir / "panels" / f"{hold['pair_id']}.png"
                render_panel(
                    old_image, aligned_old, new_image,
                    bbox_old, recommended_old, bbox_new, panel,
                )
                finding["inspection_panel"] = panel.relative_to(root).as_posix()
                finding["inspection_panel_sha256"] = file_hash(panel)
                rendered_by_key[render_key] += 1
            findings.append(finding)

    findings.sort(key=lambda row: (row["triage_lane"], row["project_id"], row["pair_id"]))
    lanes = {
        lane: [row for row in findings if row["triage_lane"] == lane]
        for lane in (
            LANE_RETIRE_NO_CHANGE,
            LANE_RETIRE_MACHINE_NO_CHANGE,
            LANE_RETIRE_CROSS_LOCATION,
            LANE_RERENDER,
            LANE_MACHINE_INSPECT,
            LANE_REBUILD,
        )
    }
    artifact_names = {
        "findings": "alignment_findings.jsonl",
        "retire_no_change": "retire_human_confirmed_no_change.jsonl",
        "retire_machine_no_change": "retire_machine_confirmed_no_engineering_change.jsonl",
        "retire_cross_location": "retire_machine_confirmed_invalid_or_cross_location.jsonl",
        "rerender": "rerender_then_human_rereview.jsonl",
        "machine_inspect": "machine_alignment_inspection.jsonl",
        "rebuild": "rebuild_missing_alignment.jsonl",
    }
    write_jsonl(output_dir / artifact_names["findings"], findings)
    write_jsonl(output_dir / artifact_names["retire_no_change"], lanes[LANE_RETIRE_NO_CHANGE])
    write_jsonl(
        output_dir / artifact_names["retire_machine_no_change"],
        lanes[LANE_RETIRE_MACHINE_NO_CHANGE],
    )
    write_jsonl(output_dir / artifact_names["retire_cross_location"], lanes[LANE_RETIRE_CROSS_LOCATION])
    write_jsonl(output_dir / artifact_names["rerender"], lanes[LANE_RERENDER])
    write_jsonl(output_dir / artifact_names["machine_inspect"], lanes[LANE_MACHINE_INSPECT])
    write_jsonl(output_dir / artifact_names["rebuild"], lanes[LANE_REBUILD])

    csv_path = output_dir / "alignment_summary.csv"
    csv_fields = (
        "pair_id", "project_id", "reserved_split", "primary_index",
        "reviewer_decision_code", "hold_reason", "homography_status",
        "projected_new_iou", "projected_center_distance_norm",
        "correspondence_class", "ncc", "mae", "corrected_text_relation",
        "corrected_old_text", "corrected_new_text", "triage_lane",
        "new_box_informative", "proposed_old_box_informative",
        "recommended_action", "requires_new_human_review", "inspection_panel",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in findings:
            local = row["local_correspondence"]
            writer.writerow({
                **{key: row.get(key, "") for key in csv_fields},
                "correspondence_class": local.get("class", ""),
                "ncc": local.get("ncc", ""),
                "mae": local.get("mae", ""),
                "new_box_informative": row["new_box_signal"].get("informative", False),
                "proposed_old_box_informative": row["proposed_old_box_signal"].get("informative", False),
            })

    contact_sheets = build_contact_sheets(root, output_dir, findings)

    after = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    report = {
        "goal": "Gold v2.0 Global",
        "status": "PASS" if before == after else "FAIL",
        "mode": "read_only_primary_visualdiff_hold_alignment_triage",
        "inputs": {
            "holds": holds_path.relative_to(root).as_posix(),
            "holds_sha256": file_hash(holds_path),
            "pool": pool_path.relative_to(root).as_posix(),
            "pool_sha256": file_hash(pool_path),
        },
        "counts": {
            "held_rows": len(findings),
            "projects": len({row["project_id"] for row in findings}),
            "homography_available": sum(row["homography_status"] == "ok" for row in findings),
            "human_confirmed_no_change_retire": len(lanes[LANE_RETIRE_NO_CHANGE]),
            "machine_confirmed_no_engineering_change_retire": len(
                lanes[LANE_RETIRE_MACHINE_NO_CHANGE]
            ),
            "machine_confirmed_cross_location_retire": len(lanes[LANE_RETIRE_CROSS_LOCATION]),
            "rerender_then_human_rereview": len(lanes[LANE_RERENDER]),
            "machine_alignment_inspection": len(lanes[LANE_MACHINE_INSPECT]),
            "rebuild_missing_alignment": len(lanes[LANE_REBUILD]),
            "new_human_review_after_machine_repair": sum(
                bool(row["requires_new_human_review"]) for row in findings
            ),
            "rendered_inspection_panels": sum("inspection_panel" in row for row in findings),
            "contact_sheets": len(contact_sheets),
        },
        "by_correspondence": dict(sorted(Counter(
            str(row["local_correspondence"].get("class") or "unavailable")
            for row in findings
        ).items())),
        "by_corrected_text_relation": dict(sorted(Counter(
            row["corrected_text_relation"] for row in findings
        ).items())),
        "by_project": dict(sorted(Counter(row["project_id"] for row in findings).items())),
        "artifacts": artifact_names | {"summary_csv": csv_path.name},
        "artifact_sha256": {
            name: file_hash(output_dir / name)
            for name in [*artifact_names.values(), csv_path.name]
        },
        "contact_sheets": contact_sheets,
        "active_gold_hashes_before": before,
        "active_gold_hashes_after": after,
        "active_gold_modified": before != after,
        "gold_rows_modified": 0,
        "safe_to_merge_gold": False,
        "interpretation": (
            "Retirement lanes consume no additional human review and remain outside Gold. "
            "Machine no-change retirement requires matching corrected text-layer evidence "
            "plus small-offset visual agreement, or strong visual agreement when neither "
            "box contains text. Rerender lanes require corrected evidence and a new human "
            "decision before any promotion preview. Machine-inspection rows remain held."
        ),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if report["status"] != "PASS":
        raise ValueError("active Gold changed during read-only alignment audit")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--holds", type=Path, required=True)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--render-per-project-lane", type=int, default=1)
    parser.add_argument(
        "--render-lane",
        action="append",
        choices=[
            LANE_RETIRE_NO_CHANGE,
            LANE_RETIRE_MACHINE_NO_CHANGE,
            LANE_RETIRE_CROSS_LOCATION,
            LANE_RERENDER,
            LANE_MACHINE_INSPECT,
            LANE_REBUILD,
        ],
        default=[],
        help="render only selected triage lanes; repeat to select multiple lanes",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    resolve = lambda value: value if value.is_absolute() else root / value
    report = build_audit(
        root,
        resolve(args.holds),
        resolve(args.pool),
        resolve(args.output_dir),
        args.render_per_project_lane,
        set(args.render_lane),
    )
    print(json.dumps({
        "status": report["status"],
        **report["counts"],
        "active_gold_modified": report["active_gold_modified"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
