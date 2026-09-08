#!/usr/bin/env python3
"""Audit visualdiff rows for structural benchmark-usability failures."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image


TODO = "CHANGE_DESC_GT_TODO"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def bbox_tuple(row: dict[str, Any], key: str) -> tuple[int, int, int, int] | None:
    bbox = row.get(key) or []
    if len(bbox) < 4:
        return None
    return tuple(int(round(float(value))) for value in bbox[:4])


def image_path(root: Path, row: dict[str, Any], version_key: str, page_key: str) -> Path:
    explicit_key = "image_old" if version_key.endswith("_old") else "image_new"
    explicit = str(row.get(explicit_key) or "").strip()
    if explicit:
        candidate = Path(explicit)
        full_path = candidate if candidate.is_absolute() else root / candidate
        if full_path.exists():
            return full_path
    doc_id = str(row.get("doc_id", ""))
    version_id = str(row.get(version_key, ""))
    page = int(row.get(page_key, 0))
    return root / "images" / f"{doc_id}__{version_id}" / f"page_{page:04d}.png"


def bbox_failure(
    root: Path,
    row: dict[str, Any],
    bbox_key: str,
    version_key: str,
    page_key: str,
) -> dict[str, Any] | None:
    pair_id = str(row.get("pair_id", ""))
    bbox = bbox_tuple(row, bbox_key)
    if bbox is None or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        return {"pair_id": pair_id, "bbox_key": bbox_key, "reason": "invalid_bbox", "bbox": bbox}
    path = image_path(root, row, version_key, page_key)
    if not path.exists():
        return {
            "pair_id": pair_id,
            "bbox_key": bbox_key,
            "reason": "missing_image",
            "image_path": path.as_posix(),
            "bbox": list(bbox),
        }
    with Image.open(path) as image:
        width, height = image.size
    if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > width or bbox[3] > height:
        return {
            "pair_id": pair_id,
            "bbox_key": bbox_key,
            "reason": "out_of_frame",
            "image_path": path.as_posix(),
            "bbox": list(bbox),
            "image_size": [width, height],
        }
    return None


def is_todo(text: Any) -> bool:
    return str(text or "").strip() in {"", TODO}


def audit(root: Path) -> dict[str, Any]:
    pairs = load_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    questions = load_jsonl(root / "visualdiff" / "annotations" / "visualdiff_questions.jsonl")
    pair_ids = [str(row.get("pair_id", "")) for row in pairs]
    question_pair_ids = [str(row.get("pair_id", "")) for row in questions]
    question_by_pair = {
        str(row.get("pair_id", "")): row for row in questions if row.get("pair_id")
    }

    duplicate_pair_ids = sorted(
        pair_id for pair_id, count in Counter(pair_ids).items() if pair_id and count > 1
    )
    duplicate_question_pair_ids = sorted(
        pair_id
        for pair_id, count in Counter(question_pair_ids).items()
        if pair_id and count > 1
    )
    pair_id_set = set(pair_ids)
    question_pair_id_set = set(question_pair_ids)
    missing_questions = sorted(pair_id_set - question_pair_id_set)
    orphan_questions = sorted(question_pair_id_set - pair_id_set)

    bbox_failures: list[dict[str, Any]] = []
    answer_mismatches: list[str] = []
    split_counts: Counter[str] = Counter()
    todo_by_split: Counter[str] = Counter()
    gold_by_split: Counter[str] = Counter()
    todo_by_project: Counter[str] = Counter()
    change_type_counts: Counter[str] = Counter()
    review_buckets: Counter[str] = Counter()
    regions: defaultdict[tuple[Any, ...], list[str]] = defaultdict(list)

    for row in pairs:
        split = str(row.get("split", "unknown"))
        project_id = str(row.get("project_id", "unknown"))
        split_counts[split] += 1
        for change_type in row.get("change_type") or ["unknown"]:
            change_type_counts[str(change_type)] += 1
        if row.get("is_titleblock"):
            review_buckets[f"{split}_titleblock"] += 1
        else:
            review_buckets[f"{split}_non_titleblock"] += 1

        desc = row.get("change_desc_gt")
        if is_todo(desc):
            todo_by_split[split] += 1
            todo_by_project[project_id] += 1
        else:
            gold_by_split[split] += 1
            question = question_by_pair.get(str(row.get("pair_id", "")))
            if question and question.get("answer_text") != desc:
                answer_mismatches.append(str(row.get("pair_id", "")))

        for bbox_key, version_key, page_key in (
            ("bbox_old", "version_id_old", "page_index_old"),
            ("bbox_new", "version_id_new", "page_index_new"),
        ):
            failure = bbox_failure(root, row, bbox_key, version_key, page_key)
            if failure:
                bbox_failures.append(failure)

        old_bbox = bbox_tuple(row, "bbox_old")
        new_bbox = bbox_tuple(row, "bbox_new")
        if old_bbox and new_bbox:
            regions[
                (
                    project_id,
                    int(row.get("page_index_old", 0)),
                    int(row.get("page_index_new", 0)),
                    old_bbox,
                    new_bbox,
                )
            ].append(str(row.get("pair_id", "")))

    duplicate_regions = {
        "|".join(map(str, key)): value for key, value in regions.items() if len(value) > 1
    }
    critical_failures = (
        len(duplicate_pair_ids)
        + len(duplicate_question_pair_ids)
        + len(missing_questions)
        + len(orphan_questions)
        + len(bbox_failures)
        + len(answer_mismatches)
    )
    return {
        "pairs": len(pairs),
        "questions": len(questions),
        "split_counts": dict(sorted(split_counts.items())),
        "todo_total": sum(todo_by_split.values()),
        "todo_by_split": dict(sorted(todo_by_split.items())),
        "todo_by_project": dict(sorted(todo_by_project.items())),
        "gold_by_split": dict(sorted(gold_by_split.items())),
        "change_type_counts": dict(sorted(change_type_counts.items())),
        "review_buckets": dict(sorted(review_buckets.items())),
        "duplicate_pair_ids": duplicate_pair_ids,
        "duplicate_question_pair_ids": duplicate_question_pair_ids,
        "duplicate_regions": duplicate_regions,
        "missing_questions": missing_questions,
        "orphan_questions": orphan_questions,
        "bbox_failures": bbox_failures,
        "answer_mismatches": answer_mismatches,
        "critical_failures": critical_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit visualdiff gold/TODO quality")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()

    report = audit(Path(args.root))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["critical_failures"]:
        print(f"[FAIL] {report['critical_failures']} critical visualdiff quality failures")
        return 1
    print("[OK] Visualdiff structural quality audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
