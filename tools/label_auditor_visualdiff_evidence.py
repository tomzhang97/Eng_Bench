#!/usr/bin/env python3
"""Add explicit OLD/NEW headers to one auditor's VisualDiff evidence panels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def center_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    left, top, right, bottom = box
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = left + max(0, (right - left - width) // 2)
    y = top + max(0, (bottom - top - height) // 2) - bounds[1]
    draw.text((x, y), text, font=font, fill="white")


def label_panel(source: Path, destination: Path) -> dict[str, Any]:
    panel = Image.open(source).convert("RGB")
    width, height = panel.size
    if width < 80 or height < 20:
        raise ValueError(f"evidence panel is too small: {source} ({width}x{height})")

    split = width // 2
    header = max(36, min(52, width // 7))
    font = load_font(max(16, min(24, header // 2)))
    canvas = Image.new("RGB", (width, height + header), "white")
    canvas.paste(panel, (0, header))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, split - 1, header - 1), fill="#8B1E1E")
    draw.rectangle((split, 0, width - 1, header - 1), fill="#155E63")
    center_text(draw, (0, 0, split, header), "OLD 旧版", font)
    center_text(draw, (split, 0, width, header), "NEW 新版", font)
    draw.line((split, 0, split, height + header - 1), fill="#111111", width=3)
    draw.rectangle((0, 0, width - 1, height + header - 1), outline="#111111", width=2)

    # Excel silently downsamples very wide images when saving. Keeping the
    # labeled panel below this width preserves exact evidence bytes in XLSX.
    if canvas.width > 600:
        scaled_height = max(1, round(canvas.height * 600 / canvas.width))
        canvas = canvas.resize((600, scaled_height), Image.Resampling.LANCZOS)

    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG", optimize=True)
    return {
        "source_path": source.resolve().as_posix(),
        "image_path": destination.resolve().as_posix(),
        "source_size": [width, height],
        "output_size": list(canvas.size),
        "split_x": split,
        "header_height": header,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    payload_path = resolve(root, args.payload).resolve()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    auditor = next(
        (
            item
            for item in payload["auditors"]
            if int(item["number"]) == args.auditor_number
        ),
        None,
    )
    if auditor is None:
        raise ValueError(f"auditor {args.auditor_number} is not present in payload")

    output_dir = resolve(root, args.output_dir).resolve()
    rows: list[dict[str, Any]] = []
    for offset, row in enumerate(auditor["rows"]):
        if row.get("task") != "visualdiff":
            continue
        source = resolve(root, str(row["evidence_path"])).resolve()
        destination = output_dir / (
            f"auditor_{args.auditor_number:02d}_excel_{offset + 7:03d}_"
            f"primary_{row['primary_index']}.png"
        )
        image_report = label_panel(source, destination)
        rows.append(
            {
                "auditor_number": args.auditor_number,
                "auditor_row": offset + 1,
                "excel_row": offset + 7,
                "primary_index": str(row["primary_index"]),
                "record_id": row.get("pair_id") or row.get("candidate_id"),
                **image_report,
            }
        )

    output_payload_path: Path | None = None
    if args.output_payload:
        replacements = {row["primary_index"]: row["image_path"] for row in rows}
        for row in auditor["rows"]:
            primary_index = str(row.get("primary_index", ""))
            if primary_index in replacements:
                row["evidence_path"] = replacements[primary_index]
        output_payload_path = resolve(root, args.output_payload).resolve()
        output_payload_path.parent.mkdir(parents=True, exist_ok=True)
        output_payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    report = {
        "goal": "Gold v2.0 Global",
        "payload": payload_path.as_posix(),
        "auditor_number": args.auditor_number,
        "workbook": auditor["workbook"],
        "output_payload": output_payload_path.as_posix() if output_payload_path else None,
        "visualdiff_rows": len(rows),
        "non_visualdiff_rows_modified": 0,
        "gold_rows_modified": 0,
        "rows": rows,
        "valid": len(rows) > 0,
    }
    report_path = resolve(root, args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", default=".")
    result.add_argument("--payload", required=True)
    result.add_argument("--auditor-number", type=int, required=True)
    result.add_argument("--output-dir", required=True)
    result.add_argument("--report", required=True)
    result.add_argument("--output-payload")
    return result


def main() -> None:
    report = build(parser().parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
