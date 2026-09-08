#!/usr/bin/env python3
"""Render legacy SVG files with modern text and auditable geometry.

Some older CorelDRAW exports embed SVG 1.0 fonts. Modern Chromium ignores those
fonts, while PyMuPDF can render their glyph paths as black silhouettes and emit
zero-area text boxes. This tool removes only the obsolete embedded font data,
inlines the applicable text styles, and renders the resulting SVG with a local
Chromium executable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from PIL import Image, ImageFont


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
ENCODING_RE = re.compile(br"<\?xml[^>]*encoding=[\"']([^\"']+)[\"']", re.I)
CSS_RULE_RE = re.compile(r"\.([A-Za-z_][\w-]*)\s*\{([^}]*)\}", re.S)
FONT_FACE_RE = re.compile(r"@font-face\s*\{[^}]*\}", re.I | re.S)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def decode_svg(data: bytes) -> str:
    match = ENCODING_RE.search(data[:512])
    candidates = [match.group(1).decode("ascii", errors="ignore")] if match else []
    candidates.extend(["utf-8", "iso-8859-1"])
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def parse_declarations(value: str | None) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for declaration in (value or "").split(";"):
        if ":" not in declaration:
            continue
        name, raw_value = declaration.split(":", 1)
        name = name.strip().lower()
        raw_value = raw_value.strip()
        if name and raw_value:
            declarations[name] = raw_value
    return declarations


def css_classes(root: ET.Element) -> dict[str, dict[str, str]]:
    classes: dict[str, dict[str, str]] = {}
    for node in root.iter():
        if local_name(node.tag) != "style" or not node.text:
            continue
        for match in CSS_RULE_RE.finditer(node.text):
            classes[match.group(1)] = parse_declarations(match.group(2))
    return classes


def remove_legacy_fonts(root: ET.Element) -> int:
    removed = 0
    for parent in list(root.iter()):
        for child in list(parent):
            if local_name(child.tag) in {"font", "font-face"}:
                parent.remove(child)
                removed += 1
    for node in root.iter():
        if local_name(node.tag) == "style" and node.text:
            node.text = FONT_FACE_RE.sub("", node.text)
    return removed


def sanitize_svg(
    data: bytes,
    *,
    pixel_width: int | None = None,
    pixel_height: int | None = None,
) -> tuple[bytes, dict[str, Any]]:
    text = decode_svg(data)
    text = re.sub(
        r"<\?xml[^>]*\?>",
        '<?xml version="1.0" encoding="utf-8"?>',
        text,
        count=1,
        flags=re.I,
    )
    root = ET.fromstring(text.encode("utf-8"))
    classes = css_classes(root)
    text_nodes = 0
    inlined_nodes = 0
    style_names = {
        "fill",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "text-anchor",
    }
    for node in root.iter():
        if local_name(node.tag) != "text":
            continue
        text_nodes += 1
        merged: dict[str, str] = {}
        for class_name in (node.get("class") or "").split():
            merged.update(classes.get(class_name, {}))
        merged.update(parse_declarations(node.get("style")))
        changed = False
        for name in style_names:
            if name in merged and name not in node.attrib:
                value = merged[name]
                if name == "font-family":
                    value = value.strip("\"'") or "Arial"
                node.set(name, value)
                changed = True
        if changed:
            inlined_nodes += 1
        node.attrib.pop("class", None)
    removed_fonts = remove_legacy_fonts(root)
    if pixel_width is not None:
        root.set("width", f"{pixel_width}px")
    if pixel_height is not None:
        root.set("height", f"{pixel_height}px")
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    output = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return output, {
        "text_nodes": text_nodes,
        "text_nodes_with_inlined_styles": inlined_nodes,
        "legacy_font_nodes_removed": removed_fonts,
    }


def parse_length_inches(value: str | None) -> float | None:
    if not value:
        return None
    match = NUMBER_RE.search(value)
    if not match:
        return None
    number = float(match.group(0))
    unit = value[match.end() :].strip().lower()
    factors = {
        "in": 1.0,
        "mm": 1.0 / 25.4,
        "cm": 1.0 / 2.54,
        "pt": 1.0 / 72.0,
        "pc": 1.0 / 6.0,
        "px": 1.0 / 96.0,
        "": 1.0 / 96.0,
    }
    return number * factors.get(unit, 1.0 / 96.0)


def target_pixel_size(data: bytes, dpi: int) -> tuple[int, int]:
    root = ET.fromstring(decode_svg(data).encode("utf-8"))
    width_in = parse_length_inches(root.get("width"))
    height_in = parse_length_inches(root.get("height"))
    if width_in and height_in:
        return max(1, math.ceil(width_in * dpi)), max(1, math.ceil(height_in * dpi))
    view_box = [float(value) for value in NUMBER_RE.findall(root.get("viewBox") or "")]
    if len(view_box) == 4:
        return max(1, math.ceil(view_box[2])), max(1, math.ceil(view_box[3]))
    raise ValueError("SVG must define physical width/height or a viewBox")


def find_chromium(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("CHROME_PATH"):
        candidates.append(Path(os.environ["CHROME_PATH"]))
    for name in ("chrome", "chromium", "chromium-browser", "msedge"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates.extend(
            [
                program_files / "Google/Chrome/Application/chrome.exe",
                program_files_x86 / "Google/Chrome/Application/chrome.exe",
                local_app_data / "Google/Chrome/Application/chrome.exe",
                program_files / "Microsoft/Edge/Application/msedge.exe",
                program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("No Chromium executable found; set CHROME_PATH or pass --chrome-path")


def render_with_chromium(
    sanitized_svg: bytes,
    output_path: Path,
    width: int,
    height: int,
    *,
    chrome_path: str | None = None,
) -> dict[str, Any]:
    chromium = find_chromium(chrome_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="engbench_svg_") as temp_dir_value:
        temp_dir = Path(temp_dir_value)
        svg_path = temp_dir / "source.svg"
        profile = temp_dir / "profile"
        screenshot = temp_dir / "render.png"
        svg_path.write_bytes(sanitized_svg)
        command = [
            str(chromium),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--run-all-compositor-stages-before-draw",
            "--force-device-scale-factor=1",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            f"--screenshot={screenshot}",
            svg_path.resolve().as_uri(),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if not screenshot.is_file():
            message = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"Chromium did not produce an SVG screenshot: {message}")
        with Image.open(screenshot) as image:
            if image.size != (width, height):
                raise RuntimeError(
                    f"Chromium rendered {image.size}, expected {(width, height)}"
                )
        shutil.copyfile(screenshot, output_path)
    return {
        "renderer": "chromium_headless_svg_compat",
        "chromium_path": str(chromium),
        "chromium_returncode": completed.returncode,
    }


def parse_number(value: str | None, default: float = 0.0) -> float:
    match = NUMBER_RE.search(value or "")
    return float(match.group(0)) if match else default


def load_font(size: int, family: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[Path | str] = []
    if os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        normalized = family.lower()
        candidates.append(fonts / ("arialbd.ttf" if "bold" in normalized else "arial.ttf"))
    candidates.extend(["DejaVuSans.ttf", "Arial.ttf"])
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size=max(1, size))
        except OSError:
            continue
    return ImageFont.load_default()


def clip_bbox(values: list[float], width: int, height: int) -> list[int]:
    x0, y0, x1, y1 = values
    x0 = max(0, min(width - 1, x0))
    y0 = max(0, min(height - 1, y0))
    x1 = max(x0 + 1, min(width, x1))
    y1 = max(y0 + 1, min(height, y1))
    return [round(x0), round(y0), round(x1), round(y1)]


def extract_text_rows(
    sanitized_svg: bytes,
    width: int,
    height: int,
    *,
    page_index: int = 0,
) -> list[dict[str, Any]]:
    root = ET.fromstring(sanitized_svg)
    view_box = [float(value) for value in NUMBER_RE.findall(root.get("viewBox") or "")]
    if len(view_box) == 4:
        min_x, min_y, view_w, view_h = view_box
    else:
        min_x = min_y = 0.0
        view_w = parse_number(root.get("width"), float(width))
        view_h = parse_number(root.get("height"), float(height))
    scale_x = width / view_w
    scale_y = height / view_h
    rows: list[dict[str, Any]] = []
    for node in root.iter():
        if local_name(node.tag) != "text":
            continue
        text = " ".join("".join(node.itertext()).split())
        if not text:
            continue
        x = (parse_number(node.get("x")) - min_x) * scale_x
        y = (parse_number(node.get("y")) - min_y) * scale_y
        font_px = max(1, round(parse_number(node.get("font-size"), 12.0) * scale_y))
        family = node.get("font-family") or "Arial"
        font = load_font(font_px, family)
        try:
            left, top, right, bottom = font.getbbox(text, anchor="ls")
        except TypeError:
            left, top, right, bottom = font.getbbox(text)
            top -= font_px
            bottom -= font_px
        anchor = (node.get("text-anchor") or "start").lower()
        text_width = right - left
        if anchor == "middle":
            x -= text_width / 2
        elif anchor == "end":
            x -= text_width
        bbox = clip_bbox([x + left, y + top, x + right, y + bottom], width, height)
        color = (node.get("fill") or "#000000").lstrip("#")
        try:
            color_value = int(color, 16)
        except ValueError:
            color_value = 0
        rows.append(
            {
                "page": page_index,
                "text": text,
                "bbox_px": bbox,
                "image_width_px": width,
                "image_height_px": height,
                "bbox_coordinate_space": "rendered_image_px",
                "bbox_source": "legacy_svg_style_and_font_metric_estimate",
                "font": family,
                "size": font_px,
                "flags": 0,
                "color": color_value,
            }
        )
    return rows


def image_stats(path: Path) -> dict[str, float]:
    with Image.open(path) as image:
        grayscale = image.convert("L")
        histogram = grayscale.histogram()
        total = image.width * image.height
        mean = sum(index * count for index, count in enumerate(histogram)) / total
        return {
            "black_fraction": sum(histogram[:16]) / total,
            "white_fraction": sum(histogram[246:]) / total,
            "mean_luma": mean,
        }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a legacy SVG with modern text")
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True, help="Source SVG relative to root")
    parser.add_argument("--output", required=True, help="Rendered PNG relative to root")
    parser.add_argument("--textlayer-output", required=True, help="JSONL output relative to root")
    parser.add_argument("--report", required=True, help="JSON report relative to root")
    parser.add_argument("--sanitized-svg-output", help="Optional sanitized SVG evidence path")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--page-index", type=int, default=0)
    parser.add_argument("--chrome-path")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    source = root / args.input
    output = root / args.output
    textlayer_output = root / args.textlayer_output
    report_path = root / args.report
    source_bytes = source.read_bytes()
    width, height = target_pixel_size(source_bytes, args.dpi)
    sanitized, sanitization = sanitize_svg(
        source_bytes,
        pixel_width=width,
        pixel_height=height,
    )
    render = render_with_chromium(
        sanitized,
        output,
        width,
        height,
        chrome_path=args.chrome_path,
    )
    rows = extract_text_rows(sanitized, width, height, page_index=args.page_index)
    write_jsonl(textlayer_output, rows)
    sanitized_path = root / args.sanitized_svg_output if args.sanitized_svg_output else None
    if sanitized_path:
        sanitized_path.parent.mkdir(parents=True, exist_ok=True)
        sanitized_path.write_bytes(sanitized)
    report = {
        "valid": bool(rows) and all(
            row["bbox_px"][2] > row["bbox_px"][0]
            and row["bbox_px"][3] > row["bbox_px"][1]
            for row in rows
        ),
        "source": args.input,
        "source_sha256": sha256_bytes(source_bytes),
        "output": args.output,
        "output_sha256": sha256_file(output),
        "textlayer_output": args.textlayer_output,
        "sanitized_svg_output": args.sanitized_svg_output or "",
        "sanitized_svg_sha256": sha256_bytes(sanitized),
        "dpi": args.dpi,
        "width": width,
        "height": height,
        "text_spans": len(rows),
        "sanitization": sanitization,
        "render": render,
        "image_stats": image_stats(output),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
