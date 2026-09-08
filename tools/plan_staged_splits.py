#!/usr/bin/env python3
"""Plan leakage-safe split reservations for staged Eng_Bench review cohorts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import manifest_maps, read_jsonl, resolve_visualdiff_docs


SPLITS = ("train", "dev", "test")


def parse_forced_split(value: str) -> tuple[str, str]:
    assignment, separator, split = value.rpartition("=")
    task, task_separator, unit_id = assignment.partition(":")
    task = task.strip().lower()
    unit_id = unit_id.strip()
    split = split.strip().lower()
    if (
        not separator
        or not task_separator
        or task not in {"microtext", "visualdiff"}
        or not unit_id
        or split not in SPLITS
    ):
        raise argparse.ArgumentTypeError(
            "forced split must use TASK:UNIT_ID=SPLIT with task microtext/visualdiff "
            "and split train/dev/test"
        )
    return f"{task}:{unit_id}", split


class DisjointSet:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, value: str) -> None:
        self.parent.setdefault(value, value)

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        self.add(left)
        self.add(right)
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root


def read_split_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    return read_jsonl(path)


def task_for_row(row: dict[str, Any]) -> str:
    return "visualdiff" if row.get("pair_id") or (row.get("image_old") and row.get("image_new")) else "microtext"


def visualdiff_project_id(row: dict[str, Any]) -> str:
    project_id = str(row.get("project_id") or "").strip()
    if project_id:
        return project_id
    pair_id = str(row.get("pair_id") or row.get("id") or "").strip()
    stem, separator, suffix = pair_id.rpartition("__")
    return stem if separator and suffix.isdigit() else pair_id


def row_identity(row: dict[str, Any]) -> str:
    if task_for_row(row) == "visualdiff":
        value = str(row.get("pair_id") or row.get("id") or "").strip()
        return f"visualdiff:{value}" if value else ""
    value = str(row.get("candidate_id") or row.get("item_id") or "").strip()
    return f"microtext:{value}" if value else ""


def source_signals(doc_id: str, docs: dict[str, dict[str, Any]]) -> set[str]:
    row = docs.get(doc_id, {})
    signals = {f"doc_id:{doc_id}"}
    for field, prefix in (
        ("sha256", "sha256"),
        ("source_candidate_id", "source_candidate_id"),
        ("same_model_id", "same_model_id"),
        ("source_url", "source_url"),
    ):
        value = str(row.get(field) or "").strip()
        if value:
            signals.add(f"{prefix}:{value.casefold()}")
    return signals


def target_counts(total: int, ratios: dict[str, float]) -> dict[str, int]:
    raw = {split: total * ratios[split] for split in SPLITS}
    result = {split: int(raw[split]) for split in SPLITS}
    remaining = total - sum(result.values())
    order = sorted(SPLITS, key=lambda split: (-(raw[split] - result[split]), SPLITS.index(split)))
    for split in order[:remaining]:
        result[split] += 1
    return result


def desired_staged_counts(
    staged_rows: int,
    current_counts: dict[str, int],
    release_targets: dict[str, int],
) -> dict[str, float]:
    deficits = {
        split: max(0, release_targets[split] - current_counts.get(split, 0))
        for split in SPLITS
    }
    total_deficit = sum(deficits.values())
    if not total_deficit:
        return {split: staged_rows / len(SPLITS) for split in SPLITS}
    return {
        split: staged_rows * deficits[split] / total_deficit
        for split in SPLITS
    }


def build_plan(
    root: Path,
    capacity_report: Path,
    row_target: int = 25_000,
    ratios: dict[str, float] | None = None,
    date_label: str = "manual",
    prior_plan: Path | None = None,
    forced_splits: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    capacity_report = capacity_report if capacity_report.is_absolute() else root / capacity_report
    ratios = ratios or {"train": 0.6, "dev": 0.2, "test": 0.2}
    if set(ratios) != set(SPLITS) or any(value < 0 for value in ratios.values()):
        raise ValueError("split ratios must define non-negative train, dev, and test values")
    if abs(sum(ratios.values()) - 1.0) > 1e-9:
        raise ValueError("split ratios must sum to 1")

    payload = json.loads(capacity_report.read_text(encoding="utf-8"))
    docs, manifest_pairs = manifest_maps(root)
    dsu = DisjointSet()
    units: dict[str, dict[str, Any]] = {}
    signal_owner: dict[str, str] = {}
    issues: list[dict[str, Any]] = []
    forced_assignments: dict[str, str] = {}
    for key, split in sorted((forced_splits or {}).items()):
        task, separator, unit_id = str(key).partition(":")
        normalized_split = str(split).strip().lower()
        if (
            not separator
            or task not in {"microtext", "visualdiff"}
            or not unit_id.strip()
            or normalized_split not in SPLITS
        ):
            issues.append(
                {
                    "type": "invalid_forced_reservation",
                    "unit": str(key),
                    "split": str(split),
                }
            )
            continue
        forced_assignments[f"{task}:{unit_id.strip()}"] = normalized_split
    prior_assignments: dict[str, str] = {}
    prior_plan_path: Path | None = None
    if prior_plan is not None:
        prior_plan_path = prior_plan if prior_plan.is_absolute() else root / prior_plan
        if not prior_plan_path.is_file():
            issues.append(
                {"type": "missing_prior_plan", "path": prior_plan_path.as_posix()}
            )
        else:
            prior_payload = json.loads(prior_plan_path.read_text(encoding="utf-8"))
            for reservation in prior_payload.get("reservations", []):
                task = str(reservation.get("task") or "").strip()
                unit_id = str(reservation.get("unit_id") or "").strip()
                split = str(reservation.get("split") or "").strip()
                if not task or not unit_id or split not in SPLITS:
                    issues.append(
                        {
                            "type": "invalid_prior_reservation",
                            "task": task,
                            "unit_id": unit_id,
                            "split": split,
                        }
                    )
                    continue
                key = f"{task}:{unit_id}"
                existing = prior_assignments.get(key)
                if existing and existing != split:
                    issues.append(
                        {
                            "type": "conflicting_prior_reservation",
                            "unit": key,
                            "splits": sorted({existing, split}),
                        }
                    )
                    continue
                prior_assignments[key] = split

    def ensure_unit(task: str, unit_id: str) -> dict[str, Any]:
        key = f"{task}:{unit_id}"
        dsu.add(key)
        return units.setdefault(
            key,
            {
                "key": key,
                "task": task,
                "unit_id": unit_id,
                "source_docs": set(),
                "signals": set(),
                "fixed_splits": set(),
                "forced_splits": set(),
                "prior_splits": {prior_assignments[key]} if key in prior_assignments else set(),
                "row_count": 0,
                "categories": Counter(),
                "cohorts": set(),
            },
        )

    def bind_sources(unit: dict[str, Any], source_docs: list[str]) -> None:
        for doc_id in source_docs:
            unit["source_docs"].add(doc_id)
            for signal in source_signals(doc_id, docs):
                unit["signals"].add(signal)
                owner = signal_owner.get(signal)
                if owner:
                    dsu.union(unit["key"], owner)
                else:
                    signal_owner[signal] = unit["key"]

    for split in SPLITS:
        for doc_id in read_split_file(root / "splits" / f"microtext_{split}.txt"):
            unit = ensure_unit("microtext", doc_id)
            unit["fixed_splits"].add(split)
            bind_sources(unit, [doc_id])
        for project_id in read_split_file(root / "splits" / f"visualdiff_{split}.txt"):
            unit = ensure_unit("visualdiff", project_id)
            unit["fixed_splits"].add(split)
            source_docs, reason = resolve_visualdiff_docs(
                {"project_id": project_id}, docs, manifest_pairs
            )
            if reason:
                issues.append(
                    {
                        "type": "unresolved_active_visualdiff_sources",
                        "unit_id": project_id,
                        "reason": reason,
                    }
                )
            bind_sources(unit, source_docs)

    seen_rows: dict[str, str] = {}
    staged_rows = 0
    for cohort in payload.get("cohorts", []):
        cohort_name = str(cohort.get("name") or "")
        cohort_path = Path(str(cohort.get("path") or ""))
        absolute = cohort_path if cohort_path.is_absolute() else root / cohort_path
        if not absolute.is_file():
            issues.append(
                {"type": "missing_cohort_file", "cohort": cohort_name, "path": absolute.as_posix()}
            )
            continue
        for row in read_rows(absolute):
            identity = row_identity(row)
            if not identity:
                issues.append({"type": "missing_row_identity", "cohort": cohort_name})
                continue
            if identity in seen_rows:
                issues.append(
                    {
                        "type": "duplicate_staged_identity",
                        "identity": identity,
                        "first_cohort": seen_rows[identity],
                        "second_cohort": cohort_name,
                    }
                )
                continue
            seen_rows[identity] = cohort_name
            task = task_for_row(row)
            if task == "microtext":
                unit_id = str(row.get("doc_id") or row.get("source_doc_id") or "").strip()
                source_docs = [unit_id] if unit_id else []
            else:
                unit_id = visualdiff_project_id(row)
                source_docs, reason = resolve_visualdiff_docs(row, docs, manifest_pairs)
                if reason:
                    issues.append(
                        {
                            "type": "unresolved_staged_visualdiff_sources",
                            "identity": identity,
                            "reason": reason,
                        }
                    )
            if not unit_id or not source_docs:
                issues.append(
                    {"type": "missing_staged_split_unit", "identity": identity, "cohort": cohort_name}
                )
                continue
            unit = ensure_unit(task, unit_id)
            bind_sources(unit, source_docs)
            unit["row_count"] += 1
            unit["cohorts"].add(cohort_name)
            unit["categories"][str(row.get("category") or row.get("change_type") or "unknown")] += 1
            staged_rows += 1

    for key, split in forced_assignments.items():
        unit = units.get(key)
        if unit is None or unit["row_count"] <= 0:
            issues.append(
                {
                    "type": "forced_unit_not_found",
                    "unit": key,
                    "split": split,
                }
            )
            continue
        unit["forced_splits"].add(split)

    components: dict[str, dict[str, Any]] = {}
    for key, unit in units.items():
        root_key = dsu.find(key)
        component = components.setdefault(
            root_key,
            {
                "unit_keys": set(),
                "fixed_splits": set(),
                "forced_splits": set(),
                "prior_splits": set(),
                "row_count": 0,
                "source_docs": set(),
                "signals": set(),
                "categories": Counter(),
                "cohorts": set(),
            },
        )
        component["unit_keys"].add(key)
        component["fixed_splits"].update(unit["fixed_splits"])
        component["forced_splits"].update(unit["forced_splits"])
        component["prior_splits"].update(unit["prior_splits"])
        component["row_count"] += unit["row_count"]
        component["source_docs"].update(unit["source_docs"])
        component["signals"].update(unit["signals"])
        component["categories"].update(unit["categories"])
        component["cohorts"].update(unit["cohorts"])

    staged_components = {
        key: value for key, value in components.items() if value["row_count"] > 0
    }
    for key, component in staged_components.items():
        if len(component["fixed_splits"]) > 1:
            issues.append(
                {
                    "type": "active_split_conflict",
                    "component": key,
                    "splits": sorted(component["fixed_splits"]),
                    "units": sorted(component["unit_keys"]),
                }
            )
        if len(component["forced_splits"]) > 1:
            issues.append(
                {
                    "type": "forced_split_conflict",
                    "component": key,
                    "splits": sorted(component["forced_splits"]),
                    "units": sorted(component["unit_keys"]),
                }
            )
        if len(component["prior_splits"]) > 1:
            issues.append(
                {
                    "type": "prior_plan_split_conflict",
                    "component": key,
                    "splits": sorted(component["prior_splits"]),
                    "units": sorted(component["unit_keys"]),
                }
            )
        if (
            len(component["fixed_splits"]) == 1
            and len(component["forced_splits"]) == 1
            and component["fixed_splits"] != component["forced_splits"]
        ):
            issues.append(
                {
                    "type": "forced_active_split_conflict",
                    "component": key,
                    "active_split": next(iter(component["fixed_splits"])),
                    "forced_split": next(iter(component["forced_splits"])),
                    "units": sorted(component["unit_keys"]),
                }
            )
        if (
            len(component["forced_splits"]) == 1
            and len(component["prior_splits"]) == 1
            and component["forced_splits"] != component["prior_splits"]
        ):
            issues.append(
                {
                    "type": "forced_prior_plan_override",
                    "component": key,
                    "prior_split": next(iter(component["prior_splits"])),
                    "forced_split": next(iter(component["forced_splits"])),
                    "units": sorted(component["unit_keys"]),
                }
            )
        if (
            len(component["fixed_splits"]) == 1
            and len(component["prior_splits"]) == 1
            and component["fixed_splits"] != component["prior_splits"]
        ):
            issues.append(
                {
                    "type": "prior_plan_active_split_conflict",
                    "component": key,
                    "active_split": next(iter(component["fixed_splits"])),
                    "prior_split": next(iter(component["prior_splits"])),
                    "units": sorted(component["unit_keys"]),
                }
            )

    current_rows = read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    current_rows += read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    current_counts = Counter(str(row.get("split") or "unassigned") for row in current_rows)
    release_targets = target_counts(row_target, ratios)
    desired = desired_staged_counts(staged_rows, dict(current_counts), release_targets)
    assigned: Counter[str] = Counter()
    component_split: dict[str, str] = {}

    for key, component in sorted(staged_components.items()):
        if len(component["fixed_splits"]) == 1:
            split = next(iter(component["fixed_splits"]))
            component_split[key] = split
            assigned[split] += component["row_count"]
        elif len(component["forced_splits"]) == 1:
            split = next(iter(component["forced_splits"]))
            component_split[key] = split
            assigned[split] += component["row_count"]
        elif len(component["prior_splits"]) == 1:
            split = next(iter(component["prior_splits"]))
            component_split[key] = split
            assigned[split] += component["row_count"]

    pending = [
        (key, component)
        for key, component in staged_components.items()
        if not component["fixed_splits"]
        and not component["forced_splits"]
        and not component["prior_splits"]
    ]
    pending.sort(key=lambda item: (-item[1]["row_count"], sorted(item[1]["unit_keys"])))
    for key, component in pending:
        rows = component["row_count"]
        scores: dict[str, float] = {}
        for candidate in SPLITS:
            score = 0.0
            for split in SPLITS:
                value = assigned[split] + (rows if split == candidate else 0)
                score += ((value - desired[split]) ** 2) / max(desired[split], 1.0)
            scores[candidate] = score
        split = min(SPLITS, key=lambda value: (scores[value], SPLITS.index(value)))
        component_split[key] = split
        assigned[split] += rows

    reservations: list[dict[str, Any]] = []
    for key, unit in sorted(units.items(), key=lambda item: (item[1]["task"], item[1]["unit_id"])):
        if unit["row_count"] <= 0:
            continue
        component_key = dsu.find(key)
        component = staged_components[component_key]
        split = component_split.get(component_key, "")
        reservation_id = hashlib.sha256(
            "\n".join(sorted(component["unit_keys"])).encode("utf-8")
        ).hexdigest()[:16]
        reservations.append(
            {
                "reservation_id": reservation_id,
                "task": unit["task"],
                "unit_id": unit["unit_id"],
                "split": split,
                "assignment_basis": (
                    "active_family_lock"
                    if component["fixed_splits"]
                    else "forced_review_reservation"
                    if component["forced_splits"]
                    else "prior_plan_lock"
                    if component["prior_splits"]
                    else "planned_deficit_balance"
                ),
                "row_count": unit["row_count"],
                "source_docs": sorted(unit["source_docs"]),
                "cohorts": sorted(unit["cohorts"]),
                "categories": dict(sorted(unit["categories"].items())),
                "component_units": sorted(component["unit_keys"]),
            }
        )

    category_by_split: dict[str, Counter[str]] = {split: Counter() for split in SPLITS}
    units_by_split: Counter[str] = Counter()
    source_docs_by_split: dict[str, set[str]] = {split: set() for split in SPLITS}
    for reservation in reservations:
        split = reservation["split"]
        if not split:
            continue
        units_by_split[split] += 1
        source_docs_by_split[split].update(reservation["source_docs"])
        category_by_split[split].update(reservation["categories"])

    fatal_types = {
        "missing_cohort_file",
        "missing_row_identity",
        "duplicate_staged_identity",
        "unresolved_staged_visualdiff_sources",
        "missing_staged_split_unit",
        "active_split_conflict",
        "missing_prior_plan",
        "invalid_prior_reservation",
        "conflicting_prior_reservation",
        "prior_plan_split_conflict",
        "prior_plan_active_split_conflict",
        "invalid_forced_reservation",
        "forced_unit_not_found",
        "forced_split_conflict",
        "forced_active_split_conflict",
    }
    fatal_issues = [issue for issue in issues if issue["type"] in fatal_types]
    return {
        "valid": not fatal_issues,
        "date_label": date_label,
        "root": root.as_posix(),
        "capacity_report": capacity_report.relative_to(root).as_posix()
        if capacity_report.is_relative_to(root)
        else capacity_report.as_posix(),
        "prior_plan": {
            "applied": prior_plan_path is not None and prior_plan_path.is_file(),
            "path": (
                prior_plan_path.relative_to(root).as_posix()
                if prior_plan_path is not None
                and prior_plan_path.is_file()
                and prior_plan_path.is_relative_to(root)
                else prior_plan_path.as_posix()
                if prior_plan_path is not None
                else None
            ),
            "unit_locks": len(prior_assignments),
        },
        "forced_splits": {
            "requested": len(forced_assignments),
            "assignments": dict(sorted(forced_assignments.items())),
        },
        "targets": {
            "row_target": row_target,
            "ratios": ratios,
            "release_split_rows": release_targets,
        },
        "current": {
            "rows": len(current_rows),
            "rows_by_split": {split: current_counts[split] for split in SPLITS},
        },
        "staged": {
            "rows": staged_rows,
            "row_identities": len(seen_rows),
            "components": len(staged_components),
            "units": len(reservations),
            "desired_rows_by_split": {split: round(desired[split], 3) for split in SPLITS},
            "reserved_rows_by_split": {split: assigned[split] for split in SPLITS},
            "reserved_units_by_split": {split: units_by_split[split] for split in SPLITS},
            "reserved_source_docs_by_split": {
                split: len(source_docs_by_split[split]) for split in SPLITS
            },
            "categories_by_split": {
                split: dict(sorted(category_by_split[split].items())) for split in SPLITS
            },
            "projected_rows_by_split": {
                split: current_counts[split] + assigned[split] for split in SPLITS
            },
            "projected_test_rows": current_counts["test"] + assigned["test"],
            "remaining_test_rows_to_5000": max(
                0, 5000 - current_counts["test"] - assigned["test"]
            ),
        },
        "issues": issues,
        "fatal_issue_count": len(fatal_issues),
        "reservations": reservations,
        "interpretation": (
            "Reservations do not change gold or frozen split files. They become enforceable only "
            "after human acceptance and strict promotion; connected source hashes, source candidates, "
            "same-model families, URLs, and document IDs are assigned together."
        ),
    }


def write_outputs(report: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = [
        "reservation_id",
        "task",
        "unit_id",
        "split",
        "assignment_basis",
        "row_count",
        "source_docs",
        "cohorts",
        "categories",
        "component_units",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for reservation in report["reservations"]:
            writer.writerow(
                {
                    **reservation,
                    "source_docs": ";".join(reservation["source_docs"]),
                    "cohorts": ";".join(reservation["cohorts"]),
                    "categories": json.dumps(reservation["categories"], sort_keys=True),
                    "component_units": ";".join(reservation["component_units"]),
                }
            )
    staged = report["staged"]
    lines = [
        "# Staged Split Reservation Plan",
        "",
        f"- Valid: `{str(report['valid']).lower()}`",
        f"- Staged rows: `{staged['rows']}`",
        f"- Staged split units: `{staged['units']}`",
        f"- Leakage-connected components: `{staged['components']}`",
        f"- Fatal issues: `{report['fatal_issue_count']}`",
        f"- Current test rows: `{report['current']['rows_by_split']['test']}`",
        f"- Reserved staged test rows: `{staged['reserved_rows_by_split']['test']}`",
        f"- Projected test rows after 100% acceptance: `{staged['projected_test_rows']}`",
        f"- Remaining to 5,000 test rows: `{staged['remaining_test_rows_to_5000']}`",
        "",
        "| Split | Desired Staged Rows | Reserved Rows | Units | Source Docs | Projected Gold Rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for split in SPLITS:
        lines.append(
            f"| {split} | {staged['desired_rows_by_split'][split]:.3f} | "
            f"{staged['reserved_rows_by_split'][split]} | {staged['reserved_units_by_split'][split]} | "
            f"{staged['reserved_source_docs_by_split'][split]} | {staged['projected_rows_by_split'][split]} |"
        )
    lines.extend(["", "## Category Reservations", ""])
    for split in SPLITS:
        categories = ", ".join(
            f"`{name}`={count}" for name, count in staged["categories_by_split"][split].items()
        ) or "none"
        lines.append(f"- {split}: {categories}")
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        for issue in report["issues"]:
            lines.append(f"- `{issue['type']}`: `{json.dumps(issue, ensure_ascii=False, sort_keys=True)}`")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--capacity-report", type=Path, required=True)
    parser.add_argument("--row-target", type=int, default=25_000)
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--dev-ratio", type=float, default=0.2)
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--date-label", default="manual")
    parser.add_argument(
        "--prior-plan",
        type=Path,
        help="Optional previous reservation plan whose existing unit assignments must remain fixed.",
    )
    parser.add_argument(
        "--force-split",
        action="append",
        type=parse_forced_split,
        default=[],
        metavar="TASK:UNIT_ID=SPLIT",
        help=(
            "Force an unpromoted staged unit into a split. Overrides a prior review plan but "
            "never an active split; may be repeated."
        ),
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)

    forced_splits: dict[str, str] = {}
    for key, split in args.force_split:
        existing = forced_splits.get(key)
        if existing and existing != split:
            parser.error(f"conflicting --force-split values for {key}: {existing} vs {split}")
        forced_splits[key] = split

    root = args.root.resolve()
    report = build_plan(
        root,
        args.capacity_report,
        row_target=args.row_target,
        ratios={"train": args.train_ratio, "dev": args.dev_ratio, "test": args.test_ratio},
        date_label=args.date_label,
        prior_plan=args.prior_plan,
        forced_splits=forced_splits,
    )
    outputs = []
    for path in (args.output_json, args.output_csv, args.output_md):
        outputs.append(path if path.is_absolute() else root / path)
    write_outputs(report, outputs[0], outputs[1], outputs[2])
    print(json.dumps({"valid": report["valid"], **report["staged"]}, indent=2, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
