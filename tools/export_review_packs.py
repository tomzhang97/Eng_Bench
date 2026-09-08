#!/usr/bin/env python3
"""Export crop-based reviewer packs for microtext and visualdiff batches."""
from __future__ import annotations

import argparse
import html
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def bbox_xyxy(row: dict[str, Any], key: str = "bbox") -> tuple[int, int, int, int]:
    bbox = row.get(key) or [0, 0, 0, 0]
    x1, y1, x2, y2 = [int(round(float(value))) for value in bbox[:4]]
    return x1, y1, x2, y2


def padded_crop(
    image_path: Path,
    bbox: tuple[int, int, int, int],
    pad_px: int,
    max_image_pixels: int | None = None,
    draw_target_box: bool = False,
) -> Image.Image | None:
    previous_max_image_pixels = Image.MAX_IMAGE_PIXELS
    if max_image_pixels is not None:
        Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        with Image.open(image_path) as image:
            source = image.convert("RGB")
            x1, y1, x2, y2 = bbox
            if x2 < 0 or y2 < 0 or x1 > source.width or y1 > source.height:
                return None
            left = max(0, x1 - pad_px)
            top = max(0, y1 - pad_px)
            right = min(source.width, x2 + pad_px)
            bottom = min(source.height, y2 + pad_px)
            if right <= left:
                return None
            if bottom <= top:
                return None
            crop = source.crop((left, top, right, bottom))
            if draw_target_box:
                target_left = max(0, x1 - left)
                target_top = max(0, y1 - top)
                target_right = min(crop.width - 1, x2 - left)
                target_bottom = min(crop.height - 1, y2 - top)
                ImageDraw.Draw(crop).rectangle(
                    (target_left, target_top, target_right, target_bottom),
                    outline="#DC2626",
                    width=3,
                )
            return crop
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max_image_pixels


def _panel_font(size: int = 18) -> ImageFont.ImageFont:
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def compose_panel(old_crop: Image.Image, new_crop: Image.Image, gap_px: int = 16) -> Image.Image:
    """Compose a labeled OLD/NEW panel so blank revisions are not mistaken for missing images."""
    width = old_crop.width + gap_px + new_crop.width
    header_height = 34
    content_height = max(old_crop.height, new_crop.height)
    height = header_height + content_height
    panel = Image.new("RGB", (width, height), color="white")
    new_x = old_crop.width + gap_px
    panel.paste(old_crop, (0, header_height))
    panel.paste(new_crop, (new_x, header_height))

    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, 0, old_crop.width - 1, header_height - 1), fill="#991B1B")
    draw.rectangle((new_x, 0, width - 1, header_height - 1), fill="#0F6B6F")
    draw.rectangle((old_crop.width, 0, new_x - 1, height - 1), fill="#E5E7EB")
    font = _panel_font()
    for label, left, right in (
        ("OLD", 0, old_crop.width),
        ("NEW", new_x, width),
    ):
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        draw.text(
            ((left + right - text_width) / 2, (header_height - text_height) / 2 - text_box[1]),
            label,
            fill="white",
            font=font,
        )
    draw.rectangle((0, 0, width - 1, height - 1), outline="#111827", width=2)
    return panel


def rel_posix(path: Path) -> str:
    return path.as_posix()


