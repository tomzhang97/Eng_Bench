#!/usr/bin/env python3
"""Verify the flat copy-ready Eng_Bench human-audit ZIP without rebuilding it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from . import build_minimal_human_audit_delivery as delivery
except ImportError:  # Direct script execution from tools/.
    import build_minimal_human_audit_delivery as delivery


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--payload-json", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-md", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    payload = json.loads(
        resolve(root, args.payload_json).read_text(encoding="utf-8")
    )
    report = delivery.verify_delivery(
        resolve(root, args.delivery_dir).resolve(),
        payload,
        resolve(root, args.zip_path).resolve(),
    )
    if args.report_json:
        path = resolve(root, args.report_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.report_md:
        path = resolve(root, args.report_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(delivery.render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
