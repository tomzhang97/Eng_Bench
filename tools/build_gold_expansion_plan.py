#!/usr/bin/env python3
"""Build an actionable source-to-gold expansion plan for Eng_Bench."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from build_v1_expansion_queue import build_queue, imported_candidate_ids
from build_source_quotas import load_csv, quota_domain, summarize_quotas
import audit_active_gold_provenance
import review_queue_inventory
from source_rights import is_release_safe_status


OPEN_STATUSES = {"", "candidate", "needs_review", "provisional_review", "todo"}
MERGEABLE_STATUSES = {"accepted", "edited", "valid", "edit"}
MICROTEXT_V1_TARGET_BY_CATEGORY = {
    "pin_label": 1000,
    "dimension_value": 900,
    "equipment_tag": 300,
    "instrument_tag": 300,
    "pipe_line_tag": 150,
    "process_value": 150,
    "room_label": 150,
    "tolerance_value": 150,
}

DOMAIN_V1_5_MICROTEXT_TARGETS = {
    "pcb_schematic": 2500,
    "datasheet_spec": 1500,
    "mechanical_cad": 2500,
    "civil_architectural": 2500,
    "pid": 2500,
}

RELEASE_TARGETS = {
    "v1_0": {
        "total_rows": 5000,
        "microtext_rows": 3000,
        "visualdiff_rows": 2500,
        "source_count": 35,
        "visualdiff_revision_families": 8,
        "test_rows": 1000,
        "test_microtext_rows": 400,
        "test_visualdiff_rows": 600,
        "baseline_reports": 5,
    },
    "v1_5": {
        "total_rows": 12000,
        "gold_source_docs": 75,
        "release_safe_inventory_docs": 75,
        "visualdiff_revision_families": 15,
        "test_rows": 2500,
        "baseline_reports": 10,
    },
    "v2_0": {
        "total_rows": 25000,
        "gold_source_docs": 150,
        "release_safe_inventory_docs": 150,
        "visualdiff_revision_families": 30,
        "test_rows": 5000,
        "baseline_reports": 20,
    },
}
HUMAN_QUEUE_FIELDS = [
    "path",
    "kind",
    "rows",
    "open_rows",
    "fresh_open_rows",
    "actionable_fresh_open_rows",
    "rights_blocked_fresh_open_rows",
    "packeted_open_rows",
    "stale_open_rows",
    "missing_evidence_rows",
    "source_count",
    "top_sources",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fieldnames: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = list(fieldnames or [])
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def infer_manifest_task(row: dict[str, Any]) -> str:
    task = str(row.get("task") or "").strip()
    if task:
        return task
    if row.get("pair_id") or row.get("old_image_path") or row.get("new_image_path"):
        return "visualdiff"
    return "microtext"


def packet_manifest_stats(root: Path, folder_path: str) -> dict[str, Any]:
    folder = Path(folder_path)
    packet_root = folder if folder.is_absolute() else root / folder
    rows = 0
    by_task: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    manifest_files: list[str] = []

    if not packet_root.exists():
        return {
            "manifest_files": [],
            "manifest_rows": 0,
            "manifest_rows_by_task": {},
            "manifest_rows_by_category": {},
            "manifest_rows_by_source": {},
        }

    for manifest_path in sorted(packet_root.rglob("manifest.jsonl")):
        rel_path = manifest_path.relative_to(root).as_posix()
        manifest_files.append(rel_path)
        for row in read_jsonl(manifest_path):
            rows += 1
            by_task[infer_manifest_task(row)] += 1
            category = str(row.get("category") or row.get("corrected_category") or "").strip()
            if category:
                by_category[category] += 1
            source = str(
                row.get("doc_id")
                or row.get("source_doc_id")
                or row.get("project_id")
                or row.get("pair_id")
                or ""
            ).strip()
            if source:
                by_source[source] += 1

    return {
        "manifest_files": manifest_files,
        "manifest_rows": rows,
        "manifest_rows_by_task": dict(sorted(by_task.items())),
        "manifest_rows_by_category": dict(by_category.most_common(12)),
        "manifest_rows_by_source": dict(by_source.most_common(12)),
    }


def human_packet_forecast(
    root: Path,
    status: dict[str, Any],
    gaps: dict[str, dict[str, Any]],
    date_label: str,
) -> dict[str, Any]:
    packet_index_path = root / "derived" / "quality" / f"human_packet_index_{date_label}.json"
    packet_index = load_json(packet_index_path)
    if not packet_index:
        return {
            "available": False,
            "packet_index_path": packet_index_path.relative_to(root).as_posix(),
            "reason": "No human packet index exists for this date label.",
        }

    packets: list[dict[str, Any]] = []
    task_totals: Counter[str] = Counter()
    category_totals: Counter[str] = Counter()
    source_totals: Counter[str] = Counter()
    review_rows_upper_bound = 0
    ready_to_send = 0
    missing_evidence_refs = 0
    manifest_rows = 0

    for packet in packet_index.get("packets", []):
        folder_path = str(packet.get("folder_path") or "")
        stats = packet_manifest_stats(root, folder_path) if folder_path else {
            "manifest_files": [],
            "manifest_rows": 0,
            "manifest_rows_by_task": {},
            "manifest_rows_by_category": {},
            "manifest_rows_by_source": {},
        }
        ready = bool(packet.get("ready_to_send"))
        review_rows = safe_int(packet.get("review_rows"))
        review_rows_upper_bound += review_rows
        ready_to_send += int(ready)
        missing_evidence_refs += safe_int(packet.get("missing_evidence_refs"))
        manifest_rows += safe_int(stats["manifest_rows"])
        task_totals.update(stats["manifest_rows_by_task"])
        category_totals.update(stats["manifest_rows_by_category"])
        source_totals.update(stats["manifest_rows_by_source"])
        packets.append(
            {
                "packet_id": packet.get("packet_id") or "",
                "kind": packet.get("kind") or "",
                "ready_to_send": ready,
                "human_status": packet.get("human_status") or "",
                "review_rows": review_rows,
                "primary_rows": safe_int(packet.get("primary_rows")),
                "extra_review_rows": safe_int(packet.get("extra_review_rows")),
                "missing_evidence_refs": safe_int(packet.get("missing_evidence_refs")),
                "folder_path": folder_path,
                "zip_path": packet.get("zip_path") or "",
                "notes": packet.get("notes") or "",
                **stats,
            }
        )

    projected_total_rows = safe_int(status.get("total_rows")) + review_rows_upper_bound
    release_projection: dict[str, dict[str, Any]] = {}
    for release, release_gaps in gaps.items():
        total_gate = release_gaps.get("total_rows", {})
        target = safe_int(total_gate.get("target"))
        release_projection[release] = {
            "target_total_rows": target,
            "current_total_rows": safe_int(status.get("total_rows")),
            "review_rows_upper_bound": review_rows_upper_bound,
            "projected_total_rows_if_all_packet_rows_accepted": projected_total_rows,
            "remaining_after_all_packet_rows_accepted": max(0, target - projected_total_rows),
        }

    return {
        "available": True,
        "packet_index_path": packet_index_path.relative_to(root).as_posix(),
        "packet_index_totals": packet_index.get("totals", {}),
        "packets_ready_to_send": ready_to_send,
        "packet_count": len(packets),
        "review_rows_upper_bound": review_rows_upper_bound,
        "missing_evidence_refs": missing_evidence_refs,
        "manifest_rows_inspectable": manifest_rows,
        "manifest_rows_by_task": dict(sorted(task_totals.items())),
        "manifest_rows_by_category": dict(category_totals.most_common(12)),
        "manifest_rows_by_source": dict(source_totals.most_common(12)),
        "release_projection": release_projection,
        "packets": packets,
        "caveat": "Forecast rows remain non-gold until humans return completed CSVs, processors stage them, maintainers inspect the staged JSONL, and merge audits pass.",
    }


def source_doc_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(metadata.get("source_doc_id") or metadata.get("doc_id") or row.get("doc_id") or "")


def logical_source_group_ids(
    unified_rows: list[dict[str, Any]],
    visualdiff_pairs: list[dict[str, Any]],
) -> set[str]:
    groups = {
        source_doc_id(row)
        for row in unified_rows
        if row.get("task") == "microtext" and source_doc_id(row)
    }
    groups.update(
        str(row.get("project_id") or row.get("pair_id"))
        for row in visualdiff_pairs
        if row.get("project_id") or row.get("pair_id")
    )
    return groups


def row_category(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(metadata.get("category") or row.get("category") or "unknown")


def visualdiff_family(pair_id: str) -> str:
    parts = pair_id.split("__")
    if len(parts) >= 4 and parts[0] == "vdiff":
        return "__".join(parts[:4])
    if len(parts) >= 3:
        return "__".join(parts[:3])
    return pair_id.rsplit("__", 1)[0]


def is_release_safe(row: dict[str, str]) -> bool:
    if row.get("task") == "reference":
        return False
    status = str(row.get("public_status") or row.get("rights_tier") or "").lower()
    return is_release_safe_status(status)


def inventory_maps(inventory_rows: list[dict[str, str]]) -> tuple[dict[str, str], set[str]]:
    by_doc: dict[str, str] = {}
    safe_docs: set[str] = set()
    for row in inventory_rows:
        doc_id = str(row.get("doc_id") or "")
        if not doc_id:
            continue
        by_doc[doc_id] = quota_domain(row.get("domain"))
        if is_release_safe(row):
            safe_docs.add(doc_id)
    return by_doc, safe_docs


def candidate_domain_maps(candidates: list[dict[str, str]]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    by_id: dict[str, dict[str, str]] = {}
    domain_by_id: dict[str, str] = {}
    for row in candidates:
        candidate_id = row.get("candidate_id", "")
        if not candidate_id:
            continue
        by_id[candidate_id] = row
        domain_by_id[candidate_id] = quota_domain(row.get("domain"))
    return domain_by_id, by_id


def infer_domain(doc_id: str, doc_to_domain: dict[str, str]) -> str:
    if doc_id in doc_to_domain:
        return doc_to_domain[doc_id]
    lowered = doc_id.lower()
    if any(token in lowered for token in ("pid", "pump", "sps", "process")):
        return "pid"
    if any(token in lowered for token in ("mechanical", "faunce", "reid", "cornell", "hunt")):
        return "mechanical_cad"
    if any(token in lowered for token in ("wsdot", "caltrans", "floor", "habs", "haer", "house", "plan")):
        return "civil_architectural"
    if any(token in lowered for token in ("viola", "bbb", "rusefi", "arduino", "pixhawk", "schem")):
        return "pcb_schematic"
    return "unknown"


def baseline_report_count(root: Path) -> int:
    count = 0
    baselines_dir = root / "results" / "baselines"
    for path in baselines_dir.glob("*_report.json") if baselines_dir.exists() else []:
        lowered = path.name.lower()
        if "oracle" in lowered or "smoke" in lowered:
            continue
        count += 1
    return count


def current_status(root: Path, inventory_rows: list[dict[str, str]]) -> dict[str, Any]:
    unified = read_jsonl(root / "eng_bench.jsonl")
    pairs = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    doc_to_domain, safe_docs = inventory_maps(inventory_rows)
    provenance = audit_active_gold_provenance.build_report(root)

    rows_by_task = Counter(str(row.get("task", "unknown")) for row in unified)
    rows_by_split = Counter(str(row.get("split", "unknown")) for row in unified)
    test_by_task = Counter(
        str(row.get("task", "unknown")) for row in unified if row.get("split") == "test"
    )
    source_ids = logical_source_group_ids(unified, pairs)
    families = {
        visualdiff_family(str(row.get("pair_id") or row.get("id") or ""))
        for row in pairs
        if row.get("pair_id") or row.get("id")
    }

    rows_by_domain: Counter[str] = Counter()
    rows_by_domain_task: dict[str, Counter[str]] = defaultdict(Counter)
    microtext_categories: Counter[str] = Counter()
    for row in unified:
        task = str(row.get("task", "unknown"))
        doc_id = source_doc_id(row)
        domain = infer_domain(doc_id, doc_to_domain)
        rows_by_domain[domain] += 1
        rows_by_domain_task[domain][task] += 1
        if task == "microtext":
            microtext_categories[row_category(row)] += 1

    return {
        "total_rows": len(unified),
        "rows_by_task": dict(sorted(rows_by_task.items())),
        "rows_by_split": dict(sorted(rows_by_split.items())),
        "test_rows_by_task": dict(sorted(test_by_task.items())),
        "source_count": len({source_id for source_id in source_ids if source_id}),
        "source_count_definition": "microtext document IDs plus visualdiff project/family IDs",
        "gold_source_docs": int(provenance["totals"]["active_source_docs"]),
        "release_safe_inventory_docs": len(safe_docs),
        "visualdiff_revision_families": len(families),
        "baseline_reports": baseline_report_count(root),
        "rows_by_domain": dict(sorted(rows_by_domain.items())),
        "rows_by_domain_task": {
            domain: dict(sorted(task_counts.items()))
            for domain, task_counts in sorted(rows_by_domain_task.items())
        },
        "microtext_categories": dict(sorted(microtext_categories.items())),
    }


def gate_gaps(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    gaps: dict[str, dict[str, Any]] = {}
    for release, targets in RELEASE_TARGETS.items():
        release_gaps: dict[str, Any] = {}
        for key, target in targets.items():
            if key == "microtext_rows":
                current = status["rows_by_task"].get("microtext", 0)
            elif key == "visualdiff_rows":
                current = status["rows_by_task"].get("visualdiff", 0)
            elif key == "test_microtext_rows":
                current = status["test_rows_by_task"].get("microtext", 0)
            elif key == "test_visualdiff_rows":
                current = status["test_rows_by_task"].get("visualdiff", 0)
            elif key == "test_rows":
                current = status["rows_by_split"].get("test", 0)
            else:
                current = status.get(key, 0)
            release_gaps[key] = {
                "current": current,
                "target": target,
                "remaining": max(0, int(target) - int(current)),
                "passes": int(current) >= int(target),
            }
        gaps[release] = release_gaps
    return gaps


def status_for(row: dict[str, Any]) -> str:
    for key in ("review_status", "human_status", "annotation_status", "status"):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def id_for(row: dict[str, Any]) -> str:
    for key in ("candidate_id", "pair_id", "id", "item_id", "question_id"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def path_fields(row: dict[str, Any]) -> list[tuple[str, str]]:
    fields = []
    for key, value in row.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if key.endswith("_path") or key in {"image_path", "image_old", "image_new"}:
            fields.append((key, value))
    return fields


def missing_paths(root: Path, row: dict[str, Any]) -> list[str]:
    missing = []
    for _key, value in path_fields(row):
        path = Path(value)
        full_path = path if path.is_absolute() else root / path
        if not full_path.exists():
            missing.append(value)
    return missing


def review_doc_or_family(row: dict[str, Any], kind: str) -> str:
    if kind == "visualdiff":
        return str(row.get("project_id") or visualdiff_family(str(row.get("pair_id") or "")) or "unknown")
    return str(row.get("doc_id") or "unknown")


def review_domain(
    row: dict[str, Any],
    kind: str,
    doc_to_domain: dict[str, str],
    candidate_to_domain: dict[str, str],
) -> str:
    candidate_id = str(row.get("source_candidate_id") or "")
    if candidate_id and candidate_id in candidate_to_domain:
        return candidate_to_domain[candidate_id]
    doc_id = str(row.get("doc_id") or "")
    if doc_id:
        return infer_domain(doc_id, doc_to_domain)
    if kind == "visualdiff":
        project = str(row.get("project_id") or row.get("pair_id") or "")
        return infer_domain(project, doc_to_domain)
    return "unknown"


def summarize_review_file(
    root: Path,
    path: Path,
    kind: str,
    doc_to_domain: dict[str, str],
    candidate_to_domain: dict[str, str],
    category_counts: dict[str, int],
) -> dict[str, Any]:
    rows = read_jsonl(path)
    statuses = Counter(status_for(row) for row in rows)
    open_rows = sum(count for status, count in statuses.items() if status in OPEN_STATUSES)
    mergeable_rows = sum(count for status, count in statuses.items() if status in MERGEABLE_STATUSES)
    missing_evidence = sum(1 for row in rows if missing_paths(root, row))
    domain_counts: Counter[str] = Counter()
    category_open: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    for row in rows:
        domain = review_domain(row, kind, doc_to_domain, candidate_to_domain)
        source = review_doc_or_family(row, kind)
        domain_counts[domain] += 1
        source_counts[source] += 1
        if status_for(row) in OPEN_STATUSES and kind == "microtext":
            category_open[str(row.get("category") or "unknown")] += 1

    category_gap_score = 0
    for category, count in category_open.items():
        target = MICROTEXT_V1_TARGET_BY_CATEGORY.get(category, 0)
        current = category_counts.get(category, 0)
        category_gap_score += min(count, max(0, target - current))
    score = open_rows * 10 + mergeable_rows * 20 + category_gap_score - missing_evidence * 30
    if kind == "visualdiff":
        score += open_rows * 5

    return {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "rows": len(rows),
        "open_rows": open_rows,
        "mergeable_rows": mergeable_rows,
        "missing_evidence_rows": missing_evidence,
        "status_counts": dict(sorted(statuses.items())),
        "domains": dict(sorted(domain_counts.items())),
        "open_categories": dict(sorted(category_open.items())),
        "top_sources": dict(source_counts.most_common(5)),
        "priority_score": score,
    }


def review_queue_status(
    root: Path,
    inventory_rows: list[dict[str, str]],
    candidates: list[dict[str, str]],
    category_counts: dict[str, int],
    packet_date_label: str | None = None,
) -> dict[str, Any]:
    doc_to_domain, _safe_docs = inventory_maps(inventory_rows)
    candidate_to_domain, _candidate_rows = candidate_domain_maps(candidates)
    files: list[dict[str, Any]] = []
    for path in sorted((root / "microtext" / "annotations").glob("microtext_review*.jsonl")):
        files.append(
            summarize_review_file(
                root, path, "microtext", doc_to_domain, candidate_to_domain, category_counts
            )
        )
    for path in sorted((root / "visualdiff" / "annotations").glob("visualdiff_review*.jsonl")):
        files.append(
            summarize_review_file(
                root, path, "visualdiff", doc_to_domain, candidate_to_domain, category_counts
            )
        )

    totals = Counter()
    by_domain: dict[str, Counter[str]] = defaultdict(Counter)
    for item in files:
        for key in ("rows", "open_rows", "mergeable_rows", "missing_evidence_rows"):
            totals[key] += int(item[key])
        for domain, count in item["domains"].items():
            by_domain[domain]["rows"] += int(count)
        for domain in item["domains"]:
            by_domain[domain]["files"] += 1
        dominant_domain = max(item["domains"], key=item["domains"].get) if item["domains"] else "unknown"
        by_domain[dominant_domain]["open_rows"] += int(item["open_rows"])
        by_domain[dominant_domain]["mergeable_rows"] += int(item["mergeable_rows"])
        by_domain[dominant_domain]["missing_evidence_rows"] += int(item["missing_evidence_rows"])
    totals["files"] = len(files)

    resolution_inventory = review_queue_inventory.inventory(
        root,
        packet_date_label=packet_date_label,
    )
    files_by_path = {str(item["path"]): item for item in files}
    human_priority: list[dict[str, Any]] = []
    for resolution_item in resolution_inventory["next_human_candidates"]:
        item = dict(files_by_path.get(str(resolution_item["path"]), resolution_item))
        for key in (
            "fresh_open_rows",
            "actionable_fresh_open_rows",
            "rights_blocked_fresh_open_rows",
            "packeted_open_rows",
            "stale_open_rows",
        ):
            item[key] = int(resolution_item.get(key, 0))
        human_priority.append(item)
    human_priority.sort(
        key=lambda item: (
            int(item.get("actionable_fresh_open_rows", 0)),
            int(item.get("priority_score", 0)),
        ),
        reverse=True,
    )

    return {
        "totals": dict(totals),
        "resolution_totals": resolution_inventory["totals"],
        "by_domain": {
            domain: dict(sorted(counts.items()))
            for domain, counts in sorted(by_domain.items())
        },
        "human_priority_files": human_priority[:20],
        "files": files,
    }


def domain_status(
    status: dict[str, Any],
    review_status: dict[str, Any],
    inventory_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    inventory_counts = Counter(
        quota_domain(row.get("domain")) for row in inventory_rows if row.get("doc_id")
    )
    safe_inventory_counts = Counter(
        quota_domain(row.get("domain")) for row in inventory_rows if is_release_safe(row)
    )
    domains = sorted(set(DOMAIN_V1_5_MICROTEXT_TARGETS) | set(status["rows_by_domain"]) | set(review_status["by_domain"]))
    rows: list[dict[str, Any]] = []
    for domain in domains:
        task_counts = status["rows_by_domain_task"].get(domain, {})
        review_counts = review_status["by_domain"].get(domain, {})
        rows.append(
            {
                "domain": domain,
                "gold_rows": status["rows_by_domain"].get(domain, 0),
                "gold_microtext_rows": task_counts.get("microtext", 0),
                "gold_visualdiff_rows": task_counts.get("visualdiff", 0),
                "release_safe_inventory_docs": safe_inventory_counts[domain],
                "inventory_docs": inventory_counts[domain],
                "review_open_rows": review_counts.get("open_rows", 0),
                "review_mergeable_rows": review_counts.get("mergeable_rows", 0),
                "review_missing_evidence_rows": review_counts.get("missing_evidence_rows", 0),
                "v1_5_microtext_target": DOMAIN_V1_5_MICROTEXT_TARGETS.get(domain, 0),
                "v1_5_microtext_remaining": max(
                    0,
                    DOMAIN_V1_5_MICROTEXT_TARGETS.get(domain, 0) - task_counts.get("microtext", 0),
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            int(row["v1_5_microtext_remaining"]),
            int(row["review_open_rows"]),
            -int(row["gold_rows"]),
        ),
        reverse=True,
    )
    return rows


def category_status(category_counts: dict[str, int], review_status: dict[str, Any]) -> list[dict[str, Any]]:
    open_by_category = Counter()
    for file_summary in review_status["files"]:
        for category, count in file_summary.get("open_categories", {}).items():
            open_by_category[category] += int(count)

    categories = sorted(set(MICROTEXT_V1_TARGET_BY_CATEGORY) | set(category_counts) | set(open_by_category))
    rows = []
    for category in categories:
        target = MICROTEXT_V1_TARGET_BY_CATEGORY.get(category, 0)
        current = category_counts.get(category, 0)
        rows.append(
            {
                "category": category,
                "current": current,
                "v1_0_target": target,
                "remaining_to_v1_0": max(0, target - current),
                "open_review_rows": open_by_category[category],
            }
        )
    rows.sort(key=lambda row: (int(row["remaining_to_v1_0"]), int(row["open_review_rows"])), reverse=True)
    return rows


def machine_import_queue(
    root: Path,
    candidates: list[dict[str, str]],
    validations: list[dict[str, str]],
    inventory_rows: list[dict[str, str]],
    limit: int,
) -> list[dict[str, str]]:
    ranked_candidates = load_csv(root / "SOURCE_CANDIDATES_RANKED.csv") or candidates
    quotas = summarize_quotas(inventory_rows, candidates, validations)
    intake_log = (root / "SOURCE_INTAKE_LOG.md").read_text(encoding="utf-8") if (root / "SOURCE_INTAKE_LOG.md").exists() else ""
    return build_queue(
        ranked_candidates,
        validations,
        quotas,
        imported_candidate_ids(intake_log),
        limit=limit,
    )


def build_plan(root: Path, limit: int = 25, date_label: str = "2026-06-03") -> dict[str, Any]:
    inventory_rows = load_csv(root / "SOURCE_INVENTORY.csv")
    candidates = load_csv(root / "SOURCE_CANDIDATES.csv")
    validations = load_csv(root / "SOURCE_CANDIDATE_VALIDATION.csv")
    status = current_status(root, inventory_rows)
    review_status = review_queue_status(
        root,
        inventory_rows,
        candidates,
        {key: int(value) for key, value in status["microtext_categories"].items()},
        packet_date_label=date_label,
    )
    gaps = gate_gaps(status)
    return {
        "date_label": date_label,
        "current": status,
        "gate_gaps": gaps,
        "human_packet_forecast": human_packet_forecast(root, status, gaps, date_label),
        "domain_status": domain_status(status, review_status, inventory_rows),
        "microtext_category_status": category_status(status["microtext_categories"], review_status),
        "review_queues": review_status,
        "machine_import_priority": machine_import_queue(
            root, candidates, validations, inventory_rows, limit=limit
        ),
        "interpretation": {
            "primary_machine_bottleneck": "Convert release-safe inventory/candidates into reviewed gold rows and new visualdiff revision families.",
            "primary_human_bottleneck": "Complete the verified packet index, then process returned packets into staged merge JSONL before promotion; do not resend raw stale, packeted, or rights-held queues.",
        },
    }


def render_table(rows: list[dict[str, Any]], fields: list[str]) -> list[str]:
    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return lines


def render_markdown(plan: dict[str, Any]) -> str:
    current = plan["current"]
    gaps = plan["gate_gaps"]
    packet_forecast = plan.get("human_packet_forecast", {})
    lines = [
        "# Eng_Bench Gold Expansion Plan",
        "",
        "This report connects release gates to the current source inventory, review queues, and machine import backlog. It is a planning artifact only; it does not promote rows into gold.",
        "",
        f"- Date label: `{plan.get('date_label', 'unknown')}`",
        "",
        "## Current Counts",
        "",
        f"- Total rows: `{current['total_rows']}`",
        f"- Rows by task: `{current['rows_by_task']}`",
        f"- Rows by split: `{current['rows_by_split']}`",
        f"- Logical source groups: `{current['source_count']}` "
        f"({current.get('source_count_definition', 'microtext docs plus visualdiff projects')})",
        f"- Physical gold source documents: `{current['gold_source_docs']}` "
        "(the provenance-gate count; visualdiff projects may use multiple revision documents)",
        f"- Release-safe inventory docs: `{current['release_safe_inventory_docs']}`",
        f"- Visualdiff revision families: `{current['visualdiff_revision_families']}`",
        f"- Counted baseline reports: `{current['baseline_reports']}`",
        "",
        "## Gate Gaps",
        "",
        "| Release | Gate | Current | Target | Remaining | Pass |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for release, release_gaps in gaps.items():
        for gate, row in release_gaps.items():
            lines.append(
                f"| {release} | {gate} | {row['current']} | {row['target']} | "
                f"{row['remaining']} | `{row['passes']}` |"
            )

    lines.extend(["", "## Current Human Packet Forecast", ""])
    if packet_forecast.get("available"):
        lines.extend(
            [
                f"- Packet index: `{packet_forecast['packet_index_path']}`",
                f"- Ready packets: `{packet_forecast['packets_ready_to_send']}/{packet_forecast['packet_count']}`",
                f"- Review rows upper bound: `{packet_forecast['review_rows_upper_bound']}`",
                f"- Inspectable manifest rows: `{packet_forecast['manifest_rows_inspectable']}`",
                f"- Missing evidence refs: `{packet_forecast['missing_evidence_refs']}`",
                f"- Manifest rows by task: `{packet_forecast['manifest_rows_by_task']}`",
                f"- Caveat: {packet_forecast['caveat']}",
                "",
                "### Release Projection If All Current Packet Rows Are Accepted",
                "",
            ]
        )
        projection_rows = [
            {
                "release": release,
                **projection,
            }
            for release, projection in packet_forecast["release_projection"].items()
        ]
        lines.extend(
            render_table(
                projection_rows,
                [
                    "release",
                    "current_total_rows",
                    "review_rows_upper_bound",
                    "projected_total_rows_if_all_packet_rows_accepted",
                    "target_total_rows",
                    "remaining_after_all_packet_rows_accepted",
                ],
            )
        )
        lines.extend(["", "### Human Packets", ""])
        lines.extend(
            render_table(
                packet_forecast["packets"],
                [
                    "packet_id",
                    "kind",
                    "ready_to_send",
                    "human_status",
                    "review_rows",
                    "manifest_rows",
                    "missing_evidence_refs",
                    "zip_path",
                ],
            )
        )
    else:
        lines.append(f"- Packet index unavailable: {packet_forecast.get('reason', 'unknown')}")

    lines.extend(["", "## Domain Conversion Status", ""])
    lines.extend(
        render_table(
            plan["domain_status"],
            [
                "domain",
                "gold_rows",
                "gold_microtext_rows",
                "gold_visualdiff_rows",
                "release_safe_inventory_docs",
                "review_open_rows",
                "review_mergeable_rows",
                "v1_5_microtext_remaining",
            ],
        )
    )

    lines.extend(["", "## Microtext Category Gaps", ""])
    lines.extend(
        render_table(
            plan["microtext_category_status"],
            ["category", "current", "v1_0_target", "remaining_to_v1_0", "open_review_rows"],
        )
    )

    resolution_totals = plan["review_queues"].get("resolution_totals", {})
    lines.extend(
        [
            "",
            "## Raw Queue Resolution",
            "",
            f"- Raw open rows: `{resolution_totals.get('open_rows', 0)}`",
            f"- Actionable release-safe fresh rows: `{resolution_totals.get('actionable_fresh_open_rows', 0)}`",
            f"- Rights-blocked fresh rows: `{resolution_totals.get('rights_blocked_fresh_open_rows', 0)}`",
            f"- Active-packet open rows: `{resolution_totals.get('packeted_open_rows', 0)}`",
            f"- Stale open rows already resolved: `{resolution_totals.get('stale_open_rows', 0)}`",
            "- Human source of truth: the verified packet index above. Raw queue files are audit history, not automatically new assignments.",
        ]
    )

    lines.extend(["", "## Highest-Priority Human Queues", ""])
    lines.extend(
        render_table(
            plan["review_queues"]["human_priority_files"][:12],
            [
                "path",
                "kind",
                "actionable_fresh_open_rows",
                "fresh_open_rows",
                "packeted_open_rows",
                "stale_open_rows",
                "missing_evidence_rows",
            ],
        )
    )

    lines.extend(["", "## Highest-Priority Machine Imports", ""])
    lines.extend(
        render_table(
            plan["machine_import_priority"][:12],
            ["candidate_id", "domain", "task_fit", "queue_score", "next_action", "reason"],
        )
    )

    lines.extend(
        [
            "",
            "## Next Actions",
            "",
            "1. Process any returned human packets with `tools/process_v1_5_human_return.py --strict`; merge only rows staged as mergeable after maintainer inspection.",
            "2. Give humans only verified packet-index work or release-safe actionable fresh queues; do not resend packeted, stale/resolved, or rights-held rows.",
            "3. Machine-side, convert the top release-safe import candidates into rendered pages, text layers, candidate JSONL, and review packs.",
            "4. Keep visualdiff family splits family-pure and rerun leakage/health gates after every merge.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Eng_Bench source-to-gold expansion plan.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--limit", type=int, default=25, help="Machine import queue limit")
    parser.add_argument("--date-label", default=date.today().isoformat(), help="Date label for dated outputs")
    parser.add_argument("--output-json", help="Output JSON path; defaults to dated quality report")
    parser.add_argument("--output-md", help="Output Markdown path; defaults to dated quality report")
    parser.add_argument("--human-csv", default="docs/GOLD_EXPANSION_HUMAN_QUEUE.csv")
    parser.add_argument("--machine-csv", default="docs/GOLD_EXPANSION_MACHINE_QUEUE.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    output_json = args.output_json or f"derived/quality/gold_expansion_plan_{args.date_label}.json"
    output_md = args.output_md or f"derived/quality/gold_expansion_plan_{args.date_label}.md"
    plan = build_plan(root, limit=args.limit, date_label=args.date_label)

    write_json(root / output_json, plan)
    md_path = root / output_md
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(plan), encoding="utf-8")
    write_csv(
        root / args.human_csv,
        plan["review_queues"]["human_priority_files"],
        fieldnames=HUMAN_QUEUE_FIELDS,
    )
    write_csv(root / args.machine_csv, plan["machine_import_priority"])

    print(f"[OK] Wrote {root / output_json}")
    print(f"[OK] Wrote {md_path}")
    print(f"[OK] Wrote {root / args.human_csv}")
    print(f"[OK] Wrote {root / args.machine_csv}")
    print(json.dumps(plan["current"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
