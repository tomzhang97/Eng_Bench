#!/usr/bin/env python3
"""Audit whether staged review rows can enter the strict promotion path."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_staged_v2_capacity as staged
from audit_active_gold_provenance import (
    file_sha256,
    manifest_maps,
    read_csv,
    resolve_visualdiff_docs,
)
from source_rights import rights_blocker
from text_encoding import mojibake_signatures
from visualdiff_merge import normalized_change_type


VALID_SPLITS = {"train", "dev", "test"}
FINAL_MICROTEXT_STATUSES = {"accepted", "edited"}
FINAL_VISUALDIFF_STATUSES = {"accepted", "edited", "valid", "edit"}
UNKNOWN_MICROTEXT_CATEGORIES = {"", "unknown", "unknown_microtext"}


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["severity", "issue", "cohort", "task", "identity", "detail"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_cohort(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("cohort must use NAME=PATH")
    name, path = value.split("=", 1)
    if not name.strip() or not path.strip():
        raise argparse.ArgumentTypeError("cohort must use non-empty NAME=PATH")
    return name.strip(), Path(path.strip())


def parse_bbox(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = tuple(int(round(float(item))) for item in value)
    except (TypeError, ValueError):
        return None
    return bbox if bbox[0] < bbox[2] and bbox[1] < bbox[3] else None


def image_dimensions(path: Path) -> tuple[int, int] | None:
    """Read trusted local image metadata without decoding engineering sheets."""
    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as image:
            return image.size
    except OSError:
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def row_strings(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"source_raw_text", "source_text_parts", "upstream_raw_text"}:
                continue
            yield from row_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from row_strings(child)
    elif isinstance(value, str):
        yield value


def page_value(row: dict[str, Any], side: str) -> int | None:
    for field in (f"page_index_{side}", f"page_{side}"):
        if row.get(field) not in (None, ""):
            try:
                return int(row[field])
            except (TypeError, ValueError):
                return None
    pair_id = str(row.get("pair_id") or "")
    for token in pair_id.split("__"):
        if token.startswith("p") and token[1:].isdigit():
            return int(token[1:])
    return None


def promotion_aliases(row: dict[str, Any], task: str) -> set[str]:
    """Return identifiers that must be unique at promotion time."""
    fields = ("pair_id", "id") if task == "visualdiff" else (
        "candidate_id",
        "item_id",
    )
    return {
        str(row.get(field) or "").strip()
        for field in fields
        if str(row.get(field) or "").strip()
    }


def lineage_aliases(row: dict[str, Any], task: str) -> set[str]:
    """Return non-unique upstream lineage identifiers for diagnostics."""
    if task != "microtext":
        return set()
    values = {
        str(row.get("pre_padding_candidate_id") or "").strip(),
        str(row.get("source_candidate_id") or "").strip(),
    }
    return {value for value in values if value.startswith("mtcand__")}


def reservation_records(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        (str(row.get("task") or ""), str(row.get("unit_id") or "")): row
        for row in payload.get("reservations", [])
    }


def review_status(row: dict[str, Any], task: str) -> str:
    if task == "visualdiff":
        return str(
            row.get("human_review_status")
            or row.get("human_status")
            or row.get("review_status")
            or ""
        ).strip().lower()
    return str(row.get("review_status") or "").strip().lower()


def source_audit(
    root: Path,
    doc_id: str,
    docs: dict[str, dict[str, Any]],
    inventory: dict[str, dict[str, str]],
) -> list[str]:
    issues: list[str] = []
    manifest = docs.get(doc_id)
    source = inventory.get(doc_id)
    if not manifest:
        return ["missing_manifest_doc"]
    if not source:
        return ["missing_inventory_doc"]
    relative = str(source.get("path") or manifest.get("path") or "").strip()
    local = resolve(root, relative) if relative else None
    if local is None or not local.is_file():
        issues.append("missing_source_payload")
    recorded = str(manifest.get("sha256") or "").strip().lower()
    if not recorded:
        issues.append("missing_recorded_sha256")
    elif local is not None and local.is_file() and file_sha256(local).lower() != recorded:
        issues.append("source_sha256_mismatch")
    if not str(source.get("source_url") or manifest.get("source_url") or "").strip():
        issues.append("missing_source_url")
    inventory_status = str(source.get("public_status") or "").strip()
    manifest_status = str(manifest.get("public_status") or "").strip()
    status = inventory_status or manifest_status
    blocker = rights_blocker(status)
    if blocker:
        issues.append(f"rights_blocked:{blocker}")
    if inventory_status and manifest_status and bool(rights_blocker(inventory_status)) != bool(rights_blocker(manifest_status)):
        issues.append("rights_status_disagreement")
    return issues


def add_issue(
    rows: list[dict[str, str]],
    counts: Counter[str],
    *,
    severity: str,
    issue: str,
    cohort: str,
    task: str,
    identity: str,
    detail: str = "",
) -> None:
    counts[f"{severity}:{issue}"] += 1
    rows.append(
        {
            "severity": severity,
            "issue": issue,
            "cohort": cohort,
            "task": task,
            "identity": identity,
            "detail": detail,
        }
    )


def build_report(
    root: Path,
    cohorts: list[tuple[str, Path]],
    split_plan_path: Path,
    *,
    date_label: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    root = root.resolve()
    split_plan_path = resolve(root, split_plan_path)
    reservations, split_issues = staged.load_split_reservations(split_plan_path)
    split_payload = json.loads(split_plan_path.read_text(encoding="utf-8"))
    reservation_ids = {
        (str(row.get("task") or ""), str(row.get("unit_id") or "")): str(row.get("reservation_id") or "")
        for row in split_payload.get("reservations", [])
    }
    docs, manifest_pairs = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or ""): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }
    active = staged.read_rows(root / "microtext/annotations/microtext_items.jsonl") + staged.read_rows(
        root / "visualdiff/annotations/visualdiff_pairs.jsonl"
    )
    active_capacity = {staged.capacity_identity(row) for row in active if staged.capacity_identity(row)}
    active_aliases = {
        f"{staged.task_for_row(row)}:{alias}"
        for row in active
        for alias in promotion_aliases(row, staged.task_for_row(row))
    }

    issue_rows: list[dict[str, str]] = []
    issue_counts: Counter[str] = Counter()
    task_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    cohort_counts: Counter[str] = Counter()
    human_actions: Counter[str] = Counter()
    change_types: Counter[str] = Counter()
    seen_capacity: set[str] = set()
    seen_promotion_aliases: set[str] = set()
    seen_lineage_aliases: set[str] = set()
    contract_notes: Counter[str] = Counter()
    split_plan_cache: dict[Path, dict[tuple[str, str], dict[str, Any]]] = {}
    source_cache: dict[str, list[str]] = {}
    dimensions: dict[Path, tuple[int, int] | None] = {}
    total_rows = 0

    for split_issue in split_issues:
        add_issue(
            issue_rows,
            issue_counts,
            severity="fatal",
            issue=str(split_issue.get("type") or "split_plan_issue"),
            cohort="split_plan",
            task=str(split_issue.get("task") or ""),
            identity=str(split_issue.get("unit_id") or ""),
            detail=json.dumps(split_issue, sort_keys=True),
        )

    for cohort_name, raw_path in cohorts:
        path = resolve(root, raw_path)
        for row in staged.read_rows(path):
            total_rows += 1
            task = staged.task_for_row(row)
            identity = staged.capacity_identity(row)
            row_identity = staged.row_identity(row)
            task_counts[task] += 1
            cohort_counts[cohort_name] += 1
            if not identity:
                add_issue(issue_rows, issue_counts, severity="fatal", issue="missing_capacity_identity", cohort=cohort_name, task=task, identity=row_identity)
                continue
            if identity in seen_capacity:
                add_issue(issue_rows, issue_counts, severity="fatal", issue="duplicate_staged_identity", cohort=cohort_name, task=task, identity=identity)
            seen_capacity.add(identity)
            aliases = {f"{task}:{alias}" for alias in promotion_aliases(row, task)}
            duplicate_aliases = aliases & seen_promotion_aliases
            if duplicate_aliases:
                add_issue(issue_rows, issue_counts, severity="fatal", issue="duplicate_staged_alias", cohort=cohort_name, task=task, identity=identity, detail=";".join(sorted(duplicate_aliases)))
            seen_promotion_aliases.update(aliases)
            lineage = {f"{task}:{alias}" for alias in lineage_aliases(row, task)}
            contract_notes["reused_upstream_lineage_aliases"] += len(lineage & seen_lineage_aliases)
            seen_lineage_aliases.update(lineage)
            if identity in active_capacity or aliases & active_aliases:
                add_issue(issue_rows, issue_counts, severity="fatal", issue="active_gold_overlap", cohort=cohort_name, task=task, identity=identity)
            if row.get("safe_to_merge_gold") is not False:
                add_issue(issue_rows, issue_counts, severity="fatal", issue="unsafe_pre_review_merge_flag", cohort=cohort_name, task=task, identity=identity, detail=repr(row.get("safe_to_merge_gold")))
            if any(mojibake_signatures(text) for text in row_strings(row)):
                add_issue(issue_rows, issue_counts, severity="fatal", issue="benchmark_text_encoding_error", cohort=cohort_name, task=task, identity=identity)

            unit = staged.staged_split_unit(row)
            expected_split = reservations.get(unit, "")
            actual_split = str(row.get("reserved_split") or "").strip().lower()
            if actual_split not in VALID_SPLITS:
                add_issue(issue_rows, issue_counts, severity="fatal", issue="missing_reserved_split", cohort=cohort_name, task=task, identity=identity, detail=actual_split)
            elif expected_split != actual_split:
                add_issue(issue_rows, issue_counts, severity="fatal", issue="split_reservation_mismatch", cohort=cohort_name, task=task, identity=identity, detail=f"expected={expected_split};actual={actual_split}")
            else:
                split_counts[actual_split] += 1
            expected_reservation_id = reservation_ids.get(unit, "")
            if expected_reservation_id and str(row.get("split_reservation_id") or "") != expected_reservation_id:
                legacy_path_text = str(row.get("split_reservation_plan") or "").strip()
                legacy_path = resolve(root, legacy_path_text) if legacy_path_text else None
                legacy_record = None
                if legacy_path is not None and legacy_path.is_file():
                    if legacy_path not in split_plan_cache:
                        split_plan_cache[legacy_path] = reservation_records(legacy_path)
                    legacy_record = split_plan_cache[legacy_path].get(unit)
                legacy_valid = bool(
                    legacy_record
                    and str(legacy_record.get("reservation_id") or "")
                    == str(row.get("split_reservation_id") or "")
                    and str(legacy_record.get("split") or "").strip().lower() == actual_split
                )
                if legacy_valid:
                    contract_notes["valid_legacy_split_reservation_ids"] += 1
                else:
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="split_reservation_id_mismatch", cohort=cohort_name, task=task, identity=identity, detail=f"expected={expected_reservation_id};actual={row.get('split_reservation_id')}" )

            status = review_status(row, task)
            final_statuses = FINAL_VISUALDIFF_STATUSES if task == "visualdiff" else FINAL_MICROTEXT_STATUSES
            if status not in final_statuses:
                human_actions[f"{task}:decision_required"] += 1

            if task == "microtext":
                doc_id = str(row.get("doc_id") or "").strip()
                doc_ids = [doc_id] if doc_id else []
                bbox_fields = [("image_path", "bbox")]
                category = str(row.get("category") or "").strip()
                answer = str(row.get("corrected_text") or row.get("proposed_text") or row.get("target_text") or "").strip()
                if category in UNKNOWN_MICROTEXT_CATEGORIES:
                    human_actions["microtext:category_correction_required"] += 1
                if not answer:
                    human_actions["microtext:text_required"] += 1
                if not str(row.get("version_id") or "").strip():
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="missing_version_id", cohort=cohort_name, task=task, identity=identity)
            else:
                project_id = str(row.get("project_id") or "").strip()
                doc_ids, resolution_issue = resolve_visualdiff_docs(row, docs, manifest_pairs)
                if not project_id:
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="missing_project_id", cohort=cohort_name, task=task, identity=identity)
                if resolution_issue:
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="visualdiff_manifest_resolution_failed", cohort=cohort_name, task=task, identity=identity, detail=resolution_issue)
                bbox_fields = [("image_old", "bbox_old"), ("image_new", "bbox_new")]
                if page_value(row, "old") is None or page_value(row, "new") is None:
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="missing_page_index", cohort=cohort_name, task=task, identity=identity)
                description = str(row.get("human_description") or row.get("description") or row.get("change_desc_gt") or "").strip()
                if not description or description == "CHANGE_DESC_GT_TODO":
                    human_actions["visualdiff:description_required"] += 1
                normalized = normalized_change_type(row)
                change_types["+".join(normalized)] += 1
                if normalized == ["unknown"]:
                    human_actions["visualdiff:change_type_confirmation_recommended"] += 1

            if not doc_ids:
                add_issue(issue_rows, issue_counts, severity="fatal", issue="missing_source_doc_id", cohort=cohort_name, task=task, identity=identity)
            for doc_id in doc_ids:
                if doc_id not in source_cache:
                    source_cache[doc_id] = source_audit(root, doc_id, docs, inventory)
                for source_issue in source_cache[doc_id]:
                    add_issue(issue_rows, issue_counts, severity="fatal", issue=source_issue, cohort=cohort_name, task=task, identity=identity, detail=doc_id)

            for image_field, bbox_field in bbox_fields:
                image_text = str(row.get(image_field) or "").strip()
                image_path = resolve(root, image_text) if image_text else None
                bbox = parse_bbox(row.get(bbox_field))
                if image_path is None or not image_path.is_file():
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="missing_evidence_image", cohort=cohort_name, task=task, identity=identity, detail=image_field)
                    continue
                if bbox is None:
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="invalid_evidence_bbox", cohort=cohort_name, task=task, identity=identity, detail=bbox_field)
                    continue
                if image_path not in dimensions:
                    dimensions[image_path] = image_dimensions(image_path)
                size = dimensions[image_path]
                if size is None:
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="unreadable_evidence_image", cohort=cohort_name, task=task, identity=identity, detail=image_field)
                elif bbox[0] < 0 or bbox[1] < 0 or bbox[2] > size[0] or bbox[3] > size[1]:
                    add_issue(issue_rows, issue_counts, severity="fatal", issue="bbox_out_of_frame", cohort=cohort_name, task=task, identity=identity, detail=f"{bbox_field}={bbox};image={size}")

    fatal_issue_occurrences = sum(
        count for key, count in issue_counts.items() if key.startswith("fatal:")
    )
    fatal_rows = len(
        {
            (row["cohort"], row["task"], row["identity"])
            for row in issue_rows
            if row["severity"] == "fatal"
        }
    )
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "split_plan": display(root, split_plan_path),
        "split_plan_sha256": file_sha256(split_plan_path),
        "cohorts": [
            {"name": name, "path": display(root, resolve(root, path)), "sha256": file_sha256(resolve(root, path))}
            for name, path in cohorts
        ],
        "counts": {
            "rows": total_rows,
            "unique_capacity_identities": len(seen_capacity),
            "tasks": dict(sorted(task_counts.items())),
            "reserved_splits": dict(sorted(split_counts.items())),
            "cohorts": dict(sorted(cohort_counts.items())),
            "source_documents_checked": len(source_cache),
            "evidence_images_checked": len(dimensions),
            "fatal_issue_rows": fatal_rows,
            "fatal_issue_occurrences": fatal_issue_occurrences,
        },
        "human_actions": dict(sorted(human_actions.items())),
        "contract_notes": dict(sorted(contract_notes.items())),
        "normalized_visualdiff_change_types": dict(sorted(change_types.items())),
        "issues": dict(sorted(issue_counts.items())),
        "structurally_ready_for_human_return_promotion": fatal_rows == 0,
        "human_review_complete": not any(
            value
            for key, value in human_actions.items()
            if key.endswith("decision_required")
            or key.endswith("correction_required")
            or key.endswith("text_required")
            or key.endswith("description_required")
        ),
        "active_gold_modified": False,
        "interpretation": (
            "Structural readiness means every staged row can enter the normal human-return and strict preview path. "
            "It does not authorize Gold promotion; human decisions and post-merge provenance, duplicate, leakage, "
            "annotation, unified, and strict-v2 gates remain mandatory."
        ),
    }
    return report, issue_rows


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        "# Staged Promotion Contract Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Staged rows: `{counts['rows']}`",
        f"- Unique identities: `{counts['unique_capacity_identities']}`",
        f"- Source documents checked: `{counts['source_documents_checked']}`",
        f"- Evidence images checked: `{counts['evidence_images_checked']}`",
        f"- Fatal issue rows: `{counts['fatal_issue_rows']}`",
        f"- Structurally ready for human-return promotion: `{str(report['structurally_ready_for_human_return_promotion']).lower()}`",
        f"- Human review complete: `{str(report['human_review_complete']).lower()}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "## Human Work Still Required",
        "",
        "| Action | Rows |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in report["human_actions"].items())
    lines.extend(["", "## Fatal Issues", "", "| Issue | Rows |", "|---|---:|"])
    fatal = {key.split(":", 1)[1]: value for key, value in report["issues"].items() if key.startswith("fatal:")}
    lines.extend(f"| {key} | {value} |" for key, value in fatal.items())
    if not fatal:
        lines.append("| none | 0 |")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort", action="append", type=parse_cohort, required=True)
    parser.add_argument("--split-plan", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--issues-csv", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report, issues = build_report(root, args.cohort, args.split_plan, date_label=args.date_label)
    write_json(resolve(root, args.output_json), report)
    write_markdown(resolve(root, args.output_md), report)
    write_csv(resolve(root, args.issues_csv), issues)
    print(json.dumps({
        "rows": report["counts"]["rows"],
        "fatal_issue_rows": report["counts"]["fatal_issue_rows"],
        "structurally_ready_for_human_return_promotion": report["structurally_ready_for_human_return_promotion"],
        "human_review_complete": report["human_review_complete"],
        "human_actions": report["human_actions"],
    }, indent=2, ensure_ascii=True))
    return 1 if args.require_ready and not report["structurally_ready_for_human_return_promotion"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
