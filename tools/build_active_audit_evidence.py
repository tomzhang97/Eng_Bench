#!/usr/bin/env python3
"""Extract exact active benchmark regions for machine audit reconciliation."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

try:
    from . import preview_reviewed_gold_promotion as preview
    from .qa_microtext_contact_sheet import fit_image
    from .reconcile_auditor_active_links import release_constraint
except ImportError:
    import preview_reviewed_gold_promotion as preview
    from qa_microtext_contact_sheet import fit_image
    from reconcile_auditor_active_links import release_constraint


# The active corpus includes archival engineering scans up to 282,401,280 pixels.
# Keep a finite ceiling while allowing the checked-in evidence builder to run on them.
DEFAULT_MAX_IMAGE_PIXELS = 300_000_000


def extract_region(image: Image.Image, bbox: list, padding: int) -> tuple[Image.Image, Image.Image, list]:
    if (not isinstance(bbox, list) or len(bbox) != 4 or any(type(v) is not int for v in bbox)
            or not (0 <= bbox[0] < bbox[2] <= image.width and 0 <= bbox[1] < bbox[3] <= image.height)):
        raise ValueError(f"invalid source bbox: {bbox}")
    if padding < 0:
        raise ValueError("padding cannot be negative")
    x1, y1, x2, y2 = bbox
    context_bbox = [max(0, x1 - padding), max(0, y1 - padding), min(image.width, x2 + padding), min(image.height, y2 + padding)]
    native = image.crop(bbox).convert("RGB")
    context = image.crop(context_bbox).convert("RGB")
    draw = ImageDraw.Draw(context)
    draw.rectangle((x1 - context_bbox[0], y1 - context_bbox[1], x2 - context_bbox[0] - 1, y2 - context_bbox[1] - 1), outline="red", width=2)
    return native, context, context_bbox


def paste_fit(canvas: Image.Image, image: Image.Image, bounds: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = bounds
    fitted = fit_image(image, x2 - x1, y2 - y1)
    canvas.paste(fitted, (x1 + (x2 - x1 - fitted.width) // 2, y1 + (y2 - y1 - fitted.height) // 2))


def build(root: Path, output: Path, *, max_image_pixels: int, padding: int = 120) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")
    constraint = release_constraint(root)
    if constraint["issues"] or not constraint.get("report_path"):
        raise ValueError(f"current audit reconciliation is not verified: {constraint}")
    report_path = root / constraint["report_path"]
    holds_path = report_path.parent / "registry_active_audit_rechecks.jsonl"
    holds = preview.read_jsonl(holds_path)
    before = preview.active_hashes(root)
    unified = preview.read_jsonl(root / "eng_bench.jsonl")
    linked = {}
    for row in unified:
        metadata = row.get("metadata") or {}
        identity = metadata.get("item_id") or metadata.get("pair_id")
        linked.setdefault(identity, []).append(row)
    output.mkdir(parents=True)
    entries, page_hashes = [], {}
    old_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        for number, hold in enumerate(holds, 1):
            identity, row, task = hold["active_gold_identity"], hold["active_row"], hold["task_type"]
            questions = linked.get(identity, [])
            if not questions:
                raise ValueError(f"active audit identity missing from unified benchmark: {identity}")
            image_lists = {tuple(q["images"]) for q in questions}
            if len(image_lists) != 1:
                raise ValueError(f"ambiguous benchmark images for {identity}")
            images = list(next(iter(image_lists)))
            sides = ["current"] if task == "microtext" else ["old", "new"]
            if len(images) != len(sides):
                raise ValueError(f"wrong benchmark image count: {identity}")
            tile = Image.new("RGB", (1200, 330 * len(sides) + 54), "white")
            draw = ImageDraw.Draw(tile)
            draw.text((8, 6), f"#{number:02d} {task} {row.get('split')}  {identity[:130]}", fill="black")
            text = str(row.get("text_gt") or row.get("change_desc_gt") or "")
            draw.text((8, 22), f"{row.get('category', '')} | answer: {text[:160]}", fill="black")
            evidence = []
            for side_index, (side, name) in enumerate(zip(sides, images)):
                page = (root / name).resolve()
                if not page.is_relative_to(root):
                    raise ValueError(f"image outside workspace: {page}")
                box = row["bbox"] if task == "microtext" else row["bbox_" + side]
                expected = {"image_index": side_index, "bbox": box}
                if any(expected not in q["evidence"] for q in questions):
                    raise ValueError(f"annotation and unified evidence differ: {identity}:{side}")
                with Image.open(page) as image:
                    if image.width * image.height > max_image_pixels:
                        raise ValueError(f"source exceeds inspected raster ceiling: {page}")
                    size = image.size
                    native, context, context_box = extract_region(image, box, padding)
                relative = page.relative_to(root).as_posix()
                page_hashes.setdefault(relative, preview.file_sha256(page))
                native_name, context_name = f"{number:02d}_{side}_native.png", f"{number:02d}_{side}_context.png"
                native.save(output / native_name)
                context.save(output / context_name)
                top = 48 + side_index * 330
                draw.text((8, top), f"{side}: exact bbox (no pixel overlays)", fill="black")
                draw.text((430, top), f"{side}: source context (red rectangle = stored bbox)", fill="black")
                paste_fit(tile, native, (8, top + 20, 410, top + 310))
                paste_fit(tile, context, (430, top + 20, 1190, top + 310))
                evidence.append({"side": side, "image_path": relative, "image_sha256": page_hashes[relative],
                                 "source_size": list(size), "bbox": box, "context_bbox": context_box,
                                 "native_path": native_name, "context_path": context_name,
                                 "native_sha256": preview.file_sha256(output / native_name),
                                 "context_sha256": preview.file_sha256(output / context_name)})
            tile_name = f"review_{number:02d}.png"
            tile.save(output / tile_name)
            entries.append({**hold, "inspection_index": number, "tile_path": tile_name,
                            "tile_sha256": preview.file_sha256(output / tile_name), "evidence": evidence,
                            "question_ids": [q["id"] for q in questions], "safe_to_merge_gold": False})
    finally:
        Image.MAX_IMAGE_PIXELS = old_limit
    preview.write_jsonl(output / "evidence_index.jsonl", entries)
    atlases = []
    microtext_entries = [entry for entry in entries if entry["task_type"] == "microtext"]
    for start in range(0, len(microtext_entries), 12):
        group = microtext_entries[start:start + 12]
        canvas = Image.new("RGB", (1440, ((len(group) + 1) // 2) * 245), "white")
        for offset, entry in enumerate(group):
            with Image.open(output / entry["tile_path"]) as tile:
                paste_fit(canvas, tile, ((offset % 2) * 720, (offset // 2) * 245, (offset % 2 + 1) * 720, (offset // 2 + 1) * 245))
        name = f"microtext_atlas_{group[0]['inspection_index']:02d}_{group[-1]['inspection_index']:02d}.png"
        canvas.save(output / name)
        atlases.append(name)
    if before != preview.active_hashes(root):
        raise ValueError("active release changed during evidence extraction")
    report = {"goal": "Gold v2.0 Global", "status": "EVIDENCE_READY_NOT_ADJUDICATED",
              "rows": len(entries), "by_task": dict(Counter(e["task_type"] for e in entries)),
              "source_reconciliation_report": constraint["report_path"], "source_report_sha256": preview.file_sha256(report_path),
              "source_image_hashes": page_hashes, "active_release_hashes": before,
              "index_sha256": preview.file_sha256(output / "evidence_index.jsonl"),
              "atlases": {name: preview.file_sha256(output / name) for name in atlases},
              "active_gold_modified": False, "human_votes_modified": False,
              "note": "Exact source pixels and current benchmark boxes; no inferred auditor reasons or automatic disposition."}
    preview.write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-image-pixels", type=int, default=DEFAULT_MAX_IMAGE_PIXELS)
    parser.add_argument("--padding", type=int, default=120)
    args = parser.parse_args()
    report = build(args.root, args.root / args.output_dir, max_image_pixels=args.max_image_pixels, padding=args.padding)
    print(json.dumps({key: report[key] for key in ("status", "rows", "by_task", "active_gold_modified")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
