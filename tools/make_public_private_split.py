#!/usr/bin/env python3
"""Build public and hidden challenge-test files from Eng_Bench test rows."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from .evaluation_release_guard import assert_release_eligible
except ImportError:
    from evaluation_release_guard import assert_release_eligible


LABEL_FIELDS = {"answer", "answer_text", "evidence", "evidence_ids", "text_gt", "change_desc_gt"}


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def row_id(row: dict[str, Any]) -> str:
    value = row.get("id") or row.get("question_id") or row.get("qid")
    return str(value) if value is not None else ""


def metadata_for(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def strip_labels(row: dict[str, Any], challenge_split: str) -> dict[str, Any]:
    public = {key: value for key, value in row.items() if key not in LABEL_FIELDS}
    metadata = metadata_for(public).copy()
    public["metadata"] = {
        key: value
        for key, value in metadata.items()
        if key not in LABEL_FIELDS and not str(key).endswith("_gt")
    }
    public["challenge_split"] = challenge_split
    return public


def labeled_row(row: dict[str, Any], challenge_split: str) -> dict[str, Any]:
    output = dict(row)
    output["challenge_split"] = challenge_split
    return output


def challenge_bucket(row: dict[str, Any]) -> str:
    metadata = metadata_for(row)
    if row.get("task") == "microtext":
        return "microtext:" + str(metadata.get("category") or row.get("category") or "unknown")
    if row.get("task") == "visualdiff":
        change_type = (
            metadata.get("change_type")
            or metadata.get("change_types")
            or row.get("change_type")
            or row.get("change_types")
            or "unknown"
        )
        if isinstance(change_type, list):
            change_type = ",".join(str(item) for item in change_type)
        return "visualdiff:" + str(change_type)
    return str(row.get("task") or "unknown")


def stable_score(identifier: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}:{identifier}".encode("utf-8")).hexdigest()


def assign_public_private(
    rows: list[dict[str, Any]],
    public_ratio: float = 0.5,
    seed: str = "2026-06-02",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not 0.0 < public_ratio < 1.0:
        raise ValueError("public_ratio must be between 0 and 1")

    test_rows = [row for row in rows if row.get("split") == "test"]
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in test_rows:
        by_bucket[challenge_bucket(row)].append(row)

    public_rows: list[dict[str, Any]] = []
    hidden_rows: list[dict[str, Any]] = []
    bucket_stats: dict[str, dict[str, int]] = {}
    for bucket, bucket_rows in sorted(by_bucket.items()):
        ordered = sorted(bucket_rows, key=lambda row: stable_score(row_id(row), seed))
        public_count = int(round(len(ordered) * public_ratio))
        if len(ordered) > 1:
            public_count = max(1, min(len(ordered) - 1, public_count))
        else:
            public_count = 1
        public_slice = ordered[:public_count]
        hidden_slice = ordered[public_count:]
        public_rows.extend(public_slice)
        hidden_rows.extend(hidden_slice)
        bucket_stats[bucket] = {
            "total": len(ordered),
            "public_test": len(public_slice),
            "hidden_test": len(hidden_slice),
        }

    public_rows.sort(key=row_id)
    hidden_rows.sort(key=row_id)
    stats = {
        "input_rows": len(rows),
        "source_test_rows": len(test_rows),
        "public_test_rows": len(public_rows),
        "hidden_test_rows": len(hidden_rows),
        "public_ratio": public_ratio,
        "seed": seed,
        "bucket_stats": bucket_stats,
        "rows_by_task": dict(Counter(str(row.get("task", "unknown")) for row in test_rows)),
    }
    return public_rows, hidden_rows, stats


def make_split_files(
    root: Path,
    input_path: Path = Path("eng_bench.jsonl"),
    public_ratio: float = 0.5,
    seed: str = "2026-06-02",
    public_inputs_path: Path = Path("release/public_inputs/eng_bench_public_test_inputs.jsonl"),
    hidden_inputs_path: Path = Path("release/public_inputs/eng_bench_hidden_test_inputs.jsonl"),
    hidden_labels_path: Path = Path("release/private_labels/eng_bench_hidden_test_labels.jsonl"),
    manifest_path: Path = Path("release/private_labels/challenge_split_manifest.json"),
) -> dict[str, Any]:
    rows = load_jsonl(root / input_path)
    safety = assert_release_eligible(root, rows)
    public_rows, hidden_rows, stats = assign_public_private(rows, public_ratio=public_ratio, seed=seed)
    stats["known_hold_screening"] = safety

    write_jsonl(root / public_inputs_path, [strip_labels(row, "public_test") for row in public_rows])
    write_jsonl(root / hidden_inputs_path, [strip_labels(row, "hidden_test") for row in hidden_rows])
    write_jsonl(root / hidden_labels_path, [labeled_row(row, "hidden_test") for row in hidden_rows])

    stats["outputs"] = {
        "public_inputs": str(public_inputs_path),
        "hidden_inputs": str(hidden_inputs_path),
        "hidden_labels_private": str(hidden_labels_path),
    }
    manifest_output = root / manifest_path
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create Eng_Bench public/hidden challenge-test files.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl", help="Unified labeled JSONL")
    parser.add_argument("--public-ratio", type=float, default=0.5, help="Approximate public-test ratio")
    parser.add_argument("--seed", default="2026-06-02", help="Deterministic assignment seed")
    parser.add_argument("--public-inputs", default="release/public_inputs/eng_bench_public_test_inputs.jsonl")
    parser.add_argument("--hidden-inputs", default="release/public_inputs/eng_bench_hidden_test_inputs.jsonl")
    parser.add_argument("--hidden-labels", default="release/private_labels/eng_bench_hidden_test_labels.jsonl")
    parser.add_argument("--manifest", default="release/private_labels/challenge_split_manifest.json")
    args = parser.parse_args(argv)

    root = Path(args.root)
    try:
        stats = make_split_files(
            root=root,
            input_path=Path(args.input),
            public_ratio=args.public_ratio,
            seed=args.seed,
            public_inputs_path=Path(args.public_inputs),
            hidden_inputs_path=Path(args.hidden_inputs),
            hidden_labels_path=Path(args.hidden_labels),
            manifest_path=Path(args.manifest),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("[BLOCKED] " + str(error))
        return 1
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
