#!/usr/bin/env python3
"""Evaluate Eng_Bench unified predictions and write baseline reports."""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


TASKS = ("microtext", "visualdiff")

HEADLINE_METRICS = (
    "microtext.normalized_exact_match",
    "microtext.character_error_rate",
    "microtext.evidence_iou_0_5",
    "visualdiff.evidence_recall_iou_0_3",
    "visualdiff.evidence_recall_iou_0_5",
    "visualdiff.old_new_side_hit_rate",
    "visualdiff.change_type_accuracy",
    "visualdiff.normalized_description_f1",
)


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def row_id(row: dict[str, Any]) -> str | None:
    value = row.get("id") or row.get("question_id") or row.get("qid")
    return str(value) if value is not None else None


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def stringify_answer(answer: Any) -> str:
    if answer is None:
        return ""
    if isinstance(answer, str):
        return answer
    if isinstance(answer, dict):
        for key in ("text", "answer", "answer_text", "value", "yes"):
            if key in answer:
                return stringify_answer(answer[key])
    if isinstance(answer, list):
        return " ".join(stringify_answer(part) for part in answer)
    return str(answer)


def answer_for(row: dict[str, Any]) -> str:
    for key in ("answer", "answer_text", "change_desc_gt", "text_gt"):
        if key in row:
            return stringify_answer(row.get(key))
    return ""


def normalize_compact(text: Any) -> str:
    return "".join(re.findall(r"[a-z0-9]+", stringify_answer(text).lower()))


def answer_tokens(text: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", stringify_answer(text).lower())


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (0 if ca == cb else 1)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def token_f1(prediction: Any, gold: Any) -> float:
    pred_tokens = answer_tokens(prediction)
    gold_tokens = answer_tokens(gold)
    if not pred_tokens and not gold_tokens:
        return 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0
    pred_counts = Counter(pred_tokens)
    gold_counts = Counter(gold_tokens)
    common = sum((pred_counts & gold_counts).values())
    if common == 0:
        return 0.0
    precision = common / len(pred_tokens)
    recall = common / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def bbox_from_entry(entry: Any) -> list[float] | None:
    raw = entry.get("bbox") if isinstance(entry, dict) else entry
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        return None


def image_index_from_entry(entry: Any) -> int | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("image_index")
    if value is None:
        side = str(entry.get("side") or "").lower()
        if side in {"old", "before", "left"}:
            return 0
        if side in {"new", "after", "right"}:
            return 1
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def evidence_entries(row: dict[str, Any]) -> list[Any]:
    evidence = row.get("evidence")
    if isinstance(evidence, list):
        return evidence
    if isinstance(evidence, dict):
        return [evidence]
    evidence_bboxes = row.get("evidence_bboxes")
    if isinstance(evidence_bboxes, list):
        return [{"bbox": bbox} for bbox in evidence_bboxes]
    return []


def bbox_iou(box1: list[float], box2: list[float]) -> float:
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])
    union = area1 + area2 - intersection
    return intersection / union if union > 0 else 0.0


def compatible_evidence_iou(gold_entry: Any, pred_entry: Any) -> float:
    gold_box = bbox_from_entry(gold_entry)
    pred_box = bbox_from_entry(pred_entry)
    if gold_box is None or pred_box is None:
        return 0.0
    gold_index = image_index_from_entry(gold_entry)
    pred_index = image_index_from_entry(pred_entry)
    if gold_index is not None and pred_index is not None and gold_index != pred_index:
        return 0.0
    return bbox_iou(gold_box, pred_box)


def evidence_hit_count(
    gold_entries: list[Any],
    pred_entries: list[Any],
    threshold: float,
) -> tuple[int, int]:
    hits = 0
    for gold_entry in gold_entries:
        if bbox_from_entry(gold_entry) is None:
            continue
        best = max(
            (compatible_evidence_iou(gold_entry, pred_entry) for pred_entry in pred_entries),
            default=0.0,
        )
        if best >= threshold:
            hits += 1
    valid_gold = sum(1 for entry in gold_entries if bbox_from_entry(entry) is not None)
    return hits, valid_gold


