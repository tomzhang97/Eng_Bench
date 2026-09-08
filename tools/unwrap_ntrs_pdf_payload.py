#!/usr/bin/env python3
"""Extract a validated PDF from a legacy NASA NTRS download envelope."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


PDF_SIGNATURE = b"%PDF-"
PDF_EOF = b"%%EOF"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def unwrap_payload(data: bytes, max_prefix_bytes: int = 4096) -> tuple[bytes, dict[str, Any]]:
    signature_offset = data.find(PDF_SIGNATURE)
    if signature_offset < 0:
        raise ValueError("PDF signature not found")
    if signature_offset > max_prefix_bytes:
        raise ValueError(
            f"PDF signature offset {signature_offset} exceeds maximum {max_prefix_bytes}"
        )
    pdf = data[signature_offset:]
    if PDF_EOF not in pdf[-2048:]:
        raise ValueError("PDF EOF marker not found near end of payload")

    header = pdf[:4096]
    linearized_length_match = re.search(rb"/L\s+(\d+)\b", header)
    declared_length = (
        int(linearized_length_match.group(1)) if linearized_length_match else None
    )
    if declared_length is not None and declared_length != len(pdf):
        raise ValueError(
            f"linearized PDF length mismatch: declared {declared_length}, found {len(pdf)}"
        )

    report = {
        "valid": True,
        "raw_bytes": len(data),
        "raw_sha256": sha256(data),
        "prefix_bytes_removed": signature_offset,
        "pdf_bytes": len(pdf),
        "pdf_sha256": sha256(pdf),
        "pdf_version": pdf[:8].decode("ascii", errors="replace"),
        "linearized_declared_length": declared_length,
        "linearized_length_matches": declared_length is None or declared_length == len(pdf),
        "gold_rows_added": 0,
    }
    return pdf, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--max-prefix-bytes", type=int, default=4096)
    args = parser.parse_args()

    if args.input.resolve() == args.output.resolve():
        parser.error("--input and --output must be different paths")
    data = args.input.read_bytes()
    pdf, report = unwrap_payload(data, max_prefix_bytes=args.max_prefix_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(pdf)
    report.update(
        input_path=args.input.as_posix(),
        output_path=args.output.as_posix(),
    )
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
