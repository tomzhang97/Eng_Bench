#!/usr/bin/env python3
"""Audit MicroText balance capacity across Gold, machine, primary, and future lanes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


CANONICAL_CATEGORIES = (
    "component_value",
    "dimension_value",
    "equipment_tag",
    "instrument_tag",
    "pin_label",
    "pipe_line_tag",
    "process_label",
    "process_value",
    "room_label",
    "tolerance_value",
)
SPLIT_PRIORITY = {"test": 0, "dev": 1, "train": 2, "": 3}
RELEASE_SPLITS = frozenset({"train", "dev", "test"})


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            rows.append(value)
    return rows


def read_row_payload(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return read_jsonl(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("rows")
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"expected JSON/JSONL row payload: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def normalized_category(row: dict[str, Any]) -> str:
    category = str(row.get("category") or "").strip()
    if not category:
        category = str((row.get("metadata") or {}).get("category") or "").strip()
    return category


def row_identities(row: dict[str, Any]) -> set[str]:
    identities: set[str] = set()
    for key in ("candidate_id", "record_id", "item_id"):
        value = str(row.get(key) or "").strip()
        if value:
            identities.add(f"id:{value}")
    source_candidate_id = str(row.get("source_candidate_id") or "").strip()
    if source_candidate_id.startswith(("mtcand__", "ocrcand__")):
        identities.add(f"id:{source_candidate_id}")
    doc_id = str(row.get("doc_id") or row.get("document_id") or "").strip()
    page_index = row.get("page_index")
    bbox = row.get("bbox")
    if doc_id and page_index is not None and isinstance(bbox, list) and len(bbox) == 4:
        identities.add(
            "region:"
            + doc_id
            + ":"
            + str(page_index)
            + ":"
            + ",".join(str(value) for value in bbox)
        )
    return identities


def category_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(normalized_category(row) for row in rows)
    return {category: int(counts.get(category, 0)) for category in CANONICAL_CATEGORIES}


def split_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(row.get("reserved_split") or row.get("split") or "") for row in rows
    )
    return dict(sorted(counts.items()))


def release_split(row: dict[str, Any]) -> str:
    split = str(row.get("reserved_split") or row.get("split") or "").strip()
    return split if split in RELEASE_SPLITS else ""


def projection(
    counts: dict[str, int],
    minimums: dict[str, int],
    *,
    pin_share_max: float,
) -> dict[str, Any]:
    total = sum(counts.values())
    pin_rows = int(counts.get("pin_label", 0))
    share = pin_rows / total if total else 0.0
    shortfalls = {
        category: max(0, int(minimums.get(category, 0)) - int(counts.get(category, 0)))
        for category in CANONICAL_CATEGORIES
    }
    return {
        "category_counts": dict(counts),
        "category_shortfalls": shortfalls,
        "open_category_floors": sum(value > 0 for value in shortfalls.values()),
        "pin_label_rows": pin_rows,
        "pin_label_share": round(share, 6),
        "pin_label_share_max": pin_share_max,
        "total_rows": total,
        "passes_balance": not any(shortfalls.values()) and share <= pin_share_max,
    }


def add_counts(base: dict[str, int], rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    result = dict(base)
    for row in rows:
        category = normalized_category(row)
        if category in result:
            result[category] += 1
    return result


def unique_lane(
    rows: list[dict[str, Any]],
    seen: set[str],
) -> tuple[list[dict[str, Any]], int, int]:
    unique: list[dict[str, Any]] = []
    overlaps = 0
    missing_identity = 0
    for row in rows:
        identities = row_identities(row)
        if not identities:
            missing_identity += 1
            continue
        if identities & seen:
            overlaps += 1
            continue
        unique.append(row)
        seen.update(identities)
    return unique, overlaps, missing_identity


def identity_union(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {identity for row in rows for identity in row_identities(row)}


def overlap_breakdown(
    rows: Iterable[dict[str, Any]],
    prior_lanes: list[tuple[str, set[str]]],
) -> dict[str, int]:
    counts = Counter({name: 0 for name, _ in prior_lanes})
    for row in rows:
        identities = row_identities(row)
        if not identities:
            continue
        for name, prior_identities in prior_lanes:
            if identities & prior_identities:
                counts[name] += 1
                break
    return dict(counts)


def row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    split = str(row.get("reserved_split") or row.get("split") or "")
    return (
        SPLIT_PRIORITY.get(split, 4),
        0 if bool(row.get("engineering_required")) else 1,
        int(row.get("primary_index") or 10**9),
        str(row.get("candidate_id") or row.get("record_id") or ""),
    )


def select_floor_rows(
    rows: list[dict[str, Any]],
    shortfalls: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    shortages: dict[str, int] = {}
    for category in CANONICAL_CATEGORIES:
        need = int(shortfalls.get(category, 0))
        if need <= 0:
            continue
        available = sorted(
            (
                row
                for row in rows
                if normalized_category(row) == category and release_split(row)
            ),
            key=row_sort_key,
        )
        selected.extend(available[:need])
        if len(available) < need:
            shortages[category] = need - len(available)
    return selected, shortages


def build_report(
    root: Path,
    *,
    capacity_report: Path,
    precalibration_report: Path,
    primary_payload: Path,
    future_capacity: Path,
    active_items: Path = Path("microtext/annotations/microtext_items.jsonl"),
    date_label: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    root = root.resolve()
    paths = {
        "active_items": resolve(root, active_items),
        "capacity_report": resolve(root, capacity_report),
        "precalibration_report": resolve(root, precalibration_report),
        "primary_payload": resolve(root, primary_payload),
        "future_capacity": resolve(root, future_capacity),
    }
    missing = [path for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError("missing inputs: " + ", ".join(str(path) for path in missing))

    capacity = read_json(paths["capacity_report"])
    precalibration = read_json(paths["precalibration_report"])
    strict_value = str((precalibration.get("artifacts") or {}).get("strict_ready") or "")
    if not strict_value:
        raise ValueError("precalibration report has no artifacts.strict_ready path")
    strict_path = resolve(root, strict_value)
    if not strict_path.is_file():
        raise ValueError(f"missing strict-ready lane: {strict_path}")

    active_rows = read_jsonl(paths["active_items"])
    strict_rows = read_jsonl(strict_path)
    primary_rows_value = read_row_payload(paths["primary_payload"])
    primary_rows = [row for row in primary_rows_value if row.get("task") == "microtext"]
    future_rows = [
        row
        for row in read_jsonl(paths["future_capacity"])
        if str(row.get("task") or "microtext") == "microtext"
    ]

    balance = capacity.get("microtext_balance_capacity") or {}
    policy = balance.get("policy") or {}
    minimums = {
        category: int((policy.get("category_minimums") or {}).get(category, 0))
        for category in CANONICAL_CATEGORIES
    }
    if not any(minimums.values()):
        raise ValueError("capacity report has no MicroText category minimums")
    pin_share_max = float(policy.get("pin_label_share_max") or 0.45)

    seen: set[str] = set()
    active_unique, active_overlap, active_missing = unique_lane(active_rows, seen)
    active_identities = identity_union(active_unique)
    strict_overlap_by_lane = overlap_breakdown(
        strict_rows, [("active_gold", active_identities)]
    )
    strict_unique, strict_overlap, strict_missing = unique_lane(strict_rows, seen)
    strict_identities = identity_union(strict_unique)
    primary_overlap_by_lane = overlap_breakdown(
        primary_rows,
        [
            ("active_gold", active_identities),
            ("machine_strict_ready", strict_identities),
        ],
    )
    primary_unique, primary_overlap, primary_missing = unique_lane(primary_rows, seen)
    primary_identities = identity_union(primary_unique)
    future_overlap_by_lane = overlap_breakdown(
        future_rows,
        [
            ("active_gold", active_identities),
            ("machine_strict_ready", strict_identities),
            ("primary_assignment", primary_identities),
        ],
    )
    future_unique, future_overlap, future_missing = unique_lane(future_rows, seen)

    active_counts = category_counts(active_unique)
    active_projection = projection(active_counts, minimums, pin_share_max=pin_share_max)
    machine_counts = add_counts(active_counts, strict_unique)
    machine_projection = projection(machine_counts, minimums, pin_share_max=pin_share_max)
    primary_priority, primary_shortages = select_floor_rows(
        primary_unique, machine_projection["category_shortfalls"]
    )
    primary_counts = add_counts(machine_counts, primary_priority)
    primary_projection = projection(primary_counts, minimums, pin_share_max=pin_share_max)
    future_closure, future_shortages = select_floor_rows(
        future_unique, primary_projection["category_shortfalls"]
    )
    closure_counts = add_counts(primary_counts, future_closure)
    closure_projection = projection(closure_counts, minimums, pin_share_max=pin_share_max)

    capacity_active = (balance.get("active") or {}).get("category_counts") or {}
    active_drift = {
        category: active_counts[category] - int(capacity_active.get(category, 0))
        for category in CANONICAL_CATEGORIES
        if active_counts[category] != int(capacity_active.get(category, 0))
    }
    report = {
        "schema": "eng_bench_microtext_balance_lanes_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_modified": False,
        "inputs": {
            name: {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)}
            for name, path in paths.items()
        }
        | {
            "strict_ready": {
                "path": strict_path.relative_to(root).as_posix(),
                "sha256": sha256(strict_path),
            }
        },
        "policy": {
            "lane_precedence": ["active_gold", "machine_strict_ready", "primary_assignment", "future_capacity"],
            "category_minimums": minimums,
            "pin_label_share_max": pin_share_max,
            "machine_rows_require_frozen_calibration": True,
            "primary_priority_uses_existing_assignment_only": True,
            "future_closure_rows_remain_non_gold": True,
            "no_direct_promotion": True,
        },
        "capacity_reference": {
            "active_category_count_drift": active_drift,
            "reference_capacity_clean": bool(capacity.get("capacity_input_clean")),
        },
        "lanes": {
            "active_gold": {
                "input_rows": len(active_rows),
                "unique_rows": len(active_unique),
                "overlap_rows": active_overlap,
                "missing_identity_rows": active_missing,
                "category_counts": category_counts(active_unique),
                "split_counts": split_counts(active_unique),
            },
            "machine_strict_ready": {
                "input_rows": len(strict_rows),
                "unique_rows_after_active": len(strict_unique),
                "active_overlap_rows": strict_overlap,
                "overlap_by_prior_lane": strict_overlap_by_lane,
                "missing_identity_rows": strict_missing,
                "category_counts": category_counts(strict_unique),
                "split_counts": split_counts(strict_unique),
                "calibration_required": True,
            },
            "primary_assignment": {
                "input_microtext_rows": len(primary_rows),
                "unique_rows_after_active_and_machine": len(primary_unique),
                "active_or_machine_overlap_rows": primary_overlap,
                "overlap_by_prior_lane": primary_overlap_by_lane,
                "missing_identity_rows": primary_missing,
                "category_counts": category_counts(primary_unique),
                "split_counts": split_counts(primary_unique),
                "unassigned_or_invalid_split_rows": sum(
                    not release_split(row) for row in primary_unique
                ),
            },
            "future_capacity": {
                "input_microtext_rows": len(future_rows),
                "unique_rows_after_prior_lanes": len(future_unique),
                "prior_lane_overlap_rows": future_overlap,
                "overlap_by_prior_lane": future_overlap_by_lane,
                "missing_identity_rows": future_missing,
                "category_counts": category_counts(future_unique),
                "split_counts": split_counts(future_unique),
                "unassigned_or_invalid_split_rows": sum(
                    not release_split(row) for row in future_unique
                ),
            },
        },
        "projections": {
            "active_gold": active_projection,
            "after_machine_calibration": machine_projection,
            "after_existing_primary_floor_priority": primary_projection,
            "after_future_floor_closure": closure_projection,
        },
        "selections": {
            "primary_priority_rows": len(primary_priority),
            "primary_priority_categories": category_counts(primary_priority),
            "primary_priority_splits": split_counts(primary_priority),
            "primary_unsplit_rows_not_counted": sum(
                not release_split(row) for row in primary_unique
            ),
            "primary_category_shortages": primary_shortages,
            "future_floor_closure_rows": len(future_closure),
            "future_floor_closure_categories": category_counts(future_closure),
            "future_floor_closure_splits": split_counts(future_closure),
            "future_unsplit_rows_not_counted": sum(
                not release_split(row) for row in future_unique
            ),
            "future_category_shortages": future_shortages,
        },
        "human_work_reduction": {
            "machine_rows_strict_ready_if_calibration_passes": len(strict_unique),
            "existing_primary_rows_already_active_gold": int(
                primary_overlap_by_lane.get("active_gold", 0)
            ),
            "existing_primary_rows_reclaimed_after_calibration": int(
                primary_overlap_by_lane.get("machine_strict_ready", 0)
            ),
            "future_rows_machine_owned_after_calibration": int(
                future_overlap_by_lane.get("machine_strict_ready", 0)
            ),
            "future_rows_already_covered_by_primary_assignment": int(
                future_overlap_by_lane.get("primary_assignment", 0)
            ),
            "remaining_existing_primary_floor_priority_rows": len(primary_priority),
            "future_floor_closure_rows_still_requiring_lane_decision": len(future_closure),
        },
        "status": "PASS" if closure_projection["passes_balance"] else "OPEN",
        "interpretation": (
            "This is a read-only capacity and priority ledger. Machine rows remain calibration-pending, "
            "primary rows refer to the existing assignment, and future rows remain unreviewed and unsafe "
            "to merge until their normal human or calibrated-machine lane and all promotion gates pass."
        ),
    }
    return report, primary_priority, future_closure


def write_primary_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "priority",
        "primary_index",
        "candidate_id",
        "category",
        "reserved_split",
        "proposed_text",
        "engineering_required",
        "engineering_reason",
        "action",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, row in enumerate(rows, 1):
            writer.writerow(
                {
                    "priority": index,
                    "primary_index": row.get("primary_index", ""),
                    "candidate_id": row.get("candidate_id") or row.get("record_id") or "",
                    "category": normalized_category(row),
                    "reserved_split": row.get("reserved_split") or "",
                    "proposed_text": row.get("proposed_text") or "",
                    "engineering_required": bool(row.get("engineering_required")),
                    "engineering_reason": row.get("engineering_reason") or "",
                    "action": "Use existing primary workbook; confirm return status before reissuing.",
                }
            )


def write_jsonl(
    rows: list[dict[str, Any]],
    path: Path,
    date_label: str,
    *,
    lane_status: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            staged = dict(row)
            staged["balance_lane_date_label"] = date_label
            staged["balance_lane_status"] = lane_status
            staged["safe_to_merge_gold"] = False
            handle.write(json.dumps(staged, ensure_ascii=False, sort_keys=True) + "\n")


def write_markdown(report: dict[str, Any], path: Path) -> None:
    projections = report["projections"]
    selections = report["selections"]
    lines = [
        "# Eng_Bench Gold v2.0 MicroText Balance Lanes",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Status: **{report['status']}**",
        "- Active Gold modified: `false`",
        "",
        "## Lane Counts",
        "",
        "| Lane | Unique rows | Notes |",
        "| --- | ---: | --- |",
        f"| Active Gold | {report['lanes']['active_gold']['unique_rows']} | Current release rows |",
        f"| Machine strict-ready | {report['lanes']['machine_strict_ready']['unique_rows_after_active']} | Calibration still required |",
        f"| Existing primary residual | {report['lanes']['primary_assignment']['unique_rows_after_active_and_machine']} | No new workbook required |",
        f"| Canonical future residual | {report['lanes']['future_capacity']['unique_rows_after_prior_lanes']} | Non-Gold review capacity |",
        "",
        "## Balance Projection",
        "",
        "| Projection | Rows | Pin share | Open floors | Pass |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for label, key in (
        ("Active Gold", "active_gold"),
        ("After machine calibration", "after_machine_calibration"),
        ("After existing primary priority", "after_existing_primary_floor_priority"),
        ("After future floor closure", "after_future_floor_closure"),
    ):
        value = projections[key]
        lines.append(
            f"| {label} | {value['total_rows']} | {value['pin_label_share']:.2%} | "
            f"{value['open_category_floors']} | `{value['passes_balance']}` |"
        )
    lines.extend(
        [
            "",
            "## Priority Outputs",
            "",
            f"- Existing primary rows prioritized: `{selections['primary_priority_rows']}`.",
            f"- Future floor-closing rows staged: `{selections['future_floor_closure_rows']}`.",
            "- Primary priorities point to stable indexes in the existing workbook and must not be reissued blindly.",
            "- Future rows remain `safe_to_merge_gold=false`; this report performs no promotion.",
            "",
            "## Interpretation",
            "",
            report["interpretation"],
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--capacity-report", type=Path, required=True)
    parser.add_argument("--precalibration-report", type=Path, required=True)
    parser.add_argument("--primary-payload", type=Path, required=True)
    parser.add_argument("--future-capacity", type=Path, required=True)
    parser.add_argument("--active-items", type=Path, default=Path("microtext/annotations/microtext_items.jsonl"))
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-primary-csv", type=Path, required=True)
    parser.add_argument("--output-primary-jsonl", type=Path)
    parser.add_argument("--output-future-jsonl", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report, primary_rows, future_rows = build_report(
        root,
        capacity_report=args.capacity_report,
        precalibration_report=args.precalibration_report,
        primary_payload=args.primary_payload,
        future_capacity=args.future_capacity,
        active_items=args.active_items,
        date_label=args.date_label,
    )
    output_json = resolve(root, args.output_json)
    output_md = resolve(root, args.output_md)
    output_primary = resolve(root, args.output_primary_csv)
    output_primary_jsonl = (
        resolve(root, args.output_primary_jsonl) if args.output_primary_jsonl else None
    )
    output_future = resolve(root, args.output_future_jsonl)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, output_md)
    write_primary_csv(primary_rows, output_primary)
    if output_primary_jsonl is not None:
        write_jsonl(
            primary_rows,
            output_primary_jsonl,
            args.date_label,
            lane_status="primary_floor_priority_existing_assignment_non_gold",
        )
    write_jsonl(
        future_rows,
        output_future,
        args.date_label,
        lane_status="future_floor_closure_non_gold",
    )
    print(json.dumps({
        "status": report["status"],
        "active_gold_modified": False,
        "human_work_reduction": report["human_work_reduction"],
        "selections": report["selections"],
        "final_projection": report["projections"]["after_future_floor_closure"],
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
