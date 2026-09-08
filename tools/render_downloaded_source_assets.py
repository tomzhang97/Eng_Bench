#!/usr/bin/env python3
"""Render downloaded source payloads and extract first-pass text layers."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageOps


RENDER_FIELDS = [
    "render_rank",
    "download_rank",
    "candidate_id",
    "domain",
    "asset_kind",
    "import_action",
    "doc_id",
    "local_path",
    "status",
    "dpi",
    "page_selection",
    "rendered_pages",
    "text_spans",
    "pages_dir",
    "textlayer_jsonl",
    "error",
    "next_step",
]
PDF_EXTENSIONS = {".pdf"}
RASTER_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
SVG_EXTENSIONS = {".svg"}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RENDER_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve_path(root: Path, path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return root / path


def portable_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def parse_pages(selection: str | None, page_count: int) -> list[int]:
    value = (selection or "1").strip().lower()
    if value in {"", "all"}:
        return list(range(page_count))
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_s, end_s = token.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            for page in range(start, end + 1):
                if 1 <= page <= page_count:
                    pages.add(page - 1)
        else:
            page = int(token)
            if 1 <= page <= page_count:
                pages.add(page - 1)
    return sorted(pages)


def rotate_bbox_xyxy(
    bbox: list[float],
    rotation: int,
    page_width: float,
    page_height: float,
) -> list[float]:
    x0, y0, x1, y1 = bbox
    rotation = rotation % 360
    if rotation == 90:
        return [page_height - y1, x0, page_height - y0, x1]
    if rotation == 180:
        return [page_width - x1, page_height - y1, page_width - x0, page_height - y0]
    if rotation == 270:
        return [y0, page_width - x1, y1, page_width - x0]
    return [x0, y0, x1, y1]


def extract_pdf_spans(page: fitz.Page, page_index: int, dpi: int) -> list[dict[str, Any]]:
    pts_to_px = dpi / 72.0
    crop = page.cropbox
    spans: list[dict[str, Any]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text") or "").strip()
                if not text:
                    continue
                bbox = span.get("bbox")
                bbox_px = None
                if bbox:
                    oriented = rotate_bbox_xyxy(
                        list(bbox),
                        page.rotation,
                        float(crop.width),
                        float(crop.height),
                    )
                    bbox_px = [coord * pts_to_px for coord in oriented]
                spans.append(
                    {
                        "page": page_index,
                        "text": text,
                        "bbox_px": bbox_px,
                        "font": span.get("font"),
                        "size": span.get("size"),
                        "flags": span.get("flags"),
                        "color": span.get("color"),
                    }
                )
    return spans


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def render_pdf_textlayer(root: Path, doc_id: str, pdf_path: Path, dpi: int, pages: str) -> dict[str, Any]:
    pages_dir = root / "derived" / f"pages_{dpi}dpi" / doc_id
    page_json_dir = root / "derived" / "textlayer" / doc_id
    textlayer_jsonl = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_json_dir.mkdir(parents=True, exist_ok=True)

    document = fitz.open(pdf_path)
    try:
        selected_pages = parse_pages(pages, document.page_count)
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        all_spans: list[dict[str, Any]] = []
        for page_index in selected_pages:
            page = document[page_index]
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
            pix.save(pages_dir / f"page_{page_index:03d}.png")
            spans = extract_pdf_spans(page, page_index, dpi)
            all_spans.extend(spans)
            page_json = {
                "doc_page": page_index,
                "spans": [
                    {
                        "page_index": span["page"],
                        "text": span["text"],
                        "bbox_px": span["bbox_px"],
                        "font": span.get("font"),
                        "size": span.get("size"),
                        "flags": span.get("flags"),
                        "color": span.get("color"),
                    }
                    for span in spans
                ],
            }
            (page_json_dir / f"page_{page_index:03d}.json").write_text(
                json.dumps(page_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        write_jsonl(textlayer_jsonl, all_spans)
        return {
            "status": "rendered_pdf_textlayer",
            "rendered_pages": len(selected_pages),
            "text_spans": len(all_spans),
            "page_selection": ",".join(str(page + 1) for page in selected_pages),
            "pages_dir": portable_path(pages_dir, root),
            "textlayer_jsonl": portable_path(textlayer_jsonl, root),
        }
    finally:
        document.close()


def render_raster_image(
    root: Path,
    doc_id: str,
    image_path: Path,
    dpi: int,
    max_image_pixels: int | None = None,
) -> dict[str, Any]:
    pages_dir = root / "derived" / f"pages_{dpi}dpi" / doc_id
    out_path = pages_dir / "page_000.png"
    pages_dir.mkdir(parents=True, exist_ok=True)
    previous_max_image_pixels = Image.MAX_IMAGE_PIXELS
    if max_image_pixels is not None:
        Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        with Image.open(image_path) as image:
            image = ImageOps.exif_transpose(image)
            image.convert("RGB").save(out_path, format="PNG", optimize=True)
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max_image_pixels
    return {
        "status": "rendered_image",
        "rendered_pages": 1,
        "text_spans": 0,
        "page_selection": "1",
        "pages_dir": portable_path(pages_dir, root),
        "textlayer_jsonl": "",
    }


def render_svg_textlayer(root: Path, doc_id: str, svg_path: Path, dpi: int) -> dict[str, Any]:
    pages_dir = root / "derived" / f"pages_{dpi}dpi" / doc_id
    textlayer_jsonl = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
    pages_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(stream=svg_path.read_bytes(), filetype="svg")
    try:
        page = document[0]
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
        pix.save(pages_dir / "page_000.png")
        scale_x = pix.width / page.rect.width
        scale_y = pix.height / page.rect.height
        rows: list[dict[str, Any]] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = str(span.get("text") or "").strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    rows.append(
                        {
                            "page": 0,
                            "text": text,
                            "bbox_px": [x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y],
                            "font": span.get("font", "svg"),
                            "size": span.get("size", 0),
                            "flags": span.get("flags", 0),
                            "color": span.get("color", 0),
                        }
                    )
        write_jsonl(textlayer_jsonl, rows)
        return {
            "status": "rendered_svg_textlayer",
            "rendered_pages": 1,
            "text_spans": len(rows),
            "page_selection": "1",
            "pages_dir": portable_path(pages_dir, root),
            "textlayer_jsonl": portable_path(textlayer_jsonl, root),
        }
    finally:
        document.close()


def base_receipt(row: dict[str, str], rank: int, dpi: int) -> dict[str, Any]:
    return {
        "render_rank": rank,
        "download_rank": row.get("download_rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "asset_kind": row.get("asset_kind", ""),
        "import_action": row.get("import_action", ""),
        "doc_id": row.get("doc_id", ""),
        "local_path": row.get("local_path", ""),
        "status": "",
        "dpi": dpi,
        "page_selection": "",
        "rendered_pages": "",
        "text_spans": "",
        "pages_dir": "",
        "textlayer_jsonl": "",
        "error": "",
        "next_step": "",
    }


def process_row(
    root: Path,
    row: dict[str, str],
    rank: int,
    *,
    dpi: int,
    pages: str,
    dry_run: bool,
    max_image_pixels: int | None = None,
) -> dict[str, Any]:
    receipt = base_receipt(row, rank, dpi)
    download_status = str(row.get("status") or "").strip()
    if download_status not in {"downloaded", "skipped_existing"}:
        receipt.update(
            status="skipped_receipt_status",
            error=f"download_receipt_status:{download_status}",
            next_step="Resolve download receipt before render/extraction.",
        )
        return receipt
    if dry_run:
        receipt.update(
            status="dry_run",
            next_step="Dry run only. Re-run without --dry-run to render or extract.",
        )
        return receipt

    local_path = str(row.get("local_path") or "").strip()
    suffix = Path(local_path).suffix.lower()
    if str(row.get("asset_kind") or "") == "archive" or suffix in {".zip", ".7z", ".tar", ".gz"}:
        receipt.update(
            status="skipped_archive_manual_unpack",
            next_step="Unpack and select engineering drawing assets before render/extraction.",
        )
        return receipt
    if not local_path:
        receipt.update(status="failed_missing_payload", error="missing local_path")
        return receipt
    source_path = resolve_path(root, local_path)
    if not source_path.exists():
        receipt.update(status="failed_missing_payload", error=f"missing file:{local_path}")
        return receipt

    doc_id = str(row.get("doc_id") or source_path.stem).strip()
    try:
        if suffix in PDF_EXTENSIONS:
            result = render_pdf_textlayer(root, doc_id, source_path, dpi, pages)
        elif suffix in RASTER_EXTENSIONS:
            result = render_raster_image(
                root,
                doc_id,
                source_path,
                dpi,
                max_image_pixels=max_image_pixels,
            )
        elif suffix in SVG_EXTENSIONS:
            result = render_svg_textlayer(root, doc_id, source_path, dpi)
        else:
            receipt.update(
                status="skipped_unsupported_type",
                error=f"unsupported_suffix:{suffix}",
                next_step="Add a converter or select a different payload.",
            )
            return receipt
    except Exception as exc:  # noqa: BLE001 - record and continue pipeline rows.
        receipt.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        return receipt

    receipt.update(result)
    receipt["next_step"] = (
        "Mine candidate annotations from rendered pages/text layers and export human review packets before gold."
    )
    return receipt


def build_report(
    root: str | Path,
    *,
    receipts_csv: str | Path,
    date_label: str | None = None,
    limit: int | None = None,
    dpi: int = 300,
    pages: str = "1",
    dry_run: bool = False,
    doc_ids: set[str] | None = None,
    max_image_pixels: int | None = None,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(receipts_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = read_csv(input_path)
    filtered_rows = [
        row for row in rows if doc_ids is None or str(row.get("doc_id") or "").strip() in doc_ids
    ]
    selected_rows = filtered_rows[:limit] if limit is not None and limit > 0 else filtered_rows
    render_receipts = [
        process_row(
            root,
            row,
            idx + 1,
            dpi=dpi,
            pages=pages,
            dry_run=dry_run,
            max_image_pixels=max_image_pixels,
        )
        for idx, row in enumerate(selected_rows)
    ]
    status_counts = Counter(str(row["status"]) for row in render_receipts)
    totals: dict[str, Any] = {
        "date_label": date_label or date.today().isoformat(),
        "download_receipt_rows": len(rows),
        "filtered_rows": len(filtered_rows),
        "processed_rows": len(render_receipts),
        "limit": limit or "",
        "dpi": dpi,
        "max_image_pixels": max_image_pixels or "",
        "pages": pages,
        "doc_ids": ",".join(sorted(doc_ids)) if doc_ids else "",
        "dry_run_mode": dry_run,
        "rendered_pages": sum(int(row["rendered_pages"] or 0) for row in render_receipts),
        "text_spans": sum(int(row["text_spans"] or 0) for row in render_receipts),
    }
    totals.update(dict(sorted(status_counts.items())))
    return {
        "date_label": totals["date_label"],
        "download_receipts_csv": input_path.as_posix(),
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "by_domain": dict(sorted(Counter(row["domain"] for row in render_receipts).items())),
        "by_action": dict(sorted(Counter(row["import_action"] for row in render_receipts).items())),
        "render_receipts": render_receipts,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Asset Render Receipts",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Download receipts CSV: `{report['download_receipts_csv']}`",
        f"- Download receipt rows: `{totals['download_receipt_rows']}`",
        f"- Filtered rows: `{totals['filtered_rows']}`",
        f"- Processed rows: `{totals['processed_rows']}`",
        f"- Doc IDs: `{totals['doc_ids']}`",
        f"- DPI: `{totals['dpi']}`",
        f"- Opt-in raster pixel ceiling: `{totals['max_image_pixels']}`",
        f"- PDF pages selection: `{totals['pages']}`",
        f"- Rendered pages: `{totals['rendered_pages']}`",
        f"- Text spans: `{totals['text_spans']}`",
        f"- Dry run mode: `{totals['dry_run_mode']}`",
        "- Receipt only: do not merge unreviewed rows into gold.",
        "",
        "## Status Counts",
        "",
    ]
    if report["status_counts"]:
        for status, count in report["status_counts"].items():
            lines.append(f"- {status}: `{count}`")
    else:
        lines.append("- none: `0`")
    lines.extend(
        [
            "",
            "## Render Receipts",
            "",
            "| Rank | Candidate | Kind | Status | Pages | Spans | Pages Dir | Textlayer |",
            "| ---: | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in report["render_receipts"]:
        lines.append(
            f"| {row['render_rank']} | `{row['candidate_id']}` | {row['asset_kind']} | {row['status']} | "
            f"{row['rendered_pages']} | {row['text_spans']} | {row['pages_dir']} | {row['textlayer_jsonl']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This pass renders or extracts review inputs only; it does not create benchmark labels.",
            "- Archive rows require explicit unpack/select before conversion.",
            "- Candidate mining and human review packets must happen before any gold merge.",
            "- Do not merge unreviewed candidates into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render downloaded source payloads and extract first-pass text layers."
    )
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--receipts-csv", default="derived/quality/source_asset_download_receipts_2026-06-16.csv")
    parser.add_argument("--output-json", default="derived/quality/source_asset_render_receipts_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_asset_render_receipts_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_asset_render_receipts_2026-06-16.csv")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        default=None,
        help=(
            "Opt-in Pillow safety ceiling for audited large raster sources. "
            "The process-wide default is restored after each raster."
        ),
    )
    parser.add_argument("--pages", default="1", help="1-based PDF page selection, e.g. 1 or 1-3")
    parser.add_argument("--doc-ids", help="Comma-separated doc_id filter")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    doc_ids = {part.strip() for part in (args.doc_ids or "").split(",") if part.strip()} or None

    root = Path(args.root)
    report = build_report(
        root,
        receipts_csv=args.receipts_csv,
        date_label=args.date_label,
        limit=args.limit,
        dpi=args.dpi,
        pages=args.pages,
        dry_run=args.dry_run,
        doc_ids=doc_ids,
        max_image_pixels=args.max_image_pixels,
    )
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_csv = Path(args.output_csv)
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    if not output_csv.is_absolute():
        output_csv = root / output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["render_receipts"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
