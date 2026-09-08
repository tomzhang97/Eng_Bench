#!/usr/bin/env python3
"""Register a workbook-only human assignment in the active packet index."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_source_conversion_readiness as source_readiness
import verify_multi_reviewer_handoff as workbook_io


MICROTEXT_FIELDS = {
    "candidate_id",
    "item_id",
    "doc_id",
    "version_id",
    "source_candidate_id",
    "image_path",
    "crop_path",
    "full_page_path",
    "bbox",
    "bbox_px",
    "page_index",
    "category",
    "target_text",
    "proposed_text",
    "raw_text",
}
VISUALDIFF_FIELDS = {
    "pair_id",
    "id",
    "project_id",
    "doc_id",
    "old_doc_id",
    "new_doc_id",
    "source_candidate_id",
    "old_image_path",
    "new_image_path",
    "image_old",
    "image_new",
    "old_crop_path",
    "new_crop_path",
    "panel_path",
    "bbox_old",
    "bbox_new",
    "old_bbox",
    "new_bbox",
    "change_type",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_write_text(
        path,
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
    )


def identity_for(row: dict[str, Any], task: str) -> str:
    fields = ("candidate_id", "item_id", "id") if task == "microtext" else ("pair_id", "id")
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def workbook_rows(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    microtext_rows, _microtext_meta = workbook_io.table_records(path, "MicroText")
    visualdiff_rows, _visualdiff_meta = workbook_io.table_records(path, "VisualDiff")
    return microtext_rows, visualdiff_rows


def source_row_score(path: Path, row: dict[str, Any], task: str) -> tuple[int, ...]:
    name = path.name.lower()
    evidence_fields = (
        ("image_path", "crop_path", "full_page_path")
        if task == "microtext"
        else (
            "panel_path",
            "old_crop_path",
            "new_crop_path",
            "old_image_path",
            "new_image_path",
            "image_old",
            "image_new",
        )
    )
    preferred_name = any(
        token in name
        for token in ("human_ready", "machine_cleared", "passing", "revalidated", "curated")
    )
    discouraged_name = any(token in name for token in ("_raw", "_held", "_holds", "superseded"))
    doc_field = "doc_id" if task == "microtext" else "project_id"
    bbox_fields = ("bbox", "bbox_px") if task == "microtext" else (
        "bbox_old",
        "bbox_new",
        "old_bbox",
        "new_bbox",
    )
    label_fields = (
        ("category", "target_text", "proposed_text")
        if task == "microtext"
        else ("change_type", "description", "proposed_description")
    )
    return (
        int(not source_readiness.review_exclusion_reason(row)),
        int(preferred_name),
        int(not discouraged_name),
        int(bool(str(row.get(doc_field) or row.get("doc_id") or "").strip())),
        int(str(row.get("version_id") or "").strip().lower() not in {"", "unknown"}),
        sum(bool(str(row.get(field) or "").strip()) for field in evidence_fields),
        sum(row.get(field) not in (None, "", []) for field in bbox_fields),
        sum(bool(str(row.get(field) or "").strip()) for field in label_fields),
    )


def collect_best_source_rows(
    root: Path,
    wanted: dict[str, set[str]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, list[str]]]:
    best: dict[tuple[str, str], tuple[tuple[int, ...], str, dict[str, Any]]] = {}
    lineage_values: dict[tuple[str, str], set[str]] = defaultdict(set)
    roots = (
        ("microtext", root / "microtext" / "annotations", "microtext_review*.jsonl"),
        ("visualdiff", root / "visualdiff" / "annotations", "visualdiff_review*.jsonl"),
    )
    for task, annotation_root, pattern in roots:
        for path in sorted(annotation_root.glob(pattern)):
            for row in read_jsonl(path):
                identity = identity_for(row, task)
                if identity not in wanted.get(task, set()):
                    continue
                lineage = str(
                    row.get("doc_id")
                    or row.get("project_id")
                    or row.get("source_candidate_id")
                    or ""
                ).strip()
                if lineage:
                    lineage_values[(task, identity)].add(lineage)
                score = source_row_score(path, row, task)
                key = (task, identity)
                candidate = (score, path.as_posix(), row)
                if key not in best or candidate[:2] > best[key][:2]:
                    best[key] = candidate
    conflicts = {
        f"{task}:{identity}": sorted(values)
        for (task, identity), values in lineage_values.items()
        if len(values) > 1
    }
    return {key: value[2] for key, value in best.items()}, conflicts


def build_manifest_rows(
    workbook_path: Path,
    microtext_records: list[dict[str, str]],
    visualdiff_records: list[dict[str, str]],
    source_rows: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    output: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for task, records, fields in (
        ("microtext", microtext_records, MICROTEXT_FIELDS),
        ("visualdiff", visualdiff_records, VISUALDIFF_FIELDS),
    ):
        for record in records:
            identity = str(
                record.get("candidate_id" if task == "microtext" else "pair_id") or ""
            ).strip()
            source = source_rows.get((task, identity))
            if source is None:
                unresolved.append(f"{task}:{identity}")
                source = {}
            row = {key: source[key] for key in fields if key in source}
            row["candidate_id" if task == "microtext" else "pair_id"] = identity
            row["task"] = task
            row["primary_index"] = str(record.get("primary_index") or record.get("#") or "")
            row["assignment_workbook"] = workbook_path.name
            row["review_status"] = "needs_review"
            row["packet_review_status"] = "awaiting_human_return"
            row["promotion_state"] = "unreviewed_candidate"
            row["safe_to_merge_gold"] = False
            output.append(row)
    return output, unresolved


def build_auditor_rows(
    auditor_paths: list[Path],
    primary_ids: set[tuple[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    issues: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in auditor_paths:
        microtext, visualdiff = workbook_rows(path)
        for task, records, field in (
            ("microtext", microtext, "candidate_id"),
            ("visualdiff", visualdiff, "pair_id"),
        ):
            for record in records:
                identity = str(record.get(field) or "").strip()
                key = (task, identity)
                if key not in primary_ids:
                    issues.append(f"{path.name}: row outside primary assignment: {task}:{identity}")
                if key in seen:
                    issues.append(f"duplicate auditor assignment: {task}:{identity}")
                seen.add(key)
                rows.append(
                    {
                        "reviewer_workbook": path.name,
                        "task": task,
                        field: identity,
                        "primary_index": str(record.get("primary_index") or record.get("#") or ""),
                    }
                )
    return rows, issues


def relative_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    return resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else resolved.as_posix()


def build_index(
    root: Path,
    primary_workbook: Path,
    auditor_workbooks: list[Path],
    sidecar_dir: Path,
    index_path: Path,
    date_label: str,
    packet_id: str,
    delivery_zip: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    primary_workbook = primary_workbook.resolve()
    auditor_workbooks = [path.resolve() for path in auditor_workbooks]
    sidecar_dir = sidecar_dir if sidecar_dir.is_absolute() else root / sidecar_dir
    index_path = index_path if index_path.is_absolute() else root / index_path
    delivery_zip = delivery_zip.resolve() if delivery_zip else None

    microtext, visualdiff = workbook_rows(primary_workbook)
    wanted = {
        "microtext": {str(row.get("candidate_id") or "").strip() for row in microtext},
        "visualdiff": {str(row.get("pair_id") or "").strip() for row in visualdiff},
    }
    source_rows, lineage_conflicts = collect_best_source_rows(root, wanted)
    manifest_rows, unresolved = build_manifest_rows(
        primary_workbook,
        microtext,
        visualdiff,
        source_rows,
    )
    primary_ids = {(row["task"], identity_for(row, row["task"])) for row in manifest_rows}
    auditor_rows, auditor_issues = build_auditor_rows(auditor_workbooks, primary_ids)

    identities = [(row["task"], identity_for(row, row["task"])) for row in manifest_rows]
    issues: list[str] = []
    if any(not identity for _task, identity in identities):
        issues.append("primary workbook contains a blank row identity")
    if len(identities) != len(set(identities)):
        issues.append("primary workbook contains duplicate row identities")
    if unresolved:
        issues.append(f"{len(unresolved)} primary rows could not be resolved to source queues")
    if lineage_conflicts:
        issues.append(f"{len(lineage_conflicts)} primary identities have conflicting source lineage")
    issues.extend(auditor_issues)

    sidecar_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = sidecar_dir / "manifest.jsonl"
    auditor_path = sidecar_dir / "auditor_assignments.jsonl"
    report_path = sidecar_dir / "assignment_report.json"
    write_jsonl(manifest_path, manifest_rows)
    write_jsonl(auditor_path, auditor_rows)

    packet = {
        "packet_id": packet_id,
        "ready_to_send": True,
        "mergeable_rows": 0,
        "manifest_rows": len(manifest_rows),
        "folder_path": relative_path(root, sidecar_dir),
        "primary_workbook": relative_path(root, primary_workbook),
        "delivery_zip": relative_path(root, delivery_zip) if delivery_zip else "",
        "delivery_zip_sha256": sha256(delivery_zip) if delivery_zip and delivery_zip.is_file() else "",
    }
    index = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_changed": False,
        "packets": [packet],
    }
    atomic_write_text(index_path, json.dumps(index, ensure_ascii=False, indent=2) + "\n")

    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "packet_id": packet_id,
        "primary_rows": len(manifest_rows),
        "microtext_rows": len(microtext),
        "visualdiff_rows": len(visualdiff),
        "source_rows_resolved": len(manifest_rows) - len(unresolved),
        "unresolved_rows": unresolved,
        "lineage_conflicts": lineage_conflicts,
        "auditor_workbooks": len(auditor_workbooks),
        "auditor_rows": len(auditor_rows),
        "unique_auditor_rows": len(
            {(row["task"], identity_for(row, row["task"])) for row in auditor_rows}
        ),
        "manifest_path": relative_path(root, manifest_path),
        "auditor_assignment_path": relative_path(root, auditor_path),
        "active_packet_index": relative_path(root, index_path),
        "active_gold_changed": False,
        "issues": issues,
        "valid": not issues,
    }
    atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--primary-workbook", type=Path, required=True)
    parser.add_argument("--auditor-workbook", type=Path, action="append", default=[])
    parser.add_argument("--sidecar-dir", type=Path, required=True)
    parser.add_argument("--index-json", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--delivery-zip", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    def rooted(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    report = build_index(
        root=root,
        primary_workbook=rooted(args.primary_workbook),
        auditor_workbooks=[rooted(path) for path in args.auditor_workbook],
        sidecar_dir=args.sidecar_dir,
        index_path=args.index_json,
        date_label=args.date_label,
        packet_id=args.packet_id,
        delivery_zip=rooted(args.delivery_zip) if args.delivery_zip else None,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if args.strict and not report["valid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