def reset_generated_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def write_microtext_index(pack_root: Path, manifest: list[dict[str, Any]]) -> None:
    rows = [
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\"><title>Microtext Review Pack</title>",
        "<style>",
        "body{font-family:Arial,sans-serif;margin:24px;}",
        "table{border-collapse:collapse;width:100%;}",
        "th,td{border:1px solid #ddd;padding:6px;vertical-align:top;font-size:13px;}",
        "th{background:#f5f5f5;position:sticky;top:0;}",
        "img{max-width:260px;max-height:120px;image-rendering:auto;}",
        "code{font-size:12px;}",
        "</style></head><body>",
        "<h1>Microtext Review Pack</h1>",
        f"<p>Rows: {len(manifest)}</p>",
        "<table>",
        "<tr><th>#</th><th>Crop</th><th>Page</th><th>Candidate</th><th>Category</th><th>Proposed Text</th><th>Context</th><th>Source</th></tr>",
    ]
    for idx, row in enumerate(manifest, start=1):
        crop_rel = str(row.get("crop_path", ""))
        crop_name = Path(crop_rel).name
        page_rel = str(row.get("page_path", ""))
        page_name = Path(page_rel).name
        page_link = (
            f"<a href=\"pages/{html.escape(page_name)}\">full page</a>" if page_name else ""
        )
        rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td><a href=\"crops/{html.escape(crop_name)}\"><img src=\"crops/{html.escape(crop_name)}\"></a></td>"
            f"<td>{page_link}</td>"
            f"<td><code>{html.escape(str(row.get('candidate_id', '')))}</code></td>"
            f"<td>{html.escape(str(row.get('category', '')))}</td>"
            f"<td>{html.escape(str(row.get('proposed_text') or row.get('target_text') or ''))}</td>"
            f"<td>{html.escape(str(row.get('text_context', '')))}</td>"
            f"<td><code>{html.escape(str(row.get('image_path', '')))}</code></td>"
            "</tr>"
        )
    rows.extend(["</table>", "</body></html>", ""])
    (pack_root / "index.html").write_text("\n".join(rows), encoding="utf-8")


def write_visualdiff_index(pack_root: Path, manifest: list[dict[str, Any]]) -> None:
    rows = [
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\"><title>Visualdiff Review Pack</title>",
        "<style>",
        "body{font-family:Arial,sans-serif;margin:24px;}",
        "table{border-collapse:collapse;width:100%;}",
        "th,td{border:1px solid #ddd;padding:6px;vertical-align:top;font-size:13px;}",
        "th{background:#f5f5f5;position:sticky;top:0;}",
        "img{max-width:720px;max-height:260px;image-rendering:auto;}",
        "code{font-size:12px;}",
        "</style></head><body>",
        "<h1>Visualdiff Review Pack</h1>",
        f"<p>Rows: {len(manifest)}</p>",
        "<table>",
        "<tr><th>#</th><th>Old / New Panel</th><th>Full Pages</th><th>Pair</th><th>Current Description</th></tr>",
    ]
    for index, row in enumerate(manifest, start=1):
        panel_name = Path(str(row.get("panel_path") or "")).name
        old_page_name = Path(str(row.get("old_page_path") or "")).name
        new_page_name = Path(str(row.get("new_page_path") or "")).name
        rows.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td><a href=\"panels/{html.escape(panel_name)}\"><img src=\"panels/{html.escape(panel_name)}\"></a></td>"
            f"<td><a href=\"pages_old/{html.escape(old_page_name)}\">old full page</a><br>"
            f"<a href=\"pages_new/{html.escape(new_page_name)}\">new full page</a></td>"
            f"<td><code>{html.escape(str(row.get('pair_id', '')))}</code></td>"
            f"<td>{html.escape(str(row.get('description') or row.get('change_desc_gt') or ''))}</td>"
            "</tr>"
        )
    rows.extend(["</table>", "</body></html>", ""])
    (pack_root / "index.html").write_text("\n".join(rows), encoding="utf-8")


