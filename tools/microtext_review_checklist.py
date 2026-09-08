#!/usr/bin/env python3
"""Export and apply spreadsheet-style microtext review checklists."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ALLOWED_STATUSES = {"accepted", "edited", "rejected", "needs_full_page"}
MERGEABLE_STATUSES = {"accepted", "edited"}
CHECKLIST_FIELDS = [
    "review_index",
    "candidate_id",
    "doc_id",
    "version_id",
    "page_index",
    "category",
    "proposed_text",
    "crop_path",
    "page_path",
    "image_path",
    "text_context",
    "question_text",
    "review_status",
    "corrected_text",
    "corrected_category",
    "review_notes",
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] = CHECKLIST_FIELDS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id", "")).strip()


def local_pack_path(value: Any, folder: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    name = Path(text).name
    if not name:
        return text
    return f"{folder}/{name}"


def export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checklist: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        checklist.append(
            {
                "review_index": idx,
                "candidate_id": candidate_id(row),
                "doc_id": row.get("doc_id", ""),
                "version_id": row.get("version_id", ""),
                "page_index": row.get("page_index", ""),
                "category": row.get("category", ""),
                "proposed_text": row.get("proposed_text") or row.get("target_text", ""),
                "crop_path": local_pack_path(row.get("crop_path", ""), "crops"),
                "page_path": local_pack_path(row.get("page_path", ""), "pages"),
                "image_path": row.get("image_path", ""),
                "text_context": row.get("text_context", ""),
                "question_text": row.get("question_text", ""),
                "review_status": "",
                "corrected_text": "",
                "corrected_category": "",
                "review_notes": "",
            }
        )
    return checklist


def apply_checklist(
    review_rows: list[dict[str, Any]],
    checklist_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], Counter[str], list[str]]:
    by_id = {candidate_id(row): dict(row) for row in review_rows if candidate_id(row)}
    stats: Counter[str] = Counter()
    errors: list[str] = []

    for checklist_row in checklist_rows:
        cid = str(checklist_row.get("candidate_id", "")).strip()
        if not cid:
            stats["missing_candidate_id"] += 1
            continue
        if cid not in by_id:
            stats["unknown_candidate_id"] += 1
            errors.append(f"unknown candidate_id: {cid}")
            continue
        status = str(checklist_row.get("review_status", "")).strip().lower()
        corrected = str(checklist_row.get("corrected_text", "")).strip()
        corrected_category = str(checklist_row.get("corrected_category", "")).strip()
        notes = str(checklist_row.get("review_notes", "")).strip()
        if not status:
            stats["blank"] += 1
            continue
        if status not in ALLOWED_STATUSES:
            stats["invalid_status"] += 1
            errors.append(f"{cid}: invalid review_status={status!r}")
            continue
        if status == "edited" and not corrected and not corrected_category:
            stats["edited_missing_correction"] += 1
            errors.append(
                f"{cid}: edited rows require corrected_text and/or corrected_category"
            )
            continue
        proposed = str(
            checklist_row.get("proposed_text") or by_id[cid].get("proposed_text") or by_id[cid].get("target_text") or ""
        ).strip()
        if status == "accepted" and not proposed:
            stats["accepted_missing_proposed_text"] += 1
            errors.append(f"{cid}: accepted rows require non-empty proposed_text; use edited with corrected_text")
            continue

        row = by_id[cid]
        row["review_status"] = status
        row["corrected_text"] = corrected
        if corrected_category:
            row["category"] = corrected_category
        row["review_notes"] = notes
        stats[status] += 1
        if status in MERGEABLE_STATUSES:
            stats["mergeable"] += 1

    ordered = [by_id.get(candidate_id(row), row) for row in review_rows]
    return ordered, stats, errors


def write_summary(path: Path, stats: Counter[str], errors: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"stats": dict(sorted(stats.items())), "errors": errors}, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export/apply microtext review checklist CSVs")
    sub = parser.add_subparsers(dest="mode", required=True)

    export = sub.add_parser("export", help="Export review JSONL rows to a CSV checklist")
    export.add_argument("--root", default=".")
    export.add_argument("--input", required=True, help="Review JSONL or review-pack manifest JSONL")
    export.add_argument("--output", required=True)

    apply = sub.add_parser("apply", help="Apply a filled CSV checklist to a review JSONL")
    apply.add_argument("--root", default=".")
    apply.add_argument("--review-jsonl", required=True)
    apply.add_argument("--checklist", required=True)
    apply.add_argument("--output", required=True)
    apply.add_argument("--summary", required=True)
    apply.add_argument("--strict", action="store_true", help="Return nonzero on checklist errors")

    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.mode == "export":
        rows = load_jsonl(root / args.input)
        checklist = export_rows(rows)
        write_csv(root / args.output, checklist)
        print(f"[OK] Wrote {len(checklist)} checklist rows to {args.output}")
        return 0

    review_rows = load_jsonl(root / args.review_jsonl)
    checklist_rows = read_csv(root / args.checklist)
    updated, stats, errors = apply_checklist(review_rows, checklist_rows)
    write_jsonl(root / args.output, updated)
    write_summary(root / args.summary, stats, errors)
    print(f"[OK] Wrote reviewed JSONL to {args.output}")
    print(f"[OK] Wrote summary to {args.summary}")
    print(dict(sorted(stats.items())))
    if errors:
        print("[WARN] Checklist errors:")
        for error in errors:
            print(f"  - {error}")
    return 1 if args.strict and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