def row_side_hit(gold_entries: list[Any], pred_entries: list[Any], threshold: float) -> bool:
    valid_gold = [entry for entry in gold_entries if bbox_from_entry(entry) is not None]
    if not valid_gold:
        return False
    gold_indexes = {image_index_from_entry(entry) for entry in valid_gold}
    required_indexes = {index for index in gold_indexes if index is not None}
    if required_indexes:
        for required_index in required_indexes:
            side_gold = [
                entry for entry in valid_gold if image_index_from_entry(entry) == required_index
            ]
            hits, total = evidence_hit_count(side_gold, pred_entries, threshold)
            if total == 0 or hits == 0:
                return False
        return True
    hits, total = evidence_hit_count(valid_gold, pred_entries, threshold)
    return total > 0 and hits == total


def change_type_set(row: dict[str, Any]) -> set[str]:
    metadata = row_metadata(row)
    value = (
        row.get("change_type")
        or row.get("change_types")
        or metadata.get("change_type")
        or metadata.get("change_types")
    )
    if value is None:
        return set()
    if isinstance(value, str):
        return {value.strip().lower()} if value.strip() else set()
    if isinstance(value, Iterable):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return {str(value).strip().lower()}


def filter_gold_rows(
    rows: list[dict[str, Any]],
    split: str | None = None,
    task: str | None = None,
) -> list[dict[str, Any]]:
    selected = rows
    if split and split != "all":
        selected = [row for row in selected if row.get("split") == split]
    if task and task != "all":
        selected = [row for row in selected if row.get("task") == task]
    return selected


def domain_for(row: dict[str, Any]) -> str:
    metadata = row_metadata(row)
    return str(
        metadata.get("domain")
        or metadata.get("doc_id")
        or metadata.get("pair_id")
        or row.get("doc_id")
        or "unknown"
    )


def prediction_map(predictions: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
    mapped: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for prediction in predictions:
        identifier = row_id(prediction)
        if identifier is None:
            continue
        if identifier in mapped:
            duplicates += 1
        mapped[identifier] = prediction
    return mapped, duplicates


def score_microtext(
    gold_rows: list[dict[str, Any]],
    predictions_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    exact = 0
    normalized_exact = 0
    total_distance = 0
    total_gold_chars = 0
    evidence_hits = 0
    evidence_total = 0
    per_category: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "exact": 0, "normalized": 0}
    )

    for gold in gold_rows:
        prediction = predictions_by_id.get(row_id(gold) or "", {})
        gold_answer = answer_for(gold)
        pred_answer = answer_for(prediction)
        raw_match = gold_answer.strip().lower() == pred_answer.strip().lower()
        norm_match = normalize_compact(gold_answer) == normalize_compact(pred_answer)
        if raw_match:
            exact += 1
        if norm_match:
            normalized_exact += 1
        gold_norm = normalize_compact(gold_answer)
        pred_norm = normalize_compact(pred_answer)
        total_distance += levenshtein(gold_norm, pred_norm)
        total_gold_chars += max(1, len(gold_norm))

        gold_evidence = evidence_entries(gold)
        pred_evidence = evidence_entries(prediction)
        hits, total = evidence_hit_count(gold_evidence, pred_evidence, 0.5)
        evidence_hits += hits
        evidence_total += total

        category = str(row_metadata(gold).get("category") or gold.get("category") or "unknown")
        per_category[category]["total"] += 1
        per_category[category]["exact"] += int(raw_match)
        per_category[category]["normalized"] += int(norm_match)

    total_rows = len(gold_rows)
    return {
        "total": total_rows,
        "exact_match": exact / total_rows if total_rows else 0.0,
        "normalized_exact_match": normalized_exact / total_rows if total_rows else 0.0,
        "character_error_rate": total_distance / total_gold_chars if total_gold_chars else 0.0,
        "evidence_iou_0_5": evidence_hits / evidence_total if evidence_total else 0.0,
        "per_category_accuracy": {
            category: {
                "total": counts["total"],
                "exact_match": counts["exact"] / counts["total"] if counts["total"] else 0.0,
                "normalized_exact_match": counts["normalized"] / counts["total"]
                if counts["total"]
                else 0.0,
            }
            for category, counts in sorted(per_category.items())
        },
    }


