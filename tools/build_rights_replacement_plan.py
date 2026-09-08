#!/usr/bin/env python3
"""Plan review-only replacements for active rows tied to rights-blocked sources."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


VALID_SPLITS = {"train", "dev", "test"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def scalar_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for nested in value.values() for item in scalar_values(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in scalar_values(nested)]
    return [str(value)] if value not in (None, "") else []


def version_matches(manifest: dict[str, Any], pair_version: Any) -> bool:
    pair_token = normalize(pair_version)
    if not pair_token:
        return False
    for value in scalar_values(manifest.get("version") or {}):
        token = normalize(value)
        if token and (token in pair_token or pair_token in token):
            return True
    doc_token = normalize(manifest.get("doc_id"))
    return bool(doc_token and pair_token in doc_token)


def visualdiff_context_matches(manifest: dict[str, Any], pair: dict[str, Any]) -> bool:
    pair_context = normalize(pair.get("doc_id") or pair.get("project_id"))
    model_context = normalize(manifest.get("same_model_id") or manifest.get("doc_id"))
    if pair_context and pair_context in model_context:
        return True
    project_context = normalize(pair.get("project_id"))
    doc_id = normalize(manifest.get("doc_id"))
    return bool(project_context and doc_id and any(token in project_context for token in re.findall(r"[a-z]+", doc_id) if len(token) >= 5))


def blocked_docs_for_pair(
    pair: dict[str, Any],
    blocked_visual_docs: list[dict[str, Any]],
) -> list[str]:
    matched: list[str] = []
    for manifest in blocked_visual_docs:
        if not visualdiff_context_matches(manifest, pair):
            continue
        if any(
            version_matches(manifest, value)
            for value in (
                pair.get("version_id_old"),
                pair.get("version_id_new"),
                pair.get("project_id"),
            )
        ):
            matched.append(str(manifest.get("doc_id") or ""))
    return sorted(set(matched))


def row_identity(row: dict[str, Any]) -> str:
    for key in ("pair_id", "candidate_id", "item_id", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def row_task(row: dict[str, Any]) -> str:
    task = str(row.get("task") or "").strip().lower()
    if task in {"microtext", "visualdiff"}:
        return task
    return "visualdiff" if row.get("pair_id") or row.get("project_id") else "microtext"


def row_unit(row: dict[str, Any]) -> str:
    if row_task(row) == "visualdiff":
        return str(row.get("project_id") or row.get("pair_id") or "").strip()
    return str(row.get("doc_id") or "").strip()


def reservation_splits(split_plan: dict[str, Any]) -> tuple[dict[tuple[str, str], str], list[str]]:
    mapping: dict[tuple[str, str], str] = {}
    issues: list[str] = []
    for reservation in split_plan.get("reservations") or []:
        task = str(reservation.get("task") or "").strip().lower()
        unit_id = str(reservation.get("unit_id") or "").strip()
        split = str(reservation.get("split") or "").strip().lower()
        if not task or not unit_id or split not in VALID_SPLITS:
            continue
        key = (task, unit_id)
        prior = mapping.get(key)
        if prior and prior != split:
            issues.append(f"conflicting split reservation for {task}:{unit_id}: {prior} vs {split}")
        mapping[key] = split
    return mapping, issues


def staged_split(row: dict[str, Any], reservations: dict[tuple[str, str], str]) -> str:
    direct = str(row.get("reserved_split") or row.get("split") or "").strip().lower()
    if direct in VALID_SPLITS:
        return direct
    return reservations.get((row_task(row), row_unit(row)), "")


def select_diverse(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(rows, key=lambda item: (row_unit(item), row_identity(item))):
        by_unit[row_unit(row)].append(row)
    selected: list[dict[str, Any]] = []
    units = sorted(by_unit)
    while units and len(selected) < limit:
        next_units: list[str] = []
        for unit in units:
            if len(selected) >= limit:
                break
            selected.append(by_unit[unit].pop(0))
            if by_unit[unit]:
                next_units.append(unit)
        units = next_units
    return selected


def build_plan(
    root: Path,
    provenance_report: Path,
    current_staged: Path,
    future_staged: Path,
    split_plan_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    provenance = read_json(provenance_report)
    blocked_doc_rows = [row for row in provenance.get("documents") or [] if row.get("blocker")]
    blocked_doc_ids = {str(row.get("doc_id") or "") for row in blocked_doc_rows}
    manifest_rows = read_jsonl(root / "manifest.jsonl")
    manifest_by_doc = {
        str(row.get("doc_id") or ""): row
        for row in manifest_rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    blocked_visual_docs = [
        manifest_by_doc[doc_id]
        for doc_id in sorted(blocked_doc_ids)
        if doc_id in manifest_by_doc
        and (
            manifest_by_doc[doc_id].get("same_model_id")
            or manifest_by_doc[doc_id].get("version")
        )
    ]

    affected_rows: list[dict[str, Any]] = []
    for item in read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl"):
        doc_id = str(item.get("doc_id") or "")
        if doc_id not in blocked_doc_ids:
            continue
        affected_rows.append(
            {
                "id": str(item.get("item_id") or item.get("id") or ""),
                "task": "microtext",
                "split": str(item.get("split") or ""),
                "blocked_doc_ids": [doc_id],
            }
        )
    unmatched_visualdiff: list[str] = []
    for pair in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"):
        docs = blocked_docs_for_pair(pair, blocked_visual_docs)
        if docs:
            affected_rows.append(
                {
                    "id": str(pair.get("pair_id") or pair.get("id") or ""),
                    "task": "visualdiff",
                    "split": str(pair.get("split") or ""),
                    "blocked_doc_ids": docs,
                }
            )
        elif any(visualdiff_context_matches(doc, pair) for doc in blocked_visual_docs):
            unmatched_visualdiff.append(str(pair.get("pair_id") or ""))

    reservations, issues = reservation_splits(read_json(split_plan_path))
    if unmatched_visualdiff:
        issues.append(f"{len(unmatched_visualdiff)} visualdiff rows matched a held family but not a held version")
    staged_rows: list[dict[str, Any]] = []
    seen_staged: set[str] = set()
    for phase, path in (("current", current_staged), ("future", future_staged)):
        for row in read_jsonl(path):
            identity = row_identity(row)
            if not identity or identity in seen_staged:
                continue
            seen_staged.add(identity)
            split = staged_split(row, reservations)
            if split not in VALID_SPLITS:
                issues.append(f"staged row lacks split reservation: {identity}")
                continue
            prepared = dict(row)
            prepared["replacement_capacity_phase"] = phase
            prepared["replacement_reserved_split"] = split
            prepared["safe_to_merge_gold"] = False
            staged_rows.append(prepared)

    need = Counter((row["task"], row["split"]) for row in affected_rows)
    available: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in staged_rows:
        available[(row_task(row), row["replacement_reserved_split"])].append(row)

    selected: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for task in ("microtext", "visualdiff"):
        for split in ("train", "dev", "test"):
            required = int(need.get((task, split), 0))
            candidates = available.get((task, split), [])
            chosen = select_diverse(candidates, required)
            for row in chosen:
                row["replacement_target_task"] = task
                row["replacement_target_split"] = split
                row["replacement_plan_status"] = "awaiting_human_review"
            selected.extend(chosen)
            coverage.append(
                {
                    "task": task,
                    "split": split,
                    "active_rows_requiring_replacement": required,
                    "rights_cleared_staged_rows_available": len(candidates),
                    "review_only_rows_selected": len(chosen),
                    "residual_rows_needed": max(0, required - len(chosen)),
                }
            )

    report = {
        "provenance_report": provenance_report.resolve().as_posix(),
        "blocked_active_source_docs": len(blocked_doc_ids),
        "blocked_doc_ids": sorted(blocked_doc_ids),
        "active_rows_requiring_replacement": len(affected_rows),
        "active_rows_by_task": dict(sorted(Counter(row["task"] for row in affected_rows).items())),
        "active_rows_by_split": dict(sorted(Counter(row["split"] for row in affected_rows).items())),
        "active_rows_by_task_split": {
            f"{task}:{split}": count for (task, split), count in sorted(need.items())
        },
        "rights_cleared_staged_rows": len(staged_rows),
        "review_only_replacement_rows_selected": len(selected),
        "residual_replacement_rows_needed": sum(row["residual_rows_needed"] for row in coverage),
        "coverage": coverage,
        "issues": issues,
        "interpretation": (
            "Selected rows are rights-cleared review capacity, not gold. Replacement requires human acceptance, "
            "source-family split locks, duplicate checks, provenance, leakage checks, and strict v2 validation."
        ),
    }
    return report, affected_rows, selected


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Rights-Blocked Active-Row Replacement Plan",
        "",
        f"- Blocked active source documents: {report['blocked_active_source_docs']}",
        f"- Active rows requiring replacement: {report['active_rows_requiring_replacement']}",
        f"- Review-only replacements selected: {report['review_only_replacement_rows_selected']}",
        f"- Residual rows still needed: {report['residual_replacement_rows_needed']}",
        "",
        "| Task | Split | Active rows | Staged available | Selected | Residual |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["coverage"]:
        lines.append(
            "| {task} | {split} | {active_rows_requiring_replacement} | "
            "{rights_cleared_staged_rows_available} | {review_only_rows_selected} | "
            "{residual_rows_needed} |".format(**row)
        )
    lines.extend(["", report["interpretation"], ""])
    if report["issues"]:
        lines.extend(["## Issues", "", *[f"- {issue}" for issue in report["issues"]], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan replacements for rights-blocked active rows")
    parser.add_argument("--root", default=".")
    parser.add_argument("--provenance-report", required=True)
    parser.add_argument("--current-staged", required=True)
    parser.add_argument("--future-staged", required=True)
    parser.add_argument("--split-plan", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--affected-jsonl", required=True)
    parser.add_argument("--selected-jsonl", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    report, affected, selected = build_plan(
        root,
        resolve(args.provenance_report),
        resolve(args.current_staged),
        resolve(args.future_staged),
        resolve(args.split_plan),
    )
    write_json(resolve(args.output_json), report)
    write_markdown(resolve(args.output_md), report)
    write_jsonl(resolve(args.affected_jsonl), affected)
    write_jsonl(resolve(args.selected_jsonl), selected)
    print(json.dumps({key: report[key] for key in ("blocked_active_source_docs", "active_rows_requiring_replacement", "review_only_replacement_rows_selected", "residual_replacement_rows_needed", "issues")}, indent=2))
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
