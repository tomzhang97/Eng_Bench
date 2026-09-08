#!/usr/bin/env python3
"""Merge validated staged split plans without changing active split files."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


VALID_SPLITS = {"train", "dev", "test"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload.get("valid", False):
        raise ValueError(f"invalid staged split plan: {path}")
    return payload


def merge_plans(
    plans: list[tuple[str, dict[str, Any]]], *, date_label: str
) -> dict[str, Any]:
    reservations: dict[tuple[str, str], dict[str, Any]] = {}
    sources: dict[tuple[str, str], str] = {}
    duplicate_keys = 0
    for plan_name, plan in plans:
        for raw in plan.get("reservations") or []:
            row = dict(raw)
            task = str(row.get("task") or "").strip().lower()
            unit_id = str(row.get("unit_id") or "").strip()
            split = str(row.get("split") or "").strip().lower()
            reservation_id = str(row.get("reservation_id") or "").strip()
            if not task or not unit_id or split not in VALID_SPLITS or not reservation_id:
                raise ValueError(f"invalid reservation in {plan_name}: {raw}")
            key = (task, unit_id)
            existing = reservations.get(key)
            if existing is not None:
                if (
                    str(existing.get("split") or "").strip().lower() != split
                    or str(existing.get("reservation_id") or "").strip() != reservation_id
                ):
                    raise ValueError(
                        f"conflicting reservation for {task}:{unit_id} in "
                        f"{sources[key]} and {plan_name}"
                    )
                duplicate_keys += 1
                continue
            reservations[key] = row
            sources[key] = plan_name

    ordered = [reservations[key] for key in sorted(reservations)]
    by_split = Counter(str(row["split"]).lower() for row in ordered)
    by_task = Counter(str(row["task"]).lower() for row in ordered)
    return {
        "goal": "Gold v2.0 Global",
        "valid": True,
        "date_label": date_label,
        "mode": "merged_staged_split_plan",
        "source_plan_count": len(plans),
        "reservation_count": len(ordered),
        "duplicate_identical_reservations": duplicate_keys,
        "reservations_by_split": dict(sorted(by_split.items())),
        "reservations_by_task": dict(sorted(by_task.items())),
        "reservations": ordered,
        "issues": [],
        "active_split_files_modified": False,
        "interpretation": (
            "The merged reservations constrain staged review rows only. They do not edit "
            "active Gold or authorize promotion."
        ),
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--plan", action="append", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    resolved = [resolve(path) for path in args.plan]
    merged = merge_plans(
        [(path.as_posix(), load_plan(path)) for path in resolved],
        date_label=args.date_label,
    )
    output_path = resolve(args.output_json)
    write_json_atomic(output_path, merged)
    report = {
        "goal": "Gold v2.0 Global",
        "valid": True,
        "date_label": args.date_label,
        "source_plans": [
            {"path": path.as_posix(), "sha256": file_sha256(path)} for path in resolved
        ],
        "output_path": output_path.as_posix(),
        "output_sha256": file_sha256(output_path),
        "reservation_count": merged["reservation_count"],
        "duplicate_identical_reservations": merged["duplicate_identical_reservations"],
        "active_split_files_modified": False,
    }
    write_json_atomic(resolve(args.report_json), report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
