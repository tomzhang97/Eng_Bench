#!/usr/bin/env python3
"""Mine split-locked, release-safe VisualDiff candidates for provenance gaps."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageStat

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from source_rights import is_release_safe_status


CANDIDATE_FILE_RE = re.compile(r"^candidates_(?:page_|p)?(\d+)\.json$")
DEFAULT_CATEGORY_QUOTAS = {
    "symbol": 508,
    "text": 100,
    "addition+text": 60,
    "deletion+text": 20,
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_inventory(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            str(row.get("doc_id") or "").strip(): str(row.get("public_status") or "").strip()
            for row in csv.DictReader(handle)
            if str(row.get("doc_id") or "").strip()
        }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def bbox_tuple(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        raise ValueError("bbox must contain four coordinates")
    x1, y1, x2, y2 = (int(round(float(item))) for item in value[:4])
    if x2 <= x1 or y2 <= y1:
        raise ValueError("bbox has no positive area")
    return x1, y1, x2, y2


def load_homography(path: Path) -> np.ndarray:
    payload = read_json(path)
    raw = payload.get("H")
    matrix = np.asarray(raw, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("homography must be a finite 3x3 matrix")
    if abs(float(np.linalg.det(matrix))) < 1e-12:
        raise ValueError("homography is singular")
    return matrix


def transform_bbox_to_old(
    bbox_new: tuple[int, int, int, int],
    homography_old_to_new: np.ndarray,
    old_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox_new
    corners = np.asarray(
        [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]],
        dtype=np.float64,
    )
    inverse = np.linalg.inv(homography_old_to_new)
    transformed = cv2.perspectiveTransform(corners, inverse)[0]
    width, height = old_size
    old_x1 = max(0, int(np.floor(transformed[:, 0].min())))
    old_y1 = max(0, int(np.floor(transformed[:, 1].min())))
    old_x2 = min(width, int(np.ceil(transformed[:, 0].max())))
    old_y2 = min(height, int(np.ceil(transformed[:, 1].max())))
    if old_x2 <= old_x1 or old_y2 <= old_y1:
        raise ValueError("homography maps bbox outside the old page")
    return old_x1, old_y1, old_x2, old_y2


def aligned_crops(
    old_image: Image.Image,
    new_image: Image.Image,
    homography_old_to_new: np.ndarray,
    bbox_new: tuple[int, int, int, int],
    pad_px: int,
) -> tuple[Image.Image, Image.Image]:
    x1, y1, x2, y2 = bbox_new
    target_x1 = max(0, x1 - pad_px)
    target_y1 = max(0, y1 - pad_px)
    target_x2 = min(new_image.width, x2 + pad_px)
    target_y2 = min(new_image.height, y2 + pad_px)
    if target_x2 <= target_x1 or target_y2 <= target_y1:
        raise ValueError("bbox is outside the new page")

    target_bbox = (target_x1, target_y1, target_x2, target_y2)
    source_bbox = transform_bbox_to_old(
        target_bbox,
        homography_old_to_new,
        (old_image.width, old_image.height),
    )
    source_x1, source_y1, _, _ = source_bbox
    old_source = np.asarray(old_image.crop(source_bbox))

    source_to_global = np.asarray(
        [[1.0, 0.0, source_x1], [0.0, 1.0, source_y1], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    global_to_target = np.asarray(
        [[1.0, 0.0, -target_x1], [0.0, 1.0, -target_y1], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    local_homography = global_to_target @ homography_old_to_new @ source_to_global
    target_size = (target_x2 - target_x1, target_y2 - target_y1)
    warped_old = cv2.warpPerspective(
        old_source,
        local_homography,
        target_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    old_crop = Image.fromarray(warped_old, mode="RGB")
    new_crop = new_image.crop(target_bbox)
    return old_crop, new_crop


def resolve_page_path(root: Path, doc_id: str, page: int) -> Path | None:
    directory = root / "derived" / "pages_300dpi" / doc_id
    for name in (
        f"p{page:04d}.png",
        f"page_{page:03d}.png",
        f"page_{page:04d}.png",
        f"{page}.png",
    ):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=24)
def load_rgb(path: str) -> Image.Image:
    previous_max = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 1_000_000_000
    try:
        with Image.open(path) as source:
            return source.convert("RGB")
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max


@lru_cache(maxsize=128)
def load_textlayer(path: str) -> dict[int, list[dict[str, Any]]]:
    pages: dict[int, list[dict[str, Any]]] = defaultdict(list)
    source = Path(path)
    if not source.is_file():
        return pages
    for row in read_jsonl(source):
        try:
            page = int(row.get("page", row.get("page_index", 0)))
        except (TypeError, ValueError):
            continue
        pages[page].append(row)
    return pages


def text_in_bbox(entries: list[dict[str, Any]], bbox: tuple[int, int, int, int]) -> str:
    x1, y1, x2, y2 = bbox
    hits: list[tuple[float, float, str]] = []
    for row in entries:
        raw_bbox = row.get("bbox_px") or row.get("bbox")
        try:
            bx1, by1, bx2, by2 = bbox_tuple(raw_bbox)
        except (TypeError, ValueError):
            continue
        center_x = (bx1 + bx2) / 2.0
        center_y = (by1 + by2) / 2.0
        if x1 <= center_x <= x2 and y1 <= center_y <= y2:
            text = " ".join(str(row.get("text") or "").split())
            if text:
                hits.append((center_y, center_x, text))
    hits.sort()
    return " ".join(hit[2] for hit in hits)


def infer_change_type(old_text: str, new_text: str) -> str:
    old_text = " ".join(old_text.split())
    new_text = " ".join(new_text.split())
    if old_text and new_text and old_text != new_text:
        return "text"
    if not old_text and new_text:
        return "addition+text"
    if old_text and not new_text:
        return "deletion+text"
    return "symbol"


def crop_with_padding(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    pad_px: int,
) -> Image.Image:
    x1, y1, x2, y2 = bbox
    left = max(0, x1 - pad_px)
    top = max(0, y1 - pad_px)
    right = min(image.width, x2 + pad_px)
    bottom = min(image.height, y2 + pad_px)
    if right <= left or bottom <= top:
        raise ValueError("bbox is outside image")
    return image.crop((left, top, right, bottom))


def crop_digest(crop: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(f"{crop.width}x{crop.height}:".encode("ascii"))
    digest.update(crop.tobytes())
    return digest.hexdigest()


def evidence_fingerprint(old_crop: Image.Image, new_crop: Image.Image) -> str:
    old_digest = crop_digest(old_crop)
    new_digest = crop_digest(new_crop)
    digest = hashlib.sha256(f"{old_digest}:{new_digest}".encode("ascii")).hexdigest()
    return f"visualdiff:sha256:{digest}"


def difference_metrics(old_crop: Image.Image, new_crop: Image.Image) -> dict[str, float | bool]:
    width = max(old_crop.width, new_crop.width)
    height = max(old_crop.height, new_crop.height)
    old_canvas = Image.new("RGB", (width, height), "white")
    new_canvas = Image.new("RGB", (width, height), "white")
    old_canvas.paste(old_crop, (0, 0))
    new_canvas.paste(new_crop, (0, 0))
    difference = ImageChops.difference(old_canvas, new_canvas)
    mean_absolute_delta = sum(ImageStat.Stat(difference).mean) / 3.0
    channels = difference.split()
    mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    histogram = mask.histogram()
    total = max(1, width * height)
    changed_ratio = sum(histogram[13:]) / total
    normalized_old = old_crop.resize((256, 256), Image.Resampling.LANCZOS)
    normalized_new = new_crop.resize((256, 256), Image.Resampling.LANCZOS)
    normalized_difference = ImageChops.difference(normalized_old, normalized_new)
    normalized_mean_delta = sum(ImageStat.Stat(normalized_difference).mean) / 3.0
    normalized_channels = normalized_difference.split()
    normalized_mask = ImageChops.lighter(
        ImageChops.lighter(normalized_channels[0], normalized_channels[1]),
        normalized_channels[2],
    )
    normalized_histogram = normalized_mask.histogram()
    normalized_changed_ratio = sum(normalized_histogram[17:]) / 65536
    return {
        "pixel_exact_match": difference.getbbox() is None,
        "mean_absolute_delta": round(mean_absolute_delta, 6),
        "changed_pixel_ratio_gt12": round(changed_ratio, 8),
        "normalized_mean_absolute_delta": round(normalized_mean_delta, 6),
        "normalized_changed_pixel_ratio_gt16": round(normalized_changed_ratio, 8),
    }


def row_images_and_bboxes(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    old_image = row.get("image_old")
    new_image = row.get("image_new")
    old_bbox = row.get("bbox_old")
    new_bbox = row.get("bbox_new")
    images = row.get("images")
    evidence = row.get("evidence")
    if isinstance(images, list) and len(images) >= 2:
        old_image = old_image or images[0]
        new_image = new_image or images[1]
    if isinstance(evidence, list) and len(evidence) >= 2:
        old_bbox = old_bbox or evidence[0].get("bbox")
        new_bbox = new_bbox or evidence[1].get("bbox")
    return old_image, new_image, old_bbox, new_bbox


def resolve_pair_unit(row: dict[str, Any], pair_ids: set[str]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    values = [
        row.get("project_id"),
        row.get("pair_id"),
        metadata.get("pair_id"),
        row.get("id"),
    ]
    for value in values:
        raw = str(value or "")
        if raw.startswith("q_"):
            raw = raw[2:]
        if raw in pair_ids:
            return raw
        matches = [pair_id for pair_id in pair_ids if raw.startswith(pair_id + "__")]
        if matches:
            return max(matches, key=len)
    return ""


def excluded_evidence(
    root: Path,
    paths: list[Path],
    pair_ids: set[str],
    pad_px: int,
) -> tuple[set[str], set[str], set[tuple[str, int, tuple[int, int, int, int]]], Counter[str]]:
    identities: set[str] = set()
    fingerprints: set[str] = set()
    bbox_keys: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    status: Counter[str] = Counter()
    for path in paths:
        for row in read_jsonl(path):
            identity = str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()
            if identity:
                identities.add(identity)
            recorded = str(row.get("replacement_evidence_fingerprint") or "").strip()
            if recorded:
                fingerprints.add(recorded)
                status["recorded_fingerprint"] += 1
            unit = resolve_pair_unit(row, pair_ids)
            old_image, new_image, old_bbox, new_bbox = row_images_and_bboxes(row)
            try:
                page = int(row.get("page_old", row.get("page", row.get("page_index", 0))))
                bbox_keys.add((unit, page, bbox_tuple(old_bbox)))
            except (TypeError, ValueError):
                pass
            if recorded or not old_image or not new_image or old_bbox is None or new_bbox is None:
                continue
            try:
                old_path = Path(str(old_image))
                new_path = Path(str(new_image))
                if not old_path.is_absolute():
                    old_path = root / old_path
                if not new_path.is_absolute():
                    new_path = root / new_path
                old_crop = crop_with_padding(load_rgb(str(old_path.resolve())), bbox_tuple(old_bbox), pad_px)
                new_crop = crop_with_padding(load_rgb(str(new_path.resolve())), bbox_tuple(new_bbox), pad_px)
                fingerprints.add(evidence_fingerprint(old_crop, new_crop))
                status["computed_fingerprint"] += 1
            except (OSError, TypeError, ValueError):
                status["fingerprint_unavailable"] += 1
    return identities, fingerprints, bbox_keys, status


def parse_category_quotas(values: list[str], target_rows: int) -> dict[str, int]:
    if not values:
        quotas = dict(DEFAULT_CATEGORY_QUOTAS)
    else:
        quotas = {}
        for value in values:
            name, separator, raw_count = value.partition("=")
            if not separator or not name.strip():
                raise ValueError(f"invalid category quota: {value}")
            quotas[name.strip()] = max(0, int(raw_count))
    total = sum(quotas.values())
    if total > target_rows:
        scale = target_rows / total
        quotas = {name: int(count * scale) for name, count in quotas.items()}
    return quotas


def select_diverse_rows(
    rows: list[dict[str, Any]],
    target_rows: int,
    max_per_pair: int,
    category_quotas: dict[str, int],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, deque[dict[str, Any]]]] = defaultdict(dict)
    for category in sorted({str(row["change_type"]) for row in rows}):
        by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["change_type"] == category:
                by_pair[str(row["project_id"])].append(row)
        for pair_id, pair_rows in by_pair.items():
            pair_rows.sort(
                key=lambda item: (
                    -float(item["gap_mining_metrics"]["changed_pixel_ratio_gt12"]),
                    -float(item["gap_mining_metrics"]["mean_absolute_delta"]),
                    float(item["gap_mining_metrics"]["bbox_area_ratio"]),
                    str(item["pair_id"]),
                )
            )
            grouped[category][pair_id] = deque(pair_rows)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    pair_counts: Counter[str] = Counter()

    def take(category: str, limit: int) -> int:
        queues = grouped.get(category, {})
        added = 0
        while added < limit and len(selected) < target_rows:
            progressed = False
            for pair_id in sorted(queues, key=lambda value: (pair_counts[value], value)):
                queue = queues[pair_id]
                if pair_counts[pair_id] >= max_per_pair or not queue:
                    continue
                row = queue.popleft()
                if row["pair_id"] in selected_ids:
                    continue
                selected.append(row)
                selected_ids.add(row["pair_id"])
                pair_counts[pair_id] += 1
                added += 1
                progressed = True
                if added >= limit or len(selected) >= target_rows:
                    break
            if not progressed:
                break
        return added

    for category, quota in category_quotas.items():
        take(category, quota)

    while len(selected) < target_rows:
        progressed = False
        categories = sorted(
            grouped,
            key=lambda category: (
                sum(1 for row in selected if row["change_type"] == category),
                category,
            ),
        )
        for category in categories:
            if take(category, 1):
                progressed = True
                if len(selected) >= target_rows:
                    break
        if not progressed:
            break
    return selected


def build_reservoir(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(args.root).resolve()
    manifest = read_jsonl(root / args.manifest)
    pairs = {
        str(row.get("pair_id")): row
        for row in manifest
        if row.get("type") == "pair" and row.get("pair_id")
    }
    inventory = read_inventory(root / args.inventory)
    split_plan = read_json(root / args.split_plan)
    allowed_splits = {value.strip() for value in args.allowed_splits.split(",") if value.strip()}
    reservations = [
        row
        for row in split_plan.get("reservations", [])
        if row.get("task") == "visualdiff" and row.get("split") in allowed_splits
    ]
    exclusion_paths = [root / value for value in args.exclude_jsonl]
    excluded_ids, excluded_fingerprints, excluded_bbox_keys, exclusion_status = excluded_evidence(
        root,
        exclusion_paths,
        set(pairs),
        args.pad_px,
    )

    rejections: Counter[str] = Counter()
    generated: list[dict[str, Any]] = []
    seen_fingerprints = set(excluded_fingerprints)
    seen_bbox_keys = set(excluded_bbox_keys)
    source_pair_counts: Counter[str] = Counter()

    for reservation in reservations:
        pair_id = str(reservation.get("unit_id") or "")
        pair = pairs.get(pair_id)
        if not pair:
            rejections["missing_manifest_pair"] += 1
            continue
        old_doc = str(pair.get("from_doc_id") or "")
        new_doc = str(pair.get("to_doc_id") or "")
        statuses = [inventory.get(old_doc, ""), inventory.get(new_doc, "")]
        if not all(is_release_safe_status(status) for status in statuses):
            rejections["non_release_safe_pair"] += 1
            continue
        align_dir = root / "derived" / "align" / pair_id
        if not align_dir.is_dir():
            rejections["missing_align_directory"] += 1
            continue
        old_textlayer = load_textlayer(str((root / "derived" / "textlayer" / f"{old_doc}.jsonl").resolve()))
        new_textlayer = load_textlayer(str((root / "derived" / "textlayer" / f"{new_doc}.jsonl").resolve()))

        for candidate_file in sorted(align_dir.glob("candidates_*.json")):
            match = CANDIDATE_FILE_RE.match(candidate_file.name)
            if not match:
                continue
            page = int(match.group(1))
            old_page = resolve_page_path(root, old_doc, page)
            new_page = resolve_page_path(root, new_doc, page)
            if old_page is None or new_page is None:
                rejections["missing_page_image"] += 1
                continue
            old_image = load_rgb(str(old_page.resolve()))
            new_image = load_rgb(str(new_page.resolve()))
            width = new_image.width
            height = new_image.height
            page_area = max(1, width * height)
            homography_path = align_dir / f"H_page_{page:03d}.json"
            try:
                homography = load_homography(homography_path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                rejections["missing_or_invalid_homography"] += 1
                continue
            payload = read_json(candidate_file)
            raw_candidates = payload.get("boxes") or payload.get("candidates") or []
            for raw in raw_candidates:
                try:
                    bbox = bbox_tuple(raw.get("bbox"))
                except (AttributeError, TypeError, ValueError):
                    rejections["invalid_bbox"] += 1
                    continue
                x1, y1, x2, y2 = bbox
                area = (x2 - x1) * (y2 - y1)
                area_ratio = area / page_area
                if area < args.min_area:
                    rejections["below_min_area"] += 1
                    continue
                if area_ratio > args.max_area_ratio:
                    rejections["above_max_area_ratio"] += 1
                    continue
                if (
                    x1 <= args.border_margin
                    or y1 <= args.border_margin
                    or x2 >= width - args.border_margin
                    or y2 >= height - args.border_margin
                ):
                    rejections["border_candidate"] += 1
                    continue
                bbox_key = (pair_id, page, bbox)
                if bbox_key in seen_bbox_keys:
                    rejections["existing_or_duplicate_bbox"] += 1
                    continue
                try:
                    bbox_old = transform_bbox_to_old(
                        bbox,
                        homography,
                        (old_image.width, old_image.height),
                    )
                    old_crop, new_crop = aligned_crops(
                        old_image,
                        new_image,
                        homography,
                        bbox,
                        args.pad_px,
                    )
                except ValueError:
                    rejections["invalid_crop"] += 1
                    continue
                metrics = difference_metrics(old_crop, new_crop)
                if metrics["pixel_exact_match"]:
                    rejections["pixel_identical"] += 1
                    continue
                if (
                    float(metrics["mean_absolute_delta"]) < args.min_mean_absolute_delta
                    and float(metrics["changed_pixel_ratio_gt12"]) < args.min_changed_pixel_ratio
                ):
                    rejections["near_identical"] += 1
                    continue
                if (
                    float(metrics["normalized_mean_absolute_delta"])
                    < args.min_normalized_mean_absolute_delta
                ):
                    rejections["near_identical_normalized"] += 1
                    continue
                fingerprint = evidence_fingerprint(old_crop, new_crop)
                if fingerprint in seen_fingerprints:
                    rejections["duplicate_evidence_fingerprint"] += 1
                    continue
                old_text = text_in_bbox(old_textlayer.get(page, []), bbox_old)
                new_text = text_in_bbox(new_textlayer.get(page, []), bbox)
                change_type = infer_change_type(old_text, new_text)
                short_hash = hashlib.sha256(
                    f"{pair_id}:{page}:{','.join(map(str, bbox))}:{fingerprint}".encode("utf-8")
                ).hexdigest()[:16]
                candidate_id = f"{pair_id}__gap_{short_hash}"
                if candidate_id in excluded_ids:
                    rejections["excluded_identity"] += 1
                    continue
                description = {
                    "symbol": "A localized graphic or schematic-symbol difference may be present.",
                    "text": f"Localized text may have changed from '{old_text}' to '{new_text}'.",
                    "addition+text": f"Localized text may have been added: '{new_text}'.",
                    "deletion+text": f"Localized text may have been removed: '{old_text}'.",
                }[change_type]
                row = {
                    "pair_id": candidate_id,
                    "project_id": pair_id,
                    "page_old": page,
                    "page_new": page,
                    "image_old": relative_path(root, old_page),
                    "image_new": relative_path(root, new_page),
                    "bbox_old": list(bbox_old),
                    "bbox_new": list(bbox),
                    "old_text": old_text,
                    "new_text": new_text,
                    "change_type": change_type,
                    "description": description,
                    "change_desc_gt": "",
                    "human_description": "",
                    "review_status": "needs_machine_review",
                    "human_review_status": "unassigned",
                    "machine_qa_status": "unverified_provenance_gap_reservoir",
                    "review_bucket": "v2_0_provenance_gap_reservoir_unverified",
                    "safe_to_merge_gold": False,
                    "promotion_state": "unreviewed_visualdiff_provenance_gap_candidate",
                    "source": "align_diffmap_candidate_provenance_gap_mining",
                    "alignment_h_path": relative_path(root, homography_path),
                    "alignment_status": "homography_applied_old_to_new",
                    "source_candidate_id": str(pair.get("source_candidate_id") or ""),
                    "old_doc_id": old_doc,
                    "new_doc_id": new_doc,
                    "source_public_status": ";".join(statuses),
                    "source_rights_check": "release_safe_status",
                    "reserved_split": str(reservation.get("split") or ""),
                    "split": "provisional_review",
                    "split_reservation_id": str(reservation.get("reservation_id") or ""),
                    "split_reservation_basis": str(reservation.get("assignment_basis") or ""),
                    "split_reservation_plan": relative_path(root, root / args.split_plan),
                    "provenance_replacement_candidate": True,
                    "replacement_for_task": "visualdiff",
                    "replacement_for_split": str(reservation.get("split") or ""),
                    "replacement_for_category": change_type,
                    "replacement_evidence_fingerprint": fingerprint,
                    "replacement_evidence_fingerprint_status": "aligned_pixel_crop_sha256",
                    "gap_mining_date_label": args.date_label,
                    "gap_mining_metrics": {
                        **metrics,
                        "bbox_area": area,
                        "bbox_area_ratio": round(area_ratio, 8),
                    },
                }
                generated.append(row)
                source_pair_counts[pair_id] += 1
                seen_fingerprints.add(fingerprint)
                seen_bbox_keys.add(bbox_key)

    category_quotas = parse_category_quotas(args.category_quota, args.target_rows)
    selected = select_diverse_rows(
        generated,
        target_rows=args.target_rows,
        max_per_pair=args.max_per_pair,
        category_quotas=category_quotas,
    )
    selected_fingerprints = {
        str(row.get("replacement_evidence_fingerprint") or "") for row in selected
    }
    report = {
        "valid": len(selected) >= args.min_output_rows,
        "date_label": args.date_label,
        "target_rows": args.target_rows,
        "minimum_output_rows": args.min_output_rows,
        "selected_rows": len(selected),
        "generated_passing_rows_before_selection": len(generated),
        "selected_unique_evidence_fingerprints": len(selected_fingerprints),
        "selected_rows_by_split": dict(sorted(Counter(row["reserved_split"] for row in selected).items())),
        "selected_rows_by_category": dict(sorted(Counter(row["change_type"] for row in selected).items())),
        "selected_rows_by_pair": dict(sorted(Counter(row["project_id"] for row in selected).items())),
        "eligible_rows_by_pair_before_selection": dict(sorted(source_pair_counts.items())),
        "reservation_count": len(reservations),
        "selected_pair_count": len({row["project_id"] for row in selected}),
        "category_quotas": category_quotas,
        "max_per_pair": args.max_per_pair,
        "rejections": dict(sorted(rejections.items())),
        "exclusion_files": [relative_path(root, path) for path in exclusion_paths],
        "exclusion_fingerprint_status": dict(sorted(exclusion_status.items())),
        "safe_to_merge_gold_rows": sum(bool(row.get("safe_to_merge_gold")) for row in selected),
        "machine_qa_status": dict(sorted(Counter(row["machine_qa_status"] for row in selected).items())),
        "interpretation": (
            "This is a machine-review reservoir, not human-reviewed or Gold data. "
            "Old-page evidence is mapped and compared through each pair's recorded homography. "
            "Rows preserve existing split-family locks and require visual QA, human review, "
            "deduplication, leakage checks, and strict promotion before use."
        ),
    }
    return selected, report


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# VisualDiff Provenance-Gap Reservoir",
        "",
        f"- Valid: `{report['valid']}`",
        f"- Selected rows: `{report['selected_rows']}`",
        f"- Minimum required: `{report['minimum_output_rows']}`",
        f"- Pixel-distinct evidence: `{report['selected_unique_evidence_fingerprints']}`",
        f"- Source pairs: `{report['selected_pair_count']}`",
        f"- Gold-mergeable rows: `{report['safe_to_merge_gold_rows']}`",
        "- Status: machine reservoir only; visual and human review remain mandatory.",
        "",
        "## Categories",
        "",
        "| Category | Rows |",
        "|---|---:|",
    ]
    lines.extend(f"| {name} | {count} |" for name, count in report["selected_rows_by_category"].items())
    lines.extend(["", "## Rejections", "", "| Reason | Rows |", "|---|---:|"])
    lines.extend(f"| {name} | {count} |" for name, count in report["rejections"].items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--manifest", default="manifest.jsonl")
    parser.add_argument("--inventory", default="SOURCE_INVENTORY.csv")
    parser.add_argument("--split-plan", required=True)
    parser.add_argument("--exclude-jsonl", action="append", default=[])
    parser.add_argument("--allowed-splits", default="test")
    parser.add_argument("--date-label", default="2026-08-11-wave101")
    parser.add_argument("--target-rows", type=int, default=1000)
    parser.add_argument("--min-output-rows", type=int, default=681)
    parser.add_argument("--max-per-pair", type=int, default=60)
    parser.add_argument("--category-quota", action="append", default=[])
    parser.add_argument("--min-area", type=int, default=120)
    parser.add_argument("--max-area-ratio", type=float, default=0.025)
    parser.add_argument("--border-margin", type=int, default=8)
    parser.add_argument("--pad-px", type=int, default=28)
    parser.add_argument("--min-mean-absolute-delta", type=float, default=0.75)
    parser.add_argument("--min-changed-pixel-ratio", type=float, default=0.003)
    parser.add_argument("--min-normalized-mean-absolute-delta", type=float, default=2.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, report = build_reservoir(args)
    root = Path(args.root).resolve()
    write_jsonl(root / args.output, rows)
    write_json(root / args.report_json, report)
    write_markdown(root / args.report_md, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
