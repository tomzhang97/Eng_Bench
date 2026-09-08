#!/usr/bin/env python3
"""Normalize microtext regions across byte-identical source document aliases."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image


Region = tuple[str, int, tuple[int, int, int, int]]


def load_payload_alias_map(path: Path) -> tuple[dict[str, str], int]:
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    groups = report.get("duplicate_groups") or report.get("groups") or []
    aliases: dict[str, str] = {}
    group_count = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        canonical = str(group.get("canonical_doc_id") or "").strip()
        doc_ids = group.get("doc_ids") or []
        if not canonical or not isinstance(doc_ids, list):
            continue
        normalized_ids = [str(value).strip() for value in doc_ids if str(value).strip()]
        if len(normalized_ids) < 2:
            continue
        group_count += 1
        for doc_id in normalized_ids:
            aliases[doc_id] = canonical
    return aliases, group_count


@lru_cache(maxsize=None)
def path_image_dimensions(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    with Image.open(path) as image:
        return image.size


def image_dimensions(
    row: dict[str, Any],
    *,
    root: Path,
    doc_id: str,
    page_index: int,
) -> tuple[int, int] | None:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    width = row.get("image_width") or metadata.get("image_width")
    height = row.get("image_height") or metadata.get("image_height")
    try:
        if int(width) > 0 and int(height) > 0:
            return int(width), int(height)
    except (TypeError, ValueError):
        pass

    image_path = str(row.get("image_path") or metadata.get("image_path") or "").strip()
    candidates: list[Path] = []
    if image_path:
        value = Path(image_path)
        candidates.append(value if value.is_absolute() else root / value)
    candidates.append(root / "derived" / "pages_300dpi" / doc_id / f"page_{page_index:03d}.png")
    for path in candidates:
        dimensions = path_image_dimensions(path.resolve())
        if dimensions:
            return dimensions
    return None


def payload_alias_region_key(
    row: dict[str, Any],
    region: Region | None,
    *,
    root: Path,
    alias_map: dict[str, str],
    scale: int = 1_000_000,
) -> Region | None:
    if not region:
        return None
    doc_id, page_index, bbox = region
    canonical_doc_id = alias_map.get(doc_id)
    if not canonical_doc_id:
        return None
    dimensions = image_dimensions(
        row,
        root=root,
        doc_id=doc_id,
        page_index=page_index,
    )
    if not dimensions:
        return None
    width, height = dimensions
    x0, y0, x1, y1 = bbox
    normalized = (
        round(x0 * scale / width),
        round(y0 * scale / height),
        round(x1 * scale / width),
        round(y1 * scale / height),
    )
    return canonical_doc_id, page_index, normalized
