#!/usr/bin/env python3
"""Check that Eng_Bench split files do not leak documents or source families."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


SPLITS = ("train", "dev", "test")


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


def check_prefix(root: Path, prefix: str) -> list[str]:
    seen: dict[str, str] = {}
    errors: list[str] = []
    for split in SPLITS:
        split_path = root / "splits" / f"{prefix}_{split}.txt"
        local_seen: set[str] = set()
        for entry in read_split_file(split_path):
            if entry in local_seen:
                errors.append(f"{prefix}: {entry} appears more than once in {split}")
            local_seen.add(entry)
            if entry in seen and seen[entry] != split:
                errors.append(f"{prefix}: {entry} appears in both {seen[entry]} and {split}")
            else:
                seen[entry] = split
    return errors


def read_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def manifest_assignments(root: Path) -> list[dict[str, str]]:
    manifest = read_manifest(root / "manifest.jsonl")
    docs = {
        str(row["doc_id"]): row
        for row in manifest
        if row.get("type") == "doc" and row.get("doc_id")
    }
    pairs = {
        str(row["pair_id"]): row
        for row in manifest
        if row.get("type") == "pair" and row.get("pair_id")
    }
    assignments: list[dict[str, str]] = []

    for split in SPLITS:
        for doc_id in read_split_file(root / "splits" / f"microtext_{split}.txt"):
            doc = docs.get(doc_id)
            if not doc:
                continue
            assignments.append(
                {
                    "task": "microtext",
                    "split": split,
                    "split_entry": doc_id,
                    "doc_id": doc_id,
                    "sha256": str(doc.get("sha256") or ""),
                    "source_candidate_id": str(doc.get("source_candidate_id") or ""),
                    "same_model_id": str(doc.get("same_model_id") or ""),
                    "source_url": str(doc.get("source_url") or ""),
                }
            )

        for pair_id in read_split_file(root / "splits" / f"visualdiff_{split}.txt"):
            pair = pairs.get(pair_id)
            if not pair:
                continue
            for field in ("from_doc_id", "to_doc_id"):
                doc_id = str(pair.get(field) or "")
                doc = docs.get(doc_id)
                if not doc:
                    continue
                assignments.append(
                    {
                        "task": "visualdiff",
                        "split": split,
                        "split_entry": pair_id,
                        "doc_id": doc_id,
                        "sha256": str(doc.get("sha256") or ""),
                        "source_candidate_id": str(doc.get("source_candidate_id") or ""),
                        "same_model_id": str(doc.get("same_model_id") or ""),
                        "source_url": str(doc.get("source_url") or ""),
                    }
                )
    return assignments


def check_manifest_families(root: Path) -> list[str]:
    assignments = manifest_assignments(root)
    errors: list[str] = []
    fields = (
        ("doc_id", "document"),
        ("sha256", "identical source hash"),
        ("source_candidate_id", "source candidate"),
        ("same_model_id", "same-model family"),
        ("source_url", "source URL"),
    )
    for field, label in fields:
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for assignment in assignments:
            value = assignment[field]
            if value:
                grouped[value].append(assignment)
        for value, rows in sorted(grouped.items()):
            splits = sorted({row["split"] for row in rows})
            if len(splits) <= 1:
                continue
            evidence = ", ".join(
                sorted(
                    {
                        f"{row['task']}:{row['split']}:{row['split_entry']}:{row['doc_id']}"
                        for row in rows
                    }
                )
            )
            errors.append(
                f"{label} {value} appears across {','.join(splits)} ({evidence})"
            )
    return errors


def check_leakage(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    errors.extend(check_prefix(root, "visualdiff"))
    errors.extend(check_prefix(root, "microtext"))
    errors.extend(check_manifest_families(root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Eng_Bench split leakage")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    args = parser.parse_args()

    errors = check_leakage(Path(args.root))
    if errors:
        print("[FAIL] Split leakage detected")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("[OK] No split leakage detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
