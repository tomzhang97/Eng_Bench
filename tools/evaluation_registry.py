#!/usr/bin/env python3
"""Identify countable, content-distinct baseline evaluation artifacts."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


IGNORED_NAME_MARKERS = ("oracle", "smoke")
SUBMISSION_REQUIRED_STRING_FIELDS = (
    "dataset_version",
    "split_manifest_hash",
    "model_name",
    "prediction_hash",
    "scorer_command",
)


def prediction_path_for_report(report_path: Path) -> Path:
    stem = report_path.name.removesuffix("_report.json")
    return report_path.with_name(f"{stem}_predictions.jsonl")


def canonical_prediction_hash(path: Path) -> tuple[str, int]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            row_id = str(row.get("id") or row.get("question_id") or row.get("qid") or "").strip()
            if not row_id or row_id in seen_ids:
                raise ValueError("prediction rows must have unique non-empty IDs")
            seen_ids.add(row_id)
            rows.append(
                {
                    "id": row_id,
                    "answer": row.get("answer")
                    or row.get("answer_text")
                    or row.get("change_desc_gt")
                    or row.get("text_gt")
                    or "",
                    "evidence": row.get("evidence") or [],
                    "change_types": row.get("change_types") or row.get("change_type") or [],
                }
            )
    if not rows:
        raise ValueError("prediction file is empty")
    rows.sort(key=lambda row: row["id"])
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), len(rows)


def baseline_registry(
    root: Path,
    baselines_dir: Path = Path("results/baselines"),
) -> dict[str, list[dict[str, Any]]]:
    counted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_hashes: dict[str, int] = {}
    directory = root / baselines_dir
    if not directory.exists():
        return {"counted": counted, "excluded": excluded}

    for report_path in sorted(directory.glob("*_report.json")):
        name = report_path.name.removesuffix("_report.json")
        lowered = name.lower()
        if any(marker in lowered for marker in IGNORED_NAME_MARKERS):
            excluded.append({"name": name, "reason": "ignored_name"})
            continue
        prediction_path = prediction_path_for_report(report_path)
        if not prediction_path.exists():
            excluded.append({"name": name, "reason": "missing_predictions"})
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("report must be a JSON object")
            prediction_hash, prediction_rows = canonical_prediction_hash(prediction_path)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            excluded.append({"name": name, "reason": "invalid_artifact", "detail": str(exc)})
            continue
        entry = {
            "name": name,
            "report_path": report_path.relative_to(root).as_posix(),
            "prediction_path": prediction_path.relative_to(root).as_posix(),
            "prediction_hash": prediction_hash,
            "prediction_rows": prediction_rows,
            "report_rows_scored": int(report.get("rows_scored") or 0),
        }
        if prediction_hash in seen_hashes:
            existing_index = seen_hashes[prediction_hash]
            existing = counted[existing_index]
            if entry["report_rows_scored"] > existing["report_rows_scored"]:
                counted[existing_index] = entry
                duplicate, duplicate_of = existing, name
            else:
                duplicate, duplicate_of = entry, existing["name"]
            excluded.append(
                {
                    "name": duplicate["name"],
                    "reason": "duplicate_predictions",
                    "duplicate_of": duplicate_of,
                    "prediction_hash": prediction_hash,
                }
            )
            continue
        seen_hashes[prediction_hash] = len(counted)
        counted.append(entry)
    return {"counted": counted, "excluded": excluded}


def is_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def is_finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def submission_schema_error(payload: Any) -> tuple[str, str] | None:
    if not isinstance(payload, dict):
        return "invalid_artifact", "submission entry must be a JSON object"

    missing = [
        field
        for field in SUBMISSION_REQUIRED_STRING_FIELDS
        if not isinstance(payload.get(field), str) or not payload[field].strip()
    ]
    if not isinstance(payload.get("metrics"), dict) or not payload["metrics"]:
        missing.append("metrics")
    if not isinstance(payload.get("confidence_intervals"), dict) or not payload["confidence_intervals"]:
        missing.append("confidence_intervals")
    if missing:
        return "missing_required_fields", ", ".join(sorted(missing))

    for field in ("split_manifest_hash", "prediction_hash"):
        if not is_sha256(payload[field]):
            return "invalid_field", f"{field} must be a 64-character SHA-256 hex digest"

    metrics = payload["metrics"]
    invalid_metrics = sorted(name for name, value in metrics.items() if not is_finite_number(value))
    if invalid_metrics:
        return "invalid_field", f"metrics must contain finite numeric values: {', '.join(invalid_metrics)}"

    intervals = payload["confidence_intervals"]
    for metric, interval in intervals.items():
        if metric not in metrics:
            return "invalid_field", f"confidence interval has no matching metric: {metric}"
        if not isinstance(interval, dict):
            return "invalid_field", f"confidence interval must be an object: {metric}"
        low = interval.get("low")
        high = interval.get("high")
        if not is_finite_number(low) or not is_finite_number(high):
            return "invalid_field", f"confidence interval needs finite low/high values: {metric}"
        if low > high:
            return "invalid_field", f"confidence interval low exceeds high: {metric}"
    return None


def submission_registry(
    root: Path,
    submissions_dir: Path = Path("leaderboard/submissions"),
) -> dict[str, list[dict[str, Any]]]:
    counted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_prediction_hashes: dict[str, str] = {}
    directory = root / submissions_dir
    if not directory.exists():
        return {"counted": counted, "excluded": excluded}

    for submission_path in sorted(directory.glob("*.json")):
        name = submission_path.stem
        lowered = name.lower()
        if any(marker in lowered for marker in (*IGNORED_NAME_MARKERS, "readme")):
            excluded.append({"name": name, "reason": "ignored_name"})
            continue
        try:
            payload = json.loads(submission_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            excluded.append({"name": name, "reason": "invalid_artifact", "detail": str(exc)})
            continue
        schema_error = submission_schema_error(payload)
        if schema_error:
            reason, detail = schema_error
            excluded.append({"name": name, "reason": reason, "detail": detail})
            continue

        prediction_hash = str(payload["prediction_hash"]).lower()
        if prediction_hash in seen_prediction_hashes:
            excluded.append(
                {
                    "name": name,
                    "reason": "duplicate_prediction_hash",
                    "duplicate_of": seen_prediction_hashes[prediction_hash],
                    "prediction_hash": prediction_hash,
                }
            )
            continue
        seen_prediction_hashes[prediction_hash] = name
        counted.append(
            {
                "name": name,
                "path": submission_path.relative_to(root).as_posix(),
                "dataset_version": payload["dataset_version"],
                "split_manifest_hash": str(payload["split_manifest_hash"]).lower(),
                "model_name": payload["model_name"],
                "prediction_hash": prediction_hash,
                "metric_count": len(payload["metrics"]),
                "confidence_interval_count": len(payload["confidence_intervals"]),
            }
        )
    return {"counted": counted, "excluded": excluded}
