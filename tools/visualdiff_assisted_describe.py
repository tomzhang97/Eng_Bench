#!/usr/bin/env python3
"""Create auditable Codex-assisted descriptions for visualdiff TODO rows."""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


TODO = "CHANGE_DESC_GT_TODO"
MAX_TEXT_CHARS = 180


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(text: Any) -> str:
    value = str(text or "").replace("\u00a0", " ")
    value = re.sub(r"\.{4,}", "...", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def truncate(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    text = normalize_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def textlayer_paths(root: Path, pair: dict[str, Any], version_id: str) -> list[Path]:
    doc_id = str(pair.get("doc_id", ""))
    names = [f"{doc_id}__{version_id}.jsonl", f"{doc_id}_{version_id}.jsonl"]
    if doc_id == "viola" and str(version_id).startswith("pcbV"):
        suffix = str(version_id).removeprefix("pcbV")
        names.extend(
            [
                f"toradex_viola_v{suffix}.jsonl",
                f"toradex__viola__datasheet__{version_id}.jsonl",
            ]
        )
    if doc_id == "bbb":
        names.append(f"bbb_schematic_rev{version_id}.jsonl")
    return [root / "derived" / "textlayer" / name for name in names]


def load_textlayer(
    root: Path,
    cache: dict[Path, list[dict[str, Any]]],
    pair: dict[str, Any],
    version_id: str,
) -> list[dict[str, Any]]:
    for path in textlayer_paths(root, pair, version_id):
        if not path.exists():
            continue
        if path not in cache:
            cache[path] = load_jsonl(path)
        return cache[path]
    return []


def nearby_or_overlapping_text(
    root: Path,
    cache: dict[Path, list[dict[str, Any]]],
    pair: dict[str, Any],
    version_id: str,
    page_index: int,
    bbox: list[float],
    margin_px: int = 20,
    limit: int = 12,
) -> str:
    if len(bbox) < 4:
        return ""
    x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
    rx1, ry1, rx2, ry2 = x1 - margin_px, y1 - margin_px, x2 + margin_px, y2 + margin_px
    cx, cy = bbox_center([x1, y1, x2, y2])
    hits: list[tuple[float, str]] = []
    for row in load_textlayer(root, cache, pair, version_id):
        if int(row.get("page", -1)) != int(page_index):
            continue
        text_bbox = row.get("bbox_px") or row.get("bbox")
        text = normalize_text(row.get("text", ""))
        if not text or not text_bbox or len(text_bbox) < 4:
            continue
        tx1, ty1, tx2, ty2 = [float(value) for value in text_bbox[:4]]
        inter = max(0.0, min(rx2, tx2) - max(rx1, tx1)) * max(
            0.0, min(ry2, ty2) - max(ry1, ty1)
        )
        if inter <= 0:
            continue
        tx, ty = bbox_center([tx1, ty1, tx2, ty2])
        hits.append((math.hypot(tx - cx, ty - cy), text))
    hits.sort(key=lambda item: item[0])
    selected: list[str] = []
    for _, text in hits:
        if text not in selected:
            selected.append(text)
        if len(selected) >= limit:
            break
    return truncate(" ".join(selected))


def page_image_path(root: Path, pair: dict[str, Any], version_key: str, page_key: str) -> Path:
    doc_id = str(pair.get("doc_id", ""))
    version_id = str(pair.get(version_key, ""))
    page = int(pair.get(page_key, 0))
    return root / "images" / f"{doc_id}__{version_id}" / f"page_{page:04d}.png"


def crop_for_diff(image: Image.Image, bbox: list[float], size: int = 96) -> Image.Image:
    x1, y1, x2, y2 = [int(round(float(value))) for value in bbox[:4]]
    pad = 8
    crop = image.crop(
        (
            max(0, x1 - pad),
            max(0, y1 - pad),
            min(image.width, x2 + pad),
            min(image.height, y2 + pad),
        )
    )
    return crop.convert("L").resize((size, size))


def mean_pixel_delta(root: Path, pair: dict[str, Any]) -> float | None:
    old_path = page_image_path(root, pair, "version_id_old", "page_index_old")
    new_path = page_image_path(root, pair, "version_id_new", "page_index_new")
    if not old_path.exists() or not new_path.exists():
        return None
    with Image.open(old_path) as old_image, Image.open(new_path) as new_image:
        old_crop = crop_for_diff(old_image, pair.get("bbox_old") or [])
        new_crop = crop_for_diff(new_image, pair.get("bbox_new") or [])
    return float(ImageStat.Stat(ImageChops.difference(old_crop, new_crop)).mean[0])


def text_diff_description(old_text: str, new_text: str) -> str:
    old_text = truncate(old_text)
    new_text = truncate(new_text)
    if old_text and new_text:
        return f"- {old_text} + {new_text}"
    if old_text:
        return f"- {old_text}"
    return f"+ {new_text}"


def visual_description(pair: dict[str, Any], context: str, delta: float | None) -> str:
    change_types = set(pair.get("change_type") or [])
    old_version = str(pair.get("version_id_old", "old"))
    new_version = str(pair.get("version_id_new", "new"))
    prefix = "Highlighted"
    if context:
        suffix = f" near \"{truncate(context, 90)}\""
    else:
        suffix = ""
    if "layout" in change_types:
        core = "layout/content position changed"
    elif "addition" in change_types:
        core = "text or graphic content was added"
    elif "deletion" in change_types:
        core = "text or graphic content was removed"
    elif "symbol" in change_types:
        core = "graphic/symbol appearance changed"
    elif "value" in change_types:
        core = "shown value changed"
    else:
        core = "visual content changed"
    if delta is not None and delta < 3:
        core += " subtly"
    return f"{prefix} {core}{suffix} from {old_version} to {new_version}."


def is_todo(row: dict[str, Any]) -> bool:
    return str(row.get("change_desc_gt") or "").strip() in {"", TODO}


def describe_pair(
    root: Path,
    cache: dict[Path, list[dict[str, Any]]],
    pair: dict[str, Any],
    reviewed_at: str,
) -> dict[str, Any]:
    out = dict(pair)
    old_text = nearby_or_overlapping_text(
        root,
        cache,
        pair,
        str(pair.get("version_id_old", "")),
        int(pair.get("page_index_old", 0)),
        pair.get("bbox_old") or [],
    )
    new_text = nearby_or_overlapping_text(
        root,
        cache,
        pair,
        str(pair.get("version_id_new", "")),
        int(pair.get("page_index_new", 0)),
        pair.get("bbox_new") or [],
    )
    delta = mean_pixel_delta(root, pair)

    if old_text != new_text and (old_text or new_text):
        out["change_desc_gt"] = text_diff_description(old_text, new_text)
        out["desc_source"] = "codex_assisted_textlayer_diff"
        out["review_confidence"] = "high"
        out["review_evidence"] = {
            "old_text": old_text,
            "new_text": new_text,
            "mean_pixel_delta": delta,
        }
    else:
        context = old_text or new_text
        out["change_desc_gt"] = visual_description(pair, context, delta)
        out["desc_source"] = "codex_assisted_visual_review"
        out["review_confidence"] = "medium" if delta is not None and delta >= 3 else "low"
        out["review_evidence"] = {
            "old_text": old_text,
            "new_text": new_text,
            "mean_pixel_delta": delta,
        }
    out["annotation_status"] = "edited"
    out["reviewed_at"] = reviewed_at
    return out


def describe_rows(
    root: Path,
    rows: list[dict[str, Any]],
    splits: set[str],
    reviewed_at: str,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    cache: dict[Path, list[dict[str, Any]]] = {}
    reviewed: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for row in rows:
        if row.get("split") not in splits or not is_todo(row):
            continue
        described = describe_pair(root, cache, row, reviewed_at)
        reviewed.append(described)
        stats["rows"] += 1
        stats[f"split_{described.get('split', 'unknown')}"] += 1
        stats[f"source_{described.get('desc_source', 'unknown')}"] += 1
        stats[f"confidence_{described.get('review_confidence', 'unknown')}"] += 1
        for change_type in described.get("change_type") or ["unknown"]:
            stats[f"change_type_{change_type}"] += 1
    return reviewed, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Codex-assisted visualdiff descriptions")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--input",
        default="visualdiff/annotations/visualdiff_review_queue.jsonl",
        help="Review queue JSONL",
    )
    parser.add_argument(
        "--output",
        default="visualdiff/annotations/visualdiff_pairs_CODEX_REVIEWED.jsonl",
        help="Reviewed output JSONL",
    )
    parser.add_argument(
        "--splits",
        default="dev,test",
        help="Comma-separated splits to describe",
    )
    parser.add_argument("--reviewed-at", default=None, help="Override reviewed_at timestamp")
    args = parser.parse_args()

    root = Path(args.root)
    splits = {part.strip() for part in args.splits.split(",") if part.strip()}
    reviewed_at = args.reviewed_at or datetime.now().isoformat(timespec="seconds")
    rows = load_jsonl(root / args.input)
    reviewed, stats = describe_rows(root, rows, splits, reviewed_at)
    save_jsonl(root / args.output, reviewed)
    print(f"[OK] Wrote {len(reviewed)} Codex-assisted reviewed rows to {args.output}")
    print(json.dumps(dict(sorted(stats.items())), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
