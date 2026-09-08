#!/usr/bin/env python3
"""Build a machine-countable leaderboard entry from scored submission artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from evaluation_registry import is_finite_number, submission_registry, submission_schema_error


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nested_metric(report: dict[str, Any], metric: str) -> Any:
    current: Any = report
    for part in metric.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "submission"


def build_entry(
    dataset_version: str,
    manifest_path: Path,
    predictions_path: Path,
    report_path: Path,
    scorer_command: str,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("score report must be a JSON object")
    model_name = str(report.get("model_name") or "").strip()
    if not model_name:
        raise ValueError("score report must include model_name")
    report_intervals = report.get("confidence_intervals")
    if not isinstance(report_intervals, dict) or not report_intervals:
        raise ValueError("score report must include non-empty confidence_intervals")

    metrics: dict[str, int | float] = {}
    intervals: dict[str, dict[str, int | float]] = {}
    for metric, interval in sorted(report_intervals.items()):
        if not isinstance(interval, dict):
            raise ValueError(f"confidence interval must be an object: {metric}")
        point = interval.get("point")
        if not is_finite_number(point):
            point = nested_metric(report, metric)
        low = interval.get("low")
        high = interval.get("high")
        if not is_finite_number(point):
            raise ValueError(f"confidence interval lacks a finite point metric: {metric}")
        metrics[metric] = point
        intervals[metric] = {"low": low, "high": high}

    entry = {
        "dataset_version": dataset_version,
        "split_manifest_hash": file_sha256(manifest_path),
        "model_name": model_name,
        "prediction_hash": file_sha256(predictions_path),
        "scorer_command": scorer_command,
        "metrics": metrics,
        "confidence_intervals": intervals,
    }
    schema_error = submission_schema_error(entry)
    if schema_error:
        _, detail = schema_error
        raise ValueError(detail)
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Register a scored, content-distinct Eng_Bench leaderboard submission."
    )
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--dataset-version", required=True, help="Frozen benchmark version")
    parser.add_argument("--manifest", required=True, help="Challenge split manifest")
    parser.add_argument("--predictions", required=True, help="Submitted prediction JSONL")
    parser.add_argument("--report", required=True, help="Scored benchmark JSON report with CIs")
    parser.add_argument("--scorer-command", required=True, help="Exact scorer command used")
    parser.add_argument("--entry-name", help="Output entry stem; defaults to a model-name slug")
    parser.add_argument("--output", help="Explicit output JSON path")
    parser.add_argument("--force", action="store_true", help="Replace an existing entry")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report_path = resolve_path(root, args.report)
    entry = build_entry(
        dataset_version=args.dataset_version,
        manifest_path=resolve_path(root, args.manifest),
        predictions_path=resolve_path(root, args.predictions),
        report_path=report_path,
        scorer_command=args.scorer_command,
    )
    name = args.entry_name or slugify(str(entry["model_name"]))
    output = resolve_path(root, args.output or f"leaderboard/submissions/{name}.json")
    if output.exists() and not args.force:
        raise FileExistsError(f"refusing to replace existing leaderboard entry: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
