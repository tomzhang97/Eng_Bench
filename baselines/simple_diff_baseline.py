#!/usr/bin/env python3
"""Simple image-difference visualdiff baselines for unified Eng_Bench rows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageChops


DEFAULT_ANSWER = "Differences detected in the highest absolute-difference region."
EDGE_ANSWER = "Structural differences detected in the changed edge-map region."
SSIM_ANSWER = "Local structure changed in the lowest structural-similarity region."
PHASE_ANSWER = "Differences detected after compensating global translation between revisions."
ORB_ANSWER = "Change suggested by the densest cluster of unmatched local keypoints."
LARGEST_CC_ANSWER = "Differences detected in the largest connected changed component."
TILE_ZNCC_ANSWER = "Change localized to the image tile with the lowest normalized cross-correlation."


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_id(row: dict[str, Any]) -> str | None:
    value = row.get("id") or row.get("question_id") or row.get("qid")
    return str(value) if value is not None else None


def resized_gray(path: Path, max_dim: int) -> tuple[Image.Image, tuple[int, int]]:
    image = Image.open(path).convert("L")
    original_size = image.size
    if max(image.size) > max_dim:
        image.thumbnail((max_dim, max_dim), Image.Resampling.BILINEAR)
    return image, original_size


def scale_bbox(
    bbox: list[int],
    resized_size: tuple[int, int],
    original_size: tuple[int, int],
) -> list[int]:
    x_scale = original_size[0] / resized_size[0]
    y_scale = original_size[1] / resized_size[1]
    return [
        int(max(0, round(bbox[0] * x_scale))),
        int(max(0, round(bbox[1] * y_scale))),
        int(min(original_size[0], round(bbox[2] * x_scale))),
        int(min(original_size[1], round(bbox[3] * y_scale))),
    ]


def diff_bbox(old_path: Path, new_path: Path, max_dim: int = 1024) -> tuple[list[int], tuple[int, int]]:
    old_image, old_original_size = resized_gray(old_path, max_dim)
    new_image, _ = resized_gray(new_path, max_dim)
    if new_image.size != old_image.size:
        new_image = new_image.resize(old_image.size, Image.Resampling.BILINEAR)

    diff = ImageChops.difference(old_image, new_image)
    arr = np.asarray(diff, dtype=np.uint8)
    if arr.size == 0 or int(arr.max()) == 0:
        width, height = old_image.size
        fallback = [width // 4, height // 4, max(width // 4 + 1, width * 3 // 4), max(height // 4 + 1, height * 3 // 4)]
        return scale_bbox(fallback, old_image.size, old_original_size), old_original_size

    nonzero = arr[arr > 0]
    threshold = max(20, int(np.percentile(nonzero, 95))) if nonzero.size else 20
    mask = arr >= threshold
    if not mask.any():
        mask = arr > 0
    ys, xs = np.where(mask)
    pad = 2
    bbox = [
        max(0, int(xs.min()) - pad),
        max(0, int(ys.min()) - pad),
        min(old_image.size[0], int(xs.max()) + 1 + pad),
        min(old_image.size[1], int(ys.max()) + 1 + pad),
    ]
    return scale_bbox(bbox, old_image.size, old_original_size), old_original_size


def edge_diff_bbox(old_path: Path, new_path: Path, max_dim: int = 1024) -> tuple[list[int], tuple[int, int]]:
    old_image, old_original_size = resized_gray(old_path, max_dim)
    new_image, _ = resized_gray(new_path, max_dim)
    if new_image.size != old_image.size:
        new_image = new_image.resize(old_image.size, Image.Resampling.BILINEAR)

    old_edges = cv2.Canny(np.asarray(old_image, dtype=np.uint8), 50, 150)
    new_edges = cv2.Canny(np.asarray(new_image, dtype=np.uint8), 50, 150)
    mask = cv2.absdiff(old_edges, new_edges) > 0
    if not mask.any():
        width, height = old_image.size
        fallback = [width // 4, height // 4, max(width // 4 + 1, width * 3 // 4), max(height // 4 + 1, height * 3 // 4)]
        return scale_bbox(fallback, old_image.size, old_original_size), old_original_size

    ys, xs = np.where(mask)
    pad = 2
    bbox = [
        max(0, int(xs.min()) - pad),
        max(0, int(ys.min()) - pad),
        min(old_image.size[0], int(xs.max()) + 1 + pad),
        min(old_image.size[1], int(ys.max()) + 1 + pad),
    ]
    return scale_bbox(bbox, old_image.size, old_original_size), old_original_size


def ssim_map(old_arr: np.ndarray, new_arr: np.ndarray) -> np.ndarray:
    """Gaussian-window SSIM map between two grayscale uint8 images."""
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    old = old_arr.astype(np.float64)
    new = new_arr.astype(np.float64)
    kernel = (11, 11)
    sigma = 1.5
    mu_old = cv2.GaussianBlur(old, kernel, sigma)
    mu_new = cv2.GaussianBlur(new, kernel, sigma)
    mu_old_sq = mu_old * mu_old
    mu_new_sq = mu_new * mu_new
    mu_old_new = mu_old * mu_new
    sigma_old = cv2.GaussianBlur(old * old, kernel, sigma) - mu_old_sq
    sigma_new = cv2.GaussianBlur(new * new, kernel, sigma) - mu_new_sq
    sigma_old_new = cv2.GaussianBlur(old * new, kernel, sigma) - mu_old_new
    numerator = (2.0 * mu_old_new + c1) * (2.0 * sigma_old_new + c2)
    denominator = (mu_old_sq + mu_new_sq + c1) * (sigma_old + sigma_new + c2)
    return numerator / np.maximum(denominator, 1e-12)


def ssim_diff_bbox(old_path: Path, new_path: Path, max_dim: int = 1024) -> tuple[list[int], tuple[int, int]]:
    old_image, old_original_size = resized_gray(old_path, max_dim)
    new_image, _ = resized_gray(new_path, max_dim)
    if new_image.size != old_image.size:
        new_image = new_image.resize(old_image.size, Image.Resampling.BILINEAR)

    similarity = ssim_map(
        np.asarray(old_image, dtype=np.uint8),
        np.asarray(new_image, dtype=np.uint8),
    )
    dissimilar = similarity < 0.55
    if not dissimilar.any():
        threshold = np.percentile(similarity, 0.5)
        dissimilar = similarity <= threshold
    if not dissimilar.any():
        width, height = old_image.size
        fallback = [width // 4, height // 4, max(width // 4 + 1, width * 3 // 4), max(height // 4 + 1, height * 3 // 4)]
        return scale_bbox(fallback, old_image.size, old_original_size), old_original_size

    ys, xs = np.where(dissimilar)
    pad = 2
    bbox = [
        max(0, int(xs.min()) - pad),
        max(0, int(ys.min()) - pad),
        min(old_image.size[0], int(xs.max()) + 1 + pad),
        min(old_image.size[1], int(ys.max()) + 1 + pad),
    ]
    return scale_bbox(bbox, old_image.size, old_original_size), old_original_size


def phase_diff_bbox(old_path: Path, new_path: Path, max_dim: int = 1024) -> tuple[list[int], tuple[int, int]]:
    """Absolute difference after phase-correlation translation compensation."""
    old_image, old_original_size = resized_gray(old_path, max_dim)
    new_image, _ = resized_gray(new_path, max_dim)
    if new_image.size != old_image.size:
        new_image = new_image.resize(old_image.size, Image.Resampling.BILINEAR)

    old_arr = np.asarray(old_image, dtype=np.float32)
    new_arr = np.asarray(new_image, dtype=np.float32)
    (shift_x, shift_y), _response = cv2.phaseCorrelate(old_arr, new_arr)
    translation = np.array([[1.0, 0.0, -shift_x], [0.0, 1.0, -shift_y]], dtype=np.float32)
    aligned_new = cv2.warpAffine(
        new_arr,
        translation,
        (old_arr.shape[1], old_arr.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    arr = cv2.absdiff(old_arr, aligned_new).astype(np.uint8)

    margin = int(max(2, abs(shift_x) + 2, abs(shift_y) + 2))
    arr[:margin, :] = 0
    arr[-margin:, :] = 0
    arr[:, :margin] = 0
    arr[:, -margin:] = 0

    nonzero = arr[arr > 0]
    threshold = max(20, int(np.percentile(nonzero, 95))) if nonzero.size else 20
    mask = arr >= threshold
    if not mask.any():
        mask = arr > 0
    if not mask.any():
        width, height = old_image.size
        fallback = [width // 4, height // 4, max(width // 4 + 1, width * 3 // 4), max(height // 4 + 1, height * 3 // 4)]
        return scale_bbox(fallback, old_image.size, old_original_size), old_original_size

    ys, xs = np.where(mask)
    pad = 2
    bbox = [
        max(0, int(xs.min()) - pad),
        max(0, int(ys.min()) - pad),
        min(old_image.size[0], int(xs.max()) + 1 + pad),
        min(old_image.size[1], int(ys.max()) + 1 + pad),
    ]
    return scale_bbox(bbox, old_image.size, old_original_size), old_original_size


def fallback_bbox(size: tuple[int, int]) -> list[int]:
    width, height = size
    return [width // 4, height // 4, max(width // 4 + 1, width * 3 // 4), max(height // 4 + 1, height * 3 // 4)]


def orb_residual_bbox(old_path: Path, new_path: Path, max_dim: int = 1024) -> tuple[list[int], tuple[int, int]]:
    """Bounding box over the densest cluster of new-image keypoints with no old match."""
    old_image, old_original_size = resized_gray(old_path, max_dim)
    new_image, _ = resized_gray(new_path, max_dim)
    if new_image.size != old_image.size:
        new_image = new_image.resize(old_image.size, Image.Resampling.BILINEAR)

    orb = cv2.ORB_create(nfeatures=3000)
    old_arr = np.asarray(old_image, dtype=np.uint8)
    new_arr = np.asarray(new_image, dtype=np.uint8)
    old_kp, old_desc = orb.detectAndCompute(old_arr, None)
    new_kp, new_desc = orb.detectAndCompute(new_arr, None)
    if old_desc is None or new_desc is None or not len(new_kp):
        return scale_bbox(fallback_bbox(old_image.size), old_image.size, old_original_size), old_original_size

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = matcher.match(new_desc, old_desc)
    matched_new = {match.queryIdx for match in matches if match.distance <= 40}
    unmatched = [new_kp[index].pt for index in range(len(new_kp)) if index not in matched_new]
    if not unmatched:
        return scale_bbox(fallback_bbox(old_image.size), old_image.size, old_original_size), old_original_size

    points = np.asarray(unmatched, dtype=np.float32)
    # Densest cluster: the point with the most neighbors inside a fixed radius.
    radius = max(24.0, max(old_image.size) * 0.03)
    distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    neighbor_counts = (distances <= radius).sum(axis=1)
    center = points[int(neighbor_counts.argmax())]
    cluster = points[np.linalg.norm(points - center, axis=1) <= radius]
    pad = 6
    bbox = [
        max(0, int(cluster[:, 0].min()) - pad),
        max(0, int(cluster[:, 1].min()) - pad),
        min(old_image.size[0], int(cluster[:, 0].max()) + 1 + pad),
        min(old_image.size[1], int(cluster[:, 1].max()) + 1 + pad),
    ]
    return scale_bbox(bbox, old_image.size, old_original_size), old_original_size


def largest_cc_bbox(old_path: Path, new_path: Path, max_dim: int = 1024) -> tuple[list[int], tuple[int, int]]:
    """Bounding box of the largest connected component of thresholded difference."""
    old_image, old_original_size = resized_gray(old_path, max_dim)
    new_image, _ = resized_gray(new_path, max_dim)
    if new_image.size != old_image.size:
        new_image = new_image.resize(old_image.size, Image.Resampling.BILINEAR)

    arr = cv2.absdiff(
        np.asarray(old_image, dtype=np.uint8),
        np.asarray(new_image, dtype=np.uint8),
    )
    nonzero = arr[arr > 0]
    threshold = max(20, int(np.percentile(nonzero, 95))) if nonzero.size else 20
    mask = (arr >= threshold).astype(np.uint8)
    if not mask.any():
        return scale_bbox(fallback_bbox(old_image.size), old_image.size, old_original_size), old_original_size
    mask = cv2.dilate(mask, np.ones((5, 5), dtype=np.uint8), iterations=1)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return scale_bbox(fallback_bbox(old_image.size), old_image.size, old_original_size), old_original_size
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(stats[largest, key]) for key in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
    pad = 2
    bbox = [
        max(0, x - pad),
        max(0, y - pad),
        min(old_image.size[0], x + w + pad),
        min(old_image.size[1], y + h + pad),
    ]
    return scale_bbox(bbox, old_image.size, old_original_size), old_original_size


def tile_zncc_bbox(
    old_path: Path,
    new_path: Path,
    max_dim: int = 1024,
    grid: int = 24,
) -> tuple[list[int], tuple[int, int]]:
    """Bounding box of the tile with the lowest zero-normalized cross-correlation."""
    old_image, old_original_size = resized_gray(old_path, max_dim)
    new_image, _ = resized_gray(new_path, max_dim)
    if new_image.size != old_image.size:
        new_image = new_image.resize(old_image.size, Image.Resampling.BILINEAR)

    old_arr = np.asarray(old_image, dtype=np.float64)
    new_arr = np.asarray(new_image, dtype=np.float64)
    height, width = old_arr.shape
    tile_w = max(8, width // grid)
    tile_h = max(8, height // grid)
    worst_score = 2.0
    worst_bbox: list[int] | None = None
    for y in range(0, height - tile_h + 1, tile_h):
        for x in range(0, width - tile_w + 1, tile_w):
            old_tile = old_arr[y : y + tile_h, x : x + tile_w]
            new_tile = new_arr[y : y + tile_h, x : x + tile_w]
            old_dev = old_tile - old_tile.mean()
            new_dev = new_tile - new_tile.mean()
            denominator = np.sqrt((old_dev * old_dev).sum() * (new_dev * new_dev).sum())
            if denominator < 1e-9:
                # Flat tiles: identical flat content correlates perfectly by convention.
                score = 1.0 if abs(old_tile.mean() - new_tile.mean()) < 2.0 else -1.0
            else:
                score = float((old_dev * new_dev).sum() / denominator)
            if score < worst_score:
                worst_score = score
                worst_bbox = [x, y, x + tile_w, y + tile_h]
    if worst_bbox is None:
        worst_bbox = fallback_bbox(old_image.size)
    return scale_bbox(worst_bbox, old_image.size, old_original_size), old_original_size


def predict_row(
    root: Path,
    row: dict[str, Any],
    max_dim: int = 1024,
    mode: str = "absolute",
) -> tuple[dict[str, Any] | None, str | None]:
    if row.get("task") != "visualdiff":
        return None, None
    identifier = row_id(row)
    images = row.get("images") or []
    if identifier is None or not isinstance(images, list) or len(images) < 2:
        return None, "missing_schema"
    old_path = root / str(images[0])
    new_path = root / str(images[1])
    if not old_path.exists() or not new_path.exists():
        return None, "missing_image"
    if mode == "edge":
        bbox, _ = edge_diff_bbox(old_path, new_path, max_dim=max_dim)
        answer = EDGE_ANSWER
        metadata = {"model": "edge_diff", "method": "canny_edge_map_difference"}
    elif mode == "ssim":
        bbox, _ = ssim_diff_bbox(old_path, new_path, max_dim=max_dim)
        answer = SSIM_ANSWER
        metadata = {"model": "ssim_diff", "method": "gaussian_window_ssim_minimum"}
    elif mode == "phase":
        bbox, _ = phase_diff_bbox(old_path, new_path, max_dim=max_dim)
        answer = PHASE_ANSWER
        metadata = {"model": "phase_diff", "method": "phase_correlation_compensated_difference"}
    elif mode == "orb_residual":
        bbox, _ = orb_residual_bbox(old_path, new_path, max_dim=max_dim)
        answer = ORB_ANSWER
        metadata = {"model": "orb_residual_diff", "method": "unmatched_orb_keypoint_density"}
    elif mode == "largest_cc":
        bbox, _ = largest_cc_bbox(old_path, new_path, max_dim=max_dim)
        answer = LARGEST_CC_ANSWER
        metadata = {"model": "largest_cc_diff", "method": "largest_connected_difference_component"}
    elif mode == "tile_zncc":
        bbox, _ = tile_zncc_bbox(old_path, new_path, max_dim=max_dim)
        answer = TILE_ZNCC_ANSWER
        metadata = {"model": "tile_zncc_diff", "method": "lowest_tile_normalized_cross_correlation"}
    else:
        bbox, _ = diff_bbox(old_path, new_path, max_dim=max_dim)
        answer = DEFAULT_ANSWER
        metadata = {"model": "simple_diff", "method": "absolute_image_difference"}
    return (
        {
            "id": identifier,
            "answer": answer,
            "evidence": [
                {"image_index": 0, "bbox": bbox},
                {"image_index": 1, "bbox": bbox},
            ],
            "metadata": metadata,
        },
        None,
    )


def predict_rows(
    root: str | Path,
    rows: list[dict[str, Any]],
    max_dim: int = 1024,
    mode: str = "absolute",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    root = Path(root)
    predictions: list[dict[str, Any]] = []
    stats = {
        "rows": len(rows),
        "visualdiff_rows": 0,
        "predictions": 0,
        "missing_schema": 0,
        "missing_image": 0,
    }
    for row in rows:
        if row.get("task") != "visualdiff":
            continue
        stats["visualdiff_rows"] += 1
        prediction, error = predict_row(root, row, max_dim=max_dim, mode=mode)
        if prediction is None:
            if error:
                stats[error] += 1
            continue
        predictions.append(prediction)
    stats["predictions"] = len(predictions)
    return predictions, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate simple-diff visualdiff predictions.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl", help="Unified Eng_Bench JSONL")
    parser.add_argument("--output", required=True, help="Output unified prediction JSONL")
    parser.add_argument("--split", default="all", help="Optional split filter")
    parser.add_argument("--max-dim", type=int, default=1024, help="Max image dimension for diffing")
    parser.add_argument(
        "--mode",
        choices=("absolute", "edge", "ssim", "phase", "orb_residual", "largest_cc", "tile_zncc"),
        default="absolute",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = load_jsonl(root / args.input)
    if args.split != "all":
        rows = [row for row in rows if row.get("split") == args.split]
    predictions, stats = predict_rows(root, rows, max_dim=args.max_dim, mode=args.mode)
    write_jsonl(root / args.output, predictions)
    print(f"[OK] Visualdiff rows: {stats['visualdiff_rows']}")
    print(f"[OK] Predictions: {stats['predictions']}")
    if stats["missing_schema"] or stats["missing_image"]:
        print(f"[WARN] Missing schema rows: {stats['missing_schema']}")
        print(f"[WARN] Missing image rows: {stats['missing_image']}")
    print(f"[OK] Wrote {root / args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
