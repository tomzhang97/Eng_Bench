#!/usr/bin/env python3
"""Sample baseline failures from prediction files for quick review."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def row_id(row: dict[str, Any]) -> str | None:
    value = row.get("id") or row.get("question_id") or row.get("qid")
    return str(value) if value is not None else None


def answer(row: dict[str, Any]) -> str:
    value = row.get("answer")
    if value is None:
        value = row.get("answer_text")
    return "" if value is None else str(value)


def normalize(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def bbox(entry: Any) -> list[float] | None:
    raw = entry.get("bbox") if isinstance(entry, dict) else entry
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        return None


def image_index(entry: Any) -> int | None:
    if not isinstance(entry, dict) or entry.get("image_index") is None:
        return None
    try:
        return int(entry["image_index"])
    except (TypeError, ValueError):
        return None


def iou(a: list[float], b: list[float]) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def evidence(row: dict[str, Any]) -> list[Any]:
    value = row.get("evidence")
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def max_evidence_iou(gold: dict[str, Any], pred: dict[str, Any]) -> float:
    best = 0.0
    for gold_ev in evidence(gold):
        gold_box = bbox(gold_ev)
        if gold_box is None:
            continue
        gold_index = image_index(gold_ev)
        for pred_ev in evidence(pred):
            pred_box = bbox(pred_ev)
            pred_index = image_index(pred_ev)
            if pred_box is None:
                continue
            if gold_index is not None and pred_index is not None and gold_index != pred_index:
                continue
            best = max(best, iou(gold_box, pred_box))
    return best


def prediction_path_for_report(report_path: Path) -> Path:
    return report_path.with_name(report_path.name.replace("_report.json", "_predictions.jsonl"))


def report_name(path: Path, payload: dict[str, Any]) -> str:
    return str(payload.get("model_name") or path.name.replace("_report.json", ""))


def collect_failure_rows(root: str | Path, max_examples: int = 10) -> list[dict[str, Any]]:
    root = Path(root)
    gold_by_id = {row_id(row): row for row in load_jsonl(root / "eng_bench.jsonl") if row_id(row)}
    failures: list[dict[str, Any]] = []
    counts_by_model: dict[str, int] = defaultdict(int)
    for report_path in sorted((root / "results" / "baselines").glob("*_report.json")):
        if any(marker in report_path.name.lower() for marker in ("oracle", "smoke")):
            continue
        report = load_json(report_path)
        model = report_name(report_path, report)
        predictions = load_jsonl(prediction_path_for_report(report_path))
        for prediction in predictions:
            if counts_by_model[model] >= max_examples:
                break
            identifier = row_id(prediction)
            gold = gold_by_id.get(identifier)
            if not gold:
                continue
            task = str(gold.get("task") or "unknown")
            normalized_match = normalize(answer(gold)) == normalize(answer(prediction))
            best_iou = max_evidence_iou(gold, prediction)
            is_failure = (
                (task == "microtext" and not normalized_match)
                or (task == "visualdiff" and best_iou < 0.3)
            )
            if not is_failure:
                continue
            failures.append(
                {
                    "model": model,
                    "task": task,
                    "split": gold.get("split", "unknown"),
                    "id": identifier,
                    "category": (gold.get("metadata") or {}).get("category", ""),
                    "gold_answer": answer(gold),
                    "pred_answer": answer(prediction),
                    "best_evidence_iou": best_iou,
                }
            )
            counts_by_model[model] += 1
    return failures


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Eng_Bench Baseline Error Analysis",
        "",
        "This file samples baseline failures for quick manual inspection. It is not a complete error taxonomy.",
        "",
        "| Model | Task | Split | ID | Category | Gold | Prediction | Best IoU |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['model']} | {row['task']} | {row['split']} | {row['id']} | "
            f"{row['category']} | {row['gold_answer']} | {row['pred_answer']} | "
            f"{row['best_evidence_iou']:.4f} |"
        )
    if not rows:
        lines.append("| none | - | - | - | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Eng_Bench baseline error-analysis samples.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output", default="results/baselines/ERROR_ANALYSIS.md")
    parser.add_argument("--max-examples", type=int, default=10)
    args = parser.parse_args(argv)

    root = Path(args.root)
    rows = collect_failure_rows(root, max_examples=args.max_examples)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(rows), encoding="utf-8")
    print(f"[OK] Wrote {output}")
    print(f"[OK] Failure samples: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
