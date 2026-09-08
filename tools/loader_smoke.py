#!/usr/bin/env python3
"""Smoke test the public Eng_Bench unified loader."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engbench import load_eng_bench  # noqa: E402


def sorted_counts(counter: Counter) -> dict[str, int]:
    return {str(key): counter[key] for key in sorted(counter)}


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(rows),
        "by_task": sorted_counts(Counter(str(row.get("task", "unknown")) for row in rows)),
        "by_split": sorted_counts(Counter(str(row.get("split", "unknown")) for row in rows)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test Eng_Bench loader.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", default="results/health/loader_smoke.json")
    args = parser.parse_args(argv)

    rows = load_eng_bench(args.root, verify_images=True)
    summary = summarize_rows(rows)
    output = Path(args.root) / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] Loaded {summary['total']} rows")
    print(f"[OK] Rows by task: {summary['by_task']}")
    print(f"[OK] Rows by split: {summary['by_split']}")
    print(f"[OK] Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