def export_microtext_pack(
    root: str | Path,
    rows: list[dict[str, Any]],
    output_dir: Path,
    pad_px: int = 24,
    max_image_pixels: int | None = None,
    draw_target_box: bool = False,
) -> Counter[str]:
    root = Path(root)
    pack_root = root / output_dir
    crop_dir = pack_root / "crops"
    page_dir = pack_root / "pages"
    reset_generated_dir(crop_dir)
    reset_generated_dir(page_dir)

    manifest: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    copied_pages: dict[Path, Path] = {}
    for idx, row in enumerate(rows):
        row_id = str(row.get("candidate_id") or f"microtext_{idx:04d}")
        image_rel = Path(str(row.get("image_path", "")))
        image_path = root / image_rel
        if not image_path.exists():
            stats["missing_images"] += 1
            continue
        crop = padded_crop(
            image_path,
            bbox_xyxy(row),
            pad_px=pad_px,
            max_image_pixels=max_image_pixels,
            draw_target_box=draw_target_box,
        )
        if crop is None:
            stats["out_of_frame"] += 1
            continue
        crop_rel = output_dir / "crops" / f"{row_id}.png"
        crop.save(root / crop_rel)
        if image_rel not in copied_pages:
            doc_id = str(row.get("doc_id") or "unknown")
            page_index = int(row.get("page_index", row.get("page", 0)))
            suffix = image_path.suffix or ".png"
            page_rel = output_dir / "pages" / f"{doc_id}__p{page_index:04d}{suffix}"
            shutil.copy2(image_path, root / page_rel)
            copied_pages[image_rel] = page_rel
            stats["source_pages"] += 1
        else:
            page_rel = copied_pages[image_rel]

        enriched = dict(row)
        enriched["crop_path"] = rel_posix(crop_rel)
        enriched["page_path"] = rel_posix(page_rel)
        enriched["target_box_drawn"] = draw_target_box
        manifest.append(enriched)
        stats["rows"] += 1
        if draw_target_box:
            stats["target_boxes"] += 1
        stats[f"category_{row.get('category', 'unknown')}"] += 1

    write_jsonl(pack_root / "manifest.jsonl", manifest)
    write_microtext_index(pack_root, manifest)
    (pack_root / "README.md").write_text(
        "\n".join(
            [
                "# Microtext Review Pack",
                "",
                f"- Rows exported: `{stats['rows']}`",
                f"- Missing source images: `{stats['missing_images']}`",
                "- Each manifest row points to `crop_path`, `image_path`, and review metadata.",
                "- `pages/` contains copied full-page evidence for crop context.",
                "- Open `index.html` to browse the crop images quickly.",
                "- When `target_box_drawn` is true, the red rectangle identifies the exact label to review.",
                "- If a checklist CSV is present, fill that CSV rather than editing JSONL.",
                "- For rows with blank `proposed_text`, use `edited`, fill `corrected_text`, and fill `corrected_category` after human reading.",
                "- This export does not change labels or promote candidates into gold.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return stats


def export_visualdiff_pack(
    root: str | Path,
    rows: list[dict[str, Any]],
    output_dir: Path,
    pad_px: int = 24,
    max_image_pixels: int | None = None,
) -> Counter[str]:
    root = Path(root)
    pack_root = root / output_dir
    old_dir = pack_root / "old"
    new_dir = pack_root / "new"
    panel_dir = pack_root / "panels"
    old_page_dir = pack_root / "pages_old"
    new_page_dir = pack_root / "pages_new"
    reset_generated_dir(old_dir)
    reset_generated_dir(new_dir)
    reset_generated_dir(panel_dir)
    reset_generated_dir(old_page_dir)
    reset_generated_dir(new_page_dir)

    manifest: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    copied_old_pages: dict[Path, Path] = {}
    copied_new_pages: dict[Path, Path] = {}
    for idx, row in enumerate(rows):
        row_id = str(row.get("pair_id") or f"visualdiff_{idx:04d}")
        old_path = root / Path(str(row.get("image_old", "")))
        new_path = root / Path(str(row.get("image_new", "")))
        if not old_path.exists() or not new_path.exists():
            stats["missing_images"] += 1
            continue

        old_crop = padded_crop(
            old_path,
            bbox_xyxy(row, "bbox_old"),
            pad_px=pad_px,
            max_image_pixels=max_image_pixels,
        )
        new_crop = padded_crop(
            new_path,
            bbox_xyxy(row, "bbox_new"),
            pad_px=pad_px,
            max_image_pixels=max_image_pixels,
        )
        if old_crop is None or new_crop is None:
            stats["out_of_frame"] += 1
            continue
        panel = compose_panel(old_crop, new_crop)

        old_rel = output_dir / "old" / f"{row_id}.png"
        new_rel = output_dir / "new" / f"{row_id}.png"
        panel_rel = output_dir / "panels" / f"{row_id}.png"
        old_crop.save(root / old_rel)
        new_crop.save(root / new_rel)
        panel.save(root / panel_rel)
        if old_path not in copied_old_pages:
            old_page_rel = output_dir / "pages_old" / f"{old_path.parent.name}__{old_path.name}"
            shutil.copy2(old_path, root / old_page_rel)
            copied_old_pages[old_path] = old_page_rel
            stats["old_source_pages"] += 1
        else:
            old_page_rel = copied_old_pages[old_path]
        if new_path not in copied_new_pages:
            new_page_rel = output_dir / "pages_new" / f"{new_path.parent.name}__{new_path.name}"
            shutil.copy2(new_path, root / new_page_rel)
            copied_new_pages[new_path] = new_page_rel
            stats["new_source_pages"] += 1
        else:
            new_page_rel = copied_new_pages[new_path]

        enriched = dict(row)
        enriched["old_crop_path"] = rel_posix(old_rel)
        enriched["new_crop_path"] = rel_posix(new_rel)
        enriched["panel_path"] = rel_posix(panel_rel)
        enriched["old_page_path"] = rel_posix(old_page_rel)
        enriched["new_page_path"] = rel_posix(new_page_rel)
        manifest.append(enriched)
        stats["rows"] += 1
        stats[f"split_{row.get('split', 'unknown')}"] += 1

    write_jsonl(pack_root / "manifest.jsonl", manifest)
    write_visualdiff_index(pack_root, manifest)
    (pack_root / "README.md").write_text(
        "\n".join(
            [
                "# Visualdiff Review Pack",
                "",
                f"- Rows exported: `{stats['rows']}`",
                f"- Missing source images: `{stats['missing_images']}`",
                "- Each manifest row points to old/new crops plus a side-by-side panel.",
                "- `pages_old/` and `pages_new/` contain copied full-page evidence.",
                "- Open `index.html` to browse panels and full pages.",
                "- Accept only real visible changes inside the marked crop.",
                "- Reject rows where old/new are identical or where the only difference is whole-crop/page alignment shift.",
                "- Use `layout` only when a specific drawing object moved relative to surrounding drawing content.",
                "- Request the full page when the crop is too tight to judge confidently.",
                "- This export does not change labels or review status.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return stats


def filter_visualdiff_rows(
    rows: list[dict[str, Any]],
    split: str | None,
    bucket: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        if split and row.get("split") != split:
            continue
        if bucket and row.get("review_bucket") != bucket:
            continue
        selected.append(row)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Export crop-based reviewer packs")
    sub = parser.add_subparsers(dest="mode", required=True)

    micro = sub.add_parser("microtext", help="Export a microtext review pack")
    micro.add_argument("--root", default=".")
    micro.add_argument("--input", default="microtext/annotations/microtext_review_batch_001.jsonl")
    micro.add_argument("--output-dir", default="derived/review_packs/microtext_batch_001")
    micro.add_argument("--pad-px", type=int, default=24)
    micro.add_argument(
        "--draw-target-box",
        action="store_true",
        help="Draw the exact target bbox in red on each exported crop.",
    )
    micro.add_argument(
        "--max-image-pixels",
        type=int,
        default=None,
        help="Opt-in Pillow safety ceiling for audited large raster sources.",
    )

    vdiff = sub.add_parser("visualdiff", help="Export a visualdiff review pack")
    vdiff.add_argument("--root", default=".")
    vdiff.add_argument("--input", default="visualdiff/annotations/visualdiff_review_queue.jsonl")
    vdiff.add_argument("--output-dir", default="derived/review_packs/visualdiff_dev_pilot_001")
    vdiff.add_argument("--split", default="dev")
    vdiff.add_argument("--bucket", default="release_non_titleblock")
    vdiff.add_argument("--limit", type=int, default=50)
    vdiff.add_argument("--pad-px", type=int, default=24)
    vdiff.add_argument(
        "--max-image-pixels",
        type=int,
        default=None,
        help="Opt-in Pillow safety ceiling for audited large raster sources.",
    )

    args = parser.parse_args()
    root = Path(args.root)
    if args.mode == "microtext":
        rows = load_jsonl(root / args.input)
        stats = export_microtext_pack(
            root,
            rows,
            Path(args.output_dir),
            pad_px=args.pad_px,
            max_image_pixels=args.max_image_pixels,
            draw_target_box=args.draw_target_box,
        )
    else:
        rows = filter_visualdiff_rows(
            load_jsonl(root / args.input),
            split=args.split,
            bucket=args.bucket,
            limit=args.limit,
        )
        stats = export_visualdiff_pack(
            root,
            rows,
            Path(args.output_dir),
            pad_px=args.pad_px,
            max_image_pixels=args.max_image_pixels,
        )

    print(f"[OK] Exported review pack with {stats['rows']} rows")
    print(dict(sorted(stats.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
