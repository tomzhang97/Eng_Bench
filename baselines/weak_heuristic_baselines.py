#!/usr/bin/env python3
"""Weak heuristic baselines for Eng_Bench leaderboard sanity checks."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image


DEFAULT_ANSWERS = {
    "microtext": "unknown",
    "visualdiff": "A visible engineering-document change is present in the marked region.",
}


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


def answer_for(row: dict[str, Any]) -> str:
    for key in ("answer", "answer_text", "change_desc_gt", "text_gt"):
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return value
        return str(value)
    return ""


def category_for(row: dict[str, Any]) -> str:
    metadata = metadata_for(row)
    return str(metadata.get("category") or row.get("category") or "unknown")


def change_type_for(row: dict[str, Any]) -> str:
    metadata = metadata_for(row)
    value = metadata.get("change_type") or metadata.get("change_types") or row.get("change_type") or "unknown"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def prediction_bucket(row: dict[str, Any]) -> str:
    task = str(row.get("task") or "unknown")
    if task == "microtext":
        return f"microtext:{category_for(row)}"
    if task == "visualdiff":
        return f"visualdiff:{change_type_for(row)}"
    return task


def first_image_size(root: Path, row: dict[str, Any]) -> tuple[int, int] | None:
    images = row.get("images") or []
    if not isinstance(images, list) or not images:
        return None
    image_path = root / str(images[0])
    if not image_path.exists():
        return None
    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        # Benchmark assets are trusted local engineering sheets and can exceed
        # Pillow's web-upload safety threshold even when only metadata is read.
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(image_path) as image:
            return image.size
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def center_bbox(root: Path, row: dict[str, Any], fraction: float = 0.25) -> list[int]:
    size = first_image_size(root, row)
    if size is None:
        return [0, 0, 1, 1]
    width, height = size
    box_width = max(1, int(round(width * fraction)))
    box_height = max(1, int(round(height * fraction)))
    x0 = max(0, (width - box_width) // 2)
    y0 = max(0, (height - box_height) // 2)
    return [x0, y0, min(width, x0 + box_width), min(height, y0 + box_height)]


def full_page_bbox(root: Path, row: dict[str, Any]) -> list[int]:
    size = first_image_size(root, row)
    if size is None:
        return [0, 0, 1, 1]
    width, height = size
    return [0, 0, width, height]


def grid_bbox(
    root: Path,
    row: dict[str, Any],
    grid_cell: int = 0,
    grid_rows: int = 3,
    grid_cols: int = 3,
) -> list[int]:
    size = first_image_size(root, row)
    if size is None:
        return [0, 0, 1, 1]
    width, height = size
    rows = max(1, grid_rows)
    cols = max(1, grid_cols)
    cell = max(0, min(grid_cell, rows * cols - 1))
    row_index = cell // cols
    col_index = cell % cols
    x0 = round(width * col_index / cols)
    y0 = round(height * row_index / rows)
    x1 = round(width * (col_index + 1) / cols)
    y1 = round(height * (row_index + 1) / rows)
    return [x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)]


def evidence_for(
    root: Path,
    row: dict[str, Any],
    mode: str = "center",
    grid_cell: int = 0,
    grid_rows: int = 3,
    grid_cols: int = 3,
) -> list[dict[str, Any]]:
    if mode == "full_page":
        bbox = full_page_bbox(root, row)
    elif mode == "grid":
        bbox = grid_bbox(root, row, grid_cell=grid_cell, grid_rows=grid_rows, grid_cols=grid_cols)
    else:
        bbox = center_bbox(root, row)
    if row.get("task") == "visualdiff":
        return [{"image_index": 0, "bbox": bbox}, {"image_index": 1, "bbox": bbox}]
    return [{"image_index": 0, "bbox": bbox}]


def metadata_template_answer(row: dict[str, Any]) -> str:
    task = str(row.get("task") or "")
    if task == "microtext":
        category = category_for(row).replace("_", " ")
        return f"unread {category}"
    if task == "visualdiff":
        change_type = change_type_for(row).replace(",", " and ").replace("_", " ")
        if not change_type or change_type == "unknown":
            return DEFAULT_ANSWERS["visualdiff"]
        return f"The marked region contains a {change_type} change."
    return "unknown"


def train_priors(rows: list[dict[str, Any]], calibration_splits: set[str]) -> dict[str, str]:
    by_bucket: dict[str, Counter[str]] = defaultdict(Counter)
    by_task: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if str(row.get("split") or "") not in calibration_splits:
            continue
        answer = answer_for(row).strip()
        if not answer or answer == "CHANGE_DESC_GT_TODO":
            continue
        task = str(row.get("task") or "unknown")
        by_bucket[prediction_bucket(row)][answer] += 1
        by_task[task][answer] += 1

    priors: dict[str, str] = {}
    for bucket, counter in by_bucket.items():
        if counter:
            priors[bucket] = counter.most_common(1)[0][0]
    for task, counter in by_task.items():
        if counter:
            priors[f"{task}:__task_default__"] = counter.most_common(1)[0][0]
    return priors


def prior_answer(row: dict[str, Any], priors: dict[str, str]) -> str:
    task = str(row.get("task") or "unknown")
    return (
        priors.get(prediction_bucket(row))
        or priors.get(f"{task}:__task_default__")
        or DEFAULT_ANSWERS.get(task, "unknown")
    )


def select_target_rows(
    rows: list[dict[str, Any]],
    split: str,
    task: str,
) -> list[dict[str, Any]]:
    selected = rows
    if split != "all":
        selected = [row for row in selected if row.get("split") == split]
    if task != "all":
        selected = [row for row in selected if row.get("task") == task]
    return selected


def predict_rows(
    root: Path,
    rows: list[dict[str, Any]],
    mode: str,
    split: str = "test",
    task: str = "all",
    calibration_splits: set[str] | None = None,
    grid_cell: int = 0,
    grid_rows: int = 3,
    grid_cols: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    calibration_splits = calibration_splits or {"train"}
    priors = train_priors(rows, calibration_splits) if mode == "label_prior" else {}
    predictions: list[dict[str, Any]] = []
    target_rows = select_target_rows(rows, split=split, task=task)
    for row in target_rows:
        identifier = row_id(row)
        if not identifier:
            continue
        if mode in {"center", "full_page", "grid"}:
            answer = DEFAULT_ANSWERS.get(str(row.get("task")), "unknown")
        elif mode == "metadata_template":
            answer = metadata_template_answer(row)
        elif mode == "label_prior":
            answer = prior_answer(row, priors)
        else:
            raise ValueError(f"unknown baseline mode: {mode}")
        predictions.append(
            {
                "id": identifier,
                "answer": answer,
                "evidence": evidence_for(
                    root,
                    row,
                    mode=mode,
                    grid_cell=grid_cell,
                    grid_rows=grid_rows,
                    grid_cols=grid_cols,
                ),
                "metadata": {
                    "model": f"weak_{mode}" if mode != "grid" else f"weak_grid_{grid_cell}",
                    "method": mode,
                    "calibration_splits": sorted(calibration_splits) if mode == "label_prior" else [],
                    "grid_cell": grid_cell if mode == "grid" else None,
                    "grid_rows": grid_rows if mode == "grid" else None,
                    "grid_cols": grid_cols if mode == "grid" else None,
                },
            }
        )
    return predictions, {
        "mode": mode,
        "target_rows": len(target_rows),
        "predictions": len(predictions),
        "split": split,
        "task": task,
        "calibration_splits": sorted(calibration_splits),
        "grid_cell": grid_cell if mode == "grid" else None,
        "grid_rows": grid_rows if mode == "grid" else None,
        "grid_cols": grid_cols if mode == "grid" else None,
        "prior_buckets": len(priors),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate weak Eng_Bench heuristic predictions.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--input", default="eng_bench.jsonl", help="Unified Eng_Bench JSONL")
    parser.add_argument("--output", required=True, help="Output prediction JSONL")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("center", "full_page", "metadata_template", "label_prior", "grid"),
    )
    parser.add_argument("--split", default="test", help="Target split")
    parser.add_argument("--task", default="all", choices=("all", "microtext", "visualdiff"))
    parser.add_argument("--grid-cell", type=int, default=0, help="Zero-based grid cell for grid mode")
    parser.add_argument("--grid-rows", type=int, default=3, help="Rows for grid mode")
    parser.add_argument("--grid-cols", type=int, default=3, help="Columns for grid mode")
    parser.add_argument(
        "--calibration-splits",
        default="train",
        help="Comma-separated splits used by label_prior; target split is not used unless explicitly listed",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = load_jsonl(root / args.input)
    calibration_splits = {part.strip() for part in args.calibration_splits.split(",") if part.strip()}
    predictions, stats = predict_rows(
        root=root,
        rows=rows,
        mode=args.mode,
        split=args.split,
        task=args.task,
        calibration_splits=calibration_splits,
        grid_cell=args.grid_cell,
        grid_rows=args.grid_rows,
        grid_cols=args.grid_cols,
    )
    write_jsonl(root / args.output, predictions)
    print(json.dumps(stats, indent=2, sort_keys=True))
    print(f"[OK] Wrote {root / args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
