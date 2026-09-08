#!/usr/bin/env python3
"""Normalize MicroText review questions from the final category."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.apply_microtext_visual_decisions import QUESTION_BY_CATEGORY


def normalize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    changed = 0
    categories: Counter[str] = Counter()
    for source in rows:
        row = dict(source)
        category = str(row.get("category") or "").strip()
        if category not in QUESTION_BY_CATEGORY:
            raise ValueError(f"unsupported or missing MicroText category: {category!r}")
        expected = QUESTION_BY_CATEGORY[category]
        previous = str(row.get("question_text") or "")
        if previous != expected:
            row["machine_question_normalized_from"] = previous
            row["question_text"] = expected
            changed += 1
        categories[category] += 1
        normalized.append(row)
    report = {
        "goal": "Gold v2.0 Global",
        "input_rows": len(rows),
        "output_rows": len(normalized),
        "changed_questions": changed,
        "unchanged_questions": len(rows) - changed,
        "categories": dict(sorted(categories.items())),
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
    }
    return normalized, report


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args(argv)
    rows, report = normalize_rows(read_jsonl(args.input))
    write_jsonl(args.output, rows)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
