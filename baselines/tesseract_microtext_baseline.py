#!/usr/bin/env python3
"""Tesseract OCR microtext baseline for unified Eng_Bench rows.

This script does not use gold answers or gold evidence boxes. It runs OCR on the
page image and selects a candidate token using the row category metadata.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image


TAG_RE = re.compile(r"^[A-Z]{1,5}[- ]?\d+[A-Z0-9./-]*$")
PIPE_RE = re.compile(r"\d+[-/][A-Z]{1,5}[-/]\d+")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def resolve_image(root: Path, row: dict[str, Any]) -> Path | None:
    images = row.get("images") if isinstance(row.get("images"), list) else []
    if not images:
        return None
    path = Path(str(images[0]))
    return path if path.is_absolute() else root / path


def parse_tesseract_tsv(tsv_text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    tokens = []
    for raw in reader:
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(raw.get("conf", "-1"))
            left = int(float(raw.get("left", "0")))
            top = int(float(raw.get("top", "0")))
            width = int(float(raw.get("width", "0")))
            height = int(float(raw.get("height", "0")))
        except ValueError:
            continue
        if conf < 0 or width <= 0 or height <= 0:
            continue
        tokens.append(
            {
                "text": text,
                "confidence": conf,
                "bbox": [left, top, left + width, top + height],
            }
        )
    return tokens


def score_token(token: dict[str, Any], category: str) -> tuple[int, float, int]:
    text = str(token.get("text") or "").strip()
    compact = re.sub(r"[^A-Za-z0-9./+-]", "", text).upper()
    has_digit = any(ch.isdigit() for ch in compact)
    score = 0
    if category in {"pin_label", "equipment_tag", "instrument_tag"}:
        if TAG_RE.match(compact):
            score += 10
        if has_digit:
            score += 3
    elif category == "pipe_line_tag":
        if PIPE_RE.search(compact):
            score += 10
        if "-" in compact or "/" in compact:
            score += 3
    elif category in {"dimension_value", "tolerance_value", "process_value"}:
        if has_digit:
            score += 6
        if any(mark in compact for mark in ("/", "+", "-", ".")):
            score += 2
    elif category == "room_label":
        if text.isupper() or text.istitle():
            score += 4
    else:
        score += int(has_digit)
    return score, float(token.get("confidence", 0.0)), -abs(len(text) - 8)


def pick_token(tokens: list[dict[str, Any]], category: str) -> dict[str, Any] | None:
    if not tokens:
        return None
    return sorted(tokens, key=lambda token: score_token(token, category), reverse=True)[0]


def run_tesseract(image_path: Path, tesseract_cmd: str, timeout: int) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / "page.png"
        Image.open(image_path).save(temp_path)
        cmd = [tesseract_cmd, str(temp_path), "stdout", "--psm", "11", "tsv"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        return []
    return parse_tesseract_tsv(result.stdout)


def predict_rows(
    root: str | Path,
    rows: list[dict[str, Any]],
    tesseract_cmd: str = "tesseract",
    timeout: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    root = Path(root)
    predictions: list[dict[str, Any]] = []
    stats = {
        "rows": len(rows),
        "microtext_rows": 0,
        "predictions": 0,
        "missing_image": 0,
        "empty_ocr": 0,
    }
    for row in rows:
        if row.get("task") != "microtext":
            continue
        stats["microtext_rows"] += 1
        image_path = resolve_image(root, row)
        if image_path is None or not image_path.exists():
            stats["missing_image"] += 1
            continue
        category = str(row_metadata(row).get("category") or "unknown")
        token = pick_token(run_tesseract(image_path, tesseract_cmd, timeout), category)
        if token is None:
            stats["empty_ocr"] += 1
            continue
        predictions.append(
            {
                "id": row.get("id"),
                "answer": token["text"],
                "evidence": [{"image_index": 0, "bbox": token["bbox"]}],
                "metadata": {
                    "model": "tesseract_microtext",
                    "method": "tesseract_full_page_category_token",
                    "category": category,
                    "confidence": token["confidence"],
                },
            }
        )
    stats["predictions"] = len(predictions)
    return predictions, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Tesseract microtext baseline predictions.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl", help="Unified Eng_Bench JSONL")
    parser.add_argument("--split", default="test", help="Split to run, or all")
    parser.add_argument("--output", required=True, help="Output prediction JSONL")
    parser.add_argument("--tesseract-cmd", default="tesseract", help="Tesseract executable")
    parser.add_argument("--timeout", type=int, default=30, help="Per-page OCR timeout seconds")
    args = parser.parse_args(argv)

    if shutil.which(args.tesseract_cmd) is None:
        print(f"[ERR] Tesseract executable not found: {args.tesseract_cmd}")
        return 2

    root = Path(args.root)
    rows = load_jsonl(root / args.input)
    if args.split != "all":
        rows = [row for row in rows if row.get("split") == args.split]
    predictions, stats = predict_rows(root, rows, args.tesseract_cmd, args.timeout)
    write_jsonl(root / args.output, predictions)
    print(f"[OK] Microtext rows: {stats['microtext_rows']}")
    print(f"[OK] Predictions: {stats['predictions']}")
    print(f"[OK] Missing image rows: {stats['missing_image']}")
    print(f"[OK] Empty OCR rows: {stats['empty_ocr']}")
    print(f"[OK] Wrote {root / args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
