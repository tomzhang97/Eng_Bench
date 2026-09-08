#!/usr/bin/env python3
"""Partition reviewed human-return rows into promotion and hold artifacts."""
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


PROMOTABLE_STATUS_ALIASES = {
    "accept": "accepted",
    "accepted": "accepted",
    "edit": "edited",
    "edited": "edited",
}
VALID_SPLITS = {"train", "dev", "test"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def row_key(task: str, row: dict[str, Any]) -> str:
    if task == "microtext":
        return str(row.get("doc_id") or "")
    return str(row.get("project_id") or "")


def row_id(task: str, row: dict[str, Any]) -> str:
    if task == "microtext":
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id:
            return candidate_id
        return "|".join(
            [
                str(row.get("doc_id") or ""),
                str(row.get("page_index") or 0),
                json.dumps(row.get("bbox") or [], separators=(",", ":")),
                str(row.get("target_text") or row.get("proposed_text") or ""),
            ]
        )
    return str(row.get("pair_id") or "")


def expand_inputs(root: Path, patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        candidate = Path(pattern)
        absolute_pattern = str(candidate if candidate.is_absolute() else root / candidate)
        matches = [Path(value) for value in glob.glob(absolute_pattern, recursive=True)]
        if not matches and Path(absolute_pattern).is_file():
            matches = [Path(absolute_pattern)]
        paths.extend(path for path in matches if path.is_file())
    return sorted(dict.fromkeys(path.resolve() for path in paths))


def curate_rows(
    task: str,
    inputs: list[Path],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    task_policy = policy.get(task) or {}
    allow = {str(key): str(value) for key, value in (task_policy.get("allow") or {}).items()}
    invalid_splits = sorted({value for value in allow.values() if value not in VALID_SPLITS})
    if invalid_splits:
        raise ValueError(f"invalid split values in policy: {invalid_splits}")

    hold_reason_by_key = {
        str(key): str(value)
        for key, value in (task_policy.get("hold_reason_by_key") or {}).items()
    }
    hold_reason_by_row_id = {
        str(key): str(value)
        for key, value in (task_policy.get("hold_reason_by_row_id") or {}).items()
    }
    default_hold_reason = str(
        task_policy.get("default_hold_reason") or "not_release_allowlisted"
    )

    promoted: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    stats: Counter[str] = Counter()
    promoted_by_key: Counter[str] = Counter()
    held_by_key: Counter[str] = Counter()
    held_by_reason: Counter[str] = Counter()
    promoted_by_split: Counter[str] = Counter()

    for path in inputs:
        for source_row in read_jsonl(path):
            row = dict(source_row)
            status = str(
                row.get("human_review_status")
                or row.get("human_status")
                or row.get("review_status")
                or row.get("status")
                or ""
            ).strip().lower()
            stats[f"review_status:{status or 'blank'}"] += 1
            normalized_status = PROMOTABLE_STATUS_ALIASES.get(status)
            if not normalized_status:
                stats["ignored_non_promotable_status"] += 1
                continue

            key = row_key(task, row)
            stable_id = row_id(task, row)
            row["integration_normalized_review_status"] = normalized_status
            row["integration_source_file"] = path.as_posix()
            row["integration_policy_key"] = key

            if stable_id in hold_reason_by_row_id:
                reason = hold_reason_by_row_id[stable_id]
            elif not key or not stable_id:
                reason = "missing_release_key_or_row_id"
            elif stable_id in seen_ids:
                reason = "duplicate_return_row_id"
            elif key not in allow:
                reason = hold_reason_by_key.get(key, default_hold_reason)
            else:
                reason = ""

            if reason:
                row["integration_hold_reason"] = reason
                held.append(row)
                held_by_key[key or "<missing>"] += 1
                held_by_reason[reason] += 1
                stats["held"] += 1
                continue

            seen_ids.add(stable_id)
            row["integration_target_split"] = allow[key]
            promoted.append(row)
            promoted_by_key[key] += 1
            promoted_by_split[allow[key]] += 1
            stats["promoted"] += 1

    report = {
        "task": task,
        "input_files": [path.as_posix() for path in inputs],
        "input_file_count": len(inputs),
        "policy_allow_keys": sorted(allow),
        "policy_allow_key_count": len(allow),
        "promoted_rows": len(promoted),
        "held_rows": len(held),
        "promoted_by_key": dict(sorted(promoted_by_key.items())),
        "promoted_by_split": dict(sorted(promoted_by_split.items())),
        "held_by_key": dict(sorted(held_by_key.items())),
        "held_by_reason": dict(sorted(held_by_reason.items())),
        "stats": dict(sorted(stats.items())),
    }
    return promoted, held, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Curate reviewed human-return rows through an explicit release policy."
    )
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--task", required=True, choices=("microtext", "visualdiff"))
    parser.add_argument(
        "--reviewed",
        action="append",
        required=True,
        help="Reviewed JSONL path or glob; may be repeated.",
    )
    parser.add_argument("--policy", required=True, help="Release-policy JSON path.")
    parser.add_argument("--output-promote", required=True)
    parser.add_argument("--output-hold", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    policy_path = Path(args.policy)
    if not policy_path.is_absolute():
        policy_path = root / policy_path
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    inputs = expand_inputs(root, args.reviewed)
    if not inputs:
        raise ValueError("no reviewed JSONL inputs matched")

    promoted, held, report = curate_rows(args.task, inputs, policy)
    output_promote = root / args.output_promote
    output_hold = root / args.output_hold
    report_path = root / args.report
    write_jsonl(output_promote, promoted)
    write_jsonl(output_hold, held)
    write_json(report_path, report)

    print(f"[OK] Curated {args.task} human-return rows")
    print(f"  promoted: {len(promoted)} -> {output_promote}")
    print(f"  held: {len(held)} -> {output_hold}")
    print(f"  report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
