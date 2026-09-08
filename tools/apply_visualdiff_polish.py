#!/usr/bin/env python3
"""Apply filled visualdiff human-polish checklists to active pairs."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REJECT_STATUSES = {"reject_unclear", "reject_no_change", "reject_bbox_mismatch"}
VALID_STATUSES = {"valid", "edit", "needs_full_page"} | REJECT_STATUSES


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_checklist(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def normalize_status(status: str | None) -> str:
    return (status or "").strip().lower()


def checklist_by_pair(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    out = {}
    for row in rows:
        pair_id = (row.get("pair_id") or "").strip()
        status = normalize_status(row.get("human_status"))
        if not pair_id or not status:
            continue
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported human_status '{status}' for {pair_id}")
        out[pair_id] = row
    return out


def mark_reviewed(row: dict[str, Any], checklist_row: dict[str, str], status: str) -> dict[str, Any]:
    out = dict(row)
    notes = (checklist_row.get("human_notes") or "").strip()
    out["human_review_status"] = status
    out["human_review_notes"] = notes
    out["human_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    return out


def apply_valid(row: dict[str, Any], checklist_row: dict[str, str]) -> dict[str, Any]:
    out = mark_reviewed(row, checklist_row, "valid")
    out["desc_source"] = "human_validated_codex_assisted"
    out["review_confidence"] = "human_validated"
    return out


def apply_edit(row: dict[str, Any], checklist_row: dict[str, str]) -> dict[str, Any]:
    description = (checklist_row.get("human_description") or "").strip()
    if not description:
        raise ValueError(f"edit status requires human_description for {row.get('pair_id')}")
    out = mark_reviewed(row, checklist_row, "edit")
    out["change_desc_gt"] = description
    out["desc_source"] = "human"
    out["review_confidence"] = "human_validated"
    return out


def apply_needs_full_page(row: dict[str, Any], checklist_row: dict[str, str]) -> dict[str, Any]:
    out = mark_reviewed(row, checklist_row, "needs_full_page")
    out["review_confidence"] = "low"
    return out


def apply_reject(row: dict[str, Any], checklist_row: dict[str, str], status: str) -> dict[str, Any]:
    out = mark_reviewed(row, checklist_row, status)
    out["quarantine_reason"] = status
    out["quarantine_source"] = "visualdiff_human_polish"
    return out


def apply_polish(
    pairs: list[dict[str, Any]],
    checklist_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    checklist = checklist_by_pair(checklist_rows)
    active: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    stats = {
        "valid": 0,
        "edit": 0,
        "reject_unclear": 0,
        "reject_no_change": 0,
        "reject_bbox_mismatch": 0,
        "needs_full_page": 0,
        "unreviewed": 0,
        "reviewed_without_pair": 0,
    }
    seen_pairs = set()

    for pair in pairs:
        pair_id = str(pair.get("pair_id", ""))
        seen_pairs.add(pair_id)
        checklist_row = checklist.get(pair_id)
        if checklist_row is None:
            active.append(pair)
            stats["unreviewed"] += 1
            continue

        status = normalize_status(checklist_row.get("human_status"))
        if status == "valid":
            active.append(apply_valid(pair, checklist_row))
        elif status == "edit":
            active.append(apply_edit(pair, checklist_row))
        elif status == "needs_full_page":
            active.append(apply_needs_full_page(pair, checklist_row))
        elif status in REJECT_STATUSES:
            quarantine.append(apply_reject(pair, checklist_row, status))
        stats[status] += 1

    stats["reviewed_without_pair"] = len(set(checklist) - seen_pairs)
    return active, quarantine, stats


def write_summary(path: Path, stats: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply visualdiff human-polish checklist decisions.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--pairs",
        default="visualdiff/annotations/visualdiff_pairs.jsonl",
        help="Active visualdiff pairs JSONL",
    )
    parser.add_argument(
        "--checklist",
        default="derived/review_packs/visualdiff_human_polish_2026-05-14/validation_checklist.csv",
        help="Filled human-polish CSV checklist",
    )
    parser.add_argument(
        "--output",
        default="visualdiff/annotations/visualdiff_pairs.jsonl",
        help="Output active pairs JSONL",
    )
    parser.add_argument(
        "--quarantine-output",
        default="visualdiff/annotations/visualdiff_pairs_HUMAN_POLISH_QUARANTINED.jsonl",
        help="Output rejected/unclear rows JSONL",
    )
    parser.add_argument(
        "--summary",
        default="derived/quality/visualdiff_human_polish_summary.json",
        help="JSON summary output",
    )
    parser.add_argument("--backup", default=None, help="Optional backup path before overwrite")
    args = parser.parse_args()

    root = Path(args.root)
    pairs_path = root / args.pairs
    checklist_path = root / args.checklist
    output_path = root / args.output
    quarantine_path = root / args.quarantine_output
    summary_path = root / args.summary

    pairs = load_jsonl(pairs_path)
    checklist_rows = load_checklist(checklist_path)
    active, quarantine, stats = apply_polish(pairs, checklist_rows)

    if output_path.exists() and output_path == pairs_path:
        backup_path = Path(args.backup) if args.backup else output_path.with_suffix(".jsonl.bak")
        shutil.copy2(output_path, backup_path)
        print(f"[*] Backed up active pairs to {backup_path}")

    save_jsonl(output_path, active)
    save_jsonl(quarantine_path, quarantine)
    write_summary(summary_path, stats)

    print("[*] Applied visualdiff human-polish checklist")
    print(f"    Valid:           {stats['valid']}")
    print(f"    Edited:          {stats['edit']}")
    print(f"    Rejected unclear:{stats['reject_unclear']}")
    print(f"    Rejected no-delta:{stats['reject_no_change']}")
    print(f"    Rejected bbox:   {stats['reject_bbox_mismatch']}")
    print(f"    Needs full page: {stats['needs_full_page']}")
    print(f"    Unreviewed:      {stats['unreviewed']}")
    if stats["reviewed_without_pair"]:
        print(f"[ERR] Reviewed rows without active pair: {stats['reviewed_without_pair']}")
        return 1
    if stats["needs_full_page"]:
        print("[WARN] Some rows still need full-page review before gold release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
