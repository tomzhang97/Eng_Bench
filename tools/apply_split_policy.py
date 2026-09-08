#!/usr/bin/env python3
"""Apply family-level split files to Eng_Bench annotation JSONL files."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


SPLITS = ("train", "dev", "test")


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


def read_split_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    entries: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            value = line.strip()
            if value and not value.startswith("#"):
                entries.append(value)
    return entries


def load_split_map(root: Path, prefix: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    duplicates: list[str] = []
    for split in SPLITS:
        for entry in read_split_file(root / "splits" / f"{prefix}_{split}.txt"):
            if entry in mapping:
                duplicates.append(f"{entry}: {mapping[entry]} and {split}")
            mapping[entry] = split
    if duplicates:
        joined = "; ".join(duplicates)
        raise ValueError(f"{prefix} split files contain duplicate entries: {joined}")
    return mapping


def visualdiff_family(row: dict[str, Any]) -> str:
    if row.get("project_id"):
        return str(row["project_id"])
    pair_id = str(row.get("pair_id", ""))
    stem, sep, suffix = pair_id.rpartition("__")
    if sep and suffix.isdigit():
        return stem
    return pair_id


def apply_visualdiff(root: Path) -> dict[str, int]:
    split_by_family = load_split_map(root, "visualdiff")
    pairs_path = root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    questions_path = root / "visualdiff" / "annotations" / "visualdiff_questions.jsonl"

    pairs = load_jsonl(pairs_path)
    pair_split_by_id: dict[str, str] = {}
    missing: Counter[str] = Counter()

    for pair in pairs:
        family = visualdiff_family(pair)
        split = split_by_family.get(family)
        if split is None:
            missing[family] += 1
            continue
        pair["split"] = split
        pair_split_by_id[str(pair.get("pair_id", ""))] = split

    if missing:
        details = ", ".join(f"{family} ({count})" for family, count in sorted(missing.items()))
        raise ValueError(f"visualdiff rows missing split mapping: {details}")

    questions = load_jsonl(questions_path)
    missing_questions: Counter[str] = Counter()
    for question in questions:
        pair_id = str(question.get("pair_id", ""))
        split = pair_split_by_id.get(pair_id)
        if split is None:
            missing_questions[pair_id] += 1
            continue
        question["split"] = split

    if missing_questions:
        details = ", ".join(
            f"{pair_id} ({count})" for pair_id, count in sorted(missing_questions.items())
        )
        raise ValueError(f"visualdiff questions missing pair mapping: {details}")

    save_jsonl(pairs_path, pairs)
    save_jsonl(questions_path, questions)

    annotations_dir = root / "visualdiff" / "annotations"
    for sidecar_name in ("visualdiff_pairs_GOLD.jsonl", "visualdiff_pairs_TODO.jsonl"):
        sidecar_path = annotations_dir / sidecar_name
        if not sidecar_path.exists():
            continue
        sidecar_pairs = load_jsonl(sidecar_path)
        for pair in sidecar_pairs:
            pair["split"] = split_by_family[visualdiff_family(pair)]
        save_jsonl(sidecar_path, sidecar_pairs)

    for sidecar_name in ("visualdiff_questions_GOLD.jsonl", "visualdiff_questions_TODO.jsonl"):
        sidecar_path = annotations_dir / sidecar_name
        if not sidecar_path.exists():
            continue
        sidecar_questions = load_jsonl(sidecar_path)
        for question in sidecar_questions:
            question["split"] = pair_split_by_id[str(question.get("pair_id", ""))]
        save_jsonl(sidecar_path, sidecar_questions)
    return dict(Counter(str(row.get("split", "unknown")) for row in pairs))


def apply_microtext(root: Path) -> dict[str, int]:
    split_by_doc = load_split_map(root, "microtext")
    items_path = root / "microtext" / "annotations" / "microtext_items.jsonl"
    questions_path = root / "microtext" / "annotations" / "microtext_questions.jsonl"

    items = load_jsonl(items_path)
    item_split_by_id: dict[str, str] = {}
    missing: Counter[str] = Counter()

    for item in items:
        doc_id = str(item.get("doc_id", ""))
        split = split_by_doc.get(doc_id)
        if split is None:
            missing[doc_id] += 1
            continue
        item["split"] = split
        item_split_by_id[str(item.get("item_id", ""))] = split

    if missing:
        details = ", ".join(f"{doc_id} ({count})" for doc_id, count in sorted(missing.items()))
        raise ValueError(f"microtext rows missing split mapping: {details}")

    questions = load_jsonl(questions_path)
    missing_questions: Counter[str] = Counter()
    for question in questions:
        split = None
        doc_id = str(question.get("doc_id", ""))
        if doc_id:
            split = split_by_doc.get(doc_id)
        if split is None:
            item_ids = question.get("item_ids") or []
            for item_id in item_ids:
                split = item_split_by_id.get(str(item_id))
                if split:
                    break
        if split is None:
            missing_questions[str(question.get("question_id", ""))] += 1
            continue
        question["split"] = split

    if missing_questions:
        details = ", ".join(
            f"{qid} ({count})" for qid, count in sorted(missing_questions.items())
        )
        raise ValueError(f"microtext questions missing split mapping: {details}")

    save_jsonl(items_path, items)
    save_jsonl(questions_path, questions)
    return dict(Counter(str(row.get("split", "unknown")) for row in items))


def apply_split_policy(root: str | Path) -> dict[str, dict[str, int]]:
    root = Path(root)
    return {
        "visualdiff_pairs_by_split": apply_visualdiff(root),
        "microtext_items_by_split": apply_microtext(root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Eng_Bench split-family policy")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    args = parser.parse_args()

    stats = apply_split_policy(Path(args.root))
    print("[OK] Applied split policy")
    for name, counts in stats.items():
        print(f"{name}: {dict(sorted(counts.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
