#!/usr/bin/env python3
"""Audit per-row baseline prediction coverage for Eng_Bench."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evaluation_registry import baseline_registry


IGNORED_MARKERS = ("oracle", "smoke")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def row_id(row: dict[str, Any]) -> str:
    value = row.get("id") or row.get("question_id") or row.get("qid")
    return str(value) if value is not None else ""


def baseline_name(path: Path) -> str:
    name = path.name
    if name.endswith("_predictions.jsonl"):
        name = name[: -len("_predictions.jsonl")]
    return name


def prediction_files(root: Path, baselines_dir: Path) -> list[Path]:
    registry = baseline_registry(root, baselines_dir=baselines_dir)
    return [root / item["prediction_path"] for item in registry["counted"]]


def audit_coverage(
    root: Path,
    gt_path: Path = Path("eng_bench.jsonl"),
    split: str = "test",
    min_baselines: int = 3,
    baselines_dir: Path = Path("results/baselines"),
) -> dict[str, Any]:
    gold_rows = [row for row in load_jsonl(root / gt_path) if split == "all" or row.get("split") == split]
    gold_by_id = {row_id(row): row for row in gold_rows if row_id(row)}
    coverage: dict[str, set[str]] = defaultdict(set)
    files = prediction_files(root, baselines_dir)
    registry = baseline_registry(root, baselines_dir=baselines_dir)
    file_rows = {}
    unknown_predictions: dict[str, int] = {}
    for path in files:
        name = baseline_name(path)
        rows = load_jsonl(path)
        file_rows[name] = len(rows)
        unknown = 0
        for row in rows:
            identifier = row_id(row)
            if identifier in gold_by_id:
                coverage[identifier].add(name)
            elif identifier:
                unknown += 1
        unknown_predictions[name] = unknown

    rows_by_coverage = Counter(len(coverage.get(identifier, set())) for identifier in gold_by_id)
    rows_by_task = Counter(str(row.get("task", "unknown")) for row in gold_by_id.values())
    failing = [
        {
            "id": identifier,
            "task": gold_by_id[identifier].get("task"),
            "coverage": len(coverage.get(identifier, set())),
            "baselines": sorted(coverage.get(identifier, set())),
        }
        for identifier in sorted(gold_by_id)
        if len(coverage.get(identifier, set())) < min_baselines
    ]
    return {
        "passed": not failing,
        "split": split,
        "min_baselines": min_baselines,
        "gold_rows": len(gold_by_id),
        "baseline_prediction_files": [str(path.relative_to(root)) for path in files],
        "baseline_prediction_file_count": len(files),
        "baseline_registry": registry,
        "prediction_rows_by_file": dict(sorted(file_rows.items())),
        "unknown_predictions_by_file": dict(sorted(unknown_predictions.items())),
        "rows_by_task": dict(sorted(rows_by_task.items())),
        "rows_by_coverage": {str(key): rows_by_coverage[key] for key in sorted(rows_by_coverage)},
        "failing_rows": len(failing),
        "failing_examples": failing[:25],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Baseline Coverage Audit",
        "",
        f"- Split: `{report['split']}`",
        f"- Required baselines per row: `{report['min_baselines']}`",
        f"- Gate passed: `{report['passed']}`",
        f"- Gold rows checked: `{report['gold_rows']}`",
        f"- Baseline prediction files: `{report['baseline_prediction_file_count']}`",
        f"- Failing rows: `{report['failing_rows']}`",
        f"- Rows by task: `{report['rows_by_task']}`",
        f"- Rows by coverage: `{report['rows_by_coverage']}`",
        "",
        "## Prediction Files",
        "",
    ]
    for path in report["baseline_prediction_files"]:
        name = baseline_name(Path(path))
        unknown = report["unknown_predictions_by_file"].get(name, 0)
        count = report["prediction_rows_by_file"].get(name, 0)
        lines.append(f"- `{path}`: `{count}` rows, `{unknown}` outside split")
    if report["failing_examples"]:
        lines.extend(["", "## Failing Examples", ""])
        for row in report["failing_examples"]:
            lines.append(
                f"- `{row['id']}` task `{row['task']}` coverage `{row['coverage']}` "
                f"from {row['baselines']}"
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit baseline coverage per Eng_Bench row.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--gt", default="eng_bench.jsonl", help="Unified labeled JSONL")
    parser.add_argument("--split", default="test", help="Split to audit")
    parser.add_argument("--min-baselines", type=int, default=3)
    parser.add_argument("--baselines-dir", default="results/baselines")
    parser.add_argument("--output-json", default="results/baselines/baseline_coverage_audit.json")
    parser.add_argument("--output-md", default="results/baselines/baseline_coverage_audit.md")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = audit_coverage(
        root=root,
        gt_path=Path(args.gt),
        split=args.split,
        min_baselines=args.min_baselines,
        baselines_dir=Path(args.baselines_dir),
    )
    json_path = root / args.output_json
    md_path = root / args.output_md
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"[OK] Wrote {json_path}")
    print(f"[OK] Wrote {md_path}")
    print(json.dumps({key: report[key] for key in ("passed", "gold_rows", "failing_rows", "rows_by_coverage")}, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