def score_visualdiff(
    gold_rows: list[dict[str, Any]],
    predictions_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    recall_hits_03 = 0
    recall_hits_05 = 0
    recall_total = 0
    side_hits = 0
    side_total = 0
    type_matches = 0
    type_total = 0
    f1_sum = 0.0
    exact = 0
    normalized_exact = 0

    for gold in gold_rows:
        prediction = predictions_by_id.get(row_id(gold) or "", {})
        gold_answer = answer_for(gold)
        pred_answer = answer_for(prediction)
        exact += int(gold_answer.strip().lower() == pred_answer.strip().lower())
        normalized_exact += int(normalize_compact(gold_answer) == normalize_compact(pred_answer))
        f1_sum += token_f1(pred_answer, gold_answer)

        gold_evidence = evidence_entries(gold)
        pred_evidence = evidence_entries(prediction)
        hits_03, total = evidence_hit_count(gold_evidence, pred_evidence, 0.3)
        hits_05, _ = evidence_hit_count(gold_evidence, pred_evidence, 0.5)
        recall_hits_03 += hits_03
        recall_hits_05 += hits_05
        recall_total += total
        if total:
            side_total += 1
            side_hits += int(row_side_hit(gold_evidence, pred_evidence, 0.3))

        gold_types = change_type_set(gold)
        if gold_types:
            type_total += 1
            type_matches += int(gold_types == change_type_set(prediction))

    total_rows = len(gold_rows)
    return {
        "total": total_rows,
        "exact_match": exact / total_rows if total_rows else 0.0,
        "normalized_exact_match": normalized_exact / total_rows if total_rows else 0.0,
        "evidence_recall_iou_0_3": recall_hits_03 / recall_total if recall_total else 0.0,
        "evidence_recall_iou_0_5": recall_hits_05 / recall_total if recall_total else 0.0,
        "old_new_side_hit_rate": side_hits / side_total if side_total else 0.0,
        "change_type_accuracy": type_matches / type_total if type_total else 0.0,
        "normalized_description_f1": f1_sum / total_rows if total_rows else 0.0,
    }


def per_domain_metrics(
    gold_rows: list[dict[str, Any]],
    predictions_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in gold_rows:
        by_domain[domain_for(row)].append(row)

    output: dict[str, dict[str, Any]] = {}
    for domain, rows in sorted(by_domain.items()):
        micro = score_microtext([row for row in rows if row.get("task") == "microtext"], predictions_by_id)
        visual = score_visualdiff([row for row in rows if row.get("task") == "visualdiff"], predictions_by_id)
        output[domain] = {
            "rows": len(rows),
            "microtext_rows": micro["total"],
            "visualdiff_rows": visual["total"],
            "microtext_normalized_exact_match": micro["normalized_exact_match"],
            "microtext_character_error_rate": micro["character_error_rate"],
            "visualdiff_evidence_recall_iou_0_5": visual["evidence_recall_iou_0_5"],
            "visualdiff_old_new_side_hit_rate": visual["old_new_side_hit_rate"],
        }
    return output


def score_predictions(
    gold_rows: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    model_name: str = "unknown",
    split: str | None = None,
    task: str | None = None,
    bootstrap_samples: int = 0,
    bootstrap_seed: int = 13,
) -> dict[str, Any]:
    selected_gold = filter_gold_rows(gold_rows, split=split, task=task)
    predictions_by_id, duplicate_predictions = prediction_map(predictions)
    missing_predictions = sum(1 for row in selected_gold if row_id(row) not in predictions_by_id)
    rows_by_task = {
        name: count
        for name, count in sorted(Counter(str(row.get("task", "unknown")) for row in selected_gold).items())
    }

    micro_gold = [row for row in selected_gold if row.get("task") == "microtext"]
    visual_gold = [row for row in selected_gold if row.get("task") == "visualdiff"]
    report = {
        "model_name": model_name,
        "split": split or "all",
        "task": task or "all",
        "rows_scored": len(selected_gold),
        "missing_predictions": missing_predictions,
        "duplicate_predictions": duplicate_predictions,
        "rows_by_task": rows_by_task,
        "microtext": score_microtext(micro_gold, predictions_by_id),
        "visualdiff": score_visualdiff(visual_gold, predictions_by_id),
        "per_domain": per_domain_metrics(selected_gold, predictions_by_id),
    }
    report["confidence_intervals"] = (
        bootstrap_confidence_intervals(
            selected_gold,
            predictions,
            report,
            samples=bootstrap_samples,
            seed=bootstrap_seed,
            model_name=model_name,
        )
        if bootstrap_samples > 0 and selected_gold
        else {}
    )
    return report


def nested_metric(report: dict[str, Any], path: str) -> float | None:
    current: Any = report
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return float(current) if isinstance(current, (int, float)) else None


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def bootstrap_confidence_intervals(
    selected_gold: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    point_report: dict[str, Any],
    samples: int,
    seed: int,
    model_name: str,
) -> dict[str, dict[str, float | int]]:
    rng = random.Random(seed)
    values_by_metric: dict[str, list[float]] = {metric: [] for metric in HEADLINE_METRICS}
    for _ in range(samples):
        sample = [selected_gold[rng.randrange(len(selected_gold))] for _ in selected_gold]
        sample_report = score_predictions(sample, predictions, model_name=model_name)
        for metric in HEADLINE_METRICS:
            value = nested_metric(sample_report, metric)
            if value is not None:
                values_by_metric[metric].append(value)

    intervals: dict[str, dict[str, float | int]] = {}
    for metric, values in values_by_metric.items():
        if metric.startswith("microtext.") and point_report["microtext"]["total"] == 0:
            continue
        if metric.startswith("visualdiff.") and point_report["visualdiff"]["total"] == 0:
            continue
        point = nested_metric(point_report, metric)
        if point is None or not values:
            continue
        intervals[metric] = {
            "point": point,
            "low": percentile(values, 0.025),
            "high": percentile(values, 0.975),
            "samples": samples,
        }
    return intervals


def format_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    micro = report["microtext"]
    visual = report["visualdiff"]
    lines = [
        "# Eng_Bench Baseline Report",
        "",
        f"- Model: {report['model_name']}",
        f"- Split: {report['split']}",
        f"- Task filter: {report['task']}",
        f"- Rows scored: {report['rows_scored']}",
        f"- Missing predictions: {report['missing_predictions']}",
        f"- Duplicate prediction IDs: {report['duplicate_predictions']}",
        f"- Rows by task: {report['rows_by_task']}",
        "",
        "## Microtext",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total | {micro['total']} |",
        f"| Exact match | {format_metric(micro['exact_match'])} |",
        f"| Normalized exact match | {format_metric(micro['normalized_exact_match'])} |",
        f"| Character error rate | {format_metric(micro['character_error_rate'])} |",
        f"| Evidence IoU@0.5 | {format_metric(micro['evidence_iou_0_5'])} |",
        "",
        "## Visualdiff",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total | {visual['total']} |",
        f"| Evidence recall@IoU 0.3 | {format_metric(visual['evidence_recall_iou_0_3'])} |",
        f"| Evidence recall@IoU 0.5 | {format_metric(visual['evidence_recall_iou_0_5'])} |",
        f"| Old/new side hit rate | {format_metric(visual['old_new_side_hit_rate'])} |",
        f"| Change-type accuracy | {format_metric(visual['change_type_accuracy'])} |",
        f"| Normalized description F1 | {format_metric(visual['normalized_description_f1'])} |",
        "",
    ]
    if micro["per_category_accuracy"]:
        lines.extend(
            [
                "## Microtext Categories",
                "",
                "| Category | Total | Exact | Normalized Exact |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for category, values in micro["per_category_accuracy"].items():
            lines.append(
                f"| {category} | {values['total']} | "
                f"{format_metric(values['exact_match'])} | "
                f"{format_metric(values['normalized_exact_match'])} |"
            )
        lines.append("")
    if report.get("confidence_intervals"):
        lines.extend(
            [
                "## Confidence Intervals",
                "",
                "| Metric | Point | 95% CI Low | 95% CI High | Samples |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for metric, values in sorted(report["confidence_intervals"].items()):
            lines.append(
                f"| {metric} | {format_metric(values['point'])} | "
                f"{format_metric(values['low'])} | {format_metric(values['high'])} | "
                f"{values['samples']} |"
            )
        lines.append("")
    if report.get("per_domain"):
        lines.extend(
            [
                "## Domains",
                "",
                "| Domain | Rows | Microtext | Visualdiff | Micro Norm EM | Micro CER | VDiff Recall@0.5 | Side Hit |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for domain, values in sorted(report["per_domain"].items()):
            lines.append(
                f"| {domain} | {values['rows']} | {values['microtext_rows']} | "
                f"{values['visualdiff_rows']} | "
                f"{format_metric(values['microtext_normalized_exact_match'])} | "
                f"{format_metric(values['microtext_character_error_rate'])} | "
                f"{format_metric(values['visualdiff_evidence_recall_iou_0_5'])} | "
                f"{format_metric(values['visualdiff_old_new_side_hit_rate'])} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_reports(
    report: dict[str, Any],
    report_json: str | Path | None = None,
    report_md: str | Path | None = None,
) -> None:
    if report_json:
        write_json(report_json, report)
    if report_md:
        output = Path(report_md)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_markdown(report), encoding="utf-8")


def run_benchmark(
    gt_path: str | Path,
    pred_path: str | Path,
    task: str | None = None,
    split: str | None = None,
    model_name: str = "unknown",
) -> dict[str, Any]:
    report = score_predictions(
        load_jsonl(gt_path),
        load_jsonl(pred_path),
        model_name=model_name,
        split=split,
        task=task,
    )
    print(f"Eng_Bench results for {report['model_name']}")
    print(f"Rows scored: {report['rows_scored']}")
    print(f"Missing predictions: {report['missing_predictions']}")
    print(f"Rows by task: {report['rows_by_task']}")
    if report["microtext"]["total"]:
        print(f"Microtext normalized EM: {report['microtext']['normalized_exact_match']:.4f}")
    if report["visualdiff"]["total"]:
        print(f"Visualdiff evidence recall@0.5: {report['visualdiff']['evidence_recall_iou_0_5']:.4f}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Eng_Bench unified benchmark runner.")
    parser.add_argument("--gt", required=True, help="Ground-truth Eng_Bench JSONL")
    parser.add_argument("--pred", required=True, help="Prediction JSONL")
    parser.add_argument("--task", default="all", choices=("all", *TASKS), help="Task filter")
    parser.add_argument("--split", default="all", help="Split filter, e.g. dev/test/all")
    parser.add_argument("--model-name", default="unknown", help="Model or baseline name")
    parser.add_argument("--report-json", help="Optional JSON report output path")
    parser.add_argument("--report-md", help="Optional Markdown report output path")
    parser.add_argument("--bootstrap-samples", type=int, default=0, help="Bootstrap samples for CIs")
    parser.add_argument("--bootstrap-seed", type=int, default=13, help="Bootstrap RNG seed")
    args = parser.parse_args(argv)

    report = score_predictions(
        load_jsonl(args.gt),
        load_jsonl(args.pred),
        model_name=args.model_name,
        split=args.split,
        task=args.task,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
    )
    write_reports(report, args.report_json, args.report_md)
    print(f"Eng_Bench results for {report['model_name']}")
    print(f"Rows scored: {report['rows_scored']}")
    print(f"Missing predictions: {report['missing_predictions']}")
    print(f"Rows by task: {report['rows_by_task']}")
    if args.report_json:
        print(f"Wrote JSON report: {args.report_json}")
    if args.report_md:
        print(f"Wrote Markdown report: {args.report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
