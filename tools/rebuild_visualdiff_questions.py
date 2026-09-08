#!/usr/bin/env python3
"""Synchronize visualdiff question answers from visualdiff pair descriptions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def rebuild_questions(
    pairs: list[dict[str, Any]], questions: list[dict[str, Any]], keep_missing: bool = False
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    pairs_by_id = {str(pair.get("pair_id", "")): pair for pair in pairs}
    rebuilt: list[dict[str, Any]] = []
    stats = {"updated": 0, "missing_pair": 0, "dropped_missing_pair": 0}

    for question in questions:
        out = dict(question)
        pair = pairs_by_id.get(str(question.get("pair_id", "")))
        if pair is None:
            stats["missing_pair"] += 1
            if not keep_missing:
                stats["dropped_missing_pair"] += 1
                continue
            rebuilt.append(out)
            continue

        out["answer_text"] = pair.get("change_desc_gt", "")
        out["split"] = pair.get("split", out.get("split", "test"))
        if pair.get("desc_source"):
            out["desc_source"] = pair["desc_source"]
        stats["updated"] += 1
        rebuilt.append(out)

    return rebuilt, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild visualdiff question answers from pairs")
    parser.add_argument(
        "--pairs",
        default="visualdiff/annotations/visualdiff_pairs.jsonl",
        help="Input visualdiff pairs JSONL",
    )
    parser.add_argument(
        "--questions",
        default="visualdiff/annotations/visualdiff_questions.jsonl",
        help="Input/output visualdiff questions JSONL",
    )
    parser.add_argument("--output", default=None, help="Optional output path; defaults to --questions")
    parser.add_argument(
        "--keep-missing",
        action="store_true",
        help="Keep questions whose pair_id no longer exists. Default drops quarantined/missing pairs.",
    )
    args = parser.parse_args()

    pairs = load_jsonl(Path(args.pairs))
    questions = load_jsonl(Path(args.questions))
    rebuilt, stats = rebuild_questions(pairs, questions, keep_missing=args.keep_missing)
    output = Path(args.output) if args.output else Path(args.questions)
    save_jsonl(output, rebuilt)

    print(f"[OK] Rebuilt {stats['updated']} visualdiff questions in {output}")
    if stats["missing_pair"]:
        print(f"[WARN] Questions without matching pair: {stats['missing_pair']}")
        print(f"[WARN] Dropped missing-pair questions: {stats['dropped_missing_pair']}")
        if args.keep_missing:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
