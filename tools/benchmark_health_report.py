#!/usr/bin/env python3
"""
Build a machine-readable Eng_Bench release health report.

The report separates v0.95 packaging blockers from later gold-maturity work.
For v0.95, the dataset should be structurally packageable: images resolve,
split leakage is clear, active rights metadata has no obvious blockers, and
existing quality audits have zero critical failures.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from audit_active_gold_provenance import build_report as build_provenance_report
from audit_active_gold_provenance import release_provenance_gate
from evaluation_registry import baseline_registry
from source_rights import rights_blocker


SPLITS = ("train", "dev", "test")
PUBLIC_INPUT_LABEL_FIELDS = {"answer", "answer_text", "evidence", "evidence_ids", "text_gt", "change_desc_gt"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def sorted_counter(counter: Counter) -> dict[str, int]:
    return {str(k): counter[k] for k in sorted(counter)}


def row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def image_missing(root: Path, image_path: str) -> bool:
    candidate = Path(image_path)
    full_path = candidate if candidate.is_absolute() else root / candidate
    return not full_path.exists()


def missing_image_details(root: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing = []
    for row in rows:
        missing_paths = [
            image_path
            for image_path in row.get("images", []) or []
            if not isinstance(image_path, str) or image_missing(root, image_path)
        ]
        if missing_paths:
            missing.append(
                {
                    "id": row.get("id") or row.get("question_id") or row.get("qid"),
                    "task": row.get("task"),
                    "split": row.get("split"),
                    "missing_paths": missing_paths,
                }
            )
    return missing


def read_split_file(path: Path) -> set[str]:
    if not path.exists():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            names.add(stripped)
    return names


def leakage_by_split(root: Path, prefix: str) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = defaultdict(list)
    for split in SPLITS:
        for name in read_split_file(root / "splits" / f"{prefix}_{split}.txt"):
            owners[name].append(split)
    return {name: splits for name, splits in sorted(owners.items()) if len(splits) > 1}


def microtext_categories_by_split(items: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_split: dict[str, Counter] = defaultdict(Counter)
    for item in items:
        by_split[str(item.get("split", "unknown"))][str(item.get("category") or "unknown")] += 1
    return {
        split: {category: counts[category] for category in sorted(counts)}
        for split, counts in sorted(by_split.items())
    }


def load_manifest_rows(root: Path) -> list[dict[str, Any]]:
    return load_jsonl(root / "manifest.jsonl")


def manifest_maps(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    docs = {}
    pairs = {}
    for row in load_manifest_rows(root):
        if row.get("type") == "doc" and row.get("doc_id"):
            docs[str(row["doc_id"])] = row
        elif row.get("type") == "pair" and row.get("pair_id"):
            pairs[str(row["pair_id"])] = row
    return docs, pairs


def pair_candidates(project_id: str) -> list[str]:
    candidates = [project_id]
    if "__to__" in project_id:
        candidates.append(project_id.replace("__to__", "_to__"))
    if "_to__" in project_id:
        candidates.append(project_id.replace("_to__", "__to__"))
    return candidates


def active_source_ids(
    microtext_items: list[dict[str, Any]],
    visualdiff_pairs: list[dict[str, Any]],
) -> list[str]:
    source_ids = {str(item.get("doc_id")) for item in microtext_items if item.get("doc_id")}
    source_ids.update(str(pair.get("project_id") or pair.get("pair_id")) for pair in visualdiff_pairs)
    return sorted(source_id for source_id in source_ids if source_id)


def active_doc_ids(
    root: Path,
    microtext_items: list[dict[str, Any]],
    visualdiff_pairs: list[dict[str, Any]],
) -> list[str]:
    docs, pairs = manifest_maps(root)
    active = {str(item.get("doc_id")) for item in microtext_items if item.get("doc_id")}
    for pair in visualdiff_pairs:
        project_id = str(pair.get("project_id") or pair.get("pair_id") or "")
        manifest_pair = next((pairs[candidate] for candidate in pair_candidates(project_id) if candidate in pairs), None)
        if manifest_pair:
            if manifest_pair.get("from_doc_id"):
                active.add(str(manifest_pair["from_doc_id"]))
            if manifest_pair.get("to_doc_id"):
                active.add(str(manifest_pair["to_doc_id"]))
    return sorted(doc_id for doc_id in active if doc_id in docs or doc_id)


def source_inventory_by_doc(root: Path) -> dict[str, dict[str, str]]:
    path = root / "SOURCE_INVENTORY.csv"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as f:
        return {row["doc_id"]: row for row in csv.DictReader(f) if row.get("doc_id")}


def rights_blockers(root: Path, active_docs: list[str]) -> list[dict[str, str]]:
    manifest_docs, _ = manifest_maps(root)
    inventory = source_inventory_by_doc(root)
    blockers = []
    for doc_id in active_docs:
        status = str(
            manifest_docs.get(doc_id, {}).get("public_status")
            or inventory.get(doc_id, {}).get("public_status")
            or ""
        )
        blocker = rights_blocker(status)
        if blocker:
            blockers.append(
                {
                    "doc_id": doc_id,
                    "public_status": status or "missing",
                    "blocker": blocker,
                }
            )
    return blockers


def quality_critical_failures(root: Path) -> dict[str, int]:
    quality_dir = root / "derived" / "quality"
    files = {
        "microtext_quality": quality_dir / "microtext_quality_audit.json",
        "microtext_split": quality_dir / "microtext_split_audit.json",
        "visualdiff_quality": quality_dir / "visualdiff_quality_audit.json",
    }
    failures = {}
    for name, path in files.items():
        payload = load_json(path)
        failures[name] = int(payload.get("critical_failures", 0)) if payload else 0
    return failures


def question_diversity(root: Path) -> dict[str, Any]:
    payload = load_json(root / "derived" / "quality" / "question_diversity_report.json")
    if not payload:
        return {"available": False, "passes": False}
    payload["available"] = True
    return payload


def question_leakage(root: Path) -> dict[str, Any]:
    payload = load_json(root / "derived" / "quality" / "question_leakage_audit.json")
    if not payload:
        return {"available": False, "critical_failures": 0}
    payload["available"] = True
    return payload


def loader_smoke(root: Path) -> dict[str, Any]:
    payload = load_json(root / "results" / "health" / "loader_smoke.json")
    if not payload:
        return {"available": False}
    payload["available"] = True
    return payload


def release_manifest(root: Path) -> dict[str, Any]:
    payload = load_json(root / "results" / "health" / "release_file_manifest.json")
    if not payload:
        return {"available": False}
    return {
        "available": True,
        "file_count": payload.get("file_count"),
        "missing_count": payload.get("missing_count"),
        "total_bytes": payload.get("total_bytes"),
        "missing_paths": payload.get("missing_paths", []),
    }


def contains_label_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PUBLIC_INPUT_LABEL_FIELDS or str(key).endswith("_gt"):
                return True
            if contains_label_field(child):
                return True
    elif isinstance(value, list):
        return any(contains_label_field(child) for child in value)
    return False


def public_inputs(root: Path) -> dict[str, Any]:
    base = root / "release" / "public_inputs"
    status: dict[str, Any] = {"available": base.exists(), "splits": {}, "label_field_hits": []}
    for split in ("dev", "test"):
        path = base / f"eng_bench_{split}_inputs.jsonl"
        rows = load_jsonl(path)
        status["splits"][split] = {"path": str(path.relative_to(root)), "rows": len(rows), "exists": path.exists()}
        for row in rows:
            if contains_label_field(row):
                status["label_field_hits"].append(row.get("id") or row.get("question_id"))
                if len(status["label_field_hits"]) >= 25:
                    break
    status["label_field_hit_count"] = len(status["label_field_hits"])
    return status


def baseline_count(root: Path) -> int:
    return len(baseline_registry(root)["counted"])


def baseline_coverage(root: Path) -> dict[str, Any]:
    payload = load_json(root / "results" / "baselines" / "baseline_coverage_audit.json")
    if not payload:
        return {"available": False, "passed": False}
    payload["available"] = True
    return payload


def is_v1_or_later(release_target: str) -> bool:
    normalized = release_target.strip().lower()
    return normalized.startswith("v1") or normalized.startswith("v2")


def release_gate_status(report: dict[str, Any], release_target: str) -> dict[str, Any]:
    blockers = []
    if report["missing_image_rows"]:
        blockers.append(f"missing_image_rows={report['missing_image_rows']}")
    if report["doc_leakage"]:
        blockers.append(f"doc_leakage={len(report['doc_leakage'])}")
    if report["family_leakage"]:
        blockers.append(f"family_leakage={len(report['family_leakage'])}")
    if report["rights_blockers"]:
        blockers.append(f"rights_blockers={len(report['rights_blockers'])}")
    total_criticals = sum(report["quality_critical_failures"].values())
    if total_criticals:
        blockers.append(f"quality_critical_failures={total_criticals}")
    question_leakage_failures = int(report["question_leakage"].get("critical_failures", 0))
    if question_leakage_failures:
        blockers.append(f"question_leakage_failures={question_leakage_failures}")
    public_input_label_hits = int(report["public_inputs"].get("label_field_hit_count", 0))
    if public_input_label_hits:
        blockers.append(f"public_input_label_field_hits={public_input_label_hits}")
    if report["visualdiff_release_todo_total"]:
        blockers.append(f"visualdiff_release_todo_total={report['visualdiff_release_todo_total']}")

    if is_v1_or_later(release_target):
        visualdiff_rows = report["rows_by_task"].get("visualdiff", 0)
        microtext_rows = report["rows_by_task"].get("microtext", 0)
        test_rows = report["rows_by_split"].get("test", 0)
        test_visualdiff_rows = report["test_rows_by_task"].get("visualdiff", 0)
        test_microtext_rows = report["test_rows_by_task"].get("microtext", 0)

        if report["total_rows"] < 5000:
            blockers.append(f"total_rows<5000 ({report['total_rows']})")
        if visualdiff_rows < 2500:
            blockers.append(f"visualdiff_rows<2500 ({visualdiff_rows})")
        if microtext_rows < 3000:
            blockers.append(f"microtext_rows<3000 ({microtext_rows})")
        if report["source_count"] < 35:
            blockers.append(f"source_count<35 ({report['source_count']})")
        if test_rows < 1000:
            blockers.append(f"test_rows<1000 ({test_rows})")
        if test_visualdiff_rows < 600:
            blockers.append(f"test_visualdiff_rows<600 ({test_visualdiff_rows})")
        if test_microtext_rows < 400:
            blockers.append(f"test_microtext_rows<400 ({test_microtext_rows})")
        if report["visualdiff_low_confidence_dev_test"]:
            blockers.append(
                "visualdiff_low_confidence_dev_test="
                f"{report['visualdiff_low_confidence_dev_test']}"
            )
        if report["baseline_count"] < 5:
            blockers.append(f"baseline_count<5 ({report['baseline_count']})")
        elif not report["baseline_coverage"].get("available", False):
            blockers.append("baseline_coverage_audit_missing")
        elif not report["baseline_coverage"].get("passed", False):
            blockers.append(
                "baseline_coverage_failing_rows="
                f"{report['baseline_coverage'].get('failing_rows', 'unknown')}"
            )
        if not report["active_gold_provenance"]["passes"]:
            blockers.append("active_gold_provenance_incomplete")

    known_gold_blockers = []
    train_todo = report["visualdiff_todo_by_split"].get("train", 0)
    if train_todo:
        known_gold_blockers.append(f"visualdiff_train_pending_descriptions={train_todo}")
    if report["visualdiff_low_confidence_total"]:
        known_gold_blockers.append(
            f"visualdiff_low_confidence_total={report['visualdiff_low_confidence_total']}"
        )

    return {
        "target": release_target,
        "passed": not blockers,
        "blockers": blockers,
        "checks": {
            "images_resolved": report["missing_image_rows"] == 0,
            "split_leakage_clear": not report["doc_leakage"] and not report["family_leakage"],
            "active_rights_clear": not report["rights_blockers"],
            "quality_critical_failures_clear": total_criticals == 0,
            "release_todos_clear": report["visualdiff_release_todo_total"] == 0,
            "v1_scale_ready": not is_v1_or_later(release_target)
            or (
                report["total_rows"] >= 5000
                and report["rows_by_task"].get("visualdiff", 0) >= 2500
                and report["rows_by_task"].get("microtext", 0) >= 3000
                and report["source_count"] >= 35
                and report["rows_by_split"].get("test", 0) >= 1000
                and report["test_rows_by_task"].get("visualdiff", 0) >= 600
                and report["test_rows_by_task"].get("microtext", 0) >= 400
            ),
            "v1_label_ready": not is_v1_or_later(release_target)
            or report["visualdiff_low_confidence_dev_test"] == 0,
            "v1_baselines_ready": not is_v1_or_later(release_target)
            or report["baseline_count"] >= 5,
            "v1_baseline_coverage_ready": not is_v1_or_later(release_target)
            or report["baseline_count"] < 5
            or bool(report["baseline_coverage"].get("passed", False)),
            "v1_provenance_ready": not is_v1_or_later(release_target)
            or bool(report["active_gold_provenance"]["passes"]),
        },
        "known_gold_blockers": known_gold_blockers,
    }


def compute_health(root: Path, release_target: str = "v0.95") -> dict[str, Any]:
    root = root.resolve()
    unified_rows = load_jsonl(root / "eng_bench.jsonl")
    visualdiff_pairs = load_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    microtext_items = load_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")

    missing_images = missing_image_details(root, unified_rows)
    rows_by_task = sorted_counter(Counter(str(row.get("task", "unknown")) for row in unified_rows))
    rows_by_split = sorted_counter(Counter(str(row.get("split", "unknown")) for row in unified_rows))
    test_rows_by_task = sorted_counter(
        Counter(str(row.get("task", "unknown")) for row in unified_rows if row.get("split") == "test")
    )
    todo_by_split = Counter(
        str(pair.get("split", "unknown"))
        for pair in visualdiff_pairs
        if pair.get("change_desc_gt") == "CHANGE_DESC_GT_TODO"
    )
    low_confidence_by_split = Counter(
        str(pair.get("split", "unknown"))
        for pair in visualdiff_pairs
        if pair.get("review_confidence") == "low"
    )
    low_confidence = sum(low_confidence_by_split.values())
    low_confidence_dev_test = sum(low_confidence_by_split[split] for split in ("dev", "test"))
    release_todo_total = sum(todo_by_split[split] for split in ("dev", "test"))
    doc_leakage = leakage_by_split(root, "microtext")
    family_leakage = leakage_by_split(root, "visualdiff")
    active_docs = active_doc_ids(root, microtext_items, visualdiff_pairs)
    provenance = build_provenance_report(root)
    provenance_gate = release_provenance_gate(root)

    report: dict[str, Any] = {
        "release_target": release_target,
        "total_rows": len(unified_rows),
        "rows_by_task": rows_by_task,
        "rows_by_split": rows_by_split,
        "test_rows_by_task": test_rows_by_task,
        "source_count": int(provenance["totals"]["active_source_docs"]),
        "active_source_ids": active_source_ids(microtext_items, visualdiff_pairs),
        "active_doc_ids": active_docs,
        "active_gold_provenance": provenance_gate,
        "missing_image_rows": len(missing_images),
        "missing_image_details": missing_images[:50],
        "visualdiff_todo_total": sum(todo_by_split.values()),
        "visualdiff_todo_by_split": {split: todo_by_split[split] for split in sorted(todo_by_split)},
        "visualdiff_release_todo_total": release_todo_total,
        "visualdiff_low_confidence_total": low_confidence,
        "visualdiff_low_confidence_by_split": {
            split: low_confidence_by_split[split] for split in sorted(low_confidence_by_split)
        },
        "visualdiff_low_confidence_dev_test": low_confidence_dev_test,
        "microtext_categories_by_split": microtext_categories_by_split(microtext_items),
        "doc_leakage": doc_leakage,
        "family_leakage": family_leakage,
        "rights_blockers": rights_blockers(root, active_docs),
        "quality_critical_failures": quality_critical_failures(root),
        "question_diversity": question_diversity(root),
        "question_leakage": question_leakage(root),
        "loader_smoke": loader_smoke(root),
        "release_manifest": release_manifest(root),
        "public_inputs": public_inputs(root),
        "baseline_count": baseline_count(root),
        "baseline_registry": baseline_registry(root),
        "baseline_coverage": baseline_coverage(root),
    }
    report["release_gate_status"] = release_gate_status(report, release_target)
    return report


def write_report(root: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    output_dir = root / "results" / "health"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmark_health_report.json"
    md_path = output_dir / "benchmark_health_report.md"
    json_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    md_text = render_markdown(report)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    target = str(report.get("release_target", "target")).replace("/", "_").replace("\\", "_")
    (output_dir / f"benchmark_health_report_{target}.json").write_text(json_text, encoding="utf-8")
    (output_dir / f"benchmark_health_report_{target}.md").write_text(md_text, encoding="utf-8")
    return json_path, md_path


def render_markdown(report: dict[str, Any]) -> str:
    gate = report["release_gate_status"]
    lines = [
        "# Eng_Bench Health Report",
        "",
        f"- Release target: {gate['target']}",
        f"- Gate passed: {gate['passed']}",
        f"- Total rows: {report['total_rows']}",
        f"- Rows by task: {report['rows_by_task']}",
        f"- Rows by split: {report['rows_by_split']}",
        f"- Test rows by task: {report['test_rows_by_task']}",
        f"- Active source count: {report['source_count']}",
        f"- Active-gold provenance: {report['active_gold_provenance']['current']}",
        f"- Missing image rows: {report['missing_image_rows']}",
        f"- Visualdiff pending descriptions: {report['visualdiff_todo_total']}",
        f"- Visualdiff low-confidence rows: {report['visualdiff_low_confidence_total']}",
        f"- Visualdiff low-confidence dev/test rows: {report['visualdiff_low_confidence_dev_test']}",
        f"- Baseline result files: {report['baseline_count']}",
        f"- Baseline coverage audit available: {report['baseline_coverage'].get('available', False)}",
        f"- Baseline coverage passed: {report['baseline_coverage'].get('passed', False)}",
        f"- Baseline coverage failing rows: {report['baseline_coverage'].get('failing_rows', 'unknown')}",
        f"- Question diversity gate: {report['question_diversity'].get('passes', False)}",
        f"- Question leakage critical failures: {report['question_leakage'].get('critical_failures', 0)}",
        f"- Loader smoke available: {report['loader_smoke'].get('available', False)}",
        f"- Release manifest available: {report['release_manifest'].get('available', False)}",
        f"- Release manifest missing files: {report['release_manifest'].get('missing_count', 'unknown')}",
        f"- Public input label-field hits: {report['public_inputs'].get('label_field_hit_count', 0)}",
        f"- Rights blockers: {len(report['rights_blockers'])}",
        "",
        "## Gate Blockers",
        "",
    ]
    if gate["blockers"]:
        lines.extend(f"- {blocker}" for blocker in gate["blockers"])
    else:
        lines.append("- None")
    lines.extend(["", "## Known Gold Blockers", ""])
    if gate["known_gold_blockers"]:
        lines.extend(f"- {blocker}" for blocker in gate["known_gold_blockers"])
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Eng_Bench release health report.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--release-target", default="v0.95", help="Release gate target")
    args = parser.parse_args()

    root = Path(args.root)
    report = compute_health(root, release_target=args.release_target)
    json_path, md_path = write_report(root, report)
    gate = report["release_gate_status"]

    print(f"[*] Wrote {json_path}")
    print(f"[*] Wrote {md_path}")
    print(f"[*] {gate['target']} gate passed: {gate['passed']}")
    if gate["blockers"]:
        print("[ERR] Gate blockers:")
        for blocker in gate["blockers"]:
            print(f"  - {blocker}")
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
