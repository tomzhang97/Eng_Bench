#!/usr/bin/env python3
"""Render labeled contact sheets for every document page in a manifest slice."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps


def read_doc_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            if row.get("type") == "doc":
                rows.append(row)
    return rows


def page_inventory(root: Path, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for doc in docs:
        doc_id = str(doc.get("doc_id") or "").strip()
        if not doc_id:
            raise ValueError("document row is missing doc_id")
        derived = doc.get("derived") if isinstance(doc.get("derived"), dict) else {}
        pages_rel = str(derived.get("pages_dir") or f"derived/pages_300dpi/{doc_id}")
        page_paths = sorted((root / pages_rel).glob("page_*.png"))
        if not page_paths:
            raise FileNotFoundError(f"no rendered pages found for {doc_id}: {pages_rel}")
        for page_path in page_paths:
            pages.append(
                {
                    "doc_id": doc_id,
                    "revision": str((doc.get("version") or {}).get("revision") or ""),
                    "source_candidate_id": str(doc.get("source_candidate_id") or ""),
                    "page": page_path.stem.removeprefix("page_"),
                    "path": page_path,
                }
            )
    return pages


def render_sheets(
    pages: list[dict[str, Any]],
    output_dir: Path,
    *,
    columns: int = 2,
    rows: int = 2,
    cell_width: int = 1000,
    cell_height: int = 760,
) -> list[dict[str, Any]]:
    if columns < 1 or rows < 1:
        raise ValueError("columns and rows must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    per_sheet = columns * rows
    sheet_count = math.ceil(len(pages) / per_sheet)
    inventory: list[dict[str, Any]] = []

    for sheet_index in range(sheet_count):
        chunk = pages[sheet_index * per_sheet : (sheet_index + 1) * per_sheet]
        canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
        draw = ImageDraw.Draw(canvas)
        sheet_pages: list[dict[str, Any]] = []

        for cell_index, page in enumerate(chunk):
            column = cell_index % columns
            row = cell_index // columns
            x0 = column * cell_width
            y0 = row * cell_height
            label = (
                f"{page['doc_id']}  rev={page['revision']}  "
                f"source={page['source_candidate_id']}  page={page['page']}"
            )
            draw.rectangle((x0, y0, x0 + cell_width - 1, y0 + cell_height - 1), outline="#777777")
            draw.text((x0 + 12, y0 + 10), label, fill="black")

            with Image.open(page["path"]) as source:
                image = source.convert("RGB")
                original_size = image.size
                fitted = ImageOps.contain(image, (cell_width - 24, cell_height - 54))
            paste_x = x0 + (cell_width - fitted.width) // 2
            paste_y = y0 + 42 + (cell_height - 54 - fitted.height) // 2
            canvas.paste(fitted, (paste_x, paste_y))
            sheet_pages.append(
                {
                    "doc_id": page["doc_id"],
                    "revision": page["revision"],
                    "page": page["page"],
                    "path": page["path"].as_posix(),
                    "width": original_size[0],
                    "height": original_size[1],
                }
            )

        output_path = output_dir / f"contact_sheet_{sheet_index + 1:02d}.png"
        canvas.save(output_path, optimize=True)
        inventory.append({"path": output_path.as_posix(), "pages": sheet_pages})

    return inventory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest-additions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--columns", type=int, default=2)
    parser.add_argument("--rows", type=int, default=2)
    parser.add_argument("--cell-width", type=int, default=1000)
    parser.add_argument("--cell-height", type=int, default=760)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    manifest_path = args.manifest_additions
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = root / output_dir

    docs = read_doc_rows(manifest_path)
    pages = page_inventory(root, docs)
    sheets = render_sheets(
        pages,
        output_dir,
        columns=args.columns,
        rows=args.rows,
        cell_width=args.cell_width,
        cell_height=args.cell_height,
    )
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "manifest_document_visual_qa",
        "manifest_additions": manifest_path.relative_to(root).as_posix(),
        "documents": len(docs),
        "pages": len(pages),
        "sheets": sheets,
        "active_gold_rows_modified": 0,
    }
    report_path = output_dir / "contact_sheet_inventory.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"documents": len(docs), "pages": len(pages), "sheets": len(sheets)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
