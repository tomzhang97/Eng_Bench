#!/usr/bin/env python3
"""Build reproducible, release-calibrated Eng_Bench paper tables."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REQUIRED_COMPARISON_DATASETS = {"DocVQA", "HotpotQA", "MuSiQue"}
REQUIRED_COMPARISON_FIELDS = {
    "dataset",
    "release_context",
    "modality",
    "primary_task",
    "reported_scale",
    "source_units",
    "supervision",
    "citation_title",
    "citation_url",
    "citation_year",
    "source_type",
    "source_evidence",
}
ALLOWED_COMPARISON_SOURCE_TYPES = {"primary_paper", "official_dataset_site"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            rows.append(row)
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def fmt_metric(value: Any) -> str:
    return "-" if value is None else f"{float(value):.4f}"


def active_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    task_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    task_split_counts: dict[str, Counter[str]] = defaultdict(Counter)
    microtext_categories: Counter[str] = Counter()
    visualdiff_change_types: Counter[str] = Counter()

    for row in rows:
        task = str(row.get("task") or "unknown")
        split = str(row.get("split") or "unknown")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        task_counts[task] += 1
        split_counts[split] += 1
        task_split_counts[task][split] += 1
        if task == "microtext":
            microtext_categories[str(metadata.get("category") or "unknown")] += 1
        elif task == "visualdiff":
            change_types = metadata.get("change_type")
            if isinstance(change_types, list):
                for value in change_types:
                    visualdiff_change_types[str(value or "unknown")] += 1
            elif change_types:
                visualdiff_change_types[str(change_types)] += 1
            else:
                visualdiff_change_types["unknown"] += 1

    return {
        "rows": len(rows),
        "task_counts": sorted_counts(task_counts),
        "split_counts": sorted_counts(split_counts),
        "task_split_counts": {
            task: sorted_counts(counts) for task, counts in sorted(task_split_counts.items())
        },
        "microtext_category_counts": sorted_counts(microtext_categories),
        "visualdiff_change_type_mentions": sorted_counts(visualdiff_change_types),
    }


def provenance_statistics(report: dict[str, Any]) -> dict[str, Any]:
    domains: dict[str, dict[str, int]] = defaultdict(lambda: {
        "source_documents": 0,
        "paper_ready_documents": 0,
        "active_row_references": 0,
    })
    for document in report.get("documents", []):
        domain = str(document.get("domain") or "unknown")
        domains[domain]["source_documents"] += 1
        domains[domain]["paper_ready_documents"] += int(bool(document.get("paper_ready")))
        domains[domain]["active_row_references"] += int(document.get("active_row_references") or 0)
    return {
        "totals": report.get("totals", {}),
        "domains": {domain: values for domain, values in sorted(domains.items())},
    }


def baseline_statistics(root: Path, gate_report: dict[str, Any]) -> list[dict[str, Any]]:
    baselines: list[dict[str, Any]] = []
    registry = gate_report.get("baseline_registry", {})
    for entry in registry.get("counted", []):
        report_path = root / str(entry.get("report_path") or "")
        if not report_path.is_file():
            raise FileNotFoundError(f"counted baseline report missing: {report_path}")
        report = read_json(report_path)
        microtext = report.get("microtext", {})
        visualdiff = report.get("visualdiff", {})
        baselines.append({
            "name": str(entry.get("name") or report.get("model_name") or "unknown"),
            "task": str(report.get("task") or "unknown"),
            "split": str(report.get("split") or "unknown"),
            "rows_scored": int(report.get("rows_scored") or 0),
            "microtext_rows": int(microtext.get("total") or 0),
            "microtext_normalized_exact_match": (
                float(microtext.get("normalized_exact_match"))
                if int(microtext.get("total") or 0) else None
            ),
            "visualdiff_rows": int(visualdiff.get("total") or 0),
            "visualdiff_description_f1": (
                float(visualdiff.get("normalized_description_f1"))
                if int(visualdiff.get("total") or 0) else None
            ),
            "visualdiff_evidence_recall_iou_0_5": (
                float(visualdiff.get("evidence_recall_iou_0_5"))
                if int(visualdiff.get("total") or 0) else None
            ),
            "prediction_sha256": str(entry.get("prediction_hash") or "").upper(),
        })
    return sorted(baselines, key=lambda row: row["name"])


def gate_statistics(gate_report: dict[str, Any]) -> dict[str, Any]:
    gates: dict[str, dict[str, Any]] = {}
    for name, gate in sorted(gate_report.get("gates", {}).items()):
        gates[name] = {
            "current": gate.get("current"),
            "target": gate.get("target"),
            "passes": bool(gate.get("passes")),
        }
    passed = sum(int(gate["passes"]) for gate in gates.values())
    return {"passed": passed, "total": len(gates), "gates": gates}


def comparison_statistics(
    comparison_data: dict[str, Any],
    active: dict[str, Any],
    provenance: dict[str, Any],
    gates: dict[str, Any],
    date_label: str,
) -> dict[str, Any]:
    rows = comparison_data.get("datasets")
    if not isinstance(rows, list) or not rows:
        raise ValueError("comparison data must contain a non-empty datasets list")
    names: list[str] = []
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"comparison row {index} must be an object")
        missing = sorted(REQUIRED_COMPARISON_FIELDS - set(row))
        if missing:
            raise ValueError(f"comparison row {index} missing fields: {missing}")
        name = str(row["dataset"]).strip()
        if not name:
            raise ValueError(f"comparison row {index} has an empty dataset name")
        names.append(name)
        source_type = str(row["source_type"]).strip()
        if source_type not in ALLOWED_COMPARISON_SOURCE_TYPES:
            raise ValueError(f"comparison row {index} has unsupported source_type: {source_type}")
        citation_url = str(row["citation_url"]).strip()
        if not citation_url.startswith("https://"):
            raise ValueError(f"comparison row {index} citation_url must use HTTPS")
        if not str(row["source_evidence"]).strip():
            raise ValueError(f"comparison row {index} source_evidence is empty")
        validated.append({key: row[key] for key in REQUIRED_COMPARISON_FIELDS})
    duplicate_names = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicate_names:
        raise ValueError(f"duplicate comparison datasets: {duplicate_names}")
    missing_datasets = sorted(REQUIRED_COMPARISON_DATASETS - set(names))
    if missing_datasets:
        raise ValueError(f"required comparison datasets missing: {missing_datasets}")

    provenance_totals = provenance.get("totals", {})
    gold_sources = gates.get("gates", {}).get("gold_source_docs", {}).get("current", "unknown")
    engbench = {
        "dataset": "Eng_Bench (active Gold)",
        "release_context": date_label,
        "modality": "engineering document images",
        "primary_task": "MicroText extraction and engineering VisualDiff",
        "reported_scale": f"{active['rows']:,} rows",
        "source_units": (
            f"{provenance_totals.get('active_source_docs', 'unknown')} active source documents; "
            f"{gold_sources} canonical paper-ready payloads"
        ),
        "supervision": "region evidence with text/category or visible-change labels",
        "citation_title": "Audited Eng_Bench snapshot",
        "citation_url": "",
        "citation_year": int(date_label[:4]),
        "source_type": "audited_local_snapshot",
        "source_evidence": "Derived from the frozen active dataset, formal gate audit, and active provenance audit.",
    }
    return {
        "status": "citation_backed_primary_sources",
        "schema_version": str(comparison_data.get("schema_version") or "unknown"),
        "retrieved_on": str(comparison_data.get("retrieved_on") or "unknown"),
        "rows": [engbench, *sorted(validated, key=lambda row: str(row["dataset"]))],
        "comparability_note": (
            "Scale is contextual only. DocVQA is image-based document QA, whereas HotpotQA and "
            "MuSiQue are text multi-hop QA; none is task-equivalent to Eng_Bench VisualDiff."
        ),
    }


def capacity_statistics(capacity_report: dict[str, Any]) -> dict[str, Any]:
    balance = capacity_report.get("microtext_balance_capacity", {})
    return {
        "capacity_input_clean": bool(capacity_report.get("capacity_input_clean")),
        "active_gold": capacity_report.get("current_gold", {}),
        "targets": capacity_report.get("targets", {}),
        "review_decisions": capacity_report.get("human_work", {}),
        "staged_microtext": balance.get("staged", {}),
        "category_floor_shortfalls_after_staging": (
            balance.get("category_floor_shortfalls_after_all_canonical_staged_rows", {})
        ),
        "row_target_projection": balance.get("row_target_projection", {}),
        "interpretation": (
            "Planning projection only. Staged rows are not Gold and cannot support release claims "
            "until human acceptance and strict promotion gates pass."
        ),
    }


def build_report(
    root: Path,
    dataset_path: Path,
    gate_path: Path,
    capacity_path: Path,
    provenance_path: Path,
    comparison_path: Path,
    date_label: str,
) -> dict[str, Any]:
    rows = read_jsonl(dataset_path)
    gate_report = read_json(gate_path)
    capacity_report = read_json(capacity_path)
    provenance_report = read_json(provenance_path)
    comparison_data = read_json(comparison_path)
    active = active_statistics(rows)
    provenance = provenance_statistics(provenance_report)
    gates = gate_statistics(gate_report)
    return {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "release_claim_status": "NOT RELEASE CLAIM READY",
        "active_gold": active,
        "provenance": provenance,
        "formal_gate_status": gates,
        "baselines": baseline_statistics(root, gate_report),
        "staged_capacity_projection": capacity_statistics(capacity_report),
        "external_benchmark_comparison": comparison_statistics(
            comparison_data, active, provenance, gates, date_label
        ),
        "inputs": {
            "dataset": {"path": dataset_path.relative_to(root).as_posix(), "sha256": sha256(dataset_path)},
            "gate_report": {"path": gate_path.relative_to(root).as_posix(), "sha256": sha256(gate_path)},
            "capacity_report": {"path": capacity_path.relative_to(root).as_posix(), "sha256": sha256(capacity_path)},
            "provenance_report": {"path": provenance_path.relative_to(root).as_posix(), "sha256": sha256(provenance_path)},
            "comparison_data": {"path": comparison_path.relative_to(root).as_posix(), "sha256": sha256(comparison_path)},
        },
    }


def markdown(report: dict[str, Any]) -> str:
    active = report["active_gold"]
    gate_status = report["formal_gate_status"]
    capacity = report["staged_capacity_projection"]
    lines = [
        "# Eng_Bench Paper Tables",
        "",
        f"Snapshot: `{report['date_label']}`  ",
        f"Target: **{report['goal']}**  ",
        f"Claim status: **{report['release_claim_status']}**",
        "",
        "> Active Gold and staged capacity are reported separately. Staged rows are never paper results.",
        "",
        "## Active Dataset Statistics",
        "",
        "| Measure | Count |",
        "| --- | ---: |",
        f"| Total rows | {active['rows']} |",
    ]
    for task, count in active["task_counts"].items():
        lines.append(f"| Task: {task} | {count} |")
    for split, count in active["split_counts"].items():
        lines.append(f"| Split: {split} | {count} |")

    lines.extend(["", "## Task by Split", "", "| Task | Train | Dev | Test | Total |", "| --- | ---: | ---: | ---: | ---: |"])
    for task, counts in active["task_split_counts"].items():
        total = sum(counts.values())
        lines.append(f"| {task} | {counts.get('train', 0)} | {counts.get('dev', 0)} | {counts.get('test', 0)} | {total} |")

    lines.extend(["", "## Active MicroText Categories", "", "| Category | Rows |", "| --- | ---: |"])
    for category, count in active["microtext_category_counts"].items():
        lines.append(f"| {category} | {count} |")

    lines.extend(["", "## Active VisualDiff Change Types", "", "| Change type | Mentions |", "| --- | ---: |"])
    for change_type, count in active["visualdiff_change_type_mentions"].items():
        lines.append(f"| {change_type} | {count} |")

    lines.extend(["", "## Active Source Domains", "", "| Domain | Source docs | Paper-ready docs | Active row references |", "| --- | ---: | ---: | ---: |"])
    for domain, values in report["provenance"]["domains"].items():
        lines.append(
            f"| {domain} | {values['source_documents']} | {values['paper_ready_documents']} | "
            f"{values['active_row_references']} |"
        )

    lines.extend(["", f"## Formal Gate Status ({gate_status['passed']}/{gate_status['total']})", "", "| Gate | Current | Target | Status |", "| --- | --- | --- | --- |"])
    for name, gate in gate_status["gates"].items():
        lines.append(f"| {name} | {gate['current']} | {gate['target']} | {'PASS' if gate['passes'] else 'OPEN'} |")

    lines.extend(["", "## Counted Baselines", "", "| Baseline | Task | Rows | MicroText NEM | VisualDiff description F1 | VisualDiff evidence R@0.5 |", "| --- | --- | ---: | ---: | ---: | ---: |"])
    for baseline in report["baselines"]:
        lines.append(
            f"| {baseline['name']} | {baseline['task']} | {baseline['rows_scored']} | "
            f"{fmt_metric(baseline['microtext_normalized_exact_match'])} | "
            f"{fmt_metric(baseline['visualdiff_description_f1'])} | "
            f"{fmt_metric(baseline['visualdiff_evidence_recall_iou_0_5'])} |"
        )

    targets = capacity["targets"]
    lines.extend([
        "",
        "## Staged Capacity Projection (Not Gold)",
        "",
        "| Gate | Active | Staged upper bound | Target | Can close if accepted |",
        "| --- | ---: | ---: | ---: | --- |",
        f"| Rows | {targets['rows']['current']} | {targets['rows']['all_staged_upper_bound']} | {targets['rows']['target']} | {targets['rows']['can_close_from_staged_capacity']} |",
        f"| Source payloads | {targets['unique_source_payloads']['current']} | {targets['unique_source_payloads']['all_staged_upper_bound']} | {targets['unique_source_payloads']['target']} | {targets['unique_source_payloads']['can_close_from_staged_capacity']} |",
        f"| VisualDiff families | {targets['visualdiff_families']['current']} | {targets['visualdiff_families']['all_staged_upper_bound']} | {targets['visualdiff_families']['target']} | {targets['visualdiff_families']['can_close_from_staged_capacity']} |",
        f"| Test rows | {targets['test_rows']['current']} | {targets['test_rows']['explicit_all_staged_upper_bound']} | {targets['test_rows']['target']} | {targets['test_rows']['can_close_from_explicit_staged_test_capacity']} |",
        "",
        f"Canonical non-pin rows still needed for the balance-compliant row target: `{capacity['row_target_projection'].get('additional_canonical_non_pin_rows_needed', 'unknown')}`.",
        "",
        "## External Benchmark Comparison",
        "",
        report["external_benchmark_comparison"]["comparability_note"],
        "",
        "| Dataset | Modality | Primary task | Reported scale | Source units | Supervision | Source |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for row in report["external_benchmark_comparison"]["rows"]:
        source = (
            f"[{row['citation_title']}]({row['citation_url']})"
            if row["citation_url"] else row["citation_title"]
        )
        lines.append(
            f"| {row['dataset']} | {row['modality']} | {row['primary_task']} | "
            f"{row['reported_scale']} | {row['source_units']} | {row['supervision']} | {source} |"
        )
    lines.extend([
        "",
        "## Reproducibility Inputs",
        "",
        "| Input | SHA-256 |",
        "| --- | --- |",
    ])
    for value in report["inputs"].values():
        lines.append(f"| `{value['path']}` | `{value['sha256']}` |")
    lines.append("")
    return "\n".join(lines)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(report: dict[str, Any], output_json: Path, output_md: Path, output_dir: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    output_md.write_text(markdown(report), encoding="utf-8")
    write_csv(
        output_dir / "active_microtext_categories.csv",
        ["category", "rows"],
        [{"category": key, "rows": value} for key, value in report["active_gold"]["microtext_category_counts"].items()],
    )
    write_csv(
        output_dir / "active_source_domains.csv",
        ["domain", "source_documents", "paper_ready_documents", "active_row_references"],
        [{"domain": key, **value} for key, value in report["provenance"]["domains"].items()],
    )
    write_csv(
        output_dir / "active_visualdiff_change_types.csv",
        ["change_type", "mentions"],
        [
            {"change_type": key, "mentions": value}
            for key, value in report["active_gold"]["visualdiff_change_type_mentions"].items()
        ],
    )
    write_csv(
        output_dir / "counted_baselines.csv",
        [
            "name", "task", "split", "rows_scored", "microtext_rows",
            "microtext_normalized_exact_match", "visualdiff_rows",
            "visualdiff_description_f1", "visualdiff_evidence_recall_iou_0_5",
            "prediction_sha256",
        ],
        report["baselines"],
    )
    write_csv(
        output_dir / "external_benchmark_comparison.csv",
        [
            "dataset", "release_context", "modality", "primary_task", "reported_scale",
            "source_units", "supervision", "citation_title", "citation_url", "citation_year",
            "source_type", "source_evidence",
        ],
        report["external_benchmark_comparison"]["rows"],
    )


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--dataset", type=Path, default=Path("eng_bench.jsonl"))
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--capacity-report", type=Path, required=True)
    parser.add_argument("--provenance-report", type=Path, required=True)
    parser.add_argument("--comparison-data", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_report(
        root,
        resolve(root, args.dataset),
        resolve(root, args.gate_report),
        resolve(root, args.capacity_report),
        resolve(root, args.provenance_report),
        resolve(root, args.comparison_data),
        args.date_label,
    )
    write_outputs(
        report,
        resolve(root, args.output_json),
        resolve(root, args.output_md),
        resolve(root, args.output_dir),
    )
    print(json.dumps({
        "active_rows": report["active_gold"]["rows"],
        "formal_gates": f"{report['formal_gate_status']['passed']}/{report['formal_gate_status']['total']}",
        "counted_baselines": len(report["baselines"]),
        "source_domains": len(report["provenance"]["domains"]),
        "release_claim_status": report["release_claim_status"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
