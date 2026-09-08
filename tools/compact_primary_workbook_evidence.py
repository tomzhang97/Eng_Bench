#!/usr/bin/env python3
"""Create workbook-display PNGs without changing source review evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


MAX_WIDTH = 760
MAX_HEIGHT = 260
PALETTE_COLORS = 128


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_rows(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from payload.get("rows") or []
    specialist = payload.get("specialist") or {}
    yield from specialist.get("visualdiff_english") or []
    yield from specialist.get("microtext_balance") or []


def compact_png(source: Path, output: Path) -> tuple[int, int, int, int]:
    with Image.open(source) as image:
        image = image.convert("RGB")
        original_width, original_height = image.size
        image.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.Resampling.LANCZOS)
        compact = image.quantize(
            colors=PALETTE_COLORS,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        compact.save(output, format="PNG", optimize=True)
        return original_width, original_height, image.width, image.height


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-payload", required=True)
    parser.add_argument("--output-payload", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--report-json", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input_payload).resolve()
    output_path = Path(args.output_payload).resolve()
    evidence_dir = Path(args.evidence_dir).resolve()
    report_path = Path(args.report_json).resolve()
    for path in (output_path, evidence_dir, report_path):
        if path.exists():
            raise FileExistsError(f"output already exists: {path}")

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    rows = list(payload_rows(payload))
    before_bytes = 0
    after_bytes = 0
    dimensions: list[dict[str, int]] = []
    for index, row in enumerate(rows, 1):
        source = Path(str(row.get("evidence_path") or ""))
        if not source.is_file():
            raise FileNotFoundError(f"evidence missing for {row.get('record_id')}: {source}")
        output = evidence_dir / f"evidence_{index:04d}.png"
        original_width, original_height, width, height = compact_png(source, output)
        before_bytes += source.stat().st_size
        after_bytes += output.stat().st_size
        dimensions.append(
            {
                "original_width": original_width,
                "original_height": original_height,
                "width": width,
                "height": height,
            }
        )
        row["source_evidence_path"] = str(source)
        row["source_evidence_sha256"] = row.get("evidence_sha256") or sha256_file(source)
        row["evidence_path"] = str(output)
        row["evidence_sha256"] = sha256_file(output)
        if index % 500 == 0:
            print(f"compacted evidence {index}/{len(rows)}")

    payload["workflow"] = str(payload.get("workflow") or "") + "; compact workbook-display evidence"
    payload.setdefault("safety", {})["source_evidence_preserved"] = True
    payload["inputs"]["source_payload"] = {
        "path": str(input_path),
        "sha256": sha256_file(input_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "status": "PASS",
        "goal": payload.get("goal"),
        "rows": len(rows),
        "input_payload": str(input_path),
        "input_payload_sha256": sha256_file(input_path),
        "output_payload": str(output_path),
        "output_payload_sha256": sha256_file(output_path),
        "source_evidence_bytes": before_bytes,
        "workbook_evidence_bytes": after_bytes,
        "size_reduction_percent": round((1 - after_bytes / before_bytes) * 100, 2),
        "maximum_width": max(item["width"] for item in dimensions),
        "maximum_height": max(item["height"] for item in dimensions),
        "source_evidence_preserved": True,
        "gold_rows_modified": 0,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
