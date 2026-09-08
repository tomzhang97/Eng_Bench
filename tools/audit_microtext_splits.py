#!/usr/bin/env python3
"""Audit active microtext split distribution and disjointness."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SPLITS = ("train", "dev", "test")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


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


def split_docs(root: Path) -> dict[str, list[str]]:
    return {
        split: read_split_file(root / "splits" / f"microtext_{split}.txt")
        for split in SPLITS
    }


def manifest_doc_families(root: Path) -> dict[str, str]:
    families: dict[str, str] = {}
    for row in load_jsonl(root / "manifest.jsonl"):
        if row.get("type") != "doc" or row.get("task") != "microtext":
            continue
        doc_id = str(row.get("doc_id", ""))
        family = str(row.get("same_model_id") or doc_id)
        if doc_id:
            families[doc_id] = family
    return families


def audit(root: Path) -> dict[str, Any]:
    items = load_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    questions = load_jsonl(root / "microtext" / "annotations" / "microtext_questions.jsonl")
    docs_by_split = split_docs(root)
    doc_to_split = {
        doc_id: split for split, docs in docs_by_split.items() for doc_id in docs
    }
    families = manifest_doc_families(root)

    item_counts = Counter(str(row.get("split", "unknown")) for row in items)
    question_counts = Counter(str(row.get("split", "unknown")) for row in questions)
    categories_by_split: dict[str, Counter[str]] = defaultdict(Counter)
    docs_by_active_split: dict[str, Counter[str]] = defaultdict(Counter)
    missing_split_rows: list[str] = []
    split_mismatch_rows: list[str] = []

    for row in items:
        item_id = str(row.get("item_id", ""))
        doc_id = str(row.get("doc_id", ""))
        split = str(row.get("split", ""))
        categories_by_split[split][str(row.get("category", "unknown"))] += 1
        docs_by_active_split[split][doc_id] += 1
        expected = doc_to_split.get(doc_id)
        if expected is None:
            missing_split_rows.append(item_id)
        elif expected != split:
            split_mismatch_rows.append(item_id)

    doc_leakage = []
    seen_docs: dict[str, str] = {}
    for split, docs in docs_by_split.items():
        local_seen = set()
        for doc_id in docs:
            if doc_id in local_seen:
                doc_leakage.append(f"{doc_id} appears more than once in {split}")
            local_seen.add(doc_id)
            if doc_id in seen_docs and seen_docs[doc_id] != split:
                doc_leakage.append(f"{doc_id} appears in both {seen_docs[doc_id]} and {split}")
            seen_docs[doc_id] = split

    family_to_splits: dict[str, set[str]] = defaultdict(set)
    for split, docs in docs_by_split.items():
        for doc_id in docs:
            family_to_splits[families.get(doc_id, doc_id)].add(split)
    family_leakage = {
        family: sorted(splits)
        for family, splits in family_to_splits.items()
        if len(splits) > 1
    }

    active_docs = {str(row.get("doc_id", "")) for row in items}
    split_docs_without_items = {
        split: sorted(set(docs) - active_docs) for split, docs in docs_by_split.items()
    }
    active_docs_without_split = sorted(active_docs - set(doc_to_split))

    empty_splits = [split for split in SPLITS if item_counts.get(split, 0) == 0]
    critical_failures = (
        len(doc_leakage)
        + len(family_leakage)
        + len(missing_split_rows)
        + len(split_mismatch_rows)
        + len(active_docs_without_split)
        + len(empty_splits)
    )

    return {
        "items": len(items),
        "questions": len(questions),
        "item_counts_by_split": dict(sorted(item_counts.items())),
        "question_counts_by_split": dict(sorted(question_counts.items())),
        "docs_by_split_file": {split: docs_by_split[split] for split in SPLITS},
        "active_docs_by_split": {
            split: dict(sorted(counter.items()))
            for split, counter in sorted(docs_by_active_split.items())
        },
        "categories_by_split": {
            split: dict(sorted(counter.items()))
            for split, counter in sorted(categories_by_split.items())
        },
        "doc_leakage": doc_leakage,
        "family_leakage": family_leakage,
        "split_docs_without_items": split_docs_without_items,
        "active_docs_without_split": active_docs_without_split,
        "missing_split_rows": missing_split_rows,
        "split_mismatch_rows": split_mismatch_rows,
        "empty_splits": empty_splits,
        "critical_failures": critical_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit microtext split policy")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()

    report = audit(Path(args.root))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["critical_failures"]:
        print(f"[FAIL] {report['critical_failures']} critical microtext split failures")
        return 1
    print("[OK] Microtext split audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
