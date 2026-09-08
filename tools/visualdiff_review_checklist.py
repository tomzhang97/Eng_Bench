#!/usr/bin/env python3
"""Export and apply spreadsheet-style visualdiff review checklists."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ALLOWED_STATUSES = {"valid", "edit", "reject_unclear", "needs_full_page"}
MERGEABLE_STATUSES = {"valid", "edit"}
LAYOUT_STATUS_GUIDANCE = (
    "layout is not a human_status. Use human_status=edit with human_description "
    "when a specific object/label/symbol/wire/table cell moved relative to nearby "
    "drawing content; use reject_unclear when old/new are identical or only the "
    "whole crop/page shifted."
)
STATUS_ALIASES = {
    "accept": "valid",
    "accepted": "valid",
    "valid": "valid",
    "ok": "valid",
    "edit": "edit",
    "edited": "edit",
    "reject": "reject_unclear",
    "rejected": "reject_unclear",
    "reject_unclear": "reject_unclear",
    "unclear": "reject_unclear",
    "full_page": "needs_full_page",
    "needs_full_page": "needs_full_page",
    "need_full_page": "needs_full_page",
    "needs full page": "needs_full_page",
}
TODO = "CHANGE_DESC_GT_TODO"
CHECKLIST_FIELDS = [
    "review_index",
    "pair_id",
    "split",
    "project_id",
    "page_old",
    "page_new",
    "change_type",
    "current_description",
    "bbox_old",
    "bbox_new",
    "old_crop_path",
    "new_crop_path",
    "panel_path",
    "old_page_path",
    "new_page_path",
    "what_to_validate",
    "human_status",
    "human_description",
    "human_notes",
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CHECKLIST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CHECKLIST_FIELDS})


def pair_id(row: dict[str, Any]) -> str:
    return str(row.get("pair_id") or row.get("id") or "").strip()


def current_description(row: dict[str, Any]) -> str:
    return str(row.get("description") or row.get("change_desc_gt") or "").strip()


def normalize_status(value: str) -> str:
    return STATUS_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())


def invalid_status_message(status: str) -> str:
    if status == "layout":
        return LAYOUT_STATUS_GUIDANCE
    return f"invalid human_status={status!r}"


def local_pack_path(value: Any, folder: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    return f"{folder}/{Path(text).name}"


def export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checklist: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        checklist.append(
            {
                "review_index": index,
                "pair_id": pair_id(row),
                "split": row.get("reserved_split") or row.get("split", ""),
                "project_id": row.get("project_id", ""),
                "page_old": row.get("page_old", row.get("page_index_old", "")),
                "page_new": row.get("page_new", row.get("page_index_new", "")),
                "change_type": row.get("change_type", ""),
                "current_description": current_description(row),
                "bbox_old": json.dumps(row.get("bbox_old", [])),
                "bbox_new": json.dumps(row.get("bbox_new", [])),
                "old_crop_path": local_pack_path(row.get("old_crop_path"), "old"),
                "new_crop_path": local_pack_path(row.get("new_crop_path"), "new"),
                "panel_path": local_pack_path(row.get("panel_path"), "panels"),
                "old_page_path": local_pack_path(row.get("old_page_path"), "pages_old"),
                "new_page_path": local_pack_path(row.get("new_page_path"), "pages_new"),
                "what_to_validate": (
                    "Confirm the old/new panel contains a real visible change inside the crop. "
                    "If old/new are identical, reject. If the whole crop/page is merely shifted or "
                    "misaligned, reject as alignment-only. Use layout only when a specific object "
                    "moves relative to surrounding drawing content. Use edit and write a concise "
                    "description for usable TODO rows; otherwise reject or request the full page."
                ),
                "human_status": "",
                "human_description": "",
                "human_notes": "",
            }
        )
    return checklist


def apply_checklist(
    review_rows: list[dict[str, Any]],
    checklist_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], Counter[str], list[str]]:
    by_id = {pair_id(row): dict(row) for row in review_rows if pair_id(row)}
    stats: Counter[str] = Counter()
    errors: list[str] = []
    for checklist_row in checklist_rows:
        identifier = str(checklist_row.get("pair_id") or "").strip()
        if not identifier:
            stats["missing_pair_id"] += 1
            continue
        if identifier not in by_id:
            stats["unknown_pair_id"] += 1
            errors.append(f"unknown pair_id: {identifier}")
            continue
        status = normalize_status(checklist_row.get("human_status") or "")
        description = str(checklist_row.get("human_description") or "").strip()
        notes = str(checklist_row.get("human_notes") or "").strip()
        if not status:
            stats["blank"] += 1
            continue
        if status not in ALLOWED_STATUSES:
            stats["invalid_status"] += 1
            errors.append(f"{identifier}: {invalid_status_message(status)}")
            continue
        row = by_id[identifier]
        existing = current_description(row) or str(checklist_row.get("current_description") or "").strip()
        if status == "edit" and not description:
            stats["edit_missing_human_description"] += 1
            errors.append(f"{identifier}: edit rows require human_description")
            continue
        if status == "valid" and (not existing or existing == TODO):
            if not description:
                stats["valid_on_todo_description"] += 1
                errors.append(
                    f"{identifier}: accepted rows on TODO descriptions require human_description"
                )
                continue
            status = "edit"

        row["human_review_status"] = status
        row["human_review_notes"] = notes
        row["human_description"] = description
        if status == "edit":
            row["description"] = description
            row["change_desc_gt"] = description
            row["desc_source"] = "human"
        stats[status] += 1
        if status in MERGEABLE_STATUSES:
            stats["mergeable"] += 1

    return [by_id.get(pair_id(row), row) for row in review_rows], stats, errors


def write_summary(path: Path, stats: Counter[str], errors: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"stats": dict(sorted(stats.items())), "errors": errors}, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    export = sub.add_parser("export")
    export.add_argument("--root", default=".")
    export.add_argument("--input", required=True)
    export.add_argument("--output", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--root", default=".")
    apply.add_argument("--review-jsonl", required=True)
    apply.add_argument("--checklist", required=True)
    apply.add_argument("--output", required=True)
    apply.add_argument("--summary", required=True)
    apply.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.mode == "export":
        rows = export_rows(load_jsonl(root / args.input))
        write_csv(root / args.output, rows)
        print(f"[OK] Wrote {len(rows)} visualdiff checklist rows to {args.output}")
        return 0

    updated, stats, errors = apply_checklist(
        load_jsonl(root / args.review_jsonl),
        read_csv(root / args.checklist),
    )
    write_jsonl(root / args.output, updated)
    write_summary(root / args.summary, stats, errors)
    print(f"[OK] Wrote reviewed visualdiff JSONL to {args.output}")
    print(dict(sorted(stats.items())))
    return 1 if args.strict and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
