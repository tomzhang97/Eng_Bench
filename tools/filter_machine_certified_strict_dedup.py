#!/usr/bin/env python3
"""Hold strict QA-key duplicates from a machine-certified preview.

Active Gold always wins. Candidate rows are considered in frozen preview order,
and only the first unseen strict validator key is retained. The tool never edits
active data and preserves every held source row in a diagnostic JSONL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import file_sha256
from benchmark_utils import dedup_key


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("source_candidate_id") or "").strip()


def select_strict_unique_item_ids(
    active_unified: list[dict[str, Any]],
    candidate_unified: list[dict[str, Any]],
) -> tuple[set[str], dict[str, str]]:
    seen: dict[tuple[Any, ...], str] = {}
    for row in active_unified:
        seen.setdefault(dedup_key(row), str(row.get("id") or "active"))
    kept: set[str] = set()
    held: dict[str, str] = {}
    for row in candidate_unified:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        item_id = str(metadata.get("item_id") or "").strip()
        key = dedup_key(row)
        if key in seen:
            held[item_id] = seen[key]
            continue
        seen[key] = str(row.get("id") or item_id)
        kept.add(item_id)
    return kept, held


def active_hashes(root: Path) -> dict[str, str]:
    paths = (
        "visualdiff/annotations/visualdiff_pairs.jsonl",
        "visualdiff/annotations/visualdiff_questions.jsonl",
        "microtext/annotations/microtext_items.jsonl",
        "microtext/annotations/microtext_questions.jsonl",
        "eng_bench.jsonl",
    )
    return {path: file_sha256(root / path) for path in paths}


def build_filter(
    root: Path,
    certified_input: Path,
    preview_dir: Path,
    output_dir: Path,
    *,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    certified_input = resolve(root, certified_input)
    preview_dir = resolve(root, preview_dir)
    output_dir = resolve(root, output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    before = active_hashes(root)

    preview_report_path = preview_dir / "promotion_preview_report.json"
    preview_unified_path = preview_dir / "eng_bench_preview.jsonl"
    prepared_items_path = preview_dir / "prepared_microtext_items.jsonl"
    report = json.loads(preview_report_path.read_text(encoding="utf-8"))
    all_unified = read_jsonl(preview_unified_path)
    prepared_items = read_jsonl(prepared_items_path)
    certified_rows = read_jsonl(certified_input)
    active_count = int((report.get("counts") or {}).get("active_gold_rows") or 0)
    candidate_unified = all_unified[active_count:]
    active_unified = all_unified[:active_count]
    issues: list[str] = []
    if report.get("active_gold_modified") is not False:
        issues.append("source_preview_modified_active_gold")
    if len(candidate_unified) != len(prepared_items):
        issues.append("candidate_unified_prepared_count_mismatch")
    if len(prepared_items) != len(certified_rows):
        issues.append("prepared_certified_count_mismatch")

    item_to_candidate = {
        str(row.get("item_id") or ""): str(row.get("source_candidate_id") or "")
        for row in prepared_items
    }
    if len(item_to_candidate) != len(prepared_items) or "" in item_to_candidate:
        issues.append("prepared_item_identity_invalid")
    kept_items, held_items = select_strict_unique_item_ids(active_unified, candidate_unified)
    kept_candidates = {item_to_candidate.get(item_id, "") for item_id in kept_items}
    held_candidates = {
        item_to_candidate.get(item_id, ""): collision
        for item_id, collision in held_items.items()
    }
    if "" in kept_candidates or "" in held_candidates:
        issues.append("candidate_mapping_missing")

    input_ids = [candidate_id(row) for row in certified_rows]
    if len(input_ids) != len(set(input_ids)) or any(not value for value in input_ids):
        issues.append("certified_input_identity_invalid")
    filtered = [row for row in certified_rows if candidate_id(row) in kept_candidates]
    held: list[dict[str, Any]] = []
    for row in certified_rows:
        identity = candidate_id(row)
        if identity not in held_candidates:
            continue
        output = dict(row)
        output["machine_hold_reason"] = "strict_duplicate_qa_key"
        output["strict_duplicate_collides_with"] = held_candidates[identity]
        output["promotion_state"] = "machine_certified_duplicate_hold"
        output["safe_to_merge_gold"] = False
        held.append(output)
    if len(filtered) + len(held) != len(certified_rows):
        issues.append("filter_partition_incomplete")

    filtered_path = output_dir / "machine_certified_strict_unique_balance_deferred.jsonl"
    held_path = output_dir / "machine_certified_strict_duplicate_hold.jsonl"
    write_jsonl(filtered_path, filtered if not issues else [])
    write_jsonl(held_path, held)
    after = active_hashes(root)
    output = {
        "schema": "eng_bench_machine_certification_strict_dedup_v1",
        "date_label": date_label,
        "goal": "Gold v2.0 Global",
        "mode": "read_only_strict_validator_dedup",
        "input_rows": len(certified_rows),
        "strict_unique_rows": len(filtered) if not issues else 0,
        "strict_duplicate_holds": len(held),
        "active_gold_modified": before != after,
        "issues": issues,
        "inputs": {
            "certified": display(root, certified_input),
            "certified_sha256": file_sha256(certified_input),
            "preview_report": display(root, preview_report_path),
            "preview_report_sha256": file_sha256(preview_report_path),
            "preview_unified_sha256": file_sha256(preview_unified_path),
        },
        "artifacts": {
            "strict_unique": {
                "path": display(root, filtered_path),
                "sha256": file_sha256(filtered_path),
            },
            "duplicate_hold": {
                "path": display(root, held_path),
                "sha256": file_sha256(held_path),
            },
        },
        "active_hashes_before": before,
        "active_hashes_after": after,
    }
    write_json(output_dir / "strict_dedup_report.json", output)
    (output_dir / "strict_dedup_report.md").write_text(
        "\n".join(
            [
                "# Machine Certification Strict Dedup",
                "",
                "**Goal:** Gold v2.0 Global",
                "",
                f"- Input certified rows: `{len(certified_rows)}`",
                f"- Strict-unique balance-deferred rows: `{len(filtered) if not issues else 0}`",
                f"- Duplicate holds: `{len(held)}`",
                f"- Active Gold modified: `{str(before != after).lower()}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hold strict QA-key duplicates from a machine-certified preview."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--certified-input", type=Path, required=True)
    parser.add_argument("--preview-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    report = build_filter(
        args.root.resolve(),
        args.certified_input,
        args.preview_dir,
        args.output_dir,
        date_label=args.date_label,
    )
    print(
        json.dumps(
            {
                "strict_unique_rows": report["strict_unique_rows"],
                "strict_duplicate_holds": report["strict_duplicate_holds"],
                "active_gold_modified": report["active_gold_modified"],
                "issues": report["issues"],
            },
            sort_keys=True,
        )
    )
    return 1 if args.require_clean and report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
