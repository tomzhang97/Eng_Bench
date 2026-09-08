#!/usr/bin/env python3
"""Render XML-format EAGLE schematics or boards into deterministic SVG/PNG pages."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import fitz


DEFAULT_LAYERS = {
    1: True,
    16: True,
    17: True,
    18: True,
    19: True,
    20: True,
    21: True,
    22: True,
    25: True,
    26: True,
    29: True,
    30: True,
    91: True,
    92: True,
    93: True,
    94: True,
    95: True,
    96: True,
    97: True,
    104: True,
}
TEXT_NODE_RE = re.compile(r"(<text\b[^>]*>)(.*?)(</text>)", re.DOTALL)
TSPAN_NODE_RE = re.compile(r"(<tspan\b[^>]*>)(.*?)(</tspan>)", re.DOTALL)
EMPTY_TEXT_NODE_RE = re.compile(r"<text\b[^>]*>\s*</text>", re.DOTALL)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sheet_count_from_xml(xml_text: str) -> int:
    root = ET.fromstring(xml_text)
    drawing = root.find("drawing")
    if drawing is None:
        raise ValueError("EAGLE XML is missing <drawing>")
    if drawing.find("board") is not None:
        return 1
    sheets = drawing.findall("./schematic/sheets/sheet")
    if not sheets:
        raise ValueError("EAGLE schematic XML contains no sheets")
    return len(sheets)


def sanitize_eagle_xml_for_renderer(xml_text: str) -> tuple[str, int]:
    """Remove invisible empty text nodes that crash eagle2svg 0.1.5."""
    return EMPTY_TEXT_NODE_RE.subn("", xml_text)


def escape_svg_text_content(value: str) -> str:
    """Escape user text while preserving renderer-generated tspan elements."""
    matches = list(TSPAN_NODE_RE.finditer(value))
    if not matches:
        return html.escape(html.unescape(value), quote=False)

    output: list[str] = []
    cursor = 0
    for match in matches:
        output.append(html.escape(html.unescape(value[cursor : match.start()]), quote=False))
        output.append(match.group(1))
        output.append(html.escape(html.unescape(match.group(2)), quote=False))
        output.append(match.group(3))
        cursor = match.end()
    output.append(html.escape(html.unescape(value[cursor:]), quote=False))
    return "".join(output)


def stabilize_svg(svg: str, extraction_timestamp: str | None = None) -> str:
    if extraction_timestamp:
        svg = svg.replace(extraction_timestamp, "")
    svg = svg.replace("\r\n", "\n")
    return TEXT_NODE_RE.sub(
        lambda match: (
            match.group(1)
            + escape_svg_text_content(match.group(2))
            + match.group(3)
        ),
        svg,
    )


def load_eagle_parser(root: Path):
    try:
        from eagle2svg import eagle_parser
    except ImportError:
        vendor = root / "derived" / "vendor" / "eagle2svg"
        if not vendor.exists():
            raise RuntimeError(
                "eagle2svg is not installed. Install eagle2svg==0.1.5 or place it at "
                "derived/vendor/eagle2svg."
            )
        sys.path.insert(0, str(vendor))
        from eagle2svg import eagle_parser
    # eagle2svg 0.1.5 uses bare open(), which follows the Windows console
    # code page and breaks valid UTF-8 EAGLE XML. Give that module an explicit
    # UTF-8 opener without modifying the vendored package.
    eagle_parser.open = open_eagle_xml
    return eagle_parser


def open_eagle_xml(filename: str):
    return open(filename, "r", encoding="utf-8")


def render_eagle_svg(
    root: Path,
    source: Path,
    sheet_index: int,
    layers: dict[int, bool] | None = None,
) -> str:
    eagle_parser = load_eagle_parser(root)
    eagle = eagle_parser.Eagle(str(source))
    extraction_timestamp = str(eagle.replace.get(">LAST_DATE_TIME") or "")
    eagle.replace[">LAST_DATE_TIME"] = ""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        eagle.render(sheet=sheet_index, layers=layers or DEFAULT_LAYERS)
    return stabilize_svg(output.getvalue(), extraction_timestamp=extraction_timestamp)


def svg_to_png(svg: str, output: Path, dpi: int = 300, grayscale: bool = False) -> tuple[int, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open(stream=svg.encode("utf-8"), filetype="svg")
    try:
        page = document[0]
        colorspace = fitz.csGRAY if grayscale else fitz.csRGB
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0),
            colorspace=colorspace,
            alpha=False,
        )
        pixmap.save(output)
        return pixmap.width, pixmap.height
    finally:
        document.close()


def render_eagle_file(
    root: Path,
    source: Path,
    doc_id: str,
    dpi: int = 300,
    grayscale: bool = True,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    source = source if source.is_absolute() else root / source
    if not source.exists():
        raise FileNotFoundError(source)
    if not doc_id.strip():
        raise ValueError("doc_id must be non-empty")

    xml_text = source.read_text(encoding="utf-8")
    sheet_count = sheet_count_from_xml(xml_text)
    render_xml, empty_text_nodes_removed = sanitize_eagle_xml_for_renderer(xml_text)
    render_source = source
    sanitized_source: Path | None = None
    if empty_text_nodes_removed:
        sanitized_dir = root / ".codex_work" / "eagle_render_sanitized"
        sanitized_dir.mkdir(parents=True, exist_ok=True)
        sanitized_source = sanitized_dir / f"{doc_id}-{sha256_file(source)[:16]}{source.suffix}"
        sanitized_source.write_text(render_xml, encoding="utf-8")
        render_source = sanitized_source
    svg_dir = root / "derived" / "eagle_svg" / doc_id
    page_dir = root / "derived" / f"pages_{dpi}dpi" / doc_id
    pages: list[dict[str, Any]] = []

    try:
        for sheet_index in range(sheet_count):
            svg = render_eagle_svg(root, render_source, sheet_index)
            svg_path = svg_dir / f"page_{sheet_index:03d}.svg"
            png_path = page_dir / f"page_{sheet_index:03d}.png"
            svg_path.parent.mkdir(parents=True, exist_ok=True)
            svg_path.write_text(svg, encoding="utf-8")
            width, height = svg_to_png(svg, png_path, dpi=dpi, grayscale=grayscale)
            pages.append(
                {
                    "page_index": sheet_index,
                    "svg_path": svg_path.relative_to(root).as_posix(),
                    "image_path": png_path.relative_to(root).as_posix(),
                    "width": width,
                    "height": height,
                }
            )
    finally:
        if sanitized_source is not None:
            sanitized_source.unlink(missing_ok=True)

    manifest = {
        "doc_id": doc_id,
        "renderer": "eagle2svg==0.1.5+PyMuPDF",
        "source_path": source.relative_to(root).as_posix(),
        "source_sha256": sha256_file(source),
        "dpi": dpi,
        "grayscale": grayscale,
        "page_count": sheet_count,
        "renderer_sanitizations": {
            "empty_text_nodes_removed": empty_text_nodes_removed,
        },
        "pages": pages,
    }
    if manifest_path is not None:
        target = manifest_path if manifest_path.is_absolute() else root / manifest_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True, help="EAGLE .sch or .brd path relative to root")
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--color", action="store_true", help="Render RGB instead of grayscale")
    parser.add_argument("--manifest", help="Optional output manifest JSON path relative to root")
    args = parser.parse_args()

    manifest = render_eagle_file(
        root=Path(args.root),
        source=Path(args.input),
        doc_id=args.doc_id,
        dpi=args.dpi,
        grayscale=not args.color,
        manifest_path=Path(args.manifest) if args.manifest else None,
    )
    print(
        f"[OK] Rendered {manifest['page_count']} EAGLE page(s) for {args.doc_id} "
        f"to derived/pages_{args.dpi}dpi/{args.doc_id}/"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
