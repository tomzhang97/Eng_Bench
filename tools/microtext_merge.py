#!/usr/bin/env python3
"""Merge reviewed microtext batch rows into item/question JSONL files."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ACCEPTED_STATUSES = {"accepted", "edited"}


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


def bbox_tuple(row: dict[str, Any]) -> tuple[int, int, int, int]:
    bbox = row.get("bbox") or [0, 0, 0, 0]
    return tuple(int(round(float(value))) for value in bbox[:4])


def quantized_bbox_tuple(row: dict[str, Any], step: int = 4) -> tuple[int, int, int, int]:
    bbox = bbox_tuple(row)
    return tuple(int(round(value / step) * step) for value in bbox)


def reviewed_key(row: dict[str, Any]) -> tuple[str, int, tuple[int, int, int, int], str]:
    return (
        str(row.get("doc_id", "")),
        int(row.get("page_index", 0)),
        quantized_bbox_tuple(row),
        str(row.get("text_gt", row.get("target_text", ""))).strip(),
    )


def region_key(row: dict[str, Any]) -> tuple[str, int, tuple[int, int, int, int]]:
    return (
        str(row.get("doc_id", "")),
        int(row.get("page_index", 0)),
        quantized_bbox_tuple(row),
    )


def answer_text(row: dict[str, Any]) -> str:
    corrected = str(row.get("corrected_text", "")).strip()
    if str(row.get("review_status", "")) == "edited" and corrected:
        return corrected
    return str(row.get("proposed_text") or row.get("target_text") or "").strip()


def candidate_suffix(row: dict[str, Any], fallback: int) -> str:
    candidate_id = str(row.get("candidate_id", ""))
    if "__" in candidate_id:
        return candidate_id.rsplit("__", 1)[-1]
    return f"{fallback:06d}"


def unique_item_id(row: dict[str, Any], existing_ids: set[str], fallback: int) -> str:
    doc_id = str(row.get("doc_id", "unknown_doc"))
    version_id = str(row.get("version_id", "unknown"))
    page = int(row.get("page_index", 0))
    base = f"mt__{doc_id}__{version_id}__p{page:04d}__{candidate_suffix(row, fallback)}"
    item_id = base
    suffix = 1
    while item_id in existing_ids:
        item_id = f"{base}_r{suffix}"
        suffix += 1
    existing_ids.add(item_id)
    return item_id


def question_for_item(item: dict[str, Any], question_text: str) -> dict[str, Any]:
    item_id = item["item_id"]
    return {
        "question_id": f"q_{item_id}",
        "item_ids": [item_id],
        "doc_id": item.get("doc_id"),
        "version_id": item.get("version_id"),
        "query_text": question_text or "What text is shown in this small region?",
        "answer_text": item.get("text_gt", ""),
        "answer_type": "span",
        "split": item.get("split", "dev"),
        "template_family": item.get("category", "unknown"),
    }


def merge_review_rows(
    existing_items: list[dict[str, Any]],
    existing_questions: list[dict[str, Any]],
    reviewed_rows: list[dict[str, Any]],
    split: str,
    reviewed_at: str,
    split_by_doc: dict[str, str] | None = None,
    require_split_map: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    merged_items = list(existing_items)
    merged_questions = list(existing_questions)
    existing_ids = {str(row.get("item_id", "")) for row in existing_items}
    existing_keys = {reviewed_key(row) for row in existing_items}
    existing_regions = {region_key(row) for row in existing_items}
    stats: Counter[str] = Counter()

    for idx, row in enumerate(reviewed_rows):
        status = str(row.get("review_status", "")).strip().lower()
        text = answer_text(row)
        prospective = dict(row)
        prospective["text_gt"] = text
        if status not in ACCEPTED_STATUSES or not text:
            stats["skipped"] += 1
            continue
        if reviewed_key(prospective) in existing_keys or region_key(prospective) in existing_regions:
            stats["duplicate"] += 1
            continue

        doc_id = str(row.get("doc_id") or "")
        assigned_split = (split_by_doc or {}).get(doc_id) or row.get("split") or split
        if require_split_map and doc_id not in (split_by_doc or {}):
            stats["missing_split_mapping"] += 1
            continue

        item_id = unique_item_id(row, existing_ids, idx)
        item = {
            "item_id": item_id,
            "doc_id": row.get("doc_id"),
            "board_id": row.get("board_id"),
            "version_id": row.get("version_id"),
            "page_index": int(row.get("page_index", 0)),
            "page_id": f"{row.get('doc_id')}__{row.get('version_id')}__p{int(row.get('page_index', 0)):04d}",
            "object_id": row.get("object_id"),
            "bbox": row.get("bbox") or [0, 0, 0, 0],
            "text_gt": text,
            "category": row.get("category", "unknown"),
            "font_height_px": row.get("font_height_px"),
            "dimension_name": row.get("dimension_name"),
            "notes": row.get("review_notes", row.get("notes", "")),
            "split": assigned_split,
            "review_status": status,
            "review_source": row.get("review_source") or row.get("source", "human"),
            "reviewed_at": reviewed_at,
            "source_candidate_id": row.get("candidate_id"),
        }
        for field in (
            "human_reviewed",
            "certification_method",
            "certification_tier",
            "certification_policy_version",
            "certification_date",
            "certification_eligibility_report",
            "certification_eligibility_report_sha256",
            "certification_calibration_checklist",
            "certification_calibration_checklist_sha256",
            "certification_calibration_attestation",
            "certification_calibration_attestation_sha256",
            "machine_certification_evidence_sha256",
        ):
            if field in row:
                item[field] = row[field]
        merged_items.append(item)
        merged_questions.append(question_for_item(item, str(row.get("question_text", ""))))
        existing_keys.add(reviewed_key(item))
        existing_regions.add(region_key(item))
        stats["accepted"] += 1

    return merged_items, merged_questions, stats


def load_split_policy(root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    duplicates: list[str] = []
    for split in ("train", "dev", "test"):
        path = root / "splits" / f"microtext_{split}.txt"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            doc_id = line.strip()
            if not doc_id or doc_id.startswith("#"):
                continue
            if doc_id in mapping:
                duplicates.append(f"{doc_id}: {mapping[doc_id]} and {split}")
            mapping[doc_id] = split
    if duplicates:
        raise ValueError("duplicate microtext split assignments: " + "; ".join(duplicates))
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge reviewed microtext rows")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--reviewed", default="microtext/annotations/microtext_review_batch_001.jsonl")
    parser.add_argument("--items", default="microtext/annotations/microtext_items.jsonl")
    parser.add_argument("--questions", default="microtext/annotations/microtext_questions.jsonl")
    parser.add_argument("--split", default="dev")
    parser.add_argument(
        "--use-split-policy",
        action="store_true",
        help="Require each reviewed doc_id to be assigned by splits/microtext_*.txt.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-items", help="Optional merged-items preview path.")
    parser.add_argument("--output-questions", help="Optional merged-questions preview path.")
    args = parser.parse_args()

    root = Path(args.root)
    reviewed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    items, questions, stats = merge_review_rows(
        existing_items=load_jsonl(root / args.items),
        existing_questions=load_jsonl(root / args.questions),
        reviewed_rows=load_jsonl(root / args.reviewed),
        split=args.split,
        reviewed_at=reviewed_at,
        split_by_doc=load_split_policy(root) if args.use_split_policy else None,
        require_split_map=args.use_split_policy,
    )

    if not args.dry_run:
        write_jsonl(root / args.items, items)
        write_jsonl(root / args.questions, questions)
    if args.output_items:
        write_jsonl(root / args.output_items, items)
    if args.output_questions:
        write_jsonl(root / args.output_questions, questions)

    action = "Would merge" if args.dry_run else "Merged"
    print(f"[OK] {action} reviewed microtext rows")
    print(dict(sorted(stats.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
