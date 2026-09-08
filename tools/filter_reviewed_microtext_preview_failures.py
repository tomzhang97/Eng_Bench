#!/usr/bin/env python3
"""Hold reviewed MicroText rows that fail a combined-Gold promotion preview."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_question_leakage import row_leaks_answer
from benchmark_utils import dedup_key


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def unified_item_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(metadata.get("item_id") or "").strip()


def collect_preview_failures(
    reviewed_rows: list[dict[str, Any]],
    prepared_items: list[dict[str, Any]],
    unified_rows: list[dict[str, Any]],
    preview_holds: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], list[str]]:
    source_to_item: dict[str, str] = {}
    item_to_source: dict[str, str] = {}
    errors: list[str] = []
    reviewed_ids = {
        str(row.get("candidate_id") or "").strip()
        for row in reviewed_rows
        if str(row.get("candidate_id") or "").strip()
    }
    for item in prepared_items:
        item_id = str(item.get("item_id") or "").strip()
        source_id = str(item.get("source_candidate_id") or "").strip()
        if not item_id or not source_id:
            errors.append("prepared_item_missing_identity")
            continue
        if source_id not in reviewed_ids:
            errors.append(f"prepared_item_source_not_reviewed:{source_id}")
            continue
        source_to_item[source_id] = item_id
        item_to_source[item_id] = source_id

    reasons: dict[str, list[str]] = defaultdict(list)

    def add_reason(source_id: str, reason: str) -> None:
        if source_id not in reviewed_ids:
            errors.append(f"failure_identity_not_reviewed:{source_id}:{reason}")
            return
        if reason not in reasons[source_id]:
            reasons[source_id].append(reason)

    for hold in preview_holds:
        if str(hold.get("task") or "").strip().lower() != "microtext":
            continue
        source_id = str(hold.get("identity") or "").strip()
        for reason in hold.get("reasons") or ["preview_hold"]:
            add_reason(source_id, str(reason))

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in unified_rows:
        groups[dedup_key(row)].append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        for row in group:
            item_id = unified_item_id(row)
            if item_id in item_to_source:
                add_reason(item_to_source[item_id], "strict_duplicate_qa_collision")

    for row in unified_rows:
        item_id = unified_item_id(row)
        if item_id in item_to_source and row_leaks_answer(row):
            add_reason(item_to_source[item_id], "question_answer_leakage")

    missing_prepared = sorted(
        source_id
        for source_id in reviewed_ids
        if source_id not in source_to_item and source_id not in reasons
    )
    errors.extend(f"reviewed_row_unaccounted:{source_id}" for source_id in missing_prepared)
    return dict(reasons), sorted(set(errors))


def partition(
    rows: list[dict[str, Any]], reasons: dict[str, list[str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    ready: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for source_row in rows:
        row = dict(source_row)
        candidate_id = str(row.get("candidate_id") or "").strip()
        row_reasons = reasons.get(candidate_id, [])
        row["safe_to_merge_gold"] = False
        if row_reasons:
            row["promotion_preview_status"] = "held"
            row["promotion_preview_hold_reasons"] = list(row_reasons)
            held.append(row)
            counts.update(row_reasons)
        else:
            row["promotion_preview_status"] = "ready_for_clean_repreview"
            ready.append(row)
    return ready, held, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--preview-dir", type=Path, required=True)
    parser.add_argument("--ready-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    reviewed_path = resolve(args.reviewed)
    preview_dir = resolve(args.preview_dir)
    paths = {
        "prepared": preview_dir / "prepared_microtext_items.jsonl",
        "unified": preview_dir / "eng_bench_preview.jsonl",
        "holds": preview_dir / "promotion_holds.jsonl",
    }
    reviewed_rows = read_jsonl(reviewed_path)
    reasons, errors = collect_preview_failures(
        reviewed_rows,
        read_jsonl(paths["prepared"]),
        read_jsonl(paths["unified"]),
        read_jsonl(paths["holds"]),
    )
    ready, held, reason_counts = partition(reviewed_rows, reasons)
    ready_path = resolve(args.ready_output)
    held_path = resolve(args.held_output)
    write_jsonl(ready_path, ready)
    write_jsonl(held_path, held)

    valid = not errors and len(ready) + len(held) == len(reviewed_rows)
    report = {
        "goal": "Gold v2.0 Global",
        "input": reviewed_path.relative_to(root).as_posix(),
        "input_sha256": sha256(reviewed_path),
        "preview_dir": preview_dir.relative_to(root).as_posix(),
        "counts": {
            "input_rows": len(reviewed_rows),
            "ready_rows": len(ready),
            "held_rows": len(held),
            "hold_reasons": dict(sorted(reason_counts.items())),
        },
        "errors": errors,
        "ready_output": ready_path.relative_to(root).as_posix(),
        "ready_sha256": sha256(ready_path),
        "held_output": held_path.relative_to(root).as_posix(),
        "held_sha256": sha256(held_path),
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "valid": valid,
        "interpretation": (
            "Ready rows only survived this preview-failure filter. A new strict preview "
            "must pass before the rows can be considered for atomic Gold promotion."
        ),
    }
    report_json = resolve(args.report_json)
    report_md = resolve(args.report_md)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Reviewed MicroText Preview-Failure Filter",
        "",
        f"- Input rows: `{len(reviewed_rows)}`",
        f"- Ready for clean re-preview: `{len(ready)}`",
        f"- Held: `{len(held)}`",
        f"- Valid: `{str(valid).lower()}`",
        "- Active Gold modified: `false`",
        "",
        "## Hold Reasons",
        "",
    ]
    lines.extend(f"- `{reason}`: `{count}`" for reason, count in sorted(reason_counts.items()))
    if errors:
        lines.extend(["", "## Errors", "", *[f"- `{error}`" for error in errors]])
    lines.extend(["", report["interpretation"], ""])
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report["counts"], indent=2, sort_keys=True))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
