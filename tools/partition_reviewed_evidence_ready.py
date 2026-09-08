#!/usr/bin/env python3
"""Partition reviewed rows against the current machine evidence-hold registry."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from . import candidate_evidence_holds
    from . import preview_reviewed_gold_promotion as preview
except ImportError:
    import candidate_evidence_holds
    import preview_reviewed_gold_promotion as preview


def partition(rows: list[dict], task: str, held_ids: set[str]) -> tuple[list[dict], list[dict]]:
    ready, holds = [], []
    seen = set()
    for row in rows:
        identity = preview.identity_for(row, task)
        if not identity or identity in seen:
            raise ValueError(f"missing or duplicate reviewed identity: {identity}")
        seen.add(identity)
        if candidate_evidence_holds.is_evidence_held(row, held_ids):
            holds.append({
                "task": task,
                "identity": identity,
                "hold_reason": "unresolved_machine_evidence_hold",
                "row": row,
            })
        else:
            ready.append(row)
    return ready, holds


def build(root: Path, inputs: dict[str, Path], output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(root / "derived/quality"):
        raise ValueError("output must be a new directory under derived/quality")
    held_ids = candidate_evidence_holds.evidence_hold_ids(root)
    pointer = root / candidate_evidence_holds.CURRENT_HOLDS
    before = preview.active_hashes(root)
    input_hashes = {}
    ready_by_task, all_holds = {}, []
    for task in ("microtext", "visualdiff"):
        path = inputs.get(task)
        rows = []
        if path is not None:
            path = path.resolve()
            if not path.is_file() or not path.is_relative_to(root):
                raise ValueError(f"reviewed input must be a file inside root: {path}")
            input_hashes[str(path)] = preview.file_sha256(path)
            rows = preview.read_jsonl(path)
        ready_by_task[task], holds = partition(rows, task, held_ids)
        all_holds.extend(holds)
    if pointer.is_file():
        input_hashes[str(pointer)] = preview.file_sha256(pointer)
    output.mkdir(parents=True)
    artifacts = {
        "microtext_reviewed.jsonl": ready_by_task["microtext"],
        "visualdiff_reviewed.jsonl": ready_by_task["visualdiff"],
        "evidence_holds.jsonl": all_holds,
    }
    for name, rows in artifacts.items():
        preview.write_jsonl(output / name, rows)
    after = preview.active_hashes(root)
    if before != after:
        raise ValueError("active Gold changed during evidence partition")
    report = {
        "goal": "Gold v2.0 Global",
        "status": "PASS",
        "input_rows": sum(len(rows) for rows in ready_by_task.values()) + len(all_holds),
        "ready_by_task": {task: len(rows) for task, rows in ready_by_task.items()},
        "held_rows": len(all_holds),
        "hold_reasons": dict(Counter(row["hold_reason"] for row in all_holds)),
        "active_machine_evidence_holds": len(held_ids),
        "input_hashes": input_hashes,
        "output_hashes": {name: preview.file_sha256(output / name) for name in artifacts},
        "active_gold_hashes_before": before,
        "active_gold_hashes_after": after,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
    }
    preview.write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--microtext-reviewed", type=Path)
    parser.add_argument("--visualdiff-reviewed", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    inputs = {
        task: resolve(path)
        for task, path in (
            ("microtext", args.microtext_reviewed),
            ("visualdiff", args.visualdiff_reviewed),
        )
        if path is not None
    }
    report = build(root, inputs, resolve(args.output_dir))
    print(json.dumps({
        "status": report["status"],
        "input_rows": report["input_rows"],
        "ready_by_task": report["ready_by_task"],
        "held_rows": report["held_rows"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
