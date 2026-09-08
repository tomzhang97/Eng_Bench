#!/usr/bin/env python3
"""Verify the flat ultrafast Eng_Bench audit ZIP without rebuilding it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from . import build_ultrafast_human_audit_delivery as delivery
except ImportError:  # Direct script execution from tools/.
    import build_ultrafast_human_audit_delivery as delivery


def render_markdown(report: dict[str, Any]) -> str:
    zip_report = report.get("zip") or {}
    return "\n".join(
        [
            "# Ultrafast Human Audit Delivery Verification",
            "",
            f"- Goal: `{report.get('goal', 'Gold v2.0 Global')}`",
            f"- Valid: `{str(report.get('valid', False)).lower()}`",
            f"- Primary rows: `{report.get('primary_rows', 0)}`",
            f"- Workbooks: `{report.get('workbook_count', 0)}`",
            f"- Embedded images: `{report.get('embedded_images', 0)}`",
            f"- Unique double-review rows: `{report.get('unique_double_review_rows', 0)}`",
            f"- ZIP entries: `{zip_report.get('entry_count', 0)}`",
            f"- Flat root files only: `{str(zip_report.get('root_files_only', False)).lower()}`",
            f"- Nested ZIP files: `{zip_report.get('nested_zip_files', 0)}`",
            f"- ZIP SHA-256: `{zip_report.get('sha256', '')}`",
            f"- Issues: `{len(report.get('issues', []))}`",
            f"- Gold rows modified: `{report.get('gold_rows_modified', 0)}`",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--payload-json", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-md", type=Path)
    return parser.parse_args()


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    delivery_dir = resolve(root, args.delivery_dir).resolve()
    payload_path = resolve(root, args.payload_json).resolve()
    zip_path = resolve(root, args.zip_path).resolve()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    report = delivery.verify_delivery(delivery_dir, payload, zip_path)

    if args.report_json:
        report_json = resolve(root, args.report_json)
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.report_md:
        report_md = resolve(root, args.report_md)
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
