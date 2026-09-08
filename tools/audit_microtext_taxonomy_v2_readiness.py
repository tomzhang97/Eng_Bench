#!/usr/bin/env python3
"""Audit staged component-value and process-label rows for canonical v2 use.

The audit is read-only. It separates evidence-complete, release-safe, split-
reserved rows from taxonomy or promotion holds and never changes active Gold.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_active_gold_provenance import file_sha256, manifest_maps, read_csv
from audit_staged_promotion_contract import source_audit
from mine_microtext_candidates import COMPONENT_VALUE_RE, usable_process_label
from prepare_incremental_human_audit_round import evidence_materializable


CATEGORY_FLOORS = {"component_value": 300, "process_label": 150}
VALID_SPLITS = {"train", "dev", "test"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def identity(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("record_id") or row.get("item_id") or "").strip()


def label_text(row: dict[str, Any]) -> str:
    for field in ("corrected_text", "proposed_text", "target_text", "raw_text", "answer"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def taxonomy_text_valid(category: str, value: str) -> bool:
    if category == "component_value":
        return bool(COMPONENT_VALUE_RE.fullmatch(value))
    if category == "process_label":
        return usable_process_label(value)
    return False


def audit_rows(
    root: Path,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    docs, _ = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if str(row.get("doc_id") or "").strip()
    }
    source_cache: dict[str, list[str]] = {}
    seen: set[str] = set()
    qualified: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    input_counts: Counter[str] = Counter()
    qualified_counts: Counter[str] = Counter()
    split_counts: Counter[tuple[str, str]] = Counter()
    hold_counts: Counter[str] = Counter()
    source_docs: defaultdict[str, set[str]] = defaultdict(set)

    for row in rows:
        category = str(row.get("category") or "").strip()
        if category not in CATEGORY_FLOORS:
            continue
        input_counts[category] += 1
        row_id = identity(row)
        value = label_text(row)
        doc_id = str(row.get("doc_id") or row.get("document_id") or "").strip()
        split = str(row.get("reserved_split") or row.get("split") or "").strip().lower()
        reasons: list[str] = []
        if not row_id:
            reasons.append("missing_identity")
        elif row_id in seen:
            reasons.append("duplicate_identity")
        else:
            seen.add(row_id)
        if row.get("safe_to_merge_gold") is not False:
            reasons.append("unsafe_merge_flag")
        if not taxonomy_text_valid(category, value):
            reasons.append("taxonomy_text_rule_failed")
        if split not in VALID_SPLITS:
            reasons.append("missing_or_invalid_reserved_split")
        if not evidence_materializable(root, row):
            reasons.append("missing_or_invalid_evidence")
        if not doc_id:
            reasons.append("missing_doc_id")
        else:
            if doc_id not in source_cache:
                source_cache[doc_id] = source_audit(root, doc_id, docs, inventory)
            reasons.extend(f"source:{reason}" for reason in source_cache[doc_id])
        if reasons:
            held_row = dict(row)
            held_row["taxonomy_v2_hold_reasons"] = sorted(set(reasons))
            held_row["taxonomy_v2_status"] = "held"
            held_row["safe_to_merge_gold"] = False
            held.append(held_row)
            hold_counts.update(held_row["taxonomy_v2_hold_reasons"])
            continue
        accepted = dict(row)
        accepted["taxonomy_v2_status"] = "machine_qualified_pending_human_review"
        accepted["taxonomy_v2_category"] = category
        accepted["safe_to_merge_gold"] = False
        qualified.append(accepted)
        qualified_counts[category] += 1
        split_counts[(category, split)] += 1
        source_docs[category].add(doc_id)

    category_readiness = {
        category: {
            "floor": floor,
            "qualified_rows": qualified_counts[category],
            "qualified_source_docs": len(source_docs[category]),
            "passes_capacity_floor": qualified_counts[category] >= floor,
        }
        for category, floor in CATEGORY_FLOORS.items()
    }
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "read_only_taxonomy_capacity_audit",
        "taxonomy_categories": sorted(CATEGORY_FLOORS),
        "input_rows": sum(input_counts.values()),
        "input_category_counts": dict(sorted(input_counts.items())),
        "qualified_rows": len(qualified),
        "qualified_category_counts": dict(sorted(qualified_counts.items())),
        "qualified_task_split_counts": {
            f"{category}:{split}": count
            for (category, split), count in sorted(split_counts.items())
        },
        "held_rows": len(held),
        "hold_reason_counts": dict(sorted(hold_counts.items())),
        "category_readiness": category_readiness,
        "taxonomy_capacity_ready": all(
            item["passes_capacity_floor"] for item in category_readiness.values()
        ),
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "interpretation": (
            "Qualified rows establish review capacity for two canonical engineering MicroText categories. "
            "They remain non-Gold until human acceptance and strict promotion. Held rows do not count."
        ),
    }
    return qualified, held, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MicroText Taxonomy v2 Readiness",
        "",
        "- Goal: **Gold v2.0 Global**",
        f"- Input taxonomy rows: `{report['input_rows']}`",
        f"- Machine-qualified pending human review: `{report['qualified_rows']}`",
        f"- Held rows: `{report['held_rows']}`",
        f"- Taxonomy capacity ready: `{str(report['taxonomy_capacity_ready']).lower()}`",
        "- Active Gold modified: `false`",
        "",
        "| Category | Qualified | Sources | Floor | Capacity floor passes |",
        "|---|---:|---:|---:|---|",
    ]
    for category, item in report["category_readiness"].items():
        lines.append(
            f"| `{category}` | {item['qualified_rows']} | {item['qualified_source_docs']} | "
            f"{item['floor']} | `{str(item['passes_capacity_floor']).lower()}` |"
        )
    lines.extend(["", "## Holds", "", "| Reason | Rows |", "|---|---:|"])
    if report["hold_reason_counts"]:
        lines.extend(
            f"| `{reason}` | {count} |"
            for reason, count in report["hold_reason_counts"].items()
        )
    else:
        lines.append("| none | 0 |")
    lines.extend(["", report["interpretation"], ""])
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort", type=Path, action="append", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--qualified-jsonl", type=Path, required=True)
    parser.add_argument("--held-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    cohorts = [resolve(root, path) for path in args.cohort]
    before = file_sha256(root / "eng_bench.jsonl")
    rows = [row for path in cohorts for row in read_jsonl(path)]
    qualified, held, report = audit_rows(root, rows)
    qualified_path = resolve(root, args.qualified_jsonl)
    held_path = resolve(root, args.held_jsonl)
    report_path = resolve(root, args.report_json)
    report_md_path = resolve(root, args.report_md)
    write_jsonl(qualified_path, qualified)
    write_jsonl(held_path, held)
    after = file_sha256(root / "eng_bench.jsonl")
    report.update(
        {
            "date_label": args.date_label,
            "cohorts": [path.as_posix() for path in cohorts],
            "cohort_sha256": {path.as_posix(): file_sha256(path) for path in cohorts},
            "qualified_jsonl": qualified_path.as_posix(),
            "qualified_sha256": file_sha256(qualified_path),
            "held_jsonl": held_path.as_posix(),
            "held_sha256": file_sha256(held_path),
            "active_gold_sha256_before": before,
            "active_gold_sha256_after": after,
            "active_gold_modified": before != after,
        }
    )
    write_json(report_path, report)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "qualified_rows": report["qualified_rows"],
                "held_rows": report["held_rows"],
                "taxonomy_capacity_ready": report["taxonomy_capacity_ready"],
                "active_gold_modified": report["active_gold_modified"],
            },
            indent=2,
        )
    )
    if report["active_gold_modified"]:
        return 1
    if args.require_ready and not report["taxonomy_capacity_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
